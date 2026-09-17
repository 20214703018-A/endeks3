#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Yemek ve Hızlı Market Teslimat Ekosistemi Toplayıcı
Resmî Yemeksepeti / Delivery Hero, Getir ve Trendyol Go ekosisteminden:
- Darkstore depoları (Yemeksepeti Market, Getir Depo, Trendyol Go Hub)
- Üye restoranlar (Ad, koordinat, açık adres, mutfak türleri, fiyat seviyesi)
- Sipariş ve değerlendirme hacimleri (ratingCount, ratingValue)
bilgilerini çeker ve warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite ambarına yazar.
40 Shard GitHub Actions ve lokal paralel çalışma desteğine sahiptir.
"""

import sys
import os
import re
import json
import time
import random
import argparse
import sqlite3
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DB = os.path.join(BASE_DIR, "warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite")
OSM_DB = os.path.join(BASE_DIR, "warehouse/product/osm_poi.sqlite")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15"
]

def init_db(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 1. Darkstore Depoları
    cur.execute("""
    CREATE TABLE IF NOT EXISTS teslimat_depolari_darkstore (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        depo_kodu TEXT UNIQUE,
        depo_adi TEXT NOT NULL,
        sehir TEXT,
        ilce TEXT,
        mahalle TEXT,
        tam_adres TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        url TEXT,
        kaynak TEXT DEFAULT 'Resmî Platform Sitemap & JSON-LD',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_darkstore_sehir ON teslimat_depolari_darkstore(sehir, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_darkstore_coords ON teslimat_depolari_darkstore(lat, lon)")

    # 2. Üye Restoranlar ve Hacimler
    cur.execute("""
    CREATE TABLE IF NOT EXISTS uye_restoranlar_ve_hacim (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        restoran_kodu TEXT UNIQUE,
        restoran_adi TEXT NOT NULL,
        mutfaklar TEXT,
        fiyat_segmenti TEXT,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        sehir TEXT,
        ilce TEXT,
        tam_adres TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        url TEXT,
        kaynak TEXT DEFAULT 'Yemeksepeti Restoran Ekosistemi',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_sehir ON uye_restoranlar_ve_hacim(sehir, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_coords ON uye_restoranlar_ve_hacim(lat, lon)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_puan ON uye_restoranlar_ve_hacim(puan, degerlendirme_sayisi)")

    conn.commit()
    conn.close()

def get_url_content(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "*/*"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

def fetch_darkstore_urls():
    url = "https://www.yemeksepeti.com/adventure-map/adventure-map-darkstore-0.xml"
    content = get_url_content(url)
    if not content:
        return []
    return re.findall(r"<loc>(.+?)</loc>", content)

def parse_darkstore_page(url):
    html = get_url_content(url)
    if not html:
        return None
    
    scripts = re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.+?)</script>', html, re.DOTALL)
    for s in scripts:
        try:
            j = json.loads(s)
            if j.get("@type") in ["Store", "GroceryStore", "LocalBusiness", "Restaurant"]:
                name = j.get("name")
                addr = j.get("address", {})
                geo = j.get("geo", {})
                
                street = addr.get("streetAddress", "") if isinstance(addr, dict) else ""
                city = addr.get("addressLocality", "") if isinstance(addr, dict) else ""
                lat = geo.get("latitude") if isinstance(geo, dict) else None
                lon = geo.get("longitude") if isinstance(geo, dict) else None
                
                if lat is not None and lon is not None:
                    parts = url.rstrip("/").split("/")
                    code = parts[-2] if len(parts) >= 2 else parts[-1]
                    
                    ilce = None
                    if "/" in street:
                        sub_parts = street.split("/")
                        if len(sub_parts) > 1:
                            ilce_cand = sub_parts[-2].strip().split(",")[-1].strip()
                            if ilce_cand:
                                ilce = ilce_cand
                    
                    return {
                        "platform": "YEMEKSEPETI_MARKET",
                        "depo_kodu": code,
                        "depo_adi": name,
                        "sehir": city,
                        "ilce": ilce,
                        "mahalle": None,
                        "tam_adres": street,
                        "lat": float(lat),
                        "lon": float(lon),
                        "url": url,
                        "kaynak": "Resmî Platform Sitemap & JSON-LD",
                        "guncellenme_tarihi": datetime.now(timezone.utc).isoformat()
                    }
        except Exception:
            continue
    return None

def parse_restaurant_page(url):
    html = get_url_content(url)
    if not html:
        return None
    
    scripts = re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.+?)</script>', html, re.DOTALL)
    for s in scripts:
        try:
            j = json.loads(s)
            if j.get("@type") in ["Restaurant", "FoodEstablishment"]:
                name = j.get("name")
                addr = j.get("address", {})
                geo = j.get("geo", {})
                rating = j.get("aggregateRating", {})
                price = j.get("priceRange")
                cuisines = j.get("servesCuisine")
                
                street = addr.get("streetAddress", "") if isinstance(addr, dict) else ""
                city = addr.get("addressLocality", "") if isinstance(addr, dict) else ""
                lat = geo.get("latitude") if isinstance(geo, dict) else None
                lon = geo.get("longitude") if isinstance(geo, dict) else None
                
                rating_val = rating.get("ratingValue") if isinstance(rating, dict) else None
                rating_cnt = rating.get("ratingCount") if isinstance(rating, dict) else None
                
                if lat is not None and lon is not None and name:
                    parts = url.rstrip("/").split("/")
                    code = parts[-2] if len(parts) >= 2 else parts[-1]
                    
                    ilce = None
                    if "/" in street:
                        sub_parts = street.split("/")
                        if len(sub_parts) > 1:
                            ilce_cand = sub_parts[-2].strip().split(",")[-1].strip()
                            if ilce_cand:
                                ilce = ilce_cand
                    
                    mutfaklar_str = json.dumps(cuisines, ensure_ascii=False) if cuisines else None
                    
                    return {
                        "platform": "YEMEKSEPETI",
                        "restoran_kodu": code,
                        "restoran_adi": name,
                        "mutfaklar": mutfaklar_str,
                        "fiyat_segmenti": price,
                        "puan": float(rating_val) if rating_val is not None else None,
                        "degerlendirme_sayisi": int(rating_cnt) if rating_cnt is not None else None,
                        "sehir": city,
                        "ilce": ilce,
                        "tam_adres": street,
                        "lat": float(lat),
                        "lon": float(lon),
                        "url": url,
                        "kaynak": "Yemeksepeti Restoran Ekosistemi",
                        "guncellenme_tarihi": datetime.now(timezone.utc).isoformat()
                    }
        except Exception:
            continue
    return None

def import_osm_delivery_depots(db_path):
    if not os.path.exists(OSM_DB):
        return 0
    osm_conn = sqlite3.connect(OSM_DB)
    osm_cur = osm_conn.cursor()
    rows = osm_cur.execute("""
    SELECT ad, lat, lon, etiketler 
    FROM poi 
    WHERE ad LIKE '%Getir%' 
       OR ad LIKE '%Yemeksepeti%' 
       OR ad LIKE '%Banabi%' 
       OR ad LIKE '%Trendyol%' 
       OR ad LIKE '%Migros Hemen%' 
       OR ad LIKE '%İstegelsin%'
    """).fetchall()
    osm_conn.close()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    added = 0
    now_utc = datetime.now(timezone.utc).isoformat()
    for ad, lat, lon, etiketler in rows:
        platform = "DIGER"
        if "Getir" in ad:
            platform = "GETIR"
        elif "Yemeksepeti" in ad or "Banabi" in ad:
            platform = "YEMEKSEPETI"
        elif "Trendyol" in ad:
            platform = "TRENDYOL_GO"
        elif "Migros" in ad:
            platform = "MIGROS_HEMEN"
            
        depo_kodu = f"OSM_{abs(hash(f'{ad}_{lat}_{lon}'))}"
        cur.execute("""
        INSERT OR IGNORE INTO teslimat_depolari_darkstore (
            platform, depo_kodu, depo_adi, sehir, ilce, mahalle, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (platform, depo_kodu, ad, None, None, None, ad, lat, lon, None, "OSM Doğrulanmış POI", now_utc))
        if cur.rowcount > 0:
            added += 1
    conn.commit()
    conn.close()
    return added

def run_collection(out_db, shard_id=None, total_shards=40, workers=4, limit=50):
    init_db(out_db)
    all_darkstore_urls = fetch_darkstore_urls()
    
    if shard_id:
        # Shard bölüşümü
        step = max(1, len(all_darkstore_urls) // total_shards)
        start = (shard_id - 1) * step
        end = start + step if shard_id < total_shards else len(all_darkstore_urls)
        target_darkstores = all_darkstore_urls[start:end]
        print(f"Shard {shard_id}/{total_shards}: {len(target_darkstores)} Darkstore işleniyor...")
    else:
        target_darkstores = all_darkstore_urls[:limit]
        print(f"Lokal mod: {len(target_darkstores)} Darkstore işleniyor...")

    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    saved_ds = 0
    
    # 429'a takılmamak için nazik thread havuzu
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_darkstore_page, u): u for u in target_darkstores}
        for f in as_completed(futures):
            res = f.result()
            if res:
                cur.execute("""
                INSERT OR REPLACE INTO teslimat_depolari_darkstore (
                    platform, depo_kodu, depo_adi, sehir, ilce, mahalle, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    res["platform"], res["depo_kodu"], res["depo_adi"], res["sehir"], res["ilce"],
                    res["mahalle"], res["tam_adres"], res["lat"], res["lon"], res["url"], res["kaynak"], res["guncellenme_tarihi"]
                ))
                conn.commit()
                saved_ds += 1
                print(f"  -> [{saved_ds}] Darkstore: {res['depo_adi']} | Şehir: {res['sehir']} | İlçe: {res['ilce']} | ({res['lat']:.4f}, {res['lon']:.4f})", flush=True)
                time.sleep(random.uniform(0.2, 0.5))

    conn.close()

    # OSM teslimat noktalarını da ekle
    osm_count = import_osm_delivery_depots(out_db)
    print(f"\nİşlem tamamlandı. {saved_ds} Darkstore + {osm_count} OSM teslimat noktası '{out_db}' dosyasına yazıldı.", flush=True)

def harvest_sample_restaurants(out_db, shard_id=None, total_shards=40, sample_limit=100, workers=3):
    init_db(out_db)
    print(f"🍽️ [ÜYE RESTORANLAR] Yemeksepeti sitemap taranıyor (Hedef: {sample_limit} restoran)...", flush=True)
    
    # 0.xml sitemap'ten linkleri al
    url = "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-0.xml"
    content = get_url_content(url)
    if not content:
        print("   ✗ Restoran sitemap açılamadı.", flush=True)
        return 0
    all_urls = re.findall(r"<loc>(.+?)</loc>", content)
    
    if shard_id:
        step = max(1, len(all_urls) // total_shards)
        start = (shard_id - 1) * step
        end = start + step if shard_id < total_shards else len(all_urls)
        shard_pool = all_urls[start:end]
        target_urls = random.sample(shard_pool, min(sample_limit, len(shard_pool)))
        print(f"Shard {shard_id}/{total_shards}: Havuzdaki {len(shard_pool)} linkten {len(target_urls)} restoran çekiliyor...", flush=True)
    else:
        target_urls = random.sample(all_urls, min(sample_limit, len(all_urls)))
    
    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    saved = 0
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_restaurant_page, u): u for u in target_urls}
        for f in as_completed(futures):
            res = f.result()
            if res:
                cur.execute("""
                INSERT OR REPLACE INTO uye_restoranlar_ve_hacim (
                    platform, restoran_kodu, restoran_adi, mutfaklar, fiyat_segmenti,
                    puan, degerlendirme_sayisi, sehir, ilce, tam_adres, lat, lon,
                    url, kaynak, guncellenme_tarihi
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    res["platform"], res["restoran_kodu"], res["restoran_adi"], res["mutfaklar"],
                    res["fiyat_segmenti"], res["puan"], res["degerlendirme_sayisi"], res["sehir"],
                    res["ilce"], res["tam_adres"], res["lat"], res["lon"], res["url"],
                    res["kaynak"], res["guncellenme_tarihi"]
                ))
                conn.commit()
                saved += 1
                print(f"  -> [{saved}/{sample_limit}] Restoran: {res['restoran_adi']} | {res['sehir']} | Puan: {res['puan']} ({res['degerlendirme_sayisi']} yorum) | Mutfak: {res['mutfaklar']}", flush=True)
                time.sleep(random.uniform(0.2, 0.5))
                
    conn.close()
    print(f"   ✓ {saved} üye restoran 'uye_restoranlar_ve_hacim' tablosuna ambarlandı.", flush=True)
    return saved

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Yemek & Hızlı Market Teslimat Ekosistemi Toplayıcı (40 Shard)")
    parser.add_argument("--shard", type=str, help="Shard no (örn: 1/40)")
    parser.add_argument("--out", type=str, default=DEFAULT_DB, help="Çıktı sqlite yolu")
    parser.add_argument("--workers", type=int, default=4, help="Paralel worker")
    parser.add_argument("--limit", type=int, default=50, help="Lokal darkstore limit")
    parser.add_argument("--restaurants", type=int, default=100, help="Çekilecek üye restoran sayısı")
    args = parser.parse_args()

    if args.shard:
        shard_id, total = map(int, args.shard.split("/"))
        run_collection(args.out, shard_id=shard_id, total_shards=total, workers=args.workers)
        if args.restaurants > 0:
            harvest_sample_restaurants(args.out, shard_id=shard_id, total_shards=total, sample_limit=args.restaurants, workers=min(3, args.workers))
    else:
        run_collection(args.out, workers=args.workers, limit=args.limit)
        if args.restaurants > 0:
            harvest_sample_restaurants(args.out, sample_limit=args.restaurants, workers=min(3, args.workers))

if __name__ == "__main__":
    main()
