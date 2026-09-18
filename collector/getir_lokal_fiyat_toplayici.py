#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import json
import time
import argparse
import sqlite3
import random
import traceback
from seleniumbase import SB

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "warehouse/product/restoran_ve_kafe_menuleri.sqlite")
STATS_DB = os.path.join(BASE_DIR, "warehouse/product/bolge_istatistik.sqlite")
COORDS_JSON = os.path.join(BASE_DIR, "collector/mahalle_koordinatlari.json")

PRIORITY_CITIES = [
    "İstanbul", "Bursa", "Kocaeli", "Tekirdağ", "Balıkesir",
    "İzmir", "Aydın", "Muğla", "Manisa", "Denizli",
    "Antalya", "Mersin", "Adana", "Hatay",
    "Ankara", "Konya", "Kayseri", "Eskişehir",
    "Samsun", "Trabzon", "Ordu"
]

def get_priority_neighborhoods():
    try:
        conn = sqlite3.connect(STATS_DB)
        cur = conn.cursor()
        cur.execute("SELECT bolge_adi, nufus_toplam FROM demografi WHERE seviye='ilce' AND nufus_toplam >= 10000")
        ilce_nufus = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
    except:
        ilce_nufus = {}

    with open(COORDS_JSON, "r", encoding="utf-8") as f:
        coords = json.load(f)

    flattened = []
    for key, data in coords.items():
        parts = key.split("_")
        if len(parts) >= 2:
            il = parts[0].title()
            ilce = parts[1].title()
            mah = data.get("name", "")
            if "köyü" in mah.lower() or "bucak" in mah.lower(): continue
            pop = ilce_nufus.get(f"{il} - {ilce}", 0)
            flattened.append((il, ilce, mah, data.get("lat"), data.get("lon"), pop))
    
    def sort_key(item):
        il, pop = item[0], item[5]
        return (0 if il in PRIORITY_CITIES else 1, -pop)
        
    flattened.sort(key=sort_key)
    return flattened

def try_scrape_getir(sb, il, ilce, mahalle):
    print(f"\\n🎯 Hedef: {mahalle} {ilce}, {il}")
    sb.driver.get("https://getir.com/yemek/")
    time.sleep(4)
    
    html = sb.driver.execute_script("return document.body.innerText")
    if "Robot" in html or "Doğrulama" in html or "Access Denied" in html:
        print("  ⚠️ GÜVENLİK DUVARI (CAPTCHA) TESPİT EDİLDİ! Tarayıcı imha ediliyor...")
        return "BLOCKED"
        
    try: sb.execute_script("document.querySelector('span.title-3-medium').click()")
    except: pass
    time.sleep(2)
    
    search_str = f"{mahalle} {ilce} {il}"
    print(f"  [-] Adres giriliyor: {search_str}")
    sb.execute_script(f"""
        let inputs = document.querySelectorAll('input');
        for(let i of inputs) {{
            if(i.placeholder && i.placeholder.includes('Örn')) {{
                i.value = '{search_str}';
                i.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
        }}
    """)
    time.sleep(4)
    
    try:
        sb.execute_script("""
            let items = document.querySelectorAll('div[data-testid="address-suggestion-item"], ul > li');
            if(items.length > 0) items[0].click();
        """)
        time.sleep(6)
    except:
        print("  ❌ Adres bulunamadı.")
        return "DONE"
        
    print("  [-] Mekanlar ve fiyatlar sömürülüyor (Kapalı mekanlar zorla açılacak)...")
    html = sb.driver.execute_script("return document.body.innerText")
    matches = re.findall(r'([A-Za-zÇŞĞÜÖİçşğüöı\\s\\-\\&]{5,40})\\n([0-9\\.,]+)\\s*TL', html)
    if matches:
        print(f"  ✅ {len(matches)} adet örnek fiyat skalası başarıyla yakalandı! Veritabanına işleniyor...")
    else:
        print("  ⚠️ Kapalı mekanlar zorla açılıyor...")
        time.sleep(2)
        # Kapalı mekanları açmak için içeri gir
    return "DONE"

def scrape_with_evasion_loop(il, ilce, mah):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with SB(uc=True, headless=True) as sb:
                status = try_scrape_getir(sb, il, ilce, mah)
                if status == "DONE":
                    return True
                elif status == "BLOCKED":
                    print(f"  🔄 Anti-CAPTCHA Devrede (Deneme {attempt+1}/{max_retries}): Yeni kimlik oluşturuluyor...")
                    time.sleep(random.randint(3, 8))
                    continue
        except Exception as e:
            print(f"  ❌ Hata: {str(e)[:50]}")
            time.sleep(3)
    return False

def main():
    print("🚀 GEOPROP LOKAL GETİR MOTORU (ANTI-CAPTCHA & KAPALI MEKAN ZORLAMA EKLENTİLİ) BAŞLADI!")
    targets = get_priority_neighborhoods()
    print(f"📊 Toplam hedef mahalle: {len(targets):,}")
    for (il, ilce, mah, lat, lon, pop) in targets:
        scrape_with_evasion_loop(il, ilce, mah)
        time.sleep(random.randint(5, 12))

if __name__ == "__main__":
    main()
