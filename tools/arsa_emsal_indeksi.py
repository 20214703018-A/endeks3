#!/usr/bin/env python3
"""Yayımlanabilir Silver arsa gözlemlerinden hızlı, linksiz ürün indeksi üretir."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb


INDEX_VERSION = "1.0.0"


def allowed_source_rows(registry: dict) -> list[tuple[str, str]]:
    allowed = []
    for item in registry.get("mappings", []):
        if (
            item.get("public_display_allowed")
            and item.get("registry_status") in {"approved", "owner_rights_declared"}
            and item.get("license_status")
            in {"verified", "user_owned_verified", "user_publication_rights_declared"}
            and item.get("domain") == "cadastre_planning"
            and item.get("entity") == "parcel_zoning"
        ):
            allowed.append((item["content_sha256"].lower(), item.get("source_table", "")))
    return allowed


def build_index(silver_root: Path, registry: dict, output_database: Path) -> dict:
    silver_root = silver_root.expanduser().resolve()
    parcel_partition = silver_root / "cadastre_planning" / "parcel_zoning"
    silver_glob = str(parcel_partition / "*.parquet")
    if not list(parcel_partition.glob("*.parquet")):
        raise RuntimeError("Silver arsa/parsel Parquet dosyaları bulunamadı.")
    allowed = allowed_source_rows(registry)
    if not allowed:
        raise RuntimeError("Yayımlanabilir arsa/parsel kaynağı bulunamadı.")

    query_db = duckdb.connect()
    query_db.execute("CREATE TABLE allowed_sources(content_sha256 VARCHAR, source_table VARCHAR)")
    query_db.executemany("INSERT INTO allowed_sources VALUES (?, ?)", allowed)
    rows = query_db.execute(
        """
        WITH candidates AS (
            SELECT
                observations.*,
                row_number() OVER (
                    PARTITION BY natural_key_sha256
                    ORDER BY quality_score DESC, collection_time DESC NULLS LAST,
                             period DESC NULLS LAST, source_content_sha256, observation_id
                ) AS candidate_rank
            FROM read_parquet(?, union_by_name=true) AS observations
            JOIN allowed_sources
              ON lower(hex(observations.source_content_sha256)) = allowed_sources.content_sha256
             AND observations.source_table = allowed_sources.source_table
            WHERE observations.record_class = 'observed'
              AND observations.validation_status = 'valid'
              AND observations.product_policy = 'eligible_after_quality_and_license_validation'
        ), parsed AS (
            SELECT
                try_cast(json_extract_string(normalized_record_json, '$.ilan_id') AS BIGINT) AS ilan_id,
                coalesce(json_extract_string(normalized_record_json, '$.kategori'), 'arsa') AS kategori,
                coalesce(json_extract_string(normalized_record_json, '$.tip'), 'Satılık Arsa') AS tip,
                json_extract_string(normalized_record_json, '$.il') AS il,
                json_extract_string(normalized_record_json, '$.ilce') AS ilce,
                json_extract_string(normalized_record_json, '$.mahalle') AS mahalle,
                try_cast(json_extract_string(normalized_record_json, '$.fiyat_tl') AS DOUBLE) AS fiyat_tl,
                try_cast(json_extract_string(normalized_record_json, '$.m2') AS DOUBLE) AS m2,
                coalesce(
                    try_cast(json_extract_string(normalized_record_json, '$.m2_birim_fiyat') AS DOUBLE),
                    try_cast(json_extract_string(normalized_record_json, '$.birim_m2_fiyat') AS DOUBLE)
                ) AS birim_m2_fiyat,
                coalesce(
                    json_extract_string(normalized_record_json, '$.tarih'),
                    json_extract_string(normalized_record_json, '$.ilan_tarihi')
                ) AS ilan_tarihi,
                collection_time AS crawled_at,
                coalesce(
                    try_cast(json_extract_string(normalized_record_json, '$.ilan_pin_lat') AS DOUBLE),
                    try_cast(json_extract_string(normalized_record_json, '$.enlem') AS DOUBLE),
                    try_cast(json_extract_string(normalized_record_json, '$.mahalle_merkez_lat') AS DOUBLE)
                ) AS enlem,
                coalesce(
                    try_cast(json_extract_string(normalized_record_json, '$.ilan_pin_lon') AS DOUBLE),
                    try_cast(json_extract_string(normalized_record_json, '$.boylam') AS DOUBLE),
                    try_cast(json_extract_string(normalized_record_json, '$.mahalle_merkez_lon') AS DOUBLE)
                ) AS boylam,
                json_extract_string(normalized_record_json, '$.ada_no') AS ada_no,
                json_extract_string(normalized_record_json, '$.parsel_no') AS parsel_no,
                json_extract_string(normalized_record_json, '$.imar_durumu') AS imar_durumu,
                json_extract_string(normalized_record_json, '$.kaks_emsal') AS kaks_emsal,
                lower(hex(source_content_sha256)) AS source_content_sha256,
                source_table,
                quality_score
            FROM candidates
            WHERE candidate_rank = 1
        ), product_ranked AS (
            SELECT
                parsed.*,
                row_number() OVER (
                    PARTITION BY ilan_id
                    ORDER BY quality_score DESC, crawled_at DESC NULLS LAST,
                             source_content_sha256, source_table
                ) AS product_rank
            FROM parsed
            WHERE lower(kategori) = 'arsa'
              AND ilan_id IS NOT NULL
              AND fiyat_tl > 0 AND m2 > 0 AND birim_m2_fiyat > 0
        )
        SELECT *
        EXCLUDE (product_rank)
        FROM product_ranked
        WHERE product_rank = 1
        ORDER BY ilan_id
        """,
        [silver_glob],
    ).fetchall()
    query_db.close()

    output_database = output_database.expanduser().resolve()
    output_database.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output_database.name}.", suffix=".tmp", dir=output_database.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(temporary) as connection:
            connection.execute(
                """
                CREATE TABLE ilanlar (
                    ilan_id INTEGER PRIMARY KEY,
                    kategori TEXT NOT NULL,
                    tip TEXT,
                    baslik TEXT,
                    il TEXT NOT NULL,
                    ilce TEXT,
                    mahalle TEXT,
                    fiyat_tl REAL NOT NULL,
                    m2 REAL NOT NULL,
                    birim_m2_fiyat REAL NOT NULL,
                    ilan_tarihi TEXT,
                    crawled_at TEXT,
                    enlem REAL,
                    boylam REAL,
                    ada_no TEXT,
                    parsel_no TEXT,
                    imar_durumu TEXT,
                    kaks_emsal TEXT,
                    source_content_sha256 TEXT NOT NULL,
                    source_table TEXT NOT NULL,
                    quality_score INTEGER,
                    source_url TEXT CHECK(source_url IS NULL)
                )
                """
            )
            connection.executemany(
                "INSERT INTO ilanlar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                [
                    (
                        row[0], row[1], row[2], f"Arsa ilanı {row[0]}", row[3], row[4], row[5],
                        row[6], row[7], row[8], row[9], row[10], row[11], row[12], row[13],
                        row[14], row[15], row[16], row[17], row[18], row[19],
                    )
                    for row in rows
                ],
            )
            connection.execute("CREATE INDEX idx_ilanlar_location ON ilanlar(il, ilce, mahalle)")
            connection.execute("CREATE INDEX idx_ilanlar_coordinates ON ilanlar(enlem, boylam)")
            connection.execute("CREATE INDEX idx_ilanlar_unit_price ON ilanlar(birim_m2_fiyat)")
            connection.execute(
                "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [
                    ("index_version", INDEX_VERSION),
                    ("generated_at", datetime.now(timezone.utc).isoformat()),
                    ("public_source_links", "false"),
                    ("row_count", str(len(rows))),
                ],
            )
        os.replace(temporary, output_database)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "index_version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(output_database),
        "row_count": len(rows),
        "allowed_source_units": len(allowed),
        "public_source_links": False,
        "internal_provenance_retained": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Linksiz ve hızlı arsa emsal ürün indeksi üretir")
    parser.add_argument("--calistir", action="store_true")
    parser.add_argument("--silver-root", required=True, type=Path)
    parser.add_argument("--source-registry", required=True, type=Path)
    parser.add_argument("--output-database", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.calistir:
        parser.error("İndeks üretimi için açıkça --calistir verin")
    registry = json.loads(args.source_registry.read_text(encoding="utf-8"))
    report = build_index(args.silver_root, registry, args.output_database)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
