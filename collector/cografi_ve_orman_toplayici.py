#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tüm Türkiye Dağlar, Zirveler, Ormanlar ve Coğrafi Varlıklar Toplayıcısı
----------------------------------------------------------------------
Türkiye'nin 81 ilindeki tüm:
- Dağlar ve Sıradağlar (T.MT, T.MTS)
- Zirveler ve Doruklar (T.PK)
- Tepeler ve Sırtlar (T.HLL, T.RDGE)
- Yaylalar ve Platolar (T.UPLD)
- Ovalar ve Vadiler (T.PLN, T.VAL)
- Ormanlar, Koruluklar ve Ağaçlandırma Alanları (V.FRST)
- Milli Parklar ve Tabiat Parkları (L.PRK, L.RESN)
- Göller, Barajlar ve Nehirler (H.LK, H.RSV, H.STM)

verilerini resmi koordinatları, metre cinsinden rakımları (elevation/DEM),
bağlı oldukları il/ilçe bilgileri ve GeoJSON/SQLite/CSV formatlarıyla toplar.
"""

import sys
import io
import json
import sqlite3
import zipfile
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cografya"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SQLITE_PATH = DATA_DIR / "turkiye_cografi_varliklar.sqlite"
GEOJSON_DAGLAR = DATA_DIR / "turkiye_daglar_ve_zirveler.geojson"
GEOJSON_ORMANLAR = DATA_DIR / "turkiye_ormanlar_ve_korunan_alanlar.geojson"
CSV_OZET = DATA_DIR / "turkiye_cografi_varliklar_ozet.csv"

# GeoNames resmi Türkiye coğrafi isim ve varlık veri tabanı dump'ı
GEONAMES_TR_URL = "http://download.geonames.org/export/dump/TR.zip"

# Orman Genel Müdürlüğü (OGM) ve Çevre Şehircilik Bakanlığı Resmi WMS/WFS Harita Servisleri
ORMAN_WMS_SERVISLERI = {
    "OGM_ORMAN_VARLIGI": {
        "aciklama": "Orman Genel Müdürlüğü - Türkiye Orman Varlığı ve Meşcere Katmanı",
        "wms_url": "https://webcbs.ogm.gov.tr/arcgis/services/OrmanVarligi/MapServer/WMSServer",
        "katmanlar": "0,1,2",
        "format": "image/png",
        "seffaflik": True
    },
    "CORINE_LAND_COVER_TURKIYE": {
        "aciklama": "Copernicus & ÇŞİDB CORINE Arazi Örtüsü (Ormanlar, Çalılıklar, Kayalıklar)",
        "wms_url": "https://image.discomap.eea.europa.eu/arcgis/services/Corine/CLC2018_WM/MapServer/WMSServer",
        "katmanlar": "14", # 311, 312, 313 Orman Sınıfları
        "format": "image/png",
        "seffaflik": True
    },
    "TKGM_PARSEL_WMS": {
        "aciklama": "Tapu ve Kadastro Genel Müdürlüğü Canlı Parsel ve Orman Kadastrosu Sınırları",
        "wms_url": "https://www.kolayimar.com/api/geo-proxy/map?slug=parsel&layers=TKGM:parseller",
        "format": "image/png"
    }
}

FEATURE_NAMES = {
    "T.MT": "Dağ",
    "T.MTS": "Sıradağlar",
    "T.PK": "Zirve / Doruk",
    "T.HLL": "Tepe",
    "T.RDGE": "Sırt / Tepe Sırası",
    "T.UPLD": "Yayla / Plato",
    "T.PLN": "Ova",
    "T.VAL": "Vadi",
    "T.PASS": "Dağ Geçidi",
    "T.RK": "Kaya / Kayalık Alan",
    "V.FRST": "Orman",
    "L.PRK": "Milli Park / Tabiat Parkı",
    "L.RESN": "Doğa Koruma Alanı / Rezerv",
    "H.LK": "Göl",
    "H.RSV": "Baraj Gölü / Rezervuar",
    "H.STM": "Nehir / Akarsu"
}

def init_database():
    """SQLite veri tabanını ve indekslerini hazırlar."""
    conn = sqlite3.connect(SQLITE_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cografi_varliklar (
            id INTEGER PRIMARY KEY,
            geoname_id INTEGER UNIQUE,
            ad TEXT NOT NULL,
            asciiname TEXT,
            tur_kodu TEXT NOT NULL,
            tur_aciklama TEXT,
            kategori TEXT NOT NULL,
            enlem REAL NOT NULL,
            boylam REAL NOT NULL,
            rakim_m INTEGER,
            il_plaka TEXT,
            son_guncelleme TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kategori ON cografi_varliklar(kategori)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tur_kodu ON cografi_varliklar(tur_kodu)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_il_plaka ON cografi_varliklar(il_plaka)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rakim ON cografi_varliklar(rakim_m)")
    conn.commit()
    conn.close()

def download_and_parse():
    """GeoNames TR veri setini indirip dağlar, ormanlar ve tabiat varlıklarını filtreler."""
    print("=" * 70)
    print("🌲 TÜM TÜRKİYE DAĞLAR, ORMANLAR VE COĞRAFİ VARLIKLAR TOPLAYICISI")
    print("=" * 70)
    print(f"📡 Kaynak indiriliyor: {GEONAMES_TR_URL} ...")

    req = urllib.request.Request(GEONAMES_TR_URL, headers={"User-Agent": "Mozilla/5.0 (TKGM Geographic Engine)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        zip_bytes = resp.read()
    
    print(f"📦 İndirme tamamlandı ({len(zip_bytes) / 1024 / 1024:.2f} MB). Ayrıştırılıyor...")

    init_database()
    conn = sqlite3.connect(SQLITE_PATH)
    cur = conn.cursor()

    daglar_zirveler = []
    ormanlar_parklar = []
    tum_kayitlar = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        with z.open("TR.txt") as f:
            for line_idx, line in enumerate(f):
                parts = line.decode("utf-8", errors="ignore").strip().split("\t")
                if len(parts) < 17:
                    continue

                gid = int(parts[0])
                name = parts[1]
                asciiname = parts[2]
                lat = float(parts[4])
                lon = float(parts[5])
                fc = parts[6]
                fcode = parts[7]
                il_plaka = parts[10]
                
                try:
                    dem_rakim = int(parts[16]) if parts[16] else None
                except ValueError:
                    dem_rakim = None

                code_key = f"{fc}.{fcode}"
                if code_key not in FEATURE_NAMES:
                    continue

                tur_aciklama = FEATURE_NAMES[code_key]

                # Kategori belirleme
                if fc == "T" and fcode in ["MT", "MTS", "PK", "HLL", "RDGE", "PASS", "RK"]:
                    kategori = "DAG_VE_TEPE"
                elif fc == "T" and fcode in ["UPLD", "PLN", "VAL"]:
                    kategori = "YAYLA_VE_OVA"
                elif fc in ["V", "L"] or fcode in ["FRST", "PRK", "RESN"]:
                    kategori = "ORMAN_VE_KORUMA_ALANI"
                elif fc == "H":
                    kategori = "SU_KUTLESI"
                else:
                    kategori = "DIGER_DOGAL_ALAN"

                record = {
                    "geoname_id": gid,
                    "ad": name,
                    "asciiname": asciiname,
                    "tur_kodu": code_key,
                    "tur_aciklama": tur_aciklama,
                    "kategori": kategori,
                    "enlem": lat,
                    "boylam": lon,
                    "rakim_m": dem_rakim,
                    "il_plaka": il_plaka
                }

                tum_kayitlar.append(record)

                # GeoJSON Feature hazırlığı
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "id": gid,
                        "ad": name,
                        "tur": tur_aciklama,
                        "kategori": kategori,
                        "rakim_metre": dem_rakim,
                        "il_plaka": il_plaka
                    }
                }

                if kategori == "DAG_VE_TEPE":
                    daglar_zirveler.append(feature)
                elif kategori == "ORMAN_VE_KORUMA_ALANI":
                    ormanlar_parklar.append(feature)

    print(f"📊 Toplam Ayrıştırılan Coğrafi Varlık: {len(tum_kayitlar)}")
    print(f"   - Dağlar, Zirveler ve Tepeler: {len(daglar_zirveler)}")
    print(f"   - Ormanlar ve Koruma Alanları: {len(ormanlar_parklar)}")

    # SQLite'a kaydet
    print("💾 SQLite veri tabanına yazılıyor...")
    cur.executemany("""
        INSERT OR REPLACE INTO cografi_varliklar (
            geoname_id, ad, asciiname, tur_kodu, tur_aciklama,
            kategori, enlem, boylam, rakim_m, il_plaka, son_guncelleme
        ) VALUES (
            :geoname_id, :ad, :asciiname, :tur_kodu, :tur_aciklama,
            :kategori, :enlem, :boylam, :rakim_m, :il_plaka, datetime('now')
        )
    """, tum_kayitlar)
    conn.commit()
    conn.close()

    # GeoJSON dosyalarını yaz
    print(f"🗺️ GeoJSON çıktısı yazılıyor: {GEOJSON_DAGLAR.name} ...")
    with open(GEOJSON_DAGLAR, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": daglar_zirveler}, f, ensure_ascii=False, indent=2)

    print(f"🌲 GeoJSON çıktısı yazılıyor: {GEOJSON_ORMANLAR.name} ...")
    with open(GEOJSON_ORMANLAR, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": ormanlar_parklar}, f, ensure_ascii=False, indent=2)

    # Özet CSV yaz
    print(f"📄 CSV özeti yazılıyor: {CSV_OZET.name} ...")
    with open(CSV_OZET, "w", encoding="utf-8") as f:
        f.write("id,ad,tur,kategori,enlem,boylam,rakim_m,il_plaka\n")
        for r in sorted(tum_kayitlar, key=lambda x: x["rakim_m"] or 0, reverse=True):
            ad_clean = r["ad"].replace(",", " ")
            f.write(f"{r['geoname_id']},{ad_clean},{r['tur_aciklama']},{r['kategori']},{r['enlem']},{r['boylam']},{r['rakim_m'] or ''},{r['il_plaka']}\n")

    print("=" * 70)
    print("✅ TÜM DAĞLAR VE ORMANLAR BAŞARIYLA DERLENDİ!")
    print(f"📁 Dosyalar: {DATA_DIR}")
    print("=" * 70)

if __name__ == "__main__":
    download_and_parse()
