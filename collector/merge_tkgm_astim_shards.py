"""40 makinenin ürettiği shard sqlite dosyalarını tek ambar DB'sinde birleştirir.

Shard'lar illere göre ayrık bölündüğü ve parsel_id global benzersiz olduğu için
çakışma olmaz; INSERT OR REPLACE ile güvenle birleştirilir.

Kullanım:
    python collector/merge_tkgm_astim_shards.py \
        --shards "artifacts/**/shard_*.sqlite" \
        --out warehouse/product/tkgm_alim_satim.sqlite
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tkgm_alim_satim_yogunlugu_toplayici import init_schema  # noqa: E402


def merge(shard_paths: list[str], out_db: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_db)), exist_ok=True)
    connection = sqlite3.connect(out_db)
    init_schema(connection)
    total_rows = 0
    for path in shard_paths:
        if not os.path.exists(path):
            print(f"atla (yok): {path}")
            continue
        connection.execute("ATTACH DATABASE ? AS shard", (path,))
        before = connection.execute(
            "SELECT COUNT(*) FROM tkgm_alim_satim_yogunlugu"
        ).fetchone()[0]
        connection.execute(
            "INSERT OR REPLACE INTO tkgm_alim_satim_yogunlugu "
            "SELECT * FROM shard.tkgm_alim_satim_yogunlugu"
        )
        connection.execute(
            "INSERT OR REPLACE INTO tkgm_analiz_kapsama "
            "SELECT * FROM shard.tkgm_analiz_kapsama"
        )
        connection.commit()
        after = connection.execute(
            "SELECT COUNT(*) FROM tkgm_alim_satim_yogunlugu"
        ).fetchone()[0]
        connection.execute("DETACH DATABASE shard")
        print(f"birleştirildi: {os.path.basename(path)} (+{after - before:,} satır)")
        total_rows = after
    # Sorgu hızını korumak için birleşme sonrası analiz.
    connection.execute("ANALYZE")
    connection.commit()
    coverage = connection.execute(
        "SELECT COUNT(*), SUM(nokta_sayisi), SUM(toplam_islem) FROM tkgm_analiz_kapsama"
    ).fetchone()
    connection.close()
    print(f"\nToplam {total_rows:,} satır. Kapsama: {coverage[0]} (tip,il,yıl) "
          f"kombinasyonu, {coverage[1] or 0:,} nokta, {coverage[2] or 0:,} işlem.")
    print(f"Ambar: {out_db}")


def main() -> int:
    parser = argparse.ArgumentParser(description="TKGM alım-satım shard birleştirici")
    parser.add_argument("--shards", required=True,
                        help="Shard sqlite dosyaları için glob deseni (tırnak içinde)")
    parser.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "warehouse", "product", "tkgm_alim_satim.sqlite"))
    args = parser.parse_args()

    paths = sorted(glob.glob(args.shards, recursive=True))
    if not paths:
        print(f"Uyarı: '{args.shards}' desenine uyan shard bulunamadı.", file=sys.stderr)
        return 1
    print(f"{len(paths)} shard bulundu.")
    merge(paths, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
