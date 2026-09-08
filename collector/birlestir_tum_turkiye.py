#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
İndirilen tüm paketleri (İlk 14 bölge + 24 il + Yeni 40 makine çıktıları)
tek bir ana klasörde birleştirir ve tüm harita poligonlarını içine kopyalar.
"""

import sys
import shutil
from pathlib import Path
from merge_csv import merge_csv_directories

def main():
    base_dir = Path(__file__).resolve().parent
    downloads = Path.home() / "Downloads"

    # Otomatik tespit edilen klasörler
    candidate_dirs = list(downloads.glob("*TUM_TURKIYE*")) + \
                     list(downloads.glob("*EKSIK*")) + \
                     list(downloads.glob("*bolge_*")) + \
                     list(downloads.glob("*tum_turkiye*")) + \
                     list(downloads.glob("m0*")) + \
                     list(downloads.glob("m1*")) + \
                     list(downloads.glob("m2*")) + \
                     list(downloads.glob("m3*")) + \
                     list(downloads.glob("m4*"))

    print("Tespit edilen klasörler:")
    for d in candidate_dirs:
        print("  -", d.name)

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

    # Poligonlar klasörünü de master pakete kopyala
    local_poly = base_dir / "data" / "poligonlar"
    hedef_poly = hedef_klasor / "poligonlar"
    if local_poly.exists():
        hedef_poly.mkdir(parents=True, exist_ok=True)
        print("\nHarita ve Mahalle Sınır Poligonları kopyalanıyor...")
        for p in local_poly.glob("*.json"):
            shutil.copy(p, hedef_poly / p.name)
        poly_count = len(list(hedef_poly.glob('*.json')))
        print(f"  ✓ {poly_count} poligon dosyası (İl, İlçe ve Mahalle sınırları) pakete eklendi!")

    print(f"\n✓ TEBRİKLER! 81 İlin tüm mahalle, piyasa ve poligon verileri başarıyla birleştirildi:")
    print(f"  Konum: {hedef_klasor}")

if __name__ == "__main__":
    main()
