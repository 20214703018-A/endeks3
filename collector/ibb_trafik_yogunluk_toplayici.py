"""İBB Saatlik Trafik Yoğunluk Verisi → cevre.sqlite::trafik_saatlik (geohash-6 hücre × gün tipi × saat profili)

Kaynak: data.ibb.gov.tr "hourly-traffic-density-data-set" — aylık CSV (~140 MB): DATE_TIME, LATITUDE, LONGITUDE, GEOHASH(6),
MIN/MAX/AVERAGE_SPEED, NUMBER_OF_VEHICLES (araç GPS'lerinden türetilmiş hücre×saat araç sayısı ve hız). Ham veri diske yazılmaz:
curl → stdout → satır satır toplanır (hücre, hafta içi/sonu, saat) → ortalama araç, ortalama hız, gözlem gün sayısı.
Hücre ≈ 1,2 km × 0,6 km (geohash-6). Yalnız İstanbul; son 12 yayımlanmış ay (2024-02 … 2025-01; İBB güncellemeyi durdurdu).
Bu "kişi yoğunluğu" değil araç trafiği; yaya/ticari yoğunluk için vekildir (kart bunu yazar).

  python3.13 collector/ibb_trafik_yogunluk_toplayici.py [--ay 12]
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "warehouse", "product", "cevre.sqlite")
PKG = "https://data.ibb.gov.tr/api/3/action/package_show?id=hourly-traffic-density-data-set"
UA = "Mozilla/5.0 (GEOPROP veri toplayici)"


def kaynaklar(n_ay: int):
    j = json.loads(subprocess.run(["curl", "-s", "-m", "60", "-A", UA, PKG], capture_output=True).stdout)
    out = []
    for r in j["result"]["resources"]:
        m = re.search(r"traffic_density_(\d{6})", r["url"])
        if m:
            out.append((m.group(1), r["url"]))
    return sorted(out)[-n_ay:]


def main():
    n_ay = int(sys.argv[sys.argv.index("--ay") + 1]) if "--ay" in sys.argv else 12
    agg: dict[tuple, list] = {}   # (geohash, gun_tipi, saat) → [arac_toplam, hiz_toplam, n, lat, lon]
    aylar = kaynaklar(n_ay)
    for ay, url in aylar:
        p = subprocess.Popen(["curl", "-sL", "-m", "1800", "-A", UA, url], stdout=subprocess.PIPE)
        rd = csv.reader(io.TextIOWrapper(p.stdout, encoding="utf-8-sig", errors="ignore"))
        hdr = next(rd); ix = {h.strip().upper(): i for i, h in enumerate(hdr)}
        n = 0
        for row in rd:
            try:
                dt = row[ix["DATE_TIME"]]; gh = row[ix["GEOHASH"]]
                d = date.fromisoformat(dt[:10]); saat = int(dt[11:13])
                arac = float(row[ix["NUMBER_OF_VEHICLES"]]); hiz = float(row[ix["AVERAGE_SPEED"]])
            except (ValueError, IndexError, KeyError):
                continue
            gt = "haftasonu" if d.weekday() >= 5 else "haftaici"
            k = (gh, gt, saat)
            a = agg.get(k)
            if a is None:
                agg[k] = [arac, hiz, 1, float(row[ix["LATITUDE"]]), float(row[ix["LONGITUDE"]])]
            else:
                a[0] += arac; a[1] += hiz; a[2] += 1
            n += 1
        p.wait(); print(f"  {ay}: {n:,} satır, hücre-saat {len(agg):,}", flush=True)
    c = sqlite3.connect(OUT)
    c.executescript("""
    DROP TABLE IF EXISTS trafik_saatlik;
    CREATE TABLE trafik_saatlik (geohash TEXT, gun_tipi TEXT, saat INTEGER, ort_arac REAL, ort_hiz REAL, gozlem INTEGER, lat REAL, lon REAL,
        PRIMARY KEY (geohash, gun_tipi, saat)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_trafik_lat ON trafik_saatlik (lat, lon);
    CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
    """)
    c.executemany("INSERT INTO trafik_saatlik VALUES (?,?,?,?,?,?,?,?)",
                  [(gh, gt, saat, round(v[0] / v[2], 1), round(v[1] / v[2], 1), v[2], v[3], v[4]) for (gh, gt, saat), v in agg.items()])
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('trafik_saatlik', ?, ?, ?)",
              (len(agg), datetime.now(timezone.utc).isoformat(), f"İBB Saatlik Trafik Yoğunluk Verisi ({aylar[0][0]}–{aylar[-1][0]}, geohash-6 × saat profili)"))
    c.commit(); c.close(); print("tamam:", len(agg))


if __name__ == "__main__":
    main()
