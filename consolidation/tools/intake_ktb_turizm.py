"""Kültür ve Turizm Bakanlığı (YİGM) turizm istatistikleri toplayıcısı.

Kaynak: https://yigm.ktb.gov.tr/TR-9851/turizm-istatistikleri.html ve alt sayfaları
(sınır istatistikleri — milliyet/sınır kapısı/ay; konaklama — il bazlı tesis/geliş/geceleme/doluluk,
bakanlık ve belediye belgeli; tesis istatistikleri — il bazlı tesis/oda/yatak; deniz turizmi/yat;
seyahat acentası sayıları; turizm gelir/gider; turist profili; iç turizm).

Sayfaları gezer, tüm "/Eklenti/" eklerini (xls/xlsx/zip/rar/pdf/doc) indirir; her dosya için
sayfa başlığı + bağlantı metni + URL manifest'e yazılır. Excel'ler olduğu gibi (sıkıştırılmış) saklanır.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake  # noqa: E402

B = "https://yigm.ktb.gov.tr"
ROOT = "/TR-9851/turizm-istatistikleri.html"
FOLLOW = re.compile(r"istatist|bulten|bülten|konaklama|sinir|sınır|tesis|yat|acenta|gelir|gider|profil|"
                    r"onceki|önceki|donem|dönem|yil|yıl|aylik|aylık|19\d\d|20\d\d|hanehalk|ic-turizm", re.I)
SKIP_EXT_MAX = {"pdf": 8_000_000, "doc": 3_000_000}


def main():
    it = Intake("turizm_ktb", rate=0.8)
    home = BeautifulSoup(it.get(B + "/").text, "html.parser")
    nav = {a.get("href") for a in home.find_all("a")}  # sitenin sabit menüsü → izlenmez
    queue, visited, atts = [(ROOT, 0)], set(), {}
    while queue:
        p, depth = queue.pop(0)
        if p in visited or depth > 5:
            continue
        visited.add(p)
        try:
            s = BeautifulSoup(it.get(B + p).text, "html.parser")
        except Exception as e:  # noqa: BLE001
            it.log(f"sayfa hata {p}: {e}")
            continue
        title = (s.find("h1") or s.find("title"))
        title = title.get_text(" ", strip=True) if title else p
        for a in s.find_all("a"):
            h = a.get("href") or ""
            t = a.get_text(" ", strip=True)
            if "/Eklenti/" in h:
                atts.setdefault(h.split("#")[0], {"page": p, "page_title": title, "text": t})
            elif re.match(r"^/TR-\d+/", h) and (h not in nav or p == ROOT) and FOLLOW.search(h + " " + t):
                if h not in visited:
                    queue.append((h, depth + 1))
    it.log(f"{len(visited)} sayfa gezildi, {len(atts)} ek bulundu")
    have = set()
    if it.manifest.exists():
        import json as _j
        have = {_j.loads(l)["source_url"] for l in it.manifest.read_text().split("\n") if l.strip()}
    n = 0
    for h, meta in atts.items():
        if B + h in have:
            continue
        fname = unquote(h.split("/Eklenti/")[1].split("?")[0])
        ext = fname.rsplit(".", 1)[-1].lower()
        try:
            hd = it.s.head(B + h, timeout=60, allow_redirects=True)
            size = int(hd.headers.get("content-length") or 0)
            if ext in SKIP_EXT_MAX and size > SKIP_EXT_MAX[ext]:
                it.log(f"büyük {ext} atlandı (HEAD {size} B): {h}")
                with (it.dir / "skipped_large.tsv").open("a") as f:
                    f.write(f"{B + h}\t{size}\t{meta['page_title']}\t{meta['text']}\n")
                continue
            r = it.get(B + h, timeout=300)
        except Exception as e:  # noqa: BLE001
            it.log(f"ek hata {h}: {e}")
            continue
        if r.status_code != 200:
            it.log(f"ek HTTP {r.status_code} {h}")
            continue
        if ext in SKIP_EXT_MAX and len(r.content) > SKIP_EXT_MAX[ext]:
            it.log(f"büyük {ext} atlandı ({len(r.content)} B): {h}")
            continue
        safe = re.sub(r"[^\w.,-]+", "_", fname)[:150]
        it.save_bytes(f"ekler/{safe}", r.content, source_url=B + h, method="http_get_attachment",
                      compress=ext not in ("zip", "rar", "xlsx"),
                      extra={"page": B + meta["page"], "page_title": meta["page_title"], "link_text": meta["text"]})
        n += 1
        time.sleep(0.3)
    it.log(f"{n} ek indirildi")


if __name__ == "__main__":
    main()
