"""Parsel için coğrafi katman bulgularını (fay, elektrik, su, dere, orman/sit/sahil,
demiryolu, planlanan yol) kullanıcı kurallarına göre üretir.

Kurallar (2026-09-12, kullanıcı):
  - ELEKTRIK_HATTI      → yalnızca parselin ÜSTÜNDEN geçiyorsa bildir.
  - Su (SU_KUYUSU, CESME_ICME_SUYU, DOGAL_PINAR, GOL_BARAJ_HAZNE, SU_BORU_HATTI)
                        → 1 km içinde bildir.
  - MEVCUT_YOL          → BU KAYNAKTAN KULLANMA (daha sağlam kaynak + uydu çıkarımı sonra).
  - DEMIRYOLU           → yakınsa bildir (mesafe).
  - PLANLANAN_YOL_PROJESI → yakınsa bildir (mesafe).
  - DİRİ_FAY_HATTI      → en yakını bildir (mesafe; risk).
  - ORMAN/SIT/SAHIL     → yalnızca parsel üstünde/bitişiğindeyse bildir.
  - SU_YOLU_DERE        → parsel üstünden veya bitişiğinden geçiyorsa bildir.

Mekânsal ölçüm: geometriler parsel merkezine göre yerel metrik düzleme yansıtılır
(equirectangular), shapely ile mesafe/kesişim hesaplanır. Kaba sınır kutusu ön
elemesi R-tree ile yapılır.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

try:
    from shapely.geometry import shape, Point
    from shapely.ops import transform as shp_transform
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False

# Bitişik sayılacak azami mesafe (m) ve "üstünde" toleransı (m).
ADJACENT_M = 25.0
OVER_M = 3.0

WATER_LAYERS = ("SU_KUYUSU", "CESME_ICME_SUYU", "DOGAL_PINAR", "GOL_BARAJ_HAZNE", "SU_BORU_HATTI")
# Parsel üstünde/bitişiğinde olması gereken alan katmanları.
ONSITE_AREA_LAYERS = ("ORMAN_ALANI", "SIT_VE_KORUNAN_ALAN", "SAHIL_SERIDI")

LAYER_LABELS = {
    "DİRİ_FAY_HATTI": "Diri fay hattı",
    "ELEKTRIK_HATTI": "Elektrik (gerilim) hattı",
    "SU_KUYUSU": "Su kuyusu",
    "CESME_ICME_SUYU": "Çeşme / içme suyu",
    "DOGAL_PINAR": "Doğal pınar",
    "GOL_BARAJ_HAZNE": "Göl / baraj / hazne",
    "SU_BORU_HATTI": "Su boru hattı",
    "SU_YOLU_DERE": "Akarsu / dere yatağı",
    "ORMAN_ALANI": "Orman alanı",
    "SIT_VE_KORUNAN_ALAN": "Sit / korunan alan",
    "SAHIL_SERIDI": "Sahil şeridi",
    "DEMIRYOLU": "Demiryolu",
    "PLANLANAN_YOL_PROJESI": "Planlanan yol projesi",
}


def _representative_point(geometry: Any):
    """GeoJSON geometriden temsili [lon, lat] noktası (ilk koordinat)."""
    coords = (geometry or {}).get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
        return [coords[0], coords[1]]
    return None


def _local_projector(lat0: float, lon0: float):
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0

    def project(x, y, z=None):
        return ((x - lon0) * kx, (y - lat0) * ky)

    return project


def _parcel_shape(parcel_geometry: Any, lat: float | None, lon: float | None):
    """Parsel poligonunu (varsa) ya da nokta geometrisini shapely şekline çevirir."""
    geom = parcel_geometry
    if isinstance(geom, str):
        try:
            geom = json.loads(geom)
        except (ValueError, TypeError):
            geom = None
    if isinstance(geom, dict):
        if geom.get("type") == "Feature":
            geom = geom.get("geometry")
        elif geom.get("type") == "FeatureCollection":
            feats = geom.get("features") or []
            geom = feats[0].get("geometry") if feats else None
    if isinstance(geom, dict) and geom.get("type") in ("Polygon", "MultiPolygon") and geom.get("coordinates"):
        try:
            return shape(geom), True
        except Exception:
            pass
    if lat is not None and lon is not None:
        return Point(lon, lat), False
    return None, False


class GeographicContextEngine:
    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def _bbox_candidates(self, connection, min_lat, max_lat, min_lon, max_lon, layers):
        placeholders = ",".join("?" for _ in layers)
        rows = connection.execute(
            f"""
            SELECT o.katman, o.ad, o.geom_tip, o.geometry
            FROM cografi_rtree r JOIN cografi_ozellikler o ON o.id = r.id
            WHERE r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=?
              AND o.katman IN ({placeholders})
            """,
            (min_lat, max_lat, min_lon, max_lon, *layers),
        ).fetchall()
        return rows

    def analyze(self, parcel_geometry: Any, lat: float | None, lon: float | None) -> dict[str, Any]:
        if not _HAS_SHAPELY or not self.database.exists():
            return {"status": "unavailable"}
        parcel, is_polygon = _parcel_shape(parcel_geometry, lat, lon)
        if parcel is None:
            return {"status": "coordinate_required"}
        # Yerel metrik düzlem (parsel merkezine göre).
        centroid = parcel.centroid
        lon0, lat0 = centroid.x, centroid.y
        project = _local_projector(lat0, lon0)
        parcel_local = shp_transform(project, parcel)

        # Katman grubu başına arama yarıçapı: her katman için 20 km kutu kullanmak İstanbul'da
        # ~5K orman/dere/göl geometrisini (1.5 MB JSON) boşuna parse ettiriyordu (300 ms).
        # Kurallar: fay = her zaman en yakın (20 km); demiryolu/planlanan yol ≤ 5 km; su ≤ 2 km;
        # elektrik/dere/orman/sit/sahil yalnız parsel üstü/bitişiği (≤ 25 m). Kutu parsel
        # sınırlarından (merkezden değil) genişletilir; büyük parsellerde kenar kaçmaz.
        min_lon_p, min_lat_p, max_lon_p, max_lat_p = parcel.bounds
        layer_groups = (
            (20000.0, ("DİRİ_FAY_HATTI",)),
            (5100.0, ("DEMIRYOLU", "PLANLANAN_YOL_PROJESI")),
            (2100.0, WATER_LAYERS),
            (1200.0, ("SU_YOLU_DERE",)),   # taşkın vekili için en yakın dere mesafesi (bildirim eşiği yine 60 m'de)
            (60.0, ("ELEKTRIK_HATTI", *ONSITE_AREA_LAYERS)),
        )
        uri = f"file:{self.database}?mode=ro"
        rows: list = []
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for search_m, layers in layer_groups:
                dlat = search_m / 110540.0
                dlon = search_m / (111320.0 * max(math.cos(math.radians(lat0)), 1e-6))
                rows.extend(self._bbox_candidates(
                    connection, min_lat_p - dlat, max_lat_p + dlat, min_lon_p - dlon, max_lon_p + dlon, layers,
                ))

        # Katman bazında en yakın mesafe ve 2km içi su sayacı.
        nearest: dict[str, dict[str, Any]] = {}
        water_within_2km: list[dict[str, Any]] = []
        for row in rows:
            try:
                raw_geom = json.loads(row["geometry"])
                geom = shp_transform(project, shape(raw_geom))
            except Exception:
                continue
            distance = parcel_local.distance(geom)  # metre
            katman = row["katman"]
            cur = nearest.get(katman)
            if cur is None or distance < cur["distance_m"]:
                nearest[katman] = {"distance_m": distance, "ad": row["ad"], "geometry": raw_geom}
            if katman in WATER_LAYERS and distance <= 2000.0:
                pt = _representative_point(raw_geom)
                water_within_2km.append({
                    "layer": katman, "label": LAYER_LABELS.get(katman, katman),
                    "ad": row["ad"], "distance_m": round(distance),
                    "lat": pt[1] if pt else None, "lon": pt[0] if pt else None,
                })

        findings: list[dict[str, Any]] = []

        def relation(distance):
            if distance <= OVER_M:
                return "over"
            if distance <= ADJACENT_M:
                return "adjacent"
            return "near"

        # 1) Elektrik hattı: yalnızca üstünden geçiyorsa.
        el = nearest.get("ELEKTRIK_HATTI")
        if el and el["distance_m"] <= OVER_M:
            findings.append({
                "layer": "ELEKTRIK_HATTI", "label": LAYER_LABELS["ELEKTRIK_HATTI"],
                "notify": True, "relation": "over", "severity": "risk",
                "distance_m": round(el["distance_m"]), "geometry": el.get("geometry"),
                "message": "Parselin üzerinden elektrik (gerilim) hattı geçiyor.",
            })

        # 2) Su kaynakları: 2 km çevresindeki sayı.
        if water_within_2km:
            water_within_2km.sort(key=lambda w: w["distance_m"])
            nearest_water = water_within_2km[0]
            # Tür bazında sayım (kuyu/çeşme/pınar/göl/boru).
            by_type: dict[str, int] = {}
            for w in water_within_2km:
                by_type[w["label"]] = by_type.get(w["label"], 0) + 1
            findings.append({
                "layer": "SU", "label": "Su kaynağı (2 km)", "notify": True,
                "relation": "within", "severity": "info", "radius_km": 2,
                "distance_m": nearest_water["distance_m"], "count": len(water_within_2km),
                "by_type": by_type, "items": water_within_2km[:30],
                "message": f"2 km çevresinde {len(water_within_2km)} su kaynağı "
                           f"(en yakın {nearest_water['label']} ~{nearest_water['distance_m']} m).",
            })

        # 3) Dere/akarsu: üstünden veya bitişiğinden geçiyorsa.
        dere = nearest.get("SU_YOLU_DERE")
        if dere and dere["distance_m"] <= ADJACENT_M:
            rel = relation(dere["distance_m"])
            findings.append({
                "layer": "SU_YOLU_DERE", "label": LAYER_LABELS["SU_YOLU_DERE"],
                "notify": True, "relation": rel, "severity": "risk",
                "distance_m": round(dere["distance_m"]), "geometry": dere.get("geometry"),
                "message": ("Parselin üzerinden akarsu/dere yatağı geçiyor."
                            if rel == "over" else
                            "Parsele bitişik akarsu/dere yatağı var."),
            })

        # 4) Orman / Sit / Sahil: yalnızca üstünde/bitişiğinde.
        for layer in ONSITE_AREA_LAYERS:
            item = nearest.get(layer)
            if item and item["distance_m"] <= ADJACENT_M:
                rel = relation(item["distance_m"])
                findings.append({
                    "layer": layer, "label": LAYER_LABELS[layer], "notify": True,
                    "relation": rel, "severity": "warn", "distance_m": round(item["distance_m"]),
                    "message": (f"Parsel {LAYER_LABELS[layer].lower()} içinde/üzerinde."
                                if rel == "over" else
                                f"Parsele bitişik {LAYER_LABELS[layer].lower()} var."),
                })

        # 5) Fay hattı: en yakını her zaman bildir (risk).
        fault = nearest.get("DİRİ_FAY_HATTI")
        if fault:
            rel = relation(fault["distance_m"])
            findings.append({
                "layer": "DİRİ_FAY_HATTI", "label": LAYER_LABELS["DİRİ_FAY_HATTI"],
                "notify": True, "relation": rel, "severity": "risk",
                "distance_m": round(fault["distance_m"]), "ad": fault["ad"],
                "geometry": fault.get("geometry"),
                "message": (f"En yakın diri fay hattı ~{round(fault['distance_m'])} m "
                            f"({'üzerinde' if rel == 'over' else 'mesafede'})."),
            })

        # 6) Demiryolu & Planlanan yol: yakınsa bildir.
        for layer in ("DEMIRYOLU", "PLANLANAN_YOL_PROJESI"):
            item = nearest.get(layer)
            if item and item["distance_m"] <= 5000.0:
                rel = relation(item["distance_m"])
                findings.append({
                    "layer": layer, "label": LAYER_LABELS[layer], "notify": True,
                    "relation": rel, "severity": "info", "distance_m": round(item["distance_m"]),
                    "message": f"En yakın {LAYER_LABELS[layer].lower()} ~{round(item['distance_m'])} m.",
                })

        findings.sort(key=lambda f: {"risk": 0, "warn": 1, "info": 2}.get(f.get("severity"), 3))
        return {
            "status": "available",
            "parcel_geometry_used": is_polygon,
            "findings": findings,
            # Afet vekili (taşkın) için: en yakın dere yatağı mesafesi (2,1 km yarıçap içinde; yoksa None)
            "en_yakin_dere_m": round(nearest["SU_YOLU_DERE"]["distance_m"]) if nearest.get("SU_YOLU_DERE") else None,
            "en_yakin_gol_baraj_m": round(nearest["GOL_BARAJ_HAZNE"]["distance_m"]) if nearest.get("GOL_BARAJ_HAZNE") else None,
            "notes": [
                "Yol (mevcut) verisi bu kaynaktan kullanılmadı; daha sağlam kaynak ve "
                "uydu görüntüsü çıkarımı (toprak yol tespiti) ile ayrıca eklenecektir.",
            ],
        }
