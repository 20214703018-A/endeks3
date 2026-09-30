#!/usr/bin/env python3
"""Ankara EGO hareket saatleri toplayıcı.

Kaynak: https://www.ego.gov.tr/hareketsaatleri?hat_no=<hat>  (EGO Genel Müdürlüğü resmî sayfası)
Her hat sayfası: hat künyesi (ad, kalkış/varış yeri, mesafe, süre), gün tipine göre (hafta içi / cumartesi / pazar)
ilk duraktan kalkış saatleri ve sıralı durak listesi (EGO durak no, ad, adres). Koordinat içermez.
Hat listesi sayfadaki üç açılır listeden (otobüs, metro, Ankaray) alınır.
Ham HTML değiştirilmeden saklanır: <RAW>/ego_ankara_hareket_saatleri/<tarih>/pages.jsonl.gz
"""
from __future__ import annotations

import re

from intake_common import Intake, now_iso

URL = "https://www.ego.gov.tr/hareketsaatleri"


def main():
    it = Intake("ego_ankara_hareket_saatleri", rate=1.0)
    first = it.get(URL, params={"hat_no": "597"})
    it.save_bytes("index.html", first.content, source_url=first.url, extra={"kind": "index"})
    html = first.text
    lines = []
    for sel in re.findall(r'<select[^>]*id="(hat_liste_[a-z]+)"[^>]*>(.*?)</select>', html, re.S):
        for val, label in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', sel[1]):
            if val and val != "0":
                lines.append((sel[0], val, label.strip()))
    it.log(f"hat listesi: {len(lines)}")
    recs = []
    for i, (grp, val, label) in enumerate(lines):
        try:
            r = it.get(URL, params={"hat_no": val}, timeout=60)
            recs.append({"liste": grp, "hat_no": val, "etiket": label, "url": r.url, "http_status": r.status_code,
                         "fetched_at": now_iso(), "html": r.text})
        except Exception as e:
            recs.append({"liste": grp, "hat_no": val, "etiket": label, "fetched_at": now_iso(), "error": repr(e)[:300]})
        if (i + 1) % 100 == 0:
            it.log(f"{i + 1}/{len(lines)}")
    it.save_jsonl("pages", recs, source_url=URL, method="http_get_per_line",
                  extra={"kind": "pages", "lines": len(lines), "errors": sum("error" in r for r in recs)})
    it.log(f"bitti: {len(recs)} sayfa, hata {sum('error' in r for r in recs)}")


if __name__ == "__main__":
    main()
