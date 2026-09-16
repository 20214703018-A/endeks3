"""Jeolojik birim (formasyon) bağlamı — MTA Yerbilimleri Portalı WMS, parsel noktası sorgusu.

Karar (2026-09-13): MTA verisinin poligonları lisans gereği ambara ALINMAZ (yasal uyarı: vektör
veri ücretli, çoğaltma/dağıtma yazılı izne bağlı). Yalnız sorgu anında parsel noktası için
WMS GetFeatureInfo ile öznitelik (kod, simge, açıklama, yaş) okunur, kaynak belirtilerek gösterilir;
geometri yalnız parsel çevresi (~3 km pencere) ile kırpılıp sadeleştirilerek **o sorgunun haritasında**
gösterilir, ambara yazılmaz; toplu tarama yapılmaz. Bu, E-Plan imar sorgusuyla aynı "canlı nokta sorgusu" sınıfıdır.

Kapsam: 1/500.000 jeoloji haritası — bölgesel ölçek. Parsel için "zemin sınıfı" değildir; jeolojik
birim (ör. Kuvaterner alüvyon, Üst Kretase kırıntılı/karbonat) bağlamıdır. Arayüz bunu açıkça yazar.

Dayanıklılık: zaman aşımı kısa (varsayılan 6 s), hata → {"status": "kaynak_erisilemedi"}; süreç içi
önbellek (koordinat 4 ondalık ≈ 11 m) aynı parselin tekrar sorgusunda ağa çıkmaz.
"""

from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

MTA_WMS_URL = "https://mtayenicbs-geoserver.mta.gov.tr/geoserver/mta/wms"
FORMATION_LAYER = "mta:PORTALFORM"
SOURCE_LABEL = "MTA Genel Müdürlüğü — Yerbilimleri Portalı, 1/500.000 Jeoloji Haritası"
SCALE_LABEL = "1/500.000"
USER_AGENT = "GEOPROP/1.0 (parcel point query)"  # ASCII: HTTP başlığı latin-1

# Yaş → çok kaba "genç/gevşek çökel mi" ipucu. Yalnız Kuvaterner'i işaretleriz; ötesi yorum ister.
QUATERNARY_KEYS = ("kuvaterner", "quaternary", "holosen", "pleyistosen")
CLIP_WINDOW_M = 3000.0      # geometri yalnız bu pencere içinde döner (harita gösterimi için)
SIMPLIFY_DEG = 0.0002       # ~20 m; 1/500.000 ölçekte görsel kayıp yok

try:
    from shapely.geometry import box as shp_box, mapping as shp_mapping, shape as shp_shape
    _HAS_SHAPELY = True
except Exception:  # pragma: no cover
    _HAS_SHAPELY = False


def _clip_geometry(geometry: dict | None, lat: float, lon: float) -> dict | None:
    """Poligonu parsel çevresindeki pencereyle kırpar ve sadeleştirir; shapely yoksa None."""
    if not geometry or not _HAS_SHAPELY:
        return None
    try:
        dlat = CLIP_WINDOW_M / 110540.0
        dlon = CLIP_WINDOW_M / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
        clipped = shp_shape(geometry).intersection(shp_box(lon - dlon, lat - dlat, lon + dlon, lat + dlat))
        if clipped.is_empty:
            return None
        return shp_mapping(clipped.simplify(SIMPLIFY_DEG, preserve_topology=True))
    except Exception:
        return None


class GeologyContextEngine:
    def __init__(self, wms_url: str = MTA_WMS_URL, timeout_s: float = 6.0, enabled: bool | None = None):
        self.wms_url = wms_url
        self.timeout_s = timeout_s
        # GEOPROP_JEOLOJI_CANLI=0 ile (testler, çevrimdışı) kapatılabilir.
        self.enabled = enabled if enabled is not None else os.environ.get("GEOPROP_JEOLOJI_CANLI", "1") != "0"
        self._cache: dict[tuple[float, float], tuple[str, list[dict[str, Any]]]] = {}

    def _get_feature_info(self, lat: float, lon: float) -> list[dict[str, Any]]:
        d = 0.0005  # ~55 m kutu; X/Y merkez piksel → nokta sorgusu
        params = {
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetFeatureInfo",
            "LAYERS": FORMATION_LAYER, "QUERY_LAYERS": FORMATION_LAYER, "SRS": "EPSG:4326",
            "BBOX": f"{lon - d},{lat - d},{lon + d},{lat + d}", "WIDTH": 101, "HEIGHT": 101, "X": 50, "Y": 50,
            "INFO_FORMAT": "application/json", "FEATURE_COUNT": 3,
        }
        req = urllib.request.Request(self.wms_url + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        feats = []
        for f in payload.get("features", []):
            if not isinstance(f, dict):
                continue
            props = dict(f.get("properties") or {})
            # Ham (tam) poligon tutulmaz; yalnız parsel penceresine kırpılmış, sadeleştirilmiş parça.
            props["_geometry"] = _clip_geometry(f.get("geometry"), lat, lon)
            feats.append(props)
        return feats

    def _lookup(self, lat_r: float, lon_r: float) -> tuple[str, list[dict[str, Any]]]:
        """Başarılı sonuçlar süreç içi önbelleğe alınır; geçici kaynak hataları alınmaz
        (bir kesinti oturum boyunca 'erişilemedi'ye kilitlenmesin)."""
        cache = self._cache
        key = (lat_r, lon_r)
        if key in cache:
            return cache[key]
        try:
            feats = self._get_feature_info(lat_r, lon_r)
        except Exception:
            return ("kaynak_erisilemedi", [])
        result = ("available" if feats else "bulunamadi", feats)
        if len(cache) >= 256:   # kırpılmış geometri taşır; bellek için küçük tutulur
            cache.pop(next(iter(cache)))
        cache[key] = result
        return result

    def formation_at(self, lat: float | None, lon: float | None) -> dict[str, Any]:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        if not self.enabled:
            return {"status": "disabled"}
        status, feats = self._lookup(round(float(lat), 4), round(float(lon), 4))
        out: dict[str, Any] = {
            "status": status, "kaynak": SOURCE_LABEL, "olcek": SCALE_LABEL,
            "sorgu_zamani": datetime.now(timezone.utc).isoformat(),
            "not": "Bölgesel ölçekli jeolojik birim; parsel zemin etüdü veya AFAD zemin sınıfı değildir.",
        }
        if status != "available":
            return out
        first = dict(feats[0])
        geometry = first.pop("_geometry", None)
        out.update({
            "geometry": geometry,
            "geometry_note": f"Parsel çevresi ~{int(CLIP_WINDOW_M / 1000)} km pencereyle kırpılmış, sadeleştirilmiş görünüm; tam poligon saklanmaz.",
            "kod": first.get("kod"), "simge": first.get("simge"),
            "aciklama": first.get("aciklama"), "yas": first.get("yas"),
            "kuvaterner": any(k in str(first.get("yas") or "").casefold() for k in QUATERNARY_KEYS),
            "birim_sayisi": len(feats),
            "diger_birimler": [{k: v for k, v in dict(f).items() if k != "_geometry"} for f in feats[1:]],
        })
        return out
