#!/usr/bin/env python3
"""
ANLIK YOĞUNLUK HEDEF LİSTESİ — ana ambardan, canlı yoğunluğu ölçülecek işletmeleri seçer.

Seçim: öncelikli iller (proje kuralı 10: Batı büyükşehirleri + Ankara) × insan trafiği olan tür grupları; her grupta
yorum sayısına göre (ziyaret yoğunluğunun gerçek göstergesi) en çok ziyaret edilenler. Grup kotaları, yoğun
restoranların bar/gece kulübü gibi küçük grupları listeden itmesini önler. Tür: google_kategori_tr (Google'ın
asıl türü) varsa o, yoksa ana_kategori.
Çıktı: CSV (feature_id, google_place_id, isim, il, ilce, kategori, grup, lat, lon, yorum_sayisi)
Kullanım: google_anlik_hedef.py --ambar ana.sqlite --out anlik_yogunluk_hedef.csv [--olcek 1.0]
"""
import argparse, csv, re, sqlite3

ILLER_TR = ["İstanbul", "İzmir", "Bursa", "Antalya", "Kocaeli", "Muğla", "Tekirdağ", "Balıkesir", "Aydın", "Ankara"]
_ASCII = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")
ILLER = sorted(set(ILLER_TR) | {x.translate(_ASCII) for x in ILLER_TR})   # ambarda iki yazım da görülebiliyor
GRUPLAR = [  # (grup, tür deseni, kota)
    ("avm",          r"alışveriş merkezi|avm",                                         400),
    ("gece_hayati",  r"gece kulübü|bar\b|pub|meyhane|birahane|lounge|disko|dans kulübü|canlı müzik|karaoke|nargile", 1500),
    ("restoran",     r"restoran|lokanta|kebap|döner|pide|pizza|burger|balık|ocakbaşı|köfte",               2200),
    ("kafe",         r"kafe|kahve|pastane|fırın|tatlı|çay bahçesi",                                       1300),
    ("market",       r"süpermarket|market|bakkal|manav|şarküteri|kasap",                                  1200),
    ("konaklama",    r"otel|pansiyon|apart|hostel|tatil köyü",                                             700),
    ("spor_eglence", r"spor salonu|fitness|yüzme|sinema|oyun salonu|bowling|halı saha",                    500),
    ("hizmet",       r"eczane|banka|kuaför|berber|güzellik|hastane|poliklinik|noter|ptt",                  700),
    ("ulasim",       r"akaryakıt|benzin|otopark|otogar|terminal|istasyon|iskele",                          500),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ambar", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--olcek", type=float, default=1.0, help="kotaları çarp (makine/süre değişirse)")
    a = ap.parse_args()
    db = sqlite3.connect(f"file:{a.ambar}?mode=ro", uri=True)
    cols = {r[1] for r in db.execute("PRAGMA table_info(google_places_ticari_yogunluk)")}
    tur = "COALESCE(google_kategori_tr, ana_kategori)" if "google_kategori_tr" in cols else "ana_kategori"
    q = (f"SELECT feature_id, google_place_id, isim, il, ilce, {tur}, lat, lon, COALESCE(yorum_sayisi,0) "
         f"FROM google_places_ticari_yogunluk WHERE feature_id IS NOT NULL AND lat IS NOT NULL "
         f"AND il IN ({','.join('?' * len(ILLER))})")
    aday = {g: [] for g, _, _ in GRUPLAR}
    desen = [(g, re.compile(rx, re.I), k) for g, rx, k in GRUPLAR]
    for row in db.execute(q, ILLER):
        k = (row[5] or "").lower()
        for g, rx, _ in desen:
            if rx.search(k):
                aday[g].append(row); break
    secilen, gorulen = [], set()
    for g, _, kota in desen:
        n = int(kota * a.olcek)
        for row in sorted(aday[g], key=lambda r: -r[8]):
            if row[0] in gorulen: continue
            gorulen.add(row[0]); secilen.append((g,) + row)
            n -= 1
            if n <= 0: break
        print(f"  {g:13s} aday {len(aday[g]):>7,} · seçilen {sum(1 for s in secilen if s[0]==g):>5,}")
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["feature_id", "google_place_id", "isim", "il", "ilce", "kategori", "grup", "lat", "lon", "yorum_sayisi"])
        for g, fid, pid, isim, il, ilce, kat, lat, lon, yorum in secilen:
            w.writerow([fid, pid, isim, il, ilce, kat, g, lat, lon, yorum])
    print(f"hedef: {len(secilen):,} işletme → {a.out}")


if __name__ == "__main__":
    main()
