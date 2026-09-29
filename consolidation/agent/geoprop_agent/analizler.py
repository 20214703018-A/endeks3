"""Analiz kataloğu — her analiz sabit, kurallı bir hesaptır. Yapay zekâ burada yoktur.

Her fonksiyon: (plan modeli, Baglam) → Sonuc. Belirsizlik/veri yokluğu AnalizHatasi ile bildirilir (tahmin yok).
Sıralamalar her zaman tam bağ kırıcı içerir (değer, sonra geo_id) → aynı girdi aynı sırayı verir.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from . import plan as P
from . import veri
from .ontology import SEVIYE_ADI, Olcu, yukle
from .sonuc import AnalizHatasi, Sonuc, Sutun, Tablo
from .textnorm import fold, ilk_kucuk, tr_sayi
from .yer import Nokta, Yer, dizin

KEY_HARITA = ["okul", "ilkokul", "ortaokul", "lise", "anaokulu", "universite", "hastane", "saglik_ocagi", "eczane",
              "otobus_duragi", "metro_istasyonu", "tramvay_duragi", "tren_istasyonu", "otopark", "park", "cami",
              "banka", "atm", "market", "avm", "otel"]
DONEM_DESEN = {"ay": r"^\d{4}-\d{2}$", "yil": r"^\d{4}$", "ceyrek": r"^\d{4}-Q\d$"}
SIRA = {"mahalle": 3, "ilce": 2, "il": 1, "ulke": 0}


# =============================================================================================== bağlam
class Baglam:
    """Bir analiz koşusunun iz defteri: sorgular, uyarılar, kullanılan kaynaklar ve kurallar."""

    def __init__(self):
        self.sorgular: list[str] = []
        self.uyarilar: list[str] = []
        self.kaynaklar: set[str] = set()
        self.edinim: set[str] = set()
        self.kurallar: set[str] = set()
        self.donemler: dict[str, str] = {}

    def uyar(self, m: str):
        if m not in self.uyarilar:
            self.uyarilar.append(m)

    def q(self, sql: str, params=None) -> list[dict]:
        self.sorgular.append(re.sub(r"\s+", " ", sql).strip())
        return veri.q(sql, params)

    def iz(self, satirlar: list[dict]):
        for r in satirlar:
            if r.get("kaynaklar"):
                self.kaynaklar.update(str(r["kaynaklar"]).split(","))
            if r.get("edinim"):
                self.edinim.add(str(r["edinim"]))
            if r.get("celiski"):
                self.kurallar.add("tekillestirme")


def _sonuc(analiz: str, plan: dict, b: Baglam, tablolar, cumleler, yontem: str) -> Sonuc:
    ont = yukle()
    kunye = {"yöntem": yontem}
    if b.donemler:
        kunye["dönem"] = "; ".join(f"{k}: {v}" for k, v in sorted(b.donemler.items()))
    if b.kaynaklar:
        kunye["kaynak tablolar"] = ", ".join(sorted(b.kaynaklar))
    if b.edinim:
        kunye["edinim sınıfı"] = ", ".join(sorted(b.edinim))
    if b.kurallar:
        kunye["uygulanan kurallar"] = ", ".join(sorted(k for k in b.kurallar if k in ont.kurallar))
    m = _meta()
    kunye["eşleşme durumu"] = m.get("mapping_status")
    return Sonuc(analiz=analiz, plan=plan, tablolar=tablolar, cumleler=cumleler, uyarilar=b.uyarilar,
                 kunye=kunye, sorgular=b.sorgular)


@lru_cache(maxsize=1)
def _meta() -> dict:
    return veri.meta()


# =============================================================================================== çözücüler
def yer_coz(metin: str, b: Baglam, seviye: str | None = None) -> Yer | Nokta:
    c = dizin().coz(metin, seviye)
    if c.durum != "kesin":
        raise AnalizHatasi(" ".join(c.uyarilar) or f"'{metin}' çözülemedi", [a.sozluk() for a in c.adaylar],
                           tur="belirsiz" if c.durum == "belirsiz" else "bulunamadi")
    for u in c.uyarilar:
        b.uyar(u)
    return c.yer


def yer_birim(metin: str, b: Baglam) -> Yer:
    """Nokta verilirse içindeki mahalleye iner."""
    y = yer_coz(metin, b)
    if isinstance(y, Nokta):
        if not y.mahalle:
            raise AnalizHatasi("Nokta bir mahalleye düşmüyor; yer adıyla deneyin.")
        b.uyar(f"Nokta ({y.lat}, {y.lon}) → içinde bulunduğu mahalle: {y.mahalle.tam_ad}")
        return y.mahalle
    return y


def olcu_coz(metin: str) -> Olcu:
    ont = yukle()
    oid, adaylar = ont.olcu_bul(metin)
    if not oid:
        raise AnalizHatasi(f"'{metin}' ontolojide kesin bir ölçüye karşılık gelmiyor.",
                           [{"id": a, "ad": ont.olcu(a).ad, "birim": ont.olcu(a).birim} for a in adaylar], tur="belirsiz")
    return ont.olcu(oid)


@lru_cache(maxsize=1)
def _kategori_listesi() -> dict[str, str]:
    rows = veri.q("SELECT DISTINCT predicted_category AS k FROM k.main.poi WHERE predicted_category IS NOT NULL ORDER BY 1")
    return {fold(r["k"]): r["k"] for r in rows}


@lru_cache(maxsize=64)
def _boyut_degerleri(oid: str) -> tuple[str, ...]:
    o = yukle().olcu(oid)
    rows = veri.q(f"SELECT DISTINCT boyut FROM ({veri.ham_sql(o, o.seviyeler[0])}) WHERE boyut IS NOT NULL ORDER BY 1")
    return tuple(r["boyut"] for r in rows)


def sektor_coz(metin: str | None) -> str | None:
    if not metin:
        return None
    s = yukle().sektor_dizini.get(fold(metin))
    if not s:
        raise AnalizHatasi(f"'{metin}' bir işletme sektörü değil.", sorted(yukle().sektorler), tur="belirsiz")
    return s


def kategori_coz(metin: str | None) -> str | None:
    if not metin:
        return None
    kl = _kategori_listesi()
    f = fold(metin)
    if f in kl:
        return kl[f]
    aday = sorted(v for k, v in kl.items() if f in k or k in f)
    if len(aday) == 1:
        return aday[0]
    raise AnalizHatasi(f"'{metin}' kesin bir işletme kategorisine karşılık gelmiyor.", aday[:15] or sorted(kl.values())[:40],
                       tur="belirsiz")


def boyut_coz(o: Olcu, metin: str | None) -> str | None:
    if not metin:
        return None
    if o.boyut == "sektor|kategori":
        if ":" in metin:
            t, _, v = metin.partition(":")
            return f"sektor:{sektor_coz(v)}" if t == "sektor" else f"kategori:{kategori_coz(v)}"
        if fold(metin) in yukle().sektor_dizini:
            return f"sektor:{sektor_coz(metin)}"
        return f"kategori:{kategori_coz(metin)}"
    if o.boyut == "osm_grup":
        gruplar = ("all", "yeme_icme", "perakende", "hizmet", "saglik", "egitim", "konaklama", "sanayi", "diger")
        f = fold(metin).replace(" ", "_")
        if f in gruplar:
            return f
        raise AnalizHatasi(f"'{metin}' bir OSM kategori grubu değil.", list(gruplar), tur="belirsiz")
    if o.boyut == "osm_kategori":
        k = yukle().harita_dizini.get(fold(metin))
        if not k:
            raise AnalizHatasi(f"'{metin}' bir harita nesnesi türü değil.", sorted(yukle().harita_esanlam), tur="belirsiz")
        return k
    if o.boyut:
        degerler = _boyut_degerleri(o.id)
        esl = [d for d in degerler if fold(d) == fold(metin)] or [d for d in degerler if fold(metin) in fold(d)]
        if len(esl) == 1:
            return esl[0]
        raise AnalizHatasi(f"'{metin}' bu ölçünün alt kırılımlarından birine kesin karşılık gelmiyor.", list(esl or degerler),
                           tur="belirsiz")
    raise AnalizHatasi(f"'{o.ad}' ölçüsünün alt kırılımı (boyut) yok.")


def _ad(gid: str) -> str:
    y = dizin().yerler.get(gid)
    return y.tam_ad if y else gid


def _ust(y: Yer) -> list[Yer]:
    return [dizin().yerler[g] for g in dizin().atalar(y)]


def fmt(v, birim: str | None) -> str:
    if v is None:
        return "veri yok"
    if birim == "%":
        return "%" + tr_sayi(v, 2)
    if birim == "‰":
        return "‰" + tr_sayi(v, 2)
    ond = 0 if birim in ("adet", "kişi", "daire", "hane", "TL", "TL/m²", "m²") and abs(v) >= 100 else None
    return f"{tr_sayi(v, ond)} {birim}".strip()


# =============================================================================================== ortak veri
def _kural_notlari(o: Olcu, b: Baglam, projeksiyon: bool = False):
    if o.kaynak.get("tablo") == "price_observation":
        b.kurallar.add("projeksiyon_haric" if not projeksiyon else "projeksiyon_haric")
        if o.olcek:
            b.kurallar.add("tekillestirme")
    if o.id.startswith("harita_") and o.id != "harita_nesnesi_sayisi":
        b.kurallar.add("osm_semantigi")
    if o.id in ("isletme_sayisi", "bin_kisi_basina_isletme", "km2_basina_isletme", "ortalama_puan", "turu_belirsiz_isletme"):
        b.kurallar.update({"gecerli_koordinat", "kategori_belirsiz"})
    if o.toplanabilir is False:
        b.kurallar.add("toplanamaz")
    if o.kaynak.get("domain") == "housing_sales":
        b.kurallar.add("seri_karistirma")


def son_donem(o: Olcu, seviye: str, geo_sql: str | None, boyut: str | None, istenen: str, b: Baglam) -> str:
    """'son' → verisi olan en güncel dönem (ölçünün birincil dönem türünde). Açık dönem verilirse varlığı denetlenir."""
    ic = veri.olcum_sql(o, seviye, boyut, geo_sql)
    if istenen and istenen != "son":
        r = b.q(f"SELECT count(*) AS n FROM ({ic}) WHERE donem = ?", [istenen])
        if not r[0]["n"]:
            ara = b.q(f"SELECT min(donem) AS a, max(donem) AS z FROM ({ic})")[0]
            raise AnalizHatasi(f"'{o.ad}' için {istenen} döneminde veri yok (mevcut aralık: {ara['a']} – {ara['z']}).")
        return istenen
    desen = DONEM_DESEN.get(o.donem_turu[0]) if o.donem_turu else None
    w = f"WHERE regexp_matches(donem, '{desen}')" if desen else ""
    r = b.q(f"SELECT max(donem) AS d FROM ({ic}) {w}")
    if not r or r[0]["d"] is None:
        raise AnalizHatasi(f"'{o.ad}' ölçüsü için seçilen kapsamda ({SEVIYE_ADI[seviye]}) veri yok.")
    return r[0]["d"]


def degerler(o: Olcu, seviye: str, geo_sql: str | None, donem: str, boyut: str | None, b: Baglam,
             projeksiyon: bool = False) -> dict[str, dict]:
    rows = b.q(f"SELECT * FROM ({veri.olcum_sql(o, seviye, boyut, geo_sql, projeksiyon)}) WHERE donem = ? ORDER BY geo_id",
               [donem])
    b.iz(rows)
    cel = [r for r in rows if r["celiski"]]
    if cel:
        b.uyar(f"{o.ad}: {len(cel)} yerde kaynaklar arasında farklı değer var; tekilleştirme kuralıyla en güncel kaynak seçildi "
               f"(ör. {_ad(cel[0]['geo_id'])}: {tr_sayi(cel[0]['deger_min'])} – {tr_sayi(cel[0]['deger_max'])}).")
    return {r["geo_id"]: r for r in rows}


def _kapsam(kapsam: str | None, seviye: str, b: Baglam) -> tuple[Yer | None, str]:
    if not kapsam:
        return None, veri.kapsam_sql(seviye, None, None)
    k = yer_birim(kapsam, b)
    if SIRA[k.seviye] > SIRA[seviye]:
        raise AnalizHatasi(f"{k.tam_ad} bir {SEVIYE_ADI[k.seviye]}; içinde '{SEVIYE_ADI[seviye]}' seviyesinde birim aranamaz.")
    return k, veri.kapsam_sql(seviye, k.geo_id, k.seviye)


def _nufus_haritasi(seviye: str, geo_sql: str, b: Baglam) -> dict[str, dict]:
    rows = b.q(f"SELECT * FROM ({veri._nufus_kesit_sql(seviye)}) WHERE geo_id IN ({geo_sql})")
    return {r["geo_id"]: r for r in rows}


def _ilan_filtresi(o: Olcu, seviye: str, geo_sql: str, donem: str, min_ilan: int, b: Baglam) -> set[str] | None:
    k = o.kaynak.get("category")
    if not k:
        b.uyar("min_ilan yalnız fiyat ölçülerinde uygulanır; yok sayıldı.")
        return None
    ilan = yukle().olcu(f"{k}_ilan_sayisi")
    d = degerler(ilan, seviye, geo_sql, donem, None, b)
    b.kurallar.add("tekillestirme")
    return {g for g, r in d.items() if r["deger"] is not None and r["deger"] >= min_ilan}


# =============================================================================================== 1. yer_profili
def yer_profili(p: P.YerProfili, b: Baglam) -> Sonuc:
    ont = yukle()
    y = yer_birim(p.yer, b)
    seviyeler = [y] + _ust(y)
    ids = [s.geo_id for s in seviyeler]
    tablo = []
    fiyat = {(r["geo_id"], r["olcu_id"]): r for r in veri.fiyat_toplu(ids)}
    b.sorgular.append(f"fiyat_toplu({ids})")
    b.iz(list(fiyat.values()))
    b.kurallar.update({"projeksiyon_haric", "tekillestirme", "toplanamaz"})
    for o in ont.olculer.values():
        if o.kaynak.get("tablo") != "price_observation" or y.seviye not in o.seviyeler:
            continue
        r0 = fiyat.get((y.geo_id, o.id))
        if not r0:
            continue
        satir = {"olcu": o.id, "ad": o.ad, "birim": o.birim, "deger": r0["deger"], "donem": r0["donem"]}
        for s in seviyeler[1:]:
            rs = fiyat.get((s.geo_id, o.id))
            satir[s.seviye] = rs["deger"] if rs and rs["donem"] == r0["donem"] else None
        tablo.append(satir)

    temalar = {"demografi", "sosyoekonomi", "konut_piyasasi", "insaat", "ticaret", "cografya"}
    if y.seviye == "il":
        temalar.add("bankacilik")
    for o in ont.olculer.values():
        if o.tema not in temalar or o.boyut or y.seviye not in o.seviyeler or o.kaynak.get("tablo") == "price_observation":
            continue
        try:
            geo = ",".join(veri.lit(g) for g in ids)
            satirlar = []
            for s in seviyeler:
                if s.seviye not in o.seviyeler:
                    continue
                rows = b.q(f"""SELECT * FROM ({veri.olcum_sql(o, s.seviye, None, f"SELECT unnest([{geo}])")})
                    {"WHERE regexp_matches(donem, '" + DONEM_DESEN[o.donem_turu[0]] + "')" if o.donem_turu and o.donem_turu[0] in DONEM_DESEN else ""}
                    ORDER BY donem DESC LIMIT 1""")
                b.iz(rows)
                satirlar.append((s, rows[0] if rows else None))
        except AnalizHatasi:
            continue
        ana = next((r for s_, r in satirlar if s_.geo_id == y.geo_id), None)
        if not ana:
            continue
        satir = {"olcu": o.id, "ad": o.ad, "birim": o.birim, "deger": ana["deger"], "donem": ana["donem"]}
        for s, r in satirlar[1:]:
            satir[s.seviye] = r["deger"] if r and r["donem"] == ana["donem"] else None
        tablo.append(satir)
        if o.id == "konut_satis_adedi":
            rs = b.q(f"""SELECT sum(deger) AS t, count(*) AS n, min(donem) AS a, max(donem) AS z FROM (
                    SELECT * FROM ({veri.olcum_sql(o, y.seviye, None, f"SELECT {veri.lit(y.geo_id)}")}) ORDER BY donem DESC LIMIT 12)""")[0]
            tablo.append({"olcu": "konut_satis_adedi_12ay", "ad": f"Konut satış sayısı, son 12 ay toplamı ({rs['a']}–{rs['z']})",
                          "birim": "adet", "deger": rs["t"], "donem": rs["z"]})
    if not tablo:
        raise AnalizHatasi(f"{y.tam_ad} için ölçü bulunamadı.")

    sutunlar = [Sutun("ad", "Ölçü"), Sutun("deger", y.tam_ad), Sutun("birim", "Birim"), Sutun("donem", "Dönem")]
    sutunlar += [Sutun(s.seviye, f"{s.ad} ({SEVIYE_ADI[s.seviye]})") for s in seviyeler[1:]]
    tablolar = [Tablo("Ölçüler", sutunlar, tablo, "Üst birim sütunları yalnız aynı dönemde veri varsa doludur")]

    kd = _kategori_sayim(y, "sektor", b)
    if kd:
        tablolar.append(kd[0])
    hk = _harita_sayim(seviyeler, b)
    if hk:
        tablolar.append(hk)

    cumleler = []
    by = {r["olcu"]: r for r in tablo}
    for oid in ("nufus", "konut_satis_m2", "konut_kira_m2", "konut_brut_kira_getirisi", "konut_yillik_degisim",
                "isletme_sayisi", "ses_skoru"):
        r = by.get(oid)
        if not r:
            continue
        ek = [f"{s.ad} {fmt(r.get(s.seviye), r['birim'])}" for s in seviyeler[1:] if r.get(s.seviye) is not None]
        cumleler.append(f"{r['ad']} ({r['donem']}): {fmt(r['deger'], r['birim'])}" + (f"; {', '.join(ek)}." if ek else "."))
    b.donemler["fiyat"] = "en güncel ölçülmüş ay (projeksiyon hariç)"
    return _sonuc("yer_profili", {"analiz": "yer_profili", "yer": y.geo_id, "yer_adi": y.tam_ad}, b, tablolar, cumleler,
                  "Her ölçünün yerdeki en güncel değeri; üst birim değerleri kaynağın kendi üst seviye serisidir (alt birimlerden hesaplanmaz).")


def _kategori_sayim(y: Yer, duzey: str, b: Baglam) -> tuple[Tablo, list[dict]] | None:
    kol = "predicted_sector" if duzey == "sektor" else "predicted_category"
    belirsiz = "(sektörü belirsiz)" if duzey == "sektor" else "(türü belirsiz)"
    seviyeler = [y] + _ust(y)
    kosul = {"mahalle": "p.assigned_geo_id = ? AND p.assignment_method LIKE 'ST_Contains%'",
             "ilce": "p.assigned_ilce_geo_id = ?", "il": "gi.il_geo_id = ?", "ulke": "? IS NOT NULL"}
    toplam = {}
    sayim = {}
    for s in seviyeler:
        rows = b.q(f"""SELECT coalesce(p.{kol}, {veri.lit(belirsiz)}) AS grup, count(*) AS n
            FROM k.main.poi p LEFT JOIN k.main.geo_entity gi ON gi.geo_id = p.assigned_ilce_geo_id
            WHERE {veri.GECERLI_KOORD} AND {kosul[s.seviye]} GROUP BY 1 ORDER BY 2 DESC, 1""", [s.geo_id])
        sayim[s.geo_id] = {r["grup"]: r["n"] for r in rows}
        toplam[s.geo_id] = sum(r["n"] for r in rows)
    if not toplam[y.geo_id]:
        return None
    b.kurallar.update({"gecerli_koordinat", "kategori_belirsiz"})
    b.kaynaklar.add("k.poi")
    satirlar = []
    for grup, n in sorted(sayim[y.geo_id].items(), key=lambda kv: (kv[0] == belirsiz, -kv[1], kv[0])):
        belirli = toplam[y.geo_id] - sayim[y.geo_id].get(belirsiz, 0)
        r = {"grup": grup, "n": n, "pay": None if grup == belirsiz or not belirli else 100.0 * n / belirli}
        for s in seviyeler[1:]:
            bs = toplam[s.geo_id] - sayim[s.geo_id].get(belirsiz, 0)
            r[f"pay_{s.seviye}"] = None if grup == belirsiz or not bs else 100.0 * sayim[s.geo_id].get(grup, 0) / bs
        satirlar.append(r)
    sut = [Sutun("grup", "Sektör" if duzey == "sektor" else "Kategori"), Sutun("n", "İşletme", "adet"),
           Sutun("pay", "Pay (türü belirli içinde)", "%")] + [Sutun(f"pay_{s.seviye}", f"{s.ad} payı", "%") for s in seviyeler[1:]]
    bel = sayim[y.geo_id].get(belirsiz, 0)
    if bel:
        b.uyar(f"{y.tam_ad}: {tr_sayi(bel)} işletmenin (%{tr_sayi(100.0 * bel / toplam[y.geo_id], 1)}) {'sektörü' if duzey == 'sektor' else 'türü'} belirsiz; paylar türü belirli işletmeler içinde hesaplandı.")
    return Tablo(f"İşletmeler — {'sektör' if duzey == 'sektor' else 'kategori'} kırılımı", sut, satirlar,
                 f"Geçerli koordinatlı {tr_sayi(toplam[y.geo_id])} işletme (kesit {veri.KESIT_ISLETME})"), satirlar


def _harita_sayim(seviyeler: list[Yer], b: Baglam) -> Tablo | None:
    kosul = {"mahalle": "p.assigned_geo_id = ?", "ilce": "p.assigned_ilce_geo_id = ?", "il": "gi.il_geo_id = ?"}
    veri_ = {}
    for s in seviyeler:
        if s.seviye not in kosul:
            continue
        rows = b.q(f"""SELECT p.osm_category AS k, count(*) AS n FROM k.main.poi_lifecycle_osm p
            LEFT JOIN k.main.geo_entity gi ON gi.geo_id = p.assigned_ilce_geo_id
            WHERE p.map_status = 'aktif' AND {kosul[s.seviye]} AND p.osm_category IN ({",".join(veri.lit(x) for x in KEY_HARITA)})
            GROUP BY 1""", [s.geo_id])
        veri_[s.geo_id] = {r["k"]: r["n"] for r in rows}
    if not any(veri_.values()):
        return None
    b.kaynaklar.add("k.poi_lifecycle_osm")
    satir = [{"tur": k, **{s.seviye: veri_.get(s.geo_id, {}).get(k, 0) for s in seviyeler if s.geo_id in veri_}} for k in KEY_HARITA]
    satir = [r for r in satir if any(v for kk, v in r.items() if kk != "tur")]
    return Tablo("Harita nesneleri (OSM, aktif)", [Sutun("tur", "Tür")] + [Sutun(s.seviye, s.ad, "adet") for s in seviyeler if s.geo_id in veri_],
                 satir, f"OSM kesiti {veri.KESIT_HARITA}; OSM kapsamı bölgeye göre eksik olabilir")


# =============================================================================================== 2. karsilastir
def karsilastir(p: P.Karsilastir, b: Baglam) -> Sonuc:
    yerler = [yer_birim(x, b) for x in p.yerler]
    if len({y.geo_id for y in yerler}) != len(yerler):
        b.uyar("Aynı yer birden çok kez verildi; tekrarlar birleştirildi.")
        yerler = list({y.geo_id: y for y in yerler}.values())
    olculer = [olcu_coz(x) for x in p.olculer]
    satirlar = {y.geo_id: {"yer": y.tam_ad, "seviye": SEVIYE_ADI[y.seviye]} for y in yerler}
    sut = [Sutun("yer", "Yer"), Sutun("seviye", "Seviye")]
    cozulen = {"analiz": "karsilastir", "yerler": [y.geo_id for y in yerler], "olculer": [], "donem": {}}
    for o in olculer:
        _kural_notlari(o, b)
        boy = boyut_coz(o, p.boyut) if p.boyut and o.boyut else None
        gruplar: dict[str, list[Yer]] = {}
        for y in yerler:
            gruplar.setdefault(y.seviye, []).append(y)
        for sev, ys in sorted(gruplar.items()):
            if sev not in o.seviyeler:
                b.uyar(f"{o.ad} {SEVIYE_ADI[sev]} seviyesinde yok: {', '.join(y.tam_ad for y in ys)}")
                continue
            geo = "SELECT unnest([" + ",".join(veri.lit(y.geo_id) for y in ys) + "])"
            d = son_donem(o, sev, geo, boy, p.donem, b)
            b.donemler[f"{o.id}/{sev}"] = d
            cozulen["donem"][f"{o.id}/{sev}"] = d
            vals = degerler(o, sev, geo, d, boy, b)
            for y in ys:
                satirlar[y.geo_id][o.id] = vals.get(y.geo_id, {}).get("deger")
                if y.geo_id not in vals:
                    b.uyar(f"{y.tam_ad}: {o.ad} için {d} döneminde veri yok.")
        cozulen["olculer"].append(o.id + (f"[{boy}]" if boy else ""))
        sut.append(Sutun(o.id, o.ad + (f" [{boy}]" if boy else ""), o.birim))
    rows = [satirlar[y.geo_id] for y in yerler]
    cumleler = []
    for o in olculer:
        dolu = [(r[o.id], r["yer"]) for r in rows if r.get(o.id) is not None]
        if len(dolu) >= 2:
            dolu.sort(key=lambda t: (-t[0], t[1]))
            cumleler.append(f"{o.ad}: en yüksek {dolu[0][1]} ({fmt(dolu[0][0], o.birim)}), en düşük {dolu[-1][1]} ({fmt(dolu[-1][0], o.birim)}).")
    return _sonuc("karsilastir", cozulen, b, [Tablo("Karşılaştırma", sut, rows)], cumleler,
                  "Her ölçü, yerlerin seviyesinde ve o yer grubunda verisi olan en güncel ortak dönemde okunur.")


# =============================================================================================== 3. sirala
def sirala(p: P.Sirala, b: Baglam) -> Sonuc:
    o = olcu_coz(p.olcu)
    _kural_notlari(o, b)
    if p.seviye not in o.seviyeler:
        raise AnalizHatasi(f"'{o.ad}' {SEVIYE_ADI[p.seviye]} seviyesinde yok (var olan: {', '.join(o.seviyeler)}).")
    boy = boyut_coz(o, p.boyut)
    k, geo = _kapsam(p.kapsam, p.seviye, b)
    d = son_donem(o, p.seviye, geo, boy, p.donem, b)
    b.donemler[o.id] = d
    vals = degerler(o, p.seviye, geo, d, boy, b)
    toplam_birim = b.q(f"SELECT count(*) AS n FROM ({geo})")[0]["n"]
    aday = {g: r for g, r in vals.items() if r["deger"] is not None}
    nuf = _nufus_haritasi(p.seviye, geo, b) if p.min_nufus is not None else {}
    if p.min_nufus is not None:
        aday = {g: r for g, r in aday.items() if (nuf.get(g, {}).get("nufus") or 0) >= p.min_nufus}
    if p.min_ilan is not None:
        izin = _ilan_filtresi(o, p.seviye, geo, d, p.min_ilan, b)
        if izin is not None:
            aday = {g: r for g, r in aday.items() if g in izin}
    if not aday:
        raise AnalizHatasi("Süzgeçlerden sonra sıralanacak birim kalmadı.")
    isaret = -1 if p.yon == "azalan" else 1
    sirali = sorted(aday.values(), key=lambda r: (isaret * r["deger"], r["geo_id"]))
    n = len(sirali)
    satirlar = []
    for i, r in enumerate(sirali[: p.limit], 1):
        y = dizin().yerler[r["geo_id"]]
        satirlar.append({"sira": i, "yer": y.tam_ad, "geo_id": y.geo_id, "deger": r["deger"],
                         "yuzdelik": 100.0 * (n - i) / (n - 1) if n > 1 and p.yon == "azalan" else (100.0 * (i - 1) / (n - 1) if n > 1 else None),
                         "celiski": r["celiski"], "lat": y.lat, "lon": y.lon})
    kapsam_ad = k.tam_ad if k else "Türkiye"
    sut = [Sutun("sira", "Sıra"), Sutun("yer", "Yer"), Sutun("deger", o.ad + (f" [{boy}]" if boy else ""), o.birim),
           Sutun("yuzdelik", "Yüzdelik dilim"), Sutun("geo_id", "geo_id")]
    suzgec = []
    if p.min_nufus is not None:
        suzgec.append(f"nüfus ≥ {tr_sayi(p.min_nufus)}")
    if p.min_ilan is not None:
        suzgec.append(f"ilan ≥ {p.min_ilan}")
    cumle = [f"{kapsam_ad} içindeki {tr_sayi(toplam_birim)} {SEVIYE_ADI[p.seviye]} biriminden {tr_sayi(n)} tanesinde {d} dönemi için "
             f"{ilk_kucuk(o.ad)} verisi var" + (f" ({', '.join(suzgec)} süzgeciyle)" if suzgec else "") + "."]
    ilk = satirlar[: min(5, len(satirlar))]
    cumle.append(("En yüksek" if p.yon == "azalan" else "En düşük") + f" {len(ilk)}: " +
                 "; ".join(f"{r['sira']}) {r['yer']} {fmt(r['deger'], o.birim)}" for r in ilk) + ".")
    if n < toplam_birim * 0.5:
        b.uyar(f"Kapsamdaki birimlerin yalnız %{tr_sayi(100.0 * n / toplam_birim, 1)}'inde veri var; sıralama bu alt kümeyi yansıtır.")
    cozulen = {"analiz": "sirala", "olcu": o.id, "seviye": p.seviye, "kapsam": k.geo_id if k else "GEO_TR", "yon": p.yon,
               "limit": p.limit, "donem": d, "boyut": boy, "min_nufus": p.min_nufus, "min_ilan": p.min_ilan}
    return _sonuc("sirala", cozulen, b, [Tablo(f"{kapsam_ad} — {o.ad} sıralaması", sut, satirlar)], cumle,
                  "Değere göre sıralama; eşitlikte geo_id. Yüzdelik: verisi olan birimler içindeki konum.")


# =============================================================================================== 4. zaman_serisi
def _ay_farki(a: str, z: str) -> int | None:
    if re.match(DONEM_DESEN["ay"], a) and re.match(DONEM_DESEN["ay"], z):
        return (int(z[:4]) - int(a[:4])) * 12 + int(z[5:7]) - int(a[5:7])
    if re.match(DONEM_DESEN["yil"], a) and re.match(DONEM_DESEN["yil"], z):
        return (int(z) - int(a)) * 12
    return None


def zaman_serisi(p: P.ZamanSerisi, b: Baglam) -> Sonuc:
    o = olcu_coz(p.olcu)
    _kural_notlari(o, b, p.projeksiyon_dahil)
    boy = boyut_coz(o, p.boyut)
    yerler = [yer_birim(x, b) for x in p.yerler]
    seri: dict[str, dict[str, dict]] = {}
    desen = DONEM_DESEN.get(o.donem_turu[0]) if o.donem_turu else None
    for y in yerler:
        if y.seviye not in o.seviyeler:
            b.uyar(f"{o.ad} {SEVIYE_ADI[y.seviye]} seviyesinde yok: {y.tam_ad}")
            continue
        w = ["TRUE"]
        prm = []
        if p.baslangic:
            w.append("donem >= ?"); prm.append(p.baslangic)
        if p.bitis:
            w.append("donem <= ?"); prm.append(p.bitis)
        if desen:
            w.append(f"regexp_matches(donem, '{desen}')")
        rows = b.q(f"""SELECT * FROM ({veri.olcum_sql(o, y.seviye, boy, f"SELECT {veri.lit(y.geo_id)}", p.projeksiyon_dahil)})
            WHERE {' AND '.join(w)} ORDER BY donem, tur""", prm)
        b.iz(rows)
        seri[y.geo_id] = {r["donem"]: r for r in rows}
        if not rows:
            b.uyar(f"{y.tam_ad}: seçilen aralıkta {o.ad} verisi yok.")
    if not any(seri.values()):
        raise AnalizHatasi(f"{o.ad} için seçilen yer/aralıkta veri yok.")
    donemler = sorted({d for s in seri.values() for d in s})
    yer_ad = {y.geo_id: y.tam_ad for y in yerler}
    satirlar = []
    for d in donemler:
        r = {"donem": d}
        tahmin = False
        for g, s in seri.items():
            r[g] = s.get(d, {}).get("deger")
            tahmin |= s.get(d, {}).get("tur") == "projeksiyon"
        r["tur"] = "TAHMİN" if tahmin else "ölçüm"
        satirlar.append(r)
    sut = [Sutun("donem", "Dönem")] + [Sutun(g, yer_ad[g], o.birim) for g in seri] + [Sutun("tur", "Tür")]

    fark_mi = o.birim in ("%", "‰")
    ozet, cumleler = [], []
    for g, s in seri.items():
        olc = [r for d, r in sorted(s.items()) if r["tur"] != "projeksiyon" and r["deger"] is not None]
        if len(olc) < 2:
            continue
        a, z = olc[0], olc[-1]
        ay = _ay_farki(a["donem"], z["donem"])
        r = {"yer": yer_ad[g], "ilk_donem": a["donem"], "ilk": a["deger"], "son_donem": z["donem"], "son": z["deger"]}
        if fark_mi:
            r["degisim"] = z["deger"] - a["deger"]
        else:
            r["degisim"] = 100.0 * (z["deger"] / a["deger"] - 1) if a["deger"] else None
            r["yillik_bilesik"] = (100.0 * ((z["deger"] / a["deger"]) ** (12.0 / ay) - 1)
                                   if ay and ay >= 12 and a["deger"] and a["deger"] > 0 and z["deger"] > 0 else None)
            onceki12 = s.get(f"{int(z['donem'][:4]) - 1}{z['donem'][4:]}") if len(z["donem"]) >= 4 else None
            r["son_12_ay"] = (100.0 * (z["deger"] / onceki12["deger"] - 1)
                              if onceki12 and onceki12.get("deger") and re.match(DONEM_DESEN["ay"], z["donem"]) else None)
        ozet.append(r)
        if fark_mi:
            cumleler.append(f"{yer_ad[g]}: {ilk_kucuk(o.ad)} {a['donem']}'de {fmt(a['deger'], o.birim)}, {z['donem']}'de "
                            f"{fmt(z['deger'], o.birim)} (fark {tr_sayi(r['degisim'], 2)} puan).")
        else:
            ek = f"; yıllık bileşik %{tr_sayi(r['yillik_bilesik'], 1)}" if r.get("yillik_bilesik") is not None else ""
            cumleler.append(f"{yer_ad[g]}: {ilk_kucuk(o.ad)} {a['donem']}'de {fmt(a['deger'], o.birim)}, {z['donem']}'de "
                            f"{fmt(z['deger'], o.birim)} (toplam değişim %{tr_sayi(r['degisim'], 1)}{ek}).")
    tablolar = [Tablo(f"{o.ad} — dönemsel seri", sut, satirlar)]
    if ozet:
        osut = [Sutun("yer", "Yer"), Sutun("ilk_donem", "İlk dönem"), Sutun("ilk", "İlk değer", o.birim),
                Sutun("son_donem", "Son dönem"), Sutun("son", "Son değer", o.birim)]
        osut += ([Sutun("degisim", "Fark", "puan")] if fark_mi else
                 [Sutun("degisim", "Toplam değişim", "%"), Sutun("yillik_bilesik", "Yıllık bileşik", "%"),
                  Sutun("son_12_ay", "Son 12 ay", "%")])
        tablolar.append(Tablo("Değişim özeti (yalnız ölçülmüş dönemler)", osut, ozet))
    if p.projeksiyon_dahil and any(r["tur"] == "TAHMİN" for r in satirlar):
        b.uyar("TAHMİN satırları kaynağın kendi projeksiyonudur; ölçülmüş veri değildir ve değişim özetine katılmadı.")
    b.donemler[o.id] = f"{donemler[0]} – {donemler[-1]}"
    cozulen = {"analiz": "zaman_serisi", "yerler": list(seri), "olcu": o.id, "baslangic": p.baslangic, "bitis": p.bitis,
               "boyut": boy, "projeksiyon_dahil": p.projeksiyon_dahil}
    return _sonuc("zaman_serisi", cozulen, b, tablolar, cumleler,
                  "Toplam değişim = son/ilk − 1; yıllık bileşik = (son/ilk)^(12/ay farkı) − 1 (≥12 ay); son 12 ay = aynı ayın bir önceki yılına göre. "
                  "Yüzde birimli ölçülerde yalnız puan farkı verilir.")


# =============================================================================================== 5. dagilim
def dagilim(p: P.Dagilim, b: Baglam) -> Sonuc:
    o = olcu_coz(p.olcu)
    _kural_notlari(o, b)
    boy = boyut_coz(o, p.boyut)
    k, geo = _kapsam(p.kapsam, p.seviye, b)
    d = son_donem(o, p.seviye, geo, boy, p.donem, b)
    b.donemler[o.id] = d
    ic = veri.olcum_sql(o, p.seviye, boy, geo)
    r = b.q(f"""SELECT count(*) AS n, min(deger) AS min, quantile_cont(deger, 0.10) AS p10, quantile_cont(deger, 0.25) AS p25,
          quantile_cont(deger, 0.5) AS medyan, quantile_cont(deger, 0.75) AS p75, quantile_cont(deger, 0.90) AS p90,
          max(deger) AS max, avg(deger) AS ortalama, stddev_pop(deger) AS std_sapma
        FROM (SELECT deger FROM ({ic}) WHERE donem = ? AND deger IS NOT NULL ORDER BY deger)""", [d])[0]
    if not r["n"]:
        raise AnalizHatasi("Dağılım için veri yok.")
    toplam = b.q(f"SELECT count(*) AS n FROM ({geo})")[0]["n"]
    kapsam_ad = k.tam_ad if k else "Türkiye"
    satir = [{"istatistik": ad, "deger": r[key]} for key, ad in
             [("n", "Verisi olan birim"), ("min", "En düşük"), ("p10", "10. yüzdelik"), ("p25", "25. yüzdelik (Ç1)"),
              ("medyan", "Medyan"), ("p75", "75. yüzdelik (Ç3)"), ("p90", "90. yüzdelik"), ("max", "En yüksek"),
              ("ortalama", "Aritmetik ortalama (ağırlıksız)"), ("std_sapma", "Standart sapma")]]
    tablolar = [Tablo(f"{kapsam_ad} — {SEVIYE_ADI[p.seviye]} düzeyinde {o.ad} dağılımı ({d})",
                      [Sutun("istatistik", "İstatistik"), Sutun("deger", "Değer", o.birim)], satir)]
    cumle = [f"{kapsam_ad} içinde {d} döneminde {ilk_kucuk(o.ad)} verisi olan {tr_sayi(r['n'])} {SEVIYE_ADI[p.seviye]} "
             f"(toplam {tr_sayi(toplam)}): medyan {fmt(r['medyan'], o.birim)}, 25.–75. yüzdelik aralığı "
             f"{fmt(r['p25'], o.birim)} – {fmt(r['p75'], o.birim)}, en düşük {fmt(r['min'], o.birim)}, en yüksek {fmt(r['max'], o.birim)}."]
    if k and k.seviye in o.seviyeler and k.seviye != p.seviye:
        kv = degerler(o, k.seviye, f"SELECT {veri.lit(k.geo_id)}", d, boy, b).get(k.geo_id)
        if kv:
            cumle.append(f"Kaynağın {k.tam_ad} için verdiği {SEVIYE_ADI[k.seviye]} değeri: {fmt(kv['deger'], o.birim)} "
                         "(alt birimlerden hesaplanmadı).")
    if not o.toplanabilir:
        b.uyar("Ağırlıksız ortalama, birimlerin büyüklüğünü (nüfus/ilan) dikkate almaz; üst birim değeri yerine kullanılmamalıdır.")
    cozulen = {"analiz": "dagilim", "olcu": o.id, "seviye": p.seviye, "kapsam": k.geo_id if k else "GEO_TR", "donem": d, "boyut": boy}
    return _sonuc("dagilim", cozulen, b, tablolar, cumle, "Sürekli yüzdelik (doğrusal ara değer); ortalama ağırlıksız.")


# =============================================================================================== 6. yakin_cevre
def _merkez(metin: str, b: Baglam) -> tuple[float, float, str, Yer | None]:
    y = yer_coz(metin, b)
    if isinstance(y, Nokta):
        return y.lat, y.lon, f"({tr_sayi(y.lat, 5)}, {tr_sayi(y.lon, 5)})", y.mahalle
    if y.lat is None:
        raise AnalizHatasi(f"{y.tam_ad} için merkez koordinatı yok.")
    b.uyar(f"Merkez olarak {y.tam_ad} poligonunun ağırlık merkezi kullanıldı; belirli bir adres için 'enlem, boylam' verin.")
    return y.lat, y.lon, y.tam_ad, y if y.seviye == "mahalle" else None


def _mesafe_sql(lat: float, lon: float, lat_col: str = "lat", lon_col: str = "lon") -> str:
    return (f"2 * 6371008.8 * asin(sqrt(pow(sin(radians({lat_col} - {lat}) / 2), 2) + cos(radians({lat})) * cos(radians({lat_col})) "
            f"* pow(sin(radians({lon_col} - {lon}) / 2), 2)))")


def _kutu(lat: float, lon: float, r: float) -> tuple[float, float, float, float]:
    dlat = r / 111320.0
    dlon = r / (111320.0 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _daire_wkt(lat: float, lon: float, r: float, n: int = 72) -> str:
    """Nokta çevresinde r metrelik daireyi yaklaşık çokgen olarak (yerel eşdikdörtgen izdüşüm) üretir."""
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * (i % n) / n
        pts.append((lon + (r * math.cos(a)) / (111320.0 * math.cos(math.radians(lat))), lat + (r * math.sin(a)) / 111320.0))
    return "POLYGON((" + ", ".join(f"{x:.7f} {y:.7f}" for x, y in pts) + "))"


def yakin_cevre(p: P.YakinCevre, b: Baglam) -> Sonuc:
    lat, lon, merkez_ad, mah = _merkez(p.merkez, b)
    r = float(p.yaricap_m)
    la0, la1, lo0, lo1 = _kutu(lat, lon, r)
    sek, kat = sektor_coz(p.sektor), kategori_coz(p.kategori)
    b.kurallar.update({"gecerli_koordinat", "kategori_belirsiz", "kisisel_veri"})
    b.kaynaklar.update({"k.poi", "k.poi_lifecycle_osm", "k.population_observation", "k.geo_entity"})
    ek = []
    prm = [la0, la1, lo0, lo1]
    if sek:
        ek.append("p.predicted_sector = ?"); prm.append(sek)
    if kat:
        ek.append("p.predicted_category = ?"); prm.append(kat)
    ek_sql = (" AND " + " AND ".join(ek)) if ek else ""
    isl = f"""SELECT * FROM (SELECT p.poi_id, p.name AS ad, p.predicted_sector AS sektor, p.predicted_category AS kategori,
                p.rating AS puan, p.rating_count AS puan_sayisi, p.lat, p.lon, p.source_coverage AS kaynak_kapsami,
                {_mesafe_sql(lat, lon, 'p.lat', 'p.lon')} AS mesafe_m
              FROM k.main.poi p WHERE {veri.GECERLI_KOORD} AND p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?{ek_sql})
            WHERE mesafe_m <= {r}"""
    sektor_rows = b.q(f"""SELECT coalesce(sektor, '(sektörü belirsiz)') AS sektor, count(*) AS n, avg(puan) AS ort_puan
        FROM ({isl}) GROUP BY 1 ORDER BY 2 DESC, 1""", prm)
    toplam = sum(x["n"] for x in sektor_rows)
    kat_rows = b.q(f"""SELECT kategori, count(*) AS n FROM ({isl}) WHERE kategori IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 15""", prm)
    liste = b.q(f"SELECT * FROM ({isl}) ORDER BY mesafe_m, poi_id LIMIT {int(p.liste_limiti)}", prm) if p.liste_limiti else []

    # harita nesneleri: yarıçap içi sayım + seçili türlerde 3 km içinde en yakın
    R2 = max(r, 3000.0)
    h0, h1, g0, g1 = _kutu(lat, lon, R2)
    har = f"""SELECT osm_category AS tur, name AS ad, lat, lon, {_mesafe_sql(lat, lon)} AS mesafe_m FROM k.main.poi_lifecycle_osm
              WHERE map_status = 'aktif' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"""
    har_rows = b.q(f"""SELECT tur, count(*) FILTER (WHERE mesafe_m <= {r}) AS yaricap_ici, min(mesafe_m) AS en_yakin_m
        FROM ({har}) WHERE tur IN ({",".join(veri.lit(x) for x in KEY_HARITA)}) GROUP BY 1 ORDER BY 1""", [h0, h1, g0, g1])

    # alan-ağırlıklı nüfus
    daire = _daire_wkt(lat, lon, r)
    nuf = b.q(f"""WITH d AS (SELECT ST_GeomFromText('{daire}') AS g),
        m AS (SELECT e.geo_id, e.geometry FROM k.main.geo_entity e, d WHERE e.level = 'mahalle'
              AND e.bbox_xmax >= {lo0} AND e.bbox_xmin <= {lo1} AND e.bbox_ymax >= {la0} AND e.bbox_ymin <= {la1}
              AND ST_Intersects(e.geometry, d.g)),
        x AS (SELECT m.geo_id, ST_Area_Spheroid(ST_FlipCoordinates(ST_Intersection(m.geometry, d.g))) AS kesisim,
                     ST_Area_Spheroid(ST_FlipCoordinates(m.geometry)) AS alan FROM m, d),
        n AS ({veri._nufus_kesit_sql('mahalle')})
        SELECT x.geo_id, x.kesisim / nullif(x.alan, 0) AS pay, n.nufus, n.nufus_kaynagi, n.nufus * x.kesisim / nullif(x.alan, 0) AS katki
        FROM x LEFT JOIN n USING (geo_id) ORDER BY x.geo_id""")
    tahmini_nufus = sum(x["katki"] or 0 for x in nuf)
    eksik_nufus = [x["geo_id"] for x in nuf if x["nufus"] is None]

    tablolar = [
        Tablo("İşletmeler — sektör", [Sutun("sektor", "Sektör"), Sutun("n", "Sayı", "adet"), Sutun("ort_puan", "Ort. puan")], sektor_rows,
              f"{int(r)} m içinde, geçerli koordinatlı"),
        Tablo("İşletmeler — en sık 15 kategori", [Sutun("kategori", "Kategori"), Sutun("n", "Sayı", "adet")], kat_rows),
        Tablo("Harita nesneleri (OSM)", [Sutun("tur", "Tür"), Sutun("yaricap_ici", f"{int(r)} m içinde", "adet"),
                                         Sutun("en_yakin_m", f"En yakın ({int(R2)} m'ye kadar)", "m")], har_rows),
        Tablo("Kesişen mahalleler (nüfus payı)", [Sutun("yer", "Mahalle"), Sutun("pay", "Dairede kalan alan payı"),
                                                  Sutun("nufus", "Mahalle nüfusu", "kişi"), Sutun("katki", "Tahmini katkı", "kişi"),
                                                  Sutun("nufus_kaynagi", "Nüfus kaynağı")],
              [{**x, "yer": _ad(x["geo_id"])} for x in nuf]),
    ]
    if liste:
        tablolar.append(Tablo(f"En yakın {len(liste)} işletme",
                              [Sutun("ad", "Ad"), Sutun("kategori", "Kategori"), Sutun("sektor", "Sektör"), Sutun("puan", "Puan"),
                               Sutun("puan_sayisi", "Puan sayısı"), Sutun("mesafe_m", "Mesafe", "m"), Sutun("lat", "Enlem"),
                               Sutun("lon", "Boylam"), Sutun("kaynak_kapsami", "Kaynak"), Sutun("poi_id", "poi_id")], liste))
    bel = next((x["n"] for x in sektor_rows if x["sektor"] == "(sektörü belirsiz)"), 0)
    cumle = [f"Merkez {merkez_ad}" + (f" ({mah.tam_ad} mahallesinde)" if mah and mah.tam_ad != merkez_ad else "") +
             f", yarıçap {tr_sayi(int(r))} m: {tr_sayi(toplam)} işletme" + (f" (filtre: {sek or kat})" if (sek or kat) else "") +
             (f", bunların {tr_sayi(bel)} tanesinin sektörü belirsiz" if bel else "") + "."]
    if nuf:
        cumle.append(f"Alan-ağırlıklı yaklaşık nüfus: {tr_sayi(round(tahmini_nufus))} kişi ({len(nuf)} mahalle kesişiyor).")
        b.uyar("Nüfus tahmini, mahalle nüfusunun mahalle alanına eşit dağıldığı varsayımıyla hesaplanır (alan-ağırlıklı ara değer); gerçek dağılım farklı olabilir.")
    if eksik_nufus:
        b.uyar(f"{len(eksik_nufus)} kesişen mahallenin nüfus verisi yok; tahmine katılmadı.")
    yakin = [x for x in har_rows if x["en_yakin_m"] is not None and x["tur"] in ("metro_istasyonu", "otobus_duragi", "okul", "eczane", "hastane")]
    if yakin:
        cumle.append("En yakın: " + "; ".join(f"{x['tur'].replace('_', ' ')} {tr_sayi(round(x['en_yakin_m']))} m" for x in yakin) + " (OSM).")
    b.donemler.update({"işletme": veri.KESIT_ISLETME, "harita": veri.KESIT_HARITA})
    cozulen = {"analiz": "yakin_cevre", "merkez": [round(lat, 6), round(lon, 6)], "yaricap_m": int(r), "sektor": sek,
               "kategori": kat, "liste_limiti": p.liste_limiti}
    return _sonuc("yakin_cevre", cozulen, b, tablolar, cumle,
                  "Mesafe: haversine (küre, R=6.371.008,8 m). Nüfus: daire çokgeni (72 köşe) ile mahalle poligonlarının kesişim alanı payı × mahalle nüfusu.")


# =============================================================================================== 7. yogunluk
def yogunluk(p: P.Yogunluk, b: Baglam) -> Sonuc:
    ont = yukle()
    sek, kat = sektor_coz(p.sektor), kategori_coz(p.kategori)
    boy = f"sektor:{sek}" if sek else (f"kategori:{kat}" if kat else None)
    k, geo = _kapsam(p.kapsam, p.seviye, b)
    b.kurallar.update({"gecerli_koordinat", "kategori_belirsiz"})
    isl = {r["geo_id"]: r for r in b.q(f"SELECT * FROM ({veri._isletme_ham('isletme_sayisi', p.seviye, boy)}) WHERE geo_id IN ({geo})")}
    nuf = _nufus_haritasi(p.seviye, geo, b)
    alan = {r["geo_id"]: r["v"] for r in b.q(f"SELECT geo_id, v FROM ({veri._alan_ham(p.seviye)}) WHERE geo_id IN ({geo})")}
    b.kaynaklar.update({"k.poi", "k.population_observation", "k.geo_entity"})
    satirlar = []
    for g in sorted(set(nuf) | set(isl)):
        n = isl.get(g, {}).get("v", 0.0) or 0.0
        np_ = nuf.get(g, {}).get("nufus")
        if p.min_nufus is not None and (np_ or 0) < p.min_nufus:
            continue
        a = alan.get(g)
        satirlar.append({"geo_id": g, "yer": _ad(g), "isletme": n, "nufus": np_, "alan_km2": a,
                         "bin_kisi_basina": 1000.0 * n / np_ if np_ else None, "km2_basina": n / a if a else None,
                         "nufus_kaynagi": nuf.get(g, {}).get("nufus_kaynagi")})
    if not satirlar:
        raise AnalizHatasi("Süzgeçlerden sonra birim kalmadı.")
    isaret = -1 if p.yon == "azalan" else 1
    satirlar.sort(key=lambda r: (r["bin_kisi_basina"] is None, isaret * (r["bin_kisi_basina"] or 0), r["geo_id"]))
    n_toplam = len(satirlar)
    for i, r in enumerate(satirlar, 1):
        r["sira"] = i
    toplam_isl = sum(r["isletme"] for r in satirlar)
    toplam_nuf = sum(r["nufus"] or 0 for r in satirlar)
    ad = sek or kat or "tüm işletmeler"
    kapsam_ad = k.tam_ad if k else "Türkiye"
    cumle = [f"{kapsam_ad} içinde {tr_sayi(n_toplam)} {SEVIYE_ADI[p.seviye]}" + (f" (nüfus ≥ {tr_sayi(p.min_nufus)})" if p.min_nufus else "") +
             f": {ad} için toplam {tr_sayi(toplam_isl)} işletme, 1.000 kişiye {tr_sayi(1000.0 * toplam_isl / toplam_nuf, 2) if toplam_nuf else '—'}."]
    ust = satirlar[: min(5, len(satirlar))]
    cumle.append(("1.000 kişiye en çok düşen" if p.yon == "azalan" else "1.000 kişiye en az düşen") + ": " +
                 "; ".join(f"{r['yer']} {tr_sayi(r['bin_kisi_basina'], 2)}" for r in ust if r["bin_kisi_basina"] is not None) + ".")
    b.donemler.update({"işletme": veri.KESIT_ISLETME, "nüfus": "mahalle: TÜİK 2025, yoksa web 2024; ilçe/il: TÜİK 2025"})
    sut = [Sutun("sira", "Sıra"), Sutun("yer", "Yer"), Sutun("isletme", ad, "adet"), Sutun("nufus", "Nüfus", "kişi"),
           Sutun("bin_kisi_basina", "1.000 kişiye", "adet"), Sutun("km2_basina", "km² başına", "adet"), Sutun("alan_km2", "Alan", "km²"),
           Sutun("geo_id", "geo_id")]
    cozulen = {"analiz": "yogunluk", "seviye": p.seviye, "kapsam": k.geo_id if k else "GEO_TR", "boyut": boy, "yon": p.yon,
               "limit": p.limit, "min_nufus": p.min_nufus}
    _ = ont
    return _sonuc("yogunluk", cozulen, b, [Tablo(f"{kapsam_ad} — {ad} yoğunluğu", sut, satirlar[: p.limit])], cumle,
                  "1.000 kişiye = işletme / nüfus × 1000; km² başına = işletme / poligon alanı. Mahalle sayımında yalnız poligon-içi atamalar.")


# =============================================================================================== 8. kategori_dagilimi
def kategori_dagilimi(p: P.KategoriDagilimi, b: Baglam) -> Sonuc:
    y = yer_birim(p.yer, b)
    kd = _kategori_sayim(y, p.duzey, b)
    if not kd:
        raise AnalizHatasi(f"{y.tam_ad} içinde geçerli koordinatlı işletme yok.")
    tablo, satirlar = kd
    b.donemler["işletme"] = veri.KESIT_ISLETME
    belirli = [r for r in satirlar if not r["grup"].startswith("(")][:5]
    cumle = [f"{y.tam_ad}: türü belirli işletmeler içinde en büyük {len(belirli)} {'sektör' if p.duzey == 'sektor' else 'kategori'}: " +
             "; ".join(f"{r['grup']} {tr_sayi(r['n'])} (%{tr_sayi(r['pay'], 1)})" for r in belirli) + "."]
    return _sonuc("kategori_dagilimi", {"analiz": "kategori_dagilimi", "yer": y.geo_id, "duzey": p.duzey}, b, [tablo], cumle,
                  "Pay = gruptaki işletme / türü belirli işletme toplamı. Üst birim payları aynı yöntemle.")


# =============================================================================================== 9. benzer_yerler
VARSAYILAN_BENZER = {
    "mahalle": ["konut_satis_m2", "konut_kira_m2", "nufus_yogunlugu", "bin_kisi_basina_isletme"],
    "ilce": ["konut_satis_m2", "konut_kira_m2", "nufus_yogunlugu", "bin_kisi_basina_isletme", "ses_skoru"],
    "il": ["konut_satis_m2", "konut_kira_m2", "nufus_yogunlugu", "bin_kisi_basina_isletme", "ses_skoru"],
}


def benzer_yerler(p: P.BenzerYerler, b: Baglam) -> Sonuc:
    y = yer_birim(p.yer, b)
    if y.seviye not in VARSAYILAN_BENZER:
        raise AnalizHatasi("Benzer yer analizi mahalle, ilçe veya il için yapılır.")
    olculer = [olcu_coz(x) for x in (p.olculer or VARSAYILAN_BENZER[y.seviye])]
    k, geo = _kapsam(p.kapsam, y.seviye, b)
    tablo: dict[str, dict] = {}
    for o in olculer:
        if y.seviye not in o.seviyeler:
            raise AnalizHatasi(f"{o.ad} {SEVIYE_ADI[y.seviye]} seviyesinde yok; benzerlik için kullanılamaz.")
        _kural_notlari(o, b)
        d = son_donem(o, y.seviye, geo, None, "son", b)
        b.donemler[o.id] = d
        for g, r in degerler(o, y.seviye, geo, d, None, b).items():
            tablo.setdefault(g, {})[o.id] = r["deger"]
    if y.geo_id not in tablo or any(tablo[y.geo_id].get(o.id) is None for o in olculer):
        eksik = [o.ad for o in olculer if tablo.get(y.geo_id, {}).get(o.id) is None]
        raise AnalizHatasi(f"{y.tam_ad} için şu ölçülerde veri yok: {', '.join(eksik)}. Farklı ölçü seti deneyin.")
    tam = {g: v for g, v in tablo.items() if all(v.get(o.id) is not None for o in olculer)}
    donus = {}
    for o in olculer:
        xs = [v[o.id] for v in tam.values()]
        log = all(x > 0 for x in xs)
        donus[o.id] = log
        tx = [math.log(x) if log else x for x in xs]
        ort = sum(tx) / len(tx)
        sd = math.sqrt(sum((t - ort) ** 2 for t in tx) / len(tx)) or 1.0
        for g, v in tam.items():
            v[f"z_{o.id}"] = ((math.log(v[o.id]) if log else v[o.id]) - ort) / sd
    ref = tam[y.geo_id]
    uz = []
    for g, v in tam.items():
        if g == y.geo_id:
            continue
        dist = math.sqrt(sum((v[f"z_{o.id}"] - ref[f"z_{o.id}"]) ** 2 for o in olculer))
        uz.append((round(dist, 12), g))
    uz.sort()
    satirlar = [{"sira": 0, "yer": y.tam_ad + " (referans)", "uzaklik": 0.0, **{o.id: ref[o.id] for o in olculer}, "geo_id": y.geo_id}]
    for i, (dist, g) in enumerate(uz[: p.n], 1):
        satirlar.append({"sira": i, "yer": _ad(g), "uzaklik": dist, **{o.id: tam[g][o.id] for o in olculer}, "geo_id": g})
    sut = [Sutun("sira", "Sıra"), Sutun("yer", "Yer"), Sutun("uzaklik", "Uzaklık (z-birimi)")] + \
          [Sutun(o.id, o.ad, o.birim) for o in olculer] + [Sutun("geo_id", "geo_id")]
    kapsam_ad = k.tam_ad if k else "Türkiye"
    cumle = [f"{y.tam_ad} için {kapsam_ad} içindeki {tr_sayi(len(tam) - 1)} aday {SEVIYE_ADI[y.seviye]} arasından, "
             f"{len(olculer)} ölçüde ({', '.join(ilk_kucuk(o.ad) for o in olculer)}) en yakın {min(p.n, len(uz))}: " +
             "; ".join(f"{r['yer']} ({tr_sayi(r['uzaklik'], 2)})" for r in satirlar[1:6]) + "."]
    if len(tam) < len(tablo):
        b.uyar(f"{len(tablo) - len(tam)} birim bazı ölçülerde veri eksik olduğu için karşılaştırmaya alınmadı.")
    cozulen = {"analiz": "benzer_yerler", "yer": y.geo_id, "olculer": [o.id for o in olculer], "kapsam": k.geo_id if k else "GEO_TR", "n": p.n}
    return _sonuc("benzer_yerler", cozulen, b, [Tablo("Benzer yerler", sut, satirlar)], cumle,
                  "Her ölçü (tümü pozitifse log dönüşümüyle) aday küme üzerinde z-skora çevrilir; uzaklık = z-skor farklarının Öklid normu. "
                  "Eşitlikte geo_id sırası. Log dönüşümü: " + ", ".join(f"{k}={'evet' if v else 'hayır'}" for k, v in donus.items()))


# =============================================================================================== 10. harita_hareketliligi
def harita_hareketliligi(p: P.HaritaHareketliligi, b: Baglam) -> Sonuc:
    ont = yukle()
    y = yer_birim(p.yer, b)
    seviyeler = [y] + _ust(y)
    b.kurallar.add("osm_semantigi")
    satirlar = []
    for s in seviyeler:
        if s.seviye not in ("mahalle", "ilce", "il"):
            continue
        vals = {}
        for oid in ("harita_mevcut", "harita_eklenen", "harita_cikan", "harita_devir_hizi"):
            rows = b.q(f"SELECT * FROM ({veri.olcum_sql(ont.olcu(oid), s.seviye, p.kategori_grubu, f'SELECT {veri.lit(s.geo_id)}')}) ORDER BY donem")
            b.iz(rows)
            for r in rows:
                vals.setdefault(r["donem"], {})[oid] = r["deger"]
        for d, v in sorted(vals.items()):
            satirlar.append({"yer": s.tam_ad, "seviye": SEVIYE_ADI[s.seviye], "donem": d, "mevcut": v.get("harita_mevcut"),
                             "eklenen": v.get("harita_eklenen"), "cikan": v.get("harita_cikan"), "devir": v.get("harita_devir_hizi")})
    if not satirlar:
        raise AnalizHatasi(f"{y.tam_ad} için OSM hareketlilik verisi yok.")
    sut = [Sutun("yer", "Yer"), Sutun("seviye", "Seviye"), Sutun("donem", "Kesit"), Sutun("mevcut", "Haritada mevcut", "adet"),
           Sutun("eklenen", "Haritaya eklenen", "adet"), Sutun("cikan", "Haritadan çıkan", "adet"), Sutun("devir", "Devir hızı", "%")]
    son = [r for r in satirlar if r["yer"] == y.tam_ad][-1]
    cumle = [f"{y.tam_ad} ({p.kategori_grubu}): {son['donem']} OSM kesitinde haritada {tr_sayi(son['mevcut'])} nesne; önceki kesitten bu yana "
             f"haritaya {tr_sayi(son['eklenen'])} eklendi, {tr_sayi(son['cikan'])} çıktı (devir %{tr_sayi(son['devir'], 1)})."]
    b.uyar("Bu sayılar OSM haritasındaki değişimdir; işletme açılışı/kapanışı anlamına gelmez (harita katkısı, etiket değişimi de etkiler). "
           "2021-01 ilk kesittir (öncesi bilinmez).")
    b.kaynaklar.add("k.analytics.mahalle_turnover_osm")
    cozulen = {"analiz": "harita_hareketliligi", "yer": y.geo_id, "kategori_grubu": p.kategori_grubu}
    return _sonuc("harita_hareketliligi", cozulen, b, [Tablo("OSM harita hareketliliği", sut, satirlar)], cumle,
                  "Devir hızı = (eklenen + çıkan) / (önceki kesitte mevcut + eklenen) × 100; üst seviyeler mahalle toplamlarından aynı formülle.")


# =============================================================================================== 11. isletme_listesi
def isletme_listesi(p: P.IsletmeListesi, b: Baglam) -> Sonuc:
    if not p.kapsam and not p.merkez:
        raise AnalizHatasi("İşletme listesi için 'kapsam' (yer) veya 'merkez' (nokta) gerekli.")
    sek, kat = sektor_coz(p.sektor), kategori_coz(p.kategori)
    w, prm = [veri.GECERLI_KOORD], []
    mesafe = "NULL"
    tanim = []
    cozulen = {"analiz": "isletme_listesi", "sektor": sek, "kategori": kat, "min_puan": p.min_puan, "min_yorum": p.min_yorum,
               "siralama": p.siralama, "limit": p.limit}
    if p.merkez:
        lat, lon, mad, _ = _merkez(p.merkez, b)
        la0, la1, lo0, lo1 = _kutu(lat, lon, p.yaricap_m)
        w.append("p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?"); prm += [la0, la1, lo0, lo1]
        mesafe = _mesafe_sql(lat, lon, "p.lat", "p.lon")
        tanim.append(f"{mad} çevresinde {tr_sayi(p.yaricap_m)} m")
        cozulen.update({"merkez": [round(lat, 6), round(lon, 6)], "yaricap_m": p.yaricap_m})
    if p.kapsam:
        k = yer_birim(p.kapsam, b)
        kol = {"mahalle": "p.assigned_geo_id", "ilce": "p.assigned_ilce_geo_id", "il": "gi.il_geo_id"}.get(k.seviye)
        if not kol:
            raise AnalizHatasi("Kapsam mahalle, ilçe veya il olmalı.")
        w.append(f"{kol} = ?"); prm.append(k.geo_id)
        tanim.append(k.tam_ad)
        cozulen["kapsam"] = k.geo_id
    if sek:
        w.append("p.predicted_sector = ?"); prm.append(sek)
    if kat:
        w.append("p.predicted_category = ?"); prm.append(kat)
    if p.min_puan is not None:
        w.append("p.rating >= ?"); prm.append(p.min_puan)
    if p.min_yorum is not None:
        w.append("p.rating_count >= ?"); prm.append(p.min_yorum)
    sirala = {"puan": "puan DESC NULLS LAST, puan_sayisi DESC NULLS LAST", "yorum_sayisi": "puan_sayisi DESC NULLS LAST",
              "mesafe": "mesafe_m NULLS LAST", "ad": "ad"}[p.siralama]
    if p.siralama == "mesafe" and not p.merkez:
        raise AnalizHatasi("Mesafeye göre sıralama için 'merkez' gerekli.")
    ic = f"""SELECT * FROM (SELECT p.poi_id, p.name AS ad, p.predicted_category AS kategori, p.predicted_sector AS sektor,
              p.rating AS puan, p.rating_count AS puan_sayisi, p.lat, p.lon, p.assigned_geo_id AS mahalle_geo_id,
              p.source_coverage AS kaynak_kapsami, p.last_observed_at AS son_gozlem, {mesafe} AS mesafe_m
            FROM k.main.poi p LEFT JOIN k.main.geo_entity gi ON gi.geo_id = p.assigned_ilce_geo_id WHERE {' AND '.join(w)})
          {"WHERE mesafe_m <= " + str(float(p.yaricap_m)) if p.merkez else ""}"""
    n = b.q(f"SELECT count(*) AS n FROM ({ic})", prm)[0]["n"]
    rows = b.q(f"SELECT * FROM ({ic}) ORDER BY {sirala}, poi_id LIMIT {int(p.limit)}", prm)
    for r in rows:
        r["mahalle"] = _ad(r["mahalle_geo_id"]) if r["mahalle_geo_id"] else None
    b.kurallar.update({"gecerli_koordinat", "kisisel_veri"})
    b.kaynaklar.add("k.poi")
    b.donemler["işletme"] = veri.KESIT_ISLETME
    sut = [Sutun("ad", "Ad"), Sutun("kategori", "Kategori"), Sutun("sektor", "Sektör"), Sutun("puan", "Puan"),
           Sutun("puan_sayisi", "Puan sayısı"), Sutun("mesafe_m", "Mesafe", "m"), Sutun("mahalle", "Mahalle"),
           Sutun("lat", "Enlem"), Sutun("lon", "Boylam"), Sutun("kaynak_kapsami", "Kaynak"), Sutun("poi_id", "poi_id")]
    filt = ", ".join(x for x in [sek, kat, f"puan ≥ {p.min_puan}" if p.min_puan is not None else None,
                                 f"puan sayısı ≥ {p.min_yorum}" if p.min_yorum is not None else None] if x)
    cumle = [f"{' / '.join(tanim)}" + (f" ({filt})" if filt else "") + f": {tr_sayi(n)} işletme bulundu, {len(rows)} tanesi listelendi "
             f"(sıralama: {p.siralama})."]
    if p.min_puan is not None and not p.min_yorum:
        b.uyar("Puan süzgeci az sayıda puanı olan işletmeleri de içerir; 'min_yorum' ile birlikte kullanmak önerilir.")
    return _sonuc("isletme_listesi", cozulen, b, [Tablo("İşletmeler", sut, rows)], cumle,
                  "Telefon/adres gibi kişisel olabilecek alanlar verilmez. Sıralamada eşitlik poi_id ile kırılır.")


# =============================================================================================== 12. iliski
def _siralar(xs: list[float]) -> list[float]:
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        for k in range(i, j + 1):
            r[idx[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((c - my) ** 2 for c in y))
    if not sx or not sy:
        return None
    return sum((a - mx) * (c - my) for a, c in zip(x, y)) / (sx * sy)


def iliski(p: P.Iliski, b: Baglam) -> Sonuc:
    ox, oy = olcu_coz(p.olcu_x), olcu_coz(p.olcu_y)
    for o in (ox, oy):
        _kural_notlari(o, b)
        if p.seviye not in o.seviyeler:
            raise AnalizHatasi(f"{o.ad} {SEVIYE_ADI[p.seviye]} seviyesinde yok.")
    bx, by_ = boyut_coz(ox, p.boyut_x), boyut_coz(oy, p.boyut_y)
    k, geo = _kapsam(p.kapsam, p.seviye, b)
    dx = son_donem(ox, p.seviye, geo, bx, p.donem_x, b)
    dy = son_donem(oy, p.seviye, geo, by_, p.donem_y, b)
    b.donemler.update({ox.id: dx, oy.id: dy})
    vx, vy = degerler(ox, p.seviye, geo, dx, bx, b), degerler(oy, p.seviye, geo, dy, by_, b)
    ortak = sorted(g for g in vx if g in vy and vx[g]["deger"] is not None and vy[g]["deger"] is not None)
    if len(ortak) < 5:
        raise AnalizHatasi(f"İki ölçünün birlikte bulunduğu birim sayısı çok az ({len(ortak)}).")
    x = [vx[g]["deger"] for g in ortak]
    y = [vy[g]["deger"] for g in ortak]
    pr, sp = _pearson(x, y), _pearson(_siralar(x), _siralar(y))
    kapsam_ad = k.tam_ad if k else "Türkiye"
    satir = [{"istatistik": "Ortak birim sayısı (n)", "deger": len(ortak)}, {"istatistik": "Pearson r", "deger": pr},
             {"istatistik": "Spearman ρ", "deger": sp}]
    cumle = [f"{kapsam_ad} içindeki {tr_sayi(len(ortak))} {SEVIYE_ADI[p.seviye]} üzerinde {ilk_kucuk(ox.ad)} ({dx}) ile {ilk_kucuk(oy.ad)} ({dy}) "
             f"arasında Pearson r = {tr_sayi(pr, 3) if pr is not None else '—'}, Spearman ρ = {tr_sayi(sp, 3) if sp is not None else '—'}."]
    b.uyar("Korelasyon iki ölçünün birlikte değişimini gösterir; neden-sonuç ilişkisi göstermez.")
    orn = [{"yer": _ad(g), "x": vx[g]["deger"], "y": vy[g]["deger"], "geo_id": g} for g in ortak]
    cozulen = {"analiz": "iliski", "olcu_x": ox.id, "olcu_y": oy.id, "seviye": p.seviye, "kapsam": k.geo_id if k else "GEO_TR",
               "donem_x": dx, "donem_y": dy, "boyut_x": bx, "boyut_y": by_}
    return _sonuc("iliski", cozulen, b, [Tablo("Korelasyon", [Sutun("istatistik", "İstatistik"), Sutun("deger", "Değer")], satir),
                                          Tablo("Veri noktaları", [Sutun("yer", "Yer"), Sutun("x", ox.ad, ox.birim), Sutun("y", oy.ad, oy.birim),
                                                                   Sutun("geo_id", "geo_id")], orn)], cumle,
                  "Pearson: doğrusal korelasyon; Spearman: sıra korelasyonu (eşit değerlere ortalama sıra).")


# =============================================================================================== 13. veri_kapsami
def veri_kapsami(p: P.VeriKapsami, b: Baglam) -> Sonuc:
    o = olcu_coz(p.olcu)
    satirlar = []
    for s in o.seviyeler:
        ic = veri.olcum_sql(o, s, None, None, projeksiyon=True) if not o.boyut or not o.turetilmis else veri.olcum_sql(o, s, None, None)
        r = b.q(f"""SELECT count(DISTINCT geo_id) AS yer_sayisi, count(DISTINCT donem) AS donem_sayisi, min(donem) AS ilk, max(donem) AS son,
              count(*) FILTER (WHERE tur = 'projeksiyon') AS tahmin_satiri, sum(celiski::INT) AS celiskili,
              count(DISTINCT boyut) AS boyut_sayisi FROM ({ic})""")[0]
        toplam = b.q(f"SELECT count(*) AS n FROM k.main.geo_entity WHERE level = ?", [s])[0]["n"]
        satirlar.append({"seviye": SEVIYE_ADI[s], "yer_sayisi": r["yer_sayisi"], "toplam_birim": toplam,
                         "kapsama": 100.0 * r["yer_sayisi"] / toplam if toplam else None, "ilk": r["ilk"], "son": r["son"],
                         "donem_sayisi": r["donem_sayisi"], "tahmin_satiri": r["tahmin_satiri"], "celiskili": r["celiskili"],
                         "boyut_sayisi": r["boyut_sayisi"]})
    sut = [Sutun("seviye", "Seviye"), Sutun("yer_sayisi", "Verisi olan yer"), Sutun("toplam_birim", "Toplam birim"),
           Sutun("kapsama", "Kapsama", "%"), Sutun("ilk", "İlk dönem"), Sutun("son", "Son dönem"), Sutun("donem_sayisi", "Dönem sayısı"),
           Sutun("tahmin_satiri", "Tahmin satırı"), Sutun("celiskili", "Çelişkili hücre"), Sutun("boyut_sayisi", "Alt kırılım sayısı")]
    tanim = o.ozet()
    cumle = [f"{o.ad} ({o.birim}): " + "; ".join(f"{r['seviye']} düzeyinde {tr_sayi(r['yer_sayisi'])}/{tr_sayi(r['toplam_birim'])} yer, "
                                                   f"{r['ilk']} – {r['son']}" for r in satirlar) + "."]
    if o.not_:
        cumle.append(o.not_)
    if o.formul:
        cumle.append(f"Formül: {o.formul}")
    return _sonuc("veri_kapsami", {"analiz": "veri_kapsami", "olcu": o.id}, b,
                  [Tablo("Veri kapsamı", sut, satirlar), Tablo("Ölçü tanımı", [Sutun("alan", "Alan"), Sutun("deger", "Değer")],
                                                              [{"alan": k2, "deger": str(v)} for k2, v in tanim.items()])],
                  cumle, "Tekilleştirilmiş gözlemler üzerinden sayım; tahmin satırları ayrıca gösterilir.")


KATALOG = {
    "yer_profili": yer_profili, "karsilastir": karsilastir, "sirala": sirala, "zaman_serisi": zaman_serisi, "dagilim": dagilim,
    "yakin_cevre": yakin_cevre, "yogunluk": yogunluk, "kategori_dagilimi": kategori_dagilimi, "benzer_yerler": benzer_yerler,
    "harita_hareketliligi": harita_hareketliligi, "isletme_listesi": isletme_listesi, "iliski": iliski, "veri_kapsami": veri_kapsami,
}
