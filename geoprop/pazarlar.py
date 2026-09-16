"""Semt pazarları — pazarlar.sqlite: en yakın N, 1 km/3 km sayımı, güne göre dağılım, kapalı/açık."""
from __future__ import annotations
import math, sqlite3
from pathlib import Path
from typing import Any
from .veri_yardimcilari import haversine_km

GUN_ETIKET = {"pazartesi": "Pazartesi", "sali": "Salı", "carsamba": "Çarşamba", "persembe": "Perşembe", "cuma": "Cuma", "cumartesi": "Cumartesi", "pazar": "Pazar"}
ARAMA_KM = 3.0


class MarketEngine:
    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='pazar'").fetchone():
                return None
            dlat = ARAMA_KM / 111.0; dlon = ARAMA_KM / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
            items = []
            for r in c.execute("SELECT * FROM pazar WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (lat - dlat, lat + dlat, lon - dlon, lon + dlon)):
                d = haversine_km(lat, lon, r["lat"], r["lon"])
                if d <= ARAMA_KM:
                    items.append({"ad": r["ad"], "ilce": r["ilce"], "mahalle": r["mahalle"], "gunler": [GUN_ETIKET.get(g, g) for g in (r["gunler"] or "").split(",") if g],
                                  "kapali": r["kapali"], "tip": r["tip"], "kaynak": r["kaynak"], "lat": r["lat"], "lon": r["lon"], "mesafe_m": round(d * 1000),
                                  # HKS kayıtları adresten kodlanır (mahalle/sokak merkezi) → konum yaklaşık
                                  "konum_yaklasik": r["kaynak"] == "hks"})
            items.sort(key=lambda x: x["mesafe_m"])
            gun_dagilim: dict[str, int] = {}
            for x in items:
                for g in x["gunler"]: gun_dagilim[g] = gun_dagilim.get(g, 0) + 1
            kaps = {r["kaynak"]: r["satir"] for r in c.execute("SELECT kaynak, satir FROM kapsama")}
        finally:
            c.close()
        return {"status": "available", "arama_km": ARAMA_KM,
                "sayim": {"1000m": sum(1 for x in items if x["mesafe_m"] <= 1000), "3000m": len(items),
                          "kapali_3000m": sum(1 for x in items if x["kapali"] == 1), "gunu_bilinen_3000m": sum(1 for x in items if x["gunler"])},
                "gun_dagilimi": gun_dagilim, "en_yakin": items[:6],
                "kaynak": "HKS (hal.gov.tr) pazar yeri kayıtları + İBB ve İzmir BB açık verisi (mahalle, gün, tip) + OSM (© OpenStreetMap katkıcıları)",
                "kapsam_notu": (f"Belediye açık verisi: İstanbul {kaps.get('ibb_acik_veri', 0)}, İzmir {kaps.get('izbb_acik_veri', 0)}; "
                                f"HKS pazar yeri kaydı {kaps.get('hks', 0)} (adres kodlaması, konum mahalle/sokak düzeyinde yaklaşık); "
                                f"OSM {kaps.get('osm', 0)} (gün/kapalı bilgisi kısmi). Mahalle bilgisi belediye ve HKS kayıtlarında.")}
