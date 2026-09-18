#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Lojistik, Teslimat Dolapları ve Dark Store Madencisi
------------------------------------------------------------------
KURAL 1 & 2: Kesinlikle uydurma, sentetik (mock) veri, fallback YOKTUR.
Her bir fiziksel teslimat noktasının KESİN (lat, lon) koordinatı zorunludur.

Hedeflenen Markalar:
- Trendyol (Gel-Al Noktaları, Express Şubeleri)
- Amazon (Lockers / Teslimat Dolapları)
- Getir / Banabi / Yemeksepeti Market (Dark Stores - Fiziksel depolar)
- PTT, Yurtiçi, Aras, MNG, Sürat Kargo Şubeleri

Çıktı: warehouse/product/lojistik_ve_darkstore_envanteri.sqlite
"""

import os
import sys
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DB = REPO_ROOT / "warehouse" / "product" / "lojistik_ve_darkstore_envanteri.sqlite"

def init_db(conn):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS fiziksel_lojistik_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marka TEXT NOT NULL,
        nokta_tipi TEXT NOT NULL, -- (Örn: Teslimat Dolabı, Kargo Şubesi, Dark Store)
        isim TEXT NOT NULL,
        il TEXT,
        ilce TEXT,
        tam_adres TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        ek_ozellikler TEXT, -- (JSON formatında çalışma saatleri vs)
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(marka, lat, lon)
    );
    CREATE INDEX IF NOT EXISTS idx_lojistik_geo ON fiziksel_lojistik_noktalari(lat, lon);
    CREATE INDEX IF NOT EXISTS idx_lojistik_marka ON fiziksel_lojistik_noktalari(marka, nokta_tipi);
    """)

def insert_locations(conn, locations):
    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0
    for loc in locations:
        if loc.get("lat") is None or loc.get("lon") is None:
            continue  # KURAL: Koordinat zorunludur, yoksa atla (mock/fallback YASAK)
            
        try:
            conn.execute("""
            INSERT OR REPLACE INTO fiziksel_lojistik_noktalari 
            (marka, nokta_tipi, isim, il, ilce, tam_adres, lat, lon, ek_ozellikler, guncellenme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                loc["marka"],
                loc["nokta_tipi"],
                loc["isim"],
                loc.get("il"),
                loc.get("ilce"),
                loc.get("tam_adres"),
                float(loc["lat"]),
                float(loc["lon"]),
                json.dumps(loc.get("ek_ozellikler", {}), ensure_ascii=False) if loc.get("ek_ozellikler") else None,
                now_iso
            ))
            written += 1
        except Exception as e:
            continue
    conn.commit()
    return written

def fetch_trendyol_gelal():
    import urllib.request
    import json
    locations = []
    print("[+] Trendyol Gel-Al ve Lockers noktaları taranıyor...")
    
    # 30 Büyükşehir için örnek merkez koordinatları (Bounding box taraması simülasyonu)
    # API: https://public-mdc.trendyol.com/discovery-web-pudo-service/api/pudos/map
    # Gerçek sistemde bu koordinatlar 81 il için grid_iterator'dan gelecek
    cities = [
        {"name": "Istanbul", "lat": 41.0082, "lon": 28.9784},
        {"name": "Ankara", "lat": 39.9334, "lon": 32.8597},
        {"name": "Izmir", "lat": 38.4237, "lon": 27.1428}
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    for city in cities:
        try:
            # Örnek payload, Trendyol harita API'si latitude/longitude ve radius ile çalışır
            url = f"https://public-mdc.trendyol.com/discovery-web-pudo-service/api/pudos/map?latitude={city['lat']}&longitude={city['lon']}&distance=50000"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    for pudo in data.get("pudos", []):
                        if not pudo.get("latitude") or not pudo.get("longitude"):
                            continue
                            
                        locations.append({
                            "marka": "Trendyol",
                            "nokta_tipi": "Gel-Al Teslimat Dolabı" if "Locker" in pudo.get("name", "") else "Esnaf Teslimat Noktası",
                            "isim": pudo.get("name", "Bilinmeyen PUDO"),
                            "il": city["name"],
                            "ilce": pudo.get("district", ""),
                            "tam_adres": pudo.get("address", ""),
                            "lat": float(pudo["latitude"]),
                            "lon": float(pudo["longitude"]),
                            "ek_ozellikler": {"workingHours": pudo.get("workingHours")}
                        })
        except Exception as e:
            print(f"  [-] {city['name']} Trendyol API Hatası: {e}")
            
    print(f"  -> {len(locations)} adet Trendyol Gel-Al noktası bulundu.")
    return locations

def fetch_amazon_lockers():
    # Amazon TR teslimat dolapları API'si veya harita kazıması
    print("[+] Amazon Lockers taranıyor... (API Entegrasyonu Eklenecek)")
    return []

def fetch_ptt_kargomat():
    # PTT kargomat lokasyonları (Önceki betikten alınabilir)
    print("[+] PTT Kargomatlar taranıyor... (API Entegrasyonu Eklenecek)")
    return []

def main():
    print("🚀 GEOPROP Lojistik, Teslimat ve Dark Store Veri Aktarımı Başlıyor...")
    os.makedirs(OUT_DB.parent, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)
    
    # 1. Trendyol Noktaları
    ty_locs = fetch_trendyol_gelal()
    w1 = insert_locations(conn, ty_locs)
    
    # 2. Amazon Lockers
    amz_locs = fetch_amazon_lockers()
    w2 = insert_locations(conn, amz_locs)
    
    # 3. PTT Kargomat
    ptt_locs = fetch_ptt_kargomat()
    w3 = insert_locations(conn, ptt_locs)
    
    conn.close()
    print("✅ Tüm lojistik noktaları (sadece kesin lokasyonu olanlar) ambarlandı.")

if __name__ == "__main__":
    main()
