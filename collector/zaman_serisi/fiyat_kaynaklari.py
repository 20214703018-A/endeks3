"""
Fiyat zaman serileri (29 Ekim 2026): akaryakıt pompa fiyatları, toptancı hal fiyatları.

akaryakit_fiyat (günlük)
  opet   api.opet.com.tr/api/fuelprices — /prices?ProvinceCode=N: her ilin bütün ilçelerinde bugünkü pompa fiyatı (81+1 il kodu;
         İstanbul 34 Anadolu + 934 Avrupa). /prices/archive?…&StartDate&EndDate: yalnız FİYATIN DEĞİŞTİĞİ günler (her gün değil) →
         ilk koşuda geçmiş doldurulur: her ilin merkez ilçesi 2015-01-01'den, bütün ilçeler son 400 gün (tablo: degisim).
         Günlük seri istenirse aradaki günler türetilmiş katmanda bir önceki değişimle doldurulur (burada yapılmaz).
  po     petrolofisi.com.tr/akaryakit-fiyatlari/<il>-akaryakit-fiyatlari — ilçe başına KDV dahil + KDV hariç fiyat (6 ürün).
  Sunucular art arda isteklerde bağlantıyı kesebiliyor → istekler arası ≥1,5 sn, kopmada 60 sn bekleyip sürdürülür.
hal_fiyat (günlük, GitHub) — yalnız İzmir · hal_hks (günlük, Mac) — hal.gov.tr yurt dışı DNS'te bile çözülmüyor (29 Eki denendi)
  izmir  openapi.izmir.bel.tr/api/ibb/halfiyatlari/{sebzemeyve|balik}/{gün} — asgari/azami/ortalama ücret (2008'den beri;
         geçmişi gece turunda çekildi — burada her koşu son 10 gün, ilk koşu son 60 gün).
  hks    hal.gov.tr Fiyat İstatistikleri (Ticaret Bakanlığı HKS ulusal günlük bülten: ürün/cins/tür, ortalama fiyat, işlem hacmi) —
         ASP.NET formu + "Excel'e çıkar" (UTF-16 HTML tablo). Her koşu son 10 gün, ilk koşu son 60 gün (2017–24 Eyl gece turunda).
Tekrarlı çekimler silinmez: aynı gün+yer+ürün yeniden görülürse son_gorulme güncellenir, DEĞER değişirse delta.py eski hâli
<tablo>__gecmis'e alır (kronolojik veri kuralı).
"""
import html as htmlmod, http.cookiejar, json, re, time, unicodedata, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from ortak import Istemci, ErisimYok, BotEngeli, db, kosu_yaz, simdi, UA
import os
SINIR = int(os.environ.get("ZS_DENEME_SINIRI", "0"))


def _tr_bugun(): return (datetime.now(timezone.utc) + timedelta(hours=3)).date().isoformat()


def _sabirli(ist, url, basliklar=None, deneme=4):
    """Bağlantı kopmasına dayanıklı JSON GET: ErisimYok/boş yanıtta 60 sn bekleyip yeniden dener."""
    for i in range(deneme):
        try:
            j = ist.json(url, basliklar=basliklar)
            if j is not None: return j
        except ErisimYok:
            ist.ardisik_zaman = 0
        time.sleep(60 * (i + 1))
    return None


def _norm(s):
    s = str(s or "").replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).replace("ı", "i"))


# ---------- akaryakıt ----------
AKARYAKIT_DDL = """
CREATE TABLE IF NOT EXISTS fiyat (kaynak TEXT, tarih TEXT, il_kodu TEXT, il TEXT, ilce_kodu TEXT, ilce TEXT, urun_kodu TEXT, urun TEXT,
    fiyat REAL, fiyat_kdv_haric REAL, birim TEXT, ilk_gorulme TEXT, son_gorulme TEXT, PRIMARY KEY (kaynak, tarih, ilce_kodu, urun_kodu));
CREATE TABLE IF NOT EXISTS degisim (kaynak TEXT, tarih TEXT, il_kodu TEXT, il TEXT, ilce_kodu TEXT, ilce TEXT, urun_kodu TEXT, urun TEXT,
    fiyat REAL, cekim_zamani TEXT, PRIMARY KEY (kaynak, tarih, ilce_kodu, urun_kodu));
"""
OPET = "https://api.opet.com.tr/api/fuelprices"
OPET_H = {"Channel": "Web", "Accept": "application/json", "Origin": "https://www.opet.com.tr"}
PO_URUN = ["benzin_95", "motorin", "gazyagi", "kalorifer_yakiti", "fuel_oil", "otogaz"]


def _yaz_fiyat(c, r, now):
    c.execute("""INSERT INTO fiyat VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (kaynak, tarih, ilce_kodu, urun_kodu) DO UPDATE SET
        fiyat=excluded.fiyat, fiyat_kdv_haric=excluded.fiyat_kdv_haric, son_gorulme=excluded.son_gorulme""", r + (now, now))


def akaryakit_fiyat(dizin, tam=False):
    c = db(f"{dizin}/akaryakit_fiyat.sqlite", AKARYAKIT_DDL)
    ist = Istemci(1.5, zaman_asimi=90); bas = simdi(); n = 0; notlar = []; gun = _tr_bugun()
    # 1) Opet bugünkü fiyatlar (il başına bütün ilçeler)
    iller = _sabirli(ist, f"{OPET}/provinces", OPET_H) or []
    if SINIR: iller = iller[:SINIR]
    ilceler = []   # (il_kodu, il, ilce_kodu, ilce)
    for il in iller:
        for d in _sabirli(ist, f"{OPET}/prices?ProvinceCode={il['code']}", OPET_H) or []:
            ilceler.append((str(il["code"]), il["name"], d.get("districtCode"), d.get("districtName")))
            for p in d.get("prices") or []:
                if not p.get("amount"): continue    # 0 = o ilçede satılmıyor
                _yaz_fiyat(c, ("opet", gun, str(il["code"]), il["name"], d.get("districtCode"), d.get("districtName"), p.get("productCode"),
                               p.get("productName"), float(p["amount"]), None, "TL/LT"), simdi()); n += 1
        c.commit()
    notlar.append(f"opet {len(iller)} il / {len(ilceler)} ilçe")
    # 2) Opet fiyat değişim arşivi: ilk koşuda geçmiş, sonra her koşu son 10 gün (yalnız merkez ilçeler; bütün ilçeler günlük fotoğraftan)
    ilk = c.execute("SELECT count(*) FROM degisim WHERE kaynak='opet'").fetchone()[0] == 0 or tam
    merkez = {}
    for ik, ia, dk, da in ilceler:
        a, b = _norm(ia.replace("ANADOLU", "").replace("AVRUPA", "")), _norm(da)
        if ik not in merkez or b in ("merkez", a): merkez[ik] = (ik, ia, dk, da)
    hedef = []
    bugun = date.fromisoformat(gun)
    for ik, ia, dk, da in ilceler:
        if ilk:
            bas_t = date(2015, 1, 1) if (ik, ia, dk, da) == merkez.get(ik) else bugun - timedelta(days=400)
        elif (ik, ia, dk, da) == merkez.get(ik):
            bas_t = bugun - timedelta(days=10)
        else:
            continue
        hedef.append((ik, ia, dk, da, bas_t))
    if SINIR: hedef = hedef[:SINIR]
    m = 0
    for ik, ia, dk, da, bas_t in hedef:
        t = bas_t
        while t <= bugun:
            e = min(t + timedelta(days=730), bugun)
            q = urllib.parse.urlencode({"ProvinceCode": ik, "DistrictCode": dk, "StartDate": t.isoformat(), "EndDate": e.isoformat(),
                                        "IncludeAllProducts": "true"})
            for g in _sabirli(ist, f"{OPET}/prices/archive?{q}", OPET_H) or []:
                for p in g.get("prices") or []:
                    if not p.get("amount"): continue
                    c.execute("INSERT OR REPLACE INTO degisim VALUES (?,?,?,?,?,?,?,?,?,?)", ("opet", (p.get("priceDate") or g.get("day"))[:10], ik, ia,
                              dk, da, p.get("productCode"), p.get("productName"), float(p["amount"]), simdi())); m += 1
            t = e + timedelta(days=1)
        c.commit()
    notlar.append(f"opet değişim {len(hedef)} ilçe ({'geçmiş doldurma' if ilk else 'son 10 gün'}) {m} satır"); n += m
    # 3) PO il sayfaları (ilçe başına KDV dahil / hariç)
    st, t = ist.get("https://www.petrolofisi.com.tr/akaryakit-fiyatlari")
    sayfalar = sorted(set(re.findall(r'href="(/akaryakit-fiyatlari/[a-z\-]+-akaryakit-fiyatlari)"', t or "")))
    sayfalar = [s for s in sayfalar if "bolgesi" not in s]
    if SINIR: sayfalar = sayfalar[:SINIR]
    k = 0
    for s in sayfalar:
        st, t = ist.get("https://www.petrolofisi.com.tr" + s)
        if st != 200 or not t: continue
        il = s.split("/")[-1].replace("-akaryakit-fiyatlari", "")
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
            td = [htmlmod.unescape(re.sub(r"<[^>]+>", " ", x)).split() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(td) != 7 or not td[0]: continue
            ilce = " ".join(td[0])
            for u, hucre in zip(PO_URUN, td[1:]):
                sayi = [float(x.replace(",", ".")) for x in hucre if re.fullmatch(r"\d+[\.,]\d+", x)]
                if not sayi: continue
                birim = next((x for x in hucre if x.startswith("TL/")), None)
                _yaz_fiyat(c, ("po", gun, il, il, f"{il}|{ilce}", ilce, u, u, sayi[0], sayi[1] if len(sayi) > 1 else None, birim), simdi()); k += 1
        c.commit()
    notlar.append(f"po {len(sayfalar)} il {k} satır"); n += k
    kosu_yaz(c, "akaryakit_fiyat", bas, n, ist.sayac, "tamam" if n else "bos", "; ".join(notlar))
    return n, ist.sayac, "; ".join(notlar)


# ---------- hal ----------
HAL_DDL = """
CREATE TABLE IF NOT EXISTS izmir (hal TEXT, bulten_tarihi TEXT, mal_id INTEGER, mal_adi TEXT, mal_tip_id INTEGER, mal_tip_adi TEXT, hal_turu INTEGER,
    birim TEXT, asgari REAL, azami REAL, ortalama REAL, ham TEXT, ilk_gorulme TEXT, son_gorulme TEXT, PRIMARY KEY (hal, bulten_tarihi, mal_id, mal_tip_id, birim));
CREATE TABLE IF NOT EXISTS hks (bulten_tarihi TEXT, veri_tarihi TEXT, urun_adi TEXT, urun_cinsi TEXT, urun_turu TEXT, ortalama_fiyat REAL, islem_hacmi REAL,
    birim TEXT, ham TEXT, ilk_gorulme TEXT, son_gorulme TEXT, PRIMARY KEY (bulten_tarihi, urun_adi, urun_cinsi, urun_turu, birim));
"""
HKS_URL = "https://www.hal.gov.tr/Sayfalar/FiyatDetaylari.aspx"   # http → https yönlendirmesi form gönderimini GET yapıyordu
HKS_P = "ctl00$ctl37$g_7e86b8d6_3aea_47cf_b1c1_939799a091e0$"


def _sayi(s):
    s = str(s or "").strip()
    if "," in s: s = s.replace(".", "").replace(",", ".")
    try: return float(s)
    except ValueError: return None


def _hks_form(t):
    f = {}
    for m in re.finditer(r"<input[^>]+>", t):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
        if a.get("name") and a.get("type", "").lower() in ("hidden", "text"):
            f[htmlmod.unescape(a["name"])] = "" if "InputKeywords" in a["name"] else htmlmod.unescape(a.get("value", ""))
    return f


def _hks_gunler(gunler):
    """HKS ulusal bülten — [(gün, bülten başlığı, satırlar)]. Çerez + ASP.NET form durumu korunur."""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def iste(veri=None):
        r = op.open(urllib.request.Request(HKS_URL, data=urllib.parse.urlencode(veri).encode() if veri else None,
                                           headers={"User-Agent": UA, "Accept-Language": "tr-TR"}), timeout=90)
        return r.read()
    t = iste().decode("utf-8", "replace"); out = []
    for g in gunler:
        time.sleep(1.0)
        try:
            f = _hks_form(t); f[HKS_P + "dateControl$dateControlDate"] = g.strftime("%d.%m.%Y"); f[HKS_P + "btnGet"] = "Fiyat Bul"
            t = iste(f).decode("utf-8", "replace")
            if not re.search(r'id="[^"]*gvFiyatlar', t):
                out.append((g, None, [])); continue
            f = _hks_form(t); f[HKS_P + "btnExcel"] = "Excel'e Çıkar"; f[HKS_P + "rblExcelOptions"] = "2"
            x = iste(f).decode("utf-16", "replace")
            satirlar = [[htmlmod.unescape(re.sub(r"<[^>]+>", "", h)).strip() for h in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
                        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", x, re.S)]
            satirlar = [s for s in satirlar if any(s)]
            bulten = satirlar[0][0] if satirlar else None
            hdr = satirlar[1] if len(satirlar) > 1 else []
            out.append((g, bulten, [dict(zip(hdr, s)) for s in satirlar[2:]]))
        except Exception as e:  # sayfa durumu bozulduysa baştan al
            out.append((g, f"hata: {type(e).__name__}", []))
            t = iste().decode("utf-8", "replace")
    return out


def hal_fiyat(dizin, tam=False):
    c = db(f"{dizin}/hal_fiyat.sqlite", HAL_DDL)
    ist = Istemci(0.5); bas = simdi(); n = 0; notlar = []
    bugun = date.fromisoformat(_tr_bugun())
    geri = 60 if c.execute("SELECT count(*) FROM izmir").fetchone()[0] == 0 or tam else 10
    gunler = [bugun - timedelta(days=i) for i in range(geri, -1, -1)]
    if SINIR: gunler = gunler[-SINIR:]
    for tur, ad in (("sebzemeyve", "İzmir BB Sebze-Meyve Hali"), ("balik", "İzmir BB Balık Hali")):
        k = 0
        for g in gunler:
            j = ist.json(f"https://openapi.izmir.bel.tr/api/ibb/halfiyatlari/{tur}/{g.isoformat()}") or {}
            bt = (j.get("BultenTarihi") or "")[:10]
            for x in j.get("HalFiyatListesi") or []:
                c.execute("""INSERT INTO izmir VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (hal, bulten_tarihi, mal_id, mal_tip_id, birim)
                    DO UPDATE SET asgari=excluded.asgari, azami=excluded.azami, ortalama=excluded.ortalama, ham=excluded.ham, son_gorulme=excluded.son_gorulme""",
                          (ad, bt or g.isoformat(), x.get("MalId"), x.get("MalAdi"), x.get("MalTipId"), x.get("MalTipAdi"), x.get("HalTuru"), x.get("Birim"),
                           x.get("AsgariUcret"), x.get("AzamiUcret"), x.get("OrtalamaUcret"), json.dumps(x, ensure_ascii=False), simdi(), simdi())); k += 1
            c.commit()
        notlar.append(f"izmir {tur} {k}"); n += k
    kosu_yaz(c, "hal_fiyat", bas, n, ist.sayac, "tamam" if n else "bos", "; ".join(notlar))
    return n, ist.sayac, "; ".join(notlar)


def hal_hks(dizin, tam=False):
    """HKS ulusal günlük bülten (Mac'te: hal.gov.tr yurt dışı IP/DNS'e kapalı). Her koşu son 10 gün; ilk koşu son 60 gün."""
    c = db(f"{dizin}/hal_hks.sqlite", HAL_DDL)
    bas = simdi(); n = 0; notlar = []
    bugun = date.fromisoformat(_tr_bugun())
    gunler = [bugun - timedelta(days=i) for i in range(60, -1, -1)]
    if SINIR: gunler = gunler[-SINIR:]
    k = 0; bos = 0
    hks_ilk = c.execute("SELECT count(*) FROM hks").fetchone()[0] == 0
    for g, bulten, satirlar in _hks_gunler(gunler if (hks_ilk or tam) else gunler[-11:]):   # ilk koşu: gece turu (24 Eyl) sonrası boşluk
        if not satirlar: bos += 1
        m = re.search(r"\((\d{2})\.(\d{2})\.(\d{4}) Tarihli", bulten or "")
        veri = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
        for r in satirlar:
            c.execute("""INSERT INTO hks VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (bulten_tarihi, urun_adi, urun_cinsi, urun_turu, birim)
                DO UPDATE SET ortalama_fiyat=excluded.ortalama_fiyat, islem_hacmi=excluded.islem_hacmi, ham=excluded.ham, son_gorulme=excluded.son_gorulme""",
                      (g.isoformat(), veri, r.get("Ürün Adı"), r.get("Ürün Cinsi"), r.get("Ürün Türü"), _sayi(r.get("Ortalama Fiyat")),
                       _sayi(r.get("İşlem Hacmi")), r.get("Birim Adı"), json.dumps(r, ensure_ascii=False), simdi(), simdi())); k += 1
        c.commit()
    notlar.append(f"hks {k} satır ({bos} gün bülten yok/hata)"); n += k
    kosu_yaz(c, "hal_hks", bas, n, 2 * len(gunler), "tamam" if n else "bos", "; ".join(notlar))
    return n, 2 * len(gunler), "; ".join(notlar)
