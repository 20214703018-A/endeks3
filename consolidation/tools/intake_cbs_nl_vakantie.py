"""Hollanda CBS (Centraal Bureau voor de Statistiek) — Hollandalıların tatilleri: varış ülkesi (Türkiye dahil),
harcama, süre, konaklama, ulaşım, organizasyon, kişi/hane özellikleri. 1969 → 2025.
Kaynak: https://opendata.cbs.nl (OData, açık lisans CC BY 4.0). Her tablo: TypedDataSet + DataProperties + boyut kod listeleri.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake  # noqa: E402

CAT = "https://opendata.cbs.nl/ODataCatalog/Tables"
FEED = "https://opendata.cbs.nl/ODataFeed/odata/{}/"


def main():
    it = Intake("cbs_nl_tatil_istatistikleri", rate=0.5)
    tabs = it.get(CAT, params={"$format": "json", "$filter": "substringof('akantie',Title)",
                               "$select": "Identifier,Title,Period,Modified,ShortDescription"}).json()["value"]
    it.save_json("catalog.json", tabs, source_url=CAT, method="odata")
    for t in tabs:
        tid = t["Identifier"]
        base = FEED.format(tid)
        try:
            meta = it.get(base + "DataProperties", params={"$format": "json"}, timeout=120).json().get("value", [])
            it.save_json(f"{tid}/DataProperties.json", meta, source_url=base + "DataProperties", method="odata")
            for d in [m for m in meta if m.get("Type") in ("Dimension", "GeoDimension", "TimeDimension")]:
                k = d["Key"]
                v = it.get(base + k, params={"$format": "json"}, timeout=120).json().get("value", [])
                it.save_json(f"{tid}/dim_{k}.json", v, source_url=base + k, method="odata")
            rows, url, params = [], base + "TypedDataSet", {"$format": "json"}
            while url:
                j = it.get(url, params=params, timeout=300).json()
                rows += j.get("value", [])
                url, params = j.get("odata.nextLink"), None
            it.save_json(f"{tid}/TypedDataSet.json", rows, source_url=base + "TypedDataSet", method="odata_paged",
                         extra={"title": t["Title"], "period": t.get("Period")})
            it.log(f"{tid}: {len(rows)} satır — {t['Title'][:70]}")
        except Exception as e:  # noqa: BLE001
            it.log(f"{tid}: hata {e!r}"[:200])


if __name__ == "__main__":
    main()
