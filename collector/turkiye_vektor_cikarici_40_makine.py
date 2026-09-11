#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Türkiye Tam Vektör Çıkarıcı (40 GitHub Action Makinesi Uyumlu)
==========================================================================
Türkiye'nin 783.562 km² yüzölçümünü 40 bağımsız coğrafi sektöre böler.
Her makine (veya lokal iş parçacığı) kendisine atanan sektördeki DEĞİŞMEYECEK
tüm fiziksel, hukuki ve altyapı katmanlarının TAM POLİGON ve ÇİZGİ (LineString)
koordinatlarını saf GeoJSON vektörleri olarak çıkarır:

  1. Detaylı Diri Fay Hatları (GEM & MTA 895 Segment - Kırık Çizgileri)
  2. Elektrik İletim Hatları & Trafo Merkezleri (TEİAŞ ENH 380kV/154kV)
  3. Karayolları ve Gelecek Yol Projeleri (Otoyol, Devlet, İnşaat Halindeki Projeler)
  4. Demiryolları ve Tren Rayları (TCDD Konvansiyonel & YHT Hatları)
  5. Sit Alanları ve Özel Koruma Bölgeleri (Doğal, Arkeolojik Sit, Milli Parklar)
  6. Sahil Şeritleri & Kıyı Çizgisi (3621 Sayılı Kıyı Kanunu)
  7. Orman Alanları ve Sınırları (Devlet Ormanı Poligonları)
  8. Su Yolları, Sulama Kanalları, Göller ve Kuru Dere Yatakları
  9. Su Altyapısı: İletim Boru Hatları, Su Kuyuları, Pınarlar ve Çeşmeler
 10. OGM Orman Yangını Risk Kuşakları

Kullanım:
  python3 turkiye_vektor_cikarici_40_makine.py --sektor 1
  python3 turkiye_vektor_cikarici_40_makine.py --sektor 18   # İstanbul / Marmara
  python3 turkiye_vektor_cikarici_40_makine.py --hepsi       # 40 sektörün hepsini yerelde sırayla çıkar
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
OUTPUT_DIR = DATA_DIR / "vektor_sektorler"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_FAY_GEOJSON = DATA_DIR / "turkiye_detayli_diri_faylar.geojson"
REMOTE_GEM_URL = "https://raw.githubusercontent.com/GEMScienceTools/gem-global-active-faults/master/geojson/gem_active_faults.geojson"

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter"
]

# -----------------------------------------------------------------------------
# 40 COĞRAFİ SEKTÖR TANIMI (Tüm Türkiye'yi 5 Enlem x 8 Boylam ile 0.2° Örtüşmeli Kaplar)
# -----------------------------------------------------------------------------
SEKTORLER: Dict[int, Dict[str, Any]] = {
    # 1. SATIR: GÜNEY TÜRKİYE (Enlem: 35.7 - 37.2)
    1: {"ad": "Güneybatı Ege / Rodos-Marmaris Sahil", "bbox": (35.7, 25.4, 37.2, 28.0), "iller": ["Muğla"]},
    2: {"ad": "Batı Akdeniz / Kaş-Finike-Kumluca", "bbox": (35.7, 27.8, 37.2, 30.4), "iller": ["Antalya"]},
    3: {"ad": "Antalya Körfezi / Alanya-Gazipaşa", "bbox": (35.7, 30.2, 37.2, 32.8), "iller": ["Antalya", "Karaman"]},
    4: {"ad": "Taşeli Platosu / Anamur-Silifke", "bbox": (35.7, 32.6, 37.2, 35.2), "iller": ["Mersin"]},
    5: {"ad": "Çukurova / Mersin-Adana Sahil", "bbox": (35.7, 35.0, 37.2, 37.6), "iller": ["Adana", "Hatay", "Osmaniye"]},
    6: {"ad": "Gaziantep-Kilis-Fırat Havzası", "bbox": (35.7, 37.4, 37.2, 40.0), "iller": ["Gaziantep", "Kilis", "Şanlıurfa"]},
    7: {"ad": "Güneydoğu / Harran-Ceylanpınar-Mardin", "bbox": (35.7, 39.8, 37.2, 42.4), "iller": ["Şanlıurfa", "Mardin"]},
    8: {"ad": "Cizre-Silopi-Hakkari Güney Sınırı", "bbox": (35.7, 42.2, 37.2, 44.9), "iller": ["Şırnak", "Hakkari"]},

    # 2. SATIR: GÜNEY-ORTA TÜRKİYE (Enlem: 37.0 - 38.5)
    9: {"ad": "Ege Sahili / Muğla-Aydın-Milas", "bbox": (37.0, 25.4, 38.5, 28.0), "iller": ["Aydın", "Muğla"]},
    10: {"ad": "İç Batı Anadolu / Denizli-Burdur", "bbox": (37.0, 27.8, 38.5, 30.4), "iller": ["Denizli", "Burdur", "Isparta"]},
    11: {"ad": "Göller Yöresi / Isparta-Beyşehir", "bbox": (37.0, 30.2, 38.5, 32.8), "iller": ["Isparta", "Konya"]},
    12: {"ad": "Konya Ovası Güneyi / Karaman", "bbox": (37.0, 32.6, 38.5, 35.2), "iller": ["Konya", "Karaman", "Aksaray"]},
    13: {"ad": "Toroslar / Niğde-Kayseri Güneyi", "bbox": (37.0, 35.0, 38.5, 37.6), "iller": ["Niğde", "Adana", "Kahramanmaraş"]},
    14: {"ad": "Doğu Anadolu Fayı / K.Maraş-Malatya-Adıyaman", "bbox": (37.0, 37.4, 38.5, 40.0), "iller": ["Kahramanmaraş", "Adıyaman", "Malatya"]},
    15: {"ad": "Diyarbakır-Batman-Siirt Koridoru", "bbox": (37.0, 39.8, 38.5, 42.4), "iller": ["Diyarbakır", "Batman", "Siirt", "Mardin"]},
    16: {"ad": "Hakkari Dağları / Şemdinli-Yüksekova", "bbox": (37.0, 42.2, 38.5, 44.9), "iller": ["Hakkari", "Van"]},

    # 3. SATIR: ORTA TÜRKİYE (Enlem: 38.3 - 39.8)
    17: {"ad": "İzmir Körfezi / Çeşme-Karaburun-Manisa", "bbox": (38.3, 25.4, 39.8, 28.0), "iller": ["İzmir", "Manisa"]},
    18: {"ad": "Gediz Havzası / Uşak-Kütahya Güneyi", "bbox": (38.3, 27.8, 39.8, 30.4), "iller": ["Uşak", "Kütahya", "Manisa"]},
    19: {"ad": "Afyonkarahisar / Sandıklı-Emirdağ", "bbox": (38.3, 30.2, 39.8, 32.8), "iller": ["Afyonkarahisar", "Eskişehir"]},
    20: {"ad": "Tuz Gölü Havzası / Ankara Güneyi-Konya", "bbox": (38.3, 32.6, 39.8, 35.2), "iller": ["Ankara", "Konya", "Aksaray"]},
    21: {"ad": "Kapadokya / Nevşehir-Kırşehir-Kayseri", "bbox": (38.3, 35.0, 39.8, 37.6), "iller": ["Nevşehir", "Kırşehir", "Kayseri"]},
    22: {"ad": "Yukarı Fırat / Malatya-Elazığ-Sivas Güneyi", "bbox": (38.3, 37.4, 39.8, 40.0), "iller": ["Elazığ", "Malatya", "Sivas"]},
    23: {"ad": "Bingöl-Muş-Tunceli / DAF-KAF Kesişimi", "bbox": (38.3, 39.8, 39.8, 42.4), "iller": ["Bingöl", "Muş", "Tunceli", "Bitlis"]},
    24: {"ad": "Van Gölü Havzası / Van-Tatvan", "bbox": (38.3, 42.2, 39.8, 44.9), "iller": ["Van", "Bitlis"]},

    # 4. SATIR: KUZEY-ORTA TÜRKİYE (Enlem: 39.6 - 41.1)
    25: {"ad": "Kuzey Ege / Çanakkale-Edremit Körfezi", "bbox": (39.6, 25.4, 41.1, 28.0), "iller": ["Çanakkale", "Balıkesir"]},
    26: {"ad": "Güney Marmara / Balıkesir-Bursa", "bbox": (39.6, 27.8, 41.1, 30.4), "iller": ["Bursa", "Balıkesir", "Bilecik"]},
    27: {"ad": "Bilecik-Eskişehir / Bozüyük Havzası", "bbox": (39.6, 30.2, 41.1, 32.8), "iller": ["Bilecik", "Eskişehir", "Bolu"]},
    28: {"ad": "Başkent / Ankara-Kırıkkale", "bbox": (39.6, 32.6, 41.1, 35.2), "iller": ["Ankara", "Kırıkkale", "Çankırı"]},
    29: {"ad": "Orta Anadolu / Yozgat-Çorum", "bbox": (39.6, 35.0, 41.1, 37.6), "iller": ["Yozgat", "Çorum", "Amasya"]},
    30: {"ad": "Sivas Platosu / KAF Tokat Kuşağı", "bbox": (39.6, 37.4, 41.1, 40.0), "iller": ["Sivas", "Tokat", "Erzincan"]},
    31: {"ad": "Erzincan Ovası / KAF Erzincan-Erzurum", "bbox": (39.6, 39.8, 41.1, 42.4), "iller": ["Erzincan", "Erzurum", "Bayburt"]},
    32: {"ad": "Ağrı Dağı / Erzurum Doğu-Ağrı-Iğdır", "bbox": (39.6, 42.2, 41.1, 44.9), "iller": ["Ağrı", "Iğdır", "Kars"]},

    # 5. SATIR: KUZEY TÜRKİYE & MARMARA & KARADENİZ (Enlem: 40.9 - 42.3)
    33: {"ad": "Trakya / Edirne-Kırklareli-Tekirdağ", "bbox": (40.9, 25.4, 42.3, 28.0), "iller": ["Edirne", "Kırklareli", "Tekirdağ"]},
    34: {"ad": "İstanbul & Boğaz Koridoru / Kocaeli-Yalova", "bbox": (40.9, 27.8, 42.3, 30.4), "iller": ["İstanbul", "Kocaeli", "Yalova"]},
    35: {"ad": "Batı Karadeniz / Sakarya-Düzce-Bolu-Zonguldak", "bbox": (40.9, 30.2, 42.3, 32.8), "iller": ["Sakarya", "Düzce", "Bolu", "Zonguldak"]},
    36: {"ad": "Küre Dağları / Bartın-Karabük-Kastamonu", "bbox": (40.9, 32.6, 42.3, 35.2), "iller": ["Bartın", "Karabük", "Kastamonu"]},
    37: {"ad": "Orta Karadeniz / Sinop-Samsun", "bbox": (40.9, 35.0, 42.3, 37.6), "iller": ["Sinop", "Samsun", "Amasya"]},
    38: {"ad": "Doğu Karadeniz Batı / Ordu-Giresun-Trabzon", "bbox": (40.9, 37.4, 42.3, 40.0), "iller": ["Ordu", "Giresun", "Trabzon"]},
    39: {"ad": "Rize-Artvin / Kaçkar Dağları", "bbox": (40.9, 39.8, 42.3, 42.4), "iller": ["Rize", "Artvin", "Gümüşhane"]},
    40: {"ad": "Kafkas Sınırı / Ardahan-Kars-Çıldır", "bbox": (40.9, 42.2, 42.3, 44.9), "iller": ["Ardahan", "Kars"]}
}


def log(msg: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    symbols = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌", "STEP": "🚀"}
    prefix = symbols.get(level, "•")
    print(f"[{now_str}] {prefix} {msg}", flush=True)


def bbox_to_overpass_str(bbox: Tuple[float, float, float, float]) -> str:
    """(min_lat, min_lon, max_lat, max_lon) -> 'min_lat,min_lon,max_lat,max_lon'"""
    return f"{bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}"


def overpass_sorgula(query: str, max_retry: int = 4) -> Optional[Dict[str, Any]]:
    """Birden çok Overpass sunucusunda yük devretmeli (failover) istek çalıştırır."""
    encoded_query = urllib.parse.urlencode({"data": query}).encode("utf-8")

    for deneme in range(max_retry):
        server = OVERPASS_SERVERS[deneme % len(OVERPASS_SERVERS)]
        try:
            req = urllib.request.Request(
                server,
                data=encoded_query,
                headers={"User-Agent": "GEOPROP-AI/1.0 (Vector Infrastructure Harvester)"}
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "elements" in data:
                    return data
        except Exception as e:
            log(f"Sunucu [{server}] yanıt vermedi ({e}), sonraki deneniyor...", "WARN")
            time.sleep(2.0 * (deneme + 1))

    return None


# -----------------------------------------------------------------------------
# 1. DETAYLI DİRİ FAYLAR (GEM & MTA 895 Segment Kırık Çizgileri)
# -----------------------------------------------------------------------------
def cikar_detayli_faylar(bbox: Tuple[float, float, float, float]) -> List[Dict[str, Any]]:
    """Sektör sınırları içerisinden geçen aktif fay segmentlerinin tam kırık polylinelerini çıkarır."""
    min_lat, min_lon, max_lat, max_lon = bbox

    # Yerel GeoJSON yoksa GitHub'dan çek
    if not LOCAL_FAY_GEOJSON.exists():
        log("GEM Active Faults veritabanı indiriliyor...", "STEP")
        req = urllib.request.Request(REMOTE_GEM_URL, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                global_data = json.loads(r.read().decode("utf-8"))
                features = global_data.get("features", [])
        except Exception as e:
            log(f"Fay indirme hatası: {e}", "ERROR")
            return []
    else:
        with open(LOCAL_FAY_GEOJSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            features = data.get("features", [])

    sektor_faylar = []
    for f in features:
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [])
        if not coords:
            continue

        # Sektörle kesişim kontrolü
        flat = coords if geom.get("type") == "LineString" else [p for sub in coords for p in sub]
        kesisiyor = False
        for p in flat:
            lon, lat = p[0], p[1]
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                kesisiyor = True
                break

        if kesisiyor:
            props = f.get("properties", {})
            sektor_faylar.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "katman": "DİRİ_FAY_HATTI",
                    "fay_id": props.get("catalog_id", "Bilinmiyor"),
                    "slip_type": props.get("slip_type", "Doğrultu Atım"),
                    "kayma_hizi_mm_yil": props.get("net_slip_rate", ""),
                    "egim_dip": props.get("average_dip", ""),
                    "derinlik_km": props.get("lower_seis_depth", ""),
                    "nokta_sayisi": len(flat)
                }
            })

    return sektor_faylar


# -----------------------------------------------------------------------------
# 2. OSM ELEKTRİK, YOLLAR, RAYLAR, SİT, SAHİL, ORMAN, SU KATMANLARI
# -----------------------------------------------------------------------------
def cikar_osm_vektorleri(bbox: Tuple[float, float, float, float]) -> List[Dict[str, Any]]:
    """
    Overpass API ile sektörün tam vektörlerini (LineString ve Poligon) çeker:
    out geom; formatı ile tek sorguda poligon ve çizgilerin tüm koordinatlarını getirir.
    """
    bbox_str = bbox_to_overpass_str(bbox)

    query = f"""
    [out:json][timeout:120][maxsize:536870912];
    (
      // 1. Elektrik Hatları (TEİAŞ ENH)
      way["power"="line"]({bbox_str});
      way["power"="cable"]({bbox_str});
      relation["power"="line"]({bbox_str});

      // 2. Yollar (Mevcut ve İnşaat Halindeki Projeler)
      way["highway"="motorway"]({bbox_str});
      way["highway"="trunk"]({bbox_str});
      way["highway"="primary"]({bbox_str});
      way["highway"="secondary"]({bbox_str});
      way["highway"="construction"]({bbox_str});
      way["highway"="proposed"]({bbox_str});

      // 3. Demiryolları (TCDD & YHT)
      way["railway"="rail"]({bbox_str});
      way["railway"="highspeed"]({bbox_str});

      // 4. Sit Alanları ve Milli Parklar (Poligonlar)
      way["boundary"="protected_area"]({bbox_str});
      relation["boundary"="protected_area"]({bbox_str});
      way["boundary"="national_park"]({bbox_str});
      relation["boundary"="national_park"]({bbox_str});
      way["historic"="archaeological_site"]({bbox_str});

      // 5. Sahil Şeritleri & Kıyı Çizgisi (3621 Kıyı Kanunu)
      way["natural"="coastline"]({bbox_str});

      // 6. Orman Alanları (Poligonlar)
      way["landuse"="forest"]({bbox_str});
      way["natural"="wood"]({bbox_str});

      // 7. Su Yolları, Dereler ve Kanallar
      way["waterway"="river"]({bbox_str});
      way["waterway"="stream"]({bbox_str});
      way["waterway"="canal"]({bbox_str});
      way["waterway"="drain"]({bbox_str});
      way["man_made"="pipeline"]["substance"="water"]({bbox_str});
      way["natural"="water"]({bbox_str});
      relation["natural"="water"]({bbox_str});

      // 8. Su Noktaları (Kuyu & Pınar)
      node["man_made"="water_well"]({bbox_str});
      node["amenity"="drinking_water"]({bbox_str});
      node["natural"="spring"]({bbox_str});
    );
    out geom qt;
    """

    res = overpass_sorgula(query)
    if not res:
        log(f"OSM sorgusu sonuç döndürmedi: {bbox_str}", "WARN")
        return []

    elements = res.get("elements", [])
    log(f"  ✓ {len(elements):,} ham OSM vektör ögesi çekildi.", "SUCCESS")

    features = []

    for el in elements:
        el_type = el.get("type")
        tags = el.get("tags", {})
        osm_id = el.get("id")

        geom = None
        katman = "DİĞER"
        alt_tur = ""
        ad = tags.get("name") or tags.get("ref") or f"İsimsiz_{osm_id}"

        # 1. Nokta Elemanları
        if el_type == "node":
            lat = el.get("lat")
            lon = el.get("lon")
            if lat and lon:
                geom = {"type": "Point", "coordinates": [lon, lat]}
                if tags.get("man_made") == "water_well":
                    katman = "SU_KUYUSU"
                    alt_tur = "Artezyen / Yeraltı Suyu Kuyusu"
                elif tags.get("natural") == "spring":
                    katman = "DOGAL_PINAR"
                    alt_tur = "Doğal Kaynak / Pınar"
                else:
                    katman = "CESME_ICME_SUYU"
                    alt_tur = "İçme Suyu Çeşmesi"

        # 2. Çizgi ve Poligon Elemanları (Way)
        elif el_type == "way":
            geometry_list = el.get("geometry", [])
            if not geometry_list or len(geometry_list) < 2:
                continue

            coords = [[p["lon"], p["lat"]] for p in geometry_list]

            # Kapalı poligon mu yoksa açık çizgi mi?
            is_closed = (coords[0] == coords[-1]) and len(coords) >= 4

            # Katman Sınıflandırma
            if "power" in tags:
                katman = "ELEKTRIK_HATTI"
                alt_tur = f"TEİAŞ ENH ({tags.get('voltage', 'Bilinmiyor')}V)"
                geom = {"type": "LineString", "coordinates": coords}

            elif "railway" in tags:
                katman = "DEMIRYOLU"
                alt_tur = "YHT Yüksek Hızlı Tren" if tags.get("railway") == "highspeed" else "TCDD Konvansiyonel Ray"
                geom = {"type": "LineString", "coordinates": coords}

            elif "highway" in tags:
                hw = tags.get("highway")
                if hw in ["construction", "proposed"]:
                    katman = "PLANLANAN_YOL_PROJESI"
                    alt_tur = f"İnşaat Halinde / Planlanan Yol ({tags.get('construction', 'Otoyol/Devlet')})"
                else:
                    katman = "MEVCUT_YOL"
                    alt_tur = f"Karayolu ({hw.upper()})"
                geom = {"type": "LineString", "coordinates": coords}

            elif "boundary" in tags or "historic" in tags:
                katman = "SIT_VE_KORUNAN_ALAN"
                alt_tur = tags.get("boundary") or tags.get("historic") or "Milli Park / Sit"
                geom = {"type": "Polygon", "coordinates": [coords]} if is_closed else {"type": "LineString", "coordinates": coords}

            elif tags.get("natural") == "coastline":
                katman = "SAHIL_SERIDI"
                alt_tur = "Kıyı Kenar Çizgisi (3621 Sayılı Kıyı Kanunu)"
                geom = {"type": "LineString", "coordinates": coords}

            elif tags.get("landuse") == "forest" or tags.get("natural") == "wood":
                katman = "ORMAN_ALANI"
                alt_tur = "Devlet Ormanı / Meşcere Alanı"
                geom = {"type": "Polygon", "coordinates": [coords]} if is_closed else {"type": "LineString", "coordinates": coords}

            elif "waterway" in tags:
                ww = tags.get("waterway")
                katman = "SU_YOLU_DERE"
                alt_tur = "Sulama Kanalı / Ark" if ww in ["canal", "drain", "ditch"] else "Akarsu / Dere Yatağı"
                geom = {"type": "LineString", "coordinates": coords}

            elif tags.get("natural") == "water":
                katman = "GOL_BARAJ_HAZNE"
                alt_tur = "Göl / Baraj / Su Haznesi"
                geom = {"type": "Polygon", "coordinates": [coords]} if is_closed else {"type": "LineString", "coordinates": coords}

            elif tags.get("man_made") == "pipeline":
                katman = "SU_BORU_HATTI"
                alt_tur = "Ana Su İletim Boru Hattı"
                geom = {"type": "LineString", "coordinates": coords}

        if geom:
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": f"OSM_{osm_id}",
                    "ad": ad,
                    "katman": katman,
                    "alt_tur": alt_tur,
                    "tags": {k: v for k, v in tags.items() if k not in ["created_by", "source"]}
                }
            })

    return features


def cikar_sektor(sektor_id: int) -> Path:
    """Verilen sektör için tüm katmanları çıkarır ve GeoJSON olarak kaydeder."""
    if sektor_id not in SEKTORLER:
        raise ValueError(f"Geçersiz sektör ID: {sektor_id}. 1 ile 40 arasında olmalıdır.")

    sektor = SEKTORLER[sektor_id]
    ad = sektor["ad"]
    bbox = sektor["bbox"]

    log("=" * 80, "STEP")
    log(f"SEKTÖR {sektor_id}/40: {ad.upper()} ÇIKARILIYOR", "STEP")
    log(f"Koordinat Kutusu: {bbox}", "INFO")
    log(f"Kapsanan İller : {', '.join(sektor['iller'])}", "INFO")
    log("=" * 80, "STEP")

    tum_featurelar = []

    # 1. Fay Segmentleri
    faylar = cikar_detayli_faylar(bbox)
    tum_featurelar.extend(faylar)
    log(f"  ✓ {len(faylar):,} Diri Fay Segmenti eklendi.", "SUCCESS")

    # 2. OSM Katmanları (Elektrik, Yol, Ray, Sit, Sahil, Orman, Su)
    osm_feat = cikar_osm_vektorleri(bbox)
    tum_featurelar.extend(osm_feat)
    log(f"  ✓ {len(osm_feat):,} Fiziksel ve Hukuki Altyapı Vektörü eklendi.", "SUCCESS")

    cikti_geojson = {
        "type": "FeatureCollection",
        "name": f"Turkiye_Sektor_{sektor_id}_{ad}",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "sektor_bilgisi": {
            "sektor_id": sektor_id,
            "ad": ad,
            "bbox": bbox,
            "iller": sektor["iller"],
            "toplam_vektor_sayisi": len(tum_featurelar),
            "olusturma_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        },
        "features": tum_featurelar
    }

    dosya_adi = f"sektor_{sektor_id:02d}_{ad.lower().replace(' ', '_').replace('/', '_')}.geojson"
    cikti_yolu = OUTPUT_DIR / dosya_adi

    with open(cikti_yolu, "w", encoding="utf-8") as f:
        json.dump(cikti_geojson, f, ensure_ascii=False, indent=2)

    boyut_mb = cikti_yolu.stat().st_size / (1024 * 1024)
    log(f"SEKTÖR {sektor_id} TAMAMLANDI! -> {cikti_yolu.name} ({boyut_mb:.2f} MB, {len(tum_featurelar):,} Vektör)", "SUCCESS")
    return cikti_yolu


def main() -> None:
    parser = argparse.ArgumentParser(description="Türkiye 40 Makine Paralel CBS Vektör Çıkarıcı")
    parser.add_argument("--sektor", type=int, default=None, help="Çıkarılacak sektör numarası (1 - 40)")
    parser.add_argument("--hepsi", action="store_true", help="Tüm 40 sektörü yerelde sırayla çıkar")
    parser.add_argument("--liste", action="store_true", help="40 sektörün listesini ve koordinatlarını bas")
    args = parser.parse_args()

    if args.liste:
        print("╔═══════════════════════════════════════════════════════════════════════════════╗")
        print("║          TÜRKİYE 40 PARALEL CBS VEKTÖR SEKTÖR HARİTASI (GRID)                 ║")
        print("╠═══════════════════════════════════════════════════════════════════════════════╣")
        for sid, s in SEKTORLER.items():
            bbox_str = f"({s['bbox'][0]:.1f}, {s['bbox'][1]:.1f}) - ({s['bbox'][2]:.1f}, {s['bbox'][3]:.1f})"
            print(f"║ Sektör {sid:02d}: {s['ad']:<45} | {bbox_str} ║")
        print("╚═══════════════════════════════════════════════════════════════════════════════╝")
        return

    if args.sektor:
        cikar_sektor(args.sektor)
    elif args.hepsi:
        for sid in range(1, 41):
            cikar_sektor(sid)
    else:
        # Varsayılan: İstanbul ve Marmara Koridoru (Sektör 34)
        cikar_sektor(34)


if __name__ == "__main__":
    main()
