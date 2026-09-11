#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tüm Türkiye Mahalle Sınır Poligonları Toplayıcısı
-------------------------------------------------
81 il dosyasındaki (city_*.json) 973 ilçenin gerçek Geo CountyId'lerini kullanarak
Emlakjet Geo API'sinden Türkiye'deki tüm mahallelerin sınır koordinatlarını
(county_{city}_{county}.json) ve birleşik GeoJSON harita dosyasını indirir.
"""

import json
import time
import random
import urllib.request
import urllib.error
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
POLYGONS_DIR = DATA_DIR / "poligonlar"
GEOJSON_OUT = DATA_DIR / "turkiye_tum_mahalle_poligonlari.geojson"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
]

API_EJ_POLYGONS = "https://www.emlakjet.com/api/geo/polygons"

def fetch_county_polygons(job):
    city_id, county_id, county_name = job
    target_file = POLYGONS_DIR / f"county_{city_id}_{county_id}.json"
    if target_file.exists():
        try:
            with open(target_file) as f:
                d = json.load(f)
                if d and d.get("polygons"):
                    return city_id, county_id, county_name, len(d.get("polygons", [])), True
        except Exception:
            pass

    url = f"{API_EJ_POLYGONS}?level=2&cityId={city_id}&countyId={county_id}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Referer": "https://www.emlakjet.com/"
    }
    req = urllib.request.Request(url, headers=headers)
    time.sleep(random.uniform(0.02, 0.05))

    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data and isinstance(data, dict) and data.get("polygons"):
                    with open(target_file, "w", encoding="utf-8") as f:
                        json.dump({
                            "city_id": city_id,
                            "county_id": county_id,
                            "county_name": county_name,
                            "polygons": data["polygons"]
                        }, f, ensure_ascii=False)
                    return city_id, county_id, county_name, len(data["polygons"]), True
                return city_id, county_id, county_name, 0, False
        except Exception:
            time.sleep(0.5 * attempt)
    return city_id, county_id, county_name, 0, False

def main():
    parser = argparse.ArgumentParser(
        description="Mevcut ilçe rehberinden mahalle poligonlarını ağ üzerinden toplar"
    )
    parser.add_argument(
        "--calistir",
        action="store_true",
        help="Ağ taramasını açıkça başlat",
    )
    args = parser.parse_args()
    if not args.calistir:
        parser.print_help()
        return

    POLYGONS_DIR.mkdir(parents=True, exist_ok=True)
    jobs = []
    for city_file in sorted(POLYGONS_DIR.glob("city_*.json")):
        with open(city_file) as f:
            d = json.load(f)
            for p in d.get("polygons", []):
                props = p.get("properties", {})
                cid = props.get("CityId")
                coid = props.get("CountyId")
                cname = props.get("County")
                if cid and coid:
                    jobs.append((cid, coid, cname))

    print(f"[*] Toplam {len(jobs)} ilçe taranarak mahalle poligonları çekiliyor...")

    toplam_mahalle = 0
    basarili_ilce = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_county_polygons, job): job for job in jobs}
        for idx, fut in enumerate(as_completed(futures), 1):
            cid, coid, cname, count, ok = fut.result()
            if ok and count > 0:
                basarili_ilce += 1
                toplam_mahalle += count
            if idx % 50 == 0 or idx == len(jobs):
                print(f"[{idx}/{len(jobs)}] Tamamlanan İlçe: {basarili_ilce} | Toplam Çekilen Mahalle Poligonu: {toplam_mahalle}")

    print(f"\n[✓] TÜM TÜRKİYE MAHALLE POLİGONLARI TAMAMLANDI!")
    print(f"    Başarılı İlçe: {basarili_ilce}/973")
    print(f"    Toplam Mahalle Poligonu: {toplam_mahalle}")

if __name__ == "__main__":
    main()
