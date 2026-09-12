#!/usr/bin/env python3
"""
tuik_il_bazli_satis_istatistikleri.py
------------------------------------
Türkiye'nin 81 iline ait resmi kamuya açık gayrimenkul ve konut satış verilerini,
ilk el satış adetlerini, yapı ruhsatı ve iskan izinlerini TÜİK CIP açık veri
servisinden otomatik olarak çeker, CSV, JSON ve SQLite'a aktarır.

Araştırma ve analiz amaçlı kamuya açık resmi veri kaynağı:
- Kaynak: TÜİK Mekânsal İstatistik Portalı (CIP) & İdari Kayıtlar
- Göstergeler:
  * INS-GK055-O006: Konut Satış Sayıları (Toplam)
  * INS-GK056-O006: Konut Satış Sayıları (İlk Satış / Sıfır Konut)
  * INS-GK058-O006: Yapı Ruhsatına Göre Daire Sayısı (Yeni İnşaat Potansiyeli)
  * INS-GK061-O006: Yapı Kullanma İzin Belgesine (İskan) Göre Daire Sayısı
"""

import sys
import os
import json
import sqlite3
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent  # /Users/acar/Desktop/tkgm
DATA_DIR = BASE_DIR / "data" / "istatistik"
DATA_DIR.mkdir(parents=True, exist_ok=True)

NUTS3_PATH = PROJECT_DIR / "data" / "nuts3.json"
SQLITE_PATH = BASE_DIR / "data" / "cografya" / "turkiye_fiziksel_ve_hukuki_altyapi.sqlite"

INDICATORS = {
    "satis_toplam": {
        "kod": "INS-GK055-O006",
        "ad": "Toplam Konut Satış Sayısı",
        "kaynak": "ilGostergeleri",
        "period": "yillik",
        "kayitSayisi": 5
    },
    "satis_ilk_el": {
        "kod": "INS-GK056-O006",
        "ad": "İlk Satış (Sıfır) Konut Sayısı",
        "kaynak": "ilGostergeleri",
        "period": "yillik",
        "kayitSayisi": 5
    },
    "yapi_ruhsat_daire": {
        "kod": "INS-GK058-O006",
        "ad": "Yapı Ruhsatı Daire Sayısı",
        "kaynak": "ilGostergeleri",
        "period": "yillik",
        "kayitSayisi": 5
    },
    "yapi_iskan_daire": {
        "kod": "INS-GK061-O006",
        "ad": "İskan Alınan Daire Sayısı",
        "kaynak": "ilGostergeleri",
        "period": "yillik",
        "kayitSayisi": 5
    }
}


def load_nuts3():
    """81 ilin plaka/duzeyKodu ve ad eşleşmesini yükler."""
    if not NUTS3_PATH.exists():
        print(f"Uyarı: {NUTS3_PATH} bulunamadı!")
        return {}
    with open(NUTS3_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    mapping = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        kod = str(props.get("duzeyKodu", ""))
        ad = props.get("ad") or props.get("name")
        bolge = props.get("bolgeKodu")
        mapping[kod] = {"il_adi": ad, "bolge_kodu": bolge, "plaka": int(kod) if kod.isdigit() else None}
    return mapping


def fetch_cip_data(gosterge_cfg):
    """TÜİK CIP API'sinden bir göstergenin 81 il verisini çeker."""
    params = urllib.parse.urlencode({
        "kaynak": gosterge_cfg["kaynak"],
        "duzey": "3",
        "gostergeNo": gosterge_cfg["kod"],
        "kayitSayisi": str(gosterge_cfg["kayitSayisi"]),
        "period": gosterge_cfg["period"]
    })
    url = f"https://cip.tuik.gov.tr/Home/GetMapData?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    
    tarihler = res.get("tarihler", [])
    birimler = {}
    for item in res.get("veriler", []):
        kod = str(item.get("duzeyKodu"))
        veri = item.get("veri", [])
        # tarihler listesiyle eşle
        birimler[kod] = {tarihler[i]: int(veri[i]) if i < len(veri) and veri[i].isdigit() else (float(veri[i]) if i < len(veri) and veri[i] else 0) for i in range(len(tarihler))}
    return tarihler, birimler


def collect_all_provinces():
    print("=" * 70)
    print("TÜİK KAMUSAL AÇIK VERİLERİ - 81 İL RESMİ SATIŞ VE YAPI İSTATİSTİKLERİ")
    print("=" * 70)

    nuts_map = load_nuts3()
    tum_veriler = {}

    for key, cfg in INDICATORS.items():
        print(f"-> Çekiliyor: {cfg['ad']} ({cfg['kod']})...")
        try:
            tarihler, birimler = fetch_cip_data(cfg)
            tum_veriler[key] = {"tarihler": tarihler, "birimler": birimler}
            print(f"   Dönemler: {tarihler} | Başarılı ({len(birimler)} il)")
        except Exception as e:
            print(f"   Hata: {e}")
            tum_veriler[key] = {"tarihler": [], "birimler": {}}

    # 81 İl Tablosunu Oluştur
    # En güncel yıl (tarihler[0]) ve önceki yıllar
    ana_tarihler = tum_veriler["satis_toplam"]["tarihler"]
    if not ana_tarihler:
        print("Veri çekilemedi.")
        return

    son_yil = ana_tarihler[0]
    onceki_yil = ana_tarihler[1] if len(ana_tarihler) > 1 else son_yil

    rapor_satirlar = []

    for kod, il_info in sorted(nuts_map.items(), key=lambda x: x[1].get("plaka") or 999):
        il_adi = il_info["il_adi"]
        plaka = il_info["plaka"]

        satis_toplam_son = tum_veriler["satis_toplam"]["birimler"].get(kod, {}).get(son_yil, 0)
        satis_toplam_onceki = tum_veriler["satis_toplam"]["birimler"].get(kod, {}).get(onceki_yil, 0)
        
        satis_ilk_el_son = tum_veriler["satis_ilk_el"]["birimler"].get(kod, {}).get(son_yil, 0)
        satis_ikinci_el = max(0, satis_toplam_son - satis_ilk_el_son)

        ruhsat_son = tum_veriler["yapi_ruhsat_daire"]["birimler"].get(kod, {}).get(son_yil, 0)
        iskan_son = tum_veriler["yapi_iskan_daire"]["birimler"].get(kod, {}).get(son_yil, 0)

        # Yıllık Değişim Oranı
        if satis_toplam_onceki > 0:
            yillik_degisim_pct = round(((satis_toplam_son - satis_toplam_onceki) / satis_toplam_onceki) * 100, 2)
        else:
            yillik_degisim_pct = 0.0

        # Sıfır Konut Payı
        ilk_el_payi = round((satis_ilk_el_son / satis_toplam_son * 100), 1) if satis_toplam_son > 0 else 0.0

        satir = {
            "plaka": plaka,
            "il_kodu": kod,
            "il_adi": il_adi,
            "bolge_kodu": il_info.get("bolge_kodu", ""),
            "son_yil": son_yil,
            "onceki_yil": onceki_yil,
            "toplam_satis_son_yil": satis_toplam_son,
            "toplam_satis_onceki_yil": satis_toplam_onceki,
            "yillik_satis_degisimi_yuzde": yillik_degisim_pct,
            "ilk_el_satis_adedi": satis_ilk_el_son,
            "ikinci_el_satis_adedi": satis_ikinci_el,
            "ilk_el_satis_orani_yuzde": ilk_el_payi,
            "yapi_ruhsati_daire_adedi": ruhsat_son,
            "iskan_daire_adedi": iskan_son,
            "tum_yillar_satis_serisi": tum_veriler["satis_toplam"]["birimler"].get(kod, {})
        }
        rapor_satirlar.append(satir)

    # 1. JSON Export
    json_path = DATA_DIR / "turkiye_81_il_resmi_satis_istatistikleri.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rapor_satirlar, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] JSON Kaydedildi: {json_path}")

    # 2. CSV Export
    csv_path = DATA_DIR / "turkiye_81_il_resmi_satis_istatistikleri.csv"
    import csv
    fieldnames = [
        "plaka", "il_adi", "bolge_kodu", "son_yil", "toplam_satis_son_yil", 
        "toplam_satis_onceki_yil", "yillik_satis_degisimi_yuzde", 
        "ilk_el_satis_adedi", "ikinci_el_satis_adedi", "ilk_el_satis_orani_yuzde",
        "yapi_ruhsati_daire_adedi", "iskan_daire_adedi"
    ]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rapor_satirlar:
            writer.writerow(r)
    print(f"[OK] CSV Kaydedildi: {csv_path}")

    # 3. SQLite Tablosu
    if SQLITE_PATH.exists():
        conn = sqlite3.connect(SQLITE_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tuik_resmi_konut_satis_istatistikleri (
                plaka INTEGER PRIMARY KEY,
                il_kodu TEXT,
                il_adi TEXT,
                bolge_kodu TEXT,
                son_yil TEXT,
                toplam_satis_son_yil INTEGER,
                toplam_satis_onceki_yil INTEGER,
                yillik_satis_degisimi_yuzde REAL,
                ilk_el_satis_adedi INTEGER,
                ikinci_el_satis_adedi INTEGER,
                ilk_el_satis_orani_yuzde REAL,
                yapi_ruhsati_daire_adedi INTEGER,
                iskan_daire_adedi INTEGER,
                satis_gecmisi_json TEXT,
                guncellenme_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for r in rapor_satirlar:
            cur.execute("""
                INSERT OR REPLACE INTO tuik_resmi_konut_satis_istatistikleri 
                (plaka, il_kodu, il_adi, bolge_kodu, son_yil, toplam_satis_son_yil, 
                 toplam_satis_onceki_yil, yillik_satis_degisimi_yuzde, ilk_el_satis_adedi, 
                 ikinci_el_satis_adedi, ilk_el_satis_orani_yuzde, yapi_ruhsati_daire_adedi, 
                 iskan_daire_adedi, satis_gecmisi_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["plaka"], r["il_kodu"], r["il_adi"], r["bolge_kodu"], r["son_yil"],
                r["toplam_satis_son_yil"], r["toplam_satis_onceki_yil"], r["yillik_satis_degisimi_yuzde"],
                r["ilk_el_satis_adedi"], r["ikinci_el_satis_adedi"], r["ilk_el_satis_orani_yuzde"],
                r["yapi_ruhsati_daire_adedi"], r["iskan_daire_adedi"],
                json.dumps(r["tum_yillar_satis_serisi"])
            ))
        conn.commit()
        conn.close()
        print(f"[OK] SQLite Veritabanı Güncellendi: {SQLITE_PATH} (tablo: tuik_resmi_konut_satis_istatistikleri)")

    # Ekrana Örnek İlk 10 İl Özeti
    print("\n" + "=" * 85)
    print(f"TÜRKİYE {son_yil} YILI RESMİ KONUT SATIŞLARI - EN ÇOK SATIŞ YAPILAN İLLER (ÖZET)")
    print("=" * 85)
    print(f"{'Plaka':<6} {'İl Adı':<16} {'Toplam Satış':<14} {'İlk El (Sıfır)':<16} {'İkinci El':<14} {'Değişim %':<12}")
    print("-" * 85)
    sirali = sorted(rapor_satirlar, key=lambda x: x["toplam_satis_son_yil"], reverse=True)
    for r in sirali[:10]:
        print(f"{str(r['plaka']):<6} {r['il_adi']:<16} {r['toplam_satis_son_yil']:<14,d} {r['ilk_el_satis_adedi']:<16,d} {r['ikinci_el_satis_adedi']:<14,d} %{r['yillik_satis_degisimi_yuzde']:<10.1f}")
    
    # Kullanıcının sorduğu koordinat (Antalya Muratpaşa)
    antalya = next((x for x in rapor_satirlar if x["plaka"] == 7), None)
    if antalya:
        print("-" * 85)
        print(f"SORGULADIĞINIZ KOORDİNATTAKİ İL (07 ANTALYA):")
        print(f" -> Toplam Konut Satışı: {antalya['toplam_satis_son_yil']:,d} adet")
        print(f" -> İlk El Satış: {antalya['ilk_el_satis_adedi']:,d} adet (%{antalya['ilk_el_satis_orani_yuzde']})")
        print(f" -> Yeni Yapı Ruhsatı Daire: {antalya['yapi_ruhsati_daire_adedi']:,d} daire")
        print(f" -> İskan İzni Daire: {antalya['iskan_daire_adedi']:,d} daire")
        print(f" -> Yıllık Değişim: %{antalya['yillik_satis_degisimi_yuzde']}")
    print("=" * 85)


if __name__ == "__main__":
    collect_all_provinces()
