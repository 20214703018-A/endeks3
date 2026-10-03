#!/usr/bin/env python3
"""
GCID SÖZLÜĞÜ (başlangıç turu) — ana ambarda geçen her Google kategori kimliğinin (gcid) Türkçe adını Google'dan öğrenir.

Neden: liste ayrıştırıcısındaki indeks hatası yüzünden (3 Ekim 2026'da düzeltildi) ~930 bin kayıtta Türkçe kategori adı
okunamadı ama gcid kimliği kaydedildi. Her kimlik için o kimliğe sahip bir örnek işletme, kendi konumunda adıyla aranır;
Google'ın döndürdüğü her sonuç (asıl tür adı, asıl tür kimliği) çifti verir — tek istekte çoğu zaman birden çok kimlik öğrenilir.
Sonuç: --out veritabanında gcid_kategori_cift tablosu (birleştirmede ana ambara geçer). Kimlik uydurulmaz/çevrilmez.

Kullanım: gcid_sozluk.py --gecmis-db ana.sqlite --out shard.sqlite [--shard 3 --num-shards 40] [--max-seconds 900]
"""
import argparse, hashlib, json, os, sqlite3, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import google_places_ve_yogunluk_toplayici as g

ap = argparse.ArgumentParser()
ap.add_argument("--gecmis-db", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--shard", type=int, default=1); ap.add_argument("--num-shards", type=int, default=1)
ap.add_argument("--max-seconds", type=int, default=900)
a = ap.parse_args()
g.init_db(a.out)
src = sqlite3.connect(f"file:{a.gecmis_db}?mode=ro", uri=True)
bilinen = set()
try:
    bilinen = {r[0] for r in src.execute("SELECT gcid FROM gcid_kategori_cift")}
except sqlite3.OperationalError:
    pass
# her asıl kimlik için en çok yorumlu örnek işletme
ornek = {}
for gc, ad, lat, lon, pid, yorum in src.execute(
        "SELECT json_extract(gcid_kategoriler,'$[0]'), isim, lat, lon, google_place_id, COALESCE(yorum_sayisi,0) "
        "FROM google_places_ticari_yogunluk WHERE gcid_kategoriler LIKE '[%' AND lat IS NOT NULL"):
    if not gc or gc in bilinen: continue
    if int(hashlib.sha256(gc.encode()).hexdigest()[:8], 16) % a.num_shards != a.shard - 1: continue
    if gc not in ornek or yorum > ornek[gc][4]: ornek[gc] = (ad, lat, lon, pid, yorum)
print(f"shard {a.shard}/{a.num_shards}: sözlükte {len(bilinen):,} kimlik var · bu shard'a düşen eksik kimlik {len(ornek):,}", flush=True)
out = sqlite3.connect(a.out); t0 = time.time(); ogrenilen = set(); istek = 0
for gc, (ad, lat, lon, pid, _) in sorted(ornek.items(), key=lambda x: -x[1][4]):
    if gc in ogrenilen: continue
    if time.time() - t0 > a.max_seconds: print("  süre doldu", flush=True); break
    try:
        sonuc = g.fetch_google_places(ad, 0, (lat, lon, 1200)); istek += 1
    except Exception as e:
        print(f"  [!] {gc}: {e}", flush=True); time.sleep(5); continue
    for v in sonuc:
        try:
            k = json.loads(v["tum_kategoriler"])[0] if v.get("tum_kategoriler") else None
            c = json.loads(v["gcid_kategoriler"])[0] if v.get("gcid_kategoriler") else None
        except Exception:
            continue
        if k and c:
            out.execute("INSERT OR REPLACE INTO gcid_kategori_cift (gcid, kategori_tr, ornek_place_id, son_gorulme) VALUES (?,?,?,?)",
                        (c, k, v.get("google_place_id"), time.strftime("%Y-%m-%dT%H:%M:%S")))
            ogrenilen.add(c)
    out.commit()
    if istek % 25 == 0:
        print(f"  {istek} istek · öğrenilen kimlik {len(ogrenilen):,} (hedef {len(ornek):,})", flush=True)
    time.sleep(random.uniform(0.4, 0.9))
hedefte = len(ogrenilen & set(ornek))
print(f"bitti: {istek} istek · öğrenilen kimlik {len(ogrenilen):,} · bu shard'ın hedefinden {hedefte:,}/{len(ornek):,}", flush=True)
