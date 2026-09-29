"""Sonuç nesnesi: tablo + kurallı cümleler + uyarılar + künye. Aynı plan + aynı veri sürümü → aynı sonuç."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass, field

from . import __version__
from .textnorm import tr_sayi


class AnalizHatasi(Exception):
    """Plan çalıştırılamadı (veri yok, belirsiz yer vb.). Mesaj kullanıcıya gösterilir; adaylar varsa eklenir."""

    def __init__(self, mesaj: str, adaylar: list | None = None, tur: str = "hata"):
        super().__init__(mesaj)
        self.adaylar = adaylar or []
        self.tur = tur


def yuvarla(x):
    """Deterministik çıktı: float'lar 6 anlamlı basamağa (paralel toplama farklarını siler)."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        if x == 0:
            return 0.0
        return float(f"{x:.6g}") if abs(x) < 1e6 else round(x, 2)
    return x


@dataclass
class Sutun:
    ad: str
    etiket: str
    birim: str | None = None


@dataclass
class Tablo:
    ad: str
    sutunlar: list[Sutun]
    satirlar: list[dict]
    aciklama: str | None = None

    def sozluk(self) -> dict:
        return {"ad": self.ad, "aciklama": self.aciklama,
                "sutunlar": [s.__dict__ for s in self.sutunlar],
                "satirlar": [{k: yuvarla(v) for k, v in r.items()} for r in self.satirlar]}

    def markdown(self, en_fazla: int = 60) -> str:
        bas = "| " + " | ".join(s.etiket + (f" ({s.birim})" if s.birim else "") for s in self.sutunlar) + " |"
        ayr = "|" + "|".join("---" for _ in self.sutunlar) + "|"
        satir = []
        for r in self.satirlar[:en_fazla]:
            h = []
            for s in self.sutunlar:
                v = r.get(s.ad)
                h.append(tr_sayi(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else ("—" if v is None else str(v)))
            satir.append("| " + " | ".join(h) + " |")
        ek = [f"_… {len(self.satirlar) - en_fazla} satır daha_"] if len(self.satirlar) > en_fazla else []
        return "\n".join([f"**{self.ad}**" + (f" — {self.aciklama}" if self.aciklama else ""), bas, ayr, *satir, *ek])


@dataclass
class Sonuc:
    analiz: str
    plan: dict                       # çözülmüş plan (geo_id'ler, ölçü kimlikleri, somut dönemler)
    tablolar: list[Tablo] = field(default_factory=list)
    cumleler: list[str] = field(default_factory=list)
    uyarilar: list[str] = field(default_factory=list)
    kunye: dict = field(default_factory=dict)
    sorgular: list[str] = field(default_factory=list)
    veri_surumu: dict = field(default_factory=dict)
    durum: str = "tamam"
    sure_sn: float | None = None

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(json.dumps({"plan": self.plan, "veri": self.veri_surumu}, sort_keys=True,
                                         ensure_ascii=False, default=str).encode()).hexdigest()[:16]

    def sonuc_hash(self) -> str:
        """Sayısal içeriğin parmak izi (deterministiklik testi için; süre ve zaman damgası hariç)."""
        icerik = {"t": [t.sozluk() for t in self.tablolar], "c": self.cumleler, "u": self.uyarilar}
        return hashlib.sha256(json.dumps(icerik, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]

    def sozluk(self, sorgular: bool = False) -> dict:
        d = {"durum": self.durum, "analiz": self.analiz, "plan": self.plan, "plan_hash": self.plan_hash,
             "sonuc_hash": self.sonuc_hash(), "veri_surumu": self.veri_surumu,
             "cumleler": self.cumleler, "uyarilar": self.uyarilar, "kunye": self.kunye,
             "tablolar": [t.sozluk() for t in self.tablolar], "sure_sn": self.sure_sn,
             "uretildi": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        if sorgular:
            d["sorgular"] = self.sorgular
        return d

    def markdown(self) -> str:
        parca = [f"### {self.analiz}  ·  plan `{self.plan_hash}`"]
        if self.cumleler:
            parca.append("\n".join(f"- {c}" for c in self.cumleler))
        for t in self.tablolar:
            parca.append(t.markdown())
        if self.uyarilar:
            parca.append("**Uyarılar**\n" + "\n".join(f"- ⚠️ {u}" for u in self.uyarilar))
        if self.kunye:
            parca.append("**Künye**\n" + "\n".join(f"- {k}: {v}" for k, v in self.kunye.items()))
        parca.append(f"_Veri: {self.veri_surumu.get('kanonik')} · ontoloji {self.veri_surumu.get('ontoloji')} · motor {__version__}_")
        return "\n\n".join(parca)
