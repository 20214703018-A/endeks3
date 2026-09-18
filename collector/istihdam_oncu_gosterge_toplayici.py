#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - İstihdam ve Öncü Gösterge (İş İlanları) Toplayıcı
=============================================================
Bu bot, bir bölgedeki "Gelecek" ticari canlılığı ve soylulaştırmayı (gentrification)
öngörmek için Kariyer ve Açık İş platformlarındaki spesifik iş ilanlarını tarar.

Hedef Meslekler (Öncü Göstergeler):
- "Barista", "Mağaza Müdürü", "Kurye", "Aşçı", "Kasiyer"
Eğer bir mahallede/ilçede lüks bir kahve zinciri veya süpermarket "Mağaza Müdürü" 
arıyorsa, o dükkan 1-2 ay içinde oraya açılacak demektir (Öncü Gösterge).

Çıktı: warehouse/product/istihdam_oncu_gostergeler.sqlite
"""

import os
import sys
import sqlite3
import time
import random
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
from curl_cffi import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO, "warehouse", "product", "istihdam_oncu_gostergeler.sqlite")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

MESLEKLER = [
    "Barista", 
    "Mağaza Müdürü", 
    "Kurye", 
    "Aşçı", 
    "Gayrimenkul Danışmanı",
    "Güzellik Uzmanı"
]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS bolgesel_istihdam_talebi (
        ilce TEXT,
        meslek TEXT,
        aktif_ilan_sayisi INTEGER,
        ornek_sirketler TEXT,
        kaynak TEXT,
        guncellenme_tarihi TEXT,
        PRIMARY KEY (ilce, meslek, guncellenme_tarihi)
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS istihdam_ham_ilanlar (
        ilan_id TEXT PRIMARY KEY,
        meslek_grubu TEXT,
        ilce TEXT,
        ilan_basligi TEXT,
        sirket_adi TEXT,
        lokasyon TEXT,
        yayinlanma_tarihi TEXT,
        ilan_linki TEXT,
        kaynak TEXT,
        cekilme_tarihi TEXT
    )
    ''')
    conn.commit()
    return conn

def search_public_jobs(shard_id=1, num_shards=1):
    import json
    import math
    print(f"🚀 [İSTİHDAM ÖNCÜ GÖSTERGE] Spesifik Ticari Rol Taraması Başlıyor... (Shard: {shard_id}/{num_shards})")
    
    ilceler = []
    rehber_path = os.path.join(REPO, "collector", "turkiye_il_ilce_rehberi.json")
    if os.path.exists(rehber_path):
        with open(rehber_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in data.items():
                for ilce in v.get("ilceler", []):
                    ilceler.append(f"{ilce['county_name']} {v['city_name']}")
    else:
        print("[-] turkiye_il_ilce_rehberi.json bulunamadı.")
        return

    ilceler = sorted(list(set(ilceler)))
    if num_shards > 1:
        step = math.ceil(len(ilceler) / num_shards)
        start = (shard_id - 1) * step
        end = start + step
        ilceler = ilceler[start:end]
        
    conn = init_db()
    cur = conn.cursor()
    now_iso = datetime.now().isoformat()
    
    total_ilan = 0
    
    for ilce in ilceler:
        for meslek in MESLEKLER:
            query = f"{meslek} {ilce}"
            url = f"https://www.linkedin.com/jobs/search?keywords={urllib.parse.quote(query)}&location={urllib.parse.quote('Turkey')}&f_TPR=r2592000"
            
            # Curl_cffi ile kendimizi Chrome olarak gösterip bloklanmayı engelliyoruz
            try:
                retry_count = 0
                max_retries = 3
                resp = None
                while retry_count < max_retries:
                    resp = requests.get(url, impersonate="chrome110", timeout=10)
                    if resp.status_code == 429:
                        print(f"  [!] {ilce} - 429 Too Many Requests, bekleniyor ({retry_count+1}/{max_retries})...")
                        time.sleep(5 * (retry_count + 1))
                        retry_count += 1
                    else:
                        break
                        
                if resp and resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    # Toplam ilan sayısını çekme
                    title_elem = soup.find('h1')
                    count = 0
                    if title_elem:
                        # "15 Barista iş ilanı, Kadıköy" gibi başlıkları ayrıştır
                        text_val = title_elem.text.strip().replace("+", "").replace(",", "")
                        nums = [int(s) for s in text_val.split() if s.isdigit()]
                        if nums:
                            count = nums[0]
                    
                    # HAM İLANLARI ÇEKME VE KAYDETME
                    companies = []
                    raw_count = 0
                    job_cards = soup.find_all('div', class_='base-search-card__info')
                    
                    for card in soup.find_all('a', class_='base-card__full-link'):
                        # A etiketinin yanındaki/içindeki bilgileri bul
                        parent = card.parent
                        title_el = parent.find('h3', class_='base-search-card__title')
                        comp_el = parent.find('h4', class_='base-search-card__subtitle')
                        loc_el = parent.find('span', class_='job-search-card__location')
                        date_el = parent.find('time')
                        
                        ilan_basligi = title_el.text.strip() if title_el else "Bilinmiyor"
                        sirket = comp_el.text.strip() if comp_el else "Bilinmiyor"
                        lokasyon = loc_el.text.strip() if loc_el else "Bilinmiyor"
                        tarih = date_el.get('datetime') if date_el else "Bilinmiyor"
                        link = card.get('href', '').split('?')[0]  # Parametreleri temizle
                        ilan_id = link.split('-')[-1] if '-' in link else link[-10:]
                        
                        if sirket not in companies:
                            companies.append(sirket)
                            
                        # Ham ilanı kaydet
                        cur.execute('''
                            INSERT OR IGNORE INTO istihdam_ham_ilanlar 
                            (ilan_id, meslek_grubu, ilce, ilan_basligi, sirket_adi, lokasyon, yayinlanma_tarihi, ilan_linki, kaynak, cekilme_tarihi)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (ilan_id, meslek, ilce, ilan_basligi, sirket, lokasyon, tarih, link, "LinkedIn", now_iso))
                        raw_count += 1
                        
                    if count > 0:
                        ornekler = ", ".join(companies[:3])
                        cur.execute(
                            "INSERT OR REPLACE INTO bolgesel_istihdam_talebi VALUES (?, ?, ?, ?, ?, ?)",
                            (ilce, meslek, count, ornekler, "LinkedIn Public Jobs", now_iso)
                        )
                        total_ilan += count
                        print(f"  ✓ {ilce} - {meslek}: {count} toplam hacim | {raw_count} ham ilan diske yazıldı (Örn: {ornekler})")
                    
                time.sleep(random.uniform(1.5, 3.0))
            except Exception as e:
                print(f"  [!] {ilce} {meslek} taraması hatası: {str(e)}")
                
    conn.commit()
    conn.close()
    print(f"✅ İstihdam Taraması Tamamlandı. Toplam {total_ilan} spesifik ticari öncü gösterge kaydı ambarlandı.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, default=1)
    parser.add_argument("--num-shards", type=int, default=1)
    args = parser.parse_args()
    search_public_jobs(args.shard, args.num_shards)
