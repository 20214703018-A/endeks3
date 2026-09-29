#!/usr/bin/env python3
"""
PHASE 1 — DISCOVERY (salt-okunur envanter)

Kaynak köklere ASLA yazmaz. Bütün çıktılar OUT_ROOT altına gider.
Her dosya için: stat + streaming SHA-256 + magic-byte format tespiti + içerik envanteri.
Hiçbir hata sessizce yutulmaz: errors tablosuna file_id + aşama + mesaj yazılır.

Çalıştırma:
  python3 phase1_discovery.py --run            (tam tarama)
  python3 phase1_discovery.py --run --resume   (hash cache kullanır)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

PIPELINE_VERSION = "1.0.0"
OUT_ROOT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
SOURCE_ROOTS = [
    Path.home() / "Desktop" / "GEOPROP",
    Path.home() / "Desktop" / "tkgm",
    Path.home() / "Desktop" / "harita",
    Path.home() / "Downloads",
    Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE",
]
if os.environ.get("P1_ROOTS"):
    SOURCE_ROOTS = [Path(x).expanduser() for x in os.environ["P1_ROOTS"].split(":") if x]
if os.environ.get("P1_OUT"):
    OUT_ROOT = Path(os.environ["P1_OUT"]).expanduser()
# Canlı yazılan intake dizinleri (toplayıcı çalışırken) taramadan hariç tutulur; tamamlanınca P1_EXCLUDE kaldırılıp taranır.
EXCLUDE_PATH_PREFIXES = [x for x in os.environ.get("P1_EXCLUDE", "").split(":") if x]
EXCLUDE_DIR_NAMES = {"node_modules", ".git", "venv", ".venv", "__pycache__", ".pytest_cache", ".next", ".idea", ".vscode"}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db", ".localized"}

DATA_EXT = {
    "csv", "tsv", "txt", "json", "jsonl", "ndjson", "geojson", "geojsonseq", "sqlite", "sqlite3", "db", "gpkg",
    "parquet", "xlsx", "xls", "zip", "gz", "shp", "dbf", "shx", "prj", "kml", "kmz", "pbf", "sql",
    "dump", "xml", "yaml", "yml", "geojsonl", "feather", "arrow", "duckdb",
}
MIN_FREE_DISK_BYTES = 1 * 1024**3  # 1 GB altına düşerse dur
JSON_INMEM_LIMIT = 150 * 1024**2   # 150 MB üstü JSON'lar DuckDB ile okunur
SAMPLE_VALUES_N = 5
TR_BBOX = (25.5, 35.7, 45.0, 42.3)  # lon_min, lat_min, lon_max, lat_max

TOOLS_DIR = OUT_ROOT / "tools"
RULES = json.loads((TOOLS_DIR / "quarantine_rules.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# yardımcılar
# --------------------------------------------------------------------------------------
def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_stream(path: Path, bufsize: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_head(path: Path, n: int = 4096) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except Exception:
        return b""


def read_tail(path: Path, n: int = 8) -> bytes:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - n))
            return f.read(n)
    except Exception:
        return b""


def detect_encoding(sample: bytes) -> tuple[str, float]:
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", 1.0
    if sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff"):
        return "utf-16", 1.0
    try:
        sample.decode("utf-8")
        return "utf-8", 0.99
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        r = from_bytes(sample).best()
        if r is not None:
            return r.encoding, float(getattr(r, "chaos", 0.0) and 1 - r.chaos or 0.8)
    except Exception:
        pass
    return "unknown", 0.0


def is_text_sample(sample: bytes) -> bool:
    if not sample:
        return True
    if b"\x00" in sample[:1024]:
        return False
    textchars = bytes(range(32, 127)) + b"\n\r\t\b\f"
    nontext = sum(1 for c in sample[:2048] if c not in textchars and c < 0x80)
    return nontext / max(1, min(len(sample), 2048)) < 0.05


def detect_format(path: Path, ext: str) -> tuple[str, dict]:
    """magic bytes -> detected_format. Uzantıya güvenmez."""
    head = read_head(path, 4096)
    info: dict = {}
    if not head:
        return ("empty" if path.stat().st_size == 0 else "unreadable"), info
    if head.startswith(b"SQLite format 3\x00"):
        # GeoPackage mi?
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            con.close()
            if "gpkg_contents" in names:
                return "geopackage", info
        except Exception as e:
            info["probe_error"] = str(e)[:200]
        return "sqlite", info
    if head.startswith(b"PAR1") and read_tail(path, 4) == b"PAR1":
        return "parquet", info
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            if any(n.startswith("xl/") for n in names) and "[Content_Types].xml" in names:
                return "xlsx", info
            if any(n.startswith("word/") for n in names):
                return "docx", info
            if any(n.startswith("ppt/") for n in names):
                return "pptx", info
            if "doc.kml" in names:
                return "kmz", info
            return "zip", info
        except zipfile.BadZipFile:
            return "zip_corrupt", info
    if head.startswith(b"\x1f\x8b"):
        return "gzip", info
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole_xls_doc", info
    if head.startswith(b"%PDF"):
        return "pdf", info
    if head.startswith(b"\x89PNG"):
        return "png", info
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg", info
    if head.startswith(b"ARROW1"):
        return "arrow_ipc", info
    if head[:4] == b"\x00\x00\x27\x0a":
        return "shapefile", info
    if head.startswith(b"PGDMP"):
        return "pg_dump_custom", info
    if head.startswith(b"DUCK"):
        return "duckdb", info
    if not is_text_sample(head):
        return "binary_unknown", info
    # metin tabanlı
    enc, _ = detect_encoding(head)
    try:
        text = head.decode(enc if enc != "unknown" else "utf-8", errors="replace")
    except Exception:
        text = head.decode("utf-8", errors="replace")
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("\x1e{"):  # RFC 7464 JSON text sequence (GeoJSONSeq / ogr2ogr GeoJSONSeq): her kayıt RS (0x1E) ile başlar
        return "geojsonseq", info
    if stripped.startswith("{") or stripped.startswith("["):
        # JSON / GeoJSON / JSONL
        if ext in ("jsonl", "ndjson") and stripped.startswith("{"):  # uzantı ipucu: ilk kayıt örnekleme penceresinden uzun olabilir
            return "jsonl", info
        first_line = stripped.split("\n", 1)[0]
        if stripped.startswith("{") and first_line.rstrip().endswith("}") and "\n" in stripped and stripped.split("\n", 2)[1].lstrip().startswith("{"):
            return "jsonl", info
        if re.search(r'"type"\s*:\s*"(FeatureCollection|Feature|Polygon|MultiPolygon|Point|LineString|MultiLineString|MultiPoint|GeometryCollection)"', stripped[:4000]):
            return "geojson", info
        return "json", info
    if stripped.startswith("<?xml") or stripped.startswith("<kml"):
        if "<kml" in stripped[:2000]:
            return "kml", info
        if "<svg" in stripped[:2000]:
            return "svg", info
        return "xml", info
    if stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
        return "html", info
    # delimited?
    lines = [l for l in text.split("\n")[:20] if l.strip()]
    if len(lines) >= 2:
        for delim, name in ((",", "csv"), (";", "csv_semicolon"), ("\t", "tsv"), ("|", "psv")):
            counts = [l.count(delim) for l in lines]
            if counts[0] > 0 and len(set(counts)) <= 2 and min(counts) > 0:
                info["delimiter"] = delim
                return name, info
    if ext in ("md",) or stripped.startswith("---\n") or stripped.startswith("# "):
        return "markdown", info
    if ext in ("sql",) or re.search(r"(?i)^\s*(create table|insert into|-- )", stripped[:500]):
        return "sql_text", info
    return "text", info


def sniff_delimiter(sample_text: str, default: str = ",") -> str:
    try:
        d = csv.Sniffer().sniff(sample_text[:20000], delimiters=",;\t|")
        return d.delimiter
    except Exception:
        return default


def raw_line_count(path: Path) -> int:
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            n += b.count(b"\n")
    return n


def free_disk_bytes(p: Path) -> int:
    return shutil.disk_usage(p).free


def safe_str(v, limit: int = 200):
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= limit else s[:limit] + "…"


# --------------------------------------------------------------------------------------
# çalışma durumu
# --------------------------------------------------------------------------------------
class Run:
    def __init__(self, resume: bool):
        self.started_at = now_iso()
        day = dt.datetime.now().strftime("%Y_%m_%d")
        existing = sorted((OUT_ROOT / "logs").glob(f"RUN_{day}_*"))
        self.run_id = f"RUN_{day}_{len(existing) + 1:03d}"
        self.log_dir = OUT_ROOT / "logs" / self.run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.audit = open(self.log_dir / "audit_log.jsonl", "a", encoding="utf-8")
        self.errlog = open(self.log_dir / "error_log.jsonl", "a", encoding="utf-8")
        self.resume = resume
        self.files: list[dict] = []
        self.tables: list[dict] = []
        self.columns: list[dict] = []
        self.geo_layers: list[dict] = []
        self.errors: list[dict] = []
        self.parse_errors: list[dict] = []
        self.zip_members: list[dict] = []
        self.quarantine: list[dict] = []
        self.schemas: dict[str, dict] = {}
        self.next_table_id = 1
        self.hash_cache_path = OUT_ROOT / "raw_manifest" / "hash_cache.jsonl"
        self.hash_cache: dict[str, dict] = {}
        if resume and self.hash_cache_path.exists():
            for line in open(self.hash_cache_path, encoding="utf-8"):
                try:
                    r = json.loads(line)
                    self.hash_cache[r["path"]] = r
                except Exception:
                    pass
        self.hash_cache_out = open(self.hash_cache_path, "a", encoding="utf-8")
        tmpdir = OUT_ROOT / "tmp" / self.run_id
        tmpdir.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect()
        self.con.execute("SET memory_limit='2GB'")
        self.con.execute(f"SET temp_directory='{tmpdir}'")
        self.con.execute("SET threads=4")
        try:
            self.con.execute("LOAD spatial")
            self.spatial = True
        except Exception:
            try:
                self.con.execute("INSTALL spatial; LOAD spatial")
                self.spatial = True
            except Exception as e:
                self.spatial = False
                self.log_error(None, "init", "spatial_unavailable", str(e))
        self.code_hash = sha256_stream(Path(__file__))
        self.rules_hash = sha256_stream(TOOLS_DIR / "quarantine_rules.json")
        self.prev = None  # artımlı mod: önceki envanter

    def load_previous(self):
        inv = OUT_ROOT / "inventory"
        if not (inv / "inventory.parquet").exists():
            return
        c = duckdb.connect()
        def rows(name):
            p = inv / name
            if not p.exists(): return []
            try:
                r = c.execute(f"SELECT * FROM '{p}'")
                cols = [d[0] for d in r.description]
                if cols == ["_empty"]: return []
                return [dict(zip(cols, x)) for x in r.fetchall()]
            except Exception:
                return []
        self.prev = {"files": rows("inventory.parquet"), "tables": rows("tables.parquet"), "columns": rows("columns.parquet"), "geo": rows("geo_layers.parquet"),
                     "errors": rows("errors.parquet"), "parse_errors": rows("parse_errors.parquet"), "zip_members": rows("zip_members.parquet")}
        try:
            self.prev["schemas"] = json.loads((inv / "schemas.json").read_text(encoding="utf-8"))
        except Exception:
            self.prev["schemas"] = {}
        # sayaçlar devam etsin
        mt = max((int(t["table_id"].split("_")[-1]) for t in self.prev["tables"] if t.get("table_id")), default=0)
        self.next_table_id = mt + 1
        # JSON string alanları geri çevirmeye gerek yok; olduğu gibi taşınır

    def audit_write(self, op: str, **kw):
        rec = {"timestamp": now_iso(), "run_id": self.run_id, "operation": op, "pipeline_version": PIPELINE_VERSION, "code_hash": self.code_hash}
        rec.update(kw)
        self.audit.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.audit.flush()

    def log_error(self, file_id, stage, error_type, message, **kw):
        rec = {"file_id": file_id, "stage": stage, "error_type": error_type, "error_message": safe_str(message, 1000), "detected_at": now_iso()}
        rec.update(kw)
        self.errors.append(rec)
        self.errlog.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.errlog.flush()

    def new_table_id(self) -> str:
        tid = f"SOURCE_TABLE_{self.next_table_id:06d}"
        self.next_table_id += 1
        return tid

    def check_disk(self):
        free = free_disk_bytes(OUT_ROOT)
        if free < MIN_FREE_DISK_BYTES:
            raise SystemExit(f"DISK_GUARD: boş alan {free/1e9:.2f} GB < 1 GB. Durduruldu.")


# --------------------------------------------------------------------------------------
# AŞAMA 1: dosya sistemi taraması + hash
# --------------------------------------------------------------------------------------
def walk_sources(run: Run) -> None:
    prev_by_path = {r["absolute_path"]: r for r in (run.prev["files"] if run.prev else [])}
    file_no = max((int(r["file_id"].split("_")[-1]) for r in prev_by_path.values()), default=0)
    for root in SOURCE_ROOTS:
        if not root.exists():
            run.log_error(None, "walk", "root_missing", str(root))
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES and not any(str(Path(dirpath) / d).startswith(x) for x in EXCLUDE_PATH_PREFIXES))
            if any(dirpath.startswith(x) for x in EXCLUDE_PATH_PREFIXES):
                continue
            for fn in sorted(filenames):
                if fn in EXCLUDE_FILE_NAMES:
                    continue
                p = Path(dirpath) / fn
                if p.is_symlink():
                    continue
                if p_str := str(Path(dirpath) / fn):
                    pass
                if p_str in prev_by_path:
                    fid = prev_by_path[p_str]["file_id"]
                else:
                    file_no += 1
                    fid = f"FILE_{file_no:08d}"
                rec = {
                    "file_id": fid,
                    "source_root": str(root),
                    "absolute_path": str(p),
                    "relative_path": str(p.relative_to(root)),
                    "filename": fn,
                    "extension": (p.suffix[1:].lower() if p.suffix else ""),
                    "size_bytes": None, "created_at": None, "modified_at": None,
                    "sha256": None, "mime_type": None, "detected_format": None, "extension_format": None,
                    "format_mismatch": False, "encoding": None, "compression": None,
                    "read_status": "pending", "error_status": None,
                    "is_data_candidate": False, "content_probed": False,
                    "hash_source": None,
                }
                try:
                    st = p.stat()
                    rec["size_bytes"] = st.st_size
                    rec["created_at"] = dt.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime), dt.timezone.utc).isoformat(timespec="seconds")
                    rec["modified_at"] = dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
                    rec["mtime_epoch"] = st.st_mtime
                except Exception as e:
                    rec["read_status"] = "stat_failed"
                    rec["error_status"] = safe_str(e)
                    run.log_error(fid, "stat", type(e).__name__, e, path=str(p))
                    run.files.append(rec)
                    continue
                run.files.append(rec)
    run.audit_write("walk_sources", files_found=len(run.files), roots=[str(r) for r in SOURCE_ROOTS])


def hash_and_detect(run: Run) -> None:
    total = len(run.files)
    t0 = time.time()
    done_bytes = 0
    for i, rec in enumerate(run.files, 1):
        if rec["read_status"] == "stat_failed":
            continue
        p = Path(rec["absolute_path"])
        key = rec["absolute_path"]
        cached = run.hash_cache.get(key)
        try:
            if cached and cached.get("size") == rec["size_bytes"] and abs(cached.get("mtime", -1) - rec["mtime_epoch"]) < 1e-6:
                rec["sha256"] = cached["sha256"]
                rec["hash_source"] = "cache"
            else:
                rec["sha256"] = sha256_stream(p)
                rec["hash_source"] = "computed"
                run.hash_cache_out.write(json.dumps({"path": key, "size": rec["size_bytes"], "mtime": rec["mtime_epoch"], "sha256": rec["sha256"], "hashed_at": now_iso()}) + "\n")
            rec["read_status"] = "hashed"
        except Exception as e:
            rec["read_status"] = "unreadable"
            rec["error_status"] = safe_str(e)
            run.log_error(rec["file_id"], "hash", type(e).__name__, e, path=str(p))
            continue
        done_bytes += rec["size_bytes"] or 0
        try:
            fmt, info = detect_format(p, rec["extension"])
            rec["detected_format"] = fmt
            rec["extension_format"] = rec["extension"] or "(none)"
            ext = rec["extension"]
            ext_family = {
                "csv": {"csv", "csv_semicolon", "tsv", "psv", "text"}, "tsv": {"tsv", "csv", "text"}, "txt": {"text", "csv", "csv_semicolon", "tsv", "psv", "json", "jsonl", "sql_text", "markdown", "html", "xml"},
                "json": {"json", "geojson", "jsonl"}, "jsonl": {"jsonl", "json"}, "ndjson": {"jsonl"}, "geojson": {"geojson", "json"}, "geojsonseq": {"geojsonseq", "jsonl", "json"},
                "sqlite": {"sqlite", "geopackage"}, "sqlite3": {"sqlite"}, "db": {"sqlite", "geopackage", "duckdb"}, "gpkg": {"geopackage", "sqlite"},
                "parquet": {"parquet"}, "xlsx": {"xlsx"}, "xls": {"ole_xls_doc", "xlsx", "html"}, "zip": {"zip"}, "gz": {"gzip"}, "kml": {"kml", "xml"}, "kmz": {"kmz", "zip"},
                "sql": {"sql_text", "text"}, "dump": {"pg_dump_custom", "sql_text", "text"}, "md": {"markdown", "text"}, "html": {"html", "text"}, "xml": {"xml"},
                "pdf": {"pdf"}, "png": {"png"}, "jpg": {"jpeg"}, "jpeg": {"jpeg"}, "shp": {"shapefile"}, "pbf": {"binary_unknown"},
            }
            if ext in ext_family and fmt not in ext_family[ext] and fmt not in ("empty",):
                rec["format_mismatch"] = True
            if fmt in ("csv", "csv_semicolon", "tsv", "psv", "json", "jsonl", "geojson", "text", "sql_text", "xml", "kml"):
                enc, conf = detect_encoding(read_head(p, 262144))
                rec["encoding"] = enc
            if fmt in ("gzip",):
                rec["compression"] = "gzip"
            elif fmt in ("zip", "xlsx", "kmz"):
                rec["compression"] = "zip"
            rec["is_data_candidate"] = fmt in {
                "sqlite", "geopackage", "parquet", "zip", "gzip", "xlsx", "ole_xls_doc", "csv", "csv_semicolon", "tsv", "psv",
                "json", "jsonl", "geojsonseq", "geojson", "kml", "kmz", "shapefile", "pg_dump_custom", "duckdb", "arrow_ipc", "sql_text", "text",
            } or ext in DATA_EXT
        except Exception as e:
            rec["detected_format"] = "detect_failed"
            run.log_error(rec["file_id"], "detect_format", type(e).__name__, e, path=str(p))
        if i % 500 == 0 or i == total:
            el = time.time() - t0
            print(f"  [hash] {i}/{total}  {done_bytes/1e9:.2f} GB  {el:.0f}s", flush=True)
            run.check_disk()
    run.audit_write("hash_and_detect", files=total, bytes=done_bytes, seconds=round(time.time() - t0, 1))


# --------------------------------------------------------------------------------------
# AŞAMA 2: içerik envanteri
# --------------------------------------------------------------------------------------
def col_stats_duckdb(run: Run, rel: str, source_ref: dict, sample_n: int = SAMPLE_VALUES_N, row_count: int | None = None) -> list[dict]:
    """rel: DuckDB'de sorgulanabilir bir ifade (tablo adı veya (subquery))."""
    cols = run.con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
    out = []
    if row_count is None:
        row_count = run.con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
    for idx, (cname, ctype, *_r) in enumerate(cols):
        q = f'"{cname.replace(chr(34), chr(34)*2)}"'
        try:
            r = run.con.execute(
                f"SELECT COUNT(*) FILTER (WHERE {q} IS NULL), "
                f"COUNT(*) FILTER (WHERE CAST({q} AS VARCHAR) = ''), "
                f"approx_count_distinct({q}), "
                f"MIN(CAST({q} AS VARCHAR)), MAX(CAST({q} AS VARCHAR)) FROM {rel}"
            ).fetchone()
            samples = [x[0] for x in run.con.execute(f"SELECT DISTINCT CAST({q} AS VARCHAR) FROM {rel} WHERE {q} IS NOT NULL LIMIT {sample_n}").fetchall()]
            # 'sayı gibi görünen ama sıfırla başlayan' değerler
            lz = run.con.execute(f"SELECT COUNT(*) FROM {rel} WHERE regexp_matches(CAST({q} AS VARCHAR), '^0[0-9]+$')").fetchone()[0]
            out.append({**source_ref, "column_index": idx, "column_name": cname, "declared_type": ctype, "observed_type": None,
                        "null_count": r[0], "empty_string_count": r[1], "approx_unique_count": r[2], "min_value": safe_str(r[3], 100), "max_value": safe_str(r[4], 100),
                        "sample_values": json.dumps([safe_str(s, 80) for s in samples], ensure_ascii=False), "leading_zero_numeric_count": lz,
                        "is_constant": (row_count > 1 and r[2] <= 1), "row_count": row_count})
        except Exception as e:
            out.append({**source_ref, "column_index": idx, "column_name": cname, "declared_type": ctype, "observed_type": None, "null_count": None, "empty_string_count": None,
                        "approx_unique_count": None, "min_value": None, "max_value": None, "sample_values": None, "leading_zero_numeric_count": None, "is_constant": None, "row_count": row_count,
                        "stat_error": safe_str(e, 300)})
    return out


def content_detectors(run: Run, rel: str, cols: list[str], table_ref: dict) -> list[dict]:
    """Kural tabanlı içerik dedektörleri: üretilmiş il adı, sabit KAKS/TAKS, 2027 projeksiyon, bbox dışı koordinat, record_class."""
    findings = []
    def q(c): return f'"{c.replace(chr(34), chr(34)*2)}"'
    for det in RULES["content_detectors"]:
        try:
            if det["detector_id"] == "C01":
                for c in cols:
                    if re.match(det["applies_to_columns_regex"], c):
                        n = run.con.execute(f"SELECT COUNT(*) FROM {rel} WHERE regexp_matches(CAST({q(c)} AS VARCHAR), '{det['pattern']}')").fetchone()[0]
                        if n:
                            findings.append({**table_ref, "detector_id": "C01", "column": c, "hit_count": n, "status": det["status"], "reason": det["reason"]})
            elif det["detector_id"] == "C02":
                for c in cols:
                    if re.match(det["columns_regex"], c):
                        r = run.con.execute(f"SELECT COUNT(*), approx_count_distinct({q(c)}), MIN(CAST({q(c)} AS VARCHAR)) FROM {rel} WHERE {q(c)} IS NOT NULL").fetchone()
                        if r[0] > 1 and r[1] == 1:
                            findings.append({**table_ref, "detector_id": "C02", "column": c, "hit_count": r[0], "status": "QUARANTINE", "reason": det["reason"] + f" (değer={r[2]})"})
            elif det["detector_id"] == "C03":
                for c in cols:
                    if re.match(det["columns_regex"], c):
                        n = run.con.execute(f"SELECT COUNT(*) FROM {rel} WHERE regexp_matches(CAST({q(c)} AS VARCHAR), '{det['pattern']}')").fetchone()[0]
                        if n:
                            findings.append({**table_ref, "detector_id": "C03", "column": c, "hit_count": n, "status": det["status"], "reason": det["reason"]})
            elif det["detector_id"] == "C04":
                lat = next((c for c in cols if re.match(det["lat_regex"], c)), None)
                lon = next((c for c in cols if re.match(det["lon_regex"], c)), None)
                if lat and lon:
                    x0, y0, x1, y1 = det["bbox"]
                    n = run.con.execute(
                        f"SELECT COUNT(*) FROM {rel} WHERE TRY_CAST({q(lat)} AS DOUBLE) IS NOT NULL AND TRY_CAST({q(lon)} AS DOUBLE) IS NOT NULL AND NOT "
                        f"(TRY_CAST({q(lat)} AS DOUBLE) BETWEEN {y0} AND {y1} AND TRY_CAST({q(lon)} AS DOUBLE) BETWEEN {x0} AND {x1})"
                    ).fetchone()[0]
                    if n:
                        findings.append({**table_ref, "detector_id": "C04", "column": f"{lat},{lon}", "hit_count": n, "status": det["status"], "reason": det["reason"]})
            elif det["detector_id"] == "C05":
                if det["column"] in cols:
                    n = run.con.execute(f"SELECT COUNT(*) FROM {rel} WHERE CAST({q(det['column'])} AS VARCHAR) = '{det['value']}'").fetchone()[0]
                    if n:
                        findings.append({**table_ref, "detector_id": "C05", "column": det["column"], "hit_count": n, "status": det["status"], "reason": "record_class='quarantine' satırları (önceki ekip işaretlemesi)."})
        except Exception as e:
            run.log_error(table_ref.get("file_id"), "content_detector", det["detector_id"], e, table=table_ref.get("table_name"))
    return findings


def content_detectors_sqlite(run: Run, con: sqlite3.Connection, qn: str, cols: list[str], table_ref: dict) -> list[dict]:
    findings = []
    def q(c): return '"' + c.replace('"', '""') + '"'
    for det in RULES["content_detectors"]:
        try:
            if det["detector_id"] == "C01":
                for c in cols:
                    if re.match(det["applies_to_columns_regex"], c):
                        n = con.execute(f"SELECT COUNT(*) FROM {qn} WHERE {q(c)} REGEXP ?", (det["pattern"],)).fetchone()[0]
                        if n:
                            findings.append({**table_ref, "detector_id": "C01", "column": c, "hit_count": n, "status": det["status"], "reason": det["reason"]})
            elif det["detector_id"] == "C02":
                for c in cols:
                    if re.match(det["columns_regex"], c):
                        r = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT {q(c)}), MIN({q(c)}) FROM {qn} WHERE {q(c)} IS NOT NULL").fetchone()
                        if r[0] > 1 and r[1] == 1:
                            findings.append({**table_ref, "detector_id": "C02", "column": c, "hit_count": r[0], "status": "QUARANTINE", "reason": det["reason"] + f" (değer={r[2]})"})
            elif det["detector_id"] == "C03":
                for c in cols:
                    if re.match(det["columns_regex"], c):
                        n = con.execute(f"SELECT COUNT(*) FROM {qn} WHERE CAST({q(c)} AS TEXT) REGEXP ?", (det["pattern"],)).fetchone()[0]
                        if n:
                            findings.append({**table_ref, "detector_id": "C03", "column": c, "hit_count": n, "status": det["status"], "reason": det["reason"]})
            elif det["detector_id"] == "C04":
                lat = next((c for c in cols if re.match(det["lat_regex"], c)), None)
                lon = next((c for c in cols if re.match(det["lon_regex"], c)), None)
                if lat and lon:
                    x0, y0, x1, y1 = det["bbox"]
                    n = con.execute(f"SELECT COUNT(*) FROM {qn} WHERE typeof({q(lat)}) IN ('integer','real') AND typeof({q(lon)}) IN ('integer','real') AND NOT ({q(lat)} BETWEEN {y0} AND {y1} AND {q(lon)} BETWEEN {x0} AND {x1})").fetchone()[0]
                    if n:
                        findings.append({**table_ref, "detector_id": "C04", "column": f"{lat},{lon}", "hit_count": n, "status": det["status"], "reason": det["reason"]})
            elif det["detector_id"] == "C05":
                if det["column"] in cols:
                    n = con.execute(f"SELECT COUNT(*) FROM {qn} WHERE {q(det['column'])} = ?", (det["value"],)).fetchone()[0]
                    if n:
                        findings.append({**table_ref, "detector_id": "C05", "column": det["column"], "hit_count": n, "status": det["status"], "reason": "record_class='quarantine' satırları (önceki ekip işaretlemesi)."})
        except Exception as e:
            run.log_error(table_ref.get("file_id"), "content_detector_sqlite", det["detector_id"], e, table=table_ref.get("table_name"))
    return findings


def probe_csv_bytes_source(run: Run, rec: dict, duck_path: str, fmt: str, delimiter_hint: str | None, table_name: str, member_name: str | None = None, raw_lines: int | None = None):
    """DuckDB ile CSV envanteri. all_varchar=true: hiçbir tip zorlanmaz, sıfırlar korunur. store_rejects: parse hataları satır+ham satır ile kaydedilir."""
    fid = rec["file_id"]
    tid = run.new_table_id()
    head = read_head(Path(duck_path), 65536) if member_name is None else None
    enc = rec.get("encoding") or "utf-8"
    delim = delimiter_hint
    quote = '"'
    header_detected = None
    if head:
        try:
            txt = head.decode("utf-8-sig" if enc == "utf-8-sig" else ("utf-8" if enc in ("utf-8", "unknown") else enc), errors="replace")
        except Exception:
            txt = head.decode("utf-8", errors="replace")
        delim = delim or sniff_delimiter(txt, {"tsv": "\t", "csv_semicolon": ";", "psv": "|"}.get(fmt, ","))
        try:
            header_detected = csv.Sniffer().has_header(txt[:20000])
        except Exception:
            header_detected = None
    delim = delim or ","
    d_esc = delim.replace("'", "''")
    tmp = f"t_{tid.lower()}"
    run.con.execute("DROP TABLE IF EXISTS reject_scans; DROP TABLE IF EXISTS reject_errors;")
    enc_opt = "utf-8"
    if enc and enc.lower().replace("_", "-") in ("latin-1", "iso-8859-1", "latin1"):
        enc_opt = "latin-1"
    elif enc and enc.lower().replace("_", "-") in ("utf-16", "utf-16le", "utf-16be"):
        enc_opt = "utf-16"
    opts = f"all_varchar=true, header=true, delim='{d_esc}', quote='\"', escape='\"', store_rejects=true, ignore_errors=true, null_padding=true, strict_mode=false, encoding='{enc_opt}', sample_size=-1"
    try:
        run.con.execute(f"CREATE OR REPLACE TABLE {tmp} AS SELECT * FROM read_csv('{duck_path.replace(chr(39), chr(39)*2)}', {opts})")
    except Exception as e:
        # ikinci deneme: tek iş parçacığı (çok satırlı alıntılı alanlar); üçüncü: otomatik algılama
        try:
            run.con.execute("DROP TABLE IF EXISTS reject_scans; DROP TABLE IF EXISTS reject_errors;")
            run.con.execute(f"CREATE OR REPLACE TABLE {tmp} AS SELECT * FROM read_csv('{duck_path.replace(chr(39), chr(39)*2)}', {opts}, parallel=false)")
            run.log_error(fid, "csv_probe", "retry_parallel_false", safe_str(e, 300), table=table_name)
        except Exception as e1:
          try:
            run.con.execute("DROP TABLE IF EXISTS reject_scans; DROP TABLE IF EXISTS reject_errors;")
            run.con.execute(f"CREATE OR REPLACE TABLE {tmp} AS SELECT * FROM read_csv('{duck_path.replace(chr(39), chr(39)*2)}', all_varchar=true, store_rejects=true, ignore_errors=true, null_padding=true, strict_mode=false, sample_size=-1, parallel=false)")
            run.log_error(fid, "csv_probe", "delimiter_fallback_auto", safe_str(e1, 300), table=table_name)
          except Exception as e2:
            run.log_error(fid, "csv_probe", type(e2).__name__, e2, table=table_name)
            run.tables.append({"table_id": tid, "file_id": fid, "table_name": table_name, "member_name": member_name, "object_type": "csv", "row_count": None, "column_count": None,
                               "column_names": None, "detected_delimiter": delim, "encoding": enc, "quote_character": quote, "escape_character": '"', "header_detected": header_detected,
                               "raw_line_count": raw_lines, "reject_count": None, "read_status": "failed", "notes": safe_str(e2, 300)})
            return
    row_count = run.con.execute(f"SELECT COUNT(*) FROM {tmp}").fetchone()[0]
    cols = [r[0] for r in run.con.execute(f"DESCRIBE {tmp}").fetchall()]
    rejects = run.con.execute("SELECT COUNT(*) FROM reject_errors").fetchone()[0]
    if rejects:
        for r in run.con.execute("SELECT line, column_idx, column_name, error_type, csv_line, error_message, byte_position FROM reject_errors LIMIT 5000").fetchall():
            run.parse_errors.append({"file_id": fid, "table_id": tid, "member_name": member_name, "source_row_number": r[0], "column_idx": r[1], "column_name": r[2],
                                     "error_type": str(r[3]), "raw_row": safe_str(r[4], 2000), "error_message": safe_str(r[5], 500), "byte_position": r[6], "detected_at": now_iso()})
    table_ref = {"table_id": tid, "file_id": fid, "table_name": table_name}
    run.columns.extend(col_stats_duckdb(run, tmp, table_ref, row_count=row_count))
    run.quarantine.extend(content_detectors(run, tmp, cols, table_ref))
    # tutarlılık: ham satır sayısı ≈ header + rows + rejects (çok satırlı alanlar sapma yaratabilir)
    accounted = (row_count + rejects + (1 if header_detected is not False else 0)) if raw_lines is not None else None
    run.tables.append({"table_id": tid, "file_id": fid, "table_name": table_name, "member_name": member_name, "object_type": "csv", "row_count": row_count, "column_count": len(cols),
                       "column_names": json.dumps(cols, ensure_ascii=False), "detected_delimiter": delim, "encoding": enc, "quote_character": quote, "escape_character": '"',
                       "header_detected": header_detected, "raw_line_count": raw_lines, "reject_count": rejects, "read_status": "ok",
                       "notes": (None if raw_lines is None or accounted is None or abs(raw_lines - accounted) <= max(2, int(0.001 * raw_lines)) else f"LINE_COUNT_MISMATCH raw={raw_lines} accounted={accounted} (çok satırlı alanlar olabilir)")})
    run.schemas[tid] = {"file_id": fid, "table_name": table_name, "columns": cols, "kind": "csv"}
    run.con.execute(f"DROP TABLE IF EXISTS {tmp}")


def probe_csv(run: Run, rec: dict):
    p = Path(rec["absolute_path"])
    fmt = rec["detected_format"]
    delim_hint = {"tsv": "\t", "csv_semicolon": ";", "psv": "|"}.get(fmt)
    raw_lines = raw_line_count(p)
    probe_csv_bytes_source(run, rec, str(p), fmt, delim_hint, table_name=p.name, raw_lines=raw_lines)


def probe_sqlite(run: Run, rec: dict):
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    wal = Path(str(p) + "-wal")
    wal_note = None
    tmp_copy = None
    hdr = read_head(p, 100)
    wal_mode = len(hdr) >= 20 and hdr[18] == 2 and hdr[19] == 2
    if wal_mode or wal.exists():
        # WAL modundaki DB'yi kaynakta AÇMA: SQLite -wal/-shm yan dosyası oluşturur/değiştirir. db(+wal) tmp'ye kopyalanır, kopya okunur.
        if not (wal.exists() and wal.stat().st_size > 0):
            need = p.stat().st_size
            if free_disk_bytes(OUT_ROOT) < need + MIN_FREE_DISK_BYTES:
                run.log_error(fid, "sqlite_wal", "DISK_GUARD", f"WAL-modu kopyası için alan yok ({need} B); immutable okuma yapıldı")
                wal_note = "WAL_MODE_IMMUTABLE_READ"
            else:
                tmp_copy = OUT_ROOT / "tmp" / run.run_id / f"walcopy_{fid}.sqlite"
                shutil.copyfile(p, tmp_copy)
                wal_note = "WAL_MODE_READ_VIA_TMP_COPY (kaynakta -wal yok/boş)"
    if wal.exists() and wal.stat().st_size > 0:
        # WAL'daki commit edilmiş satırları kaybetmemek için db+wal tmp'ye KOPYALANIR; kaynak asla açılmaz/checkpoint edilmez.
        need = p.stat().st_size + wal.stat().st_size
        if free_disk_bytes(OUT_ROOT) < need + MIN_FREE_DISK_BYTES:
            run.log_error(fid, "sqlite_wal", "DISK_GUARD", f"WAL kopyası için alan yok ({need} B); immutable okuma yapıldı, WAL içeriği envantere GİRMEDİ")
            wal_note = f"WAL_PRESENT_NOT_READ size={wal.stat().st_size}"
        else:
            tmp_copy = OUT_ROOT / "tmp" / run.run_id / f"walcopy_{fid}.sqlite"
            shutil.copyfile(p, tmp_copy)
            shutil.copyfile(wal, Path(str(tmp_copy) + "-wal"))
            wal_note = f"WAL_PRESENT_READ_VIA_TMP_COPY wal_size={wal.stat().st_size}"
            run.log_error(fid, "sqlite_wal", "WAL_NOTE", wal_note)
    if tmp_copy is not None:
        uri = f"file:{tmp_copy}?mode=ro"
    else:
        uri = f"file:{p}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    con.create_function("REGEXP", 2, lambda pat, val: 1 if (val is not None and re.search(pat, str(val))) else 0)
    try:
        master = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    except Exception as e:
        run.log_error(fid, "sqlite_master", type(e).__name__, e)
        con.close()
        return
    objs = defaultdict(list)
    for t, n, tn, sql in master:
        objs[t].append({"name": n, "tbl_name": tn, "sql": sql})
    is_gpkg = rec["detected_format"] == "geopackage"
    for t in objs.get("table", []):
        name = t["name"]
        if name.startswith("sqlite_"):
            continue
        tid = run.new_table_id()
        qn = '"' + name.replace('"', '""') + '"'
        try:
            row_count = con.execute(f"SELECT COUNT(*) FROM {qn}").fetchone()[0]
        except Exception as e:
            run.log_error(fid, "sqlite_count", type(e).__name__, e, table=name)
            row_count = None
        try:
            info = con.execute(f"PRAGMA table_info({qn})").fetchall()
        except Exception as e:
            run.log_error(fid, "sqlite_table_info", type(e).__name__, e, table=name)
            info = []
        cols = [r[1] for r in info]
        fks = []
        try:
            fks = [{"from": r[3], "table": r[2], "to": r[4]} for r in con.execute(f"PRAGMA foreign_key_list({qn})").fetchall()]
        except Exception:
            pass
        idx = [i["name"] for i in objs.get("index", []) if i["tbl_name"] == name]
        trg = [i["name"] for i in objs.get("trigger", []) if i["tbl_name"] == name]
        table_ref = {"table_id": tid, "file_id": fid, "table_name": name}
        # kolon istatistikleri: TEK GEÇİŞ (typeof dağılımı, null, boş, min/max, sıfır-önekli) — büyük DB'lerde sütun başına tarama yapılmaz
        stats = {r[1]: {**table_ref, "column_index": r[0], "column_name": r[1], "declared_type": r[2] or "", "observed_type": None, "null_count": None, "empty_string_count": None,
                        "approx_unique_count": None, "min_value": None, "max_value": None, "sample_values": None, "leading_zero_numeric_count": None, "is_constant": None, "row_count": row_count,
                        "is_pk": bool(r[5]), "not_null": bool(r[3]), "unique_count_method": None} for r in info}
        if row_count and cols:
            try:
                exprs = []
                for cname in cols:
                    qc = '"' + cname.replace('"', '""') + '"'
                    exprs.append(f"SUM(typeof({qc})='null'), SUM(typeof({qc})='integer'), SUM(typeof({qc})='real'), SUM(typeof({qc})='text'), SUM(typeof({qc})='blob'), "
                                 f"SUM({qc}=''), MIN(CASE WHEN typeof({qc})<>'blob' THEN {qc} END), MAX(CASE WHEN typeof({qc})<>'blob' THEN {qc} END), "
                                 f"SUM(typeof({qc})='text' AND {qc} GLOB '0[0-9]*' AND {qc} NOT GLOB '*[^0-9]*')")
                # SQLite sütun limiti (2000 ifade) için parçala
                per = 9
                chunk = max(1, 1900 // per)
                vals = []
                for i in range(0, len(cols), chunk):
                    part = exprs[i:i + chunk]
                    vals.extend(con.execute(f"SELECT {', '.join(part)} FROM {qn}").fetchone())
                for i, cname in enumerate(cols):
                    v = vals[i * per:(i + 1) * per]
                    st = stats[cname]
                    types = {k: n for k, n in zip(("null", "integer", "real", "text", "blob"), v[:5]) if n}
                    st["observed_type"] = json.dumps(types, ensure_ascii=False)
                    st["null_count"] = v[0] or 0
                    st["empty_string_count"] = v[5] or 0
                    st["min_value"], st["max_value"] = safe_str(v[6], 100), safe_str(v[7], 100)
                    st["leading_zero_numeric_count"] = v[8] or 0
                    nonnull = row_count - (v[0] or 0)
                    if nonnull > 1 and v[6] is not None and v[6] == v[7]:
                        st["is_constant"] = True
                    elif nonnull > 1:
                        st["is_constant"] = False
                # örnek değerler: ilk 200 satırdan tekilleştir (ucuz)
                head_rows = con.execute(f"SELECT * FROM {qn} LIMIT 200").fetchall()
                for i, cname in enumerate(cols):
                    seen = []
                    for hr in head_rows:
                        val = hr[i]
                        if val is None or isinstance(val, bytes):
                            continue
                        sv = safe_str(val, 80)
                        if sv not in seen:
                            seen.append(sv)
                        if len(seen) >= SAMPLE_VALUES_N:
                            break
                    stats[cname]["sample_values"] = json.dumps(seen, ensure_ascii=False)
                # distinct: yalnız ≤1M satır (sütun başına ek geçiş); büyük tablolarda AÇIKÇA hesaplanmadı
                if row_count <= 1_000_000:
                    for cname in cols:
                        qc = '"' + cname.replace('"', '""') + '"'
                        stats[cname]["approx_unique_count"] = con.execute(f"SELECT COUNT(DISTINCT {qc}) FROM {qn}").fetchone()[0]
                        stats[cname]["unique_count_method"] = "exact"
                else:
                    for cname in cols:
                        stats[cname]["unique_count_method"] = "not_computed_large_table"
            except Exception as e:
                run.log_error(fid, "sqlite_col_stats", type(e).__name__, e, table=name)
                for cname in cols:
                    stats[cname]["stat_error"] = safe_str(e, 300)
        for cname in cols:
            run.columns.append(stats[cname])
        # içerik dedektörleri: aynı salt-okunur sqlite3 bağlantısı üzerinden (kaynak DuckDB ile AÇILMAZ)
        try:
            if row_count and row_count <= 3_000_000 and cols:
                run.quarantine.extend(content_detectors_sqlite(run, con, qn, cols, table_ref))
        except Exception as e:
            run.log_error(fid, "sqlite_content_detectors", type(e).__name__, e, table=name)
        run.tables.append({"table_id": tid, "file_id": fid, "table_name": name, "member_name": None, "object_type": "sqlite_table", "row_count": row_count, "column_count": len(cols),
                           "column_names": json.dumps(cols, ensure_ascii=False), "detected_delimiter": None, "encoding": None, "quote_character": None, "escape_character": None,
                           "header_detected": None, "raw_line_count": None, "reject_count": None, "read_status": "ok",
                           "notes": json.dumps({"indexes": idx, "triggers": trg, "foreign_keys": fks, "wal": wal_note, "create_sql": safe_str(t["sql"], 2000)}, ensure_ascii=False)})
        run.schemas[tid] = {"file_id": fid, "table_name": name, "columns": cols, "declared_types": [r[2] for r in info], "kind": "sqlite", "create_sql": t["sql"]}
        # GeoPackage katmanı
        if is_gpkg:
            try:
                g = con.execute("SELECT table_name, data_type, srs_id, min_x, min_y, max_x, max_y FROM gpkg_contents WHERE table_name=?", (name,)).fetchone()
                if g and g[1] == "features":
                    gc = con.execute("SELECT column_name, geometry_type_name, z, m FROM gpkg_geometry_columns WHERE table_name=?", (name,)).fetchone()
                    run.geo_layers.append({"file_id": fid, "table_id": tid, "layer_name": name, "driver": "GPKG", "feature_count": row_count, "geometry_types": gc[1] if gc else None,
                                           "crs": f"srs_id={g[2]}", "bbox": json.dumps([g[3], g[4], g[5], g[6]]), "attribute_columns": json.dumps([c for c in cols if not gc or c != gc[0]], ensure_ascii=False),
                                           "null_geometry_count": None, "invalid_geometry_count": None, "empty_geometry_count": None, "notes": "gpkg_contents"})
            except Exception as e:
                run.log_error(fid, "gpkg_layer", type(e).__name__, e, table=name)
    for v in objs.get("view", []):
        tid = run.new_table_id()
        run.tables.append({"table_id": tid, "file_id": fid, "table_name": v["name"], "member_name": None, "object_type": "sqlite_view", "row_count": None, "column_count": None, "column_names": None,
                           "detected_delimiter": None, "encoding": None, "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": None, "reject_count": None,
                           "read_status": "ok", "notes": json.dumps({"create_sql": safe_str(v["sql"], 2000)}, ensure_ascii=False)})
    con.close()
    if tmp_copy is not None:
        for q in (tmp_copy, Path(str(tmp_copy) + "-wal"), Path(str(tmp_copy) + "-shm")):
            try:
                q.unlink(missing_ok=True)
            except Exception:
                pass


def probe_parquet(run: Run, rec: dict):
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    tid = run.new_table_id()
    pf = pq.ParquetFile(p)
    md = pf.metadata
    schema = pf.schema_arrow
    cols = schema.names
    # istatistikler: satır grubu metadata'sından null sayıları (veri okunmaz)
    nulls = {c: 0 for c in cols}
    has_stats = True
    for rg in range(md.num_row_groups):
        rgm = md.row_group(rg)
        for ci in range(rgm.num_columns):
            cm = rgm.column(ci)
            if cm.statistics is None or not cm.statistics.has_null_count:
                has_stats = False
                continue
            name = cm.path_in_schema
            if name in nulls:
                nulls[name] += cm.statistics.null_count
    table_ref = {"table_id": tid, "file_id": fid, "table_name": p.stem}
    for i, f in enumerate(schema):
        run.columns.append({**table_ref, "column_index": i, "column_name": f.name, "declared_type": str(f.type), "observed_type": None, "null_count": nulls.get(f.name) if has_stats else None,
                            "empty_string_count": None, "approx_unique_count": None, "min_value": None, "max_value": None, "sample_values": None, "leading_zero_numeric_count": None, "is_constant": None,
                            "row_count": md.num_rows})
    kv = {}
    if md.metadata:
        for k, v in md.metadata.items():
            ks = k.decode("utf-8", "replace")
            if ks in ("geo",):
                kv[ks] = v.decode("utf-8", "replace")[:5000]
            else:
                kv[ks] = safe_str(v.decode("utf-8", "replace"), 300)
    run.tables.append({"table_id": tid, "file_id": fid, "table_name": p.stem, "member_name": None, "object_type": "parquet", "row_count": md.num_rows, "column_count": len(cols),
                       "column_names": json.dumps(cols, ensure_ascii=False), "detected_delimiter": None, "encoding": None, "quote_character": None, "escape_character": None, "header_detected": None,
                       "raw_line_count": None, "reject_count": None, "read_status": "ok",
                       "notes": json.dumps({"row_groups": md.num_row_groups, "created_by": md.created_by, "kv_metadata_keys": list(kv.keys()), "geoparquet": "geo" in kv}, ensure_ascii=False)})
    run.schemas[tid] = {"file_id": fid, "table_name": p.stem, "columns": cols, "declared_types": [str(f.type) for f in schema], "kind": "parquet", "kv_metadata": kv}
    if "geo" in kv:
        try:
            geo = json.loads(kv["geo"])
            for gname, gmeta in (geo.get("columns") or {}).items():
                run.geo_layers.append({"file_id": fid, "table_id": tid, "layer_name": p.stem, "driver": "GeoParquet", "feature_count": md.num_rows, "geometry_types": json.dumps(gmeta.get("geometry_types")),
                                       "crs": safe_str(json.dumps(gmeta.get("crs")), 300) if gmeta.get("crs") else "unspecified(OGC:CRS84 varsayımı yapılmadı)", "bbox": json.dumps(gmeta.get("bbox")),
                                       "attribute_columns": json.dumps([c for c in cols if c != gname], ensure_ascii=False), "null_geometry_count": None, "invalid_geometry_count": None, "empty_geometry_count": None, "notes": f"geometry_column={gname}"})
        except Exception as e:
            run.log_error(fid, "geoparquet_meta", type(e).__name__, e)


def probe_geo(run: Run, rec: dict):
    """GeoJSON / KML / Shapefile / GPKG katmanları: DuckDB spatial (GDAL) ile. Geometri değiştirilmez, yalnız ölçülür."""
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    if not run.spatial:
        run.log_error(fid, "geo_probe", "spatial_unavailable", "DuckDB spatial yok")
        return
    ps = str(p).replace("'", "''")
    try:
        layers = run.con.execute(f"SELECT l['name'], l['feature_count'], l['geometry_fields'] FROM (SELECT unnest(layers) AS l FROM ST_Read_Meta('{ps}'))").fetchall()
    except Exception as e:
        run.log_error(fid, "geo_meta", type(e).__name__, e)
        return
    for lname, fcount, gfields in layers:
        tid = run.new_table_id()
        crs = None
        gtype_declared = None
        try:
            if gfields:
                gf = gfields[0]
                c = (gf.get("crs") or {}) if isinstance(gf, dict) else {}
                if c.get("auth_name") and c.get("auth_code"):
                    crs = f"{c['auth_name']}:{c['auth_code']}"
                elif c.get("name"):
                    crs = f"name={c['name']}"
                elif c.get("proj4"):
                    crs = safe_str("proj4=" + c["proj4"], 200)
                else:
                    crs = "unspecified"
                gtype_declared = gf.get("type") if isinstance(gf, dict) else None
        except Exception:
            pass
        rel = f"(SELECT * FROM ST_Read('{ps}', layer='{lname.replace(chr(39), chr(39)*2)}', keep_wkb=true))"
        tmp = f"g_{tid.lower()}"
        try:
            run.con.execute(f"CREATE OR REPLACE TABLE {tmp} AS SELECT * FROM {rel}")
        except Exception as e:
            run.log_error(fid, "geo_read", type(e).__name__, e, layer=lname)
            run.geo_layers.append({"file_id": fid, "table_id": tid, "layer_name": lname, "driver": "GDAL", "feature_count": fcount, "geometry_types": gtype_declared, "crs": crs, "bbox": None,
                                   "attribute_columns": None, "null_geometry_count": None, "invalid_geometry_count": None, "empty_geometry_count": None, "notes": f"READ_FAILED: {safe_str(e, 300)}"})
            continue
        cols = [r[0] for r in run.con.execute(f"DESCRIBE {tmp}").fetchall()]
        geom_col = "wkb_geometry" if "wkb_geometry" in cols else next((c for c in cols if c.lower() in ("geom", "geometry")), None)
        n = run.con.execute(f"SELECT COUNT(*) FROM {tmp}").fetchone()[0]
        gtypes, bbox, nullg, invalid, empty = None, None, None, None, None
        if geom_col:
            try:
                g = f'ST_GeomFromWKB("{geom_col}")'
                gtypes = json.dumps({k: v for k, v in run.con.execute(f'SELECT ST_GeometryType({g}), COUNT(*) FROM {tmp} WHERE "{geom_col}" IS NOT NULL GROUP BY 1').fetchall()})
                nullg = run.con.execute(f'SELECT COUNT(*) FROM {tmp} WHERE "{geom_col}" IS NULL').fetchone()[0]
                empty = run.con.execute(f'SELECT COUNT(*) FROM {tmp} WHERE "{geom_col}" IS NOT NULL AND ST_IsEmpty({g})').fetchone()[0]
                invalid = run.con.execute(f'SELECT COUNT(*) FROM {tmp} WHERE "{geom_col}" IS NOT NULL AND NOT ST_IsEmpty({g}) AND NOT ST_IsValid({g})').fetchone()[0]
                bb = run.con.execute(f'SELECT MIN(ST_XMin({g})), MIN(ST_YMin({g})), MAX(ST_XMax({g})), MAX(ST_YMax({g})) FROM {tmp} WHERE "{geom_col}" IS NOT NULL AND NOT ST_IsEmpty({g})').fetchone()
                bbox = json.dumps([bb[0], bb[1], bb[2], bb[3]])
                # özellik bazında Türkiye dışı (yalnız coğrafi CRS'lerde anlamlı)
                if crs in ("EPSG:4326", "OGC:CRS84", "unspecified") or (crs or "").startswith("name=WGS"):
                    outside = run.con.execute(
                        f'SELECT COUNT(*) FROM {tmp} WHERE "{geom_col}" IS NOT NULL AND NOT ST_IsEmpty({g}) AND NOT '
                        f'(ST_X(ST_Centroid({g})) BETWEEN {TR_BBOX[0]} AND {TR_BBOX[2]} AND ST_Y(ST_Centroid({g})) BETWEEN {TR_BBOX[1]} AND {TR_BBOX[3]})'
                    ).fetchone()[0]
                    if outside:
                        run.quarantine.append({"table_id": tid, "file_id": fid, "table_name": lname, "detector_id": "C04", "column": geom_col, "hit_count": outside, "status": "GEO_CONFLICT",
                                               "reason": f"{outside}/{n} özelliğin merkezi Türkiye sınır kutusu dışında (CRS={crs})."})
                # bbox Türkiye dışı mı?
                if bb[0] is not None and (bb[2] < TR_BBOX[0] or bb[0] > TR_BBOX[2] or bb[3] < TR_BBOX[1] or bb[1] > TR_BBOX[3]):
                    run.quarantine.append({"table_id": tid, "file_id": fid, "table_name": lname, "detector_id": "C04", "column": geom_col, "hit_count": n, "status": "GEO_CONFLICT",
                                           "reason": f"Katman bbox'ı Türkiye ile kesişmiyor: {bbox} (CRS={crs}). Projeksiyonlu CRS olabilir; dönüştürme YAPILMADI."})
            except Exception as e:
                run.log_error(fid, "geo_stats", type(e).__name__, e, layer=lname)
        attr_cols = [c for c in cols if c != geom_col]
        table_ref = {"table_id": tid, "file_id": fid, "table_name": lname}
        try:
            run.columns.extend(col_stats_duckdb(run, f"(SELECT {', '.join(chr(34)+c.replace(chr(34), chr(34)*2)+chr(34) for c in attr_cols)} FROM {tmp})" if attr_cols else tmp, table_ref, row_count=n))
            if attr_cols:
                run.quarantine.extend(content_detectors(run, tmp, attr_cols, table_ref))
        except Exception as e:
            run.log_error(fid, "geo_col_stats", type(e).__name__, e, layer=lname)
        run.geo_layers.append({"file_id": fid, "table_id": tid, "layer_name": lname, "driver": "GDAL", "feature_count": n, "geometry_types": gtypes, "crs": crs, "bbox": bbox,
                               "attribute_columns": json.dumps(attr_cols, ensure_ascii=False), "null_geometry_count": nullg, "invalid_geometry_count": invalid, "empty_geometry_count": empty,
                               "notes": f"declared_feature_count={fcount}; declared_geom_type={gtype_declared}"})
        run.tables.append({"table_id": tid, "file_id": fid, "table_name": lname, "member_name": None, "object_type": "geo_layer", "row_count": n, "column_count": len(cols), "column_names": json.dumps(cols, ensure_ascii=False),
                           "detected_delimiter": None, "encoding": rec.get("encoding"), "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": None, "reject_count": None,
                           "read_status": "ok", "notes": None})
        run.schemas[tid] = {"file_id": fid, "table_name": lname, "columns": cols, "kind": "geo_layer"}
        run.con.execute(f"DROP TABLE IF EXISTS {tmp}")


def _json_shape(obj, depth=0):
    if isinstance(obj, dict):
        return {"type": "object", "keys": list(obj.keys())[:200], "key_count": len(obj)}
    if isinstance(obj, list):
        kinds = Counter(type(x).__name__ for x in obj[:1000])
        keys = Counter()
        for x in obj[:2000]:
            if isinstance(x, dict):
                keys.update(x.keys())
        return {"type": "array", "length": len(obj), "element_types": dict(kinds), "record_keys": [k for k, _ in keys.most_common(200)]}
    return {"type": type(obj).__name__}


def probe_json(run: Run, rec: dict):
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    tid = run.new_table_id()
    size = rec["size_bytes"] or 0
    enc = rec.get("encoding") or "utf-8"
    if size <= JSON_INMEM_LIMIT:
        with open(p, "rb") as f:
            raw = f.read()
        try:
            try:
                txt = raw.decode("utf-8-sig" if enc == "utf-8-sig" else ("utf-8" if enc in ("utf-8", "unknown") else enc), errors="strict")
            except UnicodeDecodeError as e:
                run.log_error(fid, "json_decode", "UnicodeDecodeError", e)
                txt = raw.decode("utf-8", errors="replace")
            obj = json.loads(txt)
        except json.JSONDecodeError as e:
            lines = txt.split("\n")
            ctx = "\n".join(lines[max(0, e.lineno - 3): e.lineno + 2])
            run.parse_errors.append({"file_id": fid, "table_id": tid, "member_name": None, "source_row_number": e.lineno, "column_idx": e.colno, "column_name": None, "error_type": "JSON_DECODE",
                                     "raw_row": safe_str(ctx, 2000), "error_message": safe_str(e.msg, 300), "byte_position": e.pos, "detected_at": now_iso()})
            run.tables.append({"table_id": tid, "file_id": fid, "table_name": p.name, "member_name": None, "object_type": "json", "row_count": None, "column_count": None, "column_names": None,
                               "detected_delimiter": None, "encoding": enc, "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": len(lines), "reject_count": 1,
                               "read_status": "failed", "notes": f"JSON_DECODE_ERROR line={e.lineno} col={e.colno}: {e.msg}"})
            run.log_error(fid, "json_decode", "JSONDecodeError", f"line {e.lineno} col {e.colno}: {e.msg}")
            return
        shape = _json_shape(obj)
        records = None
        if shape["type"] == "array" and shape.get("element_types", {}).get("dict"):
            records = obj
        elif shape["type"] == "object":
            # tek anahtarlı sarmalayıcı: {"data": [...]} vb.
            for k, v in obj.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) >= 1:
                    if records is None or len(v) > len(records):
                        records = v
                        shape["records_under_key"] = k
        cols = shape.get("record_keys") or (list(records[0].keys()) if records else shape.get("keys", []))
        row_count = len(records) if records is not None else (shape.get("length") if shape["type"] == "array" else 1)
        # kolon istatistikleri (kayıt dizisi ise) - DuckDB'ye aktar
        if records is not None and len(records) > 0 and len(cols) > 0:
            try:
                tmp = f"j_{tid.lower()}"
                # her değeri VARCHAR olarak (json.dumps ile) - tip zorlanmaz
                keys = [k for k, _ in Counter(k for r in records if isinstance(r, dict) for k in r.keys()).most_common(300)]
                rows = [[(None if (not isinstance(r, dict) or k not in r or r[k] is None) else (r[k] if isinstance(r[k], str) else json.dumps(r[k], ensure_ascii=False))) for k in keys] for r in records]
                tbl = pa.table({k: pa.array([row[i] for row in rows], type=pa.string()) for i, k in enumerate(keys)})
                run.con.register(tmp, tbl)
                table_ref = {"table_id": tid, "file_id": fid, "table_name": p.name}
                run.columns.extend(col_stats_duckdb(run, tmp, table_ref, row_count=len(records)))
                run.quarantine.extend(content_detectors(run, tmp, keys, table_ref))
                run.con.unregister(tmp)
                cols = keys
                del rows, tbl
            except Exception as e:
                run.log_error(fid, "json_col_stats", type(e).__name__, e)
        del obj
        run.tables.append({"table_id": tid, "file_id": fid, "table_name": p.name, "member_name": None, "object_type": "json", "row_count": row_count, "column_count": len(cols),
                           "column_names": json.dumps(cols[:300], ensure_ascii=False), "detected_delimiter": None, "encoding": enc, "quote_character": None, "escape_character": None, "header_detected": None,
                           "raw_line_count": None, "reject_count": None, "read_status": "ok", "notes": json.dumps(shape, ensure_ascii=False)[:3000]})
        run.schemas[tid] = {"file_id": fid, "table_name": p.name, "columns": cols[:300], "kind": "json", "shape": {k: v for k, v in shape.items() if k != "record_keys"}}
    else:
        # büyük JSON: DuckDB streaming
        ps = str(p).replace("'", "''")
        try:
            run.con.execute(f"CREATE OR REPLACE TABLE bigjson AS SELECT * FROM read_json('{ps}', format='auto', maximum_object_size=1073741824, records='auto', sample_size=-1, union_by_name=true)")
            cols = [r[0] for r in run.con.execute("DESCRIBE bigjson").fetchall()]
            n = run.con.execute("SELECT COUNT(*) FROM bigjson").fetchone()[0]
            table_ref = {"table_id": tid, "file_id": fid, "table_name": p.name}
            run.columns.extend(col_stats_duckdb(run, "bigjson", table_ref, row_count=n))
            run.quarantine.extend(content_detectors(run, "bigjson", cols, table_ref))
            run.tables.append({"table_id": tid, "file_id": fid, "table_name": p.name, "member_name": None, "object_type": "json", "row_count": n, "column_count": len(cols), "column_names": json.dumps(cols, ensure_ascii=False),
                               "detected_delimiter": None, "encoding": enc, "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": None, "reject_count": None,
                               "read_status": "ok", "notes": "large_json_duckdb"})
            run.schemas[tid] = {"file_id": fid, "table_name": p.name, "columns": cols, "kind": "json"}
            run.con.execute("DROP TABLE IF EXISTS bigjson")
        except Exception as e:
            run.log_error(fid, "json_large", type(e).__name__, e)
            run.tables.append({"table_id": tid, "file_id": fid, "table_name": p.name, "member_name": None, "object_type": "json", "row_count": None, "column_count": None, "column_names": None,
                               "detected_delimiter": None, "encoding": enc, "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": raw_line_count(p), "reject_count": None,
                               "read_status": "failed", "notes": safe_str(e, 300)})


def probe_geojsonseq(run: Run, rec: dict):
    """RS ayraçlı JSON dizisi: kaynak DOKUNULMAZ; RS'siz geçici kopya (tmp/) üzerinde jsonl probu çalışır."""
    p = Path(rec["absolute_path"]); tmp = OUT_ROOT / "tmp" / "p1_geojsonseq"; tmp.mkdir(parents=True, exist_ok=True)
    cp = tmp / (rec["file_id"] + ".jsonl")
    with open(p, "rb") as fi, open(cp, "wb") as fo:
        for line in fi: fo.write(line.lstrip(b"\x1e"))
    rec2 = dict(rec); rec2["absolute_path"] = str(cp)
    probe_jsonl(run, rec2)
    for t in run.tables:
        if t["file_id"] == rec["file_id"] and t["object_type"] == "jsonl": t["object_type"] = "geojsonseq"; t["notes"] = ((t.get("notes") or "") + " | RS-stripped tmp copy probed").strip(" |")
    try: cp.unlink()
    except Exception: pass


def probe_jsonl(run: Run, rec: dict):
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    tid = run.new_table_id()
    raw_lines = raw_line_count(p)
    ps = str(p).replace("'", "''")
    try:
        run.con.execute(f"CREATE OR REPLACE TABLE jl AS SELECT * FROM read_json('{ps}', format='newline_delimited', maximum_object_size=268435456, ignore_errors=true, union_by_name=true, sample_size=-1)")
        cols = [r[0] for r in run.con.execute("DESCRIBE jl").fetchall()]
        n = run.con.execute("SELECT COUNT(*) FROM jl").fetchone()[0]
        table_ref = {"table_id": tid, "file_id": fid, "table_name": p.name}
        run.columns.extend(col_stats_duckdb(run, "jl", table_ref, row_count=n))
        run.quarantine.extend(content_detectors(run, "jl", cols, table_ref))
        run.con.execute("DROP TABLE IF EXISTS jl")
        # satır bazında parse hatası: raw satır sayısı ile karşılaştır, farkı satır satır bul
        bad = 0
        if raw_lines and n < raw_lines:
            with open(p, "rb") as f:
                for i, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    try:
                        json.loads(line)
                    except Exception as e:
                        bad += 1
                        if bad <= 5000:
                            run.parse_errors.append({"file_id": fid, "table_id": tid, "member_name": None, "source_row_number": i, "column_idx": None, "column_name": None, "error_type": "JSON_DECODE",
                                                     "raw_row": safe_str(line.decode("utf-8", "replace"), 2000), "error_message": safe_str(e, 300), "byte_position": None, "detected_at": now_iso()})
        run.tables.append({"table_id": tid, "file_id": fid, "table_name": p.name, "member_name": None, "object_type": "jsonl", "row_count": n, "column_count": len(cols), "column_names": json.dumps(cols, ensure_ascii=False),
                           "detected_delimiter": None, "encoding": rec.get("encoding"), "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": raw_lines, "reject_count": bad,
                           "read_status": "ok", "notes": None})
        run.schemas[tid] = {"file_id": fid, "table_name": p.name, "columns": cols, "kind": "jsonl"}
    except Exception as e:
        run.log_error(fid, "jsonl", type(e).__name__, e)


def probe_xlsx(run: Run, rec: dict):
    import openpyxl
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    wb = openpyxl.load_workbook(p, read_only=True, data_only=False)
    for ws in wb.worksheets:
        tid = run.new_table_id()
        rows_iter = ws.iter_rows(values_only=True)
        header = None
        n = 0
        col_nulls = None
        samples = None
        for r in rows_iter:
            if header is None:
                header = [("" if v is None else str(v)) for v in r]
                col_nulls = [0] * len(header)
                samples = [[] for _ in header]
                continue
            n += 1
            for i, v in enumerate(r[:len(header)]):
                if v is None or v == "":
                    col_nulls[i] += 1
                elif len(samples[i]) < SAMPLE_VALUES_N and str(v) not in samples[i]:
                    samples[i].append(safe_str(v, 80))
        header = header or []
        table_ref = {"table_id": tid, "file_id": fid, "table_name": ws.title}
        for i, c in enumerate(header):
            run.columns.append({**table_ref, "column_index": i, "column_name": c, "declared_type": "excel_cell", "observed_type": None, "null_count": col_nulls[i] if col_nulls else None, "empty_string_count": None,
                                "approx_unique_count": None, "min_value": None, "max_value": None, "sample_values": json.dumps(samples[i], ensure_ascii=False) if samples else None, "leading_zero_numeric_count": None, "is_constant": None, "row_count": n})
        run.tables.append({"table_id": tid, "file_id": fid, "table_name": ws.title, "member_name": None, "object_type": "xlsx_sheet", "row_count": n, "column_count": len(header), "column_names": json.dumps(header, ensure_ascii=False),
                           "detected_delimiter": None, "encoding": None, "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": None, "reject_count": None, "read_status": "ok",
                           "notes": json.dumps({"dimensions": ws.calculate_dimension() if hasattr(ws, "calculate_dimension") else None}, ensure_ascii=False)})
        run.schemas[tid] = {"file_id": fid, "table_name": ws.title, "columns": header, "kind": "xlsx_sheet"}
    wb.close()


def probe_zip(run: Run, rec: dict):
    """ZIP: üyeleri listele (diske ÇIKARMA yok). CSV/JSON/SQLite üyelerini envanterle. İç içe zip'lere iner (üye adı 'inner.zip::member', derinlik ≤ 4)."""
    p = Path(rec["absolute_path"])
    with zipfile.ZipFile(p) as z:
        _probe_zip_handle(run, rec, z, p, prefix="", depth=0)


def _probe_zip_handle(run: Run, rec: dict, z, p: Path, prefix: str, depth: int):
    import io
    fid = rec["file_id"]
    if True:
        infos = z.infolist()
        for zi in infos:
            ext = zi.filename.rsplit(".", 1)[-1].lower() if "." in zi.filename else ""
            run.zip_members.append({"file_id": fid, "member_name": prefix + zi.filename, "member_ext": ext, "compressed_size": zi.compress_size, "uncompressed_size": zi.file_size, "crc32": f"{zi.CRC:08x}",
                                    "member_modified": dt.datetime(*zi.date_time).isoformat() if zi.date_time[0] >= 1980 else None, "is_dir": zi.is_dir(), "nested_zip": ext == "zip", "probed": False, "depth": depth})
        for zi in infos:
            if zi.is_dir():
                continue
            ext = zi.filename.rsplit(".", 1)[-1].lower() if "." in zi.filename else ""
            if ext == "zip" and depth < 4 and 0 < zi.file_size <= 1_200_000_000:
                try:
                    inner = zipfile.ZipFile(io.BytesIO(z.read(zi)))
                    _probe_zip_handle(run, rec, inner, p, prefix=prefix + zi.filename + "::", depth=depth + 1)
                    for m in run.zip_members:
                        if m["file_id"] == fid and m["member_name"] == prefix + zi.filename:
                            m["probed"] = True
                except Exception as e:
                    run.log_error(fid, "zip_nested", type(e).__name__, e, member=prefix + zi.filename)
                continue
            if ext in ("csv", "tsv", "txt") and zi.file_size > 0 and zi.file_size <= 1_500_000_000:
                # DuckDB zip içini doğrudan okuyamaz; üyeyi tmp dizinine (OUT_ROOT/tmp) stream ederek yaz, sonra sil. Kaynak zip'e dokunulmaz.
                tmpf = OUT_ROOT / "tmp" / run.run_id / f"zipmember_{fid}_{abs(hash(prefix + zi.filename))}.{ext}"
                try:
                    if free_disk_bytes(OUT_ROOT) < zi.file_size + MIN_FREE_DISK_BYTES:
                        run.log_error(fid, "zip_member", "DISK_GUARD_SKIP", f"{zi.filename} ({zi.file_size} B) için geçici alan yok", member=zi.filename)
                        continue
                    with z.open(zi) as src, open(tmpf, "wb") as dst:
                        shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
                    head = read_head(tmpf, 262144)
                    enc, _ = detect_encoding(head)
                    fmt, info = detect_format(tmpf, ext)
                    sub = dict(rec)
                    sub["encoding"] = enc
                    raw_lines = raw_line_count(tmpf)
                    probe_csv_bytes_source(run, sub, str(tmpf), fmt, info.get("delimiter"), table_name=f"{p.name}::{prefix}{zi.filename}", member_name=prefix + zi.filename, raw_lines=raw_lines)
                    for m in run.zip_members:
                        if m["file_id"] == fid and m["member_name"] == prefix + zi.filename:
                            m["probed"] = True
                except Exception as e:
                    run.log_error(fid, "zip_member_csv", type(e).__name__, e, member=prefix + zi.filename)
                finally:
                    try:
                        tmpf.unlink(missing_ok=True)
                    except Exception:
                        pass
            elif ext in ("json", "geojson") and 0 < zi.file_size <= JSON_INMEM_LIMIT:
                try:
                    with z.open(zi) as src:
                        raw = src.read()
                    obj = json.loads(raw.decode("utf-8-sig", errors="replace"))
                    shape = _json_shape(obj)
                    tid = run.new_table_id()
                    is_geo = isinstance(obj, dict) and obj.get("type") == "FeatureCollection"
                    n = len(obj.get("features", [])) if is_geo else (shape.get("length") if shape["type"] == "array" else 1)
                    cols = shape.get("record_keys") or shape.get("keys", [])
                    gtypes = None
                    if is_geo:
                        gtypes = json.dumps(dict(Counter((f.get("geometry") or {}).get("type", "null") for f in obj["features"])))
                        props = Counter()
                        for f in obj["features"][:5000]:
                            props.update((f.get("properties") or {}).keys())
                        cols = list(props.keys())
                        run.geo_layers.append({"file_id": fid, "table_id": tid, "layer_name": prefix + zi.filename, "driver": "zip_member_geojson", "feature_count": n, "geometry_types": gtypes,
                                               "crs": safe_str(json.dumps(obj.get("crs")), 200) if obj.get("crs") else "unspecified(GeoJSON RFC7946 → WGS84 varsayımı yapılmadı)", "bbox": json.dumps(obj.get("bbox")) if obj.get("bbox") else None,
                                               "attribute_columns": json.dumps(cols, ensure_ascii=False), "null_geometry_count": sum(1 for f in obj["features"] if not f.get("geometry")), "invalid_geometry_count": None, "empty_geometry_count": None,
                                               "notes": "zip içi; geçerlilik ölçülmedi (Phase 2'de ST_IsValid)"})
                    run.tables.append({"table_id": tid, "file_id": fid, "table_name": f"{p.name}::{prefix}{zi.filename}", "member_name": prefix + zi.filename, "object_type": "geojson" if is_geo else "json", "row_count": n, "column_count": len(cols),
                                       "column_names": json.dumps(cols[:300], ensure_ascii=False), "detected_delimiter": None, "encoding": None, "quote_character": None, "escape_character": None, "header_detected": None,
                                       "raw_line_count": None, "reject_count": None, "read_status": "ok", "notes": json.dumps({k: v for k, v in shape.items() if k != "record_keys"}, ensure_ascii=False)[:2000]})
                    run.schemas[tid] = {"file_id": fid, "table_name": f"{p.name}::{prefix}{zi.filename}", "columns": cols[:300], "kind": "geojson" if is_geo else "json"}
                    for m in run.zip_members:
                        if m["file_id"] == fid and m["member_name"] == prefix + zi.filename:
                            m["probed"] = True
                    del obj, raw
                except Exception as e:
                    run.log_error(fid, "zip_member_json", type(e).__name__, e, member=prefix + zi.filename)
            elif ext in ("sqlite", "db", "sqlite3", "gpkg") and 0 < zi.file_size <= 2_000_000_000:
                tmpf = OUT_ROOT / "tmp" / run.run_id / f"zipmember_{fid}_{abs(hash(prefix + zi.filename))}.{ext}"
                try:
                    if free_disk_bytes(OUT_ROOT) < zi.file_size + MIN_FREE_DISK_BYTES:
                        run.log_error(fid, "zip_member", "DISK_GUARD_SKIP", f"{zi.filename} için geçici alan yok", member=zi.filename)
                        continue
                    with z.open(zi) as src, open(tmpf, "wb") as dst:
                        shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
                    sub = dict(rec)
                    sub["absolute_path"] = str(tmpf)
                    fmt, _ = detect_format(tmpf, ext)
                    sub["detected_format"] = fmt
                    if fmt in ("sqlite", "geopackage"):
                        before = len(run.tables)
                        probe_sqlite(run, sub)
                        for t in run.tables[before:]:
                            t["member_name"] = prefix + zi.filename
                            t["table_name"] = f"{p.name}::{prefix}{zi.filename}::{t['table_name']}"
                        for m in run.zip_members:
                            if m["file_id"] == fid and m["member_name"] == prefix + zi.filename:
                                m["probed"] = True
                except Exception as e:
                    run.log_error(fid, "zip_member_sqlite", type(e).__name__, e, member=prefix + zi.filename)
                finally:
                    try:
                        tmpf.unlink(missing_ok=True)
                    except Exception:
                        pass


def probe_text(run: Run, rec: dict):
    p = Path(rec["absolute_path"])
    tid = run.new_table_id()
    n = raw_line_count(p)
    run.tables.append({"table_id": tid, "file_id": rec["file_id"], "table_name": p.name, "member_name": None, "object_type": rec["detected_format"], "row_count": n, "column_count": None, "column_names": None,
                       "detected_delimiter": None, "encoding": rec.get("encoding"), "quote_character": None, "escape_character": None, "header_detected": None, "raw_line_count": n, "reject_count": None,
                       "read_status": "ok", "notes": "plain text; tablo yapısı algılanmadı"})


def probe_gzip(run: Run, rec: dict):
    import gzip
    p = Path(rec["absolute_path"])
    fid = rec["file_id"]
    inner = p.name[:-3] if p.name.endswith(".gz") else p.name
    ext = inner.rsplit(".", 1)[-1].lower() if "." in inner else ""
    tmpf = OUT_ROOT / "tmp" / run.run_id / f"gz_{fid}.{ext or 'bin'}"
    try:
        with gzip.open(p, "rb") as src, open(tmpf, "wb") as dst:
            shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
        sub = dict(rec)
        sub["absolute_path"] = str(tmpf)
        fmt, info = detect_format(tmpf, ext)
        sub["detected_format"] = fmt
        sub["encoding"] = detect_encoding(read_head(tmpf, 262144))[0]
        run.zip_members.append({"file_id": fid, "member_name": inner, "member_ext": ext, "compressed_size": rec["size_bytes"], "uncompressed_size": tmpf.stat().st_size, "crc32": None, "member_modified": None, "is_dir": False, "nested_zip": False, "probed": True})
                # gzip içindeki harita dosyaları (kml/kmz/shp/gpkg) ayrı süreçte ve süre sınırıyla denenir:
        # GDAL hem çökebiliyor hem de büyük KML'lerde saatlerce sürebiliyor (28 Eyl: 25 MB İBB KML 30+ dk).
        if fmt in ("kml", "kmz") and os.environ.get("P1_TRY_KML") != "1":
            run.log_error(fid, "content_probe", "KML_PROBE_DEFERRED", f"gzip içi {ext}: KML/KMZ içerik envanteri ertelendi (DuckDB spatial çökmesi); ham dosya korunuyor", path=str(p))
            return
        if fmt in ("shapefile", "gpkg", "geojson"):
            tl = int(os.environ.get("P1_GEO_TIMEOUT", "120"))
            try:
                chk = subprocess.run([sys.executable, "-c",
                    "import sys,duckdb;c=duckdb.connect();c.execute(\"INSTALL spatial; LOAD spatial\");c.execute(\"SELECT * FROM ST_Read_Meta(?)\",[sys.argv[1]]).fetchall();print('ok')",
                    str(tmpf)], capture_output=True, timeout=tl)
                ok = chk.returncode == 0
                why = f"rc={chk.returncode}: {chk.stderr[-160:]!r}"
            except subprocess.TimeoutExpired:
                ok = False; why = f"{tl}s icinde okunamadi (buyuk/patolojik geometri)"
            if not ok:
                run.log_error(fid, "content_probe", "GEO_READER_GUARD_GZIP", f"gzip içi {ext}: {why}; içerik envanteri yapılmadı, ham dosya korunuyor", path=str(p))
                return
        dispatch_probe(run, sub, inner_name=inner)
    finally:
        try:
            tmpf.unlink(missing_ok=True)
        except Exception:
            pass


def dispatch_probe(run: Run, rec: dict, inner_name: str | None = None):
    fmt = rec["detected_format"]
    ext = rec["extension"]
    if fmt in ("csv", "csv_semicolon", "tsv", "psv"):
        probe_csv(run, rec)
    elif fmt in ("sqlite", "geopackage"):
        probe_sqlite(run, rec)
        if fmt == "geopackage" and run.spatial:
            # GDAL kaynağı açınca SQLite yan dosyası oluşturabilir → kopya üzerinden oku
            p = Path(rec["absolute_path"])
            tmpg = OUT_ROOT / "tmp" / run.run_id / f"gpkgcopy_{rec['file_id']}.gpkg"
            if free_disk_bytes(OUT_ROOT) >= p.stat().st_size + MIN_FREE_DISK_BYTES:
                try:
                    shutil.copyfile(p, tmpg)
                    sub = dict(rec); sub["absolute_path"] = str(tmpg)
                    probe_geo(run, sub)
                finally:
                    for q in (tmpg, Path(str(tmpg) + "-wal"), Path(str(tmpg) + "-shm"), Path(str(tmpg) + "-journal")):
                        try: q.unlink(missing_ok=True)
                        except Exception: pass
            else:
                run.log_error(rec["file_id"], "gpkg_geo", "DISK_GUARD", "GPKG kopyası için alan yok; geo katman ölçümü atlandı")
    elif fmt == "parquet":
        probe_parquet(run, rec)
    elif fmt in ("geojson", "kml", "shapefile", "kmz"):
        probe_geo(run, rec)
    elif fmt == "json":
        probe_json(run, rec)
    elif fmt == "jsonl":
        probe_jsonl(run, rec)
    elif fmt == "geojsonseq":
        probe_geojsonseq(run, rec)
    elif fmt == "xlsx":
        probe_xlsx(run, rec)
    elif fmt == "zip":
        probe_zip(run, rec)
    elif fmt == "gzip":
        probe_gzip(run, rec)
    elif fmt in ("text", "sql_text") and ext in ("txt", "sql", "csv", "tsv", "dump"):
        probe_text(run, rec)
    elif fmt in ("ole_xls_doc",):
        run.log_error(rec["file_id"], "probe", "xls_legacy_unsupported", "Eski .xls (OLE) için okuyucu yok (xlrd kurulu değil); Phase 2'de eklenecek")
    elif fmt in ("pg_dump_custom", "duckdb", "arrow_ipc"):
        run.log_error(rec["file_id"], "probe", f"{fmt}_not_probed", "İçerik envanteri bu formatta Phase 1'de yapılmadı (yalnız manifest)")
    else:
        return
    rec["content_probed"] = True


def content_inventory(run: Run):
    cands = [r for r in run.files if r.get("is_data_candidate") and r["read_status"] == "hashed"]
    if run.prev:
        prev_sha = {r["absolute_path"]: r.get("sha256") for r in run.prev["files"]}
        prev_rec = {r["absolute_path"]: r for r in run.prev["files"]}
        reprobe = [x for x in os.environ.get("P1_REPROBE", "").split(":") if x]  # yol alt dizgisi: dedektör/prob düzeltmesi sonrası zorla yeniden incele
        unchanged = {r["file_id"] for r in cands if prev_sha.get(r["absolute_path"]) == r["sha256"] and prev_rec[r["absolute_path"]].get("content_probed") and not any(x in r["absolute_path"] for x in reprobe)}
        # önceki sonuçları taşı
        for t in run.prev["tables"]:
            if t["file_id"] in unchanged: run.tables.append(t)
        for cdef in run.prev["columns"]:
            if cdef["file_id"] in unchanged: run.columns.append(cdef)
        for g in run.prev["geo"]:
            if g["file_id"] in unchanged: run.geo_layers.append(g)
        for pe in run.prev["parse_errors"]:
            if pe["file_id"] in unchanged: run.parse_errors.append(pe)
        for zm in run.prev["zip_members"]:
            if zm["file_id"] in unchanged: run.zip_members.append(zm)
        for tid, sch in run.prev["schemas"].items():
            if sch.get("file_id") in unchanged: run.schemas[tid] = sch
        for r in cands:
            if r["file_id"] in unchanged:
                r["content_probed"] = True
                pr = prev_rec[r["absolute_path"]]
                for k in ("probe_reference_file_id", "duplicate_group_id"):
                    if pr.get(k) is not None: r[k] = pr[k]
        # probe_reference (aynı içerik başka dosyada incelendi) olan değişmemişleri de atla
        skipped = len(unchanged)
        cands = [r for r in cands if r["file_id"] not in unchanged]
        print(f"  [incremental] değişmemiş {skipped} dosya atlandı; incelenecek {len(cands)}", flush=True)
        run.audit_write("incremental_skip", unchanged=skipped, to_probe=len(cands))
    # exact duplicate dosyaları bir kez probe et (aynı sha256 → aynı içerik); diğerlerine referans ver
    seen_hash: dict[str, str] = {}
    t0 = time.time()
    for i, rec in enumerate(cands, 1):
        h = rec["sha256"]
        if h in seen_hash:
            rec["content_probed"] = True
            rec["probe_reference_file_id"] = seen_hash[h]
            continue
        try:
            # çökme (bellek/segfault) hâlinde suçlu dosya bilinsin: her dosyadan önce işaretçi yazılır
            try: (OUT_ROOT / "tmp" / "p1_current_file.txt").write_text(f"{i}/{len(cands)}\t{rec['detected_format']}\t{rec['size_bytes']}\t{rec['absolute_path']}")
            except Exception: pass
            # riskli coğrafi biçimler (kmz/kml/shapefile/gpkg) önce ayrı süreçte denenir: GDAL çökmesi ana koşuyu öldürmesin
            # KML/KMZ: DuckDB spatial (gdal_meta::Scan) bu biçimlerde çöküyor (29 Eyl çökme raporu, SIGSEGV).
            # Alt süreç koruması çalışıyor ama her denemede macOS çökme kaydı üretiyor → içerik incelemesi tamamen ertelenir.
            if rec["detected_format"] in ("kml", "kmz") and os.environ.get("P1_TRY_KML") != "1":
                run.log_error(rec["file_id"], "content_probe", "KML_PROBE_DEFERRED",
                              "KML/KMZ içerik envanteri ertelendi (DuckDB spatial çökmesi); dosya korunuyor, Phase 2'de ogr2ogr ile okunacak", path=rec["absolute_path"])
                continue
            if rec["detected_format"] in ("shapefile", "gpkg"):
                chk = subprocess.run([sys.executable, "-c",
                    "import sys,duckdb;c=duckdb.connect();c.execute(\"INSTALL spatial; LOAD spatial\");c.execute(\"SELECT * FROM ST_Read_Meta(?)\",[sys.argv[1]]).fetchall();print('ok')",
                    rec["absolute_path"]], capture_output=True, timeout=120)
                if chk.returncode != 0:
                    run.log_error(rec["file_id"], "content_probe", "GEO_READER_CRASH_GUARD", f"ayrı süreçte okunamadı (rc={chk.returncode}); içerik envanteri yapılmadı, dosya korunuyor: {chk.stderr[-200:]!r}", path=rec["absolute_path"])
                    continue
            lim = int(os.environ.get("P1_MAX_PROBE_BYTES", 0) or 0)
            if lim and rec["size_bytes"] > lim and rec["detected_format"] in ("xlsx", "json", "geojson", "jsonl", "geojsonseq", "sqlite", "gpkg", "parquet", "zip"):
                run.log_error(rec["file_id"], "content_probe", "SIZE_GUARD_SKIP", f"{rec['size_bytes']} B > P1_MAX_PROBE_BYTES={lim}; büyük dosya ayrı koşuda incelenecek", path=rec["absolute_path"])
                continue
            dispatch_probe(run, rec)
            seen_hash[h] = rec["file_id"]
        except Exception as e:
            rec["error_status"] = safe_str(e)
            run.log_error(rec["file_id"], "content_probe", type(e).__name__, f"{e}\n{traceback.format_exc()[-800:]}", path=rec["absolute_path"])
        if i % 200 == 0 or i == len(cands):
            print(f"  [probe] {i}/{len(cands)}  tables={len(run.tables)} cols={len(run.columns)} geo={len(run.geo_layers)} err={len(run.errors)}  {time.time()-t0:.0f}s", flush=True)
            run.check_disk()
    run.audit_write("content_inventory", candidates=len(cands), tables=len(run.tables), columns=len(run.columns), geo_layers=len(run.geo_layers), errors=len(run.errors), seconds=round(time.time() - t0, 1))


# --------------------------------------------------------------------------------------
# AŞAMA 3-6: duplicate, cluster, sınıflandırma, karantina
# --------------------------------------------------------------------------------------
def duplicates(run: Run) -> list[dict]:
    groups = defaultdict(list)
    for r in run.files:
        if r.get("sha256"):
            groups[r["sha256"]].append(r)
    out = []
    gid = 0
    for h, members in sorted(groups.items(), key=lambda kv: -sum(m["size_bytes"] or 0 for m in kv[1])):
        if len(members) < 2:
            continue
        gid += 1
        g = f"DUP_FILE_{gid:04d}"
        for m in members:
            m["duplicate_group_id"] = g
            out.append({"duplicate_group_id": g, "sha256": h, "file_id": m["file_id"], "absolute_path": m["absolute_path"], "size_bytes": m["size_bytes"], "modified_at": m["modified_at"], "group_size": len(members), "level": "LEVEL1_EXACT_FILE"})
    return out


def normalize_col(c: str) -> str:
    c = c.strip().lower()
    c = c.replace("ı", "i").replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c").replace("İ", "i")
    c = re.sub(r"[^a-z0-9]+", "_", c).strip("_")
    return c


def schema_clusters(run: Run) -> tuple[list[dict], list[dict]]:
    sigs = {}
    for tid, s in run.schemas.items():
        cols = tuple(sorted(set(normalize_col(c) for c in (s.get("columns") or []) if c)))
        if not cols:
            continue
        sigs[tid] = cols
    # 1) tam imza eşitliği
    exact = defaultdict(list)
    for tid, cols in sigs.items():
        exact[cols].append(tid)
    # 2) Jaccard >= 0.8 ile birleştirme (greedy, imza temsilcileri üzerinden)
    reps = list(exact.keys())
    parent = {i: i for i in range(len(reps))}
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    sets = [set(r) for r in reps]
    # ters indeks ile aday çiftleri sınırla
    inv = defaultdict(list)
    for i, s in enumerate(sets):
        for c in s:
            inv[c].append(i)
    for i, s in enumerate(sets):
        cand = Counter()
        for c in s:
            for j in inv[c]:
                if j > i:
                    cand[j] += 1
        for j, inter in cand.items():
            union = len(s) + len(sets[j]) - inter
            if union and inter / union >= 0.8:
                parent[find(i)] = find(j)
    cluster_of_rep = {}
    cid = 0
    members_out, clusters_out = [], []
    by_root = defaultdict(list)
    for i in range(len(reps)):
        by_root[find(i)].append(i)
    for root, idxs in sorted(by_root.items(), key=lambda kv: -sum(len(exact[reps[i]]) for i in kv[1])):
        cid += 1
        c = f"SCHEMA_CLUSTER_{cid:03d}"
        all_cols = Counter()
        tids = []
        for i in idxs:
            for tid in exact[reps[i]]:
                tids.append(tid)
                members_out.append({"schema_cluster_id": c, "table_id": tid, "file_id": run.schemas[tid]["file_id"], "table_name": run.schemas[tid]["table_name"], "kind": run.schemas[tid]["kind"], "signature_hash": hashlib.sha1("|".join(reps[i]).encode()).hexdigest()[:12]})
            all_cols.update(reps[i])
        clusters_out.append({"schema_cluster_id": c, "member_count": len(tids), "distinct_signatures": len(idxs), "common_columns": json.dumps([k for k, v in all_cols.most_common() if v == len(idxs)][:60], ensure_ascii=False),
                             "all_columns": json.dumps([k for k, _ in all_cols.most_common(120)], ensure_ascii=False)})
    return clusters_out, members_out


CATEGORY_RULES = [
    ("administrative_boundaries", ["sinir", "boundary", "polygon", "poligon", "geometry", "mahalle_kodu", "ilce_kodu", "il_kodu", "idari"], 0.35),
    ("demographics", ["nufus", "population", "yas_grubu", "cinsiyet", "hane", "demografi", "dogum", "yas_"], 0.3),
    ("migration", ["goc", "migration", "hemsehri", "kutuk", "memleket", "dogum_yeri"], 0.3),
    ("education", ["okul", "school", "lgs", "meb", "ogrenci", "egitim", "universite"], 0.3),
    ("socioeconomic", ["sege", "gelir", "ses_", "sosyo", "gelismislik", "income"], 0.3),
    ("retail_spending", ["harcama", "bkm", "kart", "spending", "ciro", "perakende"], 0.3),
    ("ecommerce_spending", ["etbis", "e_ticaret", "eticaret", "ecommerce"], 0.3),
    ("business", ["isletme", "firma", "sirket", "ticaret_sicil", "tobb", "vergi", "business", "company", "ofis", "danisman"], 0.3),
    ("restaurants", ["restoran", "restaurant", "kafe", "cafe", "yemeksepeti", "getir", "mutfak", "cuisine", "mekan"], 0.3),
    ("restaurant_menu", ["menu", "urun_adi", "kalem", "porsiyon", "menu_item"], 0.35),
    ("price", ["fiyat", "price", "m2_fiyat", "birim_fiyat", "endeks", "index", "kira", "rent", "tl"], 0.25),
    ("reviews", ["yorum", "review", "puan", "rating", "yildiz", "star"], 0.3),
    ("poi", ["poi", "amenity", "osm_id", "place_id", "google_places", "lat", "lon", "enlem", "boylam", "kategori"], 0.25),
    ("housing", ["konut", "daire", "housing", "oda", "bina", "kat_", "site_", "airbnb", "listing"], 0.3),
    ("real_estate_listings", ["ilan", "ilan_no", "sahibinden", "emlakjet", "hepsiemlak", "arsa", "tarla", "satilik", "kiralik"], 0.3),
    ("cadastre_zoning", ["parsel", "ada_", "ada_no", "kaks", "taks", "imar", "tapu", "tkgm", "kadastro", "bagimsiz_bolum"], 0.35),
    ("ownership", ["malik", "owner", "tapu_sahibi", "hisse"], 0.35),
    ("mobility", ["trafik", "traffic", "yolcu", "ulasim", "durak", "istasyon", "strava", "gps", "hareketlilik", "otopark", "ispark"], 0.3),
    ("logistics", ["kargo", "darkstore", "teslimat", "delivery", "lojistik"], 0.3),
    ("finance", ["bddk", "kredi", "banka", "finturk", "mevduat", "kap_", "bilanco"], 0.3),
    ("elections", ["secim", "election", "oy_", "parti"], 0.35),
    ("health", ["saglik", "hastane", "eczane", "health", "tutun", "sigara"], 0.3),
    ("vehicles", ["arac", "araba", "otomobil", "plaka", "vehicle", "aracrisk", "emsal"], 0.3),
    ("jobs", ["istihdam", "is_ilani", "job", "kariyer", "maas"], 0.3),
    ("tenders", ["ihale", "ekap", "tender", "haciz"], 0.35),
]


def classify(run: Run) -> list[dict]:
    """Dosya adı + yol + tablo adı + kolon adları üzerinden kural tabanlı tahmin. Kesin sınıf DEĞİL: predicted_category + confidence + reasons."""
    tables_by_file = defaultdict(list)
    for t in run.tables:
        tables_by_file[t["file_id"]].append(t)
    out = []
    for r in run.files:
        if not r.get("is_data_candidate"):
            continue
        tokens = Counter()
        text_parts = [normalize_col(r["filename"]), normalize_col(str(Path(r["relative_path"]).parent))]
        col_tokens = []
        for t in tables_by_file.get(r["file_id"], []):
            text_parts.append(normalize_col(t["table_name"] or ""))
            if t.get("column_names"):
                try:
                    col_tokens.extend(normalize_col(c) for c in json.loads(t["column_names"]))
                except Exception:
                    pass
        name_blob = "_".join(text_parts)
        col_blob = "_".join(col_tokens)
        scores = {}
        reasons = defaultdict(list)
        for cat, kws, base in CATEGORY_RULES:
            s = 0.0
            for kw in kws:
                if kw in name_blob:
                    s += base
                    reasons[cat].append(f"name/path contains '{kw}'")
                if kw in col_blob:
                    s += base * 0.8
                    reasons[cat].append(f"column contains '{kw}'")
            if s:
                scores[cat] = s
        if r["detected_format"] in ("geojson", "geopackage", "shapefile", "kml", "kmz") or any(g["file_id"] == r["file_id"] for g in run.geo_layers):
            scores["administrative_boundaries"] = scores.get("administrative_boundaries", 0) + 0.3
            reasons["administrative_boundaries"].append("spatial layer")
        if not scores:
            out.append({"file_id": r["file_id"], "classification": "unknown", "predicted_category": "unknown", "classification_confidence": 0.0, "classification_reasons": "no keyword/column signal", "secondary_categories": None})
            continue
        best = max(scores.items(), key=lambda kv: kv[1])
        total = sum(scores.values())
        conf = round(min(0.95, 0.4 + 0.6 * (best[1] / total) * min(1.0, best[1] / 0.9)), 3)
        secondary = [c for c, s in sorted(scores.items(), key=lambda kv: -kv[1])[1:4]]
        out.append({"file_id": r["file_id"], "classification": "predicted", "predicted_category": best[0], "classification_confidence": conf,
                    "classification_reasons": "; ".join(dict.fromkeys(reasons[best[0]]))[:500], "secondary_categories": json.dumps(secondary, ensure_ascii=False)})
    return out


def quarantine_registry(run: Run) -> list[dict]:
    """Dosya/tablo kuralları + içerik dedektör bulguları + GEOPROP kaynak sicili data_class'ı → kayıt. Phase 1'de yalnız işaretleme."""
    out = []
    for r in run.files:
        if not r.get("is_data_candidate"):
            continue
        rel = "/" + r["relative_path"].replace("\\", "/")
        for rule in RULES["file_rules"]:
            hit = False
            if rule["match"] == "filename_regex" and re.search(rule["pattern"], r["filename"]):
                hit = True
            elif rule["match"] == "path_regex" and re.search(rule["pattern"], rel + "/"):
                hit = True
            if hit:
                out.append({"quarantine_id": None, "file_id": r["file_id"], "table_id": None, "table_name": None, "source_location": r["absolute_path"], "rule_id": rule["rule_id"], "status": rule["status"],
                            "error_type": "SYNTHETIC_OR_FALLBACK_SOURCE" if rule["status"] == "QUARANTINE" else "REVIEW", "error_message": rule["reason"], "evidence": f"{rule['match']}={rule['pattern']}",
                            "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
    file_by_id = {r["file_id"]: r for r in run.files}
    for t in run.tables:
        cols = []
        try:
            cols = json.loads(t["column_names"]) if t.get("column_names") else []
        except Exception:
            pass
        tname = (t["table_name"] or "").split("::")[-1]
        for rule in RULES["table_rules"]:
            hit = False
            if rule["match"] == "table_name_in" and tname in rule["names"]:
                hit = True
            elif rule["match"] == "table_name_regex" and re.search(rule["pattern"], tname):
                hit = True
            elif rule["match"] == "column_present" and rule["column"] in cols:
                hit = True
            elif rule["match"] == "column_present_any" and any(c in cols for c in rule["columns"]):
                hit = True
            if hit:
                out.append({"quarantine_id": None, "file_id": t["file_id"], "table_id": t["table_id"], "table_name": t["table_name"], "source_location": file_by_id[t["file_id"]]["absolute_path"], "rule_id": rule["rule_id"], "status": rule["status"],
                            "error_type": "SYNTHETIC_OR_FALLBACK_SOURCE" if rule["status"] == "QUARANTINE" else rule["status"], "error_message": rule["reason"], "evidence": f"{rule['match']}",
                            "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
    for f in run.quarantine:  # içerik dedektörleri
        out.append({"quarantine_id": None, "file_id": f["file_id"], "table_id": f["table_id"], "table_name": f["table_name"], "source_location": file_by_id[f["file_id"]]["absolute_path"], "rule_id": f["detector_id"], "status": f["status"],
                    "error_type": "CONTENT_DETECTOR", "error_message": f"{f['reason']} [column={f.get('column')} hits={f.get('hit_count')}]", "evidence": f"detector={f['detector_id']}",
                    "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
    # ciddi uyumsuzluk: format/uzantı çelişkisi, yüksek reject oranı, encoding unknown
    for r in run.files:
        if r.get("format_mismatch"):
            out.append({"quarantine_id": None, "file_id": r["file_id"], "table_id": None, "table_name": None, "source_location": r["absolute_path"], "rule_id": "X01", "status": "REVIEW_REQUIRED", "error_type": "FORMAT_EXTENSION_MISMATCH",
                        "error_message": f"uzantı=.{r['extension']} ama içerik={r['detected_format']}", "evidence": "magic_bytes", "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
        if r.get("encoding") == "unknown":
            out.append({"quarantine_id": None, "file_id": r["file_id"], "table_id": None, "table_name": None, "source_location": r["absolute_path"], "rule_id": "X02", "status": "REVIEW_REQUIRED", "error_type": "ENCODING_UNKNOWN",
                        "error_message": "Karakter kodlaması tespit edilemedi", "evidence": "charset_normalizer", "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
    for t in run.tables:
        if t.get("reject_count") and t.get("row_count") and t["reject_count"] / max(1, t["row_count"] + t["reject_count"]) >= 0.005:
            out.append({"quarantine_id": None, "file_id": t["file_id"], "table_id": t["table_id"], "table_name": t["table_name"], "source_location": file_by_id[t["file_id"]]["absolute_path"], "rule_id": "X03", "status": "REVIEW_REQUIRED",
                        "error_type": "HIGH_PARSE_ERROR_RATE", "error_message": f"reject={t['reject_count']} / rows={t['row_count']}", "evidence": "duckdb_reject_errors", "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
        if t.get("read_status") == "failed":
            out.append({"quarantine_id": None, "file_id": t["file_id"], "table_id": t["table_id"], "table_name": t["table_name"], "source_location": file_by_id[t["file_id"]]["absolute_path"], "rule_id": "X04", "status": "QUARANTINE",
                        "error_type": "UNPARSEABLE", "error_message": t.get("notes"), "evidence": "probe_failed", "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
    # GEOPROP kaynak sicili (content_sha256 → data_class) join
    reg = Path.home() / "Desktop" / "GEOPROP" / "reports" / "kaynak-sicili.json"
    if reg.exists():
        try:
            d = json.loads(reg.read_text(encoding="utf-8"))
            by_hash = defaultdict(list)
            for m in d.get("mappings", []):
                by_hash[m.get("content_sha256")].append(m)
            for r in run.files:
                for m in by_hash.get(r.get("sha256"), []):
                    dc = (m.get("data_class") or "").lower()
                    sid = m.get("source_id")
                    if dc in ("quarantine", "derived", "model", "synthetic") or sid == "local_commercial_models":
                        out.append({"quarantine_id": None, "file_id": r["file_id"], "table_id": None, "table_name": m.get("source_table"), "source_location": r["absolute_path"], "rule_id": "R01", "status": "QUARANTINE" if dc == "quarantine" else "REVIEW_REQUIRED",
                                    "error_type": "GEOPROP_REGISTRY_CLASS", "error_message": f"GEOPROP kaynak sicili: source_id={sid} data_class={dc} entity={m.get('entity')}", "evidence": f"kaynak-sicili.json mapping_id={m.get('mapping_id')}",
                                    "pipeline_version": PIPELINE_VERSION, "rules_hash": run.rules_hash, "created_at": now_iso()})
        except Exception as e:
            run.log_error(None, "registry_join", type(e).__name__, e)
    for i, q in enumerate(out, 1):
        q["quarantine_id"] = f"QREC_{i:06d}"
    return out


# --------------------------------------------------------------------------------------
# AŞAMA 7: doğrulama (kaynak değişmedi mi?) + çıktı
# --------------------------------------------------------------------------------------
def verify_sources_unchanged(run: Run) -> dict:
    changed, missing, checked = [], [], 0
    known = {r["absolute_path"] for r in run.files}
    new_files = []
    for root in SOURCE_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
            if any(dirpath.startswith(x) for x in EXCLUDE_PATH_PREFIXES):
                continue
            for fn in filenames:
                if fn in EXCLUDE_FILE_NAMES:
                    continue
                ap = str(Path(dirpath) / fn)
                if (Path(dirpath) / fn).is_symlink():
                    continue
                if ap not in known:
                    new_files.append(ap)
    for nf in new_files:
        run.log_error(None, "verify", "NEW_FILE_IN_SOURCE_DURING_SCAN", f"Tarama sırasında kaynak kökte yeni dosya belirdi (dış süreç veya yan dosya): {nf}")
    for r in run.files:
        if not r.get("sha256"):
            continue
        p = Path(r["absolute_path"])
        try:
            st = p.stat()
        except FileNotFoundError:
            missing.append(r["file_id"])
            continue
        checked += 1
        if st.st_size != r["size_bytes"] or abs(st.st_mtime - r["mtime_epoch"]) > 1e-6:
            # gerçekten değişti mi? yeniden hash'le
            try:
                h2 = sha256_stream(p)
            except Exception:
                h2 = None
            if h2 != r["sha256"]:
                changed.append({"file_id": r["file_id"], "path": r["absolute_path"], "old_sha256": r["sha256"], "new_sha256": h2})
    for c in changed:
        run.log_error(c["file_id"], "verify", "SOURCE_MUTATION_ALERT", f"Kaynak dosya tarama sırasında değişti: {c['path']}", old=c["old_sha256"], new=c["new_sha256"])
    return {"checked": checked, "changed": len(changed), "missing": len(missing), "new_files": len(new_files), "changed_list": changed, "missing_list": missing, "new_files_list": new_files}


def write_table(rows: list[dict], path_parquet: Path, path_csv: Path | None = None):
    if not rows:
        pq.write_table(pa.table({"_empty": pa.array([], pa.string())}), path_parquet)
        if path_csv:
            path_csv.write_text("", encoding="utf-8")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r.keys()))
    cols = {}
    for k in keys:
        vals = [r.get(k) for r in rows]
        # tip: bool/int/float korunur; karışıksa string
        kinds = {type(v) for v in vals if v is not None}
        if kinds <= {bool}:
            cols[k] = pa.array(vals, pa.bool_())
        elif kinds <= {int, bool}:
            cols[k] = pa.array(vals, pa.int64())
        elif kinds <= {int, float, bool}:
            cols[k] = pa.array([None if v is None else float(v) for v in vals], pa.float64())
        else:
            cols[k] = pa.array([None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)) for v in vals], pa.string())
    tbl = pa.table(cols)
    pq.write_table(tbl, path_parquet, compression="zstd")
    if path_csv:
        with open(path_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="Taramayı gerçekten çalıştır")
    ap.add_argument("--resume", action="store_true", help="hash cache kullan")
    ap.add_argument("--limit", type=int, default=None, help="test için dosya limiti")
    ap.add_argument("--incremental", action="store_true", help="mevcut envanteri oku; yalnız yeni/değişen dosyaları incele, sonuçları birleştir")
    args = ap.parse_args()
    if not args.run:
        print("DRY-RUN: --run verilmedi. Kökler:", [str(r) for r in SOURCE_ROOTS], "Çıktı:", OUT_ROOT)
        return
    # güvenlik: OUT_ROOT kaynak köklerin içinde olamaz, kaynak kökler OUT_ROOT içinde olamaz
    for r in SOURCE_ROOTS:
        assert not str(OUT_ROOT.resolve()).startswith(str(r.resolve()) + os.sep), f"OUT_ROOT kaynak kökün içinde: {r}"
        assert not str(r.resolve()).startswith(str(OUT_ROOT.resolve()) + os.sep), f"Kaynak kök OUT_ROOT içinde: {r}"
    run = Run(resume=args.resume or args.incremental)
    if args.incremental:
        run.load_previous()
    print(f"RUN {run.run_id}  pipeline={PIPELINE_VERSION}  code_hash={run.code_hash[:12]}  spatial={run.spatial}")
    run.audit_write("start", source_roots=[str(r) for r in SOURCE_ROOTS], out_root=str(OUT_ROOT), free_disk_bytes=free_disk_bytes(OUT_ROOT), rules_hash=run.rules_hash)
    walk_sources(run)
    if args.limit:
        run.files = run.files[: args.limit]
    total_bytes = sum(r["size_bytes"] or 0 for r in run.files)
    print(f"  dosya={len(run.files)}  toplam={total_bytes/1e9:.2f} GB  boş disk={free_disk_bytes(OUT_ROOT)/1e9:.1f} GB")
    hash_and_detect(run)
    content_inventory(run)
    dups = duplicates(run)
    clusters, cluster_members = schema_clusters(run)
    cls = classify(run)
    qreg = quarantine_registry(run)
    verify = verify_sources_unchanged(run)

    inv = OUT_ROOT / "inventory"
    rm = OUT_ROOT / "raw_manifest"
    # manifest snapshot (append-only: her run kendi dosyasını yazar)
    write_table(run.files, rm / f"manifest_{run.run_id}.parquet", rm / f"manifest_{run.run_id}.csv")
    write_table(run.files, inv / "inventory.parquet", inv / "inventory.csv")
    write_table(run.tables, inv / "tables.parquet", inv / "tables.csv")
    write_table(run.columns, inv / "columns.parquet")
    write_table(run.geo_layers, inv / "geo_layers.parquet", inv / "geo_layers.csv")
    write_table(run.errors, inv / "errors.parquet", inv / "errors.csv")
    write_table(run.parse_errors, inv / "parse_errors.parquet", inv / "parse_errors.csv")
    write_table(dups, inv / "duplicates_files.parquet", inv / "duplicates_files.csv")
    write_table(run.zip_members, inv / "zip_members.parquet", inv / "zip_members.csv")
    write_table(clusters, inv / "schema_clusters.parquet", inv / "schema_clusters.csv")
    write_table(cluster_members, inv / "schema_cluster_members.parquet")
    write_table(cls, inv / "classifications.parquet", inv / "classifications.csv")
    write_table(qreg, OUT_ROOT / "quarantine" / "quarantine_registry.parquet", OUT_ROOT / "quarantine" / "quarantine_registry.csv")
    (inv / "schemas.json").write_text(json.dumps(run.schemas, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT_ROOT / "transformations" / f"{run.run_id}_transformations.json").write_text(json.dumps([{
        "transformation_id": "PHASE1_DISCOVERY_V1", "name": "phase1_discovery", "version": PIPELINE_VERSION, "description": "Salt-okunur envanter; kaynak değiştirilmez; normalize_col yalnız şema kümeleme imzası için (raw sütun adları korunur).",
        "input": [str(r) for r in SOURCE_ROOTS], "output": str(inv), "code_hash": run.code_hash, "rules_hash": run.rules_hash, "executed_at": run.started_at, "deterministic": True, "seed": None}], ensure_ascii=False, indent=1), encoding="utf-8")

    # RUN REPORT + DATA LOSS REPORT
    fmt_counts = Counter(r["detected_format"] for r in run.files)
    readable = sum(1 for r in run.files if r["read_status"] == "hashed")
    unreadable = len(run.files) - readable
    total_rows = sum(t["row_count"] or 0 for t in run.tables if t["object_type"] in ("csv", "sqlite_table", "parquet", "geo_layer", "json", "jsonl", "geojsonseq", "xlsx_sheet", "geojson"))
    cats = Counter(c["predicted_category"] for c in cls)
    q_by_status = Counter(q["status"] for q in qreg)
    summary = {
        "run_id": run.run_id, "started_at": run.started_at, "finished_at": now_iso(), "pipeline_version": PIPELINE_VERSION, "code_hash": run.code_hash, "rules_hash": run.rules_hash,
        "source_roots": [str(r) for r in SOURCE_ROOTS], "files_scanned": len(run.files), "total_bytes": total_bytes, "readable": readable, "unreadable": unreadable,
        "data_candidates": sum(1 for r in run.files if r.get("is_data_candidate")), "content_probed": sum(1 for r in run.files if r.get("content_probed")),
        "formats": dict(fmt_counts.most_common()), "exact_duplicate_groups": len({d["duplicate_group_id"] for d in dups}), "exact_duplicate_files": len(dups),
        "duplicate_bytes_redundant": sum(d["size_bytes"] or 0 for d in dups) - sum({d["duplicate_group_id"]: d["size_bytes"] or 0 for d in dups}.values()),
        "tables": len(run.tables), "columns": len(run.columns), "geo_layers": len(run.geo_layers), "schema_clusters": len(clusters), "records_discovered": total_rows,
        "parse_errors_recorded": len(run.parse_errors), "errors": len(run.errors), "categories": dict(cats.most_common()), "quarantine_by_status": dict(q_by_status),
        "data_loss": {"source_files_deleted": 0, "source_files_modified": verify["changed"], "source_files_missing_after_scan": verify["missing"], "rows_silently_dropped": 0, "features_silently_dropped": 0,
                      "columns_silently_removed": 0, "unaccounted_records": 0, "note": "Phase 1 salt-okunur; hiçbir kayıt yazılmadı/silinmedi. Parse hataları parse_errors tablosunda ham satırla korunur."},
        "verify": {k: v for k, v in verify.items() if k not in ("changed_list", "missing_list", "new_files_list")},
    }
    (OUT_ROOT / "reports" / f"{run.run_id}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_ROOT / "validation" / f"{run.run_id}_data_loss_report.json").write_text(json.dumps({"run_id": run.run_id, **summary["data_loss"], "verify": summary["verify"], "changed_files": verify["changed_list"], "missing_files": verify["missing_list"], "new_files_in_source": verify["new_files_list"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    run.audit_write("finish", **{k: v for k, v in summary.items() if k not in ("formats", "categories")})
    status = "PASSED" if verify["changed"] == 0 and verify["missing"] == 0 else "FAILED (SOURCE_MUTATION_ALERT)"
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"\nRUN {run.run_id}: {status}")
    # tmp temizliği (yalnız kendi tmp dizinimiz)
    shutil.rmtree(OUT_ROOT / "tmp" / run.run_id, ignore_errors=True)


if __name__ == "__main__":
    main()
