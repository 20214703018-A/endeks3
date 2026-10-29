"""Market Fiyatı şube kataloğu — günlük dönen dilim (29 Eki 2026).

Her gün en uzun süredir kataloğu çekilmemiş N şube seçilir (hiç çekilmemiş olanlar önce); 20 makine bu dilimi
intake_marketfiyati.py branches ile toplar. Böylece her şubenin tam ürün/fiyat kataloğu yaklaşık iki haftada bir
yenilenir ve şube×ürün fiyatları kronolojik seri olur (hiçbir eski çekim silinmez; her gün ayrı tarihli dosya).

  sec    --depots F --durum D --n 1000 --out todo.parquet   # dilimi seç
  durum  --durum D --parcalar DIR --out D2                   # bu koşuda çekilen şubeleri durum dosyasına işle
Durum dosyası: depot_id, son_cekim (ISO 8601), son_urun (o çekimde ürün sayısı), cekim_sayisi.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd


def sec(a):
    dep = pd.read_parquet(a.depots)
    if Path(a.durum).exists():
        d = pd.read_parquet(a.durum)[["depot_id", "son_cekim"]]
        dep = dep.merge(d, on="depot_id", how="left")
    else:
        dep["son_cekim"] = None
    # hiç çekilmemiş → en başta; sonra en eski çekim. Eşitlikte il önceliği yok (her gün karışık iller, sunucuya yük dağılır)
    dep["_sira"] = dep.son_cekim.fillna("0000")
    dep["_karisik"] = pd.util.hash_pandas_object(dep.depot_id, index=False).values   # sabit ama zincir/il karışık sıra
    todo = dep.sort_values(["_sira", "_karisik"]).head(a.n).drop(columns=["_sira", "_karisik"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    todo.to_parquet(a.out, index=False)
    print(f"şube listesi {len(dep)} · hiç çekilmemiş {dep.son_cekim.isna().sum()} · bu dilim {len(todo)} "
          f"(en eski çekim {todo.son_cekim.dropna().min() if todo.son_cekim.notna().any() else '-'})")


def durum(a):
    rows = []
    for p in Path(a.parcalar).rglob("depot_*.jsonl.gz"):
        with gzip.open(p, "rt") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                rows.append({"depot_id": d["depot_id"], "son_cekim": d["at"], "son_urun": d.get("n_items")})
    yeni = pd.DataFrame(rows, columns=["depot_id", "son_cekim", "son_urun"])
    yeni = yeni.sort_values("son_cekim").drop_duplicates("depot_id", keep="last")
    if Path(a.durum).exists():
        eski = pd.read_parquet(a.durum)
    else:
        eski = pd.DataFrame(columns=["depot_id", "son_cekim", "son_urun", "cekim_sayisi"])
    eski = eski.set_index("depot_id")
    for r in yeni.itertuples():
        n = int(eski.at[r.depot_id, "cekim_sayisi"]) + 1 if r.depot_id in eski.index and pd.notna(eski.at[r.depot_id, "cekim_sayisi"]) else 1
        eski.loc[r.depot_id, ["son_cekim", "son_urun", "cekim_sayisi"]] = [r.son_cekim, r.son_urun, n]
    eski.reset_index().to_parquet(a.out, index=False)
    print(f"bu koşuda çekilen şube {len(yeni)} · durum dosyasında {len(eski)} şube")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("islem", choices=["sec", "durum"])
    ap.add_argument("--depots"); ap.add_argument("--durum", required=True); ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--parcalar"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sec(a) if a.islem == "sec" else durum(a)
