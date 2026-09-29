"""Araç tanımları — MCP sunucusu ve Claude API sohbeti AYNI araçları kullanır.

Araçlar: ontoloji_ara, yer_coz ve katalogdaki her analiz için bir araç (girdi şeması plan.py'den).
Her analiz aracı: girdi → {"analiz": <ad>, **girdi} planı → motor.calistir → metin (markdown + JSON özet).
"""
from __future__ import annotations

import copy
import json

from . import plan as P
from .motor import calistir
from .ontology import yukle
from .textnorm import fold
from .yer import coz as yer_coz_


def _sema(model) -> dict:
    s = copy.deepcopy(model.model_json_schema())
    s["properties"].pop("analiz", None)
    s["required"] = [r for r in s.get("required", []) if r != "analiz"]
    s.pop("title", None)
    for v in s["properties"].values():
        v.pop("title", None)
    return s


def arac_tanimlari() -> list[dict]:
    """[{name, description, input_schema}] — Anthropic 'tools' biçimi (MCP'de inputSchema adıyla)."""
    ont = yukle()
    tanimlar = [
        {"name": "ontoloji_ara",
         "description": "Doğal dildeki bir ifadeye karşılık gelen ölçüleri (kimlik, ad, birim, seviyeler, dönem, not) bulur. "
                        "Boş metinle çağrılırsa temalara göre tüm ölçü kataloğunu, analiz türlerini, işletme sektörlerini ve "
                        "harita nesnesi türlerini döndürür. Her analizden önce ölçü kimliğini bununla doğrula.",
         "input_schema": {"type": "object", "properties": {
             "metin": {"type": "string", "description": "Aranan ifade, ör. 'kira getirisi', 'nüfus', 'konut satışı'. Boş: katalog."},
             "tema": {"type": "string", "description": "İsteğe bağlı tema süzgeci (fiyat_konut, demografi, bankacilik, ticaret...)"}},
             "required": ["metin"], "additionalProperties": False}},
        {"name": "yer_coz",
         "description": "Yer adını (veya 'enlem, boylam') veritabanındaki kimliğe çevirir. Sonuç 'kesin', 'belirsiz' (aday listesi) "
                        "veya 'bulunamadi' olur. Belirsizse kullanıcıya adayları sor; kendin seçme.",
         "input_schema": {"type": "object", "properties": {
             "metin": {"type": "string", "description": "ör. 'Bostanlı, Karşıyaka, İzmir' veya '40.98, 29.03'"},
             "seviye": {"type": "string", "enum": ["mahalle", "ilce", "il"], "description": "İsteğe bağlı seviye kısıtı"}},
             "required": ["metin"], "additionalProperties": False}},
    ]
    for ad, model in P.PLAN_MODELLERI.items():
        aciklama = (model.__doc__ or "").strip() + " — " + ont.analizler.get(ad, "")
        tanimlar.append({"name": ad, "description": aciklama.strip(" —"), "input_schema": _sema(model)})
    return tanimlar


def ontoloji_ara(metin: str, tema: str | None = None) -> dict:
    ont = yukle()
    if not metin.strip():
        temalar: dict[str, list] = {}
        for o in ont.olculer.values():
            if tema and o.tema != tema:
                continue
            temalar.setdefault(o.tema, []).append({"id": o.id, "ad": o.ad, "birim": o.birim, "seviyeler": o.seviyeler})
        from .analizler_ileri import profil_ozeti
        return {"olculer": temalar, "analizler": ont.analizler, "uygun_bolge_profilleri": profil_ozeti(),
                "isletme_sektorleri": sorted(ont.sektorler),
                "harita_nesnesi_turleri": sorted(ont.harita_esanlam), "kurallar": {k: v["tanim"] for k, v in ont.kurallar.items()}}
    from .analizler_ileri import profil_ozeti
    prof = {k: v for k, v in profil_ozeti().items() if fold(metin) in (fold(k.replace("_", " ")), fold(v["ad"]))
            or fold(k.replace("_", " ")) in fold(metin)}
    oid, adaylar = ont.olcu_bul(metin)
    ids = [oid] if oid else adaylar
    if tema:
        ids = [i for i in ids if ont.olcu(i).tema == tema]
    sonuc = {"durum": "kesin" if oid else ("aday" if ids else "yok"), "olculer": [ont.olcu(i).ozet() for i in ids]}
    f = fold(metin)
    if f in ont.sektor_dizini:
        sonuc["isletme_sektoru"] = ont.sektor_dizini[f]
    if f in ont.harita_dizini:
        sonuc["harita_nesnesi_turu"] = ont.harita_dizini[f]
    if prof:
        sonuc["uygun_bolge_profilleri"] = prof
    if not ids:
        sonuc["not"] = "Bu ifadeye karşılık gelen ölçü ontolojide yok; veri tabanında bu bilgi bulunmuyor olabilir."
    return sonuc


def calistir_arac(ad: str, girdi: dict) -> tuple[str, bool]:
    """(metin, hata_mi). Metin: insan-okur markdown + makine-okur JSON özet."""
    try:
        if ad == "ontoloji_ara":
            return json.dumps(ontoloji_ara(girdi.get("metin", ""), girdi.get("tema")), ensure_ascii=False, indent=1), False
        if ad == "yer_coz":
            return json.dumps(yer_coz_(girdi["metin"], girdi.get("seviye")).sozluk(), ensure_ascii=False, indent=1), False
        if ad not in P.PLAN_MODELLERI:
            return f"Bilinmeyen araç: {ad}", True
        s = calistir({"analiz": ad, **girdi})
        ozet = {"durum": s.durum, "plan": s.plan, "plan_hash": s.plan_hash, "sonuc_hash": s.sonuc_hash(),
                "veri_surumu": s.veri_surumu, "sure_sn": s.sure_sn}
        if "adaylar" in s.kunye:
            ozet["adaylar"] = s.kunye["adaylar"]
        return s.markdown() + "\n\n```json\n" + json.dumps(ozet, ensure_ascii=False, default=str) + "\n```", s.durum != "tamam"
    except Exception as e:  # araç hatası modele iletilir; süreç çökmez
        return f"Araç hatası ({type(e).__name__}): {e}", True
