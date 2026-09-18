#!/usr/bin/env python3
"""Restoran/perakende kaynaklarini mahalle ve sokak duzeyinde kayipsiz birlestir.

Kaynaklar birbirinin yerine gecmez: Yemeksepeti, Google snapshot, QR/web menu,
OSM guncel ve OSM yillik arsiv gozlemleri ayri provenance ile saklanir. Eksik
konumlar resmi mahalle kimligi/merkeziyle eslestirilir; kanitsiz atama yapilmaz.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from collector.ticari_taksonomi import classify
except ImportError:
    from ticari_taksonomi import classify

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = BASE_DIR / "warehouse/product/restoran_ve_kafe_menuleri.sqlite"
DEFAULT_YEMEK = BASE_DIR / "warehouse/product/yemek_ve_market_teslimat_ekosistemi.sqlite"
DEFAULT_GOOGLE = BASE_DIR / "warehouse/product/google_places_ve_yogunluk.sqlite"
DEFAULT_OSM = BASE_DIR / "warehouse/product/osm_poi.sqlite"
DEFAULT_OSM_HISTORY = BASE_DIR / "warehouse/product/osm_degisim.sqlite"
DEFAULT_REGION = BASE_DIR / "warehouse/product/bolge_istatistik.sqlite"
DEFAULT_BOUNDARY = BASE_DIR / "warehouse/product/idari_sinirlar.sqlite"

FOOD_TYPES = {
    "restaurant", "cafe", "fast_food", "bar", "pub", "bakery", "ice_cream",
    "marketplace", "greengrocer", "convenience", "supermarket", "deli",
    "confectionery", "nuts", "butcher", "seafood", "beverages",
}


def norm(value):
    import unicodedata
    text = str(value or "").strip().casefold().replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def norm_neighbourhood(value):
    value = norm(value)
    for suffix in (" mahallesi", " mahalle", " mh", " mah"):
        if value.endswith(suffix):
            return value[:-len(suffix)].strip()
    return value


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def stable_hash(*parts):
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode("utf-8")).hexdigest()


def parse_street(address):
    text = str(address or "").strip()
    if not text:
        return None, None, None
    postal = None
    match = re.search(r"\b(\d{5})\b", text)
    if match:
        postal = match.group(1)
    house = None
    match = re.search(r"\b(?:No\.?\s*:?)\s*(\d+[A-Za-z]?(?:[/\-]\d+[A-Za-z]?)?)", text, re.I)
    if match:
        house = match.group(1)
    street = None
    patterns = [
        r"([^,;/]+?\s+(?:Caddesi|Cadde|Cd\.?))(?=\s|,|;|/|$)",
        r"([^,;/]+?\s+(?:Sokak|Sokağı|Sk\.?))(?=\s|,|;|/|$)",
        r"([^,;/]+?\s+(?:Bulvarı|Bulvar|Blv\.?))(?=\s|,|;|/|$)",
        r"([^,;/]+?\s+(?:Yolu|Yol))(?=\s|,|;|/|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            street = re.sub(r"\s+", " ", match.group(1)).strip(" ,;/")
            break
    return street, house, postal


class LocationResolver:
    def __init__(self, region_db, boundary_db):
        self.city_by_norm = {}
        self.county_by_key = {}
        self.neighbourhood_by_key = {}
        self.neighbourhoods_by_county = defaultdict(list)
        self.centroid_grid = defaultdict(list)
        self._load(region_db, boundary_db)

    def _load(self, region_db, boundary_db):
        c = sqlite3.connect(f"file:{Path(region_db).resolve()}?mode=ro", uri=True)
        for city_id, name, name_norm in c.execute("SELECT city_id,ad,ad_norm FROM ref_il"):
            self.city_by_norm[norm(name_norm or name)] = (city_id, name)
        for county_id, city_id, name, name_norm in c.execute("SELECT county_id,city_id,ad,ad_norm FROM ref_ilce"):
            self.county_by_key[(city_id, norm(name_norm or name))] = (county_id, name)
        for district_id, county_id, city_id, name, name_norm in c.execute(
            "SELECT district_id,county_id,city_id,ad,ad_norm FROM ref_mahalle"
        ):
            key = (county_id, norm_neighbourhood(name_norm or name))
            row = {"district_id": district_id, "county_id": county_id, "city_id": city_id, "mahalle": name}
            self.neighbourhood_by_key[key] = row
            self.neighbourhoods_by_county[county_id].append((key[1], row))
        c.close()
        b = sqlite3.connect(f"file:{Path(boundary_db).resolve()}?mode=ro", uri=True)
        for name, city, county, lat, lon in b.execute(
            "SELECT mahalle_adi,il,ilce,lat,lon FROM mahalle_koordinat WHERE lat IS NOT NULL AND lon IS NOT NULL"
        ):
            city_row = self.city_by_norm.get(norm(city))
            if not city_row:
                continue
            county_row = self.county_by_key.get((city_row[0], norm(county)))
            if not county_row:
                continue
            nrow = self.neighbourhood_by_key.get((county_row[0], norm_neighbourhood(name)))
            if not nrow:
                continue
            row = dict(nrow, il=city_row[1], ilce=county_row[1], lat=float(lat), lon=float(lon))
            self.centroid_grid[(round(float(lat), 1), round(float(lon), 1))].append(row)
        b.close()

    def resolve(self, il=None, ilce=None, mahalle=None, address=None, lat=None, lon=None):
        city_row = self.city_by_norm.get(norm(il)) if il else None
        county_row = self.county_by_key.get((city_row[0], norm(ilce))) if city_row and ilce else None
        nrow = None
        method = None
        confidence = None
        distance_m = None
        if county_row and mahalle:
            nrow = self.neighbourhood_by_key.get((county_row[0], norm_neighbourhood(mahalle)))
            if nrow:
                method, confidence = "official_name", 1.0
        if not nrow and county_row and address:
            address_norm = norm_neighbourhood(address)
            matches = [(len(name), row) for name, row in self.neighbourhoods_by_county[county_row[0]] if name and name in address_norm]
            if matches:
                nrow = max(matches, key=lambda x: x[0])[1]
                method, confidence = "address_contains_official_name", 0.95
        if not nrow and lat is not None and lon is not None:
            lat, lon = float(lat), float(lon)
            candidates = []
            base = (round(lat, 1), round(lon, 1))
            for di in (-0.1, 0, 0.1):
                for dj in (-0.1, 0, 0.1):
                    candidates.extend(self.centroid_grid.get((round(base[0]+di, 1), round(base[1]+dj, 1)), []))
            if candidates:
                nearest = min(candidates, key=lambda x: haversine_km(lat, lon, x["lat"], x["lon"]))
                distance_m = round(haversine_km(lat, lon, nearest["lat"], nearest["lon"]) * 1000)
                if distance_m <= 3000:
                    nrow = nearest
                    city_row = (nearest["city_id"], nearest["il"])
                    county_row = (nearest["county_id"], nearest["ilce"])
                    method = "nearest_official_neighbourhood_centroid"
                    confidence = round(max(0.5, 0.9 - distance_m / 7500), 3)
        return {
            "city_id": nrow.get("city_id") if nrow else (city_row[0] if city_row else None),
            "county_id": nrow.get("county_id") if nrow else (county_row[0] if county_row else None),
            "district_id": nrow.get("district_id") if nrow else None,
            "il": (city_row[1] if city_row else il),
            "ilce": (county_row[1] if county_row else ilce),
            "mahalle": nrow.get("mahalle") if nrow else mahalle,
            "mahalle_norm": norm_neighbourhood(nrow.get("mahalle") if nrow else mahalle) or None,
            "method": method or "unresolved",
            "confidence": confidence,
            "distance_m": distance_m,
        }


def init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS isletme_gozlem (
      observation_id TEXT PRIMARY KEY,
      canonical_business_id TEXT NOT NULL,
      source TEXT NOT NULL,
      source_record_id TEXT NOT NULL,
      observed_at TEXT NOT NULL,
      record_class TEXT NOT NULL CHECK(record_class IN ('observed','quarantine')),
      business_name TEXT,
      sector TEXT,
      category TEXT,
      subcategories_json TEXT,
      cuisines_json TEXT,
      price_segment TEXT,
      business_status TEXT,
      rating REAL,
      rating_count INTEGER,
      review_count INTEGER,
      min_order_amount REAL,
      delivery_fee REAL,
      delivery_duration TEXT,
      payment_methods TEXT,
      campaigns TEXT,
      phone TEXT,
      website TEXT,
      qr_menu_url TEXT,
      working_hours_json TEXT,
      star_distribution_json TEXT,
      address TEXT,
      street TEXT,
      house_number TEXT,
      postal_code TEXT,
      city_id INTEGER,
      county_id INTEGER,
      district_id INTEGER,
      il TEXT,
      ilce TEXT,
      mahalle TEXT,
      mahalle_norm TEXT,
      lat REAL,
      lon REAL,
      location_method TEXT,
      location_confidence REAL,
      location_distance_m INTEGER,
      source_url TEXT,
      metric_status TEXT,
      payload_sha256 TEXT NOT NULL,
      raw_json TEXT NOT NULL,
      ingested_at TEXT NOT NULL,
      UNIQUE(source,source_record_id,observed_at,payload_sha256)
    );
    CREATE INDEX IF NOT EXISTS idx_isletme_gozlem_location ON isletme_gozlem(city_id,county_id,district_id,street);
    CREATE INDEX IF NOT EXISTS idx_isletme_gozlem_source_time ON isletme_gozlem(source,observed_at);
    CREATE INDEX IF NOT EXISTS idx_isletme_gozlem_business ON isletme_gozlem(canonical_business_id,observed_at);

    CREATE TABLE IF NOT EXISTS isletme_kaynak_kimligi (
      source TEXT NOT NULL,
      source_record_id TEXT NOT NULL,
      canonical_business_id TEXT NOT NULL,
      match_method TEXT NOT NULL,
      match_confidence REAL NOT NULL,
      PRIMARY KEY(source,source_record_id)
    );

    CREATE TABLE IF NOT EXISTS sentetik_menu_karantina AS
      SELECT *, 'legacy_synthetic_menu_catalog' AS quarantine_reason
      FROM mekan_menu_kalemleri_ve_fiyat_tarihcesi WHERE 0;

    CREATE TABLE IF NOT EXISTS mahalle_isletme_ozet (
      snapshot_date TEXT NOT NULL,
      city_id INTEGER,
      county_id INTEGER,
      district_id INTEGER,
      il TEXT,
      ilce TEXT,
      mahalle TEXT,
      mahalle_norm TEXT,
      kategori TEXT,
      sektor TEXT,
      aktif_isletme_sayisi INTEGER,
      kaynak_kayit_sayisi INTEGER,
      google_place_id_sayisi INTEGER,
      yemeksepeti_isletme_sayisi INTEGER,
      osm_isletme_sayisi INTEGER,
      ortalama_puan REAL,
      toplam_degerlendirme INTEGER,
      ilk_gozlem TEXT,
      son_gozlem TEXT,
      PRIMARY KEY(snapshot_date,county_id,district_id,sektor,kategori)
    );

    CREATE TABLE IF NOT EXISTS sokak_isletme_ozet (
      snapshot_date TEXT NOT NULL,
      city_id INTEGER,
      county_id INTEGER,
      district_id INTEGER,
      il TEXT,
      ilce TEXT,
      mahalle TEXT,
      street TEXT NOT NULL,
      kategori TEXT,
      sektor TEXT,
      aktif_isletme_sayisi INTEGER,
      kaynak_kayit_sayisi INTEGER,
      ortalama_puan REAL,
      toplam_degerlendirme INTEGER,
      ilk_gozlem TEXT,
      son_gozlem TEXT,
      PRIMARY KEY(snapshot_date,county_id,district_id,street,sektor,kategori)
    );

    CREATE TABLE IF NOT EXISTS isletme_yillik_trend (
      canonical_business_id TEXT NOT NULL,
      yil INTEGER NOT NULL,
      ilk_degerlendirme INTEGER,
      son_degerlendirme INTEGER,
      degerlendirme_artisi INTEGER,
      ilk_yorum INTEGER,
      son_yorum INTEGER,
      yorum_artisi INTEGER,
      gozlem_sayisi INTEGER NOT NULL,
      metric_status TEXT NOT NULL,
      PRIMARY KEY(canonical_business_id,yil)
    );

    CREATE TABLE IF NOT EXISTS kaynak_kapsam_raporu (
      kaynak TEXT PRIMARY KEY,
      gozlem_sayisi INTEGER,
      isletme_sayisi INTEGER,
      ilk_gozlem TEXT,
      son_gozlem TEXT,
      mahalle_eslesme_orani REAL,
      sokak_bulunma_orani REAL,
      guncellenme_tarihi TEXT
    );

    CREATE TABLE IF NOT EXISTS isletme_ardil_oncul_donusum_tarihcesi (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kategori TEXT NOT NULL,
      onceki_isletme_adi TEXT NOT NULL,
      yeni_isletme_adi TEXT NOT NULL,
      degisim_tarihi TEXT NOT NULL,
      donusum_tanimi TEXT NOT NULL,
      lat REAL NOT NULL,
      lon REAL NOT NULL,
      kaynak TEXT NOT NULL,
      guncellenme_tarihi TEXT NOT NULL,
      UNIQUE(onceki_isletme_adi,yeni_isletme_adi,degisim_tarihi,lat,lon)
    );
    """)


class Importer:
    def __init__(self, conn, resolver):
        self.conn = conn
        self.resolver = resolver
        self.entities = defaultdict(list)
        for source, rid, cid in conn.execute("SELECT source,source_record_id,canonical_business_id FROM isletme_kaynak_kimligi"):
            self.entities[(source, rid)] = cid

    def canonical_id(self, source, source_id, name, lat, lon):
        existing = self.conn.execute(
            "SELECT canonical_business_id FROM isletme_kaynak_kimligi WHERE source=? AND source_record_id=?",
            (source, source_id),
        ).fetchone()
        if existing:
            return existing[0]
        name_norm = norm(name)
        matched = None
        if name_norm and lat is not None and lon is not None:
            for cid, other_name, other_lat, other_lon in self.conn.execute(
                "SELECT canonical_business_id,business_name,lat,lon FROM isletme_gozlem "
                "WHERE record_class='observed' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                "ORDER BY observed_at DESC LIMIT 300",
                (float(lat)-0.002, float(lat)+0.002, float(lon)-0.003, float(lon)+0.003),
            ):
                ratio = difflib.SequenceMatcher(None, name_norm, norm(other_name)).ratio()
                if ratio >= 0.92 and haversine_km(float(lat), float(lon), float(other_lat), float(other_lon)) <= 0.15:
                    matched = cid
                    break
        cid = matched or "biz_" + stable_hash(source, source_id)[:24]
        self.conn.execute(
            "INSERT OR REPLACE INTO isletme_kaynak_kimligi VALUES (?,?,?,?,?)",
            (source, source_id, cid, "name_and_150m" if matched else "source_identity", 0.95 if matched else 1.0),
        )
        return cid

    def add(self, source, source_id, observed_at, payload, *, record_class="observed"):
        name = payload.get("name")
        lat, lon = payload.get("lat"), payload.get("lon")
        location = self.resolver.resolve(
            payload.get("il"), payload.get("ilce"), payload.get("mahalle"),
            payload.get("address"), lat, lon,
        )
        street, house, postal = parse_street(payload.get("address"))
        sector, category, inferred_subcategories = classify(
            name, payload.get("category"), payload.get("subcategories_json")
        )
        sector = payload.get("sector") or sector
        category = payload.get("canonical_category") or category
        subcategories = payload.get("subcategories_json") or inferred_subcategories
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        phash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        observed_at = str(observed_at or utc_now())
        cid = self.canonical_id(source, str(source_id), name, lat, lon)
        oid = stable_hash(source, source_id, observed_at, phash)
        columns = (
            "observation_id", "canonical_business_id", "source", "source_record_id", "observed_at", "record_class",
            "business_name", "sector", "category", "subcategories_json", "cuisines_json", "price_segment", "business_status",
            "rating", "rating_count", "review_count", "min_order_amount", "delivery_fee", "delivery_duration", "payment_methods",
            "campaigns", "phone", "website", "qr_menu_url", "working_hours_json", "star_distribution_json",
            "address", "street", "house_number", "postal_code", "city_id", "county_id", "district_id", "il", "ilce",
            "mahalle", "mahalle_norm", "lat", "lon", "location_method", "location_confidence", "location_distance_m",
            "source_url", "metric_status", "payload_sha256", "raw_json", "ingested_at",
        )
        values = (
            oid,cid,source,str(source_id),observed_at,record_class,name,sector,category,
            subcategories,payload.get("cuisines_json"),payload.get("price_segment"),payload.get("status"),payload.get("rating"),
            payload.get("rating_count"),payload.get("review_count"),payload.get("min_order_amount"),
            payload.get("delivery_fee"),payload.get("delivery_duration"),payload.get("payment_methods"),payload.get("campaigns"),
            payload.get("phone"),payload.get("website"),payload.get("qr_menu_url"),payload.get("working_hours_json"),
            payload.get("star_distribution_json"),
            payload.get("address"),street,house,postal,location["city_id"],location["county_id"],
            location["district_id"],location["il"],location["ilce"],location["mahalle"],
            location["mahalle_norm"],lat,lon,location["method"],location["confidence"],
            location["distance_m"],payload.get("source_url"),payload.get("metric_status") or "observed_fields_only",phash,raw,utc_now(),
        )
        self.conn.execute(
            f"INSERT OR IGNORE INTO isletme_gozlem ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )


def table_exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def import_yemek(importer, path):
    c = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True); c.row_factory = sqlite3.Row
    count = 0
    for row in c.execute("SELECT * FROM uye_restoranlar_ve_hacim"):
        r = dict(row)
        source = "yemeksepeti" if str(r.get("platform", "")).startswith("YEMEKSEPETI") else "local_commercial_inventory"
        importer.add(source, r["restoran_kodu"], r.get("guncellenme_tarihi"), {
            "name": r.get("restoran_adi"), "category": r.get("platform"), "subcategories_json": r.get("mutfaklar"),
            "cuisines_json": r.get("mutfaklar"), "price_segment": r.get("fiyat_segmenti"),
            "rating": r.get("puan"), "rating_count": r.get("degerlendirme_sayisi"), "review_count": r.get("yorum_sayisi"),
            "il": r.get("sehir"), "ilce": r.get("ilce"), "address": r.get("tam_adres"),
            "lat": r.get("lat"), "lon": r.get("lon"), "source_url": r.get("url"), "status": "ACTIVE",
        }); count += 1
    c.close(); return count


def import_google(importer, path):
    c = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True); c.row_factory = sqlite3.Row
    count = 0
    for row in c.execute("SELECT * FROM google_places_ticari_yogunluk"):
        r = dict(row); source = "google_snapshot" if str(r.get("kaynak", "")).startswith("Google") else "commercial_reference_inventory"
        importer.add(source, r["google_place_id"], r.get("guncellenme_tarihi"), {
            "name": r.get("isim"), "category": r.get("ana_kategori"), "subcategories_json": r.get("tum_kategoriler"),
            "rating": r.get("puan"), "rating_count": r.get("degerlendirme_sayisi"), "review_count": r.get("yorum_sayisi"),
            "il": r.get("il"), "ilce": r.get("ilce"), "mahalle": r.get("mahalle"), "address": r.get("tam_adres"),
            "lat": r.get("lat"), "lon": r.get("lon"), "source_url": r.get("maps_url"), "status": "CURRENT_SNAPSHOT",
            "phone": r.get("telefon"), "website": r.get("web_sitesi"),
            "working_hours_json": r.get("calisma_saatleri"), "star_distribution_json": r.get("yildiz_dagilimi"),
        }); count += 1
    c.close(); return count


def import_osm(importer, current_path, history_path):
    count = 0
    c = sqlite3.connect(f"file:{Path(current_path).resolve()}?mode=ro", uri=True); c.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in FOOD_TYPES)
    for row in c.execute(f"SELECT * FROM poi WHERE alt_kategori IN ({placeholders})", tuple(sorted(FOOD_TYPES))):
        r = dict(row)
        importer.add("osm_current", f"{r['osm_tip']}/{r['osm_id']}", utc_now(), {
            "name": r.get("ad"), "category": r.get("alt_kategori"), "subcategories_json": r.get("etiketler"),
            "lat": r.get("lat"), "lon": r.get("lon"), "status": "CURRENT",
        }); count += 1
    c.close()
    h = sqlite3.connect(f"file:{Path(history_path).resolve()}?mode=ro", uri=True); h.row_factory = sqlite3.Row
    for row in h.execute(f"SELECT * FROM poi_yillik WHERE alt_kategori IN ({placeholders})", tuple(sorted(FOOD_TYPES))):
        r = dict(row)
        importer.add("osm_historical", f"{r['osm_tip']}/{r['osm_id']}", r.get("tarih"), {
            "name": r.get("ad"), "category": r.get("alt_kategori"), "lat": r.get("lat"), "lon": r.get("lon"),
            "status": "OBSERVED_IN_SNAPSHOT",
        }); count += 1
    h.close(); return count


def import_osm_business_transitions(conn, history_path):
    """Yillik OSM kesitlerindeki kanitli isim degisimlerini donusum olarak saklar."""
    source = sqlite3.connect(f"file:{Path(history_path).resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in FOOD_TYPES)
    inserted = 0
    for row in source.execute(
        f"SELECT * FROM poi_yasam WHERE alt_kategori IN ({placeholders}) "
        "AND (ad_degisim=1 OR marka_degisim=1) AND gecmis IS NOT NULL",
        tuple(sorted(FOOD_TYPES)),
    ):
        r = dict(row)
        try:
            events = json.loads(r.get("gecmis") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        previous = None
        for event in events:
            if not isinstance(event, dict):
                continue
            current_name = str(event.get("ad") or event.get("marka") or "").strip()
            if previous and current_name and current_name != previous["name"]:
                old_sector, old_category, _ = classify(previous["name"], previous.get("alt"), None)
                new_sector, new_category, _ = classify(current_name, event.get("alt"), None)
                description = (
                    f"Sektor disi donusum: {old_sector} -> {new_sector}"
                    if old_sector != new_sector else
                    f"Sektor ici donusum: {old_category} -> {new_category}"
                )
                before = conn.total_changes
                conn.execute(
                    "INSERT OR IGNORE INTO isletme_ardil_oncul_donusum_tarihcesi "
                    "(kategori,onceki_isletme_adi,yeni_isletme_adi,degisim_tarihi,donusum_tanimi,lat,lon,kaynak,guncellenme_tarihi) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (new_category, previous["name"], current_name, event.get("t"), description,
                     r.get("lat"), r.get("lon"), "OSM yillik kesit gozlemi", utc_now()),
                )
                inserted += conn.total_changes - before
            if current_name:
                previous = {"name": current_name, "alt": event.get("alt")}
    source.close()
    return inserted


def import_existing_restaurant_intelligence(importer):
    """Ayni hedef DB'deki gercek menu/yasam gozlemlerini ortak modele al."""
    conn = importer.conn
    if not table_exists(conn, "isletme_tarihsel_yasam_dongusu"):
        return 0
    conn.row_factory = sqlite3.Row
    count = 0
    for row in conn.execute("SELECT * FROM isletme_tarihsel_yasam_dongusu"):
        r = dict(row)
        fabricated_lifecycle = (
            r.get("ilk_tespit_tarihi") == "2022-06-15"
            and r.get("son_tespit_tarihi") == "2026-09-15"
            and r.get("faaliyet_suresi_ay") == 51
        )
        importer.add("restaurant_intelligence", r["mekan_id"], r.get("guncellenme_tarihi"), {
            "name": r.get("mekan_adi"), "sector": r.get("sektor"),
            "canonical_category": r.get("ana_kategori"), "subcategories_json": r.get("alt_kategoriler"),
            "cuisines_json": r.get("mutfaklar"), "price_segment": r.get("fiyat_segmenti"),
            "status": r.get("durum"), "rating": r.get("puan"), "rating_count": r.get("degerlendirme_sayisi"),
            "review_count": r.get("yorum_sayisi"), "min_order_amount": r.get("min_sepet_tutari"),
            "delivery_fee": r.get("teslimat_ucreti"), "delivery_duration": r.get("teslimat_suresi"),
            "payment_methods": r.get("odeme_yontemleri"), "campaigns": r.get("kampanyalar"),
            "phone": r.get("telefon"), "website": r.get("web_sitesi"), "qr_menu_url": r.get("qr_menu_url"),
            "working_hours_json": r.get("calisma_saatleri"), "star_distribution_json": r.get("yildiz_dagilimi"),
            "il": r.get("il"), "ilce": r.get("ilce"), "mahalle": r.get("mahalle"), "address": r.get("tam_adres"),
            "lat": r.get("lat"), "lon": r.get("lon"), "metric_status": "legacy_lifecycle_fields_quarantined" if fabricated_lifecycle else "observed_fields_only",
        }, record_class="quarantine" if fabricated_lifecycle else "observed")
        count += 1
    conn.row_factory = None
    return count


def quarantine_legacy_menu(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mekan_menu_kalemleri_ve_fiyat_tarihcesi)")}
    if "record_class" not in cols:
        conn.execute("ALTER TABLE mekan_menu_kalemleri_ve_fiyat_tarihcesi ADD COLUMN record_class TEXT DEFAULT 'observed'")
    if "record_class_reason" not in cols:
        conn.execute("ALTER TABLE mekan_menu_kalemleri_ve_fiyat_tarihcesi ADD COLUMN record_class_reason TEXT")
    where = "platform LIKE 'Sektörel % Menü Kataloğu' OR platform='Dükkan İçi Fiziki / Masa QR Menü'"
    conn.execute(f"UPDATE mekan_menu_kalemleri_ve_fiyat_tarihcesi SET record_class='quarantine', record_class_reason='legacy_synthetic_menu_catalog' WHERE {where}")
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(mekan_menu_kalemleri_ve_fiyat_tarihcesi)")]
    quarantine_cols = {r[1] for r in conn.execute("PRAGMA table_info(sentetik_menu_karantina)")}
    common = [c for c in existing_cols if c in quarantine_cols]
    if common:
        names = ",".join(f'"{c}"' for c in common)
        conn.execute(f"INSERT OR IGNORE INTO sentetik_menu_karantina ({names}) SELECT {names} FROM mekan_menu_kalemleri_ve_fiyat_tarihcesi WHERE record_class='quarantine'")
    return conn.execute("SELECT count(*) FROM mekan_menu_kalemleri_ve_fiyat_tarihcesi WHERE record_class='quarantine'").fetchone()[0]


def rebuild_summaries(conn):
    conn.execute("DELETE FROM mahalle_isletme_ozet")
    conn.execute("DELETE FROM sokak_isletme_ozet")
    conn.execute("""
    INSERT INTO mahalle_isletme_ozet (
      snapshot_date,city_id,county_id,district_id,il,ilce,mahalle,mahalle_norm,kategori,sektor,
      aktif_isletme_sayisi,kaynak_kayit_sayisi,google_place_id_sayisi,yemeksepeti_isletme_sayisi,
      osm_isletme_sayisi,ortalama_puan,toplam_degerlendirme,ilk_gozlem,son_gozlem
    )
    SELECT substr(max(observed_at),1,10),city_id,county_id,district_id,il,ilce,mahalle,mahalle_norm,
           coalesce(category,'diger'),coalesce(sector,'diger'),count(DISTINCT canonical_business_id),count(*),
           count(DISTINCT CASE WHEN source='google_snapshot' THEN source_record_id END),
           count(DISTINCT CASE WHEN source='yemeksepeti' THEN source_record_id END),
           count(DISTINCT CASE WHEN source LIKE 'osm_%' THEN source_record_id END),
           round(avg(rating),2),sum(coalesce(rating_count,0)),min(observed_at),max(observed_at)
    FROM isletme_gozlem WHERE record_class='observed' AND district_id IS NOT NULL
    GROUP BY city_id,county_id,district_id,il,ilce,mahalle,mahalle_norm,coalesce(category,'diger'),coalesce(sector,'diger')
    """)
    conn.execute("""
    INSERT INTO sokak_isletme_ozet (
      snapshot_date,city_id,county_id,district_id,il,ilce,mahalle,street,kategori,sektor,
      aktif_isletme_sayisi,kaynak_kayit_sayisi,ortalama_puan,toplam_degerlendirme,ilk_gozlem,son_gozlem
    )
    SELECT substr(max(observed_at),1,10),city_id,county_id,district_id,il,ilce,mahalle,street,
           coalesce(category,'diger'),coalesce(sector,'diger'),count(DISTINCT canonical_business_id),count(*),round(avg(rating),2),
           sum(coalesce(rating_count,0)),min(observed_at),max(observed_at)
    FROM isletme_gozlem WHERE record_class='observed' AND district_id IS NOT NULL AND street IS NOT NULL
    GROUP BY city_id,county_id,district_id,il,ilce,mahalle,street,coalesce(category,'diger'),coalesce(sector,'diger')
    """)
    conn.execute("DELETE FROM isletme_yillik_trend")
    conn.execute("""
    INSERT INTO isletme_yillik_trend
    WITH ranked AS (
      SELECT canonical_business_id,cast(substr(observed_at,1,4) AS INTEGER) AS yil,
             rating_count,review_count,observed_at,
             row_number() OVER (PARTITION BY canonical_business_id,substr(observed_at,1,4) ORDER BY observed_at) AS rn_first,
             row_number() OVER (PARTITION BY canonical_business_id,substr(observed_at,1,4) ORDER BY observed_at DESC) AS rn_last
      FROM isletme_gozlem WHERE record_class='observed'
    ), aggregate_rows AS (
      SELECT canonical_business_id,yil,
             max(CASE WHEN rn_first=1 THEN rating_count END) AS first_rating,
             max(CASE WHEN rn_last=1 THEN rating_count END) AS last_rating,
             max(CASE WHEN rn_first=1 THEN review_count END) AS first_review,
             max(CASE WHEN rn_last=1 THEN review_count END) AS last_review,
             count(*) AS observations
      FROM ranked GROUP BY canonical_business_id,yil
    )
    SELECT canonical_business_id,yil,first_rating,last_rating,
           CASE WHEN observations>=2 AND first_rating IS NOT NULL AND last_rating IS NOT NULL THEN last_rating-first_rating END,
           first_review,last_review,
           CASE WHEN observations>=2 AND first_review IS NOT NULL AND last_review IS NOT NULL THEN last_review-first_review END,
           observations,CASE WHEN observations>=2 THEN 'observed_delta' ELSE 'insufficient_observations' END
    FROM aggregate_rows
    """)
    conn.execute("DELETE FROM kaynak_kapsam_raporu")
    conn.execute("""
    INSERT INTO kaynak_kapsam_raporu
    SELECT source,count(*),count(DISTINCT source_record_id),min(observed_at),max(observed_at),
           round(1.0*sum(CASE WHEN district_id IS NOT NULL THEN 1 ELSE 0 END)/count(*),4),
           round(1.0*sum(CASE WHEN street IS NOT NULL THEN 1 ELSE 0 END)/count(*),4),?
    FROM isletme_gozlem GROUP BY source
    """, (utc_now(),))


def run(args):
    target = Path(args.out).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size:
        backup = target.with_suffix(target.suffix + "." + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak")
        shutil.copy2(target, backup)
        print(f"Yedek: {backup}")
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        init_schema(conn)
        resolver = LocationResolver(args.region_db, args.boundary_db)
        importer = Importer(conn, resolver)
        counts = {
            "yemek": import_yemek(importer, args.yemek_db),
            "google": import_google(importer, args.google_db),
            "osm": import_osm(importer, args.osm_db, args.osm_history_db),
            "restaurant_intelligence": import_existing_restaurant_intelligence(importer),
        }
        counts["osm_business_transitions"] = import_osm_business_transitions(conn, args.osm_history_db)
        quarantined = quarantine_legacy_menu(conn)
        rebuild_summaries(conn)
        conn.commit()
        result = {
            "sources": counts,
            "observations": conn.execute("SELECT count(*) FROM isletme_gozlem").fetchone()[0],
            "businesses": conn.execute("SELECT count(DISTINCT canonical_business_id) FROM isletme_gozlem WHERE record_class='observed'").fetchone()[0],
            "matched_neighbourhood": conn.execute("SELECT count(*) FROM isletme_gozlem WHERE district_id IS NOT NULL").fetchone()[0],
            "matched_street": conn.execute("SELECT count(*) FROM isletme_gozlem WHERE street IS NOT NULL").fetchone()[0],
            "quarantined_menu_rows": quarantined,
            "neighbourhood_summary_rows": conn.execute("SELECT count(*) FROM mahalle_isletme_ozet").fetchone()[0],
            "street_summary_rows": conn.execute("SELECT count(*) FROM sokak_isletme_ozet").fetchone()[0],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        conn.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Restoran ve perakende verilerini mahalle/sokak duzeyinde birlestir")
    p.add_argument("--calistir", action="store_true", help="Veritabani yazimini acikca etkinlestir")
    p.add_argument("--out", default=str(DEFAULT_TARGET))
    p.add_argument("--yemek-db", default=str(DEFAULT_YEMEK))
    p.add_argument("--google-db", default=str(DEFAULT_GOOGLE))
    p.add_argument("--osm-db", default=str(DEFAULT_OSM))
    p.add_argument("--osm-history-db", default=str(DEFAULT_OSM_HISTORY))
    p.add_argument("--region-db", default=str(DEFAULT_REGION))
    p.add_argument("--boundary-db", default=str(DEFAULT_BOUNDARY))
    args = p.parse_args(argv)
    if not args.calistir:
        p.error("Yazma islemi icin --calistir zorunludur")
    return run(args)


if __name__ == "__main__":
    main()
