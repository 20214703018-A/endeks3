#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite Verilerini CSV Formatına Dışa Aktarma Aracı
--------------------------------------------------
İl, İlçe ve Mahalle düzeyindeki tüm tabloları ve ham_json içindeki gizli
harcama, medeni durum ve 47 yaş grubu piramidini Excel uyumlu (UTF-8 with BOM)
CSV formatında dışa aktarır.
"""

import csv
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "piyasa_verileri.db"
CSV_OUT_DIR = DATA_DIR / "csv_ciktilari"

TABLOLAR = {
    "demografi": "01_demografi_ve_nufus",
    "yillik_satislar": "02_yillik_satislar_2010_2024",
    "fiyat_ozet": "03_fiyat_ozet_konut_arsa",
    "fiyat_trend": "04_aylik_fiyat_trendi_2021_2026",
    "fiyat_dagilim": "05_oda_yas_kat_isitma_kirilimlari",
    "hemsehri": "06_hemsehri_kutuk_dagilimi",
    "secim_sonuclari": "07_secim_sonuclari_ve_oylar",
    "poi_noktalari": "08_ilce_onemli_noktalar_poi",
    "emlak_ofisleri": "09_emlak_ofisleri_rehberi",
    "danismanlar": "10_gayrimenkul_danismanlari",
    "sirketler": "11_insaat_ve_proje_sirketleri",
    "iller": "12_iller_listesi",
    "ilceler": "13_ilceler_listesi",
    "mahalleler": "14_mahalleler_listesi",
}

def export_hidden_json_tables(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT seviye, city_id, county_id, district_id, bolge_adi, ham_json FROM demografi WHERE ham_json IS NOT NULL")
        rows = cur.fetchall()
    except Exception:
        return

    if not rows:
        return

    print("Ham JSON içindeki e-ticaret, medeni durum ve yaş piramidi aktarılıyor...")
    ecom_rows = []
    marital_rows = []
    age_rows = []

    for sev, cid, coid, did, bolge, ham in rows:
        try:
            d = json.loads(ham)
        except Exception:
            continue

        ecom_rows.append({
            "seviye": sev, "city_id": cid, "county_id": coid, "district_id": did, "bolge_adi": bolge,
            "online_pazaryeri_tl": d.get("OnlineRetailOnlyMarketplace"),
            "online_tatil_seyahat_tl": d.get("OnlineVacationTravel"),
            "online_yasal_bahis_tl": d.get("OnlineLegalBetting"),
            "online_elektronik_tl": d.get("MultichannelRetailElectronics"),
            "online_giyim_ayakkabi_tl": d.get("MultichannelRetailClothingShoes"),
            "ev_dekorasyon_tl": d.get("MultichannelRetailHomeDecoration"),
            "e_ticaret_kullanici_sayisi": d.get("ECommerceCount"),
            "aylik_gida_harcamasi": d.get("ExpenseFood"),
            "aylik_barinma_kira_harcamasi": d.get("ExpenseShelter"),
            "aylik_ulasim_harcamasi": d.get("ExpenseTransportation"),
            "aylik_restoran_yeme_icme": d.get("ExpenseRestaurant"),
            "aylik_giyim_harcamasi": d.get("ExpenseClothing"),
            "aylik_saglik_harcamasi": d.get("ExpenseHealth"),
            "aylik_egitim_harcamasi": d.get("ExpenseEducation"),
            "aylik_eglence_kultur": d.get("ExpenseEntertainment"),
            "aylik_alkol_tutun": d.get("ExpenseAlcoholAndSmoking"),
            "aylik_toplam_harcama": d.get("ExpenseTotal"),
            "toplam_tasarruf": d.get("SavingTotal")
        })

        marital_rows.append({
            "seviye": sev, "city_id": cid, "county_id": coid, "district_id": did, "bolge_adi": bolge,
            "evli_sayisi": d.get("Married"),
            "bekar_hic_evlenmemis": d.get("MarriedNever"),
            "bosanmis_sayisi": d.get("Divorced"),
            "dul_sayisi": d.get("Widow"),
            "toplam_konut_sayisi": d.get("HousingCount"),
            "toplam_ticari_mulk": d.get("CommercialCount"),
            "yazlik_konut_sayisi": d.get("SummerResortCount"),
            "sahibinden_ilan_sayisi": d.get("TotalOwnerListingCount"),
            "emlakci_ilan_sayisi": d.get("TotalAgentListingCount")
        })

        age_dict = {"seviye": sev, "city_id": cid, "county_id": coid, "district_id": did, "bolge_adi": bolge}
        for k in sorted(d.keys()):
            if k.startswith("Age_"):
                age_dict[k] = d[k]
        age_rows.append(age_dict)

    def write_dict_csv(data_list, filename):
        if not data_list: return
        file_path = CSV_OUT_DIR / filename
        fields = list(data_list[0].keys())
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(data_list)
        print(f"  ✓ {filename} ({len(data_list)} kayıt)")

    write_dict_csv(ecom_rows, "15_e_ticaret_ve_harcama_kalemleri.csv")
    write_dict_csv(marital_rows, "16_medeni_durum_ve_gayrimenkul_stoku.csv")
    write_dict_csv(age_rows, "17_detayli_yas_piramidi_47_grup.csv")

def export():
    if not DB_PATH.exists():
        print(f"HATA: Veritabanı bulunamadı: {DB_PATH}")
        return

    CSV_OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("Veritabanı tabloları CSV formatına aktarılıyor...")

    for tablo, dosya_adi in TABLOLAR.items():
        try:
            cur.execute(f"PRAGMA table_info({tablo})")
            kolonlar = [row[1] for row in cur.fetchall()]
            if not kolonlar: continue

            secili = [k for k in kolonlar if k not in ("ham_json", "parti_sonuclari_json")]
            cur.execute(f"SELECT {', '.join(secili)} FROM {tablo}")
            satirlar = cur.fetchall()

            hedef_csv = CSV_OUT_DIR / f"{dosya_adi}.csv"
            with open(hedef_csv, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(secili)
                writer.writerows(satirlar)

            print(f"  ✓ {dosya_adi}.csv ({len(satirlar)} kayıt)")
        except Exception as e:
            print(f"  ✗ {tablo} aktarılırken hata: {e}")

    export_hidden_json_tables(conn)
    conn.close()
    print(f"\nTüm CSV dosyaları hazırlandı: {CSV_OUT_DIR}")

if __name__ == "__main__":
    export()
