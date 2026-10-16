"""
Uluslararası turizm ve havayolu toplayıcıları (12 Ekim 2026; kullanıcı: "turizm için sadece Türkiye değil AB, Rusya vb. kaynaklar; havayolu
istatistikleri, turist harcamaları; çok detaylı").
  eurostat_turizm   Eurostat toplu TSV: avia_par_tr (Türk havalimanları × bütün partner havalimanları, aylık yolcu/sefer/koltuk),
                    avia_par_<ülke> (AB/EFTA/Balkan/İngiltere havalimanları ↔ TÜRK havalimanları satırları), tour_dem_ttw/tnw
                    (AB yerleşiklerinin ülke bazında seyahat/geceleme — TR satırları), tour_occ_arnraw/ninraw (köken ülkeye göre varış/geceleme),
                    avia_paoc (ülke bazında yolcu). Uzun biçim: veri_seti × boyutlar × dönem × değer (+ Eurostat bayrağı). Aylık.
  dunya_bankasi     Dünya Bankası turizm göstergeleri (bütün ülkeler, yıllık): varış, çıkış, gelir, harcama, yolcu taşımacılığı.
  yabanci_kur       Rusya MB (CBR) günlük kurları + ECB referans kurları (EUR bazlı) — yabancı turistin satın alma gücü. Günlük.
  ktb_turizm        KTB YİGM: sınır (milliyete göre gelen), turizm gelir/gider, konaklama, tesis, yat, iç turizm, turist profili sayfalarındaki
                    bütün ek dosyalar (xls/xlsx/rar/pdf) ham saklanır; yenileri eklenir. Haftalık.
  rusya_turizm      Rosstat/FedStat dış turizm sayfaları (Türkiye IP'sine kapalı; GitHub'dan denenir, engelse not düşer). Aylık.
"""
import csv, gzip, html as htmlmod, io, json, re, urllib.parse
from ortak import Istemci, BotEngeli, db, kosu_yaz, simdi, bugun

AVIA_ULKE = ["at", "be", "bg", "ch", "cy", "de", "dk", "ee", "el", "es", "fi", "fr", "hr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mt", "nl", "no",
             "pl", "pt", "ro", "se", "si", "sk", "uk", "rs", "me", "mk", "ba"]
TUR_DS = ["tour_dem_ttw", "tour_dem_tnw", "tour_occ_arnraw", "tour_occ_ninraw", "tour_occ_arnat", "tour_occ_ninat", "avia_paoc", "tour_ce_oar"]


def _tsv_satirlar(b):
    """Eurostat SDMX 2.1 TSV (gz) → (boyut_adlari, [(boyut_degerleri, donem, deger, bayrak)])"""
    t = gzip.decompress(b).decode("utf-8", "replace")
    r = csv.reader(io.StringIO(t), delimiter="\t")
    bas = next(r); boyut = bas[0].split("\\")[0].split(","); donemler = [x.strip() for x in bas[1:]]
    for satir in r:
        anahtar = satir[0].split(",")
        for d, h in zip(donemler, satir[1:]):
            h = h.strip()
            if not h or h.startswith(":"): continue
            m = re.match(r"([-0-9.]+)\s*([a-z]*)", h)
            if m: yield boyut, anahtar, d, float(m.group(1)), m.group(2) or None


def eurostat_turizm(dizin, tam=False):
    c = db(f"{dizin}/eurostat_turizm.sqlite", """CREATE TABLE IF NOT EXISTS gozlem (veri_seti TEXT, anahtar TEXT, boyutlar TEXT, donem TEXT, deger REAL, bayrak TEXT,
          ilk_gorulme TEXT, son_gorulme TEXT, PRIMARY KEY (veri_seti, anahtar, donem));
        CREATE TABLE IF NOT EXISTS hava_hat (veri_seti TEXT, birim TEXT, olcu TEXT, rapor_havalimani TEXT, partner_havalimani TEXT, donem TEXT, deger REAL,
          bayrak TEXT, son_gorulme TEXT, PRIMARY KEY (veri_seti, birim, olcu, rapor_havalimani, partner_havalimani, donem));""")
    ist = Istemci(2.0, zaman_asimi=180); bas = simdi(); n = 0; notlar = []
    url = lambda ds: f"https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{ds}?format=TSV&compressed=true"
    for cc in ["tr"] + AVIA_ULKE:
        ds = f"avia_par_{cc}"; st, b = ist.get(url(ds), ham=True)
        if st != 200 or not b: notlar.append(f"{ds}:{st}"); continue
        k = 0
        for boyut, anahtar, d, v, bay in _tsv_satirlar(b):
            a = dict(zip(boyut, anahtar)); pr = a.get("airp_pr", "")
            if cc != "tr" and "_TR_" not in pr: continue    # yalnız Türkiye bağlantılı hatlar
            p = pr.split("_"); rap = "_".join(p[:2]); par = "_".join(p[2:4]) if len(p) >= 4 else None
            c.execute("INSERT OR REPLACE INTO hava_hat VALUES (?,?,?,?,?,?,?,?,?)", (ds, a.get("unit"), a.get("tra_meas"), rap, par, d, v, bay, simdi()))
            k += 1
        n += k; c.commit()
    for ds in TUR_DS:
        st, b = ist.get(url(ds), ham=True)
        if st != 200 or not b: notlar.append(f"{ds}:{st}"); continue
        for boyut, anahtar, d, v, bay in _tsv_satirlar(b):
            if "TR" not in anahtar: continue                 # Türkiye geçen satırlar (hedef, köken ya da raporlayan ülke)
            key = ",".join(anahtar)
            c.execute("""INSERT INTO gozlem VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET deger = excluded.deger, bayrak = excluded.bayrak,
                         son_gorulme = excluded.son_gorulme""", (ds, key, json.dumps(dict(zip(boyut, anahtar))), d, v, bay, simdi(), simdi())); n += 1
        c.commit()
    kosu_yaz(c, "eurostat_turizm", bas, n, ist.sayac, "tamam" if not notlar else "kismi", "; ".join(notlar) or None); return n, ist.sayac, "; ".join(notlar) or None


WB_GOSTERGE = {"ST.INT.ARVL": "uluslararasi_varis", "ST.INT.DPRT": "uluslararasi_cikis", "ST.INT.RCPT.CD": "turizm_geliri_usd",
               "ST.INT.RCPT.XP.ZS": "turizm_geliri_ihracat_payi", "ST.INT.XPND.CD": "turizm_harcamasi_usd", "ST.INT.XPND.MP.ZS": "turizm_harcamasi_ithalat_payi",
               "ST.INT.TRNR.CD": "yolcu_tasima_geliri_usd", "ST.INT.TRNX.CD": "yolcu_tasima_harcamasi_usd", "ST.INT.TVLR.CD": "seyahat_kalemi_geliri_usd",
               "ST.INT.TVLX.CD": "seyahat_kalemi_harcamasi_usd", "IS.AIR.PSGR": "hava_yolcu_sayisi", "IS.AIR.DPRT": "hava_kalkis_sayisi",
               "NY.GDP.PCAP.CD": "kisi_basi_gsyh_usd", "PA.NUS.FCRF": "resmi_kur_lcu_usd", "SP.POP.TOTL": "nufus"}


def dunya_bankasi(dizin, tam=False):
    c = db(f"{dizin}/dunya_bankasi_turizm.sqlite", """CREATE TABLE IF NOT EXISTS gosterge (ulke_kodu TEXT, ulke TEXT, gosterge_kodu TEXT, gosterge TEXT, yil INTEGER,
        deger REAL, son_gorulme TEXT, PRIMARY KEY (ulke_kodu, gosterge_kodu, yil));""")
    ist = Istemci(0.5); bas = simdi(); n = 0
    for kod, ad in WB_GOSTERGE.items():
        sayfa, sayfalar = 1, 1
        while sayfa <= sayfalar:
            j = ist.json(f"https://api.worldbank.org/v2/country/all/indicator/{kod}?format=json&per_page=20000&page={sayfa}&date=1995:2030")
            if not j or len(j) < 2: break
            sayfalar = j[0].get("pages", 1)
            for r in j[1] or []:
                if r.get("value") is None: continue
                c.execute("INSERT OR REPLACE INTO gosterge VALUES (?,?,?,?,?,?,?)", (r["countryiso3code"] or r["country"]["id"], r["country"]["value"], kod, ad,
                          int(r["date"]), float(r["value"]), simdi())); n += 1
            sayfa += 1
        c.commit()
    kosu_yaz(c, "dunya_bankasi", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


def yabanci_kur(dizin, tam=False):
    import xml.etree.ElementTree as ET
    c = db(f"{dizin}/yabanci_kur.sqlite", """CREATE TABLE IF NOT EXISTS kur (kaynak TEXT, tarih TEXT, kod TEXT, baz TEXT, birim INTEGER, deger REAL, cekim_zamani TEXT,
        PRIMARY KEY (kaynak, tarih, kod, baz));""")
    ist = Istemci(0.5); bas = simdi(); n = 0
    st, b = ist.get("https://www.cbr.ru/scripts/XML_daily.asp", ham=True)
    if st == 200 and b:
        r = ET.fromstring(b); d = r.get("Date"); tarih = f"{d[6:10]}-{d[3:5]}-{d[0:2]}"
        for v in r.findall("Valute"):
            c.execute("INSERT OR REPLACE INTO kur VALUES ('cbr',?,?,'RUB',?,?,?)", (tarih, v.findtext("CharCode"), int(v.findtext("Nominal") or 1),
                      float((v.findtext("Value") or "0").replace(",", ".")), simdi())); n += 1
    son = "?lastNObservations=10" if not tam else "?startPeriod=2015-01-01"
    st, t = ist.get(f"https://data-api.ecb.europa.eu/service/data/EXR/D..EUR.SP00.A{son}&format=csvdata")
    for r in csv.DictReader(io.StringIO(t or "")):
        if r.get("OBS_VALUE"):
            c.execute("INSERT OR REPLACE INTO kur VALUES ('ecb',?,?,'EUR',1,?,?)", (r["TIME_PERIOD"], r["CURRENCY"], float(r["OBS_VALUE"]), simdi())); n += 1
    kosu_yaz(c, "yabanci_kur", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


KTB_SAYFA = ["/TR-249702/sinir-istatistikleri.html", "/TR-201116/turizm-gelirleri-ve-giderleri.html", "/TR-201120/konaklama-istatistikleri.html",
             "/TR-201131/tesis-istatistikleri.html", "/TR-201144/deniz-turizm-isletmeleri-ve-yat-istatistikleri.html",
             "/TR-201148/ic-turizm--hanehalki-arastirmasi.html", "/TR-201149/turist-profili--gelis-amaci-egitim-seviyesi-vb.html",
             "/TR-9851/turizm-istatistikleri.html"]


def ktb_turizm(dizin, tam=False):
    c = db(f"{dizin}/ktb_turizm.sqlite", """CREATE TABLE IF NOT EXISTS dosya (url TEXT PRIMARY KEY, baslik TEXT, sayfa TEXT, uzanti TEXT, boyut INTEGER, ilk_gorulme TEXT,
        icerik BLOB); CREATE TABLE IF NOT EXISTS sayfa (url TEXT PRIMARY KEY, baslik TEXT, son_gorulme TEXT);""")
    ist = Istemci(1.0); bas = simdi(); n = 0; K = "https://yigm.ktb.gov.tr"
    kuyruk = list(KTB_SAYFA); gezildi = set(); dosyalar = {}
    while kuyruk and len(gezildi) < 200:
        s = kuyruk.pop(0)
        if s in gezildi: continue
        gezildi.add(s); st, t = ist.get(K + s)
        if st != 200 or not t: continue
        c.execute("INSERT OR REPLACE INTO sayfa VALUES (?,?,?)", (K + s, (re.search(r"<title>(.*?)</title>", t, re.S) or [None, None])[1], simdi()))
        for u, b in re.findall(r'href="(/Eklenti/[^"]+)"[^>]*>([^<]{0,200})', t):
            dosyalar.setdefault(htmlmod.unescape(u), (htmlmod.unescape(b).strip(), s))
        # bir alt düzey: aynı bölümdeki yıl/ay sayfaları (ör. "2024 Yılı Sınır İstatistikleri")
        if s in KTB_SAYFA:
            for u, b in re.findall(r'href="(/TR-\d+/[^"]+\.html)"[^>]*>([^<]{0,160})', t):
                if re.search(r"20\d\d|ist(at|it)|bülten|bulten|sınır|konaklama|gelir|tesis|yat|profil", b + u, re.I): kuyruk.append(u)
    var = {r[0] for r in c.execute("SELECT url FROM dosya")}
    for u, (b, s) in dosyalar.items():
        if K + u in var: continue
        st, icerik = ist.get(K + urllib.parse.quote(u, safe="/,%?="), ham=True)
        if st != 200 or not icerik: continue
        c.execute("INSERT OR IGNORE INTO dosya VALUES (?,?,?,?,?,?,?)", (K + u, b, K + s, u.split("?")[0].rsplit(".", 1)[-1].lower(), len(icerik), simdi(), icerik))
        n += 1; c.commit()
    kosu_yaz(c, "ktb_turizm", bas, n, ist.sayac, "tamam", f"{len(gezildi)} sayfa, {len(dosyalar)} dosya bağlantısı"); return n, ist.sayac, None


def rusya_turizm(dizin, tam=False):
    c = db(f"{dizin}/rusya_turizm.sqlite", """CREATE TABLE IF NOT EXISTS sayfa (url TEXT, cekim_gunu TEXT, http INTEGER, boyut INTEGER, icerik BLOB, PRIMARY KEY (url, cekim_gunu));""")
    ist = Istemci(2.0, zaman_asimi=40); bas = simdi(); n = 0; notlar = []
    for u in ["https://rosstat.gov.ru/statistics/turizm", "https://www.fedstat.ru/indicator/38479", "https://www.atorus.ru/"]:
        st, b = ist.get(u, ham=True, deneme=2)
        notlar.append(f"{u.split('/')[2]}:{st}")
        if st == 200 and b:
            c.execute("INSERT OR REPLACE INTO sayfa VALUES (?,?,?,?,?)", (u, bugun(), st, len(b), b)); n += 1
    c.commit(); kosu_yaz(c, "rusya_turizm", bas, n, ist.sayac, "tamam" if n else "erisim_yok", "; ".join(notlar)); return n, ist.sayac, "; ".join(notlar)
