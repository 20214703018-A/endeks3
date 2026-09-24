"""Resmî kurum sitelerinden istatistik ek dosyalarını (xls/xlsx/csv/zip/pdf) ve istatistik sayfalarını toplayan
genel tarayıcı. Her site için başlangıç sayfaları, izlenecek bağlantı deseni ve ek dosya boyut sınırları tanımlıdır.

Kullanım: python3 intake_site_attachments.py <site> [<site> ...]   (site anahtarları: SITES)
Çıktı: GEOPROP_RAW_INTAKE/<domain>/<tarih>/ekler/... + pages/... ; manifest'te sayfa başlığı ve bağlantı metni.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake  # noqa: E402

ATT = re.compile(r"\.(xlsx?|xlsm|csv|zip|rar|7z|pdf|ods|json|geojson|kmz|kml|shp)(\?|$|&)", re.I)
SITES = {
    "tobb_sirket_istatistik": {
        "domain": "tobb_istatistik",
        "roots": ["https://www.tobb.org.tr/BilgiErisimMudurlugu/Sayfalar/KurulanKapananSirketistatistikleri.php",
                  "https://www.tobb.org.tr/BilgiErisimMudurlugu/Sayfalar/sanayi-kapasite-raporu-istatistikleri.php",
                  "https://www.tobb.org.tr/SanayiMudurlugu/Sayfalar/sanayi-kapasite-raporu-istatistikleri-aylik.php",
                  "https://tobb.org.tr/FuarlarMudurlugu/Sayfalar/Istatistikler.php"],
        "follow": r"istatist|Istatist", "depth": 1, "pdf_max": 15e6, "save_pages": True, "verify": False,
    },
    "kgm": {
        "domain": "kgm_karayollari",
        "roots": [f"https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Trafik/TrafikHacimHaritalari{y}.aspx" for y in range(2015, 2026)]
        + ["https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Trafik/TrafikHacimHaritasi.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Istatistikler/DevletveIlYolEnvanteri.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Istatistikler/OtoyolEnvanterBilgisi.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Istatistikler/TrafikveUlasim.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Istatistikler/SanatYapilariBakimOnarimIsletmeBilgileri.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Trafik/TrafikKazalariOzeti.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Trafik/TrafikveUlasimBilgileri.aspx",
           "https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Trafik/KaraNoktalar.aspx"],
        "follow": r"/Istatistikler/|TrafikHacim|Trafik/Trafik|OtoyolKopru|Otoyol", "depth": 1, "pdf_max": 60e6,
        "att_filter": r"Istatistik|Trafik|Hacim|Otoyol|Envanter|Seyir|Tasima|Kaza|Kopru|Tunel|istatistik",
        "save_pages": True,
    },
    "uab_denizcilik": {
        "domain": "uab_denizcilik_istatistik",
        "roots": ["https://denizcilikistatistikleri.uab.gov.tr/" + p for p in
                  ["", "yuk-istatistikleri", "konteyner-istatistikleri", "turk-bogazlari-gemi-gecis-istatistikleri",
                   "kabotaj-istatistikleri", "kruvaziyer-istatistikleri", "ro-ro-arac-istatistikleri",
                   "gemi-istatistikleri", "filo-istatistikleri", "diger-istatistikler"]],
        "follow": r"istatist", "depth": 2, "pdf_max": 20e6, "save_pages": True,
    },
    "sgk": {
        "domain": "sgk_istatistik",
        "roots": ["https://www.sgk.gov.tr/Istatistik/Yillik/fcd5e59b-6af9-4d90-a451-ee7500eb1cb4/",
                  "https://www.sgk.gov.tr/Istatistik/Aylik/42919466-593f-4600-937d-1f95c9e252e6/",
                  "https://www.sgk.gov.tr/Istatistik/Devredilen/eb4b6b6f-f41a-4d49-8690-797141bfdc8d/"],
        "follow": r"[Ii]statistik/(Yillik|Aylik|Devredilen)", "depth": 2, "pdf_max": 10e6, "save_pages": True,
    },
    "ticaret_bakanligi": {
        "domain": "ticaret_bakanligi_istatistik",
        "roots": ["https://ticaret.gov.tr/istatistikler/bakanlik-istatistikleri",
                  "https://ticaret.gov.tr/istatistikler/arastirma-ve-raporlar",
                  "https://ticaret.gov.tr/istatistikler/dis-ticaret-istatistikleri",
                  "https://ticaret.gov.tr/istatistikler/istatistiki-veri-kaynaklari",
                  "https://ticaret.gov.tr/serbest-bolgeler/serbest-bolgeler-istatistikleri"],
        "follow": r"/istatistikler/|istatistik|serbest-bolgeler", "depth": 3, "pdf_max": 20e6, "save_pages": True,
    },
}


def main():
    for key in sys.argv[1:] or list(SITES):
        cfg = SITES[key]
        it = Intake(cfg["domain"], rate=1.0)
        if cfg.get("verify") is False:  # sertifika zinciri eksik site (curl'de geçerli, requests'te değil)
            import urllib3
            urllib3.disable_warnings()
            it.s.verify = False
        host_ok = {urlparse(r).netloc for r in cfg["roots"]}
        follow = re.compile(cfg["follow"])
        att_filter = re.compile(cfg["att_filter"], re.I) if cfg.get("att_filter") else None
        have = set()
        if it.manifest.exists():
            have = {json.loads(l)["source_url"] for l in it.manifest.read_text().splitlines() if l.strip()}
        queue = [(r, 0) for r in cfg["roots"]]
        visited, atts = set(), {}
        while queue:
            u, depth = queue.pop(0)
            if u in visited:
                continue
            visited.add(u)
            try:
                r = it.get(u, timeout=90)
            except Exception as e:  # noqa: BLE001
                it.log(f"sayfa hata {u}: {e}")
                continue
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                continue
            s = BeautifulSoup(r.text, "html.parser")
            title = (s.find("h1") or s.find("title"))
            title = title.get_text(" ", strip=True)[:200] if title else u
            if cfg.get("save_pages") and u not in have:
                name = re.sub(r"[^\w.-]+", "_", urlparse(u).path.strip("/") or "index")[:150] + ".html"
                it.save_bytes(f"pages/{name}", r.content, source_url=u, method="http_get_page", extra={"page_title": title})
            for a in s.find_all("a"):
                h = (a.get("href") or "").strip()
                if not h or h.startswith(("#", "javascript", "mailto")):
                    continue
                full = urljoin(u, h).split("#")[0]
                t = a.get_text(" ", strip=True)
                if ATT.search(full):
                    if att_filter and not att_filter.search(full + " " + t + " " + title):
                        continue
                    atts.setdefault(full, {"page": u, "page_title": title, "text": t[:200]})
                elif urlparse(full).netloc in host_ok and depth < cfg["depth"] and follow.search(full + " " + t):
                    if full not in visited:
                        queue.append((full, depth + 1))
        it.log(f"{key}: {len(visited)} sayfa, {len(atts)} ek")
        n = 0
        for full, meta in atts.items():
            if full in have:
                continue
            q = parse_qs(urlparse(full).query)
            fname = (q.get("f") or [None])[0] or unquote(urlparse(full).path.rsplit("/", 1)[-1]) or "dosya"
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            try:
                if ext in ("pdf", "zip", "rar", "7z"):
                    hd = it.s.head(full, timeout=60, allow_redirects=True)
                    size = int(hd.headers.get("content-length") or 0)
                    lim = cfg.get("pdf_max", 20e6) if ext == "pdf" else cfg.get("zip_max", 400e6)
                    if size > lim:
                        with (it.dir / "skipped_large.tsv").open("a") as f:
                            f.write(f"{full}\t{size}\t{meta['page_title']}\t{meta['text']}\n")
                        continue
                r = it.get(full, timeout=300)
            except Exception as e:  # noqa: BLE001
                it.log(f"ek hata {full}: {e}")
                continue
            if r.status_code != 200:
                continue
            sub = re.sub(r"[^\w.,-]+", "_", unquote(urlparse(full).path.strip("/")) + ("_" + fname if q.get("f") else ""))[-180:]
            it.save_bytes(f"ekler/{sub}", r.content, source_url=full, method="http_get_attachment",
                          compress=ext not in ("zip", "rar", "7z", "xlsx", "kmz"),
                          extra={"page": meta["page"], "page_title": meta["page_title"], "link_text": meta["text"]})
            n += 1
            time.sleep(0.3)
        it.log(f"{key}: {n} ek indirildi")


if __name__ == "__main__":
    main()
