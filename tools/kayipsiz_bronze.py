#!/usr/bin/env python3
"""Kataloglanmış kaynakları içerik-adresli Bronze Parquet katmanına yazar.

Her benzersiz içerik SHA-256 değeri yalnız bir kez işlenir. Aynı içeriğin tüm
fiziksel ve arşiv içi oluşumları ``veri-katalogu`` manifestinde korunur. Bu
araç kaynakları değiştirmez; çıktı dosyalarını önce geçici ada yazıp doğrulama
sonrası atomik olarak yayımlar.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from tools.veri_katalogu import CLASSIFICATION_VERSION, classify, hash_file
except ModuleNotFoundError:  # ``python tools/kayipsiz_bronze.py`` doğrudan çağrısı
    from veri_katalogu import CLASSIFICATION_VERSION, classify, hash_file


SUPPORTED_SUFFIXES = {".csv", ".json", ".geojson", ".db", ".sqlite"}
PARQUET_METADATA = {
    b"bronze_version": b"1.0.0",
    b"classification_version": CLASSIFICATION_VERSION.encode("ascii"),
}
ROW_SCHEMA = pa.schema(
    [
        ("source_content_sha256", pa.string()),
        ("source_row_number", pa.int64()),
        ("row_sha256", pa.string()),
        ("raw_record_json", pa.large_string()),
        ("parse_status", pa.string()),
        ("domain", pa.string()),
        ("entity", pa.string()),
        ("data_class", pa.string()),
        ("schema_fingerprint", pa.string()),
    ],
    metadata=PARQUET_METADATA,
)
JSON_DOCUMENT_SCHEMA = pa.schema(
    [
        ("source_content_sha256", pa.string()),
        ("raw_payload", pa.large_binary()),
        ("payload_bytes", pa.int64()),
        ("json_type", pa.string()),
        ("domain", pa.string()),
        ("entity", pa.string()),
        ("data_class", pa.string()),
    ],
    metadata=PARQUET_METADATA,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def row_digest(raw_record_json: str) -> str:
    return hashlib.sha256(raw_record_json.encode("utf-8", errors="surrogatepass")).hexdigest()


def locator_payload(locator: str) -> bytes:
    parts = locator.split("::")
    payload = Path(parts[0]).read_bytes()
    for member in parts[1:]:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            payload = archive.read(member)
    return payload


@contextlib.contextmanager
def open_locator(locator: str) -> Iterator[BinaryIO]:
    if "::" not in locator:
        with Path(locator).open("rb") as stream:
            yield stream
        return
    stream = io.BytesIO(locator_payload(locator))
    try:
        yield stream
    finally:
        stream.close()


def representative_occurrences(catalog: dict) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for occurrence in catalog["occurrences"]:
        if occurrence.get("suffix") not in SUPPORTED_SUFFIXES:
            continue
        groups[occurrence["content_sha256"]].append(occurrence)

    representatives = []
    for content_hash, occurrences in sorted(groups.items()):
        occurrences.sort(
            key=lambda item: (
                item.get("validation_status") == "invalid",
                item["kind"] != "physical_file",
                item.get("archive_depth", 0),
                item["locator"],
            )
        )
        representative = dict(occurrences[0])
        representative["source_content_sha256"] = content_hash
        representative["occurrence_count"] = len(occurrences)
        representative["all_locators"] = [item["locator"] for item in occurrences]
        representatives.append(representative)
    return representatives


def output_path(root: Path, kind: str, content_hash: str, suffix: str = "") -> Path:
    filename = f"{content_hash}{suffix}.parquet"
    return root / kind / content_hash[:2] / filename


def publish_parquet(temporary: Path, target: Path, expected_rows: int) -> int:
    metadata = pq.read_metadata(temporary)
    if metadata.num_rows != expected_rows:
        raise RuntimeError(
            f"Parquet satır doğrulaması başarısız: beklenen={expected_rows}, yazılan={metadata.num_rows}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, target)
    return metadata.num_rows


def existing_parquet_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        metadata = pq.read_metadata(path)
        file_metadata = metadata.metadata or {}
        if file_metadata.get(b"classification_version") != CLASSIFICATION_VERSION.encode("ascii"):
            return None
        return metadata.num_rows
    except (OSError, pa.ArrowException):
        return None


def append_row_batch(writer: pq.ParquetWriter, columns: dict[str, list]) -> int:
    if not columns["source_row_number"]:
        return 0
    table = pa.Table.from_pydict(columns, schema=ROW_SCHEMA)
    writer.write_table(table)
    count = table.num_rows
    for values in columns.values():
        values.clear()
    return count


def new_row_batch() -> dict[str, list]:
    return {field.name: [] for field in ROW_SCHEMA}


def add_row(
    batch: dict[str, list],
    *,
    content_hash: str,
    row_number: int,
    raw_record_json: str,
    parse_status: str,
    classification: dict,
    schema_fingerprint: str,
) -> None:
    batch["source_content_sha256"].append(content_hash)
    batch["source_row_number"].append(row_number)
    batch["row_sha256"].append(row_digest(raw_record_json))
    batch["raw_record_json"].append(raw_record_json)
    batch["parse_status"].append(parse_status)
    batch["domain"].append(classification["domain"])
    batch["entity"].append(classification["entity"])
    batch["data_class"].append(classification["data_class"])
    batch["schema_fingerprint"].append(schema_fingerprint)


def write_csv_asset(asset: dict, output_root: Path, batch_size: int) -> dict:
    content_hash = asset["source_content_sha256"]
    target = output_path(output_root, "csv", content_hash)
    expected = asset.get("row_count")
    current_hash = (
        hash_file(Path(asset["locator"]))
        if "::" not in asset["locator"]
        else hashlib.sha256(locator_payload(asset["locator"])).hexdigest()
    )
    if current_hash != content_hash:
        raise RuntimeError(f"Kaynak hash değişti: {asset['locator']}")
    existing = existing_parquet_rows(target)
    if existing is not None and expected == existing:
        return {"status": "reused", "output": str(target), "rows": existing}

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    classification = asset["classification"]
    schema_fingerprint = asset.get("schema_fingerprint", "")
    written = 0
    try:
        with open_locator(asset["locator"]) as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8-sig", errors="surrogateescape", newline="")
            try:
                reader = csv.reader(text, delimiter=asset.get("delimiter", ";"))
                header = next(reader, [])
                writer = pq.ParquetWriter(temporary, ROW_SCHEMA, compression="zstd")
                try:
                    batch = new_row_batch()
                    for row_number, row in enumerate(reader, start=1):
                        raw_record = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
                        status = "valid" if len(row) == len(header) else "malformed_column_count"
                        add_row(
                            batch,
                            content_hash=content_hash,
                            row_number=row_number,
                            raw_record_json=raw_record,
                            parse_status=status,
                            classification=classification,
                            schema_fingerprint=schema_fingerprint,
                        )
                        if len(batch["source_row_number"]) >= batch_size:
                            written += append_row_batch(writer, batch)
                    written += append_row_batch(writer, batch)
                finally:
                    writer.close()
            finally:
                text.detach()
        publish_parquet(temporary, target, written)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"status": "written", "output": str(target), "rows": written}


def write_json_asset(asset: dict, output_root: Path) -> dict:
    content_hash = asset["source_content_sha256"]
    target = output_path(output_root, "json", content_hash)
    existing = existing_parquet_rows(target)
    if existing == 1:
        return {"status": "reused", "output": str(target), "rows": 1}
    payload = locator_payload(asset["locator"])
    if hashlib.sha256(payload).hexdigest() != content_hash:
        raise RuntimeError(f"Kaynak hash değişti: {asset['locator']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    classification = asset["classification"]
    table = pa.Table.from_pydict(
        {
            "source_content_sha256": [content_hash],
            "raw_payload": [payload],
            "payload_bytes": [len(payload)],
            "json_type": [asset.get("json_type", "unknown")],
            "domain": [classification["domain"]],
            "entity": [classification["entity"]],
            "data_class": [classification["data_class"]],
        },
        schema=JSON_DOCUMENT_SCHEMA,
    )
    pq.write_table(table, temporary, compression="zstd")
    publish_parquet(temporary, target, 1)
    return {"status": "written", "output": str(target), "rows": 1}


def sqlite_json_value(value):
    if isinstance(value, bytes):
        return {"__type": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    return value


@contextlib.contextmanager
def sqlite_path(locator: str) -> Iterator[Path]:
    if "::" not in locator:
        yield Path(locator)
        return
    payload = locator_payload(locator)
    with tempfile.NamedTemporaryFile("wb", suffix=".sqlite", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def write_sqlite_asset(
    asset: dict,
    output_root: Path,
    batch_size: int,
    domains: set[str] | None = None,
) -> dict:
    content_hash = asset["source_content_sha256"]
    table_results = []
    total_rows = 0
    with sqlite_path(asset["locator"]) as database:
        if hash_file(database) != content_hash:
            raise RuntimeError(f"Kaynak hash değişti: {asset['locator']}")
        uri = f"file:{database.resolve()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite bütünlük hatası: {asset['locator']}: {integrity}")
            names = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (table_name,) in names:
                quoted = table_name.replace('"', '""')
                column_rows = connection.execute(f'PRAGMA table_info("{quoted}")').fetchall()
                columns = [row[1] for row in column_rows]
                declared_types = [row[2] for row in column_rows]
                schema_value = json.dumps(
                    {"columns": columns, "declared_types": declared_types},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                schema_fingerprint = hashlib.sha256(schema_value.encode("utf-8")).hexdigest()
                classification = classify(asset["locator"], columns, table_name=table_name)
                if domains and classification["domain"] not in domains:
                    continue
                safe_table = hashlib.sha256(table_name.encode("utf-8")).hexdigest()[:16]
                target = output_path(output_root, "sqlite", content_hash, f"-{safe_table}")
                expected = connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
                existing = existing_parquet_rows(target)
                if existing == expected:
                    table_results.append(
                        {
                            "table": table_name,
                            "status": "reused",
                            "output": str(target),
                            "rows": existing,
                            "columns": columns,
                            "declared_types": declared_types,
                            "schema_fingerprint": schema_fingerprint,
                            "classification": classification,
                        }
                    )
                    total_rows += existing
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                writer = pq.ParquetWriter(temporary, ROW_SCHEMA, compression="zstd")
                written = 0
                try:
                    batch = new_row_batch()
                    cursor = connection.execute(f'SELECT * FROM "{quoted}"')
                    for row_number, row in enumerate(cursor, start=1):
                        values = [sqlite_json_value(value) for value in row]
                        raw_record = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
                        add_row(
                            batch,
                            content_hash=content_hash,
                            row_number=row_number,
                            raw_record_json=raw_record,
                            parse_status="valid",
                            classification=classification,
                            schema_fingerprint=schema_fingerprint,
                        )
                        if len(batch["source_row_number"]) >= batch_size:
                            written += append_row_batch(writer, batch)
                    written += append_row_batch(writer, batch)
                finally:
                    writer.close()
                publish_parquet(temporary, target, written)
                total_rows += written
                table_results.append(
                    {
                        "table": table_name,
                        "status": "written",
                        "output": str(target),
                        "rows": written,
                        "columns": columns,
                        "declared_types": declared_types,
                        "schema_fingerprint": schema_fingerprint,
                        "classification": classification,
                    }
                )
        finally:
            connection.close()
    return {"status": "written", "rows": total_rows, "tables": table_results}


def check_free_space(path: Path, minimum_free_bytes: int) -> None:
    probe = path if path.exists() else path.parent
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < minimum_free_bytes:
        raise RuntimeError(
            f"Yetersiz boş alan: {free} bayt; güvenli alt sınır {minimum_free_bytes} bayt"
        )


def build_bronze(
    catalog: dict,
    output_root: Path,
    *,
    domains: set[str] | None = None,
    batch_size: int = 25_000,
    minimum_free_bytes: int = 3 * 1024**3,
) -> dict:
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    check_free_space(output_root, minimum_free_bytes)

    results = []
    status_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    source_rows = 0
    written_rows = 0
    rows_by_domain: Counter[str] = Counter()
    rows_by_data_class: Counter[str] = Counter()

    for asset in representative_occurrences(catalog):
        suffix = asset["suffix"]
        classification = asset["classification"]
        if domains and suffix not in {".db", ".sqlite"} and classification["domain"] not in domains:
            results.append(
                {
                    "content_sha256": asset["source_content_sha256"],
                    "locator": asset["locator"],
                    "suffix": suffix,
                    "status": "out_of_selected_domain",
                    "classification": classification,
                }
            )
            status_counts["out_of_selected_domain"] += 1
            continue
        if asset.get("validation_status") == "invalid":
            results.append(
                {
                    "content_sha256": asset["source_content_sha256"],
                    "locator": asset["locator"],
                    "all_locators": asset["all_locators"],
                    "occurrence_count": asset["occurrence_count"],
                    "suffix": suffix,
                    "status": "invalid_preserved_in_catalog",
                    "classification": classification,
                }
            )
            status_counts["invalid_preserved_in_catalog"] += 1
            continue

        check_free_space(output_root, minimum_free_bytes)
        try:
            if suffix == ".csv":
                source_rows += asset.get("row_count", 0)
                detail = write_csv_asset(asset, output_root, batch_size)
            elif suffix in {".json", ".geojson"}:
                source_rows += 1
                detail = write_json_asset(asset, output_root)
            elif suffix in {".db", ".sqlite"}:
                detail = write_sqlite_asset(asset, output_root, batch_size, domains=domains)
                source_rows += detail["rows"]
            else:
                continue
            written_rows += detail["rows"]
            if detail.get("tables") is not None:
                for table in detail["tables"]:
                    table_classification = table["classification"]
                    rows_by_domain[table_classification["domain"]] += table["rows"]
                    rows_by_data_class[table_classification["data_class"]] += table["rows"]
            else:
                rows_by_domain[classification["domain"]] += detail["rows"]
                rows_by_data_class[classification["data_class"]] += detail["rows"]
            type_counts[suffix] += 1
            status_counts[detail["status"]] += 1
            results.append(
                {
                    "content_sha256": asset["source_content_sha256"],
                    "locator": asset["locator"],
                    "all_locators": asset["all_locators"],
                    "occurrence_count": asset["occurrence_count"],
                    "suffix": suffix,
                    "classification": classification,
                    **detail,
                }
            )
        except Exception as exc:
            status_counts["technical_error"] += 1
            results.append(
                {
                    "content_sha256": asset["source_content_sha256"],
                    "locator": asset["locator"],
                    "all_locators": asset["all_locators"],
                    "occurrence_count": asset["occurrence_count"],
                    "suffix": suffix,
                    "classification": classification,
                    "status": "technical_error",
                    "error": str(exc),
                }
            )

    technical_errors = status_counts["technical_error"]
    report = {
        "bronze_version": "1.0.0",
        "classification_version": CLASSIFICATION_VERSION,
        "generated_at": utc_now(),
        "catalog_generated_at": catalog.get("generated_at"),
        "catalog_version": catalog.get("catalog_version"),
        "output_root": str(output_root),
        "selected_domains": sorted(domains) if domains else "all",
        "summary": {
            "unique_assets_considered": len(results),
            "source_logical_rows": source_rows,
            "written_or_reused_rows": written_rows,
            "row_balance_ok": source_rows == written_rows and technical_errors == 0,
            "technical_errors": technical_errors,
            "statuses": dict(sorted(status_counts.items())),
            "asset_types": dict(sorted(type_counts.items())),
            "logical_rows_by_domain": dict(sorted(rows_by_domain.items())),
            "logical_rows_by_data_class": dict(sorted(rows_by_data_class.items())),
        },
        "assets": results,
    }
    atomic_json(output_root / "bronze-manifest.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kataloglanmış benzersiz içerikleri kayıpsız Bronze Parquet katmanına yazar"
    )
    parser.add_argument("--calistir", action="store_true", help="Bronze üretimini açıkça başlat")
    parser.add_argument("--catalog", required=True, type=Path, help="veri_katalogu.py JSON çıktısı")
    parser.add_argument("--output-root", required=True, type=Path, help="Bronze çıktı klasörü")
    parser.add_argument("--domain", action="append", dest="domains", help="Yalnız seçilen domain; tekrarlanabilir")
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--min-free-gib", type=float, default=3.0)
    parser.add_argument(
        "--allow-catalog-errors",
        action="store_true",
        help="Katalogdaki bilinen invalid/teknik oluşumları manifestte tutarak devam et",
    )
    args = parser.parse_args(argv)

    if not args.calistir:
        parser.error("Üretim için açıkça --calistir verin")
    if args.batch_size < 1:
        parser.error("--batch-size pozitif olmalı")
    if not args.catalog.exists():
        parser.error(f"Katalog bulunamadı: {args.catalog}")

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if catalog.get("errors") and not args.allow_catalog_errors:
        parser.error(
            "Katalog teknik/invalid kayıtlar içeriyor; inceleyip --allow-catalog-errors ile açıkça devam edin"
        )
    report = build_bronze(
        catalog,
        args.output_root,
        domains=set(args.domains) if args.domains else None,
        batch_size=args.batch_size,
        minimum_free_bytes=int(args.min_free_gib * 1024**3),
    )
    print((args.output_root.expanduser().resolve() / "bronze-manifest.json"))
    return 0 if report["summary"]["technical_errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
