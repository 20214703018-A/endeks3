"""
Zaman serisi kaynakları. Her fonksiyon: topla(dizin, tam=False) → (satır, istek, not). Kaynak başına bir sqlite: <dizin>/<ad>.sqlite.
Sıklıklar iş akışında (zaman_serisi.yml) tanımlı; 'tam' = haftalık/aylık derin tarama.
"""
import html as htmlmod, json, os, re, urllib.parse
from datetime import date, timedelta
import xml.etree.ElementTree as ET
SINIR = int(os.environ.get("ZS_DENEME_SINIRI", "0"))   # >0: deneme koşusu (döngüler kısaltılır)
from ortak import Istemci, BotEngeli, db, kosu_yaz, simdi, bugun, next_payload, nesneler

ILLER = ["Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Amasya", "Ankara", "Antalya", "Artvin", "Aydın", "Balıkesir", "Bilecik", "Bingöl", "Bitlis",
         "Bolu", "Burdur", "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır", "Edirne", "Elazığ", "Erzincan", "Erzurum", "Eskişehir",
         "Gaziantep", "Giresun", "Gümüşhane", "Hakkari", "Hatay", "Isparta", "Mersin", "İstanbul", "İzmir", "Kars", "Kastamonu", "Kayseri",
         "Kırklareli", "Kırşehir", "Kocaeli", "Konya", "Kütahya", "Malatya", "Manisa", "Kahramanmaraş", "Mardin", "Muğla", "Muş", "Nevşehir",
         "Niğde", "Ordu", "Rize", "Sakarya", "Samsun", "Siirt", "Sinop", "Sivas", "Tekirdağ", "Tokat", "Trabzon", "Tunceli", "Şanlıurfa", "Uşak",
         "Van", "Yozgat", "Zonguldak", "Aksaray", "Bayburt", "Karaman", "Kırıkkale", "Batman", "Şırnak", "Bartın", "Ardahan", "Iğdır", "Yalova",
         "Karabük", "Kilis", "Osmaniye", "Düzce"]   # plaka sırası (1–81)


# ---------- 1) TCMB döviz kurları (günlük; ilk koşuda 2015'ten geriye doldurma) ----------
def tcmb_kur(dizin, tam=False):
    c = db(f"{dizin}/tcmb_kur.sqlite", """CREATE TABLE IF NOT EXISTS kur (tarih TEXT, kod TEXT, ad TEXT, birim INTEGER, doviz_alis REAL, doviz_satis REAL,
        efektif_alis REAL, efektif_satis REAL, cekim_zamani TEXT, PRIMARY KEY (tarih, kod));""")
    ist = Istemci(0.3); bas = simdi(); n = 0
    son = c.execute("SELECT max(tarih) FROM kur").fetchone()[0]
    d = date.fromisoformat(son) + timedelta(days=1) if son else date(2015, 1, 1)
    gunler = [d + timedelta(days=i) for i in range((date.today() - d).days + 1)]
    f = lambda e, k: (float(e.findtext(k)) if (e.findtext(k) or "").strip() else None)
    if SINIR: gunler = gunler[-SINIR:]
    for g in gunler:
        if g.weekday() >= 5: continue
        url = "https://www.tcmb.gov.tr/kurlar/today.xml" if g == date.today() else f"https://www.tcmb.gov.tr/kurlar/{g:%Y%m}/{g:%d%m%Y}.xml"
        st, t = ist.get(url)
        if st != 200 or not t or "<Tarih_Date" not in t: continue   # tatil günü / yayımlanmamış
        r = ET.fromstring(t.encode()); tarih = r.get("Date")
        try: tarih = f"{tarih[6:10]}-{tarih[0:2]}-{tarih[3:5]}"   # MM/DD/YYYY
        except Exception: tarih = g.isoformat()
        for e in r.findall("Currency"):
            c.execute("INSERT OR REPLACE INTO kur VALUES (?,?,?,?,?,?,?,?,?)", (tarih, e.get("CurrencyCode"), e.findtext("Isim"), int(e.findtext("Unit") or 1),
                      f(e, "ForexBuying"), f(e, "ForexSelling"), f(e, "BanknoteBuying"), f(e, "BanknoteSelling"), simdi())); n += 1
        if n % 500 == 0: c.commit()
    kosu_yaz(c, "tcmb_kur", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


# ---------- 2) resmî tatiller (haftalık) ----------
def resmi_tatil(dizin, tam=False):
    c = db(f"{dizin}/resmi_tatil.sqlite", """CREATE TABLE IF NOT EXISTS tatil (tarih TEXT, ad TEXT, yerel_ad TEXT, turler TEXT, ilk_gorulme TEXT, son_gorulme TEXT,
        PRIMARY KEY (tarih, ad));""")
    ist = Istemci(0.5); bas = simdi(); n = 0
    for y in range(date.today().year - (8 if tam else 1), date.today().year + 3):
        for h in ist.json(f"https://date.nager.at/api/v3/PublicHolidays/{y}/TR") or []:
            c.execute("""INSERT INTO tatil VALUES (?,?,?,?,?,?) ON CONFLICT (tarih, ad) DO UPDATE SET son_gorulme = excluded.son_gorulme""",
                      (h["date"], h.get("name"), h.get("localName"), json.dumps(h.get("types")), simdi(), simdi())); n += 1
    kosu_yaz(c, "resmi_tatil", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


# ---------- 3) hava tahmini (günlük; her çekim ayrı 'tahmin sürümü' — tahminin kendisi de zaman serisi) ----------
def hava_tahmin(dizin, tam=False):
    c = db(f"{dizin}/hava_tahmin.sqlite", """CREATE TABLE IF NOT EXISTS il_merkez (il TEXT PRIMARY KEY, plaka INTEGER, lat REAL, lon REAL, kaynak TEXT);
        CREATE TABLE IF NOT EXISTS tahmin (il TEXT, hedef_tarih TEXT, cekim_gunu TEXT, tmax REAL, tmin REAL, his_tmax REAL, yagis_mm REAL,
          yagis_olasilik REAL, ruzgar_max REAL, hava_kodu INTEGER, gunes_sa REAL, uv_max REAL, cekim_zamani TEXT, PRIMARY KEY (il, hedef_tarih, cekim_gunu));""")
    ist = Istemci(0.4); bas = simdi(); n = 0
    var = {r[0] for r in c.execute("SELECT il FROM il_merkez")}
    for i, il in enumerate(ILLER, 1):
        if il in var: continue
        j = ist.json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": il, "countryCode": "TR", "count": 5, "language": "tr"}))
        r = next((x for x in (j or {}).get("results", []) if x.get("feature_code", "").startswith("PPLA")), None) or ((j or {}).get("results") or [None])[0]
        if r: c.execute("INSERT OR REPLACE INTO il_merkez VALUES (?,?,?,?,?)", (il, i, r["latitude"], r["longitude"], "open-meteo geocoding"))
    c.commit()
    D = "temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,weather_code,sunshine_duration,uv_index_max"
    for il, lat, lon in c.execute("SELECT il, lat, lon FROM il_merkez").fetchall():
        j = ist.json(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily={D}&timezone=Europe%2FIstanbul&forecast_days=16")
        if not j or "daily" not in j: continue
        dd = j["daily"]
        for k, t in enumerate(dd["time"]):
            g = lambda a: dd.get(a, [None] * 99)[k]
            c.execute("INSERT OR REPLACE INTO tahmin VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (il, t, bugun(), g("temperature_2m_max"), g("temperature_2m_min"),
                      g("apparent_temperature_max"), g("precipitation_sum"), g("precipitation_probability_max"), g("wind_speed_10m_max"), g("weather_code"),
                      (g("sunshine_duration") or 0) / 3600 if g("sunshine_duration") is not None else None, g("uv_index_max"), simdi())); n += 1
    kosu_yaz(c, "hava_tahmin", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


# ---------- 4) Biletix etkinlikleri (günlük anlık görüntü: önümüzdeki 365 gün) ----------
def biletix(dizin, tam=False):
    c = db(f"{dizin}/etkinlik_biletix.sqlite", """CREATE TABLE IF NOT EXISTS etkinlik (etkinlik_id TEXT, cekim_gunu TEXT, ad TEXT, tur TEXT, mekan_kodu TEXT, mekan TEXT,
        sehir TEXT, baslangic TEXT, bitis TEXT, durum TEXT, kategori TEXT, sanatci TEXT, grup TEXT, ham_json TEXT, cekim_zamani TEXT, PRIMARY KEY (etkinlik_id, cekim_gunu));""")
    ist = Istemci(0.7); bas = simdi(); n = 0; bas_ = 0; toplam = None
    il = lambda v: (v[0] if isinstance(v, list) and v else v)
    while toplam is None or bas_ < toplam:
        q = urllib.parse.urlencode({"start": bas_, "rows": 500, "q": "*:*", "fq": "start:[NOW TO NOW+365DAY]", "sort": "start asc", "wt": "json"})
        j = ist.json(f"https://www.biletix.com/solr/tr/select/?{q}")
        if not j: break
        toplam = j["response"]["numFound"]; docs = j["response"]["docs"]
        if not docs: break
        for d in docs:
            kat = d.get("category") or d.get("maincategory") or d.get("subcategory")
            d2 = {k: v for k, v in d.items() if k not in ("description",)}
            c.execute("INSERT OR REPLACE INTO etkinlik VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (d.get("id"), bugun(), d.get("sname") or il(d.get("name")),
                      d.get("type"), d.get("venuecode"), d.get("svenue") or il(d.get("venue")), il(d.get("city")), d.get("start"), d.get("end"), d.get("status"),
                      json.dumps(kat, ensure_ascii=False) if kat else None, json.dumps(d.get("artist"), ensure_ascii=False), il(d.get("group")),
                      json.dumps(d2, ensure_ascii=False), simdi())); n += 1
        bas_ += len(docs); c.commit()
    kosu_yaz(c, "biletix", bas, n, ist.sayac, "tamam", f"numFound={toplam}"); return n, ist.sayac, None


# ---------- 5) Şikayetvar marka istatistikleri (günlük: en çok şikâyet alan 2.400 marka; haftalık: l1y ≥ 5 olanların hepsi) ----------
def sikayetvar(dizin, tam=False):
    c = db(f"{dizin}/sikayetvar.sqlite", """CREATE TABLE IF NOT EXISTS marka (marka_id INTEGER, cekim_gunu TEXT, ad TEXT, gorunen_ad TEXT, slug TEXT, uyelik TEXT,
        l1y_sikayet INTEGER, l1y_cozulen INTEGER, l1y_cozum_orani REAL, l1y_yanit_orani REAL, l1y_yanit_suresi_sn INTEGER, l1y_memnuniyet REAL,
        l1y_yildiz REAL, l1y_anket INTEGER, l1y_tekil_goruntulenme INTEGER, l1y_goruntulenme INTEGER, tum_zaman_sikayet INTEGER, kategoriler TEXT,
        sayfa INTEGER, sira INTEGER, cekim_zamani TEXT, PRIMARY KEY (marka_id, cekim_gunu));""")
    ist = Istemci(1.2); bas = simdi(); n = 0; sayfa = 1; sinir = SINIR or (2000 if tam else 150); not_ = None
    try:
        while sayfa <= sinir:
            st, t = ist.get(f"https://www.sikayetvar.com/all-brands?page={sayfa}")
            if st != 200 or not t: not_ = f"sayfa {sayfa}: http {st}"; break
            firmalar = [o for o in nesneler(next_payload(t), "company") if "l1yStats" in o]
            if not firmalar: break
            for k, o in enumerate(firmalar, 1):
                s = o.get("l1yStats") or {}
                c.execute("INSERT OR REPLACE INTO marka VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (o.get("id"), bugun(), o.get("name"),
                          o.get("displayName"), o.get("url"), o.get("subscriptionType"), s.get("complaintCount"), s.get("resolvedCount"), s.get("resolveRatio"),
                          s.get("answerRatio"), s.get("answerTime"), s.get("satisfactionRatio"), s.get("starRatio"), s.get("surveyCount"),
                          s.get("uniqueViewCount"), s.get("viewCount"), o.get("allTimeComplaintCount"),
                          json.dumps([x.get("name") for x in o.get("fixedCategories") or []], ensure_ascii=False), sayfa, k, simdi())); n += 1
            c.commit()
            if min((o.get("l1yStats") or {}).get("complaintCount") or 0 for o in firmalar) < 5: break
            sayfa += 1
    except BotEngeli as e:
        not_ = f"bot doğrulaması: {e} — durduruldu (atlatılmaz)"
    kosu_yaz(c, "sikayetvar", bas, n, ist.sayac, "tamam" if not not_ else "kismi", not_); return n, ist.sayac, not_


# ---------- 6) iş ilanı sayıları (Secretcv il × sektör; haftalık) ----------
def is_ilani(dizin, tam=False):
    c = db(f"{dizin}/is_ilani.sqlite", """CREATE TABLE IF NOT EXISTS ilan_sayisi (url TEXT, cekim_gunu TEXT, il_slug TEXT, sektor_slug TEXT, ilan_sayisi INTEGER,
        cekim_zamani TEXT, PRIMARY KEY (url, cekim_gunu));""")
    ist = Istemci(1.0); bas = simdi(); n = 0; not_ = None
    st, t = ist.get("https://www.secretcv.com/sitemap/tum-sehir-sektor")
    urls = re.findall(r"<loc>([^<]+)</loc>", t or "")
    if SINIR: urls = urls[:SINIR]
    try:
        for u in urls:
            u = htmlmod.unescape(u)
            st, s = ist.get(u)
            if st not in (200, 410): continue          # 410 = o il × sektörde şu an ilan yok (sayfa kaldırılmış) → 0 kaydedilir
            m = re.search(r"(\d[\d.]*)\s*İş İlanı Listelendi", re.sub(r"<[^>]+>", " ", s or ""))
            il_ = re.search(r"/is-ilanlari/([a-z0-9-]+?)-is-ilanlari", u); sek = re.search(r"[?&]s=([a-z0-9-]+)", u)
            c.execute("INSERT OR REPLACE INTO ilan_sayisi VALUES (?,?,?,?,?,?)", (u, bugun(), il_.group(1) if il_ else None, sek.group(1) if sek else None,
                      int(m.group(1).replace(".", "")) if m else 0, simdi())); n += 1
            if n % 100 == 0: c.commit()
    except BotEngeli as e:
        not_ = f"bot doğrulaması: {e}"
    kosu_yaz(c, "is_ilani", bas, n, ist.sayac, "tamam" if not not_ else "kismi", not_); return n, ist.sayac, not_


# ---------- 7) borsa şirketi mali tabloları (İş Yatırım; haftalık; son 8 çeyrek) ----------
def mali_tablo(dizin, tam=False):
    c = db(f"{dizin}/mali_tablo.sqlite", """CREATE TABLE IF NOT EXISTS sirket (borsa_kodu TEXT PRIMARY KEY, unvan TEXT, il TEXT, kaynak TEXT, guncellenme TEXT);
        CREATE TABLE IF NOT EXISTS kalem (borsa_kodu TEXT, donem TEXT, mali_grup TEXT, kalem_kodu TEXT, kalem_tr TEXT, kalem_en TEXT, deger REAL,
          ilk_gorulme TEXT, son_gorulme TEXT, PRIMARY KEY (borsa_kodu, donem, mali_grup, kalem_kodu));""")
    ist = Istemci(0.8); bas = simdi(); n = 0
    st, t = ist.get("https://www.kap.org.tr/tr/bist-sirketler")
    kodlar = {}
    for m in re.finditer(r'stockCode\\?":\\?"([A-Z0-9, ]+)\\?"', t or ""):
        for k in m.group(1).split(","): kodlar[k.strip()] = None
    for k in kodlar: c.execute("INSERT OR IGNORE INTO sirket (borsa_kodu, kaynak, guncellenme) VALUES (?, 'kap bist-sirketler', ?)", (k, simdi()))
    c.commit()
    kodlar = [r[0] for r in c.execute("SELECT borsa_kodu FROM sirket WHERE borsa_kodu <> ''")]
    if SINIR: kodlar = kodlar[:SINIR]
    bu = date.today(); ceyrek = []
    y, a = bu.year, (bu.month - 1) // 3 * 3
    for _ in range(8):
        if a == 0: y, a = y - 1, 12
        ceyrek.append((y, a)); a -= 3
    for kod in kodlar:
        for grup in ("XI_29", "UFRS"):
            bulundu = False
            for parca in (ceyrek[:4], ceyrek[4:]):
                q = {"companyCode": kod, "exchange": "TRY", "financialGroup": grup}
                for i, (yy, aa) in enumerate(parca, 1): q[f"year{i}"] = yy; q[f"period{i}"] = aa
                j = ist.json("https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo?" + urllib.parse.urlencode(q))
                for r in (j or {}).get("value") or []:
                    for i, (yy, aa) in enumerate(parca, 1):
                        v = r.get(f"value{i}")
                        if v in (None, ""): continue
                        bulundu = True
                        c.execute("""INSERT INTO kalem VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET deger = excluded.deger, son_gorulme = excluded.son_gorulme""",
                                  (kod, f"{yy}/{aa:02d}", grup, r.get("itemCode"), (r.get("itemDescTr") or "").strip(), (r.get("itemDescEng") or "").strip(),
                                   float(v), simdi(), simdi())); n += 1
            if bulundu: break
        c.commit()
    kosu_yaz(c, "mali_tablo", bas, n, ist.sayac, "tamam", f"{len(kodlar)} şirket"); return n, ist.sayac, None


# ---------- 8) DHMİ havalimanı istatistik dosyaları (aylık; ham dosya saklanır) ----------
def dhmi(dizin, tam=False):
    c = db(f"{dizin}/dhmi.sqlite", """CREATE TABLE IF NOT EXISTS dosya (url TEXT PRIMARY KEY, ad TEXT, klasor INTEGER, uzanti TEXT, boyut INTEGER, ilk_gorulme TEXT,
        icerik BLOB);""")
    ist = Istemci(1.0); bas = simdi(); n = 0
    st, t = ist.get("https://www.dhmi.gov.tr/Sayfalar/Istatistikler.aspx")
    linkler = sorted({htmlmod.unescape(u) for u in re.findall(r'href="(https://www\.dhmi\.gov\.tr/Lists/Istatislikler/Attachments/[^"]+)"', t or "")})
    var = {r[0] for r in c.execute("SELECT url FROM dosya")}
    if SINIR: linkler = linkler[:SINIR]
    for u in linkler:
        if u in var: continue
        st, b = ist.get(urllib.parse.quote(u, safe=":/%"), ham=True)
        if st != 200 or not b: continue
        k = re.search(r"/Attachments/(\d+)/", u)
        c.execute("INSERT OR IGNORE INTO dosya VALUES (?,?,?,?,?,?,?)", (u, urllib.parse.unquote(u.rsplit("/", 1)[1]), int(k.group(1)) if k else None,
                  u.rsplit(".", 1)[-1].lower(), len(b), simdi(), b)); n += 1; c.commit()
    kosu_yaz(c, "dhmi", bas, n, ist.sayac, "tamam", f"{len(linkler)} bağlantı"); return n, ist.sayac, None


# ---------- 9) TFF Süper Lig fikstürü (haftalık) ----------
def tff_fikstur(dizin, tam=False):
    c = db(f"{dizin}/tff_fikstur.sqlite", """CREATE TABLE IF NOT EXISTS mac (lig TEXT, tarih TEXT, saat TEXT, ev TEXT, deplasman TEXT, skor TEXT, ilk_gorulme TEXT,
        son_gorulme TEXT, PRIMARY KEY (lig, tarih, ev, deplasman));""")
    ist = Istemci(1.0); bas = simdi(); n = 0
    st, b = ist.get("https://www.tff.org/default.aspx?pageID=198", ham=True)
    s = (b or b"").decode("windows-1254", "replace")
    tok = [x.strip() for x in re.sub(r"<[^>]+>", "|", s).split("|")]
    tok = [x for x in (htmlmod.unescape(x).replace("\xa0", " ").strip() for x in tok) if x and x not in ("Detaylar",)]
    for i, x in enumerate(tok):
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", x) and i >= 1 and i + 2 < len(tok):
            saat = tok[i + 1] if re.fullmatch(r"\d{2}:\d{2}", tok[i + 1]) else None
            j = i + (2 if saat else 1)
            skor = tok[j] if re.fullmatch(r"\d+\s*-\s*\d+", tok[j]) else None
            dep = tok[j + 1] if skor else tok[j]
            ev = tok[i - 1]
            if ev.isdigit() or not re.search(r"[A-ZÇĞİÖŞÜ]{3}", ev): continue
            g = f"{x[6:10]}-{x[3:5]}-{x[0:2]}"
            c.execute("""INSERT INTO mac VALUES ('super_lig',?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET saat = excluded.saat, skor = excluded.skor,
                         son_gorulme = excluded.son_gorulme""", (g, saat, ev, dep, skor, simdi(), simdi())); n += 1
    kosu_yaz(c, "tff_fikstur", bas, n, ist.sayac, "tamam"); return n, ist.sayac, None


from turizm import eurostat_turizm, dunya_bankasi, yabanci_kur, ktb_turizm, rusya_turizm
from opensky import opensky
from ucus_ekrani import ucus_ekrani
from ek_kaynaklar import afad_deprem, ispark, ibb_acik_veri, appstore, tranco, gdelt, hisse_fiyat, resmi_gazete, dosya_sayfa, bkm_tablo
from fiyat_kaynaklari import akaryakit_fiyat, hal_fiyat, hal_hks
from altyapi_kaynaklari import sarj_istasyon
KAYNAKLAR = {"ucus_ekrani": ucus_ekrani, "afad_deprem": afad_deprem, "ispark": ispark, "ibb_acik_veri": ibb_acik_veri, "appstore": appstore, "tranco": tranco, "gdelt": gdelt,
             "hisse_fiyat": hisse_fiyat, "resmi_gazete": resmi_gazete, "dosya_sayfa": dosya_sayfa, "bkm_tablo": bkm_tablo, "opensky": opensky, "eurostat_turizm": eurostat_turizm, "dunya_bankasi": dunya_bankasi, "yabanci_kur": yabanci_kur, "ktb_turizm": ktb_turizm,
             "rusya_turizm": rusya_turizm, "tcmb_kur": tcmb_kur, "resmi_tatil": resmi_tatil, "hava_tahmin": hava_tahmin, "biletix": biletix, "sikayetvar": sikayetvar,
             "is_ilani": is_ilani, "mali_tablo": mali_tablo, "dhmi": dhmi, "tff_fikstur": tff_fikstur,
             "akaryakit_fiyat": akaryakit_fiyat, "hal_fiyat": hal_fiyat, "hal_hks": hal_hks, "sarj_istasyon": sarj_istasyon}
DOSYA = {"ucus_ekrani": "ucus_ekrani", "afad_deprem": "afad_deprem", "ispark": "ispark", "ibb_acik_veri": "ibb_acik_veri", "appstore": "appstore", "tranco": "tranco", "gdelt": "gdelt_tr",
         "hisse_fiyat": "hisse_fiyat", "resmi_gazete": "resmi_gazete", "dosya_sayfa": "istatistik_dosya", "bkm_tablo": "bkm", "opensky": "opensky_ucus", "eurostat_turizm": "eurostat_turizm", "dunya_bankasi": "dunya_bankasi_turizm", "yabanci_kur": "yabanci_kur", "ktb_turizm": "ktb_turizm",
         "rusya_turizm": "rusya_turizm", "tcmb_kur": "tcmb_kur", "resmi_tatil": "resmi_tatil", "hava_tahmin": "hava_tahmin", "biletix": "etkinlik_biletix", "sikayetvar": "sikayetvar",
         "is_ilani": "is_ilani", "mali_tablo": "mali_tablo", "dhmi": "dhmi", "tff_fikstur": "tff_fikstur",
         "akaryakit_fiyat": "akaryakit_fiyat", "hal_fiyat": "hal_fiyat", "hal_hks": "hal_hks", "sarj_istasyon": "sarj_istasyon"}
