#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Türkiye 40 Sektör CBS Vektör Birleştirici
=====================================================
40 GitHub Action makinesinden (veya yerel işlemlerden) gelen sektör GeoJSON
dosyalarını toplar, mükerrer kayıtları (OSM ID ve Fay ID) ayıklar, katmanlarına
göre organize eder ve harita üzerinde tek tıkla açılabilecek master GeoJSON katmanlarını üretir.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
INPUT_SEKTORLER_DIR = DATA_DIR / "vektor_sektorler"
OUTPUT_MASTER_DIR = DATA_DIR / "master_harita_katmanlari"

OUTPUT_MASTER_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    symbols = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌", "STEP": "🚀"}
    prefix = symbols.get(level, "•")
    print(f"[{now_str}] {prefix} {msg}", flush=True)


KATMAN_ESLESTIRME = {
    "DİRİ_FAY_HATTI": "turkiye_tam_diri_faylar.geojson",
    "ELEKTRIK_HATTI": "turkiye_tam_elektrik_hatlari.geojson",
    "MEVCUT_YOL": "turkiye_tam_yollar_ve_projeler.geojson",
    "PLANLANAN_YOL_PROJESI": "turkiye_tam_yollar_ve_projeler.geojson",
    "DEMIRYOLU": "turkiye_tam_demiryollari.geojson",
    "SIT_VE_KORUNAN_ALAN": "turkiye_tam_sit_alanlari.geojson",
    "SAHIL_SERIDI": "turkiye_tam_sahil_seritleri.geojson",
    "ORMAN_ALANI": "turkiye_tam_orman_alanlari.geojson",
    "SU_YOLU_DERE": "turkiye_tam_su_yollari_ve_kanallar.geojson",
    "GOL_BARAJ_HAZNE": "turkiye_tam_su_yollari_ve_kanallar.geojson",
    "SU_BORU_HATTI": "turkiye_tam_su_yollari_ve_kanallar.geojson",
    "SU_KUYUSU": "turkiye_tam_su_noktalari_ve_kuyular.geojson",
    "DOGAL_PINAR": "turkiye_tam_su_noktalari_ve_kuyular.geojson",
    "CESME_ICME_SUYU": "turkiye_tam_su_noktalari_ve_kuyular.geojson",
}


def birlestir_sektorler(girdi_dizini: Optional[Path] = None) -> None:
    input_dir = girdi_dizini or INPUT_SEKTORLER_DIR
    if not input_dir.exists():
        log(f"Girdi dizini bulunamadı: {input_dir}", "ERROR")
        return

    geojson_dosyalari = sorted(list(input_dir.glob("sektor_*.geojson")))
    if not geojson_dosyalari:
        log(f"{input_dir} dizininde 'sektor_*.geojson' dosyası bulunamadı!", "WARN")
        return

    log("=" * 80, "STEP")
    log(f"40 SEKTÖR VEKTÖRLERİ BİRLEŞTİRİLİYOR (Toplam {len(geojson_dosyalari)} Dosya)", "STEP")
    log("=" * 80, "STEP")

    gorulen_idler: Set[str] = set()
    ayrilmis_katmanlar: Dict[str, List[Dict[str, Any]]] = {v: [] for v in set(KATMAN_ESLESTIRME.values())}
    tum_birlesik_features: List[Dict[str, Any]] = []

    for fpath in geojson_dosyalari:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            features = data.get("features", [])
            eklenen = 0
            for feat in features:
                props = feat.get("properties", {})
                # Unique ID: ya props["id"] ya da fay_id
                fid = props.get("id") or props.get("fay_id")
                if not fid:
                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    fid = f"{props.get('katman')}_{len(coords)}"

                if fid in gorulen_idler:
                    continue  # Sektör sınırındaki mükerrer kesişimi engelle

                gorulen_idler.add(fid)
                tum_birlesik_features.append(feat)

                katman = props.get("katman", "")
                hedef_dosya = KATMAN_ESLESTIRME.get(katman, "turkiye_diger_vektorler.geojson")
                if hedef_dosya not in ayrilmis_katmanlar:
                    ayrilmis_katmanlar[hedef_dosya] = []
                ayrilmis_katmanlar[hedef_dosya].append(feat)
                eklenen += 1

            log(f"  • {fpath.name:<45} : +{eklenen:,} yeni tekil vektör", "INFO")
        except Exception as e:
            log(f"Hata ({fpath.name}): {e}", "ERROR")

    log("-" * 80, "STEP")
    log(f"Toplam {len(gorulen_idler):,} benzersiz vektör poligon ve çizgisi birleştirildi.", "SUCCESS")
    log("-" * 80, "STEP")

    # 1. Her bir tematik katmanı ayrı GeoJSON olarak kaydet
    for dosya_adi, feats in ayrilmis_katmanlar.items():
        if not feats:
            continue
        out_path = OUTPUT_MASTER_DIR / dosya_adi
        out_json = {
            "type": "FeatureCollection",
            "name": dosya_adi.replace(".geojson", ""),
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "toplam_nesne": len(feats),
            "olusturma_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "features": feats
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_json, f, ensure_ascii=False)

        boyut_mb = out_path.stat().st_size / (1024 * 1024)
        log(f"  ✓ {dosya_adi:<38} : {len(feats):>7,} nesne ({boyut_mb:6.2f} MB)", "SUCCESS")

    # 2. Tek birleşik master GeoJSON dosyası da oluştur
    master_tum_path = OUTPUT_MASTER_DIR / "turkiye_tam_birlesik_envanter.geojson"
    master_tum_json = {
        "type": "FeatureCollection",
        "name": "Turkiye_Tam_Fiziksel_ve_Hukuki_Altyapi_Envanteri",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "toplam_nesne": len(tum_birlesik_features),
        "olusturma_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "features": tum_birlesik_features
    }
    with open(master_tum_path, "w", encoding="utf-8") as f:
        json.dump(master_tum_json, f, ensure_ascii=False)

    master_mb = master_tum_path.stat().st_size / (1024 * 1024)
    log(f"  ★ BİRLEŞİK TÜM KATMANLAR GEOJSON : {len(tum_birlesik_features):,} nesne ({master_mb:.2f} MB)", "SUCCESS")
    log(f"Master Dizin: {OUTPUT_MASTER_DIR}", "INFO")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="40 Sektör GeoJSON Birleştirici")
    parser.add_argument("--girdi", type=str, default=None, help="Sektör GeoJSON dosyalarının bulunduğu dizin")
    args = parser.parse_args()

    girdi_path = Path(args.girdi) if args.girdi else None
    birlestir_sektorler(girdi_path)
