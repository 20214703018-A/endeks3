#!/usr/bin/env python3
"""
PHASE 2 — GENEL STAGING ARACI (tablo → GeoParquet/Parquet, raw_* + typed + provenance)

Kaynak türleri: sqlite_table | csv | json_dict (anahtar→obje) | json_records | geojson
Her satır: raw_<sütun> (metin, dokunulmamış) + opsiyonel geometry_original (WKB) + provenance + review_flags.
Kaynak salt okunur (SQLite: immutable=1 veya WAL varsa tmp kopya). Append-only: aynı çıktı dosyası varsa yazmaz.
Sayım: input = staged (+ quarantined); UNACCOUNTED ≠ 0 → yazılmaz.

  python3 phase2_stage_generic.py --spec specs/reference_geography_aux.json --dry-run
  python3 phase2_stage_generic.py --spec specs/reference_geography_aux.json --apply [--only S3]
"""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, io, json, re, shutil, sqlite3, sys, time
from pathlib import Path
import duckdb, pyarrow as pa, pyarrow.parquet as pq
from shapely import wkb as shp_wkb
from shapely.geometry import shape, Point

PIPELINE_VERSION = "1.0.0"; STANDARD_VERSION = "1.0.0"
OUT_ROOT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
TR_BBOX = (25.5, 35.7, 45.0, 42.3)
csv.field_size_limit(1 << 30)


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def canon(o): return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def inventory_lookup(abs_path: str, table_name: str | None):
    con = duckdb.connect()
    r = con.execute(f"SELECT file_id, sha256 FROM '{OUT_ROOT}/inventory/inventory.parquet' WHERE absolute_path = ?", [abs_path]).fetchone()
    if not r: raise SystemExit(f"Envanterde yok (önce artımlı Phase 1): {abs_path}")
    tid = None
    if table_name:
        t = con.execute(f"SELECT table_id FROM '{OUT_ROOT}/inventory/tables.parquet' WHERE file_id = ? AND table_name = ?", [r[0], table_name]).fetchone()
        tid = t[0] if t else None
    else:
        t = con.execute(f"SELECT table_id FROM '{OUT_ROOT}/inventory/tables.parquet' WHERE file_id = ? LIMIT 1", [r[0]]).fetchone()
        tid = t[0] if t else None
    return r[0], r[1], tid


def open_sqlite_ro(path: Path, tmpdir: Path):
    hdr = path.open("rb").read(100)
    wal = Path(str(path) + "-wal")
    if (len(hdr) >= 20 and hdr[18] == 2) or wal.exists():
        tmpdir.mkdir(parents=True, exist_ok=True)
        cp = tmpdir / (path.name + ".copy"); shutil.copyfile(path, cp)
        if wal.exists() and wal.stat().st_size > 0: shutil.copyfile(wal, Path(str(cp) + "-wal"))
        return sqlite3.connect(f"file:{cp}?mode=ro", uri=True), cp
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True), None


def _read_text_bytes(p: Path) -> bytes:
    b = p.read_bytes()
    if b[:2] == b"\x1f\x8b":
        import gzip; b = gzip.decompress(b)
    return b


def read_rows(src: dict, tmpdir: Path):
    """→ (header:list[str], rows:list[list], input_count:int, note:str)"""
    p = Path(src["path"]).expanduser(); kind = src["kind"]
    if kind == "sqlite_table":
        con, cp = open_sqlite_ro(p, tmpdir)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        cur = con.execute(f'SELECT * FROM "{src["table"]}"')
        header = [d[0] for d in cur.description]; rows = [list(r) for r in cur.fetchall()]; con.close()
        if cp:
            for q in (cp, Path(str(cp) + "-wal"), Path(str(cp) + "-shm")): q.unlink(missing_ok=True)
        return header, rows, len(rows), "sqlite"
    if kind == "csv":
        raw = p.read_bytes(); enc = src.get("encoding", "utf-8-sig")
        text = raw.decode(enc, errors="strict")
        rd = csv.reader(io.StringIO(text), delimiter=src.get("delimiter", ","))
        header = next(rd); rows = [r for r in rd]
        return header, rows, len(rows), f"csv enc={enc}"
    if kind == "json_dict":
        d = json.loads(_read_text_bytes(p).decode("utf-8-sig"))
        keys = set()
        for k, v in d.items():
            if isinstance(v, dict): keys.update(v.keys())
        keys = sorted(keys); header = ["_key"] + keys + ["_value_json"]
        rows = []
        for k, v in d.items():
            if isinstance(v, dict): rows.append([k] + [v.get(c) for c in keys] + [canon(v)])
            else: rows.append([k] + [None] * len(keys) + [canon(v)])
        return header, rows, len(d), "json_dict"
    if kind == "json_nested_guide":
        # {"1": {"il": "...", "ilceler": [...]}} vb. → her (il, ilçe) bir satır; yapı bilinmiyorsa _value_json ile ham korunur
        d = json.loads(_read_text_bytes(p).decode("utf-8-sig"))
        rows = []
        for k, v in d.items():
            if isinstance(v, dict):
                il = v.get("il") or v.get("ad") or v.get("name")
                lst = None
                for lk in ("ilceler", "ilçeler", "districts", "counties"):
                    if isinstance(v.get(lk), list): lst = v[lk]; break
                if lst:
                    for j, ilce in enumerate(lst):
                        rows.append([k, il, canon(ilce) if not isinstance(ilce, str) else ilce, j + 1, canon(v)])
                else: rows.append([k, il, None, None, canon(v)])
            elif isinstance(v, list):
                for j, ilce in enumerate(v): rows.append([k, None, canon(ilce) if not isinstance(ilce, str) else ilce, j + 1, canon(v)])
            else: rows.append([k, None, None, None, canon(v)])
        return ["_key", "il", "ilce", "ilce_sira", "_value_json"], rows, len(rows), "json_nested_guide"
    if kind == "json_records":
        d = json.loads(_read_text_bytes(p).decode("utf-8-sig"))
        recs = None; under = None
        if isinstance(d, list) and d and isinstance(d[0], dict): recs = d
        elif isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and (recs is None or len(v) > len(recs)): recs, under = v, k
        if recs is None: return ["_value_json"], [[canon(d)]], 1, "json_records:no_records"
        keys = []
        for rec in recs:
            if isinstance(rec, dict):
                for k in rec.keys():
                    if k not in keys: keys.append(k)
        keys = keys + ["_value_json"]
        rows = [([(rec.get(k) if isinstance(rec.get(k), (str, int, float, bool)) or rec.get(k) is None else canon(rec.get(k))) for k in keys[:-1]] + [None]) if isinstance(rec, dict)
                else ([None] * (len(keys) - 1) + [canon(rec)]) for rec in recs]  # dict olmayan kayıtlar _value_json'da ham korunur
        return keys, rows, len(recs), f"json_records under={under}"
    if kind == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=False); ws = wb[src["table"]]
        it = ws.iter_rows(values_only=True); header = [("" if v is None else str(v)) for v in next(it, [])]
        rows = [[(None if v is None else (v if isinstance(v, str) else str(v))) for v in r[:len(header)]] for r in it]
        wb.close(); return header, rows, len(rows), "xlsx"
    if kind == "geojson":
        d = json.loads(_read_text_bytes(p).decode("utf-8-sig"))
        feats = d.get("features", [])
        keys = []
        for f in feats:
            for k in (f.get("properties") or {}).keys():
                if k not in keys: keys.append(k)
        header = keys + ["_geometry_json", "_feature_id"]
        rows = [[(f.get("properties") or {}).get(k) for k in keys] + [canon(f.get("geometry")), f.get("id")] for f in feats]
        return header, rows, len(feats), f"geojson crs={canon(d.get('crs'))}"
    if kind == "xls":  # eski Excel (OLE) — xlrd; tüm hücreler metin olarak
        import xlrd
        wb = xlrd.open_workbook(p); ws = wb.sheet_by_name(src["table"]) if src.get("table") else wb.sheet_by_index(0)
        hr = int(src.get("header_row", 0)); header = [str(ws.cell_value(hr, c)).replace("\n", " ").strip() or f"col_{c}" for c in range(ws.ncols)]
        rows = [[(None if ws.cell_value(r, c) in ("", None) else str(ws.cell_value(r, c))) for c in range(ws.ncols)] for r in range(hr + 1, ws.nrows)]
        return header, rows, len(rows), f"xls sheet={ws.name} header_row={hr}"
    if kind == "duckdb_table":  # başka bir DuckDB dosyasındaki tablo — salt okunur ekleme, tüm sütunlar metin
        import duckdb as _d
        con = _d.connect(); con.execute(f"ATTACH '{str(p).replace(chr(39), chr(39)*2)}' AS g (READ_ONLY)")
        cols = [r[0] for r in con.execute(f'DESCRIBE g."{src["table"]}"').fetchall()]
        rows = [[(None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v))) for v in r] for r in con.execute(f'SELECT * FROM g."{src["table"]}"').fetchall()]
        con.close(); return cols, rows, len(rows), "duckdb_table"
    if kind == "geojsonseq":
        feats = []
        for line in _read_text_bytes(p).split(b"\n"):
            line = line.strip().lstrip(b"\x1e")
            if line: feats.append(json.loads(line))
        keys = []
        for f in feats:
            for k in (f.get("properties") or {}).keys():
                if k not in keys: keys.append(k)
        header = keys + ["_geometry_json", "_feature_id"]
        rows = [[(f.get("properties") or {}).get(k) for k in keys] + [canon(f.get("geometry")), f.get("id")] for f in feats]
        return header, rows, len(feats), "geojsonseq (RFC 7464, RS stripped)"
    raise ValueError(kind)


def build_geometry(rec_raw: dict, src: dict):
    """spec.geometry: {"type":"geojson_text","column":"geometri"} | {"type":"latlon","lat":"lat","lon":"lon"} | {"type":"geojson_feature"}"""
    g = src.get("geometry")
    if not g: return None, None, None
    try:
        if g["type"] == "geojson_text":
            t = rec_raw.get(g["column"])
            if not t: return None, "missing", None
            geom = shape(json.loads(t))
        elif g["type"] == "geojson_feature":
            t = rec_raw.get("_geometry_json")
            if not t or t == "null": return None, "missing", None
            geom = shape(json.loads(t))
        elif g["type"] == "latlon":
            la, lo = rec_raw.get(g["lat"]), rec_raw.get(g["lon"])
            if la in (None, "") or lo in (None, ""): return None, "missing", None
            geom = Point(float(lo), float(la))
        else: return None, None, None
        status = "valid" if geom.is_valid else "invalid"
        return shp_wkb.dumps(geom), status, geom
    except Exception as e:
        return None, f"construction_failed: {str(e)[:120]}", None


def _extract_zip_member(zip_path: Path, member: str, tmpdir: Path) -> Path:
    """Üye yolu iç içe olabilir ('inner.zip::member'); ziputil.open_nested katmanları açar. Kaynak zip'e yazılmaz."""
    import sys; sys.path.insert(0, str(Path(__file__).parent)); from ziputil import extract_member
    tmpdir.mkdir(parents=True, exist_ok=True)
    out = tmpdir / ("zipm_" + re.sub(r'[^0-9A-Za-z_.]', '_', member)[-120:])
    return extract_member(zip_path, member, out, tmpdir)


def stage_source(src: dict, batch_id: str, tmpdir: Path):
    p = Path(src["path"]).expanduser()
    fid, sha, tid = inventory_lookup(str(p), src.get("inventory_table") or src.get("table"))
    assert sha256_bytes(p.read_bytes()) == sha, f"SOURCE_MUTATION_ALERT: {p}"
    locator = str(p); extracted = None
    if src.get("zip_member"):
        extracted = _extract_zip_member(p, src["zip_member"], tmpdir); locator = f"{p}::{src['zip_member']}"
        src = {**src, "path": str(extracted)}
    header, rows, n_in, note = read_rows(src, tmpdir)
    if extracted: extracted.unlink(missing_ok=True)
    out_rows = []; geom_types = set(); bboxes = []
    for i, r in enumerate(rows, 1):
        raw = {h: (None if v is None else (v if isinstance(v, str) else (v.hex() if isinstance(v, (bytes, bytearray)) else str(v)))) for h, v in zip(header, r)}
        rec = {f"raw_{re.sub(r'[^0-9A-Za-z_]', '_', h)}": raw[h] for h in header}
        rec["raw_row_json"] = canon(raw)
        flags = []
        wkb_bytes, gstatus, geom = build_geometry(raw, src)
        if src.get("geometry"):
            rec["geometry_original"] = wkb_bytes; rec["geometry_validity_status"] = gstatus
            rec["source_crs"] = src["geometry"].get("crs", "EPSG:4326")
            if geom is not None:
                b = geom.bounds; rec["bbox_xmin"], rec["bbox_ymin"], rec["bbox_xmax"], rec["bbox_ymax"] = b; bboxes.append(b); geom_types.add(geom.geom_type)
                c = geom.representative_point(); rec["outside_tr_bbox"] = not (TR_BBOX[0] <= c.x <= TR_BBOX[2] and TR_BBOX[1] <= c.y <= TR_BBOX[3])
                if rec["outside_tr_bbox"]: flags.append("GEO_CONFLICT_OUTSIDE_TR")
                if gstatus == "invalid": flags.append("GEOMETRY_INVALID")
            else:
                rec["bbox_xmin"] = rec["bbox_ymin"] = rec["bbox_xmax"] = rec["bbox_ymax"] = None; rec["outside_tr_bbox"] = None
                if gstatus and gstatus.startswith("construction_failed"): flags.append("GEOMETRY_CONSTRUCTION_FAILED")
                elif gstatus == "missing": flags.append("GEOMETRY_MISSING")
        for f in src.get("review_flags", []): flags.append(f)
        rec.update({"source_file_id": fid, "source_table_id": tid, "source_row_number": i, "source_object_path": src.get("object_path_template", "row[{i}]").format(i=i),
                    "source_file_sha256": sha, "source_row_hash": sha256_bytes(canon(raw).encode()), "source_relative_path": locator,
                    "acquisition_class": src.get("acquisition_class", "web_research"), "distribution_class": src.get("distribution_class", "public"), "sensitivity": src.get("sensitivity", "none"),
                    "transformation_id": src.get("transformation_id", "T00_RAW_COPY_V1"), "import_batch_id": batch_id, "pipeline_version": PIPELINE_VERSION, "standard_version": STANDARD_VERSION,
                    "staged_at": now(), "review_flags": json.dumps(flags)})
        out_rows.append(rec)
    return fid, header, out_rows, n_in, note, geom_types, bboxes


def to_table(rows, src, geom_types, bboxes):
    keys = list(rows[0].keys()); cols = {}
    for k in keys:
        vals = [r.get(k) for r in rows]
        if k == "geometry_original": cols[k] = pa.array(vals, pa.binary())
        elif k == "source_row_number": cols[k] = pa.array(vals, pa.int64())
        elif k.startswith("bbox_"): cols[k] = pa.array(vals, pa.float64())
        elif k == "outside_tr_bbox": cols[k] = pa.array(vals, pa.bool_())
        else: cols[k] = pa.array([None if v is None else str(v) for v in vals], pa.string())
    t = pa.table(cols); md = {b"geoprop_pipeline_version": PIPELINE_VERSION.encode(), b"geoprop_standard_version": STANDARD_VERSION.encode(), b"geoprop_source_spec": canon(src).encode()}
    if src.get("geometry") and bboxes:
        crs = src["geometry"].get("crs", "EPSG:4326")
        md[b"geo"] = json.dumps({"version": "1.1.0", "primary_column": "geometry_original", "columns": {"geometry_original": {"encoding": "WKB", "geometry_types": sorted(geom_types),
                                 "crs": ({"type": "GeographicCRS", "name": "WGS 84", "id": {"authority": "EPSG", "code": 4326}} if crs == "EPSG:4326" else {"id": {"authority": crs.split(":")[0], "code": int(crs.split(":")[1])}}),
                                 "bbox": [min(b[0] for b in bboxes), min(b[1] for b in bboxes), max(b[2] for b in bboxes), max(b[3] for b in bboxes)]}}}).encode()
    return t.replace_schema_metadata(md)


def stage_source_stream(src: dict, batch_id: str, tmpdir: Path, apply: bool, family: str):
    """DuckDB akış yolu (engine='duckdb'): csv | sqlite_table, geometri yok. Satırlar belleğe alınmaz; Parquet doğrudan yazılır.
    raw_* = her sütun VARCHAR; source_row_number = dosya/rowid sırası (tek iş parçacığı); source_row_hash = sha256(sütunlar); review_flags SQL'de.
    CSV parse hataları store_rejects ile karantina kaydına gider (ham satır korunur)."""
    p = Path(src["path"]).expanduser()
    fid, sha, tid = inventory_lookup(str(p), src.get("inventory_table") or src.get("table"))
    assert sha256_bytes(p.read_bytes()) == sha, f"SOURCE_MUTATION_ALERT: {p}"
    con = duckdb.connect(); con.execute("SET memory_limit='2GB'"); con.execute("SET threads=1"); con.execute(f"SET temp_directory='{tmpdir}'")
    tmpdir.mkdir(parents=True, exist_ok=True)
    cp = None; extracted = None; locator = str(p)
    if src.get("zip_member"):
        # ZIP üyesi: diske tmp olarak açılır (ZIP'e dokunulmaz), okunduktan sonra silinir
        extracted = _extract_zip_member(p, src["zip_member"], tmpdir)
        locator = f"{p}::{src['zip_member']}"; p = extracted
    if src["kind"] == "parquet":
        rel = f"read_parquet('{str(p).replace(chr(39), chr(39)*2)}')"; rejects_supported = False
    elif src["kind"] == "jsonl":
        rel = f"read_json('{str(p).replace(chr(39), chr(39)*2)}', format='newline_delimited', maximum_object_size=268435456, ignore_errors=true, union_by_name=true, sample_size=-1)"; rejects_supported = False
    elif src["kind"] == "sqlite_table":
        # kaynak DB DuckDB ile AÇILMAZ (yan dosya riski) → tmp kopya
        cp = tmpdir / (p.name + ".copy"); shutil.copyfile(p, cp)
        wal = Path(str(p) + "-wal")
        if wal.exists() and wal.stat().st_size > 0: shutil.copyfile(wal, Path(str(cp) + "-wal"))
        con.execute("INSTALL sqlite; LOAD sqlite"); con.execute("SET sqlite_all_varchar=true"); con.execute(f"ATTACH '{cp}' AS s (TYPE sqlite, READ_ONLY)")
        rel = f's."{src["table"]}"'
        rejects_supported = False
    else:
        enc = {"utf-8-sig": "utf-8", "utf-8": "utf-8", "latin-1": "latin-1", "utf-16": "utf-16"}.get(src.get("encoding", "utf-8-sig"), "utf-8")
        d = src.get("delimiter", ",").replace("'", "''")
        rel = f"read_csv('{str(p).replace(chr(39), chr(39)*2)}', all_varchar=true, header=true, delim='{d}', quote='\"', escape='\"', store_rejects=true, ignore_errors=true, null_padding=true, strict_mode=false, encoding='{enc}', parallel=false, sample_size=-1)"
        rejects_supported = True
    desc = con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall(); cols = [r[0] for r in desc]
    nested = {r[0] for r in desc if str(r[1]).upper().startswith(("STRUCT", "MAP", "LIST")) or str(r[1]).endswith("[]")}  # iç içe tipler JSON metni olarak saklanır (repr değil)
    if src["kind"] == "csv" and len(cols) == 1:
        head = p.open("rb").readline().decode("utf-8", "replace")
        for alt in (";", ",", "\t", "|"):
            if alt != src.get("delimiter", ",") and head.count(alt) >= 2:
                con.close(); raise ValueError(f"DELIMITER_SUSPECT: tek sütun okundu; başlıkta '{alt}' ×{head.count(alt)} var — spec delimiter yanlış")
    def q(c): return '"' + c.replace('"', '""') + '"'
    def safe(c): return re.sub(r'[^0-9A-Za-z_]', '_', c)
    def rv(c): return f"to_json({q(c)})::VARCHAR" if c in nested else f"CAST({q(c)} AS VARCHAR)"
    raw_exprs = ", ".join(f'{rv(c)} AS "raw_{safe(c)}"' for c in cols)
    hash_expr = "sha256(concat_ws(chr(31), " + ", ".join(f"coalesce({rv(c)}, chr(0))" for c in cols) + "))"
    # review flags: kaynağın projeksiyon sütunu varsa onu kullan; yoksa dönem > kesim tarihi → şüpheli projeksiyon
    flag_parts = [f"'{f}'" for f in src.get("review_flags", [])]
    period_col = next((c for c in cols if c.lower() in ("ay", "donem", "period", "yil_ay")), None)
    proj_col = next((c for c in cols if c.lower() in ("projeksiyon", "projection", "is_projection")), None)
    cutoff = src.get("observation_cutoff", "2026-08")
    if proj_col:
        flag_parts.append(f"CASE WHEN CAST({q(proj_col)} AS VARCHAR) IN ('1','true','True') THEN 'SOURCE_MARKED_PROJECTION' END")
    if period_col:
        flag_parts.append(f"CASE WHEN CAST({q(period_col)} AS VARCHAR) > '{cutoff}' THEN 'C03_PERIOD_AFTER_CUTOFF_{cutoff}' END")
    flags_expr = ("to_json(list_filter([" + ", ".join(flag_parts) + "], x -> x IS NOT NULL))") if flag_parts else "'[]'"
    consts = {"source_file_id": fid, "source_table_id": tid, "source_file_sha256": sha, "source_relative_path": locator,
              "acquisition_class": src.get("acquisition_class", "web_research"), "distribution_class": src.get("distribution_class", "public"), "sensitivity": src.get("sensitivity", "none"),
              "transformation_id": src.get("transformation_id", "T00_RAW_COPY_V1"), "import_batch_id": batch_id, "pipeline_version": PIPELINE_VERSION, "standard_version": STANDARD_VERSION, "staged_at": now()}
    const_exprs = ", ".join(f"'{str(v).replace(chr(39), chr(39)*2)}' AS {k}" if v is not None else f"NULL::VARCHAR AS {k}" for k, v in consts.items())
    select = f"SELECT {raw_exprs}, row_number() OVER () AS source_row_number, {hash_expr} AS source_row_hash, {const_exprs}, {flags_expr} AS review_flags FROM {rel}"
    n_loaded = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
    n_rej = 0
    if rejects_supported:
        try: n_rej = con.execute("SELECT COUNT(*) FROM reject_errors").fetchone()[0]
        except Exception: n_rej = 0  # reject tablosu yalnız hata olunca oluşur
    n_in = n_loaded + n_rej
    raw_lines = None
    if src["kind"] == "csv":
        n = 0
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(8 << 20), b""): n += chunk.count(b"\n")
        raw_lines = n
    rep = {"source_id": src["source_id"], "batch_id": batch_id, "target": src["target"], "file_id": fid, "input_count": n_in, "successfully_loaded": n_loaded, "quarantined": n_rej, "unaccounted": 0,
           "columns": len(cols), "raw_line_count": raw_lines, "engine": "duckdb", "note": f"{src['kind']}"}
    if raw_lines is not None and abs(raw_lines - 1 - n_in) > max(2, int(0.001 * raw_lines)):
        rep["note"] += f" LINE_COUNT_MISMATCH raw_lines={raw_lines} accounted={n_in + 1} (çok satırlı alan olabilir)"
    if apply:
        target_dir = OUT_ROOT / "staging" / f"v{PIPELINE_VERSION}" / family / src["target"]
        if src.get("mode") == "quarantine": target_dir = OUT_ROOT / "quarantine" / "staged" / family / src["target"]
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / f"part-{batch_id}.parquet"
        if out.exists(): rep["error"] = f"{out} zaten var (append-only)"; con.close(); return rep
        if n_in == 0:
            rep["readback_ok"] = True; rep["output"] = None; rep["note"] += " EMPTY_SOURCE_TABLE (0 satır; dosya yazılmadı)"
            audit = {"timestamp": now(), "operation": "phase2_stage_stream", "family": family, "source_id": src["source_id"], "batch_id": batch_id, "input": src["path"], "table": src.get("table"), "output": None,
                     "records_read": 0, "records_written": 0, "records_quarantined": 0, "readback_ok": True, "pipeline_version": PIPELINE_VERSION, "code_hash": sha256_bytes(Path(__file__).read_bytes()), "zip_member": src.get("zip_member"), "note": "EMPTY_SOURCE_TABLE"}
            with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
            con.close(); return rep
        con.execute(f"COPY ({select}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        n_back = duckdb.connect().execute(f"SELECT COUNT(*) FROM '{out}'").fetchone()[0]
        rep["readback_ok"] = (n_back == n_loaded); rep["output"] = str(out)
        if n_rej:
            qd = OUT_ROOT / "quarantine" / "records"; qd.mkdir(parents=True, exist_ok=True)
            con.execute(f"COPY (SELECT '{fid}' AS source_file_id, '{str(p).replace(chr(39), chr(39)*2)}' || '#line=' || CAST(line AS VARCHAR) AS source_location, csv_line AS raw_data, error_type::VARCHAR AS error_type, error_message, '{PIPELINE_VERSION}' AS pipeline_version, '{now()}' AS created_at, line AS source_row_number, column_name FROM reject_errors) TO '{qd / (batch_id + '_quarantined_records.parquet')}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        flags = con.execute(f"SELECT f, count(*) FROM (SELECT unnest(from_json(review_flags, '[\"VARCHAR\"]')) f FROM '{out}') GROUP BY 1").fetchall()
        rep["review_flags"] = {k: v for k, v in flags}
        audit = {"timestamp": now(), "operation": "phase2_stage_stream", "family": family, "source_id": src["source_id"], "batch_id": batch_id, "input": src["path"], "table": src.get("table"),
                 "output": str(out), "records_read": n_in, "records_written": n_back, "records_quarantined": n_rej, "readback_ok": rep["readback_ok"], "pipeline_version": PIPELINE_VERSION,
                 "code_hash": sha256_bytes(Path(__file__).read_bytes()), "zip_member": src.get("zip_member")}
        with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
    con.close()
    if cp:
        for qf in (cp, Path(str(cp) + "-wal"), Path(str(cp) + "-shm")): qf.unlink(missing_ok=True)
    if extracted: extracted.unlink(missing_ok=True)
    return rep


def stage_source_geo_stream(src: dict, batch_id: str, tmpdir: Path, apply: bool, family: str):
    """engine='duckdb_geo': GeoJSON/GPKG/KML → DuckDB spatial ST_Read; geometri WKB olarak; öznitelikler raw_*; satırlar belleğe alınmaz."""
    p = Path(src["path"]).expanduser()
    fid, sha, tid = inventory_lookup(str(p), src.get("inventory_table") or src.get("table"))
    assert sha256_bytes(p.read_bytes()) == sha, f"SOURCE_MUTATION_ALERT: {p}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    src_path = p; locator = str(p)
    if src.get("zip_member"):
        src_path = _extract_zip_member(p, src["zip_member"], tmpdir); locator = f"{p}::{src['zip_member']}"
    elif p.suffix.lower() in (".gpkg", ".sqlite", ".db"):
        src_path = tmpdir / (p.name + ".copy"); shutil.copyfile(p, src_path)  # GDAL kaynağı açmasın
    con = duckdb.connect(); con.execute("INSTALL spatial; LOAD spatial"); con.execute("SET memory_limit='2GB'"); con.execute("SET threads=1"); con.execute(f"SET temp_directory='{tmpdir}'")
    ps = str(src_path).replace("'", "''")
    layer = src.get("layer")
    rel = f"ST_Read('{ps}'" + (f", layer='{layer.replace(chr(39), chr(39)*2)}'" if layer else "") + ", keep_wkb=true)"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
    gcol = "wkb_geometry" if "wkb_geometry" in cols else next((c for c in cols if c.lower() in ("geom", "geometry")), None)
    attrs = [c for c in cols if c != gcol]
    def q(c): return '"' + c.replace('"', '""') + '"'
    def safe(c): return re.sub(r'[^0-9A-Za-z_]', '_', c)
    raw_exprs = ", ".join(f'CAST({q(c)} AS VARCHAR) AS "raw_{safe(c)}"' for c in attrs) or "NULL AS raw__none"
    hash_expr = "sha256(concat_ws(chr(31), " + ", ".join([f"coalesce(CAST({q(c)} AS VARCHAR), chr(0))" for c in attrs] + ([f"coalesce(CAST({q(gcol)} AS VARCHAR), chr(0))"] if gcol else [])) + "))"
    geom_exprs = (f"{q(gcol)} AS geometry_original, CASE WHEN {q(gcol)} IS NULL THEN 'missing' WHEN ST_IsEmpty(ST_GeomFromWKB({q(gcol)})) THEN 'empty' WHEN ST_IsValid(ST_GeomFromWKB({q(gcol)})) THEN 'valid' ELSE 'invalid' END AS geometry_validity_status, "
                  f"ST_XMin(ST_GeomFromWKB({q(gcol)})) AS bbox_xmin, ST_YMin(ST_GeomFromWKB({q(gcol)})) AS bbox_ymin, ST_XMax(ST_GeomFromWKB({q(gcol)})) AS bbox_xmax, ST_YMax(ST_GeomFromWKB({q(gcol)})) AS bbox_ymax, "
                  f"ST_GeometryType(ST_GeomFromWKB({q(gcol)}))::VARCHAR AS geometry_type") if gcol else "NULL::BLOB AS geometry_original, 'missing' AS geometry_validity_status, NULL::DOUBLE AS bbox_xmin, NULL::DOUBLE AS bbox_ymin, NULL::DOUBLE AS bbox_xmax, NULL::DOUBLE AS bbox_ymax, NULL::VARCHAR AS geometry_type"
    crs = src.get("crs") or "unspecified"
    consts = {"source_file_id": fid, "source_table_id": tid, "source_file_sha256": sha, "source_relative_path": locator + (f"::{layer}" if layer else ""), "source_crs": crs,
              "acquisition_class": src.get("acquisition_class", "web_research"), "distribution_class": src.get("distribution_class", "public"), "sensitivity": src.get("sensitivity", "none"),
              "transformation_id": "T02_GEOJSON_TEXT_TO_WKB_V1", "import_batch_id": batch_id, "pipeline_version": PIPELINE_VERSION, "standard_version": STANDARD_VERSION, "staged_at": now()}
    const_exprs = ", ".join(f"'{str(v).replace(chr(39), chr(39)*2)}' AS {k}" if v is not None else f"NULL::VARCHAR AS {k}" for k, v in consts.items())
    flags = "to_json(list_filter([CASE WHEN geometry_validity_status='invalid' THEN 'GEOMETRY_INVALID' END, CASE WHEN geometry_validity_status='missing' THEN 'GEOMETRY_MISSING' END" + "".join(f", '{f}'" for f in src.get("review_flags", [])) + "], x -> x IS NOT NULL))"
    select = f"SELECT *, {flags} AS review_flags FROM (SELECT {raw_exprs}, {geom_exprs}, row_number() OVER () AS source_row_number, {hash_expr} AS source_row_hash, {const_exprs} FROM {rel})"
    n_in = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
    rep = {"source_id": src["source_id"], "batch_id": batch_id, "target": src["target"], "file_id": fid, "input_count": n_in, "successfully_loaded": n_in, "quarantined": 0, "unaccounted": 0, "columns": len(attrs), "engine": "duckdb_geo", "note": f"geo layer={layer}"}
    if apply:
        target_dir = OUT_ROOT / "staging" / f"v{PIPELINE_VERSION}" / family / src["target"]
        if src.get("mode") == "quarantine": target_dir = OUT_ROOT / "quarantine" / "staged" / family / src["target"]
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / f"part-{batch_id}.parquet"
        if out.exists(): con.close(); rep["error"] = f"{out} zaten var (append-only)"; return rep
        con.execute(f"COPY ({select}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        n_back = duckdb.connect().execute(f"SELECT COUNT(*) FROM '{out}'").fetchone()[0]
        rep["readback_ok"] = (n_back == n_in); rep["output"] = str(out)
        rep["review_flags"] = {k: v for k, v in con.execute(f"SELECT f, count(*) FROM (SELECT unnest(from_json(review_flags, '[\"VARCHAR\"]')) f FROM '{out}') GROUP BY 1").fetchall()}
        audit = {"timestamp": now(), "operation": "phase2_stage_geo_stream", "family": family, "source_id": src["source_id"], "batch_id": batch_id, "input": str(p), "table": layer, "output": str(out), "records_read": n_in, "records_written": n_back, "records_quarantined": 0, "readback_ok": rep["readback_ok"], "pipeline_version": PIPELINE_VERSION, "code_hash": sha256_bytes(Path(__file__).read_bytes()), "zip_member": src.get("zip_member")}
        with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
    con.close()
    if src_path != p: src_path.unlink(missing_ok=True)
    return rep


def stage_source_sqlite_chunked(src: dict, batch_id: str, tmpdir: Path, apply: bool, family: str, chunk: int = 50000):
    """engine='sqlite_chunked': WITHOUT ROWID tabloları için Python sqlite3 ile parça parça Parquet (satır sırası = cursor sırası)."""
    p = Path(src["path"]).expanduser()
    fid, sha, tid = inventory_lookup(str(p), src.get("inventory_table") or src.get("table"))
    assert sha256_bytes(p.read_bytes()) == sha, f"SOURCE_MUTATION_ALERT: {p}"
    locator = str(p); extracted = None
    if src.get("zip_member"):
        extracted = _extract_zip_member(p, src["zip_member"], tmpdir)
        locator = f"{p}::{src['zip_member']}"; p = extracted
    con, cp = open_sqlite_ro(p, tmpdir); con.text_factory = lambda b: b.decode("utf-8", "replace")
    n_in = con.execute(f'SELECT COUNT(*) FROM "{src["table"]}"').fetchone()[0]
    cur = con.execute(f'SELECT * FROM "{src["table"]}"'); header = [d[0] for d in cur.description]
    def safe(c): return re.sub(r'[^0-9A-Za-z_]', '_', c)
    period_col = next((c for c in header if c.lower() in ("ay", "donem", "period", "yil_ay")), None)
    proj_col = next((c for c in header if c.lower() in ("projeksiyon", "projection", "is_projection")), None)
    cutoff = src.get("observation_cutoff", "2026-08"); base_flags = list(src.get("review_flags", []))
    consts = {"source_file_id": fid, "source_table_id": tid, "source_file_sha256": sha, "source_relative_path": locator,
              "acquisition_class": src.get("acquisition_class", "web_research"), "distribution_class": src.get("distribution_class", "public"), "sensitivity": src.get("sensitivity", "none"),
              "transformation_id": src.get("transformation_id", "T00_RAW_COPY_V1"), "import_batch_id": batch_id, "pipeline_version": PIPELINE_VERSION, "standard_version": STANDARD_VERSION, "staged_at": now()}
    rep = {"source_id": src["source_id"], "batch_id": batch_id, "target": src["target"], "file_id": fid, "input_count": n_in, "successfully_loaded": 0, "quarantined": 0, "unaccounted": 0, "columns": len(header), "engine": "sqlite_chunked", "note": "sqlite WITHOUT ROWID"}
    writer = None; out = None; n_written = 0; flag_counts = {}
    if apply:
        target_dir = OUT_ROOT / "staging" / f"v{PIPELINE_VERSION}" / family / src["target"]
        if src.get("mode") == "quarantine": target_dir = OUT_ROOT / "quarantine" / "staged" / family / src["target"]
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / f"part-{batch_id}.parquet"
        if out.exists(): con.close(); rep["error"] = f"{out} zaten var (append-only)"; return rep
    rowno = 0
    while True:
        rows = cur.fetchmany(chunk)
        if not rows: break
        cols = {f"raw_{safe(h)}": [] for h in header}; rn = []; rh = []; fl = []
        for r in rows:
            rowno += 1
            vals = [None if v is None else (v if isinstance(v, str) else (v.hex() if isinstance(v, (bytes, bytearray)) else str(v))) for v in r]
            for h, v in zip(header, vals): cols[f"raw_{safe(h)}"].append(v)
            rn.append(rowno); rh.append(hashlib.sha256("\x1f".join("\x00" if v is None else v for v in vals).encode()).hexdigest())
            f = list(base_flags)
            if proj_col and vals[header.index(proj_col)] in ("1", "true", "True"): f.append("SOURCE_MARKED_PROJECTION")
            if period_col and (vals[header.index(period_col)] or "") > cutoff: f.append(f"C03_PERIOD_AFTER_CUTOFF_{cutoff}")
            for x in f: flag_counts[x] = flag_counts.get(x, 0) + 1
            fl.append(json.dumps(f))
        n_written += len(rows)
        if apply:
            arrays = {k: pa.array(v, pa.string()) for k, v in cols.items()}
            arrays["source_row_number"] = pa.array(rn, pa.int64()); arrays["source_row_hash"] = pa.array(rh, pa.string())
            for k, v in consts.items(): arrays[k] = pa.array([v] * len(rows), pa.string())
            arrays["review_flags"] = pa.array(fl, pa.string())
            t = pa.table(arrays)
            if writer is None: writer = pq.ParquetWriter(out, t.schema, compression="zstd")
            writer.write_table(t)
    if writer: writer.close()
    con.close()
    if cp:
        for qf in (cp, Path(str(cp) + "-wal"), Path(str(cp) + "-shm")): qf.unlink(missing_ok=True)
    if extracted: extracted.unlink(missing_ok=True)
    rep["successfully_loaded"] = n_written; rep["unaccounted"] = n_in - n_written; rep["review_flags"] = flag_counts
    if apply and n_in == 0:
        rep["readback_ok"] = True; rep["output"] = None; rep["note"] += " EMPTY_SOURCE_TABLE"
        audit = {"timestamp": now(), "operation": "phase2_stage_sqlite_chunked", "family": family, "source_id": src["source_id"], "batch_id": batch_id, "input": src["path"], "table": src.get("table"), "output": None,
                 "records_read": 0, "records_written": 0, "records_quarantined": 0, "readback_ok": True, "pipeline_version": PIPELINE_VERSION, "code_hash": sha256_bytes(Path(__file__).read_bytes()), "zip_member": src.get("zip_member"), "note": "EMPTY_SOURCE_TABLE"}
        with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
        return rep
    if apply:
        n_back = duckdb.connect().execute(f"SELECT COUNT(*) FROM '{out}'").fetchone()[0]
        rep["readback_ok"] = (n_back == n_written); rep["output"] = str(out)
        audit = {"timestamp": now(), "operation": "phase2_stage_sqlite_chunked", "family": family, "source_id": src["source_id"], "batch_id": batch_id, "input": src["path"], "table": src.get("table"),
                 "output": str(out), "records_read": n_in, "records_written": n_back, "records_quarantined": 0, "readback_ok": rep["readback_ok"], "pipeline_version": PIPELINE_VERSION, "code_hash": sha256_bytes(Path(__file__).read_bytes()), "zip_member": src.get("zip_member")}
        with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
    return rep


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--spec", required=True)
    g = ap.add_mutually_exclusive_group(required=True); g.add_argument("--dry-run", action="store_true"); g.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="virgülle ayrılmış source_id listesi")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    family = spec["family"]; day = dt.datetime.now().strftime("%Y_%m_%d")
    tmpdir = OUT_ROOT / "tmp" / f"stage_{family}_{day}"
    only = {x.strip() for x in a.only.split(",")} if a.only else None
    summary = []
    for src in spec["sources"]:
        if only and src["source_id"] not in only: continue
        if src.get("mode") == "reference_only":
            summary.append({"source_id": src["source_id"], "mode": "reference_only", "note": src.get("note")}); continue
        batch_id = f"BATCH_{day}_{family}_{src['source_id']}"
        t0 = time.time()
        if src.get("engine") in ("duckdb", "sqlite_chunked", "duckdb_geo"):
            try:
                rep = {"duckdb": stage_source_stream, "sqlite_chunked": stage_source_sqlite_chunked, "duckdb_geo": stage_source_geo_stream}[src["engine"]](src, batch_id, tmpdir, a.apply, family)
            except Exception as e:
                rep = {"source_id": src["source_id"], "batch_id": batch_id, "target": src["target"], "input_count": 0, "successfully_loaded": 0, "quarantined": 0, "unaccounted": 0, "error": f"{type(e).__name__}: {str(e)[:300]}"}
            rep["seconds"] = round(time.time() - t0, 1)
            summary.append(rep); print(json.dumps(rep, ensure_ascii=False))
            if rep.get("error"): print("HATA:", rep["error"], file=sys.stderr)
            elif a.apply: print(f"  YAZILDI {Path(rep['output']).name if rep.get('output') else '(boş tablo, dosya yok)'} ({rep['successfully_loaded']} satır, readback_ok={rep.get('readback_ok')}, karantina={rep['quarantined']})")
            (OUT_ROOT / "validation").mkdir(exist_ok=True)
            (OUT_ROOT / "validation" / f"{batch_id}_balance.json").write_text(json.dumps({**rep, "mode": "apply" if a.apply else "dry-run"}, ensure_ascii=False, indent=1))
            continue
        try:
            fid, header, rows, n_in, note, gtypes, bboxes = stage_source(src, batch_id, tmpdir)
        except Exception as e:
            rep = {"source_id": src["source_id"], "batch_id": batch_id, "target": src["target"], "input_count": 0, "successfully_loaded": 0, "quarantined": 0, "unaccounted": 0, "error": f"{type(e).__name__}: {str(e)[:300]}", "seconds": round(time.time() - t0, 1)}
            summary.append(rep); print(json.dumps(rep, ensure_ascii=False)); print("HATA:", rep["error"], file=sys.stderr)
            (OUT_ROOT / "validation").mkdir(exist_ok=True)
            (OUT_ROOT / "validation" / f"{batch_id}_balance.json").write_text(json.dumps({**rep, "mode": "apply" if a.apply else "dry-run"}, ensure_ascii=False, indent=1))
            continue
        unacc = n_in - len(rows)
        est = sum(len(r["raw_row_json"]) for r in rows) * 0.5 + sum(len(r.get("geometry_original") or b"") for r in rows) * 0.8
        flags = {}
        for r in rows:
            for f in json.loads(r["review_flags"]): flags[f] = flags.get(f, 0) + 1
        rep = {"source_id": src["source_id"], "batch_id": batch_id, "target": src["target"], "file_id": fid, "input_count": n_in, "successfully_loaded": len(rows), "quarantined": 0, "unaccounted": unacc,
               "columns": len(header), "geometry_types": sorted(gtypes), "review_flags": flags, "estimated_bytes": int(est), "note": note, "seconds": round(time.time() - t0, 1)}
        summary.append(rep)
        print(json.dumps(rep, ensure_ascii=False))
        if unacc != 0: print(f"HATA UNACCOUNTED={unacc} — yazılmadı", file=sys.stderr); continue
        if a.apply and rows:
            target_dir = OUT_ROOT / "staging" / f"v{PIPELINE_VERSION}" / family / src["target"]
            if src.get("distribution_class") == "quarantine" or src.get("mode") == "quarantine":
                target_dir = OUT_ROOT / "quarantine" / "staged" / family / src["target"]
            target_dir.mkdir(parents=True, exist_ok=True)
            out = target_dir / f"part-{batch_id}.parquet"
            if out.exists(): print(f"HATA: {out} zaten var (append-only)", file=sys.stderr); continue
            pq.write_table(to_table(rows, src, gtypes, bboxes), out, compression="zstd")
            n_back = duckdb.connect().execute(f"SELECT COUNT(*) FROM '{out}'").fetchone()[0]
            audit = {"timestamp": now(), "operation": "phase2_stage_generic", "family": family, "source_id": src["source_id"], "batch_id": batch_id, "input": src["path"], "table": src.get("table"), "zip_member": src.get("zip_member"),
                     "output": str(out), "records_read": n_in, "records_written": n_back, "records_quarantined": 0, "readback_ok": n_back == len(rows), "pipeline_version": PIPELINE_VERSION,
                     "code_hash": sha256_bytes(Path(__file__).read_bytes())}
            with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
            print(f"  YAZILDI {out.name} ({n_back} satır, readback_ok={audit['readback_ok']})")
        (OUT_ROOT / "validation").mkdir(exist_ok=True)
        (OUT_ROOT / "validation" / f"{batch_id}_balance.json").write_text(json.dumps({**rep, "mode": "apply" if a.apply else "dry-run"}, ensure_ascii=False, indent=1))
    shutil.rmtree(tmpdir, ignore_errors=True)
    print(json.dumps({"family": family, "sources": len(summary), "total_input": sum(s.get("input_count", 0) for s in summary), "total_loaded": sum(s.get("successfully_loaded", 0) for s in summary),
                      "total_unaccounted": sum(s.get("unaccounted", 0) for s in summary)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
