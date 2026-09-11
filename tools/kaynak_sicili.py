#!/usr/bin/env python3
"""Silver kaynak birimlerini sürümlü veri hakları ve kullanım siciline bağlar.

Araç hiçbir kaynak dosyayı değiştirmez. Her Silver kaynak birimini tam olarak bir
mantıksal kaynak tanımına eşler; eşleşme, hak sahibi beyanı, upstream lisans
durumu ve ürün kullanım kapıları raporda birlikte saklanır.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


REGISTRY_VERSION = "1.1.0"
REQUIRED_POLICY_FIELDS = {
    "registry_status",
    "license_status",
    "public_display_allowed",
    "scoring_allowed",
    "internal_quality_review_allowed",
    "attribution_required",
    "retention_policy",
    "freshness_sla_days",
    "reviewed_at",
}


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


def atomic_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source_id",
        "display_name",
        "provider",
        "source_mode",
        "registry_status",
        "license_status",
        "public_display_allowed",
        "scoring_allowed",
        "source_units",
        "logical_rows",
        "entities",
        "earliest_modified",
        "latest_modified",
    ]
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_definitions(definitions: dict) -> None:
    if definitions.get("registry_definition_version") != REGISTRY_VERSION:
        raise ValueError("Desteklenmeyen kaynak sicili tanım sürümü")
    rights = definitions.get("rights_declaration") or {}
    if not rights.get("rights_holder") or not rights.get("dataset_license_id"):
        raise ValueError("Hak sahibi ve veri lisansı kimliği zorunludur")
    default_policy = definitions.get("default_policy") or {}
    missing = REQUIRED_POLICY_FIELDS - set(default_policy)
    if missing:
        raise ValueError(f"Varsayılan politikada eksik alanlar: {sorted(missing)}")
    source_ids = [item.get("source_id") for item in definitions.get("sources", [])]
    if not source_ids or any(not value for value in source_ids):
        raise ValueError("Her kaynak tanımının source_id alanı olmalıdır")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Tekrarlanan source_id bulundu")
    fallbacks = [item for item in definitions["sources"] if (item.get("match") or {}).get("fallback")]
    if len(fallbacks) != 1:
        raise ValueError("Tam olarak bir fallback kaynak tanımı bulunmalıdır")


def rule_matches(rule: dict, domain: str | None, entity: str | None) -> bool:
    match = rule.get("match")
    if not match or match.get("fallback"):
        return False
    domains = match.get("domains")
    entities = match.get("entities")
    return (not domains or domain in domains) and (not entities or entity in entities)


def select_source(definitions: dict, domain: str | None, entity: str | None) -> tuple[dict, bool]:
    fallback = None
    for rule in definitions["sources"]:
        if (rule.get("match") or {}).get("fallback"):
            fallback = rule
        elif rule_matches(rule, domain, entity):
            return rule, False
    if fallback is None:
        raise ValueError("Fallback kaynak tanımı bulunamadı")
    return fallback, True


def merged_policy(definitions: dict, source: dict) -> dict:
    policy = {**definitions["default_policy"], **(source.get("policy") or {})}
    missing = REQUIRED_POLICY_FIELDS - set(policy)
    if missing:
        raise ValueError(f"{source['source_id']} politikasında eksik alanlar: {sorted(missing)}")
    return policy


def modified_times_by_hash(catalog: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for occurrence in catalog.get("occurrences", []):
        modified = occurrence.get("modified")
        if modified:
            result[occurrence["content_sha256"]].append(modified)
    return result


def mapping_id(content_hash: str, source_table: str) -> str:
    value = f"{content_hash}|{source_table}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def build_registry(silver: dict, catalog: dict, definitions: dict) -> dict:
    validate_definitions(definitions)
    modified_by_hash = modified_times_by_hash(catalog)
    mappings = []
    fallback_count = 0
    source_stats: dict[str, dict] = defaultdict(
        lambda: {
            "source_units": 0,
            "logical_rows": 0,
            "domains": set(),
            "entities": set(),
            "modified": [],
        }
    )

    for unit in silver.get("sources", []):
        classification = unit.get("classification") or {}
        domain, entity = classification.get("domain"), classification.get("entity")
        content_hash = unit.get("content_sha256", unit.get("content_hash", ""))
        locator = unit.get("source_locator", unit.get("locator", ""))
        is_empty_local_collector_artifact = (
            content_hash == hashlib.sha256(b"").hexdigest()
            and int(unit.get("rows", 0) or 0) == 0
            and "/collector/data/" in locator
        )
        if is_empty_local_collector_artifact:
            source = next(
                item
                for item in definitions["sources"]
                if item["source_id"] == "collector_operational_metadata"
            )
            used_fallback = False
            mapping_method = "empty_local_collector_artifact"
        else:
            source, used_fallback = select_source(definitions, domain, entity)
            mapping_method = "fallback" if used_fallback else "domain_entity_rule"
        fallback_count += int(used_fallback)
        policy = merged_policy(definitions, source)
        source_table = unit.get("source_table", "")
        modified = sorted(modified_by_hash.get(content_hash, []))
        rows = int(unit.get("rows", 0) or 0)
        mapping = {
            "mapping_id": mapping_id(content_hash, source_table),
            "source_id": source["source_id"],
            "content_sha256": content_hash,
            "source_table": source_table,
            "source_locator": locator,
            "all_locators": unit.get("all_locators", []),
            "source_occurrence_count": unit.get("source_occurrence_count", 1),
            "logical_rows": rows,
            "status": unit.get("status"),
            "domain": domain,
            "entity": entity,
            "data_class": classification.get("data_class"),
            "earliest_modified": modified[0] if modified else None,
            "latest_modified": modified[-1] if modified else None,
            "registry_status": policy["registry_status"],
            "license_status": policy["license_status"],
            "public_display_allowed": bool(policy["public_display_allowed"]),
            "scoring_allowed": bool(policy["scoring_allowed"]),
            "internal_quality_review_allowed": bool(policy["internal_quality_review_allowed"]),
            "attribution_required": bool(policy["attribution_required"]),
            "retention_policy": policy["retention_policy"],
            "freshness_sla_days": policy["freshness_sla_days"],
            "reviewed_at": policy["reviewed_at"],
            "mapping_method": mapping_method,
        }
        mappings.append(mapping)
        stats = source_stats[source["source_id"]]
        stats["source_units"] += 1
        stats["logical_rows"] += rows
        if domain:
            stats["domains"].add(domain)
        if entity:
            stats["entities"].add(entity)
        stats["modified"].extend(modified)

    sources = []
    for declaration in definitions["sources"]:
        stats = source_stats[declaration["source_id"]]
        policy = merged_policy(definitions, declaration)
        modified = sorted(stats["modified"])
        sources.append(
            {
                "source_id": declaration["source_id"],
                "display_name": declaration["display_name"],
                "provider": declaration["provider"],
                "source_mode": declaration["source_mode"],
                "acquisition_owner": declaration["acquisition_owner"],
                "active_without_assets": bool(declaration.get("active_without_assets")),
                "notes": declaration.get("notes"),
                **policy,
                "source_units": stats["source_units"],
                "logical_rows": stats["logical_rows"],
                "domains": sorted(stats["domains"]),
                "entities": sorted(stats["entities"]),
                "earliest_modified": modified[0] if modified else None,
                "latest_modified": modified[-1] if modified else None,
            }
        )

    unit_total = len(silver.get("sources", []))
    mapped_ids = {item["mapping_id"] for item in mappings}
    registered_rows = sum(item["logical_rows"] for item in mappings)
    summary = {
        "source_units": unit_total,
        "mapped_source_units": len(mappings),
        "unique_mappings": len(mapped_ids),
        "mapping_balance_ok": unit_total == len(mappings) == len(mapped_ids),
        "logical_rows": registered_rows,
        "silver_logical_rows": silver.get("summary", {}).get("source_logical_rows"),
        "row_balance_ok": registered_rows == silver.get("summary", {}).get("source_logical_rows"),
        "fallback_source_units": fallback_count,
        "public_display_source_units": sum(item["public_display_allowed"] for item in mappings),
        "scoring_allowed_source_units": sum(item["scoring_allowed"] for item in mappings),
        "registry_statuses": dict(sorted(Counter(item["registry_status"] for item in mappings).items())),
        "license_statuses": dict(sorted(Counter(item["license_status"] for item in mappings).items())),
    }
    return {
        "registry_version": REGISTRY_VERSION,
        "definition_version": definitions["registry_definition_version"],
        "rights_declaration": definitions["rights_declaration"],
        "catalog_generated_at": catalog.get("generated_at"),
        "silver_generated_at": silver.get("generated_at"),
        "summary": summary,
        "sources": sources,
        "mappings": mappings,
    }


def source_summary_rows(registry: dict) -> list[dict]:
    rows = []
    for source in registry["sources"]:
        rows.append(
            {
                "source_id": source["source_id"],
                "display_name": source["display_name"],
                "provider": source["provider"],
                "source_mode": source["source_mode"],
                "registry_status": source["registry_status"],
                "license_status": source["license_status"],
                "public_display_allowed": int(source["public_display_allowed"]),
                "scoring_allowed": int(source["scoring_allowed"]),
                "source_units": source["source_units"],
                "logical_rows": source["logical_rows"],
                "entities": ",".join(source["entities"]),
                "earliest_modified": source["earliest_modified"],
                "latest_modified": source["latest_modified"],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Silver kaynak sicili ve kullanım kapıları üretir")
    parser.add_argument("--calistir", action="store_true", help="Üretimi açıkça başlat")
    parser.add_argument("--silver-manifest", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--definitions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--csv-output", type=Path)
    args = parser.parse_args(argv)
    if not args.calistir:
        parser.error("Üretim için açıkça --calistir verin")
    silver = json.loads(args.silver_manifest.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    definitions = json.loads(args.definitions.read_text(encoding="utf-8"))
    registry = build_registry(silver, catalog, definitions)
    atomic_json(args.output, registry)
    if args.csv_output:
        atomic_csv(args.csv_output, source_summary_rows(registry))
    print(json.dumps(registry["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if registry["summary"]["mapping_balance_ok"] and registry["summary"]["row_balance_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
