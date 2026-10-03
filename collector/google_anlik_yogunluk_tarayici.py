#!/usr/bin/env python3
"""
GOOGLE ANLIK YOĞUNLUK — TARAYICI YÖNTEMİ (Playwright, görünmez Chromium)

Neden: maps/preview/place isteği yoğunluk bölümünü ([84]) yalnız rastgele ~%0,6 yanıtta içeriyor (3 Ekim 2026 ölçümü:
1.578 ölçümün 8'i; IP engeli değil — başarılı yanıtlar sıranın rastgele yerlerinde). Google Haritalar sayfası ise
"Popüler saatler" grafiğini her uygun işletmede gösterir. Bu araç işletme sayfasını gerçek tarayıcıda açar ve grafikteki
erişilebilirlik etiketlerini (aria-label, ör. "Şu anda %69 yoğun, normalde %50") olduğu gibi okur.

Kurallar: etiket metinleri HAM saklanır (ham_etiketler); canlı yüzde yalnız "Şu anda/Şu an ... %N" kalıbı
gerçekten varsa çıkarılır, tahmin yok. Sayfa açılamazsa 'hata', grafik yoksa 'populer_yok'.
Çıktı tablosu google_anlik_yogunluk ile aynı (+ ham_etiketler, yontem sütunları).

Kullanım: google_anlik_yogunluk_tarayici.py --hedef hedef.csv --out shard.sqlite --shard 1 --num-shards 6
          [--max-seconds 2700] [--limit N] [--tur-dilimi 0 --tur-sayisi 3]
"""
import argparse, csv, gzip, hashlib, json, os, random, re, sqlite3, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from google_anlik_yogunluk import init_db as _init_db

TR = ZoneInfo("Europe/Istanbul")
CANLI_RX = re.compile(r"(?:Şu anda|Şu an|Currently)\D{0,40}?%\s?(\d{1,3})|(?:Şu anda|Şu an|Currently)\D{0,40}?(\d{1,3})\s?%", re.I)


def init_db(path):
    c = _init_db(path)
    for col in ("ham_etiketler TEXT", "yontem TEXT"):
        try: c.execute(f"ALTER TABLE google_anlik_yogunluk ADD COLUMN {col}")
        except sqlite3.OperationalError: pass
    c.commit()
    return c


def hedefler(path, shard, n, dilim, dilim_sayisi):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fid = r.get("feature_id")
            if not fid: continue
            h = int(hashlib.sha256(fid.encode()).hexdigest()[:8], 16)
            # makine payı + tur dilimi: liste turlar arasında dönüşümlü ölçülür (her tur 1/dilim_sayisi kadarı)
            if h % n == shard - 1 and (h // n) % dilim_sayisi == dilim:
                yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hedef", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=1); ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--max-seconds", type=int, default=2700); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tur-dilimi", type=int, default=-1, help="-1: saat bazlı otomatik (TR saati // 3 mod tur-sayisi)")
    ap.add_argument("--tur-sayisi", type=int, default=3)
    ap.add_argument("--kosu-id", default=os.environ.get("GITHUB_RUN_ID", "yerel"))
    a = ap.parse_args()
    dilim = a.tur_dilimi if a.tur_dilimi >= 0 else (datetime.now(TR).hour // 3) % a.tur_sayisi
    hedef = list(hedefler(a.hedef, a.shard, a.num_shards, dilim, a.tur_sayisi))
    random.shuffle(hedef)
    if a.limit: hedef = hedef[:a.limit]
    print(f"shard {a.shard}/{a.num_shards} · tur dilimi {dilim}/{a.tur_sayisi}: {len(hedef):,} işletme", flush=True)
    c = init_db(a.out)
    from playwright.sync_api import sync_playwright
    sayac = {}; t0 = time.time()
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = br.new_context(locale="tr-TR", timezone_id="Europe/Istanbul", viewport={"width": 1280, "height": 900},
                             user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
        page = ctx.new_page()
        page.route(re.compile(r".*\.(png|jpg|jpeg|webp|gif|woff2?)(\?.*)?$"), lambda r: r.abort())   # görseller gereksiz
        for i, r in enumerate(hedef, 1):
            if time.time() - t0 > a.max_seconds:
                print(f"  süre doldu ({i-1:,}/{len(hedef):,})", flush=True); break
            now = datetime.now(timezone.utc); tr = now.astimezone(TR)
            durum, yuzde, etiket, ham = "hata", None, None, None
            try:
                url = f"https://www.google.com/maps/place/data=!4m2!3m1!1s{r['feature_id']}?hl=tr&gl=tr"
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_selector('[aria-label*="yoğun" i], [aria-label*="busy" i]', timeout=8000)
                except Exception:
                    pass
                etiketler = page.eval_on_selector_all(
                    '[aria-label*="yoğun" i], [aria-label*="busy" i]', "els => els.map(e => e.getAttribute('aria-label'))")
                etiketler = [e for e in dict.fromkeys(etiketler) if e]
                if etiketler:
                    ham = json.dumps(etiketler, ensure_ascii=False)
                    canli = next((e for e in etiketler if CANLI_RX.search(e)), None)
                    if canli:
                        m = CANLI_RX.search(canli); yuzde = int(m.group(1) or m.group(2)); etiket = canli; durum = "canli"
                    else:
                        durum = "canli_yok"
                else:
                    durum = "populer_yok"
            except Exception as e:
                durum = "hata"; etiket = f"{type(e).__name__}: {str(e)[:120]}"
            sayac[durum] = sayac.get(durum, 0) + 1
            c.execute("""INSERT OR REPLACE INTO google_anlik_yogunluk
                (olcum_id, feature_id, google_place_id, isim, il, ilce, kategori, lat, lon, olcum_utc, olcum_tr, tr_gun, tr_saat,
                 durum, canli_saat, canli_yuzde, canli_etiket, kosu_id, ham_etiketler, yontem)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                f"{r['feature_id']}|{now.strftime('%Y-%m-%dT%H:%M')}", r["feature_id"], r.get("google_place_id"), r.get("isim"),
                r.get("il"), r.get("ilce"), r.get("kategori"), float(r["lat"]), float(r["lon"]),
                now.isoformat(timespec="seconds"), tr.isoformat(timespec="seconds"), tr.isoweekday(), tr.hour,
                durum, tr.hour if yuzde is not None else None, yuzde, etiket, a.kosu_id, ham, "tarayici"))
            if i % 25 == 0:
                c.commit(); print(f"  {i:,}/{len(hedef):,} · {sayac} · {(time.time()-t0)/i:.1f} sn/işletme", flush=True)
            if i <= 3 and ham:
                print(f"  örnek etiketler ({r.get('isim')}): {ham[:300]}", flush=True)
            time.sleep(random.uniform(0.3, 0.8))
        br.close()
    c.commit(); c.close()
    print(f"bitti: {sum(sayac.values()):,} ölçüm · {sayac}", flush=True)


if __name__ == "__main__":
    main()
