#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Yemek ve Hızlı Market Teslimat Ekosistemi Toplayıcı
Resmî Yemeksepeti / Delivery Hero ekosisteminden:
- 139.968 Üye Restoran ve Mahalle Esnafı (Ad, koordinat, adres, mutfak türleri, fiyat segmenti)
- Darkstore depoları (Yemeksepeti Market, Getir Depo, Trendyol Go Hub)
- Değerlendirme ve sipariş hacimleri (ratingCount, ratingValue)
bilgilerini 40 sanal makinede paralel toplayıp warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite ambarına yazar.
4 saatlik emniyet zamanlayıcısına (max-seconds) sahiptir; süre dolduğunda otomatik kapanıp verileri ambarlar.
"""

import sys
import os
import re
import json
import hashlib
import time
import random
import argparse
import sqlite3
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from collector.toplayici_guvenligi import fetch_text
except ImportError:
    from toplayici_guvenligi import fetch_text

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DB = os.path.join(BASE_DIR, "warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite")
OSM_DB = os.path.join(BASE_DIR, "warehouse/product/osm_poi.sqlite")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15"
]

RESTAURANT_SITEMAPS = [
    "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-0.xml",
    "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-1.xml",
    "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-2.xml",
    "https://www.yemeksepeti.com/adventure-map/adventure-map-shop-0.xml"
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
        minimum_sepet_tutari REAL,
        gonderim_ucreti REAL,
        kategori TEXT,
        yorum_sayisi INTEGER,
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
    try:
        cur.execute("ALTER TABLE uye_restoranlar_ve_hacim ADD COLUMN yorum_sayisi INTEGER")
    except Exception:
        pass
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_sehir ON uye_restoranlar_ve_hacim(sehir, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_coords ON uye_restoranlar_ve_hacim(lat, lon)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_restoran_puan ON uye_restoranlar_ve_hacim(puan, degerlendirme_sayisi)")

    # 3. Mahalle Esnaf Noktaları (Bakkal, Manav, Kasap vb.)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mahalle_esnaf_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        esnaf_kodu TEXT UNIQUE,
        esnaf_adi TEXT NOT NULL,
        tur TEXT,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        minimum_sepet_tutari REAL,
        gonderim_ucreti REAL,
        kategori TEXT,
        yorum_sayisi INTEGER,
        sehir TEXT,
        ilce TEXT,
        tam_adres TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        url TEXT,
        kaynak TEXT DEFAULT 'Yemeksepeti Mahalle',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN ilce TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN tam_adres TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN puan REAL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN degerlendirme_sayisi INTEGER")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN minimum_sepet_tutari REAL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN gonderim_ucreti REAL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN kategori TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE mahalle_esnaf_noktalari ADD COLUMN yorum_sayisi INTEGER")
    except Exception:
        pass
    cur.execute("CREATE INDEX IF NOT EXISTS idx_esnaf_sehir ON mahalle_esnaf_noktalari(sehir, ilce)")

    # 4. Checkpoint / Tarama Geçmişi
    cur.execute("""
    CREATE TABLE IF NOT EXISTS yemeksepeti_tarama_gecmisi (
        url TEXT PRIMARY KEY,
        kod TEXT,
        tur TEXT,
        guncellenme_tarihi TEXT,
        durum TEXT,
        http_status INTEGER,
        deneme_sayisi INTEGER DEFAULT 0,
        hata_kodu TEXT,
        hata_detayi TEXT
    )
    """)
    for col_def in [
        ("durum", "TEXT"), ("http_status", "INTEGER"), ("deneme_sayisi", "INTEGER DEFAULT 0"),
        ("hata_kodu", "TEXT"), ("hata_detayi", "TEXT")
    ]:
        try:
            cur.execute(f"ALTER TABLE yemeksepeti_tarama_gecmisi ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass
    cur.execute("""
    CREATE TABLE IF NOT EXISTS yemeksepeti_isletme_gozlem (
        observation_id TEXT PRIMARY KEY,
        restoran_kodu TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        kaynak_url TEXT NOT NULL,
        UNIQUE(restoran_kodu, observed_at)
    )
    """)

    conn.commit()
    conn.close()

def get_url_content(url, timeout=12):
    result = fetch_text(url, headers={
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "*/*"
    }, timeout=timeout, attempts=3, min_bytes=1,
       blocked_markers=("Access to this page has been denied", "Attention Required"))
    return result.body


def fetch_venue_page(url, timeout=15):
    return fetch_text(url, headers={
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }, timeout=timeout, attempts=3, min_bytes=3000,
       blocked_markers=("Access to this page has been denied", "Attention Required"))

def fetch_darkstore_urls():
    url = "https://www.yemeksepeti.com/adventure-map/adventure-map-darkstore-0.xml"
    content = get_url_content(url)
    if not content:
        return []
    return re.findall(r"<loc>(.+?)</loc>", content)

def fetch_all_restaurant_and_shop_urls():
    urls = []
    for sm in RESTAURANT_SITEMAPS:
        content = get_url_content(sm)
        if content:
            found = re.findall(r"<loc>(.+?)</loc>", content)
            urls.extend(found)
    return urls

def parse_darkstore_page(url):
    html = get_url_content(url)
    if not html:
        return None
    
    scripts = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.+?)</script>', html, re.DOTALL)
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

def parse_venue_page(url, html=None):
    html = html if html is not None else get_url_content(url)
    if not html:
        return None
    
    scripts = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.+?)</script>', html, re.DOTALL)
    for s in scripts:
        try:
            j = json.loads(s)
            item_type = j.get("@type")
            if item_type in ["Restaurant", "FoodEstablishment", "LocalBusiness", "Store", "GroceryStore"]:
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
                review_cnt = rating.get("reviewCount") if isinstance(rating, dict) else None
                
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
                    
                    is_shop = ("/shop/" in url) or (item_type in ["LocalBusiness", "Store", "GroceryStore"])
                    platform = "YEMEKSEPETI_MAHALLE" if is_shop else "YEMEKSEPETI"
                    mutfaklar_str = json.dumps(cuisines, ensure_ascii=False) if cuisines else None
                    
                    val_deg = int(rating_cnt) if rating_cnt is not None else None
                    val_yor = int(review_cnt) if review_cnt is not None else val_deg

                    
                    # GEOPROP - Kategorizasyon, Min Sepet ve Teslimat Regex Enjeksiyonu
                    min_sepet = None
                    gonderim = None
                    kategori_kat = "Restoran"
                    try:
                        sep_match = re.search(r'Minimum Sepet Tutarı.*?(\d+)[.,]?\d*\s*TL', html, re.IGNORECASE)
                        if sep_match: min_sepet = float(sep_match.group(1))
                        
                        gon_match = re.search(r'Gönderim Ücreti.*?(\d+)[.,]?\d*\s*TL', html, re.IGNORECASE)
                        if gon_match: gonderim = float(gon_match.group(1))
                        elif 'Ücretsiz Gönderim' in html: gonderim = 0.0
                        
                        url_lower = url.lower()
                        if 'market' in url_lower or 'grocery' in html.lower(): kategori_kat = "Market"
                        elif 'petshop' in url_lower: kategori_kat = "Petshop"
                        elif 'su' in url_lower and 'damacana' in html.lower(): kategori_kat = "Su"
                        elif 'manav' in url_lower: kategori_kat = "Manav"
                        elif 'kasap' in url_lower: kategori_kat = "Kasap"
                    except:
                        pass

                    return {
                        "is_shop": is_shop,
                        "platform": platform,
                        "kategori": kategori_kat,
                        "minimum_sepet_tutari": min_sepet,
                        "gonderim_ucreti": gonderim,
                        "kod": code,
                        "isim": name,
                        "mutfaklar": mutfaklar_str,
                        "fiyat_segmenti": price,
                        "puan": float(rating_val) if rating_val is not None else None,
                        "degerlendirme_sayisi": val_deg,
                        "yorum_sayisi": val_yor,
                        "sehir": city,
                        "ilce": ilce,
                        "tam_adres": street,
                        "lat": float(lat),
                        "lon": float(lon),
                        "url": url,
                        "kaynak": "Yemeksepeti Mahalle" if is_shop else "Yemeksepeti Restoran Ekosistemi",
                        "guncellenme_tarihi": datetime.now(timezone.utc).isoformat()
                    }
        except Exception:
            continue
    return None


def fetch_and_parse_venue_page(url):
    result = fetch_venue_page(url)
    return parse_venue_page(url, result.body), result

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

def harvest_darkstores(out_db, shard_id=None, total_shards=40, workers=4):
    all_darkstore_urls = fetch_darkstore_urls()
    if not all_darkstore_urls:
        return 0
    
    if shard_id:
        step = max(1, len(all_darkstore_urls) // total_shards)
        start = (shard_id - 1) * step
        end = start + step if shard_id < total_shards else len(all_darkstore_urls)
        target_darkstores = all_darkstore_urls[start:end]
    else:
        target_darkstores = all_darkstore_urls
        
    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    saved_ds = 0
    
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
                print(f"  [Depo] Darkstore: {res['depo_adi']} | {res['sehir']}/{res['ilce']} | ({res['lat']:.4f}, {res['lon']:.4f})", flush=True)
                time.sleep(random.uniform(0.1, 0.2))

    conn.close()
    return saved_ds

def harvest_restaurants_and_shops(out_db, shard_id=None, total_shards=40, limit=None, workers=8, max_seconds=14400):
    init_db(out_db)
    print("🍽️ [RESTORAN & ESNAF] Yemeksepeti sitemap indeksleri (139.968 URL) taranıyor...", flush=True)
    
    all_urls = fetch_all_restaurant_and_shop_urls()
    if not all_urls:
        print("   ✗ Sitemap linkleri alınamadı.", flush=True)
        return 0
        
    if shard_id:
        step = max(1, len(all_urls) // total_shards)
        start = (shard_id - 1) * step
        end = start + step if shard_id < total_shards else len(all_urls)
        target_urls = all_urls[start:end]
        print(f"Shard {shard_id}/{total_shards}: Toplam {len(all_urls):,} linkten bu makineye tahsis edilen {len(target_urls):,} link taranacak.", flush=True)
    else:
        target_urls = all_urls
        print(f"Lokal mod: Toplam {len(target_urls):,} link taranacak.", flush=True)
        
    if limit and limit > 0:
        target_urls = target_urls[:limit]
        
    conn = sqlite3.connect(out_db)
    cur = conn.cursor()
    
    # Önceden tarananları kontrol et (Checkpoint)
    cur.execute("SELECT url FROM yemeksepeti_tarama_gecmisi WHERE durum='BASARILI'")
    done_urls = set(r[0] for r in cur.fetchall())
    pending_urls = [u for u in target_urls if u not in done_urls]
    
    print(f"Başlangıç Durumu: {len(target_urls):,} hedeften {len(done_urls):,} taranmış, {len(pending_urls):,} yeni işletme linki işleniyor.", flush=True)
    print(f"Emniyet Zamanlayıcısı: {max_seconds/3600:.1f} saat | İş parçacığı: {workers} paralel worker\n", flush=True)
    
    start_time = time.time()
    saved = 0
    batch_count = 0
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_and_parse_venue_page, u): u for u in pending_urls}
        for f in as_completed(futures):
            # 4 Saatlik Emniyet Zamanlayıcısı Kontrolü
            if time.time() - start_time >= max_seconds:
                print(f"\n[⏰ 4 Saatlik Emniyet Sınırına Ulaşıldı ({max_seconds} sn)] Toplanan tüm veriler ambarlandı, güvenli kapanış yapılıyor...", flush=True)
                break
                
            u = futures[f]
            res, fetch_result = f.result()
            now_utc = datetime.now(timezone.utc).isoformat()
            
            if res:
                if res["is_shop"]:
                    cur.execute("""
                    INSERT OR REPLACE INTO mahalle_esnaf_noktalari (
                        esnaf_kodu, esnaf_adi, tur, puan, degerlendirme_sayisi, yorum_sayisi,
                        sehir, ilce, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        res["kod"], res["isim"], res["mutfaklar"] or "Mahalle Esnafı",
                        res["puan"], res["degerlendirme_sayisi"], res.get("yorum_sayisi"),
                        res["sehir"], res["ilce"], res["tam_adres"], res["lat"], res["lon"],
                        res["url"], res["kaynak"], res["guncellenme_tarihi"]
                    ))
                    
                cur.execute("""
                INSERT OR REPLACE INTO uye_restoranlar_ve_hacim (
                    platform, restoran_kodu, restoran_adi, mutfaklar, fiyat_segmenti,
                    puan, degerlendirme_sayisi, yorum_sayisi, sehir, ilce, tam_adres, lat, lon, url, kaynak, guncellenme_tarihi
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    res["platform"], res["kod"], res["isim"], res["mutfaklar"],
                    res["fiyat_segmenti"], res["puan"], res["degerlendirme_sayisi"], res.get("yorum_sayisi"),
                    res["sehir"], res["ilce"], res["tam_adres"], res["lat"], res["lon"],
                    res["url"], res["kaynak"], res["guncellenme_tarihi"]
                ))
                saved += 1
                payload = json.dumps(res, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                observation_id = hashlib.sha256(
                    f"yemeksepeti|{res['kod']}|{res['guncellenme_tarihi']}|{payload_hash}".encode("utf-8")
                ).hexdigest()
                cur.execute("""
                INSERT OR IGNORE INTO yemeksepeti_isletme_gozlem
                    (observation_id, restoran_kodu, observed_at, payload_json, payload_sha256, kaynak_url)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (observation_id, res["kod"], res["guncellenme_tarihi"], payload, payload_hash, u))
                puan_str = f"Puan: {res['puan']}" if res['puan'] is not None else "Puan: -"
                deg_cnt = res.get('degerlendirme_sayisi')
                yor_cnt = res.get('yorum_sayisi')
                if deg_cnt is not None and yor_cnt is not None and deg_cnt != yor_cnt:
                    yorum_str = f"({deg_cnt} oy, {yor_cnt} yorum)"
                elif deg_cnt is not None:
                    yorum_str = f"({deg_cnt} değerlendirme)"
                else:
                    yorum_str = ""
                print(f"  -> [{saved}/{len(pending_urls)}] {res['platform']}: {res['isim']} | {res['sehir'] or '-'}/{res['ilce'] or '-'} | {puan_str} {yorum_str} | ({res['lat']:.4f}, {res['lon']:.4f})", flush=True)
                
            cur.execute("""
            INSERT OR REPLACE INTO yemeksepeti_tarama_gecmisi
                (url, kod, tur, guncellenme_tarihi, durum, http_status, deneme_sayisi, hata_kodu, hata_detayi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                u, res["kod"] if res else None, res["platform"] if res else None, now_utc,
                "BASARILI" if res else ("YENIDEN_DENE" if not fetch_result.ok else "AYRISTIRILAMADI"),
                fetch_result.status, fetch_result.attempts,
                None if res else (fetch_result.error_code or "PARSE_EMPTY"),
                None if res else (fetch_result.error_detail or "Isletme JSON-LD verisi bulunamadi"),
            ))
            
            batch_count += 1
            if batch_count % 50 == 0:
                conn.commit()
                
    conn.commit()
    conn.close()
    print(f"\n   ✓ İşlem tamamlandı: {saved} işletme ambarlandı. Çıktı DB: '{out_db}'", flush=True)
    return saved

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Yemek & Hızlı Market Teslimat Ekosistemi Toplayıcı (40 Shard)")
    parser.add_argument("--num-shards", type=int, default=40, help="Total shards")
    parser.add_argument("--shard", type=str, help="Shard no (örn: 1/40)")
    parser.add_argument("--out", type=str, default=DEFAULT_DB, help="Çıktı sqlite yolu")
    parser.add_argument("--workers", type=int, default=8, help="Paralel worker sayısı (varsayılan: 8)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum işlenecek link sayısı (varsayılan: sınırsız)")
    parser.add_argument("--max-seconds", type=int, default=14400, help="Maksimum çalışma süresi saniye (varsayılan: 14400 = 4 saat)")
    parser.add_argument("--no-darkstore", action="store_true", help="Darkstore taramasını atla")
    args = parser.parse_args()

    init_db(args.out)

    shard_id, total = None, 40
    if args.shard:
        if "/" in args.shard:
            shard_id = int(args.shard.split("/")[0])
            total = int(args.shard.split("/")[1])
        else:
            shard_id = int(args.shard)
            total = int(args.num_shards)
    else:
        total = int(args.num_shards)

    # 1. Darkstore depoları ve OSM noktaları
    if not args.no_darkstore:
        ds_saved = harvest_darkstores(args.out, shard_id=shard_id, total_shards=total, workers=min(4, args.workers))
        osm_saved = import_osm_delivery_depots(args.out)
        print(f"Darkstore ve OSM depoları ambarlandı: {ds_saved} Darkstore + {osm_saved} OSM deposu.", flush=True)

    # 2. Üye restoranlar ve mahalle esnafı (139.968 işletme havuzundan shard dilimi)
    harvest_restaurants_and_shops(
        args.out,
        shard_id=shard_id,
        total_shards=total,
        limit=args.limit,
        workers=args.workers,
        max_seconds=args.max_seconds
    )

if __name__ == "__main__":
    main()
