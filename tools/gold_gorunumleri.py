#!/usr/bin/env python3
"""Silver Parquet katmanı üzerinde kayıpsız, sürümlü Gold DuckDB görünümleri kurar."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb


GOLD_VERSION = "1.3.0"
RESOLUTION_RULE_VERSION = "canonical_v4_explainable_conflict_resolution"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def provenance_rows(silver_manifest: dict) -> list[tuple]:
    rows = []
    for source in silver_manifest["sources"]:
        rows.append(
            (
                source.get("content_sha256", source.get("content_hash")),
                source.get("source_table", ""),
                source.get("source_locator", source.get("locator", "")),
                json.dumps(source.get("all_locators", []), ensure_ascii=False),
                source.get("source_occurrence_count", source.get("occurrence_count", 1)),
                source.get("classification", {}).get("domain"),
                source.get("classification", {}).get("entity"),
                source.get("classification", {}).get("data_class"),
                source.get("output"),
                source.get("rows", 0),
                source.get("status"),
            )
        )
    return rows


def governance_rows(source_registry: dict | None) -> list[tuple]:
    if not source_registry:
        return []
    return [
        (
            item["content_sha256"],
            item.get("source_table", ""),
            item["source_id"],
            item["registry_status"],
            item["license_status"],
            bool(item["public_display_allowed"]),
            bool(item["scoring_allowed"]),
            bool(item["internal_quality_review_allowed"]),
            bool(item["attribution_required"]),
            item["retention_policy"],
            item.get("freshness_sla_days"),
            item.get("reviewed_at"),
        )
        for item in source_registry.get("mappings", [])
    ]


def create_views(connection: duckdb.DuckDBPyConnection, silver_glob: str) -> None:
    source = quote_sql(silver_glob)
    connection.execute(
        f"""
        CREATE VIEW silver_observations AS
        SELECT * FROM read_parquet({source}, union_by_name=true, hive_partitioning=false);

        CREATE VIEW eligible_observations AS
        SELECT *
        FROM silver_observations
        WHERE validation_status = 'valid'
          AND record_class = 'observed'
          AND product_policy = 'eligible_after_quality_and_license_validation';

        CREATE VIEW projection_observations AS
        SELECT * FROM silver_observations WHERE record_class = 'projection';

        CREATE VIEW market_projection_audit AS
        SELECT
            entity,
            record_class_reason,
            projection_origin,
            min(period) AS earliest_period,
            max(period) AS latest_period,
            count(*) AS observation_count,
            count(DISTINCT natural_key_sha256) AS natural_key_count
        FROM silver_observations
        WHERE record_class = 'projection'
          AND entity IN ('price_trend', 'price_summary', 'annual_sales')
        GROUP BY entity, record_class_reason, projection_origin;

        CREATE VIEW future_dated_observed_anomalies AS
        SELECT *
        FROM silver_observations
        WHERE record_class = 'observed'
          AND entity IN ('price_trend', 'price_summary', 'annual_sales')
          AND classification_reference_time IS NOT NULL
          AND (
              (regexp_matches(period, '^\\d{{4}}$')
               AND substr(period, 1, 4) > substr(classification_reference_time, 1, 4))
              OR
              (regexp_matches(period, '^\\d{{4}}-\\d{{1,2}}$')
               AND strptime(period, '%Y-%m') > strptime(substr(classification_reference_time, 1, 7), '%Y-%m'))
          );

        CREATE VIEW quarantine_observations AS
        SELECT * FROM silver_observations WHERE record_class = 'quarantine';

        CREATE VIEW validation_review AS
        SELECT * FROM silver_observations WHERE validation_status <> 'valid';

        CREATE VIEW exact_observation_duplicate_groups AS
        SELECT
            domain,
            entity,
            entity_variant,
            natural_key_sha256,
            normalized_payload_sha256,
            count(*) AS observation_count
        FROM silver_observations
        WHERE natural_key_kind = 'natural'
        GROUP BY ALL
        HAVING count(*) > 1;

        CREATE VIEW data_conflict_groups AS
        SELECT
            domain,
            entity,
            entity_variant,
            natural_key_sha256,
            count(*) AS observation_count,
            count(DISTINCT normalized_payload_sha256) AS distinct_payload_count,
            min(period) AS earliest_period,
            max(period) AS latest_period
        FROM silver_observations
        WHERE natural_key_kind = 'natural'
          AND validation_status = 'valid'
        GROUP BY domain, entity, entity_variant, natural_key_sha256
        HAVING count(DISTINCT normalized_payload_sha256) > 1;

        CREATE VIEW resolution_evidence AS
        SELECT
            silver_observations.*,
            CASE
                WHEN source_governance.registry_status = 'approved'
                 AND source_governance.license_status IN ('verified', 'user_owned_verified') THEN 500
                WHEN source_governance.registry_status = 'owner_rights_declared'
                 AND source_governance.license_status IN (
                    'user_publication_rights_declared', 'user_owned_verified'
                 ) THEN 400
                WHEN source_governance.internal_quality_review_allowed THEN 200
                ELSE 0
            END AS source_governance_rank,
            CASE silver_observations.record_class
                WHEN 'observed' THEN 30
                WHEN 'projection' THEN 20
                WHEN 'quarantine' THEN 0
                ELSE 10
            END AS record_class_rank,
            coalesce(
                json_array_length(json_keys(try_cast(normalized_record_json AS JSON))),
                0
            ) AS payload_completeness
        FROM silver_observations
        LEFT JOIN source_governance
          ON lower(hex(silver_observations.source_content_sha256)) = source_governance.content_sha256
         AND silver_observations.source_table = source_governance.source_table
        WHERE silver_observations.natural_key_kind = 'natural'
          AND silver_observations.validation_status = 'valid';

        CREATE VIEW resolution_ranked_candidates AS
        SELECT
            *,
            row_number() OVER (
                PARTITION BY natural_key_sha256
                ORDER BY
                    record_class_rank DESC,
                    source_governance_rank DESC,
                    quality_score DESC,
                    payload_completeness DESC,
                    collection_time DESC NULLS LAST,
                    period DESC NULLS LAST,
                    source_content_sha256 ASC,
                    observation_id ASC
            ) AS resolution_rank
        FROM resolution_evidence;

        CREATE VIEW conflict_resolution_decisions AS
        SELECT
            conflicts.*,
            winner.observation_id AS selected_observation_id,
            winner.source_content_sha256 AS selected_source_content_sha256,
            winner.source_table AS selected_source_table,
            winner.quality_score AS selected_quality_score,
            winner.normalized_record_json AS selected_record_json,
            greatest(conflicts.observation_count - 1, 0) AS alternative_observation_count,
            CASE
                WHEN winner.record_class_rank > coalesce(runner.record_class_rank, -1)
                    THEN 'record_class_priority'
                WHEN winner.source_governance_rank > coalesce(runner.source_governance_rank, -1)
                    THEN 'source_governance_priority'
                WHEN winner.quality_score > coalesce(runner.quality_score, -1)
                    THEN 'quality_score'
                WHEN winner.payload_completeness > coalesce(runner.payload_completeness, -1)
                    THEN 'payload_completeness'
                WHEN winner.collection_time IS NOT NULL
                 AND winner.collection_time IS DISTINCT FROM runner.collection_time
                    THEN 'latest_collection_time'
                WHEN winner.period IS NOT NULL
                 AND winner.period IS DISTINCT FROM runner.period
                    THEN 'latest_period'
                ELSE 'stable_content_hash_tiebreak'
            END AS selection_reason,
            CASE
                WHEN winner.record_class_rank > coalesce(runner.record_class_rank, -1)
                    THEN 'high'
                WHEN winner.source_governance_rank > coalesce(runner.source_governance_rank, -1)
                    THEN 'high'
                WHEN winner.quality_score >= coalesce(runner.quality_score, -1) + 10
                    THEN 'high'
                WHEN winner.quality_score > coalesce(runner.quality_score, -1)
                  OR winner.payload_completeness > coalesce(runner.payload_completeness, -1)
                    THEN 'medium'
                ELSE 'low'
            END AS resolution_confidence,
            'resolved_for_internal_canonical' AS decision_status,
            {quote_sql(RESOLUTION_RULE_VERSION)} AS resolution_rule_version
        FROM data_conflict_groups AS conflicts
        JOIN resolution_ranked_candidates AS winner
          ON conflicts.natural_key_sha256 = winner.natural_key_sha256
         AND winner.resolution_rank = 1
        LEFT JOIN resolution_ranked_candidates AS runner
          ON conflicts.natural_key_sha256 = runner.natural_key_sha256
         AND runner.resolution_rank = 2;

        CREATE VIEW canonical_resolution AS
        SELECT
            * EXCLUDE (
                source_governance_rank,
                record_class_rank,
                payload_completeness,
                resolution_rank
            ),
            {quote_sql(RESOLUTION_RULE_VERSION)} AS resolution_rule_version,
            'provisional_unverified_source' AS canonical_status,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM data_conflict_groups AS conflicts
                    WHERE conflicts.natural_key_sha256 = ranked.natural_key_sha256
                ) THEN 'resolved_deterministically'
                ELSE 'single_distinct_payload'
            END AS conflict_status
        FROM resolution_ranked_candidates AS ranked
        WHERE resolution_rank = 1
          AND record_class = 'observed'
          AND product_policy = 'eligible_after_quality_and_license_validation';

        CREATE VIEW canonical_with_provenance AS
        SELECT
            canonical_resolution.*,
            source_provenance.source_locator,
            source_provenance.all_locators_json,
            source_provenance.source_occurrence_count AS manifest_occurrence_count
        FROM canonical_resolution
        LEFT JOIN source_provenance
         ON lower(hex(canonical_resolution.source_content_sha256)) = source_provenance.content_sha256
         AND canonical_resolution.source_table = source_provenance.source_table;

        CREATE VIEW canonical_with_governance AS
        SELECT
            canonical_with_provenance.*,
            source_governance.source_id,
            source_governance.registry_status,
            source_governance.license_status,
            coalesce(source_governance.public_display_allowed, false) AS public_display_allowed,
            coalesce(source_governance.scoring_allowed, false) AS scoring_allowed,
            coalesce(source_governance.internal_quality_review_allowed, false)
                AS internal_quality_review_allowed,
            coalesce(source_governance.attribution_required, true) AS attribution_required,
            source_governance.retention_policy,
            source_governance.freshness_sla_days,
            source_governance.reviewed_at AS source_reviewed_at
        FROM canonical_with_provenance
        LEFT JOIN source_governance
          ON lower(hex(canonical_with_provenance.source_content_sha256)) = source_governance.content_sha256
         AND canonical_with_provenance.source_table = source_governance.source_table;

        CREATE VIEW publishable_canonical AS
        SELECT *
        FROM canonical_with_governance
        WHERE public_display_allowed
          AND registry_status IN ('approved', 'owner_rights_declared')
          AND license_status IN (
              'verified', 'user_owned_verified', 'user_publication_rights_declared'
          );

        CREATE VIEW scoring_eligible_canonical AS
        SELECT *
        FROM canonical_with_governance
        WHERE scoring_allowed
          AND registry_status IN ('approved', 'owner_verified', 'owner_rights_declared')
          AND license_status IN (
              'verified', 'user_owned_verified', 'user_publication_rights_declared'
          );

        CREATE VIEW source_governance_gaps AS
        SELECT
            source_provenance.*,
            source_governance.source_id,
            source_governance.registry_status,
            source_governance.license_status
        FROM source_provenance
        LEFT JOIN source_governance
          ON source_provenance.content_sha256 = source_governance.content_sha256
         AND source_provenance.source_table = source_governance.source_table
        WHERE source_governance.content_sha256 IS NULL
           OR source_governance.registry_status NOT IN ('approved', 'owner_rights_declared')
           OR source_governance.license_status NOT IN (
               'verified', 'user_owned_verified', 'user_publication_rights_declared'
           );
        """
    )


def build_gold(
    silver_manifest: dict,
    output_database: Path,
    source_registry: dict | None = None,
) -> dict:
    output_database = output_database.expanduser().resolve()
    output_database.parent.mkdir(parents=True, exist_ok=True)
    silver_root = Path(silver_manifest["output_root"]).resolve()
    silver_glob = str(silver_root / "**" / "*.parquet")
    if not any(silver_root.rglob("*.parquet")):
        raise RuntimeError(f"Silver Parquet bulunamadı: {silver_root}")

    temporary = output_database.with_name(f".{output_database.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    connection = duckdb.connect(str(temporary))
    try:
        connection.execute("SET preserve_insertion_order=false")
        connection.execute(
            """
            CREATE TABLE source_provenance (
                content_sha256 VARCHAR NOT NULL,
                source_table VARCHAR NOT NULL,
                source_locator VARCHAR,
                all_locators_json VARCHAR,
                source_occurrence_count INTEGER,
                domain VARCHAR,
                entity VARCHAR,
                data_class VARCHAR,
                silver_output VARCHAR,
                logical_rows BIGINT,
                status VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO source_provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            provenance_rows(silver_manifest),
        )
        connection.execute(
            """
            CREATE TABLE source_governance (
                content_sha256 VARCHAR NOT NULL,
                source_table VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                registry_status VARCHAR NOT NULL,
                license_status VARCHAR NOT NULL,
                public_display_allowed BOOLEAN NOT NULL,
                scoring_allowed BOOLEAN NOT NULL,
                internal_quality_review_allowed BOOLEAN NOT NULL,
                attribution_required BOOLEAN NOT NULL,
                retention_policy VARCHAR NOT NULL,
                freshness_sla_days INTEGER,
                reviewed_at VARCHAR
            )
            """
        )
        rows = governance_rows(source_registry)
        if rows:
            connection.executemany(
                "INSERT INTO source_governance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        create_views(connection, silver_glob)
        counts = {
            "silver_observations": connection.execute("SELECT count(*) FROM silver_observations").fetchone()[0],
            "eligible_observations": connection.execute("SELECT count(*) FROM eligible_observations").fetchone()[0],
            "projection_observations": connection.execute("SELECT count(*) FROM projection_observations").fetchone()[0],
            "market_projection_observations": connection.execute(
                "SELECT coalesce(sum(observation_count), 0) FROM market_projection_audit"
            ).fetchone()[0],
            "future_dated_observed_anomalies": connection.execute(
                "SELECT count(*) FROM future_dated_observed_anomalies"
            ).fetchone()[0],
            "registered_source_units": connection.execute(
                "SELECT count(*) FROM source_governance"
            ).fetchone()[0],
            "source_governance_gaps": connection.execute(
                "SELECT count(*) FROM source_governance_gaps"
            ).fetchone()[0],
            "publishable_canonical": connection.execute(
                "SELECT count(*) FROM publishable_canonical"
            ).fetchone()[0],
            "scoring_eligible_canonical": connection.execute(
                "SELECT count(*) FROM scoring_eligible_canonical"
            ).fetchone()[0],
            "quarantine_observations": connection.execute("SELECT count(*) FROM quarantine_observations").fetchone()[0],
            "validation_review": connection.execute("SELECT count(*) FROM validation_review").fetchone()[0],
            "canonical_rows": connection.execute("SELECT count(*) FROM canonical_resolution").fetchone()[0],
            "data_conflict_groups": connection.execute("SELECT count(*) FROM data_conflict_groups").fetchone()[0],
            "resolved_conflict_groups": connection.execute(
                "SELECT count(*) FROM conflict_resolution_decisions "
                "WHERE decision_status = 'resolved_for_internal_canonical'"
            ).fetchone()[0],
            "exact_duplicate_groups": connection.execute(
                "SELECT count(*) FROM exact_observation_duplicate_groups"
            ).fetchone()[0],
        }
        connection.execute("CHECKPOINT")
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    connection.close()
    os.replace(temporary, output_database)

    report = {
        "gold_version": GOLD_VERSION,
        "resolution_rule_version": RESOLUTION_RULE_VERSION,
        "generated_at": utc_now(),
        "silver_manifest": str((silver_root / "silver-manifest.json").resolve()),
        "source_registry_loaded": bool(source_registry),
        "source_registry_version": source_registry.get("registry_version") if source_registry else None,
        "dataset_license_id": (
            source_registry.get("rights_declaration", {}).get("dataset_license_id")
            if source_registry
            else None
        ),
        "rights_holder": (
            source_registry.get("rights_declaration", {}).get("rights_holder")
            if source_registry
            else None
        ),
        "database": str(output_database),
        "silver_glob": silver_glob,
        "counts": counts,
        "views": [
            "silver_observations",
            "eligible_observations",
            "projection_observations",
            "market_projection_audit",
            "future_dated_observed_anomalies",
            "quarantine_observations",
            "validation_review",
            "exact_observation_duplicate_groups",
            "data_conflict_groups",
            "resolution_evidence",
            "resolution_ranked_candidates",
            "conflict_resolution_decisions",
            "canonical_resolution",
            "canonical_with_provenance",
            "canonical_with_governance",
            "publishable_canonical",
            "scoring_eligible_canonical",
            "source_governance_gaps",
        ],
        "canonical_warning": (
            "Kanonik seçim geçicidir; kaynak lisansı ve dış doğrulama tamamlanmadan "
            "resmi değer veya kesin fiyat olarak sunulamaz."
        ),
    }
    atomic_json(output_database.parent / "gold-manifest.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Silver üzerinde Gold DuckDB görünümleri kurar")
    parser.add_argument("--calistir", action="store_true", help="Gold üretimini açıkça başlat")
    parser.add_argument("--silver-manifest", required=True, type=Path)
    parser.add_argument("--output-database", required=True, type=Path)
    parser.add_argument("--source-registry", type=Path)
    args = parser.parse_args(argv)
    if not args.calistir:
        parser.error("Üretim için açıkça --calistir verin")
    manifest = json.loads(args.silver_manifest.read_text(encoding="utf-8"))
    source_registry = (
        json.loads(args.source_registry.read_text(encoding="utf-8"))
        if args.source_registry
        else None
    )
    report = build_gold(manifest, args.output_database, source_registry)
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
