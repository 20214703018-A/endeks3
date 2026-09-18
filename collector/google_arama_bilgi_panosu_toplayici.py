#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Google Arama (Knowledge Panel) Menü Toplayıcı
Özellikler:
- Hedef iller: Antalya, İstanbul, Ankara, Bursa, Konya, Eskişehir, Muğla, İzmir, Mersin, Aydın.
- Nüfusu 30.000'den küçük ilçeleri ve 5.000'den küçük mahalleleri EKLER/ATLAR.
- Lokal (headless=False) çalışıp CAPTCHA'ya yakalanmadan menü ve görsel çeker.
"""

import sys
import os
import re
import time
import sqlite3
import urllib.parse
from playwright.sync_api import sync_playwright

DB_MENU = "warehouse/product/restoran_ve_kafe_menuleri.sqlite"
DB_STATS = "warehouse/product/bolge_istatistik.sqlite"
TARGET_CITIES = ['Antalya', 'İstanbul', 'Ankara', 'Bursa', 'Konya', 'Eskişehir', 'Muğla', 'İzmir', 'Mersin', 'Aydın']
MIN_COUNTY_POP = 30000
MIN_NEIGHBORHOOD_POP = 5000

def normalize_tr(text):
    if not text: return ""
    text = text.lower()
    text = text.replace('ı', 'i').replace('i̇', 'i').replace('ğ', 'g')
    text = text.replace('ü', 'u').replace('ş', 's').replace('ö', 'o').replace('ç', 'c')
    text = re.sub(r'[^a-z0-9]', '', text)
    return text

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

def get_valid_locations():
    valid_counties = set()
    valid_neighborhoods = set()
    
    if os.path.exists(DB_STATS):
        conn = sqlite3.connect(DB_STATS)
        cur = conn.cursor()
        
        # İlçeler (Nüfus >= 30,000)
        cur.execute(f"""
            SELECT b.ad, a.bolge_adi
            FROM demografi a 
            JOIN ref_il b ON a.city_id = b.city_id 
            WHERE a.seviye = 'ilce' 
              AND b.ad IN ({','.join(['?']*len(TARGET_CITIES))})
              AND a.nufus_toplam >= ?
        """, (*TARGET_CITIES, MIN_COUNTY_POP))
        for row in cur.fetchall():
            ilce_ad = row[1].split(' - ')[-1].strip()
            valid_counties.add((normalize_tr(row[0]), normalize_tr(ilce_ad)))
            
        # Mahalleler (Nüfus >= 5,000)
        cur.execute(f"""
            SELECT b.ad, a.mahalle_norm
            FROM demografi a 
            JOIN ref_il b ON a.city_id = b.city_id 
            WHERE a.seviye = 'mahalle' 
              AND b.ad IN ({','.join(['?']*len(TARGET_CITIES))})
              AND a.nufus_toplam >= ?
        """, (*TARGET_CITIES, MIN_NEIGHBORHOOD_POP))
        for row in cur.fetchall():
            valid_neighborhoods.add((normalize_tr(row[0]), row[1]))
            
        conn.close()
        print(f"Bölge İstatistikleri: {len(valid_counties)} geçerli ilçe, {len(valid_neighborhoods)} geçerli mahalle yüklendi.")
    return valid_counties, valid_neighborhoods

def get_target_venues(valid_counties, valid_neighborhoods):
    places_db = "warehouse/product/google_places_ve_yogunluk.sqlite"
    if not os.path.exists(places_db):
        print(f"Mekan veritabanı bulunamadı: {places_db}")
        return []
        
    conn = sqlite3.connect(places_db)
    cur = conn.cursor()
    
    # 1. Bütün Restoran ve Kafeleri Çek
    cur.execute(f"""
        SELECT google_place_id, isim, ilce, il, mahalle, yorum_sayisi
        FROM google_places_ticari_yogunluk
        WHERE il IN ({','.join(['?']*len(TARGET_CITIES))})
          AND yorum_sayisi >= 50
          AND (
              ana_kategori LIKE '%Restoran%' OR 
              ana_kategori LIKE '%Kafe%' OR 
              ana_kategori LIKE '%Lokanta%' OR 
              ana_kategori LIKE '%Pastane%' OR 
              ana_kategori LIKE '%Kahve%'
          )
        ORDER BY yorum_sayisi DESC
    """, (*TARGET_CITIES,))
    all_venues = cur.fetchall()
    conn.close()
    
    filtered_venues = []
    for v in all_venues:
        mekan_id, adi, ilce, il, mahalle, yorum = v
        il_norm = normalize_tr(il)
        ilce_norm = normalize_tr(ilce)
        mahalle_norm = normalize_tr(mahalle)
        
        # İlçe Kontrolü
        if valid_counties and (il_norm, ilce_norm) not in valid_counties:
            continue
            
        # Mahalle Kontrolü
        if mahalle_norm and valid_neighborhoods and (il_norm, mahalle_norm) not in valid_neighborhoods:
            continue
            
        filtered_venues.append({
            "id": mekan_id, "adi": adi, "ilce": ilce, "il": il, "mahalle": mahalle
        })
        
    return filtered_venues

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
        print("  ❌ CAPTCHA EKRANI GELDİ! Lütfen açılan tarayıcı penceresinde 'Ben Robot Değilim' kutusunu işaretleyin. 30 saniye bekleniyor...")
        for _ in range(30):
            time.sleep(1)
            if "CAPTCHA" not in page.title() and "Robot" not in page.title():
                print("  ✅ CAPTCHA ÇÖZÜLDÜ! Devam ediliyor...")
                break
        else:
            print("  ⚠️ CAPTCHA çözülemedi, mekan atlanıyor.")
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
    conn = init_db()
    valid_counties, valid_neighborhoods = get_valid_locations()
    venues = get_target_venues(valid_counties, valid_neighborhoods)
    
    if not venues:
        print("Kriterlere uygun (Nüfus vb.) mekan bulunamadı!")
        return
        
    print(f"\n🚀 Playwright başlatılıyor... Hedef {len(venues)} mekan.")
    with sync_playwright() as p:
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
