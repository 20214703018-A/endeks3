"""URAP Türkiye genel sıralaması (Tablo 10) → universite.sqlite::urap (kardeş dosya).

Kaynak: URAP (ODTÜ) yıllık Türkiye sıralaması PDF'i; yalnız sıra, üniversite adı, şehir ve toplam puan alınır.
Uydurma yok: adı `universite.ad_norm` ile birebir eşleşmeyen satırlar `universite_ad_norm=NULL` bırakılır.

  python3.13 collector/urap_toplayici.py --pdf /tmp/urap.pdf --yil 2025-2026
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import date

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "warehouse", "product", "universite.sqlite")
_SATIR = re.compile(r"(?m)^\s*(\d{1,3})\s*\n?(.*?)\s+((?:\d+,\d+\s+){8}\d+,\d+)\s*$", re.S)
_AD = re.compile(r"(.+?(?:ÜNİVERSİTESİ(?:-\s*CERRAHPAŞA)?|ENSTİTÜSÜ))\s+(.+)$")


def tablo_10(pdf: str) -> list[tuple[int, str, str, float]]:
    r = PdfReader(pdf)
    sayfalar = [i for i, p in enumerate(r.pages) if "Tablo 10." in p.extract_text()[:200]]
    if not sayfalar:
        raise SystemExit("Tablo 10 bulunamadı")
    ilk = sayfalar[0]
    parcalar = []
    for i in range(ilk, len(r.pages)):
        t = r.pages[i].extract_text()
        if i > ilk and "Tablo 11" in t:
            parcalar.append(t.split("Tablo 11", 1)[0])
            break
        parcalar.append(t)
    txt = "\n".join(parcalar)
    txt = txt.split("Toplam Puan", 1)[-1]                      # tablo başlığını at
    txt = re.sub(r"\n\d{1,2} \n \n", "\n", txt)               # sayfa numaraları
    rows = []
    for m in _SATIR.finditer(txt):
        body = re.sub(r"\s+", " ", m.group(2)).strip()
        mm = _AD.match(body)
        if not mm:
            print("ayrıştırılamadı:", m.group(1), body, file=sys.stderr)
            continue
        ad = re.sub(r"-\s+", "-", mm.group(1)).strip()
        rows.append((int(m.group(1)), ad, mm.group(2).strip(), float(m.group(3).split()[-1].replace(",", "."))))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--yil", required=True)
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()
    rows = tablo_10(a.pdf)
    c = sqlite3.connect(a.db)
    c.execute("CREATE TABLE IF NOT EXISTS urap (yil TEXT, sira INTEGER, ad TEXT, sehir TEXT, toplam_puan REAL, "
              "universite_ad_norm TEXT, PRIMARY KEY (yil, sira))")
    c.execute("CREATE INDEX IF NOT EXISTS idx_urap_ad ON urap (universite_ad_norm)")
    adlar = {r[0] for r in c.execute("SELECT ad_norm FROM universite")}
    c.execute("DELETE FROM urap WHERE yil=?", (a.yil,))
    esl = 0
    for sira, ad, sehir, puan in rows:
        n = normalize_name(ad)
        hit = n if n in adlar else None
        esl += hit is not None
        c.execute("INSERT INTO urap VALUES (?,?,?,?,?,?)", (a.yil, sira, ad, sehir, puan, hit))
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='kapsama'").fetchone():
        c.execute("INSERT OR REPLACE INTO kapsama (tablo, satir, guncellenme, kaynak) VALUES (?,?,?,?)",
                  ("urap", len(rows), date.today().isoformat(), f"URAP Türkiye sıralaması {a.yil} (Tablo 10)"))
    c.commit()
    siralar = {s for s, *_ in rows}
    print(f"urap {a.yil}: {len(rows)} satır, {esl} üniversite eşleşti, eksik sıra: {sorted(set(range(1, max(siralar) + 1)) - siralar)}")
    for r in c.execute("SELECT sira, ad FROM urap WHERE yil=? AND universite_ad_norm IS NULL", (a.yil,)):
        print("  eşleşmedi:", r)


if __name__ == "__main__":
    main()
