#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Türkiye Su Altyapısı, Kuyular ve Doğal Varlıklar Ham Envanter Çıkarıcı
==================================================================================
Bu script, Türkiye genelindeki su hatları, kuyular, pınarlar, depolar, kanallar
ve aktif fay hatlarının NET COĞRAFİ KONUMLARINI (Enlem, Boylam, Rakım, Çizgisel Geometri)
ham veri olarak toplar ve SQLite / GeoJSON / CSV formatlarında arşivler.

Toplanan Ham Varlık Katmanları:
  1. Su Hatları & Sulama Kanalları (LineString geometrileri, koordinat zincirleri, boru hatları)
  2. Su Kuyuları & Artezyenler (Point koordinatları, derinlik/kot, kuyu adı/numarası)
  3. Doğal Pınarlar, Menbalar, Çeşmeler & Hayratlar (Point koordinatları, kot)
  4. Su Depoları, Kuleleri & Arıtma Tesisleri (Point koordinatları, kapasite/tür)
  5. Akarsular, Dereler, Kuru Dere Yatakları & Göller (Point/Line koordinatları)
  6. MTA Diri Fay Hatları (Segment başlangıç/bitiş koordinatları ve fay tipi)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
GEOJSON_DIR = DATA_DIR / "geojson"
CSV_DIR = DATA_DIR / "csv_envanter"

DATA_DIR.mkdir(parents=True, exist_ok=True)
GEOJSON_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "turkiye_su_ve_altyapi_envanteri.sqlite"
GEONAMES_TR_URL = "http://download.geonames.org/export/dump/TR.zip"

# MTA Diri Fay Segmentleri Ham Koordinatları
MTA_DIRI_FAYLARI_HAM = [
    {"ad": "KAF - Marmara Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.750, "lon1": 29.850, "lat2": 40.850, "lon2": 27.500, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - İzmit / Sapanca Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.720, "lon1": 29.900, "lat2": 40.700, "lon2": 30.400, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Bolu / Gerede Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.730, "lon1": 31.500, "lat2": 40.800, "lon2": 32.300, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Çerkeş / Ilgaz Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.820, "lon1": 32.900, "lat2": 41.000, "lon2": 34.000, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Tosya / Kargı Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 41.020, "lon1": 34.000, "lat2": 41.100, "lon2": 34.800, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Merzifon / Erbaa Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.850, "lon1": 35.500, "lat2": 40.700, "lon2": 36.600, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Niksar / Reşadiye Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.600, "lon1": 36.950, "lat2": 40.380, "lon2": 37.500, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Suşehri / Erzincan Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 40.150, "lon1": 38.100, "lat2": 39.750, "lon2": 39.500, "tip": "Doğrultu Atımlı"},
    {"ad": "KAF - Karlıova Düğüm Segmenti", "sistem": "Kuzey Anadolu Fay Sistemi", "lat1": 39.300, "lon1": 41.000, "lat2": 39.350, "lon2": 41.150, "tip": "Doğrultu Atımlı"},
    {"ad": "DAF - Maraş / Türkoğlu Segmenti", "sistem": "Doğu Anadolu Fay Sistemi", "lat1": 37.380, "lon1": 36.850, "lat2": 37.600, "lon2": 37.100, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "DAF - Pazarcık / Erkenek Segmenti", "sistem": "Doğu Anadolu Fay Sistemi", "lat1": 37.500, "lon1": 37.300, "lat2": 37.950, "lon2": 38.000, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "DAF - Doğanyol / Pütürge Segmenti", "sistem": "Doğu Anadolu Fay Sistemi", "lat1": 38.150, "lon1": 38.500, "lat2": 38.450, "lon2": 39.100, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "DAF - Sivrice / Elazığ Segmenti", "sistem": "Doğu Anadolu Fay Sistemi", "lat1": 38.450, "lon1": 39.100, "lat2": 38.650, "lon2": 39.750, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "DAF - Palu / Bingöl Segmenti", "sistem": "Doğu Anadolu Fay Sistemi", "lat1": 38.700, "lon1": 39.950, "lat2": 39.050, "lon2": 40.800, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "DAF - Hazar / Karlıova Segmenti", "sistem": "Doğu Anadolu Fay Sistemi", "lat1": 39.100, "lon1": 40.800, "lat2": 39.300, "lon2": 41.000, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "BAF - Gediz Graben Fayı", "sistem": "Batı Anadolu Fay Sistemi", "lat1": 38.550, "lon1": 27.400, "lat2": 38.480, "lon2": 28.200, "tip": "Normal Fay"},
    {"ad": "BAF - Büyük Menderes Graben Fayı", "sistem": "Batı Anadolu Fay Sistemi", "lat1": 37.850, "lon1": 27.800, "lat2": 37.920, "lon2": 28.500, "tip": "Normal Fay"},
    {"ad": "BAF - Küçük Menderes Fayı", "sistem": "Batı Anadolu Fay Sistemi", "lat1": 38.150, "lon1": 27.350, "lat2": 38.180, "lon2": 27.800, "tip": "Normal Fay"},
    {"ad": "BAF - İzmir Fayı", "sistem": "Batı Anadolu Fay Sistemi", "lat1": 38.380, "lon1": 27.000, "lat2": 38.450, "lon2": 27.250, "tip": "Doğrultu / Normal"},
    {"ad": "BAF - Bakırçay Graben Fayı", "sistem": "Batı Anadolu Fay Sistemi", "lat1": 39.100, "lon1": 27.150, "lat2": 39.200, "lon2": 27.600, "tip": "Normal Fay"},
    {"ad": "Tuz Gölü Fayı", "sistem": "İç Anadolu Fay Kuşağı", "lat1": 38.300, "lon1": 33.950, "lat2": 39.150, "lon2": 33.300, "tip": "Eğim Atımlı Normal"},
    {"ad": "Ecemiş Fayı", "sistem": "İç Anadolu / Akdeniz Fayı", "lat1": 38.000, "lon1": 35.000, "lat2": 37.400, "lon2": 35.100, "tip": "Sol Yanal Doğrultu Atımlı"},
    {"ad": "Ölü Deniz Fayı - Hatay Segmenti", "sistem": "Ölü Deniz Fay Sistemi", "lat1": 35.850, "lon1": 36.150, "lat2": 36.500, "lon2": 36.250, "tip": "Sol Yanal Doğrultu Atımlı"}
]


def log(msg: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    symbols = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌", "STEP": "🚀"}
    prefix = symbols.get(level, "•")
    print(f"[{now_str}] {prefix} {msg}", flush=True)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def init_envanter_db(conn: sqlite3.Connection) -> None:
    """Ham varlık tablolarını hazırlar."""
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    # 1. Su Hatları & Sulama Kanalları (Çizgisel / LineString)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_su_hatlari_ve_kanallar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT,
        ad TEXT NOT NULL,
        tur TEXT NOT NULL,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_m REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL,
        kaynak TEXT NOT NULL
    );
    """)

    # 2. Su Kuyuları & Artezyenler (Noktasal)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_su_kuyulari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT NOT NULL,
        tur TEXT NOT NULL,
        enlem REAL NOT NULL,
        boylam REAL NOT NULL,
        rakim_m INTEGER,
        il_plaka TEXT,
        kaynak TEXT NOT NULL
    );
    """)

    # 3. Doğal Pınarlar, Menbalar, Çeşmeler (Noktasal)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_pinarlar_ve_cesmeler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT NOT NULL,
        tur TEXT NOT NULL,
        enlem REAL NOT NULL,
        boylam REAL NOT NULL,
        rakim_m INTEGER,
        il_plaka TEXT,
        kaynak TEXT NOT NULL
    );
    """)

    # 4. Su Depoları & Tesisler (Noktasal)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_su_depolari_ve_tesisler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT NOT NULL,
        tur TEXT NOT NULL,
        enlem REAL NOT NULL,
        boylam REAL NOT NULL,
        rakim_m INTEGER,
        il_plaka TEXT,
        kaynak TEXT NOT NULL
    );
    """)

    # 5. Su Yolları, Dereler, Barajlar & Göller
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_su_yollari_ve_dereler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT NOT NULL,
        tur TEXT NOT NULL,
        enlem REAL NOT NULL,
        boylam REAL NOT NULL,
        rakim_m INTEGER,
        il_plaka TEXT,
        kaynak TEXT NOT NULL
    );
    """)

    # 6. Diri Fay Hatları (Segmentler)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_diri_fay_hatlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ad TEXT NOT NULL,
        sistem TEXT NOT NULL,
        tip TEXT NOT NULL,
        lat1 REAL NOT NULL,
        lon1 REAL NOT NULL,
        lat2 REAL NOT NULL,
        lon2 REAL NOT NULL,
        uzunluk_km REAL NOT NULL
    );
    """)

    conn.commit()


def extract_geonames_water_features(conn: sqlite3.Connection) -> None:
    """GeoNames TR resmi envanterinden tüm kuyu, pınar, depo, akarsu ve göllerin ham koordinatlarını toplar."""
    log("GeoNames TR hidrolojik ve su varlıkları dump'ı indiriliyor...", "STEP")
    req = urllib.request.Request(GEONAMES_TR_URL, headers={"User-Agent": "curl/8.7.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            zip_bytes = resp.read()
    except Exception as e:
        log(f"GeoNames indirme hatası: {e}", "ERROR")
        return

    log(f"İndirildi ({len(zip_bytes) / 1024 / 1024:.2f} MB). Ayrıştırma başlıyor...", "INFO")
    cur = conn.cursor()

    kuyu_count = 0
    pinar_count = 0
    depo_count = 0
    su_yolu_count = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        with z.open("TR.txt") as f:
            for line in f:
                parts = line.decode("utf-8", errors="ignore").split("\t")
                if len(parts) < 17:
                    continue

                gid = str(parts[0])
                name = parts[1]
                lat = float(parts[4])
                lon = float(parts[5])
                fc = parts[6]
                fcode = parts[7]
                il_plaka = parts[10]
                try:
                    rakim = int(parts[16]) if parts[16] else None
                except ValueError:
                    rakim = None

                code = f"{fc}.{fcode}"

                # A) KUYULAR
                if code in ("H.WLL", "H.WLLQ"):
                    cur.execute("""
                    INSERT OR IGNORE INTO ham_su_kuyulari (harici_id, ad, tur, enlem, boylam, rakim_m, il_plaka, kaynak)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'GEONAMES')
                    """, (f"GN_{gid}", name, "SU_KUYUSU", lat, lon, rakim, il_plaka))
                    kuyu_count += 1

                # B) DOĞAL PINARLAR
                elif code in ("H.SPNG", "H.SPNT"):
                    cur.execute("""
                    INSERT OR IGNORE INTO ham_pinarlar_ve_cesmeler (harici_id, ad, tur, enlem, boylam, rakim_m, il_plaka, kaynak)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'GEONAMES')
                    """, (f"GN_{gid}", name, "DOGAL_PINAR", lat, lon, rakim, il_plaka))
                    pinar_count += 1

                # C) SU DEPOLARI VE KULELERİ
                elif code in ("H.RSVT", "H.RSV", "H.WTRW"):
                    cur.execute("""
                    INSERT OR IGNORE INTO ham_su_depolari_ve_tesisler (harici_id, ad, tur, enlem, boylam, rakim_m, il_plaka, kaynak)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'GEONAMES')
                    """, (f"GN_{gid}", name, "SU_DEPOSU", lat, lon, rakim, il_plaka))
                    depo_count += 1

                # D) SU YOLLARI, DERELER, KURU DERELER, GÖLLER
                elif code in ("H.STM", "H.STMI", "H.LK", "H.PND", "H.CNL", "H.DTCHI"):
                    tur_adi = "AKARSU" if code == "H.STM" else ("KURU_DERE" if code == "H.STMI" else ("GOL_BARAJ" if code in ("H.LK", "H.PND") else "SULAMA_KANALI"))
                    cur.execute("""
                    INSERT OR IGNORE INTO ham_su_yollari_ve_dereler (harici_id, ad, tur, enlem, boylam, rakim_m, il_plaka, kaynak)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'GEONAMES')
                    """, (f"GN_{gid}", name, tur_adi, lat, lon, rakim, il_plaka))
                    su_yolu_count += 1

    conn.commit()
    log(f"  ✓ GeoNames Kuyular: {kuyu_count:,} adet", "SUCCESS")
    log(f"  ✓ GeoNames Pınarlar: {pinar_count:,} adet", "SUCCESS")
    log(f"  ✓ GeoNames Depolar: {depo_count:,} adet", "SUCCESS")
    log(f"  ✓ GeoNames Su Yolları & Göller: {su_yolu_count:,} adet", "SUCCESS")


def extract_osm_water_pipelines_and_canals(conn: sqlite3.Connection, bbox: Optional[Tuple[float, float, float, float]] = None) -> None:
    """
    OpenStreetMap'ten su hatları (pipeline=water) ve sulama kanallarının (waterway=canal)
    tüm polylines / linestring geometrilerini ham koordinat dizisi olarak çeker.
    """
    log("OpenStreetMap su isale hatları ve sulama kanalları geometrileri sorgulanıyor...", "STEP")

    if bbox:
        min_lat, min_lon, max_lat, max_lon = bbox
        bbox_str = f"({min_lat:.4f},{min_lon:.4f},{max_lat:.4f},{max_lon:.4f})"
        query = f"""[out:json][timeout:60];
(
  way["pipeline"="water"]{bbox_str};
  way["man_made"="pipeline"]["substance"="water"]{bbox_str};
  way["waterway"="canal"]{bbox_str};
  way["waterway"="drain"]{bbox_str};
  node["man_made"="water_well"]{bbox_str};
  node["amenity"="drinking_water"]{bbox_str};
);
out geom;
"""
    else:
        # Tüm Türkiye
        query = """[out:json][timeout:180];
area["ISO3166-1"="TR"][admin_level=2]->.turkey;
(
  way["pipeline"="water"](area.turkey);
  way["man_made"="pipeline"]["substance"="water"](area.turkey);
  way["waterway"="canal"](area.turkey);
);
out geom;
"""

    headers = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw_json = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"OSM Overpass sorgu hatası: {e}. (Ağ/zaman aşımı)", "WARN")
        return

    elements = raw_json.get("elements", [])
    log(f"OSM'den {len(elements):,} adet su altyapı elemanı alındı. Veritabanına işleniyor...", "INFO")

    cur = conn.cursor()
    hat_sayisi = 0
    kuyu_sayisi = 0
    cesme_sayisi = 0

    for el in elements:
        el_id = str(el.get("id"))
        tags = el.get("tags", {})
        el_type = el.get("type")
        name = tags.get("name") or tags.get("description") or f"İsimsiz Hat / {el_id}"

        # Çizgisel Su Hatları ve Kanallar (Ways)
        if el_type == "way":
            geom = el.get("geometry", [])
            if len(geom) < 2:
                continue

            # Çizgi geometrisini hesapla
            p_first = geom[0]
            p_last = geom[-1]

            # Toplam hat uzunluğunu hesapla
            toplam_uzunluk = 0.0
            coords_list = []
            for i in range(len(geom)):
                coords_list.append([geom[i]["lon"], geom[i]["lat"]])
                if i > 0:
                    toplam_uzunluk += haversine(geom[i-1]["lat"], geom[i-1]["lon"], geom[i]["lat"], geom[i]["lon"])

            pipeline_tag = tags.get("pipeline") or tags.get("man_made")
            waterway_tag = tags.get("waterway")

            if pipeline_tag:
                tur = "ISALE_BORU_HATTI"
            elif waterway_tag == "canal":
                tur = "SULAMA_KANALI"
            elif waterway_tag == "drain":
                tur = "DRENAJ_ARKI"
            else:
                tur = "SU_HATTI"

            cur.execute("""
            INSERT OR REPLACE INTO ham_su_hatlari_ve_kanallar (
                harici_id, ad, tur, baslangic_lat, baslangic_lon, bitis_lat, bitis_lon,
                nokta_sayisi, uzunluk_m, koordinatlar_geojson, kaynak
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OSM')
            """, (
                f"OSM_WAY_{el_id}", name, tur,
                p_first["lat"], p_first["lon"],
                p_last["lat"], p_last["lon"],
                len(geom), round(toplam_uzunluk, 1),
                json.dumps(coords_list),
            ))
            hat_sayisi += 1

        # Noktasal Kuyular ve Çeşmeler (Nodes)
        elif el_type == "node":
            lat = el.get("lat")
            lon = el.get("lon")
            if not lat or not lon:
                continue

            if tags.get("man_made") == "water_well":
                cur.execute("""
                INSERT OR IGNORE INTO ham_su_kuyulari (harici_id, ad, tur, enlem, boylam, kaynak)
                VALUES (?, ?, 'SU_KUYUSU', ?, ?, 'OSM')
                """, (f"OSM_NODE_{el_id}", name, lat, lon))
                kuyu_sayisi += 1
            elif tags.get("amenity") == "drinking_water":
                cur.execute("""
                INSERT OR IGNORE INTO ham_pinarlar_ve_cesmeler (harici_id, ad, tur, enlem, boylam, kaynak)
                VALUES (?, ?, 'CESME_HAYRAT', ?, ?, 'OSM')
                """, (f"OSM_NODE_{el_id}", name, lat, lon))
                cesme_sayisi += 1

    conn.commit()
    log(f"  ✓ OSM Su Boru Hatları & Kanallar: {hat_sayisi:,} hat geometrisi yüklendi.", "SUCCESS")
    if kuyu_sayisi:
        log(f"  ✓ OSM Ek Kuyular: {kuyu_sayisi:,} adet", "SUCCESS")
    if cesme_sayisi:
        log(f"  ✓ OSM Ek Çeşmeler: {cesme_sayisi:,} adet", "SUCCESS")


def populate_fault_lines_geometry(conn: sqlite3.Connection) -> None:
    """MTA Diri Fay Hatlarının ham segment koordinatlarını yükler."""
    cur = conn.cursor()
    cur.execute("DELETE FROM ham_diri_fay_hatlari;")

    for f in MTA_DIRI_FAYLARI_HAM:
        uzunluk_km = haversine(f["lat1"], f["lon1"], f["lat2"], f["lon2"]) / 1000.0
        cur.execute("""
        INSERT INTO ham_diri_fay_hatlari (ad, sistem, tip, lat1, lon1, lat2, lon2, uzunluk_km)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (f["ad"], f["sistem"], f["tip"], f["lat1"], f["lon1"], f["lat2"], f["lon2"], round(uzunluk_km, 1)))

    conn.commit()
    log(f"  ✓ MTA Diri Fay Hatları: {len(MTA_DIRI_FAYLARI_HAM)} ana segment geometrisi yüklendi.", "SUCCESS")


def export_to_geojson_and_csv(conn: sqlite3.Connection) -> None:
    """Veritabanındaki tüm ham varlıkları haritacılık (GeoJSON) ve tablo (CSV) formatlarına döker."""
    log("Ham varlık envanteri GeoJSON ve CSV dosyalarına dışa aktarılıyor...", "STEP")
    cur = conn.cursor()

    # 1. Su Hatları & Kanallar (LineString GeoJSON & CSV)
    cur.execute("SELECT id, harici_id, ad, tur, baslangic_lat, baslangic_lon, bitis_lat, bitis_lon, nokta_sayisi, uzunluk_m, koordinatlar_geojson, kaynak FROM ham_su_hatlari_ve_kanallar;")
    hatlar = cur.fetchall()

    geojson_hatlar = {"type": "FeatureCollection", "features": []}
    csv_hatlar = []

    for r in hatlar:
        coords = json.loads(r[10])
        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "id": r[0], "harici_id": r[1], "ad": r[2], "tur": r[3],
                "uzunluk_m": r[9], "kaynak": r[11]
            }
        }
        geojson_hatlar["features"].append(feature)
        csv_hatlar.append({
            "id": r[0], "harici_id": r[1], "ad": r[2], "tur": r[3],
            "baslangic_lat": r[4], "baslangic_lon": r[5],
            "bitis_lat": r[6], "bitis_lon": r[7],
            "nokta_sayisi": r[8], "uzunluk_m": r[9], "kaynak": r[11]
        })

    with open(GEOJSON_DIR / "turkiye_su_hatlari_ve_kanallar.geojson", "w", encoding="utf-8") as f:
        json.dump(geojson_hatlar, f, ensure_ascii=False, indent=2)

    if csv_hatlar:
        with open(CSV_DIR / "su_hatlari_ve_kanallar.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_hatlar[0].keys()))
            w.writeheader()
            w.writerows(csv_hatlar)

    # 2. Su Kuyuları (Point GeoJSON & CSV)
    cur.execute("SELECT id, harici_id, ad, tur, enlem, boylam, rakim_m, il_plaka, kaynak FROM ham_su_kuyulari;")
    kuyular = cur.fetchall()
    geojson_kuyu = {"type": "FeatureCollection", "features": []}
    csv_kuyu = []

    for r in kuyular:
        geojson_kuyu["features"].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r[5], r[4]]},
            "properties": {"id": r[0], "harici_id": r[1], "ad": r[2], "tur": r[3], "rakim_m": r[6], "il_plaka": r[7], "kaynak": r[8]}
        })
        csv_kuyu.append({
            "id": r[0], "harici_id": r[1], "ad": r[2], "tur": r[3], "enlem": r[4], "boylam": r[5], "rakim_m": r[6], "il_plaka": r[7], "kaynak": r[8]
        })

    with open(GEOJSON_DIR / "turkiye_su_kuyulari.geojson", "w", encoding="utf-8") as f:
        json.dump(geojson_kuyu, f, ensure_ascii=False, indent=2)

    if csv_kuyu:
        with open(CSV_DIR / "su_kuyulari.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_kuyu[0].keys()))
            w.writeheader()
            w.writerows(csv_kuyu)

    # 3. Pınarlar ve Çeşmeler (Point GeoJSON & CSV)
    cur.execute("SELECT id, harici_id, ad, tur, enlem, boylam, rakim_m, il_plaka, kaynak FROM ham_pinarlar_ve_cesmeler;")
    pinarlar = cur.fetchall()
    geojson_pinar = {"type": "FeatureCollection", "features": []}
    csv_pinar = []

    for r in pinarlar:
        geojson_pinar["features"].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r[5], r[4]]},
            "properties": {"id": r[0], "harici_id": r[1], "ad": r[2], "tur": r[3], "rakim_m": r[6], "il_plaka": r[7], "kaynak": r[8]}
        })
        csv_pinar.append({
            "id": r[0], "harici_id": r[1], "ad": r[2], "tur": r[3], "enlem": r[4], "boylam": r[5], "rakim_m": r[6], "il_plaka": r[7], "kaynak": r[8]
        })

    with open(GEOJSON_DIR / "turkiye_dogal_pinarlar_ve_cesmeler.geojson", "w", encoding="utf-8") as f:
        json.dump(geojson_pinar, f, ensure_ascii=False, indent=2)

    if csv_pinar:
        with open(CSV_DIR / "dogal_pinarlar_ve_cesmeler.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_pinar[0].keys()))
            w.writeheader()
            w.writerows(csv_pinar)

    # 4. Diri Fay Hatları (LineString GeoJSON & CSV)
    cur.execute("SELECT id, ad, sistem, tip, lat1, lon1, lat2, lon2, uzunluk_km FROM ham_diri_fay_hatlari;")
    faylar = cur.fetchall()
    geojson_fay = {"type": "FeatureCollection", "features": []}
    csv_fay = []

    for r in faylar:
        geojson_fay["features"].append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[r[5], r[4]], [r[7], r[6]]]},
            "properties": {"id": r[0], "ad": r[1], "sistem": r[2], "tip": r[3], "uzunluk_km": r[8]}
        })
        csv_fay.append({
            "id": r[0], "ad": r[1], "sistem": r[2], "tip": r[3], "lat1": r[4], "lon1": r[5], "lat2": r[6], "lon2": r[7], "uzunluk_km": r[8]
        })

    with open(GEOJSON_DIR / "turkiye_diri_fay_hatlari.geojson", "w", encoding="utf-8") as f:
        json.dump(geojson_fay, f, ensure_ascii=False, indent=2)

    if csv_fay:
        with open(CSV_DIR / "diri_fay_hatlari.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_fay[0].keys()))
            w.writeheader()
            w.writerows(csv_fay)

    log(f"  ✓ GeoJSON Dosyaları Oluşturuldu: {GEOJSON_DIR}", "SUCCESS")
    log(f"  ✓ CSV Dosyaları Oluşturuldu: {CSV_DIR}", "SUCCESS")


def main() -> None:
    parser = argparse.ArgumentParser(description="Türkiye Su Altyapısı, Kuyular ve Fay Hatları Ham Konum Envanteri Çıkarıcı")
    parser.add_argument("--bolge", type=str, default="marmara", choices=["marmara", "ege", "akdeniz", "ic_anadolu", "turkiye"], help="OSM su hatları sorgu kapsamı")
    parser.add_argument("--skip-osm", action="store_true", help="OSM sorgusunu atla, yalnızca GeoNames ve MTA faylarını işle")
    args = parser.parse_args()

    log("=" * 80, "STEP")
    log("TÜRKİYE SU HATLARI, KUYULAR VE DOĞAL VARLIKLAR HAM CBS ENVANTERİ HAZIRLANIYOR", "STEP")
    log("=" * 80, "STEP")

    conn = sqlite3.connect(str(DB_PATH))
    init_envanter_db(conn)

    # 1. GeoNames Türkiye Su Varlıkları (Kuyular, Pınarlar, Depolar, Nehirler)
    extract_geonames_water_features(conn)

    # 2. MTA Diri Fay Hatları Segment Geometrileri
    populate_fault_lines_geometry(conn)

    # 3. OpenStreetMap Su İsale Hatları ve Kanallar (LineString Geometrisi)
    if not args.skip_osm:
        bbox_map = {
            "marmara": (40.0, 26.0, 42.0, 30.5),
            "ege": (36.5, 26.0, 39.5, 29.5),
            "akdeniz": (36.0, 29.5, 37.5, 36.5),
            "ic_anadolu": (37.5, 30.5, 40.5, 36.5),
            "turkiye": None
        }
        bbox = bbox_map.get(args.bolge)
        extract_osm_water_pipelines_and_canals(conn, bbox=bbox)

    # 4. GeoJSON ve CSV Dışa Aktarımı
    export_to_geojson_and_csv(conn)

    conn.close()
    log("TÜM HAM SPATIAL CBS ENVANTERİ BAŞARIYLA TAMAMLANDI!", "SUCCESS")
    log(f"SQLite Dosyası : {DB_PATH}", "INFO")
    log(f"GeoJSON Dizini : {GEOJSON_DIR}", "INFO")
    log(f"CSV Dizini     : {CSV_DIR}", "INFO")


if __name__ == "__main__":
    main()
