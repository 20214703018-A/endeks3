#!/usr/bin/env python3
"""Yerel CSV/SQLite/JSON/ZIP veri kaynaklarını değiştirmeden kataloglar.

Araç hiçbir arşivi kalıcı olarak açmaz ve hiçbir veritabanına yazmaz. Büyük
veri havuzlarında önce hangi dosyanın kanonik kaynak olacağına karar vermek
için makine-okunur bir envanter üretir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sqlite3
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, TextIO


DATA_SUFFIXES = {".csv", ".db", ".sqlite", ".json", ".geojson", ".zip"}


def dosya_ozeti(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ayirac_bul(header_line: str) -> str:
    return ";" if header_line.count(";") >= header_line.count(",") else ","


def csv_ozeti(stream: BinaryIO, *, source: str) -> dict:
    with io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text:
        first = text.readline()
        delimiter = ayirac_bul(first)
        text.seek(0)
        reader = csv.reader(text, delimiter=delimiter)
        header = next(reader, [])
        rows = 0
        malformed = 0
        for row in reader:
            rows += 1
            if len(row) != len(header):
                malformed += 1
    return {
        "source": source,
        "delimiter": delimiter,
        "columns": header,
        "column_count": len(header),
        "row_count": rows,
        "malformed_rows": malformed,
    }


def csv_path_ozeti(path: Path) -> dict:
    with path.open("rb") as stream:
        return {**dosya_ozeti(path), **csv_ozeti(stream, source=str(path.resolve()))}


def sqlite_ozeti(path: Path) -> dict:
    result = dosya_ozeti(path)
    if path.stat().st_size == 0:
        return {**result, "integrity": "empty_file", "tables": []}
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            tables = []
            names = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (name,) in names:
                quoted = name.replace('"', '""')
                count = conn.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
                columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{quoted}")')]
                tables.append({"name": name, "rows": count, "columns": columns})
        finally:
            conn.close()
        return {**result, "integrity": integrity, "tables": tables}
    except sqlite3.Error as exc:
        return {**result, "integrity": "error", "error": str(exc), "tables": []}


def json_ozeti(path: Path) -> dict:
    result = dosya_ozeti(path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if isinstance(value, list):
            shape = {"type": "array", "items": len(value)}
        elif isinstance(value, dict):
            shape = {"type": "object", "keys": len(value), "key_sample": list(value)[:12]}
            if value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
                shape["features"] = len(value["features"])
            if isinstance(value.get("polygons"), list):
                shape["polygons"] = len(value["polygons"])
        else:
            shape = {"type": type(value).__name__}
        return {**result, **shape}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {**result, "type": "invalid", "error": str(exc)}


def zip_ozeti(path: Path, csv_satirlari: bool) -> dict:
    result = dosya_ozeti(path)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [i for i in archive.infolist() if not i.is_dir()]
            suffixes = Counter(Path(i.filename).suffix.lower() or "[no-ext]" for i in entries)
            csv_entries = []
            if csv_satirlari:
                for info in entries:
                    if info.filename.lower().endswith(".csv"):
                        with archive.open(info) as stream:
                            profile = csv_ozeti(stream, source=f"{path.resolve()}::{info.filename}")
                        profile["bytes"] = info.file_size
                        csv_entries.append(profile)
            return {
                **result,
                "valid": True,
                "entry_count": len(entries),
                "uncompressed_bytes": sum(i.file_size for i in entries),
                "entry_types": dict(sorted(suffixes.items())),
                "nested_archives": [i.filename for i in entries if i.filename.lower().endswith(".zip")],
                "csv_entries": csv_entries,
            }
    except (OSError, zipfile.BadZipFile) as exc:
        return {**result, "valid": False, "error": str(exc)}


def kaynak_dosyalari(roots: list[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for root in roots:
        root = root.expanduser().resolve()
        candidates = [root] if root.is_file() else root.rglob("*") if root.is_dir() else []
        for path in candidates:
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.suffix.lower() in DATA_SUFFIXES:
                found[str(path.resolve())] = path
    return [found[k] for k in sorted(found)]


def envanter(roots: list[Path], *, zip_csv_satirlari: bool, hashes: bool) -> dict:
    files = kaynak_dosyalari(roots)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": [str(p.expanduser().resolve()) for p in roots],
        "summary": dict(sorted(Counter(p.suffix.lower() for p in files).items())),
        "csv": [],
        "sqlite": [],
        "json": [],
        "zip": [],
    }
    for path in files:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            item = csv_path_ozeti(path)
            result["csv"].append(item)
        elif suffix in {".db", ".sqlite"}:
            item = sqlite_ozeti(path)
            result["sqlite"].append(item)
        elif suffix in {".json", ".geojson"}:
            item = json_ozeti(path)
            result["json"].append(item)
        elif suffix == ".zip":
            item = zip_ozeti(path, zip_csv_satirlari)
            result["zip"].append(item)
        else:
            continue
        if hashes:
            item["sha256"] = sha256(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CSV, SQLite, JSON, GeoJSON ve ZIP veri envanteri çıkarır")
    parser.add_argument("roots", nargs="+", type=Path, help="Taranacak dosya veya klasörler")
    parser.add_argument("--zip-csv-satirlari", action="store_true", help="ZIP içindeki CSV satırlarını da say")
    parser.add_argument("--hashes", action="store_true", help="Dosyalar için SHA-256 hesapla")
    parser.add_argument("--output", type=Path, help="JSON raporunu bu dosyaya yaz")
    args = parser.parse_args(argv)

    missing = [str(p) for p in args.roots if not p.expanduser().exists()]
    if missing:
        parser.error("Bulunamayan kaynak: " + ", ".join(missing))

    report = envanter(args.roots, zip_csv_satirlari=args.zip_csv_satirlari, hashes=args.hashes)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(args.output.resolve())
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
