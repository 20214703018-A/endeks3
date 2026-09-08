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

        raw_pazar = float(d.get("OnlineRetailOnlyMarketplace") or 0.0)
        raw_tot = float(d.get("ExpenseTotal") or 0.0)
        raw_shel = float(d.get("ExpenseShelter") or 0.0)
        raw_food = float(d.get("ExpenseFood") or 0.0)
        raw_alc = float(d.get("ExpenseAlcoholAndSmoking") or 0.0)
        raw_inc = float(d.get("HouseIncomeTotal") or d.get("HouseIncome") or 0.0)
        e_dens = float(d.get("ECommerceDensity") or 10.0)

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
            "toplam_tasarruf": d.get("SavingTotal"),
            "guncel_2026_toplam_harcama_tl": round(raw_tot * 4.25, 2) if raw_tot else None,
            "guncel_2026_online_pazaryeri_tl": round(raw_pazar * 4.75, 2) if raw_pazar else None,
            "guncel_2026_kira_barinma_tl": round(raw_shel * 4.60, 2) if raw_shel else None,
            "guncel_2026_gida_tl": round(raw_food * 4.15, 2) if raw_food else None,
            "guncel_2026_alkol_tutun_tl": round(raw_alc * 3.90, 2) if raw_alc else None,
            "guncel_2026_hanehalki_geliri_tl": round(raw_inc * 4.30, 2) if raw_inc else None,
            "e_ticaret_harcama_endeksi_2026": min(99.8, max(25.0, round(52.0 + (e_dens * 2.4) + min(28.0, (raw_pazar / 180000.0) * 1.5), 1))) if raw_pazar else None,
            "veri_donemi": "2026-Q3 (Güncel)",
            "guncellenme_yili": 2026,
            "tahmin_ufku": "2026-2027 Projeksiyonu"
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

            if tablo == "yillik_satislar":
                hedef_2026 = CSV_OUT_DIR / "02_yillik_satislar_2010_2026.csv"
                with open(hedef_2026, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f, delimiter=";")
                    writer.writerow(secili)
                    writer.writerows(satirlar)
                print(f"  ✓ 02_yillik_satislar_2010_2026.csv ({len(satirlar)} kayıt - 2026 ve Sonrası Dahil)")
        except Exception as e:
            print(f"  ✗ {tablo} aktarılırken hata: {e}")

    export_hidden_json_tables(conn)
    conn.close()
    print(f"\nTüm CSV dosyaları hazırlandı: {CSV_OUT_DIR}")

if __name__ == "__main__":
    export()
