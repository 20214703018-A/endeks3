#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Google Arama (Knowledge Panel) Menü Toplayıcı
Kullanıcının gönderdiği ekran görüntülerine istinaden özel olarak yazılmıştır.
Google Haritalar yerine doğrudan Google Arama (Web Search) panosunu hedefler.
Çektiği Veriler:
1. "Menüde öne çıkanlar" (Yemek fotoğrafları)
2. "Menüyü göster" (Fiziksel menü sayfalarının fotoğrafları)
3. "Sağlayan: Yemeksepeti" (Yapısal metin fiyatları ve kategoriler)
"""

import sys
import os
import re
import time
import json
import sqlite3
import argparse
from playwright.sync_api import sync_playwright

DB_PATH = "warehouse/product/restoran_ve_kafe_menuleri.sqlite"
TARGET_CITIES = ['Antalya', 'İstanbul', 'Mersin', 'Ankara', 'Bursa', 'Çanakkale', 'Aydın', 'Eskişehir', 'Muğla', 'Trabzon', 'Rize', 'Konya', 'Edirne', 'Tekirdağ']

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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

def get_target_venues(conn, shard_id, num_shards):
    # Dummy listesi - Gerçekte fiziksel_ticari_isletmeler'den alınabilir
    return [
        {"adi": "Lara Aspava", "ilce": "Muratpaşa", "il": "Antalya"},
        {"adi": "Marje Mantı", "ilce": "Muratpaşa", "il": "Antalya"},
        {"adi": "Aşşk Kahve", "ilce": "Beşiktaş", "il": "İstanbul"}
    ]

def scrape_knowledge_panel(page, mekan_adi, ilce, il, conn):
    query = f"{mekan_adi} {ilce} {il} menü"
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=tr"
    
    print(f"🔍 Taranıyor: {query}")
    page.goto(url, timeout=20000)
    page.wait_for_timeout(3000)
    
    try: page.click("button:has-text('Tümünü kabul et')", timeout=2000)
    except: pass
    
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    donem = time.strftime('%Y-%m')
    cur = conn.cursor()
    
    # 1. Fotoğrafları Topla (Menüde öne çıkanlar & Menüyü göster)
    gorsel_sayisi = 0
    # "Menüde öne çıkanlar" veya "Menüyü göster" altındaki görseller
    # Google bilgi panosunda data-attrid="kc:/local:menu" vs bulunur
    imgs = page.query_selector_all("g-scrolling-carousel img")
    for img in imgs:
        src = img.get_attribute("src") or img.get_attribute("data-src")
        if src and src.startswith("http"):
            # Yüksek çözünürlüklü halini almaya çalış
            src = re.sub(r'=w\d+-h\d+-.*', '=w1080-h1080', src)
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO mekan_menu_gorselleri
                    (mekan_id, mekan_adi, gorsel_url, kaynak, kategori, tarama_tarihi, il, ilce)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (mekan_adi, mekan_adi, src, "GoogleKnowledgePanel", "Menü/Öne Çıkanlar", now, il, ilce))
                if cur.rowcount > 0: gorsel_sayisi += 1
            except: pass

    # 2. Yapısal Menüyü (Sağlayan: Yemeksepeti vs) Çek
    fiyat_sayisi = 0
    try:
        # Menü butonuna tıkla
        btn = page.query_selector("a:has-text('Menü'), button:has-text('Menü')")
        if btn:
            btn.click(timeout=5000)
            page.wait_for_timeout(4000) # Popup'ın yüklenmesini bekle
            
            # Popup içindeki metni al
            popup_text = page.locator("body").inner_text()
            
            sağlayıcı = "Google_Arama_Panosu"
            if "Sağlayan: Yemeksepeti" in popup_text: sağlayıcı = "Yemeksepeti"
            elif "Sağlayan: Getir" in popup_text: sağlayıcı = "Getir"
            
            # Fiyatları eşleştir
            fiyatlar = re.findall(r'([A-Za-zÇŞĞÜÖİçşğüöı\s]{3,40})\s*(\d+(?:,\d{2})?)\s*(?:TL|₺)', popup_text)
            for urun, fiyat_str in fiyatlar:
                if len(urun.strip()) < 3: continue
                try:
                    fiyat = float(fiyat_str.replace(',', '.'))
                    cur.execute("""
                        INSERT INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi 
                        (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori, urun_adi, fiyat, ilce, il, guncellenme_tarihi)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (mekan_adi, mekan_adi, donem, now, "BilgiPanosu", sağlayıcı, "Menü", urun.strip(), fiyat, ilce, il, now))
                    fiyat_sayisi += 1
                except: pass
    except: pass
    
    conn.commit()
    print(f"  ✅ {mekan_adi}: {gorsel_sayisi} Görsel, {fiyat_sayisi} Yapısal Fiyat Kaydedildi.")

import urllib.parse
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=str, default="1/40")
    args = parser.parse_args()
    
    conn = init_db()
    venues = get_target_venues(conn, 1, 1)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            locale="tr-TR"
        )
        page = context.new_page()
        
        for v in venues:
            scrape_knowledge_panel(page, v['adi'], v['ilce'], v['il'], conn)
            
        browser.close()

if __name__ == "__main__":
    main()
