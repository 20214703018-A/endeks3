"""
Ek zaman serisi kaynakları (12 Ekim 2026; kullanıcı: "eksik verilerin hepsini ekle ve başlat").
  afad_deprem    AFAD deprem kayıtları (konum, büyüklük, derinlik) — günlük; ilk koşu 1 yıl geri
  ispark         İSPARK otoparkları: kapasite, boş yer, doluluk — 3 saatte bir anlık görüntü
  ibb_acik_veri  İBB açık veri portalı: bütün veri setlerinin kataloğu (son güncelleme) + trafik/otopark/yolcu/ulaşım/hava kalitesi
                 kaynak dosyaları (CSV/XLSX/JSON ≤ 60 MB; değiştiyse yeniden) — haftalık
  appstore       App Store Türkiye en çok indirilen ücretsiz/ücretli ilk 100 uygulama — günlük
  tranco         Tranco web sitesi popülerlik sıralaması: bütün .tr alan adları + dünya ilk 20.000 — günlük
  gdelt          GDELT 2.0 olay akışı: Türkiye'de geçen ya da Türk aktörlü haber olayları (son 24 saat, 15 dk'lık dosyalar) — günlük
  hisse_fiyat    BIST hisse günlük kapanış/hacim (İş Yatırım) — iş günü; ilk koşu 2015'ten
  resmi_gazete   Resmî Gazete günlük fihristi (başlık, bölüm, bağlantı) — günlük
  dosya_sayfa    istatistik dosya sayfaları (TOBB kurulan/kapanan şirket, KGM trafik hacmi, TBB banka istatistikleri): yeni dosyalar ham BLOB — haftalık
  bkm_tablo      BKM kartlı ödeme tabloları (sektörel gelişim, yerli/yabancı kartların yurt içi/dışı kullanımı, il bazlı …): HTML tablo satırları — haftalık
"""
import csv, gzip, html as htmlmod, io, json, re, time, urllib.parse, zipfile
from datetime import date, datetime, timedelta, timezone
from ortak import Istemci, BotEngeli, db, kosu_yaz, simdi, bugun


def afad_deprem(dizin, tam=False):
    c = db(f"{dizin}/afad_deprem.sqlite", """CREATE TABLE IF NOT EXISTS deprem (event_id TEXT PRIMARY KEY, tarih TEXT, lat REAL, lon REAL, derinlik REAL, buyukluk REAL,
        tur TEXT, yer TEXT, il TEXT, ilce TEXT, mahalle TEXT, ham TEXT, cekim_zamani TEXT);""")
    ist = Istemci(1.0); bas = simdi(); n = 0
    son = c.execute("SELECT max(tarih) FROM deprem").fetchone()[0]
    b = (datetime.fromisoformat(son[:10]) - timedelta(days=2)).date() if son else date.today() - timedelta(days=365)
    while b <= date.today():
        e = min(b + timedelta(days=30), date.today() + timedelta(days=1))
        j = ist.json(f"https://deprem.afad.gov.tr/apiv2/event/filter?start={b}T00:00:00&end={e}T00:00:00&format=json") or []
        for x in j:
            c.execute("INSERT OR REPLACE INTO deprem VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (x.get("eventID"), x.get("date"), float(x.get("latitude") or 0),
                      float(x.get("longitude") or 0), float(x.get("depth") or 0), float(x.get("magnitude") or 0), x.get("type"), x.get("location"),
                      x.get("province"), x.get("district"), x.get("neighborhood"), json.dumps(x, ensure_ascii=False), simdi())); n += 1
        c.commit(); b = e
    kosu_yaz(c, "afad_deprem", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


def ispark(dizin, tam=False):
    c = db(f"{dizin}/ispark.sqlite", """CREATE TABLE IF NOT EXISTS otopark (park_id INTEGER, cekim_saati TEXT, ad TEXT, lat REAL, lon REAL, kapasite INTEGER, bos INTEGER,
        ilce TEXT, park_turu TEXT, ucretsiz_dk INTEGER, acik INTEGER, ham TEXT, PRIMARY KEY (park_id, cekim_saati));""")
    ist = Istemci(1.0); bas = simdi(); n = 0; saat = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00Z")
    for x in ist.json("https://api.ibb.gov.tr/ispark/Park") or []:
        c.execute("INSERT OR REPLACE INTO otopark VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (x.get("parkID"), saat, x.get("parkName"), float(x.get("lat") or 0),
                  float(x.get("lng") or 0), x.get("capacity"), x.get("emptyCapacity"), x.get("district"), x.get("parkType"), x.get("freeTime"),
                  x.get("isOpen"), json.dumps(x, ensure_ascii=False))); n += 1
    c.commit(); kosu_yaz(c, "ispark", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


IBB_ANAHTAR = re.compile(r"trafik|otopark|ispark|yolcu|yolculuk|istasyon|metro|tramvay|otobüs|otobus|vapur|deniz|bisiklet|hava kalite|ulaşım|ulasim|akaryakıt|nüfus|nufus", re.I)


def ibb_acik_veri(dizin, tam=False):
    c = db(f"{dizin}/ibb_acik_veri.sqlite", """CREATE TABLE IF NOT EXISTS veri_seti (paket_id TEXT PRIMARY KEY, ad TEXT, baslik TEXT, kurum TEXT, etiketler TEXT,
          son_degisim TEXT, kaynak_sayisi INTEGER, son_gorulme TEXT);
        CREATE TABLE IF NOT EXISTS kaynak (kaynak_id TEXT, son_degisim TEXT, paket_id TEXT, ad TEXT, bicim TEXT, url TEXT, boyut INTEGER, icerik BLOB,
          cekim_zamani TEXT, PRIMARY KEY (kaynak_id, son_degisim));""")
    ist = Istemci(0.7, zaman_asimi=180); bas = simdi(); n = 0; bas_ = 0
    while True:
        j = ist.json(f"https://data.ibb.gov.tr/api/3/action/package_search?rows=200&start={bas_}")
        if not j or not j.get("success"): break
        ps = j["result"]["results"]
        if not ps: break
        for p in ps:
            c.execute("INSERT OR REPLACE INTO veri_seti VALUES (?,?,?,?,?,?,?,?)", (p["id"], p.get("name"), p.get("title"), (p.get("organization") or {}).get("title"),
                      json.dumps([t.get("name") for t in p.get("tags") or []], ensure_ascii=False), p.get("metadata_modified"), len(p.get("resources") or []), simdi()))
            if not IBB_ANAHTAR.search((p.get("title") or "") + " " + " ".join(t.get("name", "") for t in p.get("tags") or [])): continue
            for r in p.get("resources") or []:
                bic = (r.get("format") or "").lower(); sd = r.get("last_modified") or r.get("metadata_modified") or r.get("created")
                if bic not in ("csv", "xlsx", "xls", "json", "geojson") or not r.get("url"): continue
                if c.execute("SELECT 1 FROM kaynak WHERE kaynak_id = ? AND son_degisim = ?", (r["id"], sd)).fetchone(): continue
                if (r.get("size") or 0) > 60e6: continue
                st, b = ist.get(r["url"], ham=True, deneme=2)
                if st != 200 or not b or len(b) > 60e6: continue
                c.execute("INSERT OR REPLACE INTO kaynak VALUES (?,?,?,?,?,?,?,?,?)", (r["id"], sd, p["id"], r.get("name"), bic, r["url"], len(b), b, simdi())); n += 1
            c.commit()
        bas_ += len(ps)
    kosu_yaz(c, "ibb_acik_veri", bas, n, ist.sayac, "tamam", f"{bas_} veri seti"); return n, ist.sayac, None


def appstore(dizin, tam=False):
    c = db(f"{dizin}/appstore.sqlite", """CREATE TABLE IF NOT EXISTS sira (liste TEXT, cekim_gunu TEXT, sira INTEGER, app_id TEXT, ad TEXT, gelistirici TEXT, tur TEXT,
        url TEXT, cikis TEXT, PRIMARY KEY (liste, cekim_gunu, sira));""")
    ist = Istemci(1.0); bas = simdi(); n = 0
    for liste in ("top-free", "top-paid"):
        j = ist.json(f"https://rss.marketingtools.apple.com/api/v2/tr/apps/{liste}/100/apps.json") or {}
        for i, a in enumerate((j.get("feed") or {}).get("results") or [], 1):
            c.execute("INSERT OR REPLACE INTO sira VALUES (?,?,?,?,?,?,?,?,?)", (liste, bugun(), i, a.get("id"), a.get("name"), a.get("artistName"),
                      ",".join(g.get("name", "") for g in a.get("genres") or []), a.get("url"), a.get("releaseDate"))); n += 1
    c.commit(); kosu_yaz(c, "appstore", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


def tranco(dizin, tam=False):
    c = db(f"{dizin}/tranco.sqlite", """CREATE TABLE IF NOT EXISTS sira (liste_id TEXT, liste_tarihi TEXT, alan_adi TEXT, sira INTEGER, PRIMARY KEY (liste_id, alan_adi));""")
    ist = Istemci(1.0, zaman_asimi=180); bas = simdi(); n = 0
    j = ist.json("https://tranco-list.eu/api/lists/date/latest") or {}
    lid = j.get("list_id")
    if lid and not c.execute("SELECT 1 FROM sira WHERE liste_id = ? LIMIT 1", (lid,)).fetchone():
        st, b = ist.get(f"https://tranco-list.eu/download/{lid}/1000000", ham=True)
        if st == 200 and b:
            for r in csv.reader(io.StringIO(b.decode("utf-8", "replace"))):
                if len(r) < 2: continue
                s, d = int(r[0]), r[1]
                if s <= 20000 or d.endswith(".tr"):
                    c.execute("INSERT OR REPLACE INTO sira VALUES (?,?,?,?)", (lid, j.get("created_on") or bugun(), d, s)); n += 1
    c.commit(); kosu_yaz(c, "tranco", bas, n, ist.sayac, "tamam", f"liste {lid}"); return n, ist.sayac, None


GDELT_KOL = ["GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate", "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
             "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code", "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code", "Actor2Code",
             "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode", "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code", "Actor2Type1Code",
             "Actor2Type2Code", "Actor2Type3Code", "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass", "GoldsteinScale",
             "NumMentions", "NumSources", "NumArticles", "AvgTone", "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode", "Actor1Geo_ADM1Code",
             "Actor1Geo_ADM2Code", "Actor1Geo_Lat", "Actor1Geo_Long", "Actor1Geo_FeatureID", "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
             "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code", "Actor2Geo_Lat", "Actor2Geo_Long", "Actor2Geo_FeatureID", "ActionGeo_Type", "ActionGeo_FullName",
             "ActionGeo_CountryCode", "ActionGeo_ADM1Code", "ActionGeo_ADM2Code", "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID", "DATEADDED", "SOURCEURL"]


def gdelt(dizin, tam=False):
    c = db(f"{dizin}/gdelt_tr.sqlite", """CREATE TABLE IF NOT EXISTS olay (event_id TEXT PRIMARY KEY, tarih TEXT, aktor1 TEXT, aktor1_ulke TEXT, aktor2 TEXT, aktor2_ulke TEXT,
          olay_kodu TEXT, olay_kok TEXT, quad INTEGER, goldstein REAL, bahsedilme INTEGER, kaynak_sayisi INTEGER, makale INTEGER, ton REAL,
          yer TEXT, yer_ulke TEXT, adm1 TEXT, lat REAL, lon REAL, eklenme TEXT, kaynak_url TEXT);
        CREATE TABLE IF NOT EXISTS dosya (url TEXT PRIMARY KEY, satir INTEGER, cekim_zamani TEXT);""")
    ist = Istemci(0.3, zaman_asimi=120); bas = simdi(); n = 0
    simdi_ = datetime.now(timezone.utc).replace(second=0, microsecond=0); simdi_ -= timedelta(minutes=simdi_.minute % 15)
    for k in range(4 * 24, 0, -1):
        t = simdi_ - timedelta(minutes=15 * k); u = f"http://data.gdeltproject.org/gdeltv2/{t:%Y%m%d%H%M%S}.export.CSV.zip"
        if c.execute("SELECT 1 FROM dosya WHERE url = ?", (u,)).fetchone(): continue
        st, b = ist.get(u, ham=True, deneme=2)
        if st != 200 or not b: continue
        say = 0
        try:
            z = zipfile.ZipFile(io.BytesIO(b)); t_ = z.read(z.namelist()[0]).decode("utf-8", "replace")
        except Exception: continue
        for r in csv.reader(io.StringIO(t_), delimiter="\t"):
            if len(r) < len(GDELT_KOL): continue
            d = dict(zip(GDELT_KOL, r))
            if not ("TU" in (d["ActionGeo_CountryCode"], d["Actor1Geo_CountryCode"], d["Actor2Geo_CountryCode"]) or "TUR" in (d["Actor1CountryCode"], d["Actor2CountryCode"])): continue
            f = lambda x: float(x) if x not in ("", None) else None
            c.execute("INSERT OR REPLACE INTO olay VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (d["GLOBALEVENTID"], d["SQLDATE"], d["Actor1Name"],
                      d["Actor1CountryCode"], d["Actor2Name"], d["Actor2CountryCode"], d["EventCode"], d["EventRootCode"], int(d["QuadClass"] or 0),
                      f(d["GoldsteinScale"]), int(d["NumMentions"] or 0), int(d["NumSources"] or 0), int(d["NumArticles"] or 0), f(d["AvgTone"]),
                      d["ActionGeo_FullName"], d["ActionGeo_CountryCode"], d["ActionGeo_ADM1Code"], f(d["ActionGeo_Lat"]), f(d["ActionGeo_Long"]),
                      d["DATEADDED"], d["SOURCEURL"])); say += 1
        c.execute("INSERT OR REPLACE INTO dosya VALUES (?,?,?)", (u, say, simdi())); c.commit(); n += say
    kosu_yaz(c, "gdelt", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


def hisse_fiyat(dizin, tam=False):
    c = db(f"{dizin}/hisse_fiyat.sqlite", """CREATE TABLE IF NOT EXISTS fiyat (kod TEXT, tarih TEXT, kapanis REAL, en_dusuk REAL, en_yuksek REAL, hacim_tl REAL, hacim_lot REAL,
          piyasa_degeri REAL, ham TEXT, cekim_zamani TEXT, PRIMARY KEY (kod, tarih));
        CREATE TABLE IF NOT EXISTS kod (kod TEXT PRIMARY KEY, kaynak TEXT, guncellenme TEXT);""")
    ist = Istemci(0.6); bas = simdi(); n = 0
    st, t = ist.get("https://www.kap.org.tr/tr/bist-sirketler")
    for m in re.finditer(r'stockCode\\?":\\?"([A-Z0-9, ]+)\\?"', t or ""):
        for k in m.group(1).split(","):
            if k.strip(): c.execute("INSERT OR IGNORE INTO kod VALUES (?, 'kap', ?)", (k.strip(), simdi()))
    c.commit()
    for (kod,) in c.execute("SELECT kod FROM kod").fetchall():
        son = c.execute("SELECT max(tarih) FROM fiyat WHERE kod = ?", (kod,)).fetchone()[0]
        b = (datetime.fromisoformat(son) - timedelta(days=3)).date() if son else date(2015, 1, 1)
        j = ist.json("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil?" +
                     urllib.parse.urlencode({"hisse": kod, "startdate": b.strftime("%d-%m-%Y"), "enddate": date.today().strftime("%d-%m-%Y")}))
        for r in (j or {}).get("value") or []:
            tr = r.get("HGDG_TARIH") or ""
            try: tr = datetime.strptime(tr, "%d-%m-%Y").date().isoformat()
            except Exception: pass
            c.execute("INSERT OR REPLACE INTO fiyat VALUES (?,?,?,?,?,?,?,?,?,?)", (kod, tr, r.get("HGDG_KAPANIS"), r.get("HGDG_MIN"), r.get("HGDG_MAX"),
                      r.get("HGDG_HACIM"), r.get("HGDG_AOF"), r.get("PD"), json.dumps(r, ensure_ascii=False), simdi())); n += 1
        c.commit()
    kosu_yaz(c, "hisse_fiyat", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


def resmi_gazete(dizin, tam=False):
    c = db(f"{dizin}/resmi_gazete.sqlite", """CREATE TABLE IF NOT EXISTS madde (tarih TEXT, sira INTEGER, bolum TEXT, baslik TEXT, url TEXT, cekim_zamani TEXT,
        PRIMARY KEY (tarih, url));""")
    ist = Istemci(1.5); bas = simdi(); n = 0; not_ = None
    try:
        st, t = ist.get("https://www.resmigazete.gov.tr/")
        bolum = None; s = t or ""
        for i, m in enumerate(re.finditer(r'<div class="card-title[^"]*"[^>]*>(.*?)</div>|<a[^>]+href="([^"]+\.(?:htm|pdf))"[^>]*>(.*?)</a>', s, re.S)):
            if m.group(1): bolum = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip(); continue
            u = urllib.parse.urljoin("https://www.resmigazete.gov.tr/", m.group(2)); b = re.sub(r"<[^>]+>|\s+", " ", htmlmod.unescape(m.group(3))).strip()
            if not b or "eskiler" not in u: continue
            c.execute("INSERT OR IGNORE INTO madde VALUES (?,?,?,?,?,?)", (bugun(), i, bolum, b, u, simdi())); n += 1
    except BotEngeli as e:
        not_ = f"bot doğrulaması: {e}"
    c.commit(); kosu_yaz(c, "resmi_gazete", bas, n, ist.sayac, "tamam" if not not_ else "kismi", not_); return n, ist.sayac, not_


DOSYA_SAYFA = {"tobb_sirket": ["https://www.tobb.org.tr/BilgiErisimMudurlugu/Sayfalar/KurulanKapananSirketistatistikleri.php"],
               "kgm_trafik": ["https://www.kgm.gov.tr/Sayfalar/KGM/SiteTr/Trafik/TrafikHacimHaritasi.aspx"],
               "tbb_banka": ["https://www.tbb.org.tr/tr/bankacilik/banka-ve-sektor-bilgileri/istatistiki-raporlar/59"]}


def dosya_sayfa(dizin, tam=False):
    c = db(f"{dizin}/istatistik_dosya.sqlite", """CREATE TABLE IF NOT EXISTS dosya (url TEXT PRIMARY KEY, kaynak TEXT, baslik TEXT, uzanti TEXT, boyut INTEGER,
        ilk_gorulme TEXT, icerik BLOB);""")
    ist = Istemci(1.0, zaman_asimi=120); bas = simdi(); n = 0
    var = {r[0] for r in c.execute("SELECT url FROM dosya")}
    for ad, sayfalar in DOSYA_SAYFA.items():
        for s in sayfalar:
            st, t = ist.get(s)
            for u, b in re.findall(r'href="([^"]+\.(?:xlsx|xls|csv|zip|pdf)[^"]*)"[^>]*>(.*?)</a>', t or "", re.S | re.I):
                u = urllib.parse.urljoin(s, htmlmod.unescape(u))
                if u in var: continue
                st2, icerik = ist.get(urllib.parse.quote(u, safe=":/%?=&"), ham=True, deneme=2)
                if st2 != 200 or not icerik or len(icerik) > 80e6: continue
                c.execute("INSERT OR IGNORE INTO dosya VALUES (?,?,?,?,?,?,?)", (u, ad, re.sub(r"<[^>]+>|\s+", " ", htmlmod.unescape(b)).strip()[:300],
                          u.split("?")[0].rsplit(".", 1)[-1].lower(), len(icerik), simdi(), icerik)); n += 1; var.add(u); c.commit()
    kosu_yaz(c, "dosya_sayfa", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


# 15 Eki 2026: bkm.com.tr "Dönemsel Bilgiler" menüsündeki güncel sayfalar (eski listedeki bazı adresler 404 veriyordu).
# BKM yurt dışı IP'lerine (GitHub makineleri) 200 kodlu sahte "Bulunamadı" sayfası döner → bu kaynak Mac'te (TR IP) çalışır:
# GEOPROP_CONSOLIDATION/run/bkm_haftalik/calistir.sh (launchd com.geoprop.bkm-haftalik).
BKM_SAYFA = [
    "secilen-aya-ait-istatistikler", "secilen-aya-ait-sektorel-gelisim", "secilen-sektore-gore-aylik-gelisim",
    "kart-sayilari", "temassiz-kart-sayilari", "pos-atm-okc-yazilim-sayilari", "temassiz-pos-okc-yazilim-sayilari",
    "yerli-kredi-kartlarinin-yurt-ici-kullanimi", "yerli-kredi-kartlarinin-yurtdisi-kullanimi", "yerli-kredi-kartlarinin-yurt-ici-ve-yurtdisi-kullanimi",
    "yerli-banka-kartlarinin-yurt-ici-kullanimi", "yerli-banka-kartlarinin-yurtdisi-kullanimi", "yerli-banka-kartlarinin-yurtici-ve-yurtdisi-kullanimi",
    "yabanci-kredi-kartlarinin-yurt-ici-kullanimi", "yabanci-banka-kartlarinin-yurt-ici-kullanimi",
    "yerli-ve-yabanci-kredi-kartlarinin-yurt-ici-kullanimi", "yerli-ve-yabanci-banka-kartlarinin-yurt-ici-kullanimi",
    "yerli-kartlarin-yurt-ici-ve-yurt-disi-bireyselticari-kullanimi", "yerli-kartlarin-yurt-ici-ve-yurt-disi-taksitli-kullanimi",
    "yerli-kartlarin-yurt-ici-ve-yurt-disi-temassiz-kullanimi", "yerli-kartlarin-yurt-ici-ve-yurt-disi-temassiz-kullaniminin-sektorel-dagilimi",
    "yerli-on-odemeli-kartlarin-yurt-ici-ve-yurt-disi-kullanimi",
    "mektupla-telefonla-siparis-ve-e-ticaret-islemleri", "internetten-yapilan-kartli-odeme-islemleri",
    "internetten-yapilan-kartli-odeme-islemlerinin-sektorel-dagilimi", "internetten-yapilan-kartli-odemelerin-secilen-aya-ait-sektorel-gelisimi",
    "mobil-temassiz-karekodla-yapilan-odeme-islemleri",
]
# Ay seçimli sayfalar (filter_year / filter_month parametresi alır)
BKM_AYLIK = {"secilen-aya-ait-istatistikler", "secilen-aya-ait-sektorel-gelisim",
             "internetten-yapilan-kartli-odemelerin-secilen-aya-ait-sektorel-gelisimi",
             "yerli-kartlarin-yurt-ici-ve-yurt-disi-temassiz-kullaniminin-sektorel-dagilimi"}


def bkm_tablo(dizin, tam=False):
    c = db(f"{dizin}/bkm.sqlite", """CREATE TABLE IF NOT EXISTS hucre (sayfa TEXT, parametre TEXT, tablo INTEGER, satir INTEGER, sutun INTEGER, deger TEXT, cekim_gunu TEXT,
        PRIMARY KEY (sayfa, parametre, tablo, satir, sutun, cekim_gunu));""")
    ist = Istemci(1.5); bas = simdi(); n = 0; sahte = 0; sayfa_ok = 0
    bu = date.today(); aylar = [(bu.year, bu.month)]
    for _ in range(24 if tam else 3):
        y, a = aylar[-1]; aylar.append((y - 1, 12) if a == 1 else (y, a - 1))
    for s in BKM_SAYFA:
        parametreler = [""] + ([f"?filter_year={y}&filter_month={a}" for y, a in aylar] if s in BKM_AYLIK else [])
        for p in parametreler:
            st, t = ist.get(f"https://bkm.com.tr/{s}/{p}")
            if st != 200 or not t: continue
            if "<title>Bulunamadı</title>" in t:      # IP engeli: 200 kodlu sahte sayfa
                sahte += 1
                if sahte >= 3 and sayfa_ok == 0:
                    kosu_yaz(c, "bkm_tablo", bas, 0, ist.sayac, "engel", "BKM bu IP'ye sahte 'Bulunamadı' sayfası veriyor (yurt dışı IP) — Mac'te çalıştırın")
                    return 0, ist.sayac, "IP engeli (yurt dışı)"
                continue
            sayfa_ok += 1
            for ti, tb in enumerate(re.findall(r"<table.*?</table>", t, re.S | re.I)):
                for ri, tr in enumerate(re.findall(r"<tr.*?</tr>", tb, re.S | re.I)):
                    for ci, td in enumerate(re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)):
                        v = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", td))).strip()
                        if v:
                            c.execute("INSERT OR REPLACE INTO hucre VALUES (?,?,?,?,?,?,?)", (s, p, ti, ri, ci, v, bugun())); n += 1
            c.commit()
    kosu_yaz(c, "bkm_tablo", bas, n, ist.sayac, "tamam", f"{sayfa_ok} sayfa")
    return n, ist.sayac, f"{sayfa_ok} sayfa"
