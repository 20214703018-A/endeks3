"""Üniversite kampüsleri — poligon (OSM), ilçe bazlı öğrenci sayısı (YÖK T102, AÖF hariç); universite.sqlite.

- Yarıçapta kampüsler: poligon varsa parsele **poligon mesafesi** (içindeyse 0), yoksa merkez noktası.
- Her kampüs: üniversite, ilçe, ilçedeki AÖF-hariç öğrenci (YÖK T102 2025-26), aynı ilçedeki kampüs sayısı
  (öğrenci sayısı ilçe düzeyindedir; birden fazla kampüs varsa ORTAK gösterilir, bölünmez — uydurma yok),
  alan (ha), poligon GeoJSON (haritada çizim).
- Üniversite genel: örgün/ikinci/uzaktan/AÖF kırılımı; URAP Türkiye genel sırası (tablo varsa; yoksa "sıralama yok").
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from .veri_yardimcilari import haversine_km, normalize_name, tr_title

try:
    from shapely.geometry import Point, shape
    _HAS_SHAPELY = True
except Exception:  # pragma: no cover
    _HAS_SHAPELY = False

ARAMA_KM = 10.0
EN_YAKIN_N = 6


class UniversityEngine:
    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def _conn(self):
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='kampus'").fetchone():
            c.close(); return None
        return c

    @staticmethod
    def _urap(c, ad: str | None):
        if not ad or not c.execute("SELECT 1 FROM sqlite_master WHERE name='urap'").fetchone():
            return None
        r = c.execute("SELECT yil, sira, toplam_puan, (SELECT COUNT(*) FROM urap u2 WHERE u2.yil=urap.yil) AS n "
                      "FROM urap WHERE universite_ad_norm=? ORDER BY yil DESC LIMIT 1", (normalize_name(ad),)).fetchone()
        return {"yil": r["yil"], "sira": r["sira"], "toplam_puan": r["toplam_puan"], "siralanan": r["n"]} if r else None

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._conn()
        if c is None:
            return None
        try:
            dlat = ARAMA_KM / 111.0; dlon = ARAMA_KM / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
            rows = c.execute("SELECT * FROM kampus WHERE COALESCE(bina_parcasi,0)=0 AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                             (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
            items = []
            pt = Point(lon, lat) if _HAS_SHAPELY else None
            for r in rows:
                d_km = haversine_km(lat, lon, r["lat"], r["lon"]); icinde = False
                geom = json.loads(r["geometri"]) if r["geometri"] else None
                if geom and pt is not None:
                    try:
                        g = shape(geom)
                        if g.contains(pt):
                            d_km, icinde = 0.0, True
                        else:
                            # derece → km (yerel yaklaşık)
                            near = g.exterior.interpolate(g.exterior.project(pt)) if g.geom_type == "Polygon" else None
                            if near is not None:
                                d_km = haversine_km(lat, lon, near.y, near.x)
                    except Exception:
                        pass
                if d_km > ARAMA_KM:
                    continue
                if not r["universite"] and not geom:
                    continue   # üniversitesi bilinmeyen nokta (bölüm/bina düzeyi) — kampüs olarak sayılmaz
                uni = None
                if r["universite"]:
                    uni = c.execute("SELECT tur, il, kampus_toplam, aof_toplam, uzaktan_toplam, toplam_t FROM universite WHERE ad=?", (r["universite"],)).fetchone()
                items.append({
                    "ad": r["ad"], "universite": r["universite"], "universite_turu": uni["tur"] if uni else None,
                    "ilce": r["ilce"], "mesafe_m": round(d_km * 1000), "parsel_icinde": icinde,
                    "alan_ha": round((r["alan_m2"] or 0) / 1e4, 1) if r["alan_m2"] else None,
                    "ilce_ogrenci_aof_haric": r["ilce_ogrenci"], "ilce_aof": r["ilce_aof"], "ilce_kampus_sayisi": r["ilce_kampus_sayisi"],
                    "universite_toplam_aof_haric": uni["kampus_toplam"] if uni else None, "universite_aof": uni["aof_toplam"] if uni else None,
                    "universite_uzaktan": uni["uzaktan_toplam"] if uni else None,
                    "lat": r["lat"], "lon": r["lon"], "geometri": geom, "eslesme": r["eslesme"], "urap": self._urap(c, r["universite"]),
                })
            # YÖK T102'de öğrencisi olan ama OSM'de kampüs nesnesi olmayan (üniversite, ilçe) çiftleri:
            # ilçe merkezinde "ilçe düzeyi" olarak gösterilir (kampüs poligonu yok; konum yaklaşık).
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='ilce_merkez'").fetchone():
                var = {(x["universite"], x["ilce"]) for x in items if x["universite"] and x["ilce"]}
                var |= {(r["universite"], r["ilce"]) for r in c.execute("SELECT universite, ilce FROM kampus WHERE universite IS NOT NULL AND ilce IS NOT NULL AND COALESCE(bina_parcasi,0)=0")}
                merkezler = {(r["il_norm"], r["ilce_norm"]): (r["lat"], r["lon"]) for r in c.execute(
                    "SELECT il_norm, ilce_norm, lat, lon FROM ilce_merkez WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (lat - dlat, lat + dlat, lon - dlon, lon + dlon))}
                for r in c.execute("""SELECT ui.universite, ui.il, ui.ilce, ui.ilce_norm, ui.kampus_toplam, ui.aof_toplam, u.tur, u.kampus_toplam AS u_top, u.aof_toplam AS u_aof, u.uzaktan_toplam AS u_uz
                                      FROM universite_ilce ui JOIN universite u ON u.ad=ui.universite WHERE ui.ilce <> 'İLÇE BELİRTİLMEMİŞ'"""):
                    m = merkezler.get((normalize_name(r["il"]), r["ilce_norm"]))
                    if not m:
                        # T102 ili üniversitenin ili; başka ildeki kampüs ilçesi (Başkent/Selçuklu gibi) → ilçe adı Türkiye'de tekse kabul
                        adaylar = [v for (iln, icn), v in merkezler.items() if icn == r["ilce_norm"]]
                        if len(adaylar) == 1:
                            m = adaylar[0]
                    if not m:
                        continue   # KKTC vb. (Türkiye sınır verisi dışında)
                    r = dict(r); r["lat"], r["lon"] = m
                    if (r["universite"], r["ilce"]) in var or (r["ilce"] == "MERKEZ" and any(a == r["universite"] for a, _ in var)):
                        continue
                    d_km = haversine_km(lat, lon, r["lat"], r["lon"])
                    if d_km > ARAMA_KM:
                        continue
                    items.append({"ad": f"{tr_title(r['universite'])} — {tr_title(r['ilce'])} ilçesi (kampüs poligonu yok)", "universite": r["universite"],
                                  "universite_turu": r["tur"], "ilce": r["ilce"], "mesafe_m": round(d_km * 1000), "parsel_icinde": False, "alan_ha": None,
                                  "ilce_ogrenci_aof_haric": r["kampus_toplam"], "ilce_aof": r["aof_toplam"], "ilce_kampus_sayisi": None,
                                  "universite_toplam_aof_haric": r["u_top"], "universite_aof": r["u_aof"], "universite_uzaktan": r["u_uz"],
                                  "lat": r["lat"], "lon": r["lon"], "geometri": None, "eslesme": "T102 ilçe düzeyi (konum: ilçe merkezi, yaklaşık)", "konum_yaklasik": True, "urap": self._urap(c, r["universite"])})
            items.sort(key=lambda x: x["mesafe_m"])
            meta = {r["tablo"]: (r["guncellenme"], r["kaynak"]) for r in c.execute("SELECT tablo, guncellenme, kaynak FROM kapsama")}
        finally:
            c.close()
        return {
            "status": "available", "arama_km": ARAMA_KM, "bulunan": len(items), "kampusler": items[:EN_YAKIN_N],
            "kaynak": "Kampüs poligonları: © OpenStreetMap katkıcıları (ODbL); öğrenci sayıları: YÖK Tablo 102 (2025-26), açıköğretim hariç; akademik sıra: URAP Türkiye genel sıralaması",
            "urap": {"guncellenme": meta["urap"][0], "kaynak": meta["urap"][1]} if "urap" in meta else None,
            "not": ("Öğrenci sayısı YÖK'te ilçe düzeyindedir; aynı ilçede birden fazla kampüs varsa sayı o ilçedeki kampüslerin toplamıdır, "
                    "kampüslere bölünmez. Uzaktan öğretim sayıya dahil, açıköğretim hariç."),
        }
