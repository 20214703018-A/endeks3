#!/usr/bin/env python3
"""Yerel veri havuzunu kayıpsız biçimde kataloglar ve sınıflandırır.

Bu araç kaynak dosyaları değiştirmez, arşivleri kalıcı olarak açmaz ve satırları
birleştirmez. Her fiziksel dosyayı ve ZIP içindeki her veri üyesini ayrı bir
``occurrence`` olarak saklar; içerik SHA-256 değerleri aynı olan oluşumları
gruplayarak tekrarların kaynağını görünür kılar.

Kullanım örneği::

    python3 tools/veri_katalogu.py --calistir \
      collector/data /path/to/veri-paketleri \
      --output reports/veri-katalogu.json \
      --csv-output reports/veri-katalogu.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


DATA_SUFFIXES = {".csv", ".db", ".sqlite", ".json", ".geojson", ".zip"}
CSV_SUFFIXES = {".csv"}
SQLITE_SUFFIXES = {".db", ".sqlite"}
JSON_SUFFIXES = {".json", ".geojson"}
CLASSIFICATION_VERSION = "rules_v2"


def utc_iso(timestamp: float | None = None) -> str:
    value = datetime.now(timezone.utc) if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)
    return value.isoformat()


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hash_stream(stream)


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def detect_delimiter(header_line: str) -> str:
    candidates = [";", ",", "\t", "|"]
    return max(candidates, key=header_line.count)


def csv_profile(stream: BinaryIO) -> dict:
    wrapper = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="")
    try:
        first = wrapper.readline()
        delimiter = detect_delimiter(first)
        wrapper.seek(0)
        reader = csv.reader(wrapper, delimiter=delimiter)
        columns = next(reader, [])
        row_count = 0
        malformed_rows = 0
        replacement_character_rows = 0
        for row in reader:
            row_count += 1
            malformed_rows += int(len(row) != len(columns))
            replacement_character_rows += int(any("\ufffd" in value for value in row))
        schema_value = json.dumps(
            {"delimiter": delimiter, "columns": columns}, ensure_ascii=False, sort_keys=True
        )
        return {
            "delimiter": delimiter,
            "columns": columns,
            "column_count": len(columns),
            "row_count": row_count,
            "malformed_rows": malformed_rows,
            "replacement_character_rows": replacement_character_rows,
            "schema_fingerprint": hashlib.sha256(schema_value.encode("utf-8")).hexdigest(),
        }
    finally:
        wrapper.detach()


def json_profile(stream: BinaryIO) -> dict:
    wrapper = io.TextIOWrapper(stream, encoding="utf-8", errors="strict")
    try:
        value = json.load(wrapper)
    except (UnicodeError, json.JSONDecodeError) as exc:
        return {"json_type": "invalid", "parse_error": str(exc)}
    finally:
        wrapper.detach()
    if isinstance(value, list):
        return {"json_type": "array", "item_count": len(value)}
    if isinstance(value, dict):
        result = {
            "json_type": "object",
            "key_count": len(value),
            "key_sample": list(value)[:20],
        }
        if value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
            result["feature_count"] = len(value["features"])
        if isinstance(value.get("polygons"), list):
            result["polygon_count"] = len(value["polygons"])
        return result
    return {"json_type": type(value).__name__}


def classify(locator: str, columns: Iterable[str] = (), *, table_name: str = "") -> dict:
    normalized_locator = normalize_text(locator)
    normalized_columns = {normalize_text(column) for column in columns}
    normalized_table = normalize_text(table_name)
    haystack = "_".join([normalized_locator, normalized_table, *sorted(normalized_columns)])

    rules = [
        (("turkiye_il_ilce_rehberi",), ("reference_geography", "administrative_guide")),
        (("durum_json",), ("operational_metadata", "collector_checkpoint")),
        (("parsel", "imar"), ("cadastre_planning", "parcel_zoning")),
        (("poligon", "geojson"), ("geospatial", "administrative_boundary")),
        (("mahalleler_listesi", "district_name"), ("reference_geography", "neighbourhood")),
        (("ilceler_listesi", "county_name"), ("reference_geography", "district")),
        (("iller_listesi", "city_name"), ("reference_geography", "province")),
        (("cografi_varlik", "geoname"), ("geospatial", "gazetteer")),
        (("fiyat_trend", "emlakjet_trend", "aylik_fiyat_trendi"), ("property_market", "price_trend")),
        (("fiyat_dagilim", "emlakjet_dagilim", "kirilim"), ("property_market", "price_distribution")),
        (("fiyat_ozet", "emlakjet_bolge"), ("property_market", "price_summary")),
        (("satilik_arsa_ilan",), ("listing_observation", "land_listing")),
        (("satilik_konut_ilan",), ("listing_observation", "residential_listing")),
        (("satilik_isyeri_ilan",), ("listing_observation", "commercial_listing")),
        (("ilanlar", "ilan_id"), ("listing_observation", "listing")),
        (("yillik_satis",), ("property_market", "annual_sales")),
        (("demografi", "nufus", "yas_piramidi", "medeni_durum"), ("demographics", "population_profile")),
        (("hemsehri", "kutuk"), ("restricted_context", "registry_origin_distribution")),
        (("secim", "oylar"), ("restricted_context", "election_results")),
        (("sigara", "tutun"), ("restricted_context", "health_behaviour_statistics")),
        (("poi", "onemli_nokta"), ("points_of_interest", "poi")),
        (("emlak_ofis",), ("industry_directory", "real_estate_office")),
        (("danisman",), ("industry_directory", "real_estate_advisor")),
        (("insaat", "proje_sirket", "sirketler"), ("industry_directory", "construction_company")),
        (("kargo", "teslimat_nokta"), ("logistics", "delivery_point")),
        (("eticaret", "e_ticaret", "harcama"), ("commercial_intelligence", "spending_ecommerce")),
        (("ciro", "ticari_cekicilik"), ("commercial_intelligence", "commercial_potential")),
        (("sege", "gelismislik"), ("commercial_intelligence", "socioeconomic_development")),
        (("arabam", "arac"), ("auxiliary_non_property", "vehicle_market")),
    ]

    domain, entity = "unclassified", "unknown"
    for keywords, target in rules:
        if any(keyword in haystack for keyword in keywords):
            domain, entity = target
            break

    member_name = locator.rsplit("::", 1)[-1]
    suffix = PurePosixPath(member_name).suffix.lower()
    if domain == "unclassified" and suffix == ".zip":
        domain, entity = "data_container", "archive_bundle"
    elif domain == "unclassified" and suffix in SQLITE_SUFFIXES:
        domain, entity = "data_container", "database_bundle"

    if "demo" in haystack:
        data_class = "demo"
    elif normalized_table in {"parsel_imar_kayitlari", "bagimsiz_bolumler"} and (
        "parsel_imar_degisiklikleri" in normalized_locator
    ):
        data_class = "quarantine"
    elif any(token in haystack for token in ("projeksiyon", "tahmin_ufku", "projection")):
        data_class = "mixed_observed_projection"
    else:
        data_class = "observed"

    if domain == "restricted_context":
        product_policy = "exclude_from_property_scoring_and_targeting"
    elif domain == "demographics":
        product_policy = "aggregate_context_only_no_housing_suitability_scoring"
    elif domain == "auxiliary_non_property":
        product_policy = "exclude_from_v1_property_core_review_later"
    elif data_class == "quarantine":
        product_policy = "exclude_from_canonical_outputs"
    else:
        product_policy = "eligible_after_quality_and_license_validation"

    personal_markers = {"telefon", "adres", "danisman_adi", "gorsel_url"}
    privacy = (
        "personal_data_review"
        if normalized_columns.intersection(personal_markers)
        else "no_direct_personal_field_detected"
    )
    return {
        "domain": domain,
        "entity": entity,
        "data_class": data_class,
        "product_policy": product_policy,
        "privacy": privacy,
        "classification_method": CLASSIFICATION_VERSION,
    }


def physical_files(roots: list[Path]) -> list[tuple[Path, Path]]:
    found: dict[str, tuple[Path, Path]] = {}
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        candidates = [root] if root.is_file() else root.rglob("*") if root.is_dir() else []
        for path in candidates:
            if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in DATA_SUFFIXES:
                continue
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(root) if root.is_dir() else Path(resolved.name)
            except ValueError:
                relative = Path(resolved.name)
            found[str(resolved)] = (root, relative)
    return [found[key] for key in sorted(found)]


def profile_physical(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        with path.open("rb") as stream:
            return csv_profile(stream)
    if suffix in JSON_SUFFIXES:
        with path.open("rb") as stream:
            return json_profile(stream)
    return {}


def sqlite_tables(path: Path, file_hash: str, locator: str) -> tuple[list[dict], list[str]]:
    tables: list[dict] = []
    errors: list[str] = []
    if path.stat().st_size == 0:
        return tables, [f"{locator}: empty_sqlite_file"]
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                errors.append(f"sqlite_integrity:{integrity}")
            names = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (name,) in names:
                quoted = name.replace('"', '""')
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")')]
                row_count = connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
                schema_value = json.dumps(columns, ensure_ascii=False)
                tables.append(
                    {
                        "asset_id": stable_id("table", f"{locator}::{name}"),
                        "kind": "sqlite_table",
                        "locator": f"{locator}::{name}",
                        "container_sha256": file_hash,
                        "table_name": name,
                        "columns": columns,
                        "column_count": len(columns),
                        "row_count": row_count,
                        "schema_fingerprint": hashlib.sha256(schema_value.encode("utf-8")).hexdigest(),
                        "classification": classify(locator, columns, table_name=name),
                    }
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        errors.append(f"sqlite_error:{exc}")
    return tables, errors


def member_bytes(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    with archive.open(info) as stream:
        return stream.read()


def walk_zip(
    payload: bytes | None,
    *,
    path: Path | None,
    locator: str,
    parent_sha256: str,
    depth: int,
    max_depth: int,
) -> tuple[list[dict], list[str]]:
    occurrences: list[dict] = []
    errors: list[str] = []
    try:
        archive_source = io.BytesIO(payload) if payload is not None else path
        with zipfile.ZipFile(archive_source) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                suffix = PurePosixPath(info.filename).suffix.lower()
                if suffix not in DATA_SUFFIXES:
                    continue
                member_locator = f"{locator}::{info.filename}"
                try:
                    data = member_bytes(archive, info)
                    content_hash = hashlib.sha256(data).hexdigest()
                    profile: dict = {}
                    if suffix in CSV_SUFFIXES:
                        profile = csv_profile(io.BytesIO(data))
                    elif suffix in JSON_SUFFIXES:
                        profile = json_profile(io.BytesIO(data))
                    classification = classify(member_locator, profile.get("columns", []))
                    occurrence = {
                        "occurrence_id": stable_id("occ", member_locator),
                        "kind": "archive_member",
                        "locator": member_locator,
                        "relative_path": info.filename,
                        "suffix": suffix,
                        "bytes": info.file_size,
                        "compressed_bytes": info.compress_size,
                        "crc32": f"{info.CRC:08x}",
                        "content_sha256": content_hash,
                        "container_sha256": parent_sha256,
                        "archive_depth": depth,
                        "classification": classification,
                        **profile,
                    }
                    if info.file_size == 0 or profile.get("json_type") == "invalid":
                        occurrence["validation_status"] = "invalid"
                        occurrence["classification"]["data_class"] = "invalid"
                        occurrence["classification"]["product_policy"] = "exclude_from_canonical_outputs"
                    else:
                        occurrence["validation_status"] = "profiled"
                    occurrences.append(occurrence)
                    if suffix == ".zip":
                        if depth >= max_depth:
                            occurrence["nested_archive_status"] = "max_depth_reached"
                        else:
                            nested, nested_errors = walk_zip(
                                data,
                                path=None,
                                locator=member_locator,
                                parent_sha256=content_hash,
                                depth=depth + 1,
                                max_depth=max_depth,
                            )
                            occurrence["nested_archive_status"] = "profiled"
                            occurrences.extend(nested)
                            errors.extend(nested_errors)
                except (OSError, UnicodeError, csv.Error, zipfile.BadZipFile) as exc:
                    errors.append(f"{member_locator}: {exc}")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"{locator}: {exc}")
    return occurrences, errors


def build_catalog(roots: list[Path], *, max_archive_depth: int = 3) -> dict:
    occurrences: list[dict] = []
    tables: list[dict] = []
    errors: list[str] = []

    for root, relative_path in physical_files(roots):
        path = root / relative_path if root.is_dir() else root
        path = path.resolve()
        stat = path.stat()
        locator = str(path)
        try:
            content_hash = hash_file(path)
            profile = profile_physical(path)
            occurrence = {
                "occurrence_id": stable_id("occ", locator),
                "kind": "physical_file",
                "locator": locator,
                "root": str(root),
                "relative_path": str(relative_path),
                "suffix": path.suffix.lower(),
                "bytes": stat.st_size,
                "modified": utc_iso(stat.st_mtime),
                "content_sha256": content_hash,
                "archive_depth": 0,
                "classification": classify(locator, profile.get("columns", [])),
                **profile,
            }
            if stat.st_size == 0 or profile.get("json_type") == "invalid":
                occurrence["validation_status"] = "invalid"
                occurrence["classification"]["data_class"] = "invalid"
                occurrence["classification"]["product_policy"] = "exclude_from_canonical_outputs"
            else:
                occurrence["validation_status"] = "profiled"
            occurrences.append(occurrence)
            if path.suffix.lower() in SQLITE_SUFFIXES:
                found_tables, sqlite_errors = sqlite_tables(path, content_hash, locator)
                tables.extend(found_tables)
                errors.extend(sqlite_errors)
            elif path.suffix.lower() == ".zip":
                nested, zip_errors = walk_zip(
                    None,
                    path=path,
                    locator=locator,
                    parent_sha256=content_hash,
                    depth=1,
                    max_depth=max_archive_depth,
                )
                occurrences.extend(nested)
                errors.extend(zip_errors)
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(f"{locator}: {exc}")

    by_hash: dict[str, list[dict]] = defaultdict(list)
    content_sizes: dict[str, int] = {}
    for occurrence in occurrences:
        content_hash = occurrence.get("content_sha256")
        if not content_hash:
            continue
        by_hash[content_hash].append(occurrence)
        content_sizes.setdefault(content_hash, occurrence.get("bytes", 0))

    duplicates = []
    for content_hash, items in sorted(by_hash.items()):
        if len(items) < 2:
            continue
        duplicates.append(
            {
                "content_sha256": content_hash,
                "bytes": content_sizes.get(content_hash, 0),
                "occurrence_count": len(items),
                "locators": [item["locator"] for item in items],
            }
        )

    domain_counts = Counter(item["classification"]["domain"] for item in occurrences)
    domain_counts.update(item["classification"]["domain"] for item in tables)
    class_counts = Counter(item["classification"]["data_class"] for item in occurrences)
    class_counts.update(item["classification"]["data_class"] for item in tables)
    kind_counts = Counter(item["kind"] for item in occurrences)
    kind_counts.update(item["kind"] for item in tables)

    physical = [item for item in occurrences if item["kind"] == "physical_file"]
    csv_assets = [item for item in occurrences if item["suffix"] == ".csv"]
    return {
        "catalog_version": "1.0.0",
        "classification_version": CLASSIFICATION_VERSION,
        "generated_at": utc_iso(),
        "roots": [str(root.expanduser().resolve()) for root in roots],
        "summary": {
            "physical_file_occurrences": len(physical),
            "archive_member_occurrences": len(occurrences) - len(physical),
            "sqlite_table_assets": len(tables),
            "exact_duplicate_groups": len(duplicates),
            "physical_bytes_with_repeats": sum(item.get("bytes", 0) for item in physical),
            "unique_content_bytes": sum(content_sizes.values()),
            "csv_rows_with_occurrences": sum(item.get("row_count", 0) for item in csv_assets),
            "malformed_csv_rows": sum(item.get("malformed_rows", 0) for item in csv_assets),
            "errors": len(errors),
            "kinds": dict(sorted(kind_counts.items())),
            "domains": dict(sorted(domain_counts.items())),
            "data_classes": dict(sorted(class_counts.items())),
        },
        "occurrences": occurrences,
        "sqlite_tables": tables,
        "exact_duplicate_groups": duplicates,
        "errors": errors,
    }


def atomic_write_text(path: Path, payload: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_csv(path: Path, catalog: dict) -> None:
    output = io.StringIO(newline="")
    columns = [
        "kind",
        "locator",
        "suffix",
        "bytes",
        "content_sha256",
        "row_count",
        "column_count",
        "schema_fingerprint",
        "domain",
        "entity",
        "data_class",
        "product_policy",
        "privacy",
    ]
    writer = csv.DictWriter(output, fieldnames=columns, delimiter=";")
    writer.writeheader()
    for item in [*catalog["occurrences"], *catalog["sqlite_tables"]]:
        classification = item["classification"]
        writer.writerow(
            {
                "kind": item["kind"],
                "locator": item["locator"],
                "suffix": item.get("suffix", ""),
                "bytes": item.get("bytes", ""),
                "content_sha256": item.get("content_sha256", item.get("container_sha256", "")),
                "row_count": item.get("row_count", ""),
                "column_count": item.get("column_count", ""),
                "schema_fingerprint": item.get("schema_fingerprint", ""),
                "domain": classification["domain"],
                "entity": classification["entity"],
                "data_class": classification["data_class"],
                "product_policy": classification["product_policy"],
                "privacy": classification["privacy"],
            }
        )
    atomic_write_text(path, "\ufeff" + output.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Veri dosyalarını ve arşiv üyelerini değiştirmeden sınıflandırır"
    )
    parser.add_argument("roots", nargs="+", type=Path, help="Taranacak dosya veya klasörler")
    parser.add_argument("--calistir", action="store_true", help="Salt okunur katalog taramasını başlat")
    parser.add_argument("--output", type=Path, help="JSON katalog hedefi")
    parser.add_argument("--csv-output", type=Path, help="Excel uyumlu özet CSV hedefi")
    parser.add_argument(
        "--max-archive-depth", type=int, default=3, choices=range(1, 6), metavar="1-5"
    )
    args = parser.parse_args(argv)

    if not args.calistir:
        parser.error("Tarama için açıkça --calistir verin; varsayılan durumda hiçbir veri okunmaz")
    missing = [str(path) for path in args.roots if not path.expanduser().exists()]
    if missing:
        parser.error("Bulunamayan kaynak: " + ", ".join(missing))

    catalog = build_catalog(args.roots, max_archive_depth=args.max_archive_depth)
    payload = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        atomic_write_text(args.output, payload)
        print(args.output.expanduser().resolve())
    else:
        print(payload, end="")
    if args.csv_output:
        write_csv(args.csv_output, catalog)
        print(args.csv_output.expanduser().resolve())
    return 0 if not catalog["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
