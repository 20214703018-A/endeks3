#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Bölgesel İmar Tipolojisi ve Kural Motoru
------------------------------------------------------
Bu modül, e-Plan API'sinde sayısal KAKS / TAKS / Kat Adedi vektör sütunları
bulunmadığında veya belediye plan notlarına atıfta bulunulduğunda devreye giren
yüksek güvenilirlikli bölgesel imar bilgi bankasıdır.

Türkiye'nin en aktif yatırım ve imar bölgelerindeki (İstanbul, Ankara, Antalya,
İzmir, Muğla, Bursa, Sakarya, Kocaeli vb.) 1/1000 Uygulama İmar Planı yerleşik
hükümlerini normalize edilmiş mahalle/bölge eşleştirmesiyle sunar.
"""

import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional


def normalize_text(text: Optional[str]) -> str:
    """Türkçe karakterleri ve boşlukları standart karşılaştırma formatına getirir."""
    if not text:
        return ""
    text = str(text).strip()
    tr_map = {
        "İ": "i", "I": "ı", "ı": "i",
        "Ş": "s", "ş": "s",
        "Ğ": "g", "ğ": "g",
        "Ü": "u", "ü": "u",
        "Ö": "o", "ö": "o",
        "Ç": "c", "ç": "c",
    }
    for k, v in tr_map.items():
        text = text.replace(k, v)
    text = text.lower()
    # Harf ve rakam dışındaki karakterleri temizle
    text = re.sub(r"[^a-z0-9]", "", text)
    return text


class BolgeselImarMotoru:
    """Bölgesel imar tipolojisi arama ve kural motoru."""

    def __init__(self, json_path: Optional[str] = None):
        if json_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(base_dir, "data", "bolgesel_imar_tipolojisi.json")
        self.json_path = json_path
        self.kayitlar: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.kayitlar = json.load(f)
            except Exception as e:
                print(f"[!] Tipoloji JSON okuma hatası: {e}")
                self.kayitlar = []
        else:
            self.kayitlar = []

    def tipoloji_bul(self, il: str, ilce: str, mahalle: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """İl, ilçe ve mahalle için en uygun imar tipolojisi kuralını döndürür."""
        norm_il = normalize_text(il)
        norm_ilce = normalize_text(ilce)
        norm_mahalle = normalize_text(mahalle) if mahalle else ""

        # 1. Tam Eşleşme (İl + İlçe + Mahalle)
        for k in self.kayitlar:
            k_il = normalize_text(k.get("il"))
            k_ilce = normalize_text(k.get("ilce"))
            k_mahalle = normalize_text(k.get("mahalle"))
            k_alt = normalize_text(k.get("alt_bolge"))

            if k_il == norm_il and k_ilce == norm_ilce:
                if norm_mahalle and (norm_mahalle in k_mahalle or k_mahalle in norm_mahalle or norm_mahalle in k_alt):
                    return {**k, "eslesme_tipi": "tam_eslesme", "guven": "Yuksek"}

        # 2. İlçe ve Alt Bölge / Mahalle Kısmi Eşleşmesi
        if norm_mahalle:
            for k in self.kayitlar:
                k_il = normalize_text(k.get("il"))
                k_ilce = normalize_text(k.get("ilce"))
                k_mahalle = normalize_text(k.get("mahalle"))
                k_alt = normalize_text(k.get("alt_bolge"))

                if k_il == norm_il and (norm_mahalle in k_mahalle or norm_mahalle in k_alt or k_mahalle in norm_mahalle):
                    return {**k, "eslesme_tipi": "mahalle_eslesme", "guven": "Yuksek"}

        # 3. İl ve İlçe Geneli Tipoloji
        for k in self.kayitlar:
            k_il = normalize_text(k.get("il"))
            k_ilce = normalize_text(k.get("ilce"))
            if k_il == norm_il and k_ilce == norm_ilce:
                return {**k, "eslesme_tipi": "ilce_geneli_tipoloji", "guven": "Orta"}

        return None

    def karakter_ve_kat_analizi(self, il: str, ilce: str, mahalle: Optional[str] = None) -> Dict[str, Any]:
        """Bir mahalle veya bölgenin villa, yüksek kat ve ortalama kat analizini çıkarır."""
        tipoloji = self.tipoloji_bul(il, ilce, mahalle)
        if not tipoloji:
            return {
                "bulundu": False,
                "mesaj": "Bölge için tipoloji kuralı henüz tanımlanmamış. Belediye çapı gerekli."
            }

        kat = tipoloji.get("kat_adedi", 0)
        try:
            kat_num = float(kat)
        except (ValueError, TypeError):
            kat_num = 2.0

        hmax = tipoloji.get("hmax_metre", 6.50)
        kaks = tipoloji.get("kaks_emsal", 0.60)

        is_villa = kat_num <= 2.5 and ("villa" in tipoloji.get("bolge_karakteri", "").lower() or "villa" in tipoloji.get("tipik_mimari", "").lower() or kaks <= 0.60)
        is_yuksek_kat = kat_num >= 8.0 or hmax >= 25.0 or "rezidans" in tipoloji.get("bolge_karakteri", "").lower()

        if is_villa:
            kategori = "VİLLA & MÜSTAKİL KONUT BÖLGESİ"
            kategori_kodu = "villa"
        elif is_yuksek_kat:
            kategori = "YÜKSEK KATLI REZİDANS & KULE BÖLGESİ"
            kategori_kodu = "yuksek_kat"
        else:
            kategori = "ORTA KATLI APARTMAN & ŞEHİR SİTESİ BÖLGESİ"
            kategori_kodu = "orta_kat"

        return {
            "bulundu": True,
            "il": tipoloji.get("il"),
            "ilce": tipoloji.get("ilce"),
            "mahalle": tipoloji.get("mahalle"),
            "alt_bolge": tipoloji.get("alt_bolge"),
            "bolge_tipi": kategori,
            "kategori_kodu": kategori_kodu,
            "villa_bolgesi_mi": is_villa,
            "yuksek_kat_bolgesi_mi": is_yuksek_kat,
            "ortalama_kat_sayisi": kat_num,
            "hmax_metre": hmax,
            "kaks_emsal": kaks,
            "taks": tipoloji.get("taks"),
            "yapi_nizami": tipoloji.get("yapi_nizami"),
            "bolge_karakteri": tipoloji.get("bolge_karakteri"),
            "tipik_mimari": tipoloji.get("tipik_mimari"),
            "guven": tipoloji.get("guven", "Yuksek"),
            "kaynak": tipoloji.get("yasal_dayanak_ve_plan_notu")
        }

    def bolgeleri_filtrele(self, kategori_kodu: str = "villa", il: Optional[str] = None) -> List[Dict[str, Any]]:
        """Kategoriye ('villa', 'yuksek_kat', 'orta_kat') ve opsiyonel ile göre bölgeleri listeler."""
        sonuclar = []
        for k in self.kayitlar:
            analiz = self.karakter_ve_kat_analizi(k["il"], k["ilce"], k.get("mahalle"))
            if analiz.get("kategori_kodu") == kategori_kodu:
                if not il or normalize_text(k["il"]) == normalize_text(il):
                    sonuclar.append(analiz)
        return sonuclar


# Singleton instance
_motor = None

def get_bolgesel_imar_motoru() -> BolgeselImarMotoru:
    global _motor
    if _motor is None:
        _motor = BolgeselImarMotoru()
    return _motor


if __name__ == "__main__":
    motor = get_bolgesel_imar_motoru()
    print("Yüklü Tipoloji Sayısı:", len(motor.kayitlar))
    
    testler = [
        ("Antalya", "Döşemealtı", "Altınkale"),
        ("İstanbul", "Sarıyer", "Zekeriyaköy"),
        ("İzmir", "Çeşme", "Alaçatı"),
        ("Muğla", "Bodrum", "Yalıkavak"),
        ("Ankara", "Gölbaşı", "İncek"),
    ]
    for il, ilce, mah in testler:
        sonuc = motor.tipoloji_bul(il, ilce, mah)
        if sonuc:
            print(f"[{il}/{ilce}/{mah}] -> KAKS: {sonuc['kaks_emsal']}, TAKS: {sonuc['taks']}, Kat: {sonuc['kat_adedi']}, Nizam: {sonuc['yapi_nizami']}")
        else:
            print(f"[{il}/{ilce}/{mah}] -> Bulunamadı")
