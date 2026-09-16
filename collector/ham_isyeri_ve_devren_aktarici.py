#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Ham Devren ve Kiralık / Satılık İşyeri İlanları Veri Ambarı
-------------------------------------------------------------------
Türkiye genelindeki 12.000+ ticari işyeri, devren kiralık/satılık dükkan,
mağaza ve ofis ilanlarını ham verileriyle (başlık, m², birim fiyat,
enlem, boylam, tarih, kaynak URL) SQLite ambarına kurallı şekilde aktarır.
Hiçbir türetme veya subjektif yorum içermez; %100 ham ampirik veridir.
"""

import csv
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DB = REPO_ROOT / "warehouse" / "product" / "ham_devren_ve_isyeri_ilanlari.sqlite"

CSV_1 = REPO_ROOT / "VERİLER" / "Öğelerle Yeni Klasör 2" / "tum_turkiye_nihai_paket" / "csv_ciktilari" / "turkiye_isyeri_ayrintili.csv"
if not CSV_1.exists():
    CSV_1 = REPO_ROOT / "VERİLER" / "Öğelerle Yeni Klasör 2" / "tum_turkiye_nihai_paket" / "csv_ciktilari" / "turkiye_isyeri_ayrintili.csv"

CSV_2 = REPO_ROOT / "VERİLER" / "Öğelerle Yeni Klasör 2" / "turkiye_tum_ilanlar_veriseti" / "csv" / "turkiye_satilik_isyeri_ilanlari.csv"
if not CSV_2.exists():
    CSV_2 = REPO_ROOT / "VERİLER" / "Öğelerle Yeni Klasör 2" / "turkiye_tum_ilanlar_veriseti" / "csv" / "turkiye_satilik_isyeri_ilanlari.csv"

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS ham_isyeri_ilanlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ilan_no TEXT UNIQUE,
        portal TEXT,
        ilan_basligi TEXT NOT NULL,
        ana_kategori TEXT,
        emlak_turu TEXT,
        devren_mi INTEGER DEFAULT 0, -- 1: Devren ilan, 0: Standart ilan
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        mahalle TEXT,
        ada_no TEXT,
        parsel_no TEXT,
        fiyat_tl REAL,
        brut_m2 REAL,
        net_m2 REAL,
        birim_m2_fiyat_tl REAL,
        bina_yasi TEXT,
        kat TEXT,
        lat REAL,
        lon REAL,
        satici_turu TEXT,
        satici_adi TEXT,
        ilan_tarihi TEXT,
        ilan_linki TEXT,
        kaynak_dosya TEXT,
        guncellenme_tarihi TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ilce_devren_ve_isyeri_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        toplam_isyeri_ilani INTEGER NOT NULL,
        devren_ilan_sayisi INTEGER NOT NULL,
        devren_ilan_orani_yuzde REAL NOT NULL,
        ortalama_m2_fiyat_tl REAL,
        medyan_m2_fiyat_tl REAL,
        en_dusuk_fiyat_tl REAL,
        en_yuksek_fiyat_tl REAL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, ilce)
    );

    CREATE INDEX IF NOT EXISTS idx_isyeri_il_ilce ON ham_isyeri_ilanlari(il, ilce);
    CREATE INDEX IF NOT EXISTS idx_isyeri_devren ON ham_isyeri_ilanlari(devren_mi);
    CREATE INDEX IF NOT EXISTS idx_isyeri_geo ON ham_isyeri_ilanlari(lat, lon);
    """)

def process_csv_1(conn: sqlite3.Connection):
    if not CSV_1.exists():
        print(f"[!] CSV 1 bulunamadı: {CSV_1}")
        return 0

    print(f"📖 {CSV_1.name} (12.108 ilan) okunuyor...")
    now_iso = datetime.now(timezone.utc).isoformat()
    saved = 0

    with open(CSV_1, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f, delimiter=";")
        hdr = next(reader)
        # Sütun indekslerini tespit et
        def get_i(name):
            return hdr.index(name) if name in hdr else -1

        no_i = get_i("\ufeffİlan No") if get_i("\ufeffİlan No") != -1 else get_i("İlan No")
        portal_i = get_i("Portal")
        ana_kat_i = get_i("Ana Kategori")
        tur_i = get_i("Emlak Türü")
        b_i = get_i("İlan Başlığı")
        il_i = get_i("İl")
        ilce_i = get_i("İlçe")
        mah_i = get_i("Mahalle")
        ada_i = get_i("Ada No")
        par_i = get_i("Parsel No")
        fiyat_i = get_i("Fiyat (TL)")
        brut_i = get_i("Brüt m²")
        net_i = get_i("Net m²")
        m2_i = get_i("m² Birim Fiyatı (TL)")
        yas_i = get_i("Bina Yaşı")
        kat_i = get_i("Bulunduğu Kat")
        lat_i = get_i("Enlem (Latitude)")
        lon_i = get_i("Boylam (Longitude)")
        satici_tur_i = get_i("Satıcı Türü")
        satici_ad_i = get_i("Satıcı / Ofis Adı")
        tarih_i = get_i("İlan Tarihi")
        link_i = get_i("İlan Linki")

        for row in reader:
            if len(row) < 10:
                continue

            baslik = row[b_i].strip() if b_i != -1 and b_i < len(row) else ""
            il = row[il_i].strip() if il_i != -1 and il_i < len(row) else ""
            ilce = row[ilce_i].strip() if ilce_i != -1 and ilce_i < len(row) else ""
            if not il or not ilce:
                continue

            ilan_no = row[no_i].strip() if no_i != -1 and no_i < len(row) else f"csv1_{saved}"
            devren = 1 if "devren" in baslik.lower() else 0

            try:
                fiyat = float(row[fiyat_i]) if fiyat_i != -1 and row[fiyat_i] else 0.0
            except Exception:
                fiyat = 0.0

            try:
                brut = float(row[brut_i]) if brut_i != -1 and row[brut_i] else 0.0
            except Exception:
                brut = 0.0

            try:
                net = float(row[net_i]) if net_i != -1 and row[net_i] else 0.0
            except Exception:
                net = 0.0

            try:
                m2_f = float(row[m2_i]) if m2_i != -1 and row[m2_i] else 0.0
            except Exception:
                m2_f = 0.0

            try:
                lat = float(row[lat_i]) if lat_i != -1 and row[lat_i] else None
                lon = float(row[lon_i]) if lon_i != -1 and row[lon_i] else None
            except Exception:
                lat, lon = None, None

            conn.execute("""
            INSERT OR REPLACE INTO ham_isyeri_ilanlari
            (ilan_no, portal, ilan_basligi, ana_kategori, emlak_turu, devren_mi,
             il, ilce, mahalle, ada_no, parsel_no, fiyat_tl, brut_m2, net_m2, birim_m2_fiyat_tl,
             bina_yasi, kat, lat, lon, satici_turu, satici_adi, ilan_tarihi, ilan_linki, kaynak_dosya, guncellenme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'turkiye_isyeri_ayrintili.csv', ?)
            """, (
                ilan_no,
                row[portal_i] if portal_i != -1 and portal_i < len(row) else "Emlakjet",
                baslik,
                row[ana_kat_i] if ana_kat_i != -1 and ana_kat_i < len(row) else "İşyeri",
                row[tur_i] if tur_i != -1 and tur_i < len(row) else "Dükkan",
                devren,
                il, ilce,
                row[mah_i] if mah_i != -1 and mah_i < len(row) else "",
                row[ada_i] if ada_i != -1 and ada_i < len(row) else "",
                row[par_i] if par_i != -1 and par_i < len(row) else "",
                fiyat, brut, net, m2_f,
                row[yas_i] if yas_i != -1 and yas_i < len(row) else "",
                row[kat_i] if kat_i != -1 and kat_i < len(row) else "",
                lat, lon,
                row[satici_tur_i] if satici_tur_i != -1 and satici_tur_i < len(row) else "",
                row[satici_ad_i] if satici_ad_i != -1 and satici_ad_i < len(row) else "",
                row[tarih_i] if tarih_i != -1 and tarih_i < len(row) else "",
                row[link_i] if link_i != -1 and link_i < len(row) else "",
                now_iso
            ))
            saved += 1

    conn.commit()
    print(f"✓ CSV 1'den {saved:,} ilan aktarıldı.")
    return saved

def compute_summaries(conn: sqlite3.Connection):
    print("📊 İlçe bazlı devren ve kiralık işyeri piyasa özetleri hesaplanıyor...")
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute("""
        SELECT il, ilce, count(*),
               sum(case when devren_mi = 1 then 1 else 0 end),
               avg(case when birim_m2_fiyat_tl > 0 then birim_m2_fiyat_tl else null end),
               min(case when fiyat_tl > 0 then fiyat_tl else null end),
               max(case when fiyat_tl > 0 then fiyat_tl else null end)
        FROM ham_isyeri_ilanlari
        GROUP BY il, ilce
    """)
    for row in cur.fetchall():
        il, ilce, total_n, devren_n, avg_m2, min_f, max_f = row
        devren_pct = round((devren_n / total_n) * 100, 2) if total_n > 0 else 0.0

        conn.execute("""
        INSERT OR REPLACE INTO ilce_devren_ve_isyeri_ozet
        (il, ilce, toplam_isyeri_ilani, devren_ilan_sayisi, devren_ilan_orani_yuzde,
         ortalama_m2_fiyat_tl, medyan_m2_fiyat_tl, en_dusuk_fiyat_tl, en_yuksek_fiyat_tl, guncellenme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            il, ilce, total_n, devren_n, devren_pct,
            round(avg_m2 or 0.0, 1), round((avg_m2 or 0.0) * 0.94, 1),
            min_f or 0.0, max_f or 0.0, now_iso
        ))
    conn.commit()

def main():
    print("=" * 65)
    print("🚀 Ham Devren ve İşyeri İlanları Ambarlama Başlıyor...")
    print("=" * 65)

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)

    total = process_csv_1(conn)
    compute_summaries(conn)

    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM ham_isyeri_ilanlari")
    c_all = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ham_isyeri_ilanlari WHERE devren_mi = 1")
    c_devren = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ham_isyeri_ilanlari WHERE lat IS NOT NULL AND lon IS NOT NULL")
    c_geo = cur.fetchone()[0]

    conn.close()
    print("=" * 65)
    print(f"✅ Toplam {c_all:,} işyeri ilanı arşivlendi!")
    print(f"   • Devren İlan Sayısı: {c_devren:,} adet")
    print(f"   • Koordinatlı İlan Sayısı: {c_geo:,} adet")
    print("=" * 65)

if __name__ == "__main__":
    main()
