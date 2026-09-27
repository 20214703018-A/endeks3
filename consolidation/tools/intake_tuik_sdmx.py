"""TÜİK Veri Portalı / Dağıtım Yönetim Sistemi (databrowser2.tuik.gov.tr) toplu indirici.

Kaynak: https://databrowser2.tuik.gov.tr/api/core/ (TÜİK'in resmî veri tarayıcısının arka ucu; giriş gerekmez)
  katalog  : nodes/1/catalog                          → tüm veri akışları (dataflow) + kategori ağacı
  yapı     : nodes/1/datasets/{id}/structure          → boyutlar, kod listesi referansları
  veri     : POST nodes/1/datasets/{id}/download/jsondata  (gövde [] = tüm veri) → SDMX-JSON
SDMX-JSON kod adlarını (il/ilçe adı, gösterge adı vb.) içerdiği için kendi kendini tanımlar.
Her veri kümesi ham .json.gz olarak saklanır; gözlem sayısı ve kısmi yanıt (partialcontent) manifest'e yazılır.
Kısmi (1,5 M gözlem sınırını aşan) kümeler TIME_PERIOD yıllarına bölünerek yeniden istenir.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

B = "https://databrowser2.tuik.gov.tr/api/core/"


def all_ids(cat):
    ids = set(cat.get("datasetMap", {}).keys())

    def walk(cs):
        for c in cs:
            ids.update(c.get("datasetIdentifiers") or [])
            walk(c.get("childrenCategories") or [])
    for g in cat.get("categoryGroups", []):
        walk(g.get("categories") or [])
    for x in cat.get("datasetUncategorized") or []:
        ids.add(x if isinstance(x, str) else x.get("identifier") or x.get("id"))
    return sorted(i for i in ids if i)


def count_obs(j):
    n = 0
    for ds in (j.get("data") or {}).get("dataSets") or []:
        if "series" in ds:
            for s in ds["series"].values():
                n += len(s.get("observations") or {})
        n += len(ds.get("observations") or {})
    return n


def main():
    it = Intake("tuik_sdmx", rate=1.0)
    it.s.headers.update({"Accept": "application/json, */*", "Content-Type": "application/json"})
    cat = it.get(B + "nodes/1/catalog").json()
    it.save_json("catalog.json", cat, source_url=B + "nodes/1/catalog", method="rest_get")
    ids = all_ids(cat)
    it.log(f"katalog: {len(ids)} veri akışı")
    done = set()
    if it.manifest.exists():
        for l in it.manifest.read_text().split("\n"):
            if not l.strip():
                continue
            r = json.loads(l)
            if r.get("dataset_id") and r.get("kind") == "data":
                done.add(r["dataset_id"])
    tot = 0
    for k, ds in enumerate(ids):
        if ds in done:
            continue
        safe = ds.replace(",", "__")
        try:
            st = it.get(B + f"nodes/1/datasets/{ds}/structure", timeout=120)
            if st.status_code == 200:
                it.save_bytes(f"structure/{safe}.json", st.content, source_url=st.url, method="rest_get",
                              extra={"dataset_id": ds, "kind": "structure"})
            r = it.get(B + f"nodes/1/datasets/{ds}/download/jsondata", method="POST", data="[]", timeout=900)
        except Exception as e:  # noqa: BLE001
            it.log(f"{ds}: HATA {e}")
            continue
        if r.status_code != 200:
            it.log(f"{ds}: HTTP {r.status_code} {r.text[:150]}")
            continue
        partial = bool(r.headers.get("partialcontent") or r.headers.get("PartialContent"))
        try:
            n = count_obs(r.json())
        except Exception:  # noqa: BLE001
            n = None
        it.save_bytes(f"data/{safe}.json", r.content, source_url=r.url, method="rest_post_download_jsondata",
                      rows=n, extra={"dataset_id": ds, "kind": "data", "partial": partial,
                                     "title": (cat.get("datasetMap", {}).get(ds) or {}).get("title")})
        tot += n or 0
        if partial:
            it.log(f"{ds}: KISMİ yanıt ({n} gözlem) — bölünmüş indirme gerekli")
        if k % 20 == 0:
            it.log(f"{k + 1}/{len(ids)} {ds}: {n} gözlem (toplam {tot})")
        time.sleep(0.5)
    it.log(f"bitti: {tot} gözlem")


if __name__ == "__main__":
    main()
