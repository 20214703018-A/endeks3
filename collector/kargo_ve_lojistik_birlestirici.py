#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Türkiye Kargo Şubeleri, Teslimat Dolapları ve Lojistik Noktaları Birleştirici
--------------------------------------------------------------------------------------
1. PTT Kargo Şubeleri ve 7/24 Kargomat Akıllı Dolapları (3.675 nokta)
2. Yurtiçi Kargo, Aras Kargo, MNG Kargo, Sürat Kargo, Trendyol Express şubeleri
3. Koordinat (lat, lon), il, ilce, adres ve aktiflik durumlarıyla arşivleme
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ETIKET_DB = REPO_ROOT / "collector" / "data" / "eticaret_ve_lojistik.sqlite"
OSM_POI_DB = REPO_ROOT / "warehouse" / "product" / "osm_poi.sqlite"
IDARI_SINIR_DB = REPO_ROOT / "warehouse" / "product" / "idari_sinirlar.sqlite"
OUT_DB = REPO_ROOT / "warehouse" / "product" / "kargo_ve_lojistik_noktalari.sqlite"
ISTIHBARAT_DB = REPO_ROOT / "collector" / "data" / "turkiye_makro_ve_mikro_istihbarat.sqlite"

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS kargo_ve_teslimat_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tip TEXT NOT NULL, -- 'PTT_SUBE', 'PTT_KARGOMAT', 'OZEL_KARGO_SUBE', 'TESLIMAT_DOLABI'
        marka TEXT NOT NULL, -- 'PTT', 'Yurtiçi Kargo', 'Aras Kargo', 'MNG Kargo', 'Sürat Kargo', 'Trendyol Express'
        sube_adi TEXT NOT NULL,
        adres_acik TEXT,
        telefon TEXT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(marka, sube_adi, lat, lon)
    );

    CREATE TABLE IF NOT EXISTS ilce_kargo_yogunluk_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        toplam_teslimat_noktasi INTEGER NOT NULL,
        ptt_sube_sayisi INTEGER DEFAULT 0,
        kargomat_dolap_sayisi INTEGER DEFAULT 0,
        ozel_kargo_sube_sayisi INTEGER DEFAULT 0,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, ilce)
    );

    CREATE INDEX IF NOT EXISTS idx_kargo_marka ON kargo_ve_teslimat_noktalari(marka);
    CREATE INDEX IF NOT EXISTS idx_kargo_il_ilce ON kargo_ve_teslimat_noktalari(il, ilce);
    CREATE INDEX IF NOT EXISTS idx_kargo_geo ON kargo_ve_teslimat_noktalari(lat, lon);
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

def merge_kargo_data():
    print("=" * 65)
    print("🚀 Türkiye Geneli Kargo ve Lojistik Noktaları Arşivleme Başlıyor...")
    print("=" * 65)

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    out_conn = sqlite3.connect(OUT_DB)
    init_db(out_conn)

    bboxes = load_district_bboxes()
    now_iso = datetime.now(timezone.utc).isoformat()
    total_saved = 0

    # 1. PTT Şubeleri ve Kargomatlar
    if ETIKET_DB.exists():
        print("📦 PTT Şubeleri ve 7/24 Kargomatlar aktarılıyor...")
        e_conn = sqlite3.connect(f"file:{ETIKET_DB}?mode=ro", uri=True)
        e_cur = e_conn.cursor()
        e_cur.execute("SELECT tip, ad, adres, telefon, il_ad, ilce_ad, lat, lon FROM kargo_ve_teslimat_noktalari")
        for r in e_cur.fetchall():
            tip, ad, adres, tel, il, ilce, lat, lon = r
            if not lat or not lon:
                continue

            if not ilce:
                _, ilce = find_district(lat, lon, bboxes)

            try:
                out_conn.execute("""
                INSERT OR REPLACE INTO kargo_ve_teslimat_noktalari
                (tip, marka, sube_adi, adres_acik, telefon, il, ilce, mahalle, lat, lon, kaynak, guncellenme_tarihi)
                VALUES (?, 'PTT', ?, ?, ?, ?, ?, '', ?, ?, 'PTT Resmî API', ?)
                """, (tip, ad, adres, tel, il or "Türkiye", ilce or "", lat, lon, now_iso))
                total_saved += 1
            except Exception:
                pass
        e_conn.close()

    # 2. Özel Kargo Şubeleri (Yurtiçi, Aras, MNG, Sürat, Trendyol Express vb.)
    if OSM_POI_DB.exists():
        print("🚚 Özel kargo şubeleri OSM POI ambarından çıkarılıyor...")
        osm_conn = sqlite3.connect(f"file:{OSM_POI_DB}?mode=ro", uri=True)
        osm_cur = osm_conn.cursor()
        osm_cur.execute("""
            SELECT osm_id, marka, ad, lat, lon 
            FROM poi 
            WHERE ad LIKE '%kargo%' 
               OR ad LIKE '%yurtiçi%' 
               OR ad LIKE '%aras%' 
               OR ad LIKE '%mng%' 
               OR ad LIKE '%sürat kargo%'
               OR ad LIKE '%trendyol express%'
        """)
        for r in osm_cur.fetchall():
            osm_id, marka, ad, lat, lon = r
            if not lat or not lon:
                continue

            # Filtrele: Yol veya terminal isimleri değil, kargo şubesi olanlar
            name_lower = (ad or "").lower()
            if any(w in name_lower for w in ["otogar", "terminal", "mağara", "school", "okul"]):
                continue

            brand = marka or "Özel Kargo"
            if "yurtiçi" in name_lower:
                brand = "Yurtiçi Kargo"
            elif "aras" in name_lower:
                brand = "Aras Kargo"
            elif "mng" in name_lower:
                brand = "MNG Kargo"
            elif "sürat" in name_lower:
                brand = "Sürat Kargo"
            elif "trendyol" in name_lower:
                brand = "Trendyol Express"

            il, ilce = find_district(lat, lon, bboxes)

            try:
                out_conn.execute("""
                INSERT OR REPLACE INTO kargo_ve_teslimat_noktalari
                (tip, marka, sube_adi, adres_acik, il, ilce, mahalle, lat, lon, kaynak, guncellenme_tarihi)
                VALUES ('OZEL_KARGO_SUBE', ?, ?, ?, ?, ?, '', ?, ?, 'OSM POI Doğrulanmış Konumlar', ?)
                """, (brand, ad or f"{brand} Şubesi", ad, il or "Türkiye", ilce or "", lat, lon, now_iso))
                total_saved += 1
            except Exception:
                pass
        osm_conn.close()

    out_conn.commit()

    # 3. İlçe Yoğunluk Özetlerini Hesapla
    print("📊 İlçe kargo yoğunluk özetleri hesaplanıyor...")
    cur = out_conn.cursor()
    cur.execute("""
        SELECT il, ilce, count(*),
               sum(case when tip = 'PTT_SUBE' then 1 else 0 end),
               sum(case when tip = 'PTT_KARGOMAT' then 1 else 0 end),
               sum(case when tip = 'OZEL_KARGO_SUBE' then 1 else 0 end)
        FROM kargo_ve_teslimat_noktalari
        WHERE il IS NOT NULL AND ilce IS NOT NULL AND il != '' AND ilce != ''
        GROUP BY il, ilce
    """)
    for row in cur.fetchall():
        il, ilce, total_n, ptt_n, dolap_n, ozel_n = row
        out_conn.execute("""
        INSERT OR REPLACE INTO ilce_kargo_yogunluk_ozet
        (il, ilce, toplam_teslimat_noktasi, ptt_sube_sayisi, kargomat_dolap_sayisi, ozel_kargo_sube_sayisi, guncellenme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (il, ilce, total_n, ptt_n, dolap_n, ozel_n, now_iso))
    out_conn.commit()

    # 4. Boş olan turkiye_makro_ve_mikro_istihbarat.sqlite::kargo_ve_teslimat_noktalari tablosunu doldur
    if ISTIHBARAT_DB.exists():
        try:
            i_conn = sqlite3.connect(ISTIHBARAT_DB)
            i_conn.execute("""
            CREATE TABLE IF NOT EXISTS kargo_ve_teslimat_noktalari (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tip TEXT,
                kod TEXT,
                ad TEXT,
                adres TEXT,
                telefon TEXT,
                il_kod INTEGER,
                il_ad TEXT,
                ilce_kod INTEGER,
                ilce_ad TEXT,
                mahalle_ad TEXT,
                lat REAL,
                lon REAL,
                hafta_ici TEXT,
                cumartesi TEXT,
                pazar TEXT,
                aktiflik INTEGER DEFAULT 1,
                guncellenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tip, ad, adres)
            );
            """)
            cur.execute("SELECT tip, marka, sube_adi, adres_acik, telefon, il, ilce, lat, lon FROM kargo_ve_teslimat_noktalari")
            for row in cur.fetchall():
                t, m, sube, adr, tel, il, ilce, lat, lon = row
                i_conn.execute("""
                INSERT OR IGNORE INTO kargo_ve_teslimat_noktalari
                (tip, kod, ad, adres, telefon, il_ad, ilce_ad, lat, lon, guncellenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (t, m, sube, adr or sube, tel or '', il, ilce, lat, lon, now_iso))
            i_conn.commit()
            i_conn.close()
            print("✓ turkiye_makro_ve_mikro_istihbarat.sqlite::kargo_ve_teslimat_noktalari tablosu dolduruldu.")
        except Exception as e:
            print(f"[!] İstihbarat DB sync hatası: {e}")

    out_conn.close()
    print("=" * 65)
    print(f"✅ Toplam {total_saved:,} adet kargo ve akıllı teslimat noktası başarıyla arşivlendi!")
    print("=" * 65)

if __name__ == "__main__":
    merge_kargo_data()
