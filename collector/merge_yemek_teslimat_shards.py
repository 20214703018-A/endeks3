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

try:
    from collector.yemek_teslimat_ekosistemi_toplayici import init_db
except ImportError:
    from yemek_teslimat_ekosistemi_toplayici import init_db

DEFAULT_OUT = "warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite"

def table_exists(conn, schema, table):
    return conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def merge_common_columns(conn, table, mode="OR REPLACE"):
    if not table_exists(conn, "shard", table):
        return
    target = [row[1] for row in conn.execute(f"PRAGMA main.table_info({table})") if row[1] != "id"]
    source = {row[1] for row in conn.execute(f"PRAGMA shard.table_info({table})")}
    common = [column for column in target if column in source]
    if not common:
        return
    quoted = ", ".join(f'"{column}"' for column in common)
    conn.execute(
        f"INSERT {mode} INTO main.{table} ({quoted}) SELECT {quoted} FROM shard.{table}"
    )

def merge(shard_paths, out_db):
    os.makedirs(os.path.dirname(os.path.abspath(out_db)), exist_ok=True)
    init_db(out_db)
    conn = sqlite3.connect(out_db)

    merged = 0
    for path in shard_paths:
        if not os.path.exists(path):
            continue
        try:
            conn.execute("ATTACH DATABASE ? AS shard", (path,))
            merge_common_columns(conn, "teslimat_depolari_darkstore")
            merge_common_columns(conn, "uye_restoranlar_ve_hacim")
            merge_common_columns(conn, "mahalle_esnaf_noktalari")
            merge_common_columns(conn, "yemeksepeti_isletme_gozlem", "OR IGNORE")
            merge_common_columns(conn, "yemeksepeti_tarama_gecmisi")
                
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
    count_ds = conn.execute("SELECT COUNT(*) FROM teslimat_depolari_darkstore").fetchone()[0]
    count_rst = conn.execute("SELECT COUNT(*) FROM uye_restoranlar_ve_hacim").fetchone()[0]
    count_esn = conn.execute("SELECT COUNT(*) FROM mahalle_esnaf_noktalari").fetchone()[0]
    print(f"\nToplam {merged} shard birleştirildi.")
    print(f"  - Darkstore / Teslimat Depoları: {count_ds:,} kayıt")
    print(f"  - Üye Restoranlar: {count_rst:,} kayıt")
    print(f"  - Mahalle Esnafı (Bakkal/Manav vb.): {count_esn:,} kayıt")
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
