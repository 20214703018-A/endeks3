"""Projeksiyon doğruluk değerlendirmesi (salt okunur): seriyi bir kesim tarihinde kes, ufuk kadar ileri tahmin et,
gerçekleşenle karşılaştır. Rapor: medyan mutlak % hata, %80 aralık kapsaması (iyi ayarlıysa ≈ %80), 'değişim yok' (düz)
kıyas modeline göre üstünlük.

  python3 tests/projeksiyon_degerlendirme.py konut_satis_m2 ilce 2025-08 12
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geoprop_agent import projeksiyon as pj  # noqa: E402
from geoprop_agent import veri  # noqa: E402
from geoprop_agent.ontology import yukle  # noqa: E402


def degerlendir(oid: str, seviye: str, kesim: str, ufuk: int, mevsimsel: bool = False, iller: tuple[str, ...] = ()) -> dict:
    """iller: yalnız bu illerin birimleri (ör. ('GEO_IL_34', 'GEO_IL_35')); boşsa tümü."""
    o = yukle().olcu(oid)
    filtre = None
    if iller:
        filtre = "SELECT geo_id FROM k.main.geo_entity WHERE il_geo_id IN (" + ",".join(veri.lit(i) for i in iller) + ")"
    rows = veri.q(f"SELECT geo_id, donem, deger FROM ({veri.olcum_sql(o, seviye, None, filtre)}) ORDER BY geo_id, donem")
    seriler: dict[str, list] = {}
    for r in rows:
        seriler.setdefault(r["geo_id"], []).append((r["donem"], r["deger"]))
    hedef_i = pj.donem_indeks(kesim)[0] + ufuk
    hatalar, duz_hata, icerde, n = [], [], 0, 0
    for g, s in seriler.items():
        egitim = [x for x in s if x[0] <= kesim]
        gercek = {pj.donem_indeks(d)[0]: v for d, v in s}
        if len(egitim) < (24 if len(kesim) > 4 else 8) or egitim[-1][0] != kesim or hedef_i not in gercek or not gercek[hedef_i]:
            continue
        try:
            t = pj.tahmin_et(egitim, ufuk, mevsimsel=mevsimsel)
        except ValueError:
            continue
        y = gercek[hedef_i]
        hatalar.append(abs(t.orta[-1] / y - 1) * 100)
        duz_hata.append(abs(egitim[-1][1] / y - 1) * 100)
        icerde += t.alt80[-1] <= y <= t.ust80[-1]
        n += 1
    return {"olcu": oid, "seviye": seviye, "iller": list(iller) or "tümü", "kesim": kesim, "ufuk": ufuk, "seri_sayisi": n,
            "medyan_mutlak_hata_%": round(statistics.median(hatalar), 2) if n else None,
            "ortalama_mutlak_hata_%": round(statistics.mean(hatalar), 2) if n else None,
            "duz_model_medyan_hata_%": round(statistics.median(duz_hata), 2) if n else None,
            "aralik80_kapsama_%": round(100 * icerde / n, 1) if n else None}


if __name__ == "__main__":
    a = sys.argv[1:] or ["konut_satis_m2", "ilce", "2025-08", "12"]
    print(degerlendir(a[0], a[1], a[2], int(a[3]), mevsimsel=len(a) > 4))
