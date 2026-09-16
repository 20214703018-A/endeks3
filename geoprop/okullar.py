"""Resmî okullar (MEB) — yakınlık + kapasite + LGS puanı/sıralaması; resmi_egitim.sqlite (kardeş dosya).

- Kademe başına en yakın N okul: anaokulu, ilkokul, ortaokul, lise (tüm lise türleri), özel (bilsem/özel eğitim).
- Her okul: mesafe, öğrenci/öğretmen/derslik, öğrenci-derslik ve öğrenci-öğretmen oranı (yalnız veri varsa).
- Liseler: LGS taban puanı (ilk yerleştirme), ulusal sıra/yüzdelik, il sırası; sınavsız (adrese dayalı)
  liselerde puan yoktur → "puan_verisi": "yok" (uydurma yok).
- Yarıçapta en iyi liseler: LGS puanına göre ilk 5 (varsayılan 5 km).
Sorgu `(tur, lat, lon)` indeksiyle; salt-okunur; tablo yoksa None. Kaynak: MEB (meb.gov.tr, e-okul).
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

from .veri_yardimcilari import haversine_km

KADEMELER: tuple[tuple[str, str, tuple[str, ...], float, int], ...] = (
    ("anaokulu", "Anaokulu", ("anaokulu",), 3.0, 3),
    ("ilkokul", "İlkokul", ("ilkokul",), 4.0, 3),
    ("ortaokul", "Ortaokul", ("ortaokul", "imam_hatip_ortaokulu"), 4.0, 3),
    ("lise", "Lise", ("anadolu_lisesi", "fen_lisesi", "sosyal_bilimler_lisesi", "imam_hatip_lisesi", "meslek_lisesi", "lise"), 8.0, 5),
    ("ozel", "BİLSEM / özel eğitim", ("bilsem", "ozel_egitim"), 10.0, 2),
)
TUR_ETIKET = {"anaokulu": "Anaokulu", "ilkokul": "İlkokul", "ortaokul": "Ortaokul", "imam_hatip_ortaokulu": "İmam Hatip Ortaokulu",
              "anadolu_lisesi": "Anadolu Lisesi", "fen_lisesi": "Fen Lisesi", "sosyal_bilimler_lisesi": "Sosyal Bilimler Lisesi",
              "imam_hatip_lisesi": "Anadolu İmam Hatip Lisesi", "meslek_lisesi": "Mesleki ve Teknik Anadolu Lisesi", "lise": "Lise",
              "bilsem": "Bilim ve Sanat Merkezi", "ozel_egitim": "Özel Eğitim", "halk_egitim": "Halk Eğitim / MEM"}
EN_IYI_LISE_KM = 5.0


class SchoolEngine:
    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def _conn(self):
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='okul'").fetchone():
            c.close(); return None
        return c

    @staticmethod
    def _bbox(lat, lon, r_km):
        dlat = r_km / 111.0
        dlon = r_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
        return lat - dlat, lat + dlat, lon - dlon, lon + dlon

    def _near(self, c, lat, lon, turler, r_km):
        a0, a1, o0, o1 = self._bbox(lat, lon, r_km)
        rows = []
        for tur in turler:
            rows += c.execute("SELECT kurum_kodu, ad, tur, il, ilce, lat, lon, derslik, ogretmen, ogrenci, koordinat_kaynagi FROM okul "
                              "WHERE tur=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (tur, a0, a1, o0, o1)).fetchall()
        out = []
        for r in rows:
            d = haversine_km(lat, lon, r["lat"], r["lon"])
            if d <= r_km:
                x = dict(r); x["mesafe_m"] = round(d * 1000); out.append(x)
        out.sort(key=lambda x: x["mesafe_m"])
        return out

    @staticmethod
    def _lgs(c, kurum_kodu):
        r = c.execute("SELECT taban_ilk, taban_nakil, ulusal_sira, ulusal_yuzdelik, il_sira, tur_sira, yil, okul_turu "
                      "FROM lgs_taban WHERE kurum_kodu=? AND COALESCE(taban_ilk, taban_nakil) IS NOT NULL "
                      "ORDER BY COALESCE(taban_ilk, taban_nakil) DESC LIMIT 1", (kurum_kodu,)).fetchone()
        if not r:
            return {"puan_verisi": "yok", "not": "Sınavsız (adrese dayalı) yerleştirme; LGS taban puanı yok."}
        return {"puan_verisi": "var", "taban_puan": r["taban_ilk"] if r["taban_ilk"] is not None else r["taban_nakil"],
                "yil": r["yil"], "ulusal_sira": r["ulusal_sira"], "ulusal_yuzdelik": r["ulusal_yuzdelik"], "il_sira": r["il_sira"],
                "tur_sira": r["tur_sira"], "lgs_okul_turu": r["okul_turu"]}

    @staticmethod
    def _item(x, lgs=None):
        ogr, ogt, der = x.get("ogrenci"), x.get("ogretmen"), x.get("derslik")
        it = {"kurum_kodu": x["kurum_kodu"], "ad": x["ad"], "tur": x["tur"], "tur_etiket": TUR_ETIKET.get(x["tur"], x["tur"]),
              "ilce": x.get("ilce"), "lat": x["lat"], "lon": x["lon"], "mesafe_m": x["mesafe_m"],
              "konum_yaklasik": "yaklaşık" in (x.get("koordinat_kaynagi") or ""),
              "ogrenci": ogr, "ogretmen": ogt, "derslik": der,
              # MEB sayfalarında hatalı giriş olabiliyor (713 öğrenci / 1 derslik): makul aralık dışı oran gösterilmez.
              "ogrenci_derslik": round(ogr / der, 1) if ogr and der and 3 <= ogr / der <= 80 else None,
              "ogrenci_ogretmen": round(ogr / ogt, 1) if ogr and ogt and 2 <= ogr / ogt <= 60 else None}
        if lgs is not None:
            it["lgs"] = lgs
        return it

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._conn()
        if c is None:
            return None
        try:
            kademeler = {}
            for key, label, turler, r_km, n in KADEMELER:
                near = self._near(c, lat, lon, turler, r_km)
                items = [self._item(x, self._lgs(c, x["kurum_kodu"]) if key == "lise" else None) for x in near[:n]]
                kademeler[key] = {"etiket": label, "arama_km": r_km, "bulunan": len(near), "en_yakin": items}
            # yarıçapta LGS puanına göre en iyi liseler
            lise_turler = KADEMELER[3][2]
            adaylar = self._near(c, lat, lon, lise_turler, EN_IYI_LISE_KM)
            puanli = []
            for x in adaylar:
                lgs = self._lgs(c, x["kurum_kodu"])
                if lgs["puan_verisi"] == "var":
                    puanli.append(self._item(x, lgs))
            puanli.sort(key=lambda i: -(i["lgs"]["taban_puan"] or 0))
            meta = {r["tablo"]: r["guncellenme"] for r in c.execute("SELECT tablo, guncellenme FROM kapsama")} if c.execute("SELECT 1 FROM sqlite_master WHERE name='kapsama'").fetchone() else {}
            lgs_yil = c.execute("SELECT MAX(yil) FROM lgs_taban").fetchone()[0]
        finally:
            c.close()
        return {
            "status": "available",
            "kaynak": "MEB okul listesi ve okul sayfaları (meb.gov.tr / meb.k12.tr); LGS taban puanları e-okul tercih listesi",
            "lgs_yili": lgs_yil,
            "guncellenme": meta.get("okul"),
            "kademeler": kademeler,
            "en_iyi_liseler_5km": {"yaricap_km": EN_IYI_LISE_KM, "puanli_lise": len(puanli), "toplam_lise": len(adaylar), "liste": puanli[:5]},
            "not": ("Puan/sıralama yalnız sınavla öğrenci alan liseler için (LGS taban puanı; ulusal sıra tüm programlar arasında). "
                    "İlkokul/ortaokul ve adrese dayalı liselerde resmî puan yoktur; kapasite (öğrenci/derslik) gösterilir."),
        }
