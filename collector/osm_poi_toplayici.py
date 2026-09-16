"""OSM Türkiye POI ambarı — Geofabrik PBF → warehouse/product/osm_poi.sqlite (skill: geoprop-veri-mimarisi).

Neden PBF: Overpass public sunucusu yavaş/kırılgan ve rate-limitli; tam kesit (~650 MB, günlük güncel)
tek geçişte işlenir, ürün DB küçük kalır (~150–200 MB), sorgu R-tree ile ms düzeyinde çalışır.
Lisans: ODbL — saklama ve türev üretim serbest, kaynak "© OpenStreetMap katkıcıları" gösterilir.

Çıktı tabloları (hepsi açık sınıf, PII yok):
  poi(id, osm_tip, osm_id, kategori, alt_kategori, marka, ad, lat, lon, etiketler_json)
  poi_rtree(id, min_lat, max_lat, min_lon, max_lon)
  hat(id, osm_id, tur, ref, ad, operator, from_ad, to_ad, renk)          -- route=bus/tram/subway/train/ferry
  hat_durak(hat_id, sira, durak_osm_tip, durak_osm_id)                    -- hat → durak sırası
  kapsama(kategori, alt_kategori, sayi, guncellenme)

Kategori haritası KATEGORILER'de; zincir (marka) tespiti `brand` etiketi + MARKALAR ad eşlemesi ile.
Ad normalizasyonu paylaşılan `normalize_name` (ürün DB'sinde `ad_norm` indeksli, WHERE'de fonksiyon yok).

Kullanım:
  python3.13 collector/osm_poi_toplayici.py [--pbf warehouse/raw/osm/turkey-latest.osm.pbf] [--out warehouse/product/osm_poi.sqlite]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

import osmium

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402

DEFAULT_PBF = os.path.join(REPO, "warehouse", "raw", "osm", "turkey-latest.osm.pbf")
DEFAULT_OUT = os.path.join(REPO, "warehouse", "product", "osm_poi.sqlite")

# (kategori, alt_kategori) ← etiket kuralları. İlk eşleşen kazanır; sıra önemlidir (özel → genel).
# Değer: liste of (key, value|None|set) koşulları; hepsi sağlanmalı.
KATEGORILER: list[tuple[str, str, list[tuple[str, object]]]] = [
    # sağlık
    ("saglik", "hastane", [("amenity", "hospital")]),
    ("saglik", "saglik_ocagi", [("amenity", {"clinic", "doctors", "health_post"}), ("_ad_icerir", ("aile sagligi", "saglik ocagi", "toplum sagligi", " asm", "saglik merkezi", "saglik evi"))]),
    ("saglik", "saglik_ocagi", [("healthcare", {"centre", "clinic"}), ("_ad_icerir", ("aile sagligi", "saglik ocagi", "toplum sagligi", " asm", "saglik merkezi", "saglik evi"))]),
    ("saglik", "klinik", [("amenity", {"clinic", "doctors"})]),
    ("saglik", "eczane", [("amenity", "pharmacy")]),
    ("saglik", "veteriner", [("amenity", "veterinary")]),
    # eğitim
    ("egitim", "universite", [("amenity", {"university", "college"})]),
    ("egitim", "okul", [("amenity", "school")]),
    ("egitim", "anaokulu", [("amenity", "kindergarten")]),
    ("egitim", "kutuphane", [("amenity", "library")]),
    # ulaşım
    ("ulasim", "tren_istasyonu", [("railway", "station"), ("station", {None, "train"})]),
    ("ulasim", "metro_istasyonu", [("railway", "station"), ("station", "subway")]),
    ("ulasim", "tramvay_duragi", [("railway", "tram_stop")]),
    ("ulasim", "metro_istasyonu", [("station", "subway")]),
    ("ulasim", "tren_istasyonu", [("public_transport", "station"), ("train", "yes")]),
    ("ulasim", "otobus_duragi", [("highway", "bus_stop")]),
    ("ulasim", "otobus_duragi", [("public_transport", "platform"), ("bus", "yes")]),
    ("ulasim", "otogar", [("amenity", "bus_station")]),
    ("ulasim", "iskele", [("amenity", "ferry_terminal")]),
    ("ulasim", "havalimani", [("aeroway", "aerodrome"), ("_yok", ("military",)), ("_ad_icermez", ("hava ussu", "askeri", "air base"))]),
    ("ulasim", "liman", [("harbour", "yes")]),
    ("ulasim", "liman", [("industrial", "port")]),
    ("ulasim", "liman", [("landuse", "port")]),
    ("ulasim", "otopark", [("amenity", "parking")]),
    ("ulasim", "benzin_istasyonu", [("amenity", "fuel")]),
    # alışveriş
    ("alisveris", "avm", [("shop", "mall")]),
    ("alisveris", "market", [("shop", {"supermarket", "convenience", "grocery"})]),
    ("alisveris", "tekel", [("shop", {"alcohol", "tobacco"})]),
    ("alisveris", "pazar", [("amenity", "marketplace")]),
    ("ibadet", "cami", [("amenity", "place_of_worship"), ("religion", "muslim")]),
    ("ibadet", "ibadethane", [("amenity", "place_of_worship")]),
    ("alisveris", "hal", [("_ad_icerir", ("toptanci hal", "sebze hal", "meyve hal", "toptanci sebze", "toptanci meyve", "balik hali", "sebze ve meyve hal"))]),
    ("alisveris", "toptanci", [("shop", "wholesale")]),
    ("alisveris", "firin", [("shop", "bakery")]),
    # yeme-içme
    ("yeme_icme", "kafe", [("amenity", "cafe")]),
    ("yeme_icme", "restoran", [("amenity", "restaurant")]),
    ("yeme_icme", "fast_food", [("amenity", "fast_food")]),
    ("yeme_icme", "bar", [("amenity", {"bar", "pub"})]),
    # sanayi / iş
    ("sanayi", "osb", [("landuse", "industrial"), ("_ad_icerir", ("organize sanayi", "osb"))]),
    ("sanayi", "sanayi_sitesi", [("landuse", "industrial"), ("_ad_icerir", ("sanayi sitesi", "san. sit", "küçük sanayi"))]),
    ("sanayi", "sanayi_alani", [("landuse", "industrial")]),
    ("sanayi", "fabrika", [("man_made", "works")]),
    # spor / kültür
    ("spor", "stadyum", [("leisure", "stadium")]),
    ("spor", "spor_salonu", [("leisure", {"sports_centre", "fitness_centre", "sports_hall"})]),
    ("spor", "yuzme_havuzu", [("leisure", "swimming_pool")]),
    ("spor", "saha", [("leisure", "pitch")]),
    ("kultur", "muze", [("tourism", "museum")]),
    ("kultur", "sinema", [("amenity", "cinema")]),
    ("kultur", "tiyatro", [("amenity", "theatre")]),
    ("kultur", "park", [("leisure", "park")]),
    # kamu / hizmet
    ("kamu", "belediye", [("amenity", "townhall")]),
    ("kamu", "karakol", [("amenity", "police")]),
    ("kamu", "itfaiye", [("amenity", "fire_station")]),
    ("kamu", "postane", [("amenity", "post_office")]),
    ("kamu", "banka", [("amenity", "bank")]),
    ("kamu", "atm", [("amenity", "atm")]),
    ("kamu", "adliye", [("amenity", "courthouse")]),
    ("ibadet", "cami", [("amenity", "place_of_worship"), ("religion", "muslim")]),
    ("ibadet", "ibadethane", [("amenity", "place_of_worship")]),
    ("konaklama", "otel", [("tourism", {"hotel", "hostel", "guest_house", "motel"})]),
    # Baz istasyonu / haberleşme kulesi: BTK resmî liste yok → OSM (kısmi kapsam; "yaklaşık, resmî değil" etiketiyle kullanılır)
    ("altyapi", "baz_istasyonu", [("man_made", {"mast", "tower", "antenna"}), ("tower:type", {"communication", "telecom", "gsm", "cellular"})]),
    ("altyapi", "baz_istasyonu", [("man_made", {"mast", "tower", "antenna"}), ("communication:mobile_phone", "yes")]),
    ("altyapi", "baz_istasyonu", [("telecom", {"antenna", "base_station", "mast"})]),
    ("altyapi", "yuksek_gerilim_diregi", [("power", {"tower"})]),
    ("altyapi", "trafo", [("power", {"substation"})]),
    # Genel iş yeri sınıfları (yoğunluk oranı ve ofis/mağaza kümeleri için): yukarıdaki özel kurallar önce eşleşir
    ("hizmet", "kuafor_guzellik", [("shop", {"hairdresser", "beauty", "massage"})]),
    ("hizmet", "eczane_disi_saglik", [("shop", {"optician", "medical_supply"})]),
    ("is", "ofis", [("office", "*")]),
    ("alisveris", "magaza", [("shop", "*")]),
    ("is", "zanaat", [("craft", "*")]),
]

# Zincir tespiti: normalize edilmiş ad/brand içinde geçen anahtar → kanonik marka.
MARKALAR: dict[str, tuple[str, ...]] = {
    # market
    "BİM": ("bim",), "A101": ("a101", "a 101"), "ŞOK": ("sok market", "sok"), "Migros": ("migros", "migros jet", "macrocenter"),
    "CarrefourSA": ("carrefour",), "Metro": ("metro market", "metro gross"), "File": ("file market",), "Hakmar": ("hakmar",),
    "Tarım Kredi": ("tarim kredi",), "Gürmar": ("gurmar",), "Altunbilekler": ("altunbilek",), "Ekomini": ("ekomini",),
    "Onur Market": ("onur market",), "Kim Market": ("kim market",), "Bizim Toptan": ("bizim toptan",), "Happy Center": ("happy center",),
    # kahve
    "Starbucks": ("starbucks",), "Kahve Dünyası": ("kahve dunyasi",), "Espressolab": ("espressolab", "espresso lab"),
    "Gloria Jean's": ("gloria jean",), "Coffy": ("coffy",), "Tchibo": ("tchibo",), "Caribou": ("caribou",), "Cafe Nero": ("caffe nero", "cafe nero"),
    "Petra": ("petra roasting", "petra coffee"), "Arabica": ("arabica",),
    # restoran / fast food
    "McDonald's": ("mcdonald",), "Burger King": ("burger king",), "KFC": ("kfc",), "Domino's": ("domino",), "Popeyes": ("popeyes",),
    "Subway": ("subway",), "Little Caesars": ("little caesar",), "Pizza Hut": ("pizza hut",), "Komagene": ("komagene",),
    "Tavuk Dünyası": ("tavuk dunyasi",), "Köfteci Yusuf": ("kofteci yusuf",), "Simit Sarayı": ("simit sarayi",), "Baydöner": ("baydoner",),
    "Dürümle": ("durumle",), "Hd İskender": ("hd iskender",), "Pidem": ("pidem",), "Usta Dönerci": ("usta donerci",), "Bursa İshakbey": ("ishakbey",),
    "Maydonoz Döner": ("maydonoz",), "Arby's": ("arby",), "Sbarro": ("sbarro",), "Terra Pizza": ("terra pizza",),
    # tekel / diğer
    "Tekel": ("tekel",), "Gratis": ("gratis",), "Watsons": ("watsons",), "Rossmann": ("rossmann",), "LC Waikiki": ("lc waikiki", "lcw"),
    "Koçtaş": ("koctas",), "IKEA": ("ikea",), "Teknosa": ("teknosa",), "MediaMarkt": ("media markt", "mediamarkt"), "Decathlon": ("decathlon",),
    # spor
    "MACFit": ("macfit", "mac fit"), "Sports International": ("sports international",), "Fitness Time": ("fitness time",),
    # akaryakıt
    "Opet": ("opet",), "Shell": ("shell",), "Petrol Ofisi": ("petrol ofisi", "po ", "petrolofisi"), "BP": ("bp ",), "Total": ("totalenergies", "total "),
    "Aytemiz": ("aytemiz",), "Lukoil": ("lukoil",), "Alpet": ("alpet",), "Türkiye Petrolleri": ("turkiye petrolleri", "tp "),
    # otel / diğer
    "Hilton": ("hilton",), "Radisson": ("radisson",), "Ibis": ("ibis ",), "Divan": ("divan ",), "Dedeman": ("dedeman",),
    # banka
    "Ziraat": ("ziraat",), "İş Bankası": ("is bankasi", "isbank"), "Garanti BBVA": ("garanti",), "Yapı Kredi": ("yapi kredi", "yapikredi"),
    "Akbank": ("akbank",), "Halkbank": ("halkbank", "halk bankasi"), "VakıfBank": ("vakifbank", "vakif bank"), "QNB": ("qnb", "finansbank"),
    "DenizBank": ("denizbank",), "TEB": ("teb ",), "ING": ("ing bank", "ing "), "Kuveyt Türk": ("kuveyt turk",), "Albaraka": ("albaraka",),
}
# Kelime sınırlı eşleşme: "po " gibi kısa anahtarlar alt-dize olarak her yerde geçer (Petrol Ofisi
# yanlış pozitifleri); \b ile yalnız tam kelime eşleşir.
_MARKA_INDEX = [(re.compile(r"(?<![a-z0-9])" + re.escape(normalize_name(k).strip()) + r"(?![a-z0-9])"), kanon)
                for kanon, keys in MARKALAR.items() for k in keys]

# Okul alt türü: ad ipuçlarından (MEB adlandırması). Sıra: lise/ortaokul kelimeleri ilkokuldan önce.
OKUL_TURLERI = (("lise", ("lisesi", "anadolu lisesi", "meslek lisesi", "imam hatip lisesi", "fen lisesi", "lise")),
                ("ortaokul", ("ortaokulu", "ortaokul")),
                ("ilkokul", ("ilkokulu", "ilkokul", "ilkogretim")),
                ("anaokulu", ("anaokulu", "kres", "gunduz bakim")),
                ("ozel_egitim", ("ozel egitim",)))


def marka_bul(ad_norm: str, brand: str | None) -> str | None:
    if brand:
        b = normalize_name(brand)
        for rx, kanon in _MARKA_INDEX:
            if rx.search(b):
                return kanon
        return brand.strip()[:60]
    for rx, kanon in _MARKA_INDEX:
        if rx.search(ad_norm):
            return kanon
    return None


def okul_turu(ad_norm: str, tags) -> str:
    lvl = tags.get("isced:level") or tags.get("school:type") or ""
    for tur, keys in OKUL_TURLERI:
        if any(k in ad_norm for k in keys):
            return tur
    if lvl:
        if "3" in lvl or "lise" in lvl.lower(): return "lise"
        if "2" in lvl: return "ortaokul"
        if "1" in lvl: return "ilkokul"
    return "okul"


def siniflandir(tags) -> tuple[str, str] | None:
    ad_norm = normalize_name(tags.get("name") or "")
    for kat, alt, kosullar in KATEGORILER:
        ok = True
        for key, val in kosullar:
            if key == "_ad_icerir":
                if not any(v in " " + ad_norm + " " for v in val):
                    ok = False; break
                continue
            if key == "_ad_icermez":
                if any(v in " " + ad_norm + " " for v in val):
                    ok = False; break
                continue
            if key == "_yok":   # bu etiketler varsa eşleşme yok (ör. askeri havaalanı)
                if any(k in tags for k in val):
                    ok = False; break
                continue
            tv = tags.get(key)
            if val == "*":          # etiket var mı (herhangi bir değer)
                if tv is None or tv in ("no", "vacant"):
                    ok = False; break
                continue
            if isinstance(val, set):
                if None in val and tv is None:
                    continue
                if tv not in val:
                    ok = False; break
            elif val is None:
                continue
            elif tv != val:
                ok = False; break
        if ok:
            if alt == "okul":
                alt = okul_turu(ad_norm, tags)
            return kat, alt
    return None


SCHEMA = """
DROP TABLE IF EXISTS poi; DROP TABLE IF EXISTS poi_rtree; DROP TABLE IF EXISTS hat; DROP TABLE IF EXISTS hat_durak; DROP TABLE IF EXISTS kapsama;
CREATE TABLE poi (id INTEGER PRIMARY KEY, osm_tip TEXT, osm_id INTEGER, kategori TEXT, alt_kategori TEXT, marka TEXT,
                  ad TEXT, ad_norm TEXT, lat REAL, lon REAL, etiketler TEXT, geometri TEXT, alan_m2 REAL);
CREATE VIRTUAL TABLE poi_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE hat (id INTEGER PRIMARY KEY, osm_id INTEGER, tur TEXT, ref TEXT, ad TEXT, operator TEXT, from_ad TEXT, to_ad TEXT, renk TEXT);
CREATE TABLE hat_durak (hat_id INTEGER, sira INTEGER, durak_osm_tip TEXT, durak_osm_id INTEGER, PRIMARY KEY (hat_id, sira)) WITHOUT ROWID;
CREATE TABLE kapsama (kategori TEXT, alt_kategori TEXT, sayi INTEGER, guncellenme TEXT, PRIMARY KEY (kategori, alt_kategori));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

KEEP_TAGS = ("name", "brand", "operator", "opening_hours", "website", "addr:street", "addr:district",
             "addr:city", "cuisine", "isced:level", "capacity", "healthcare", "emergency", "railway", "station", "public_transport",
             "highway", "shop", "amenity", "leisure", "landuse", "tourism", "network", "ref", "religion", "denomination", "building")
# Alan geometrisi (sadeleştirilmiş GeoJSON) saklanan alt kategoriler: kampüs/OSB/sanayi sitesi/stadyum/park/AVM/hastane.
GEOMETRI_SAKLA = {"universite", "osb", "sanayi_sitesi", "sanayi_alani", "stadyum", "park", "avm", "hastane", "otogar", "liman", "havalimani"}


class PoiHandler(osmium.SimpleHandler):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        self.rows: list[tuple] = []
        self.n = 0
        self.hat_rows: list[tuple] = []
        self.hat_durak_rows: list[tuple] = []
        self.alan_hata = 0
        self.t0 = time.time()

    def _emit(self, osm_tip: str, osm_id: int, tags, lat: float, lon: float, geometri=None, alan_m2=None):
        cls = siniflandir(tags)
        if cls is None:
            return
        kat, alt = cls
        ad = tags.get("name") or tags.get("brand") or None
        ad_norm = normalize_name(ad or "")
        marka = marka_bul(ad_norm, tags.get("brand"))
        kept = {k: tags[k] for k in KEEP_TAGS if k in tags}
        if alt not in GEOMETRI_SAKLA:
            geometri, alan_m2 = None, None
        self.rows.append((osm_tip, osm_id, kat, alt, marka, ad, ad_norm or None, lat, lon, json.dumps(kept, ensure_ascii=False), geometri, alan_m2))
        if len(self.rows) >= 5000:
            self.flush()

    def flush(self):
        if self.rows:
            cur = self.conn.executemany(
                "INSERT INTO poi (osm_tip, osm_id, kategori, alt_kategori, marka, ad, ad_norm, lat, lon, etiketler, geometri, alan_m2) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                self.rows)
            self.n += len(self.rows); self.rows = []
            self.conn.commit()
            print(f"  {self.n:>9,} poi  {time.time() - self.t0:6.0f}s", flush=True)

    def node(self, n):
        if len(n.tags) == 0:
            return
        self._emit("node", n.id, n.tags, n.location.lat, n.location.lon)

    def area(self, a):
        # way ve multipolygon alanları (okul kampüsü, OSB, AVM, stadyum…) → temsilci nokta (centroid)
        if len(a.tags) == 0 or siniflandir(a.tags) is None:
            return
        try:
            rings = [[(p.location.lon, p.location.lat) for p in ring if p.location.valid()] for ring in a.outer_rings()]
            pts = [(lat, lon) for ring in rings for lon, lat in ring]
        except Exception as exc:  # alan kurulamadı (eksik düğüm) — sayılır, sessiz yutulmaz
            self.alan_hata += 1
            if self.alan_hata <= 3:
                print("  alan atlandı:", a.orig_id(), type(exc).__name__, exc, flush=True)
            return
        if not pts:
            self.alan_hata += 1
            return
        lat = sum(p[0] for p in pts) / len(pts); lon = sum(p[1] for p in pts) / len(pts)
        geometri = alan = None
        cls = siniflandir(a.tags)
        if cls and cls[1] in GEOMETRI_SAKLA:
            try:
                from shapely.geometry import MultiPolygon, Polygon, mapping
                polys = [Polygon(r) for r in rings if len(r) >= 4]
                g = MultiPolygon(polys) if len(polys) > 1 else polys[0]
                g = g.buffer(0).simplify(0.00005, preserve_topology=True)   # ~5 m
                geometri = json.dumps(mapping(g))
                alan = round(abs(g.area) * 111320 * 111320 * abs(__import__("math").cos(__import__("math").radians(lat))))
            except Exception:
                geometri = alan = None
        self._emit("way" if a.from_way() else "relation", a.orig_id(), a.tags, lat, lon, geometri, alan)

    def relation(self, r):
        route = r.tags.get("route")
        if r.tags.get("type") != "route" or route not in ("bus", "tram", "subway", "train", "light_rail", "ferry", "trolleybus", "minibus"):
            return
        hat_id = len(self.hat_rows) + 1
        self.hat_rows.append((hat_id, r.id, route, r.tags.get("ref"), r.tags.get("name"), r.tags.get("operator"),
                              r.tags.get("from"), r.tags.get("to"), r.tags.get("colour")))
        sira = 0
        for m in r.members:
            if m.role in ("stop", "platform", "stop_entry_only", "stop_exit_only", "platform_entry_only", "platform_exit_only"):
                sira += 1
                self.hat_durak_rows.append((hat_id, sira, {"n": "node", "w": "way", "r": "relation"}[m.type], m.ref))


def build(pbf: str, out: str) -> None:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    h = PoiHandler(conn)
    print(f"PBF işleniyor: {pbf} ({os.path.getsize(pbf) / 1e6:.0f} MB)")
    h.apply_file(pbf, locations=True, idx="flex_mem")
    h.flush()
    conn.executemany("INSERT INTO hat VALUES (?,?,?,?,?,?,?,?,?)", h.hat_rows)
    conn.executemany("INSERT OR IGNORE INTO hat_durak VALUES (?,?,?,?)", h.hat_durak_rows)
    conn.commit()
    print("indeksler…")
    conn.executescript("""
        INSERT INTO poi_rtree SELECT id, lat, lat, lon, lon FROM poi;
        CREATE INDEX idx_poi_kat ON poi (kategori, alt_kategori);
        CREATE INDEX idx_poi_alt_lat ON poi (alt_kategori, lat, lon);  -- kategori-özel yakınlık: kategori → enlem bandı
        CREATE INDEX idx_poi_marka ON poi (marka);
        CREATE INDEX idx_poi_osm ON poi (osm_tip, osm_id);
        CREATE INDEX idx_poi_adnorm ON poi (ad_norm);
        CREATE INDEX idx_hat_durak_durak ON hat_durak (durak_osm_tip, durak_osm_id);
    """)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO kapsama SELECT kategori, alt_kategori, COUNT(*), ? FROM poi GROUP BY 1, 2", (now,))
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
        ("kaynak", "OpenStreetMap (Geofabrik turkey-latest.osm.pbf) — © OpenStreetMap katkıcıları, ODbL"),
        ("pbf_mtime", datetime.fromtimestamp(os.path.getmtime(pbf), timezone.utc).isoformat()),
        ("islenme", now), ("poi_sayisi", str(h.n)), ("hat_sayisi", str(len(h.hat_rows))),
    ])
    conn.commit()
    conn.execute("ANALYZE"); conn.execute("VACUUM"); conn.close()
    os.replace(tmp, out)
    print(f"alan hatası: {h.alan_hata:,}")
    print(f"tamam → {out} ({os.path.getsize(out) / 1e6:.0f} MB), poi={h.n:,}, hat={len(h.hat_rows):,}, hat_durak={len(h.hat_durak_rows):,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", default=DEFAULT_PBF)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    if not os.path.exists(a.pbf):
        print("PBF yok:", a.pbf); return 1
    build(a.pbf, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
