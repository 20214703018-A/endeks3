"""ÇŞB SİM — Ulusal Hava Kalitesi İzleme Ağı → warehouse/product/cevre.sqlite (hava_istasyon, hava_gunluk, hava_yillik)

Kaynak: sim.csb.gov.tr
  POST /Services/GetAirQualityStations?type=0         → istasyon listesi (koordinat, il/ilçe, tip, anlık değerler)
  POST /STN/STN_Report/StationDataDownloadNewData     → istasyon × gün (PM10, PM2.5, SO2, NO2; µg/m³); antiforgery token sayfadan
Günlük ortalama, son 365 gün. Yıllık özet: ortalama, geçerli gün, AB günlük sınırı (PM10 > 50 µg/m³) aşım gün sayısı,
DSÖ 2021 kılavuz (PM10 yıllık 15, PM2.5 yıllık 5) karşılaştırması motorda hesaplanır. Uydurma yok: ölçümü olmayan gün NULL.
HTTP: urllib bu makinede sertifika zinciri hatası veriyor → `curl` alt süreçle (çerez kavanozu). Nezaket: istek başına 0,4 sn.

  python3.13 collector/sim_hava_kalitesi_toplayici.py [--gun 365] [--reset]
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402

OUT = os.path.join(REPO, "warehouse", "product", "cevre.sqlite")
BASE = "https://sim.csb.gov.tr"
UA = "Mozilla/5.0 (GEOPROP veri toplayici)"
PARAMS = ("PM10", "PM25", "SO2", "NO2")


def _curl(url, jar, data=None, headers=()):
    cmd = ["curl", "-s", "-m", "90", "-A", UA, "-c", jar, "-b", jar]
    for h in headers:
        cmd += ["-H", h]
    if data is not None:
        cmd += ["--data", data] if data else ["-H", "Content-Length: 0", "-X", "POST"]
    cmd.append(url)
    return subprocess.run(cmd, capture_output=True, timeout=120).stdout.decode("utf-8", "ignore")


def istasyonlar(jar):
    j = json.loads(_curl(f"{BASE}/Services/GetAirQualityStations?type=0", jar, data=""))
    out = []
    for o in j.get("objects", []):
        m = re.match(r"POINT \(([-\d.]+) ([-\d.]+)\)", o.get("Location") or "")
        if not m:
            continue
        out.append({"id": o["id"], "kod": o.get("Code"), "ad": o.get("Name"), "il": o.get("City_Title"), "ilce": o.get("Town_Title"),
                    "tip": o.get("SubType_Title"), "lon": float(m.group(1)), "lat": float(m.group(2)), "son_veri": o.get("LastDataDate")})
    return out


def main():
    gun = int(sys.argv[sys.argv.index("--gun") + 1]) if "--gun" in sys.argv else 365
    if "--reset" in sys.argv and os.path.exists(OUT):
        os.remove(OUT)
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS hava_istasyon (id TEXT PRIMARY KEY, kod TEXT, ad TEXT, il TEXT, ilce TEXT, il_norm TEXT, tip TEXT, lat REAL, lon REAL, son_veri TEXT, guncellenme TEXT);
    CREATE INDEX IF NOT EXISTS idx_hava_istasyon_lat ON hava_istasyon (lat, lon);
    CREATE TABLE IF NOT EXISTS hava_gunluk (istasyon_id TEXT, gun TEXT, pm10 REAL, pm25 REAL, so2 REAL, no2 REAL, PRIMARY KEY (istasyon_id, gun)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
    """)
    jar = os.path.join(tempfile.gettempdir(), "geoprop_sim_jar.txt")
    page = _curl(f"{BASE}/STN/STN_Report/StationDataDownloadNew", jar)
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page)
    if not m:
        raise SystemExit("antiforgery token bulunamadı")
    tok = m.group(1)
    now = datetime.now(timezone.utc).isoformat()
    st = istasyonlar(jar)
    for s in st:
        c.execute("INSERT OR REPLACE INTO hava_istasyon VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (s["id"], s["kod"], s["ad"], s["il"], s["ilce"], normalize_name(s["il"] or ""), s["tip"], s["lat"], s["lon"], s["son_veri"], now))
    c.commit(); print("istasyon:", len(st))
    bas, bit = (date.today() - timedelta(days=gun)).strftime("%d.%m.%Y"), date.today().strftime("%d.%m.%Y")
    ok = 0
    for i, s in enumerate(st, 1):
        data = "&".join([f"__RequestVerificationToken={tok}", "StationType=1", f"StationIds={s['id']}"] + [f"Parameters={p}" for p in PARAMS] +
                        ["DataPeriods=16", f"StartDateTime={bas}", f"EndDateTime={bit}"])
        try:
            j = json.loads(_curl(f"{BASE}/STN/STN_Report/StationDataDownloadNewData", jar, data=data,
                                 headers=["X-Requested-With: XMLHttpRequest", f"Referer: {BASE}/STN/STN_Report/StationDataDownloadNew"]))
            rows = (j.get("Object") or {}).get("Data") or []
        except Exception:
            rows = []
        for r in rows:
            c.execute("INSERT OR REPLACE INTO hava_gunluk VALUES (?,?,?,?,?,?)", (s["id"], r["ReadTime"][:10], r.get("PM10"), r.get("PM25"), r.get("SO2"), r.get("NO2")))
        ok += bool(rows)
        if i % 25 == 0:
            c.commit(); print(f"  {i}/{len(st)} veri gelen {ok}", flush=True)
        time.sleep(0.4)
    c.commit()
    for t, k in (("hava_istasyon", "ÇŞB SİM UHKİA istasyon listesi"), ("hava_gunluk", f"ÇŞB SİM günlük ortalama (son {gun} gün)")):
        c.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?,?)", (t, c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], now, k))
    c.execute("ANALYZE"); c.commit(); c.close()
    print("tamam; veri gelen istasyon:", ok, "/", len(st))


if __name__ == "__main__":
    main()
