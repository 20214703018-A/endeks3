#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Tam Kapsamlı Fiziksel, Hukuki ve Altyapı Spatial Sorgulayıcı
========================================================================
Herhangi bir arsa koordinatı (Enlem, Boylam) için yerel SQLite CBS veritabanından:
  1. En yakın Elektrik İletim Hattı (TEİAŞ Yüksek Gerilim / ENH, Voltaj, Mesafe)
  2. En yakın Mevcut Yol (Yol Sınıfı, Asfalt/Toprak, Mesafe)
  3. En yakın Gelecek Yol Planı & İnşaat Halindeki Yol (Proje Adı, Mesafe)
  4. En yakın Tren Rayı / Demiryolu (YHT / Konvansiyonel Ray, Mesafe)
  5. En yakın Sit Alanı & Özel Koruma Bölgesi (Doğal Sit, Arkeolojik Sit, Milli Park, Mesafe)
  6. Sahil Şeridi & Kıyı Çizgisi (3621 Sayılı Kıyı Kanunu 50m / 100m Bandı Tespiti)
  7. En yakın Orman Alanı ve Orman Sınırı Mesafesi
  8. En yakın Dere Yatağı & Islah Kanalı Mesafesi
  9. OGM Orman Yangını Risk Kuşağı
ölçümlerini 1-5 milisaniye içinde sorgular.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
DB_PATH = DATA_DIR / "turkiye_fiziksel_ve_hukuki_altyapi.sqlite"


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def min_distance_to_polyline(lat: float, lon: float, coords: List[List[float]]) -> float:
    """Noktanın bir polyline çizgisine (koordinat listesine: [[lon, lat], ...]) olan en kısa mesafesini bulur."""
    min_dist = float("inf")
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i+1]

        # Doğru parçasına izdüşüm
        cos_lat = math.cos(math.radians(lat))
        x0 = lon * 111320.0 * cos_lat
        y0 = lat * 111320.0
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


@dataclass
class ParselTamAltyapiRaporu:
    enlem: float
    boylam: float
    
    # 1. Elektrik Hatları
    en_yakin_elektrik_hatti_adi: Optional[str]
    elektrik_hatti_voltaj: Optional[str]
    elektrik_hatti_mesafe_m: Optional[float]
    
    # 2. Yol Ağı ve Gelecek Planlar
    en_yakin_yol_adi: Optional[str]
    en_yakin_yol_sinifi: Optional[str]
    yol_mesafe_m: Optional[float]
    en_yakin_planlanan_yol_adi: Optional[str]
    planlanan_yol_mesafe_m: Optional[float]
    
    # 3. Demiryolları
    en_yakin_tren_hatti_adi: Optional[str]
    tren_hatti_tipi: Optional[str]
    tren_hatti_mesafe_m: Optional[float]
    
    # 4. Sit ve Korunan Alanlar
    en_yakin_sit_alani_adi: Optional[str]
    sit_koruma_kategorisi: Optional[str]
    sit_alani_mesafe_m: Optional[float]
    parsel_sit_icerisinde_mi: bool
    
    # 5. Sahil Şeridi (Kıyı Kanunu)
    sahil_seridi_mesafe_m: Optional[float]
    kiyi_kanunu_50m_yasak_bandinda_mi: bool
    kiyi_kanunu_100m_sahil_seridinde_mi: bool
    
    # 6. Orman Alanları
    en_yakin_orman_adi: Optional[str]
    orman_mesafe_m: Optional[float]
    
    # 7. Dere Yatakları
    en_yakin_dere_yatagi_adi: Optional[str]
    dere_yatagi_turu: Optional[str]
    dere_yatagi_mesafe_m: Optional[float]
    
    # 8. Yangın Riski
    yangin_risk_kategorisi: str

    # 9. Detaylı Diri Fay Hatları (GEM / MTA 895 Segment)
    en_yakin_detayli_fay_id: Optional[str]
    en_yakin_detayli_fay_sistemi: Optional[str]
    detayli_fay_kinematik_tipi: Optional[str]
    detayli_fay_kayma_hizi_mm_yil: Optional[str]
    detayli_fay_segment_uzunlugu_km: Optional[float]
    detayli_fay_mesafesi_m: Optional[float]
    detayli_fay_mesafesi_km: Optional[float]


class TamFizikselAltyapiSorgulayici:
    """Yerel SQLite R*Tree CBS veritabanı üzerinden tüm fiziksel katmanları sorgulayan motor."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        if not self.db_path.exists():
            raise FileNotFoundError(f"Fiziksel altyapı veritabanı bulunamadı: {self.db_path}. Lütfen tum_fiziksel_ve_hukuki_altyapi_cikarici.py çalıştırın.")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def sorgula_en_yakin_cizgi(self, tablo: str, rtree_tablo: str, lat: float, lon: float, yaricap_m: float = 15000.0) -> Tuple[Optional[sqlite3.Row], float]:
        """R*Tree kullanarak verilen koordinata en yakın çizgisel varlığı (hat, yol, sahil, dere) ve kesin mesafesini bulur."""
        conn = self._get_conn()
        cur = conn.cursor()

        d_lat = yaricap_m / 111320.0
        d_lon = yaricap_m / (111320.0 * max(0.1, math.cos(math.radians(lat))))

        cur.execute(f"""
        SELECT t.*
        FROM {rtree_tablo} r
        JOIN {tablo} t ON r.id = t.id
        WHERE r.minX <= ? AND r.maxX >= ? AND r.minY <= ? AND r.maxY >= ?
        """, (lon + d_lon, lon - d_lon, lat + d_lat, lat - d_lat))

        rows = cur.fetchall()
        conn.close()

        if not rows:
            return None, 999999.0

        min_dist = float("inf")
        secilen_row = None

        for r in rows:
            try:
                coords = json.loads(r["koordinatlar_geojson"])
                d = min_distance_to_polyline(lat, lon, coords)
                if d < min_dist:
                    min_dist = d
                    secilen_row = r
            except Exception:
                pass

        return secilen_row, round(min_dist, 1)

    def sorgula_yangin_riski(self, il_adi: Optional[str]) -> str:
        """İl adına göre OGM yangın hassasiyet derecesini çeker."""
        if not il_adi:
            return "2. Derece Yangın Kuşağı (Genel Marmara/Ege Geçiş)"
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT risk_kategorisi, aciklama FROM ham_yangin_risk_kusaklari WHERE il_adi LIKE ? LIMIT 1;", (f"%{il_adi}%",))
        row = cur.fetchone()
        conn.close()
        if row:
            return row["aciklama"]
        return "2. Derece Orta Risk Kuşağı"

    def analiz_et(self, lat: float, lon: float, il_adi: Optional[str] = None) -> ParselTamAltyapiRaporu:
        """Arsa koordinatları için tüm 8 altyapı ve hukuki kısıt katmanını anında sorgular."""
        # 1. Elektrik İletim Hatları
        el_row, el_dist = self.sorgula_en_yakin_cizgi("ham_elektrik_hatlari", "rtree_elektrik", lat, lon, yaricap_m=10000.0)

        # 2. Mevcut Yol Ağı
        conn = self._get_conn()
        cur = conn.cursor()
        d_lat = 5000.0 / 111320.0
        d_lon = 5000.0 / (111320.0 * max(0.1, math.cos(math.radians(lat))))
        cur.execute("""
        SELECT t.* FROM rtree_yollar r JOIN ham_yollar_ve_projeler t ON r.id = t.id
        WHERE r.minX <= ? AND r.maxX >= ? AND r.minY <= ? AND r.maxY >= ?
        """, (lon + d_lon, lon - d_lon, lat + d_lat, lat - d_lat))
        yol_rows = cur.fetchall()
        conn.close()

        en_yakin_mevcut_yol = None
        min_yol_dist = float("inf")
        en_yakin_planlanan_yol = None
        min_plan_dist = float("inf")

        for r in yol_rows:
            try:
                coords = json.loads(r["koordinatlar_geojson"])
                d = min_distance_to_polyline(lat, lon, coords)
                if r["durum"] == "INSAAT_HALINDE_PLANLANAN":
                    if d < min_plan_dist:
                        min_plan_dist = d
                        en_yakin_planlanan_yol = r
                else:
                    if d < min_yol_dist:
                        min_yol_dist = d
                        en_yakin_mevcut_yol = r
            except Exception:
                pass

        # 3. Demiryolları
        rail_row, rail_dist = self.sorgula_en_yakin_cizgi("ham_demiryollari", "rtree_demiryollari", lat, lon, yaricap_m=15000.0)

        # 4. Sit Alanları
        sit_row, sit_dist = self.sorgula_en_yakin_cizgi("ham_sit_alanlari_ve_ozel_bolgeler", "rtree_sit_alanlari", lat, lon, yaricap_m=15000.0)

        # 5. Sahil Şeridi (Kıyı Çizgisi)
        sahil_row, sahil_dist = self.sorgula_en_yakin_cizgi("ham_sahil_seritleri", "rtree_sahil", lat, lon, yaricap_m=15000.0)

        # 6. Orman Alanları
        orman_row, orman_dist = self.sorgula_en_yakin_cizgi("ham_orman_alanlari", "rtree_ormanlar", lat, lon, yaricap_m=15000.0)

        # 7. Dere Yatakları
        dere_row, dere_dist = self.sorgula_en_yakin_cizgi("ham_dere_yataklari", "rtree_dere_yataklari", lat, lon, yaricap_m=8000.0)

        # 8. Yangın Riski
        yangin_aciklama = self.sorgula_yangin_riski(il_adi)

        # 9. Detaylı Diri Fay Hatları (GEM / MTA 895 Segment)
        fay_row, fay_dist = self.sorgula_en_yakin_cizgi("detayli_diri_faylar", "rtree_detayli_faylar", lat, lon, yaricap_m=100000.0)
        if not fay_row:
            # 100 km içinde bulunamadıysa tüm tabloyu tara
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT * FROM detayli_diri_faylar;")
            all_faylar = cur.fetchall()
            conn.close()
            min_dist = float("inf")
            for r in all_faylar:
                try:
                    coords = json.loads(r["koordinatlar_geojson"])
                    d = min_distance_to_polyline(lat, lon, coords)
                    if d < min_dist:
                        min_dist = d
                        fay_row = r
                except Exception:
                    pass
            fay_dist = round(min_dist, 1)

        fay_km = round(fay_dist / 1000.0, 2) if fay_row else None

        return ParselTamAltyapiRaporu(
            enlem=lat,
            boylam=lon,
            en_yakin_elektrik_hatti_adi=el_row["ad"] if el_row else None,
            elektrik_hatti_voltaj=el_row["voltaj"] if el_row else None,
            elektrik_hatti_mesafe_m=el_dist if el_row else None,
            en_yakin_yol_adi=en_yakin_mevcut_yol["ad"] if en_yakin_mevcut_yol else None,
            en_yakin_yol_sinifi=en_yakin_mevcut_yol["yol_sinifi"] if en_yakin_mevcut_yol else None,
            yol_mesafe_m=round(min_yol_dist, 1) if en_yakin_mevcut_yol else None,
            en_yakin_planlanan_yol_adi=en_yakin_planlanan_yol["ad"] if en_yakin_planlanan_yol else None,
            planlanan_yol_mesafe_m=round(min_plan_dist, 1) if en_yakin_planlanan_yol else None,
            en_yakin_tren_hatti_adi=rail_row["ad"] if rail_row else None,
            tren_hatti_tipi=rail_row["hat_tipi"] if rail_row else None,
            tren_hatti_mesafe_m=rail_dist if rail_row else None,
            en_yakin_sit_alani_adi=sit_row["ad"] if sit_row else None,
            sit_koruma_kategorisi=sit_row["koruma_kategorisi"] if sit_row else None,
            sit_alani_mesafe_m=sit_dist if sit_row else None,
            parsel_sit_icerisinde_mi=(sit_dist <= 10.0) if sit_row else False,
            sahil_seridi_mesafe_m=sahil_dist if sahil_row else None,
            kiyi_kanunu_50m_yasak_bandinda_mi=(sahil_dist <= 50.0) if sahil_row else False,
            kiyi_kanunu_100m_sahil_seridinde_mi=(sahil_dist <= 100.0) if sahil_row else False,
            en_yakin_orman_adi=orman_row["ad"] if orman_row else None,
            orman_mesafe_m=orman_dist if orman_row else None,
            en_yakin_dere_yatagi_adi=dere_row["ad"] if dere_row else None,
            dere_yatagi_turu=dere_row["tur"] if dere_row else None,
            dere_yatagi_mesafe_m=dere_dist if dere_row else None,
            yangin_risk_kategorisi=yangin_aciklama,
            en_yakin_detayli_fay_id=fay_row["catalog_id"] if fay_row else None,
            en_yakin_detayli_fay_sistemi=fay_row["fay_sistemi"] if fay_row else None,
            detayli_fay_kinematik_tipi=fay_row["slip_type"] if fay_row else None,
            detayli_fay_kayma_hizi_mm_yil=fay_row["net_slip_rate"] if fay_row else None,
            detayli_fay_segment_uzunlugu_km=fay_row["uzunluk_km"] if fay_row else None,
            detayli_fay_mesafesi_m=fay_dist if fay_row else None,
            detayli_fay_mesafesi_km=fay_km
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parsel Tam Altyapı ve Kısıt Sorgulayıcı")
    parser.add_argument("--lat", type=float, default=41.0740, help="Enlem")
    parser.add_argument("--lon", type=float, default=28.2460, help="Boylam")
    parser.add_argument("--il", type=str, default="İstanbul", help="İl adı")
    parser.add_argument("--json", action="store_true", help="JSON formatında çıktı")
    args = parser.parse_args()

    sorgulayici = TamFizikselAltyapiSorgulayici()
    rapor = sorgulayici.analiz_et(args.lat, args.lon, il_adi=args.il)

    if args.json:
        print(json.dumps(asdict(rapor), ensure_ascii=False, indent=2))
    else:
        d = asdict(rapor)
        print("╔═══════════════════════════════════════════════════════════════════════════════╗")
        print("║      GEOPROP AI - PARSEL FİZİKSEL, ALTYAPI VE HUKUKİ KISIT ENVENTERİ          ║")
        print("╠═══════════════════════════════════════════════════════════════════════════════╣")
        print(f"║ Koordinat : {args.lat:.5f}, {args.lon:.5f} ({args.il})")
        print("╠═══════════════════════════════════════════════════════════════════════════════╣")
        print(f"║ 1. ELEKTRİK İLETİM HATTI : {d['en_yakin_elektrik_hatti_adi'] or 'Uzak'} ({d['elektrik_hatti_voltaj'] or ''}V) - {d['elektrik_hatti_mesafe_m']} m")
        print(f"║ 2. MEVCUT YOL AĞI        : {d['en_yakin_yol_adi'] or 'İsimsiz Yol'} ({d['en_yakin_yol_sinifi']}) - {d['yol_mesafe_m']} m")
        print(f"║ 3. GELECEK YOL PLANI     : {d['en_yakin_planlanan_yol_adi'] or 'Yakında Proje Yok'} - {d['planlanan_yol_mesafe_m']} m")
        print(f"║ 4. DEMİRYOLU / TREN RAYI : {d['en_yakin_tren_hatti_adi'] or 'Uzak'} ({d['tren_hatti_tipi']}) - {d['tren_hatti_mesafe_m']} m")
        print(f"║ 5. SİT & KORUMA ALANI    : {d['en_yakin_sit_alani_adi'] or 'Kayıt Yok'} ({d['sit_koruma_kategorisi']}) - {d['sit_alani_mesafe_m']} m (Sit İçi: {d['parsel_sit_icerisinde_mi']})")
        print(f"║ 6. SAHİL & KIYI ŞERİDİ   : {d['sahil_seridi_mesafe_m']} m (50m Yasak Bandı: {d['kiyi_kanunu_50m_yasak_bandinda_mi']}, 100m Bandı: {d['kiyi_kanunu_100m_sahil_seridinde_mi']})")
        print(f"║ 7. ORMAN ALANI & SINIRI  : {d['en_yakin_orman_adi'] or 'Devlet Ormanı'} - {d['orman_mesafe_m']} m")
        print(f"║ 8. DERE YATAĞI & ISLAH   : {d['en_yakin_dere_yatagi_adi'] or 'Dere'} ({d['dere_yatagi_turu']}) - {d['dere_yatagi_mesafe_m']} m")
        print(f"║ 9. ORMAN YANGINI RİSKİ   : {d['yangin_risk_kategorisi']}")
        print(f"║ 10. DETAYLI DİRİ FAY HATTI: {d['en_yakin_detayli_fay_id']} ({d['en_yakin_detayli_fay_sistemi']})")
        print(f"║     - Mekanizma / Tip    : {d['detayli_fay_kinematik_tipi']}")
        print(f"║     - Yıllık Kayma Hızı  : {d['detayli_fay_kayma_hizi_mm_yil']} mm/yıl")
        print(f"║     - Segment Uzunluğu   : {d['detayli_fay_segment_uzunlugu_km']} km")
        print(f"║     - Parsel Fay Mesafesi: {d['detayli_fay_mesafesi_km']} km ({d['detayli_fay_mesafesi_m']} metre)")
        print("╚═══════════════════════════════════════════════════════════════════════════════╝")
