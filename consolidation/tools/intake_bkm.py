"""BKM (Bankalararası Kart Merkezi) dönemsel istatistikleri — tüm sayfalar × tüm yıl/aylar.

Kaynak: https://bkm.com.tr/raporlar-ve-yayinlar/donemsel-bilgiler/ altındaki sayfalar; her sayfa
?filter_year=YYYY&filter_month=M ile o ayın tablosunu döndürür (kartlı ödeme işlem adedi/tutarı; sektörel;
internetten (e-ticaret) kartlı ödemeler ve sektörel dağılımı; yerli/yabancı kartların yurt içi/dışı kullanımı;
POS/ATM/ÖKC ve kart sayıları; temassız; karekod). Ham HTML (.gz) + ayrıştırılmış tablolar (parquet, metin olarak).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

ROOT = "https://bkm.com.tr/raporlar-ve-yayinlar/donemsel-bilgiler/"
SKIP = re.compile(r"raporlar-ve-yayinlar|faydali|bkm-hakkinda|/bkm/|/en/|^https://bkm.com.tr/$|urunler-ve-hizmetler|kurumsal-iletisim|basin|duyuru|kariyer|iletisim|haber|kvkk|cerez|mevzuat|sss")


def main():
    it = Intake("bkm_donemsel", rate=0.8)
    from bs4 import BeautifulSoup
    s = BeautifulSoup(it.get(ROOT).text, "html.parser")
    pages = []
    for a in s.find_all("a"):
        h = a.get("href") or ""
        if h.startswith("https://bkm.com.tr/") and not SKIP.search(h) and h not in pages:
            pages.append(h)
    it.log(f"{len(pages)} istatistik sayfası")
    out = it.dir / "pages.jsonl"
    done = set()
    if out.exists():
        done = {(json.loads(l)["page"], json.loads(l)["y"], json.loads(l)["m"]) for l in out.read_text().split("\n") if l.strip()}
    today = dt.date.today()
    tables = []
    for p in pages:
        for y in range(today.year, 2009, -1):
            empty_year = True
            for m in range(12, 0, -1):
                if (y == today.year and m > today.month) or (p, y, m) in done:
                    continue
                try:
                    r = it.get(p, params={"filter_year": y, "filter_month": m}, timeout=60)
                except Exception as e:  # noqa: BLE001
                    it.log(f"hata {p} {y}-{m}: {e}")
                    continue
                try:
                    tabs = pd.read_html(io.StringIO(r.text), thousands=None, decimal=",", flavor="lxml")
                except ValueError:
                    tabs = []
                data_tabs = [t for t in tabs if t.shape[0] >= 2 and t.shape[1] >= 2]
                rec = {"page": p, "y": y, "m": m, "status": r.status_code, "at": now_iso(),
                       "tables": [t.astype(str).to_dict(orient="split") for t in data_tabs]}
                with out.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if data_tabs:
                    empty_year = False
            if empty_year and y < today.year - 1:
                it.log(f"{p}: {y} yılında veri yok → daha eskiye gidilmiyor")
                break
        it.log(f"sayfa bitti: {p}")
    it._record(out, source_url=ROOT, method="http_get_year_month_html_tables",
               rows=sum(1 for _ in out.open()), note="her satır: sayfa×yıl×ay; tablolar pandas split biçiminde (metin)")
    it.log("bitti")


if __name__ == "__main__":
    main()
