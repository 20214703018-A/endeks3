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

İşletme kaydı (11 Eki 2026): her ölçümde aynı sayfadan işletmenin o anki Google kaydı da zaman damgasıyla yazılır →
google_place_anlik_kayit (olcum_id ile bağlı): sayfadaki ad, kategori, puan, yorum sayısı, yıldız dağılımı, açık/kapalı durumu,
7 günlük çalışma saatleri, adres, telefon, web sitesi, plus code, fiyat düzeyi ve sayfa öğeleri (data-item-id etiketleri).
Yorum yazan kişilerin adları/yorum metinleri ALINMAZ (kişisel veri). Telefon/web sitesi kanonikte firma kimlik grafiğine bağlanır.

Saat dağılımı (11 Eki 2026): iş akışı her saat çalışır; liste 7 parçaya bölünür ve her saat sıradaki parça ölçülür
(parça = UTC saat sayacı mod 7). 7 ile 24 aralarında asal → her işletme her gün farklı saatlere düşer, 7 günde günün
24 saatinin hepsinde ölçülür (işletme başına günde ~3,4 ölçüm). GitHub bazı zamanlanmış koşuları kaçırsa da döngü kayar, saat boşluğu kalmaz.

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
    c.execute("""CREATE TABLE IF NOT EXISTS google_place_anlik_kayit (
        olcum_id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, olcum_utc TEXT NOT NULL, olcum_tr TEXT, kosu_id TEXT,
        isim_sayfa TEXT, kategori_sayfa TEXT, puan REAL, yorum_sayisi INTEGER, yildiz_dagilimi TEXT,
        acik_durum TEXT, calisma_saatleri TEXT, adres TEXT, telefon TEXT, web_sitesi TEXT, plus_code TEXT, fiyat_duzeyi TEXT,
        ogeler TEXT, sayfa_url TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_place_kayit_fid ON google_place_anlik_kayit(feature_id, olcum_utc)")
    c.commit()
    return c


# Sayfadaki işletme kaydı (yorum yazanların adları/yorum metinleri bilerek ALINMAZ)
KAYIT_JS = r"""() => {
  const all = s => Array.from(document.querySelectorAll(s));
  const h1 = document.querySelector('h1');
  const imgs = all('[role=img][aria-label]').map(e => e.getAttribute('aria-label') || '');
  const ogeler = all('[data-item-id]').map(e => ({id: e.getAttribute('data-item-id'), etiket: (e.getAttribute('aria-label') || '').trim(),
                                                  href: e.getAttribute('href')}))
                    .filter(o => !/review|yorum/i.test(o.id || ''));
  const saat = all('button[aria-label*="Çalışma saatlerini kopyala"], button[aria-label*="Copy open hours"]').map(e => e.getAttribute('aria-label'));
  // açık/kapalı satırı (ör. "Açık · Kapanış saati: 22:00"); önce bilinen kutu, yoksa metin kalıbı
  const kutu = document.querySelector('span.ZDu9vd, div.o0Svhf');
  const durum = (kutu && (kutu.innerText || '').trim()) || all('span').map(e => (e.innerText || '').trim())
                    .find(t => /^(Açık|Kapalı|Kalıcı olarak kapandı|Geçici olarak kapalı|Yakında kapanacak|Yakında açılacak|Open|Closed)( |·|$)/.test(t) && t.length < 80);
  const fiyat = all('[aria-label^="Fiyat"], [aria-label^="Price"], [aria-label*="kişi başı"]').map(e => e.getAttribute('aria-label'))[0] || null;
  const kat = all('button[jsaction*="category"]').map(e => e.innerText.trim()).filter(Boolean)[0] || null;
  return {isim: h1 ? h1.innerText.trim() : null, kategori: kat, imgs, ogeler, saat, durum: durum || null, fiyat};
}"""
SAYI = lambda t: int(re.sub(r"[^\d]", "", t)) if t and re.search(r"\d", t) else None


def kayit_ayikla(d):
    """KAYIT_JS çıktısı → tablo alanları (yalnız sayfada gerçekten yazanlar; tahmin yok)."""
    puan = yorum = None; dag = {}
    for t in d.get("imgs") or []:
        m = re.match(r"^\s*([1-5])\s*yıldızlı\s*,\s*([\d.]+)\s*yorum", t)
        if m: dag[m.group(1)] = SAYI(m.group(2)); continue
        m = re.match(r"^\s*(\d,\d|\d)\s*yıldızlı\s*$", t)
        if m and puan is None: puan = float(m.group(1).replace(",", "."))
        m = re.match(r"^\s*([\d.]+)\s*yorum\s*$", t)
        if m and yorum is None: yorum = SAYI(m.group(1))
    og = d.get("ogeler") or []
    bul = lambda f: next((o for o in og if f(o.get("id") or "")), None)
    adr, tel, web, plus = bul(lambda i: i == "address"), bul(lambda i: i.startswith("phone:tel:")), bul(lambda i: i == "authority"), bul(lambda i: i == "oloc")
    temiz = lambda o, on: (o["etiket"].split(":", 1)[1].strip() if o and ":" in o["etiket"] else None) if o else None
    saat = {}
    for t in d.get("saat") or []:
        g, _, h = t.partition(",")
        saat[g.strip()] = h.replace("Çalışma saatlerini kopyala", "").replace("Copy open hours", "").strip(" ,")
    return dict(isim_sayfa=d.get("isim"), kategori_sayfa=d.get("kategori"), puan=puan, yorum_sayisi=yorum,
                yildiz_dagilimi=json.dumps(dag, ensure_ascii=False) if dag else None, acik_durum=(" ".join(re.sub("[\ue000-\uf8ff]", " ", d["durum"]).split()) or None) if d.get("durum") else None,
                calisma_saatleri=json.dumps(saat, ensure_ascii=False) if saat else None, adres=temiz(adr, "Adres"),
                telefon=(tel["id"].split("phone:tel:", 1)[1] if tel else None), web_sitesi=(web.get("href") if web else None),
                plus_code=temiz(plus, "Plus code"), fiyat_duzeyi=d.get("fiyat"),
                ogeler=json.dumps([o for o in og if o.get("id") not in ("address", "oloc")], ensure_ascii=False) if og else None)


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
    ap.add_argument("--tur-dilimi", type=int, default=-1, help="-1: saat bazlı otomatik (UTC saat sayacı mod tur-sayisi)")
    ap.add_argument("--tur-sayisi", type=int, default=7,
                    help="saatlik koşu; 7 parça (24 ile aralarında asal) → her işletme 7 günde 24 saatin hepsinde ölçülür; "
                         "9.000 / 7 ≈ 1.290 işletme/koşu ≈ 215/makine ≈ 32 dk")
    ap.add_argument("--kosu-id", default=os.environ.get("GITHUB_RUN_ID", "yerel"))
    a = ap.parse_args()
    # saat sayacı: art arda saatlik koşular listenin sıradaki parçasını ölçer; her parça her gün farklı saatlere düşer
    dilim = a.tur_dilimi if a.tur_dilimi >= 0 else int(time.time() // 3600) % a.tur_sayisi
    hedef = list(hedefler(a.hedef, a.shard, a.num_shards, dilim, a.tur_sayisi))
    random.shuffle(hedef)
    if a.limit: hedef = hedef[:a.limit]
    print(f"shard {a.shard}/{a.num_shards} · tur dilimi {dilim}/{a.tur_sayisi}: {len(hedef):,} işletme", flush=True)
    c = init_db(a.out)
    from playwright.sync_api import sync_playwright
    sayac = {}; t0 = time.time()
    # Google bazı makinelere (IP'ye göre, bölgeden bağımsız; 3 Eki ölçümü 8/18 tam) "kısıtlı görünüm" veriyor: sayfa tam ama
    # "Popüler saatler" bölümü yok. Makine hiç tam görünüm görmediyse grafiksiz sayfa 'kisitli_gorunum' (ölçülemedi) sayılır,
    # 'populer_yok' DEĞİL. İlk 5 işletmede tam görünüm yoksa önce temiz oturum, sonra mobil görünüm denenir; yine yoksa çıkılır.
    tam_gorunum_goruldu = False; kisitli_seri = 0; varyant = 0
    VARYANTLAR = [
        dict(locale="tr-TR", timezone_id="Europe/Istanbul", viewport={"width": 1280, "height": 900},
             user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
        dict(locale="tr-TR", timezone_id="Europe/Istanbul", viewport={"width": 1366, "height": 860},
             user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
        dict(locale="tr-TR", timezone_id="Europe/Istanbul", viewport={"width": 1440, "height": 900},
             user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"),
        "yeniden_baslat",   # tarayıcıyı tamamen kapatıp yeniden aç (yeni süreç, yeni oturum)
    ]
    # Not: mobil görünüm (Pixel 7) denendi — Haritalar sayfası hiç yüklenmiyor, her işletme 30 sn zaman aşımı (4 Eki).
    with sync_playwright() as p:
        tarayici = {"br": p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])}
        def yeni_sayfa(v):
            if v == "yeniden_baslat":
                try: tarayici["br"].close()
                except Exception: pass
                tarayici["br"] = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                v = VARYANTLAR[0]
            cx = tarayici["br"].new_context(**v); pg = cx.new_page()
            pg.route(re.compile(r".*\.(png|jpg|jpeg|webp|gif|woff2?)(\?.*)?$"), lambda r: r.abort())   # görseller gereksiz
            return cx, pg
        ctx, page = yeni_sayfa(VARYANTLAR[0])
        for i, r in enumerate(hedef, 1):
            if time.time() - t0 > a.max_seconds:
                print(f"  süre doldu ({i-1:,}/{len(hedef):,})", flush=True); break
            now = datetime.now(timezone.utc); tr = now.astimezone(TR)
            durum, yuzde, etiket, ham, genel, tani, kayit = "hata", None, None, None, None, None, None
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
                try:
                    kayit = kayit_ayikla(page.evaluate(KAYIT_JS)); kayit["sayfa_url"] = page.url[:500]
                except Exception as e:
                    sayac["kayit_hata"] = sayac.get("kayit_hata", 0) + 1
                if etiketler:
                    ham = json.dumps(etiketler, ensure_ascii=False)
                    canli = next((e for e in etiketler if CANLI_RX.search(e)), None)
                    if canli:
                        m = CANLI_RX.search(canli); yuzde = int(m.group(1) or m.group(2)); etiket = canli; durum = "canli"
                        g = GENEL_RX.search(canli); genel = int(g.group(1)) if g else None
                    else:
                        durum = "canli_yok"
                else:
                    html = page.content()
                    durum = "populer_yok" if tam_gorunum_goruldu else "kisitli_gorunum"
                    tani = (f"başlık={page.title()[:60]} | 'Popüler saatler' metni={'Popüler saatler' in html or 'Popular times' in html}"
                            f" | img-role={page.locator('[role=img]').count()} | adres={page.url[:90]}")
                    if sayac.get("populer_yok", 0) + sayac.get("kisitli_gorunum", 0) == 0 and os.environ.get("ANLIK_EKRAN"):
                        # makine başına ilk "grafik yok" sayfasının görüntüsü ve HTML'i (tanı için artifact'a gider)
                        page.screenshot(path=os.path.join(os.environ["ANLIK_EKRAN"], f"grafik_yok_{a.shard}.png"), full_page=True)
                        open(os.path.join(os.environ["ANLIK_EKRAN"], f"grafik_yok_{a.shard}.html"), "w").write(html)
            except Exception as e:
                durum = "hata"; etiket = f"{type(e).__name__}: {str(e)[:120]}"
            if durum in ("canli", "canli_yok"):
                tam_gorunum_goruldu = True; kisitli_seri = 0
            elif durum in ("kisitli_gorunum", "hata"):
                kisitli_seri += 1        # zaman aşımı serisi de (bozuk oturum) varyant değiştirmeyi tetikler
            sayac[durum] = sayac.get(durum, 0) + 1
            c.execute("""INSERT OR REPLACE INTO google_anlik_yogunluk
                (olcum_id, feature_id, google_place_id, isim, il, ilce, kategori, lat, lon, olcum_utc, olcum_tr, tr_gun, tr_saat,
                 durum, canli_saat, canli_yuzde, canli_etiket, kosu_id, ham_etiketler, yontem, genelde_yuzde, tani)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                f"{r['feature_id']}|{now.strftime('%Y-%m-%dT%H:%M')}", r["feature_id"], r.get("google_place_id"), r.get("isim"),
                r.get("il"), r.get("ilce"), r.get("kategori"), float(r["lat"]), float(r["lon"]),
                now.isoformat(timespec="seconds"), tr.isoformat(timespec="seconds"), tr.isoweekday(), tr.hour,
                durum, tr.hour if yuzde is not None else None, yuzde, etiket, a.kosu_id, ham, "tarayici", genel, tani))
            if kayit and kayit.get("isim_sayfa"):
                k = ["isim_sayfa", "kategori_sayfa", "puan", "yorum_sayisi", "yildiz_dagilimi", "acik_durum", "calisma_saatleri", "adres",
                     "telefon", "web_sitesi", "plus_code", "fiyat_duzeyi", "ogeler", "sayfa_url"]
                c.execute(f"INSERT OR REPLACE INTO google_place_anlik_kayit (olcum_id, feature_id, olcum_utc, olcum_tr, kosu_id, {','.join(k)}) "
                          f"VALUES (?,?,?,?,?,{','.join('?' * len(k))})",
                          (f"{r['feature_id']}|{now.strftime('%Y-%m-%dT%H:%M')}", r["feature_id"], now.isoformat(timespec="seconds"),
                           tr.isoformat(timespec="seconds"), a.kosu_id, *[kayit.get(x) for x in k]))
                sayac["kayit"] = sayac.get("kayit", 0) + 1
            if durum in ("populer_yok", "kisitli_gorunum") and sayac.get(durum, 0) <= 2:
                print(f"  tanı (grafik yok): {r.get('isim')} · {tani}", flush=True)
            if i % 25 == 0:
                c.commit(); print(f"  {i:,}/{len(hedef):,} · {sayac} · {(time.time()-t0)/i:.1f} sn/işletme", flush=True)
            if i <= 3 and ham:
                print(f"  örnek etiketler ({r.get('isim')}): {ham[:300]}", flush=True)
            if kisitli_seri >= 5 and (not tam_gorunum_goruldu or durum == "hata"):
                varyant += 1
                if varyant >= len(VARYANTLAR):
                    print(f"  [!] bu makinede ölçüm olmuyor ({len(VARYANTLAR)} varyant denendi) — kalan {len(hedef)-i:,} işletme "
                          f"ölçülmeden çıkılıyor", flush=True)
                    break
                print(f"  [!] art arda {kisitli_seri} kısıtlı görünüm/zaman aşımı — tarayıcı varyantı {varyant} deneniyor", flush=True)
                ctx.close(); ctx, page = yeni_sayfa(VARYANTLAR[varyant]); kisitli_seri = 0
            time.sleep(random.uniform(0.3, 0.8))
        tarayici["br"].close()
    c.commit(); c.close()
    olcum = sum(v for k, v in sayac.items() if k in ("canli", "canli_yok", "populer_yok", "kisitli_gorunum", "hata"))
    print(f"bitti: {olcum:,} ölçüm · {sayac}", flush=True)


if __name__ == "__main__":
    main()
