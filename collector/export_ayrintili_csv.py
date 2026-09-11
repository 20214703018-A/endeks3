#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Ayrıntılı Gayrimenkul CSV Dışa Aktarıcı & 40 Makine Konsolidatörü
========================================================================
1. SQLite veritabanındaki ilanları (Ev, Arsa, Dükkan) en ayrıntılı mahalle,
   GPS (Lat/Lon) ve Ada/Parsel bilgileriyle Excel uyumlu UTF-8 CSV'lere döker.
2. GitHub Actions 40 makine paralel çıktısını (--birlestir-dizin) tek bir veritabanında toplar.
"""

import sys
import glob
import tarfile
import sqlite3
import argparse
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "turkiye_tum_ilanlar.sqlite"
CSV_DIR = DATA_DIR / "csv_ciktilari"

def merge_all_groups(input_dir):
    """GitHub Actions 40 runner'ından gelen tüm SQLite dosyalarını birleştirir"""
    print("=" * 70)
    print(f"📦 40 MAKİNE VERİ KONSOLİDASYONU BAŞLATILIYOR: {input_dir}")
    print("=" * 70)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    # Ana tabloyu oluştur
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ilanlar (
      ilan_id TEXT PRIMARY KEY,
      kaynak TEXT,
      kategori TEXT,
      alt_tip TEXT,
      baslik TEXT,
      il TEXT,
      ilce TEXT,
      mahalle TEXT,
      mahalle_id INTEGER,
      resmi_mahalle_id TEXT,
      ada TEXT,
      parsel TEXT,
      imar_durumu TEXT,
      tapu_durumu TEXT,
      kaks_emsal TEXT,
      fiyat_tl INTEGER,
      para_birimi TEXT DEFAULT 'TL',
      m2_brut REAL,
      m2_net REAL,
      birim_fiyat REAL,
      oda_sayisi TEXT,
      bina_yasi TEXT,
      kat TEXT,
      lat REAL,
      lon REAL,
      koordinat_tipi TEXT,
      satici_turu TEXT,
      satici_adi TEXT,
      satici_telefon TEXT,
      tarih TEXT,
      gorsel_url TEXT,
      url TEXT,
      eklenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Tarball dosyalarını aç
    tar_files = glob.glob(f"{input_dir}/**/*.tar.gz", recursive=True)
    print(f"📁 Bulunan grup arşiv paketi: {len(tar_files)}")
    for tf in tar_files:
        try:
            with tarfile.open(tf, "r:gz") as t:
                t.extractall("temp_ext")
        except Exception as e:
            print(f"⚠️ Arşiv açma uyarısı ({tf}): {e}")

    # Tüm SQLite dosyalarını tara ve birleştir
    db_candidates = (
        glob.glob(f"{input_dir}/**/*.sqlite", recursive=True) +
        glob.glob(f"{input_dir}/**/*.db", recursive=True) +
        glob.glob("temp_ext/**/*.sqlite", recursive=True) +
        glob.glob("temp_ext/**/*.db", recursive=True)
    )

    db_candidates = list(set([os_p for os_p in db_candidates if Path(os_p).resolve() != DB_PATH.resolve()]))
    print(f"🔍 Birleştirilecek veritabanı sayısı: {len(db_candidates)}")

    toplam_eklenen = 0
    for db_path in db_candidates:
        try:
            c2 = sqlite3.connect(db_path)
            cur2 = c2.cursor()
            
            # Kolon listesini al
            cur2.execute("PRAGMA table_info(ilanlar);")
            cols = [r[1] for r in cur2.fetchall()]
            if not cols:
                c2.close()
                continue

            col_str = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))

            cur2.execute(f"SELECT {col_str} FROM ilanlar;")
            rows = cur2.fetchall()
            
            if rows:
                cur.executemany(f"INSERT OR REPLACE INTO ilanlar ({col_str}) VALUES ({placeholders})", rows)
                toplam_eklenen += len(rows)
            c2.close()
        except Exception as e:
            print(f"⚠️ DB okuma hatası ({db_path}): {e}")

    conn.commit()
    cur.execute("SELECT count(*) FROM ilanlar;")
    genel_toplam = cur.fetchone()[0]
    conn.close()

    print(f"✅ Konsolidasyon tamamlandı! Bu oturumda birleştirilen: {toplam_eklenen:,} satır.")
    print(f"💎 Veritabanındaki Güncel Toplam Tekil İlan: {genel_toplam:,}")
    print("=" * 70)

def export_all():
    if not DB_PATH.exists():
        print(f"❌ Veritabanı bulunamadı: {DB_PATH}")
        return

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Kolon göçü kontrolü
    cur.execute("PRAGMA table_info(ilanlar);")
    cols = [r[1] for r in cur.fetchall()]
    new_cols = {
        "resmi_mahalle_id": "TEXT",
        "ada": "TEXT",
        "parsel": "TEXT",
        "imar_durumu": "TEXT",
        "tapu_durumu": "TEXT",
        "kaks_emsal": "TEXT",
        "koordinat_tipi": "TEXT"
    }
    for c_name, c_type in new_cols.items():
        if c_name not in cols:
            cur.execute(f"ALTER TABLE ilanlar ADD COLUMN {c_name} {c_type};")
    conn.commit()

    categories = [
        ("konut", "turkiye_konut_ayrintili.csv", "Ev / Konut İlanları"),
        ("arsa", "turkiye_arsa_ayrintili.csv", "Arsa / Tarla İlanları"),
        ("isyeri", "turkiye_isyeri_ayrintili.csv", "Dükkan / İşyeri İlanları")
    ]

    print("=" * 70)
    print("📊 AYRINTILI GAYRİMENKUL CSV DIŞA AKTARIMI (EXCEL UYUMLU UTF-8 BOM)")
    print("=" * 70)

    column_mapping = {
        "ilan_id": "İlan No",
        "kaynak": "Portal",
        "kategori": "Ana Kategori",
        "alt_tip": "Emlak Türü",
        "baslik": "İlan Başlığı",
        "il": "İl",
        "ilce": "İlçe",
        "mahalle": "Mahalle",
        "mahalle_id": "Portal Mahalle ID",
        "resmi_mahalle_id": "Resmi TKGM Mahalle ID",
        "ada": "Ada No",
        "parsel": "Parsel No",
        "imar_durumu": "İmar Durumu",
        "tapu_durumu": "Tapu Durumu",
        "kaks_emsal": "KAKS / Emsal",
        "fiyat_tl": "Fiyat (TL)",
        "para_birimi": "Para Birimi",
        "m2_brut": "Brüt m²",
        "m2_net": "Net m²",
        "birim_fiyat": "m² Birim Fiyatı (TL)",
        "oda_sayisi": "Oda Sayısı",
        "bina_yasi": "Bina Yaşı",
        "kat": "Bulunduğu Kat",
        "lat": "Enlem (Latitude)",
        "lon": "Boylam (Longitude)",
        "koordinat_tipi": "Koordinat Hassasiyeti",
        "satici_turu": "Satıcı Türü",
        "satici_adi": "Satıcı / Ofis Adı",
        "satici_telefon": "İletişim Telefonu",
        "tarih": "İlan Tarihi",
        "url": "İlan Linki",
        "gorsel_url": "Kapak Fotoğrafı",
        "eklenme_tarihi": "Veritabanı Kayıt Zamanı"
    }

    for kat, filename, label in categories:
        query = f"""
            SELECT 
                ilan_id, kaynak, kategori, alt_tip, baslik,
                il, ilce, mahalle, mahalle_id, resmi_mahalle_id,
                ada, parsel, imar_durumu, tapu_durumu, kaks_emsal,
                fiyat_tl, para_birimi, m2_brut, m2_net, birim_fiyat,
                oda_sayisi, bina_yasi, kat, lat, lon, koordinat_tipi,
                satici_turu, satici_adi, satici_telefon,
                tarih, url, gorsel_url, eklenme_tarihi
            FROM ilanlar
            WHERE kategori = '{kat}'
            ORDER BY il, ilce, mahalle, fiyat_tl DESC;
        """
        df = pd.read_sql_query(query, conn)

        if df.empty:
            print(f"⚪ {label}: Kayıt bulunamadı, atlandı.")
            continue

        df.rename(columns=column_mapping, inplace=True)
        out_file = CSV_DIR / filename
        df.to_csv(out_file, index=False, sep=";", encoding="utf-8-sig")

        total = len(df)
        mahalle_count = df["Mahalle"].nunique() if "Mahalle" in df else 0
        coord_count = df["Enlem (Latitude)"].notna().sum() if "Enlem (Latitude)" in df else 0
        coord_pct = round((coord_count / total) * 100, 1) if total > 0 else 0
        kesin_count = (df["Koordinat Hassasiyeti"] == "KESIN_PIN").sum() if "Koordinat Hassasiyeti" in df else 0
        ada_count = df["Ada No"].notna().sum() if "Ada No" in df else 0

        print(f"✅ {label}: {out_file.name}")
        print(f"   ├─ Toplam İlan: {total:,}")
        print(f"   ├─ Kapsanan Mahalle Sayısı: {mahalle_count:,}")
        print(f"   ├─ Harita GPS Pini (Tam Koordinat): {kesin_count:,} (%{round((kesin_count/total)*100, 1) if total>0 else 0})")
        print(f"   ├─ Toplam Koordinatlı İlan: {coord_count:,} (%{coord_pct})")
        if ada_count > 0:
            print(f"   └─ Ada / Parsel Tespit Edilen: {ada_count:,}")
        else:
            print(f"   └─ Ada / Parsel: -")

    conn.close()
    print("=" * 70)
    print(f"📁 Tüm CSV dosyaları hazır: {CSV_DIR}")
    print("=" * 70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ayrıntılı CSV Dışa Aktarıcı & Veri Birleştirici")
    parser.add_argument("--birlestir-dizin", type=str, default=None, help="Birleştirilecek grup dosyalarının bulunduğu dizin")
    args = parser.parse_args()

    if args.birlestir_dizin:
        merge_all_groups(args.birlestir_dizin)

    export_all()
