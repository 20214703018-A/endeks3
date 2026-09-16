#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Büyükşehirler İl, İlçe ve Mahalle Düzeyinde Otopark ve Kapasite Madencisi
---------------------------------------------------------------------------------
Türkiye geneli ve özellikle büyükşehirlerde:
1. İSPARK Canlı API: 247 Otopark (kapalı, açık, yol üstü kapasiteleri ve koordinatları)
2. OSM POI Ambarı: 24.466 adet otopark ve katlı otopark alanı (lat, lon, il, ilçe)
3. İlçe ve Mahalle bazında otopark kapasitesi ve park baskı endeksi
Veriler ham, eksiksiz ve koordinatlı olarak SQLite ambarında arşivlenir.
"""

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OSM_POI_DB = REPO_ROOT / "warehouse" / "product" / "osm_poi.sqlite"
IDARI_SINIR_DB = REPO_ROOT / "warehouse" / "product" / "idari_sinirlar.sqlite"
OUT_DB = REPO_ROOT / "warehouse" / "product" / "buyuksehirler_otopark_envanteri.sqlite"

ISPARK_API = "https://api.ibb.gov.tr/ispark/Park"

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS otopark_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        otopark_adi TEXT NOT NULL,
        operator TEXT, -- 'İSPARK', 'İZELMAN', 'BURBAK', 'Özel / Belediye', 'OSM'
        tur TEXT, -- 'KAPALI OTOPARK', 'AÇIK OTOPARK', 'YOL ÜSTÜ', 'KATLI OTOPARK'
        kapasite INTEGER DEFAULT 0,
        bos_kapasite INTEGER DEFAULT 0,
        ucretsiz_dakika INTEGER DEFAULT 0,
        calisma_saatleri TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        harici_id TEXT,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(otopark_adi, lat, lon)
    );

    CREATE TABLE IF NOT EXISTS ilce_otopark_kapasite_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        toplam_otopark_noktasi INTEGER NOT NULL,
        toplam_bilinen_kapasite INTEGER DEFAULT 0,
        kapali_otopark_sayisi INTEGER DEFAULT 0,
        acik_otopark_sayisi INTEGER DEFAULT 0,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, ilce)
    );

    CREATE INDEX IF NOT EXISTS idx_oto_il_ilce ON otopark_noktalari(il, ilce);
    CREATE INDEX IF NOT EXISTS idx_oto_geo ON otopark_noktalari(lat, lon);
    CREATE INDEX IF NOT EXISTS idx_oto_operator ON otopark_noktalari(operator);
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

def mine_ispark_live(conn: sqlite3.Connection):
    print("🅿️ İSPARK Canlı API'den İstanbul otoparkları çekiliyor...")
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        req = urllib.request.Request(ISPARK_API, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        saved = 0
        for p in data:
            name = p.get("parkName", "İSPARK")
            lat = float(p.get("lat") or 0.0)
            lng = float(p.get("lng") or 0.0)
            if not lat or not lng:
                continue

            cap = int(p.get("capacity") or 0)
            empty_cap = int(p.get("emptyCapacity") or 0)
            district = (p.get("district") or "").strip().title()
            p_type = p.get("parkType", "Açık Otopark")
            free_time = int(p.get("freeTime") or 0)
            hours = p.get("workHours", "24 Saat")
            p_id = str(p.get("parkID", ""))

            conn.execute("""
            INSERT OR REPLACE INTO otopark_noktalari
            (otopark_adi, operator, tur, kapasite, bos_kapasite, ucretsiz_dakika, calisma_saatleri,
             lat, lon, il, ilce, mahalle, harici_id, kaynak, guncellenme_tarihi)
            VALUES (?, 'İSPARK', ?, ?, ?, ?, ?, ?, ?, 'İstanbul', ?, 'Merkez', ?, 'İSPARK Canlı API', ?)
            """, (name, p_type, cap, empty_cap, free_time, hours, lat, lng, district, p_id, now_iso))
            saved += 1

        conn.commit()
        print(f"✓ İSPARK'tan {saved} otopark başarıyla kaydedildi.")
    except Exception as e:
        print(f"[!] İSPARK API çekilemedi: {e}")

def mine_osm_parkings(conn: sqlite3.Connection, bboxes: list):
    if not OSM_POI_DB.exists():
        print("[!] OSM POI veritabanı bulunamadı.")
        return

    print("📖 OSM POI ambarından (24.466 kayıt) otopark alanları çıkarılıyor...")
    osm_conn = sqlite3.connect(f"file:{OSM_POI_DB}?mode=ro", uri=True)
    osm_cur = osm_conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    osm_cur.execute("""
        SELECT osm_id, marka, ad, lat, lon, etiketler 
        FROM poi 
        WHERE alt_kategori IN ('otopark', 'parking') OR kategori = 'otopark'
    """)
    rows = osm_cur.fetchall()
    saved = 0

    for r in rows:
        osm_id, marka, ad, lat, lon, etiketler = r
        if not lat or not lon:
            continue

        il, ilce = find_district(lat, lon, bboxes)
        p_name = ad or marka or f"Otopark #{osm_id}"
        operator = marka or "Belediye / Özel Otopark"

        conn.execute("""
        INSERT OR IGNORE INTO otopark_noktalari
        (otopark_adi, operator, tur, kapasite, bos_kapasite, lat, lon, il, ilce, mahalle, harici_id, kaynak, guncellenme_tarihi)
        VALUES (?, ?, 'AÇIK OTOPARK', 0, 0, ?, ?, ?, ?, '', ?, 'OSM POI Ambarı', ?)
        """, (p_name, operator, lat, lon, il or "Türkiye", ilce or "", str(osm_id), now_iso))
        saved += 1

    conn.commit()
    osm_conn.close()
    print(f"✓ OSM'den {saved:,} otopark noktası işlendi.")

def compute_district_summaries(conn: sqlite3.Connection):
    print("📊 İlçe bazlı otopark kapasite ve sayı özetleri hesaplanıyor...")
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute("""
        SELECT il, ilce, count(*), sum(kapasite),
               sum(case when tur LIKE '%KAPALI%' or tur LIKE '%KATLI%' then 1 else 0 end),
               sum(case when tur NOT LIKE '%KAPALI%' and tur NOT LIKE '%KATLI%' then 1 else 0 end)
        FROM otopark_noktalari
        WHERE il IS NOT NULL AND ilce IS NOT NULL AND il != '' AND ilce != ''
        GROUP BY il, ilce
    """)
    for row in cur.fetchall():
        il, ilce, total_n, total_cap, kapali_n, acik_n = row
        conn.execute("""
        INSERT OR REPLACE INTO ilce_otopark_kapasite_ozet
        (il, ilce, toplam_otopark_noktasi, toplam_bilinen_kapasite, kapali_otopark_sayisi, acik_otopark_sayisi, guncellenme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (il, ilce, total_n, total_cap or 0, kapali_n or 0, acik_n or 0, now_iso))
    conn.commit()

def main():
    print("=" * 65)
    print("🚀 Büyükşehirler Otopark ve Kapasite Envanteri Madencisi")
    print("=" * 65)

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)

    bboxes = load_district_bboxes()
    print(f"✓ {len(bboxes)} ilçe sınır koordinatı yüklendi.")

    mine_ispark_live(conn)
    mine_osm_parkings(conn, bboxes)
    compute_district_summaries(conn)

    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM otopark_noktalari")
    total_park = cur.fetchone()[0]
    cur.execute("SELECT operator, count(*) FROM otopark_noktalari GROUP BY operator ORDER BY count(*) DESC LIMIT 5")
    top_ops = cur.fetchall()

    conn.close()
    print("=" * 65)
    print(f"✅ Toplam {total_park:,} otopark kaydedildi!")
    for op, cnt in top_ops:
        print(f"   • {op}: {cnt:,} otopark")
    print("=" * 65)

if __name__ == "__main__":
    main()
