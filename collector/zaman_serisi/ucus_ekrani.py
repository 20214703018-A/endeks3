"""
Havalimanı uçuş bilgi ekranları (12 Ekim 2026; kullanıcı: "bir havayolu ek uçuş eklerse tespit edebilir miyiz").
  İstanbul Havalimanı (IST): umbraco/api/FlightInfo/GetFlightStatusBoard (bugün + yarın; geliş/gidiş × iç/dış hat; sayfa başına 50, 'moreFlight' ile sayfalama)
  Sabiha Gökçen (SAW): uçuş bilgi ekranı sayfası (sunucuda oluşturulmuş tablo; uçuş kimliği fid ile)
Her 6 saatte bir anlık görüntü → ucus_gozlem (uçuş × çekim). Ardışık görüntüler karşılaştırılarak: yeni_sefer (önceki görüntüde yoktu ve kalkış/varışa
≥ 6 saat var), iptal (durum İPTAL), saat değişikliği → ucus_degisim tablosu.
Antalya (AYT) bot doğrulaması (F5 TSPD) kullanıyor → atlatılmaz, toplanmaz.
"""
import html as htmlmod, json, re, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from ortak import Istemci, db, kosu_yaz, simdi, UA

TR = timezone(timedelta(hours=3))


def _kaydet(c, cekim, satirlar):
    for r in satirlar:
        c.execute("INSERT OR REPLACE INTO ucus_gozlem VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (r["havalimani"], r["anahtar"], cekim, r["yon"], r["hat"],
                  r["ucus_no"], r["havayolu"], r.get("havayolu_kodu"), r.get("karsi_kod"), r.get("karsi_sehir"), r.get("planlanan"), r.get("tahmini"),
                  r.get("kapi"), r.get("durum"), json.dumps(r.get("kod_paylasim") or [], ensure_ascii=False), r.get("tarih"), json.dumps(r.get("ham"), ensure_ascii=False)))


def ucus_ekrani(dizin, tam=False):
    c = db(f"{dizin}/ucus_ekrani.sqlite", """CREATE TABLE IF NOT EXISTS ucus_gozlem (havalimani TEXT, anahtar TEXT, cekim_zamani TEXT, yon TEXT, hat TEXT, ucus_no TEXT, havayolu TEXT,
          havayolu_kodu TEXT, karsi_kod TEXT, karsi_sehir TEXT, planlanan TEXT, tahmini TEXT, kapi TEXT, durum TEXT, kod_paylasim TEXT, tarih TEXT, ham TEXT,
          PRIMARY KEY (havalimani, anahtar, cekim_zamani));
        CREATE TABLE IF NOT EXISTS ucus_degisim (havalimani TEXT, anahtar TEXT, tespit_zamani TEXT, degisim TEXT, ucus_no TEXT, havayolu TEXT, yon TEXT, hat TEXT,
          karsi_sehir TEXT, planlanan TEXT, onceki TEXT, simdiki TEXT, PRIMARY KEY (havalimani, anahtar, degisim, tespit_zamani));""")
    ist = Istemci(1.0); bas = simdi(); cekim = simdi(); satirlar = []; notlar = []
    # --- IST ---
    bugun = datetime.now(TR).date()
    for nat, yon in ((0, "gelis"), (1, "gidis")):
        for intl, hat in ((0, "ic"), (1, "dis")):
            for gun in (bugun, bugun + timedelta(days=1)):
                tarih = f"{gun}T00:00:00"; btn = ""; son_ = None
                for _ in range(60):
                    v = urllib.parse.urlencode({"nature": nat, "searchTerm": "changeflight", "pageSize": 50, "isInternational": intl, "date": tarih,
                                                "endDate": f"{gun}T23:59:59", "culture": "tr", "clickedButton": btn}).encode()
                    try:
                        r = urllib.request.urlopen(urllib.request.Request("https://www.istairport.com/umbraco/api/FlightInfo/GetFlightStatusBoard", data=v,
                                                   headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.istairport.com/"}), timeout=40)
                        d = json.loads(r.read()); ist.sayac += 1
                    except Exception as e:
                        notlar.append(f"IST {yon}/{hat}: {type(e).__name__}"); break
                    f = ((d.get("result") or {}).get("data") or {}).get("flights") or [] if d.get("status") else []
                    f = [x for x in f if (x.get("scheduledDatetime") or "")[:10] == str(gun)]
                    if not f: break
                    for x in f:
                        satirlar.append({"havalimani": "IST", "anahtar": str(x.get("id")), "yon": yon, "hat": hat, "ucus_no": x.get("flightNumber"),
                                         "havayolu": x.get("airlineName"), "havayolu_kodu": x.get("airlineCode"),
                                         "karsi_kod": x.get("fromCityCode") if nat == 0 else x.get("toCityCode"),
                                         "karsi_sehir": x.get("fromCityName") if nat == 0 else x.get("toCityName"), "planlanan": x.get("scheduledDatetime"),
                                         "tahmini": x.get("estimatedDatetime"), "kapi": x.get("gate"), "durum": x.get("remark") or x.get("airsideRemark"),
                                         "kod_paylasim": x.get("codeshare"), "tarih": str(gun), "ham": x})
                    yeni_son = f[-1]["scheduledDatetime"]
                    if yeni_son == son_: break
                    son_ = yeni_son; tarih = yeni_son; btn = "moreFlight"
    # --- SAW ---
    for sayfa, yon in (("ucus-bilgi-ekrani", None),):
        st, t = ist.get("https://www.sabihagokcen.aero/yolcu-ve-ziyaretciler/yolcu-rehberi/" + sayfa)
        for blok in re.findall(r'<div class="fids-tr">(.*?)</div>\s*(?=<div class="fids-tr">|</div>)', t or "", re.S):
            m = re.search(r'ucus-ayrintilari\.fid\((\d+)\)\.d\((\d)\)\.i\((\d)\)', blok)
            if not m: continue
            al = lambda k: (lambda x: htmlmod.unescape(x.group(1)).strip() if x else None)(re.search(r'<span class="' + k + r'[^"]*">(.*?)</span>', blok, re.S))
            satirlar.append({"havalimani": "SAW", "anahtar": m.group(1), "yon": "gidis" if m.group(2) == "1" else "gelis", "hat": "dis" if m.group(3) == "1" else "ic",
                             "ucus_no": al("flight"), "havayolu": al("airlinename"), "karsi_sehir": al("city"), "planlanan": al("scheduled"),
                             "tahmini": al("estimated"), "kapi": al("gate"), "durum": re.sub(r"<[^>]+>", "", al("remark") or ""), "tarih": str(bugun),
                             "ham": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", blok)).strip()[:500]})
    _kaydet(c, cekim, satirlar); c.commit()
    # --- değişim tespiti: bu görüntü ↔ aynı havalimanının bir önceki görüntüsü ---
    for ap in ("IST", "SAW"):
        once = c.execute("SELECT max(cekim_zamani) FROM ucus_gozlem WHERE havalimani = ? AND cekim_zamani < ?", (ap, cekim)).fetchone()[0]
        if not once: continue
        c.execute("""INSERT OR IGNORE INTO ucus_degisim SELECT y.havalimani, y.anahtar, ?, 'yeni_sefer', y.ucus_no, y.havayolu, y.yon, y.hat, y.karsi_sehir, y.planlanan,
                     NULL, y.durum FROM ucus_gozlem y WHERE y.havalimani = ? AND y.cekim_zamani = ?
                     AND NOT EXISTS (SELECT 1 FROM ucus_gozlem o WHERE o.havalimani = y.havalimani AND o.anahtar = y.anahtar AND o.cekim_zamani = ?)
                     AND (y.havalimani <> 'IST' OR y.planlanan >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '+9 hours'))""", (cekim, ap, cekim, once))
        c.execute("""INSERT OR IGNORE INTO ucus_degisim SELECT y.havalimani, y.anahtar, ?, CASE WHEN upper(y.durum) LIKE '%İPTAL%' OR upper(y.durum) LIKE '%IPTAL%'
                     OR upper(y.durum) LIKE '%CANCEL%' THEN 'iptal' ELSE 'saat_degisti' END, y.ucus_no, y.havayolu, y.yon, y.hat, y.karsi_sehir, y.planlanan,
                     o.planlanan || ' | ' || coalesce(o.durum, ''), y.planlanan || ' | ' || coalesce(y.durum, '')
                     FROM ucus_gozlem y JOIN ucus_gozlem o ON o.havalimani = y.havalimani AND o.anahtar = y.anahtar AND o.cekim_zamani = ?
                     WHERE y.havalimani = ? AND y.cekim_zamani = ? AND (o.planlanan <> y.planlanan OR
                       ((upper(y.durum) LIKE '%İPTAL%' OR upper(y.durum) LIKE '%IPTAL%' OR upper(y.durum) LIKE '%CANCEL%') AND coalesce(o.durum, '') <> y.durum))""",
                  (cekim, once, ap, cekim))
    c.commit()
    say = c.execute("SELECT degisim, count(*) FROM ucus_degisim WHERE tespit_zamani = ? GROUP BY 1", (cekim,)).fetchall()
    not_ = (f"IST {sum(1 for s in satirlar if s['havalimani'] == 'IST')}, SAW {sum(1 for s in satirlar if s['havalimani'] == 'SAW')}; değişim {dict(say)}"
            + ("; " + "; ".join(notlar) if notlar else ""))
    kosu_yaz(c, "ucus_ekrani", bas, len(satirlar), ist.sayac, "tamam", not_); return len(satirlar), ist.sayac, not_
