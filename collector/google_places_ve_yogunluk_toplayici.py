#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Google Places ve Ticari Yoğunluk Toplayıcı
Resmî Google Places API anahtarı gerektirmeden, Google Maps tersine mühendislik
uç noktası üzerinden işletme/nokta bazlı:
- google_place_id, cid
- Ticari isim, birincil ve tüm kategoriler
- Puan (rating)
- Yorum sayısı (review_count - ciro ve kümülatif müşteri hacim vekili)
- Enlem, boylam (WGS84)
- Açık adres, mahalle, ilçe, il
- Haftalık çalışma saatleri
- Zaman damgası (ISO 8601 UTC)
bilgilerini toplayıp warehouse/product/google_places_ve_yogunluk.sqlite ambarına yazar.
40 Shard GitHub Actions ve lokal paralel çalışma desteğine sahiptir.
"""

import sys
import os
import json
import time
import random
import argparse
import sqlite3
import urllib.request
import urllib.parse
from collections import deque
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DB = os.path.join(BASE_DIR, "warehouse/product/google_places_ve_yogunluk.sqlite")
ZINCIR_DB = os.path.join(BASE_DIR, "warehouse/product/zincir_markalar_ve_finans.sqlite")

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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_ticari_yogunluk (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_place_id TEXT UNIQUE,
        cid TEXT,
        isim TEXT NOT NULL,
        arama_terimi TEXT,
        ana_kategori TEXT,
        tum_kategoriler TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        tam_adres TEXT,
        mahalle TEXT,
        ilce TEXT,
        il TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        maps_url TEXT,
        kaynak TEXT DEFAULT 'Google Maps',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_arama_gecmisi (
        arama_terimi TEXT PRIMARY KEY,
        bulunan_adet INTEGER,
        son_tarama_tarihi TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_ilce ON google_places_ticari_yogunluk(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_coords ON google_places_ticari_yogunluk(lat, lon)")
    conn.commit()
    conn.close()

def is_query_done(conn, query):
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM google_places_arama_gecmisi WHERE arama_terimi = ?", (query,))
        return cur.fetchone() is not None
    except Exception:
        return False

def record_query(conn, query, count):
    try:
        cur = conn.cursor()
        now_utc = datetime.now(timezone.utc).isoformat()
        cur.execute("""
        INSERT OR REPLACE INTO google_places_arama_gecmisi (arama_terimi, bulunan_adet, son_tarama_tarihi)
        VALUES (?, ?, ?)
        """, (query, count, now_utc))
        conn.commit()
    except Exception:
        pass

def parse_google_maps_response(content):
    for chunk in content.split('/*""*/'):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            j = json.loads(chunk)
            if "d" in j:
                d_str = j["d"]
                if d_str.startswith(")]}'\n"):
                    d_str = d_str[5:]
                data = json.loads(d_str)
                return data
        except Exception:
            continue
    return None

def extract_venue_from_v14(v14, search_query):
    if not v14 or len(v14) < 15:
        return None
    
    name = v14[11] if len(v14) > 11 and v14[11] else None
    if not name:
        return None
    
    # Koordinatlar: v14[9] = [None, None, lat, lon]
    coords = v14[9] if len(v14) > 9 and v14[9] and len(v14[9]) >= 4 else None
    lat = coords[2] if coords and coords[2] is not None else None
    lon = coords[3] if coords and coords[3] is not None else None
    
    if lat is None or lon is None:
        return None
    
    # Kategoriler
    cats = v14[13] if len(v14) > 13 and v14[13] else []
    ana_kategori = cats[0] if cats else None
    tum_kategoriler = json.dumps(cats, ensure_ascii=False) if cats else None
    
    # Puan ve Yorum Sayısı: v14[4]
    rating = None
    reviews = None
    if len(v14) > 4 and v14[4]:
        f4 = v14[4]
        if len(f4) > 7 and f4[7] is not None:
            try:
                rating = float(f4[7])
            except Exception:
                pass
        if len(f4) > 8 and isinstance(f4[8], (int, float)):
            reviews = int(f4[8])
        if reviews is None:
            def search_yorum(obj):
                if isinstance(obj, str) and ("yorum" in obj.lower() or "review" in obj.lower()):
                    digits = "".join(ch for ch in obj if ch.isdigit())
                    if digits:
                        return int(digits)
                elif isinstance(obj, list):
                    for it in obj:
                        res = search_yorum(it)
                        if res is not None:
                            return res
                return None
            reviews = search_yorum(f4)
    
    # Kimlikler
    place_id = v14[78] if len(v14) > 78 and v14[78] else None
    cid = v14[10] if len(v14) > 10 and v14[10] else None
    
    # Adres
    tam_adres = v14[18] if len(v14) > 18 and v14[18] else (v14[39] if len(v14) > 39 else None)
    mahalle = v14[14] if len(v14) > 14 and v14[14] else None
    
    ilce_il = v14[166] if len(v14) > 166 and v14[166] else None
    ilce = None
    il = None
    if ilce_il and "/" in ilce_il:
        parts = ilce_il.split("/")
        ilce = parts[0].strip()
        il = parts[1].strip()
    elif ilce_il:
        ilce = ilce_il.strip()
    
    # Telefon
    telefon = None
    if len(v14) > 178 and v14[178] and len(v14[178]) > 0 and len(v14[178][0]) > 0:
        telefon = v14[178][0][0]
    
    # Çalışma saatleri
    calisma_saatleri = None
    if len(v14) > 203 and v14[203] and len(v14[203]) > 0:
        calisma_saatleri = json.dumps(v14[203][0], ensure_ascii=False)
    
    maps_url = v14[42] if len(v14) > 42 and v14[42] else None
    now_utc = datetime.now(timezone.utc).isoformat()
    
    return {
        "google_place_id": place_id or f"CID_{cid or name}",
        "cid": str(cid) if cid else None,
        "isim": str(name),
        "arama_terimi": search_query,
        "ana_kategori": str(ana_kategori) if ana_kategori else None,
        "tum_kategoriler": tum_kategoriler,
        "puan": rating,
        "yorum_sayisi": reviews,
        "tam_adres": str(tam_adres) if tam_adres else None,
        "mahalle": str(mahalle) if mahalle else None,
        "ilce": str(ilce) if ilce else None,
        "il": str(il) if il else None,
        "lat": float(lat),
        "lon": float(lon),
        "telefon": str(telefon) if telefon else None,
        "calisma_saatleri": calisma_saatleri,
        "maps_url": str(maps_url) if maps_url else None,
        "kaynak": "Google Maps (Reverse Engineered)",
        "guncellenme_tarihi": now_utc
    }

NON_COMMERCIAL_KEYWORDS = [
    "sitesi", "apartmanı", "konutları", "evleri", "köyü", "mezarlığı", "camii", "tatil sitesi", "yerleşim yeri",
    "muhtarlığı", "kaymakamlığı", "belediye başkanlığı", "hükümet konağı", "ilçe jandarma", "polis merkezi", "karakolu"
]

NON_COMMERCIAL_CATEGORIES = [
    "yerleşim yeri", "ilçe", "il", "köy", "mahalle", "tatil sitesi", "konut kompleksi", "apartman", "cami", "mezarlık",
    "belediye binası", "hükümet dairesi", "adliye", "karakol", "askeri üs"
]

def is_valid_commercial_venue(name, category=None, rating=None, reviews=None, is_candidate=False):
    """Sadece gerçek ticari işletmeleri (dükkan, market, restoran, mağaza vb.) kabul eder.
    İdari sınırlar, mahalle/köy merkezleri, yol segmanları ve konut sitelerini eler."""
    if not name:
        return False
    name_clean = name.strip()
    n_low = name_clean.lower()
    c_low = (category or "").lower().strip()
    
    # 1. Google Maps idari fallback'i (örn. 'Malkara, Tekirdağ, Türkiye' veya 'Sındırgı/Balıkesir, Türkiye')
    if name_clean.endswith(", Türkiye") or name_clean.endswith(", Turkey") or name_clean.endswith("/Türkiye"):
        return False
    if "/" in name_clean and (", Türkiye" in name_clean or ", Turkey" in name_clean or name_clean.count(",") >= 1):
        return False
    if n_low.endswith(" türkiye") or n_low.endswith(" turkey"):
        return False
        
    # 2. Yol / Cadde Segmanı Filtresi (Cumhuriyet Cd., Kağızman Cd. vb.)
    cadde_ekleri = [" cd.", " cad.", " sk.", " sok.", " bulv.", " bulvarı", " caddesi", " sokağı"]
    if any(n_low.endswith(ce) for ce in cadde_ekleri) and (not category or category == "Ticari Mekan"):
        return False
        
    # 3. İdari / Konut / Dini kategoriler
    if any(ncc in c_low for ncc in NON_COMMERCIAL_CATEGORIES):
        return False
        
    # 4. İsimde site, apartman, konut vb. geçiyorsa ve açık bir ticari kategori yoksa ele
    if any(kw in n_low for kw in NON_COMMERCIAL_KEYWORDS):
        if not any(ck in c_low for ck in ["restoran", "kafe", "market", "otel", "dükkan", "mağaza", "fırın", "pastane", "avm", "lokanta"]):
            return False
            
    # 5. Detaylı kayıtlarda (V14) kategori ve puan/yorum hiçbiri yoksa harita geometrisidir
    if not is_candidate and not category and rating is None and reviews is None:
        return False
        
    return True

def fetch_google_places(query):
    """Google Maps üzerinden tekil değil, sorguda dönen TÜM ticari işletmeleri liste halinde çeker."""
    encoded_q = urllib.parse.quote(query)
    url = f"https://www.google.com/search?tbm=map&tch=1&hl=tr&q={encoded_q}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "*/*"
    }
    req = urllib.request.Request(url, headers=headers)
    venues = []
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            data = parse_google_maps_response(content)
            if not data:
                return []
            
            # Durum 1: data[0][1] içindeki TÜM mekanlar (Detaylı V14 formatı)
            if len(data) > 0 and len(data[0]) > 1 and data[0][1]:
                for item in data[0][1]:
                    if isinstance(item, list) and len(item) > 14 and item[14]:
                        v = extract_venue_from_v14(item[14], query)
                        if v and is_valid_commercial_venue(v["isim"], v["ana_kategori"], v.get("puan"), v.get("yorum_sayisi")):
                            venues.append(v)
            
            # Durum 2: data[37] aday listesi (Eğer durum 1 boşsa)
            if not venues and len(data) > 37 and data[37] and isinstance(data[37], list) and len(data[37]) > 2:
                d37 = data[37]
                sub_list = d37[2]
                if sub_list and isinstance(sub_list, list):
                    for cand in sub_list:
                        if cand and len(cand) > 4:
                            coords = cand[3] if len(cand) > 3 else None
                            cid = cand[2] if len(cand) > 2 else None
                            title = cand[4] if len(cand) > 4 else None
                            if coords and len(coords) >= 4 and coords[2] is not None and coords[3] is not None and title:
                                if is_valid_commercial_venue(title, "Ticari Mekan", None, None, is_candidate=True):
                                    now_utc = datetime.now(timezone.utc).isoformat()
                                    venues.append({
                                        "google_place_id": f"CID_{cid}",
                                        "cid": str(cid),
                                        "isim": str(title),
                                        "arama_terimi": query,
                                        "ana_kategori": "Ticari Mekan",
                                        "tum_kategoriler": None,
                                        "puan": None,
                                        "yorum_sayisi": None,
                                        "tam_adres": str(title),
                                        "mahalle": None,
                                        "ilce": None,
                                        "il": None,
                                        "lat": float(coords[2]),
                                        "lon": float(coords[3]),
                                        "telefon": None,
                                        "calisma_saatleri": None,
                                        "maps_url": f"https://www.google.com/maps?cid={cid}" if cid else None,
                                        "kaynak": "Google Maps (Aday Listesi)",
                                        "guncellenme_tarihi": now_utc
                                    })
    except Exception as e:
        print(f"Hata ({query}): {e}", file=sys.stderr)
    return venues

def save_venue(conn, venue):
    if not venue:
        return False
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO google_places_ticari_yogunluk (
        google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
        puan, yorum_sayisi, tam_adres, mahalle, ilce, il,
        lat, lon, telefon, calisma_saatleri, maps_url, kaynak, guncellenme_tarihi
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        venue["google_place_id"],
        venue["cid"],
        venue["isim"],
        venue["arama_terimi"],
        venue["ana_kategori"],
        venue["tum_kategoriler"],
        venue["puan"],
        venue["yorum_sayisi"],
        venue["tam_adres"],
        venue["mahalle"],
        venue["ilce"],
        venue["il"],
        venue["lat"],
        venue["lon"],
        venue["telefon"],
        venue["calisma_saatleri"],
        venue["maps_url"],
        venue["kaynak"],
        venue["guncellenme_tarihi"]
    ))
    conn.commit()
    return True

BATI_DB = os.path.join(BASE_DIR, "warehouse/product/bati_ticari_istihbarat.sqlite")

COMMERCIAL_CORRIDORS_81_PROVINCES = [
    # 01 Adana
    "Ziyapaşa Bulvarı Seyhan Adana", "M1 Adana AVM Seyhan Adana", "Optimum AVM Yüreğir Adana",
    # 02 Adıyaman
    "Gölbaşı Caddesi Merkez Adıyaman", "Sümer Meydanı Merkez Adıyaman",
    # 03 Afyonkarahisar
    "Afium Outlet AVM Merkez Afyonkarahisar", "Park Afyon AVM Merkez Afyonkarahisar", "Ambaryolu Caddesi Merkez Afyonkarahisar",
    # 04 Ağrı
    "Cumhuriyet Caddesi Merkez Ağrı", "Kağızman Caddesi Merkez Ağrı",
    # 05 Amasya
    "Mustafa Kemal Paşa Caddesi Merkez Amasya", "Amasya Park AVM Merkez Amasya",
    # 06 Ankara
    "Tunalı Hilmi Caddesi Çankaya Ankara", "Kızılay Yüksel Caddesi Çankaya Ankara", "Bahçelievler 7. Cadde Çankaya Ankara",
    "Armada AVM Çankaya Ankara", "Panora AVM Çankaya Ankara", "Ankamall AVM Yenimahalle Ankara", "Çukurambar Muhsin Yazıcıoğlu Caddesi Çankaya Ankara",
    # 07 Antalya
    "TerraCity AVM Muratpaşa Antalya", "MarkAntalya AVM Muratpaşa Antalya", "5M Migros AVM Konyaaltı Antalya",
    "Mall of Antalya Kepez Antalya", "Lara Caddesi Muratpaşa Antalya", "Konyaaltı Sahil Yaşam Parkı Antalya",
    "Kaleiçi Çarşı Muratpaşa Antalya", "Kültür Kafe Caddesi Kepez Antalya", "Alanyum AVM Alanya Antalya", "The Land of Legends Serik Antalya",
    # 08 Artvin
    "İnönü Caddesi Merkez Artvin", "Hopa Sahil Caddesi Hopa Artvin",
    # 09 Aydın
    "Forum Aydın AVM Efeler Aydın", "Starbucks Kuşadası Marina Aydın", "Kuşadası Barlar Sokağı Aydın", "Didim Altınkum Sahil Aydın",
    # 10 Balıkesir
    "10 Burda AVM Altıeylül Balıkesir", "Milli Kuvvetler Caddesi Karesi Balıkesir", "Ayvalık Cunda Sahil Balıkesir", "Bandırma Liman AVM Balıkesir",
    # 11 Bilecik
    "Tevfikbey Caddesi Merkez Bilecik", "Bozüyük Sarar Outlet Bozüyük Bilecik",
    # 12 Bingöl
    "Genç Caddesi Merkez Bingöl", "Kalium AVM Merkez Bingöl",
    # 13 Bitlis
    "Tatvan Yaşam AVM Tatvan Bitlis", "Cumhuriyet Caddesi Merkez Bitlis",
    # 14 Bolu
    "14 Burda AVM Merkez Bolu", "Highway Outlet AVM Bolu", "İzzet Baysal Caddesi Merkez Bolu",
    # 15 Burdur
    "Gazi Caddesi Merkez Burdur", "Cumhuriyet Meydanı Merkez Burdur",
    # 16 Bursa
    "Sur Yapı Marka AVM Nilüfer Bursa", "Korupark AVM Osmangazi Bursa", "Zafer Plaza AVM Osmangazi Bursa",
    "Downtown AVM Osmangazi Bursa", "Fatih Sultan Mehmet Bulvarı Nilüfer Bursa", "Özlüce Ahmet Taner Kışlalı Bulvarı Nilüfer Bursa", "Görükle Yerleşim Çarşı Nilüfer Bursa",
    # 17 Çanakkale
    "17 Burda AVM Merkez Çanakkale", "Kordon Boyu Çanakkale", "Saat Kulesi Meydanı Merkez Çanakkale",
    # 18 Çankırı
    "Yunus AVM Merkez Çankırı", "Atatürk Bulvarı Merkez Çankırı",
    # 19 Çorum
    "AHL Park AVM Merkez Çorum", "Gazi Caddesi Merkez Çorum",
    # 20 Denizli
    "Forum Çamlık AVM Pamukkale Denizli", "Sümerpark AVM Merkezefendi Denizli", "Çamlık Caddesi Pamukkale Denizli",
    # 21 Diyarbakır
    "Ceylan Karavil Park AVM Kayapınar Diyarbakır", "Forum Diyarbakır Yenişehir Diyarbakır", "Ofis Sanat Sokağı Yenişehir Diyarbakır", "Sur İçi Gazi Caddesi Sur Diyarbakır",
    # 22 Edirne
    "Erasta AVM Merkez Edirne", "Margi Outlet AVM Merkez Edirne", "Saraçlar Caddesi Merkez Edirne",
    # 23 Elazığ
    "Elysium AVM Merkez Elazığ", "Park Yirmiüç AVM Merkez Elazığ", "Gazi Caddesi Merkez Elazığ",
    # 24 Erzincan
    "Ermerkez AVM Merkez Erzincan", "Ordu Caddesi Merkez Erzincan",
    # 25 Erzurum
    "MNG AVM Yakutiye Erzurum", "Forum Erzurum Palandöken Erzurum", "Cumhuriyet Caddesi Yakutiye Erzurum",
    # 26 Eskişehir
    "Espark AVM Tepebaşı Eskişehir", "Vega Outlet Tepebaşı Eskişehir", "Doktorlar Caddesi Tepebaşı Eskişehir", "Adalar Porsuk Çayı Çevresi Odunpazarı Eskişehir",
    # 27 Gaziantep
    "Sanko Park AVM Şehitkamil Gaziantep", "Forum Gaziantep Şehitkamil Gaziantep", "Primemall AVM Şehitkamil Gaziantep", "Gazi Muhtar Paşa Bulvarı Şehitkamil Gaziantep",
    # 28 Giresun
    "G-City AVM Merkez Giresun", "Gazi Caddesi Merkez Giresun",
    # 29 Gümüşhane
    "Atatürk Caddesi Merkez Gümüşhane", "Zafer Meydanı Merkez Gümüşhane",
    # 30 Hakkari
    "Cumhuriyet Caddesi Merkez Hakkari", "Yüksekova Cengiz Topel Caddesi Hakkari",
    # 31 Hatay
    "Palladium AVM Defne Hatay", "Prime Mall İskenderun Hatay", "İskenderun Sahil Kordonu Hatay",
    # 32 Isparta
    "Iyaşpark AVM Merkez Isparta", "Meydan AVM Merkez Isparta", "Mimar Sinan Caddesi Merkez Isparta",
    # 33 Mersin
    "Forum Mersin AVM Yenişehir Mersin", "Sayapark AVM Yenişehir Mersin", "Mersin Marina Akdeniz Mersin", "Kushimoto Sokağı Yenişehir Mersin", "Tarsu AVM Tarsus Mersin",
    # 34 İstanbul
    "Zorlu Center Beşiktaş İstanbul", "İstinyePark AVM Sarıyer İstanbul", "Cevahir AVM Şişli İstanbul",
    "Kanyon AVM Levent Beşiktaş İstanbul", "Vadistanbul AVM Sarıyer İstanbul", "Mall of İstanbul Başakşehir İstanbul",
    "Akasya AVM Üsküdar İstanbul", "Emaar Square AVM Üsküdar İstanbul", "Metropol İstanbul Ataşehir",
    "Viaport Asia Pendik İstanbul", "Bağdat Caddesi Kadıköy İstanbul", "Moda Sahil Kadıköy İstanbul",
    "İstiklal Caddesi Beyoğlu İstanbul", "Abdi İpekçi Caddesi Nişantaşı Şişli İstanbul", "Bebek Sahil Beşiktaş İstanbul",
    # 35 İzmir
    "İstinyePark İzmir Balçova İzmir", "Hilltown AVM Karşıyaka İzmir", "Mavibahçe AVM Karşıyaka İzmir",
    "Forum Bornova AVM İzmir", "Optimum AVM Gaziemir İzmir", "Kordon Boyu Alsancak Konak İzmir",
    "Kıbrıs Şehitleri Caddesi Konak İzmir", "Bostanlı Balıkçılar Meydanı Karşıyaka İzmir", "Tarihi Kemeraltı Çarşısı Konak İzmir",
    "Alaçatı Çarşı Çeşme İzmir", "Urla Sanat Sokağı Urla İzmir",
    # 36 Kars
    "Kazım Karabekir Paşa Caddesi Merkez Kars", "Faikbey Caddesi Merkez Kars",
    # 37 Kastamonu
    "Kastamall AVM Merkez Kastamonu", "Nasrullah Meydanı Merkez Kastamonu",
    # 38 Kayseri
    "Forum Kayseri Melikgazi Kayseri", "Kayseri Park AVM Melikgazi Kayseri", "Sivas Caddesi Kocasinan Kayseri", "Talas Meydan Kafe Koridoru Kayseri",
    # 39 Kırklareli
    "39 Burda AVM Lüleburgaz Kırklareli", "İstasyon Caddesi Lüleburgaz Kırklareli", "Cumhuriyet Caddesi Merkez Kırklareli",
    # 40 Kırşehir
    "Cacabey Meydanı Merkez Kırşehir", "Terme Caddesi Merkez Kırşehir",
    # 41 Kocaeli
    "Symbol AVM İzmit Kocaeli", "41 Burda AVM İzmit Kocaeli", "Gebze Center AVM Gebze Kocaeli", "Outlet Center İzmit Kocaeli", "Fethiye Caddesi İzmit Kocaeli",
    # 42 Konya
    "Kentplaza AVM Selçuklu Konya", "M1 Konya AVM Selçuklu Konya", "KuleSite AVM Selçuklu Konya", "Zafer Meydanı Yaya Caddesi Meram Konya", "Bosna Hersek Kafe Caddesi Selçuklu Konya",
    # 43 Kütahya
    "Sera Kütahya AVM Merkez Kütahya", "Sevgi Yolu Caddesi Merkez Kütahya",
    # 44 Malatya
    "MalatyaPark AVM Yeşilyurt Malatya", "İnönü Caddesi Battalgazi Malatya", "Kanalboyu Caddesi Yeşilyurt Malatya",
    # 45 Manisa
    "Magnesia AVM Şehzadeler Manisa", "45 Park AVM Yunusemre Manisa", "Mustafa Kemal Paşa Caddesi Şehzadeler Manisa",
    # 46 Kahramanmaraş
    "Piazza AVM Onikişubat Kahramanmaraş", "Trabzon Bulvarı Dulkadiroğlu Kahramanmaraş",
    # 47 Mardin
    "Mardian Mall AVM Artuklu Mardin", "1. Cadde Tarihi Mardin Çarşısı Artuklu Mardin", "Yenişehir Barış Caddesi Artuklu Mardin",
    # 48 Muğla
    "Yalıkavak Marina Bodrum Muğla", "Midpoint Bodrum Marina Muğla", "Bodrum Barlar Sokağı Muğla",
    "Göcek Marina Fethiye Muğla", "Fethiye Paspatur Çarşısı Fethiye Muğla", "Marmaris Marina Muğla", "Rüya Park AVM Menteşe Muğla",
    # 49 Muş
    "İstasyon Caddesi Merkez Muş", "Cumhuriyet Caddesi Merkez Muş",
    # 50 Nevşehir
    "Forum Kapadokya AVM Merkez Nevşehir", "Göreme Çarşı Nevşehir", "Ürgüp Çarşı Nevşehir",
    # 51 Niğde
    "Niğde Park AVM Merkez Niğde", "Bor Caddesi Merkez Niğde",
    # 52 Ordu
    "Novada AVM Altınordu Ordu", "Süleyman Felek Caddesi Altınordu Ordu", "Teleferik Meydanı Altınordu Ordu",
    # 53 Rize
    "Şimal AVM Merkez Rize", "Atatürk Caddesi Merkez Rize",
    # 54 Sakarya
    "Agora AVM Serdivan Sakarya", "Serdivan AVM Serdivan Sakarya", "Cadde 54 Serdivan Sakarya", "Çark Caddesi Adapazarı Sakarya",
    # 55 Samsun
    "Piazza AVM Canik Samsun", "Samsun CityMall AVM Atakum Samsun", "Çiftlik Caddesi İlkadım Samsun", "Atakum Sahil Şeridi Atakum Samsun",
    # 56 Siirt
    "Güres Caddesi Merkez Siirt", "Andera Park AVM Merkez Siirt",
    # 57 Sinop
    "Sakarya Caddesi Merkez Sinop", "Sinop Liman Kordonu Merkez Sinop",
    # 58 Sivas
    "Primemall AVM Merkez Sivas", "İstasyon Caddesi Merkez Sivas", "Atatürk Caddesi Merkez Sivas",
    # 59 Tekirdağ
    "Tekira AVM Süleymanpaşa Tekirdağ", "Orion AVM Çorlu Tekirdağ", "Trend Arena AVM Çorlu Tekirdağ", "Hükümet Caddesi Süleymanpaşa Tekirdağ",
    # 60 Tokat
    "Novada AVM Merkez Tokat", "Gaziosmanpaşa Bulvarı Merkez Tokat",
    # 61 Trabzon
    "Forum Trabzon Ortahisar Trabzon", "Varlıbaş AVM Ortahisar Trabzon", "Uzun Sokak Ortahisar Trabzon", "Kunduracılar Caddesi Ortahisar Trabzon",
    # 62 Tunceli
    "Sanat Sokağı Moğultay Merkez Tunceli", "Cumhuriyet Caddesi Merkez Tunceli",
    # 63 Şanlıurfa
    "Piazza AVM Eyyübiye Şanlıurfa", "Urfa City AVM Haliliye Şanlıurfa", "Balıklıgöl Çarşısı Eyyübiye Şanlıurfa", "Sarayönü Caddesi Haliliye Şanlıurfa",
    # 64 Uşak
    "Festiva AVM Merkez Uşak", "İsmetpaşa Caddesi Merkez Uşak",
    # 65 Van
    "Van AVM İpekyolu Van", "Cumhuriyet Caddesi İpekyolu Van", "Maraş Caddesi İpekyolu Van",
    # 66 Yozgat
    "Novada AVM Merkez Yozgat", "Lise Caddesi Merkez Yozgat",
    # 67 Zonguldak
    "DemirPark AVM Merkez Zonguldak", "WestaLife AVM Merkez Zonguldak", "Gazipaşa Caddesi Merkez Zonguldak",
    # 68 Aksaray
    "Nora City AVM Merkez Aksaray", "Ebulfeyz Elçibey Caddesi Merkez Aksaray",
    # 69 Bayburt
    "Cumhuriyet Caddesi Merkez Bayburt", "Saat Kulesi Meydanı Merkez Bayburt",
    # 70 Karaman
    "Park Karaman AVM Merkez Karaman", "İsmet Paşa Caddesi Merkez Karaman",
    # 71 Kırıkkale
    "Podium AVM Yahşihan Kırıkkale", "Zafer Caddesi Merkez Kırıkkale",
    # 72 Batman
    "Batman Park AVM Merkez Batman", "Turgut Özal Bulvarı Merkez Batman",
    # 73 Şırnak
    "Cizre Park AVM Cizre Şırnak", "Sanat Sokağı Cizre Şırnak",
    # 74 Bartın
    "Hükümet Caddesi Merkez Bartın", "Amasra Çarşı Bartın",
    # 75 Ardahan
    "Kongre Caddesi Merkez Ardahan", "Kura Nehri Sahil Parkı Ardahan",
    # 76 Iğdır
    "Vali Yolu Caddesi Merkez Iğdır", "Cumhuriyet Caddesi Merkez Iğdır",
    # 77 Yalova
    "Özdilek AVM Çiftlikköy Yalova", "Star AVM Merkez Yalova", "Gazipaşa Sahil Caddesi Merkez Yalova",
    # 78 Karabük
    "Kares AVM Safranbolu Karabük", "Safranbolu Tarihi Çarşı Karabük",
    # 79 Kilis
    "Cumhuriyet Caddesi Merkez Kilis", "Nemika Caddesi Merkez Kilis",
    # 80 Osmaniye
    "Park 328 AVM Merkez Osmaniye", "Atatürk Caddesi Merkez Osmaniye",
    # 81 Düzce
    "Krempark AVM Merkez Düzce", "İstanbul Caddesi Merkez Düzce"
]

SINIR_DB = os.path.join(BASE_DIR, "warehouse/product/idari_sinirlar.sqlite")

def get_all_commercial_corridor_queries():
    """81 il ve 1.000+ ilçenin ticari çarşı, AVM ve restoran sorgularını derler."""
    queries = list(COMMERCIAL_CORRIDORS_81_PROVINCES)
    if os.path.exists(SINIR_DB):
        try:
            conn = sqlite3.connect(f"file:{SINIR_DB}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT il_adi, ad FROM sinir WHERE seviye = 'ilce' ORDER BY il_adi, ad")
            for il, ilce in cur.fetchall():
                if il and ilce:
                    queries.append(f"{ilce} {il} AVM")
                    queries.append(f"{ilce} {il} Çarşı")
                    queries.append(f"{ilce} {il} Restoran")
            conn.close()
        except Exception:
            pass
    return queries

MAHALLE_JSON = os.path.join(BASE_DIR, "collector/mahalle_koordinatlari.json")

def get_mahalle_queries_by_shard(shard_id, total_shards=40, limit=None):
    """Her shard için Türkiye'nin 32.973 kentsel mahallesinden dengeli bir dilim seçer."""
    if not os.path.exists(MAHALLE_JSON):
        return []
    try:
        with open(MAHALLE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Kentsel / ticari mahalleleri köy ve mezralardan ayır
        urban_keys = [
            k for k, v in data.items() 
            if "Köyü" not in v.get("name", "") and "Mezra" not in v.get("name", "")
        ]
        step = max(1, len(urban_keys) // total_shards)
        start = (shard_id - 1) * step
        end = start + step if shard_id < total_shards else len(urban_keys)
        shard_keys = urban_keys[start:end]
        if limit and limit > 0:
            shard_keys = shard_keys[:limit]
        
        queries = []
        for k in shard_keys:
            parts = k.split("_")
            il = parts[0].capitalize()
            ilce = parts[1].capitalize() if len(parts) > 1 else ""
            mah = data[k].get("name", "").replace("Mahallesi", "").strip()
            queries.append(f"{mah} Mahallesi {ilce} {il} dükkanlar")
            queries.append(f"{mah} Mahallesi {ilce} {il} restoranlar")
        return queries
    except Exception:
        return []

def get_commercial_corridors_by_shard(shard_id, total_shards=40, mahalle_limit=None):
    """40 Shard için dengeli 81 il, ilçe ve MAHALLE MAHALLE detaylı ticari sorgu havuzu oluşturur."""
    all_corridors = get_all_commercial_corridor_queries()
    step = max(1, len(all_corridors) // total_shards)
    start = (shard_id - 1) * step
    end = start + step if shard_id < total_shards else len(all_corridors)
    corridor_slice = all_corridors[start:end]
    
    # Mahalle Mahalle detaylı aramaları ekle (mahalle_limit None ise shard'daki TÜM kentsel mahalleleri alır)
    mahalle_slice = get_mahalle_queries_by_shard(shard_id, total_shards, limit=mahalle_limit)
    
    combined = corridor_slice + mahalle_slice
    return combined

def sync_to_bati_warehouse(venue):
    if not os.path.exists(BATI_DB) or not venue:
        return
    try:
        conn = sqlite3.connect(BATI_DB)
        cur = conn.cursor()
        cur.execute("""
        INSERT OR REPLACE INTO google_places_ticari_yogunluk (
            google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
            puan, yorum_sayisi, tam_adres, mahalle, ilce, il,
            lat, lon, telefon, calisma_saatleri, maps_url, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            venue["google_place_id"], venue["cid"], venue["isim"], venue["arama_terimi"],
            venue["ana_kategori"], venue["tum_kategoriler"], venue["puan"], venue["yorum_sayisi"],
            venue["tam_adres"], venue["mahalle"], venue["ilce"], venue["il"],
            venue["lat"], venue["lon"], venue["telefon"], venue["calisma_saatleri"],
            venue["maps_url"], venue["kaynak"], venue["guncellenme_tarihi"]
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Google Places & Ticari Yoğunluk Toplayıcı (81 İl & 40 Shard)")
    parser.add_argument("--shard", type=str, help="Shard numarası (örn: 1/40)")
    parser.add_argument("--out", type=str, default=DEFAULT_DB, help="Çıktı sqlite veritabanı")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum işlenecek sorgu sayısı (varsayılan: sınırsız)")
    parser.add_argument("--mahalle-limit", type=int, default=None, help="Shard başına işlenecek mahalle sayısı (varsayılan: tüm kentsel mahalleler)")
    parser.add_argument("--max-seconds", type=int, default=14400, help="Maksimum çalışma süresi saniye (varsayılan: 14400 = 4 saat emniyet sınırı)")
    parser.add_argument("--no-deep", action="store_true", help="Yoğun ticari mahallelerde otonom derinleştirmeyi devre dışı bırakır")
    parser.add_argument("--force", action="store_true", help="Daha önce taranmış sorguları atlamadan yeniden tara")
    parser.add_argument("--all", action="store_true", help="Tüm 81 il sorgularını tek seferde çalıştır")
    args = parser.parse_args()

    init_db(args.out)
    conn = sqlite3.connect(args.out)

    if args.all:
        initial_queries = get_all_commercial_corridor_queries()
        print(f"81 İl Tam Kapsama Modu: {len(initial_queries)} ticari koridor ve ilçe sorgulanıyor...")
    elif args.shard:
        shard_id, total = map(int, args.shard.split("/"))
        initial_queries = get_commercial_corridors_by_shard(shard_id, total, mahalle_limit=args.mahalle_limit)
        print(f"Shard {shard_id}/{total}: {len(initial_queries)} temel ticari & mahalle sorgusu yüklendi...")
    else:
        initial_queries = COMMERCIAL_CORRIDORS_81_PROVINCES[:args.limit or 50]
        print(f"Varsayılan mod: {len(initial_queries)} sorgu işleniyor...")

    deep_enabled = not args.no_deep
    work_queue = deque(initial_queries)
    seen_queries = set(initial_queries)

    processed = 0
    success = 0
    start_time = time.time()

    print(f"Otonom Madencilik Başlatıldı: Başlangıç Havuzu = {len(work_queue)} sorgu | Derinleştirme = {'AÇIK' if deep_enabled else 'KAPALI'} | Emniyet Sınırı = {args.max_seconds/3600:.1f} saat\n", flush=True)

    while work_queue:
        if args.limit and processed >= args.limit:
            print(f"\n[Durduruldu] Belirtilen maksimum sorgu limitine ({args.limit}) ulaşıldı.", flush=True)
            break

        if time.time() - start_time >= args.max_seconds:
            print(f"\n[⏰ 4 Saatlik Emniyet Sınırına Ulaşıldı ({args.max_seconds} sn)] Ambar verileri kaydedildi, GitHub Actions artifact yüklemesine geçiliyor.", flush=True)
            break

        q = work_queue.popleft()

        # Önceden işlenmişse ve force yoksa hızlıca atla (otonom kaldığı yerden devam)
        if not args.force and is_query_done(conn, q):
            continue

        processed += 1
        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"[{processed}] (Kuyrukta: {len(work_queue)} | Hız: {rate:.1f} sorgu/sn) Sorgu: '{q}'...", flush=True)

        venues = fetch_google_places(q)
        if venues:
            for res in venues:
                save_venue(conn, res)
                sync_to_bati_warehouse(res)
                success += 1
                puan_str = f"Puan: {res['puan']}" if res['puan'] is not None else "Puan: -"
                yorum_str = f"({res['yorum_sayisi']} yorum)" if res['yorum_sayisi'] is not None else ""
                print(f"  -> Bulundu: {res['isim']} | Kat: {res['ana_kategori']} | {puan_str} {yorum_str} | ({res['lat']:.4f}, {res['lon']:.4f})", flush=True)

            # OTONOM DERİNLEŞTİRME:
            # Eğer bir mahallenin 'dükkanlar' sorgusunda >= 3 işletme bulunduysa,
            # o mahallenin ticari bir çarşısı olduğu kesindir.
            # Otonom olarak kafeler, marketler ve mağazalar için derinleşme sorgularını kuyruğa ekle!
            if deep_enabled and len(venues) >= 3 and " dükkanlar" in q:
                base_prefix = q.replace(" dükkanlar", "").strip()
                for sub_cat in ["kafeler", "marketler", "mağazalar"]:
                    sub_q = f"{base_prefix} {sub_cat}"
                    if sub_q not in seen_queries and not is_query_done(conn, sub_q):
                        seen_queries.add(sub_q)
                        work_queue.append(sub_q)
                        print(f"  [⚡ Otonom Derinleştirme] Yoğun ticari aks tespit edildi -> Kuyruğa eklendi: '{sub_q}'", flush=True)
        else:
            print(f"  -> Sonuç alınamadı: {q}", flush=True)

        record_query(conn, q, len(venues))
        time.sleep(random.uniform(0.4, 0.8))

    conn.close()
    print(f"\nİşlem tamamlandı. Toplam {processed} sorgudan {success} mekan ambarlandı.", flush=True)
    print(f"Çıktı DB: {args.out}", flush=True)

if __name__ == "__main__":
    main()
