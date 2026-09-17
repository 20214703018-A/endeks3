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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_ilce ON google_places_ticari_yogunluk(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_coords ON google_places_ticari_yogunluk(lat, lon)")
    conn.commit()
    conn.close()

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
        if len(f4) > 8 and f4[8] is not None:
            try:
                reviews = int(f4[8])
            except Exception:
                pass
        # Fallback: f4[3][1] örneğin '15.969 yorum'
        if reviews is None and len(f4) > 3 and f4[3] and isinstance(f4[3], list) and len(f4[3]) > 1:
            try:
                s = str(f4[3][1])
                digits = "".join(ch for ch in s if ch.isdigit())
                if digits:
                    reviews = int(digits)
            except Exception:
                pass
    
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

def fetch_google_place(query):
    encoded_q = urllib.parse.quote(query)
    url = f"https://www.google.com/search?tbm=map&tch=1&hl=tr&q={encoded_q}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "*/*"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            data = parse_google_maps_response(content)
            if not data:
                return None
            
            # Durum 1: data[0][1] içinde doğrudan mekan
            if len(data) > 0 and len(data[0]) > 1 and data[0][1]:
                item0 = data[0][1][0]
                if isinstance(item0, list) and len(item0) > 14 and item0[14]:
                    return extract_venue_from_v14(item0[14], query)
            
            # Durum 2: data[37] tekil aday
            if len(data) > 37 and data[37] and isinstance(data[37], list) and len(data[37]) > 2:
                d37 = data[37]
                sub_list = d37[2]
                if sub_list and len(sub_list) > 0 and len(sub_list[0]) > 4:
                    cand = sub_list[0]
                    coords = cand[3] if len(cand) > 3 else None
                    cid = cand[2] if len(cand) > 2 else None
                    title = cand[4] if len(cand) > 4 else None
                    if coords and len(coords) >= 4 and coords[2] is not None and coords[3] is not None:
                        now_utc = datetime.now(timezone.utc).isoformat()
                        return {
                            "google_place_id": f"CID_{cid}",
                            "cid": str(cid),
                            "isim": str(title or query),
                            "arama_terimi": query,
                            "ana_kategori": None,
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
                            "kaynak": "Google Maps (Disambiguation)",
                            "guncellenme_tarihi": now_utc
                        }
    except Exception as e:
        print(f"Hata ({query}): {e}", file=sys.stderr)
    return None

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

def get_commercial_corridors_by_shard(shard_id, total_shards=40):
    """40 Shard için dengeli ticari sorgu havuzu oluşturur."""
    all_corridors = [
        # İstanbul - Anadolu
        "Starbucks Moda Kadıköy İstanbul", "Espressolab Moda Kadıköy İstanbul", "Midpoint Caddebostan Kadıköy İstanbul",
        "Big Chefs Moda Teras Kadıköy İstanbul", "Happy Moon's Fenerbahçe Kadıköy İstanbul", "Cookshop Bağdat Caddesi Kadıköy İstanbul",
        "Günaydın Kebap Suadiye Kadıköy İstanbul", "Divan Brasserie Kalamış Kadıköy İstanbul", "Akasya AVM Üsküdar İstanbul",
        "Emaar Square AVM Üsküdar İstanbul", "Mado Üsküdar Sahil İstanbul", "Starbucks Ataşehir Metropol İstanbul",
        "Watergarden Ataşehir İstanbul", "Brandium AVM Ataşehir İstanbul", "Piazza AVM Maltepe İstanbul",
        "Hilltown AVM Maltepe İstanbul", "Viaport Asia Pendik İstanbul", "Pendik Marina Çarşı İstanbul",
        # İstanbul - Avrupa
        "Zorlu Center Beşiktaş İstanbul", "Kanyon AVM Levent Beşiktaş İstanbul", "İstinyePark AVM Sarıyer İstanbul",
        "Vadistanbul AVM Sarıyer İstanbul", "Cevahir AVM Şişli İstanbul", "Lucca Bebek Beşiktaş İstanbul",
        "Mangerie Bebek Beşiktaş İstanbul", "Bebek Kahve Beşiktaş İstanbul", "Emirgan Sütiş Sarıyer İstanbul",
        "House Cafe Ortaköy Beşiktaş İstanbul", "Midyeci Ahmet Beşiktaş Çarşı İstanbul", "Nusr-Et Steakhouse Etiler Beşiktaş İstanbul",
        "Cookshop Nişantaşı Şişli İstanbul", "House Cafe Nişantaşı Şişli İstanbul", "Midpoint Beyoğlu İstiklal İstanbul",
        "Hafız Mustafa Sirkeci Fatih İstanbul", "Pandeli Restoran Mısır Çarşısı Fatih İstanbul", "Karaköy Güllüoğlu Beyoğlu İstanbul",
        "Mall of İstanbul Başakşehir İstanbul", "Marmara Forum Bakırköy İstanbul", "Capacity AVM Bakırköy İstanbul",
        "Ataköy Marina Bakırköy İstanbul", "Aqua Florya AVM Bakırköy İstanbul", "Torium AVM Esenyurt İstanbul",
        # İzmir
        "Midpoint Alsancak Konak İzmir", "Mado Kordon Alsancak Konak İzmir", "Sevinç Pastanesi Alsancak Konak İzmir",
        "Reyhan Pastanesi Alsancak Konak İzmir", "Kıbrıs Şehitleri Caddesi Konak İzmir", "İstinyePark İzmir Balçova İzmir",
        "Hilltown AVM Karşıyaka İzmir", "Mavibahçe AVM Karşıyaka İzmir", "Optimum AVM Gaziemir İzmir",
        "Bostanlı Balıkçılar Meydanı Karşıyaka İzmir", "Agora AVM Balçova İzmir", "Forum Bornova AVM İzmir",
        "Kordon Boyu Konak İzmir", "Tarihi Kemeraltı Çarşısı Konak İzmir", "Alaçatı Çarşı Çeşme İzmir",
        "Starbucks Alaçatı Çeşme İzmir", "Urla Sanat Sokağı İzmir", "Urla İskele Balıkçılar İzmir",
        # Bursa
        "Zafer Plaza AVM Osmangazi Bursa", "Korupark AVM Osmangazi Bursa", "Sur Yapı Marka AVM Nilüfer Bursa",
        "Anatolium AVM Osmangazi Bursa", "Downtown AVM Osmangazi Bursa", "Fatih Sultan Mehmet Bulvarı Nilüfer Bursa",
        "Özlüce Ahmet Taner Kışlalı Bulvarı Nilüfer Bursa", "Kebapçı İskender Osmangazi Bursa", "Tarihi Uludağ Kebapçısı Osmangazi Bursa",
        # Antalya
        "TerraCity AVM Muratpaşa Antalya", "Mall of Antalya Kepez Antalya", "MarkAntalya AVM Muratpaşa Antalya",
        "5M Migros AVM Konyaaltı Antalya", "Agora AVM Kepez Antalya", "Lara Caddesi Muratpaşa Antalya",
        "Konyaaltı Sahil Yaşam Parkı Antalya", "Kaleiçi Çarşı Muratpaşa Antalya", "7 Mehmet Muratpaşa Antalya",
        "The Land of Legends Serik Antalya", "Alanyum AVM Alanya Antalya", "Side Antik Kenti Manavgat Antalya",
        # Muğla & Aydın
        "Midpoint Bodrum Marina Muğla", "Marina Yacht Club Bodrum Muğla", "Yalıkavak Marina Bodrum Muğla",
        "Bodrum Barlar Sokağı Muğla", "Gümüşlük Sahil Balıkçıları Bodrum Muğla", "Göcek Marina Fethiye Muğla",
        "Fethiye Paspatur Çarşısı Muğla", "Marmaris Marina Muğla", "Akyaka Azmak Nehri Ula Muğla",
        "Starbucks Kuşadası Marina Aydın", "Kuşadası Barlar Sokağı Aydın", "Didim Altınkum Sahil Aydın",
        # Kocaeli & Balıkesir & Tekirdağ
        "Symbol AVM İzmit Kocaeli", "41 Burda AVM İzmit Kocaeli", "Outlet Center İzmit Kocaeli",
        "Gebze Center AVM Kocaeli", "10 Burda AVM Altıeylül Balıkesir", "Ayvalık Cunda Sahil Balıkesir",
        "Ayvalık Taksiyarhis Kilisesi Çevresi Balıkesir", "Tekira AVM Süleymanpaşa Tekirdağ",
        # Antalya Genişletilmiş Çıpa Koridorlar
        "Kültür Mahallesi Kafe Caddesi Kepez Antalya", "Şarampol Caddesi Yaya Yolu Muratpaşa Antalya",
        "Işıklar Caddesi Muratpaşa Antalya", "Güllük Caddesi Muratpaşa Antalya",
        "Akdeniz Üniversitesi Kampüs Çarşı Kepez Antalya", "Lara Balıkevi Muratpaşa Antalya",
        "Big Chefs Lara Muratpaşa Antalya", "Shakespeare Coffee Bistro Konyaaltı Antalya",
        # İzmir Genişletilmiş Çıpa Koridorlar
        "Bornova Küçükpark Meydanı İzmir", "Gül Sokak Alsancak Konak İzmir",
        "Bostanlı Cemal Gürsel Caddesi Karşıyaka İzmir", "Bayraklı Manavkuyu Kafe Koridoru İzmir",
        "Karşıyaka Çarşı Yaya Yolu İzmir", "Asansör Restoran Konak İzmir",
        # Bursa Genişletilmiş Çıpa Koridorlar
        "Görükle Yerleşim Çarşı Nilüfer Bursa", "Heykel Atatürk Caddesi Osmangazi Bursa",
        "Altıparmak Caddesi Osmangazi Bursa", "Starbucks FSM Bulvarı Nilüfer Bursa",
        # Ankara Çıpa Koridorlar
        "Tunalı Hilmi Caddesi Çankaya Ankara", "Kızılay Yüksel Caddesi Çankaya Ankara",
        "Bahçelievler 7. Cadde Çankaya Ankara", "Çukurambar Muhsin Yazıcıoğlu Caddesi Çankaya Ankara",
        "Armada AVM Çankaya Ankara", "Panora AVM Çankaya Ankara", "Ankamall AVM Yenimahalle Ankara",
        # İstanbul Ekstra Çıpa Koridorlar
        "Abdi İpekçi Caddesi Nişantaşı Şişli İstanbul", "Kadıköy Boğa Meydanı İstanbul",
        "Karaköy Kemankeş Caddesi Beyoğlu İstanbul", "Sirkeci Hocapaşa Lezzet Sokağı Fatih İstanbul",
        "Beşiktaş Köyiçi Çarşı İstanbul", "Bağdat Caddesi Şaşkınbakkal Kadıköy İstanbul"
    ]
    # Shard'a göre dilimle
    step = max(1, len(all_corridors) // total_shards)
    start = (shard_id - 1) * step
    end = start + step if shard_id < total_shards else len(all_corridors)
    return all_corridors[start:end]

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Google Places & Ticari Yoğunluk Toplayıcı (40 Shard)")
    parser.add_argument("--shard", type=str, help="Shard numarası (örn: 1/40)")
    parser.add_argument("--out", type=str, default=DEFAULT_DB, help="Çıktı sqlite veritabanı")
    parser.add_argument("--limit", type=int, default=50, help="Maksimum sorgu")
    args = parser.parse_args()

    init_db(args.out)

    if args.shard:
        shard_id, total = map(int, args.shard.split("/"))
        queries = get_commercial_corridors_by_shard(shard_id, total)
        print(f"Shard {shard_id}/{total}: {len(queries)} ticari koridor sorgusu işleniyor...")
    else:
        queries = get_commercial_corridors_by_shard(1, 1)[:args.limit]
        print(f"Varsayılan mod: {len(queries)} sorgu işleniyor...")

    conn = sqlite3.connect(args.out)
    success = 0
    for idx, q in enumerate(queries):
        print(f"[{idx+1}/{len(queries)}] Sorgu: '{q}'...")
        res = fetch_google_place(q)
        if res:
            save_venue(conn, res)
            success += 1
            print(f"  -> Bulundu: {res['isim']} | Kat: {res['ana_kategori']} | Puan: {res['puan']} | Yorum: {res['yorum_sayisi']} | ({res['lat']}, {res['lon']})")
        else:
            print(f"  -> Sonuç alınamadı: {q}")
        time.sleep(random.uniform(0.6, 1.4))

    conn.close()
    print(f"\nİşlem tamamlandı. Toplam {len(queries)} sorgudan {success} mekan ambarlandı.")
    print(f"Çıktı DB: {args.out}")

if __name__ == "__main__":
    main()
