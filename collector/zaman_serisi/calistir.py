#!/usr/bin/env python3
"""Zaman serisi toplayıcılarını çalıştırır: calistir.py --kaynak tcmb_kur,biletix --dizin db/ [--tam]. Her kaynak bağımsızdır; biri düşerse diğerleri sürer."""
import argparse, json, os, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kaynaklar import KAYNAKLAR

ap = argparse.ArgumentParser(); ap.add_argument("--kaynak", required=True); ap.add_argument("--dizin", required=True); ap.add_argument("--tam", action="store_true")
a = ap.parse_args(); os.makedirs(a.dizin, exist_ok=True); ozet = {}; hata = 0
for k in [x.strip() for x in a.kaynak.split(",") if x.strip()]:
    t = time.time()
    try:
        n, istek, not_ = KAYNAKLAR[k](a.dizin, tam=a.tam); ozet[k] = {"satir": n, "istek": istek, "not": not_, "sn": round(time.time() - t)}
    except Exception as e:
        hata += 1; ozet[k] = {"hata": f"{type(e).__name__}: {str(e)[:200]}"}; traceback.print_exc()
    print(k, json.dumps(ozet[k], ensure_ascii=False), flush=True)
print(json.dumps(ozet, ensure_ascii=False))
sys.exit(1 if hata == len(ozet) else 0)
