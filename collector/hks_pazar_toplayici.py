"""HKS (Ticaret Bakanlığı Hal Kayıt Sistemi) resmî pazar yeri kaydı → pazarlar.sqlite::hks_pazar

hal.gov.tr/Sayfalar/Pazar-Yerleri.aspx: il başına iki adımlı ASP.NET postback (il seçimi → listele).
Alanlar: ad, tür (Semt/Üretici), adres, il, ilçe, semt/mahalle, kuruluş günleri. Koordinat yok →
collector.geocode ile mahalle/sokak düzeyi (yaklaşık). Belediye açık verisi (İBB/İzmir) varsa o esas.
"""
from __future__ import annotations
import html, os, re, sqlite3, sys, time, urllib.parse, urllib.request, http.cookiejar
from datetime import datetime, timezone
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, tr_title  # noqa: E402
URL = "https://www.hal.gov.tr/Sayfalar/Pazar-Yerleri.aspx"
OUT = os.path.join(REPO, "warehouse", "product", "pazarlar.sqlite")
PRE = "ctl00$ctl37$g_30a7d712_ef07_41e9_ac82_423bb766d072$"
GUN = {"pzt": "pazartesi", "pazartesi": "pazartesi", "salı": "sali", "sali": "sali", "çrş": "carsamba", "çarşamba": "carsamba", "prş": "persembe", "perşembe": "persembe",
       "cuma": "cuma", "cmt": "cumartesi", "cumartesi": "cumartesi", "pazar": "pazar", "paz": "pazar"}


def _hidden(h):
    out = {}
    for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', h):
        n = re.search(r'name="([^"]+)"', m.group(0)); v = re.search(r'value="([^"]*)"', m.group(0))
        if n: out[n.group(1)] = v.group(1) if v else ""
    return out


def gunler(s):
    out = []
    for tok in re.split(r"[,/;\-\s]+", (s or "").lower()):
        g = GUN.get(tok.strip())
        if g and g not in out: out.append(g)
    return ",".join(out)


def il_listesi(il_kodu: int, op, hdr):
    h = op.open(urllib.request.Request(URL, headers=hdr), timeout=30).read().decode("utf-8", "ignore")
    f = _hidden(h); f.update({"__EVENTTARGET": PRE + "ddlIl", "__EVENTARGUMENT": "", PRE + "ddlIl": str(il_kodu), PRE + "ddlPazarTuru": "0"})
    h2 = op.open(urllib.request.Request(URL, data=urllib.parse.urlencode(f).encode(), headers=hdr), timeout=60).read().decode("utf-8", "ignore")
    f = _hidden(h2); f.update({"__EVENTTARGET": "", "__EVENTARGUMENT": "", PRE + "ddlIl": str(il_kodu), PRE + "ddlIlce": "0", PRE + "ddlPazarTuru": "0", PRE + "BtnAra": "Pazar Yeri Bul"})
    h3 = op.open(urllib.request.Request(URL, data=urllib.parse.urlencode(f).encode(), headers=hdr), timeout=180).read().decode("utf-8", "ignore")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h3, re.S):
        cells = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", td))).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 7 and cells[3]: rows.append(cells[:7])
    return rows


def main():
    c = sqlite3.connect(OUT, timeout=60); now = datetime.now(timezone.utc).isoformat()
    c.executescript("""CREATE TABLE IF NOT EXISTS hks_pazar (id INTEGER PRIMARY KEY, il TEXT, ilce TEXT, semt TEXT, mahalle TEXT, ad TEXT, tur TEXT, adres TEXT,
                       gunler TEXT, kapali INTEGER, lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT, UNIQUE (il, ilce, ad, adres, gunler));""")
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)); hdr = {"User-Agent": "Mozilla/5.0 (GEOPROP; heytteknoloji@gmail.com)", "Referer": URL}
    toplam = 0
    for il in range(1, 82):
        try:
            rows = il_listesi(il, op, hdr)
        except Exception as exc:
            print(f"  il {il}: HATA {type(exc).__name__}", flush=True); time.sleep(3); continue
        for ad, tur, adres, il_ad, ilce, semt, gun in rows:
            m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü0-9\.\- ]{2,40}?)\s*(?:Mahallesi|Mah\.|MAHALLESİ|MAH\.|MAHALLESI|Mh\.)", adres, re.I)
            mah = tr_title(m.group(1)) if m else (tr_title(semt) if semt and semt.upper() != "MERKEZ" else None)
            c.execute("INSERT OR IGNORE INTO hks_pazar (il, ilce, semt, mahalle, ad, tur, adres, gunler, kapali, guncellenme) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (il_ad.strip().upper(), tr_title(ilce), tr_title(semt) if semt else None, mah, ad.strip(), tur, adres, gunler(gun),
                       1 if "kapal" in normalize_name(ad) or "kapal" in normalize_name(adres) else None, now))
        c.commit(); toplam += len(rows); print(f"  il {il:2}: {len(rows):4} (toplam {toplam})", flush=True); time.sleep(0.8)
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('hks', ?, ?)", (c.execute("SELECT COUNT(*) FROM hks_pazar").fetchone()[0], now)); c.commit(); c.close()
    print("HKS pazar:", toplam)


if __name__ == "__main__" and "--koordinat" not in sys.argv:
    main()


def koordinat(limit=None):
    """Adres (mahalle/sokak) → collector.geocode; sonra pazar tablosuna 'hks' kaynağıyla birleştir (150 m içinde belediye/OSM kaydı varsa atlanır)."""
    from collector.geocode import adres_kodla
    from geoprop.veri_yardimcilari import haversine_km
    c = sqlite3.connect(OUT, timeout=60); now = datetime.now(timezone.utc).isoformat()
    rows = c.execute("SELECT id, il, ilce, semt, mahalle, ad, adres FROM hks_pazar WHERE lat IS NULL AND koordinat_kaynagi IS NULL").fetchall()
    if limit: rows = rows[:limit]
    print(f"HKS pazar koordinat: {len(rows):,}", flush=True); ok = 0
    for i, (pid, il, ilce, semt, mah, ad, adres) in enumerate(rows, 1):
        q_adres = adres if adres and len(adres) > 8 else (f"{mah} Mahallesi" if mah else "")
        hit = adres_kodla(q_adres, il, ilce, ad if "pazar" in normalize_name(ad) else None)
        if hit: c.execute("UPDATE hks_pazar SET lat=?, lon=?, koordinat_kaynagi=?, guncellenme=? WHERE id=?", (hit[0], hit[1], hit[2], now, pid)); ok += 1
        else: c.execute("UPDATE hks_pazar SET koordinat_kaynagi='bulunamadı', guncellenme=? WHERE id=?", (now, pid))
        if i % 50 == 0: c.commit(); print(f"  {i:,}/{len(rows):,} bulundu {ok:,}", flush=True)
    c.commit()
    # birleştir
    mevcut = c.execute("SELECT lat, lon FROM pazar WHERE kaynak<>'hks'").fetchall(); n = 0
    c.execute("DELETE FROM pazar WHERE kaynak='hks'")
    for il, ilce, mah, ad, tur, gun, kapali, lat, lon in c.execute("SELECT il, ilce, mahalle, ad, tur, gunler, kapali, lat, lon FROM hks_pazar WHERE lat IS NOT NULL").fetchall():
        if any(abs(lat - la) < 0.002 and abs(lon - lo) < 0.003 and haversine_km(lat, lon, la, lo) < 0.15 for la, lo in mevcut): continue
        c.execute("INSERT INTO pazar (kaynak, il, ilce, mahalle, ad, gunler, kapali, tip, lat, lon, guncellenme) VALUES ('hks',?,?,?,?,?,?,?,?,?,?)", (il, ilce, mah, ad, gun, kapali, tur, lat, lon, now)); n += 1
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('hks', ?, ?)", (n, now)); c.commit(); c.close()
    print(f"koordinatlı {ok:,}; pazar tablosuna eklenen {n:,}")


if __name__ == "__main__" and "--koordinat" in sys.argv:
    koordinat()
