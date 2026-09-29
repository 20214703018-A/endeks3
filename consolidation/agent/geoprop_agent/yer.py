"""Yer çözücü: serbest metin → geo_id. Deterministik; belirsizlikte TAHMİN ETMEZ, aday listesi döner.

Kurallar (sırayla):
  1. geo_id doğrudan verilirse aynen kullanılır.
  2. 'enlem, boylam' verilirse nokta olarak döner (içinde kaldığı mahalle poligonla bulunur).
  3. Metin virgüllerle ya da ardışık kelime gruplarıyla parçalanır; her parça yer adlarıyla (Türkçe harf katlanmış) eşlenir.
     Aday puanı = adayın kendisi + üst birimlerinden (ilçe, il) metinde ayrı parçalarla eşleşenlerin sayısı.
     En yüksek puan kazanır; eşitlikte daha üst seviye (il > ilçe > mahalle) seçilir;
     aynı seviyede birden çok eşit aday kalırsa BELİRSİZ döner.
  4. 'mahallesi', 'ilçesi', 'ili' gibi sözcükler seviye ipucu olarak kullanılır.
  5. Kesin eşleşme yoksa yazım benzerliğiyle öneri listesi döner (otomatik seçim yok).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from functools import lru_cache

from . import veri
from .ontology import SEVIYE_ADI
from .textnorm import fold, norm, norm_yer

SEVIYE_SIRA = {"ulke": 0, "il": 1, "ilce": 2, "mahalle": 3}
_SEVIYE_SOZCUK = {"mahallesi", "mah", "mh", "mahalle", "ilcesi", "ili", "koyu", "koy"}
_KOORD = re.compile(r"^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$")


@dataclass
class Yer:
    geo_id: str
    seviye: str
    ad: str
    ilce: str | None
    il: str | None
    il_geo_id: str | None
    ust_geo_id: str | None
    lat: float | None
    lon: float | None
    eslesme_bandi: str | None = None   # TKGM bağ bandı (mahalle)
    bayraklar: str | None = None

    @property
    def tam_ad(self) -> str:
        parca = [self.ad]
        if self.seviye == "mahalle" and self.ilce:
            parca.append(self.ilce)
        if self.seviye in ("mahalle", "ilce") and self.il:
            parca.append(self.il)
        return ", ".join(parca)

    def sozluk(self) -> dict:
        return {"geo_id": self.geo_id, "seviye": self.seviye, "ad": self.tam_ad, "lat": self.lat, "lon": self.lon}


@dataclass
class Nokta:
    lat: float
    lon: float
    mahalle: Yer | None = None

    def sozluk(self) -> dict:
        return {"nokta": [self.lat, self.lon], "mahalle": self.mahalle.sozluk() if self.mahalle else None}


@dataclass
class Cozum:
    durum: str                      # 'kesin' | 'belirsiz' | 'bulunamadi'
    yer: Yer | Nokta | None = None
    adaylar: list[Yer] = field(default_factory=list)
    uyarilar: list[str] = field(default_factory=list)

    def sozluk(self) -> dict:
        return {"durum": self.durum, "yer": self.yer.sozluk() if self.yer else None,
                "adaylar": [a.sozluk() for a in self.adaylar], "uyarilar": self.uyarilar}


class YerDizini:
    def __init__(self):
        rows = veri.q("""SELECT g.geo_id, g.level, g.name, g.parent_geo_id, g.il_geo_id, g.centroid_lat, g.centroid_lon,
                g.tkgm_link_band, g.link_flags, p.name AS ust_ad, i.name AS il_ad
            FROM k.main.geo_entity g
            LEFT JOIN k.main.geo_entity p ON p.geo_id = g.parent_geo_id
            LEFT JOIN k.main.geo_entity i ON i.geo_id = g.il_geo_id
            ORDER BY g.geo_id""")
        self.yerler: dict[str, Yer] = {}
        self.anahtar: dict[str, list[str]] = {}
        for r in rows:
            lvl = r["level"]
            y = Yer(geo_id=r["geo_id"], seviye=lvl, ad=r["name"],
                    ilce=r["ust_ad"] if lvl == "mahalle" else (r["name"] if lvl == "ilce" else None),
                    il=r["il_ad"] if lvl in ("mahalle", "ilce") else (r["name"] if lvl == "il" else None),
                    il_geo_id=r["il_geo_id"], ust_geo_id=r["parent_geo_id"], lat=r["centroid_lat"], lon=r["centroid_lon"],
                    eslesme_bandi=r["tkgm_link_band"] if lvl == "mahalle" else None, bayraklar=r["link_flags"])
            self.yerler[y.geo_id] = y
            for a in {norm_yer(y.ad), norm(y.ad)}:
                if a:
                    self.anahtar.setdefault(a, []).append(y.geo_id)
        for a in ("turkiye", "tr", "ulke", "tumturkiye"):
            self.anahtar.setdefault(a, []).extend(g for g, y in self.yerler.items() if y.seviye == "ulke")
        self._anahtar_listesi = sorted(self.anahtar)

    def atalar(self, y: Yer) -> list[str]:
        out = []
        if y.seviye == "mahalle" and y.ust_geo_id:
            out.append(y.ust_geo_id)
        if y.seviye in ("mahalle", "ilce") and y.il_geo_id:
            out.append(y.il_geo_id)
        return out

    # ------------------------------------------------------------------------------------------
    def coz(self, metin: str, seviye: str | None = None) -> Cozum:
        metin = (metin or "").strip()
        if not metin:
            return Cozum("bulunamadi", uyarilar=["Yer adı boş"])
        if metin in self.yerler:
            return Cozum("kesin", self.yerler[metin])
        m = _KOORD.match(metin)
        if m:
            lat, lon = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
            return self.nokta(lat, lon)

        ipucu = seviye
        f = fold(metin)
        if ipucu is None:
            if re.search(r"\b(mahallesi|mah|mh|koyu)\b", f):
                ipucu = "mahalle"
            elif re.search(r"\bilcesi\b", f):
                ipucu = "ilce"
            elif re.search(r"\bili\b", f):
                ipucu = "il"

        parcalar = [p for p in re.split(r"[,/;]| - ", metin) if p.strip()]
        spans = []   # (baş, son, anahtar)
        if len(parcalar) > 1:
            for i, p in enumerate(parcalar):
                spans.append((i, i + 1, norm_yer(p)))
        else:
            tok = fold(metin).split()
            for i in range(len(tok)):
                for j in range(i + 1, min(len(tok), i + 5) + 1):
                    spans.append((i, j, norm_yer(" ".join(tok[i:j]))))

        eslesen: dict[str, list[tuple[int, int]]] = {}
        for bas, son, a in spans:
            for gid in self.anahtar.get(a, []):
                eslesen.setdefault(gid, []).append((bas, son))
        if not eslesen:
            return self._oneri(metin, ipucu)

        puanli = []
        for gid, araliklar in eslesen.items():
            y = self.yerler[gid]
            if ipucu and y.seviye != ipucu:
                continue
            for (bas, son) in araliklar:
                kullanilan = [(bas, son)]
                puan = 1
                for ata in self.atalar(y):
                    for ab, as_ in eslesen.get(ata, []):
                        if all(as_ <= b or ab >= s for b, s in kullanilan):
                            kullanilan.append((ab, as_))
                            puan += 1
                            break
                kapsanan = sum(s - b for b, s in kullanilan)
                puanli.append((puan, kapsanan, -SEVIYE_SIRA[y.seviye], gid, tuple(sorted(kullanilan))))
        if not puanli:
            return self._oneri(metin, ipucu)
        puanli.sort(reverse=True)
        en = puanli[0]
        esit = sorted({p[3] for p in puanli if p[:3] == en[:3]})
        if len(esit) == 1:
            y = self.yerler[esit[0]]
            uy = []
            kullanilan = [p[4] for p in puanli if p[3] == y.geo_id][0]
            birimler = parcalar if len(parcalar) > 1 else fold(metin).split()
            artan = [b.strip() for i, b in enumerate(birimler)
                     if not any(bb <= i < ss for bb, ss in kullanilan) and norm_yer(b)]
            artan = [a for a in artan if fold(a) not in _SEVIYE_SOZCUK]
            if artan:
                uy.append(f"Metnin şu kısmı hiçbir resmî yer adıyla eşleşmedi ve yok sayıldı: {', '.join(artan)}. "
                          "Semt adları (resmî mahalle olmayan) veritabanında yoktur; resmî mahalle adını kullanın.")
            if y.seviye == "mahalle" and en[0] == 1 and len(birimler) == 1:
                uy.append(f"Yalnız mahalle adıyla eşleşti: {y.tam_ad}. Başka bir yer kastedildiyse ilçe/il ekleyin.")
            digerleri = sorted({p[3] for p in puanli if p[3] != y.geo_id and p[0] == en[0]})
            if digerleri:
                uy.append(f"'{metin}' → {y.tam_ad} ({SEVIYE_ADI[y.seviye]}) seçildi; aynı adla {len(digerleri)} başka yer daha var "
                          "(başkası kastedildiyse ilçe/il ekleyin).")
            if y.seviye == "mahalle" and y.eslesme_bandi in ("review_recommended", "no_auto_merge", "no_candidate"):
                uy.append(f"{y.tam_ad}: resmî kayıt (TKGM) eşleşmesi '{y.eslesme_bandi}' — insan onayı bekliyor.")
            return Cozum("kesin", y, adaylar=[self.yerler[g] for g in digerleri[:5]], uyarilar=uy)
        return Cozum("belirsiz", adaylar=[self.yerler[g] for g in esit[:15]],
                     uyarilar=[f"'{metin}' {len(esit)} farklı yerle eşleşti; ilçe/il ekleyin ya da geo_id seçin."])

    def _oneri(self, metin: str, ipucu: str | None) -> Cozum:
        a = norm_yer(metin)
        yakin = difflib.get_close_matches(a, self._anahtar_listesi, n=8, cutoff=0.8)
        ad = []
        for k in yakin:
            for gid in self.anahtar[k]:
                y = self.yerler[gid]
                if (not ipucu or y.seviye == ipucu) and gid not in ad:
                    ad.append(gid)
        ad.sort(key=lambda g: (SEVIYE_SIRA[self.yerler[g].seviye], g))
        return Cozum("bulunamadi", adaylar=[self.yerler[g] for g in ad[:10]],
                     uyarilar=[f"'{metin}' için kesin eşleşme yok." + (" Yazımı benzer adaylar listelendi." if ad else "")])

    def nokta(self, lat: float, lon: float) -> Cozum:
        if not (35.5 <= lat <= 42.5 and 25.5 <= lon <= 45.0):
            return Cozum("bulunamadi", uyarilar=[f"({lat}, {lon}) Türkiye sınırları dışında; enlem/boylam sırası ters olabilir."])
        r = veri.q("""SELECT geo_id FROM k.main.geo_entity WHERE level = 'mahalle'
              AND bbox_xmin <= ? AND bbox_xmax >= ? AND bbox_ymin <= ? AND bbox_ymax >= ?
              AND ST_Contains(geometry, ST_Point(?, ?)) ORDER BY geo_id""", [lon, lon, lat, lat, lon, lat])
        mah = self.yerler[r[0]["geo_id"]] if r else None
        uy = [] if mah else ["Nokta hiçbir mahalle poligonunun içinde değil (kıyı/sınır dışı olabilir)."]
        if len(r) > 1:
            uy.append(f"Nokta {len(r)} mahalle poligonunun içinde (çakışan poligon); ilki seçildi.")
        return Cozum("kesin", Nokta(lat, lon, mah), uyarilar=uy)


@lru_cache(maxsize=1)
def dizin() -> YerDizini:
    return YerDizini()


def coz(metin: str, seviye: str | None = None) -> Cozum:
    return dizin().coz(metin, seviye)
