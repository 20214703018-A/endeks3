#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Google Places Shard Birleştirici
40 sanal makinenin ürettiği shard_*.sqlite dosyalarını tek merkezî ambar DB'sinde birleştirir.
"""

import os
import glob
import sqlite3
import argparse

DEFAULT_OUT = "warehouse/product/google_places_ve_yogunluk.sqlite"

def init_schema(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_ticari_yogunluk (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_place_id TEXT UNIQUE,
        cid TEXT,
        isim TEXT NOT NULL,
        arama_terimi TEXT,
        ana_kategori TEXT,
        tum_kategoriler TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        degerlendirme_sayisi INTEGER,
        yildiz_dagilimi TEXT,
        tam_adres TEXT,
        mahalle TEXT,
        ilce TEXT,
        il TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        maps_url TEXT,
        kaynak TEXT DEFAULT 'Google Maps',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    try:
        cur.execute("ALTER TABLE google_places_ticari_yogunluk ADD COLUMN degerlendirme_sayisi INTEGER")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE google_places_ticari_yogunluk ADD COLUMN yildiz_dagilimi TEXT")
    except Exception:
        pass
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_ilce ON google_places_ticari_yogunluk(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_coords ON google_places_ticari_yogunluk(lat, lon)")
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
            try:
                conn.execute("ALTER TABLE shard.google_places_ticari_yogunluk ADD COLUMN degerlendirme_sayisi INTEGER")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE shard.google_places_ticari_yogunluk ADD COLUMN yildiz_dagilimi TEXT")
            except Exception:
                pass

            conn.execute("""
            INSERT OR REPLACE INTO google_places_ticari_yogunluk (
                google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
                puan, yorum_sayisi, degerlendirme_sayisi, yildiz_dagilimi, tam_adres, mahalle, ilce, il,
                lat, lon, telefon, calisma_saatleri, maps_url, kaynak, guncellenme_tarihi
            ) SELECT 
                google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
                puan, yorum_sayisi, COALESCE(degerlendirme_sayisi, yorum_sayisi), yildiz_dagilimi, tam_adres, mahalle, ilce, il,
                lat, lon, telefon, calisma_saatleri, maps_url, kaynak, guncellenme_tarihi
            FROM shard.google_places_ticari_yogunluk
            """)
            conn.commit()
            conn.execute("DETACH DATABASE shard")
            merged += 1
            print(f"✓ Birleştirildi: {os.path.basename(path)}")
        except Exception as e:
            print(f"[HATA] {path}: {e}")
            try:
                conn.execute("DETACH DATABASE shard")
            except Exception:
                pass

    conn.execute("ANALYZE")
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM google_places_ticari_yogunluk").fetchone()[0]
    print(f"\nToplam {merged} shard birleştirildi. Ambar toplamı: {count:,} mekan.")
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="Google Places Shard Birleştirici")
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
