#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - QR Menü ve Web Sitesi Fiyat Toplayıcı (Bulut Motoru)
Bu script, Google Places üzerinden bulunmuş olan işletmelerin "web_sitesi" linklerini okur.
Adisyo, FineDine, Qrmenu.com.tr gibi sağlayıcıları algılar ve arka plandan (JSON) fiyatlarını söker.
"""

import os
import re
import json
import math
import time
import argparse
import urllib.request
import urllib.error
import sqlite3

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLACES_DB = os.path.join(BASE_DIR, "warehouse/product/fiziksel_ticari_isletmeler.sqlite")
MENU_DB = os.path.join(BASE_DIR, "warehouse/product/restoran_ve_kafe_menuleri.sqlite")

def init_db():
    conn = sqlite3.connect(MENU_DB)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mekan_menu_kalemleri_ve_fiyat_tarihcesi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mekan_id TEXT NOT NULL,
        mekan_adi TEXT NOT NULL,
        donem TEXT NOT NULL,
        tarih TEXT NOT NULL,
        fiyat_turu TEXT NOT NULL,
        platform TEXT NOT NULL,
        kategori TEXT NOT NULL,
        urun_adi TEXT NOT NULL,
        aciklama TEXT,
        fiyat REAL NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(mekan_id, urun_adi, donem, fiyat_turu)
    )""")
    conn.commit()
    return conn

def get_websites_from_db(shard_id, num_shards):
    if not os.path.exists(PLACES_DB):
        print("⚠️ HATA: Google Places veritabanı bulunamadı. Lütfen önce Places toplayıcısının bitmesini bekleyin.")
        return []
        
    try:
        conn = sqlite3.connect(PLACES_DB)
        cur = conn.cursor()
        cur.execute("""
            SELECT google_place_id, mekan_adi, web_sitesi 
            FROM isletme_tarihsel_yasam_dongusu 
            WHERE web_sitesi IS NOT NULL AND web_sitesi != ''
            ORDER BY google_place_id
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"⚠️ Veritabanı okuma hatası: {e}")
        return []

    if not shard_id or not num_shards or num_shards <= 1:
        return rows
        
    step = math.ceil(len(rows) / num_shards)
    start = (shard_id - 1) * step
    end = start + step
    return rows[start:end]

def fetch_html(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8', errors='ignore')
    except:
        return ""

def extract_qrmenu_com_tr(html, mekan_id, mekan_adi, conn):
    """qrmenu.com.tr sağlayıcısı için genel bir HTML regex veya JSON ayıklayıcı"""
    # Basit bir Regex örneği (gerçekte sağlayıcının state'ine bakılır)
    items = re.findall(r'<div class="product-name">([^<]+)</div>.*?<div class="product-price">([0-9.,]+)', html, re.DOTALL)
    
    # Alternatif: JSON LD veya JavaScript içindeki window.initialState
    json_match = re.search(r'window\.menuData\s*=\s*({.*?});', html)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            items = []
            for cat in data.get('categories', []):
                for prod in cat.get('products', []):
                    items.append((prod['name'], str(prod['price'])))
        except:
            pass

    return save_items(items, mekan_id, mekan_adi, "QRMenu.com.tr", conn)

def extract_adisyo(url, mekan_id, mekan_adi, conn):
    """Adisyo, QR menüleri genellikle API'den çeker."""
    # Adisyo URL'si örn: adisyo.com/menu/mekan-adi
    html = fetch_html(url)
    items = re.findall(r'"productName"\s*:\s*"([^"]+)".*?"price"\s*:\s*([0-9.]+)', html)
    return save_items(items, mekan_id, mekan_adi, "Adisyo", conn)

def extract_generic_prices(html, mekan_id, mekan_adi, conn):
    """Bilinmeyen sitelerden Regex ile zorla fiyat kazıma."""
    # <title> veya H1, H2, class="price" arar
    items = []
    # Çok basit bir heuristic: (Ürün Adı) ... (Fiyat) TL
    matches = re.findall(r'>\s*([A-Za-zÇŞĞÜÖİçşğüöı\s]{3,30})\s*<.*?([0-9]+[,.][0-9]{2})\s*(?:TL|₺)', html, re.IGNORECASE)
    for m in matches:
        items.append((m[0].strip(), m[1]))
    return save_items(items, mekan_id, mekan_adi, "Generic Web", conn)

def save_items(items, mekan_id, mekan_adi, platform, conn):
    if not items:
        return 0
        
    cur = conn.cursor()
    count = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    donem = time.strftime("%Y-%m")
    
    for name, price_str in items:
        try:
            price = float(price_str.replace(',', '.'))
            if price <= 0: continue
            
            cur.execute("""
                INSERT OR IGNORE INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi 
                (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori, urun_adi, aciklama, fiyat, guncellenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mekan_id, mekan_adi, donem, now, "Fiziksel_QR", platform, "Menü", name, "", price, now))
            count += 1
        except:
            pass
            
    conn.commit()
    return count

def process_venue(venue, conn):
    mekan_id, mekan_adi, url = venue
    
    if not url.startswith('http'):
        url = 'http://' + url

    print(f"🔍 Taranıyor: {mekan_adi[:20]} -> {url[:30]}...")
    html = fetch_html(url)
    if not html:
        print("  ❌ Siteye ulaşılamadı veya timeout.")
        return

    extracted_count = 0
    if "qrmenu.com" in url.lower() or "qr" in html.lower()[:2000]:
        extracted_count = extract_qrmenu_com_tr(html, mekan_id, mekan_adi, conn)
    elif "adisyo.com" in url.lower():
        extracted_count = extract_adisyo(url, mekan_id, mekan_adi, conn)
    else:
        # Genel site kazıma
        extracted_count = extract_generic_prices(html, mekan_id, mekan_adi, conn)

    if extracted_count > 0:
        print(f"  ✅ Başarılı: {extracted_count} adet ürün/fiyat çıkartıldı!")
    else:
        print("  ⚠️ Menü veya fiyat deseni bulunamadı.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, default=1)
    parser.add_argument("--num-shards", type=int, default=1)
    args = parser.parse_args()

    conn = init_db()
    venues = get_websites_from_db(args.shard, args.num_shards)
    
    print(f"🚀 [QR VE WEB MENÜ MOTORU] Makine {args.shard}/{args.num_shards}: {len(venues)} adet web sitesi tarayacak.")
    
    for venue in venues:
        process_venue(venue, conn)

if __name__ == "__main__":
    main()
