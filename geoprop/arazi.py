"""Arazi (rakım / eğim / bakı) bağlamı — sayısal yükseklik modelinden, parsel çevresi penceresi.

Kaynak sırası (aynı arayüz, etiket değişir):
  1. Copernicus DEM EEA-10 (10 m) — CCM yetkisi + S3 anahtarı gelince `dem_eea10` sağlayıcısı eklenir.
  2. Terrarium (AWS Terrain Tiles; SRTM/Copernicus 30 m sınıfı, z14'te 7 m'ye örneklenmiş) — anahtarsız.

Dürüstlük kuralı (kullanıcı 2026-09-13): 30 m sınıfı DEM 500 m² parselin İÇ eğimini ölçemez.
Bu motor sonuçları "bölgesel arazi eğimi" olarak etiketler; parsel penceresi (min 90 m) ve
çevre penceresi (300 m) ayrı verilir. 10 m kaynak devreye girince `kapsam` "parsel_yaklasik" olur.

Uydurma yok: kaynak yoksa/hata → {"status": "kaynak_erisilemedi"}; koordinat yoksa "coordinate_required".
"""

from __future__ import annotations

import io
import math
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

try:
    import numpy as np
    from PIL import Image
    _HAS_DEPS = True
except Exception:  # pragma: no cover
    _HAS_DEPS = False

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_ZOOM = 14
USER_AGENT = "GEOPROP/1.0 (terrain window)"
PARCEL_WINDOW_M = 90.0      # parsel penceresi (30 m DEM'de en az 3x3 gerçek hücre)
AREA_WINDOW_M = 300.0       # çevre penceresi (yamaç/vadi bağlamı)

SLOPE_CLASSES = ((5, "duz", "Düz"), (15, "hafif", "Hafif eğimli"), (30, "orta", "Orta eğimli"), (math.inf, "dik", "Dik"))
ASPECT_NAMES = ("K", "KD", "D", "GD", "G", "GB", "B", "KB")


def _tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _slope_class(pct: float) -> tuple[str, str]:
    for limit, key, label in SLOPE_CLASSES:
        if pct < limit:
            return key, label
    return "dik", "Dik"


class TerrainEngine:
    def __init__(self, timeout_s: float = 8.0, enabled: bool | None = None):
        self.timeout_s = timeout_s
        self.enabled = enabled if enabled is not None else os.environ.get("GEOPROP_ARAZI_CANLI", "1") != "0"
        self._tiles: dict[tuple[int, int, int], Any] = {}

    # ---------- kaynak: Terrarium ----------
    def _fetch_tile(self, z: int, x: int, y: int):
        key = (z, x, y)
        if key in self._tiles:
            return self._tiles[key]
        req = urllib.request.Request(TERRARIUM_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            img = Image.open(io.BytesIO(resp.read())).convert("RGB")
        arr = np.asarray(img, dtype=np.float64)
        elev = arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0
        if len(self._tiles) >= 64:
            self._tiles.pop(next(iter(self._tiles)))
        self._tiles[key] = elev
        return elev

    def _window(self, lat: float, lon: float, half_m: float):
        """Merkez etrafında ±half_m pencere: (rakım grid, hücre boyu m)."""
        z = TERRARIUM_ZOOM
        n = 2 ** z
        mpp = 156543.03392 * math.cos(math.radians(lat)) / n  # m / piksel
        half_px = max(2, int(math.ceil(half_m / mpp)))
        # Merkezin global piksel koordinatı
        gx = (lon + 180.0) / 360.0 * n * 256
        gy = (1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n * 256
        x0, y0 = int(gx) - half_px, int(gy) - half_px
        x1, y1 = int(gx) + half_px + 1, int(gy) + half_px + 1
        out = np.empty((y1 - y0, x1 - x0), dtype=np.float64)
        for ty in range(y0 // 256, (y1 - 1) // 256 + 1):
            for tx in range(x0 // 256, (x1 - 1) // 256 + 1):
                tile = self._fetch_tile(z, tx, ty)
                sx0, sy0 = max(x0, tx * 256), max(y0, ty * 256)
                sx1, sy1 = min(x1, tx * 256 + 256), min(y1, ty * 256 + 256)
                out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = tile[sy0 - ty * 256:sy1 - ty * 256, sx0 - tx * 256:sx1 - tx * 256]
        return out, mpp

    # ---------- hesap ----------
    @staticmethod
    def _slope_aspect(elev, cell_m: float):
        gy, gx = np.gradient(elev, cell_m)          # m/m
        slope = np.hypot(gx, gy)                    # tan
        # Bakı = yokuş aşağı yön; satır ekseni güneye artar → kuzey bileşeni +gy, doğu bileşeni -gx.
        aspect = (np.degrees(np.arctan2(-gx, gy)) + 360.0) % 360.0  # 0=K, saat yönü
        return slope, aspect

    @staticmethod
    def _stats(elev, slope, aspect) -> dict[str, Any]:
        s_pct = slope * 100.0
        # Baki: eğimle ağırlıklı dairesel ortalama (düz hücreler yönü bozmasın)
        w = slope + 1e-9
        ang = np.radians(aspect)
        mean_aspect = (math.degrees(math.atan2(float((w * np.sin(ang)).sum()), float((w * np.cos(ang)).sum()))) + 360.0) % 360.0
        ort = float(s_pct.mean())
        key, label = _slope_class(ort)
        return {
            "rakim_m": {"min": round(float(elev.min()), 1), "ort": round(float(elev.mean()), 1), "max": round(float(elev.max()), 1)},
            "rakim_fark_m": round(float(elev.max() - elev.min()), 1),
            "egim_pct": {"ort": round(ort, 1), "max": round(float(s_pct.max()), 1)},
            "egim_derece": round(math.degrees(math.atan(ort / 100.0)), 1),
            "sinif": key, "sinif_etiket": label,
            "baki_derece": round(mean_aspect),
            "baki_yon": ASPECT_NAMES[int(((mean_aspect + 22.5) % 360) // 45)],
        }

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any]:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        if not self.enabled or not _HAS_DEPS:
            return {"status": "disabled"}
        try:
            elev_area, cell = self._window(float(lat), float(lon), AREA_WINDOW_M / 2)
        except Exception:
            return {"status": "kaynak_erisilemedi", "kaynak": "Terrarium / Copernicus-SRTM 30 m"}
        slope_a, aspect_a = self._slope_aspect(elev_area, cell)
        # Parsel penceresi: merkezden ±45 m
        h = max(1, int(round(PARCEL_WINDOW_M / 2 / cell)))
        cy, cx = elev_area.shape[0] // 2, elev_area.shape[1] // 2
        sl = (slice(cy - h, cy + h + 1), slice(cx - h, cx + h + 1))
        parcel = self._stats(elev_area[sl], slope_a[sl], aspect_a[sl])
        area = self._stats(elev_area, slope_a, aspect_a)
        center_elev = float(elev_area[cy, cx])
        return {
            "status": "available",
            "kaynak": "AWS Terrain Tiles (Terrarium) — Copernicus/SRTM 30 m sınıfı, z14 örnekleme",
            "cozunurluk_m": 30, "hucre_m": round(cell, 1),
            "kapsam": "bolgesel",
            "not": ("30 m sınıfı model: parsel İÇİ eğim/mikro rölyef (istinat, kazı-dolgu) ölçülmez; "
                    "değerler parsel çevresi 90 m ve 300 m pencerelerin arazi eğimidir. "
                    "10 m Copernicus EEA-10 erişimi açılınca 'parsel eğimi (yaklaşık)' düzeyine geçer."),
            "merkez_rakim_m": round(center_elev, 1),
            "parsel_90m": parcel,
            "cevre_300m": area,
            "sorgu_zamani": datetime.now(timezone.utc).isoformat(),
        }
