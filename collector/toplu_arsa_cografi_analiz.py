#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Toplu Arsa ve Parsel 1. Grup Coğrafi & Su Altyapısı Zenginleştirici
=============================================================================
CSV dosyalarını veya ilçe/mahalle listelerini okur; her bir kayıt için:
  - Eğim (%), Eğim Açısı (°), Zemin Sınıfı
  - Bakı Yönü (Güney, Kuzey vb.), Güneşlenme Puanı
  - Su Varlığı ("Arsada Su Var mı?"), Şebeke Mesafesi, Kuyu/Artezyen Potansiyeli
  - Taşkın & Sel Riski, Akarsu Mesafesi
  - Diri Fay Mesafesi (km) ve Sismik Kuşak
  - 1. Grup Birleşik Fiziksel Arazi Skoru (0 - 100)
hesaplayarak CSV ve SQLite veritabanına işler.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from cografi_ve_altyapi_motoru import CografiVeAltyapiMotoru

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
OUTPUT_CSV_DIR = BASE_DIR / "data" / "csv_ciktilari"
OUTPUT_CSV_DIR.mkdir(parents=True, exist_ok=True)
MAHALLE_KOORD_PATH = BASE_DIR / "mahalle_koordinatlari.json" if (BASE_DIR / "mahalle_koordinatlari.json").exists() else BASE_DIR / "data" / "mahalle_koordinatlari.json"


def log(msg: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    symbols = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌", "STEP": "🚀"}
    prefix = symbols.get(level, "•")
    print(f"[{now_str}] {prefix} {msg}", flush=True)


def islem_yap_tekil(row: Dict[str, Any], motor: CografiVeAltyapiMotoru) -> Dict[str, Any]:
    """Tek bir satır için 1. Grup coğrafi ve su analizini yapar ve satırı zenginleştirir."""
    # Enlem ve boylam sütunlarını bul
    lat_keys = ["lat", "enlem", "latitude", "Lat", "Enlem"]
    lon_keys = ["lon", "lng", "boylam", "longitude", "Long", "Boylam"]

    lat = None
    lon = None

    for k in lat_keys:
        if k in row and row[k]:
            try:
                lat = float(row[k])
                break
            except ValueError:
                pass

    for k in lon_keys:
        if k in row and row[k]:
            try:
                lon = float(row[k])
                break
            except ValueError:
                pass

    if lat is None or lon is None:
        return row

    try:
        rapor = motor.analiz_et(lat, lon, canli_osm_tara=False)
        row["rakim_m"] = rapor.topografya.rakim_m
        row["egim_yuzde"] = rapor.topografya.egim_yuzde
        row["egim_derece"] = rapor.topografya.egim_derece
        row["egim_sinifi"] = rapor.topografya.egim_sinifi
        row["hafriyat_etkisi"] = rapor.topografya.insaat_hafriyat_etkisi
        row["baki_yonu"] = rapor.topografya.baki_yonu
        row["baki_derece"] = rapor.topografya.baki_derece
        row["guneslenme_skoru"] = rapor.topografya.guneslenme_skoru
        
        row["arsada_su_var_mi"] = rapor.su_altyapisi.arsa_su_durumu
        row["su_guvenlik_skoru"] = rapor.su_altyapisi.su_guvenlik_skoru
        row["sebeke_durumu"] = rapor.su_altyapisi.sebeke_durumu
        row["sebeke_mesafe_m"] = round(rapor.su_altyapisi.en_yakin_sebeke_mesafe_m, 0)
        row["yeralti_suyu_potansiyeli"] = rapor.su_altyapisi.yeralti_suyu_potansiyeli
        row["sondaj_derinligi_m"] = rapor.su_altyapisi.tahmini_sondaj_derinligi_m
        row["tarimsal_sulama_imkani"] = rapor.su_altyapisi.tarimsal_sulama_imkani
        row["su_temin_onerisi"] = rapor.su_altyapisi.su_temin_onerisi
        
        row["taskin_riski"] = rapor.taskin_riski.taskin_riski_derecesi
        row["en_yakin_akarsu_m"] = round(rapor.taskin_riski.akarsu_mesafe_m, 0) if rapor.taskin_riski.akarsu_mesafe_m else ""
        row["diri_fay_adi"] = rapor.deprem_ve_fay.en_yakin_fay_adi
        row["fay_mesafesi_km"] = rapor.deprem_ve_fay.fay_mesafesi_km
        row["sismik_risk"] = rapor.deprem_ve_fay.sismik_risk_derecesi
        
        row["arazi_fiziksel_puani"] = rapor.arazi_fiziksel_kalite_puani
        row["arazi_kalite_sinifi"] = rapor.arazi_kalite_sinifi
    except Exception as e:
        row["cografi_hata"] = str(e)

    return row


def zenginlestir_csv(girdi_csv: Path, cikti_csv: Path, max_workers: int = 8, limit: Optional[int] = None) -> None:
    """Mevcut bir arsa CSV dosyasını 1. Grup coğrafi ve su verileriyle zenginleştirir."""
    if not girdi_csv.exists():
        log(f"Girdi dosyası bulunamadı: {girdi_csv}", "ERROR")
        return

    log(f"CSV Okunuyor: {girdi_csv.name}", "STEP")
    rows: List[Dict[str, Any]] = []
    with open(girdi_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if limit and idx >= limit:
                break
            rows.append(dict(row))

    total = len(rows)
    log(f"Toplam {total:,} arsa kaydı analiz edilecek (İş Parçacığı Sayısı: {max_workers})...", "INFO")

    motor = CografiVeAltyapiMotoru()
    zenginlestirilmis: List[Dict[str, Any]] = []
    tamamlanan = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(islem_yap_tekil, r, motor): r for r in rows}
        for future in as_completed(future_map):
            res = future.result()
            zenginlestirilmis.append(res)
            tamamlanan += 1
            if tamamlanan % 25 == 0 or tamamlanan == total:
                elapsed = time.time() - t0
                speed = tamamlanan / max(0.1, elapsed)
                log(f"İlerleme: {tamamlanan:,} / {total:,} (%{tamamlanan * 100 / total:.1f}) | Hız: {speed:.1f} arsa/sn", "INFO")

    if not zenginlestirilmis:
        log("İşlenecek kayıt bulunamadı.", "WARN")
        return

    fieldnames = list(zenginlestirilmis[0].keys())
    with open(cikti_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(zenginlestirilmis)

    log(f"Zenginleştirilmiş CSV Kaydedildi: {cikti_csv}", "SUCCESS")
    log(f"Toplam Süre: {time.time() - t0:.1f} saniye", "SUCCESS")


def ornek_ilce_analizi(il_adi: str, ilce_adi: str, limit: int = 15) -> None:
    """Verilen il ve ilçedeki mahalleler için örnek 1. grup ve su varlığı analizi oluşturur."""
    if not MAHALLE_KOORD_PATH.exists():
        log(f"Mahalle koordinat dosyası bulunamadı: {MAHALLE_KOORD_PATH}", "ERROR")
        return

    with open(MAHALLE_KOORD_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # İlgili il/ilçeyi filtrele
    search_prefix = f"{il_adi.lower()}_{ilce_adi.lower()}_"
    secilenler = []
    for key, val in data.items():
        if key.startswith(search_prefix):
            secilenler.append({
                "il": il_adi.title(),
                "ilce": ilce_adi.title(),
                "mahalle": val.get("name"),
                "lat": val.get("lat"),
                "lon": val.get("lon")
            })
            if len(secilenler) >= limit:
                break

    if not secilenler:
        # Alternatif filtreleme
        for key, val in data.items():
            if ilce_adi.lower() in key:
                secilenler.append({
                    "il": il_adi.title(),
                    "ilce": ilce_adi.title(),
                    "mahalle": val.get("name"),
                    "lat": val.get("lat"),
                    "lon": val.get("lon")
                })
                if len(secilenler) >= limit:
                    break

    log(f"{il_adi.title()} - {ilce_adi.title()} için {len(secilenler)} mahalle/arsa noktası analiz ediliyor...", "STEP")
    motor = CografiVeAltyapiMotoru()
    sonuclar = []

    for item in secilenler:
        res = islem_yap_tekil(item, motor)
        sonuclar.append(res)
        print(f"  • {item['mahalle']:<20} | Su: {item.get('arsada_su_var_mi', '')[:35]} | Eğim: %{item.get('egim_yuzde', '')} ({item.get('baki_yonu', '')}) | Puan: {item.get('arazi_fiziksel_puani')}/100")

    cikti_dosya = OUTPUT_CSV_DIR / f"{il_adi.lower()}_{ilce_adi.lower()}_1_grup_cografi_ve_su_analizi.csv"
    if sonuclar:
        with open(cikti_dosya, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(sonuclar[0].keys()))
            writer.writeheader()
            writer.writerows(sonuclar)
        log(f"Çıktı kaydedildi: {cikti_dosya}", "SUCCESS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Toplu 1. Grup Coğrafi ve Su Altyapısı Analizi")
    parser.add_argument("--girdi", type=str, help="Girdi CSV dosya yolu")
    parser.add_argument("--cikti", type=str, help="Çıktı CSV dosya yolu")
    parser.add_argument("--il", type=str, default="istanbul", help="Örnek il adı")
    parser.add_argument("--ilce", type=str, default="silivri", help="Örnek ilçe adı")
    parser.add_argument("--limit", type=int, default=15, help="Analiz edilecek maksimum kayıt sayısı")
    parser.add_argument("--workers", type=int, default=8, help="Paralel iş parçacığı sayısı")
    args = parser.parse_args()

    if args.girdi:
        girdi_p = Path(args.girdi)
        cikti_p = Path(args.cikti) if args.cikti else OUTPUT_CSV_DIR / f"zenginlestirilmis_{girdi_p.name}"
        zenginlestir_csv(girdi_p, cikti_p, max_workers=args.workers, limit=args.limit)
    else:
        ornek_ilce_analizi(args.il, args.ilce, limit=args.limit)
