#!/usr/bin/env python3
"""Kamu tesisleri ambarı v1 — hastane, ağız-diş merkezi, okul, üniversite (staging parquet).

Girdi (ham kökler değiştirilmez):
  - Sağlık Bakanlığı KHGM "2. ve 3. Basamak Kamu Sağlık Tesisleri Güncel Listesi" (4 sürüm, 2022-06 → 2022-12;
    GEOPROP_RAW_INTAKE/khgm_saglik_tesisleri/<tarih>/tesisler_uzun.parquet) — kurum kodu, il/ilçe, tür, rol,
    tescilli yatak, ünite, EAH, üniversite protokolü, DETSİS. KOORDİNAT YOK.
  - Konum adayları: OSM Türkiye (osm_pbf_katmanlar/2026-09-24/egitim_saglik_kamu.parquet, amenity=hospital/clinic)
    ve kanonik POI (Google kaynaklı, adında "hastane" geçen, coord_validity='valid').
  - MEB okulları (GEOPROP/warehouse/product/resmi_egitim.sqlite::okul — MEB okul sayfaları, 2026-09-13)
  - YÖK üniversite öğrenci (universite.sqlite::universite, T102 2025-26), kampüs (OSM alanı), öğretim elemanı
    (yok_istatistik/<tarih>/ogretim_elemani_universite_bazinda_2025_2026.csv)

Çıktı: GEOPROP_CONSOLIDATION/staging/v1.0.0/public_facilities/kamu_v1/<tablo>.parquet

Konum eşleştirme (hastane): ad sadeleştirilir (Türkçe harf katlama, "T.C. Sağlık Bakanlığı" ve noktalama atılır),
aynı ildeki aday noktalarla karşılaştırılır. Puan = 0,5 × kelime örtüşmesi (Jaccard) + 0,5 × karakter benzerliği
(difflib oranı) + 0,1 aynı ilçe bonusu. Eşik 0,62; en iyi aday ile ikinci aday arası fark < 0,03 ise ve ikisi
300 m'den uzaksa "belirsiz" sayılır, konum verilmez. konum_kaynagi sütunu:
  osm_ad_eslesmesi / google_ad_eslesmesi ... aday noktanın koordinatı (eslesme_skoru, eslesen_ad ile)
  geocode_ad_ilce_il ........................ Photon/Nominatim, ad + ilçe + il (yaklaşık)
  ilce_merkezi_yaklasik ..................... hiçbiri tutmadı; ilçe ağırlık merkezi (kural 5: boş bırakma, işaretle)
"""
from __future__ import annotations

import difflib
import glob
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import fill_admin_nearest, now_iso  # noqa: E402

HOME = Path.home()
RAW = HOME / "Desktop/GEOPROP_RAW_INTAKE"
WH = HOME / "Desktop/GEOPROP/warehouse/product"
CANON = HOME / "Desktop/GEOPROP_CONSOLIDATION/canonical/v1.1/geoprop_canonical_v1_1.duckdb"
OUT = HOME / "Desktop/GEOPROP_CONSOLIDATION/staging/v1.0.0/public_facilities/kamu_v1"
OSM_LAYER = RAW / "osm_pbf_katmanlar/2026-09-24/egitim_saglik_kamu.parquet"
BUILT_AT = now_iso()

_TR = str.maketrans({"İ": "i", "I": "ı", "Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u"})
_FOLD = str.maketrans("çğıöşü", "cgiosu")
_BOILER = re.compile(r"\b(t ?c|saglik bakanligi|ozel)\b")
_ABBR = [(r"\bdh\b", "devlet hastanesi"), (r"\beah\b", "egitim ve arastirma hastanesi"),
         (r"\bhast\b", "hastanesi"), (r"\bhst\b", "hastanesi"), (r"\bdr\b", "doktor")]


def fold(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s)).translate(_TR).lower().translate(_FOLD)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core(name: str) -> str:
    s = fold(name)
    for a, b in _ABBR:
        s = re.sub(a, b, s)
    s = _BOILER.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# Tür/nitelik kelimeleri: kurumu ayırt etmez ("Göle Devlet Hastanesi" ≠ "Ardahan Devlet Hastanesi")
TYPE_WORDS = set("""devlet hastanesi hastane hastaneleri ilce egitim ve arastirma sehir entegre bolge agiz dis sagligi
merkezi merkez hastaliklari ruh akil cocuk kadin dogum gogus fizik fiziksel tedavi tip rehabilitasyon onkoloji kalp
damar goz kemik ortopedi travmatoloji meslek universitesi universite uygulama doktor dr prof op sehit piyade cavus
yerleskesi kampus ek bina binasi semt poliklinigi poliklinik acil hospital""".split())

# Tür sınıfı: farklı sınıftaki kurumlar eşleşemez (ağız-diş ≠ şehir hastanesi, ruh sağlığı ≠ genel)
_CLASSES = [("agiz_dis", ("agiz dis", "adsm", "dis hastanesi", "dis sagligi")), ("ruh", ("ruh sagligi", "akil")),
            ("fizik", ("fizik tedavi", "fiziksel tip", "rehabilitasyon")), ("gogus", ("gogus",)),
            ("kadin_cocuk", ("kadin dogum", "kadin ve cocuk", "dogumevi", "cocuk")), ("onkoloji", ("onkoloji",)),
            ("goz", ("goz ",)), ("kalp", ("kalp damar", "kalp merkezi")), ("birinci_basamak",
            ("aile sagligi", "toplum sagligi", "saglik ocagi", "semt poliklinigi", "ilce saglik mudurlugu"))]
_REJECT = re.compile(r"\b(ozel|eski|veteriner|eczane|laboratuvar|kizilay|vakif)\b")


def type_class(name: str) -> str:
    s = fold(name) + " "
    return next((c for c, keys in _CLASSES if any(k in s for k in keys)), "genel")


def split_tokens(name: str, il_f: str) -> tuple[set, set]:
    toks = [t for t in core(name).split() if t not in il_f.split()]
    return {t for t in toks if t not in TYPE_WORDS and len(t) > 1}, {t for t in toks if t in TYPE_WORDS}


def fuzzy_f1(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    m = sum(1 for x in a if any(x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.85 for y in b))
    return 2 * m / (len(a) + len(b))


def jacc(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def hav_m(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742000 * np.arcsin(np.sqrt(a))


# --------------------------------------------------------------------------------------------- hastane
def khgm_wide() -> pd.DataFrame:
    files = sorted(glob.glob(str(RAW / "khgm_saglik_tesisleri/*/tesisler_uzun.parquet")))
    long = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    wide = long.pivot_table(index=["liste_tarihi", "sayfa", "satir_no"], columns="sutun", values="deger",
                            aggfunc="first").reset_index()
    wide["KURUM KODU"] = wide["KURUM KODU"].fillna(wide.pop("KURUM KODU 1"))  # ADSM sayfasında başlık farklı
    ren = {"KURUM KODU": "kurum_kodu", "İL": "il", "İLÇE": "ilce",
           "KURUM ADI": "kurum_adi", "E.A.H": "egitim_arastirma", "KURUM TÜRÜ": "kurum_turu",
           "Tescil Edilen Ünit Sayısı": "tescilli_unite_sayisi", "Tescil Edilen Rolü": "tescilli_rol",
           "Tescil Edilen Yatak Sayısı": "tescilli_yatak_sayisi",
           "BİRLİKTE KULLANIM PROTOKOLU YAPILAN ÜNİVERSİTENİN ADI": "birlikte_kullanim_universite",
           "DETSİS KODU": "detsis_kodu", "SIRA NO": "sira_no"}
    wide = wide.rename(columns=ren)
    wide = wide[[c for c in wide.columns if not str(c).startswith("bos_")]]
    wide = wide[wide["kurum_adi"].notna() & wide["il"].notna()].copy()  # alt bilgi/boş satırlar
    for c in ("kurum_kodu", "tescilli_yatak_sayisi", "tescilli_unite_sayisi", "detsis_kodu", "sira_no"):
        wide[c] = pd.to_numeric(wide[c].astype(str).str.replace(r"\.0$", "", regex=True).str.strip(),
                                errors="coerce").astype("Int64")
    for c in ("il", "ilce", "kurum_adi", "kurum_turu", "tescilli_rol", "egitim_arastirma", "birlikte_kullanim_universite"):
        wide[c] = wide[c].astype("string").str.strip()
    wide["tesis_grubu"] = np.where(wide["sayfa"].str.upper().str.contains("ADSM"), "agiz_dis_sagligi_merkezi", "hastane")
    wide["egitim_arastirma"] = wide["egitim_arastirma"].notna()
    return wide


def location_candidates() -> pd.DataFrame:
    osm = duckdb.sql(f"""
        select 'osm' kaynak, osm_type || '/' || osm_id aday_id, name ad, lat, lon, il_adi il, ilce_adi ilce
        from '{OSM_LAYER}'
        where json_extract_string(tags_json,'$.amenity') in ('hospital','clinic','dentist')
          and name is not null and lat is not null""").df()
    con = duckdb.connect(str(CANON), read_only=True)
    g = con.sql("""
        select 'google' kaynak, p.poi_id::varchar aday_id, p.name ad, p.lat, p.lon, il.name il, ic.name ilce
        from poi p
        left join geo_entity ic on ic.geo_id = p.assigned_ilce_geo_id
        left join geo_entity il on il.geo_id = ic.il_geo_id
        where p.coord_validity = 'valid' and p.primary_source <> 'osm'
          and regexp_matches(lower(p.name), 'hastane|ağız ve diş|agiz ve dis|adsm')""").df()
    con.close()
    c = pd.concat([osm, g], ignore_index=True)
    c = c[~c["ad"].map(lambda a: bool(_REJECT.search(fold(a))))].reset_index(drop=True)
    c["il_f"], c["ilce_f"] = c["il"].map(fold), c["ilce"].map(fold)
    c["sinif"] = c["ad"].map(type_class)
    c["univ"] = c["ad"].map(lambda a: "universite" in fold(a))
    tt = [split_tokens(a, i) for a, i in zip(c["ad"], c["il_f"])]
    c["dist"], c["typ"] = [t[0] for t in tt], [t[1] for t in tt]
    return c


def match_locations(h: pd.DataFrame, cand: pd.DataFrame) -> pd.DataFrame:
    """Puan = 0,6 × ayırt edici kelime benzerliği (bulanık F1) + 0,25 × tür kelimesi örtüşmesi + 0,15 × aynı ilçe.
    Kabul: puan ≥ 0,6 VE (aynı ilçe VEYA ayırt edici benzerlik ≥ 0,8). Belirsiz: ikinci aday 0,03 içinde,
    farklı ayırt edici adlı ve 300 m'den uzak."""
    by_il = {k: v.reset_index(drop=True) for k, v in cand.groupby("il_f")}
    out = []
    for r in h.itertuples():
        il_f, ilce_f = fold(r.il), fold(r.ilce)
        dh, th = split_tokens(r.kurum_adi, il_f)
        pool = by_il.get(il_f)
        rec = {"kurum_kodu": r.kurum_kodu, "tesis_grubu": r.tesis_grubu}
        if pool is None or pool.empty:
            out.append(rec | {"konum_kaynagi": None})
            continue
        same = pool["ilce_f"].values == ilce_f
        tok = np.array([fuzzy_f1(dh, d) for d in pool["dist"]])
        s = 0.6 * tok + 0.25 * np.array([jacc(th, t) for t in pool["typ"]]) + 0.15 * same
        # üniversite hastanesi ≠ Bakanlık tesisi (adında üniversite geçmeyen kuruma üniversite adayı verilmez)
        ok = ((s >= 0.6) & (same | (tok >= 0.8)) & (pool["sinif"].values == type_class(r.kurum_adi))
              & ~(pool["univ"].values & ("universite" not in fold(r.kurum_adi))))
        s = np.where(ok, s, -1.0)
        order = np.argsort(-s)
        best = order[0]
        second = order[1] if len(order) > 1 else None
        amb = bool(second is not None and s[second] > 0 and s[best] - s[second] < 0.03
                   and pool["dist"][best] != pool["dist"][second]
                   and hav_m(pool.lat[best], pool.lon[best], pool.lat[second], pool.lon[second]) > 300)
        if s[best] > 0 and not amb:
            b = pool.iloc[best]
            rec |= {"lat": b.lat, "lon": b.lon, "konum_kaynagi": f"{b.kaynak}_ad_eslesmesi",
                    "eslesme_skoru": round(float(s[best]), 3), "eslesen_ad": b.ad, "eslesen_aday_id": b.aday_id}
        else:
            rec |= {"konum_kaynagi": "belirsiz" if amb else None,
                    "eslesme_skoru": round(float(s[best]), 3) if s[best] > 0 else None,
                    "en_yakin_aday_ad": pool.ad[best] if s[best] > 0 else None}
        out.append(rec)
    return pd.DataFrame(out)


GEOCACHE = OUT / "_geocode_cache.json"


def geocode_fallback(cur: pd.DataFrame) -> pd.DataFrame:
    """Eşleşmeyenler: Photon/Nominatim (ad + ilçe + il; il/ilçe zinciri doğrulanır) → yoksa ilçe merkezi."""
    sys.path.insert(0, str(HOME / "Desktop/endeks3"))
    from collector.geocode import adres_kodla  # noqa: E402
    cache = json.loads(GEOCACHE.read_text()) if GEOCACHE.exists() else {}
    miss = cur["lat"].isna()
    for i in cur.index[miss]:
        r = cur.loc[i]
        ad = re.sub(r"^\s*T\.?\s*C\.?\s*Sağlık Bakanlığı\s*", "", r.kurum_adi, flags=re.I).strip()
        key = f"{r.il}|{r.ilce}|{ad}"
        if key not in cache:
            hit = adres_kodla(None, r.il, None if str(r.ilce).upper() == "MERKEZ" else r.ilce, kurum_adi=ad)
            cache[key] = list(hit) if hit else None
            GEOCACHE.write_text(json.dumps(cache, ensure_ascii=False))
        if cache[key]:
            cur.loc[i, ["lat", "lon"]] = cache[key][:2]
            cur.loc[i, "konum_kaynagi"] = "geocode_ad_ilce_il"
            cur.loc[i, "konum_notu"] = cache[key][2]
    # son çare: ilçe ağırlık merkezi (kanonik geo_entity, ad eşleşmesi il içinde)
    con = duckdb.connect(str(CANON), read_only=True)
    ge = con.sql("""select ic.name ilce, il.name il, ic.centroid_lat lat, ic.centroid_lon lon
                    from geo_entity ic join geo_entity il on il.geo_id = ic.il_geo_id
                    where ic.level = 'ilce' and il.level = 'il'""").df()
    con.close()
    ge["k"] = ge["il"].map(fold) + "|" + ge["ilce"].map(fold)
    cen = ge.drop_duplicates("k").set_index("k")
    for i in cur.index[cur["lat"].isna()]:
        r = cur.loc[i]
        k = fold(r.il) + "|" + fold(r.ilce)
        if k in cen.index:
            cur.loc[i, ["lat", "lon"]] = cen.loc[k, ["lat", "lon"]].values
            cur.loc[i, "konum_kaynagi"] = "ilce_merkezi_yaklasik"
    return cur


def build_hastane():
    wide = khgm_wide()
    latest = wide["liste_tarihi"].max()
    cur = wide[wide["liste_tarihi"] == latest].drop_duplicates(["tesis_grubu", "kurum_kodu"]).copy()
    # sürüm geçmişi: her kurumun listede ilk/son görüldüğü tarih, yatak ve rol değişimi
    hist = (wide.sort_values("liste_tarihi")
            .groupby(["tesis_grubu", "kurum_kodu"])
            .agg(ilk_liste=("liste_tarihi", "first"), son_liste=("liste_tarihi", "last"),
                 liste_sayisi=("liste_tarihi", "nunique"),
                 yatak_ilk=("tescilli_yatak_sayisi", "first"), rol_ilk=("tescilli_rol", "first"))
            .reset_index())
    cur = cur.merge(hist, on=["tesis_grubu", "kurum_kodu"], how="left")
    cand = location_candidates()
    loc = match_locations(cur, cand)
    cur = cur.merge(loc, on=["kurum_kodu", "tesis_grubu"], how="left")
    cur["konum_notu"] = None
    cur = geocode_fallback(cur)
    cur["kaynak"] = "Sağlık Bakanlığı KHGM 2. ve 3. Basamak Kamu Sağlık Tesisleri Güncel Listesi"
    cur["veri_tarihi"] = cur["liste_tarihi"]
    cur["guncellenme_tarihi"] = BUILT_AT
    return cur, wide


# --------------------------------------------------------------------------------------------- ortak
def assign_admin(df: pd.DataFrame) -> pd.DataFrame:
    """lat/lon → kanonik mahalle/ilçe/il kimliği (poligon içi; dışarıda kalan → en yakın mahalle, işaretli)."""
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(f"attach '{CANON}' as k (read_only)")
    con.sql("create temp table mah as select geo_id, name, parent_geo_id, il_geo_id, geometry from k.geo_entity "
            "where level='mahalle' and geometry is not null")
    pts = df[["lat", "lon"]].reset_index().rename(columns={"index": "_rid"})
    con.register("pts", pts)
    j = con.sql("""select p._rid, m.geo_id mahalle_geo_id, m.name mahalle_adi, m.parent_geo_id ilce_geo_id, m.il_geo_id
                   from pts p join mah m on st_contains(m.geometry, st_point(p.lon, p.lat))
                   where p.lat is not null""").df().drop_duplicates("_rid").set_index("_rid")
    names = con.sql("select geo_id, name from k.geo_entity where level in ('il','ilce')").df().set_index("geo_id")["name"]
    con.close()
    df = df.drop(columns=[c for c in ("mahalle_geo_id", "mahalle_adi", "ilce_geo_id", "il_geo_id") if c in df], errors="ignore")
    df = df.join(j)
    df = fill_admin_nearest(df)
    df["ilce_adi_geo"] = df["ilce_geo_id"].map(names)
    df["il_adi_geo"] = df["il_geo_id"].map(names)
    return df


# --------------------------------------------------------------------------------------------- okul
def build_okul() -> pd.DataFrame:
    """MEB okul sayfaları (resmi_egitim.sqlite::okul; öğretmen/öğrenci/derslik okulun kendi 'Okulumuz Hakkında'
    sayfasından, koordinat okul sitesinin harita sayfasından). Değer dönüşümü yok; yalnız idari kimlik eklenir."""
    c = sqlite3.connect(f"file:{WH / 'resmi_egitim.sqlite'}?mode=ro", uri=True)
    d = pd.read_sql("select * from okul", c)
    c.close()
    d = d.rename(columns={"ad": "okul_adi", "tur": "okul_turu", "ogretmen": "ogretmen_sayisi",
                          "ogrenci": "ogrenci_sayisi", "derslik": "derslik_sayisi",
                          "guncellenme": "koordinat_cekim_zamani", "istatistik_guncellenme": "istatistik_cekim_zamani"})
    d["meb_sayfa_url"] = "https://" + d["host"] + ".meb.k12.tr"
    d["ogrenci_per_ogretmen"] = (d["ogrenci_sayisi"] / d["ogretmen_sayisi"].where(d["ogretmen_sayisi"] > 0)).round(2)
    d["ogrenci_per_derslik"] = (d["ogrenci_sayisi"] / d["derslik_sayisi"].where(d["derslik_sayisi"] > 0)).round(2)
    d = assign_admin(d)
    d["kaynak"] = "MEB okul siteleri (meb.gov.tr okullar listesi + <okul>.meb.k12.tr okulumuz_hakkinda / harita)"
    d["guncellenme_tarihi"] = BUILT_AT
    return d


# --------------------------------------------------------------------------------------------- üniversite
def build_universite() -> tuple[pd.DataFrame, pd.DataFrame]:
    c = sqlite3.connect(f"file:{WH / 'universite.sqlite'}?mode=ro", uri=True)
    ogr = pd.read_sql("select * from universite", c)
    kampus = pd.read_sql("select * from kampus", c)
    urap = pd.read_sql("select * from urap", c)
    c.close()
    oe_f = sorted(glob.glob(str(RAW / "yok_istatistik/*/ogretim_elemani_universite_bazinda_2025_2026.csv")))[-1]
    oe = pd.read_csv(oe_f)
    oe = oe[oe["universite"] != "TOPLAM"].rename(columns={"universite_turu": "tur_oe", "il": "il_oe"})
    oe = oe.rename(columns={c: "oe_" + c for c in oe.columns if c.endswith(("_e", "_k", "_t"))})  # öğretim elemanı
    ogr = ogr.rename(columns={"toplam_e": "ogrenci_toplam_e", "toplam_k": "ogrenci_toplam_k", "toplam_t": "ogrenci_toplam_t"})
    oe["k"] = oe["universite"].map(fold)
    ogr["k"] = ogr["ad"].map(fold)
    u = ogr.merge(oe, on="k", how="outer", indicator="eslesme")
    u["ad"] = u["ad"].fillna(u["universite"])
    u["il"] = u["il"].fillna(u["il_oe"])
    u["tur"] = u["tur"].fillna(u["tur_oe"])
    u["eslesme"] = u["eslesme"].map({"both": "ogrenci+personel", "left_only": "yalniz_ogrenci",
                                      "right_only": "yalniz_personel"})
    u["ogrenci_per_ogretim_elemani"] = (u["ogrenci_toplam_t"] / u["oe_toplam_t"].where(u["oe_toplam_t"] > 0)).round(2)
    u = u.drop(columns=["k", "universite", "il_oe", "tur_oe"])
    u["kaynak"] = "YÖK T102 öğrenci (2025-26) + istatistik.yok.gov.tr üniversite bazında öğretim elemanı (2025-26)"
    u["guncellenme_tarihi"] = BUILT_AT
    if "ad" in urap:
        urap["k"] = urap["ad"].map(fold)
    kampus["guncellenme_tarihi"] = BUILT_AT
    return u, kampus


# --------------------------------------------------------------------------------------------- SGK
SGK_2025 = RAW / "sgk_istatistik/2026-09-24/ekler/download_downloadfile_cebae149-a5ac-41ba-8a5a-cbaf2af17dcb.zip"


def build_sgk() -> tuple[pd.DataFrame, pd.DataFrame]:
    """SGK İstatistik Yıllığı 2025, Bölüm 1: Tablo 1.29 (4/1-c memur, il × cinsiyet, 2016–2025) ve Tablo 1.11
    (4/1-a zorunlu sigortalı ve iş yeri, il × NACE Rev.2.1 faaliyet grubu, 2025)."""
    import io
    import zipfile
    z = zipfile.ZipFile(SGK_2025)
    b = z.read(next(n for n in z.namelist() if "BÖLÜM 1" in n))
    t = pd.read_excel(io.BytesIO(b), sheet_name="TABLO-1.29", header=None)
    years = t.iloc[5].ffill()
    rows = []
    for _, r in t.iloc[7:].iterrows():
        if pd.isna(r[1]):
            continue
        for j in range(2, t.shape[1]):
            if pd.isna(r[j]):
                continue
            cins = str(t.iloc[6, j]).split("\n")[0].strip().lower()
            rows.append({"il_kodu": str(r[0]).zfill(2), "il": str(r[1]).strip(), "yil": int(years[j]),
                         "cinsiyet": {"erkek": "E", "kadın": "K", "toplam": "T"}[cins], "memur_4c_aktif": int(r[j])})
    c4 = pd.DataFrame(rows)
    t = pd.read_excel(io.BytesIO(b), sheet_name="TABLO-1.11", header=None)
    il_row = t.iloc[5].ffill()
    rows = []
    for _, r in t.iloc[8:].iterrows():
        kod, ad = r[0], r[1]
        if pd.isna(ad) and not str(kod).startswith("Ek-9"):
            continue
        for j in range(2, t.shape[1], 2):
            il = str(il_row[j]).strip()
            if fold(il).startswith(("toplam", "genel toplam")):
                continue
            rows.append({"faaliyet_kodu": str(kod).strip(), "faaliyet_adi": str(ad if pd.notna(ad) else kod).strip(),
                         "il": il, "is_yeri": pd.to_numeric(r[j], errors="coerce"),
                         "sigortali_4a": pd.to_numeric(r[j + 1], errors="coerce"), "yil": 2025})
    a4 = pd.DataFrame(rows).dropna(subset=["is_yeri", "sigortali_4a"], how="all")
    kod = c4.drop_duplicates("il").assign(f=lambda x: x["il"].map(fold)).set_index("f")["il_kodu"]
    a4["il_kodu"] = a4["il"].map(fold).map(kod)  # yazım farkı (Hakkari/Hakkarı) plaka koduyla giderilir
    assert a4["il_kodu"].notna().all(), a4.loc[a4["il_kodu"].isna(), "il"].unique()
    for d in (c4, a4):
        d["kaynak"] = "SGK İstatistik Yıllığı 2025, Bölüm 1 (" + ("Tablo 1.29" if d is c4 else "Tablo 1.11") + ")"
        d["guncellenme_tarihi"] = BUILT_AT
    return c4, a4


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    if not only or "hastane" in only:
        cur, wide = build_hastane()
        wide.to_parquet(OUT / "kamu_saglik_tesisi_liste_surumleri.parquet", index=False)
        cur = assign_admin(cur)
        cur.to_parquet(OUT / "kamu_saglik_tesisi.parquet", index=False)
        print(cur.groupby(["tesis_grubu", "konum_kaynagi"], dropna=False).size())
    if not only or "okul" in only:
        o = build_okul()
        o.to_parquet(OUT / "meb_okul.parquet", index=False)
        print("okul", len(o), o["admin_match"].value_counts(dropna=False).to_dict())
    if not only or "universite" in only:
        u, k = build_universite()
        u.to_parquet(OUT / "universite.parquet", index=False)
        k.to_parquet(OUT / "universite_kampus.parquet", index=False)
        print("universite", u["eslesme"].value_counts().to_dict())
    if not only or "sgk" in only:
        c4, a4 = build_sgk()
        c4.to_parquet(OUT / "sgk_memur_4c_il.parquet", index=False)
        a4.to_parquet(OUT / "sgk_4a_il_faaliyet.parquet", index=False)
        print("sgk 4c", len(c4), "4a", len(a4))


if __name__ == "__main__":
    main()
