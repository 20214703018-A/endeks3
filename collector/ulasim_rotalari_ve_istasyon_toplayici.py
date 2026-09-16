#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Türkiye Geneli Raylı Sistem, Tramvay, Vapur ve Toplu Taşıma Rota & İstasyon Madencisi
---------------------------------------------------------------------------------------------
81 il genelinde:
1. Tüm Metro, Tramvay, Banliyö (Marmaray, Başkentray, İZBAN, Gaziray), Vapur/Deniz ve Metrobüs Hatları
2. İstasyon ve Durak Listesi (sıra no, isim, tür, enlem, boylam, il, ilçe)
3. Hat Rotaları (LineString GeoJSON formatında rota geometrisi, toplam uzunluk km, durak sayısı)
4. İBB Açık Veri Portalı 343 istasyon canlı entegrasyonu
Tüm veriler ham, tam ve koordinatlı olarak SQLite ambarında arşivlenir.
"""

import math
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OSM_POI_DB = REPO_ROOT / "warehouse" / "product" / "osm_poi.sqlite"
IDARI_SINIR_DB = REPO_ROOT / "warehouse" / "product" / "idari_sinirlar.sqlite"
OUT_DB = REPO_ROOT / "warehouse" / "product" / "turkiye_toplu_tasima_rotalari.sqlite"
BATI_DB = REPO_ROOT / "warehouse" / "product" / "bati_ticari_istihbarat.sqlite"

IBB_RAIL_GEOJSON = "https://data.ibb.gov.tr/dataset/04ec9805-2483-46c7-914f-30c50857a846/resource/3dc8203f-3613-48a8-85e9-24fffb7821ad/download/rayli_sistem_istasyon_poi_verisi.geojson"

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS ulasim_hatlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hat_id_osm INTEGER,
        tur TEXT NOT NULL, -- 'subway', 'tram', 'train', 'ferry', 'light_rail', 'bus'
        hat_kodu TEXT,
        hat_adi TEXT NOT NULL,
        operator TEXT,
        renk TEXT,
        il TEXT,
        durak_sayisi INTEGER DEFAULT 0,
        toplam_uzunluk_km REAL DEFAULT 0.0,
        rota_geojson TEXT, -- LineString koordinat dizisi GeoJSON
        kaynak TEXT DEFAULT 'Resmî OSM Ulaşım Ağları & Belediye Açık Veri',
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(hat_id_osm, hat_adi)
    );

    CREATE TABLE IF NOT EXISTS ulasim_istasyonlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hat_id INTEGER,
        hat_adi TEXT,
        tur TEXT NOT NULL,
        sira_no INTEGER,
        istasyon_adi TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        il TEXT,
        ilce TEXT,
        osm_id INTEGER,
        kaynak TEXT DEFAULT 'Resmî OSM Durak Ambarı',
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(hat_adi, sira_no, lat, lon)
    );

    CREATE INDEX IF NOT EXISTS idx_uh_tur ON ulasim_hatlari(tur);
    CREATE INDEX IF NOT EXISTS idx_uh_il ON ulasim_hatlari(il);
    CREATE INDEX IF NOT EXISTS idx_ui_geo ON ulasim_istasyonlari(lat, lon);
    CREATE INDEX IF NOT EXISTS idx_ui_il_ilce ON ulasim_istasyonlari(il, ilce);
    """)

def load_district_bboxes():
    bboxes = []
    if not IDARI_SINIR_DB.exists():
        return bboxes
    conn = sqlite3.connect(f"file:{IDARI_SINIR_DB}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT il_adi, ad, min_lat, max_lat, min_lon, max_lon FROM sinir WHERE seviye = 'ilce'")
    for row in cur.fetchall():
        il, ilce, min_lat, max_lat, min_lon, max_lon = row
        if min_lat and max_lat and min_lon and max_lon:
            bboxes.append((il, ilce, float(min_lat), float(max_lat), float(min_lon), float(max_lon)))
    conn.close()
    return bboxes

def find_district(lat, lon, bboxes):
    for il, ilce, min_lat, max_lat, min_lon, max_lon in bboxes:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return il, ilce
    return None, None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def mine_osm_transit_routes(conn: sqlite3.Connection, bboxes: list):
    if not OSM_POI_DB.exists():
        print("[!] OSM POI veritabanı bulunamadı.")
        return

    print("📖 OSM POI ambarından raylı sistem, tramvay, vapur ve banliyö hatları çıkarılıyor...")
    osm_conn = sqlite3.connect(f"file:{OSM_POI_DB}?mode=ro", uri=True)
    osm_cur = osm_conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Sadece raylı, vapur ve ana toplu taşıma hatlarını önceliklendir (tüm 2.996 hat)
    osm_cur.execute("""
        SELECT id, osm_id, tur, ref, ad, operator, renk 
        FROM hat 
        WHERE tur IN ('subway', 'tram', 'train', 'ferry', 'light_rail', 'trolleybus')
           OR ad LIKE '%metro%' OR ad LIKE '%tramvay%' OR ad LIKE '%vapur%' OR ad LIKE '%marmaray%' OR ad LIKE '%banliyö%'
    """)
    hatlar = osm_cur.fetchall()
    print(f"  ✓ {len(hatlar)} adet raylı ve deniz toplu taşıma hattı tespit edildi.")

    total_stops = 0
    total_lines = 0

    for hat in hatlar:
        h_id, h_osm_id, tur, ref, ad, operator, renk = hat
        if not ad:
            continue

        # Hat duraklarını sırayla çek
        osm_cur.execute("""
            SELECT hd.sira, p.osm_id, p.ad, p.lat, p.lon 
            FROM hat_durak hd 
            JOIN poi p ON hd.durak_osm_id = p.osm_id 
            WHERE hd.hat_id = ? 
            ORDER BY hd.sira ASC
        """, (h_id,))
        duraklar = osm_cur.fetchall()

        coords = []
        uzunluk_km = 0.0
        il_tahmin = None

        for idx, d in enumerate(duraklar):
            sira, d_osm_id, d_ad, lat, lon = d
            if not lat or not lon:
                continue

            coords.append([lon, lat])
            if len(coords) > 1:
                uzunluk_km += haversine(coords[-2][1], coords[-2][0], lat, lon)

            il, ilce = find_district(lat, lon, bboxes)
            if il and not il_tahmin:
                il_tahmin = il

            conn.execute("""
            INSERT OR REPLACE INTO ulasim_istasyonlari
            (hat_id, hat_adi, tur, sira_no, istasyon_adi, lat, lon, il, ilce, osm_id, guncellenme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (h_id, ad, tur, sira, d_ad or f"Durak {sira}", lat, lon, il or "Türkiye", ilce or "", d_osm_id, now_iso))
            total_stops += 1

        rota_geojson = json.dumps({
            "type": "LineString",
            "coordinates": coords
        }) if len(coords) >= 2 else None

        conn.execute("""
        INSERT OR REPLACE INTO ulasim_hatlari
        (hat_id_osm, tur, hat_kodu, hat_adi, operator, renk, il, durak_sayisi, toplam_uzunluk_km, rota_geojson, guncellenme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (h_osm_id, tur, ref or "", ad, operator or "", renk or "", il_tahmin or "Türkiye", len(coords), round(uzunluk_km, 2), rota_geojson, now_iso))
        total_lines += 1

    conn.commit()
    osm_conn.close()
    print(f"✓ {total_lines} hat rotası ve {total_stops:,} istasyon başarıyla işlendi.")

def mine_ibb_rail_stations(conn: sqlite3.Connection, bboxes: list):
    print("🚇 İBB Açık Veri Portalı 343 Raylı Sistem İstasyonu GeoJSON indiriliyor...")
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        req = urllib.request.Request(IBB_RAIL_GEOJSON, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        features = data.get("features", [])
        saved = 0
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue

            lon, lat = coords[0], coords[1]
            ist_ad = props.get("ISTASYON_ADI") or props.get("ADI") or "İBB İstasyon"
            hat_ad = props.get("HAT_ADI") or props.get("HAT") or "İBB Raylı Hat"
            tur = props.get("TURU") or "Metro"

            il, ilce = find_district(lat, lon, bboxes)

            conn.execute("""
            INSERT OR REPLACE INTO ulasim_istasyonlari
            (hat_id, hat_adi, tur, sira_no, istasyon_adi, lat, lon, il, ilce, kaynak, guncellenme_tarihi)
            VALUES (9999, ?, ?, 0, ?, ?, ?, 'İstanbul', ?, 'İBB Açık Veri GeoJSON', ?)
            """, (hat_ad, tur, ist_ad, lat, lon, ilce or "İstanbul", now_iso))
            saved += 1

        conn.commit()
        print(f"✓ İBB Açık Veri'den {saved} istasyon eklendi/güncellendi.")
    except Exception as e:
        print(f"[!] İBB istasyonları indirilemedi: {e}")

def main():
    print("=" * 65)
    print("🚀 Türkiye Geneli Toplu Taşıma Rota ve İstasyon Madencisi")
    print("=" * 65)

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)

    bboxes = load_district_bboxes()
    print(f"✓ {len(bboxes)} ilçe sınır koordinatı yüklendi.")

    mine_osm_transit_routes(conn, bboxes)
    mine_ibb_rail_stations(conn, bboxes)

    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM ulasim_hatlari")
    h_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ulasim_istasyonlari")
    i_count = cur.fetchone()[0]
    cur.execute("SELECT tur, count(*) FROM ulasim_hatlari GROUP BY tur")
    tur_counts = cur.fetchall()

    conn.close()
    print("=" * 65)
    print(f"✅ Toplam {h_count} hat rotası ve {i_count:,} istasyon kaydedildi!")
    for tur, cnt in tur_counts:
        print(f"   • {tur}: {cnt} hat")
    print("=" * 65)

if __name__ == "__main__":
    main()
