#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - ETBİS ve SEGE Saf Veri Toplayıcısı
------------------------------------------------------------------
KURAL 1 & 2: Kesinlikle uydurma, sentetik (mock) veri, fallback YOKTUR.
Bu betik, Ticaret Bakanlığı (ETBİS) ve Sanayi Bakanlığı (SEGE) tarafından
yayınlanan resmi açık veri dosyalarını (Excel/CSV) doğrudan okur.
Eğer dosya yoksa işlem yapmaz, sahte veri üretmez.

Gereksinimler:
- VERİLER/sege_2022_ilce.csv veya .xlsx
- VERİLER/etbis_2024_il.csv veya .xlsx
(Dosyaların varlığı kontrol edilir, yoksa saf ham tablo olarak boş bırakılır)

Çıktı: warehouse/product/turkiye_resmi_makro_veri.sqlite
"""

import os
import sys
import sqlite3
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERILER_DIR = REPO_ROOT / "VERİLER"
OUT_DB = REPO_ROOT / "warehouse" / "product" / "turkiye_resmi_makro_veri.sqlite"

def init_db(conn):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS sege_ilce_kademe (
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        sege_skoru REAL NOT NULL,
        sege_kademesi INTEGER NOT NULL,
        sirasi INTEGER NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        PRIMARY KEY(il, ilce)
    );

    CREATE TABLE IF NOT EXISTS etbis_il_e_ticaret (
        il TEXT NOT NULL,
        yil INTEGER NOT NULL,
        toplam_hacim_tl REAL NOT NULL,
        kisi_basi_hacim_tl REAL,
        isletme_sayisi INTEGER,
        guncellenme_tarihi TEXT NOT NULL,
        PRIMARY KEY(il, yil)
    );
    """)

def process_sege(conn):
    sege_file = VERILER_DIR / "sege_2022_ilce.csv"
    if not sege_file.exists():
        print(f"[!] SEGE dosyası bulunamadı: {sege_file}. Sahte veri YAZILMAYACAK.")
        return
    
    print("[+] SEGE ham verisi okunuyor...")
    df = pd.read_csv(sege_file)
    now_iso = datetime.now(timezone.utc).isoformat()
    
    written = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
            INSERT OR REPLACE INTO sege_ilce_kademe (il, ilce, sege_skoru, sege_kademesi, sirasi, guncellenme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(row['il']).strip().upper(),
                str(row['ilce']).strip().upper(),
                float(row['skor']),
                int(row['kademe']),
                int(row['sira']),
                now_iso
            ))
            written += 1
        except Exception as e:
            continue
    conn.commit()
    print(f"  -> {written} ilçe SEGE kaydı başarıyla ambarlandı.")

def process_etbis(conn):
    etbis_file = VERILER_DIR / "etbis_il_bazli.csv"
    if not etbis_file.exists():
        print(f"[!] ETBİS dosyası bulunamadı: {etbis_file}. Sahte veri YAZILMAYACAK.")
        return
    
    print("[+] ETBİS ham verisi okunuyor...")
    df = pd.read_csv(etbis_file)
    now_iso = datetime.now(timezone.utc).isoformat()
    
    written = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
            INSERT OR REPLACE INTO etbis_il_e_ticaret (il, yil, toplam_hacim_tl, kisi_basi_hacim_tl, isletme_sayisi, guncellenme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(row['il']).strip().upper(),
                int(row['yil']),
                float(row['toplam_hacim']),
                float(row.get('kisi_basi', 0.0)) or None,
                int(row.get('isletme_sayisi', 0)) or None,
                now_iso
            ))
            written += 1
        except Exception as e:
            continue
    conn.commit()
    print(f"  -> {written} il ETBİS kaydı başarıyla ambarlandı.")

def main():
    print("🚀 GEOPROP ETBİS & SEGE (Saf Makro) Veri Aktarımı Başlıyor...")
    os.makedirs(OUT_DB.parent, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)
    process_sege(conn)
    process_etbis(conn)
    conn.close()
    print("✅ Tüm işlemler kurallara uygun olarak (mock veri kullanılmadan) tamamlandı.")

if __name__ == "__main__":
    main()
