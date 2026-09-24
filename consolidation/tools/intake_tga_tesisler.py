"""Kültür ve Turizm Bakanlığı belgeli konaklama tesisleri (tam liste) — TGA web sitesinin veri servisi.

Kaynak: https://cms-backend.tga.gov.tr/api/ktb-accommodation/  (tga.gov.tr "T.C. Kültür ve Turizm Bakanlığı
Belgeli Konaklama Tesisleri" sayfasının arka ucu; herkese açık). Alanlar: tesis adı, tür, sınıf, il, ilçe,
belge türü (Turizm İşletmesi / Yatırım / Basit Konaklama), belge no.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

B = "https://cms-backend.tga.gov.tr/api/ktb-accommodation/"


def main():
    it = Intake("turizm_tga_belgeli_tesisler", rate=1.0)
    it.s.headers["Accept"] = "application/json"
    q = {"page": 1, "page_size": 1000, "tesis_turu": "Tümü", "tesis_sinifi": "Tümü"}
    rows, pages = [], []
    while True:
        j = it.get(B, params=q, timeout=120).json()
        d = j.get("data") or {}
        pages.append(j)
        rows += d.get("rows") or []
        it.log(f"sayfa {q['page']}/{d.get('total_page')} — {len(rows)}/{d.get('total_row')}")
        if q["page"] >= (d.get("total_page") or 0) or not d.get("rows"):
            break
        q["page"] += 1
    it.save_json("raw_pages.json", pages, source_url=B, method="rest_get_paginated", rows=len(rows))
    df = pd.DataFrame(rows)
    df["guncellenme_tarihi"] = now_iso()
    it.save_parquet("ktb_belgeli_konaklama_tesisleri", df, source_url=B, method="rest_get_paginated",
                    note="koordinat yok; il/ilçe adı var (eşleme konsolidasyonda)")
    it.log(f"{len(df)} tesis")


if __name__ == "__main__":
    main()
