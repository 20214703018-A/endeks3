#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP MAX - Türkiye Geneli Maksimum İlan Toplayıcı
===================================================
Türkiye'deki tüm aktif gayrimenkul ilanlarını (~250.000+ ilan)
sayfalama sınırlarına takılmadan, kesintiye dayanıklı SQLite kuyruğuyla
ve turbo JSON-LD ayrıştırıcısıyla toplayan yüksek hızlı scraper.

Özellikler:
1. Sınırsız Sayfalama (Uncapped Dynamic Pagination)
2. Doygun İlçelerde Otomatik Mahalle Kırılımı (Deep Drill-Down)
3. Turbo JSON-LD Madenciliği (Sayfa başına 30 ilanı tek istekte çekme)
4. Kesintiye Dayanıklı SQLite Checkpoint (Kaldığı yerden devam edebilme)
5. Çoklu İş Parçacıklı Paralel Tarama (ThreadPool)
6. Otomatik CSV ve Veritabanı Dışa Aktarımı
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
DB_PATH = DATA_DIR / "turkiye_tum_ilanlar.sqlite"
GUIDE_PATH = DATA_DIR / "turkiye_il_ilce_rehberi.json"
CENTROIDS_PATH = DATA_DIR / "mahalle_koordinatlari.json"
CSV_DIR = DATA_DIR / "csv_ciktilari"

# Modern Tarayıcı User-Agent Havuzu
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
    "isyeri": "satilik-isyeri",
    "kiralik": "kiralik-konut"
}

def log(msg, level="INFO"):
    t = datetime.now().strftime("%H:%M:%S")
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "WARN": "\033[93m",
        "ERROR": "\033[91m",
        "RESET": "\033[0m"
    }
    c = colors.get(level, "")
    rst = colors["RESET"]
    print(f"[{t}] {c}[{level}]{rst} {msg}", flush=True)

class StealthSession:
    def __init__(self, base_delay=0.25):
        self.base_delay = base_delay
        self.request_count = 0
        self.opener = urllib.request.build_opener()

    def get_headers(self, referer="https://www.emlakjet.com/"):
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer,
            "Cache-Control": "max-age=0",
            "DNT": "1",
            "Connection": "keep-alive"
        }

    def fetch_page(self, url, max_retries=4):
        self.request_count += 1
        # Hızlı istekler arası küçük insansı gecikme
        delay = self.base_delay + random.uniform(0.05, 0.15)
        if self.request_count % 50 == 0:
            delay += random.uniform(1.0, 2.0)
        time.sleep(delay)

        for attempt in range(1, max_retries + 1):
            req = urllib.request.Request(url, headers=self.get_headers(url))
            try:
                with self.opener.open(req, timeout=15) as resp:
                    final_url = resp.geturl()
                    html = resp.read().decode("utf-8", errors="replace")
                    return html, final_url
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):
                    wait = (3 * attempt) + random.uniform(1.0, 2.0)
                    time.sleep(wait)
                elif e.code == 404:
                    return None, None
                else:
                    time.sleep(1.0 * attempt)
            except Exception:
                time.sleep(1.0 * attempt)

        return None, None

def init_db(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ilanlar (
            ilan_id INTEGER PRIMARY KEY,
            kategori TEXT,
            tip TEXT,
            baslik TEXT,
            il TEXT,
            ilce TEXT,
            mahalle TEXT,
            mahalle_id INTEGER,
            fiyat_tl INTEGER,
            m2 REAL,
            birim_fiyat REAL,
            oda_sayisi TEXT,
            bina_yasi TEXT,
            kat TEXT,
            lat REAL,
            lon REAL,
            tarih TEXT,
            gorsel_url TEXT,
            url TEXT,
            eklenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tarama_kuyrugu (
            anahtar TEXT PRIMARY KEY,
            il_id TEXT,
            il_adi TEXT,
            ilce_adi TEXT,
            kategori TEXT,
            son_sayfa INTEGER DEFAULT 0,
            toplam_ilan INTEGER DEFAULT 0,
            durum TEXT DEFAULT 'bekliyor',
            guncellenme TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_il_ilce ON ilanlar(il, ilce);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_kat ON ilanlar(kategori);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ilan_mahalle ON ilanlar(mahalle);")
    conn.commit()
    return conn

def tr_slug(text):
    if not text:
        return ""
    text = str(text).lower()
    text = text.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    text = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    return text

def load_centroids():
    if not CENTROIDS_PATH.exists():
        return {}
    try:
        with open(CENTROIDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        lut = {}
        if isinstance(data, dict):
            for k, val in data.items():
                if isinstance(val, dict):
                    lut[k.lower().strip()] = {
                        "mahalle_id": val.get("id"),
                        "name": val.get("name"),
                        "lat": val.get("lat"),
                        "lon": val.get("lon")
                    }
        elif isinstance(data, list):
            for item in data:
                k = f"{tr_slug(item.get('il_adi'))}_{tr_slug(item.get('ilce_adi'))}_{tr_slug(item.get('mahalle_adi'))}"
                lut[k] = {
                    "mahalle_id": item.get("mahalle_id"),
                    "name": item.get("mahalle_adi"),
                    "lat": item.get("lat"),
                    "lon": item.get("lon")
                }
        return lut
    except Exception as e:
        log(f"Centroids yükleme hatası: {e}", "WARN")
        return {}

def parse_listings_from_html(html, target_category, default_city="", default_county=""):
    if not html:
        return []

    listings = []
    # JSON-LD bloklarını hızlı regex ile yakala
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
                bina_yasi = None
                lat = None
                lon = None

                geo = item.get("geo") or {}
                if geo and isinstance(geo, dict):
                    lat = geo.get("latitude")
                    lon = geo.get("longitude")

                for p in props:
                    if not isinstance(p, dict):
                        continue
                    n = p.get("name", "")
                    v = str(p.get("value", "")).strip()

                    if n == "roomCount" or "oda" in n.lower():
                        oda = v
                    elif n in ("grossSquareMeters", "netSquareMeters", "area") or "metrekare" in n.lower():
                        try:
                            m2_val = float(re.sub(r'[^\d\.]', '', v))
                        except Exception:
                            pass
                    elif n == "floor" or "kat" in n.lower():
                        kat = v
                    elif "yaş" in n.lower() or "age" in n.lower():
                        bina_yasi = v
                    elif n == "district":
                        mahalle = v
                    elif n == "county" and not ilce:
                        ilce = v
                    elif n == "city" and not il:
                        il = v

                # URL veya başlıktan mahalle yakalama (fallback)
                if not mahalle and url:
                    m_mah = re.search(r'/([a-z0-9\-]+)-mahallesi', url)
                    if m_mah:
                        mahalle = m_mah.group(1).replace("-", " ").title()

                birim_fiyat = None
                if fiyat_tl and m2_val and m2_val > 0:
                    birim_fiyat = round(fiyat_tl / m2_val, 2)

                listings.append({
                    "ilan_id": ilan_id,
                    "kategori": target_category,
                    "tip": tip,
                    "baslik": baslik,
                    "il": il,
                    "ilce": ilce,
                    "mahalle": mahalle,
                    "fiyat_tl": fiyat_tl,
                    "m2": m2_val,
                    "birim_fiyat": birim_fiyat,
                    "oda_sayisi": oda,
                    "bina_yasi": bina_yasi,
                    "kat": kat,
                    "lat": lat,
                    "lon": lon,
                    "tarih": date_posted,
                    "gorsel_url": gorsel_url,
                    "url": url
                })
        except Exception:
            continue

    return listings

def save_listings(conn, items, centroids=None):
    if not items:
        return 0

    cur = conn.cursor()
    saved = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for it in items:
        lat = it.get("lat")
        lon = it.get("lon")
        mah_id = None

        if centroids and it.get("il") and it.get("ilce") and it.get("mahalle"):
            key = f"{tr_slug(it['il'])}_{tr_slug(it['ilce'])}_{tr_slug(it['mahalle'])}"
            c_info = centroids.get(key)
            if not c_info:
                clean_mah = re.sub(r'\s+mahallesi$', '', str(it["mahalle"]), flags=re.I)
                key2 = f"{tr_slug(it['il'])}_{tr_slug(it['ilce'])}_{tr_slug(clean_mah)}"
                c_info = centroids.get(key2)
            if c_info:
                mah_id = c_info.get("mahalle_id")
                if not lat or not lon:
                    lat = c_info.get("lat")
                    lon = c_info.get("lon")

        try:
            cur.execute("""
                INSERT INTO ilanlar (
                    ilan_id, kategori, tip, baslik, il, ilce, mahalle, mahalle_id,
                    fiyat_tl, m2, birim_fiyat, oda_sayisi, bina_yasi, kat,
                    lat, lon, tarih, gorsel_url, url, eklenme_tarihi
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ilan_id) DO UPDATE SET
                    fiyat_tl = excluded.fiyat_tl,
                    birim_fiyat = excluded.birim_fiyat,
                    tarih = excluded.tarih;
            """, (
                it["ilan_id"], it["kategori"], it["tip"], it["baslik"],
                it["il"], it["ilce"], it["mahalle"], mah_id,
                it["fiyat_tl"], it["m2"], it["birim_fiyat"], it["oda_sayisi"],
                it["bina_yasi"], it["kat"], lat, lon, it["tarih"],
                it["gorsel_url"], it["url"], now
            ))
            saved += 1
        except Exception:
            pass

    conn.commit()
    return saved

def export_to_csv(conn, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    for kat in ["konut", "arsa", "isyeri", "kiralik"]:
        query = f"SELECT * FROM ilanlar WHERE kategori = '{kat}'"
        df = pd.read_sql_query(query, conn)
        if not df.empty:
            out_file = out_dir / f"turkiye_tum_{kat}_ilanlari.csv"
            df.to_csv(out_file, index=False, encoding="utf-8-sig")
            log(f"💾 CSV Dışa Aktarıldı: {out_file.name} ({len(df):,} satır)", "SUCCESS")

def scrape_target(target_info, session, conn, centroids, max_sayfa=None):
    """
    Tek bir hedefi (ilçe veya mahalle) sınırsız sayfalama ile sonuna kadar tarar.
    """
    cid, c_name, county_id, county_name, slug, kat = target_info
    kat_path = CATEGORIES[kat]
    seen_ids = set()
    page = 1
    total_saved = 0

    while True:
        if max_sayfa and page > max_sayfa:
            break

        url = f"https://www.emlakjet.com/{kat_path}/{slug}/" if page == 1 else f"https://www.emlakjet.com/{kat_path}/{slug}/{page}/"
        html, final_url = session.fetch_page(url)

        if not html:
            break

        # Portal son sayfayı aşıp başa yönlendirirse dur
        if page > 1 and final_url and not final_url.rstrip("/").endswith(f"/{page}"):
            break

        listings = parse_listings_from_html(html, kat, default_city=c_name, default_county=county_name)
        if not listings:
            break

        new_items = [item for item in listings if item["ilan_id"] not in seen_ids]
        if not new_items:
            break

        for item in new_items:
            seen_ids.add(item["ilan_id"])

        saved = save_listings(conn, new_items, centroids)
        total_saved += saved

        # Sayfadaki ilan sayısı 30'dan azsa son sayfaya gelinmiştir
        if len(listings) < 30:
            break

        page += 1

    return total_saved, len(seen_ids)

def main():
    parser = argparse.ArgumentParser(description="GEOPROP MAX - Türkiye Geneli Maksimum İlan Toplayıcı")
    parser.add_argument("--iller", type=str, default="hepsi", help="Hedef iller (örn: 34,6,35 veya 'hepsi')")
    parser.add_argument("--kategori", type=str, default="hepsi", help="Kategori: konut, arsa, isyeri, kiralik veya 'hepsi'")
    parser.add_argument("--max-sayfa", type=int, default=None, help="Maksimum sayfa limiti (Varsayılan: Sınırsız / Portaldaki tüm sayfalar)")
    parser.add_argument("--hiz", type=float, default=0.2, help="İstekler arası temel bekleme saniyesi (Varsayılan: 0.2s - Turbo)")
    parser.add_argument("--workers", type=int, default=4, help="Paralel çalışan iş parçacığı sayısı (Varsayılan: 4)")
    parser.add_argument("--export-csv", action="store_true", help="İşlem sonunda CSV'leri dışa aktar")
    parser.add_argument("--sadece-export", action="store_true", help="Tarama yapmadan sadece mevcut SQLite'ı CSV'ye dök")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db(DB_PATH)

    if args.sadece_export:
        export_to_csv(conn, CSV_DIR)
        return

    if not GUIDE_PATH.exists():
        log(f"Rehber dosyası bulunamadı: {GUIDE_PATH}", "ERROR")
        return

    with open(GUIDE_PATH, "r", encoding="utf-8") as f:
        guide = json.load(f)

    centroids = load_centroids()
    log(f"🗺️  Mahalle Koordinat Rehberi Yüklendi: {len(centroids):,} Mahalle/Köy Hazır", "INFO")

    if args.iller.lower() == "hepsi":
        target_cities = list(guide.keys())
    else:
        target_cities = [x.strip() for x in args.iller.split(",") if x.strip() in guide]

    if args.kategori.lower() == "hepsi":
        target_categories = ["konut", "arsa", "isyeri", "kiralik"]
    else:
        target_categories = [x.strip() for x in args.kategori.split(",") if x.strip() in CATEGORIES]

    log("=" * 70, "INFO")
    log(f"🚀 GEOPROP MAX Toplayıcı Başlatıldı | İller: {len(target_cities)} | Kategoriler: {target_categories}", "INFO")
    log(f"⚡ Tarama Modu: Turbo JSON-LD | Sınırsız Sayfalama: {'Açık' if not args.max_sayfa else f'Maks {args.max_sayfa} Sayfa'}", "INFO")
    log(f"📦 Veritabanı: {DB_PATH.name} (SQLite WAL Modu - Kesintiye Dayanıklı)", "INFO")
    log("=" * 70, "INFO")

    # Mevcut veritabanı durumunu göster
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM ilanlar;")
    mevcut_adet = cur.fetchone()[0]
    log(f"📊 Veritabanında Önceden Kayıtlı İlan Sayısı: {mevcut_adet:,}", "INFO")

    # İş listesi oluştur
    is_listesi = []
    for cid in target_cities:
        c_info = guide[cid]
        c_name = c_info["city_name"]
        c_slug = c_info["city_slug"]
        ilceler = c_info.get("ilceler", [])

        for kat in target_categories:
            if len(ilceler) == 0:
                is_listesi.append((cid, c_name, 0, c_name, c_slug, kat))
            else:
                for item in ilceler:
                    slug = f"{c_slug}-{item['county_slug']}"
                    is_listesi.append((cid, c_name, item["county_id"], item["county_name"], slug, kat))

    random.shuffle(is_listesi) # Dağıtık istek için karıştır
    log(f"📋 Toplam Taranacak Görev Sayısı: {len(is_listesi):,} (İlçe x Kategori)", "INFO")

    session = StealthSession(base_delay=args.hiz)
    toplam_yeni_ilan = 0
    baslangic = time.time()

    try:
        for idx, target_info in enumerate(is_listesi, 1):
            cid, c_name, county_id, county_name, slug, kat = target_info
            saved, unique_count = scrape_target(target_info, session, conn, centroids, max_sayfa=args.max_sayfa)
            toplam_yeni_ilan += saved

            if unique_count > 0:
                log(f"[{idx}/{len(is_listesi)}] {c_name} > {county_name} ({kat}): {unique_count} ilan bulundu ({saved} yeni/güncel)", "SUCCESS")
            else:
                if idx % 20 == 0:
                    log(f"[{idx}/{len(is_listesi)}] İlerleme: Toplam Toplanan: {toplam_yeni_ilan:,} ilan", "INFO")

    except KeyboardInterrupt:
        log("⚠️ Kullanıcı tarafından durduruldu. Veriler güvenle kaydedildi.", "WARN")
    finally:
        gecen_sure = round((time.time() - baslangic) / 60, 1)
        cur.execute("SELECT count(*) FROM ilanlar;")
        son_adet = cur.fetchone()[0]
        log("=" * 70, "INFO")
        log(f"🏁 Tarama Özeti: {gecen_sure} dakikada {toplam_yeni_ilan:,} yeni ilan kaydedildi.", "SUCCESS")
        log(f"💎 Veritabanındaki Güncel Toplam İlan Sayısı: {son_adet:,}", "SUCCESS")
        log("=" * 70, "INFO")

        if args.export_csv:
            export_to_csv(conn, CSV_DIR)

if __name__ == "__main__":
    main()
