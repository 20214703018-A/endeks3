#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Türkiye Geneli Çıpa Markalar, Kahve Zincirleri, Restoranlar, Banka ve ATM Madencisi
---------------------------------------------------------------------------------------------
81 il genelinde:
1. Tüm Banka Şubeleri ve ATM'leri (Ziraat, İş, Garanti, Akbank, Yapı Kredi, Halkbank, Vakıf, QNB, Denizbank vb.)
2. Ulusal ve Uluslararası Kahve Zincirleri (Starbucks, Kahve Dünyası, Espressolab, Tchibo, Gloria Jean's vb.)
3. Fast Food ve Restoran Zincirleri (McDonald's, Burger King, KFC, Popeyes, Domino's, Tavuk Dünyası, Köfteci Yusuf vb.)
4. Zincir Süpermarketler ve Kozmetik Perakende (BİM, A101, Şok, Migros, CarrefourSA, File, Gratis, Watsons vb.)
5. Moda ve Teknoloji Çıpa Markaları (Zara, LCW, Mavi, Boyner, Teknosa, MediaMarkt, Decathlon vb.)
Tüm noktalar enlem (lat), boylam (lon), il, ilce ve ISO 8601 tarih damgasıyla SQLite ambarında toplanır.
"""

import os
import re
import sys
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OSM_POI_DB = REPO_ROOT / "warehouse" / "product" / "osm_poi.sqlite"
IDARI_SINIR_DB = REPO_ROOT / "warehouse" / "product" / "idari_sinirlar.sqlite"
OUT_DB = REPO_ROOT / "warehouse" / "product" / "zincir_markalar_ve_finans.sqlite"

BANKA_LISTESI = [
    "Ziraat", "İş Bankası", "Garanti BBVA", "Garanti", "Akbank", "Yapı Kredi",
    "Halkbank", "VakıfBank", "DenizBank", "QNB", "Finansbank", "TEB",
    "Kuveyt Türk", "ING", "Şekerbank", "Türkiye Finans", "Albaraka",
    "HSBC", "Vakıf Katılım", "Ziraat Katılım", "Emlak Katılım", "Burgan",
    "Fibabanka", "Alternatif Bank", "Anadolubank", "Odeabank", "Aktif Bank"
]

KAHVE_ZINCIRLERI = [
    "Starbucks", "Kahve Dünyası", "Espressolab", "Tchibo", "Gloria Jean's",
    "Caribou Coffee", "Arabica Coffee House", "Federal Coffee", "Kronotrop",
    "Coffeetopia", "Coffee Department", "Petra Roasting", "Mackbear", "Soulmate",
    "Barn's Coffee", "Nevada Coffee", "Green White", "Brew Mood"
]

RESTORAN_VE_FASTFOOD = [
    "McDonald's", "Burger King", "KFC", "Popeyes", "Domino's", "Domino's Pizza",
    "Pizza Hut", "Little Caesars", "HD İskender", "Tavuk Dünyası", "Köfteci Yusuf",
    "BigChefs", "Midpoint", "Cookshop", "Happy Moon's", "Mado", "Simit Sarayı",
    "Baydöner", "Bursa Kebap Evi", "Sbarro", "Arby's", "Subway", "Carl's Jr.",
    "Shake Shack", "Dürümle", "Döner Stop", "Kasap Döner", "Usta Dönerci"
]

ZINCIR_MARKET_VE_PERAKENDE = [
    "BİM", "A101", "Şok", "Migros", "CarrefourSA", "File", "File Market",
    "Tarım Kredi", "Hakmar", "Macrocenter", "Metro", "Bizim Toptan",
    "Gratis", "Watsons", "Rossmann", "Eve Shop", "Sephora",
    "LC Waikiki", "DeFacto", "Koton", "Mavi", "Boyner", "Zara", "Bershka",
    "Pull&Bear", "Stradivarius", "Massimo Dutti", "Oysho", "Mango", "H&M",
    "Decathlon", "Teknosa", "MediaMarkt", "Vatan Bilgisayar", "İkea", "Koçtaş"
]

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS poi_zincir_ve_finans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        ana_tur TEXT NOT NULL,
        marka TEXT NOT NULL,
        sube_adi TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        adres_acik TEXT,
        telefon TEXT,
        osm_id INTEGER,
        kaynak TEXT DEFAULT 'Resmî OSM POI Ambarı & Doğrulanmış Konumlar',
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(kategori, marka, lat, lon)
    );

    CREATE TABLE IF NOT EXISTS marka_ilce_dagilim_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        kategori TEXT NOT NULL,
        marka TEXT NOT NULL,
        sube_sayisi INTEGER NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, ilce, kategori, marka)
    );

    CREATE INDEX IF NOT EXISTS idx_zf_kat ON poi_zincir_ve_finans(kategori);
    CREATE INDEX IF NOT EXISTS idx_zf_marka ON poi_zincir_ve_finans(marka);
    CREATE INDEX IF NOT EXISTS idx_zf_il_ilce ON poi_zincir_ve_finans(il, ilce);
    CREATE INDEX IF NOT EXISTS idx_zf_geo ON poi_zincir_ve_finans(lat, lon);
    """)

def load_district_bboxes():
    """idari_sinirlar.sqlite içindeki ilçe BBOX sınırlarını yükler."""
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

def mine_from_osm_poi(out_conn: sqlite3.Connection, bboxes: list):
    if not OSM_POI_DB.exists():
        print("[!] OSM POI veritabanı bulunamadı.")
        return

    print("📖 OSM POI ambarından (623K kayıt) çıpa markalar ve bankalar taranıyor...")
    osm_conn = sqlite3.connect(f"file:{OSM_POI_DB}?mode=ro", uri=True)
    osm_cur = osm_conn.cursor()

    now_iso = datetime.now(timezone.utc).isoformat()
    inserted = 0

    # 1. Bankalar ve ATM'ler
    print("  -> Banka ve ATM lokasyonları çıkarılıyor...")
    osm_cur.execute("""
        SELECT osm_id, alt_kategori, marka, ad, lat, lon, etiketler 
        FROM poi 
        WHERE alt_kategori IN ('banka', 'atm') OR kategori IN ('banka', 'atm', 'finans')
    """)
    for r in osm_cur.fetchall():
        osm_id, alt_kat, marka, ad, lat, lon, etiketler = r
        if not lat or not lon:
            continue

        brand = marka or ""
        if not brand and ad:
            for b in BANKA_LISTESI:
                if b.lower() in ad.lower():
                    brand = b
                    break
        if not brand:
            brand = "Diğer / Bağımsız ATM"

        kat = "ATM" if alt_kat == "atm" or "atm" in (ad or "").lower() else "BANKA"
        il, ilce = find_district(lat, lon, bboxes)

        try:
            out_conn.execute("""
            INSERT OR REPLACE INTO poi_zincir_ve_finans
            (kategori, ana_tur, marka, sube_adi, lat, lon, il, ilce, mahalle, osm_id, guncellenme_tarihi)
            VALUES (?, 'Finansal Hizmetler', ?, ?, ?, ?, ?, ?, '', ?, ?)
            """, (kat, brand, ad or f"{brand} {kat}", lat, lon, il or "Türkiye", ilce or "", osm_id, now_iso))
            inserted += 1
        except Exception:
            pass

    # 2. Kahve Zincirleri
    print("  -> Kahve zincirleri taranıyor...")
    for k in KAHVE_ZINCIRLERI:
        osm_cur.execute("""
            SELECT osm_id, alt_kategori, marka, ad, lat, lon 
            FROM poi 
            WHERE marka LIKE ? OR ad LIKE ?
        """, (f"%{k}%", f"%{k}%"))
        for r in osm_cur.fetchall():
            osm_id, alt_kat, marka, ad, lat, lon = r
            if not lat or not lon:
                continue
            il, ilce = find_district(lat, lon, bboxes)
            try:
                out_conn.execute("""
                INSERT OR REPLACE INTO poi_zincir_ve_finans
                (kategori, ana_tur, marka, sube_adi, lat, lon, il, ilce, mahalle, osm_id, guncellenme_tarihi)
                VALUES ('KAHVE_ZINCIRI', 'Yeme-İçme & Çekim', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """, (k, ad or f"{k} Şubesi", lat, lon, il or "Türkiye", ilce or "", osm_id, now_iso))
                inserted += 1
            except Exception:
                pass

    # 3. Restoran ve Fast Food Zincirleri
    print("  -> Restoran ve Fast-Food zincirleri taranıyor...")
    for r_marka in RESTORAN_VE_FASTFOOD:
        osm_cur.execute("""
            SELECT osm_id, alt_kategori, marka, ad, lat, lon 
            FROM poi 
            WHERE marka LIKE ? OR ad LIKE ?
        """, (f"%{r_marka}%", f"%{r_marka}%"))
        for r in osm_cur.fetchall():
            osm_id, alt_kat, marka, ad, lat, lon = r
            if not lat or not lon:
                continue
            il, ilce = find_district(lat, lon, bboxes)
            try:
                out_conn.execute("""
                INSERT OR REPLACE INTO poi_zincir_ve_finans
                (kategori, ana_tur, marka, sube_adi, lat, lon, il, ilce, mahalle, osm_id, guncellenme_tarihi)
                VALUES ('RESTORAN_ZINCIRI', 'Yeme-İçme & Çekim', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """, (r_marka, ad or f"{r_marka} Şubesi", lat, lon, il or "Türkiye", ilce or "", osm_id, now_iso))
                inserted += 1
            except Exception:
                pass

    # 4. Süpermarket ve Perakende Mağaza Zincirleri
    print("  -> Süpermarket ve Perakende zincirleri taranıyor...")
    for m_marka in ZINCIR_MARKET_VE_PERAKENDE:
        osm_cur.execute("""
            SELECT osm_id, alt_kategori, marka, ad, lat, lon 
            FROM poi 
            WHERE marka LIKE ? OR ad LIKE ?
        """, (f"%{m_marka}%", f"%{m_marka}%"))
        for r in osm_cur.fetchall():
            osm_id, alt_kat, marka, ad, lat, lon = r
            if not lat or not lon:
                continue
            il, ilce = find_district(lat, lon, bboxes)
            kat = "ZINCIR_MARKET" if m_marka in ["BİM", "A101", "Şok", "Migros", "CarrefourSA", "File", "Tarım Kredi", "Hakmar", "Macrocenter", "Metro"] else "PERAKENDE_MAGAZA"
            try:
                out_conn.execute("""
                INSERT OR REPLACE INTO poi_zincir_ve_finans
                (kategori, ana_tur, marka, sube_adi, lat, lon, il, ilce, mahalle, osm_id, guncellenme_tarihi)
                VALUES (?, 'Perakende & Alışveriş', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """, (kat, m_marka, ad or f"{m_marka} Şubesi", lat, lon, il or "Türkiye", ilce or "", osm_id, now_iso))
                inserted += 1
            except Exception:
                pass

    out_conn.commit()
    osm_conn.close()
    print(f"✓ OSM POI'den toplam {inserted:,} adet çıpa marka ve finans noktası kaydedildi.")

def compute_district_summaries(conn: sqlite3.Connection):
    """İlçe ve marka bazında toplam şube sayılarını özet tabloya yazar."""
    print("📊 İlçe bazlı marka özetleri oluşturuluyor...")
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute("""
        SELECT il, ilce, kategori, marka, count(*) 
        FROM poi_zincir_ve_finans 
        WHERE il IS NOT NULL AND ilce IS NOT NULL AND il != '' AND ilce != ''
        GROUP BY il, ilce, kategori, marka
    """)
    for row in cur.fetchall():
        il, ilce, kat, marka, count = row
        conn.execute("""
        INSERT OR REPLACE INTO marka_ilce_dagilim_ozet
        (il, ilce, kategori, marka, sube_sayisi, guncellenme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (il, ilce, kat, marka, count, now_iso))
    conn.commit()

def main():
    print("=" * 65)
    print("🚀 Türkiye Geneli Çıpa Markalar, Finans ve Perakende Madencisi")
    print("=" * 65)

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)

    bboxes = load_district_bboxes()
    print(f"✓ {len(bboxes)} ilçe sınır koordinatı yüklendi.")

    mine_from_osm_poi(conn, bboxes)
    compute_district_summaries(conn)

    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM poi_zincir_ve_finans")
    total_poi = cur.fetchone()[0]
    cur.execute("SELECT kategori, count(*) FROM poi_zincir_ve_finans GROUP BY kategori")
    kat_counts = cur.fetchall()

    conn.close()
    print("=" * 65)
    print(f"✅ Toplam {total_poi:,} adet nokta kaydedildi!")
    for k, cnt in kat_counts:
        print(f"   • {k}: {cnt:,} nokta")
    print("=" * 65)

if __name__ == "__main__":
    main()
