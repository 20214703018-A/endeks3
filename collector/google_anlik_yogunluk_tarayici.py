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
# Google etiketi (ölçüldü 3 Eki 2026): "Şu anki yoğunluk %83. Genellikle yoğunluk %81 oranında."
CANLI_RX = re.compile(r"(?:Şu anki|Şu anda|Şu an|Currently)\D{0,40}?%\s?(\d{1,3})|(?:Şu anki|Şu anda|Şu an|Currently)\D{0,40}?(\d{1,3})\s?%", re.I)
GENEL_RX = re.compile(r"(?:Genellikle|Normalde|usually)\D{0,40}?%\s?(\d{1,3})", re.I)
RET_RX = re.compile(r"Tümünü reddet|Reject all|Hepsini reddet", re.I)


def init_db(path):
    c = _init_db(path)
    for col in ("ham_etiketler TEXT", "yontem TEXT", "genelde_yuzde INTEGER", "tani TEXT"):
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
    ap.add_argument("--tur-sayisi", type=int, default=5, help="~9 sn/işletme: 6 makine × 45 dk ≈ 1.800 işletme/tur → 9.000 / 5")
    ap.add_argument("--kosu-id", default=os.environ.get("GITHUB_RUN_ID", "yerel"))
    a = ap.parse_args()
    # 3 saatlik dilim sayacı: art arda turlar listenin farklı parçasını ölçer, her parça sırayla farklı saatlere düşer
    dilim = a.tur_dilimi if a.tur_dilimi >= 0 else int(time.time() // (3 * 3600)) % a.tur_sayisi
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
            durum, yuzde, etiket, ham, genel, tani = "hata", None, None, None, None, None
            try:
                url = f"https://www.google.com/maps/place/data=!4m2!3m1!1s{r['feature_id']}?hl=tr&gl=tr"
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # çerez onay ekranı (bazı makinelerde): zorunlu olmayan çerezleri REDDET, sonra sayfaya dön
                if "consent." in page.url or page.locator("button", has_text=RET_RX).count():
                    try:
                        page.locator("button", has_text=RET_RX).first.click(timeout=5000)
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                        sayac["onay_reddedildi"] = sayac.get("onay_reddedildi", 0) + 1
                        if "/maps/place" not in page.url:
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                try:
                    page.wait_for_selector('[aria-label*="yoğun" i], [aria-label*="busy" i]', timeout=8000)
                except Exception:
                    pass
                SEC = '[aria-label*="yoğun" i], [aria-label*="busy" i]'
                oku = lambda: [e for e in dict.fromkeys(page.eval_on_selector_all(
                    SEC, "els => els.map(e => e.getAttribute('aria-label'))")) if e]
                etiketler = oku()
                if not etiketler:
                    # kurtarma 1: yan paneli aşağı kaydır (grafik tembel yükleniyor olabilir)
                    for _ in range(4):
                        page.mouse.move(200, 600); page.mouse.wheel(0, 1500); page.wait_for_timeout(900)
                    etiketler = oku()
                    if etiketler: sayac["kaydirma_ile"] = sayac.get("kaydirma_ile", 0) + 1
                if not etiketler:
                    # kurtarma 2: yeniden yükle ve daha uzun bekle
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    try: page.wait_for_selector(SEC, timeout=12000)
                    except Exception: pass
                    etiketler = oku()
                    if etiketler: sayac["yeniden_yukleme_ile"] = sayac.get("yeniden_yukleme_ile", 0) + 1
                if etiketler:
                    ham = json.dumps(etiketler, ensure_ascii=False)
                    canli = next((e for e in etiketler if CANLI_RX.search(e)), None)
                    if canli:
                        m = CANLI_RX.search(canli); yuzde = int(m.group(1) or m.group(2)); etiket = canli; durum = "canli"
                        g = GENEL_RX.search(canli); genel = int(g.group(1)) if g else None
                    else:
                        durum = "canli_yok"
                else:
                    durum = "populer_yok"
                    html = page.content()
                    tani = (f"başlık={page.title()[:60]} | 'Popüler saatler' metni={'Popüler saatler' in html or 'Popular times' in html}"
                            f" | img-role={page.locator('[role=img]').count()} | adres={page.url[:90]}")
                    if sayac.get("populer_yok", 0) == 0 and os.environ.get("ANLIK_EKRAN"):
                        # makine başına ilk "grafik yok" sayfasının görüntüsü ve HTML'i (tanı için artifact'a gider)
                        page.screenshot(path=os.path.join(os.environ["ANLIK_EKRAN"], f"grafik_yok_{a.shard}.png"), full_page=True)
                        open(os.path.join(os.environ["ANLIK_EKRAN"], f"grafik_yok_{a.shard}.html"), "w").write(html)
            except Exception as e:
                durum = "hata"; etiket = f"{type(e).__name__}: {str(e)[:120]}"
            sayac[durum] = sayac.get(durum, 0) + 1
            c.execute("""INSERT OR REPLACE INTO google_anlik_yogunluk
                (olcum_id, feature_id, google_place_id, isim, il, ilce, kategori, lat, lon, olcum_utc, olcum_tr, tr_gun, tr_saat,
                 durum, canli_saat, canli_yuzde, canli_etiket, kosu_id, ham_etiketler, yontem, genelde_yuzde, tani)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                f"{r['feature_id']}|{now.strftime('%Y-%m-%dT%H:%M')}", r["feature_id"], r.get("google_place_id"), r.get("isim"),
                r.get("il"), r.get("ilce"), r.get("kategori"), float(r["lat"]), float(r["lon"]),
                now.isoformat(timespec="seconds"), tr.isoformat(timespec="seconds"), tr.isoweekday(), tr.hour,
                durum, tr.hour if yuzde is not None else None, yuzde, etiket, a.kosu_id, ham, "tarayici", genel, tani))
            if durum == "populer_yok" and sayac.get("populer_yok", 0) <= 3:
                print(f"  tanı (grafik yok): {r.get('isim')} · {tani}", flush=True)
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
