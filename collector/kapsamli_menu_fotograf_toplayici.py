#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Kapsamlı Menü ve Menü Fotoğrafı Toplayıcı
Sadece belirli illerde (14 il), mahalle mahalle Restoran, Kafe ve Gece Kulübü taraması yapar.
Google'dan menü fiyatlarını, güncellenme tarihlerini ve menü fotoğraflarını çeker.
Google'da yoksa mekanın kendi web sitesine gidip menü/fiyat taraması yapar.
"""

import os
import re
import json
import time
import math
import argparse
import urllib.request
import urllib.parse
import sqlite3

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MENU_DB = os.path.join(BASE_DIR, "warehouse/product/restoran_ve_kafe_menuleri.sqlite")
STATS_DB = os.path.join(BASE_DIR, "warehouse/product/bolge_istatistik.sqlite")

TARGET_CITIES = [
    "Antalya", "İstanbul", "Mersin", "Ankara", "Bursa", 
    "Çanakkale", "Aydın", "Eskişehir", "Muğla", "Trabzon", 
    "Rize", "Konya", "Edirne", "Tekirdağ"
]
CATEGORIES = ["Restoran", "Kafe", "Gece Kulübü"]

def init_db():
    os.makedirs(os.path.dirname(MENU_DB), exist_ok=True)
    conn = sqlite3.connect(MENU_DB)
    cur = conn.cursor()
    # Fiyat kalemleri tablosu
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
        orijinal_fiyat REAL,
        para_birimi TEXT DEFAULT 'TRY',
        fiyat_guncellenme_zamani TEXT,
        tam_adres TEXT,
        mahalle TEXT,
        ilce TEXT NOT NULL,
        il TEXT NOT NULL,
        lat REAL,
        lon REAL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(mekan_id, urun_adi, donem, fiyat_turu)
    )""")
    
    # Menü fotoğrafları tablosu
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mekan_menu_gorselleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mekan_id TEXT NOT NULL,
        mekan_adi TEXT NOT NULL,
        gorsel_url TEXT NOT NULL,
        kaynak TEXT NOT NULL,
        fotograf_tarihi TEXT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        tarama_tarihi TEXT NOT NULL,
        UNIQUE(mekan_id, gorsel_url)
    )""")
    conn.commit()
    return conn

def get_mahalleler_by_shard(shard_id, num_shards):
    """Hedef illerdeki tüm mahalleleri veritabanından çeker ve shard'lara böler."""
    if not os.path.exists(STATS_DB):
        # Fallback dummy listesi (DB yoksa)
        print("⚠️ bolge_istatistik.sqlite bulunamadı, fallback kullanılıyor...")
        all_mahalle = [f"Merkez {il}" for il in TARGET_CITIES]
    else:
        conn = sqlite3.connect(STATS_DB)
        cur = conn.cursor()
        
        # Eğer ref_mahalle veya idari_sinirlar tablosu kullanılıyorsa
        # bolge_istatistik veritabanında il, ilce, mahalle hiyerarşisi var
        try:
            placeholders = ",".join("?" * len(TARGET_CITIES))
            cur.execute(f"""
                SELECT r_il.ad, r_ilce.ad, r_mahalle.ad 
                FROM ref_mahalle r_mahalle
                JOIN ref_ilce r_ilce ON r_mahalle.county_id = r_ilce.county_id
                JOIN ref_il r_il ON r_ilce.city_id = r_il.city_id
                WHERE r_il.ad IN ({placeholders})
                ORDER BY r_il.ad, r_ilce.ad, r_mahalle.ad
            """, TARGET_CITIES)
            rows = cur.fetchall()
            all_mahalle = [f"{r[2]} {r[1]} {r[0]}" for r in rows]
        except Exception as e:
            print(f"⚠️ Mahalle çekme hatası: {e}. Fallback kullanılıyor.")
            all_mahalle = [f"Merkez {il}" for il in TARGET_CITIES]
        finally:
            conn.close()

    # Eğer hiç mahalle gelmezse, en azından illeri arayalım
    if not all_mahalle:
        all_mahalle = TARGET_CITIES

    # Shard hesaplama
    step = math.ceil(len(all_mahalle) / num_shards)
    start = (shard_id - 1) * step
    end = start + step
    return all_mahalle[start:end]

def fetch_google_pb(query):
    import urllib.parse, urllib.request
    encoded_q = urllib.parse.quote(query)
    pb_param = (
        "!4m12!1m3!1d150000!2d35.0!3d39.0"
        "!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1"
        "!7i20!8i20!10b1!12m8!1m1!18b1!2m3!5m1!6e2!20e3!10b1!16b1"
        "!19m4!2m3!1i360!2i120!4i8"
        "!20m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        "!7m42!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3"
        "!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e9!2b1!3e2!1m3!1e10!2b0!3e3"
        "!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!2b1!4b1!9b0"
        "!22m3!1s!7e81!15b1!24m2!2b1!4b1!26m4!1e12!1e13!1e3!1e1!30m1!2b1!36b1"
        "!43b1!52b1!47m0!49m7!3b1!6m2!1b1!2b1!7m2!1e3!2b1!50m4!2e3!3m2!1b1!3b1"
    )
    url = f"https://www.google.com/search?tbm=map&hl=tr&gl=tr&q={encoded_q}&pb={pb_param}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Referer': 'https://www.google.com/maps',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"  ❌ PB Fetch hatası: {e}")
        return ""


def extract_website_prices(url, mekan_id, mekan_adi, il, ilce, mahalle, conn):
    """Mekanın web sitesine gidip menü, fiyat ve görselleri arar"""
    if not url.startswith('http'): url = 'http://' + url
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
            # Fiyat Regex'i (Basit Heuristic: (Ürün Adı) ... (Fiyat) TL)
            matches = re.findall(r'>\s*([A-Za-zÇŞĞÜÖİçşğüöı\s]{3,30})\s*<.*?([0-9]+[,.][0-9]{2})\s*(?:TL|₺|TRY)', html, re.IGNORECASE | re.DOTALL)
            
            # Görsel (İçinde menu geçen resimler)
            img_matches = re.findall(r'<img[^>]+src=["\']([^"\']*(?:menu|fiyat)[^"\']*\.(?:jpg|jpeg|png))["\']', html, re.IGNORECASE)
            
            cur = conn.cursor()
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            donem = time.strftime("%Y-%m")
            
            fiyat_count = 0
            for name, price_str in matches:
                try:
                    price = float(price_str.replace(',', '.'))
                    cur.execute("""
                        INSERT OR IGNORE INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi 
                        (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori, urun_adi, fiyat, ilce, il, mahalle, guncellenme_tarihi)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (mekan_id, mekan_adi, donem, now, "Fiziksel_Web_Menu", "WebSitesi", "Menü", name.strip(), price, ilce, il, mahalle, now))
                    fiyat_count += 1
                except:
                    pass
            
            img_count = 0
            for img in img_matches:
                img_url = img if img.startswith('http') else urllib.parse.urljoin(url, img)
                cur.execute("""
                    INSERT OR IGNORE INTO mekan_menu_gorselleri
                    (mekan_id, mekan_adi, gorsel_url, kaynak, tarama_tarihi, il, ilce, mahalle)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (mekan_id, mekan_adi, img_url, "WebSitesi", now, il, ilce, mahalle))
                img_count += 1
                
            conn.commit()
            if fiyat_count > 0 or img_count > 0:
                print(f"    🌐 Websitesi: {fiyat_count} fiyat, {img_count} menü fotoğrafı çıkarıldı.")
                
    except Exception as e:
        pass # Timeout veya hata durumunda sessizce geç

def get_venues_from_tree(node, results):
    if isinstance(node, dict):
        if '1205891' in node:
            try:
                name = node['1205891'][3][0][0]
                website = None
                mekan_id = None
                
                # Check for place id
                try: mekan_id = node['1205891'][78][0]
                except: pass
                
                if not mekan_id and '525001528' in node:
                    try: mekan_id = node['525001528'][2]
                    except: pass
                    
                # Check for website
                if '1205891' in node and len(node['1205891']) > 7 and node['1205891'][7] and len(node['1205891'][7]) > 0:
                    website = node['1205891'][7][0]
                    
                import json
                results.append({
                    "id": mekan_id or name,
                    "name": name,
                    "website": website,
                    "raw_dump": json.dumps(node)
                })
            except Exception as e:
                pass
        for v in node.values():
            get_venues_from_tree(v, results)
    elif isinstance(node, list):
        for it in node:
            get_venues_from_tree(it, results)

def parse_pb_response(html):
    import json
    venues = []
    
    clean = html.strip()
    data = None
    if clean.startswith(")]}'"):
        try: data = json.loads(clean[5:].strip())
        except: pass
            
    if not data:
        for chunk in html.split('/*""*/'):
            chunk = chunk.strip()
            if not chunk: continue
            try:
                j = json.loads(chunk)
                if "d" in j:
                    d_str = j["d"]
                    if d_str.startswith(")]}'\n") or d_str.startswith(")]}'"):
                        d_str = d_str[5:]
                    data = json.loads(d_str)
                    break
            except Exception:
                continue
            
    if data:
        get_venues_from_tree(data, venues)
            
    return venues

def parse_venue_and_save(venue, il, ilce, mahalle, conn):
    try:
        import time, re
        mekan_id = venue["id"]
        mekan_adi = venue["name"]
        website = venue["website"]
        val_str = venue["raw_dump"]
        
        cur = conn.cursor()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        donem = time.strftime("%Y-%m")
        
        fiyat_count = 0
        img_count = 0
        
        photo_urls = re.findall(r'(https://lh5\.googleusercontent\.com/p/[a-zA-Z0-9_-]+)', val_str)
        
        menu_items = re.findall(r'\["([^"]{3,40})","([^"]*?)",null,null,null,"([0-9]+(?:,[0-9]{2})?)\s*(?:TL|₺|TRY)"', val_str)
        
        if not menu_items:
            menu_items_alt = re.findall(r'\["([^"]{3,40})",null,null,null,null,"([0-9]+(?:,[0-9]{2})?)\s*(?:TL|₺|TRY)"', val_str)
            menu_items = [(m[0], "", m[1]) for m in menu_items_alt]
            
        for item_name, item_desc, item_price in menu_items:
            try:
                price = float(item_price.replace(',', '.'))
                cur.execute("""
                    INSERT OR IGNORE INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi 
                    (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori, urun_adi, aciklama, fiyat, ilce, il, mahalle, guncellenme_tarihi, fiyat_guncellenme_zamani)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mekan_id, mekan_adi, donem, now, "Google_Menu", "GoogleMaps", "Menü", item_name, item_desc, price, ilce, il, mahalle, now, "Google Orijinal"))
                fiyat_count += 1
            except:
                pass

        for p_url in set(photo_urls[:5]):
            cur.execute("""
                INSERT OR IGNORE INTO mekan_menu_gorselleri
                (mekan_id, mekan_adi, gorsel_url, kaynak, tarama_tarihi, il, ilce, mahalle)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (mekan_id, mekan_adi, p_url, "GoogleMaps", now, il, ilce, mahalle))
            img_count += 1
            
        conn.commit()
        
        if fiyat_count > 0 or img_count > 0:
            print(f"  ✅ {mekan_adi}: {fiyat_count} Google Menü öğesi, {img_count} Fotoğraf")
        else:
            if website and "google" not in website.lower():
                extract_website_prices(website, mekan_id, mekan_adi, il, ilce, mahalle, conn)

    except Exception as e:
        pass
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=str, default="1/40")
    args = parser.parse_args()
    
    shard_str = str(args.shard)
    if '/' in shard_str:
        shard_id, num_shards = map(int, shard_str.split('/'))
    else:
        shard_id, num_shards = int(shard_str), 1
        
    print(f"🚀 Başlatılıyor: Menü ve Fotoğraf Toplayıcı (Makine {shard_id}/{num_shards})")
    
    conn = init_db()
    mahalleler = get_mahalleler_by_shard(shard_id, num_shards)
    
    print(f"📍 Bu makine {len(mahalleler)} bölgede tarama yapacak.")
    
    for loc in mahalleler:
        parts = loc.split(' ')
        il = parts[-1]
        ilce = parts[-2] if len(parts) > 1 else ""
        mahalle = " ".join(parts[:-2]) if len(parts) > 2 else ""
        
        for cat in CATEGORIES:
            query = f"{loc} {cat}"
            print(f"🔍 Aranan: {query}")
            
            html = fetch_google_pb(query)
            venues = parse_pb_response(html)
            
            if venues:
                for v in venues:
                    parse_venue_and_save(v, il, ilce, mahalle, conn)
            
            time.sleep(2) # Google'ı çok hızlı yormamak için

if __name__ == "__main__":
    main()
