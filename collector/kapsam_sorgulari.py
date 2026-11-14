#!/usr/bin/env python3
"""
KAPSAM SORGULARI (14 Kas 2026) — Google Haritalar kapsamını sektör ve bölge olarak genişletmek için ilçe düzeyi metin sorguları.

Neden: kanonikte 2,56 M işletme var; ölçümde (a) temel/ek karo setlerinde hiç aranmayan sektörler (fitness, spa, mimar, yazılım,
kombi servisi, fabrika, müteahhit, tarım…) ve (b) büyükşehir dışında karo taraması olmaması (metin sorgusu ~160 sonuçta tıkanır)
nedeniyle eksiklik görüldü. Camiler toplayıcının ticari-olmayan süzgecine takıldığı için 2 bin (gerçek ~90 bin).
Çıktılar (satır başına bir sorgu; toplayıcı --sorgu-dosyasi ile okur, shard = satır sırası mod toplam):
  kapsam_sorgulari.txt : KAPSAM seti × 973 ilçe  +  temel+ek set × büyükşehir-kentsel olmayan ilçeler
  dini_sorgulari.txt   : cami / mescit / cemevi / kilise / sinagog × 973 ilçe (KAMU_MOD=1 ile taranır: ticari süzgeç kapalı)
Kullanım: python collector/kapsam_sorgulari.py --cikti-dizini .
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import google_places_ve_yogunluk_toplayici as T  # noqa: E402

DINI = ["cami", "mescit", "cemevi", "kilise", "sinagog"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--cikti-dizini", default="."); a = ap.parse_args()
    ilceler = {}   # (il, ilçe) -> büyükşehir kentsel mahallesi var mı
    for _k, il, ilce, _m, tier in T.load_mahalle_tiers():
        ilceler[(il, ilce)] = ilceler.get((il, ilce), False) or tier == 1
    if not ilceler: sys.exit("mahalle listesi boş")
    kapsam, dini = [], []
    for (il, ilce), bsehir in sorted(ilceler.items()):
        yer = f"{ilce} {il}" if ilce and ilce.lower() != "merkez" else f"{il} merkez"
        kapsam += [f"{k} {yer}" for k in T.KARO_KATEGORILER_KAPSAM]
        if not bsehir:   # karo taraması olmayan ilçelerde temel ve ek türler de ilçe düzeyinde aranır
            kapsam += [f"{k} {yer}" for k in dict.fromkeys(T.KARO_KATEGORILER + T.KARO_KATEGORILER_EK)]
        dini += [f"{k} {yer}" for k in DINI]
    for ad, lst in (("kapsam_sorgulari.txt", kapsam), ("dini_sorgulari.txt", dini)):
        with open(os.path.join(a.cikti_dizini, ad), "w", encoding="utf-8") as f: f.write("\n".join(lst) + "\n")
        print(f"{ad}: {len(lst):,} sorgu", flush=True)
    print(f"ilçe {len(ilceler):,} (büyükşehir kentsel {sum(ilceler.values()):,})")


if __name__ == "__main__":
    main()
