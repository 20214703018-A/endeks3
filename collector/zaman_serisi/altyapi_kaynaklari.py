"""
Altyapı zaman serileri (29 Ekim 2026).

sarj_istasyon (3 saatte bir)
  EPDK Şarj@TR herkese açık servisi (sarjtr.epdk.gov.tr/sarjet/api/stations — mobil uygulamanın listesi): her istasyon için konum,
  marka (işletmeci), ad, yeşil enerji, soket kimlikleri ve "şu an müsait" bayrağı. İki tablo:
    istasyon   istasyon başına bir satır (ilk_gorulme / son_gorulme; marka/ad/soket sayısı değişirse delta.py eski hâli __gecmis'e alır)
    durum      her çekimde istasyon başına müsaitlik (cekim_saati = UTC saat) → doluluk ve kurulum hızı zaman serisi
  Sunucunun sertifika zinciri eksik (gece turunda da böyleydi) → yalnız bu adres için sertifika doğrulaması kapalı.
"""
import json, ssl, urllib.request
from datetime import datetime, timezone
from ortak import db, kosu_yaz, simdi

SARJ = "https://sarjtr.epdk.gov.tr/sarjet/api/stations"


def sarj_istasyon(dizin, tam=False):
    c = db(f"{dizin}/sarj_istasyon.sqlite", """
        CREATE TABLE IF NOT EXISTS istasyon (istasyon_id INTEGER PRIMARY KEY, lat REAL, lon REAL, marka TEXT, ad TEXT, yesil TEXT,
            soket_sayisi INTEGER, soketler TEXT, ilk_gorulme TEXT, son_gorulme TEXT);
        CREATE TABLE IF NOT EXISTS durum (istasyon_id INTEGER, cekim_saati TEXT, musait INTEGER, PRIMARY KEY (istasyon_id, cekim_saati));""")
    bas = simdi(); saat = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00Z")
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    veri = None
    for _ in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(SARJ, headers={"User-Agent": "Dart/3.1 (dart:io)", "Accept": "application/json"}),
                                       timeout=120, context=ctx)
            veri = json.loads(r.read()); break
        except Exception as e:
            hata = f"{type(e).__name__}: {e}"
    if not isinstance(veri, list):
        kosu_yaz(c, "sarj_istasyon", bas, 0, 3, "hata", hata if veri is None else "beklenmeyen yanıt")
        return 0, 3, "liste alınamadı"
    now = simdi(); n = 0
    for x in veri:
        sid = x.get("id")
        if sid is None: continue
        sok = [s.get("id") for s in x.get("sockets") or []]
        c.execute("""INSERT INTO istasyon VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (istasyon_id) DO UPDATE SET lat=excluded.lat, lon=excluded.lon,
            marka=excluded.marka, ad=excluded.ad, yesil=excluded.yesil, soket_sayisi=excluded.soket_sayisi, soketler=excluded.soketler,
            son_gorulme=excluded.son_gorulme""", (sid, x.get("lat"), x.get("lng"), x.get("brand"), x.get("title"), x.get("green"), len(sok),
                                                  json.dumps(sok), now, now))
        c.execute("INSERT OR REPLACE INTO durum VALUES (?,?,?)", (sid, saat, 1 if x.get("available") else 0)); n += 1
    c.commit()
    musait = sum(1 for x in veri if x.get("available"))
    kosu_yaz(c, "sarj_istasyon", bas, n, 1, "tamam", f"{n} istasyon, {musait} müsait")
    return n, 1, f"{n} istasyon, {musait} müsait"
