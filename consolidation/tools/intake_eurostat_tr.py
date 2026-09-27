"""Eurostat — Türkiye ile ilgili turizm ve havayolu verileri (Avrupa'dan Türkiye'ye talep tarafı).

Kaynak: Eurostat dissemination API (resmî, açık)
  içindekiler: https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en
  toplu veri : https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{kod}/?format=TSV&compressed=true
Seçilen kümeler: tour_* (AB ülkelerinde yaşayanların seyahatleri: varış ülkesi, amaç, süre, konaklama, ulaşım,
harcama; Türkiye'nin kendi bildirdiği konaklama verileri) ve avia_* (havalimanı çiftleri bazında yolcu/uçuş/yük —
ör. Almanya havalimanları ↔ Antalya; ülke çifti bazında yolcu).
Her küme akış halinde indirilir; yalnız Türkiye'ye ait satırlar (herhangi bir boyutta 'TR' kodu veya '_TR_' içeren
havalimanı çifti) + başlık satırı saklanır. Tam küme boyutu ve satır sayısı manifest'e yazılır.
"""
from __future__ import annotations

import gzip
import io
import re
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake  # noqa: E402

TOC = "https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en"
DATA = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{}/?format=TSV&compressed=true"
PREFIX = re.compile(r"^(tour_|avia_)")
TR_TOKEN = re.compile(r"(^|[,_\t])TR($|[,_\t])|_LT[A-Z]{2}\b")  # ülke kodu TR veya Türk havalimanı ICAO (LTxx)


def main():
    it = Intake("eurostat_turkiye_turizm_havayolu", rate=1.0)
    toc = it.get(TOC, timeout=300).text
    it.save_bytes("toc.txt", toc.encode(), source_url=TOC, method="http_get")
    codes = []
    for line in toc.splitlines()[1:]:
        cols = [c.strip().strip('"') for c in line.split("\t")]
        if len(cols) > 2 and cols[2] == "dataset" and PREFIX.match(cols[1]):
            codes.append((cols[1], cols[0].strip()))
    codes = list(dict.fromkeys(codes))
    it.log(f"{len(codes)} küme (tour_/avia_)")
    have = set()
    if it.manifest.exists():
        import json
        have = {json.loads(l).get("dataset") for l in it.manifest.read_text().split("\n") if l.strip()}
    for code, title in codes:
        if code in have:
            continue
        url = DATA.format(code)
        try:
            r = it.s.get(url, stream=True, timeout=600)
            if r.status_code != 200:
                it.log(f"{code}: HTTP {r.status_code}")
                continue
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            buf, header, keep, total, raw_bytes = b"", None, [], 0, 0
            for chunk in r.iter_content(1 << 20):
                raw_bytes += len(chunk)
                buf += d.decompress(chunk)
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    if header is None:
                        header = ln
                        continue
                    total += 1
                    key = ln.split(b"\t", 1)[0].decode("utf-8", "replace")
                    if TR_TOKEN.search(key):
                        keep.append(ln)
            if buf.strip():
                total += 1
                if TR_TOKEN.search(buf.split(b"\t", 1)[0].decode("utf-8", "replace")):
                    keep.append(buf)
        except Exception as e:  # noqa: BLE001
            it.log(f"{code}: hata {e!r}"[:200])
            continue
        if not keep:
            it.log(f"{code}: Türkiye satırı yok ({total} satır)")
            with (it.dir / "no_tr_datasets.tsv").open("a") as f:
                f.write(f"{code}\t{total}\t{title}\n")
            continue
        it.save_bytes(f"data/{code}_TR.tsv", header + b"\n" + b"\n".join(keep) + b"\n", source_url=url,
                      method="sdmx_tsv_stream_filter_TR", rows=len(keep),
                      extra={"dataset": code, "title": title, "full_rows": total, "full_gz_bytes": raw_bytes})
        it.log(f"{code}: {len(keep)}/{total} Türkiye satırı — {title[:70]}")


if __name__ == "__main__":
    main()
