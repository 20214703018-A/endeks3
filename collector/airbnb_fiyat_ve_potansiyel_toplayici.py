#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Doğrudan Python Airbnb Pazar & Kısa Dönem Kiralama (STR) Potansiyeli Toplayıcı
-----------------------------------------------------------------------------------------
Bu modül, Airbnb arama motorunun dahili mimarisini doğrudan sorgulayarak:
1. Türkiye'nin herhangi bir bölgesi (Bodrum, Marmaris, Kaş, Çeşme, Kadıköy vb.) veya
2. Doğrudan verilen bir koordinat ve yarıçap (lat, lon, yarıçap km)
için aktif Airbnb ilanlarını, gecelik fiyatları (ADR), doluluk tahminini,
yıllık brüt/net getiri projeksiyonunu ve 7464 sayılı kanun risk analizini çıkarır.
"""

import argparse
import base64
import csv
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as c_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False


DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "airbnb")
os.makedirs(DATA_DIR, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

@dataclass
class AirbnbIlani:
    id: str
    baslik: str
    alt_baslik: str
    oda_tipi: str
    gecelik_fiyat_tl: float
    fiyat_1_gun_tl: Optional[float] = None
    fiyat_1_hafta_tl: Optional[float] = None
    fiyat_1_ay_tl: Optional[float] = None
    fiyat_3_ay_tl: Optional[float] = None
    puan: Optional[float] = None
    yorum_sayisi: int = 0
    enlem: float = 0.0
    boylam: float = 0.0
    url: str = ""
    rozetler: List[str] = field(default_factory=list)
    resim_url: Optional[str] = None

@dataclass
class BolgeselAirbnbPotansiyeli:
    bolge_adi: str
    toplam_ilan_sayisi: int
    medyan_gecelik_tl: float
    min_gecelik_tl: float
    max_gecelik_tl: float
    fiyat_bandi_25_75_tl: Tuple[float, float]
    vade_fiyatlari: Dict[str, Optional[float]]
    ortalama_puan: float
    oda_tipi_dagilimi: Dict[str, int]
    tahmini_yillik_doluluk_yuzde: float
    tahmini_yillik_brut_gelir_tl: float
    tahmini_yillik_net_gelir_tl: float
    klasik_kira_tl: Optional[float]
    airbnb_prim_carpani: Optional[float]
    yasal_7464_risk_durumu: str


class AirbnbToplayici:
    """Airbnb arama motorunu doğrudan Python üzerinden sorgulayan toplayıcı sınıf."""

    def __init__(self):
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }

    def _fetch_html(self, url: str) -> str:
        if HAS_CURL_CFFI:
            try:
                r = c_requests.get(url, headers=self.headers, impersonate="chrome124", timeout=15)
                if r.status_code == 200:
                    return r.text
            except Exception as e:
                print(f"[!] curl_cffi hata ({e}), standart urllib ile deneniyor...")
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")


    def _parse_id(self, raw_id: str) -> str:
        if not raw_id:
            return ""
        if raw_id.startswith("RGVt"):
            try:
                decoded = base64.b64decode(raw_id).decode("utf-8")
                return decoded.split(":")[-1]
            except Exception:
                pass
        if ":" in raw_id:
            return raw_id.split(":")[-1]
        return raw_id

    def _extract_nightly_price(self, p_obj: dict) -> Optional[float]:
        """Airbnb fiyat bloğundan net gecelik fiyatı çıkarır."""
        if not p_obj:
            return None

        # 1. '5 gece x ₺11.437,56' açıklamasından net birim gecelik fiyat
        expl = p_obj.get("explanationData") or {}
        for grp in expl.get("priceDetails", []):
            for item in grp.get("items", []):
                desc = item.get("description", "")
                match = re.search(r"\b(\d+)\s*gece\s*x\s*₺?([\d\.,]+)", desc)
                if match:
                    gecelik_str = match.group(2).replace(".", "").replace(",", ".")
                    try:
                        return float(gecelik_str)
                    except ValueError:
                        pass

        # 2. primaryLine içinden indirimli veya standart fiyat
        primary = p_obj.get("primaryLine") or {}
        p_str = primary.get("discountedPrice") or primary.get("price")
        qualifier = primary.get("qualifier", "")
        if p_str:
            nums = re.sub(r"[^\d]", "", p_str)
            if nums:
                total = float(nums)
                q_match = re.search(r"(\d+)\s*gece", qualifier)
                if q_match:
                    nights = int(q_match.group(1))
                    if nights > 0:
                        return total / nights
                return total
        return None

    def _parse_search_results(self, html: str) -> List[AirbnbIlani]:
        """Airbnb HTML içerisindeki niobeClientData JSON ağacından ilanları ayrıştırır."""
        scripts = re.findall(r'<script[^>]*type=[\"\']application/json[\"\'][^>]*>(.*?)</script>', html)
        ilanlar: List[AirbnbIlani] = []

        for s in scripts:
            if "niobeClientData" not in s:
                continue
            try:
                data = json.loads(s)
                # staysSearch sonuç bloğunu bul
                results = None
                try:
                    results = data["niobeClientData"][0][1]["data"]["presentation"]["staysSearch"]["results"]["searchResults"]
                except (KeyError, IndexError, TypeError):
                    pass

                if not results:
                    continue

                for r in results:
                    # Başlık & Alt Başlık
                    title = (
                        r.get("title")
                        or (r.get("nameLocalized") or {}).get("localizedStringWithTranslationPreference")
                        or "İsimsiz İlan"
                    )
                    sub = r.get("subtitle") or ""

                    # Fiyat
                    p_obj = r.get("structuredDisplayPrice")
                    nightly = self._extract_nightly_price(p_obj)
                    if not nightly or nightly <= 0:
                        continue

                    # Konum
                    loc_obj = (r.get("demandStayListing") or {}).get("location", {}).get("coordinate", {})
                    lat = loc_obj.get("latitude")
                    lon = loc_obj.get("longitude")
                    if lat is None or lon is None:
                        continue

                    # ID ve Link
                    raw_id = (r.get("demandStayListing") or {}).get("id", "")
                    clean_id = self._parse_id(raw_id)
                    url = f"https://www.airbnb.com.tr/rooms/{clean_id}" if clean_id else ""

                    # Puan & Değerlendirme
                    rating_val = None
                    reviews_cnt = 0
                    rating_label = r.get("avgRatingA11yLabel") or ""
                    # Örn: '5 üzerinden ortalama 5,0 puan, 47 değerlendirme'
                    m_rate = re.search(r"(\d+[\.,]\d+)\s*puan", rating_label)
                    if m_rate:
                        rating_val = float(m_rate.group(1).replace(",", "."))
                    m_cnt = re.search(r"(\d+)\s*değerlendirme", rating_label)
                    if m_cnt:
                        reviews_cnt = int(m_cnt.group(1))

                    # Oda Tipi Tahmini
                    oda_tipi = "Daire / Ev"
                    if "villa" in title.lower() or "villa" in sub.lower():
                        oda_tipi = "Müstakil Villa"
                    elif "oda" in title.lower() or "oda" in sub.lower() or "room" in title.lower():
                        oda_tipi = "Özel Oda"
                    elif "daire" in title.lower() or "apart" in title.lower():
                        oda_tipi = "Daire"
                    elif "kır evi" in title.lower() or "bungalov" in title.lower() or "taş ev" in title.lower():
                        oda_tipi = "Kır Evi / Bungalov"

                    # Rozetler
                    badges = [b.get("localKey") for b in r.get("badges", []) if b.get("localKey")]

                    # Resim
                    pic_id = (r.get("contextualPictures") or [{}])[0].get("id")
                    img_url = f"https://a0.muscache.com/im/pictures/{pic_id}.jpg" if pic_id else None

                    ilanlar.append(AirbnbIlani(
                        id=clean_id,
                        baslik=title,
                        alt_baslik=sub,
                        oda_tipi=oda_tipi,
                        gecelik_fiyat_tl=round(nightly),
                        puan=rating_val,
                        yorum_sayisi=reviews_cnt,
                        enlem=lat,
                        boylam=lon,
                        url=url,
                        rozetler=badges,
                        resim_url=img_url
                    ))
                break
            except Exception:
                continue

        return ilanlar

    def sorgula_bolge(
        self,
        bolge_adi: str,
        checkin: Optional[str] = None,
        checkout: Optional[str] = None
    ) -> List[AirbnbIlani]:
        """Şehir veya ilçe/bölge adına (ve varsa checkin/checkout tarihine) göre Airbnb'den aktif ilanları çeker."""
        encoded = urllib.parse.quote(bolge_adi)
        url = f"https://www.airbnb.com.tr/s/homes?query={encoded}"
        if checkin and checkout:
            url += f"&checkin={checkin}&checkout={checkout}"
        tarih_bilgi = f" ({checkin} - {checkout})" if checkin and checkout else ""
        print(f"[*] Airbnb araması yapılıyor: {bolge_adi}{tarih_bilgi} -> {url}")
        html = self._fetch_html(url)
        ilanlar = self._parse_search_results(html)
        print(f"[+] '{bolge_adi}'{tarih_bilgi} için {len(ilanlar)} aktif ilan başarıyla çekildi.")
        return ilanlar

    def sorgula_bolge_vadeli(self, bolge_adi: str, limit_per_vade: int = 30) -> List[AirbnbIlani]:
        """
        Kullanıcı Talebi:
        1 gün sonrası, 1 hafta sonrası, 1 ay sonrası ve 3 ay sonrası için
        Airbnb fiyatlarını ve doluluk eğrisini çeker.
        """
        now = datetime.now()
        vadeler = [
            ("1_gun", "1 Gün Sonrası", now + timedelta(days=1), now + timedelta(days=3)),
            ("1_hafta", "1 Hafta Sonrası", now + timedelta(days=7), now + timedelta(days=9)),
            ("1_ay", "1 Ay Sonrası", now + timedelta(days=30), now + timedelta(days=32)),
            ("3_ay", "3 Ay Sonrası", now + timedelta(days=90), now + timedelta(days=92)),
        ]
        
        ilanlar_map: Dict[str, AirbnbIlani] = {}

        print(f"\n📅 [{bolge_adi}] 4 Zaman Ufku İçin Taraması Başlatılıyor (1g, 1h, 1a, 3a)...")
        for vade_kodu, etiket, cin_dt, cout_dt in vadeler:
            cin_str = cin_dt.strftime("%Y-%m-%d")
            cout_str = cout_dt.strftime("%Y-%m-%d")
            try:
                sonuclar = self.sorgula_bolge(bolge_adi, checkin=cin_str, checkout=cout_str)
                count = 0
                for ilan in sonuclar:
                    if ilan.id not in ilanlar_map:
                        ilanlar_map[ilan.id] = ilan
                    mevcut = ilanlar_map[ilan.id]
                    
                    if vade_kodu == "1_gun":
                        mevcut.fiyat_1_gun_tl = ilan.gecelik_fiyat_tl
                    elif vade_kodu == "1_hafta":
                        mevcut.fiyat_1_hafta_tl = ilan.gecelik_fiyat_tl
                    elif vade_kodu == "1_ay":
                        mevcut.fiyat_1_ay_tl = ilan.gecelik_fiyat_tl
                    elif vade_kodu == "3_ay":
                        mevcut.fiyat_3_ay_tl = ilan.gecelik_fiyat_tl
                        
                    count += 1
                    if count >= limit_per_vade:
                        break
                print(f"  └─ {etiket} ({cin_str} - {cout_str}): {min(count, len(sonuclar))} ilan işlendi.")
                time.sleep(1.0)
            except Exception as e:
                print(f"[!] {etiket} sorgulanırken hata: {e}")

        # Her ilanın genel medyan/ortalama fiyatını vadelere göre güncelle
        for ilan in ilanlar_map.values():
            fiyat_listesi = [
                f for f in [ilan.fiyat_1_gun_tl, ilan.fiyat_1_hafta_tl, ilan.fiyat_1_ay_tl, ilan.fiyat_3_ay_tl]
                if f is not None and f > 0
            ]
            if fiyat_listesi:
                ilan.gecelik_fiyat_tl = round(sum(fiyat_listesi) / len(fiyat_listesi))

        toplam = list(ilanlar_map.values())
        print(f"[✔] '{bolge_adi}' için 4 vade boyunca toplam {len(toplam)} tekil ilan toplandı.\n")
        return toplam

    def sorgula_coklu_bolge(self, bolge_listesi: List[str], limit_per_bolge: int = 50, vadeli: bool = True) -> List[AirbnbIlani]:
        """Birden fazla bölge veya il için sırayla Airbnb sorgusu yapar (varsayılan vadeli)."""
        tum_ilanlar: List[AirbnbIlani] = []
        gorulen_idler = set()
        for b in bolge_listesi:
            b_clean = b.strip()
            if not b_clean:
                continue
            query_name = f"{b_clean}, Türkiye" if "türkiye" not in b_clean.lower() and "turkey" not in b_clean.lower() else b_clean
            try:
                if vadeli:
                    ilanlar = self.sorgula_bolge_vadeli(query_name, limit_per_vade=limit_per_bolge)
                else:
                    ilanlar = self.sorgula_bolge(query_name)

                eklenen = 0
                for il in ilanlar:
                    if il.id not in gorulen_idler:
                        gorulen_idler.add(il.id)
                        tum_ilanlar.append(il)
                        eklenen += 1
                print(f"  -> '{b_clean}' bölgesinden {eklenen} tekil ilan eklendi (Gruptaki toplam: {len(tum_ilanlar)}).")
                time.sleep(1.2)
            except Exception as e:
                print(f"[!] '{b_clean}' sorgulanırken hata: {e}")
        return tum_ilanlar



    def sorgula_koordinat(self, lat: float, lon: float, yaricap_km: float = 3.0) -> List[AirbnbIlani]:
        """Verilen enlem/boylam ve yarıçap (km) çevresindeki kutuyu (bbox) Airbnb'de arar."""
        # 1 derece enlem ~ 111 km
        delta_lat = yaricap_km / 111.0
        # 1 derece boylam ~ 111 * cos(lat) km
        delta_lon = yaricap_km / (111.0 * math.cos(math.radians(lat)))

        ne_lat = round(lat + delta_lat, 5)
        ne_lng = round(lon + delta_lon, 5)
        sw_lat = round(lat - delta_lat, 5)
        sw_lng = round(lon - delta_lon, 5)

        url = (
            f"https://www.airbnb.com.tr/s/homes?"
            f"ne_lat={ne_lat}&ne_lng={ne_lng}&sw_lat={sw_lat}&sw_lng={sw_lng}&search_by_map=true"
        )
        print(f"[*] Koordinat çevresi ({yaricap_km} km) kutusu sorgulanıyor: [{sw_lat}, {sw_lng}] - [{ne_lat}, {ne_lng}]")
        html = self._fetch_html(url)
        ilanlar = self._parse_search_results(html)
        print(f"[+] Belirtilen koordinat yarıçapında {len(ilanlar)} aktif ilan bulundu.")
        return ilanlar

    def potansiyel_analizi_yap(
        self,
        ilanlar: List[AirbnbIlani],
        bolge_adi: str = "Bölge",
        klasik_aylik_kira_tl: Optional[float] = None
    ) -> BolgeselAirbnbPotansiyeli:
        """Toplanan ilanlar üzerinden getiri, doluluk, net nakit akışı, 4 farklı zaman vadesi ve 7464 uyumunu analiz eder."""
        if not ilanlar:
            return BolgeselAirbnbPotansiyeli(
                bolge_adi=bolge_adi,
                toplam_ilan_sayisi=0,
                medyan_gecelik_tl=0.0,
                min_gecelik_tl=0.0,
                max_gecelik_tl=0.0,
                fiyat_bandi_25_75_tl=(0.0, 0.0),
                vade_fiyatlari={"1_gun": None, "1_hafta": None, "1_ay": None, "3_ay": None},
                ortalama_puan=0.0,
                oda_tipi_dagilimi={},
                tahmini_yillik_doluluk_yuzde=0.0,
                tahmini_yillik_brut_gelir_tl=0.0,
                tahmini_yillik_net_gelir_tl=0.0,
                klasik_kira_tl=klasik_aylik_kira_tl,
                airbnb_prim_carpani=None,
                yasal_7464_risk_durumu="Veri yok"
            )

        fiyatlar = sorted([i.gecelik_fiyat_tl for i in ilanlar if i.gecelik_fiyat_tl > 0])
        medyan_fiyat = fiyatlar[len(fiyatlar) // 2] if fiyatlar else 0.0
        min_fiyat = fiyatlar[0] if fiyatlar else 0.0
        max_fiyat = fiyatlar[-1] if fiyatlar else 0.0
        p25 = fiyatlar[int(len(fiyatlar) * 0.25)] if fiyatlar else 0.0
        p75 = fiyatlar[int(len(fiyatlar) * 0.75)] if fiyatlar else 0.0

        def med_hesapla(arr):
            temiz = [x for x in arr if x is not None and x > 0]
            if not temiz:
                return None
            temiz.sort()
            return round(temiz[len(temiz) // 2])

        vade_fiyatlari = {
            "1_gun": med_hesapla([i.fiyat_1_gun_tl for i in ilanlar]),
            "1_hafta": med_hesapla([i.fiyat_1_hafta_tl for i in ilanlar]),
            "1_ay": med_hesapla([i.fiyat_1_ay_tl for i in ilanlar]),
            "3_ay": med_hesapla([i.fiyat_3_ay_tl for i in ilanlar]),
        }

        puanlar = [i.puan for i in ilanlar if i.puan is not None]
        ort_puan = round(sum(puanlar) / len(puanlar), 2) if puanlar else 4.85

        oda_dagilimi = {}
        for i in ilanlar:
            oda_dagilimi[i.oda_tipi] = oda_dagilimi.get(i.oda_tipi, 0) + 1

        # Türkiye turizm ve şehir ortalamalarına göre sezonsal doluluk projeksiyonu
        is_coastal = any(x in bolge_adi.lower() for x in ["bodrum", "marmaris", "kaş", "datça", "çeşme", "fethiye", "antalya"])
        tahmini_doluluk = 0.55 if is_coastal else 0.65
        kiralanan_gun = 365 * tahmini_doluluk

        # Brüt Yıllık Gelir
        brut_gelir = medyan_fiyat * kiralanan_gun

        # Net Gelir Hesabı:
        # - Platform Komisyonu (%15)
        # - Temizlik ve Operasyon (%12)
        # - Faturalar / Aidat (%10)
        # - %2 Konaklama Vergisi + Gelir Vergisi/Stopaj (%18)
        # Toplam gider ~ %55, Kalan Net ~ %45
        net_gelir = brut_gelir * (1.0 - 0.15 - 0.12 - 0.10 - 0.20)

        # Klasik kira ile karşılaştırma
        prim_carpani = None
        if klasik_aylik_kira_tl and klasik_aylik_kira_tl > 0:
            yillik_klasik = klasik_aylik_kira_tl * 12
            prim_carpani = round(net_gelir / yillik_klasik, 2)

        # 7464 Sayılı Kanun Uyarısı
        villa_orani = oda_dagilimi.get("Müstakil Villa", 0) / (len(ilanlar) or 1)
        if villa_orani >= 0.4:
            yasal_risk = "DÜŞÜK RİSK: Bölgede müstakil villa/tek tapu yoğunlukta; 7464 izin belgesi alma şansı yüksektir."
        else:
            yasal_risk = "YÜKSEK RİSK: Bölgede apartman dairesi yoğunlukta; 7464 gereği bina maliklerinin %100 oybirliği şarttır."

        return BolgeselAirbnbPotansiyeli(
            bolge_adi=bolge_adi,
            toplam_ilan_sayisi=len(ilanlar),
            medyan_gecelik_tl=round(medyan_fiyat),
            min_gecelik_tl=round(min_fiyat),
            max_gecelik_tl=round(max_fiyat),
            fiyat_bandi_25_75_tl=(round(p25), round(p75)),
            vade_fiyatlari=vade_fiyatlari,
            ortalama_puan=ort_puan,
            oda_tipi_dagilimi=oda_dagilimi,
            tahmini_yillik_doluluk_yuzde=round(tahmini_doluluk * 100, 1),
            tahmini_yillik_brut_gelir_tl=round(brut_gelir),
            tahmini_yillik_net_gelir_tl=round(net_gelir),
            klasik_kira_tl=klasik_aylik_kira_tl,
            airbnb_prim_carpani=prim_carpani,
            yasal_7464_risk_durumu=yasal_risk
        )

    def export_csv(self, ilanlar: List[AirbnbIlani], dosya_adi: str) -> str:
        filepath = os.path.join(DATA_DIR, f"{dosya_adi}.csv")
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "baslik", "alt_baslik", "oda_tipi", "gecelik_fiyat_tl",
                "fiyat_1_gun_tl", "fiyat_1_hafta_tl", "fiyat_1_ay_tl", "fiyat_3_ay_tl",
                "puan", "yorum_sayisi", "enlem", "boylam", "url", "resim_url"
            ])
            for i in ilanlar:
                writer.writerow([
                    i.id, i.baslik, i.alt_baslik, i.oda_tipi, i.gecelik_fiyat_tl,
                    i.fiyat_1_gun_tl or "", i.fiyat_1_hafta_tl or "", i.fiyat_1_ay_tl or "", i.fiyat_3_ay_tl or "",
                    i.puan, i.yorum_sayisi, i.enlem, i.boylam, i.url, i.resim_url
                ])
        print(f"[+] CSV kaydedildi: {filepath}")
        return filepath

    def export_geojson(self, ilanlar: List[AirbnbIlani], dosya_adi: str) -> str:
        filepath = os.path.join(DATA_DIR, f"{dosya_adi}.geojson")
        features = []
        for i in ilanlar:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [i.boylam, i.enlem]
                },
                "properties": {
                    "katman": "AIRBNB_ILANI",
                    "id": i.id,
                    "ad": i.baslik,
                    "alt_tur": i.oda_tipi,
                    "fiyat_tl": i.gecelik_fiyat_tl,
                    "fiyat_1_gun_tl": i.fiyat_1_gun_tl,
                    "fiyat_1_hafta_tl": i.fiyat_1_hafta_tl,
                    "fiyat_1_ay_tl": i.fiyat_1_ay_tl,
                    "fiyat_3_ay_tl": i.fiyat_3_ay_tl,
                    "puan": i.puan,
                    "yorum": i.yorum_sayisi,
                    "url": i.url
                }
            })
        data = {
            "type": "FeatureCollection",
            "features": features
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[+] GeoJSON kaydedildi: {filepath}")
        return filepath


def birlestir_tum_sonuclari(data_dir: str = DATA_DIR):
    """Tüm 40 makinenin artifaktlarını ve parça çıktılarını tek bir master dosyada birleştirir."""
    print("=" * 70)
    print("🚀 GEOPROP AI - 40 MAKİNE AIRBNB ÇIKTILARINI BİRLEŞTİRME VE MASTER KATMAN")
    print("=" * 70)
    
    all_features = []
    seen_features: Dict[str, dict] = {}
    all_rows = []
    seen_ids = set()
    
    search_dirs = [data_dir, "indirilmis_artifaktlar", "artifacts"]
    for s_dir in search_dirs:
        if not os.path.exists(s_dir):
            continue
        for root, _, files in os.walk(s_dir):
            for file in files:
                if file.endswith(".geojson") and ("airbnb" in file.lower() or "grup_" in file.lower()) and "tam" not in file.lower():
                    fp = os.path.join(root, file)
                    try:
                        with open(fp, "r", encoding="utf-8") as gf:
                            data = json.load(gf)
                            feats = data.get("features", [])
                            for feat in feats:
                                pid = feat.get("properties", {}).get("id")
                                if not pid:
                                    continue
                                if pid not in seen_features:
                                    seen_features[pid] = feat
                                else:
                                    # Vade fiyatlarını birleştir
                                    mevcut_p = seen_features[pid].get("properties", {})
                                    yeni_p = feat.get("properties", {})
                                    for v_key in ["fiyat_1_gun_tl", "fiyat_1_hafta_tl", "fiyat_1_ay_tl", "fiyat_3_ay_tl"]:
                                        if not mevcut_p.get(v_key) and yeni_p.get(v_key):
                                            mevcut_p[v_key] = yeni_p.get(v_key)
                    except Exception as e:
                        print(f"[!] GeoJSON okuma hatası ({fp}): {e}")
                        
                if file.endswith(".csv") and ("airbnb" in file.lower() or "grup_" in file.lower()) and "tam" not in file.lower():
                    fp = os.path.join(root, file)
                    try:
                        with open(fp, "r", encoding="utf-8") as cf:
                            reader = csv.DictReader(cf)
                            for row in reader:
                                rid = row.get("id")
                                if rid and rid not in seen_ids:
                                    seen_ids.add(rid)
                                    all_rows.append(row)
                    except Exception as e:
                        print(f"[!] CSV okuma hatası ({fp}): {e}")

    all_features = list(seen_features.values())
    master_geojson_path = os.path.join(data_dir, "turkiye_tam_airbnb_ilanlari.geojson")
    master_csv_path = os.path.join(data_dir, "turkiye_tam_airbnb_ilanlari.csv")
    master_summary_path = os.path.join(data_dir, "turkiye_airbnb_pazar_analizi.json")

    # Master GeoJSON Kaydet
    with open(master_geojson_path, "w", encoding="utf-8") as f:
        json.dump({
            "type": "FeatureCollection",
            "features": all_features
        }, f, ensure_ascii=False, indent=2)
    print(f"[✔] Master Airbnb GeoJSON yazıldı: {master_geojson_path} ({len(all_features)} tekil ilan)")

    # Master CSV Kaydet
    csv_rows = all_rows
    if not csv_rows and all_features:
        for feat in all_features:
            p = feat.get("properties", {})
            g = feat.get("geometry", {}).get("coordinates", [0, 0])
            csv_rows.append({
                "id": p.get("id", ""),
                "baslik": p.get("ad", ""),
                "alt_baslik": "",
                "oda_tipi": p.get("alt_tur", ""),
                "gecelik_fiyat_tl": p.get("fiyat_tl", 0),
                "fiyat_1_gun_tl": p.get("fiyat_1_gun_tl", ""),
                "fiyat_1_hafta_tl": p.get("fiyat_1_hafta_tl", ""),
                "fiyat_1_ay_tl": p.get("fiyat_1_ay_tl", ""),
                "fiyat_3_ay_tl": p.get("fiyat_3_ay_tl", ""),
                "puan": p.get("puan", ""),
                "yorum_sayisi": p.get("yorum", 0),
                "boylam": g[0],
                "enlem": g[1],
                "url": p.get("url", ""),
                "resim_url": ""
            })
    fieldnames = [
        "id", "baslik", "alt_baslik", "oda_tipi", "gecelik_fiyat_tl",
        "fiyat_1_gun_tl", "fiyat_1_hafta_tl", "fiyat_1_ay_tl", "fiyat_3_ay_tl",
        "puan", "yorum_sayisi", "enlem", "boylam", "url", "resim_url"
    ]
    with open(master_csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"[✔] Master Airbnb CSV yazıldı: {master_csv_path} ({len(csv_rows)} kayıt)")

    # İstatistiksel Pazar Özeti
    if all_features:
        fiyatlar = [f.get("properties", {}).get("fiyat_tl", 0) for f in all_features if f.get("properties", {}).get("fiyat_tl", 0) > 0]
        v1g = [f.get("properties", {}).get("fiyat_1_gun_tl") for f in all_features if f.get("properties", {}).get("fiyat_1_gun_tl")]
        v1h = [f.get("properties", {}).get("fiyat_1_hafta_tl") for f in all_features if f.get("properties", {}).get("fiyat_1_hafta_tl")]
        v1a = [f.get("properties", {}).get("fiyat_1_ay_tl") for f in all_features if f.get("properties", {}).get("fiyat_1_ay_tl")]
        v3a = [f.get("properties", {}).get("fiyat_3_ay_tl") for f in all_features if f.get("properties", {}).get("fiyat_3_ay_tl")]

        def med(arr):
            return sorted(arr)[len(arr) // 2] if arr else None

        if fiyatlar:
            fiyatlar.sort()
            medyan = fiyatlar[len(fiyatlar) // 2]
            p25 = fiyatlar[int(len(fiyatlar) * 0.25)]
            p75 = fiyatlar[int(len(fiyatlar) * 0.75)]
            ozet = {
                "toplam_aktif_ilan": len(all_features),
                "medyan_gecelik_fiyat_tl": medyan,
                "fiyat_bandi_25_75": [p25, p75],
                "vade_medyan_fiyatlari": {
                    "1_gun_sonrasi_tl": med(v1g),
                    "1_hafta_sonrasi_tl": med(v1h),
                    "1_ay_sonrasi_tl": med(v1a),
                    "3_ay_sonrasi_tl": med(v3a),
                },
                "min_fiyat_tl": fiyatlar[0],
                "max_fiyat_tl": fiyatlar[-1],
                "tahmini_yillik_doluluk_orani": 0.60,
                "ortalama_yillik_net_nakit_akisi_tl": round(medyan * 365 * 0.60 * 0.45),
                "guncelleme_tarihi": datetime.now().isoformat()
            }
            with open(master_summary_path, "w", encoding="utf-8") as f:
                json.dump(ozet, f, ensure_ascii=False, indent=2)
            print(f"[✔] Master Airbnb Pazar Özeti yazıldı: {master_summary_path}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Doğrudan Python Airbnb Pazar & Çoklu Vade Potansiyel Toplayıcı")
    parser.add_argument("--bolge", type=str, help="Arama yapılacak tek bölge/şehir adı")
    parser.add_argument("--iller", "--bolgeler", dest="iller", type=str, help="Virgülle ayrılmış il/bölge listesi (örn: 'Bodrum, Marmaris' veya 'adana,adiyaman')")
    parser.add_argument("--cikis-ek", type=str, help="Çıktı dosyası grup eki (örn: 'grup_1')")
    parser.add_argument("--birlestir", action="store_true", help="Tüm parçalı 40 makine çıktılarını birleştir")
    parser.add_argument("--lat", type=float, help="Merkez enlem koordinatı")
    parser.add_argument("--lon", type=float, help="Merkez boylam koordinatı")
    parser.add_argument("--yaricap", type=float, default=3.0, help="Koordinat aramasında yarıçap (km)")
    parser.add_argument("--limit", type=int, default=30, help="Vade başına maksimum çekilecek ilan sayısı")
    parser.add_argument("--tek-tarih", action="store_true", help="4 vade yerine tek tarihli standart arama yap")
    parser.add_argument("--klasik-kira", type=float, help="Bölgedeki ortalama aylık klasik kira (TL)")
    parser.add_argument("--export", action="store_true", help="Sonuçları CSV ve GeoJSON olarak dışa aktar")

    args = parser.parse_args()

    if args.birlestir:
        birlestir_tum_sonuclari()
        return

    toplayici = AirbnbToplayici()

    if args.iller:
        bolge_list = [x.strip() for x in args.iller.split(",") if x.strip()]
        bolge_adi = args.cikis_ek or (bolge_list[0] if len(bolge_list) == 1 else f"{len(bolge_list)}_bolge")
        ilanlar = toplayici.sorgula_coklu_bolge(bolge_list, limit_per_bolge=args.limit, vadeli=not args.tek_tarih)
    elif args.lat is not None and args.lon is not None:
        bolge_adi = f"Koordinat_{args.lat:.4f}_{args.lon:.4f}"
        ilanlar = toplayici.sorgula_koordinat(args.lat, args.lon, args.yaricap)
    else:
        bolge_adi = args.bolge or "Bodrum, Muğla"
        if args.tek_tarih:
            ilanlar = toplayici.sorgula_bolge(bolge_adi)
        else:
            ilanlar = toplayici.sorgula_bolge_vadeli(bolge_adi, limit_per_vade=args.limit)

    analiz = toplayici.potansiyel_analizi_yap(
        ilanlar,
        bolge_adi=bolge_adi,
        klasik_aylik_kira_tl=args.klasik_kira
    )

    print("\n" + "=" * 65)
    print(f"  AIRBNB PAZAR & ÇOKLU ZAMAN VADELİ GETİRİ ANALİZİ: {bolge_adi}")
    print("=" * 65)
    print(f"• Toplam Tekil İlan Sayısı: {analiz.toplam_ilan_sayisi} adet")
    print(f"• Medyan Gecelik Fiyat    : {analiz.medyan_gecelik_tl:,.0f} TL / gece (Vade Ortalaması)")
    v = analiz.vade_fiyatlari
    f1g = f"{v.get('1_gun'):,.0f} TL" if v.get('1_gun') else "Veri yok"
    f1h = f"{v.get('1_hafta'):,.0f} TL" if v.get('1_hafta') else "Veri yok"
    f1a = f"{v.get('1_ay'):,.0f} TL" if v.get('1_ay') else "Veri yok"
    f3a = f"{v.get('3_ay'):,.0f} TL" if v.get('3_ay') else "Veri yok"
    print(f"  ├─ 📅 1 Gün Sonrası     : {f1g}")
    print(f"  ├─ 📅 1 Hafta Sonrası   : {f1h}")
    print(f"  ├─ 📅 1 Ay Sonrası      : {f1a}")
    print(f"  └─ 📅 3 Ay Sonrası      : {f3a}")
    print(f"• Gecelik Fiyat Bandı     : {analiz.fiyat_bandi_25_75_tl[0]:,.0f} TL - {analiz.fiyat_bandi_25_75_tl[1]:,.0f} TL")
    print(f"• Min / Max Fiyat         : {analiz.min_gecelik_tl:,.0f} TL - {analiz.max_gecelik_tl:,.0f} TL")
    print(f"• Ortalama Değerlendirme  : {analiz.ortalama_puan} / 5.0")
    print(f"• Oda Tipi Dağılımı       : {analiz.oda_tipi_dagilimi}")
    print(f"• Tahmini Yıllık Doluluk  : %{analiz.tahmini_yillik_doluluk_yuzde}")
    print(f"• Tahmini Yıllık Brüt     : {analiz.tahmini_yillik_brut_gelir_tl:,.0f} TL")
    print(f"• Tahmini Yıllık Net      : {analiz.tahmini_yillik_net_gelir_tl:,.0f} TL (Vergi/Giderler düşülmüş)")

    if analiz.klasik_kira_tl:
        print(f"• Klasik Yıllık Kira      : {analiz.klasik_kira_tl * 12:,.0f} TL")
        print(f"• Airbnb Prim Çarpanı     : {analiz.airbnb_prim_carpani}x (Net Airbnb / Klasik Kira)")
        if analiz.airbnb_prim_carpani >= 1.3:
            print("  -> KARAR: Airbnb yüksek prim üretiyor, operasyona değer.")
        else:
            print("  -> KARAR: Klasik kiralama operasyonel zahmet ve risk açısından daha avantajlı.")

    print(f"\n[Yasal Uyarı / 7464]: {analiz.yasal_7464_risk_durumu}")
    print("=" * 65)

    if (args.export or args.cikis_ek) and ilanlar:
        dosya_adi = f"airbnb_{args.cikis_ek}" if args.cikis_ek else re.sub(r"[^\w\-_]", "_", bolge_adi.lower())
        toplayici.export_csv(ilanlar, dosya_adi)
        toplayici.export_geojson(ilanlar, dosya_adi)


if __name__ == "__main__":
    main()


