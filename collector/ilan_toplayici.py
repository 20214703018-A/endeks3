#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Hibrit Gayrimenkul & Detaylı Arsa İstihbarat Toplayıcısı
---------------------------------------------------------------------
1. Konut & İşyeri (Dükkan): Arama sayfalarından yüksek hızda (fiyat, m², kat, oda, mahalle ve GPS).
2. Arsa & Tarla: Arama listesi + Eşzamanlı Detay Sayfası Madenciliği ile:
   - Ada No & Parsel No
   - İmar Durumu (Konut, Ticari, Tarla vb.)
   - Tapu Durumu (Müstakil, Hisseli vb.)
   - Kat Karşılığı Verilme Durumu
   - KAKS / Emsal Oranı
   - İlan Sahibinin Haritada İşaretlediği Tam Pin Koordinatı (Lat/Lon)
   - 51.171 Mahalle/Köy Veritabanı ile Mahalle ID Eşleşmesi
"""

import os
import sys
import re
import json
import time
import random
import sqlite3
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ilanlar.db"
GUIDE_PATH = DATA_DIR / "turkiye_il_ilce_rehberi.json"
if not GUIDE_PATH.exists():
    GUIDE_PATH = BASE_DIR / "turkiye_il_ilce_rehberi.json"

CENTROIDS_PATH = BASE_DIR / "mahalle_koordinatlari.json"
if not CENTROIDS_PATH.exists():
    CENTROIDS_PATH = DATA_DIR / "mahalle_koordinatlari.json"

CSV_DIR = DATA_DIR / "csv_ciktilari"

# Modern tarayıcı User-Agent havuzu
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:129.0) Gecko/20100101 Firefox/129.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
]

CATEGORIES = {
    "konut": "satilik-konut",
    "arsa": "satilik-arsa",
    "isyeri": "satilik-isyeri"
}

# 51.171 mahallenin koordinat önbelleği
CENTROIDS_CACHE = None

def load_centroids():
    global CENTROIDS_CACHE
    if CENTROIDS_CACHE is not None:
        return CENTROIDS_CACHE
    if CENTROIDS_PATH.exists():
        try:
            with open(CENTROIDS_PATH, "r", encoding="utf-8") as f:
                CENTROIDS_CACHE = json.load(f)
                return CENTROIDS_CACHE
        except Exception as e:
            print("Centroids yükleme hatası:", e)
    CENTROIDS_CACHE = {}
    return CENTROIDS_CACHE

def find_mahalle_coords(city, county, mahalle):
    """Mahalle adına göre 51.171 noktalı veri setinden merkez koordinatını bulur"""
    centroids = load_centroids()
    if not centroids or not mahalle:
        return None, None, None

    def clean(s):
        tr_map = str.maketrans('çğıöşüÇĞİÖŞÜ', 'cgiosuCGIOSU')
        return re.sub(r'[^a-z0-9]+', '', str(s).translate(tr_map).lower())

    c_s = clean(city)
    co_s = clean(county)
    m_raw = clean(mahalle)
    m_no_suffix = re.sub(r'(mahallesi|mah|koyu|koy)$', '', m_raw)

    candidates = [
        f"{c_s}_{co_s}_{m_raw}",
        f"{c_s}_{co_s}_{m_no_suffix}",
        f"{c_s}_{co_s}_{m_no_suffix}koyu",
        f"{c_s}_{co_s}_{m_no_suffix}mahallesi",
    ]
    for cand in candidates:
        if cand in centroids:
            c = centroids[cand]
            return c.get("id"), c.get("lat"), c.get("lon")
    return None, None, None

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    badges = {
        "INFO": "\033[94m[*]\033[0m",
        "SUCCESS": "\033[92m[✓]\033[0m",
        "WARN": "\033[93m[!]\033[0m",
        "ERROR": "\033[91m[✗]\033[0m"
    }
    print(f"{ts} {badges.get(level, '[*]')} {msg}", flush=True)

class StealthSession:
    """WAF ve bot korumasını atlatmak için insansı oturum motoru"""
    def __init__(self, base_delay=0.4):
        self.base_delay = base_delay
        self.request_count = 0

    def get_headers(self, referer="https://www.emlakjet.com/"):
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
            "Referer": referer,
            "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }

    def fetch_page(self, url, max_retries=3, is_detail=False):
        self.request_count += 1
        delay = (self.base_delay * 0.5 if is_detail else self.base_delay) + random.uniform(0.1, 0.3)
        if self.request_count % 35 == 0:
            delay += random.uniform(2.0, 3.5)
        time.sleep(delay)

        for attempt in range(1, max_retries + 1):
            req = urllib.request.Request(url, headers=self.get_headers(url))
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    final_url = resp.geturl()
                    html = resp.read().decode("utf-8", errors="replace")
                    return html, final_url
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):
                    wait = (6 * attempt) + random.uniform(1.0, 2.5)
                    ra = e.headers.get("Retry-After")
                    if ra and ra.isdigit():
                        wait = max(wait, int(ra) + 1)
                    time.sleep(wait)
                elif e.code == 404:
                    return None, None
                else:
                    time.sleep(1.5 * attempt)
            except Exception:
                time.sleep(1.5 * attempt)
        return None, None

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ilanlar (
        ilan_id INTEGER PRIMARY KEY,
        kategori TEXT,
        tip TEXT,
        baslik TEXT,
        url TEXT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        mahalle_id INTEGER,
        enlem REAL,
        boylam REAL,
        ilan_pin_lat REAL,
        ilan_pin_lon REAL,
        fiyat_tl INTEGER,
        m2 REAL,
        birim_m2_fiyat REAL,
        ada_no TEXT,
        parsel_no TEXT,
        imar_durumu TEXT,
        tapu_durumu TEXT,
        kat_karsiligi TEXT,
        kaks_emsal TEXT,
        oda_sayisi TEXT,
        bulundugu_kat TEXT,
        ilan_etiketi TEXT,
        ilan_tarihi TEXT,
        gorsel_url TEXT,
        city_id INTEGER,
        county_id INTEGER,
        crawled_at TEXT
    )""")

    # Kolon göç kontrolü (migration)
    cur.execute("PRAGMA table_info(ilanlar);")
    cols = [r[1] for r in cur.fetchall()]
    new_cols = {
        "mahalle_id": "INTEGER",
        "enlem": "REAL",
        "boylam": "REAL",
        "ilan_pin_lat": "REAL",
        "ilan_pin_lon": "REAL",
        "ada_no": "TEXT",
        "parsel_no": "TEXT",
        "imar_durumu": "TEXT",
        "tapu_durumu": "TEXT",
        "kat_karsiligi": "TEXT",
        "kaks_emsal": "TEXT"
    }
    for col, ctype in new_cols.items():
        if col not in cols:
            cur.execute(f"ALTER TABLE ilanlar ADD COLUMN {col} {ctype};")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_konum ON ilanlar(il, ilce, mahalle, kategori);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_coords ON ilanlar(enlem, boylam);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_ada_parsel ON ilanlar(ada_no, parsel_no);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_fiyat ON ilanlar(fiyat_tl, m2);")
    conn.commit()
    return conn

def extract_land_detail(html):
    """Arsa detay sayfasından Ada, Parsel, İmar, Tapu, KAKS ve tam harita pinini çıkarır"""
    if not html:
        return {}

    # 1. Tam Harita Pini (Enlem / Boylam)
    pin_m = re.search(r'lat[^\d]+([0-9]{2}\.[0-9]+)[^\d]+lng[^\d]+([0-9]{2}\.[0-9]+)', html)
    pin_lat = float(pin_m.group(1)) if pin_m else None
    pin_lon = float(pin_m.group(2)) if pin_m else None

    ada = None
    parsel = None
    imar = None
    tapu = None
    kat_karsiligi = None

    # 2. Yapılandırılmış Özellik Tablosu
    props = re.findall(r'\"name\":\s*\"([^\"]+)\",\s*\"value\":\s*\"([^\"]+)\"', html)
    for n, v in props:
        nl = n.lower()
        if nl == 'ada':
            ada = v.strip()
        elif nl == 'parsel':
            parsel = v.strip()
        elif 'imar' in nl and not imar:
            imar = v.strip()
        elif 'tapu' in nl and not tapu:
            tapu = v.strip()
        elif 'kat karşılığı' in nl:
            kat_karsiligi = v.strip()

    # 3. İlan Açıklama Metni Analizi
    desc_m = re.search(r'\"description\":\s*\"(.*?)\"', html)
    desc = desc_m.group(1).encode().decode('unicode_escape', errors='ignore') if desc_m else ''

    if not ada and desc:
        m_ada = re.search(r'(?:ada|ada\s*no)[\s:]*([0-9]+)', desc, re.IGNORECASE)
        if m_ada:
            ada = m_ada.group(1)

    if not parsel and desc:
        m_parsel = re.search(r'(?:parsel|parsel\s*no)[\s:]*([0-9]+)', desc, re.IGNORECASE)
        if m_parsel:
            parsel = m_parsel.group(1)

    # İmar durumu tahmini (metinden)
    if not imar and desc:
        d_low = desc.lower()
        if 'konut imar' in d_low: imar = 'Konut İmarlı'
        elif 'ticari imar' in d_low: imar = 'Ticari İmarlı'
        elif 'tarla' in d_low: imar = 'Tarla'
        elif 'zeytinlik' in d_low: imar = 'Zeytinlik'
        elif 'sanayi' in d_low: imar = 'Sanayi İmarlı'
        elif 'turizm' in d_low: imar = 'Turizm İmarlı'

    # KAKS / Emsal oranı
    kaks = None
    if desc:
        m_kaks = re.search(r'(?:kaks|emsal)[^\d:]*([0-9.,]+)', desc, re.IGNORECASE)
        if m_kaks:
            kaks = m_kaks.group(1)

    return {
        "ilan_pin_lat": pin_lat,
        "ilan_pin_lon": pin_lon,
        "ada_no": ada,
        "parsel_no": parsel,
        "imar_durumu": imar,
        "tapu_durumu": tapu,
        "kat_karsiligi": kat_karsiligi,
        "kaks_emsal": kaks
    }

def enrich_land_listing(listing, session):
    """Bir arsa ilanının detay sayfasına girerek ilave alanları ekler"""
    url = listing.get("url")
    if not url:
        return listing

    html, _ = session.fetch_page(url, is_detail=True)
    if html:
        detail_data = extract_land_detail(html)
        listing.update(detail_data)
    return listing

def parse_listings_from_html(html, target_category, city_id, county_id, default_city="", default_county=""):
    if not html:
        return []

    listings = []
    scripts = re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.DOTALL)

    for s in scripts:
        try:
            d = json.loads(s)
            graph = d.get("@graph")
            if not graph or not isinstance(graph, list):
                continue

            for item in graph:
                if item.get("@type") != "RealEstateListing":
                    continue

                url = item.get("url", "")
                m_id = re.search(r'-(\d+)$', url)
                if not m_id:
                    continue
                ilan_id = int(m_id.group(1))

                baslik = item.get("name", "").strip()
                date_posted = item.get("datePosted", "")
                gorsel_url = item.get("image", "")

                offers = item.get("offers") or {}
                fiyat_tl = None
                try:
                    fiyat_tl = int(offers.get("price", 0))
                except Exception:
                    pass

                props = item.get("additionalProperty") or []
                tip = target_category
                mahalle = ""
                ilce = default_county
                il = default_city
                oda = None
                m2_val = None
                kat = None
                etiket = None

                for p in props:
                    p_name = p.get("name", "")
                    p_val = p.get("value", "")

                    if p_name == "İlan Tipi":
                        tip = p_val
                    elif p_name == "Konum":
                        parts = [x.strip() for x in p_val.split(",")]
                        if len(parts) >= 2:
                            mahalle = parts[0]
                            ilce = parts[1]
                        elif len(parts) == 1:
                            mahalle = parts[0]
                    elif p_name == "Oda Sayısı":
                        oda = p_val
                    elif p_name == "Metrekare":
                        m_m2 = re.search(r'([\d.,]+)', str(p_val))
                        if m_m2:
                            try:
                                m2_val = float(m_m2.group(1).replace(".", "").replace(",", "."))
                            except Exception:
                                pass
                    elif p_name == "Kat":
                        kat = p_val
                    elif p_name == "İlan Etiketi":
                        etiket = p_val

                birim_m2 = round(fiyat_tl / m2_val, 1) if (fiyat_tl and m2_val and m2_val > 0) else None

                # Mahalle GPS koordinatları ve Mahalle ID'si
                mahalle_id, lat, lon = find_mahalle_coords(il, ilce, mahalle)

                listings.append({
                    "ilan_id": ilan_id,
                    "kategori": target_category,
                    "tip": tip,
                    "baslik": baslik,
                    "url": url,
                    "il": il,
                    "ilce": ilce,
                    "mahalle": mahalle,
                    "mahalle_id": mahalle_id,
                    "enlem": lat,
                    "boylam": lon,
                    "ilan_pin_lat": None,
                    "ilan_pin_lon": None,
                    "fiyat_tl": fiyat_tl,
                    "m2": m2_val,
                    "birim_m2_fiyat": birim_m2,
                    "ada_no": None,
                    "parsel_no": None,
                    "imar_durumu": None,
                    "tapu_durumu": None,
                    "kat_karsiligi": None,
                    "kaks_emsal": None,
                    "oda_sayisi": oda,
                    "bulundugu_kat": kat,
                    "ilan_etiketi": etiket,
                    "ilan_tarihi": date_posted,
                    "gorsel_url": gorsel_url,
                    "city_id": city_id,
                    "county_id": county_id,
                    "crawled_at": datetime.now().isoformat()
                })
        except Exception:
            pass

    return listings

def save_listings(conn, listings):
    if not listings:
        return 0
    cur = conn.cursor()
    cur.executemany("""
    INSERT OR REPLACE INTO ilanlar (
        ilan_id, kategori, tip, baslik, url, il, ilce, mahalle, mahalle_id,
        enlem, boylam, ilan_pin_lat, ilan_pin_lon, fiyat_tl, m2, birim_m2_fiyat,
        ada_no, parsel_no, imar_durumu, tapu_durumu, kat_karsiligi, kaks_emsal,
        oda_sayisi, bulundugu_kat, ilan_etiketi, ilan_tarihi, gorsel_url,
        city_id, county_id, crawled_at
    ) VALUES (
        :ilan_id, :kategori, :tip, :baslik, :url, :il, :ilce, :mahalle, :mahalle_id,
        :enlem, :boylam, :ilan_pin_lat, :ilan_pin_lon, :fiyat_tl, :m2, :birim_m2_fiyat,
        :ada_no, :parsel_no, :imar_durumu, :tapu_durumu, :kat_karsiligi, :kaks_emsal,
        :oda_sayisi, :bulundugu_kat, :ilan_etiketi, :ilan_tarihi, :gorsel_url,
        :city_id, :county_id, :crawled_at
    )""", listings)
    conn.commit()
    return len(listings)

def export_to_csv(conn, target_dir):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    cur = conn.cursor()

    # 1. ARSALAR (Detaylı Ada/Parsel/İmar Tablosu)
    cur.execute("""
        SELECT ilan_id, kategori, tip, il, ilce, mahalle, mahalle_id, ada_no, parsel_no,
               imar_durumu, tapu_durumu, kaks_emsal, kat_karsiligi,
               ilan_pin_lat, ilan_pin_lon, enlem, boylam,
               fiyat_tl, m2, birim_m2_fiyat, ilan_etiketi, ilan_tarihi, url
        FROM ilanlar WHERE kategori = 'arsa'
        ORDER BY il, ilce, mahalle, fiyat_tl DESC
    """)
    arsa_rows = cur.fetchall()
    if arsa_rows:
        import csv
        f_path = target_dir / "satilik_arsa_ilanlari.csv"
        with open(f_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "İlan ID", "Kategori", "Tip", "İl", "İlçe", "Mahalle", "Mahalle ID", "Ada No", "Parsel No",
                "İmar Durumu", "Tapu Durumu", "KAKS / Emsal", "Kat Karşılığı",
                "İlan Pin Lat", "İlan Pin Lon", "Mahalle Merkez Lat", "Mahalle Merkez Lon",
                "Fiyat (TL)", "m²", "₺/m² Birim Fiyat", "Etiket", "Tarih", "URL"
            ])
            w.writerows(arsa_rows)
        log(f"Arsa ilanları CSV'ye aktarıldı: {f_path.name} ({len(arsa_rows)} satır - Ada/Parsel/İmar Detaylı)", "SUCCESS")

    # 2. KONUT & İŞYERİ
    for kat in ("konut", "isyeri"):
        cur.execute("""
            SELECT ilan_id, kategori, tip, il, ilce, mahalle, mahalle_id, enlem, boylam,
                   fiyat_tl, m2, birim_m2_fiyat, oda_sayisi, bulundugu_kat, ilan_etiketi, ilan_tarihi, url
            FROM ilanlar WHERE kategori = ?
            ORDER BY il, ilce, mahalle, fiyat_tl DESC
        """, (kat,))
        rows = cur.fetchall()
        if not rows:
            continue

        import csv
        f_path = target_dir / f"satilik_{kat}_ilanlari.csv"
        with open(f_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "İlan ID", "Kategori", "Tip", "İl", "İlçe", "Mahalle", "Mahalle ID",
                "Enlem (Lat)", "Boylam (Lon)", "Fiyat (TL)", "m²", "₺/m² Birim Fiyat",
                "Oda Sayısı", "Bulunduğu Kat", "Etiket", "Tarih", "URL"
            ])
            w.writerows(rows)
        log(f"{kat.capitalize()} ilanları CSV'ye aktarıldı: {f_path.name} ({len(rows)} satır)", "SUCCESS")

def main():
    parser = argparse.ArgumentParser(description="GEOPROP AI - Hibrit İlan Toplayıcı")
    parser.add_argument("--iller", type=str, default="34", help="Hedef il plaka/ID (örn: 34,6,35 veya 'hepsi')")
    parser.add_argument("--kategori", type=str, default="hepsi", help="Kategori: konut, arsa, isyeri veya 'hepsi'")
    parser.add_argument("--max-sayfa", type=int, default=30, help="İlçe başına maksimum sayfa derinliği (varsayılan: 30)")
    parser.add_argument("--hiz", type=float, default=0.4, help="İstekler arası temel bekleme (saniye)")
    parser.add_argument("--arsa-detay", dest="arsa_detay", action="store_true", default=True, help="Arsalarda detay sayfasına girerek Ada/Parsel/İmar topla (Varsayılan: Açık)")
    parser.add_argument("--arsa-detaysiz", dest="arsa_detay", action="store_false", help="Arsalarda detay sayfasına girmeden hızlı liste modunda topla")
    parser.add_argument("--sadece-il", action="store_true", help="İlçe kırmadan doğrudan il geneli tara")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db(DB_PATH)
    session = StealthSession(base_delay=args.hiz)

    if not GUIDE_PATH.exists():
        log(f"Rehber dosyası bulunamadı: {GUIDE_PATH}", "ERROR")
        return

    with open(GUIDE_PATH, "r", encoding="utf-8") as f:
        guide = json.load(f)

    centroids = load_centroids()
    log(f"🗺️  Mahalle Koordinat Veritabanı Yüklendi: {len(centroids):,} Mahalle/Köy Aktif", "INFO")

    if args.iller.lower() == "hepsi":
        target_cities = list(guide.keys())
    else:
        target_cities = [x.strip() for x in args.iller.split(",") if x.strip() in guide]

    if args.kategori.lower() == "hepsi":
        target_categories = ["arsa", "konut", "isyeri"]
    else:
        target_categories = [x.strip() for x in args.kategori.split(",") if x.strip() in CATEGORIES]

    log("=" * 65, "INFO")
    log(f"🚀 Hibrit İlan Toplayıcı Başlatıldı | İller: {len(target_cities)} | Kategoriler: {target_categories}", "INFO")
    log(f"📍 Konut & Dükkan: Liste Modu (Hızlı) | Arsa: {'DETAYLI (Ada, Parsel, İmar, Pin)' if args.arsa_detay else 'Liste Modu'}", "INFO")
    log(f"🛡️  Stealth Modu: Aktif (Rastgele Jitter, TLS Browser İmzası, 429 Koruması)", "INFO")
    log("=" * 65, "INFO")

    toplam_toplanan = 0

    for c_idx, cid in enumerate(target_cities, 1):
        c_info = guide[cid]
        c_name = c_info["city_name"]
        c_slug = c_info["city_slug"]
        ilceler = c_info.get("ilceler", [])

        log(f"[{c_idx}/{len(target_cities)}] {c_name} (ID: {cid}) taranıyor... ({len(ilceler)} ilçe)", "INFO")

        for kat in target_categories:
            kat_path = CATEGORIES[kat]

            if args.sadece_il or len(ilceler) == 0:
                targets = [(0, c_name, c_slug)]
            else:
                targets = [(item["county_id"], item["county_name"], f"{c_slug}-{item['county_slug']}") for item in ilceler]

            for county_id, county_name, slug in targets:
                seen_ids_in_county = set()

                for page in range(1, args.max_sayfa + 1):
                    url = f"https://www.emlakjet.com/{kat_path}/{slug}/" if page == 1 else f"https://www.emlakjet.com/{kat_path}/{slug}/{page}/"
                    html, final_url = session.fetch_page(url)

                    if not html:
                        break

                    if page > 1 and final_url and not final_url.rstrip("/").endswith(f"/{page}"):
                        break

                    listings = parse_listings_from_html(html, kat, int(cid), county_id, default_city=c_name, default_county=county_name)
                    if not listings:
                        break

                    new_items = [item for item in listings if item["ilan_id"] not in seen_ids_in_county]
                    if not new_items:
                        break

                    for item in new_items:
                        seen_ids_in_county.add(item["ilan_id"])

                    # ARSA DETAY ZENGİNLEŞTİRME (Ada, Parsel, İmar, Tapu, Pin)
                    if kat == "arsa" and args.arsa_detay:
                        enriched_items = []
                        with ThreadPoolExecutor(max_workers=5) as executor:
                            future_to_item = {executor.submit(enrich_land_listing, item, session): item for item in new_items}
                            for future in as_completed(future_to_item):
                                try:
                                    enriched = future.result()
                                    enriched_items.append(enriched)
                                except Exception:
                                    enriched_items.append(future_to_item[future])
                        new_items = enriched_items

                    saved_count = save_listings(conn, new_items)
                    toplam_toplanan += saved_count

                    if len(listings) < 30:
                        break

                if seen_ids_in_county:
                    detail_note = " + Ada/Parsel/Pin Detaylı" if (kat == "arsa" and args.arsa_detay) else ""
                    log(f"  └─ {county_name} ({kat}): {len(seen_ids_in_county)} ilan toplandı{detail_note}", "SUCCESS")

    log("=" * 65, "INFO")
    log(f"🎉 Tarama Tamamlandı! Toplam Yeni İlan: {toplam_toplanan:,}", "SUCCESS")
    log("=" * 65, "INFO")

    export_to_csv(conn, CSV_DIR)
    conn.close()

if __name__ == "__main__":
    main()
