"""Ontoloji yükleyici: ontology.yaml → düz ölçü kataloğu + eş anlam dizinleri.

Deterministik: aynı YAML → aynı katalog (sıralı sözlükler, sabit parmak izi).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import yaml

from .paths import ONTOLOGY
from .textnorm import fold

SEVIYELER = ("mahalle", "ilce", "il", "ulke")
SEVIYE_ADI = {"mahalle": "mahalle", "ilce": "ilçe", "il": "il", "ulke": "ülke"}


@dataclass
class Olcu:
    id: str
    ad: str
    birim: str
    seviyeler: list[str]
    donem_turu: list[str]
    tema: str
    toplanabilir: bool = False
    esanlam: list[str] = field(default_factory=list)
    boyut: str | None = None          # ör. sektor, bkm_sektor, bina_turu, osm_kategori
    kaynak: dict = field(default_factory=dict)  # serving build eşlemesi
    olcek: str | None = None
    turetilmis: bool = False
    formul: str | None = None
    not_: str | None = None

    def ozet(self) -> dict:
        d = asdict(self)
        d["not"] = d.pop("not_")
        d.pop("kaynak")
        return {k: v for k, v in d.items() if v not in (None, [], {})}


def _liste(x) -> list[str]:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


class Ontoloji:
    def __init__(self, raw: dict):
        self.raw = raw
        self.surum = raw["surum"]
        self.olculer: dict[str, Olcu] = {}
        self._yukle_fiyat(raw["fiyat_sablonlari"])
        self._yukle_diger(raw["diger_olculer"])
        self._yukle_banka(raw["banka_olculeri"])
        self._yukle_turetilmis(raw["turetilmis_olculer"])
        self.kurallar = {k["id"]: k for k in raw["kurallar"]}
        self.analizler = dict(raw["analizler"])
        self.sektorler = dict(raw["isletme_sektorleri"])
        self.harita_esanlam = dict(raw["harita_nesne_esanlam"])
        self.olcek_kurallari = raw["olcek_kurallari"]
        self._esanlam_dizini()

    # ------------------------------------------------------------------ yükleme
    def _ekle(self, o: Olcu):
        if o.id in self.olculer:
            raise ValueError(f"Ontolojide yinelenen ölçü kimliği: {o.id}")
        self.olculer[o.id] = o

    def _yukle_fiyat(self, fs: dict):
        kats = fs["kategoriler"]
        disi = {(d["kategori"], d["metric"]) for d in fs.get("katalog_disi", [])}
        for metric, m in fs["metrikler"].items():
            for k in m["kategoriler"]:
                if (k, metric) in disi:
                    continue
                K = kats[k]["ad"]
                oid = m["id"].format(k=k)
                es = []
                for e in m.get("esanlam", []):  # kategori eş anlamları da açılır: '{k} fiyatı' → 'ev fiyatı', 'daire fiyatı'
                    for kad in [k, *kats[k].get("esanlam", [])]:
                        v = e.format(k=kad, K=K)
                        if v not in es:
                            es.append(v)
                self._ekle(Olcu(
                    id=oid, ad=m["ad"].format(k=k, K=K), birim=m["birim"],
                    seviyeler=["mahalle", "ilce", "il"], donem_turu=["ay"], tema=f"fiyat_{k}",
                    toplanabilir=bool(m.get("toplanabilir", False)), esanlam=es,
                    kaynak={"tablo": "price_observation", "category": k, "metric": metric},
                    olcek=m.get("olcek"),
                    not_="İlan verisi (web araştırması). 2026-08 sonrası aylar kaynağın projeksiyonudur, varsayılan dışlanır.",
                ))

    def _yukle_diger(self, d: dict):
        for oid, m in d.items():
            self._ekle(Olcu(
                id=oid, ad=m["ad"], birim=str(m["birim"]), seviyeler=_liste(m["seviyeler"]),
                donem_turu=_liste(m.get("donem_turu")), tema=m.get("tema", "genel"),
                toplanabilir=bool(m.get("toplanabilir", False)), esanlam=_liste(m.get("esanlam")),
                boyut=m.get("boyut"), kaynak=m.get("kaynak", {"tablo": "population_observation"} if oid == "nufus" else {}),
                not_=m.get("not"),
            ))

    def _yukle_banka(self, b: dict):
        for metric, m in b["liste"].items():
            self._ekle(Olcu(
                id=m["id"], ad=m["ad"], birim=str(m["birim"]), seviyeler=list(b["seviyeler"]),
                donem_turu=[b["donem_turu"]], tema=b["tema"], toplanabilir=bool(m.get("toplanabilir", False)),
                esanlam=_liste(m.get("esanlam")), kaynak={"domain": b["kaynak_domain"], "metric": metric},
                not_="BDDK FinTürk il verisi (çeyrek sonu ayları).",
            ))

    def _yukle_turetilmis(self, t: dict):
        for oid, m in t.items():
            self._ekle(Olcu(
                id=oid, ad=m["ad"], birim=str(m["birim"]), seviyeler=_liste(m["seviyeler"]),
                donem_turu=["kesit"], tema=m.get("tema", "turetilmis"), toplanabilir=bool(m.get("toplanabilir", False)),
                esanlam=_liste(m.get("esanlam")), turetilmis=True, formul=m.get("formul"),
                boyut={"isletme_sayisi": "sektor|kategori", "bin_kisi_basina_isletme": "sektor|kategori",
                       "km2_basina_isletme": "sektor|kategori", "ortalama_puan": "sektor|kategori",
                       "harita_nesnesi_sayisi": "osm_kategori", "harita_eklenen": "osm_grup", "harita_cikan": "osm_grup",
                       "harita_mevcut": "osm_grup", "harita_devir_hizi": "osm_grup"}.get(oid),
                not_=m.get("donem"),
            ))

    def _esanlam_dizini(self):
        self.esanlam: dict[str, str] = {}
        cakisma: dict[str, set] = {}
        for o in self.olculer.values():
            for anahtar in [o.id, o.ad, *o.esanlam]:
                f = fold(anahtar.replace("_", " "))
                if not f:
                    continue
                if f in self.esanlam and self.esanlam[f] != o.id:
                    cakisma.setdefault(f, {self.esanlam[f]}).add(o.id)
                self.esanlam.setdefault(f, o.id)
        # çakışan eş anlamlar kesin eşlemeden çıkarılır (belirsiz → aday listesi)
        for f in cakisma:
            self.esanlam.pop(f, None)
        self.cakisan_esanlam = {k: sorted(v) for k, v in cakisma.items()}
        self.sektor_dizini = {}
        for s, es in self.sektorler.items():
            for e in [s, *es]:
                self.sektor_dizini.setdefault(fold(e), s)
        self.harita_dizini = {}
        for k, es in self.harita_esanlam.items():
            for e in [k.replace("_", " "), *es]:
                self.harita_dizini.setdefault(fold(e), k)

    # ------------------------------------------------------------------ sorgu
    def olcu_bul(self, metin: str) -> tuple[str | None, list[str]]:
        """Kesin eşleşme → (id, []); değilse (None, adaylar). Adaylar deterministik sıralıdır."""
        if metin in self.olculer:
            return metin, []
        f = fold(metin.replace("_", " "))
        if f in self.esanlam:
            return self.esanlam[f], []
        if f in self.cakisan_esanlam:
            return None, self.cakisan_esanlam[f]
        jet = set(f.split())
        puan = []
        for anahtar, oid in self.esanlam.items():
            ortak = len(jet & set(anahtar.split()))
            if ortak:
                puan.append((-ortak / max(len(jet), len(anahtar.split())), oid))
        adaylar = []
        for _, oid in sorted(puan):
            if oid not in adaylar:
                adaylar.append(oid)
        return None, adaylar[:8]

    def olcu(self, oid: str) -> Olcu:
        if oid not in self.olculer:
            raise KeyError(oid)
        return self.olculer[oid]

    def parmak_izi(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def katalog(self, tema: str | None = None) -> list[dict]:
        return [o.ozet() for o in self.olculer.values() if tema is None or o.tema == tema]


@lru_cache(maxsize=1)
def yukle() -> Ontoloji:
    with open(ONTOLOGY, encoding="utf-8") as f:
        return Ontoloji(yaml.safe_load(f))
