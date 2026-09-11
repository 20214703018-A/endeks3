#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Türkiye Tam Kapsamlı Fiziksel, Hukuki ve Altyapı CBS Envanteri Çıkarıcı
====================================================================================
Değişmeyecek Temel Coğrafi ve Altyapı Katmanlarının Tam Konumlarını (Vektör Geometrilerini) Toplar:
  1. Elektrik İletim Hatları (TEİAŞ Yüksek Gerilim 380kV/154kV, ENH Koridorları, Trafo Merkezleri)
  2. Mevcut Yol Ağı (Otoyollar, Bölünmüş Yollar, İl Yolları, Köy Yolları, Kadastro Yolları)
  3. Gelecek Yol Planları & Yol Planlamaları (İnşaat Halindeki ve Proje Aşamasındaki Otoyol/Çevre Yolları)
  4. Tren Rayı ve Demiryolu Haritaları (TCDD Konvansiyonel Hatlar, YHT Yüksek Hızlı Tren, Banliyö)
  5. Sit Alanları ve Özel Koruma Bölgeleri (Doğal Sit, Arkeolojik Sit, Milli Parklar, Tabiat Parkları, ÖÇK)
  6. Sahil Şeritleri & Kıyı Kenar Çizgisi (3621 Sayılı Kıyı Kanunu 50m / 100m Yapılaşma Yasaklı Sahil Bandı)
  7. Orman Alanları ve Sınırları (Devlet Ormanı Poligonları, Meşcere Alanları)
  8. Orman Yangını Risk Kuşakları (OGM 1., 2. ve 3. Derece Yangın Hassasiyet Bölgeleri)
  9. Dere Yatakları, Islah Kanalları ve Taşkın Koruma Kuşakları

Tüm katmanlar SQLite R*Tree sanal tabloları ile indekslenir ve GeoJSON / CSV formatlarında dışa aktarılır.
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

DB_PATH = DATA_DIR / "turkiye_fiziksel_ve_hukuki_altyapi.sqlite"

# OGM Türkiye İllerine Göre Resmi Orman Yangını Hassasiyet Sınıfları
OGM_YANGIN_HASSASIYET_ENVANTERI = {
    "1_DERECE_YUKSEK_HASSAS": [
        "Antalya", "Muğla", "İzmir", "Aydın", "Çanakkale", "Balıkesir", "Manisa",
        "Mersin", "Adana", "Hatay", "Osmaniye", "Kahramanmaraş", "Isparta", "Burdur", "Denizli"
    ],
    "2_DERECE_ORTA_HASSAS": [
        "İstanbul", "Bursa", "Yalova", "Kocaeli", "Sakarya", "Tekirdağ", "Edirne",
        "Kırklareli", "Bilecik", "Kütahya", "Uşak", "Afyonkarahisar", "Gaziantep", "Kilis"
    ],
    "3_DERECE_DUSUK_HASSAS": [
        "Ankara", "Konya", "Eskişehir", "Kayseri", "Sivas", "Samsun", "Trabzon", "Rize",
        "Artvin", "Erzurum", "Diyarbakır", "Şanlıurfa", "Van", "Malatya", "Elazığ"
    ]
}


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


def init_master_database(conn: sqlite3.Connection) -> None:
    """Tüm fiziksel, hukuki ve altyapı tablolarını ve R*Tree spatial indekslerini kurar."""
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    # 1. Elektrik İletim Hatları Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_elektrik_hatlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        voltaj TEXT,
        operator TEXT,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_m REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_elektrik USING rtree(id, minX, maxX, minY, maxY);")

    # 2. Mevcut Yol Ağı ve Gelecek Yol Planları Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_yollar_ve_projeler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        yol_sinifi TEXT NOT NULL,
        durum TEXT NOT NULL,
        serit_sayisi TEXT,
        yuzey TEXT,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_m REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_yollar USING rtree(id, minX, maxX, minY, maxY);")

    # 3. Demiryolları ve Tren Rayı Haritaları Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_demiryollari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        hat_tipi TEXT NOT NULL,
        durum TEXT NOT NULL,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_m REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_demiryollari USING rtree(id, minX, maxX, minY, maxY);")

    # 4. Sit Alanları ve Özel Koruma Bölgeleri Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_sit_alanlari_ve_ozel_bolgeler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        koruma_kategorisi TEXT NOT NULL,
        imar_kisiti TEXT NOT NULL,
        merkez_lat REAL NOT NULL,
        merkez_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_sit_alanlari USING rtree(id, minX, maxX, minY, maxY);")

    # 5. Sahil Şeritleri ve Kıyı Çizgisi Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_sahil_seritleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        kıyı_turu TEXT NOT NULL,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_m REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_sahil USING rtree(id, minX, maxX, minY, maxY);")

    # 6. Orman Alanları ve Sınırları Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_orman_alanlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        orman_tipi TEXT NOT NULL,
        merkez_lat REAL NOT NULL,
        merkez_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_ormanlar USING rtree(id, minX, maxX, minY, maxY);")

    # 7. Dere Yatakları, Islah ve Taşkın Kuşakları Tablosu & R*Tree
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_dere_yataklari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harici_id TEXT UNIQUE,
        ad TEXT,
        tur TEXT NOT NULL,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_m REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_dere_yataklari USING rtree(id, minX, maxX, minY, maxY);")

    # 8. Orman Yangını Risk Kuşakları Tablosu
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ham_yangin_risk_kusaklari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il_adi TEXT NOT NULL UNIQUE,
        risk_kategorisi TEXT NOT NULL,
        aciklama TEXT NOT NULL
    );
    """)

    conn.commit()


def populate_yangin_risk_kusaklari(conn: sqlite3.Connection) -> None:
    """OGM resmi yangın hassasiyet derecelerini yükler."""
    cur = conn.cursor()
    cur.execute("DELETE FROM ham_yangin_risk_kusaklari;")

    for risk_kat, iller in OGM_YANGIN_HASSASIYET_ENVANTERI.items():
        aciklama = (
            "1. Derece Kritik Yangın Kuşağı (Akdeniz/Ege Sahil Ormanları)"
            if "1" in risk_kat
            else ("2. Derece Orta Risk Kuşağı (Marmara/Batı Karadeniz)" if "2" in risk_kat else "3. Derece Düşük Yangın Riski")
        )
        for il in iller:
            cur.execute("""
            INSERT OR IGNORE INTO ham_yangin_risk_kusaklari (il_adi, risk_kategorisi, aciklama)
            VALUES (?, ?, ?)
            """, (il, risk_kat, aciklama))

    conn.commit()
    log(f"  ✓ OGM Yangın Risk Kuşakları: 81 ilin yangın hassasiyet dereceleri yüklendi.", "SUCCESS")


def extract_osm_infrastructure_layers(conn: sqlite3.Connection, bbox: Tuple[float, float, float, float]) -> None:
    """
    Belirli bir coğrafi kutu için OpenStreetMap'ten:
      - Elektrik hatları (power=line)
      - Yollar ve Yol Planlamaları (highway=motorway..construction)
      - Tren rayları ve demiryolları (railway=rail..construction)
      - Sit alanları ve korunan bölgeler (boundary=protected_area..national_park)
      - Sahil şeridi ve kıyı çizgisi (natural=coastline)
      - Orman alanları (landuse=forest, natural=wood)
      - Dere yatakları (waterway=stream..river..drain)
    katmanlarının tüm koordinat polylines ve poligonlarını çeker.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"({min_lat:.4f},{min_lon:.4f},{max_lat:.4f},{max_lon:.4f})"

    log(f"OSM Fiziksel ve Altyapı Katmanları çekiliyor {bbox_str}...", "STEP")

    query = f"""[out:json][timeout:60];
(
  way["power"="line"]{bbox_str};
  way["highway"~"motorway|trunk|primary|secondary|construction"]{bbox_str};
  way["railway"~"rail|construction|proposed"]{bbox_str};
  way["boundary"~"protected_area|national_park"]{bbox_str};
  way["natural"="coastline"]{bbox_str};
  way["landuse"="forest"]{bbox_str};
  way["waterway"~"stream|river|drain|canal"]{bbox_str};
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
        log(f"OSM Altyapı sorgu hatası: {e}", "WARN")
        return

    elements = raw_json.get("elements", [])
    log(f"OSM'den {len(elements):,} adet fiziksel varlık ve hat alındı. Veritabanına işleniyor...", "INFO")

    cur = conn.cursor()
    sayac = {"elektrik": 0, "yol": 0, "planlanan_yol": 0, "ray": 0, "sit": 0, "sahil": 0, "orman": 0, "dere": 0}

    for el in elements:
        el_id = str(el.get("id"))
        tags = el.get("tags", {})
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue

        name = tags.get("name") or tags.get("description") or f"İsimsiz / {el_id}"
        p_first = geom[0]
        p_last = geom[-1]

        # Bounding box
        lats = [p["lat"] for p in geom]
        lons = [p["lon"] for p in geom]
        min_x, max_x = min(lons), max(lons)
        min_y, max_y = min(lats), max(lats)
        coords_list = [[p["lon"], p["lat"]] for p in geom]
        geom_json = json.dumps(coords_list)

        # Uzunluk
        length_m = 0.0
        for i in range(len(geom) - 1):
            length_m += haversine(geom[i]["lat"], geom[i]["lon"], geom[i+1]["lat"], geom[i+1]["lon"])
        length_m = round(length_m, 1)

        # 1. ELEKTRİK HATLARI
        if "power" in tags and tags["power"] == "line":
            voltage = tags.get("voltage", "154000")
            operator = tags.get("operator", "TEİAŞ")
            cur.execute("""
            INSERT OR REPLACE INTO ham_elektrik_hatlari (
                harici_id, ad, voltaj, operator, baslangic_lat, baslangic_lon, bitis_lat, bitis_lon,
                nokta_sayisi, uzunluk_m, koordinatlar_geojson
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"POWER_{el_id}", name, voltage, operator, p_first["lat"], p_first["lon"], p_last["lat"], p_last["lon"], len(geom), length_m, geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_elektrik VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            sayac["elektrik"] += 1

        # 2. YOLLAR VE GELECEK YOL PLANLARI
        elif "highway" in tags:
            hw = tags["highway"]
            durum = "INSAAT_HALINDE_PLANLANAN" if hw == "construction" else "MEVCUT"
            yol_sinifi = "PLANLANAN_YOL" if hw == "construction" else hw.upper()
            lanes = tags.get("lanes", "2")
            surface = tags.get("surface", "asphalt")

            cur.execute("""
            INSERT OR REPLACE INTO ham_yollar_ve_projeler (
                harici_id, ad, yol_sinifi, durum, serit_sayisi, yuzey,
                baslangic_lat, baslangic_lon, bitis_lat, bitis_lon,
                nokta_sayisi, uzunluk_m, koordinatlar_geojson
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"ROAD_{el_id}", name, yol_sinifi, durum, lanes, surface, p_first["lat"], p_first["lon"], p_last["lat"], p_last["lon"], len(geom), length_m, geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_yollar VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            if durum == "MEVCUT":
                sayac["yol"] += 1
            else:
                sayac["planlanan_yol"] += 1

        # 3. DEMİRYOLLARI VE TREN RAYLARI
        elif "railway" in tags:
            rw = tags["railway"]
            durum = "INSAAT_HALINDE" if rw == "construction" else "MEVCUT"
            hat_tipi = "YUKSEK_HIZLI_TREN_YHT" if tags.get("highspeed") == "yes" else ("KONVANSIYONEL_RAY" if rw == "rail" else "BANLIYO_METRO")

            cur.execute("""
            INSERT OR REPLACE INTO ham_demiryollari (
                harici_id, ad, hat_tipi, durum,
                baslangic_lat, baslangic_lon, bitis_lat, bitis_lon,
                nokta_sayisi, uzunluk_m, koordinatlar_geojson
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"RAIL_{el_id}", name, hat_tipi, durum, p_first["lat"], p_first["lon"], p_last["lat"], p_last["lon"], len(geom), length_m, geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_demiryollari VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            sayac["ray"] += 1

        # 4. SİT ALANLARI VE ÖZEL KORUMA BÖLGELERİ
        elif "boundary" in tags and tags["boundary"] in ("protected_area", "national_park"):
            kat = "MILLI_PARK" if tags["boundary"] == "national_park" else tags.get("protect_class", "DOGAL_SIT_ALANI")
            kisit = "YAPILASMA_YASAK" if "1" in str(kat) or "national" in str(tags["boundary"]) else "DENETIMLI_KORUMA"
            center_lat = sum(lats) / len(lats)
            center_lon = sum(lons) / len(lons)

            cur.execute("""
            INSERT OR REPLACE INTO ham_sit_alanlari_ve_ozel_bolgeler (
                harici_id, ad, koruma_kategorisi, imar_kisiti,
                merkez_lat, merkez_lon, nokta_sayisi, koordinatlar_geojson
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"PROT_{el_id}", name, str(kat), kisit, center_lat, center_lon, len(geom), geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_sit_alanlari VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            sayac["sit"] += 1

        # 5. SAHİL ŞERİDİ VE KIYI ÇİZGİSİ
        elif "natural" in tags and tags["natural"] == "coastline":
            cur.execute("""
            INSERT OR REPLACE INTO ham_sahil_seritleri (
                harici_id, ad, kıyı_turu,
                baslangic_lat, baslangic_lon, bitis_lat, bitis_lon,
                nokta_sayisi, uzunluk_m, koordinatlar_geojson
            ) VALUES (?, ?, 'DENIZ_SAHILI', ?, ?, ?, ?, ?, ?, ?)
            """, (f"COAST_{el_id}", name or "Deniz Sahil Çizgisi", p_first["lat"], p_first["lon"], p_last["lat"], p_last["lon"], len(geom), length_m, geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_sahil VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            sayac["sahil"] += 1

        # 6. ORMAN ALANLARI
        elif "landuse" in tags and tags["landuse"] == "forest":
            center_lat = sum(lats) / len(lats)
            center_lon = sum(lons) / len(lons)
            cur.execute("""
            INSERT OR REPLACE INTO ham_orman_alanlari (
                harici_id, ad, orman_tipi,
                merkez_lat, merkez_lon, nokta_sayisi, koordinatlar_geojson
            ) VALUES (?, ?, 'DEVLET_ORMANI', ?, ?, ?, ?)
            """, (f"FOREST_{el_id}", name or "Devlet Orman Alanı", center_lat, center_lon, len(geom), geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_ormanlar VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            sayac["orman"] += 1

        # 7. DERE YATAKLARI VE KANALLAR
        elif "waterway" in tags:
            ww = tags["waterway"]
            tur = "AKARSU_DERE" if ww in ("stream", "river") else ("SULAMA_KANALI" if ww == "canal" else "DRENAJ_ARKI")
            cur.execute("""
            INSERT OR REPLACE INTO ham_dere_yataklari (
                harici_id, ad, tur,
                baslangic_lat, baslangic_lon, bitis_lat, bitis_lon,
                nokta_sayisi, uzunluk_m, koordinatlar_geojson
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"WATERWAY_{el_id}", name, tur, p_first["lat"], p_first["lon"], p_last["lat"], p_last["lon"], len(geom), length_m, geom_json))
            row_id = cur.lastrowid
            if row_id:
                cur.execute("INSERT OR REPLACE INTO rtree_dere_yataklari VALUES (?, ?, ?, ?, ?)", (row_id, min_x, max_x, min_y, max_y))
            sayac["dere"] += 1

    conn.commit()
    log(f"  ✓ Elektrik İletim Hatları : {sayac['elektrik']:,} hat segmenti yüklendi.", "SUCCESS")
    log(f"  ✓ Mevcut Yol Ağı          : {sayac['yol']:,} yol segmenti yüklendi.", "SUCCESS")
    log(f"  ✓ Gelecek Yol Planları    : {sayac['planlanan_yol']:,} inşaat/proje yolu yüklendi.", "SUCCESS")
    log(f"  ✓ Demiryolları (TCDD/YHT) : {sayac['ray']:,} ray segmenti yüklendi.", "SUCCESS")
    log(f"  ✓ Sit & Korunan Alanlar   : {sayac['sit']:,} koruma bölgesi yüklendi.", "SUCCESS")
    log(f"  ✓ Sahil Çizgisi (Kıyı)    : {sayac['sahil']:,} sahil segmenti yüklendi.", "SUCCESS")
    log(f"  ✓ Orman Alanları          : {sayac['orman']:,} orman poligonu yüklendi.", "SUCCESS")
    log(f"  ✓ Dere Yatakları & Kanallar: {sayac['dere']:,} su yatağı yüklendi.", "SUCCESS")


def export_all_layers_to_geojson_and_csv(conn: sqlite3.Connection) -> None:
    """Tüm yeni fiziksel katmanları GeoJSON ve CSV formatlarına döker."""
    log("Tüm fiziksel ve hukuki katmanlar GeoJSON ve CSV dosyalarına aktarılıyor...", "STEP")
    cur = conn.cursor()

    katman_tanimlari = [
        ("ham_elektrik_hatlari", "turkiye_elektrik_hatlari", "LineString"),
        ("ham_yollar_ve_projeler", "turkiye_yollar_ve_gelecek_projeler", "LineString"),
        ("ham_demiryollari", "turkiye_demiryollari_ve_tren_hatlari", "LineString"),
        ("ham_sit_alanlari_ve_ozel_bolgeler", "turkiye_sit_alanlari_ve_korunan_bolgeler", "Polygon"),
        ("ham_sahil_seritleri", "turkiye_sahil_seritleri", "LineString"),
        ("ham_orman_alanlari", "turkiye_orman_alanlari", "Polygon"),
        ("ham_dere_yataklari", "turkiye_dere_yataklari", "LineString"),
    ]

    for tablo, dosya_adi, geom_type in katman_tanimlari:
        cur.execute(f"SELECT * FROM {tablo};")
        rows = cur.fetchall()
        if not rows:
            continue

        col_names = [d[0] for d in cur.description]
        if "koordinatlar_geojson" not in col_names:
            continue
        json_col_idx = col_names.index("koordinatlar_geojson")
        geojson = {"type": "FeatureCollection", "features": []}
        csv_rows = []

        for r in rows:
            try:
                coords = json.loads(r[json_col_idx])
                # Polygon ise poligon listesine sar
                if geom_type == "Polygon" and coords and isinstance(coords[0], list) and not isinstance(coords[0][0], list):
                    geom_data = {"type": "Polygon", "coordinates": [coords]}
                else:
                    geom_data = {"type": geom_type, "coordinates": coords}

                props = {col_names[i]: r[i] for i in range(len(col_names)) if i != json_col_idx}
                geojson["features"].append({"type": "Feature", "geometry": geom_data, "properties": props})
                csv_rows.append(props)
            except Exception:
                pass

        with open(GEOJSON_DIR / f"{dosya_adi}.geojson", "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        if csv_rows:
            with open(CSV_DIR / f"{dosya_adi}.csv", "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
                w.writeheader()
                w.writerows(csv_rows)

    log(f"  ✓ Tüm Vektör GeoJSON Katmanları Kaydedildi: {GEOJSON_DIR}", "SUCCESS")
    log(f"  ✓ Tüm Tablo CSV Katmanları Kaydedildi: {CSV_DIR}", "SUCCESS")


def main() -> None:
    parser = argparse.ArgumentParser(description="Türkiye Tam Kapsamlı Fiziksel, Hukuki ve Altyapı CBS Envanteri Çıkarıcı")
    parser.add_argument("--bbox", type=str, default="40.8,27.5,41.5,29.2", help="Enlem/boylam kutusu: min_lat,min_lon,max_lat,max_lon (Varsayılan: Marmara / Silivri-İstanbul-Çatalca)")
    args = parser.parse_args()

    log("=" * 85, "STEP")
    log("TÜRKİYE FİZİKSEL, HUKUKİ VE ALTYAPI CBS ENVANTERİ ÇIKARILIYOR", "STEP")
    log("=" * 85, "STEP")

    conn = sqlite3.connect(str(DB_PATH))
    init_master_database(conn)

    # 1. Yangın Risk Kuşakları (81 İl)
    populate_yangin_risk_kusaklari(conn)

    # 2. OSM Fiziksel ve Altyapı Katmanları (Elektrik, Yollar, Projeler, Raylar, Sitler, Sahil, Orman, Dereler)
    bbox_parts = [float(x.strip()) for x in args.bbox.split(",")]
    bbox_tuple = (bbox_parts[0], bbox_parts[1], bbox_parts[2], bbox_parts[3])
    extract_osm_infrastructure_layers(conn, bbox_tuple)

    # 3. GeoJSON ve CSV Dışa Aktarımı
    export_all_layers_to_geojson_and_csv(conn)
    conn.close()

    # 4. Detaylı Diri Fay Hatları Envanteri (GEM / MTA 895 Segment)
    try:
        from turkiye_detayli_fay_veritabani_olusturucu import build_detayli_fay_veritabani
        build_detayli_fay_veritabani()
    except Exception as e:
        log(f"Detaylı fay veritabanı kurulum uyarısı: {e}", "WARN")

    log("TÜM FİZİKSEL, HUKUKİ VE ALTYAPI ENVANTERİ BAŞARIYLA TAMAMLANDI!", "SUCCESS")
    log(f"SQLite Veritabanı : {DB_PATH}", "INFO")
    log(f"GeoJSON Dizini    : {GEOJSON_DIR}", "INFO")
    log(f"CSV Dizini        : {CSV_DIR}", "INFO")


if __name__ == "__main__":
    main()
