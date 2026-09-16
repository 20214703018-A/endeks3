#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Yemek ve Market Teslimat Ekosistemi Shard Birleştirici
40 sanal makinenin ürettiği shard_*.sqlite dosyalarını tek ambar DB'sinde birleştirir.
"""

import os
import glob
import sqlite3
import argparse

DEFAULT_OUT = "warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite"

def init_schema(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS teslimat_depolari_darkstore (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        depo_kodu TEXT UNIQUE,
        depo_adi TEXT NOT NULL,
        sehir TEXT,
        ilce TEXT,
        mahalle TEXT,
        tam_adres TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        url TEXT,
        kaynak TEXT DEFAULT 'Resmî Platform Sitemap & JSON-LD',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_darkstore_sehir ON teslimat_depolari_darkstore(sehir, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_darkstore_coords ON teslimat_depolari_darkstore(lat, lon)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS uye_restoranlar_ve_hacim (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        restoran_kodu TEXT UNIQUE,
        restoran_adi TEXT NOT NULL,
        mutfaklar TEXT,
        fiyat_segmenti TEXT,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        sehir TEXT,
        ilce TEXT,
        tam_adres TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        url TEXT,
        kaynak TEXT DEFAULT 'Yemeksepeti Restoran Ekosistemi',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_sehir ON uye_restoranlar_ve_hacim(sehir, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_coords ON uye_restoranlar_ve_hacim(lat, lon)")
    conn.commit()

def merge(shard_paths, out_db):
    os.makedirs(os.path.dirname(os.path.abspath(out_db)), exist_ok=True)
    conn = sqlite3.connect(out_db)
    init_schema(conn)

    merged = 0
    for path in shard_paths:
        if not os.path.exists(path):
            continue
        try:
            conn.execute("ATTACH DATABASE ? AS shard", (path,))
            conn.execute("""
            INSERT OR REPLACE INTO teslimat_depolari_darkstore (
                platform, depo_kodu, depo_adi, sehir, ilce, mahalle, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
            ) SELECT 
                platform, depo_kodu, depo_adi, sehir, ilce, mahalle, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
            FROM shard.teslimat_depolari_darkstore
            """)
            
            # Eğer restoron tablosu varsa birleştir
            has_rest = conn.execute("SELECT count(*) FROM shard.sqlite_master WHERE type='table' AND name='uye_restoranlar_ve_hacim'").fetchone()[0]
            if has_rest:
                conn.execute("""
                INSERT OR REPLACE INTO uye_restoranlar_ve_hacim (
                    platform, restoran_kodu, restoran_adi, mutfaklar, fiyat_segmenti,
                    puan, degerlendirme_sayisi, sehir, ilce, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
                ) SELECT 
                    platform, restoran_kodu, restoran_adi, mutfaklar, fiyat_segmenti,
                    puan, degerlendirme_sayisi, sehir, ilce, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
                FROM shard.uye_restoranlar_ve_hacim
                """)
                
            conn.commit()
            conn.execute("DETACH DATABASE shard")
            merged += 1
            print(f"✓ Birleştirildi: {os.path.basename(path)}")
        except Exception as e:
            print(f"[HATA] {path}: {e}")

    conn.execute("ANALYZE")
    conn.commit()
    count_ds = conn.execute("SELECT COUNT(*) FROM teslimat_depolari_darkstore").fetchone()[0]
    count_rst = conn.execute("SELECT COUNT(*) FROM uye_restoranlar_ve_hacim").fetchone()[0]
    print(f"\nToplam {merged} shard birleştirildi.")
    print(f"  - Darkstore / Teslimat Depoları: {count_ds:,} kayıt")
    print(f"  - Üye Restoranlar: {count_rst:,} kayıt")
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="Yemek ve Market Teslimat Shard Birleştirici")
    parser.add_argument("--shards", type=str, default="artifacts/**/shard_*.sqlite", help="Glob deseni")
    parser.add_argument("--out", type=str, default=DEFAULT_OUT, help="Hedef DB")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.shards, recursive=True))
    if not paths:
        paths = sorted(glob.glob("shard_*.sqlite"))
    if not paths:
        print("Birleştirilecek shard bulunamadı.")
        return
    merge(paths, args.out)

if __name__ == "__main__":
    main()
