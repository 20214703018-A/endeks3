"""Altın plan testleri: durum, beklenen değerler ve deterministiklik (aynı plan → aynı sonuç parmak izi).

Çalıştırma:  cd consolidation/agent && python3 -m pytest -q tests/
Kanonik veritabanı salt okunur açılır; test hiçbir dosyaya yazmaz.
"""
from pathlib import Path

import pytest
import yaml

from geoprop_agent.motor import calistir

ALTIN = yaml.safe_load((Path(__file__).parent / "altin_planlar.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("durum", ALTIN, ids=[d["ad"] for d in ALTIN])
def test_altin_plan(durum):
    s1 = calistir(durum["plan"])
    b = durum.get("beklenen", {})
    assert s1.durum == b.get("durum", "tamam"), (s1.durum, s1.uyarilar)
    if "plan_yer" in b:
        assert s1.plan["yer"] == b["plan_yer"]
    if "donem" in b:
        assert s1.plan["donem"] == b["donem"]
    if "ilk_geo_id" in b:
        assert s1.tablolar[0].satirlar[0]["geo_id"] == b["ilk_geo_id"]
    s2 = calistir(durum["plan"])
    assert s1.plan_hash == s2.plan_hash
    assert s1.sonuc_hash() == s2.sonuc_hash(), "aynı plan farklı sonuç verdi (deterministik değil)"


def test_yasak_ifade_yok():
    """OSM hareketlilik çıktısında 'açıldı/kapandı' dili kullanılmaz (kural osm_semantigi)."""
    s = calistir({"analiz": "harita_hareketliligi", "yer": "Karşıyaka", "kategori_grubu": "yeme_icme"})
    metin = " ".join(s.cumleler).lower()
    for ifade in ("açıldı", "kapandı", "açılış", "kapanış"):
        assert ifade not in metin


def test_kisisel_veri_yok():
    s = calistir({"analiz": "isletme_listesi", "kapsam": "Caferağa, Kadıköy", "limit": 20})
    for t in s.tablolar:
        for sut in t.sutunlar:
            assert sut.ad not in ("phone", "telefon", "address_raw", "adres")
