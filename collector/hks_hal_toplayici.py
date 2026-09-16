"""HKS resmî toptancı hal listesi → warehouse/product/onemli_tesisler.sqlite::hal
hal.gov.tr/Sayfalar/Toptanci-Halleri.aspx?sid=<il> : ad, tür (Belediye/Özel), faaliyet tarihi, adres, il. Telefon saklanmaz.
Koordinat: adres → collector.geocode (yaklaşık); OSM'de hal varsa (osm_poi alt_kategori='hal', aynı il, ≤3 km) nokta OSM'den.
"""
from __future__ import annotations
import html, os, re, sqlite3, sys, time, urllib.request
from datetime import datetime, timezone
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import haversine_km, normalize_name, tr_title  # noqa: E402
OUT = os.path.join(REPO, "warehouse", "product", "onemli_tesisler.sqlite")
UA = {"User-Agent": "Mozilla/5.0 (GEOPROP; heytteknoloji@gmail.com)"}
IL_AD = {1:"ADANA",2:"ADIYAMAN",3:"AFYONKARAHİSAR",4:"AĞRI",5:"AMASYA",6:"ANKARA",7:"ANTALYA",8:"ARTVİN",9:"AYDIN",10:"BALIKESİR",11:"BİLECİK",12:"BİNGÖL",13:"BİTLİS",14:"BOLU",15:"BURDUR",16:"BURSA",17:"ÇANAKKALE",18:"ÇANKIRI",19:"ÇORUM",20:"DENİZLİ",21:"DİYARBAKIR",22:"EDİRNE",23:"ELAZIĞ",24:"ERZİNCAN",25:"ERZURUM",26:"ESKİŞEHİR",27:"GAZİANTEP",28:"GİRESUN",29:"GÜMÜŞHANE",30:"HAKKARİ",31:"HATAY",32:"ISPARTA",33:"MERSİN",34:"İSTANBUL",35:"İZMİR",36:"KARS",37:"KASTAMONU",38:"KAYSERİ",39:"KIRKLARELİ",40:"KIRŞEHİR",41:"KOCAELİ",42:"KONYA",43:"KÜTAHYA",44:"MALATYA",45:"MANİSA",46:"KAHRAMANMARAŞ",47:"MARDİN",48:"MUĞLA",49:"MUŞ",50:"NEVŞEHİR",51:"NİĞDE",52:"ORDU",53:"RİZE",54:"SAKARYA",55:"SAMSUN",56:"SİİRT",57:"SİNOP",58:"SİVAS",59:"TEKİRDAĞ",60:"TOKAT",61:"TRABZON",62:"TUNCELİ",63:"ŞANLIURFA",64:"UŞAK",65:"VAN",66:"YOZGAT",67:"ZONGULDAK",68:"AKSARAY",69:"BAYBURT",70:"KARAMAN",71:"KIRIKKALE",72:"BATMAN",73:"ŞIRNAK",74:"BARTIN",75:"ARDAHAN",76:"IĞDIR",77:"YALOVA",78:"KARABÜK",79:"KİLİS",80:"OSMANİYE",81:"DÜZCE"}


def main(sadece_osm: bool = False, belde: bool = False):
    c = sqlite3.connect(OUT, timeout=60); now = datetime.now(timezone.utc).isoformat()
    c.executescript("""CREATE TABLE IF NOT EXISTS hal (id INTEGER PRIMARY KEY, il TEXT, ilce TEXT, ad TEXT, tur TEXT, faaliyet_tarihi TEXT, adres TEXT, lat REAL, lon REAL,
                       koordinat_kaynagi TEXT, guncellenme TEXT, UNIQUE (il, ad)); CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);""")
    n = 0
    for sid in ([] if sadece_osm else range(1, 82)):
        try:
            h = urllib.request.urlopen(urllib.request.Request(f"https://www.hal.gov.tr/Sayfalar/Toptanci-Halleri.aspx?sid={sid}", headers=UA), timeout=40).read().decode("utf-8", "ignore")
        except Exception as exc:
            print(f"  il {sid}: HATA {type(exc).__name__}"); continue
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
            cells = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", td))).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(cells) >= 7 and cells[0] and cells[0] != "Toptancı Hal Adı":
                c.execute("INSERT OR IGNORE INTO hal (il, ad, tur, faaliyet_tarihi, adres, guncellenme) VALUES (?,?,?,?,?,?)", (IL_AD[sid], cells[0], cells[1], cells[2], cells[3], now)); n += 1
        c.commit(); time.sleep(0.4)
    print("hal:", n)
    # koordinat: 1) OSM hal eşleşmesi (aynı il, ad benzer veya tek hal), 2) geocode
    from collector.geocode import adres_kodla
    from geoprop.idari import AdminLookup
    A = AdminLookup(os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite"))
    o = sqlite3.connect(f"file:{os.path.join(REPO, 'warehouse', 'product', 'osm_poi.sqlite')}?mode=ro", uri=True)
    osm = [(ad, lat, lon) for ad, lat, lon in o.execute("SELECT ad, lat, lon FROM poi WHERE alt_kategori='hal'")]
    for hid, il, ad, adres in ([] if sadece_osm else c.execute("SELECT id, il, ad, adres FROM hal WHERE lat IS NULL").fetchall()):
        hit = None
        # OSM: adında il/ilçe kelimesi geçen hal
        key = normalize_name(re.sub(r"BELEDİYESİ|TOPTANCI|HALİ|SEBZE|MEYVE|VE|BÜYÜKŞEHİR", " ", ad)).strip()
        cands = [(la, lo) for a, la, lo in osm if a and key and key.split()[0] in normalize_name(a)]
        if len(cands) == 1 and A.lookup(*cands[0])[0] and normalize_name(A.lookup(*cands[0])[0]) == normalize_name(il):
            hit = (cands[0][0], cands[0][1], "osm: ad eşleşmesi")
        if not hit:
            m = re.search(r"\b([A-ZÇĞİÖŞÜa-zçğıöşü]+)\s*/\s*" + re.escape(il[:4]), adres or "")
            ilce = m.group(1) if m else None
            hit = adres_kodla(adres, il, ilce, ad)
        if hit:
            il2, ilce2 = A.lookup(hit[0], hit[1])
            c.execute("UPDATE hal SET lat=?, lon=?, koordinat_kaynagi=?, ilce=? WHERE id=?", (hit[0], hit[1], hit[2], tr_title(ilce2) if ilce2 else None, hid))
        else:
            c.execute("UPDATE hal SET koordinat_kaynagi='bulunamadı' WHERE id=?", (hid,))
        c.commit()
    # 2. geçiş: zayıf konumlar (yalnız ad ile geocode / bulunamadı) → il içindeki OSM hal nesnesine bağla.
    #   a) 'ad eşleşmesi (yaklaşık)': aynı ildeki en yakın OSM hal ≤ 8 km ise ona taşı (İBB hali: Eyüpsultan→Bayrampaşa gibi)
    #   b) 'bulunamadı': ilde başka kayda bağlanmamış tek OSM hal varsa ona bağla. Aksi hâlde dokunma (uydurma yok).
    osm_il = {}
    for a, la, lo in osm:
        il2 = A.lookup(la, lo)[0]
        if il2 and not re.search(r"inşaat|balık|balik|et hali|çiçek", a or "", re.I):   # balık/et/çiçek hali sebze-meyve hali değildir
            osm_il.setdefault(normalize_name(il2), []).append((a, la, lo))
    kullanilan = {(round(la, 5), round(lo, 5)) for la, lo in c.execute("SELECT lat, lon FROM hal WHERE koordinat_kaynagi LIKE 'osm%'")}
    for hid, il, ad, kk, la0, lo0 in c.execute("SELECT id, il, ad, koordinat_kaynagi, lat, lon FROM hal WHERE koordinat_kaynagi IN ('ad eşleşmesi (yaklaşık)', 'bulunamadı')").fetchall():
        adaylar = [x for x in osm_il.get(normalize_name(il), []) if (round(x[1], 5), round(x[2], 5)) not in kullanilan]
        hit = None
        toptanci = [x for x in adaylar if re.search(r"toptancı|toptanci", x[0] or "", re.I)]
        if kk == "ad eşleşmesi (yaklaşık)" and len(toptanci) == 1 and "ŞUBESİ" not in ad:
            hit = (toptanci[0][1], toptanci[0][2], "osm: il içinde tek 'toptancı hal' nesnesi")
        elif kk == "ad eşleşmesi (yaklaşık)" and la0 is not None and adaylar:
            if True:
                en = min(adaylar, key=lambda x: haversine_km(la0, lo0, x[1], x[2]))
                if haversine_km(la0, lo0, en[1], en[2]) <= 8:
                    hit = (en[1], en[2], "osm: il içinde en yakın hal (≤8 km; ad eşleşmesi yaklaşık)")
        elif kk == "bulunamadı" and len(adaylar) == 1 and "ŞUBESİ" not in ad:
            hit = (adaylar[0][1], adaylar[0][2], "osm: il içinde tek hal")
        if hit:
            kullanilan.add((round(hit[0], 5), round(hit[1], 5)))
            il2, ilce2 = A.lookup(hit[0], hit[1])
            c.execute("UPDATE hal SET lat=?, lon=?, koordinat_kaynagi=?, ilce=? WHERE id=?", (hit[0], hit[1], hit[2], tr_title(ilce2) if ilce2 else None, hid))
    c.commit()
    # 3. geçiş: "… X ŞUBESİ" satırları — X bir ilçe adıysa koordinat o ilçede olmalı; değilse OSM hal (o ilçede) → ilçe merkezi
    # (yaklaşık). X ilçe değilse (belde/mahalle) ve konum ana halin ilçesine düşmüşse koordinat silinir ('bulunamadı').
    ilceler = {(normalize_name(il_adi), normalize_name(ad)): (la, lo) for ad, il_adi, la, lo in
               sqlite3.connect(f"file:{os.path.join(REPO, 'warehouse', 'product', 'idari_sinirlar.sqlite')}?mode=ro", uri=True)
               .execute("SELECT ad, il_adi, lat, lon FROM sinir WHERE seviye='ilce'")}
    ana_ilce = {il: ilce for il, ilce in c.execute("SELECT il, ilce FROM hal WHERE ad NOT LIKE '%ŞUBESİ%' AND ilce IS NOT NULL")}
    for hid, il, ad, ilce, la0, lo0, kk in c.execute("SELECT id, il, ad, ilce, lat, lon, koordinat_kaynagi FROM hal WHERE ad LIKE '%ŞUBESİ%'").fetchall():
        m = re.search(r"HALİ\s+(.+?)\s+ŞUBESİ", ad)
        if not m:
            continue
        x, il_n = normalize_name(m.group(1)), normalize_name(il)
        if (il_n, x) in ilceler:
            if la0 is not None and normalize_name(ilce or "") == x:
                continue   # doğru ilçede
            osm_ilce = [o for o in osm_il.get(il_n, []) if normalize_name(A.lookup(o[1], o[2])[1] or "") == x
                        and (round(o[1], 5), round(o[2], 5)) not in kullanilan]
            if osm_ilce:
                hit = (osm_ilce[0][1], osm_ilce[0][2], "osm: şube ilçesindeki hal"); kullanilan.add((round(hit[0], 5), round(hit[1], 5)))
            else:
                hit = (*ilceler[(il_n, x)], "şube ilçe merkezi (yaklaşık)")
            c.execute("UPDATE hal SET lat=?, lon=?, koordinat_kaynagi=?, ilce=? WHERE id=?", (hit[0], hit[1], hit[2], tr_title(m.group(1)), hid))
        elif la0 is not None and ilce and ana_ilce.get(il) == ilce and "osm" in (kk or "") or (kk == "ad eşleşmesi (yaklaşık)" and ana_ilce.get(il) == ilce):
            c.execute("UPDATE hal SET lat=NULL, lon=NULL, koordinat_kaynagi='bulunamadı', ilce=NULL WHERE id=?", (hid,))
    c.commit()
    if belde:   # 4. geçiş (geocode; tek süreç kuralı): ilçe olmayan şube yerleşimleri (belde/mahalle) → yerleşim merkezi;
        #            koordinatı sıfırlanmış ana hal satırları → adres/ad ile yeniden kodla (sonraki --sadece-osm geçişi OSM'ye oturtur)
        for hid, il, ad, adres in c.execute("SELECT id, il, ad, adres FROM hal WHERE lat IS NULL AND koordinat_kaynagi <> 'bulunamadı'").fetchall():
            m = re.search(r"HALİ\s+(.+?)\s+ŞUBESİ", ad)
            hit = adres_kodla(f"{tr_title(m.group(1))} Beldesi", il, None) if m else adres_kodla(adres, il, None, ad)
            if hit and not m:
                il2, ilce2 = A.lookup(hit[0], hit[1])
                c.execute("UPDATE hal SET lat=?, lon=?, koordinat_kaynagi=?, ilce=? WHERE id=?", (hit[0], hit[1], hit[2], tr_title(ilce2) if ilce2 else None, hid)); continue
            if hit:
                il2, ilce2 = A.lookup(hit[0], hit[1])
                c.execute("UPDATE hal SET lat=?, lon=?, koordinat_kaynagi='şube yerleşim merkezi (yaklaşık)', ilce=? WHERE id=?", (hit[0], hit[1], tr_title(ilce2) if ilce2 else None, hid))
        c.commit()
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('hal', ?, ?, 'HKS hal.gov.tr toptancı hal listesi')", (c.execute("SELECT COUNT(*) FROM hal").fetchone()[0], now)); c.commit()
    print("koordinatlı:", c.execute("SELECT SUM(lat IS NOT NULL), COUNT(*) FROM hal").fetchone())


if __name__ == "__main__":
    main(sadece_osm="--sadece-osm" in sys.argv, belde="--belde" in sys.argv)
