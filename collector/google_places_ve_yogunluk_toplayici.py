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
import re
import json
import hashlib
import time
import random
import argparse
import sqlite3
import urllib.request
import urllib.parse
from collections import deque
from datetime import datetime, timezone

try:
    from collector.ticari_taksonomi import classify
except ImportError:
    from ticari_taksonomi import classify

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
        sektor TEXT,
        ana_kategori TEXT,
        alt_kategoriler TEXT,
        tum_kategoriler TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        degerlendirme_sayisi INTEGER,
        yildiz_dagilimi TEXT,
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
        saatlik_yogunluk_json TEXT,
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    for col_def in [("sektor", "TEXT"), ("alt_kategoriler", "TEXT"), ("degerlendirme_sayisi", "INTEGER"), ("yildiz_dagilimi", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE google_places_ticari_yogunluk ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_arama_gecmisi (
        arama_terimi TEXT PRIMARY KEY,
        bulunan_adet INTEGER,
        son_tarama_tarihi TEXT,
        durum TEXT,
        hata_kodu TEXT,
        hata_detayi TEXT
    )
    """)
    for col_def in [("durum", "TEXT"), ("hata_kodu", "TEXT"), ("hata_detayi", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE google_places_arama_gecmisi ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_gozlem (
        observation_id TEXT PRIMARY KEY,
        google_place_id TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        arama_terimi TEXT,
        isim TEXT NOT NULL,
        ana_kategori TEXT,
        tum_kategoriler TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        degerlendirme_sayisi INTEGER,
        tam_adres TEXT,
        mahalle TEXT,
        ilce TEXT,
        il TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        maps_url TEXT,
        kaynak TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        UNIQUE(google_place_id, observed_at)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_yorumlar_ve_niyet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_place_id TEXT NOT NULL,
        mekan_adi TEXT NOT NULL,
        yorum_metni TEXT NOT NULL,
        puan REAL,
        kayit_tarihi TEXT NOT NULL,
        UNIQUE(google_place_id, yorum_metni)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gp_yorum_place ON google_places_yorumlar_ve_niyet(google_place_id)")
    try:
        cur.execute("ALTER TABLE google_places_ticari_yogunluk ADD COLUMN ornek_yorum TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

# Önceki koşulardan gelen (salt okunur) ana ambar bağlantısı. GitHub Actions'ta her shard
# sıfır bir DB'ye yazar; "bu sorgu daha önce tarandı mı?" sorusu hem yerel DB'ye hem de
# --gecmis-db ile verilen ana ambara sorulur. Böylece koşular artımlı (incremental) olur.
HIST_CONN = None
# Dolu sonuç veren sorgu bu kadar gün boyunca yenilenmez; boş sonuç verenler daha uzun süre
# tekrar sorgulanmaz (kırsal mahallelerin her koşuda yeniden dövülmesini engeller).
REFRESH_DAYS_BASARILI = 6.5
REFRESH_DAYS_BOS = 45.0

def get_query_history(conn, query):
    """Sorgunun son kaydını (bulunan_adet, durum, gün_önce) döndürür; yoksa None.
    Önce yerel shard DB'sine, sonra varsa ana ambara bakar; en yeni kayıt kazanır."""
    best = None
    for c in (conn, HIST_CONN):
        if c is None:
            continue
        try:
            row = c.execute(
                "SELECT bulunan_adet, durum, julianday('now')-julianday(son_tarama_tarihi) "
                "FROM google_places_arama_gecmisi WHERE arama_terimi=?",
                (query,),
            ).fetchone()
        except Exception:
            row = None
        if row and (best is None or (row[2] is not None and row[2] < best[2])):
            best = row
    return best

def is_query_done(conn, query):
    row = get_query_history(conn, query)
    if not row:
        return False
    count, status, age = row
    if age is None:
        return False
    if status == "BASARILI" and (count or 0) > 0:
        return age < REFRESH_DAYS_BASARILI
    if status == "BOS_SONUC":
        return age < REFRESH_DAYS_BOS
    return False

def record_query(conn, query, count, status=None, error_code=None, error_detail=None):
    try:
        cur = conn.cursor()
        now_utc = datetime.now(timezone.utc).isoformat()
        cur.execute("""
        INSERT OR REPLACE INTO google_places_arama_gecmisi
            (arama_terimi, bulunan_adet, son_tarama_tarihi, durum, hata_kodu, hata_detayi)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (query, count, now_utc, status or ("BASARILI" if count > 0 else "BOS_SONUC"), error_code, error_detail))
        conn.commit()
    except Exception:
        pass

def parse_google_maps_response(content):
    clean = content.strip()
    if clean.startswith(")]}'"):
        try:
            return json.loads(clean[5:].strip())
        except Exception:
            pass
    for chunk in content.split('/*""*/'):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            j = json.loads(chunk)
            if "d" in j:
                d_str = j["d"]
                if d_str.startswith(")]}'\n") or d_str.startswith(")]}'"):
                    d_str = d_str[5:]
                data = json.loads(d_str)
                return data
        except Exception:
            continue
    return None
def parse_ratings_and_reviews(v14):
    """
    Google Maps Protobuf v14 ağacından puan, toplam değerlendirme/oy sayısı,
    yazılı yorum sayısı ve 1-5 yıldız histogramını ayrıştırır.
    """
    rating = None
    degerlendirme_sayisi = None
    yorum_sayisi = None
    yildiz_dagilimi = None

    # 1. v14[4] Ana Puan Bloğu
    if len(v14) > 4 and v14[4]:
        f4 = v14[4]
        if len(f4) > 7 and f4[7] is not None:
            try:
                rating = round(float(f4[7]), 1)
            except Exception:
                pass
        if len(f4) > 8 and isinstance(f4[8], (int, float)):
            val = int(f4[8])
            if 0 <= val < 10_000_000:
                degerlendirme_sayisi = val
        
        # Sadece metin içeren elemandan yorum sayısını al (URL olanları atla)
        if len(f4) > 3 and f4[3] and isinstance(f4[3], list) and len(f4[3]) > 1:
            raw_text = str(f4[3][1])
            if not raw_text.startswith("http") and "/" not in raw_text:
                digits = "".join(ch for ch in raw_text if ch.isdigit())
                if digits and len(digits) <= 8:
                    num = int(digits)
                    if num < 10_000_000:
                        yorum_sayisi = num
                        if degerlendirme_sayisi is None:
                            degerlendirme_sayisi = num

    # 2. Yıldız Dağılımı (Histogram) ve Ayrıntılı Yazılı Yorum Taraması
    def scan_for_histogram(obj):
        nonlocal yildiz_dagilimi, yorum_sayisi, rating, degerlendirme_sayisi
        if isinstance(obj, list):
            if len(obj) >= 3 and isinstance(obj[0], (int, float)) and isinstance(obj[1], list) and len(obj[1]) == 5:
                if all(isinstance(x, (int, float)) and 0 <= x < 10_000_000 for x in obj[1]):
                    stars = [int(x) for x in obj[1]]
                    sum_stars = sum(stars)
                    total_reviews = int(obj[2]) if isinstance(obj[2], (int, float)) else sum_stars
                    yildiz_dagilimi = json.dumps({
                        "1_yildiz": stars[0],
                        "2_yildiz": stars[1],
                        "3_yildiz": stars[2],
                        "4_yildiz": stars[3],
                        "5_yildiz": stars[4]
                    })
                    yorum_sayisi = total_reviews
                    if degerlendirme_sayisi is None or degerlendirme_sayisi < sum_stars:
                        degerlendirme_sayisi = sum_stars
                    if rating is None:
                        rating = round(float(obj[0]), 1)
                    return True
            for it in obj:
                if scan_for_histogram(it):
                    return True
        return False

    scan_for_histogram(v14)

    # 3. Eğer v14[4] içinde hâlâ bulunamadıysa güvenli fallback tarama
    if degerlendirme_sayisi is None or yorum_sayisi is None:
        def search_yorum(obj):
            if isinstance(obj, str) and not obj.startswith("http") and "/" not in obj:
                if "yorum" in obj.lower() or "review" in obj.lower() or "değerlendirme" in obj.lower():
                    digits = "".join(ch for ch in obj if ch.isdigit())
                    if digits and len(digits) <= 8:
                        val = int(digits)
                        if 0 <= val < 10_000_000:
                            return val
            elif isinstance(obj, list):
                for it in obj:
                    res = search_yorum(it)
                    if res is not None:
                        return res
            return None
        fallback_count = search_yorum(v14[4] if len(v14) > 4 and v14[4] else v14)
        if fallback_count:
            if degerlendirme_sayisi is None:
                degerlendirme_sayisi = fallback_count
            if yorum_sayisi is None:
                yorum_sayisi = fallback_count

    # 4. Kural 5 gereği cross-check & eksiksiz tamamlama
    if degerlendirme_sayisi is None and yorum_sayisi is not None:
        degerlendirme_sayisi = yorum_sayisi
    elif yorum_sayisi is None and degerlendirme_sayisi is not None:
        yorum_sayisi = degerlendirme_sayisi

    if degerlendirme_sayisi is not None and (degerlendirme_sayisi > 10_000_000 or degerlendirme_sayisi < 0):
        degerlendirme_sayisi = None
    if yorum_sayisi is not None and (yorum_sayisi > 10_000_000 or yorum_sayisi < 0):
        yorum_sayisi = None

    return rating, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi

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
    
    # Puan, Değerlendirme (Oy) Sayısı, Yazılı Yorum Sayısı ve Yıldız Dağılımı
    rating, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi = parse_ratings_and_reviews(v14)
    
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
        "yorum_sayisi": yorum_sayisi,
        "degerlendirme_sayisi": degerlendirme_sayisi,
        "yildiz_dagilimi": yildiz_dagilimi,
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


def iter_v14_records(root):
    """Yanıt içindeki konumu değişebilen ayrıntılı işletme kayıtlarını bulur.

    Google'ın iç dizilerinde sabit bir üst indeks varsaymak yerine, yalnızca ad ve
    geçerli WGS84 koordinat taşıyan v14 düğümlerini kabul eder. Bu, yerleşim adı
    gibi data[37] adaylarının işletme diye yazılmasını önler.
    """
    stack = [root]
    seen = set()
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
            continue
        if not isinstance(node, list):
            continue
        node_id = id(node)
        if node_id in seen:
            continue
        seen.add(node_id)
        if len(node) >= 15 and isinstance(node[11], str) and node[11].strip():
            coords = node[9] if len(node) > 9 else None
            if isinstance(coords, list) and len(coords) >= 4:
                lat, lon = coords[2], coords[3]
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    if 35.0 <= float(lat) <= 43.0 and 25.0 <= float(lon) <= 46.0:
                        yield node
        stack.extend(item for item in node if isinstance(item, (list, dict)))

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


def clean_query_for_google(query):
    # Google Maps tbm=map&tch=1 API'si OSB veya Mahallesi gibi resmi kelimelerde sınır poligonu döndürüp
    # mekanları gizleyebiliyor. Bu yüzden sorguyu sadeleştiriyoruz.
    q = query.replace(" Osb ", " ")
    q = q.replace(" OSB ", " ")
    q = q.replace(" Organize Sanayi Bölgesi ", " ")
    q = q.replace(" Mahallesi ", " ")
    q = q.replace(" Mah. ", " ")
    q = q.replace(" Köyü ", " ")
    # Boşlukları temizle
    q = " ".join(q.split())
    return q

def parse_pb_venue_dict(v, query, now_utc):
    name = None
    try:
        name = v["1205891"][3][0][0]
    except Exception:
        pass
    if not name and "525001528" in v:
        try:
            name = v["525001528"][2]
        except Exception:
            pass
    if not name or len(name.strip()) < 2:
        return None
    name = name.strip()

    categories = []
    try:
        cats_raw = v.get("21255108", [])[0][0]
        for c in cats_raw:
            if isinstance(c, list) and len(c) > 0 and isinstance(c[0], str):
                categories.append(c[0])
    except Exception:
        pass

    comment = None
    if "137321474" in v:
        try:
            comment = v["137321474"][0][0][42][0][0]
            if comment and isinstance(comment, str):
                comment = comment.strip(' "')
        except Exception:
            pass

    lat, lon, rating = None, None, None
    def extract_props(obj):
        nonlocal lat, lon
        if isinstance(obj, (int, float)):
            fval = float(obj)
            if lat is None and 35.0 <= fval <= 43.0 and len(str(fval)) > 6:
                lat = fval
            elif lon is None and 25.0 <= fval <= 46.0 and len(str(fval)) > 6:
                lon = fval
        elif isinstance(obj, list):
            if len(obj) >= 2 and isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                f0, f1 = float(obj[0]), float(obj[1])
                if 35.0 <= f0 <= 43.0 and 25.0 <= f1 <= 46.0:
                    lat, lon = f0, f1
            for it in obj:
                extract_props(it)
        elif isinstance(obj, dict):
            for val in obj.values():
                extract_props(val)

    extract_props(v)

    reviews_count = 0
    try:
        f78 = v["1205891"][78]
        sub = f78[0][1][0]
        if len(sub) > 5 and isinstance(sub[5], int):
            reviews_count = sub[5]
        if len(sub) > 6 and isinstance(sub[6], (int, float)):
            rating = round(float(sub[6]), 1)
        elif len(f78) > 1 and len(f78[1]) > 1 and len(f78[1][1]) > 0:
            sub1 = f78[1][1][0]
            if len(sub1) > 6 and isinstance(sub1[6], (int, float)):
                rating = round(float(sub1[6]), 1)
    except Exception:
        pass

    if not lat or not lon:
        return None

    place_id = hashlib.sha256(f"{name}_{lat}_{lon}".encode("utf-8")).hexdigest()[:24]
    cid = str(abs(hash(place_id)) % (10**18))

    parts = query.split()
    il = parts[-2] if len(parts) >= 2 else ""
    ilce = parts[-3] if len(parts) >= 3 else ""

    return {
        "google_place_id": place_id,
        "cid": cid,
        "isim": name,
        "arama_terimi": query,
        "ana_kategori": categories[0] if categories else "Ticari Mekan",
        "alt_kategoriler": json.dumps(categories[1:], ensure_ascii=False) if len(categories) > 1 else None,
        "tum_kategoriler": json.dumps(categories, ensure_ascii=False) if categories else None,
        "puan": rating,
        "yorum_sayisi": reviews_count,
        "degerlendirme_sayisi": reviews_count,
        "yildiz_dagilimi": None,
        "tam_adres": f"{query.replace('restoranlar', '').replace('dükkanlar', '').strip()}",
        "mahalle": parts[0] if len(parts) > 0 else None,
        "ilce": ilce,
        "il": il,
        "lat": lat,
        "lon": lon,
        "telefon": None,
        "calisma_saatleri": None,
        "maps_url": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
        "kaynak": "Google Maps PB",
        "guncellenme_tarihi": now_utc,
        "ornek_yorum": comment
    }

PAGE_SIZE = 20

def fetch_google_places(query, offset=0):
    """Google Maps üzerinden tekil değil, sorguda dönen TÜM ticari işletmeleri zengin liste halinde çeker.

    offset: sayfalama (0, 20, 40 ...). pb parametresindeki `!7i20` sayfa boyu, `!8iN` ise
    atlanacak sonuç sayısıdır. (Eski sürümde `!8i20` sabitti; yani her sorgunun ilk 20 —
    en alakalı — sonucu atlanıyor, 20'den az sonucu olan mahalleler "boş" görünüyordu.)
    """
    encoded_q = urllib.parse.quote(query)
    pb_param = (
        "!4m12!1m3!1d150000!2d35.0!3d39.0"
        "!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1"
        f"!7i{PAGE_SIZE}!8i{int(offset)}!10b1!12m8!1m1!18b1!2m3!5m1!6e2!20e3!10b1!16b1"
        "!19m4!2m3!1i360!2i120!4i8"
        "!20m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        "!7m42!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3"
        "!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e9!2b1!3e2!1m3!1e10!2b0!3e3"
        "!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!2b1!4b1!9b0"
        "!22m3!1s!7e81!15b1!24m2!2b1!4b1!26m4!1e12!1e13!1e3!1e1!30m1!2b1!36b1"
        "!43b1!52b1!47m0!49m7!3b1!6m2!1b1!2b1!7m2!1e3!2b1!50m4!2e3!3m2!1b1!3b1"
    )

    urls = [
        f"https://www.google.com/search?tbm=map&hl=tr&gl=tr&q={encoded_q}&pb={pb_param}",
    ]
    if offset == 0:
        # Yedek uç nokta sayfalama bilmez; yalnızca ilk sayfa için kullanılır.
        urls.append(f"https://www.google.com/search?tbm=map&tch=1&hl=tr&q={encoded_q}")

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "*/*",
        "Referer": "https://www.google.com/maps"
    }

    venues = []
    found_ids = set()
    now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=14) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                data = parse_google_maps_response(content)
                if not data:
                    continue

                # 1. Zengin PB Düğüm Taraması (1205891 blokları)
                def find_rich_nodes(obj):
                    if isinstance(obj, dict):
                        if "1205891" in obj:
                            v = parse_pb_venue_dict(obj, query, now_utc)
                            if v and is_valid_commercial_venue(
                                v["isim"], v["ana_kategori"], v.get("puan"), v.get("yorum_sayisi")
                            ):
                                identity = v.get("google_place_id") or (v["isim"], v["lat"], v["lon"])
                                if identity not in found_ids:
                                    found_ids.add(identity)
                                    venues.append(v)
                            return
                        for val in obj.values():
                            find_rich_nodes(val)
                    elif isinstance(obj, list):
                        for item in obj:
                            find_rich_nodes(item)

                find_rich_nodes(data)

                # 2. V14 Geleneksel Düğüm Taraması
                for v14 in iter_v14_records(data):
                    v = extract_venue_from_v14(v14, query)
                    if not v or not is_valid_commercial_venue(
                        v["isim"], v["ana_kategori"], v.get("puan"), v.get("yorum_sayisi")
                    ):
                        continue
                    identity = v.get("google_place_id") or (v["isim"], v["lat"], v["lon"])
                    if identity not in found_ids:
                        found_ids.add(identity)
                        venues.append(v)

                if len(venues) >= 3:
                    break
        except Exception as e:
            continue

    return venues

def save_venue(conn, venue):

    # KURAL: Koordinatsız işletme kaydedilmez
    if not venue.get("lat") or not venue.get("lon"):
        return

    if not venue:
        return False
    cur = conn.cursor()
    sector, canonical_category, subcategories = classify(
        venue.get("isim"), venue.get("ana_kategori"), venue.get("tum_kategoriler")
    )
    venue["sektor"] = venue.get("sektor") or sector
    venue["alt_kategoriler"] = venue.get("alt_kategoriler") or subcategories
    if not venue.get("ana_kategori") or venue.get("ana_kategori") == "Ticari Mekan":
        venue["ana_kategori"] = canonical_category
    observed_at = venue["guncellenme_tarihi"]
    payload = json.dumps(venue, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    observation_id = hashlib.sha256(
        f"google_places|{venue['google_place_id']}|{observed_at}|{payload_hash}".encode("utf-8")
    ).hexdigest()
    cur.execute("""
    INSERT OR IGNORE INTO google_places_gozlem (
        observation_id, google_place_id, observed_at, arama_terimi, isim,
        ana_kategori, tum_kategoriler, puan, yorum_sayisi, degerlendirme_sayisi,
        tam_adres, mahalle, ilce, il, lat, lon, telefon, calisma_saatleri,
        maps_url, kaynak, payload_sha256
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        observation_id, venue["google_place_id"], observed_at, venue.get("arama_terimi"), venue["isim"],
        venue.get("ana_kategori"), venue.get("tum_kategoriler"), venue.get("puan"),
        venue.get("yorum_sayisi"), venue.get("degerlendirme_sayisi") or venue.get("yorum_sayisi"),
        venue.get("tam_adres"), venue.get("mahalle"), venue.get("ilce"), venue.get("il"),
        venue["lat"], venue["lon"], venue.get("telefon"), venue.get("calisma_saatleri"),
        venue.get("maps_url"), venue.get("kaynak") or "Google Places", payload_hash,
    ))
    cur.execute("""
    INSERT OR REPLACE INTO google_places_ticari_yogunluk (
        google_place_id, cid, isim, arama_terimi, sektor, ana_kategori, alt_kategoriler, tum_kategoriler,
        puan, yorum_sayisi, degerlendirme_sayisi, yildiz_dagilimi, tam_adres, mahalle, ilce, il,
        lat, lon, telefon, calisma_saatleri, maps_url, kaynak, guncellenme_tarihi, ornek_yorum
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        venue["google_place_id"],
        venue["cid"],
        venue["isim"],
        venue["arama_terimi"],
        venue.get("sektor"),
        venue["ana_kategori"],
        venue.get("alt_kategoriler"),
        venue["tum_kategoriler"],
        venue["puan"],
        venue.get("yorum_sayisi"),
        venue.get("degerlendirme_sayisi") or venue.get("yorum_sayisi"),
        venue.get("yildiz_dagilimi"),
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
        venue["guncellenme_tarihi"],
        venue.get("ornek_yorum")
    ))
    if venue.get("ornek_yorum"):
        try:
            cur.execute("""
            INSERT OR IGNORE INTO google_places_yorumlar_ve_niyet (
                google_place_id, mekan_adi, yorum_metni, puan, kayit_tarihi
            ) VALUES (?, ?, ?, ?, ?)
            """, (
                venue["google_place_id"],
                venue["isim"],
                venue["ornek_yorum"],
                venue.get("puan"),
                venue["guncellenme_tarihi"]
            ))
        except Exception:
            pass
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
REHBER_JSON = os.path.join(BASE_DIR, "collector/turkiye_il_ilce_rehberi.json")

# 6360 sayılı kanun kapsamındaki 30 büyükşehir (slug). Bu illerde köyler "mahalle" olarak
# listelendiği için isimden köy ayıklamak mümkün değildir; ayrım yoğunluk üzerinden yapılır.
BUYUKSEHIR_SLUGS = {
    "istanbul", "ankara", "izmir", "bursa", "antalya", "adana", "konya", "gaziantep", "mersin",
    "kocaeli", "diyarbakir", "hatay", "manisa", "kayseri", "samsun", "balikesir", "kahramanmaras",
    "van", "aydin", "denizli", "sakarya", "tekirdag", "mugla", "eskisehir", "mardin", "malatya",
    "trabzon", "erzurum", "ordu", "sanliurfa",
}

# Katman (tier) tanımları:
#  1 = büyükşehirde kentsel mahalle  -> tam derinlik, sayfalama, ek kategoriler
#  2 = diğer illerde kentsel mahalle -> standart derinlik
#  3 = kırsal mahalle / eski köy     -> tek sorgu, derinleşme yok
TIER_PROFILE = {
    1: {"temel": ("dükkanlar", "restoranlar"), "derin_esik": 3, "max_sayfa": 6, "derin_sayfa": 4, "ek_kategori": True},
    2: {"temel": ("dükkanlar", "restoranlar"), "derin_esik": 3, "max_sayfa": 3, "derin_sayfa": 2, "ek_kategori": False},
    3: {"temel": ("dükkanlar",), "derin_esik": None, "max_sayfa": 1, "derin_sayfa": 1, "ek_kategori": False},
}
KENTSEL_KOMSU_YARICAP_KM = 2.0   # bu yarıçapta ...
KENTSEL_KOMSU_ESIK = 2           # ... en az bu kadar başka mahalle varsa "kentsel" sayılır

DEEP_SUBCATEGORIES = [
    "restoranlar", "kafeler", "pastaneler fırınlar", "marketler bakkallar",
    "manavlar kuruyemişçiler kasaplar", "giyim mağazaları", "elektronikçiler",
    "sağlık medikal", "kuaför güzellik", "yapı tesisat", "otomotiv servisleri",
]
DEEP_SUBCATEGORIES_EXTRA = [
    "eczaneler", "oteller pansiyonlar", "spor salonları", "mobilya dekorasyon mağazaları",
    "emlak ofisleri", "eğitim kursları dershaneler",
]

# sorgu metni -> {"tier": 1|2|3, "tip": "temel"|"derin"|"koridor"}  (main döngüsü buradan okur)
QUERY_META = {}

_MAHALLE_CACHE = None

def _load_rehber_names():
    """slug -> gerçek isim eşlemesi (kandira -> Kandıra, 19mayis -> 19 Mayıs)."""
    il_map, ilce_map = {}, {}
    if os.path.exists(REHBER_JSON):
        try:
            with open(REHBER_JSON, "r", encoding="utf-8") as f:
                rehber = json.load(f)
            for v in rehber.values():
                il_map[v["city_slug"]] = v["city_name"]
                for c in v.get("ilceler", []):
                    ilce_map[(v["city_slug"], c["county_slug"])] = c["county_name"]
        except Exception:
            pass
    return il_map, ilce_map

def load_mahalle_tiers():
    """Tüm mahalleleri okur, kentsel/kırsal yoğunluk analizini yapar ve
    [(key, il_adi, ilce_adi, mahalle_adi, tier), ...] listesini (sabit sırayla) döndürür."""
    global _MAHALLE_CACHE
    if _MAHALLE_CACHE is not None:
        return _MAHALLE_CACHE
    if not os.path.exists(MAHALLE_JSON):
        _MAHALLE_CACHE = []
        return _MAHALLE_CACHE
    import math
    with open(MAHALLE_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    il_map, ilce_map = _load_rehber_names()
    # İsmi "Köyü"/"Mezra" olanlar zaten kırsaldır; geri kalan için yoğunluk ölçülür.
    keys = [k for k, v in data.items()
            if "Köyü" not in v.get("name", "") and "Mezra" not in v.get("name", "")
            and v.get("lat") is not None and v.get("lon") is not None]
    pts = {k: (float(data[k]["lat"]), float(data[k]["lon"])) for k in keys}
    cell_lat, cell_lon = 0.02, 0.025   # ~2.2 km hücreler
    grid = {}
    for k, (la, lo) in pts.items():
        grid.setdefault((int(la / cell_lat), int(lo / cell_lon)), []).append(k)

    def komsu_sayisi(k):
        la, lo = pts[k]
        gi, gj = int(la / cell_lat), int(lo / cell_lon)
        n = 0
        cos_la = math.cos(math.radians(la))
        for i in (gi - 1, gi, gi + 1):
            for j in (gj - 1, gj, gj + 1):
                for o in grid.get((i, j), ()):
                    if o == k:
                        continue
                    dl = (pts[o][0] - la) * 111.0
                    dn = (pts[o][1] - lo) * 111.0 * cos_la
                    if dl * dl + dn * dn <= KENTSEL_KOMSU_YARICAP_KM ** 2:
                        n += 1
        return n

    out = []
    for k in keys:
        parts = k.split("_")
        il_slug = parts[0]
        ilce_slug = parts[1] if len(parts) > 1 else ""
        il = il_map.get(il_slug, il_slug.capitalize())
        ilce = ilce_map.get((il_slug, ilce_slug), ilce_slug.capitalize())
        mah = data[k].get("name", "").replace("Mahallesi", "").strip()
        kentsel = komsu_sayisi(k) >= KENTSEL_KOMSU_ESIK
        tier = 1 if (kentsel and il_slug in BUYUKSEHIR_SLUGS) else (2 if kentsel else 3)
        out.append((k, il, ilce, mah, tier))
    _MAHALLE_CACHE = out
    return out

def build_mahalle_queries(mah_entries):
    """Mahalle kayıtlarından katmana göre temel sorguları üretir ve QUERY_META'yı doldurur."""
    queries = []
    for _key, il, ilce, mah, tier in mah_entries:
        prefix = f"{mah} Mahallesi {ilce} {il}".replace("  ", " ").strip()
        for suffix in TIER_PROFILE[tier]["temel"]:
            q = f"{prefix} {suffix}"
            QUERY_META[q] = {"tier": tier, "tip": "temel"}
            queries.append(q)
    return queries

def get_mahalle_queries_by_shard(shard_id, total_shards=40, limit=None):
    """Her shard için mahalleleri sırayla dağıtır (i % total == shard-1). Ardışık dilimleme
    yerine serpiştirme kullanılır ki her shard'a büyükşehir ve kırsal karışık düşsün
    (koşu süreleri dengelenir)."""
    try:
        entries = [e for i, e in enumerate(load_mahalle_tiers()) if i % total_shards == shard_id - 1]
        if limit and limit > 0:
            entries = entries[:limit]
        counts = {1: 0, 2: 0, 3: 0}
        for e in entries:
            counts[e[4]] += 1
        print(f"Shard {shard_id}/{total_shards} mahalle katmanları: "
              f"büyükşehir-kentsel={counts[1]} kentsel={counts[2]} kırsal={counts[3]}")
        return build_mahalle_queries(entries)
    except Exception as e:
        print(f"[!] Mahalle sorguları üretilemedi: {e}")
        return []


def _street_from_address(address):
    text = str(address or "")
    for pattern in (
        r"([^,;/]+?\s+(?:Caddesi|Cadde|Cd\.?))(?=\s|,|;|/|$)",
        r"([^,;/]+?\s+(?:Sokak|Sokağı|Sk\.?))(?=\s|,|;|/|$)",
        r"([^,;/]+?\s+(?:Bulvarı|Bulvar|Blv\.?))(?=\s|,|;|/|$)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,;/")
    return None


def get_street_queries_by_shard(shard_id, total_shards=40, limit=None):
    """Yerel ambarlardaki acik adreslerden kararlı sokak tarama tohumlari üret."""
    sources = [
        (os.path.join(BASE_DIR, "warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite"),
         "SELECT tam_adres,sehir,ilce,NULL FROM uye_restoranlar_ve_hacim WHERE tam_adres IS NOT NULL"),
        (os.path.join(BASE_DIR, "warehouse/product/google_places_ve_yogunluk.sqlite"),
         "SELECT tam_adres,il,ilce,mahalle FROM google_places_ticari_yogunluk WHERE tam_adres IS NOT NULL"),
        (os.path.join(BASE_DIR, "warehouse/product/zincir_markalar_ve_finans.sqlite"),
         "SELECT adres_acik,il,ilce,mahalle FROM poi_zincir_ve_finans WHERE adres_acik IS NOT NULL"),
    ]
    seeds = set()
    for db_path, sql in sources:
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            for address, il, ilce, mahalle in conn.execute(sql):
                street = _street_from_address(address)
                if street and il:
                    seeds.add((str(il), str(ilce or ""), str(mahalle or ""), street))
            conn.close()
        except Exception:
            continue
    selected = []
    for seed in sorted(seeds):
        digest = int(hashlib.sha256("|".join(seed).encode("utf-8")).hexdigest()[:12], 16)
        if digest % total_shards == shard_id - 1:
            selected.append(seed)
    if limit and limit > 0:
        selected = selected[:limit]
    category_groups = (
        "restoran kafe pastane",
        "market bakkal manav kuruyemiş kasap",
        "giyim elektronik mağazaları",
        "sağlık güzellik işletmeleri",
        "yapı tesisat otomotiv işletmeleri",
    )
    return [
        f"{street} {mahalle} {ilce} {il} {group}".replace("  ", " ").strip()
        for il, ilce, mahalle, street in selected
        for group in category_groups
    ]

def get_commercial_corridors_by_shard(shard_id, total_shards=40, mahalle_limit=None, street_limit=None):
    """40 Shard için dengeli 81 il, ilçe ve MAHALLE MAHALLE detaylı ticari sorgu havuzu oluşturur."""
    all_corridors = get_all_commercial_corridor_queries()
    step = max(1, len(all_corridors) // total_shards)
    start = (shard_id - 1) * step
    end = start + step if shard_id < total_shards else len(all_corridors)
    corridor_slice = all_corridors[start:end]
    for q in corridor_slice:
        QUERY_META[q] = {"tier": 1, "tip": "koridor"}

    # Mahalle Mahalle detaylı aramaları ekle (mahalle_limit None ise shard'daki TÜM kentsel mahalleleri alır)
    mahalle_slice = get_mahalle_queries_by_shard(shard_id, total_shards, limit=mahalle_limit)
    street_slice = get_street_queries_by_shard(shard_id, total_shards, limit=street_limit)
    
    combined = corridor_slice + mahalle_slice + street_slice
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
            puan, yorum_sayisi, degerlendirme_sayisi, yildiz_dagilimi, tam_adres, mahalle, ilce, il,
            lat, lon, telefon, calisma_saatleri, maps_url, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            venue["google_place_id"], venue["cid"], venue["isim"], venue["arama_terimi"],
            venue["ana_kategori"], venue["tum_kategoriler"], venue["puan"],
            venue.get("yorum_sayisi"), venue.get("degerlendirme_sayisi") or venue.get("yorum_sayisi"),
            venue.get("yildiz_dagilimi"),
            venue["tam_adres"], venue["mahalle"], venue["ilce"], venue["il"],
            venue["lat"], venue["lon"], venue["telefon"], venue["calisma_saatleri"],
            venue["maps_url"], venue["kaynak"], venue["guncellenme_tarihi"]
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass

def main():
    global HIST_CONN, REFRESH_DAYS_BASARILI, REFRESH_DAYS_BOS
    parser = argparse.ArgumentParser(description="GEOPROP Google Places & Ticari Yoğunluk Toplayıcı (81 İl & 40 Shard)")
    parser.add_argument("--num-shards", type=int, default=40, help="Total shards")
    parser.add_argument("--shard", type=str, help="Shard numarası (örn: 1/40)")
    parser.add_argument("--out", type=str, default=DEFAULT_DB, help="Çıktı sqlite veritabanı")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum işlenecek sorgu sayısı (varsayılan: sınırsız)")
    parser.add_argument("--mahalle-limit", type=int, default=None, help="Shard başına işlenecek mahalle sayısı (varsayılan: tüm kentsel mahalleler)")
    parser.add_argument("--sokak-limit", type=int, default=None, help="Shard basina adres ambarindan alinacak sokak tohumu sayisi")
    parser.add_argument("--max-seconds", type=int, default=14400, help="Maksimum çalışma süresi saniye (varsayılan: 14400 = 4 saat emniyet sınırı)")
    parser.add_argument("--no-deep", action="store_true", help="Yoğun ticari mahallelerde otonom derinleştirmeyi devre dışı bırakır")
    parser.add_argument("--force", action="store_true", help="Daha önce taranmış sorguları atlamadan yeniden tara")
    parser.add_argument("--all", action="store_true", help="Tüm 81 il sorgularını tek seferde çalıştır")
    parser.add_argument("--gecmis-db", type=str, default=None,
                        help="Önceki koşuların birleşik ana ambarı (salt okunur). Orada yakın tarihte taranmış sorgular atlanır.")
    parser.add_argument("--yenileme-gunu", type=float, default=REFRESH_DAYS_BASARILI,
                        help="Dolu sonuç veren sorgunun kaç gün sonra yeniden taranacağı (varsayılan 6.5)")
    parser.add_argument("--bos-yenileme-gunu", type=float, default=REFRESH_DAYS_BOS,
                        help="Boş sonuç veren sorgunun kaç gün sonra yeniden denenceği (varsayılan 45)")
    args = parser.parse_args()

    REFRESH_DAYS_BASARILI = args.yenileme_gunu
    REFRESH_DAYS_BOS = args.bos_yenileme_gunu
    if args.gecmis_db and os.path.exists(args.gecmis_db) and os.path.abspath(args.gecmis_db) != os.path.abspath(args.out):
        try:
            HIST_CONN = sqlite3.connect(f"file:{os.path.abspath(args.gecmis_db)}?mode=ro", uri=True)
            n_hist = HIST_CONN.execute("SELECT COUNT(*) FROM google_places_arama_gecmisi").fetchone()[0]
            print(f"📚 Geçmiş ambar bağlandı: {args.gecmis_db} ({n_hist:,} sorgu kaydı) — yakın tarihli sorgular atlanacak.")
        except Exception as e:
            print(f"[!] Geçmiş ambar açılamadı ({e}); sıfırdan taranacak.")
            HIST_CONN = None
    elif args.gecmis_db:
        print(f"ℹ️ Geçmiş ambar bulunamadı ({args.gecmis_db}); ilk koşu gibi sıfırdan taranacak.")

    init_db(args.out)
    conn = sqlite3.connect(args.out)

    if args.all:
        initial_queries = get_all_commercial_corridor_queries()
        print(f"81 İl Tam Kapsama Modu: {len(initial_queries)} ticari koridor ve ilçe sorgulanıyor...")
    elif args.shard:
        if "/" in args.shard:
            shard_id = int(args.shard.split("/")[0])
            total = int(args.shard.split("/")[1])
        else:
            shard_id = int(args.shard)
            total = int(args.num_shards)
        initial_queries = get_commercial_corridors_by_shard(
            shard_id, total, mahalle_limit=args.mahalle_limit, street_limit=args.sokak_limit
        )
        print(f"Shard {shard_id}/{total}: {len(initial_queries)} temel ticari & mahalle sorgusu yüklendi...")
    else:
        initial_queries = get_all_commercial_corridor_queries()
        print(f"Varsayılan Mod: {len(initial_queries)} ticari aks sorgulanıyor...")

    if args.limit:
        initial_queries = initial_queries[:args.limit]

    work_queue = deque(initial_queries)
    seen_queries = set(initial_queries)

    print(f"Başlatıldı: Toplam {len(work_queue)} başlangıç sorgusu işlenecek.")
    if not args.no_deep:
        print("⚡ Otonom Derinleştirme AKTİF: Yoğun bulunan mahallelerde çarşı dükkanları otomatik derinleştirilecek.")

    success = 0
    processed = 0
    skipped = 0
    pages_fetched = 0
    start_time = time.time()
    deep_enabled = not args.no_deep

    def enqueue_deep(q, found_count):
        """'dükkanlar' sorgusunda yeterli işletme bulunduysa mahallenin alt kategori
        sorgularını kuyruğa ekler. Katman 3 (kırsal) hiç derinleşmez."""
        if not deep_enabled or " dükkanlar" not in q:
            return
        meta = QUERY_META.get(q, {"tier": 2, "tip": "temel"})
        profile = TIER_PROFILE[meta["tier"]]
        if profile["derin_esik"] is None or found_count < profile["derin_esik"]:
            return
        base_prefix = q.replace(" dükkanlar", "").strip()
        cats = list(DEEP_SUBCATEGORIES) + (DEEP_SUBCATEGORIES_EXTRA if profile["ek_kategori"] else [])
        for sub_cat in cats:
            sub_q = f"{base_prefix} {sub_cat}"
            if sub_q in seen_queries:
                continue
            seen_queries.add(sub_q)
            QUERY_META[sub_q] = {"tier": meta["tier"], "tip": "derin"}
            if not args.force and is_query_done(conn, sub_q):
                continue
            work_queue.append(sub_q)
        print(f"  [⚡ Otonom Derinleştirme] Katman {meta['tier']} ticari aks -> {len(cats)} alt kategori kuyruğa eklendi: '{base_prefix}'", flush=True)

    while work_queue:
        if args.max_seconds and (time.time() - start_time) >= args.max_seconds:
            print(f"\n⏰ [EMNİYET SINIRI] Maksimum çalışma süresine ({args.max_seconds} sn = {args.max_seconds/3600:.1f} saat) ulaşıldı!", flush=True)
            print("Veritabanı güvenle kaydedilip kapatılıyor...", flush=True)
            break

        q = work_queue.popleft()

        # Önceden işlenmişse ve force yoksa hızlıca atla (otonom kaldığı yerden devam).
        # Atlanan bir temel sorgunun geçmişteki sonucu yeterliyse derinleşme yine kuyruğa girer;
        # böylece önceki koşuda süre yetmediği için yarım kalan alt kategoriler tamamlanır.
        if not args.force and is_query_done(conn, q):
            skipped += 1
            hist = get_query_history(conn, q)
            if hist:
                enqueue_deep(q, hist[0] or 0)
            continue

        processed += 1
        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"[{processed}] (Kuyrukta: {len(work_queue)} | Atlanan: {skipped} | Hız: {rate:.1f} sorgu/sn) Sorgu: '{q}'...", flush=True)

        meta = QUERY_META.get(q, {"tier": 2, "tip": "temel"})
        profile = TIER_PROFILE[meta["tier"]]
        max_pages = profile["derin_sayfa"] if meta["tip"] == "derin" else profile["max_sayfa"]

        # Sayfalama: sayfa doluysa (20 sonuç) ve katman izin veriyorsa bir sonraki sayfayı da çek.
        venues = []
        seen_ids = set()
        for page in range(max_pages):
            page_venues = fetch_google_places(q, offset=page * PAGE_SIZE)
            pages_fetched += 1
            new_in_page = 0
            for v in page_venues:
                identity = v.get("google_place_id") or (v["isim"], v["lat"], v["lon"])
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                venues.append(v)
                new_in_page += 1
            if len(page_venues) < PAGE_SIZE or new_in_page == 0:
                break
            time.sleep(random.uniform(0.3, 0.6))
        if len(venues) > PAGE_SIZE:
            print(f"  [📄 Sayfalama] {q} -> {len(venues)} sonuç ({min(max_pages, (len(venues) + PAGE_SIZE - 1) // PAGE_SIZE)} sayfa)", flush=True)

        if venues:
            for res in venues:
                save_venue(conn, res)
                sync_to_bati_warehouse(res)
                success += 1
                puan_str = f"Puan: {res['puan']} ★" if res['puan'] is not None else "Puan: -"
                deg_cnt = res.get('degerlendirme_sayisi')
                yor_cnt = res.get('yorum_sayisi')
                if deg_cnt is not None and yor_cnt is not None and deg_cnt != yor_cnt:
                    metrics_str = f"({deg_cnt:,} kişi oy verdi, {yor_cnt:,} kişi yazılı yorum yaptı)"
                elif deg_cnt is not None:
                    metrics_str = f"({deg_cnt:,} kişi değerlendirdi/oy verdi)"
                elif yor_cnt is not None:
                    metrics_str = f"({yor_cnt:,} yazılı yorum)"
                else:
                    metrics_str = "(0 değerlendirme)"
                print(f"  -> Bulundu: {res['isim']} | Kat: {res['ana_kategori']} | {puan_str} {metrics_str} | ({res['lat']:.4f}, {res['lon']:.4f})", flush=True)

            # OTONOM DERİNLEŞTİRME (katmana göre): 'dükkanlar' sorgusunda yeterli işletme
            # bulunan mahallede alt kategori sorguları kuyruğa eklenir; kırsal katman derinleşmez.
            enqueue_deep(q, len(venues))
        else:
            print(f"  -> Sonuç alınamadı: {q}", flush=True)

        record_query(conn, q, len(venues))
        time.sleep(random.uniform(0.4, 0.8))

    conn.close()
    if HIST_CONN is not None:
        HIST_CONN.close()
    print(f"\nİşlem tamamlandı. Toplam {processed} sorgu ({pages_fetched} sayfa isteği) işlendi, "
          f"{skipped} sorgu geçmişten atlandı, {success} mekan ambarlandı.", flush=True)
    print(f"Çıktı DB: {args.out}", flush=True)

if __name__ == "__main__":
    main()
