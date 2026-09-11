#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Arsa ve Arazi Su Altyapısı & Su Varlığı Toplayıcı ve Analiz Motoru
=============================================================================
"Arsada Su Var mı Yok mu?" Sorusunun 360 Derece Yanıtı:
  1. Şebeke İçme Suyu: En yakın meskûn mahal / yerleşim yeri şebeke mesafesi ve bağlantı maliyeti.
  2. Yeraltı Suyu & Artezyen Kuyu Potansiyeli: Hidrolojik vadi tabanı kot farkı, yakın kuyu envanteri.
  3. Doğal Pınar & Menba Varlığı: 1000m yarıçap içindeki tescilli doğal su kaynakları.
  4. Tarımsal Sulama Altyapısı: Sulama kanalları, drenaj arkları, baraj/gölet erişimi.
  5. Canlı Mikro Su Altyapısı Zenginleştirme: OSM çeşme, hayrat, kuyu ve su depolarının tespiti.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "turkiye_altyapi_ve_riskler.sqlite"
MAHALLE_KOORD_PATH = BASE_DIR / "mahalle_koordinatlari.json" if (BASE_DIR / "mahalle_koordinatlari.json").exists() else BASE_DIR / "data" / "mahalle_koordinatlari.json"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """İki coğrafi koordinat arasındaki kuş uçuşu mesafeyi metre cinsinden hesaplar."""
    r = 6371000.0  # Dünya yarıçapı (metre)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def get_bounding_box(lat: float, lon: float, radius_m: float) -> Tuple[float, float, float, float]:
    """Verilen merkez ve metre yarıçap için (min_lon, max_lon, min_lat, max_lat) döndürür."""
    lat_delta = radius_m / 111320.0
    lon_delta = radius_m / (111320.0 * max(0.1, math.cos(math.radians(lat))))
    return (lon - lon_delta, lon + lon_delta, lat - lat_delta, lat + lat_delta)


@dataclass
class HamSuVerisi:
    enlem: float
    boylam: float
    
    # 1. Şebeke Altyapısı (Meskûn Mahal Merkez Hattı)
    sebeke_yerlesim_adi: str
    sebeke_mesafe_m: float
    
    # 2. Yeraltı Suyu & Kuyu Envanteri
    en_yakin_kuyu_adi: Optional[str]
    en_yakin_kuyu_mesafe_m: Optional[float]
    en_yakin_kuyu_rakim_m: Optional[int]
    yari_cap_3km_kuyu_sayisi: int
    
    # 3. Doğal Pınar & Kaynak
    en_yakin_pinar_adi: Optional[str]
    en_yakin_pinar_mesafe_m: Optional[float]
    en_yakin_pinar_rakim_m: Optional[int]
    yari_cap_3km_pinar_sayisi: int
    
    # 4. Sulama Altyapısı (Kanal, Depo)
    en_yakin_kanal_adi: Optional[str]
    en_yakin_kanal_mesafe_m: Optional[float]
    en_yakin_su_deposu_adi: Optional[str]
    en_yakin_su_deposu_mesafe_m: Optional[float]
    
    # 5. Hidroloji & Su Yolları
    en_yakin_akarsu_adi: Optional[str]
    en_yakin_akarsu_mesafe_m: Optional[float]
    en_yakin_akarsu_rakim_m: Optional[int]
    akarsu_kot_farki_m: Optional[float]
    en_yakin_kuru_dere_adi: Optional[str]
    en_yakin_kuru_dere_mesafe_m: Optional[float]
    en_yakin_kuru_dere_rakim_m: Optional[int]
    kuru_dere_kot_farki_m: Optional[float]
    en_yakin_gol_baraj_adi: Optional[str]
    en_yakin_gol_baraj_mesafe_m: Optional[float]


@dataclass
class SuVarligiSonucu:
    enlem: float
    boylam: float
    arsa_su_durumu: str
    su_guvenlik_skoru: int
    sebeke_durumu: str
    en_yakin_sebeke_mesafe_m: float
    en_yakin_yerlesim_adi: str
    tahmini_sebeke_maliyeti_tl: str
    yeralti_suyu_potansiyeli: str
    en_yakin_kuyu_mesafe_m: Optional[float]
    en_yakin_kuyu_adi: Optional[str]
    tahmini_sondaj_derinligi_m: str
    dsi_ruhsat_durumu: str
    tarimsal_sulama_imkani: str
    en_yakin_kanal_mesafe_m: Optional[float]
    en_yakin_kanal_adi: Optional[str]
    dogal_pinar_var_mi: bool
    en_yakin_pinar_mesafe_m: Optional[float]
    en_yakin_pinar_adi: Optional[str]
    en_yakin_akarsu_mesafe_m: Optional[float]
    en_yakin_akarsu_adi: Optional[str]
    su_temin_onerisi: str
    tahmini_toplam_su_butcesi_tl: str


class ArsaSuAltyapiToplayici:
    """Arsa ve arazi su altyapısı verilerini yerel CBS veritabanından ve harita ağlarından sorgulayan motor."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        if not self.db_path.exists():
            raise FileNotFoundError(f"Altyapı veritabanı bulunamadı: {self.db_path}. Lütfen önce altyapi_veritabani_olusturucu.py çalıştırın.")

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def sorgula_en_yakin_meskun_mahal(self, lat: float, lon: float, arama_yaricapi_m: float = 15000.0) -> Tuple[Optional[str], float]:
        """En yakın meskûn mahal veya köy yerleşim merkezini R*Tree kullanarak bulur."""
        conn = self.get_connection()
        cur = conn.cursor()

        min_x, max_x, min_y, max_y = get_bounding_box(lat, lon, arama_yaricapi_m)

        cur.execute("""
        SELECT m.ad, m.enlem, m.boylam
        FROM rtree_meskun r
        JOIN meskun_mahaller m ON r.id = m.id
        WHERE r.minX >= ? AND r.maxX <= ? AND r.minY >= ? AND r.maxY <= ?
        """, (min_x, max_x, min_y, max_y))

        rows = cur.fetchall()
        conn.close()

        if not rows:
            return None, 999999.0

        en_yakin_ad = None
        min_mesafe = float("inf")

        for r in rows:
            dist = haversine_distance(lat, lon, r["enlem"], r["boylam"])
            if dist < min_mesafe:
                min_mesafe = dist
                en_yakin_ad = r["ad"]

        return en_yakin_ad, round(min_mesafe, 1)

    def sorgula_su_kaynaklari(self, lat: float, lon: float, arama_yaricapi_m: float = 10000.0) -> Dict[str, Any]:
        """Su altyapısı ve kaynakları tablosundan kuyu, pınar, kanal, depo envanterini çeker."""
        conn = self.get_connection()
        cur = conn.cursor()

        min_x, max_x, min_y, max_y = get_bounding_box(lat, lon, arama_yaricapi_m)

        cur.execute("""
        SELECT s.ad, s.tur_kodu, s.tur_aciklama, s.kategori, s.enlem, s.boylam, s.rakim_m
        FROM rtree_su_kaynaklari r
        JOIN su_altyapisi_ve_kaynaklar s ON r.id = s.id
        WHERE r.minX >= ? AND r.maxX <= ? AND r.minY >= ? AND r.maxY <= ?
        """, (min_x, max_x, min_y, max_y))

        rows = cur.fetchall()
        conn.close()

        sonuc: Dict[str, Any] = {
            "kuyular": [],
            "pinarlar": [],
            "kanallar": [],
            "depolar": [],
            "en_yakin_kuyu": None,
            "en_yakin_pinar": None,
            "en_yakin_kanal": None,
            "en_yakin_depo": None
        }

        for r in rows:
            dist = haversine_distance(lat, lon, r["enlem"], r["boylam"])
            item = {
                "ad": r["ad"],
                "tur_kodu": r["tur_kodu"],
                "tur_aciklama": r["tur_aciklama"],
                "kategori": r["kategori"],
                "enlem": r["enlem"],
                "boylam": r["boylam"],
                "rakim_m": r["rakim_m"],
                "mesafe_m": round(dist, 1)
            }

            if "KUYU" in r["kategori"]:
                sonuc["kuyular"].append(item)
            elif "PINAR" in r["kategori"]:
                sonuc["pinarlar"].append(item)
            elif "KANAL" in r["kategori"]:
                sonuc["kanallar"].append(item)
            elif "DEPO" in r["kategori"]:
                sonuc["depolar"].append(item)

        # Mesafeye göre sırala
        for k in ["kuyular", "pinarlar", "kanallar", "depolar"]:
            sonuc[k].sort(key=lambda x: x["mesafe_m"])

        sonuc["en_yakin_kuyu"] = sonuc["kuyular"][0] if sonuc["kuyular"] else None
        sonuc["en_yakin_pinar"] = sonuc["pinarlar"][0] if sonuc["pinarlar"] else None
        sonuc["en_yakin_kanal"] = sonuc["kanallar"][0] if sonuc["kanallar"] else None
        sonuc["en_yakin_depo"] = sonuc["depolar"][0] if sonuc["depolar"] else None

        return sonuc

    def sorgula_su_yollari(self, lat: float, lon: float, arama_yaricapi_m: float = 8000.0) -> Dict[str, Any]:
        """Nehir, dere, çay ve mevsimlik su yollarını çeker."""
        conn = self.get_connection()
        cur = conn.cursor()

        min_x, max_x, min_y, max_y = get_bounding_box(lat, lon, arama_yaricapi_m)

        cur.execute("""
        SELECT s.ad, s.tur_kodu, s.tur_aciklama, s.kategori, s.enlem, s.boylam, s.rakim_m
        FROM rtree_su_yollari r
        JOIN su_yollari_ve_dereler s ON r.id = s.id
        WHERE r.minX >= ? AND r.maxX <= ? AND r.minY >= ? AND r.maxY <= ?
        """, (min_x, max_x, min_y, max_y))

        rows = cur.fetchall()
        conn.close()

        akarsular = []
        kuru_dereler = []

        for r in rows:
            dist = haversine_distance(lat, lon, r["enlem"], r["boylam"])
            item = {
                "ad": r["ad"],
                "tur_kodu": r["tur_kodu"],
                "tur_aciklama": r["tur_aciklama"],
                "kategori": r["kategori"],
                "enlem": r["enlem"],
                "boylam": r["boylam"],
                "rakim_m": r["rakim_m"],
                "mesafe_m": round(dist, 1)
            }
            if r["kategori"] == "SU_YOLU_KURU_DERE":
                kuru_dereler.append(item)
            else:
                akarsular.append(item)

        akarsular.sort(key=lambda x: x["mesafe_m"])
        kuru_dereler.sort(key=lambda x: x["mesafe_m"])

        return {
            "en_yakin_akarsu": akarsular[0] if akarsular else None,
            "en_yakin_kuru_dere": kuru_dereler[0] if kuru_dereler else None,
            "toplam_akarsu_sayisi": len(akarsular),
            "toplam_kuru_dere_sayisi": len(kuru_dereler)
        }

    def zenginlestir_mikro_osm_noktalari(self, lat: float, lon: float, radius_m: float = 1200.0, timeout: int = 4) -> List[Dict[str, Any]]:
        """Parselin yakın çevresindeki (1.2 km) OSM köy çeşmesi, hayrat, kuyu ve su depolarını sorgular."""
        min_x, max_x, min_y, max_y = get_bounding_box(lat, lon, radius_m)
        query = f"""[out:json][timeout:{timeout}];
(
  node["amenity"="drinking_water"]({min_y:.4f},{min_x:.4f},{max_y:.4f},{max_x:.4f});
  node["man_made"="water_well"]({min_y:.4f},{min_x:.4f},{max_y:.4f},{max_x:.4f});
  node["man_made"="water_tap"]({min_y:.4f},{min_x:.4f},{max_y:.4f},{max_x:.4f});
  node["man_made"="water_tower"]({min_y:.4f},{min_x:.4f},{max_y:.4f},{max_x:.4f});
);
out body;
"""
        headers = {
            "User-Agent": "curl/8.7.1",
            "Accept": "*/*"
        }
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=data, headers=headers)

        eklenenler = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elements = data.get("elements", [])
                for el in elements:
                    el_lat = el.get("lat")
                    el_lon = el.get("lon")
                    tags = el.get("tags", {})
                    name = tags.get("name", "İsimsiz Su Noktası")
                    amenity = tags.get("amenity")
                    man_made = tags.get("man_made")

                    if amenity == "drinking_water":
                        tur_aciklama = f"Köy Çeşmesi / Hayrat ({name})"
                        kategori = "SU_ALTYAPISI_CESME"
                    elif man_made == "water_well":
                        tur_aciklama = f"Su Kuyusu / Artezyen ({name})"
                        kategori = "SU_ALTYAPISI_KUYU"
                    elif man_made == "water_tap":
                        tur_aciklama = f"Su Musluğu / Vana ({name})"
                        kategori = "SU_ALTYAPISI_VANA"
                    elif man_made == "water_tower":
                        tur_aciklama = f"Su Kulesi / Deposu ({name})"
                        kategori = "SU_ALTYAPISI_DEPO"
                    else:
                        continue

                    dist = haversine_distance(lat, lon, el_lat, el_lon)
                    eklenenler.append({
                        "ad": name,
                        "tur_aciklama": tur_aciklama,
                        "kategori": kategori,
                        "enlem": el_lat,
                        "boylam": el_lon,
                        "mesafe_m": round(dist, 1)
                    })
        except Exception:
            # Ağ zaman aşımında yerel veritabanı ile kesintisiz devam edilir
            pass

        return eklenenler

    def ham_su_verisi(self, lat: float, lon: float, arsa_rakim: Optional[float] = None) -> HamSuVerisi:
        """Herhangi bir yorum veya puan içermeyen, tamamen ham ölçüm ve mesafe verilerini döndürür."""
        yerlesim_adi, sebeke_mesafe = self.sorgula_en_yakin_meskun_mahal(lat, lon)
        kaynaklar = self.sorgula_su_kaynaklari(lat, lon, arama_yaricapi_m=10000.0)
        su_yollari = self.sorgula_su_yollari(lat, lon, arama_yaricapi_m=10000.0)

        en_yakin_kuyu = kaynaklar.get("en_yakin_kuyu")
        en_yakin_pinar = kaynaklar.get("en_yakin_pinar")
        en_yakin_kanal = kaynaklar.get("en_yakin_kanal")
        en_yakin_depo = kaynaklar.get("en_yakin_depo")

        en_yakin_akarsu = su_yollari.get("en_yakin_akarsu")
        en_yakin_kuru_dere = su_yollari.get("en_yakin_kuru_dere")

        kuyu_3km = sum(1 for k in kaynaklar.get("kuyular", []) if k["mesafe_m"] <= 3000.0)
        pinar_3km = sum(1 for p in kaynaklar.get("pinarlar", []) if p["mesafe_m"] <= 3000.0)

        akarsu_kot_farki = None
        if arsa_rakim is not None and en_yakin_akarsu and en_yakin_akarsu.get("rakim_m") is not None:
            akarsu_kot_farki = round(arsa_rakim - en_yakin_akarsu["rakim_m"], 1)

        kuru_dere_kot_farki = None
        if arsa_rakim is not None and en_yakin_kuru_dere and en_yakin_kuru_dere.get("rakim_m") is not None:
            kuru_dere_kot_farki = round(arsa_rakim - en_yakin_kuru_dere["rakim_m"], 1)

        en_yakin_gol = None
        gol_mesafe = None
        gol_ad = None
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            min_x, max_x, min_y, max_y = get_bounding_box(lat, lon, 15000.0)
            cur.execute("""
            SELECT s.ad, s.enlem, s.boylam
            FROM rtree_su_yollari r
            JOIN su_yollari_ve_dereler s ON r.id = s.id
            WHERE r.minX >= ? AND r.maxX <= ? AND r.minY >= ? AND r.maxY <= ?
              AND s.kategori IN ('SU_YOLU_GOL', 'SU_ALTYAPISI_DEPO')
            """, (min_x, max_x, min_y, max_y))
            gol_rows = cur.fetchall()
            conn.close()

            if gol_rows:
                min_g_dist = float("inf")
                for gr in gol_rows:
                    d = haversine_distance(lat, lon, gr["enlem"], gr["boylam"])
                    if d < min_g_dist:
                        min_g_dist = d
                        gol_ad = gr["ad"]
                gol_mesafe = round(min_g_dist, 1)
        except Exception:
            pass

        return HamSuVerisi(
            enlem=lat,
            boylam=lon,
            sebeke_yerlesim_adi=yerlesim_adi or "Tespit Edilemedi",
            sebeke_mesafe_m=round(sebeke_mesafe, 1),
            en_yakin_kuyu_adi=en_yakin_kuyu["ad"] if en_yakin_kuyu else None,
            en_yakin_kuyu_mesafe_m=en_yakin_kuyu["mesafe_m"] if en_yakin_kuyu else None,
            en_yakin_kuyu_rakim_m=en_yakin_kuyu["rakim_m"] if en_yakin_kuyu else None,
            yari_cap_3km_kuyu_sayisi=kuyu_3km,
            en_yakin_pinar_adi=en_yakin_pinar["ad"] if en_yakin_pinar else None,
            en_yakin_pinar_mesafe_m=en_yakin_pinar["mesafe_m"] if en_yakin_pinar else None,
            en_yakin_pinar_rakim_m=en_yakin_pinar["rakim_m"] if en_yakin_pinar else None,
            yari_cap_3km_pinar_sayisi=pinar_3km,
            en_yakin_kanal_adi=en_yakin_kanal["ad"] if en_yakin_kanal else None,
            en_yakin_kanal_mesafe_m=en_yakin_kanal["mesafe_m"] if en_yakin_kanal else None,
            en_yakin_su_deposu_adi=en_yakin_depo["ad"] if en_yakin_depo else None,
            en_yakin_su_deposu_mesafe_m=en_yakin_depo["mesafe_m"] if en_yakin_depo else None,
            en_yakin_akarsu_adi=en_yakin_akarsu["ad"] if en_yakin_akarsu else None,
            en_yakin_akarsu_mesafe_m=en_yakin_akarsu["mesafe_m"] if en_yakin_akarsu else None,
            en_yakin_akarsu_rakim_m=en_yakin_akarsu["rakim_m"] if en_yakin_akarsu else None,
            akarsu_kot_farki_m=akarsu_kot_farki,
            en_yakin_kuru_dere_adi=en_yakin_kuru_dere["ad"] if en_yakin_kuru_dere else None,
            en_yakin_kuru_dere_mesafe_m=en_yakin_kuru_dere["mesafe_m"] if en_yakin_kuru_dere else None,
            en_yakin_kuru_dere_rakim_m=en_yakin_kuru_dere["rakim_m"] if en_yakin_kuru_dere else None,
            kuru_dere_kot_farki_m=kuru_dere_kot_farki,
            en_yakin_gol_baraj_adi=gol_ad,
            en_yakin_gol_baraj_mesafe_m=gol_mesafe
        )

    def analiz_et(self, lat: float, lon: float, canli_osm_tara: bool = True) -> SuVarligiSonucu:
        """Arsanın su varlığı ve altyapı durumunu hesaplar."""
        # 1. En yakın meskûn mahal (Şebeke suyu temsilcisi)
        yerlesim_adi, sebeke_mesafe = self.sorgula_en_yakin_meskun_mahal(lat, lon)
        yerlesim_adi = yerlesim_adi or "En Yakın Yerleşim"

        # 2. Su kaynakları (Kuyu, Pınar, Kanal, Depo)
        kaynaklar = self.sorgula_su_kaynaklari(lat, lon)

        # 3. Su yolları (Akarsu, Dere)
        su_yollari = self.sorgula_su_yollari(lat, lon)

        # 4. Opsiyonel Canlı Mikro OSM Su Noktaları
        mikro_noktalar = []
        if canli_osm_tara:
            mikro_noktalar = self.zenginlestir_mikro_osm_noktalari(lat, lon, radius_m=1500.0)

        # Mikro noktalardan daha yakın kuyu/çeşme varsa güncelle
        en_yakin_kuyu = kaynaklar["en_yakin_kuyu"]
        en_yakin_pinar = kaynaklar["en_yakin_pinar"]
        en_yakin_kanal = kaynaklar["en_yakin_kanal"]
        en_yakin_akarsu = su_yollari["en_yakin_akarsu"]

        for mn in mikro_noktalar:
            if "KUYU" in mn["kategori"]:
                if not en_yakin_kuyu or mn["mesafe_m"] < en_yakin_kuyu["mesafe_m"]:
                    en_yakin_kuyu = mn
            elif "CESME" in mn["kategori"] or "PINAR" in mn["kategori"]:
                if not en_yakin_pinar or mn["mesafe_m"] < en_yakin_pinar["mesafe_m"]:
                    en_yakin_pinar = mn

        # --- ÇOK KATMANLI SU KARAR MATRİSİ ---
        su_skoru = 0

        # A) Şebeke Suyu Değerlendirmesi (Max 45 Puan)
        if sebeke_mesafe <= 150.0:
            sebeke_durumu = "Bitişik / Mevcut"
            sebeke_maliyet = "Düşük (15.000 - 30.000 TL Abone Bağlantı Harcı)"
            su_skoru += 45
        elif sebeke_mesafe <= 500.0:
            sebeke_durumu = "Yakın (Hat Çekilebilir)"
            maliyet_tahmini = int(sebeke_mesafe * 180 + 25000)
            sebeke_maliyet = f"Orta (~{maliyet_tahmini:,} TL Boru & Kazı Dahil)"
            su_skoru += 32
        elif sebeke_mesafe <= 1500.0:
            sebeke_durumu = "Orta Mesafe (Genişleme Bölgesi)"
            maliyet_tahmini = int(sebeke_mesafe * 180 + 35000)
            sebeke_maliyet = f"Yüksek (~{maliyet_tahmini:,} TL Özel İdare / Belediye İzni Şart)"
            su_skoru += 18
        else:
            sebeke_durumu = "Yok / Çok Uzak"
            sebeke_maliyet = "Münferit Hat Çekmek Ekonomik Değil (>250.000 TL)"
            su_skoru += 5

        # B) Yeraltı Suyu & Kuyu / Artezyen Potansiyeli (Max 30 Puan)
        # Akarsu / dere mesafesi taban suyu göstergesidir
        akarsu_dist = en_yakin_akarsu["mesafe_m"] if en_yakin_akarsu else 9999.0
        kuyu_dist = en_yakin_kuyu["mesafe_m"] if en_yakin_kuyu else 9999.0

        if akarsu_dist < 600.0 or kuyu_dist < 800.0:
            yeralti_potansiyeli = "Çok Yüksek (Sığ Akifer / Taban Suyu Güçlü)"
            sondaj_derinligi = "30 - 60 metre"
            su_skoru += 30
        elif akarsu_dist < 2000.0 or kuyu_dist < 2500.0:
            yeralti_potansiyeli = "Orta (Orta Derinlikte Akifer)"
            sondaj_derinligi = "60 - 110 metre"
            su_skoru += 20
        else:
            yeralti_potansiyeli = "Düşük / Derin Sondaj Gerekli"
            sondaj_derinligi = "120 - 200+ metre (Kuru çıkma riski var)"
            su_skoru += 10

        # C) Tarımsal Sulama Kanalı & Açık Su (Max 15 Puan)
        kanal_dist = en_yakin_kanal["mesafe_m"] if en_yakin_kanal else 9999.0
        if kanal_dist < 400.0:
            sulama_imkani = "Mevcut (Sulama Kanalı Yanında)"
            su_skoru += 15
        elif kanal_dist < 1500.0 or akarsu_dist < 400.0:
            sulama_imkani = "Yakın Çevrede (Motor / Pompa ile Çekilebilir)"
            su_skoru += 10
        else:
            sulama_imkani = "Yok / Kuru Tarım Bölgesi"
            su_skoru += 2

        # D) Doğal Pınar & Menba Varlığı (Max 10 Puan)
        pinar_dist = en_yakin_pinar["mesafe_m"] if en_yakin_pinar else 9999.0
        dogal_pinar_var = pinar_dist < 1000.0
        if dogal_pinar_var:
            su_skoru += 10
        elif pinar_dist < 2500.0:
            su_skoru += 5

        # Skor Sınırlandırma (0-100)
        su_skoru = max(5, min(100, su_skoru))

        # Genel Hüküm
        if sebeke_durumu == "Bitişik / Mevcut":
            genel_hukum = "💧 KESİN MEVCUT (Şebeke Suyu Bitişikte)"
            oneri = "Belediye / İSKİ / İZSU / ASKİ su idaresine başvuru yapılarak doğrudan sayaç ve abone bağlantısı yapılabilir."
            toplam_butce = "15.000 - 35.000 TL"
        elif su_skoru >= 65:
            genel_hukum = "🌱 YÜKSEK (Şebeke Yakın & Kuyu / Sondaj Açılabilir)"
            oneri = "Şebeke hattı 200-500m mesafededir veya 40-70m sondaj kuyusu açılarak kesintisiz tarımsal ve evsel su temin edilebilir."
            toplam_butce = "60.000 - 130.000 TL"
        elif su_skoru >= 40:
            genel_hukum = "🚜 ŞARTLI (Sondaj Kuyu Ruhsatı / Derin Kuyu Gerekir)"
            oneri = "Şebeke suyu uzak olduğundan DSİ'den Yeraltı Suyu Arama Ruhsatı alınarak 80-140m artezyen sondaj vurulmalıdır."
            toplam_butce = "120.000 - 240.000 TL"
        else:
            genel_hukum = "🏜️ KIRAÇ (Su Altyapısı Uzak / Susuz Arazi)"
            oneri = "Arazide yerleşik şebeke ve sığ kuyu suyu yoktur. Yağmur suyu toplama havuzu (sarnıç), kapalı su deposu ve tankerle su temini düşünülmelidir."
            toplam_butce = "Taşıma Su / Sarnıç Kurulumu (~50.000 - 90.000 TL)"

        # DSİ Ruhsat durumu
        dsi_durumu = "10 metre üzeri sondajlar için DSİ 167 Sayılı Yeraltı Suları Kanunu gereği Arama & Kullanma Ruhsatı gereklidir."

        return SuVarligiSonucu(
            enlem=lat,
            boylam=lon,
            arsa_su_durumu=genel_hukum,
            su_guvenlik_skoru=su_skoru,
            sebeke_durumu=sebeke_durumu,
            en_yakin_sebeke_mesafe_m=sebeke_mesafe,
            en_yakin_yerlesim_adi=yerlesim_adi,
            tahmini_sebeke_maliyeti_tl=sebeke_maliyet,
            yeralti_suyu_potansiyeli=yeralti_potansiyeli,
            en_yakin_kuyu_mesafe_m=en_yakin_kuyu["mesafe_m"] if en_yakin_kuyu else None,
            en_yakin_kuyu_adi=en_yakin_kuyu["ad"] if en_yakin_kuyu else None,
            tahmini_sondaj_derinligi_m=sondaj_derinligi,
            dsi_ruhsat_durumu=dsi_durumu,
            tarimsal_sulama_imkani=sulama_imkani,
            en_yakin_kanal_mesafe_m=en_yakin_kanal["mesafe_m"] if en_yakin_kanal else None,
            en_yakin_kanal_adi=en_yakin_kanal["ad"] if en_yakin_kanal else None,
            dogal_pinar_var_mi=dogal_pinar_var,
            en_yakin_pinar_mesafe_m=en_yakin_pinar["mesafe_m"] if en_yakin_pinar else None,
            en_yakin_pinar_adi=en_yakin_pinar["ad"] if en_yakin_pinar else None,
            en_yakin_akarsu_mesafe_m=en_yakin_akarsu["mesafe_m"] if en_yakin_akarsu else None,
            en_yakin_akarsu_adi=en_yakin_akarsu["ad"] if en_yakin_akarsu else None,
            su_temin_onerisi=oneri,
            tahmini_toplam_su_butcesi_tl=toplam_butce
        )


def format_su_raporu(s: SuVarligiSonucu) -> str:
    """Su durumu sonucunu terminal ve raporlama için renkli ve estetik formatlar."""
    lines = [
        "╔═══════════════════════════════════════════════════════════════════════════════╗",
        "║           GEOPROP AI - ARSA SU ALTYAPISI VE SU VARLIĞI RAPORU                 ║",
        "╠═══════════════════════════════════════════════════════════════════════════════╣",
        f"║ Koordinat: {s.enlem:.5f}, {s.boylam:.5f}                                               ║",
        f"║ ARSADA SU VAR MI?: {s.arsa_su_durumu:<48} ║",
        f"║ SU GÜVENLİK SKORU: {s.su_guvenlik_skoru}/100                                                ║",
        "╠═══════════════════════════════════════════════════════════════════════════════╣",
        "║ 1. ŞEBEKE İÇME SUYU DURUMU:                                                   ║",
        f"║   • Şebeke Durumu        : {s.sebeke_durumu:<46} ║",
        f"║   • En Yakın Meskûn Mahal: {s.en_yakin_yerlesim_adi} ({s.en_yakin_sebeke_mesafe_m:.0f} metre)",
        f"║   • Tahmini Bağlantı Maliyeti: {s.tahmini_sebeke_maliyeti_tl}",
        "║                                                                               ║",
        "║ 2. YERALTI SUYU & ARTEZYEN KUYU POTANSİYELİ:                                  ║",
        f"║   • Akifer Potansiyeli   : {s.yeralti_suyu_potansiyeli}",
        f"║   • Tahmini Sondaj Derinliği: {s.tahmini_sondaj_derinligi_m}",
        f"║   • En Yakın Kuyu / Artezyen: {s.en_yakin_kuyu_adi or 'Kayıt Yok'} ({f'{s.en_yakin_kuyu_mesafe_m:.0f} m' if s.en_yakin_kuyu_mesafe_m else 'Mevcut Değil'})",
        "║                                                                               ║",
        "║ 3. TARIMSAL SULAMA & DOĞAL SU KAYNAKLARI:                                     ║",
        f"║   • Sulama Kanalı İmkanı : {s.tarimsal_sulama_imkani}",
        f"║   • En Yakın Sulama Kanalı: {s.en_yakin_kanal_adi or 'Kayıt Yok'} ({f'{s.en_yakin_kanal_mesafe_m:.0f} m' if s.en_yakin_kanal_mesafe_m else 'Uzak'})",
        f"║   • Doğal Pınar / Menba  : {s.en_yakin_pinar_adi or 'Kayıt Yok'} ({f'{s.en_yakin_pinar_mesafe_m:.0f} m' if s.en_yakin_pinar_mesafe_m else 'Uzak'})",
        f"║   • En Yakın Akarsu / Dere: {s.en_yakin_akarsu_adi or 'Kayıt Yok'} ({f'{s.en_yakin_akarsu_mesafe_m:.0f} m' if s.en_yakin_akarsu_mesafe_m else 'Uzak'})",
        "╠═══════════════════════════════════════════════════════════════════════════════╣",
        "║ 4. EYLEM PLANI VE MALİYET REHBERİ:                                            ║",
        f"║   • Öneri : {s.su_temin_onerisi}",
        f"║   • Bütçe : {s.tahmini_toplam_su_butcesi_tl}",
        "╚═══════════════════════════════════════════════════════════════════════════════╝"
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arsa Su Altyapısı ve Su Varlığı Analizi")
    parser.add_argument("--lat", type=float, default=41.0740, help="Enlem (Örn: 41.0740)")
    parser.add_argument("--lon", type=float, default=28.2460, help="Boylam (Örn: 28.2460)")
    parser.add_argument("--no-osm", action="store_true", help="Canlı OSM mikro taramasını atla (yalnızca yerel DB)")
    parser.add_argument("--json", action="store_true", help="JSON formatında çıktı ver")
    args = parser.parse_args()

    toplayici = ArsaSuAltyapiToplayici()
    sonuc = toplayici.analiz_et(args.lat, args.lon, canli_osm_tara=not args.no_osm)

    if args.json:
        print(json.dumps(asdict(sonuc), ensure_ascii=False, indent=2))
    else:
        print(format_su_raporu(sonuc))
