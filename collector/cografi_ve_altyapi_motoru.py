#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - 1. Grup Arazi Coğrafi, Topoğrafik ve Altyapı Analiz Motoru
======================================================================
Herhangi bir arsa/parsel koordinatı (Enlem, Boylam) için 1. Grup Katmanları:
  1. Topoğrafya: Rakım, Eğim Açısı (°), Eğim Oranı (%), İnşaat Maliyet Etkisi
  2. Bakı (Aspect): Yön Açısı (0-360°), Pusula Yönü (Güney, Batı vb.), Güneşlenme Skoru
  3. Su Varlığı & Altyapısı ("Arsada Su Var mı?"): Şebeke, Kuyu/Sondaj, Sulama Kanalı
  4. Hidroloji & Taşkın Riski: En yakın akarsu, kuru dere yatağı, sel güvenlik bandı
  5. MTA Diri Fay Hatları & Sismik Risk: En yakın aktif fay, segment adı, deprem kuşağı
  6. Ulaşım & Şebeke Yakınlığı: En yakın meskûn mahal, elektrik/yol tampon mesafesi
  7. 1. Grup Birleşik Arazi Fiziksel Skoru (0 - 100)
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

# Aynı dizindeki Su Altyapısı Toplayıcısını dahil et
from arsa_su_altyapi_toplayici import ArsaSuAltyapiToplayici, SuVarligiSonucu, haversine_distance

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
DB_PATH = DATA_DIR / "turkiye_altyapi_ve_riskler.sqlite"


def point_to_line_segment_distance(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bir noktanın bir doğru parçasına (fay segmentine) olan en kısa mesafesini (metre) hesaplar."""
    # Küçük alanlar için düzlemsel izdüşüm projeksiyonu (Equirectangular)
    lat_rad = math.radians(lat)
    cos_lat = math.cos(lat_rad)

    x0 = lon * 111320.0 * cos_lat
    y0 = lat * 111320.0
    x1 = lon1 * 111320.0 * cos_lat
    y1 = lat1 * 111320.0
    x2 = lon2 * 111320.0 * cos_lat
    y2 = lat2 * 111320.0

    dx = x2 - x1
    dy = y2 - y1
    segment_len_sq = dx * dx + dy * dy

    if segment_len_sq == 0.0:
        return math.hypot(x0 - x1, y0 - y1)

    t = max(0.0, min(1.0, ((x0 - x1) * dx + (y0 - y1) * dy) / segment_len_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy

    return math.hypot(x0 - proj_x, y0 - proj_y)


@dataclass
class HamCografiVeri:
    enlem: float
    boylam: float
    
    # 1. Topoğrafya (DEM 30m)
    rakim_m: float
    egim_yuzde: float
    egim_derece: float
    baki_derece: float
    baki_kardinal: str
    grid_3x3_rakimlar: List[float]
    delta_z_3x3_m: float
    
    # 2. Su Altyapısı & Su Varlığı (Mesafe ve Varlıklar)
    sebeke_yerlesim_adi: str
    sebeke_mesafe_m: float
    en_yakin_kuyu_adi: Optional[str]
    en_yakin_kuyu_mesafe_m: Optional[float]
    en_yakin_kuyu_rakim_m: Optional[int]
    yari_cap_3km_kuyu_sayisi: int
    en_yakin_pinar_adi: Optional[str]
    en_yakin_pinar_mesafe_m: Optional[float]
    en_yakin_pinar_rakim_m: Optional[int]
    yari_cap_3km_pinar_sayisi: int
    en_yakin_kanal_adi: Optional[str]
    en_yakin_kanal_mesafe_m: Optional[float]
    en_yakin_su_deposu_adi: Optional[str]
    en_yakin_su_deposu_mesafe_m: Optional[float]
    
    # 3. Hidroloji & Su Yolları
    en_yakin_akarsu_adi: Optional[str]
    en_yakin_akarsu_mesafe_m: Optional[float]
    en_yakin_akarsu_rakim_m: Optional[int]
    akarsu_kot_farki_m: Optional[float]
    en_yakin_kuru_dere_adi: Optional[str]
    en_yakin_kuru_dere_mesafe_m: Optional[float]
    kuru_dere_kot_farki_m: Optional[float]
    en_yakin_gol_baraj_adi: Optional[str]
    en_yakin_gol_baraj_mesafe_m: Optional[float]
    
    # 4. Fay Hattı & Sismik Mesafe (Makro ve Detaylı 895 Segment)
    diri_fay_adi: str
    diri_fay_sistemi: str
    diri_fay_tipi: str
    diri_fay_mesafesi_km: float
    detayli_fay_id: str
    detayli_fay_sistemi: str
    detayli_fay_tipi: str
    detayli_fay_kayma_hizi_mm_yil: str
    detayli_fay_segment_uzunlugu_km: float
    detayli_fay_mesafesi_m: float
    detayli_fay_mesafesi_km: float


@dataclass
class TopografyaSonucu:
    rakim_m: float
    egim_yuzde: float
    egim_derece: float
    egim_sinifi: str              # Düz, Hafif, Orta, Dik, Sarp
    insaat_hafriyat_etkisi: str   # Minimum, Standart, Kademeli, Yüksek
    insaat_maliyet_carpani: float # 1.0x - 1.8x
    
    baki_derece: float            # 0 - 360°
    baki_yonu: str                # Güney, Güneydoğu, Kuzey vb.
    guneslenme_potansiyeli: str   # Mükemmel, Çok İyi, Orta, Düşük
    guneslenme_skoru: int         # 0 - 100 puan


@dataclass
class DepremVeFaySonucu:
    en_yakin_fay_adi: str
    fay_sistemi: str
    fay_tipi: str
    fay_mesafesi_km: float
    sismik_risk_derecesi: str     # 1. Derece Yüksek, 2. Derece Orta vb.
    sismik_guvenlik_puani: int    # 0 - 100 puan
    detayli_fay_id: Optional[str] = None
    detayli_fay_sistemi: Optional[str] = None
    detayli_fay_tipi: Optional[str] = None
    detayli_fay_kayma_hizi_mm_yil: Optional[str] = None
    detayli_fay_segment_uzunlugu_km: Optional[float] = None
    detayli_fay_mesafesi_m: Optional[float] = None
    detayli_fay_mesafesi_km: Optional[float] = None


@dataclass
class TaskinRiskiSonucu:
    en_yakin_akarsu_adi: Optional[str]
    akarsu_mesafe_m: Optional[float]
    en_yakin_kuru_dere_adi: Optional[str]
    kuru_dere_mesafe_m: Optional[float]
    taskin_riski_derecesi: str    # Güvenli, Düşük Risk, Orta Risk, Çok Yüksek Risk
    taskin_guvenlik_puani: int    # 0 - 100 puan


@dataclass
class ArsaTamCografiRapor:
    enlem: float
    boylam: float
    analiz_zamani: str
    
    # 1. Topoğrafya ve Bakı
    topografya: TopografyaSonucu
    
    # 2. Su Varlığı ve Altyapısı
    su_altyapisi: SuVarligiSonucu
    
    # 3. Taşkın ve Hidroloji
    taskin_riski: TaskinRiskiSonucu
    
    # 4. Sismik Risk ve Fay Mesafesi
    deprem_ve_fay: DepremVeFaySonucu
    
    # 5. Ulaşım ve Yerleşim
    en_yakin_yerlesim_adi: str
    yerlesim_mesafe_m: float
    
    # 6. Birleşik Fiziksel Arazi Puanı
    arazi_fiziksel_kalite_puani: int  # 0 - 100
    arazi_kalite_sinifi: str          # A+, A, B, C, D
    ozet_degerlendirme: str


class CografiVeAltyapiMotoru:
    """1. Grup arazi verilerini (Eğim, Bakı, Su, Fay, Taşkın) hesaplayan ana motor."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.su_toplayici = ArsaSuAltyapiToplayici(self.db_path)
        try:
            from turkiye_detayli_fay_veritabani_olusturucu import DetayliFaySorgulayici
            self.detayli_fay_sorgulayici = DetayliFaySorgulayici()
        except Exception:
            self.detayli_fay_sorgulayici = None

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def hesapla_topografya_ve_baki(self, lat: float, lon: float) -> TopografyaSonucu:
        """
        Open-Meteo DEM 30m 3x3 yükseklik matrisini çeker ve Horn's algoritması ile
        kesin Eğim (%), Eğim Açısı (°) ve Bakı Yönünü (0-360°) hesaplar.
        """
        step = 0.0003  # ~33 metre grid adımı
        lats = [
            lat + step, lat + step, lat + step,
            lat, lat, lat,
            lat - step, lat - step, lat - step
        ]
        lons = [
            lon - step, lon, lon + step,
            lon - step, lon, lon + step,
            lon - step, lon, lon + step
        ]

        lat_str = ",".join(f"{x:.6f}" for x in lats)
        lon_str = ",".join(f"{x:.6f}" for x in lons)
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat_str}&longitude={lon_str}"

        elevations = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (GEOPROP Topography Engine)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elevations = data.get("elevation")
        except Exception:
            pass

        # Ağ hatasında varsayılan düzlük modeli
        if not elevations or len(elevations) != 9:
            elevations = [100.0] * 9

        # 3x3 Grid Matrisi:
        # z00 (KD), z01 (K),  z02 (KB)
        # z10 (D),  z11 (M),  z12 (B)
        # z20 (GD), z21 (G),  z22 (GB)
        z00, z01, z02 = elevations[0], elevations[1], elevations[2]
        z10, z11, z12 = elevations[3], elevations[4], elevations[5]
        z20, z21, z22 = elevations[6], elevations[7], elevations[8]

        merkez_rakim = z11

        # Metre cinsinden piksel mesafeleri
        cos_lat = math.cos(math.radians(lat))
        dx = 111320.0 * cos_lat * step
        dy = 111320.0 * step

        # Horn's Topographic Formulas (1981):
        dz_dx = ((z02 + 2.0 * z12 + z22) - (z00 + 2.0 * z10 + z20)) / (8.0 * dx)
        dz_dy = ((z20 + 2.0 * z21 + z22) - (z00 + 2.0 * z01 + z02)) / (8.0 * dy)

        slope_rad = math.atan(math.sqrt(dz_dx * dz_dx + dz_dy * dz_dy))
        slope_deg = math.degrees(slope_rad)
        slope_pct = math.tan(slope_rad) * 100.0

        # Eğim Sınıflandırması & İnşaat Maliyeti
        if slope_pct <= 3.0:
            egim_sinifi = "Düz (%0 - %3)"
            hafriyat_etkisi = "Minimum (Hafriyatsız / Düşük Maliyet)"
            maliyet_carpani = 1.00
        elif slope_pct <= 8.0:
            egim_sinifi = "Hafif Eğimli (%3 - %8)"
            hafriyat_etkisi = "İdeal (Mükemmel Doğal Drenaj, Standart Temel)"
            maliyet_carpani = 1.05
        elif slope_pct <= 15.0:
            egim_sinifi = "Orta Meyilli (%8 - %15)"
            hafriyat_etkisi = "Kademeli / Teraslama (Manzara Avantajı, Orta Hafriyat)"
            maliyet_carpani = 1.20
        elif slope_pct <= 25.0:
            egim_sinifi = "Fazla Eğimli (%15 - %25)"
            hafriyat_etkisi = "Yüksek (İstinat Duvarı ve Dolgu/Kazı Zorunlu)"
            maliyet_carpani = 1.45
        else:
            egim_sinifi = "Çok Dik / Sarp (> %25)"
            hafriyat_etkisi = "Kritik (Ağır Zemin Güçlendirme / Heyelan Riski)"
            maliyet_carpani = 1.80

        # Bakı (Aspect) Hesabı (Kuzey = 0°, Doğu = 90°, Güney = 180°, Batı = 270°)
        if dz_dx == 0.0 and dz_dy == 0.0:
            aspect_deg = 180.0  # Düz zemin
        else:
            aspect_deg = 180.0 + math.degrees(math.atan2(dz_dy, -dz_dx))
            if aspect_deg >= 360.0:
                aspect_deg -= 360.0
            elif aspect_deg < 0.0:
                aspect_deg += 360.0

        # Pusula Yönü ve Güneşlenme Değerlendirmesi
        if slope_pct < 1.0:
            baki_yonu = "Düz Zemin (Açık Bakı)"
            guneslenme = "Mükemmel (Tüm Gün Güneş Alır)"
            gunes_skoru = 95
        elif 337.5 <= aspect_deg or aspect_deg < 22.5:
            baki_yonu = "Kuzey (K)"
            guneslenme = "Düşük (Kışın Soğuk / Don Riski Yüksek)"
            gunes_skoru = 40
        elif 22.5 <= aspect_deg < 67.5:
            baki_yonu = "Kuzeydoğu (KD)"
            guneslenme = "Orta (Sabah Güneşi / Serin Cephe)"
            gunes_skoru = 60
        elif 67.5 <= aspect_deg < 112.5:
            baki_yonu = "Doğu (D)"
            guneslenme = "Çok İyi (Tarımsal Verim Yüksek / Sabah Işığı)"
            gunes_skoru = 80
        elif 112.5 <= aspect_deg < 157.5:
            baki_yonu = "Güneydoğu (GD)"
            guneslenme = "Mükemmel (Bağ/Bahçe ve Konut İçin Optimum)"
            gunes_skoru = 95
        elif 157.5 <= aspect_deg < 202.5:
            baki_yonu = "Güney (G)"
            guneslenme = "Zirve (Maksimum Güneşlenme / Isınma Tasarrufu)"
            gunes_skoru = 100
        elif 202.5 <= aspect_deg < 247.5:
            baki_yonu = "Güneybatı (GB)"
            guneslenme = "Çok Yüksek (Sıcak Cephe / Uzun Güneşlenme)"
            gunes_skoru = 90
        elif 247.5 <= aspect_deg < 292.5:
            baki_yonu = "Batı (B)"
            guneslenme = "İyi (Öğleden Sonra Güneşi)"
            gunes_skoru = 75
        else:
            baki_yonu = "Kuzeybatı (KB)"
            guneslenme = "Orta-Düşük (Rüzgar ve Fırtına Cephesi)"
            gunes_skoru = 50

        return TopografyaSonucu(
            rakim_m=round(merkez_rakim, 1),
            egim_yuzde=round(slope_pct, 1),
            egim_derece=round(slope_deg, 1),
            egim_sinifi=egim_sinifi,
            insaat_hafriyat_etkisi=hafriyat_etkisi,
            insaat_maliyet_carpani=maliyet_carpani,
            baki_derece=round(aspect_deg, 1),
            baki_yonu=baki_yonu,
            guneslenme_potansiyeli=guneslenme,
            guneslenme_skoru=gunes_skoru
        )

    def hesapla_fay_ve_sismik_risk(self, lat: float, lon: float) -> DepremVeFaySonucu:
        """Türkiye Diri Fay Hatları tablosunu sorgulayarak en yakın fay segmentini ve riskini belirler."""
        conn = self._get_connection()
        cur = conn.cursor()

        # 80 km yarıçaptaki fayları tara
        delta = 80000.0 / 111320.0
        cur.execute("""
        SELECT f.ad, f.sistem, f.tip, f.lat1, f.lon1, f.lat2, f.lon2
        FROM rtree_faylar r
        JOIN diri_fay_hatlari f ON r.id = f.id
        WHERE r.minX <= ? AND r.maxX >= ? AND r.minY <= ? AND r.maxY >= ?
        """, (lon + delta, lon - delta, lat + delta, lat - delta))

        rows = cur.fetchall()

        # Eğer R*Tree boşsa tüm fayları doğrudan incele
        if not rows:
            cur.execute("SELECT ad, sistem, tip, lat1, lon1, lat2, lon2 FROM diri_fay_hatlari;")
            rows = cur.fetchall()

        conn.close()

        min_mesafe_m = float("inf")
        secilen_fay = None

        for r in rows:
            dist = point_to_line_segment_distance(lat, lon, r["lat1"], r["lon1"], r["lat2"], r["lon2"])
            if dist < min_mesafe_m:
                min_mesafe_m = dist
                secilen_fay = r

        fay_km = round(min_mesafe_m / 1000.0, 1)

        # Detaylı Diri Fay Sorgusu (895 Segment)
        detayli_res = None
        if self.detayli_fay_sorgulayici:
            try:
                detayli_res = self.detayli_fay_sorgulayici.en_yakin_fay_sorgula(lat, lon)
            except Exception:
                pass

        detayli_id = detayli_res.get("catalog_id") if detayli_res else None
        detayli_sis = detayli_res.get("fay_sistemi") if detayli_res else None
        detayli_tip = detayli_res.get("slip_type") if detayli_res else None
        detayli_hiz = detayli_res.get("net_slip_rate_mm_yil") if detayli_res else None
        detayli_uzun = detayli_res.get("fay_uzunluk_km") if detayli_res else None
        detayli_m = detayli_res.get("mesafe_m") if detayli_res else None
        detayli_km = detayli_res.get("mesafe_km") if detayli_res else None

        if not secilen_fay or fay_km > 150.0:
            return DepremVeFaySonucu(
                en_yakin_fay_adi="Ana Diri Fay Hattından Uzak",
                fay_sistemi="Düşük Sismik Kuşak",
                fay_tipi="Masif / Kararlı Zemin",
                fay_mesafesi_km=fay_km,
                sismik_risk_derecesi="4. Derece Düşük Sismik Risk",
                sismik_guvenlik_puani=95,
                detayli_fay_id=detayli_id,
                detayli_fay_sistemi=detayli_sis,
                detayli_fay_tipi=detayli_tip,
                detayli_fay_kayma_hizi_mm_yil=detayli_hiz,
                detayli_fay_segment_uzunlugu_km=detayli_uzun,
                detayli_fay_mesafesi_m=detayli_m,
                detayli_fay_mesafesi_km=detayli_km
            )

        if fay_km <= 5.0:
            risk_derece = "1. Derece Kritik Sismik Risk (Fay Sakınım Bandı Yakını)"
            guvenlik_puani = 25
        elif fay_km <= 15.0:
            risk_derece = "1. Derece Yüksek Sismik Risk (Fay Etki Alanı)"
            guvenlik_puani = 50
        elif fay_km <= 35.0:
            risk_derece = "2. Derece Orta Sismik Risk"
            guvenlik_puani = 70
        elif fay_km <= 70.0:
            risk_derece = "3. Derece Düşük-Orta Sismik Risk"
            guvenlik_puani = 85
        else:
            risk_derece = "4. Derece Güvenli Bölge"
            guvenlik_puani = 95

        return DepremVeFaySonucu(
            en_yakin_fay_adi=secilen_fay["ad"],
            fay_sistemi=secilen_fay["sistem"],
            fay_tipi=secilen_fay["tip"] or "Doğrultu Atımlı",
            fay_mesafesi_km=fay_km,
            sismik_risk_derecesi=risk_derece,
            sismik_guvenlik_puani=guvenlik_puani,
            detayli_fay_id=detayli_id,
            detayli_fay_sistemi=detayli_sis,
            detayli_fay_tipi=detayli_tip,
            detayli_fay_kayma_hizi_mm_yil=detayli_hiz,
            detayli_fay_segment_uzunlugu_km=detayli_uzun,
            detayli_fay_mesafesi_m=detayli_m,
            detayli_fay_mesafesi_km=detayli_km
        )

    def hesapla_taskin_riski(self, lat: float, lon: float) -> TaskinRiskiSonucu:
        """Akarsu ve kuru dere yataklarına olan mesafeye göre sel/taşkın riskini belirler."""
        su_yollari = self.su_toplayici.sorgula_su_yollari(lat, lon, arama_yaricapi_m=10000.0)

        en_yakin_akarsu = su_yollari.get("en_yakin_akarsu")
        en_yakin_kuru_dere = su_yollari.get("en_yakin_kuru_dere")

        akarsu_m = en_yakin_akarsu["mesafe_m"] if en_yakin_akarsu else 9999.0
        kuru_dere_m = en_yakin_kuru_dere["mesafe_m"] if en_yakin_kuru_dere else 9999.0

        min_mesafe = min(akarsu_m, kuru_dere_m)

        if min_mesafe <= 60.0:
            risk = "🚨 ÇOK YÜKSEK TAŞKIN RİSKİ (Dere Yatağı Emniyet Bandı İçinde)"
            puan = 20
        elif min_mesafe <= 150.0:
            risk = "⚠️ ORTA DERECELİ TAŞKIN RİSKİ (DSİ Sedde & Kot Güvenliği Gerekli)"
            puan = 55
        elif min_mesafe <= 400.0:
            risk = "🟡 DÜŞÜK TAŞKIN RİSKİ (Aşırı Yağışlarda Taban Suyu Takip Edilmeli)"
            puan = 80
        else:
            risk = "✅ GÜVENLİ (Dere Taşkın Etki Alanı Dışında)"
            puan = 100

        return TaskinRiskiSonucu(
            en_yakin_akarsu_adi=en_yakin_akarsu["ad"] if en_yakin_akarsu else None,
            akarsu_mesafe_m=akarsu_m if en_yakin_akarsu else None,
            en_yakin_kuru_dere_adi=en_yakin_kuru_dere["ad"] if en_yakin_kuru_dere else None,
            kuru_dere_mesafe_m=kuru_dere_m if en_yakin_kuru_dere else None,
            taskin_riski_derecesi=risk,
            taskin_guvenlik_puani=puan
        )

    def ham_analiz_et(self, lat: float, lon: float) -> HamCografiVeri:
        """Sadece ve sadece ham coğrafi, topoğrafik, hidrolojik ve fay mesafe verilerini çeker."""
        step = 0.0003
        lats = [
            lat + step, lat + step, lat + step,
            lat, lat, lat,
            lat - step, lat - step, lat - step
        ]
        lons = [
            lon - step, lon, lon + step,
            lon - step, lon, lon + step,
            lon - step, lon, lon + step
        ]
        lat_str = ",".join(f"{x:.6f}" for x in lats)
        lon_str = ",".join(f"{x:.6f}" for x in lons)
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat_str}&longitude={lon_str}"

        elevations = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (GEOPROP Topography Engine)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elevations = data.get("elevation")
        except Exception:
            pass

        if not elevations or len(elevations) != 9:
            elevations = [100.0] * 9

        z00, z01, z02 = elevations[0], elevations[1], elevations[2]
        z10, z11, z12 = elevations[3], elevations[4], elevations[5]
        z20, z21, z22 = elevations[6], elevations[7], elevations[8]
        merkez_rakim = z11

        cos_lat = math.cos(math.radians(lat))
        dx = 111320.0 * cos_lat * step
        dy = 111320.0 * step

        dz_dx = ((z02 + 2.0 * z12 + z22) - (z00 + 2.0 * z10 + z20)) / (8.0 * dx)
        dz_dy = ((z20 + 2.0 * z21 + z22) - (z00 + 2.0 * z01 + z02)) / (8.0 * dy)

        slope_rad = math.atan(math.sqrt(dz_dx * dz_dx + dz_dy * dz_dy))
        slope_deg = math.degrees(slope_rad)
        slope_pct = math.tan(slope_rad) * 100.0

        if dz_dx == 0.0 and dz_dy == 0.0:
            aspect_deg = 180.0
        else:
            aspect_deg = 180.0 + math.degrees(math.atan2(dz_dy, -dz_dx))
            if aspect_deg >= 360.0:
                aspect_deg -= 360.0
            elif aspect_deg < 0.0:
                aspect_deg += 360.0

        kardinal_yonler = ["K", "KD", "D", "GD", "G", "GB", "B", "KB"]
        idx = int((aspect_deg + 22.5) // 45) % 8
        baki_kardinal = kardinal_yonler[idx]

        # Ham Su Verileri
        su = self.su_toplayici.ham_su_verisi(lat, lon, arsa_rakim=merkez_rakim)

        # Ham Fay Verileri
        fay = self.hesapla_fay_ve_sismik_risk(lat, lon)

        return HamCografiVeri(
            enlem=lat,
            boylam=lon,
            rakim_m=round(merkez_rakim, 1),
            egim_yuzde=round(slope_pct, 2),
            egim_derece=round(slope_deg, 2),
            baki_derece=round(aspect_deg, 1),
            baki_kardinal=baki_kardinal,
            grid_3x3_rakimlar=elevations,
            delta_z_3x3_m=round(max(elevations) - min(elevations), 1),
            sebeke_yerlesim_adi=su.sebeke_yerlesim_adi,
            sebeke_mesafe_m=su.sebeke_mesafe_m,
            en_yakin_kuyu_adi=su.en_yakin_kuyu_adi,
            en_yakin_kuyu_mesafe_m=su.en_yakin_kuyu_mesafe_m,
            en_yakin_kuyu_rakim_m=su.en_yakin_kuyu_rakim_m,
            yari_cap_3km_kuyu_sayisi=su.yari_cap_3km_kuyu_sayisi,
            en_yakin_pinar_adi=su.en_yakin_pinar_adi,
            en_yakin_pinar_mesafe_m=su.en_yakin_pinar_mesafe_m,
            en_yakin_pinar_rakim_m=su.en_yakin_pinar_rakim_m,
            yari_cap_3km_pinar_sayisi=su.yari_cap_3km_pinar_sayisi,
            en_yakin_kanal_adi=su.en_yakin_kanal_adi,
            en_yakin_kanal_mesafe_m=su.en_yakin_kanal_mesafe_m,
            en_yakin_su_deposu_adi=su.en_yakin_su_deposu_adi,
            en_yakin_su_deposu_mesafe_m=su.en_yakin_su_deposu_mesafe_m,
            en_yakin_akarsu_adi=su.en_yakin_akarsu_adi,
            en_yakin_akarsu_mesafe_m=su.en_yakin_akarsu_mesafe_m,
            en_yakin_akarsu_rakim_m=su.en_yakin_akarsu_rakim_m,
            akarsu_kot_farki_m=su.akarsu_kot_farki_m,
            en_yakin_kuru_dere_adi=su.en_yakin_kuru_dere_adi,
            en_yakin_kuru_dere_mesafe_m=su.en_yakin_kuru_dere_mesafe_m,
            kuru_dere_kot_farki_m=su.kuru_dere_kot_farki_m,
            en_yakin_gol_baraj_adi=su.en_yakin_gol_baraj_adi,
            en_yakin_gol_baraj_mesafe_m=su.en_yakin_gol_baraj_mesafe_m,
            diri_fay_adi=fay.en_yakin_fay_adi,
            diri_fay_sistemi=fay.fay_sistemi,
            diri_fay_tipi=fay.fay_tipi,
            diri_fay_mesafesi_km=fay.fay_mesafesi_km,
            detayli_fay_id=fay.detayli_fay_id or "",
            detayli_fay_sistemi=fay.detayli_fay_sistemi or "",
            detayli_fay_tipi=fay.detayli_fay_tipi or "",
            detayli_fay_kayma_hizi_mm_yil=fay.detayli_fay_kayma_hizi_mm_yil or "",
            detayli_fay_segment_uzunlugu_km=fay.detayli_fay_segment_uzunlugu_km or 0.0,
            detayli_fay_mesafesi_m=fay.detayli_fay_mesafesi_m or 0.0,
            detayli_fay_mesafesi_km=fay.detayli_fay_mesafesi_km or 0.0
        )

    def analiz_et(self, lat: float, lon: float, canli_osm_tara: bool = False) -> ArsaTamCografiRapor:
        """Verilen koordinat için tüm 1. Grup arazi ve su altyapı analizlerini tek seferde yürütür."""
        # 1. Topoğrafya ve Bakı
        topo = self.hesapla_topografya_ve_baki(lat, lon)

        # 2. Su Varlığı ve Altyapısı ("Arsada Su Var mı?")
        su = self.su_toplayici.analiz_et(lat, lon, canli_osm_tara=canli_osm_tara)

        # 3. Taşkın ve Hidroloji
        taskin = self.hesapla_taskin_riski(lat, lon)

        # 4. Sismik Risk ve Fay Mesafesi
        fay = self.hesapla_fay_ve_sismik_risk(lat, lon)

        # 5. Ulaşım ve Meskûn Mahal
        yerlesim_adi, yerlesim_mesafe = self.su_toplayici.sorgula_en_yakin_meskun_mahal(lat, lon)
        yerlesim_adi = yerlesim_adi or "En Yakın Yerleşim"

        # --- 6. 1. GRUP BİRLEŞİK ARAZİ FİZİKSEL PUANI (0 - 100) ---
        # Ağırlık Dağılımı:
        #   • Su Varlığı & Altyapısı: %35
        #   • Eğim & Zemin Kolaylığı: %25
        #   • Bakı & Güneşlenme     : %15
        #   • Taşkın & Sel Güvenliği: %15
        #   • Deprem & Fay Güvenliği: %10

        # Eğim Puanı
        if topo.egim_yuzde <= 3.0:
            egim_puani = 100
        elif topo.egim_yuzde <= 8.0:
            egim_puani = 90
        elif topo.egim_yuzde <= 15.0:
            egim_puani = 75
        elif topo.egim_yuzde <= 25.0:
            egim_puani = 50
        else:
            egim_puani = 30

        toplam_puan = int(
            su.su_guvenlik_skoru * 0.35 +
            egim_puani * 0.25 +
            topo.guneslenme_skoru * 0.15 +
            taskin.taskin_guvenlik_puani * 0.15 +
            fay.sismik_guvenlik_puani * 0.10
        )
        toplam_puan = max(10, min(100, toplam_puan))

        if toplam_puan >= 85:
            sinif = "A+ MÜKEMMEL ARAZİ"
            ozet = "Altyapısı hazır, ideal düzlükte/hafif meyilli, güney cepheli ve doğal risklerden arındırılmış birinci sınıf arsa."
        elif toplam_puan >= 70:
            sinif = "A ÇOK İYİ ARAZİ"
            ozet = "Su imkanları güçlü, topoğrafik yapısı konut ve tarım için elverişli, yüksek değerleme potansiyeline sahip arazi."
        elif toplam_puan >= 55:
            sinif = "B STANDART ARAZİ"
            ozet = "Geliştirilebilir arsa; altyapı hattı çekilmesi veya sondaj kuyu çalışması yapılması gerekir."
        elif toplam_puan >= 40:
            sinif = "C ZORLU ARAZİ"
            ozet = "Eğim veya su altyapısı eksikliği nedeniyle başlangıç sermayesi/hafriyat harcaması gerektiren mülk."
        else:
            sinif = "D RİSKLİ / KIRAÇ ARAZİ"
            ozet = "Su temini zor, sarp zemin veya fay/taşkın tampon bölgesinde yer alan, yüksek yatırım riski taşıyan arazi."

        return ArsaTamCografiRapor(
            enlem=lat,
            boylam=lon,
            analiz_zamani=datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            topografya=topo,
            su_altyapisi=su,
            taskin_riski=taskin,
            deprem_ve_fay=fay,
            en_yakin_yerlesim_adi=yerlesim_adi,
            yerlesim_mesafe_m=round(yerlesim_mesafe, 1),
            arazi_fiziksel_kalite_puani=toplam_puan,
            arazi_kalite_sinifi=sinif,
            ozet_degerlendirme=ozet
        )


def format_tam_cografi_rapor(r: ArsaTamCografiRapor) -> str:
    """Tam 1. Grup analiz raporunu görsel ve zenginleştirilmiş formatta terminale basar."""
    fay = r.deprem_ve_fay
    lines = [
        "╔═══════════════════════════════════════════════════════════════════════════════════════╗",
        "║              GEOPROP AI - 1. GRUP ARAZİ VE FİZİKSEL ALTYAPI ANALİZİ                   ║",
        "╠═══════════════════════════════════════════════════════════════════════════════════════╣",
        f"║ Koordinat    : {r.enlem:.5f}, {r.boylam:.5f}                                              ║",
        f"║ Analiz Tarihi: {r.analiz_zamani:<70} ║",
        f"║ GENEL NOT    : {r.arazi_kalite_sinifi:<70} ║",
        f"║ FİZİKSEL PUAN: {r.arazi_fiziksel_kalite_puani}/100                                                               ║",
        "╠═══════════════════════════════════════════════════════════════════════════════════════╣",
        "║ 1. TOPOĞRAFYA, EĞİM VE BAKI (GÜNEŞLENME) ANALİZİ:                                     ║",
        f"║   • Rakım (Kot)          : {r.topografya.rakim_m} metre (Deniz Seviyesinden)",
        f"║   • Eğim Oranı / Açısı   : %{r.topografya.egim_yuzde} ({r.topografya.egim_derece}°)",
        f"║   • Zemin Sınıfı         : {r.topografya.egim_sinifi}",
        f"║   • Hafriyat / Temel Etki: {r.topografya.insaat_hafriyat_etkisi}",
        f"║   • Bakı (Pusula Yönü)   : {r.topografya.baki_yonu} ({r.topografya.baki_derece}°)",
        f"║   • Güneşlenme Potansiyeli: {r.topografya.guneslenme_potansiyeli} (Puan: {r.topografya.guneslenme_skoru}/100)",
        "║                                                                                       ║",
        "║ 2. SU VARLIĞI VE ALTYAPISI (\"ARSADA SU VAR MI?\"):                                    ║",
        f"║   • Su Durumu Kararı     : {r.su_altyapisi.arsa_su_durumu}",
        f"║   • Su Güvenlik Skoru    : {r.su_altyapisi.su_guvenlik_skoru}/100",
        f"║   • Şebeke İçme Suyu     : {r.su_altyapisi.sebeke_durumu} (Mesafe: {r.su_altyapisi.en_yakin_sebeke_mesafe_m:.0f} m, {r.su_altyapisi.en_yakin_yerlesim_adi})",
        f"║   • Şebeke Maliyeti      : {r.su_altyapisi.tahmini_sebeke_maliyeti_tl}",
        f"║   • Yeraltı Suyu / Sondaj: {r.su_altyapisi.yeralti_suyu_potansiyeli} ({r.su_altyapisi.tahmini_sondaj_derinligi_m})",
        f"║   • Sulama Kanalı        : {r.su_altyapisi.tarimsal_sulama_imkani}",
        f"║   • Su Eylem Önerisi     : {r.su_altyapisi.su_temin_onerisi}",
        "║                                                                                       ║",
        "║ 3. DOĞAL RİSKLER (TAŞKIN, SEL VE AKTİF FAY ANALİZİ):                                  ║",
        f"║   • Taşkın & Sel Güvenliği: {r.taskin_riski.taskin_riski_derecesi}",
        f"║   • En Yakın Akarsu / Dere : {r.taskin_riski.en_yakin_akarsu_adi or 'Uzak'} ({f'{r.taskin_riski.akarsu_mesafe_m:.0f} m' if r.taskin_riski.akarsu_mesafe_m else 'Kayıt Yok'})",
        f"║   • En Yakın Kuru Dere     : {r.taskin_riski.en_yakin_kuru_dere_adi or 'Uzak'} ({f'{r.taskin_riski.kuru_dere_mesafe_m:.0f} m' if r.taskin_riski.kuru_dere_mesafe_m else 'Kayıt Yok'})",
        f"║   • Diri Fay Segmenti      : {fay.en_yakin_fay_adi} ({fay.fay_mesafesi_km} km)",
        f"║   • Sismik Kuşak & Sistem  : {fay.fay_sistemi} - {fay.sismik_risk_derecesi}",
        f"║   • DETAYLI DİRİ FAY       : {fay.detayli_fay_id or 'Kayıt Yok'} ({fay.detayli_fay_sistemi or ''})",
        f"║     - Mekanizma / Tip      : {fay.detayli_fay_tipi or 'Bilinmiyor'}",
        f"║     - Yıllık Kayma Hızı    : {fay.detayli_fay_kayma_hizi_mm_yil or '-'} mm/yıl",
        f"║     - Segment Uzunluğu     : {fay.detayli_fay_segment_uzunlugu_km or '-'} km",
        f"║     - Parsel Fay Mesafesi  : {fay.detayli_fay_mesafesi_km or '-'} km ({fay.detayli_fay_mesafesi_m or '-'} m)",
        "║                                                                                       ║",
        "║ 4. ULAŞIM VE ÇEVRESEL YERLEŞİM:                                                       ║",
        f"║   • En Yakın Meskûn Mahal  : {r.en_yakin_yerlesim_adi} ({r.yerlesim_mesafe_m:.0f} metre)",
        "╠═══════════════════════════════════════════════════════════════════════════════════════╣",
        f"║ SONUÇ & TAVSİYE: {r.ozet_degerlendirme:<68} ║",
        "╚═══════════════════════════════════════════════════════════════════════════════════════╝"
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1. Grup Arazi Fiziksel ve Su Altyapısı Motoru")
    parser.add_argument("--lat", type=float, default=41.0740, help="Enlem")
    parser.add_argument("--lon", type=float, default=28.2460, help="Boylam")
    parser.add_argument("--osm", action="store_true", help="Canlı OSM mikro su taramasını aç")
    parser.add_argument("--json", action="store_true", help="JSON çıktısı ver")
    args = parser.parse_args()

    motor = CografiVeAltyapiMotoru()
    rapor = motor.analiz_et(args.lat, args.lon, canli_osm_tara=args.osm)

    if args.json:
        print(json.dumps(asdict(rapor), ensure_ascii=False, indent=2))
    else:
        print(format_tam_cografi_rapor(rapor))
