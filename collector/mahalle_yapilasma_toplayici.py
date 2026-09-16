"""Mahalle yapılaşma/konut profili toplayıcısı.

Kaynak: tüm ilanlar ambarındaki KONUT kayıtları (kat, m2_brut/net, oda_sayisi).
Mahalle (il+ilçe+mahalle adı) bazında ortalama konut m², kat (ortalama/maks) ve
oda dağılımını üretip ürün ambarına yazar. Not: kat, dairenin bulunduğu kattır
(binanın toplam kat sayısı değil); yine de yapılaşma yüksekliği için göstergedir.
Bina yaşı kaynak veride boş olduğundan üretilmez.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(
    REPO, "VERİLER", "Öğelerle Yeni Klasör 2", "tum_turkiye_nihai_paket",
    "turkiye_tum_ilanlar.sqlite",
)
DEFAULT_OUT = os.path.join(REPO, "warehouse", "product", "mahalle_yapilasma.sqlite")

_FLOOR_RE = re.compile(r"^\s*(\d{1,2})\.\s*Kat", re.IGNORECASE)


def parse_floor(value: str):
    if not value:
        return None
    m = _FLOOR_RE.match(value)
    if m:
        return int(m.group(1))
    low = value.strip().lower()
    if low.startswith(("zemin", "düz giriş", "duz giris", "giriş", "yüksek giriş", "yuksek giris")):
        return 0
    if low.startswith("bahçe") or low.startswith("bahce"):
        return 0
    return None


def build(source: str, out: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        """
        SELECT il, ilce, mahalle, kat, m2_brut, m2_net, oda_sayisi
        FROM ilanlar
        WHERE kategori='konut' AND il IS NOT NULL AND ilce IS NOT NULL AND mahalle IS NOT NULL
        """
    ).fetchall()
    src.close()

    agg: dict[tuple, dict] = {}
    for r in rows:
        key = (r["il"].strip(), r["ilce"].strip(), r["mahalle"].strip())
        a = agg.setdefault(key, {"m2b": [], "m2n": [], "floors": [], "oda": {}})
        m2b = r["m2_brut"]
        if isinstance(m2b, (int, float)) and m2b > 0:
            a["m2b"].append(float(m2b))
        m2n = r["m2_net"]
        if isinstance(m2n, (int, float)) and m2n > 0:
            a["m2n"].append(float(m2n))
        fl = parse_floor(r["kat"])
        if fl is not None:
            a["floors"].append(fl)
        oda = (r["oda_sayisi"] or "").strip()
        if oda:
            a["oda"][oda] = a["oda"].get(oda, 0) + 1

    out_conn = sqlite3.connect(out)
    out_conn.executescript(
        """
        DROP TABLE IF EXISTS mahalle_yapilasma_ozet;
        CREATE TABLE mahalle_yapilasma_ozet (
            il TEXT, ilce TEXT, mahalle TEXT,
            konut_ilan INTEGER,
            ort_m2_brut REAL, medyan_m2_net REAL,
            ort_kat REAL, max_kat INTEGER,
            oda_dagilimi TEXT,
            PRIMARY KEY (il, ilce, mahalle)
        );
        """
    )
    written = 0
    for (il, ilce, mahalle), a in agg.items():
        if not a["m2b"]:
            continue
        oda_top = sorted(a["oda"].items(), key=lambda kv: -kv[1])[:5]
        out_conn.execute(
            "INSERT OR REPLACE INTO mahalle_yapilasma_ozet VALUES (?,?,?,?,?,?,?,?,?)",
            (
                il, ilce, mahalle, len(a["m2b"]),
                round(statistics.mean(a["m2b"]), 1),
                round(statistics.median(a["m2n"]), 1) if a["m2n"] else None,
                round(statistics.mean(a["floors"]), 1) if a["floors"] else None,
                max(a["floors"]) if a["floors"] else None,
                json.dumps([{"oda": k, "n": v} for k, v in oda_top], ensure_ascii=False),
            ),
        )
        written += 1
    out_conn.execute("CREATE INDEX idx_yap_lokasyon ON mahalle_yapilasma_ozet(il, ilce, mahalle)")
    out_conn.commit()
    out_conn.close()
    print(f"{written:,} mahalle yazıldı → {out}")


def main() -> int:
    p = argparse.ArgumentParser(description="Mahalle konut/yapılaşma profili toplayıcısı")
    p.add_argument("--source", default=DEFAULT_SOURCE)
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args()
    if not os.path.exists(args.source):
        print(f"Kaynak bulunamadı: {args.source}")
        return 1
    build(args.source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
