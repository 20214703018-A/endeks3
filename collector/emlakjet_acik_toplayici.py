#!/usr/bin/env python3
"""
EMLAKJET AÇIK VERİ TOPLAYICISI — herkese açık fiyat verisi (veri kaybı denetimi 16 Eki 2026).

İki mod:
  trend   emlakjet.com/api/endeksa/dynamictrend — daha önce alınmamış mülk tipleri, il/ilçe/mahalle (51.206 bölge):
            trend_arazi    PropertyCategory=3 PropertyType=2  (arsa sayfasındaki "arazi")
            trend_daire    PropertyCategory=1 PropertyType=1
            trend_mustakil PropertyCategory=1 PropertyType=3
          (konut tümü 1/4 ve arsa 3/1 daha önce alındı.) Ham yanıt saklanır: aylık trend + kırılımlar + ısı haritası.
  piyasa  emlakjet.com/emlak-piyasasi/<tip> sayfaları (sunucuda üretilmiş HTML): Türkiye → il → ilçe sayfalarındaki alt bölge tabloları
          ("rows": ad, m² fiyatı, toplam fiyat, amortisman, yıllık değişim, bağlantı). İlçe sayfası mahalle satırlarını verir.
          Tipler: satilik-ticari, kiralik-is-yeri, satilik-konut, kiralik-konut, satilik-arsa, satilik-arazi.
Kibar: istek başına ≥ 1 sn, 429/5xx'te üstel bekleme; bot doğrulaması atlatılmaz (görülürse durur). Kişisel veri yok.
Kullanım:
  emlakjet_acik_toplayici.py trend  --bolge collector/data/emlakjet_bolge_kimlik.json.gz --shard 3/20 --cikti out.sqlite
  emlakjet_acik_toplayici.py piyasa --tip satilik-ticari --cikti out.sqlite
"""
import argparse, gzip, json, random, re, sqlite3, time, urllib.error, urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
TREND = ("https://www.emlakjet.com/api/endeksa/dynamictrend?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level={lv}{bolge}"
         "&PropertyCategory={pc}&PropertyType={pt}&Rooms=5&Static=false&Trend=true&Types=true&Wkt=")
TIPLER = {"trend_arazi": (3, 2), "trend_daire": (1, 1), "trend_mustakil": (1, 3)}
KOK = "https://www.emlakjet.com"


def simdi(): return datetime.now(timezone.utc).isoformat(timespec="seconds")


def getir(url, deneme=5):
    bekle = 2.0
    for _ in range(deneme):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html", "Referer": "https://www.emlakjet.com/",
                                                       "Accept-Language": "tr-TR,tr;q=0.9"})
            with urllib.request.urlopen(req, timeout=40) as f:
                return f.status, f.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(bekle + random.random()); bekle *= 2; continue
            try: return e.code, e.read().decode("utf-8", "replace")[:2000]
            except Exception: return e.code, None
        except Exception:
            time.sleep(bekle + random.random()); bekle *= 2
    return None, None


def bot_mu(metin): return bool(metin) and any(k in metin.lower() for k in ("captcha", "cf-chl", "challenge-platform", "attention required"))


def trend(a):
    i, n = map(int, a.shard.split("/"))
    hedef = [tuple(x) for x in json.load(gzip.open(a.bolge, "rt", encoding="utf-8"))]
    pay = [h for j, h in enumerate(hedef) if j % n == i - 1]
    uclar = [u for u in a.uclar.split(",") if u]
    o = sqlite3.connect(a.cikti)
    o.execute("""CREATE TABLE IF NOT EXISTS yanit (uc TEXT, seviye INTEGER, city_id INTEGER, county_id INTEGER, district_id INTEGER, bolge_adi TEXT,
                 http INTEGER, alinma_zamani TEXT, json TEXT, PRIMARY KEY (uc, seviye, city_id, county_id, district_id))""")
    yapilmis = set(o.execute("SELECT uc, seviye, city_id, county_id, district_id FROM yanit WHERE http = 200").fetchall())
    isler = [(u, h) for h in pay for u in uclar if (u, h[0], h[1], h[2], h[3]) not in yapilmis]
    print(f"bölge {len(hedef):,} · pay {len(pay):,} · iş {len(isler):,}", flush=True)
    t0 = time.time(); seri = 0
    for m, (uc, (lv, c, k, d, ad)) in enumerate(isler, 1):
        if time.time() - t0 > a.sure: print("süre doldu", flush=True); break
        pc, pt = TIPLER[uc]
        bolge = f"&CityId={c}" + (f"&CountyId={k}" if lv >= 2 else "") + (f"&DistrictId={d}" if lv == 3 else "")
        t1 = time.time(); kod, metin = getir(TREND.format(lv=lv, bolge=bolge, pc=pc, pt=pt))
        o.execute("INSERT OR REPLACE INTO yanit VALUES (?,?,?,?,?,?,?,?,?)", (uc, lv, c, k, d, ad, kod, simdi(), metin))
        if kod == 200: seri = 0
        else:
            seri += 1
            if bot_mu(metin): print("bot doğrulaması — duruluyor", flush=True); break
            if seri >= 60: print("art arda 60 hata — duruluyor", flush=True); break
        if m % 300 == 0: o.commit(); print(f"{m:,}/{len(isler):,} · {m / (time.time() - t0):.2f}/sn", flush=True)
        time.sleep(max(0.0, a.aralik - (time.time() - t1)))
    o.commit()
    print("BİTTİ", o.execute("SELECT uc, http, count(*) FROM yanit GROUP BY 1, 2").fetchall(), flush=True)


def satirlar(html):
    """Sayfadaki "rows":[...] dizilerini JSON olarak çıkarır (RSC akışındaki kaçışlar çözülür)."""
    s = html.replace('\\"', '"').replace("\\\\", "\\")
    out = []
    for m in re.finditer(r'"rows":\[', s):
        i = m.end() - 1; derin = 0
        for j in range(i, min(len(s), i + 2_000_000)):
            ch = s[j]
            if ch == "[": derin += 1
            elif ch == "]":
                derin -= 1
                if derin == 0:
                    try: out.extend(x for x in json.loads(s[i:j + 1]) if isinstance(x, dict))
                    except Exception: pass
                    break
    return out


def piyasa(a):
    o = sqlite3.connect(a.cikti)
    o.execute("""CREATE TABLE IF NOT EXISTS sayfa (tip TEXT, yol TEXT PRIMARY KEY, seviye INTEGER, http INTEGER, alinma_zamani TEXT,
                 satir_sayisi INTEGER, satirlar_json TEXT, baslik TEXT)""")
    kuyruk = [(f"/emlak-piyasasi/{a.tip}", 0)]; gorulen = set(); seri = 0; t0 = time.time()
    while kuyruk:
        yol, sv = kuyruk.pop(0)
        if yol in gorulen: continue
        gorulen.add(yol)
        once = o.execute("SELECT satirlar_json FROM sayfa WHERE yol = ? AND http = 200", [yol]).fetchone()
        if once: rows = json.loads(once[0] or "[]")
        else:
            t1 = time.time(); kod, html = getir(KOK + yol)
            rows = satirlar(html) if kod == 200 and html else []
            baslik = (re.search(r"<title>([^<]+)</title>", html or "") or [None, None])[1]
            o.execute("INSERT OR REPLACE INTO sayfa VALUES (?,?,?,?,?,?,?,?)", (a.tip, yol, sv, kod, simdi(), len(rows), json.dumps(rows, ensure_ascii=False), baslik))
            o.commit()
            if kod != 200:
                seri += 1
                if bot_mu(html): print("bot doğrulaması — duruluyor", flush=True); break
                if seri >= 30: print("art arda 30 hata — duruluyor", flush=True); break
            else: seri = 0
            time.sleep(max(0.0, a.aralik - (time.time() - t1)))
        if sv < 2:   # Türkiye → il → ilçe; ilçe sayfasının satırları mahallelerdir (mahalle sayfası açılmaz)
            for r in rows:
                h = r.get("regionHref")
                if h and r.get("regionLinkable", True) and h.startswith(f"/emlak-piyasasi/{a.tip}/"): kuyruk.append((h, sv + 1))
        if len(gorulen) % 100 == 0: print(f"{a.tip}: {len(gorulen):,} sayfa · kuyruk {len(kuyruk):,} · {time.time()-t0:.0f} sn", flush=True)
    print("BİTTİ", a.tip, o.execute("SELECT seviye, http, count(*), sum(satir_sayisi) FROM sayfa GROUP BY 1, 2").fetchall(), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("mod", choices=["trend", "piyasa"]); ap.add_argument("--cikti", required=True)
    ap.add_argument("--bolge"); ap.add_argument("--shard", default="1/1"); ap.add_argument("--uclar", default=",".join(TIPLER))
    ap.add_argument("--tip", default="satilik-ticari"); ap.add_argument("--aralik", type=float, default=1.0)
    ap.add_argument("--sure", type=float, default=5.5 * 3600)
    a = ap.parse_args()
    trend(a) if a.mod == "trend" else piyasa(a)
