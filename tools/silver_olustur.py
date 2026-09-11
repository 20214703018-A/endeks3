#!/usr/bin/env python3
"""Bronze Parquet varlıklarını kayıpsız, ortak Silver gözlem sözleşmesine taşır.

Silver hiçbir Bronze satırını silmez. Kaynak satırın özgün biçimi Bronze'da
kalırken burada normalize edilmiş alan adları, deterministik doğal anahtar,
satır sınıfı, dönem, kalite ve doğrulama bilgileri tutulur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from tools.veri_katalogu import CLASSIFICATION_VERSION, normalize_text
except ModuleNotFoundError:  # Doğrudan ``python tools/silver_olustur.py`` çağrısı
    from veri_katalogu import CLASSIFICATION_VERSION, normalize_text


SILVER_VERSION = "1.1.0"
CONTRACT_VERSION = "entity_contracts_v3"
SCHEMA_METADATA = {
    b"silver_version": SILVER_VERSION.encode("ascii"),
    b"classification_version": CLASSIFICATION_VERSION.encode("ascii"),
    b"contract_version": CONTRACT_VERSION.encode("ascii"),
}
SILVER_SCHEMA = pa.schema(
    [
        ("observation_id", pa.binary(32)),
        ("source_content_sha256", pa.binary(32)),
        ("source_table", pa.string()),
        ("source_row_number", pa.int64()),
        ("source_row_sha256", pa.binary(32)),
        ("normalized_payload_sha256", pa.binary(32)),
        ("domain", pa.string()),
        ("entity", pa.string()),
        ("entity_variant", pa.string()),
        ("record_class", pa.string()),
        ("record_class_reason", pa.string()),
        ("projection_origin", pa.string()),
        ("classification_reference_time", pa.string()),
        ("product_policy", pa.string()),
        ("privacy", pa.string()),
        ("natural_key_sha256", pa.binary(32)),
        ("natural_key_json", pa.string()),
        ("natural_key_kind", pa.string()),
        ("period", pa.string()),
        ("collection_time", pa.string()),
        ("normalized_record_json", pa.large_string()),
        ("source_parse_status", pa.string()),
        ("validation_status", pa.string()),
        ("validation_errors_json", pa.string()),
        ("quality_score", pa.int16()),
        ("source_occurrence_count", pa.int32()),
        ("source_schema_fingerprint", pa.string()),
    ],
    metadata=SCHEMA_METADATA,
)

NULL_TEXT = {"", "null", "none", "nan", "n/a", "-"}
NUMERIC_MARKERS = (
    "fiyat",
    "m2",
    "oran",
    "sayi",
    "sayisi",
    "gelir",
    "ciro",
    "skor",
    "endeks",
    "yuzde",
    "hacim",
    "rakim",
    "lat",
    "lon",
    "enlem",
    "boylam",
    "amortisman",
    "getiri",
    "degisim",
    "sure",
    "yas",
    "kaks",
    "taks",
    "emsal",
    "alan",
)
INTEGER_FIELDS = {
    "city_id",
    "county_id",
    "district_id",
    "mahalle_id",
    "ilan_sayisi",
    "toplam_ilan_sayisi",
    "yil",
    "guncellenme_yili",
    "projeksiyon",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_bytes(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).digest()


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


def check_free_space(path: Path, minimum_free_bytes: int) -> None:
    probe = path if path.exists() else path.parent
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < minimum_free_bytes:
        raise RuntimeError(
            f"Yetersiz boş alan: {free} bayt; güvenli alt sınır {minimum_free_bytes} bayt"
        )


def catalog_occurrences_by_hash(catalog: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for item in catalog["occurrences"]:
        result.setdefault(item["content_sha256"], []).append(item)
    return result


def best_catalog_occurrence(items: list[dict]) -> dict:
    return sorted(
        items,
        key=lambda item: (
            item.get("validation_status") == "invalid",
            item.get("kind") != "physical_file",
            item.get("archive_depth", 0),
            item["locator"],
        ),
    )[0]


def source_units(bronze: dict, catalog: dict) -> Iterator[dict]:
    by_hash = catalog_occurrences_by_hash(catalog)
    for asset in bronze["assets"]:
        content_hash = asset["content_sha256"]
        catalog_item = best_catalog_occurrence(by_hash[content_hash])
        common = {
            "content_hash": content_hash,
            "locator": asset["locator"],
            "all_locators": asset.get("all_locators", [asset["locator"]]),
            "occurrence_count": asset.get("occurrence_count", 1),
            "suffix": asset["suffix"],
            # Satırın kendi toplama zamanı yoksa kaynağın değiştirilme zamanını,
            # arşiv üyelerinde de kataloğun sabit üretim zamanını kullanırız.
            # Bu değer Silver çıktısında saklandığı için yeniden üretilebilir.
            "reference_time": catalog_item.get("modified") or catalog["generated_at"],
        }
        if asset["status"] == "invalid_preserved_in_catalog":
            yield {**common, "status": "invalid_preserved_in_bronze", "rows": 0}
            continue
        if asset.get("tables") is not None:
            for table in asset["tables"]:
                yield {
                    **common,
                    "status": "ready",
                    "source_table": table["table"],
                    "output": table["output"],
                    "rows": table["rows"],
                    "columns": table["columns"],
                    "schema_fingerprint": table["schema_fingerprint"],
                    "classification": table["classification"],
                    "source_kind": "row_records",
                }
            continue
        classification = asset["classification"]
        source_kind = "json_document" if asset["suffix"] in {".json", ".geojson"} else "row_records"
        yield {
            **common,
            "status": "ready",
            "source_table": "",
            "output": asset["output"],
            "rows": asset["rows"],
            "columns": catalog_item.get("columns", []),
            "schema_fingerprint": catalog_item.get("schema_fingerprint", ""),
            "classification": classification,
            "source_kind": source_kind,
        }


def parse_number(value: str, integer: bool) -> int | float | str:
    candidate = value.strip().replace(" ", "")
    if not candidate:
        return value
    if candidate.count(",") == 1 and "." not in candidate:
        candidate = candidate.replace(",", ".")
    try:
        number = float(candidate)
    except ValueError:
        return value
    if integer and number.is_integer():
        return int(number)
    return number


def normalize_value(field: str, value):
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.lower() in NULL_TEXT:
        return None
    if field in INTEGER_FIELDS:
        return parse_number(stripped, integer=True)
    if any(marker in field for marker in NUMERIC_MARKERS):
        return parse_number(stripped, integer=False)
    return stripped


def normalize_record(columns: list[str], values: list) -> tuple[dict, list[str]]:
    result: dict[str, object] = {}
    errors: list[str] = []
    for index, value in enumerate(values):
        original = columns[index] if index < len(columns) else f"extra_{index - len(columns) + 1}"
        field = normalize_text(original) or f"field_{index + 1}"
        if field in result:
            suffix = 2
            while f"{field}__{suffix}" in result:
                suffix += 1
            errors.append(f"duplicate_normalized_column:{field}")
            field = f"{field}__{suffix}"
        result[field] = normalize_value(field, value)
    if len(values) != len(columns):
        errors.append(f"column_count_mismatch:expected={len(columns)}:actual={len(values)}")
    return result, errors


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "evet", "yes", "projection", "projeksiyon"}


FUTURE_SENSITIVE_ENTITIES = {"price_trend", "price_summary", "annual_sales"}


def period_position(value: str | None) -> tuple[int, int] | None:
    """Dönemi karşılaştırılabilir yıl/ay ikilisine çevirir.

    Yıllık serilerde ay ``0`` bırakılır; böylece aynı yıl geleceğe dönük kabul
    edilmez. Çeyrekler, çeyreğin son ayı ile temsil edilir.
    """
    if not value:
        return None
    match = re.match(r"^\s*(\d{4})(?:-(\d{1,2})|[- ]?Q([1-4]))?", str(value), re.IGNORECASE)
    if not match:
        return None
    year = int(match.group(1))
    if match.group(2):
        month = int(match.group(2))
        if not 1 <= month <= 12:
            return None
        return year, month
    if match.group(3):
        return year, int(match.group(3)) * 3
    return year, 0


def reference_position(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.match(r"^\s*(\d{4})-(\d{1,2})", str(value))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return (year, month) if 1 <= month <= 12 else None


def period_is_future(period: str | None, reference_time: str | None) -> bool:
    period_value = period_position(period)
    reference_value = reference_position(reference_time)
    if not period_value or not reference_value:
        return False
    if period_value[1] == 0:
        return period_value[0] > reference_value[0]
    return period_value > reference_value


def row_class(
    base_class: str,
    entity: str,
    record: dict,
    *,
    period: str | None,
    reference_time: str | None,
    locator: str,
) -> tuple[str, str, str | None]:
    """Satırı sınıflandırır ve kararın gerekçesi/kaynağını döndürür."""
    if base_class in {"demo", "quarantine", "invalid"}:
        return base_class, "protected_base_class", None

    source_name = normalize_text(locator)
    parsed_period = period_position(period)
    if (
        entity == "annual_sales"
        and parsed_period
        and parsed_period[0] in {2025, 2026, 2027}
        and any(marker in source_name for marker in ("yillik_satis", "piyasa_verileri"))
    ):
        return "projection", "collector_fixed_growth_period", "collector_fixed_growth"

    is_future = entity in FUTURE_SENSITIVE_ENTITIES and period_is_future(period, reference_time)

    if "projeksiyon" in record:
        if truthy(record["projeksiyon"]):
            return "projection", "explicit_projection_flag", "source_declared"
        if is_future:
            origin = (
                "collector_fixed_growth"
                if entity == "annual_sales"
                and any(marker in source_name for marker in ("yillik_satis", "piyasa_verileri"))
                else "provider_future_inferred"
            )
            return "projection", "future_period_overrides_false_flag", origin
        return "observed", "explicit_observed_flag", None
    if "projection" in record:
        if truthy(record["projection"]):
            return "projection", "explicit_projection_flag", "source_declared"
        if is_future:
            return "projection", "future_period_overrides_false_flag", "provider_future_inferred"
        return "observed", "explicit_observed_flag", None
    if record.get("projeksiyon_donemi") not in (None, "", 0, "0"):
        return "projection", "declared_projection_horizon", "source_declared"
    if record.get("tahmin_ufku") not in (None, "", 0, "0"):
        return "projection", "declared_projection_horizon", "source_declared"
    if is_future:
        origin = (
            "collector_fixed_growth"
            if entity == "annual_sales"
            and any(marker in source_name for marker in ("yillik_satis", "piyasa_verileri"))
            else "provider_future_inferred"
        )
        return "projection", "future_period_after_reference", origin
    if base_class == "mixed_observed_projection":
        return "observed_unresolved_projection_flag", "missing_projection_indicator", None
    return base_class, "base_classification", None


def entity_variant(entity: str, record: dict, source_table: str, locator: str) -> str:
    fields = set(record)
    if entity in {
        "province",
        "district",
        "neighbourhood",
        "price_summary",
        "price_trend",
        "price_distribution",
        "annual_sales",
        "poi",
        "gazetteer",
        "real_estate_office",
        "real_estate_advisor",
        "construction_company",
        "delivery_point",
        "spending_ecommerce",
        "socioeconomic_development",
        "registry_origin_distribution",
        "election_results",
    }:
        return entity
    if entity == "population_profile":
        if any(field.startswith("age_") for field in fields):
            return "age_pyramid"
        if fields.intersection({"evli_sayisi", "bekar_hic_evlenmemis", "bosanmis_sayisi"}):
            return "marital_and_housing"
        return "demographics"
    if entity == "health_behaviour_statistics":
        if any("sigara" in field or "tutun" in field for field in fields):
            return "smoking"
        if any(field.startswith("online_") or "e_ticaret" in field for field in fields):
            return "digital_spending"
        return "consumer_behaviour"
    if entity == "parcel_zoning":
        if "degisiklik_turu" in fields:
            return "plan_change"
        if "bolum_no" in fields:
            return "independent_unit"
        if "ilan_id" in fields and not {"ada_no", "parsel_no"}.issubset(fields):
            return "land_listing_zoning"
        return "parcel"
    if entity == "listing":
        return str(record.get("kategori") or "listing")
    if source_table:
        return normalize_text(source_table)
    name = Path(locator.rsplit("::", 1)[-1]).stem
    return normalize_text(name)


LOCATION_GROUPS = [
    ("city_id", "county_id", "district_id"),
    ("city_id", "county_id"),
    ("il", "ilce", "mahalle"),
    ("il_adi", "ilce_adi", "mahalle_adi"),
    ("bolge_adi",),
]


ENTITY_KEYS = {
    "province": [("city_id",), ("city_name",), ("il",), ("il_adi",)],
    "district": [("city_id", "county_id"), ("il", "ilce"), ("il_adi", "ilce_adi")],
    "neighbourhood": [
        ("city_id", "county_id", "district_id"),
        ("il", "ilce", "mahalle"),
        ("il_adi", "ilce_adi", "mahalle_adi"),
    ],
    "price_summary": [],
    "price_trend": [],
    "price_distribution": [],
    "annual_sales": [],
    "listing": [("ilan_id",), ("id",), ("url",)],
    "land_listing": [("ilan_id",), ("url",)],
    "residential_listing": [("ilan_id",), ("url",)],
    "commercial_listing": [("ilan_id",), ("url",)],
    "poi": [("city_id", "county_id", "poi_id"), ("poi_id",), ("poi_adi", "kategori_id", "bolge_adi")],
    "gazetteer": [("geoname_id",), ("id",), ("ad", "enlem", "boylam")],
    "real_estate_office": [("ofis_id",), ("ofis_adi", "city_id")],
    "real_estate_advisor": [("danisman_id",), ("danisman_adi", "ofis_adi")],
    "construction_company": [("sirket_id",), ("sirket_adi",)],
    "delivery_point": [("tip", "kod"), ("ad", "adres")],
}


def nonempty(record: dict, fields: tuple[str, ...]) -> bool:
    return all(record.get(field) not in (None, "") for field in fields)


def location_fields(record: dict) -> tuple[str, ...]:
    for group in LOCATION_GROUPS:
        present = tuple(field for field in group if record.get(field) not in (None, "", 0, "0"))
        if present:
            return present
    return ()


def natural_key(
    entity: str,
    variant: str,
    record: dict,
    *,
    content_hash: str,
    source_table: str,
    row_number: int,
) -> tuple[dict, str]:
    candidates = list(ENTITY_KEYS.get(entity, []))
    location = location_fields(record)
    if entity == "price_summary":
        candidates = [location + tuple(x for x in ("kategori", "tip", "donem") if x in record)]
    elif entity == "price_trend":
        candidates = [location + tuple(x for x in ("kategori", "tip", "ay") if x in record)]
    elif entity == "price_distribution":
        candidates = [
            location
            + tuple(x for x in ("kategori", "tip", "donem", "dagilim_turu", "dagilim", "segment") if x in record)
        ]
    elif entity == "annual_sales":
        candidates = [location + tuple(x for x in ("yil",) if x in record)]
    elif entity == "parcel_zoning":
        if nonempty(record, ("ada_no", "parsel_no")):
            candidates = [location + ("ada_no", "parsel_no")]
            if variant == "plan_change":
                candidates[0] += tuple(
                    field for field in ("plan_kodu", "aski_baslangic") if field in record
                )
        else:
            candidates = [("ilan_id",), ("url",)]
    elif entity in {
        "population_profile",
        "registry_origin_distribution",
        "election_results",
        "health_behaviour_statistics",
        "spending_ecommerce",
        "socioeconomic_development",
    }:
        extras = tuple(
            field
            for field in (
                "donem",
                "veri_donemi",
                "yil",
                "guncellenme_yili",
                "kutuk_ili",
                "secim_kodu",
            )
            if field in record
        )
        candidates = [location + extras]

    for fields in candidates:
        if fields and nonempty(record, fields):
            key = {"entity": entity, "variant": variant}
            key.update((field, record[field]) for field in fields)
            return key, "natural"
    return {
        "entity": entity,
        "variant": variant,
        "source_content_sha256": content_hash,
        "source_table": source_table,
        "source_row_number": row_number,
    }, "synthetic_lineage"


def first_text(record: dict, fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def validate_record(record: dict, key_kind: str, source_parse_status: str) -> list[str]:
    errors: list[str] = []
    if key_kind != "natural":
        errors.append("missing_natural_key")
    if source_parse_status != "valid":
        errors.append(f"source_parse_status:{source_parse_status}")
    for field in ("fiyat_tl", "fiyat", "ortalama_fiyat", "satilik_m2_fiyat", "kiralik_m2_fiyat"):
        value = record.get(field)
        if isinstance(value, (int, float)) and value < 0:
            errors.append(f"negative_value:{field}")
    for field, low, high in (("lat", -90, 90), ("enlem", -90, 90), ("lon", -180, 180), ("boylam", -180, 180)):
        value = record.get(field)
        if isinstance(value, (int, float)) and not low <= value <= high:
            errors.append(f"coordinate_out_of_range:{field}")
    return errors


def quality_score(record_class: str, errors: list[str], has_period: bool) -> int:
    score = {
        "observed": 70,
        "projection": 40,
        "observed_unresolved_projection_flag": 35,
        "demo": 10,
        "quarantine": 0,
        "invalid": 0,
    }.get(record_class, 50)
    score -= 25 * int("missing_natural_key" in errors)
    score -= 20 * int(any(error.startswith("source_parse_status:") for error in errors))
    score += 5 * int(has_period)
    return max(0, min(100, score))


def new_batch() -> dict[str, list]:
    return {field.name: [] for field in SILVER_SCHEMA}


def append_batch(writer: pq.ParquetWriter, values: dict[str, list]) -> int:
    if not values["source_row_number"]:
        return 0
    table = pa.Table.from_pydict(values, schema=SILVER_SCHEMA)
    writer.write_table(table)
    count = table.num_rows
    for column in values.values():
        column.clear()
    return count


def existing_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        metadata = pq.read_metadata(path)
        values = metadata.metadata or {}
        expected = {
            b"silver_version": SILVER_VERSION.encode("ascii"),
            b"classification_version": CLASSIFICATION_VERSION.encode("ascii"),
            b"contract_version": CONTRACT_VERSION.encode("ascii"),
        }
        if any(values.get(key) != value for key, value in expected.items()):
            return None
        return metadata.num_rows
    except (OSError, pa.ArrowException):
        return None


def output_path(root: Path, unit: dict) -> Path:
    table_part = normalize_text(unit["source_table"]) or "document"
    source_id = hashlib.sha256(
        f"{unit['content_hash']}|{unit['source_table']}".encode("utf-8")
    ).hexdigest()
    classification = unit["classification"]
    return root / classification["domain"] / classification["entity"] / f"{source_id}-{table_part}.parquet"


def append_observation(
    batch: dict[str, list],
    *,
    unit: dict,
    row_number: int,
    row_hash: bytes,
    record: dict,
    normalize_errors: list[str],
    parse_status: str,
) -> tuple[str, str, str]:
    classification = unit["classification"]
    period = first_text(record, ("ay", "donem", "yil", "veri_donemi", "guncellenme_yili"))
    collection_time = first_text(record, ("toplanma_zamani", "guncellenme_tarihi", "tarih", "created_at"))
    classification_reference_time = collection_time or unit["reference_time"]
    record_class, record_class_reason, projection_origin = row_class(
        classification["data_class"],
        classification["entity"],
        record,
        period=period,
        reference_time=classification_reference_time,
        locator=unit["locator"],
    )
    variant = entity_variant(classification["entity"], record, unit["source_table"], unit["locator"])
    key, key_kind = natural_key(
        classification["entity"],
        variant,
        record,
        content_hash=unit["content_hash"],
        source_table=unit["source_table"],
        row_number=row_number,
    )
    key_json = json.dumps(key, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    normalized_json = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    validation_errors = normalize_errors + validate_record(record, key_kind, parse_status)
    validation_status = "valid" if not validation_errors else "review"
    observation_seed = f"{unit['content_hash']}|{unit['source_table']}|{row_number}|{row_hash.hex()}"

    values = {
        "observation_id": digest_bytes(observation_seed),
        "source_content_sha256": bytes.fromhex(unit["content_hash"]),
        "source_table": unit["source_table"],
        "source_row_number": row_number,
        "source_row_sha256": row_hash,
        "normalized_payload_sha256": digest_bytes(normalized_json),
        "domain": classification["domain"],
        "entity": classification["entity"],
        "entity_variant": variant,
        "record_class": record_class,
        "record_class_reason": record_class_reason,
        "projection_origin": projection_origin,
        "classification_reference_time": classification_reference_time,
        "product_policy": classification["product_policy"],
        "privacy": classification["privacy"],
        "natural_key_sha256": digest_bytes(key_json),
        "natural_key_json": key_json,
        "natural_key_kind": key_kind,
        "period": period,
        "collection_time": collection_time,
        "normalized_record_json": normalized_json,
        "source_parse_status": parse_status,
        "validation_status": validation_status,
        "validation_errors_json": json.dumps(validation_errors, ensure_ascii=True, separators=(",", ":")),
        "quality_score": quality_score(record_class, validation_errors, period is not None),
        "source_occurrence_count": unit["occurrence_count"],
        "source_schema_fingerprint": unit["schema_fingerprint"],
    }
    for field in SILVER_SCHEMA:
        batch[field.name].append(values[field.name])
    return record_class, validation_status, key_kind


def write_unit(unit: dict, target: Path, batch_size: int) -> dict:
    expected = unit["rows"]
    current = existing_rows(target)
    if current == expected:
        return {"status": "reused", "rows": current, "output": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    writer = pq.ParquetWriter(temporary, SILVER_SCHEMA, compression="zstd", use_dictionary=True)
    written = 0
    class_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    key_counts: Counter[str] = Counter()
    try:
        batch = new_batch()
        source = pq.ParquetFile(unit["output"])
        if unit["source_kind"] == "json_document":
            row_hash = bytes.fromhex(unit["content_hash"])
            record = {
                "raw_document_reference": unit["content_hash"],
                "document_suffix": unit["suffix"],
            }
            rc, vs, kk = append_observation(
                batch,
                unit=unit,
                row_number=1,
                row_hash=row_hash,
                record=record,
                normalize_errors=["specialized_json_adapter_required"],
                parse_status="valid",
            )
            class_counts[rc] += 1
            validation_counts[vs] += 1
            key_counts[kk] += 1
        else:
            for arrow_batch in source.iter_batches(
                batch_size=batch_size,
                columns=["source_row_number", "row_sha256", "raw_record_json", "parse_status"],
            ):
                rows = arrow_batch.to_pydict()
                for row_number, row_hash_hex, raw_json, parse_status in zip(
                    rows["source_row_number"],
                    rows["row_sha256"],
                    rows["raw_record_json"],
                    rows["parse_status"],
                ):
                    raw_values = json.loads(raw_json)
                    record, normalize_errors = normalize_record(unit["columns"], raw_values)
                    rc, vs, kk = append_observation(
                        batch,
                        unit=unit,
                        row_number=row_number,
                        row_hash=bytes.fromhex(row_hash_hex),
                        record=record,
                        normalize_errors=normalize_errors,
                        parse_status=parse_status,
                    )
                    class_counts[rc] += 1
                    validation_counts[vs] += 1
                    key_counts[kk] += 1
                    if len(batch["source_row_number"]) >= batch_size:
                        written += append_batch(writer, batch)
        written += append_batch(writer, batch)
    except Exception:
        writer.close()
        temporary.unlink(missing_ok=True)
        raise
    writer.close()
    metadata = pq.read_metadata(temporary)
    if metadata.num_rows != expected or written != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"Silver satır dengesi bozuk: beklenen={expected}, yazılan={written}, parquet={metadata.num_rows}"
        )
    os.replace(temporary, target)
    return {
        "status": "written",
        "rows": written,
        "output": str(target),
        "record_classes": dict(sorted(class_counts.items())),
        "validation_statuses": dict(sorted(validation_counts.items())),
        "natural_key_kinds": dict(sorted(key_counts.items())),
    }


def aggregate_reused_output(result: dict) -> dict:
    table = pq.read_table(
        result["output"],
        columns=["record_class", "validation_status", "natural_key_kind"],
    )
    return {
        "record_classes": dict(sorted(Counter(table["record_class"].to_pylist()).items())),
        "validation_statuses": dict(sorted(Counter(table["validation_status"].to_pylist()).items())),
        "natural_key_kinds": dict(sorted(Counter(table["natural_key_kind"].to_pylist()).items())),
    }


def build_silver(
    bronze: dict,
    catalog: dict,
    output_root: Path,
    *,
    batch_size: int = 25_000,
    minimum_free_bytes: int = 2 * 1024**3,
) -> dict:
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    check_free_space(output_root, minimum_free_bytes)
    results = []
    status_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    key_counts: Counter[str] = Counter()
    source_rows = 0
    output_rows = 0

    for unit in source_units(bronze, catalog):
        if unit["status"] != "ready":
            results.append(unit)
            status_counts[unit["status"]] += 1
            continue
        source_rows += unit["rows"]
        check_free_space(output_root, minimum_free_bytes)
        target = output_path(output_root, unit)
        try:
            detail = write_unit(unit, target, batch_size)
            if detail["status"] == "reused":
                detail.update(aggregate_reused_output(detail))
            output_rows += detail["rows"]
            status_counts[detail["status"]] += 1
            for key, value in detail["record_classes"].items():
                class_counts[key] += value
            for key, value in detail["validation_statuses"].items():
                validation_counts[key] += value
            for key, value in detail["natural_key_kinds"].items():
                key_counts[key] += value
            results.append(
                {
                    "content_sha256": unit["content_hash"],
                    "source_table": unit["source_table"],
                    "source_locator": unit["locator"],
                    "all_locators": unit["all_locators"],
                    "source_occurrence_count": unit["occurrence_count"],
                    "classification": unit["classification"],
                    **detail,
                }
            )
        except Exception as exc:
            status_counts["technical_error"] += 1
            results.append(
                {
                    "content_sha256": unit["content_hash"],
                    "source_table": unit["source_table"],
                    "source_locator": unit["locator"],
                    "status": "technical_error",
                    "rows": unit["rows"],
                    "error": str(exc),
                }
            )

    technical_errors = status_counts["technical_error"]
    report = {
        "silver_version": SILVER_VERSION,
        "classification_version": CLASSIFICATION_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at": utc_now(),
        "bronze_generated_at": bronze.get("generated_at"),
        "catalog_generated_at": catalog.get("generated_at"),
        "output_root": str(output_root),
        "summary": {
            "source_units": len(results),
            "source_logical_rows": source_rows,
            "written_or_reused_rows": output_rows,
            "row_balance_ok": source_rows == output_rows and technical_errors == 0,
            "technical_errors": technical_errors,
            "statuses": dict(sorted(status_counts.items())),
            "record_classes": dict(sorted(class_counts.items())),
            "validation_statuses": dict(sorted(validation_counts.items())),
            "natural_key_kinds": dict(sorted(key_counts.items())),
        },
        "sources": results,
    }
    atomic_json(output_root / "silver-manifest.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bronze verilerini kayıpsız Silver gözlemlerine dönüştürür")
    parser.add_argument("--calistir", action="store_true", help="Silver üretimini açıkça başlat")
    parser.add_argument("--bronze-manifest", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--min-free-gib", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not args.calistir:
        parser.error("Üretim için açıkça --calistir verin")
    if args.batch_size < 1:
        parser.error("--batch-size pozitif olmalı")
    bronze = json.loads(args.bronze_manifest.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    report = build_silver(
        bronze,
        catalog,
        args.output_root,
        batch_size=args.batch_size,
        minimum_free_bytes=int(args.min_free_gib * 1024**3),
    )
    print(args.output_root.expanduser().resolve() / "silver-manifest.json")
    return 0 if report["summary"]["row_balance_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
