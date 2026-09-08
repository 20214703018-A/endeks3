#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hızlı POI (Perakende/Donatı) ve Sayfalamalı Emlak Ofisi & Danışman Rehberi Toplayıcısı
--------------------------------------------------------------------------------------
- POI Modu: İlçelerdeki tüm BİM, A101, Migros, kafe, eczane, banka, durak vb. noktaları ?size=2000 ile eksiksiz toplar.
- Ofis/Danışman Modu: Türkiye genelindeki tüm emlak ofislerini ve danışmanlarını şehir slug'ı ile sayfalamalı olarak telefonlarıyla çeker.
"""

import os
import sys
import csv
import json
import time
import random
import sqlite3
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_OUT_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "piyasa_verileri.db"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
]

API_EJ_TREND = "https://www.emlakjet.com/api/endeksa/dynamictrend"
API_EJ_POI = "https://api.emlakjet.com/e6t/v1/location/district/{county_id}/poi?size=2000"
API_EJ_OFFICES = "https://api.emlakjet.com/atlas-real-estate-office/v1/listing/emlak-ofisleri/{city_slug}"
API_EJ_AGENTS = "https://api.emlakjet.com/atlas-real-estate-office/v1/listing/danismanlar/{city_slug}"

ILLER_SLUG = {
    1: 'adana', 2: 'adiyaman', 3: 'afyonkarahisar', 4: 'agri', 5: 'amasya', 6: 'ankara', 7: 'antalya', 8: 'artvin',
    9: 'aydin', 10: 'balikesir', 11: 'bilecik', 12: 'bingol', 13: 'bitlis', 14: 'bolu', 15: 'burdur', 16: 'bursa',
    17: 'canakkale', 18: 'cankiri', 19: 'corum', 20: 'denizli', 21: 'diyarbakir', 22: 'edirne', 23: 'elazig', 24: 'erzincan',
    25: 'erzurum', 26: 'eskisehir', 27: 'gaziantep', 28: 'giresun', 29: 'gumushane', 30: 'hakkari', 31: 'hatay', 32: 'isparta',
    33: 'mersin', 34: 'istanbul', 35: 'izmir', 36: 'kars', 37: 'kastamonu', 38: 'kayseri', 39: 'kirklareli', 40: 'kirsehir',
    41: 'kocaeli', 42: 'konya', 43: 'kutahya', 44: 'malatya', 45: 'manisa', 46: 'kahramanmaras', 47: 'mardin', 48: 'mugla',
    49: 'mus', 50: 'nevsehir', 51: 'nigde', 52: 'ordu', 53: 'rize', 54: 'sakarya', 55: 'samsun', 56: 'siirt',
    57: 'sinop', 58: 'sivas', 59: 'tekirdag', 60: 'tokat', 61: 'trabzon', 62: 'tunceli', 63: 'sanliurfa', 64: 'usak',
    65: 'van', 66: 'yozgat', 67: 'zonguldak', 68: 'aksaray', 69: 'bayburt', 70: 'karaman', 71: 'kirikkale', 72: 'batman',
    73: 'sirnak', 74: 'bartin', 75: 'ardahan', 76: 'igdir', 77: 'yalova', 78: 'karabuk', 79: 'kilis', 80: 'osmaniye', 81: 'duzce'
}

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "[*]", "SUCCESS": "[✓]", "WARN": "[!]", "ERROR": "[✗]"}
    print(f"{timestamp} {symbols.get(level, '[*]')} {msg}", flush=True)

class PoliteHTTP:
    def __init__(self, delay=0.3, max_retries=5):
        self.delay = delay
        self.max_retries = max_retries

    def get(self, url):
        time.sleep(self.delay + random.uniform(0.05, 0.15))
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.emlakjet.com/"
        }
        req = urllib.request.Request(url, headers=headers)
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = resp.read()
                    if not raw: return None
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):
                    wait = (3 * attempt) + random.uniform(1, 2)
                    log(f"Hız limiti (HTTP {e.code}). {wait:.1f} sn bekleniyor...", "WARN")
                    time.sleep(wait)
                elif e.code in (404, 400):
                    return None
                else:
                    if attempt == self.max_retries: return None
                    time.sleep(1.5 * attempt)
            except Exception:
                if attempt == self.max_retries: return None
                time.sleep(1.0)
        return None

def init_db(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS poi_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_id INTEGER,
        county_id INTEGER,
        bolge_adi TEXT,
        poi_id INTEGER UNIQUE,
        kategori_id INTEGER,
        alt_kategori TEXT,
        poi_adi TEXT,
        slug TEXT
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS emlak_ofisleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_id INTEGER,
        il_adi TEXT,
        ofis_id INTEGER UNIQUE,
        ofis_adi TEXT,
        slug TEXT,
        adres TEXT,
        telefon TEXT,
        danisman_sayisi INTEGER,
        ilan_sayisi INTEGER
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS danismanlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_id INTEGER,
        il_adi TEXT,
        danisman_id TEXT UNIQUE,
        danisman_adi TEXT,
        unvan TEXT,
        ofis_adi TEXT,
        telefon TEXT,
        aktif_ilan_sayisi INTEGER
    );
    """)
    conn.commit()

def run_poi(http, conn, iller_listesi):
    cur = conn.cursor()
    log("POI Perakende Taraması Başlıyor (size=2000)...", "INFO")
    
    toplam_poi = 0
    for city_id in iller_listesi:
        res = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=1&CityId={city_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=true&Trend=false&Types=false&Wkt=")
        ilceler = res.get("Static", []) if res else []
        log(f"İl ID {city_id}: {len(ilceler)} ilçe için POI noktaları toplanıyor...", "INFO")

        for ilce in ilceler:
            county_id = int(ilce.get("CountyId", 0))
            county_name = ilce.get("CountyName") or ilce.get("DisplayName", "")
            if not county_id: continue

            bolge_tam = f"İl {city_id} - {county_name}"
            poi_data = http.get(API_EJ_POI.format(county_id=county_id))
            if not poi_data or not isinstance(poi_data, dict): continue

            pois = poi_data.get("result") or []
            eklenen = 0
            for p in pois:
                pid = p.get("id")
                if not pid: continue
                cur.execute("""
                INSERT OR REPLACE INTO poi_noktalari (city_id, county_id, bolge_adi, poi_id, kategori_id, alt_kategori, poi_adi, slug)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (city_id, county_id, bolge_tam, pid, p.get("categoryId"), p.get("subCategoryName"), p.get("name"), p.get("slug")))
                eklenen += 1

            conn.commit()
            toplam_poi += eklenen
            log(f"  ✓ {county_name}: {eklenen} POI noktası kaydedildi.", "INFO")

    log(f"POI taraması bitti. Toplam {toplam_poi} nokta kaydedildi.", "SUCCESS")

def run_ofis_ve_danisman(http, conn, iller_listesi):
    cur = conn.cursor()
    log("Sayfalamalı Emlak Ofisi ve Danışman Rehberi Taraması Başlıyor...", "INFO")

    toplam_ofis = 0
    toplam_danisman = 0

    for city_id in iller_listesi:
        city_slug = ILLER_SLUG.get(city_id, str(city_id))
        il_adi = city_slug.capitalize()

        # 1. Emlak Ofisleri Sayfalama
        log(f"İl ID {city_id} ({city_slug}) - Emlak Ofisleri taranıyor...", "INFO")
        ilk_sayfa = http.get(f"{API_EJ_OFFICES.format(city_slug=city_slug)}?page=1")
        if ilk_sayfa and isinstance(ilk_sayfa, dict):
            max_page = min(ilk_sayfa.get("MaximumPage") or 1, 50)
            total_count = ilk_sayfa.get("TotalCount") or 0
            log(f"  {city_slug}: Toplam {total_count} ofis, {max_page} sayfa...", "INFO")

            for page in range(1, max_page + 1):
                data = ilk_sayfa if page == 1 else http.get(f"{API_EJ_OFFICES.format(city_slug=city_slug)}?page={page}")
                if not data or not isinstance(data, dict): continue

                for o in data.get("Results") or []:
                    oid = o.get("Id")
                    if not oid: continue
                    cur.execute("""
                    INSERT OR REPLACE INTO emlak_ofisleri (city_id, il_adi, ofis_id, ofis_adi, slug, adres, telefon, danisman_sayisi, ilan_sayisi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (city_id, il_adi, oid, o.get("Name"), o.get("Slug"), o.get("LocationSummary"), o.get("PhoneNumber"), o.get("RegionCount"), o.get("ListingCount")))
                    toplam_ofis += 1
                conn.commit()

        # 2. Danışmanlar Sayfalama
        log(f"İl ID {city_id} ({city_slug}) - Danışmanlar taranıyor...", "INFO")
        ilk_d_sayfa = http.get(f"{API_EJ_AGENTS.format(city_slug=city_slug)}?page=1")
        if ilk_d_sayfa and isinstance(ilk_d_sayfa, dict):
            max_d_page = min(ilk_d_sayfa.get("MaximumPage") or 1, 50)
            total_d_count = ilk_d_sayfa.get("TotalCount") or 0
            log(f"  {city_slug}: Toplam {total_d_count} danışman, {max_d_page} sayfa...", "INFO")

            for page in range(1, max_d_page + 1):
                data = ilk_d_sayfa if page == 1 else http.get(f"{API_EJ_AGENTS.format(city_slug=city_slug)}?page={page}")
                if not data or not isinstance(data, dict): continue

                for d in data.get("Results") or []:
                    did = str(d.get("Id", ""))
                    if not did: continue
                    cur.execute("""
                    INSERT OR REPLACE INTO danismanlar (city_id, il_adi, danisman_id, danisman_adi, unvan, ofis_adi, telefon, aktif_ilan_sayisi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (city_id, il_adi, did, f"{d.get('Name', '')} {d.get('Surname', '')}".strip(), d.get("DistrictName"), d.get("OfficeName"), d.get("PhoneNumber"), d.get("Last1MonthListingCount")))
                    toplam_danisman += 1
                conn.commit()

    log(f"Ofis & Danışman taraması bitti: Toplam {toplam_ofis} ofis, {toplam_danisman} danışman kaydedildi.", "SUCCESS")

def export_to_csv(conn):
    CSV_OUT_DIR.mkdir(parents=True, exist_ok=True)
    cur = conn.cursor()
    tablo_eslesme = {
        "poi_noktalari": "08_ilce_onemli_noktalar_poi",
        "emlak_ofisleri": "09_emlak_ofisleri_rehberi",
        "danismanlar": "10_gayrimenkul_danismanlari"
    }
    for tablo, dosya_adi in tablo_eslesme.items():
        try:
            cur.execute(f"PRAGMA table_info({tablo})")
            kolonlar = [row[1] for row in cur.fetchall()]
            if not kolonlar: continue
            cur.execute(f"SELECT * FROM {tablo}")
            rows = cur.fetchall()
            if not rows: continue
            hedef = CSV_OUT_DIR / f"{dosya_adi}.csv"
            with open(hedef, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(kolonlar)
                writer.writerows(rows)
            log(f"CSV Üretildi: {dosya_adi}.csv ({len(rows)} satır)", "SUCCESS")
        except Exception as e:
            log(f"CSV aktarma hatası ({tablo}): {e}", "ERROR")

def main():
    parser = argparse.ArgumentParser(description="Hızlı POI ve Sayfalamalı Emlak Rehberi Toplayıcı")
    parser.add_argument("--mod", choices=["poi", "ofis", "hepsi"], required=True, help="Çalışma modu")
    parser.add_argument("--iller", type=str, required=True, help="Plaka/ID listesi (örn: 34 veya 6,35,42)")
    parser.add_argument("--hiz", type=float, default=0.25, help="İstekler arası bekleme süresi")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    http = PoliteHTTP(delay=args.hiz)
    iller_listesi = [int(x.strip()) for x in args.iller.split(",") if x.strip().isdigit()]

    if args.mod in ("poi", "hepsi"):
        run_poi(http, conn, iller_listesi)
    if args.mod in ("ofis", "hepsi"):
        run_ofis_ve_danisman(http, conn, iller_listesi)

    export_to_csv(conn)
    conn.close()
    log(f"Tüm işlemler tamamlandı. Veritabanı: {DB_PATH}", "SUCCESS")

if __name__ == "__main__":
    main()
