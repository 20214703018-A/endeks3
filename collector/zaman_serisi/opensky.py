"""
OpenSky Network günlük uçuşları (12 Ekim 2026; kullanıcının OpenSky hesabı, OAuth istemci kimliği → GitHub secret OPENSKY_CLIENT_ID/SECRET).
Türkiye'nin 36 havalimanına inen/kalkan uçuşlar (bir önceki UTC günü; OpenSky gece toplu işler): çağrı kodu → havayolu (ilk 3 harf ICAO),
kalkış/varış havalimanı → ülke (ICAO ön eki). Kredi bütçesi: istek başına ~30, günde 4.000 → her koşu dünü alır, kalan krediyle geriye doğru
eksik günleri doldurur (en çok 30 gün geri; kalan kredi < 400'de durur).
Sınır: OpenSky alıcı ağına dayanır; Rusya/Orta Doğu üzerinde kapsama zayıf → bu bölgelerden gelen uçuşların bir kısmında kalkış yeri boş ('?').
"""
import json, os, time, urllib.parse, urllib.request
from ortak import Istemci, db, kosu_yaz, simdi

HAVALIMANI = {"LTFM": "İstanbul", "LTFJ": "İstanbul Sabiha Gökçen", "LTAI": "Antalya", "LTBJ": "İzmir", "LTAC": "Ankara Esenboğa", "LTBS": "Dalaman",
              "LTFE": "Bodrum-Milas", "LTCG": "Trabzon", "LTAF": "Adana", "LTFG": "Alanya Gazipaşa", "LTAJ": "Gaziantep", "LTCE": "Erzurum",
              "LTAU": "Kayseri", "LTAN": "Konya", "LTCI": "Van", "LTAT": "Malatya", "LTCC": "Diyarbakır", "LTAY": "Denizli", "LTFH": "Samsun",
              "LTCN": "Kahramanmaraş", "LTAP": "Amasya Merzifon", "LTCF": "Kars", "LTBQ": "Kocaeli", "LTBU": "Tekirdağ Çorlu", "LTFD": "Balıkesir Edremit",
              "LTBH": "Çanakkale", "LTCR": "Mardin", "LTCS": "Şanlıurfa GAP", "LTAZ": "Nevşehir Kapadokya", "LTFC": "Isparta", "LTCJ": "Batman",
              "LTDA": "Hatay", "LTBZ": "Kütahya Zafer", "LTCK": "Muş", "LTBA": "İstanbul Atatürk", "LTAR": "Sivas"}
ONEK = {"ED": "Almanya", "ET": "Almanya", "EG": "Birleşik Krallık", "EH": "Hollanda", "EB": "Belçika", "EK": "Danimarka", "EN": "Norveç", "ES": "İsveç",
        "EF": "Finlandiya", "EP": "Polonya", "EI": "İrlanda", "EL": "Lüksemburg", "EV": "Letonya", "EY": "Litvanya", "EE": "Estonya", "LF": "Fransa",
        "LE": "İspanya", "LI": "İtalya", "LS": "İsviçre", "LO": "Avusturya", "LK": "Çekya", "LZ": "Slovakya", "LH": "Macaristan", "LR": "Romanya",
        "LB": "Bulgaristan", "LG": "Yunanistan", "LC": "Kıbrıs", "LY": "Sırbistan/Karadağ", "LD": "Hırvatistan", "LJ": "Slovenya", "LQ": "Bosna-Hersek",
        "LW": "Kuzey Makedonya", "LA": "Arnavutluk", "LU": "Moldova", "LM": "Malta", "LP": "Portekiz", "LX": "Cebelitarık", "BI": "İzlanda", "LT": "Türkiye",
        "LL": "İsrail", "UK": "Ukrayna", "UM": "Belarus", "UB": "Azerbaycan", "UG": "Gürcistan", "UD": "Ermenistan", "UA": "Kazakistan/Kırgızistan",
        "UT": "Orta Asya (Özbekistan/Türkmenistan/Tacikistan)", "UU": "Rusya", "UW": "Rusya", "UR": "Rusya", "UN": "Rusya", "US": "Rusya", "UL": "Rusya",
        "UI": "Rusya", "UH": "Rusya", "UE": "Rusya", "UO": "Rusya", "OI": "İran", "OR": "Irak", "OS": "Suriye", "OJ": "Ürdün", "OL": "Lübnan",
        "OE": "Suudi Arabistan", "OM": "BAE", "OT": "Katar", "OB": "Bahreyn", "OK": "Kuveyt", "OO": "Umman", "OY": "Yemen", "HE": "Mısır", "HL": "Libya",
        "DT": "Tunus", "DA": "Cezayir", "GM": "Fas", "HS": "Sudan", "HA": "Etiyopya", "HK": "Kenya", "FA": "Güney Afrika", "VI": "Hindistan",
        "VA": "Hindistan", "VO": "Hindistan", "VE": "Hindistan", "OP": "Pakistan", "OA": "Afganistan", "ZB": "Çin", "ZS": "Çin", "ZG": "Çin", "ZU": "Çin",
        "RK": "Güney Kore", "RJ": "Japonya", "VT": "Tayland", "WS": "Singapur", "WM": "Malezya", "WI": "Endonezya", "VV": "Vietnam", "RP": "Filipinler",
        "K": "ABD", "C": "Kanada", "MM": "Meksika", "SB": "Brezilya", "SA": "Arjantin", "Y": "Avustralya"}
ulke = lambda k: (ONEK.get((k or "")[:2]) or ONEK.get((k or "")[:1])) if k else None


def opensky(dizin, tam=False):
    c = db(f"{dizin}/opensky_ucus.sqlite", """CREATE TABLE IF NOT EXISTS ucus (icao24 TEXT, cagri_kodu TEXT, havayolu_icao TEXT, kalkis TEXT, kalkis_ulke TEXT, varis TEXT,
          varis_ulke TEXT, ilk_gorulme INTEGER, son_gorulme INTEGER, kalkis_zamani_utc TEXT, varis_zamani_utc TEXT, sorgu_havalimani TEXT, yon TEXT,
          sorgu_gunu TEXT, cekim_zamani TEXT, PRIMARY KEY (icao24, ilk_gorulme, sorgu_havalimani, yon));
        CREATE TABLE IF NOT EXISTS gun_durum (sorgu_gunu TEXT, havalimani TEXT, yon TEXT, ucus INTEGER, http INTEGER, cekim_zamani TEXT,
          PRIMARY KEY (sorgu_gunu, havalimani, yon));""")
    cid, sec = os.environ.get("OPENSKY_CLIENT_ID"), os.environ.get("OPENSKY_CLIENT_SECRET")
    if not cid or not sec: kosu_yaz(c, "opensky", simdi(), 0, 0, "kimlik_yok"); return 0, 0, "OPENSKY_CLIENT_ID/SECRET yok"
    bas = simdi(); n = 0; ist = 0; kalan = 4000; jeton = {"t": 0, "v": None}

    def tok():
        if time.time() > jeton["t"] - 60:
            v = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
            r = urllib.request.urlopen(urllib.request.Request("https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
                                       data=v, headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30)
            j = json.loads(r.read()); jeton["v"] = j["access_token"]; jeton["t"] = time.time() + j.get("expires_in", 1800)
        return jeton["v"]
    bugun0 = int(time.time() // 86400) * 86400
    yapildi = {(r[0], r[1], r[2]) for r in c.execute("SELECT sorgu_gunu, havalimani, yon FROM gun_durum WHERE http IN (200, 404)")}
    for geri in range(1, 31):
        gun = bugun0 - geri * 86400; g = time.strftime("%Y-%m-%d", time.gmtime(gun))
        for ap in HAVALIMANI:
            for yon in ("arrival", "departure"):
                if (g, ap, yon) in yapildi: continue
                if kalan < 400: break
                st, f = None, []
                try:
                    time.sleep(0.6); ist += 1
                    r = urllib.request.urlopen(urllib.request.Request(f"https://opensky-network.org/api/flights/{yon}?airport={ap}&begin={gun}&end={gun + 86399}",
                                               headers={"Authorization": "Bearer " + tok()}), timeout=90)
                    st = r.status; f = json.loads(r.read() or b"[]"); kalan = int(r.headers.get("X-Rate-Limit-Remaining") or kalan)
                except urllib.error.HTTPError as e:
                    st = e.code
                    if e.code == 429: kalan = 0; break
                except Exception:
                    st = -1
                for x in f or []:
                    cs = (x.get("callsign") or "").strip()
                    c.execute("INSERT OR REPLACE INTO ucus VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (x.get("icao24"), cs or None, cs[:3] if len(cs) >= 4 and cs[:3].isalpha() else None,
                              x.get("estDepartureAirport"), ulke(x.get("estDepartureAirport")), x.get("estArrivalAirport"), ulke(x.get("estArrivalAirport")),
                              x.get("firstSeen"), x.get("lastSeen"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x["firstSeen"])) if x.get("firstSeen") else None,
                              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x["lastSeen"])) if x.get("lastSeen") else None, ap, "varis" if yon == "arrival" else "kalkis",
                              g, simdi())); n += 1
                c.execute("INSERT OR REPLACE INTO gun_durum VALUES (?,?,?,?,?,?)", (g, ap, yon, len(f or []), st, simdi())); c.commit()
            if kalan < 400: break
        if kalan < 400: break
    kosu_yaz(c, "opensky", bas, n, ist, "tamam", f"kalan kredi {kalan}"); return n, ist, f"kalan kredi {kalan}"
