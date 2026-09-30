"""Kamu binası noktalarının çevresindeki OSM bina poligonları (taban alanı + kat) — memur yoğunluğu türetimi girdisi.

Hedef noktalar: osm_pbf_katmanlar/2026-09-24/egitim_saglik_kamu.parquet içindeki okul/hastane/üniversite DIŞI kamu
nesneleri (office=government, amenity=townhall/courthouse/police/fire_station/post_office). Bina kaynağı: yerel tam
Türkiye kesiti ~/Downloads/turkey-250101-internal.osm.pbf (Geofabrik, 2025-01-01; 2026-09-24 kesiti disk
temizliğinde silinmiş — binalar yavaş değişir, kesit tarihi her satırda yazılı).

Disk kısıtı (≈3 GB boş) nedeniyle ara dosya yok: osmium tags-filter | osmium export (geojsonseq) | Python akış
süzgeci. Süzgeç: bina poligonunun temsil noktası herhangi bir hedef noktaya ≤ 150 m ise tutulur (0,002° ızgara).
Çıktı: GEOPROP_RAW_INTAKE/osm_kamu_bina/<tarih>/kamu_cevresi_binalar.parquet (tüm etiketler + WKT + alan) ve
hedef_noktalar.parquet.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

PBF = Path.home() / "Downloads/turkey-250101-internal.osm.pbf"
LAYER = Path.home() / "Desktop/GEOPROP_RAW_INTAKE/osm_pbf_katmanlar/2026-09-24/egitim_saglik_kamu.parquet"
RADIUS_M = 150
CELL = 0.002  # derece (~220 m enlem)


def targets() -> pd.DataFrame:
    return duckdb.sql(f"""
        select osm_type, osm_id, name, lat, lon, geom_type, geometry_wkt, il_adi, ilce_adi, tags_json,
               coalesce(json_extract_string(tags_json,'$.amenity'), 'office=' || json_extract_string(tags_json,'$.office')) tur
        from '{LAYER}'
        where (json_extract_string(tags_json,'$.office') = 'government'
               or json_extract_string(tags_json,'$.amenity') in ('townhall','courthouse','police','fire_station','post_office'))
          and lat is not null""").df()


def main():
    it = Intake("osm_kamu_bina", rate=0)
    t = targets()
    it.save_parquet("hedef_noktalar", t, source_url=str(LAYER), method="duckdb_filter",
                    note="okul/hastane/üniversite dışı kamu nesneleri")
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for la, lo in zip(t.lat, t.lon):
        grid.setdefault((int(la // CELL), int(lo // CELL)), []).append((la, lo))

    def near(la: float, lo: float) -> bool:
        ci, cj = int(la // CELL), int(lo // CELL)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for pa, po in grid.get((ci + di, cj + dj), ()):
                    dy = (la - pa) * 111_320
                    dx = (lo - po) * 111_320 * math.cos(math.radians(pa))
                    if dx * dx + dy * dy <= RADIUS_M ** 2:
                        return True
        return False

    it.log(f"{len(t)} hedef nokta; akış başlıyor ({PBF.name})")
    # export çok poligonlu ilişkiler için girdiyi iki kez okur → stdin olamaz; yalnız binaları içeren geçici pbf
    tmp = it.dir / "_binalar_gecici.osm.pbf"
    subprocess.run(["osmium", "tags-filter", "-O", "-o", str(tmp), str(PBF), "w/building", "r/building"], check=True)
    it.log(f"geçici bina dosyası {tmp.stat().st_size / 1e6:.0f} MB")
    f2 = subprocess.Popen(["osmium", "export", str(tmp), "-f", "geojsonseq", "-o", "-",
                           "--geometry-types=polygon", "-a", "type,id,version,timestamp", "-i", "sparse_mem_array"],
                          stdout=subprocess.PIPE, text=True)
    keep, n = [], 0
    for line in f2.stdout:
        line = line.lstrip("\x1e").strip()
        if not line:
            continue
        n += 1
        g = json.loads(line)
        ring = g["geometry"]["coordinates"][0] if g["geometry"]["type"] == "Polygon" else g["geometry"]["coordinates"][0][0]
        lo = sum(p[0] for p in ring) / len(ring)
        la = sum(p[1] for p in ring) / len(ring)
        if near(la, lo):
            p = g["properties"]
            keep.append({"osm_type": p.pop("@type", None), "osm_id": p.pop("@id", None),
                         "osm_version": p.pop("@version", None), "osm_timestamp": p.pop("@timestamp", None),
                         "building": p.get("building"), "levels": p.get("building:levels"), "name": p.get("name"),
                         "tags_json": json.dumps(p, ensure_ascii=False), "geometry_geojson": json.dumps(g["geometry"]),
                         "lat": la, "lon": lo})
        if n % 1_000_000 == 0:
            it.log(f"{n:,} bina okundu, {len(keep):,} tutuldu")
    f2.wait()
    tmp.unlink()
    it.log(f"toplam {n:,} bina, {len(keep):,} kamu noktası çevresinde")
    df = pd.DataFrame(keep)
    con = duckdb.connect()
    con.sql("load spatial")
    con.register("b", df)
    df = con.sql("""select * exclude (geometry_geojson),
                           st_astext(st_geomfromgeojson(geometry_geojson)) geometry_wkt,
                           st_area_spheroid(st_flipcoordinates(st_geomfromgeojson(geometry_geojson))) taban_alani_m2
                    from b""").df()
    df["osm_kesit"] = "2025-01-01"
    df["guncellenme_tarihi"] = now_iso()
    it.save_parquet("kamu_cevresi_binalar", df, source_url="file://" + str(PBF), method="osmium_stream_filter",
                    note=f"bina poligonu temsil noktası hedef kamu noktasına ≤ {RADIUS_M} m; toplam okunan bina {n}")


if __name__ == "__main__":
    main()
