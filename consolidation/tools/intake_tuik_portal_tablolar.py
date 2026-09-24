"""TÜİK Veri Portalı — tüm istatistik tabloları (Excel) + bülten ekleri; SDMX'e taşınmamış konular dahil.

Kaynak: https://veriportali.tuik.gov.tr/api/tr/data/search (portalın kendi arama servisi; giriş gerekmez)
  typeIds: 2 = Tablolar ve Grafikler, 5 = Veritabanı, 6 = Yayın, 1 = Haber Bülteni, 4 = Rapor
  indirme: /api/tr/data/downloads?t=i&p=...  (arama sonucundaki url)
Turizm (çıkış yapan ziyaretçi: milliyet, yaş, cinsiyet, eğitim, çalışma durumu, geliş amacı, konaklama türü,
harcama türü, kalış süresi; hanehalkı yurt içi turizm) ve diğer tüm konular. Arşiv (eski yıllar) dahil.
Bülten sayfalarındaki (/tr/press/N) indirilebilir tablolar da toplanır.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake  # noqa: E402

B = "https://veriportali.tuik.gov.tr"


def search(it, type_ids, archive, page):
    body = {"text": "", "page": page, "typeIds": type_ids, "categoryIds": [], "subCategoryIds": [], "years": [],
            "levels": [], "archive": archive, "autoFilter": False}
    r = it.get(B + "/api/tr/data/search", method="POST", json=body, timeout=60,
               headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"})
    d = (r.json() or {}).get("data") or {}
    return d.get("data") or [], d.get("total")


def main():
    it = Intake("tuik_portal_tablolar", rate=0.8)
    items = {}
    for tids in ([2], [5], [6], [4], [1]):
        for archive in (False, True):
            page = 1
            while True:
                try:
                    res, total = search(it, tids, archive, page)
                except Exception as e:  # noqa: BLE001
                    it.log(f"arama hata {tids} {archive} {page}: {e}")
                    break
                if not res:
                    break
                for x in res:
                    items.setdefault(x["url"], {**x, "archive": archive})
                page += 1
            it.log(f"tip {tids} arşiv={archive}: toplam {len(items)} öğe")
    it.save_json("items.json", list(items.values()), source_url=B + "/api/tr/data/search", method="rest_post_paginated")
    have = set()
    if it.manifest.exists():
        have = {json.loads(l)["source_url"] for l in it.manifest.read_text().splitlines() if l.strip()}
    press = [x for x in items.values() if x["type"] == 1]
    files = [x for x in items.values() if x["type"] != 1]
    # bülten sayfalarındaki indirme bağlantıları
    for p in press:
        try:
            r = it.get(B + p["url"], timeout=60)
        except Exception:  # noqa: BLE001
            continue
        for u in set(re.findall(r'(/api/tr/data/downloads\?[^"\'<> ]+)', r.text)):
            files.append({"type": "press_attachment", "title": p["title"], "url": u.replace("&amp;", "&"), "press": p["url"]})
    it.log(f"{len(files)} indirilebilir dosya")
    n = 0
    for x in files:
        url = B + x["url"]
        if url in have:
            continue
        try:
            r = it.get(url, timeout=300)
        except Exception as e:  # noqa: BLE001
            it.log(f"indirme hata {x.get('title')}: {e}")
            continue
        if r.status_code != 200:
            continue
        cd = r.headers.get("content-disposition", "")
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
        fname = unquote(m.group(1)) if m else re.sub(r"[^\w.-]+", "_", x.get("title", "dosya"))[:120]
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        safe = re.sub(r"[^\w.,-]+", "_", f"{x['type']}_{fname}")[-180:]
        it.save_bytes(f"files/{safe}", r.content, source_url=url, method="rest_get_download",
                      compress=ext not in ("xlsx", "zip", "rar", "docx"),
                      extra={"title": x.get("title"), "item_type": x["type"], "archive": x.get("archive"),
                             "press": x.get("press"), "date": x.get("date")})
        n += 1
        time.sleep(0.3)
    it.log(f"{n} dosya indirildi")


if __name__ == "__main__":
    main()
