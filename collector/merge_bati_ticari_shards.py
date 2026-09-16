"""40 sanal makinenin ürettiği Batı Büyükşehirleri ticari shard sqlite dosyalarını tek ambar DB'sinde birleştirir.

Kullanım:
    python collector/merge_bati_ticari_shards.py \
        --shards "artifacts/**/shard_*.sqlite" \
        --out warehouse/product/bati_ticari_istihbarat.sqlite
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bati_buyuksehirler_ticari_toplayici import init_schema  # noqa: E402


def merge(shard_paths: list[str], out_db: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_db)), exist_ok=True)
    connection = sqlite3.connect(out_db)
    init_schema(connection)

    tables = [
        "istasyon_yolcu_akisi",
        "ticari_koridor_ve_aks",
        "isletme_turnover_ve_stabilite",
        "ticari_kira_ve_devren_piyasa",
        "bkm_sektorel_kart_harcama",
        "mikro_ticari_ciro_skoru"
    ]

    total_shards = 0
    for path in shard_paths:
        if not os.path.exists(path):
            continue
        try:
            connection.execute("ATTACH DATABASE ? AS shard", (path,))
            for tbl in tables:
                connection.execute(f"INSERT OR REPLACE INTO {tbl} SELECT * FROM shard.{tbl}")
            connection.commit()
            connection.execute("DETACH DATABASE shard")
            total_shards += 1
            print(f"✓ Birleştirildi: {os.path.basename(path)}")
        except Exception as e:
            print(f"[UYARI] {path} birleştirilirken hata: {e}")

    connection.execute("ANALYZE")
    connection.commit()

    print(f"\nToplam {total_shards} shard dosyası '{out_db}' ambarında başarıyla birleştirildi.")
    cur = connection.cursor()
    for tbl in tables:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  - {tbl:32}: {cur.fetchone()[0]:,} kayıt")
    connection.close()


def main():
    parser = argparse.ArgumentParser(description="Batı Ticari Shard Birleştirici")
    parser.add_argument("--shards", type=str, default="artifacts/**/shard_*.sqlite", help="Glob deseni")
    parser.add_argument("--out", type=str, default="warehouse/product/bati_ticari_istihbarat.sqlite", help="Hedef DB")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.shards, recursive=True))
    if not paths:
        # Fallback: doğrudan geçerli klasörde shard_*.sqlite ara
        paths = sorted(glob.glob("shard_*.sqlite"))

    if not paths:
        print(f"Birleştirilecek shard bulunamadı ({args.shards}).")
        return

    merge(paths, args.out)


if __name__ == "__main__":
    main()
