"""Geofabrik Türkiye OSM PBF'inden ulaşım/lojistik/perakende/turizm katmanlarını çıkarır (osmium).

Kaynak: https://download.geofabrik.de/europe/turkey-latest.osm.pbf (md5 doğrulandı) — OpenStreetMap
katkıcıları, ODbL (atıf gerekir). Her katman:
  1) osmium tags-filter → katman .osm.pbf (geçici)
  2) osmium export → GeoJSONSeq (tüm etiketler + type/id/version/timestamp)   [ham, .geojsonseq.gz]
  3) DuckDB spatial: merkez nokta (lat/lon), WKT geometri, uzunluk/alan, kanonik mahalle poligonlarıyla
     il/ilçe/mahalle ataması → parquet
Rota ilişkileri (otobüs/tren/karayolu güzergâhları) OPL (üye listeli) olarak ayrıca yazılır.
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, fill_admin_nearest, now_iso  # noqa: E402

PBF = Path.home() / "Desktop/GEOPROP_RAW_INTAKE/osm_pbf/2026-09-24/turkey-latest.osm.pbf"
CANON = Path.home() / "Desktop/GEOPROP_CONSOLIDATION/canonical/v1.1/geoprop_canonical_v1_1.duckdb"
SRC = "https://download.geofabrik.de/europe/turkey-latest.osm.pbf"

LAYERS = {
    "limanlar": ["nwr/harbour", "nwr/landuse=port", "nwr/industrial=port", "nwr/seamark:type=harbour,port",
                 "nwr/amenity=ferry_terminal", "nwr/leisure=marina", "nwr/man_made=pier"],
    "demiryolu_hatlari": ["w/railway=rail,light_rail,subway,tram,narrow_gauge,construction,proposed,disused,abandoned,preserved,funicular"],
    "demiryolu_istasyonlari": ["nwr/railway=station,halt,yard,stop,junction,freight_station,goods,level_crossing,tram_stop",
                               "nwr/landuse=railway", "nwr/public_transport=station"],
    "otogarlar_duraklar": ["nwr/amenity=bus_station", "nwr/highway=bus_stop", "nwr/public_transport=platform,stop_position"],
    "sanayi_lojistik": ["nwr/landuse=industrial", "nwr/industrial", "nwr/building=warehouse,industrial,factory",
                        "nwr/office=logistics", "nwr/amenity=customs", "nwr/shop=wholesale", "nwr/man_made=works"],
    "tir_yol_hizmetleri": ["nwr/highway=rest_area,services,weighbridge,toll_gantry", "nwr/barrier=toll_booth",
                           "nwr/amenity=weighbridge", "nwr/hgv=designated", "nwr/access=hgv"],
    "akaryakit_istasyonlari": ["nwr/amenity=fuel"],
    "sinir_kapilari": ["nwr/barrier=border_control", "nwr/amenity=border_control"],
    "havalimanlari": ["nwr/aeroway=aerodrome,terminal,heliport,runway,apron,helipad"],
    "sarj_istasyonlari_osm": ["nwr/amenity=charging_station"],
    "ana_yol_agi": ["w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link"],
    "otoparklar": ["nwr/amenity=parking,parking_entrance,bicycle_parking,motorcycle_parking", "nwr/parking"],
    "pazar_hal": ["nwr/amenity=marketplace"],
    "perakende_poi": ["nwr/shop", "nwr/craft"],
    "ofis_poi": ["nwr/office"],
    "hizmet_poi": ["nwr/amenity", "nwr/healthcare", "nwr/leisure", "nwr/club"],
    "turizm_poi": ["nwr/tourism", "nwr/historic"],
    "enerji_altyapi": ["nwr/power=plant,generator,substation", "w/power=line"],
    "egitim_saglik_kamu": ["nwr/amenity=school,university,college,kindergarten,hospital,clinic,townhall,courthouse,police,fire_station,post_office",
                           "nwr/office=government"],
}
ROUTES = {"guzergah_iliskileri": ["r/route=train,railway,bus,coach,trolleybus,road,ferry,tram,subway,light_rail"]}


def sh(cmd):
    subprocess.run(cmd, check=True)


HISTORY_LAYERS = {"perakende_poi", "ofis_poi", "hizmet_poi", "turizm_poi", "akaryakit_istasyonlari",
                  "sarj_istasyonlari_osm", "otoparklar", "sanayi_lojistik", "tir_yol_hizmetleri", "egitim_saglik_kamu",
                  "pazar_hal", "otogarlar_duraklar"}


def main():
    global PBF, SRC
    args = sys.argv[1:]
    label, raw = None, True
    if "--pbf" in args:  # yerel geçmiş kesit: --pbf <yol> --label <YYYY-MM-DD>
        i = args.index("--pbf")
        PBF, SRC = Path(args[i + 1]), "file://" + args[i + 1]
        label = args[args.index("--label") + 1]
        args = [a for j, a in enumerate(args) if j not in (i, i + 1, args.index("--label"), args.index("--label") + 1)]
        raw = False
    only = set(args) or (HISTORY_LAYERS if label else set())
    it = Intake("osm_pbf_katmanlar", run_date=label)
    tmp = it.dir / "_tmp"
    tmp.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(f"attach '{CANON}' as k (read_only)")
    con.sql("create temp table mah as select geo_id, name, parent_geo_id, il_geo_id, geometry from k.geo_entity "
            "where level='mahalle' and geometry is not null")
    osm_ts = subprocess.run(["osmium", "fileinfo", "-g", "header.option.timestamp", str(PBF)],
                            capture_output=True, text=True).stdout.strip() or (label or "")
    for name, filt in {**LAYERS, **ROUTES}.items():
        if only and name not in only:
            continue
        if (it.dir / f"{name}.parquet").exists() or (it.dir / f"{name}.opl.gz").exists():
            continue
        it.log(f"{name}: filtre {filt}")
        f_pbf = tmp / f"{name}.osm.pbf"
        sh(["osmium", "tags-filter", "-O", "-o", str(f_pbf), str(PBF), *filt])
        if name in ROUTES:
            opl = tmp / f"{name}.opl"
            sh(["osmium", "tags-filter", "-O", "-R", "-o", str(opl), str(PBF), *filt])
            data = opl.read_bytes()
            it.save_bytes(f"{name}.opl", data, source_url=SRC, method="osmium_tags_filter_opl",
                          rows=data.count(b"\n"), note=f"OSM tabanı {osm_ts}; ilişki + üye listesi")
            opl.unlink()
            f_pbf.unlink()
            continue
        seq = tmp / f"{name}.geojsonseq"
        sh(["osmium", "export", "-O", "-f", "geojsonseq", "-x", "print_record_separator=false",
            "-a", "type,id,version,timestamp", "-o", str(seq), str(f_pbf)])
        # ham kayıt (sıkıştırılmış) — geçmiş kesitlerde kaynak PBF zaten yerelde olduğundan atlanır
        if raw:
            gz = it.dir / f"raw/{name}.geojsonseq.gz"
            gz.parent.mkdir(exist_ok=True)
            with seq.open("rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            n_raw = sum(1 for _ in seq.open("rb"))
            it._record(gz, source_url=SRC, method="osmium_export_geojsonseq", rows=n_raw,
                       note=f"OSM tabanı {osm_ts}")
        # düz tablo + idari atama
        con.sql(f"""
            create or replace temp table l as
            select (j.properties->>'@type') as osm_type, (j.properties->>'@id')::bigint as osm_id,
                   (j.properties->>'@version')::int as osm_version, (j.properties->>'@timestamp') as osm_timestamp,
                   (j.properties->>'name') as "name", (j.properties->>'operator') as "operator",
                   (j.properties->>'brand') as brand, (j.properties->>'ref') as "ref",
                   json(j.properties) as tags_json,
                   st_geomfromgeojson(json(j.geometry)) as g
            from read_json('{seq}', format='newline_delimited', columns={{'type':'VARCHAR','geometry':'JSON','properties':'JSON'}},
                           maximum_object_size=200000000) j
        """)
        df = con.sql("""
            select l.osm_type, l.osm_id, l.osm_version, l.osm_timestamp, l.name, l.operator, l.brand, l.ref, l.tags_json,
                   st_geometrytype(l.g)::varchar geom_type,
                   st_y(st_pointonsurface(l.g)) lat, st_x(st_pointonsurface(l.g)) lon,
                   case when st_geometrytype(l.g)::varchar like '%LINESTRING%' then st_length_spheroid(st_flipcoordinates(l.g)) end length_m,
                   case when st_geometrytype(l.g)::varchar like '%POLYGON%' then st_area_spheroid(st_flipcoordinates(l.g)) end area_m2,
                   st_astext(l.g) geometry_wkt,
                   m.geo_id mahalle_geo_id, m.name mahalle_adi, m.parent_geo_id ilce_geo_id, m.il_geo_id il_geo_id
            from l left join mah m on st_contains(m.geometry, st_pointonsurface(l.g))
        """).df()
        df = df.drop_duplicates(subset=["osm_type", "osm_id"])
        names = con.sql("select geo_id, name from k.geo_entity where level in ('il','ilce')").df().set_index("geo_id")["name"]
        df["ilce_adi"] = df.ilce_geo_id.map(names)
        df["il_adi"] = df.il_geo_id.map(names)
        df = fill_admin_nearest(df)
        df["osm_base_timestamp"] = osm_ts
        df["guncellenme_tarihi"] = now_iso()
        it.save_parquet(name, df, source_url=SRC, method="osmium_export+duckdb_spatial",
                        note="OpenStreetMap katkıcıları, ODbL; il/ilçe/mahalle = kanonik poligonlarla nokta-içinde")
        it.log(f"{name}: {len(df)} öğe; ile atanmış %{100 * df.il_geo_id.notna().mean():.1f}")
        seq.unlink()
        f_pbf.unlink()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
