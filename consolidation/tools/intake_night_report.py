"""Gece toplama turu raporu: GEOPROP_RAW_INTAKE altındaki tüm manifest.jsonl dosyalarını tarar;
kaynak (klasör) başına dosya sayısı, satır/kayıt, bayt, yöntem, kaynak alan adları, ilk/son çekim zamanı.
Ayrıca ara ilerleme dosyalarını (jsonl yanıt günlükleri) sayar. Çıktı: JSON + Markdown (reports/).
Kullanım: python3 intake_night_report.py [--since 2026-09-24]
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

RAW = Path.home() / "Desktop/GEOPROP_RAW_INTAKE"
OUT = Path(__file__).resolve().parent.parent / "reports"


def count_lines(p: Path) -> int:
    try:
        op = gzip.open if p.suffix == ".gz" else open
        with op(p, "rb") as f:
            return sum(1 for _ in f)
    except Exception:  # noqa: BLE001
        return -1


def main():
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2026-09-24"
    rows = []
    for d in sorted(RAW.glob("*/*")):
        if not d.is_dir() or not d.name[:4].isdigit():
            continue
        if d.name < since and not d.parent.name.startswith("osm_pbf_katmanlar"):
            continue
        man = d / "manifest.jsonl"
        agg = {"kaynak": d.parent.name, "klasor": str(d.relative_to(RAW)), "dosya": 0, "satir": 0, "bayt": 0,
               "yontemler": set(), "alanlar": set(), "ilk": None, "son": None}
        if man.exists():
            for l in man.read_text().splitlines():
                try:
                    r = json.loads(l)
                except ValueError:
                    continue
                agg["dosya"] += 1
                agg["satir"] += r.get("rows") or 0
                agg["bayt"] += r.get("bytes") or 0
                agg["yontemler"].add(r.get("method"))
                u = r.get("source_url") or ""
                if u.startswith("http"):
                    agg["alanlar"].add(urlparse(u).netloc)
                t = r.get("fetched_at")
                if t:
                    agg["ilk"] = min(agg["ilk"] or t, t)
                    agg["son"] = max(agg["son"] or t, t)
        # ilerleme günlükleri (manifest'e henüz yazılmamış canlı kayıtlar)
        prog = {}
        for p in list(d.glob("*.jsonl")) + list(d.glob("*.jsonl.gz")) + list(d.glob("*/raw_*/part_*.jsonl.gz")) \
                + list(d.glob("depots_raw/part_*.jsonl.gz")):
            if p.name == "manifest.jsonl":
                continue
            key = p.parent.name + "/" if "part_" in p.name else ""
            prog[key + (p.name if "part_" not in p.name else "parts")] = prog.get(key + (p.name if "part_" not in p.name else "parts"), 0) + count_lines(p)
        agg["ilerleme_kayitlari"] = prog
        agg["disk_bayt"] = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        agg["yontemler"] = sorted(x for x in agg["yontemler"] if x)
        agg["alanlar"] = sorted(agg["alanlar"])
        rows.append(agg)
    OUT.mkdir(exist_ok=True)
    (OUT / f"NIGHT_INTAKE_{since}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    md = [f"# Gece veri toplama turu — {since}", "", "| Kaynak klasörü | Dosya | Kayıt (manifest) | Canlı kayıt | Disk | Alan adları |",
          "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        live = sum(v for v in r["ilerleme_kayitlari"].values() if v > 0)
        md.append(f"| {r['klasor']} | {r['dosya']} | {r['satir']:,} | {live:,} | {r['disk_bayt'] / 1e6:,.1f} MB | {', '.join(r['alanlar'])[:80]} |")
    tot = sum(r["disk_bayt"] for r in rows)
    md += ["", f"Toplam disk: {tot / 1e9:.2f} GB, {sum(r['dosya'] for r in rows):,} dosya."]
    (OUT / f"NIGHT_INTAKE_{since}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
