#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Türkiye 895 Segmentli Detaylı Diri Fay CBS Veritabanı ve Envanteri
==============================================================================
GEM (Global Earthquake Model) & MTA Türkiye Diri Fay Haritası Envanterinden:
Türkiye genelindeki 895 adet bağımsız diri fay segmentinin tüm kırık koordinatlarını,
fay sistemini, kinematik mekanizmasını (doğrultu atım, normal, ters/bindirme),
yıllık kayma hızını (mm/yıl) ve sismik kilitlenme derinliğini (km) ham veri olarak
SQLite R*Tree, GeoJSON ve CSV formatlarında derler ve indeksler.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
import urllib.request
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

LOCAL_GEOJSON_PATH = DATA_DIR / "turkiye_detayli_diri_faylar.geojson"
DB_PATH = DATA_DIR / "turkiye_fiziksel_ve_hukuki_altyapi.sqlite"
GEM_URL = "https://raw.githubusercontent.com/GEMScienceTools/gem-global-active-faults/master/geojson/gem_active_faults.geojson"


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


def min_distance_to_polyline(lat: float, lon: float, coords: List[List[float]]) -> float:
    """Noktanın bir polyline çizgisine (koordinat listesine: [[lon, lat], ...]) olan en kısa mesafesini bulur."""
    min_dist = float("inf")
    cos_lat = math.cos(math.radians(lat))
    x0 = lon * 111320.0 * cos_lat
    y0 = lat * 111320.0

    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i+1]

        x1 = lon1 * 111320.0 * cos_lat
        y1 = lat1 * 111320.0
        x2 = lon2 * 111320.0 * cos_lat
        y2 = lat2 * 111320.0

        dx = x2 - x1
        dy = y2 - y1
        l2 = dx * dx + dy * dy
        if l2 == 0.0:
            d = math.hypot(x0 - x1, y0 - y1)
        else:
            t = max(0.0, min(1.0, ((x0 - x1) * dx + (y0 - y1) * dy) / l2))
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy
            d = math.hypot(x0 - proj_x, y0 - proj_y)

        if d < min_dist:
            min_dist = d

    return min_dist


def tespit_et_fay_sistemi(lat_ort: float, lon_ort: float, slip_type: str) -> str:
    """Fayın coğrafi konumuna ve kayma tipine göre bağlı olduğu ana tektonik sistemi belirler."""
    slip_lower = slip_type.lower()
    
    # 1. Kuzey Anadolu Fay Sistemi (KAF Kuşağı)
    if (40.2 <= lat_ort <= 41.5 and 26.0 <= lon_ort <= 39.5) or (39.5 <= lat_ort <= 40.5 and 38.0 <= lon_ort <= 41.2):
        if "dextral" in slip_lower or "strike" in slip_lower:
            return "Kuzey Anadolu Fay Sistemi (KAF)"
        return "Kuzey Anadolu Fay Zonu İkincil / Çapraz Kırığı"

    # 2. Doğu Anadolu Fay Sistemi (DAF Kuşağı)
    if 36.5 <= lat_ort <= 39.5 and 36.0 <= lon_ort <= 41.5 and lon_ort >= (36.0 + (lat_ort - 36.5) * 1.5):
        if "sinistral" in slip_lower:
            return "Doğu Anadolu Fay Sistemi (DAF)"
        return "Doğu Anadolu Fay Kuşağı Yan Segmenti"

    # 3. Batı Anadolu Açılma Rejimi & Graben Fayları
    if 36.5 <= lat_ort <= 40.2 and 26.0 <= lon_ort <= 30.5:
        if "normal" in slip_lower:
            return "Batı Anadolu Graben Fay Sistemi (BAF)"
        elif "strike" in slip_lower or "dextral" in slip_lower or "sinistral" in slip_lower:
            return "Batı Anadolu Doğrultu Atımlı Makaslama Fayı"
        return "Batı Anadolu Aktif Fay Zonu"

    # 4. İç Anadolu Fay Sistemi
    if 37.5 <= lat_ort <= 40.5 and 30.5 <= lon_ort <= 36.5:
        return "İç Anadolu Fay Kuşağı (Tuz Gölü / Ecemiş / Orta Anadolu)"

    # 5. Güneydoğu Anadolu Bindirme Kuşağı & Ölü Deniz
    if 36.5 <= lat_ort <= 38.5 and 37.0 <= lon_ort <= 44.5:
        return "Güneydoğu Anadolu Bindirme Kuşağı / Bitlis-Zagros"

    if lat_ort < 36.8 and 35.5 <= lon_ort <= 37.0:
        return "Ölü Deniz Fay Sistemi"

    return "Türkiye Bölgesel Diri Fay Segmenti"


def build_detayli_fay_veritabani() -> None:
    """895 detaylı fay segmentini SQLite veritabanına ve R*Tree spatial indeksine yükler."""
    log("=" * 80, "STEP")
    log("TÜRKİYE 895 SEGMENTLİ DETAYLI DİRİ FAY VERİTABANI OLUŞTURULUYOR", "STEP")
    log("=" * 80, "STEP")

    # 1. GeoJSON Dosyasını Temin Et
    if not LOCAL_GEOJSON_PATH.exists():
        log("GEM Global Active Faults GeoJSON indiriliyor...", "STEP")
        req = urllib.request.Request(GEM_URL, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                global_data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log(f"İndirme hatası: {e}", "ERROR")
            return

        turkey_features = []
        for f in global_data.get("features", []):
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            flat = coords if geom.get("type") == "LineString" else [p for sub in coords for p in sub]
            for pt in flat:
                if 35.5 <= pt[1] <= 42.5 and 25.5 <= pt[0] <= 45.0:
                    turkey_features.append(f)
                    break

        out_data = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": turkey_features
        }
        with open(LOCAL_GEOJSON_PATH, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)
        log(f"Türkiye aktif fay GeoJSON kaydedildi: {LOCAL_GEOJSON_PATH}", "SUCCESS")
    else:
        log(f"Yerel GeoJSON dosyası mevcut: {LOCAL_GEOJSON_PATH}", "INFO")

    with open(LOCAL_GEOJSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    log(f"Toplam {len(features):,} detaylı aktif fay segmenti ayrıştırılıyor...", "INFO")

    # 2. SQLite Veritabanı Tablosu ve R*Tree
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS detayli_diri_faylar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        catalog_id TEXT NOT NULL,
        fay_sistemi TEXT NOT NULL,
        slip_type TEXT NOT NULL,
        net_slip_rate TEXT,
        average_dip TEXT,
        average_rake TEXT,
        lower_seis_depth TEXT,
        baslangic_lat REAL NOT NULL,
        baslangic_lon REAL NOT NULL,
        bitis_lat REAL NOT NULL,
        bitis_lon REAL NOT NULL,
        nokta_sayisi INTEGER NOT NULL,
        uzunluk_km REAL NOT NULL,
        koordinatlar_geojson TEXT NOT NULL
    );
    """)

    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_detayli_faylar USING rtree(id, minX, maxX, minY, maxY);")
    cur.execute("DELETE FROM detayli_diri_faylar;")
    cur.execute("DELETE FROM rtree_detayli_faylar;")

    csv_rows = []

    for f in features:
        props = f.get("properties", {})
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [])
        if not coords or len(coords) < 2:
            continue

        cid = props.get("catalog_id", "BILINMIYOR")
        stype = props.get("slip_type", "Bilinmiyor")
        srate = props.get("net_slip_rate", "")
        dip = props.get("average_dip", "")
        rake = props.get("average_rake", "")
        depth = props.get("lower_seis_depth", "")

        lats = [p[1] for p in coords]
        lons = [p[0] for p in coords]
        min_x, max_x = min(lons), max(lons)
        min_y, max_y = min(lats), max(lats)
        lat_ort = sum(lats) / len(lats)
        lon_ort = sum(lons) / len(lons)

        fay_sistemi = tespit_et_fay_sistemi(lat_ort, lon_ort, stype)

        uzunluk_km = 0.0
        for i in range(len(coords) - 1):
            uzunluk_km += haversine(coords[i][1], coords[i][0], coords[i+1][1], coords[i+1][0]) / 1000.0
        uzunluk_km = round(uzunluk_km, 2)

        cur.execute("""
        INSERT INTO detayli_diri_faylar (
            catalog_id, fay_sistemi, slip_type, net_slip_rate, average_dip, average_rake, lower_seis_depth,
            baslangic_lat, baslangic_lon, bitis_lat, bitis_lon, nokta_sayisi, uzunluk_km, koordinatlar_geojson
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cid, fay_sistemi, stype, srate, dip, rake, depth,
            coords[0][1], coords[0][0], coords[-1][1], coords[-1][0],
            len(coords), uzunluk_km, json.dumps(coords)
        ))

        row_id = cur.lastrowid
        cur.execute("""
        INSERT INTO rtree_detayli_faylar VALUES (?, ?, ?, ?, ?)
        """, (row_id, min_x, max_x, min_y, max_y))

        csv_rows.append({
            "id": row_id,
            "catalog_id": cid,
            "fay_sistemi": fay_sistemi,
            "slip_type": stype,
            "net_slip_rate_mm_yil": srate,
            "average_dip": dip,
            "lower_seis_depth_km": depth,
            "baslangic_lat": coords[0][1],
            "baslangic_lon": coords[0][0],
            "bitis_lat": coords[-1][1],
            "bitis_lon": coords[-1][0],
            "nokta_sayisi": len(coords),
            "uzunluk_km": uzunluk_km
        })

    conn.commit()
    conn.close()

    log(f"  ✓ {len(csv_rows):,} detaylı aktif fay segmenti SQLite R*Tree veritabanına işlendi.", "SUCCESS")

    # 3. CSV Dosyası Olarak Kaydet
    csv_out = CSV_DIR / "turkiye_detayli_diri_faylar.csv"
    if csv_rows:
        with open(csv_out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)
        log(f"  ✓ CSV Envanteri Kaydedildi: {csv_out}", "SUCCESS")

    # 4. GeoJSON Kopyasını geojson dizinine de kaydet
    dest_geojson = GEOJSON_DIR / "turkiye_detayli_diri_faylar.geojson"
    with open(dest_geojson, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"  ✓ GeoJSON Envanteri Kaydedildi: {dest_geojson}", "SUCCESS")


class DetayliFaySorgulayici:
    """895 detaylı aktif fay segmenti üzerinden milisaniyelik en kısa mesafe sorgulayabilen motor."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        if not self.db_path.exists():
            build_detayli_fay_veritabani()

    def en_yakin_fay_sorgula(self, lat: float, lon: float, arama_yaricapi_km: float = 100.0) -> Dict[str, Any]:
        """Verilen koordinata en yakın detaylı diri fay segmentini ve milimetrik mesafesini hesaplar."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        d_lat = (arama_yaricapi_km * 1000.0) / 111320.0
        d_lon = (arama_yaricapi_km * 1000.0) / (111320.0 * max(0.1, math.cos(math.radians(lat))))

        cur.execute("""
        SELECT f.*
        FROM rtree_detayli_faylar r
        JOIN detayli_diri_faylar f ON r.id = f.id
        WHERE r.minX <= ? AND r.maxX >= ? AND r.minY <= ? AND r.maxY >= ?
        """, (lon + d_lon, lon - d_lon, lat + d_lat, lat - d_lat))

        rows = cur.fetchall()

        # Eğer yarıçapta yoksa tüm fayları tara
        if not rows:
            cur.execute("SELECT * FROM detayli_diri_faylar;")
            rows = cur.fetchall()

        conn.close()

        min_dist_m = float("inf")
        en_yakin = None

        for r in rows:
            try:
                coords = json.loads(r["koordinatlar_geojson"])
                d = min_distance_to_polyline(lat, lon, coords)
                if d < min_dist_m:
                    min_dist_m = d
                    en_yakin = r
            except Exception:
                pass

        if not en_yakin:
            return {
                "fay_bulundu": False,
                "mesafe_km": 999.0
            }

        mesafe_km = round(min_dist_m / 1000.0, 2)
        mesafe_m = round(min_dist_m, 1)

        return {
            "fay_bulundu": True,
            "catalog_id": en_yakin["catalog_id"],
            "fay_sistemi": en_yakin["fay_sistemi"],
            "slip_type": en_yakin["slip_type"],
            "net_slip_rate_mm_yil": en_yakin["net_slip_rate"],
            "average_dip": en_yakin["average_dip"],
            "lower_seis_depth_km": en_yakin["lower_seis_depth"],
            "fay_uzunluk_km": en_yakin["uzunluk_km"],
            "mesafe_km": mesafe_km,
            "mesafe_m": mesafe_m,
            "nokta_sayisi": en_yakin["nokta_sayisi"]
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Türkiye Detaylı Diri Fay Veritabanı ve Sorgulayıcı")
    parser.add_argument("--build", action="store_true", help="Veritabanını sıfırdan kur")
    parser.add_argument("--lat", type=float, default=41.0740, help="Test enlemi")
    parser.add_argument("--lon", type=float, default=28.2460, help="Test boylamı")
    args = parser.parse_args()

    if args.build or not DB_PATH.exists():
        build_detayli_fay_veritabani()

    s = DetayliFaySorgulayici()
    sonuc = s.en_yakin_fay_sorgula(args.lat, args.lon)
    print("\n╔═══════════════════════════════════════════════════════════════════════════════╗")
    print("║               GEOPROP AI - DETAYLI DİRİ FAY SORGULAMA SONUCU                  ║")
    print("╠═══════════════════════════════════════════════════════════════════════════════╣")
    print(f"║ Koordinat        : {args.lat:.5f}, {args.lon:.5f}")
    print(f"║ En Yakın Fay ID  : {sonuc.get('catalog_id')}")
    print(f"║ Tektonik Sistem  : {sonuc.get('fay_sistemi')}")
    print(f"║ Fay Türü (Mekanizma): {sonuc.get('slip_type')}")
    print(f"║ Kayma Hızı       : {sonuc.get('net_slip_rate_mm_yil')} mm/yıl")
    print(f"║ Segment Uzunluğu : {sonuc.get('fay_uzunluk_km')} km ({sonuc.get('nokta_sayisi')} kırık noktası)")
    print(f"║ Net Kuş Uçuşu Mesafe: {sonuc.get('mesafe_km')} km ({sonuc.get('mesafe_m')} metre)")
    print("╚═══════════════════════════════════════════════════════════════════════════════╝")
