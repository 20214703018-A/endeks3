"""Koordinat → il/ilçe (OSM idari sınır poligonları; idari_sinirlar.sqlite). Bbox ön eleme + shapely içerme."""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
from shapely.geometry import Point, shape


class AdminLookup:
    def __init__(self, database: str | Path):
        self.database = Path(database); self._cache: dict[int, object] = {}
        self._conn = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) if self.database.exists() else None

    def _geom(self, sid, g):
        if sid not in self._cache: self._cache[sid] = shape(json.loads(g))
        return self._cache[sid]

    def lookup(self, lat: float, lon: float) -> tuple[str | None, str | None]:
        if self._conn is None: return (None, None)
        p = Point(lon, lat)
        for sid, ad, il_adi, g in self._conn.execute("SELECT id, ad, il_adi, geometri FROM sinir WHERE seviye='ilce' AND min_lat<=? AND max_lat>=? AND min_lon<=? AND max_lon>=?", (lat, lat, lon, lon)):
            if self._geom(sid, g).contains(p): return (il_adi, ad)
        for sid, ad, g in self._conn.execute("SELECT id, ad, geometri FROM sinir WHERE seviye='il' AND min_lat<=? AND max_lat>=? AND min_lon<=? AND max_lon>=?", (lat, lat, lon, lon)):
            if self._geom(sid, g).contains(p): return (ad, None)
        return (None, None)
