"""Birleşik Krallık ONS 'Travelpac' — International Passenger Survey (IPS) veri paketi, 1994–2023.

Kaynak: https://www.ons.gov.uk/peoplepopulationandcommunity/leisureandtourism/datasets/travelpac (OGL lisansı)
Her kayıt bir ağırlıklı ziyaret grubudur: yıl, çeyrek, yön (İngiltere'den yurt dışına / yurt dışından İngiltere'ye),
ülke, amaç, ulaşım (hava/deniz/tünel), paket tur, yaş grubu, cinsiyet, süre bandı + ziyaret sayısı, geceleme, harcama.
Ham zip olduğu gibi saklanır; ayrıca ülke = Türkiye olan satırlar düz tabloya süzülür (travelpac_turkiye.parquet).
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

PAGE = "https://www.ons.gov.uk/peoplepopulationandcommunity/leisureandtourism/datasets/travelpac"
B = "https://www.ons.gov.uk"


def read_any(name, data):
    ext = name.lower().rsplit(".", 1)[-1]
    if ext == "csv":
        return [pd.read_csv(io.BytesIO(data), dtype=str, encoding_errors="replace")]
    if ext in ("xlsx", "xls"):
        xl = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str)
        return list(xl.values())
    return []


def main():
    it = Intake("ons_travelpac_uk", rate=1.5)
    html = it.get(PAGE).text
    links = list(dict.fromkeys(re.findall(r'href="(/file\?uri=[^"]+\.zip)"', html)))
    it.log(f"{len(links)} zip")
    frames = []
    for l in links:
        url = B + l
        try:
            r = it.get(url, timeout=600)
        except Exception as e:  # noqa: BLE001
            it.log(f"hata {url}: {e}")
            continue
        name = l.split("/")[-2] + "__" + l.split("/")[-1]
        it.save_bytes(f"zip/{name}", r.content, source_url=url, method="http_get", compress=False)
        try:
            z = zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile:
            continue
        for m in z.namelist():
            for df in read_any(m, z.read(m)):
                cols = {c.lower(): c for c in df.columns}
                ccol = next((cols[c] for c in cols if c in ("country", "country visited", "countryvisited", "country_visited")), None)
                if ccol is None:
                    ccol = next((c for c in df.columns if "country" in c.lower()), None)
                if ccol is None:
                    continue
                t = df[df[ccol].astype(str).str.contains("Turkey|Türkiye|Turkiye", case=False, na=False)].copy()
                if len(t):
                    t["kaynak_zip"] = name
                    t["kaynak_dosya"] = m
                    frames.append(t)
        it.log(f"{name}: tamam")
    if frames:
        out = pd.concat(frames, ignore_index=True).astype(str)
        out["guncellenme_tarihi"] = now_iso()
        it.save_parquet("travelpac_turkiye", out, source_url=PAGE, method="zip_extract_filter_turkey",
                        note="ülke sütunu Turkey/Türkiye olan satırlar; tüm boyutlar metin olarak")
        it.log(f"Türkiye satırı: {len(out)}")


if __name__ == "__main__":
    main()
