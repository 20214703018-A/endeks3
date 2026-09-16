"""İBB Birleştirilmiş Stratejik Gürültü Haritası (2024, TÜBİTAK MAM) → cevre.sqlite::gurultu_hat (+ R-tree)

Kaynak: data.ibb.gov.tr GeoJSON (EPSG:3857) — 55 / 65 / 75 dB eş-gürültü **çizgileri** (LineString), Lgündüz ve Lgece.
Çizgi olduğu için "parsel X dB bandında" kesin söylenemez; motor her seviye için en yakın çizgiye mesafeyi verir
("65 dB gündüz hattına 40 m" gibi). Geometri 4326'ya çevrilip 2 m toleransla sadeleştirilir, WKB olarak saklanır.
Yalnız İstanbul. Streaming JSON okuma (ijson yoksa tam yükleme: ~240 MB dosya, ~1,5 GB RAM).

  python3.13 collector/ibb_gurultu_toplayici.py
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

from shapely.geometry import LineString, shape
from shapely import wkb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
RAW = os.path.join(REPO, "warehouse", "raw", "ibb")
OUT = os.path.join(REPO, "warehouse", "product", "cevre.sqlite")
DOSYALAR = (("gunduz", "gurultu_lgunduz.geojson"), ("gece", "gurultu_lgece.geojson"))


def _3857_to_4326(x, y):
    lon = x / 20037508.34 * 180
    lat = math.degrees(2 * math.atan(math.exp(y / 20037508.34 * math.pi)) - math.pi / 2)
    return lon, lat


def main():
    c = sqlite3.connect(OUT)
    c.executescript("""
    DROP TABLE IF EXISTS gurultu_hat; DROP TABLE IF EXISTS gurultu_rtree;
    CREATE TABLE gurultu_hat (id INTEGER PRIMARY KEY, donem TEXT, seviye_db INTEGER, uzunluk_m REAL, geom BLOB);
    CREATE VIRTUAL TABLE gurultu_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);
    CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
    """)
    rid = 0
    for donem, ad in DOSYALAR:
        p = os.path.join(RAW, ad)
        if not os.path.exists(p):
            print("yok:", ad); continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        n = 0
        for ft in data["features"]:
            g = ft["geometry"]
            if g["type"] != "LineString" or len(g["coordinates"]) < 2:
                continue
            pts = [_3857_to_4326(x, y) for x, y in g["coordinates"]]
            line = LineString(pts).simplify(0.00002, preserve_topology=False)   # ≈2 m
            b = line.bounds
            rid += 1
            c.execute("INSERT INTO gurultu_hat VALUES (?,?,?,?,?)", (rid, donem, int(ft["properties"].get("ISOVALUE") or 0), ft["properties"].get("SHAPE_Length"), wkb.dumps(line)))
            c.execute("INSERT INTO gurultu_rtree VALUES (?,?,?,?,?)", (rid, b[1], b[3], b[0], b[2]))
            n += 1
        c.commit(); print(donem, "çizgi:", n)
        del data
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('gurultu_hat', ?, ?, 'İBB Birleştirilmiş Stratejik Gürültü Haritası 2024 (55/65/75 dB eş-gürültü çizgileri, Lgündüz/Lgece)')",
              (rid, datetime.now(timezone.utc).isoformat()))
    c.commit(); c.execute("ANALYZE"); c.commit(); c.close(); print("tamam", rid)


if __name__ == "__main__":
    main()
