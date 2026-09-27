"""Belediye / kamu açık veri portalları (CKAN) toplayıcısı.

Her portal için: (1) tüm veri seti meta verisi (package_search, sayfalı) → catalog.json.gz
(2) veri dosyaları (CSV/XLSX/XLS/JSON/GeoJSON/KML/ZIP/SHP…) → kaynak başına boyut sınırıyla indirme.
Büyük dosyalar atlanır ve skipped_large.tsv'ye yazılır (boyut, URL, veri seti) — sonradan GitHub Actions ile
çekilebilir. API/WMS gibi dosya olmayan kaynakların yalnız meta verisi saklanır.
Kullanım: python3 intake_ckan_portals.py <portal> [...]   (anahtarlar: PORTALS)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

PORTALS = {  # 2026-09-24 erişim testi: yalnız yanıt veren portallar (Kocaeli 502; Antalya/Ankara/Mersin/Tekirdağ yanıtsız)
    "ibb": "https://data.ibb.gov.tr",
    "izmir": "https://acikveri.bizizmir.com",
    "bursa": "https://acikyesil.bursa.bel.tr",
    "konya": "https://acikveri.konya.bel.tr",
    "gaziantep": "https://acikveri.gaziantep.bel.tr",
    "balikesir": "https://acikveri.balikesir.bel.tr",
    "denizli": "https://acikveri.denizli.bel.tr",
    "manisa": "https://acikveri.manisa.bel.tr",
    "sakarya": "https://veri.sakarya.bel.tr",
}
FILE_FMT = re.compile(r"^(csv|xlsx?|json|geojson|kml|kmz|zip|shp|xml|txt|ods|tsv|parquet|gpkg|rar)$", re.I)
MAX_BYTES = 300e6


def main():
    for key in sys.argv[1:] or list(PORTALS):
        base = PORTALS[key]
        it = Intake(f"acikveri_ckan_{key}", rate=0.5)
        if key in ("denizli", "gaziantep"):  # sunucu sertifika zinciri eksik (requests SSLError)
            import urllib3
            urllib3.disable_warnings()
            it.s.verify = False
        # 1) katalog
        pkgs, start = [], 0
        while True:
            try:
                r = it.get(f"{base}/api/3/action/package_search", params={"rows": 500, "start": start}, timeout=120)
                res = r.json()["result"]
            except Exception as e:  # noqa: BLE001
                it.log(f"katalog hata: {e}")
                break
            pkgs += res["results"]
            start += 500
            if start >= res["count"] or not res["results"]:
                break
        if not pkgs:
            continue
        it.save_json("catalog.json", pkgs, source_url=f"{base}/api/3/action/package_search", method="ckan_api")
        n_res = sum(len(p.get("resources") or []) for p in pkgs)
        it.log(f"{key}: {len(pkgs)} veri seti, {n_res} kaynak")
        have = set()
        if it.manifest.exists():
            have = {json.loads(l)["source_url"] for l in it.manifest.read_text().split("\n") if l.strip()}
        # 2) dosyalar
        n = skipped = 0
        for p in pkgs:
            for res in p.get("resources") or []:
                url = (res.get("url") or "").strip()
                fmt = (res.get("format") or "").strip().lower().lstrip(".")
                ext = unquote(urlparse(url).path).rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
                if not url.startswith("http") or url in have:
                    continue
                if not (FILE_FMT.match(fmt or "") or FILE_FMT.match(ext or "")):
                    continue
                try:
                    hd = it.s.head(url, timeout=60, allow_redirects=True)
                    size = int(hd.headers.get("content-length") or 0)
                except Exception:  # noqa: BLE001
                    size = 0
                if size > MAX_BYTES:
                    skipped += 1
                    with (it.dir / "skipped_large.tsv").open("a") as f:
                        f.write(f"{url}\t{size}\t{p.get('name')}\t{res.get('name')}\n")
                    continue
                try:
                    r = it.get(url, timeout=600, stream=False)
                except Exception as e:  # noqa: BLE001
                    it.log(f"indirme hata {url}: {e}")
                    continue
                if r.status_code != 200 or len(r.content) > MAX_BYTES:
                    continue
                name = re.sub(r"[^\w.,-]+", "_", f"{p.get('name')}/{res.get('id')}_{unquote(urlparse(url).path.rsplit('/', 1)[-1])}")[-200:]
                comp = (fmt or ext) not in ("zip", "xlsx", "kmz", "parquet", "gpkg", "rar")
                it.save_bytes(f"files/{name}", r.content, source_url=url, method="ckan_resource_download",
                              compress=comp, extra={"dataset": p.get("name"), "dataset_title": p.get("title"),
                                                    "resource_id": res.get("id"), "resource_name": res.get("name"),
                                                    "format": fmt, "organization": (p.get("organization") or {}).get("title"),
                                                    "last_modified": res.get("last_modified") or res.get("created")})
                n += 1
                if n % 100 == 0:
                    it.log(f"{key}: {n} dosya indirildi")
                time.sleep(0.2)
        it.log(f"{key}: {n} dosya indirildi, {skipped} büyük dosya atlandı")


if __name__ == "__main__":
    main()
