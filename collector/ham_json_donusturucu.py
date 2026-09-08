#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daha önce çekilmiş olan piyasa_verileri.db veya zip dosyalarının içindeki
'ham_json' sütununu okuyarak tek bir yeni API isteği atmadan:
1. E-Ticaret ve Harcama Kalemleri
2. 47 Gruplu Detaylı Yaş Piramidi
3. Medeni Durum Dağılımı
4. Gayrimenkul & Yazlık Stok Analizi
tablolarını saniyeler içinde CSV olarak üretir.
"""

import os
import sys
import glob
import json
import csv
import sqlite3
import zipfile
from pathlib import Path

def extract_from_db(db_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT seviye, city_id, county_id, district_id, bolge_adi, ham_json FROM demografi WHERE ham_json IS NOT NULL")
        rows = cur.fetchall()
    except Exception as e:
        print(f"Hata ({db_path}): {e}")
        return

    if not rows:
        return

    ecom_rows = []
    age_rows = []
    marital_rows = []

    for sev, cid, coid, did, bolge, ham in rows:
        try:
            d = json.loads(ham)
        except Exception:
            continue

        # 1. E-Ticaret ve Harcama Kalemleri
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

        # 2. Medeni Durum
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

        # 3. 47 Gruplu Yaş Piramidi
        age_dict = {"seviye": sev, "city_id": cid, "county_id": coid, "district_id": did, "bolge_adi": bolge}
        for k in sorted(d.keys()):
            if k.startswith("Age_"):
                age_dict[k] = d[k]
        age_rows.append(age_dict)

    # CSV'lere Yaz
    def write_csv(data_list, filename):
        if not data_list: return
        file_path = output_dir / filename
        fields = list(data_list[0].keys())
        is_new = not file_path.exists()
        with open(file_path, "a" if not is_new else "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
            if is_new:
                writer.writeheader()
            for r in data_list:
                writer.writerow(r)

    write_csv(ecom_rows, "07_e_ticaret_ve_harcama_kalemleri.csv")
    write_csv(marital_rows, "08_medeni_durum_ve_gayrimenkul_stoku.csv")
    write_csv(age_rows, "09_detayli_yas_piramidi_47_grup.csv")
    print(f"  ✓ {db_path.name}: {len(rows)} satır başarıyla yeni tablolara dönüştürüldü.")

def main():
    downloads = Path.home() / "Downloads"
    output_dir = Path.home() / "Desktop" / "GECMIS_VERIDEN_URETILEN_CSVLER"

    print("İndirilenler klasöründeki geçmiş veri paketleri aranıyor...")
    
    # 1. Doğrudan .db dosyaları
    dbs = list(downloads.glob("**/*.db")) + list(Path("collector/data").glob("*.db"))
    
    # 2. Eğer bolge_*.zip varsa içindeki db'leri geçici klasöre çıkar
    zip_files = list(downloads.glob("*bolge_*.zip")) + list(downloads.glob("*paket_*.zip"))
    temp_dir = Path("/tmp/extracted_dbs")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    for z in zip_files:
        try:
            with zipfile.ZipFile(z, 'r') as zf:
                for member in zf.namelist():
                    if member.endswith("piyasa_verileri.db"):
                        extracted = zf.extract(member, temp_dir)
                        dbs.append(Path(extracted))
        except Exception as e:
            print(f"Zip okuma hatası ({z.name}): {e}")

    # Tekilleştir
    unique_dbs = list(set(dbs))
    print(f"Toplam {len(unique_dbs)} adet veritabanı bulundu.")

    for db in unique_dbs:
        extract_from_db(db, output_dir)

    print(f"\n✓ TAMAMLANDI! Tek bir saniye beklemeden ve yeniden çalıştırmadan:")
    print(f"  Klasör konumu: {output_dir}")

if __name__ == "__main__":
    main()
