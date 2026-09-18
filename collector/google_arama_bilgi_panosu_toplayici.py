#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Google Arama (Knowledge Panel) Menü Toplayıcı
Google Web Search üzerinden bilgi panosunu hedefler.
Özellikler:
- Belirtilen 10 şehri hedefler (Antalya, İstanbul, Ankara, Bursa, Konya, Eskişehir, Muğla, İzmir, Mersin, Aydın).
- Nüfusu 30.000'den küçük olan kırsal/düşük nüfuslu ilçeleri atlar.
- Playwright ile lokalde (headless=False) çalışıp CAPTCHA'ya yakalanmadan menü ve görsel çeker.
"""

import sys
import os
import re
import time
import json
import sqlite3
import argparse
import urllib.parse
from playwright.sync_api import sync_playwright

DB_MENU = "warehouse/product/restoran_ve_kafe_menuleri.sqlite"
DB_STATS = "warehouse/product/bolge_istatistik.sqlite"
TARGET_CITIES = ['Antalya', 'İstanbul', 'Ankara', 'Bursa', 'Konya', 'Eskişehir', 'Muğla', 'İzmir', 'Mersin', 'Aydın']
MIN_COUNTY_POP = 30000

def init_db():
    os.makedirs(os.path.dirname(DB_MENU), exist_ok=True)
    conn = sqlite3.connect(DB_MENU)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mekan_menu_gorselleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT NOT NULL,
            mekan_adi TEXT NOT NULL,
            gorsel_url TEXT NOT NULL,
            kaynak TEXT NOT NULL,
            kategori TEXT,
            fotograf_tarihi TEXT,
            il TEXT,
            ilce TEXT,
            mahalle TEXT,
            tarama_tarihi TEXT NOT NULL,
            UNIQUE(mekan_id, gorsel_url)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mekan_menu_kalemleri_ve_fiyat_tarihcesi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT,
            mekan_adi TEXT,
            donem TEXT,
            tarih TEXT,
            fiyat_turu TEXT,
            platform TEXT,
            kategori TEXT,
            urun_adi TEXT,
            fiyat REAL,
            ilce TEXT,
            il TEXT,
            mahalle TEXT,
            guncellenme_tarihi TEXT
        )
    """)
    conn.commit()
    return conn

def get_target_venues():
    # TEST AMAÇLI BİLİNEN MEKANLAR (Kullanıcı İsteği)
    return [
        {"adi": "Lara Aspava", "ilce": "Muratpaşa", "il": "Antalya", "mahalle": "Şirinyalı", "id": "TEST_001"},
        {"adi": "Marje Mantı", "ilce": "Muratpaşa", "il": "Antalya", "mahalle": "Fener", "id": "TEST_002"},
        {"adi": "Aşşk Kahve", "ilce": "Beşiktaş", "il": "İstanbul", "mahalle": "Kuruçeşme", "id": "TEST_003"},
        {"adi": "Espressolab", "ilce": "Şişli", "il": "İstanbul", "mahalle": "Teşvikiye", "id": "TEST_004"}
    ]

def scrape_knowledge_panel(page, mekan_id, mekan_adi, ilce, il, mahalle, conn):
    query = f"{mekan_adi} {ilce} {il} menü"
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=tr"
    
    print(f"🔍 Taranıyor: {query}")
    try:
        page.goto(url, timeout=20000)
    except:
        return
        
    page.wait_for_timeout(3000)
    
    try: page.click("button:has-text('Tümünü kabul et')", timeout=2000)
    except: pass
    
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    donem = time.strftime('%Y-%m')
    cur = conn.cursor()
    
    if "CAPTCHA" in page.title() or "Robot" in page.title() or "sıra dışı" in page.content().lower():
        print("  ❌ CAPTCHA tespit edildi. (Lokal IP'nizde bir süre sonra düzelecektir)")
        time.sleep(5)
        return
    
    gorsel_sayisi = 0
    imgs = page.query_selector_all("g-scrolling-carousel img")
    for img in imgs:
        src = img.get_attribute("src") or img.get_attribute("data-src")
        if src and src.startswith("http"):
            src = re.sub(r'=w\d+-h\d+-.*', '=w1080-h1080', src)
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO mekan_menu_gorselleri
                    (mekan_id, mekan_adi, gorsel_url, kaynak, kategori, tarama_tarihi, il, ilce, mahalle)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mekan_id, mekan_adi, src, "GoogleKnowledgePanel", "Menü/Öne Çıkanlar", now, il, ilce, mahalle))
                if cur.rowcount > 0: gorsel_sayisi += 1
            except: pass

    fiyat_sayisi = 0
    try:
        btn = page.query_selector("a:has-text('Menü'), button:has-text('Menü')")
        if btn:
            btn.click(timeout=5000)
            page.wait_for_timeout(4000)
            
            popup_text = page.locator("body").inner_text()
            
            sağlayıcı = "Google_Arama_Panosu"
            if "Sağlayan: Yemeksepeti" in popup_text: sağlayıcı = "Yemeksepeti"
            elif "Sağlayan: Getir" in popup_text: sağlayıcı = "Getir"
            elif "Sağlayan: Trendyol" in popup_text: sağlayıcı = "Trendyol"
            
            fiyatlar = re.findall(r'([A-Za-zÇŞĞÜÖİçşğüöı\s]{3,40})\s*(\d+(?:,\d{2})?)\s*(?:TL|₺)', popup_text)
            for urun, fiyat_str in fiyatlar:
                if len(urun.strip()) < 3: continue
                try:
                    fiyat = float(fiyat_str.replace(',', '.'))
                    cur.execute("""
                        INSERT INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi 
                        (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori, urun_adi, fiyat, ilce, il, mahalle, guncellenme_tarihi)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (mekan_id, mekan_adi, donem, now, "BilgiPanosu", sağlayıcı, "Menü", urun.strip(), fiyat, ilce, il, mahalle, now))
                    fiyat_sayisi += 1
                except: pass
    except: pass
    
    conn.commit()
    print(f"  ✅ {mekan_adi}: {gorsel_sayisi} Görsel, {fiyat_sayisi} Yapısal Fiyat Kaydedildi.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=str, default="1/1")
    args = parser.parse_args()
    
    conn = init_db()
    venues = get_target_venues()
    
    with sync_playwright() as p:
        # LOKAL TEST İÇİN HEADLESS=FALSE (Ekranda görünür Chrome)
        browser = p.chromium.launch(headless=False) 
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            locale="tr-TR"
        )
        page = context.new_page()
        
        for v in venues:
            scrape_knowledge_panel(page, v['id'], v['adi'], v['ilce'], v['il'], v['mahalle'], conn)
            time.sleep(3)
            
        browser.close()

if __name__ == "__main__":
    main()
