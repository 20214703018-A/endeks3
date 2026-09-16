"""OSM idari sınırlar (il admin_level=4, ilçe admin_level=6) → warehouse/product/idari_sinirlar.sqlite

Kullanım: koordinat → il/ilçe (poligon içerme). Kaynak © OpenStreetMap katkıcıları (ODbL).
Tablo: sinir(id, seviye 'il'|'ilce', ad, ad_norm, il_adi, lat, lon, min_lat, max_lat, min_lon, max_lon, geometri GeoJSON)
"""
from __future__ import annotations
import json, os, sqlite3, sys, time
import osmium
from shapely.geometry import MultiPolygon, Polygon, mapping
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402
PBF = os.path.join(REPO, "warehouse", "raw", "osm", "turkey-latest.osm.pbf")
OUT = os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite")


class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.rows = []
    def area(self, a):
        t = a.tags
        if t.get("boundary") != "administrative" or t.get("admin_level") not in ("4", "6") or not t.get("name"):
            return
        try:
            polys = []
            for ring in a.outer_rings():
                ext = [(p.location.lon, p.location.lat) for p in ring if p.location.valid()]
                holes = [[(p.location.lon, p.location.lat) for p in ir if p.location.valid()] for ir in a.inner_rings(ring)]
                if len(ext) >= 4: polys.append(Polygon(ext, [h for h in holes if len(h) >= 4]))
            if not polys: return
            g = (MultiPolygon(polys) if len(polys) > 1 else polys[0]).buffer(0).simplify(0.0003, preserve_topology=True)
            c = g.representative_point(); b = g.bounds
            self.rows.append(("il" if t["admin_level"] == "4" else "ilce", t["name"], normalize_name(t["name"]), None, c.y, c.x, b[1], b[3], b[0], b[2], json.dumps(mapping(g))))
        except Exception:
            return


def main():
    h = H(); t0 = time.time(); h.apply_file(PBF, locations=True, idx="flex_mem")
    c = sqlite3.connect(OUT + ".tmp")
    c.executescript("""CREATE TABLE sinir (id INTEGER PRIMARY KEY, seviye TEXT, ad TEXT, ad_norm TEXT, il_adi TEXT, lat REAL, lon REAL,
                       min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL, geometri TEXT);
                       CREATE INDEX idx_sinir_bbox ON sinir (seviye, min_lat, max_lat, min_lon, max_lon);""")
    c.executemany("INSERT INTO sinir (seviye, ad, ad_norm, il_adi, lat, lon, min_lat, max_lat, min_lon, max_lon, geometri) VALUES (?,?,?,?,?,?,?,?,?,?,?)", h.rows)
    # ilçe → il: ilçe temsilci noktası hangi il poligonunda
    from shapely.geometry import shape, Point
    iller = [(ad, shape(json.loads(g))) for ad, g in c.execute("SELECT ad, geometri FROM sinir WHERE seviye='il'")]
    for sid, lat, lon in c.execute("SELECT id, lat, lon FROM sinir WHERE seviye='ilce'").fetchall():
        p = Point(lon, lat)
        for ad, g in iller:
            if g.contains(p): c.execute("UPDATE sinir SET il_adi=? WHERE id=?", (ad, sid)); break
    c.commit(); c.close(); os.replace(OUT + ".tmp", OUT)
    print(f"il: {sum(1 for r in h.rows if r[0]=='il')}, ilçe: {sum(1 for r in h.rows if r[0]=='ilce')}, {time.time()-t0:.0f}s → {OUT}")


if __name__ == "__main__":
    main()
