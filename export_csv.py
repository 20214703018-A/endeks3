#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite Verilerini CSV Formatına Dışa Aktarma Aracı
--------------------------------------------------
İl, İlçe ve Mahalle düzeyindeki tüm tabloları Excel uyumlu (UTF-8 with BOM)
CSV formatında dışa aktarır.
"""

import csv
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "piyasa_verileri.db"
CSV_OUT_DIR = DATA_DIR / "csv_ciktilari"

TABLOLAR = {
    "demografi": "01_demografi_ve_nufus",
    "yillik_satislar": "02_yillik_satislar_2010_2024",
    "fiyat_ozet": "03_fiyat_ozet_konut_arsa",
    "fiyat_trend": "04_aylik_fiyat_trendi_2021_2026",
    "fiyat_dagilim": "05_oda_yas_kat_isitma_kirilimlari",
    "hemsehri": "06_hemsehri_kutuk_dagilimi",
    "secim_sonuclari": "07_secim_sonuclari_ve_oylar",
    "poi_noktalari": "08_ilce_onemli_noktalar_poi",
    "emlak_ofisleri": "09_emlak_ofisleri_rehberi",
    "danismanlar": "10_gayrimenkul_danismanlari",
    "sirketler": "11_insaat_ve_proje_sirketleri",
    "iller": "12_iller_listesi",
    "ilceler": "13_ilceler_listesi",
    "mahalleler": "14_mahalleler_listesi",
}

def export():
    if not DB_PATH.exists():
        print(f"HATA: Veritabanı bulunamadı: {DB_PATH}")
        return

    CSV_OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("Veritabanı tabloları CSV formatına aktarılıyor...")

    for tablo, dosya_adi in TABLOLAR.items():
        try:
            cur.execute(f"PRAGMA table_info({tablo})")
            kolonlar = [row[1] for row in cur.fetchall()]
            if not kolonlar: continue

            secili = [k for k in kolonlar if k not in ("ham_json", "parti_sonuclari_json")]
            cur.execute(f"SELECT {', '.join(secili)} FROM {tablo}")
            satirlar = cur.fetchall()

            hedef_csv = CSV_OUT_DIR / f"{dosya_adi}.csv"
            with open(hedef_csv, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(secili)
                writer.writerows(satirlar)

            print(f"  ✓ {dosya_adi}.csv ({len(satirlar)} kayıt)")
        except Exception as e:
            print(f"  ✗ {tablo} aktarılırken hata: {e}")

    conn.close()
    print(f"\nTüm CSV dosyaları hazırlandı: {CSV_OUT_DIR}")

if __name__ == "__main__":
    export()
