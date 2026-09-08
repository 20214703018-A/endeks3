#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Farklı sanal makinelerden gelen CSV çıktılarını tek bir ana veri setinde birleştirir.
"""

import os
import sys
import glob
import csv
from pathlib import Path

def merge_csv_directories(input_dirs, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Birleştirilecek CSV dosya adları
    target_files = [
        "01_demografi_ve_nufus.csv",
        "02_yillik_satislar_2010_2024.csv",
        "03_fiyat_ozet_konut_arsa.csv",
        "04_aylik_fiyat_trendi_2021_2026.csv",
        "05_oda_yas_kat_isitma_kirilimlari.csv",
        "06_hemsehri_kutuk_dagilimi.csv",
        "07_secim_sonuclari_ve_oylar.csv",
        "08_ilce_onemli_noktalar_poi.csv",
        "09_emlak_ofisleri_rehberi.csv",
        "10_gayrimenkul_danismanlari.csv",
        "11_insaat_ve_proje_sirketleri.csv",
        "12_iller_listesi.csv",
        "13_ilceler_listesi.csv",
        "14_mahalleler_listesi.csv",
        "15_e_ticaret_ve_harcama_kalemleri.csv",
        "16_medeni_durum_ve_gayrimenkul_stoku.csv",
        "17_detayli_yas_piramidi_47_grup.csv"
    ]

    for fname in target_files:
        merged_rows = []
        header = None
        seen_signatures = set()

        for dir_path in input_dirs:
            p = Path(dir_path)
            # Hem doğrudan dizin altında hem de alt klasörlerde ara
            matches = list(p.glob(f"**/{fname}"))
            for csv_path in matches:
                try:
                    with open(csv_path, "r", encoding="utf-8-sig") as f:
                        reader = csv.reader(f, delimiter=";")
                        try:
                            file_header = next(reader)
                        except StopIteration:
                            continue

                        if header is None:
                            header = file_header

                        for row in reader:
                            if not row: continue
                            # İkinci kolondan sonrasını imza olarak al (id kolonundaki çakışmaları engellemek için)
                            sig = ";".join(row[1:]) if len(row) > 1 else ";".join(row)
                            if sig not in seen_signatures:
                                seen_signatures.add(sig)
                                merged_rows.append(row)
                except Exception as e:
                    print(f"Hata ({csv_path}): {e}")

        if header and merged_rows:
            out_file = output_dir / fname
            with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(header)
                for new_id, row in enumerate(merged_rows, 1):
                    # Eğer ilk kolon 'id' ise yeniden sırala
                    if header[0].lower() == "id":
                        row[0] = str(new_id)
                    writer.writerow(row)
            print(f"✓ Birleştirildi: {fname:<40} -> {len(merged_rows):>7} satır")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Kullanım: python3 merge_csv.py <cikti_klasoru> <girdi_klasoru_1> [girdi_klasoru_2 ...]")
        sys.exit(1)

    out_dir = sys.argv[1]
    in_dirs = sys.argv[2:]
    merge_csv_directories(in_dirs, out_dir)
