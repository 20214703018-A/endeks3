"""İleri modüller: uygun bölge skorlaması ve gelecek projeksiyonu.

Bunlar 'model çıktısı'dır (ölçüm değil); ama DETERMİNİSTİKTİR ve tüm varsayımlar açıktır:
  • uygun_bolge: profil kriterleri + ağırlıklar (profiller.yaml) → yüzdelik sıra skorları → ağırlıklı ortalama.
    Sağlamlık: her kriter tek tek çıkarıldığında sıranın hangi aralıkta kaldığı raporlanır.
  • gelecek_projeksiyonu / bolge_gelecek_raporu: projeksiyon.py (geçmişte sınanmış model topluluğu, ölçülmüş hata aralığı).
    Web'den gelen bilgi yalnız 'dis_varsayimlar' ile, kaynağı ve tarihiyle girer; plan parmak izine dahildir.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

import yaml

from . import plan as P
from . import projeksiyon as pj
from . import veri
from .analizler import (DONEM_DESEN, KATALOG, SIRA, Baglam, _ad, _kapsam, _kural_notlari, _nufus_haritasi, _siralar,
                        _sonuc, boyut_coz, degerler, fmt, olcu_coz, son_donem, yer_birim)
from .ontology import SEVIYE_ADI, Olcu, yukle
from .paths import PKG
from .sonuc import AnalizHatasi, Sutun, Tablo
from .textnorm import ilk_kucuk, tr_sayi
from .yer import Yer, dizin

PROJEKSIYON_UYARI = ("Projeksiyon geçmiş eğilimin istatistiksel uzantısıdır (ölçüm değil). Faiz, politika, afet, büyük proje gibi "
                     "kırılmaları içermez; %80 aralık, modelin geçmişteki gerçek hatalarından hesaplanmıştır.")


# Ölçülmüş kalibrasyon (tests/projeksiyon_degerlendirme.py, 2026-09-28): %80 aralığın gerçekleşenleri kapsama oranı
KALIBRASYON = {
    (True, False, "mahalle"): "Doğrulama: mahalle fiyat projeksiyonlarında %80 aralık geçmişte gerçekleşenlerin yalnız ~%64'ünü kapsadı (medyan hata %14); gerçek belirsizlik gösterilenden geniştir.",
    (True, False, "ilce"): "Doğrulama: ilçe fiyat projeksiyonlarında %80 aralık geçmişte gerçekleşenlerin ~%73–79'unu kapsadı (12 ay medyan hata: m² %9,5, kira %14,9).",
    (False, True, "ilce"): "Doğrulama: ilçe nüfus projeksiyonlarında 5 yıllık %80 aralık geçmişte gerçekleşenlerin ~%70'ini kapsadı (medyan hata %3,2).",
}


@lru_cache(maxsize=1)
def profiller() -> dict:
    with open(PKG / "profiller.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["profiller"]


def profil_ozeti() -> dict:
    return {k: {"ad": v["ad"], "tur": v["tur"], "seviyeler": v["seviyeler"],
                "kriterler": [f"{c['olcu']}{'[' + c['boyut'] + ']' if c.get('boyut') else ''} ({c['yon']}, {c['agirlik']})"
                              for c in v["kriterler"]]} for k, v in profiller().items()}


# =============================================================================================== uygun_bolge
def _kaynak_seviye(o: Olcu, seviye: str) -> str | None:
    """Ölçü bu seviyede yoksa, en yakın ÜST seviye (değer oradan devralınır)."""
    if seviye in o.seviyeler:
        return seviye
    for s in ("ilce", "il", "ulke"):
        if SIRA[s] < SIRA[seviye] and s in o.seviyeler:
            return s
    return None


def _ata(y: Yer, hedef: str) -> str | None:
    if hedef == y.seviye:
        return y.geo_id
    if hedef == "ilce" and y.seviye == "mahalle":
        return y.ust_geo_id
    if hedef == "il":
        return y.il_geo_id
    if hedef == "ulke":
        return "GEO_TR"
    return None


def uygun_bolge(p: P.UygunBolge, b: Baglam) -> Sonuc:
    if not p.profil and not p.kriterler:
        raise AnalizHatasi("Profil veya kriter listesi gerekli.", sorted(profiller()), tur="belirsiz")
    kriterler: list[dict] = []
    baslik = "özel kriterler"
    if p.profil:
        pr = profiller().get(p.profil)
        if not pr:
            raise AnalizHatasi(f"'{p.profil}' adlı profil yok.", [{"id": k, "ad": v["ad"], "tur": v["tur"]} for k, v in profiller().items()],
                               tur="belirsiz")
        if p.seviye not in pr["seviyeler"]:
            b.uyar(f"'{pr['ad']}' profili {', '.join(pr['seviyeler'])} için tasarlandı; {SEVIYE_ADI[p.seviye]} düzeyinde bazı kriterler üst birimden devralınacak.")
        kriterler = [dict(c) for c in pr["kriterler"]]
        baslik = f"'{pr['ad']}' profili"
    for c in p.kriterler or []:
        kriterler.append(c.model_dump())
    if p.agirliklar:
        for anahtar, w in p.agirliklar.items():
            hit = [c for c in kriterler if anahtar in (c["olcu"], f"{c['olcu']}[{c.get('boyut')}]")]
            if not hit:
                b.uyar(f"Ağırlık değişikliği '{anahtar}' hiçbir kritere uymadı; yok sayıldı.")
            for c in hit:
                c["agirlik"] = w
        kriterler = [c for c in kriterler if c["agirlik"] > 0]
    if not kriterler:
        raise AnalizHatasi("Tüm kriterlerin ağırlığı sıfır.")

    k, geo = _kapsam(p.kapsam, p.seviye, b)
    adaylar = [r["geo_id"] for r in b.q(f"SELECT geo_id FROM ({geo}) ORDER BY 1")]
    if p.min_nufus is not None:
        nuf = _nufus_haritasi(p.seviye, geo, b)
        adaylar = [g for g in adaylar if (nuf.get(g, {}).get("nufus") or 0) >= p.min_nufus]
    if len(adaylar) < 2:
        raise AnalizHatasi("Kapsamda yeterli aday birim yok.")
    yerler = dizin().yerler

    tablo_k, puanlar = [], []   # puanlar[j] = {geo: yönlü yüzdelik}
    ham: dict[str, dict] = {g: {} for g in adaylar}
    for j, c in enumerate(kriterler):
        o = olcu_coz(c["olcu"])
        _kural_notlari(o, b)
        boy = boyut_coz(o, c.get("boyut")) if c.get("boyut") else None
        ks = _kaynak_seviye(o, p.seviye)
        if not ks:
            b.uyar(f"{o.ad}: {SEVIYE_ADI[p.seviye]} ve üst seviyelerde veri yok; kriter çıkarıldı.")
            puanlar.append({}); tablo_k.append({**c, "ad": o.ad, "durum": "veri yok"}); continue
        eslem = {g: _ata(yerler[g], ks) for g in adaylar}
        ust_ids = sorted({v for v in eslem.values() if v})
        geo_k = "SELECT unnest([" + ",".join(veri.lit(x) for x in ust_ids) + "])" if ks != p.seviye else geo
        try:
            d = son_donem(o, ks, geo_k, boy, "son", b)
        except AnalizHatasi as e:
            b.uyar(f"{o.ad}: {e}; kriter çıkarıldı.")
            puanlar.append({}); tablo_k.append({**c, "ad": o.ad, "durum": "veri yok"}); continue
        vals = degerler(o, ks, geo_k, d, boy, b)
        x = {g: vals[eslem[g]]["deger"] for g in adaylar if eslem[g] in vals and vals[eslem[g]]["deger"] is not None}
        for g, v in x.items():
            ham[g][j] = v
        if len(x) >= 2:
            gs = sorted(x)
            r = _siralar([x[g] for g in gs])
            n = len(gs)
            yuz = {g: 100.0 * (r[i] - 1) / (n - 1) for i, g in enumerate(gs)}
            if c["yon"] == "eksi":
                yuz = {g: 100.0 - v for g, v in yuz.items()}
        else:
            yuz = {}
        puanlar.append(yuz)
        devr = ks != p.seviye
        if devr:
            b.uyar(f"{o.ad}{' [' + boy + ']' if boy else ''}: {SEVIYE_ADI[p.seviye]} düzeyinde yok, {SEVIYE_ADI[ks]} değeri devralındı "
                   f"(aynı {SEVIYE_ADI[ks]}deki tüm adaylar aynı puanı alır).")
        tablo_k.append({"olcu": o.id, "boyut": boy, "ad": o.ad, "yon": c["yon"], "agirlik": c["agirlik"], "neden": c.get("neden"),
                        "kaynak_seviye": SEVIYE_ADI[ks] + (" (devralındı)" if devr else ""), "donem": d,
                        "veri_olan_aday": len(x), "durum": "kullanıldı"})

    W = sum(c["agirlik"] for c in kriterler)

    def skorla(dislanan: int | None = None) -> dict[str, tuple[float, float]]:
        out = {}
        for g in adaylar:
            top, wsum, wtop = 0.0, 0.0, 0.0
            for j, c in enumerate(kriterler):
                if j == dislanan:
                    continue
                wtop += c["agirlik"]
                if g in puanlar[j]:
                    top += c["agirlik"] * puanlar[j][g]; wsum += c["agirlik"]
            if wtop and wsum / wtop >= p.min_kapsama:
                out[g] = (top / wsum, wsum / wtop)
        return out

    ana = skorla()
    if not ana:
        raise AnalizHatasi(f"Veri kapsaması %{int(p.min_kapsama * 100)} eşiğini geçen aday yok; min_kapsama'yı düşürün ya da kapsamı değiştirin.")
    sira = sorted(ana, key=lambda g: (-round(ana[g][0], 9), g))
    rank = {g: i + 1 for i, g in enumerate(sira)}
    aralik = {g: [rank[g], rank[g]] for g in sira[: p.limit]}
    for j in range(len(kriterler)):
        if not puanlar[j]:
            continue
        alt = skorla(j)
        s2 = sorted(alt, key=lambda g: (-round(alt[g][0], 9), g))
        r2 = {g: i + 1 for i, g in enumerate(s2)}
        for g in aralik:
            if g in r2:
                aralik[g][0] = min(aralik[g][0], r2[g]); aralik[g][1] = max(aralik[g][1], r2[g])

    satirlar = []
    for g in sira[: p.limit]:
        y = yerler[g]
        r = {"sira": rank[g], "yer": y.tam_ad, "skor": ana[g][0], "kapsama": 100 * ana[g][1],
             "sira_araligi": f"{aralik[g][0]}–{aralik[g][1]}", "lat": y.lat, "lon": y.lon, "geo_id": g}
        for j, c in enumerate(kriterler):
            r[f"k{j}"] = puanlar[j].get(g)
            r[f"h{j}"] = ham[g].get(j)
        satirlar.append(r)
    etiket = [f"{c['olcu']}{'[' + str(c.get('boyut')) + ']' if c.get('boyut') else ''}" for c in kriterler]
    sut = [Sutun("sira", "Sıra"), Sutun("yer", "Yer"), Sutun("skor", "Uygunluk skoru (0–100)"), Sutun("kapsama", "Veri kapsaması", "%"),
           Sutun("sira_araligi", "Sıra aralığı (kriter çıkarma testi)")] + \
          [Sutun(f"k{j}", f"{etiket[j]} ({'+' if c['yon'] == 'arti' else '−'}{tr_sayi(c['agirlik'])}) yüzdelik") for j, c in enumerate(kriterler)] + \
          [Sutun("geo_id", "geo_id")]
    ham_sut = [Sutun("sira", "Sıra"), Sutun("yer", "Yer")] + \
              [Sutun(f"h{j}", etiket[j], yukle().olcu(c["olcu"]).birim) for j, c in enumerate(kriterler)] + [Sutun("lat", "Enlem"), Sutun("lon", "Boylam")]
    ksut = [Sutun("ad", "Kriter"), Sutun("boyut", "Alt kırılım"), Sutun("yon", "Yön"), Sutun("agirlik", "Ağırlık"),
            Sutun("kaynak_seviye", "Veri seviyesi"), Sutun("donem", "Dönem"), Sutun("veri_olan_aday", "Verisi olan aday"),
            Sutun("durum", "Durum"), Sutun("neden", "Gerekçe (varsayım)")]
    kapsam_ad = k.tam_ad if k else "Türkiye"
    ilk = satirlar[:5]
    cumle = [f"{baslik} ({len([t for t in tablo_k if t.get('durum') == 'kullanıldı'])} kriter) ile {kapsam_ad} içindeki "
             f"{tr_sayi(len(adaylar))} aday {SEVIYE_ADI[p.seviye]}den {tr_sayi(len(ana))} tanesi skorlandı. İlk {len(ilk)}: " +
             "; ".join(f"{r['sira']}) {r['yer']} {tr_sayi(r['skor'], 1)} (sıra aralığı {r['sira_araligi']})" for r in ilk) + ".",
             "Skor, profil varsayımlarına göre göreli bir sıralamadır (talep, ciro veya kâr ölçümü değildir); ağırlıklar değiştirilerek yeniden hesaplanabilir."]
    b.kurallar.add("uygunluk_skoru")
    cozulen = {"analiz": "uygun_bolge", "profil": p.profil, "kriterler": [{**{kk: c.get(kk) for kk in ("olcu", "boyut", "yon", "agirlik")}} for c in kriterler],
               "seviye": p.seviye, "kapsam": k.geo_id if k else "GEO_TR", "limit": p.limit, "min_nufus": p.min_nufus, "min_kapsama": p.min_kapsama}
    return _sonuc("uygun_bolge", cozulen, b,
                  [Tablo("Uygun bölgeler", sut, satirlar), Tablo("Ham değerler (ilk sıradakiler)", ham_sut, satirlar),
                   Tablo("Kriterler ve varsayımlar", ksut, tablo_k)], cumle,
                  "Her kriterde aday küme içindeki yüzdelik sıra (eşitlere ortalama sıra; 'eksi' yönde 100 − yüzdelik); skor = ağırlıklı ortalama "
                  "(yalnız verisi olan kriterler; kapsama = bu kriterlerin ağırlık payı). Sıra aralığı: her kriter tek tek çıkarıldığında alınan en iyi–en kötü sıra.")


# =============================================================================================== projeksiyon çekirdeği
def _seri(o: Olcu, y_geo: str, seviye: str, boy: str | None, b: Baglam) -> list[tuple[str, float]]:
    desen = DONEM_DESEN.get(o.donem_turu[0]) if o.donem_turu else None
    w = f"WHERE regexp_matches(donem, '{desen}')" if desen else ""
    rows = b.q(f"SELECT donem, deger FROM ({veri.olcum_sql(o, seviye, boy, f'SELECT {veri.lit(y_geo)}')}) {w} ORDER BY donem")
    b.iz(rows)
    return [(r["donem"], r["deger"]) for r in rows]


def _kaynak_tahmini(o: Olcu, y_geo: str, seviye: str, b: Baglam) -> dict[str, float]:
    if o.kaynak.get("tablo") != "price_observation":
        return {}
    rows = b.q(f"SELECT donem, deger FROM ({veri.olcum_sql(o, seviye, None, f'SELECT {veri.lit(y_geo)}', projeksiyon=True)}) "
               "WHERE tur = 'projeksiyon' ORDER BY donem")
    return {r["donem"]: r["deger"] for r in rows}


def _enflasyon(dis: list[P.DisVarsayim]) -> dict[str, float]:
    return {d.donem: d.deger for d in dis if d.anahtar == "yillik_enflasyon" and d.donem and d.deger is not None}


def _reel(orta: list[float], donemler: list[str], son_donem: str, enf: dict[str, float]) -> list[float | None]:
    """Nominal tahmini son ölçülen dönemin fiyatlarına indirger. Aylık seride ay başına (1+π_yıl)^(1/12)."""
    out, carpan = [], 1.0
    aylik = bool(re.match(DONEM_DESEN["ay"], son_donem))
    for v, d in zip(orta, donemler):
        yil = d[:4]
        if yil not in enf:
            out.append(None); carpan = float("nan"); continue
        carpan *= (1 + enf[yil] / 100) ** (1 / 12 if aylik else 1)
        out.append(v / carpan if math.isfinite(carpan) else None)
    return out


def projeksiyon_hesapla(o: Olcu, y: Yer, ufuk: int | None, boy: str | None, dis: list[P.DisVarsayim], b: Baglam) -> dict:
    if o.turetilmis:
        raise AnalizHatasi(f"{o.ad} tek kesitlik türetilmiş bir ölçü; zaman serisi yok, projeksiyon yapılamaz.")
    if o.birim in ("%", "‰"):
        raise AnalizHatasi(f"{o.ad} bir oran/değişim ölçüsü; negatif değer alabildiği için doğrudan projekte edilmez. Seviye ölçüsünü "
                           "(ör. m² fiyatı) projekte edin; değişim oradan okunur.")
    if y.seviye not in o.seviyeler:
        raise AnalizHatasi(f"{o.ad} {SEVIYE_ADI[y.seviye]} seviyesinde yok (var olan: {', '.join(o.seviyeler)}).")
    seri = _seri(o, y.geo_id, y.seviye, boy, b)
    if len(seri) < 6:
        raise AnalizHatasi(f"{y.tam_ad} için {o.ad} serisi çok kısa ({len(seri)} dönem); projeksiyon için en az 6 dönem gerekir.")
    tur = pj.donem_indeks(seri[-1][0])[1]
    ufuk = ufuk or {"ay": 12, "yil": 5, "ceyrek": 4}[tur]
    _, _, adim, _ = pj.duzenli_seri(seri)
    if tur == "ay" and adim == 3 and ufuk > 12:
        ufuk = 12
    mevsimsel = tur == "ay" and adim == 1 and o.birim in ("adet", "daire")
    try:
        t = pj.tahmin_et(seri, ufuk, mevsimsel=mevsimsel)
    except ValueError as e:
        raise AnalizHatasi(str(e))
    for u in t.uyarilar:
        b.uyar(u)
    kal = KALIBRASYON.get((o.kaynak.get("tablo") == "price_observation", o.id == "nufus", y.seviye))
    if kal:
        b.uyar(kal)
    kt = _kaynak_tahmini(o, y.geo_id, y.seviye, b)
    enf = _enflasyon(dis)
    reel = _reel(t.orta, t.donemler, t.son_donem, enf) if enf and o.birim.startswith("TL") else None
    senaryo = None
    buy = [d for d in dis if d.anahtar == "nufus_yillik_buyume" and d.deger is not None]
    if buy and o.id == "nufus" and tur == "yil":
        g = buy[0].deger / 100
        senaryo = [t.son_deger * (1 + g) ** k for k in range(1, len(t.orta) + 1)]
    return {"olcu": o, "yer": y, "seri": seri, "t": t, "kaynak_tahmini": kt, "reel": reel, "senaryo": senaryo, "mevsimsel": mevsimsel,
            "adim": adim, "tur": tur}


def _dis_tablo(dis: list[P.DisVarsayim], b: Baglam) -> Tablo | None:
    if not dis:
        return None
    for d in dis:
        b.kaynaklar.add(f"dış:{d.kaynak_adi}")
    b.edinim.add("web_research (dış varsayım)")
    b.kurallar.add("dis_varsayim")
    return Tablo("Dış kaynak bilgileri (web / kullanıcı)", [Sutun("anahtar", "Tür"), Sutun("aciklama", "Açıklama"), Sutun("deger", "Değer"),
                                                           Sutun("birim", "Birim"), Sutun("donem", "Dönem"), Sutun("kaynak_adi", "Kaynak"),
                                                           Sutun("kaynak_url", "Adres"), Sutun("erisim_tarihi", "Erişim"), Sutun("alinti", "Alıntı")],
                 [d.model_dump() for d in dis], "Hesaba yalnız 'yillik_enflasyon' (reel dönüşüm) ve 'nufus_yillik_buyume' (senaryo) girer; 'baglam' bilgi amaçlıdır")


# =============================================================================================== gelecek_projeksiyonu
def gelecek_projeksiyonu(p: P.GelecekProjeksiyonu, b: Baglam) -> Sonuc:
    o = olcu_coz(p.olcu)
    _kural_notlari(o, b)
    boy = boyut_coz(o, p.boyut)
    y = yer_birim(p.yer, b)
    r = projeksiyon_hesapla(o, y, p.ufuk, boy, p.dis_varsayimlar, b)
    t: pj.Tahmin = r["t"]
    b.kurallar.add("projeksiyon_model")
    satir = []
    for i, d in enumerate(t.donemler):
        s = {"donem": d, "orta": t.orta[i], "alt80": t.alt80[i], "ust80": t.ust80[i], "kaynak_tahmini": r["kaynak_tahmini"].get(d)}
        if r["reel"]:
            s["reel"] = r["reel"][i]
        if r["senaryo"]:
            s["senaryo"] = r["senaryo"][i]
        satir.append(s)
    sut = [Sutun("donem", "Dönem"), Sutun("orta", "Orta tahmin", o.birim), Sutun("alt80", "%80 alt", o.birim), Sutun("ust80", "%80 üst", o.birim)]
    if r["kaynak_tahmini"]:
        sut.append(Sutun("kaynak_tahmini", "Kaynağın kendi tahmini", o.birim))
    if r["reel"]:
        sut.append(Sutun("reel", f"Reel orta ({t.son_donem} fiyatlarıyla)", o.birim))
    if r["senaryo"]:
        sut.append(Sutun("senaryo", "Dış büyüme senaryosu", o.birim))
    gecmis = [{"donem": d, "deger": v} for d, v in r["seri"][-24:]]
    model = [{"model": m, "agirlik": 100 * w, "gecmis_hata": t.model_hatalari.get(m)} for m, w in sorted(t.agirliklar.items(), key=lambda kv: -kv[1])]
    hata = [{"ufuk": k, "ortalama_hata": v} for k, v in t.topluluk_hatasi.items()]
    tablolar = [Tablo(f"{y.tam_ad} — {o.ad} projeksiyonu", sut, satir),
                Tablo("Son gözlemler", [Sutun("donem", "Dönem"), Sutun("deger", "Değer", o.birim)], gecmis),
                Tablo("Model topluluğu", [Sutun("model", "Model"), Sutun("agirlik", "Ağırlık", "%"), Sutun("gecmis_hata", "Geçmiş sınama hatası", "%")], model,
                      f"{t.sinama_sayisi} başlangıç noktasından geriye dönük sınama"),
                Tablo("Ufka göre geçmiş hata", [Sutun("ufuk", "Ufuk (dönem)"), Sutun("ortalama_hata", "Ortalama mutlak hata", "%")], hata)]
    dt = _dis_tablo(p.dis_varsayimlar, b)
    if dt:
        tablolar.append(dt)
    son = satir[-1]
    h = len(satir)
    hh = t.topluluk_hatasi.get(h) or (list(t.topluluk_hatasi.values())[-1] if t.topluluk_hatasi else None)
    cumle = [f"{y.tam_ad} — {ilk_kucuk(o.ad)}: son ölçülen değer {fmt(t.son_deger, o.birim)} ({t.son_donem}). "
             f"{son['donem']} için model topluluğunun orta tahmini {fmt(son['orta'], o.birim)} "
             f"(%80 aralık {fmt(son['alt80'], o.birim)} – {fmt(son['ust80'], o.birim)}; son değere göre %{tr_sayi(100 * (son['orta'] / t.son_deger - 1), 1)})."]
    if hh is not None:
        cumle.append(f"Geçmiş sınamada bu ufka yakın tahminlerin ortalama hatası %{tr_sayi(hh, 1)}.")
    if son.get("kaynak_tahmini") is not None:
        cumle.append(f"Kaynağın kendi tahmini ({son['donem']}): {fmt(son['kaynak_tahmini'], o.birim)}.")
    if son.get("reel") is not None:
        cumle.append(f"Dış enflasyon varsayımıyla reel orta tahmin ({t.son_donem} fiyatlarıyla): {fmt(son['reel'], o.birim)} "
                     f"(reel değişim %{tr_sayi(100 * (son['reel'] / t.son_deger - 1), 1)}).")
    elif _enflasyon(p.dis_varsayimlar) and o.birim.startswith("TL"):
        b.uyar("Enflasyon varsayımı projeksiyon ufkunun tüm yıllarını kapsamıyor; eksik yıllar için reel değer hesaplanmadı.")
    if son.get("senaryo") is not None:
        cumle.append(f"Dış büyüme senaryosu ({p.dis_varsayimlar[0].kaynak_adi if p.dis_varsayimlar else ''}) ile {son['donem']}: {fmt(son['senaryo'], o.birim)}.")
    b.uyar(PROJEKSIYON_UYARI)
    if r["mevsimsel"]:
        b.uyar("Aylık sayım serisi: mevsimsel modeller topluluğa dahil edildi.")
    b.donemler[o.id] = f"gözlem {r['seri'][0][0]} – {t.son_donem}; projeksiyon {t.donemler[0]} – {t.donemler[-1]}"
    cozulen = {"analiz": "gelecek_projeksiyonu", "yer": y.geo_id, "olcu": o.id, "ufuk": h, "boyut": boy,
               "dis_varsayimlar": [d.model_dump() for d in p.dis_varsayimlar]}
    return _sonuc("gelecek_projeksiyonu", cozulen, b, tablolar, cumle,
                  "Log uzayında model topluluğu (son dönem eğilimleri, sönümlü trend, düz; aylık sayımlarda mevsimsel); ağırlık = 1/geçmiş hata; "
                  "%80 aralık = topluluğun geriye dönük sınamadaki gerçek hata yüzdelikleri (yetersizse √ufuk genişletme).")


# =============================================================================================== bolge_gelecek_raporu
def bolge_gelecek_raporu(p: P.BolgeGelecekRaporu, b: Baglam) -> Sonuc:
    ont = yukle()
    y = yer_birim(p.yer, b)
    ilce = dizin().yerler.get(y.ust_geo_id) if y.seviye == "mahalle" else None
    il = dizin().yerler.get(y.il_geo_id) if y.seviye in ("mahalle", "ilce") else None
    b.kurallar.add("projeksiyon_model")
    yil_ufku = math.ceil(p.ufuk_ay / 12) + 2
    hedefler = [("nufus", yil_ufku), ("konut_satis_m2", p.ufuk_ay), ("konut_kira_m2", p.ufuk_ay), ("konut_ort_kira", p.ufuk_ay),
                ("arsa_satis_m2", p.ufuk_ay), ("konut_satis_adedi", p.ufuk_ay)]
    ozet, cumle = [], []
    for oid, uf in hedefler:
        o = ont.olcu(oid)
        _kural_notlari(o, b)
        aday_yer = [y] + ([ilce] if ilce else []) + ([il] if il else [])
        for yy in aday_yer:
            if yy.seviye not in o.seviyeler:
                continue
            try:
                r = projeksiyon_hesapla(o, yy, uf, None, p.dis_varsayimlar, b)
            except AnalizHatasi:
                continue
            t = r["t"]
            k = len(t.orta) - 1
            hh = t.topluluk_hatasi.get(k + 1) or (list(t.topluluk_hatasi.values())[-1] if t.topluluk_hatasi else None)
            satir = {"olcu": o.ad, "yer": yy.tam_ad, "seviye": SEVIYE_ADI[yy.seviye], "son_donem": t.son_donem, "son": t.son_deger,
                     "hedef_donem": t.donemler[k], "orta": t.orta[k], "alt80": t.alt80[k], "ust80": t.ust80[k],
                     "degisim": 100 * (t.orta[k] / t.son_deger - 1), "gecmis_hata": hh,
                     "kaynak_tahmini": r["kaynak_tahmini"].get(t.donemler[k]),
                     "reel_degisim": (100 * (r["reel"][k] / t.son_deger - 1) if r["reel"] and r["reel"][k] else None),
                     "birim": o.birim}
            if r["tur"] == "ay" and o.birim == "adet":
                # sayım serilerinde tek ay yerine ufuk boyunca toplam ve son 12 ay gerçekleşen
                satir["olcu"] = o.ad + " — ufuk toplamı (aralık: aylık aralıkların toplamı, tutucu)"
                satir["son"] = sum(v for _, v in r["seri"][-len(t.orta):])
                satir["orta"], satir["alt80"], satir["ust80"] = sum(t.orta), sum(t.alt80), sum(t.ust80)
                satir["degisim"] = 100 * (satir["orta"] / satir["son"] - 1) if satir["son"] else None
                satir["son_donem"] = f"son {len(t.orta)} ay"
                satir["hedef_donem"] = f"{t.donemler[0]}–{t.donemler[-1]}"
            ozet.append(satir)
            if yy is not y:
                b.uyar(f"{o.ad}: {SEVIYE_ADI[y.seviye]} düzeyinde seri yok/kısa; {yy.tam_ad} ({SEVIYE_ADI[yy.seviye]}) projeksiyonu verildi.")
            cumle.append(f"{o.ad} ({yy.tam_ad}): {satir['son_donem']} {fmt(satir['son'], o.birim)} → {satir['hedef_donem']} orta tahmin "
                         f"{fmt(satir['orta'], o.birim)} (%80: {fmt(satir['alt80'], o.birim)} – {fmt(satir['ust80'], o.birim)}).")
            break
    if not ozet:
        raise AnalizHatasi(f"{y.tam_ad} ve üst birimleri için projeksiyona uygun seri bulunamadı.")

    # öncü göstergeler (ölçüm; projeksiyon değil)
    oncu = []
    ref = y if y.seviye != "mahalle" else ilce
    for oid in ("yapi_ruhsati_daire", "yapi_kullanma_daire"):
        o = ont.olcu(oid)
        if ref and ref.seviye in o.seviyeler:
            for d, v in _seri(o, ref.geo_id, ref.seviye, None, b)[-4:]:
                oncu.append({"gosterge": o.ad, "yer": ref.tam_ad, "donem": d, "deger": v, "birim": o.birim})
    if il:
        for d, v in _seri(ont.olcu("goc_net"), il.geo_id, "il", None, b)[-4:]:
            oncu.append({"gosterge": "Net göç", "yer": il.tam_ad, "donem": d, "deger": v, "birim": "kişi"})
    elif y.seviye == "il":
        for d, v in _seri(ont.olcu("goc_net"), y.geo_id, "il", None, b)[-4:]:
            oncu.append({"gosterge": "Net göç", "yer": y.tam_ad, "donem": d, "deger": v, "birim": "kişi"})
    for oid, ad in (("harita_eklenen", "OSM haritaya eklenen (tüm)"), ("harita_cikan", "OSM haritadan çıkan (tüm)")):
        rows = b.q(f"SELECT donem, deger FROM ({veri.olcum_sql(ont.olcu(oid), y.seviye, 'all', f'SELECT {veri.lit(y.geo_id)}')}) ORDER BY donem")
        for rr in rows[-3:]:
            oncu.append({"gosterge": ad, "yer": y.tam_ad, "donem": rr["donem"], "deger": rr["deger"], "birim": "adet"})
    if oncu:
        b.kurallar.add("osm_semantigi")
    tablolar = [
        Tablo("Projeksiyon özeti", [Sutun("olcu", "Ölçü"), Sutun("yer", "Yer"), Sutun("son_donem", "Son dönem"), Sutun("son", "Son değer"),
                                    Sutun("hedef_donem", "Hedef dönem"), Sutun("orta", "Orta tahmin"), Sutun("alt80", "%80 alt"), Sutun("ust80", "%80 üst"),
                                    Sutun("degisim", "Değişim (orta)", "%"), Sutun("gecmis_hata", "Geçmiş hata (bu ufuk)", "%"),
                                    Sutun("kaynak_tahmini", "Kaynağın tahmini"), Sutun("reel_degisim", "Reel değişim (dış enflasyonla)", "%"),
                                    Sutun("birim", "Birim")], ozet),
        Tablo("Öncü göstergeler (ölçülmüş)", [Sutun("gosterge", "Gösterge"), Sutun("yer", "Yer"), Sutun("donem", "Dönem"), Sutun("deger", "Değer"),
                                              Sutun("birim", "Birim")], oncu,
              "Ruhsat → gelecekteki konut arzı; kullanma izni → tamamlanan arz; göç → talep; OSM → harita değişimi (açılış/kapanış değildir)"),
    ]
    dt = _dis_tablo(p.dis_varsayimlar, b)
    if dt:
        tablolar.append(dt)
    b.uyar(PROJEKSIYON_UYARI)
    b.donemler["projeksiyon ufku"] = f"aylık {p.ufuk_ay} ay; yıllık {yil_ufku} yıl"
    cozulen = {"analiz": "bolge_gelecek_raporu", "yer": y.geo_id, "ufuk_ay": p.ufuk_ay, "dis_varsayimlar": [d.model_dump() for d in p.dis_varsayimlar]}
    return _sonuc("bolge_gelecek_raporu", cozulen, b, tablolar, cumle,
                  "Her ölçü gelecek_projeksiyonu yöntemiyle; yerin kendi serisi yoksa/kısaysa en yakın üst birimin serisi kullanılır ve belirtilir.")


KATALOG.update({"uygun_bolge": uygun_bolge, "gelecek_projeksiyonu": gelecek_projeksiyonu, "bolge_gelecek_raporu": bolge_gelecek_raporu})
