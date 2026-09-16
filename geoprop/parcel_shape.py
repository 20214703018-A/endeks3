"""Parsel geometrisinden (TKGM poligonu) şekil düzenliliğini hesaplar.

Metrikler: dikdörtgensellik (alan / minimum döndürülmüş dikdörtgen alanı),
en-boy oranı, köşe sayısı, kompaktlık (Polsby-Popper). Bunlardan parselin şekli
düzenli / düzenliye yakın / düzensiz / çok düzensiz olarak sınıflandırılır.
"""

from __future__ import annotations

import json
import math
from typing import Any

try:
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False

SHAPE_LABELS = {
    "regular": "Düzenli",
    "near_regular": "Düzenliye yakın",
    "irregular": "Düzensiz",
    "very_irregular": "Çok düzensiz",
}


def _extract_polygon(geometry: Any):
    geom = geometry
    if isinstance(geom, str):
        try:
            geom = json.loads(geom)
        except (ValueError, TypeError):
            return None
    if isinstance(geom, dict):
        if geom.get("type") == "Feature":
            geom = geom.get("geometry")
        elif geom.get("type") == "FeatureCollection":
            feats = geom.get("features") or []
            geom = feats[0].get("geometry") if feats else None
    if isinstance(geom, dict) and geom.get("type") in ("Polygon", "MultiPolygon") and geom.get("coordinates"):
        return geom
    return None


def analyze_shape(geometry: Any) -> dict[str, Any] | None:
    """Poligon parsel geometrisinden şekil metriklerini üretir; poligon yoksa None."""
    if not _HAS_SHAPELY:
        return None
    geom = _extract_polygon(geometry)
    if geom is None:
        return None
    try:
        poly = shape(geom)
    except Exception:
        return None
    if poly.is_empty or not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None

    # Yerel metrik düzleme yansıt (parsel merkezine göre equirectangular).
    c = poly.centroid
    lat0, lon0 = c.y, c.x
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    local = shp_transform(lambda x, y, z=None: ((x - lon0) * kx, (y - lat0) * ky), poly)

    area = local.area
    perimeter = local.length
    if area <= 0 or perimeter <= 0:
        return None

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mrr = local.minimum_rotated_rectangle
    rectangularity = area / mrr.area if getattr(mrr, "area", 0) > 0 else 0.0
    # Minimum döndürülmüş dikdörtgenin kenar uzunlukları → en-boy oranı.
    coords = list(mrr.exterior.coords)
    edges = []
    for i in range(len(coords) - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        edges.append(math.hypot(dx, dy))
    edges = sorted(e for e in edges if e > 0)
    short = edges[0] if edges else 0.0
    long = edges[-1] if edges else 0.0
    aspect_ratio = (long / short) if short > 0 else None
    compactness = 4 * math.pi * area / (perimeter ** 2)  # 1.0 = daire

    # Dış halka köşe sayısı (kapanış noktası hariç, sadeleştirilmiş).
    ring = local.exterior if hasattr(local, "exterior") else None
    if ring is not None:
        simplified = local.simplify(max(perimeter * 0.01, 0.5), preserve_topology=True)
        try:
            vertices = max(len(list(simplified.exterior.coords)) - 1, 3)
        except Exception:
            vertices = max(len(list(ring.coords)) - 1, 3)
    else:
        vertices = None

    ar = aspect_ratio or 1.0
    if rectangularity >= 0.90 and ar <= 3.0:
        classification = "regular"
    elif rectangularity >= 0.78 and ar <= 5.0:
        classification = "near_regular"
    elif rectangularity >= 0.60:
        classification = "irregular"
    else:
        classification = "very_irregular"

    return {
        "status": "computed_from_geometry",
        "classification": classification,
        "label": SHAPE_LABELS[classification],
        "rectangularity": round(rectangularity, 3),
        "aspect_ratio": round(aspect_ratio, 2) if aspect_ratio else None,
        "compactness": round(compactness, 3),
        "vertices": vertices,
        "area_m2": round(area, 1),
        "perimeter_m": round(perimeter, 1),
    }
