"""Motor: plan (dict/JSON) → doğrula → analiz → Sonuc. Tek giriş noktası; tüm arayüzler (CLI, MCP, API) bunu çağırır."""
from __future__ import annotations

import time
from functools import lru_cache

from pydantic import ValidationError

from . import __version__, analizler, analizler_ileri, veri  # noqa: F401 (ileri modüller KATALOG'a kaydolur)
from . import plan as P
from .ontology import yukle
from .sonuc import AnalizHatasi, Sonuc


@lru_cache(maxsize=1)
def veri_surumu() -> dict:
    m = veri.meta()
    return {"kanonik": m.get("model_version"), "kanonik_kod": (m.get("code_hash_v1_5") or "")[:16],
            "gozlem_kesimi": m.get("observation_cutoff"), "ontoloji": f"{yukle().surum}/{yukle().parmak_izi()}", "motor": __version__}


def calistir(plan: dict) -> Sonuc:
    t0 = time.time()
    analiz = plan.get("analiz", "?") if isinstance(plan, dict) else "?"
    try:
        p = P.dogrula(plan)
    except ValidationError as e:
        hatalar = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        return Sonuc(analiz=analiz, plan=plan if isinstance(plan, dict) else {}, durum="gecersiz_plan", uyarilar=hatalar,
                     veri_surumu=veri_surumu(), cumleler=["Plan şemaya uymuyor; analiz çalıştırılmadı."])
    b = analizler.Baglam()
    try:
        s = analizler.KATALOG[p.analiz](p, b)
    except AnalizHatasi as e:
        s = Sonuc(analiz=p.analiz, plan=p.model_dump(), durum=e.tur, uyarilar=[*b.uyarilar, str(e)], veri_surumu=veri_surumu(),
                  cumleler=[str(e)], sorgular=b.sorgular)
        if e.adaylar:
            s.kunye["adaylar"] = e.adaylar
    s.veri_surumu = veri_surumu()
    s.sure_sn = round(time.time() - t0, 2)
    return s


def isit() -> None:
    """Sunucu açılışında bir kez: yer dizini, ontoloji ve poligon alanları belleğe alınır (ilk soru hızlanır)."""
    from .yer import dizin
    yukle()
    dizin()
    for sev in ("mahalle", "ilce", "il"):
        veri._alan_tablosu(sev)
    veri_surumu()
