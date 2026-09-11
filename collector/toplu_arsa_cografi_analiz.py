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
        ham = motor.ham_analiz_et(lat, lon)
        
        # 1. Topoğrafya Ham Verileri (Ölçüm & Açı)
        row["rakim_m"] = ham.rakim_m
        row["egim_yuzde"] = ham.egim_yuzde
        row["egim_derece"] = ham.egim_derece
        row["baki_derece"] = ham.baki_derece
        row["baki_kardinal"] = ham.baki_kardinal
        row["delta_z_3x3_m"] = ham.delta_z_3x3_m
        
        # 2. Su Altyapısı Ham Verileri (Mesafe, Ad ve Kot)
        row["sebeke_yerlesim_adi"] = ham.sebeke_yerlesim_adi
        row["sebeke_mesafe_m"] = ham.sebeke_mesafe_m
        row["en_yakin_kuyu_adi"] = ham.en_yakin_kuyu_adi or ""
        row["en_yakin_kuyu_mesafe_m"] = ham.en_yakin_kuyu_mesafe_m if ham.en_yakin_kuyu_mesafe_m is not None else ""
        row["en_yakin_kuyu_rakim_m"] = ham.en_yakin_kuyu_rakim_m if ham.en_yakin_kuyu_rakim_m is not None else ""
        row["yari_cap_3km_kuyu_sayisi"] = ham.yari_cap_3km_kuyu_sayisi
        row["en_yakin_pinar_adi"] = ham.en_yakin_pinar_adi or ""
        row["en_yakin_pinar_mesafe_m"] = ham.en_yakin_pinar_mesafe_m if ham.en_yakin_pinar_mesafe_m is not None else ""
        row["en_yakin_pinar_rakim_m"] = ham.en_yakin_pinar_rakim_m if ham.en_yakin_pinar_rakim_m is not None else ""
        row["yari_cap_3km_pinar_sayisi"] = ham.yari_cap_3km_pinar_sayisi
        row["en_yakin_kanal_adi"] = ham.en_yakin_kanal_adi or ""
        row["en_yakin_kanal_mesafe_m"] = ham.en_yakin_kanal_mesafe_m if ham.en_yakin_kanal_mesafe_m is not None else ""
        row["en_yakin_su_deposu_adi"] = ham.en_yakin_su_deposu_adi or ""
        row["en_yakin_su_deposu_mesafe_m"] = ham.en_yakin_su_deposu_mesafe_m if ham.en_yakin_su_deposu_mesafe_m is not None else ""
        
        # 3. Hidroloji Ham Verileri (Mesafe ve Kot Farkı)
        row["en_yakin_akarsu_adi"] = ham.en_yakin_akarsu_adi or ""
        row["en_yakin_akarsu_mesafe_m"] = ham.en_yakin_akarsu_mesafe_m if ham.en_yakin_akarsu_mesafe_m is not None else ""
        row["en_yakin_akarsu_rakim_m"] = ham.en_yakin_akarsu_rakim_m if ham.en_yakin_akarsu_rakim_m is not None else ""
        row["akarsu_kot_farki_m"] = ham.akarsu_kot_farki_m if ham.akarsu_kot_farki_m is not None else ""
        row["en_yakin_kuru_dere_adi"] = ham.en_yakin_kuru_dere_adi or ""
        row["en_yakin_kuru_dere_mesafe_m"] = ham.en_yakin_kuru_dere_mesafe_m if ham.en_yakin_kuru_dere_mesafe_m is not None else ""
        row["en_yakin_gol_baraj_adi"] = ham.en_yakin_gol_baraj_adi or ""
        row["en_yakin_gol_baraj_mesafe_m"] = ham.en_yakin_gol_baraj_mesafe_m if ham.en_yakin_gol_baraj_mesafe_m is not None else ""
        
        # 4. Fay Ham Verileri (Mesafe, Ad ve Tür)
        row["diri_fay_adi"] = ham.diri_fay_adi
        row["diri_fay_sistemi"] = ham.diri_fay_sistemi
        row["diri_fay_tipi"] = ham.diri_fay_tipi
        row["diri_fay_mesafesi_km"] = ham.diri_fay_mesafesi_km
    except Exception as e:
        row["hata"] = str(e)

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
        print(f"  • {item['mahalle']:<20} | Eğim: %{item.get('egim_yuzde')} (Bakı: {item.get('baki_kardinal')} {item.get('baki_derece')}°) | Şebeke: {item.get('sebeke_mesafe_m')}m | Akarsu: {item.get('en_yakin_akarsu_mesafe_m')}m (Kot farkı: {item.get('akarsu_kot_farki_m')}m) | Fay: {item.get('diri_fay_mesafesi_km')}km")

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
