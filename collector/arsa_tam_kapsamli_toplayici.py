#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Tam Kapsamlı Arsa & Tarla Mahalle İstihbarat Toplayıcısı
====================================================================
Türkiye'nin 81 ili ve 967 ilçesindeki tüm mahalleler için:
  1. Mahalle Düzeyi Arsa & Tarla Güncel Fiyat Özeti & Endeksi (IndexSale)
  2. Mahalle Düzeyi 80 Aylık Tarihsel Fiyat & Endeks Trendi (2021-2027)
  3. Arsa Büyüklük Segmentleri (AreaSegment: 1-1.000m², 1.001-10.000m² vb.)
  4. Mikro Lokasyon Koordinat Fiyat Isı Haritası (HeatMapSales)
verilerini çeker, SQLite veritabanına kaydeder ve CSV olarak dışa aktarır.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

# --- DİZİNLER VE YOLLAR ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_OUT_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "arsa_piyasa_istihbarati.db"
REHBER_PATH = BASE_DIR / "turkiye_il_ilce_rehberi.json"
STATE_PATH = BASE_DIR / "state_arsa_toplayici.json"

API_EJ_TREND = "https://www.emlakjet.com/api/endeksa/dynamictrend"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
]

CATEGORIES = [
    {"kategori": "arsa", "property_category": 3, "property_type": 1, "tanim": "İmarlı Satılık Arsa"},
    {"kategori": "tarla", "property_category": 3, "property_type": 2, "tanim": "Tarla / Tarım Arazisi"}
]


def log(msg: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    symbols = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌", "STEP": "🚀"}
    prefix = symbols.get(level, "•")
    print(f"[{now_str}] {prefix} {msg}", flush=True)


class RobustHttpClient:
    def __init__(self, timeout: int = 15):
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.timeout = timeout

    def get_json(self, url: str) -> Optional[Dict[str, Any]]:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.emlakjet.com/",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        for attempt in range(3):
            try:
                r = self.session.get(url, headers=headers, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 404:
                    return None
                elif r.status_code == 429:
                    time.sleep(2.0 + attempt * 2)
                else:
                    time.sleep(0.5)
            except Exception:
                time.sleep(1.0)
        return None


def get_db_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS arsa_mahalle_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        property_type INTEGER NOT NULL,
        city_id INTEGER NOT NULL,
        county_id INTEGER NOT NULL,
        district_id INTEGER NOT NULL,
        il_adi TEXT,
        ilce_adi TEXT,
        mahalle_adi TEXT,
        slug TEXT,
        donem TEXT,
        satilik_m2_fiyat REAL,
        min_m2_fiyat REAL,
        max_m2_fiyat REAL,
        ortalama_fiyat REAL,
        ortalama_m2 REAL,
        fiyat_endeksi REAL,
        aylik_fiyat_degisim REAL,
        yillik_fiyat_degisim REAL,
        ilan_sayisi INTEGER,
        ilanda_kalma_suresi_gun INTEGER,
        stok_degisim_orani REAL,
        yillik_stok_degisim REAL,
        guncellenme_tarihi TEXT,
        UNIQUE(kategori, city_id, county_id, district_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS arsa_mahalle_alan_segmentleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        city_id INTEGER NOT NULL,
        county_id INTEGER NOT NULL,
        district_id INTEGER NOT NULL,
        bolge_adi TEXT,
        segment_adi TEXT NOT NULL,
        satilik_m2_fiyat REAL,
        min_m2_fiyat REAL,
        max_m2_fiyat REAL,
        ortalama_fiyat REAL,
        ortalama_m2 REAL,
        ilan_sayisi INTEGER,
        ilan_orani REAL,
        ilanda_kalma_suresi INTEGER,
        guncellenme_tarihi TEXT,
        UNIQUE(kategori, city_id, county_id, district_id, segment_adi)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS arsa_fiyat_isi_haritasi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        city_id INTEGER NOT NULL,
        county_id INTEGER NOT NULL,
        district_id INTEGER NOT NULL,
        bolge_adi TEXT,
        lat REAL,
        lon REAL,
        m2_fiyat REAL,
        guncellenme_tarihi TEXT,
        UNIQUE(kategori, city_id, county_id, district_id, lat, lon)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS arsa_mahalle_trend (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        property_type INTEGER NOT NULL,
        city_id INTEGER NOT NULL,
        county_id INTEGER NOT NULL,
        district_id INTEGER NOT NULL,
        il_adi TEXT,
        ilce_adi TEXT,
        mahalle_adi TEXT,
        ay TEXT NOT NULL,
        satilik_m2_fiyat REAL,
        min_m2_fiyat REAL,
        max_m2_fiyat REAL,
        ortalama_fiyat REAL,
        ortalama_m2 REAL,
        fiyat_endeksi REAL,
        ilan_sayisi INTEGER,
        aylik_fiyat_degisim REAL,
        yillik_fiyat_degisim REAL,
        ilanda_kalma_suresi_gun INTEGER,
        projeksiyon INTEGER DEFAULT 0,
        guncellenme_tarihi TEXT,
        UNIQUE(kategori, city_id, county_id, district_id, ay)
    );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_ozet_loc ON arsa_mahalle_ozet(city_id, county_id, district_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trend_loc ON arsa_mahalle_trend(district_id, ay);")
    conn.commit()
    conn.close()


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"completed_ilce_keys": [], "completed_trend_districts": []}


def save_state(state: Dict[str, Any]) -> None:
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp_path.replace(STATE_PATH)


def process_ilce_static(
    client: RobustHttpClient,
    city_id: int,
    city_name: str,
    county_id: int,
    county_name: str,
    cat_conf: Dict[str, Any]
) -> Tuple[int, int, int]:
    """
    İlçe düzeyinde Static=true çağrısı yaparak ilçeye bağlı tüm mahallelerin:
    - Güncel arsa/tarla özetlerini
    - Alan büyüklük segmentlerini
    - Isı haritası koordinatlarını
    tek seferde kaydeder.
    """
    kat = cat_conf["kategori"]
    ptype = cat_conf["property_type"]
    pcat = cat_conf["property_category"]

    url = (
        f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3"
        f"&Level=2&CityId={city_id}&CountyId={county_id}&PropertyCategory={pcat}"
        f"&PropertyType={ptype}&Rooms=5&Static=true&Trend=false&Types=true&Wkt="
    )

    resp = client.get_json(url)
    if not resp:
        return (0, 0, 0)

    now_iso = datetime.now().isoformat()
    conn = get_db_connection()
    cur = conn.cursor()

    ozet_count = 0
    segment_count = 0
    heatmap_count = 0

    # 1. Mahalle Özetleri (Static Listesi)
    static_list = resp.get("Static", [])
    if isinstance(static_list, list):
        for m in static_list:
            dist_id = m.get("DistrictId")
            dist_name = m.get("DistrictName") or m.get("DisplayName", "")
            if not dist_id:
                continue

            year = m.get("PropertyYear")
            month = m.get("PropertyMonth")
            donem = f"{year}-{str(month).zfill(2)}" if (year and month) else "2026-08"

            cur.execute("""
            INSERT OR REPLACE INTO arsa_mahalle_ozet (
                kategori, property_type, city_id, county_id, district_id,
                il_adi, ilce_adi, mahalle_adi, slug, donem,
                satilik_m2_fiyat, min_m2_fiyat, max_m2_fiyat,
                ortalama_fiyat, ortalama_m2, fiyat_endeksi,
                aylik_fiyat_degisim, yillik_fiyat_degisim,
                ilan_sayisi, ilanda_kalma_suresi_gun,
                stok_degisim_orani, yillik_stok_degisim, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kat, ptype, city_id, county_id, dist_id,
                city_name, county_name, dist_name, m.get("Slug"), donem,
                m.get("UnitPriceForSale"), m.get("MinUnitPriceForSale"), m.get("MaxUnitPriceForSale"),
                m.get("PriceForSale"), m.get("ComparableAreaForSale"), m.get("IndexSale"),
                m.get("PriceChangeSale"), m.get("UnitPriceSaleAnnualChange"),
                m.get("CountForSale") or 0, m.get("ListingPeriodForSale"),
                m.get("StockRatioSale"), m.get("AnnualStockChangeSale"), now_iso
            ))
            ozet_count += 1

    # 2. Alan Büyüklük Segmentleri (AreaSegment)
    area_segments = resp.get("AreaSegment", [])
    if isinstance(area_segments, list):
        for s in area_segments:
            seg_name = s.get("ListingType")
            if not seg_name:
                continue
            dist_id = s.get("DistrictId") or 0
            bolge = f"{city_name} - {county_name}"

            cur.execute("""
            INSERT OR REPLACE INTO arsa_mahalle_alan_segmentleri (
                kategori, city_id, county_id, district_id, bolge_adi, segment_adi,
                satilik_m2_fiyat, min_m2_fiyat, max_m2_fiyat, ortalama_fiyat, ortalama_m2,
                ilan_sayisi, ilan_orani, ilanda_kalma_suresi, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kat, city_id, county_id, dist_id, bolge, seg_name,
                s.get("UnitPriceForSale"), s.get("MinUnitPriceForSale"), s.get("MaxUnitPriceForSale"),
                s.get("PriceForSale"), s.get("ComparableAreaForSale"),
                s.get("CountForSale") or 0, s.get("CountForSaleRatio"),
                s.get("ListingPeriodForSale"), now_iso
            ))
            segment_count += 1

    # 3. Isı Haritası Koordinatları (HeatMapSales)
    heat_points = resp.get("HeatMapSales", [])
    if isinstance(heat_points, list):
        for h in heat_points:
            lat = h.get("Lat")
            lon = h.get("Long")
            price = h.get("Price")
            if lat and lon and price:
                dist_id = h.get("DistrictId") or 0
                bolge = f"{city_name} - {county_name}"
                try:
                    cur.execute("""
                    INSERT OR IGNORE INTO arsa_fiyat_isi_haritasi (
                        kategori, city_id, county_id, district_id, bolge_adi,
                        lat, lon, m2_fiyat, guncellenme_tarihi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        kat, city_id, county_id, dist_id, bolge,
                        float(lat), float(lon), float(price), now_iso
                    ))
                    heatmap_count += 1
                except ValueError:
                    pass

    conn.commit()
    conn.close()
    return (ozet_count, segment_count, heatmap_count)


def process_mahalle_trend(
    client: RobustHttpClient,
    city_id: int,
    city_name: str,
    county_id: int,
    county_name: str,
    district_id: int,
    district_name: str,
    cat_conf: Dict[str, Any]
) -> int:
    """Tek bir mahallenin 80 aylık tarihsel arsa trend serisini çeker."""
    kat = cat_conf["kategori"]
    ptype = cat_conf["property_type"]
    pcat = cat_conf["property_category"]

    url = (
        f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3"
        f"&Level=3&CityId={city_id}&CountyId={county_id}&DistrictId={district_id}"
        f"&PropertyCategory={pcat}&PropertyType={ptype}&Rooms=5&Static=false&Trend=true&Types=true&Wkt="
    )

    resp = client.get_json(url)
    if not resp:
        return 0

    trend_list = resp.get("Trend", [])
    if not trend_list or not isinstance(trend_list, list):
        return 0

    now_iso = datetime.now().isoformat()
    conn = get_db_connection()
    cur = conn.cursor()
    saved = 0

    for t in trend_list:
        p_date = t.get("PropertyDate") or ""
        ay = p_date[:7] if len(p_date) >= 7 else f"{t.get('PropertyYear')}-{str(t.get('PropertyMonth', 1)).zfill(2)}"
        if not ay or len(ay) != 7:
            continue

        cur.execute("""
        INSERT OR REPLACE INTO arsa_mahalle_trend (
            kategori, property_type, city_id, county_id, district_id,
            il_adi, ilce_adi, mahalle_adi, ay,
            satilik_m2_fiyat, min_m2_fiyat, max_m2_fiyat,
            ortalama_fiyat, ortalama_m2, fiyat_endeksi,
            ilan_sayisi, aylik_fiyat_degisim, yillik_fiyat_degisim,
            ilanda_kalma_suresi_gun, projeksiyon, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            kat, ptype, city_id, county_id, district_id,
            city_name, county_name, district_name, ay,
            t.get("UnitPriceForSale"), t.get("MinUnitPriceForSale"), t.get("MaxUnitPriceForSale"),
            t.get("PriceForSale"), t.get("ComparableAreaForSale"), t.get("IndexSale"),
            t.get("CountForSale") or 0, t.get("PriceChangeSale"), t.get("UnitPriceSaleAnnualChange"),
            t.get("ListingPeriodForSale"), 1 if ay > "2026-08" else 0, now_iso
        ))
        saved += 1

    conn.commit()
    conn.close()
    return saved


def export_to_csv() -> None:
    CSV_OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection()

    tables = [
        ("arsa_mahalle_ozet", "03_arsa_tarla_mahalle_fiyat_ozet.csv"),
        ("arsa_mahalle_trend", "04_arsa_tarla_mahalle_80_aylik_trend.csv"),
        ("arsa_mahalle_alan_segmentleri", "05_arsa_mahalle_alan_segmentleri.csv"),
        ("arsa_fiyat_isi_haritasi", "08_arsa_koordinat_fiyat_isi_haritasi.csv")
    ]

    log("Veritabanı tabloları CSV formatına aktarılıyor...", "STEP")
    for tbl_name, csv_name in tables:
        target_csv = CSV_OUT_DIR / csv_name
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {tbl_name}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

        with open(target_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(cols)
            w.writerows(rows)
        log(f"  ✓ {csv_name}: {len(rows):,} satır dışa aktarıldı.", "SUCCESS")

    conn.close()


def parse_iller(iller_str: Optional[str]) -> Optional[set[int]]:
    if not iller_str or iller_str.strip().lower() in {"hepsi", "all", ""}:
        return None
    res = set()
    for x in iller_str.split(","):
        x = x.strip()
        if x.isdigit():
            res.add(int(x))
    return res if res else None


def merge_from_directory(import_dir: Path) -> None:
    """40 ayrı runnerdan gelen tüm tar.gz / sqlite / db dosyalarını tek ana veritabanında birleştirir."""
    import shutil
    import tarfile

    import_dir = import_dir.resolve()
    if not import_dir.exists():
        log(f"Hata: Birleştirilecek dizin bulunamadı: {import_dir}", "ERROR")
        return

    init_db()
    master_conn = get_db_connection()
    master_cur = master_conn.cursor()

    log(f"Dağıtık runner çıktıları taranıyor: {import_dir}", "STEP")

    # 1. Varsa tüm .tar.gz arşivlerini aç
    tar_files = list(import_dir.rglob("*.tar.gz"))
    for tf in tar_files:
        try:
            with tarfile.open(tf, "r:gz") as tar:
                extract_path = tf.parent / tf.stem.replace(".tar", "")
                extract_path.mkdir(exist_ok=True)
                tar.extractall(path=extract_path)
        except Exception as e:
            log(f"Arşiv açma hatası ({tf.name}): {e}", "WARN")

    # 2. Tüm SQLite veritabanlarını bul
    db_candidates = list(import_dir.rglob("*.sqlite")) + list(import_dir.rglob("*.db"))
    # Ana DB'yi ele
    dbs_to_merge = [p for p in db_candidates if p.resolve() != DB_PATH.resolve()]

    log(f"Toplam {len(dbs_to_merge)} veritabanı parçası bulundu. Birleştirme başlıyor...", "INFO")

    merged_counts = {"ozet": 0, "segment": 0, "isi": 0, "trend": 0}

    for db_path in dbs_to_merge:
        try:
            src_conn = sqlite3.connect(str(db_path))
            src_cur = src_conn.cursor()

            # arsa_mahalle_ozet
            try:
                src_cur.execute("SELECT * FROM arsa_mahalle_ozet")
                rows = src_cur.fetchall()
                if rows:
                    cols = [d[0] for d in src_cur.description if d[0] != "id"]
                    placeholders = ", ".join(["?"] * len(cols))
                    col_names = ", ".join(cols)
                    col_indices = [i for i, d in enumerate(src_cur.description) if d[0] != "id"]
                    insert_rows = [[r[i] for i in col_indices] for r in rows]
                    master_cur.executemany(
                        f"INSERT OR REPLACE INTO arsa_mahalle_ozet ({col_names}) VALUES ({placeholders})",
                        insert_rows
                    )
                    merged_counts["ozet"] += len(rows)
            except Exception:
                pass

            # arsa_mahalle_alan_segmentleri
            try:
                src_cur.execute("SELECT * FROM arsa_mahalle_alan_segmentleri")
                rows = src_cur.fetchall()
                if rows:
                    cols = [d[0] for d in src_cur.description if d[0] != "id"]
                    placeholders = ", ".join(["?"] * len(cols))
                    col_names = ", ".join(cols)
                    col_indices = [i for i, d in enumerate(src_cur.description) if d[0] != "id"]
                    insert_rows = [[r[i] for i in col_indices] for r in rows]
                    master_cur.executemany(
                        f"INSERT OR REPLACE INTO arsa_mahalle_alan_segmentleri ({col_names}) VALUES ({placeholders})",
                        insert_rows
                    )
                    merged_counts["segment"] += len(rows)
            except Exception:
                pass

            # arsa_fiyat_isi_haritasi
            try:
                src_cur.execute("SELECT * FROM arsa_fiyat_isi_haritasi")
                rows = src_cur.fetchall()
                if rows:
                    cols = [d[0] for d in src_cur.description if d[0] != "id"]
                    placeholders = ", ".join(["?"] * len(cols))
                    col_names = ", ".join(cols)
                    col_indices = [i for i, d in enumerate(src_cur.description) if d[0] != "id"]
                    insert_rows = [[r[i] for i in col_indices] for r in rows]
                    master_cur.executemany(
                        f"INSERT OR IGNORE INTO arsa_fiyat_isi_haritasi ({col_names}) VALUES ({placeholders})",
                        insert_rows
                    )
                    merged_counts["isi"] += len(rows)
            except Exception:
                pass

            # arsa_mahalle_trend
            try:
                src_cur.execute("SELECT * FROM arsa_mahalle_trend")
                rows = src_cur.fetchall()
                if rows:
                    cols = [d[0] for d in src_cur.description if d[0] != "id"]
                    placeholders = ", ".join(["?"] * len(cols))
                    col_names = ", ".join(cols)
                    col_indices = [i for i, d in enumerate(src_cur.description) if d[0] != "id"]
                    insert_rows = [[r[i] for i in col_indices] for r in rows]
                    master_cur.executemany(
                        f"INSERT OR REPLACE INTO arsa_mahalle_trend ({col_names}) VALUES ({placeholders})",
                        insert_rows
                    )
                    merged_counts["trend"] += len(rows)
            except Exception:
                pass

            src_conn.close()
            master_conn.commit()
            log(f"  ✓ Birleştirildi: {db_path.name}", "INFO")

        except Exception as e:
            log(f"Veritabanı okuma hatası ({db_path.name}): {e}", "WARN")

    master_conn.commit()
    master_conn.close()

    log(
        f"BİRLEŞTİRME TAMAMLANDI! Toplam Aktarılan: "
        f"Özet: {merged_counts['ozet']:,} | Segment: {merged_counts['segment']:,} | "
        f"Isı: {merged_counts['isi']:,} | Trend: {merged_counts['trend']:,}",
        "SUCCESS"
    )
    export_to_csv()


def run_stage_1(workers: int = 4, allowed_cities: Optional[set[int]] = None) -> None:
    """Aşama 1: Tüm ilçeler taranarak tüm mahallelerin özet, segment ve ısı haritası çekilir."""
    if not REHBER_PATH.exists():
        log(f"Hata: Rehber dosyası bulunamadı: {REHBER_PATH}", "ERROR")
        return

    with open(REHBER_PATH, "r", encoding="utf-8") as f:
        rehber = json.load(f)

    state = load_state()
    tasks = []

    for city_key, c_val in rehber.items():
        city_id = c_val["city_id"]
        if allowed_cities and city_id not in allowed_cities:
            continue

        city_name = c_val["city_name"]
        for ilce in c_val.get("ilceler", []):
            county_id = ilce["county_id"]
            county_name = ilce["county_name"]
            for cat_conf in CATEGORIES:
                task_key = f"{city_id}_{county_id}_{cat_conf['kategori']}"
                if task_key not in state.get("completed_ilce_keys", []):
                    tasks.append((city_id, city_name, county_id, county_name, cat_conf, task_key))

    total_tasks = len(tasks)
    city_filter_info = f" (Filtre: {len(allowed_cities)} il)" if allowed_cities else ""
    log(f"AŞAMA 1 BAŞLIYOR: Toplam {total_tasks} ilçe/kategori görevi yürütülecek{city_filter_info} (İş parçacığı: {workers}).", "STEP")

    if total_tasks == 0:
        log("Aşama 1 zaten tamamlanmış, tüm ilçe ve mahalle özetleri güncel.", "SUCCESS")
        return

    client = RobustHttpClient()
    completed_count = 0
    total_ozet = 0
    total_seg = 0
    total_heat = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(process_ilce_static, client, t[0], t[1], t[2], t[3], t[4]): t
            for t in tasks
        }

        for future in as_completed(future_map):
            task_info = future_map[future]
            task_key = task_info[5]
            city_name = task_info[1]
            county_name = task_info[3]
            kat = task_info[4]["kategori"]

            try:
                ozet_c, seg_c, heat_c = future.result()
                total_ozet += ozet_c
                total_seg += seg_c
                total_heat += heat_c
                completed_count += 1

                state["completed_ilce_keys"].append(task_key)
                if completed_count % 10 == 0:
                    save_state(state)

                if ozet_c > 0:
                    log(f"[{completed_count}/{total_tasks}] {city_name} - {county_name} ({kat}): {ozet_c} mahalle, {seg_c} segment, {heat_c} nokta.", "INFO")
                else:
                    log(f"[{completed_count}/{total_tasks}] {city_name} - {county_name} ({kat}): Veri yok.", "WARN")

            except Exception as e:
                log(f"Hata ({city_name} - {county_name}): {e}", "ERROR")

    save_state(state)
    log(f"AŞAMA 1 TAMAMLANDI! Toplam: {total_ozet:,} mahalle arsa/tarla özeti, {total_seg:,} alan segmenti, {total_heat:,} ısı noktası kaydedildi.", "SUCCESS")
    export_to_csv()


def run_stage_2(workers: int = 4, only_active: bool = True, allowed_cities: Optional[set[int]] = None) -> None:
    """Aşama 2: Mahalle bazında 80 aylık tarihsel trend serisini çeker."""
    conn = get_db_connection()
    cur = conn.cursor()

    query = """
    SELECT DISTINCT kategori, property_type, city_id, county_id, district_id, il_adi, ilce_adi, mahalle_adi, ilan_sayisi
    FROM arsa_mahalle_ozet
    WHERE 1=1
    """
    params = []
    if only_active:
        query += " AND ilan_sayisi > 0"
    if allowed_cities:
        placeholders = ", ".join(["?"] * len(allowed_cities))
        query += f" AND city_id IN ({placeholders})"
        params.extend(list(allowed_cities))

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    state = load_state()
    tasks = []

    for r in rows:
        kat, ptype, cid, coid, did, cname, coname, dname, isayisi = r
        trend_key = f"{cid}_{coid}_{did}_{kat}"
        if trend_key not in state.get("completed_trend_districts", []):
            cat_conf = {"kategori": kat, "property_type": ptype, "property_category": 3}
            tasks.append((cid, cname, coid, coname, did, dname, cat_conf, trend_key))

    total_tasks = len(tasks)
    city_filter_info = f" (Filtre: {len(allowed_cities)} il)" if allowed_cities else ""
    log(f"AŞAMA 2 BAŞLIYOR: Toplam {total_tasks} mahallenin 80 aylık trend serisi çekilecek{city_filter_info} (Workers: {workers}).", "STEP")

    if total_tasks == 0:
        log("Aşama 2 zaten tamamlanmış, hedeflenen mahalle trendleri mevcut.", "SUCCESS")
        return

    client = RobustHttpClient()
    completed_count = 0
    total_months = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(process_mahalle_trend, client, t[0], t[1], t[2], t[3], t[4], t[5], t[6]): t
            for t in tasks
        }

        for future in as_completed(future_map):
            task_info = future_map[future]
            trend_key = task_info[7]
            m_name = task_info[5]
            kat = task_info[6]["kategori"]

            try:
                m_count = future.result()
                total_months += m_count
                completed_count += 1

                state["completed_trend_districts"].append(trend_key)
                if completed_count % 25 == 0:
                    save_state(state)
                    log(f"Trend İlerleme: [{completed_count}/{total_tasks}] mahalle tamamlandı. Toplam {total_months:,} ay serisi.", "INFO")

            except Exception as e:
                log(f"Hata (Mahalle: {m_name}): {e}", "ERROR")

    save_state(state)
    log(f"AŞAMA 2 TAMAMLANDI! Toplam: {total_months:,} aylık trend satırı kaydedildi.", "SUCCESS")
    export_to_csv()


def main():
    parser = argparse.ArgumentParser(description="Tam Kapsamlı Mahalle Arsa & Tarla İstihbarat Toplayıcısı")
    parser.add_argument("--asama", type=str, choices=["1", "2", "hepsi", "export", "birlestir"], default="1",
                        help="Çalıştırma modu: 1 (Hızlı Mahalle Özeti & Segmentler), 2 (80 Aylık Trend), hepsi, export, birlestir")
    parser.add_argument("--iller", type=str, default=None,
                        help="İl Plaka Kodları (virgülle ayrılmış, örn: '1,2' veya '34' veya 'hepsi')")
    parser.add_argument("--birlestir-dizin", type=str, default=None,
                        help="40 runnerdan indirilen artifakt klasörünü birleştirir (örn: 'tum_gruplar')")
    parser.add_argument("--workers", type=int, default=4, help="Eşzamanlı iş parçacığı sayısı (varsayılan: 4)")
    parser.add_argument("--all-mahalle", action="store_true", help="İlanı olmayan mahallelerin de trendini çek (Aşama 2 için)")
    args = parser.parse_args()

    if args.birlestir_dizin or args.asama == "birlestir":
        dizin = args.birlestir_dizin or "tum_gruplar"
        merge_from_directory(Path(dizin))
        return

    init_db()
    allowed_cities = parse_iller(args.iller)

    if args.asama == "1":
        run_stage_1(workers=args.workers, allowed_cities=allowed_cities)
    elif args.asama == "2":
        run_stage_2(workers=args.workers, only_active=not args.all_mahalle, allowed_cities=allowed_cities)
    elif args.asama == "hepsi":
        run_stage_1(workers=args.workers, allowed_cities=allowed_cities)
        run_stage_2(workers=args.workers, only_active=not args.all_mahalle, allowed_cities=allowed_cities)
    elif args.asama == "export":
        export_to_csv()


if __name__ == "__main__":
    main()

