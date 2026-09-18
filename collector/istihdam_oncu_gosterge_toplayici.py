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

# Hedef ilçeler (Pilot olarak Batı Büyükşehirleri ve İstanbul'un değerli ilçeleri)
ILCELER = [
    "Kadıköy", "Şişli", "Beşiktaş", "Sarıyer", "Ataşehir", "Bakırköy", 
    "Çankaya", "Nilüfer", "Karşıyaka", "Bornova", "Muratpaşa", "Bodrum"
]

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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bolgesel_istihdam_talebi (
        ilce TEXT,
        meslek TEXT,
        aktif_ilan_sayisi INTEGER,
        ornek_sirketler TEXT,
        kaynak TEXT,
        guncellenme_tarihi TEXT,
        PRIMARY KEY (ilce, meslek, guncellenme_tarihi)
    )
    """)
    conn.commit()
    return conn

def search_public_jobs():
    print("🚀 [İSTİHDAM ÖNCÜ GÖSTERGE] Spesifik Ticari Rol Taraması Başlıyor...")
    conn = init_db()
    cur = conn.cursor()
    now_iso = datetime.now().isoformat()
    
    total_ilan = 0
    
    for ilce in ILCELER:
        for meslek in MESLEKLER:
            query = f"{meslek} {ilce}"
            url = f"https://www.linkedin.com/jobs/search?keywords={urllib.parse.quote(query)}&location={urllib.parse.quote('Turkey')}&f_TPR=r2592000"
            
            # Curl_cffi ile kendimizi Chrome olarak gösterip bloklanmayı engelliyoruz
            try:
                resp = requests.get(url, impersonate="chrome110", timeout=10)
                if resp.status_code == 200:
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
                    
                    # Örnek şirketleri çekme
                    companies = []
                    for comp in soup.find_all('h4', class_='base-search-card__subtitle'):
                        c_name = comp.text.strip()
                        if c_name and c_name not in companies:
                            companies.append(c_name)
                    
                    if count > 0:
                        ornekler = ", ".join(companies[:3])
                        cur.execute(
                            "INSERT INTO bolgesel_istihdam_talebi VALUES (?, ?, ?, ?, ?, ?)",
                            (ilce, meslek, count, ornekler, "LinkedIn Public Jobs", now_iso)
                        )
                        total_ilan += count
                        print(f"  ✓ {ilce} - {meslek}: {count} ilan bulundu. (Örn: {ornekler})")
                    
                time.sleep(random.uniform(1.5, 3.0))
            except Exception as e:
                print(f"  [!] {ilce} {meslek} taraması hatası: {str(e)}")
                
    conn.commit()
    conn.close()
    print(f"✅ İstihdam Taraması Tamamlandı. Toplam {total_ilan} spesifik ticari öncü gösterge kaydı ambarlandı.")

if __name__ == "__main__":
    search_public_jobs()
