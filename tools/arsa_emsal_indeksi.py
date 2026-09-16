#!/usr/bin/env python3
"""Silver katmanından linksiz, kanonik arsa ilanı ve mahalle endeksi üretir."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoprop.veri_yardimcilari import ensure_normalized_name_columns  # noqa: E402

INDEX_VERSION = "2.1.0"
ALLOWED_STATUSES = {"approved", "owner_rights_declared"}
ALLOWED_LICENSES = {"verified", "user_owned_verified", "user_publication_rights_declared"}


def allowed_source_rows(registry: dict) -> list[tuple[str, str, str, str, str]]:
    return [
        (
            item["content_sha256"].lower(), item.get("source_table", ""),
            item.get("domain", ""), item.get("entity", ""), item.get("source_id", ""),
        )
        for item in registry.get("mappings", [])
        if item.get("public_display_allowed")
        and item.get("registry_status") in ALLOWED_STATUSES
        and item.get("license_status") in ALLOWED_LICENSES
    ]


def parquet_files(root: Path, partitions: list[tuple[str, str]]) -> list[str]:
    return [
        str(path)
        for domain, entity in partitions
        for path in sorted((root / domain / entity).glob("*.parquet"))
    ]


def sql_paths(paths: list[str]) -> str:
    return "[" + ",".join("'" + path.replace("'", "''") + "'" for path in paths) + "]"


def stream_insert(duck, sqlite_connection, query: str, insert_sql: str) -> int:
    cursor = duck.execute(query)
    total = 0
    while True:
        rows = cursor.fetchmany(20_000)
        if not rows:
            return total
        sqlite_connection.executemany(insert_sql, rows)
        total += len(rows)


def create_listing_view(duck, files: list[str]) -> None:
    duck.execute(
        f"""
        CREATE VIEW listing_product_rows AS
        WITH candidates AS (
            SELECT o.*,
                json_array_length(json_keys(try_cast(normalized_record_json AS JSON))) completeness,
                row_number() OVER (
                    PARTITION BY natural_key_sha256
                    ORDER BY quality_score DESC, collection_time DESC NULLS LAST,
                             period DESC NULLS LAST, source_content_sha256, observation_id
                ) candidate_rank
            FROM read_parquet({sql_paths(files)}, union_by_name=true) o
            JOIN allowed_sources a
              ON lower(hex(o.source_content_sha256))=a.content_sha256
             AND o.source_table=a.source_table
            WHERE o.record_class='observed' AND o.validation_status='valid'
              AND o.product_policy='eligible_after_quality_and_license_validation'
        ), parsed AS (
            SELECT
                coalesce(j->>'$.ilan_id', j->>'$.ilan_no') raw_id,
                lower(coalesce(nullif(j->>'$.kaynak',''), nullif(j->>'$.portal',''),
                    CASE WHEN lower(coalesce(j->>'$.url',j->>'$.ilan_linki','')) LIKE '%emlakjet.%'
                         THEN 'emlakjet' ELSE 'unknown' END)) provider,
                lower(coalesce(j->>'$.kategori',j->>'$.ana_kategori','')) kategori,
                coalesce(j->>'$.tip',j->>'$.alt_tip',j->>'$.emlak_turu','Satılık Arsa') tip,
                coalesce(j->>'$.baslik',j->>'$.ilan_basligi') baslik,
                j->>'$.il' il, j->>'$.ilce' ilce, j->>'$.mahalle' mahalle,
                try_cast(j->>'$.fiyat_tl' AS DOUBLE) fiyat_tl,
                coalesce(try_cast(j->>'$.m2' AS DOUBLE),try_cast(j->>'$.m2_brut' AS DOUBLE),
                         try_cast(j->>'$.brut_m2' AS DOUBLE)) m2,
                coalesce(try_cast(j->>'$.m2_birim_fiyat' AS DOUBLE),
                         try_cast(j->>'$.birim_m2_fiyat' AS DOUBLE),
                         try_cast(j->>'$.birim_fiyat' AS DOUBLE),
                         try_cast(j->>'$.m2_birim_fiyati_tl' AS DOUBLE)) birim_m2_fiyat,
                coalesce(j->>'$.tarih',j->>'$.ilan_tarihi') ilan_tarihi,
                collection_time crawled_at,
                coalesce(try_cast(j->>'$.ilan_pin_lat' AS DOUBLE),try_cast(j->>'$.enlem' AS DOUBLE),
                         try_cast(j->>'$.lat' AS DOUBLE),try_cast(j->>'$.enlem_latitude' AS DOUBLE),
                         try_cast(j->>'$.mahalle_merkez_lat' AS DOUBLE)) enlem,
                coalesce(try_cast(j->>'$.ilan_pin_lon' AS DOUBLE),try_cast(j->>'$.boylam' AS DOUBLE),
                         try_cast(j->>'$.lon' AS DOUBLE),try_cast(j->>'$.boylam_longitude' AS DOUBLE),
                         try_cast(j->>'$.mahalle_merkez_lon' AS DOUBLE)) boylam,
                coalesce(j->>'$.ada_no',j->>'$.ada') ada_no,
                coalesce(j->>'$.parsel_no',j->>'$.parsel') parsel_no,
                j->>'$.imar_durumu' imar_durumu, j->>'$.kaks_emsal' kaks_emsal,
                lower(hex(source_content_sha256)) source_hash, source_table,
                quality_score, completeness
            FROM (SELECT *, try_cast(normalized_record_json AS JSON) j FROM candidates)
            WHERE candidate_rank=1
        ), keyed AS (
            SELECT concat(provider,':',CASE WHEN provider='emlakjet'
                THEN regexp_replace(lower(raw_id),'^ej_','') ELSE lower(raw_id) END) listing_key, *
            FROM parsed
            WHERE kategori='arsa' AND raw_id IS NOT NULL
              AND fiyat_tl>0 AND m2>0 AND birim_m2_fiyat>0
        ), ranked AS (
            SELECT *, row_number() OVER (PARTITION BY listing_key ORDER BY
                quality_score DESC, completeness DESC, crawled_at DESC NULLS LAST,
                source_hash, source_table) product_rank,
                count(*) OVER (PARTITION BY listing_key) candidate_count
            FROM keyed
        )
        SELECT * EXCLUDE(product_rank) FROM ranked WHERE product_rank=1
        """
    )


def create_market_view(duck, files: list[str]) -> None:
    if not files:
        return
    duck.execute(
        f"""
        CREATE VIEW owner_market_rows AS
        WITH ranked AS (
            SELECT o.*, row_number() OVER (
                PARTITION BY natural_key_sha256 ORDER BY
                CASE record_class WHEN 'observed' THEN 2 WHEN 'projection' THEN 1 ELSE 0 END DESC,
                quality_score DESC, collection_time DESC NULLS LAST,
                source_content_sha256, observation_id
            ) canonical_rank
            FROM read_parquet({sql_paths(files)}, union_by_name=true) o
            JOIN allowed_sources a
              ON lower(hex(o.source_content_sha256))=a.content_sha256
             AND o.source_table=a.source_table
            WHERE a.source_id='owner_land_neighbourhood_index_2026_09'
              AND o.record_class IN ('observed','projection')
              AND o.validation_status='valid'
        ) SELECT * FROM ranked WHERE canonical_rank=1
        """
    )


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE ilanlar (
          listing_key TEXT PRIMARY KEY, ilan_id NUMERIC, provider TEXT NOT NULL,
          kategori TEXT NOT NULL, tip TEXT, baslik TEXT, il TEXT NOT NULL, ilce TEXT, mahalle TEXT,
          fiyat_tl REAL NOT NULL, m2 REAL NOT NULL, birim_m2_fiyat REAL NOT NULL,
          ilan_tarihi TEXT, crawled_at TEXT, enlem REAL, boylam REAL, ada_no TEXT, parsel_no TEXT,
          imar_durumu TEXT, kaks_emsal TEXT, source_content_sha256 TEXT NOT NULL,
          source_table TEXT NOT NULL, quality_score INTEGER, source_candidate_count INTEGER NOT NULL,
          source_url TEXT CHECK(source_url IS NULL));
        CREATE TABLE arsa_mahalle_ozet (
          kategori TEXT NOT NULL, city_id INTEGER, county_id INTEGER, district_id INTEGER,
          il TEXT, ilce TEXT, mahalle TEXT, slug TEXT, donem TEXT,
          satilik_m2_fiyat REAL, min_m2_fiyat REAL, max_m2_fiyat REAL, ortalama_fiyat REAL,
          ortalama_m2 REAL, fiyat_endeksi REAL, aylik_fiyat_degisim REAL,
          yillik_fiyat_degisim REAL, ilan_sayisi INTEGER, ilanda_kalma_suresi_gun INTEGER,
          stok_degisim_orani REAL, yillik_stok_degisim REAL, guncellenme_tarihi TEXT,
          source_content_sha256 TEXT NOT NULL, source_url TEXT CHECK(source_url IS NULL),
          PRIMARY KEY(kategori,city_id,county_id,district_id,donem)) WITHOUT ROWID;
        CREATE TABLE arsa_mahalle_trend (
          kategori TEXT NOT NULL, city_id INTEGER, county_id INTEGER, district_id INTEGER,
          il TEXT, ilce TEXT, mahalle TEXT, ay TEXT NOT NULL, satilik_m2_fiyat REAL,
          min_m2_fiyat REAL, max_m2_fiyat REAL, ortalama_fiyat REAL, ortalama_m2 REAL,
          fiyat_endeksi REAL, ilan_sayisi INTEGER, aylik_fiyat_degisim REAL,
          yillik_fiyat_degisim REAL, ilanda_kalma_suresi_gun INTEGER, record_class TEXT NOT NULL,
          guncellenme_tarihi TEXT, source_content_sha256 TEXT NOT NULL,
          source_url TEXT CHECK(source_url IS NULL),
          PRIMARY KEY(kategori,city_id,county_id,district_id,ay)) WITHOUT ROWID;
        CREATE TABLE arsa_alan_segmentleri (
          kategori TEXT NOT NULL, city_id INTEGER, county_id INTEGER, district_id INTEGER,
          bolge_adi TEXT, segment_adi TEXT NOT NULL, satilik_m2_fiyat REAL,
          min_m2_fiyat REAL, max_m2_fiyat REAL, ortalama_fiyat REAL, ortalama_m2 REAL,
          ilan_sayisi INTEGER, ilan_orani REAL, ilanda_kalma_suresi INTEGER,
          guncellenme_tarihi TEXT, source_content_sha256 TEXT NOT NULL,
          source_url TEXT CHECK(source_url IS NULL),
          PRIMARY KEY(kategori,city_id,county_id,district_id,segment_adi)) WITHOUT ROWID;
        """
    )


def market_select(entity: str, fields: list[tuple[str, str]], observed_only: bool = False) -> str:
    expressions = []
    for name, sql_type in fields:
        value = f"j->>'$.{name}'"
        expressions.append(f"try_cast({value} AS {sql_type})" if sql_type else value)
    where = f"entity='{entity}'" + (" AND record_class='observed'" if observed_only else "")
    return (
        "SELECT " + ",".join(expressions)
        + ", lower(hex(source_content_sha256)), NULL "
        + "FROM (SELECT *,try_cast(normalized_record_json AS JSON) j FROM owner_market_rows) "
        + f"WHERE {where}"
    )


def build_index(silver_root: Path, registry: dict, output_database: Path) -> dict:
    root = silver_root.expanduser().resolve()
    listing_files = parquet_files(root, [
        ("cadastre_planning","parcel_zoning"), ("listing_observation","listing"),
        ("listing_observation","land_listing"),
    ])
    market_files = parquet_files(root, [
        ("property_market","price_summary"), ("property_market","price_trend"),
        ("property_market","price_distribution"), ("property_market","price_heatmap"),
    ])
    if not listing_files:
        raise RuntimeError("Silver arsa ilanı Parquet dosyaları bulunamadı.")
    allowed = allowed_source_rows(registry)
    if not allowed:
        raise RuntimeError("Yayımlanabilir kaynak bulunamadı.")

    duck = duckdb.connect()
    duck.execute("CREATE TABLE allowed_sources(content_sha256 VARCHAR,source_table VARCHAR,domain VARCHAR,entity VARCHAR,source_id VARCHAR)")
    duck.executemany("INSERT INTO allowed_sources VALUES (?,?,?,?,?)", allowed)
    create_listing_view(duck, listing_files)
    create_market_view(duck, market_files)

    output = output_database.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.",suffix=".tmp",dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    counts = {"listings":0,"listing_conflict_keys":0,"market_summary":0,"market_trend":0,"market_segments":0}
    try:
        with sqlite3.connect(temporary) as connection:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            create_schema(connection)
            counts["listings"] = stream_insert(duck, connection,
                """SELECT listing_key,raw_id,provider,kategori,tip,coalesce(baslik,concat('Arsa ilanı ',raw_id)),
                il,ilce,mahalle,fiyat_tl,m2,birim_m2_fiyat,ilan_tarihi,crawled_at,enlem,boylam,
                ada_no,parsel_no,imar_durumu,kaks_emsal,source_hash,source_table,quality_score,candidate_count,NULL
                FROM listing_product_rows ORDER BY listing_key""",
                "INSERT INTO ilanlar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
            counts["listing_conflict_keys"] = duck.execute(
                "SELECT count(*) FROM listing_product_rows WHERE candidate_count>1").fetchone()[0]

            summary_fields = [(n,t) for n,t in [
                ("kategori",""),("city_id","INTEGER"),("county_id","INTEGER"),("district_id","INTEGER"),
                ("il_adi",""),("ilce_adi",""),("mahalle_adi",""),("slug",""),("donem",""),
                ("satilik_m2_fiyat","DOUBLE"),("min_m2_fiyat","DOUBLE"),("max_m2_fiyat","DOUBLE"),
                ("ortalama_fiyat","DOUBLE"),("ortalama_m2","DOUBLE"),("fiyat_endeksi","DOUBLE"),
                ("aylik_fiyat_degisim","DOUBLE"),("yillik_fiyat_degisim","DOUBLE"),("ilan_sayisi","INTEGER"),
                ("ilanda_kalma_suresi_gun","INTEGER"),("stok_degisim_orani","DOUBLE"),
                ("yillik_stok_degisim","DOUBLE"),("guncellenme_tarihi","")]]
            trend_fields = [(n,t) for n,t in [
                ("kategori",""),("city_id","INTEGER"),("county_id","INTEGER"),("district_id","INTEGER"),
                ("il_adi",""),("ilce_adi",""),("mahalle_adi",""),("ay",""),
                ("satilik_m2_fiyat","DOUBLE"),("min_m2_fiyat","DOUBLE"),("max_m2_fiyat","DOUBLE"),
                ("ortalama_fiyat","DOUBLE"),("ortalama_m2","DOUBLE"),("fiyat_endeksi","DOUBLE"),
                ("ilan_sayisi","INTEGER"),("aylik_fiyat_degisim","DOUBLE"),("yillik_fiyat_degisim","DOUBLE"),
                ("ilanda_kalma_suresi_gun","INTEGER")]]
            segment_fields = [(n,t) for n,t in [
                ("kategori",""),("city_id","INTEGER"),("county_id","INTEGER"),("district_id","INTEGER"),
                ("bolge_adi",""),("segment_adi",""),("satilik_m2_fiyat","DOUBLE"),
                ("min_m2_fiyat","DOUBLE"),("max_m2_fiyat","DOUBLE"),("ortalama_fiyat","DOUBLE"),
                ("ortalama_m2","DOUBLE"),("ilan_sayisi","INTEGER"),("ilan_orani","DOUBLE"),
                ("ilanda_kalma_suresi","INTEGER"),("guncellenme_tarihi","")]]
            if market_files:
                counts["market_summary"] = stream_insert(duck,connection,
                    market_select("price_summary",summary_fields,True),
                    "INSERT OR REPLACE INTO arsa_mahalle_ozet VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
                trend_query = market_select("price_trend",trend_fields).replace(
                    ", lower(hex(source_content_sha256)), NULL ",
                    ", record_class, j->>'$.guncellenme_tarihi', lower(hex(source_content_sha256)), NULL ")
                counts["market_trend"] = stream_insert(duck,connection,trend_query,
                    "INSERT OR REPLACE INTO arsa_mahalle_trend VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
                counts["market_segments"] = stream_insert(duck,connection,
                    market_select("price_distribution",segment_fields,True),
                    "INSERT OR REPLACE INTO arsa_alan_segmentleri VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

            connection.executescript("""
              CREATE INDEX idx_ilanlar_location ON ilanlar(il,ilce,mahalle);
              CREATE INDEX idx_ilanlar_coordinates ON ilanlar(enlem,boylam);
              CREATE INDEX idx_ilanlar_unit_price ON ilanlar(birim_m2_fiyat);
              CREATE INDEX idx_endeks_location ON arsa_mahalle_ozet(il,ilce,mahalle);
              CREATE INDEX idx_trend_location_period ON arsa_mahalle_trend(il,ilce,mahalle,ay);
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            # İsim→id/scope çözümü fonksiyonsuz WHERE ile indeksten yapılsın (bkz. veri_yardimcilari).
            for table in ("ilanlar", "arsa_mahalle_ozet", "arsa_mahalle_trend"):
                ensure_normalized_name_columns(connection, table)
            generated_at = datetime.now(timezone.utc).isoformat()
            connection.executemany("INSERT INTO metadata VALUES (?,?)",[
                ("index_version",INDEX_VERSION),("generated_at",generated_at),("public_source_links","false"),
                *[(key,str(value)) for key,value in counts.items()],
            ])
            connection.execute("PRAGMA optimize")
        os.replace(temporary,output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        duck.close()
    return {"index_version":INDEX_VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),
            "database":str(output),"row_count":counts["listings"],"counts":counts,
            "allowed_source_units":len(allowed),"public_source_links":False,
            "internal_provenance_retained":True}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="Linksiz arsa emsal ve mahalle endeks veritabanı üretir")
    parser.add_argument("--calistir",action="store_true")
    parser.add_argument("--silver-root",required=True,type=Path)
    parser.add_argument("--source-registry",required=True,type=Path)
    parser.add_argument("--output-database",required=True,type=Path)
    args=parser.parse_args(argv)
    if not args.calistir:
        parser.error("İndeks üretimi için açıkça --calistir verin")
    report=build_index(args.silver_root,json.loads(args.source_registry.read_text(encoding="utf-8")),args.output_database)
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
