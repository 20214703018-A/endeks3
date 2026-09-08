#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
İndirilen tüm paketleri (İlk 14 bölge + Yeni çekilen 24 il)
tek bir ana klasörde birleştirip hazır eder.
"""

import sys
from pathlib import Path
from merge_csv import merge_csv_directories

def main():
    base_dir = Path(__file__).resolve().parent
    downloads = Path.home() / "Downloads"

    # Otomatik tespit edilen klasörler
    candidate_dirs = list(downloads.glob("*TUM_TURKIYE*")) + \
                     list(downloads.glob("*EKSIK*")) + \
                     list(downloads.glob("*bolge_*")) + \
                     list(downloads.glob("*tum_turkiye*"))

    print("Tespit edilen klasörler:")
    for d in candidate_dirs:
        print("  -", d)

    hedef_klasor = Path.home() / "Desktop" / "TUM_TURKIYE_81_IL_EKSIKSIZ_CSV"
    print(f"\nBirleştiriliyor -> {hedef_klasor}...")
    
    if len(sys.argv) > 1:
        in_dirs = [Path(x) for x in sys.argv[1:]]
    else:
        in_dirs = candidate_dirs

    if not in_dirs:
        print("Birleştirilecek girdi klasörü bulunamadı!")
        return

    merge_csv_directories(in_dirs, hedef_klasor)
    print(f"\n✓ TEBRİKLER! 81 İlin tüm mahalle verileri başarıyla birleştirildi:")
    print(f"  Konum: {hedef_klasor}")

if __name__ == "__main__":
    main()
