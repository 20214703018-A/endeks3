#!/usr/bin/env python3
"""
PHASE 2 — STAGING, Aile 1: reference_geography — S1/S2 (poligonlar/city_*.json, county_*.json)

Çıktı: staging/v{PIPELINE}/reference_geography/stg_admin_polygon/il_kodu=NN/part-*.parquet  (GeoParquet 1.1, EPSG:4326)
Kaynak: salt okunur. Her poligon = 1 satır: raw_* (metin), typed_*, geometry_original (WKB), provenance.
Sayım denklemi her dosya için: input = staged + quarantined; UNACCOUNTED ≠ 0 → batch yazılmaz.

Kullanım:
  python3 phase2_stage_reference_geography.py --dry-run [--il 1]
  python3 phase2_stage_reference_geography.py --apply --il 1        (pilot: Adana)
  python3 phase2_stage_reference_geography.py --apply               (81 il)
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, shutil, sys, time
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
import duckdb
from shapely.geometry import Polygon, MultiPolygon
from shapely import wkb as shp_wkb
from shapely.validation import explain_validity

PIPELINE_VERSION = "1.0.0"
STANDARD_VERSION = "1.0.0"
OUT_ROOT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
SRC_DIR = Path.home() / "Desktop" / "GEOPROP" / "collector" / "data" / "poligonlar"
STG = OUT_ROOT / "staging" / f"v{PIPELINE_VERSION}" / "reference_geography" / "stg_admin_polygon"
TRANSFORM_ID = "T01_LATLON_PAIRS_TO_WKB_V1"
TR_BBOX = (25.5, 35.7, 45.0, 42.3)


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def sha256_bytes(b: bytes): return hashlib.sha256(b).hexdigest()
def canon(obj): return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_inventory_ids():
    con = duckdb.connect()
    rows = con.execute(f"SELECT absolute_path, file_id, sha256 FROM '{OUT_ROOT}/inventory/inventory.parquet' WHERE absolute_path LIKE '{SRC_DIR}/%'").fetchall()
    tids = con.execute(f"SELECT i.absolute_path, t.table_id FROM '{OUT_ROOT}/inventory/tables.parquet' t JOIN '{OUT_ROOT}/inventory/inventory.parquet' i ON t.file_id=i.file_id WHERE i.absolute_path LIKE '{SRC_DIR}/%'").fetchall()
    tid = {p: t for p, t in tids}
    return {p: (fid, sha, tid.get(p)) for p, fid, sha in rows}


def rings_to_geometry(coords):
    """[[{latitude,longitude},...], ...] -> shapely geometry. Değerler değiştirilmez (yuvarlama yok).
    1 halka → Polygon; >1 halka: sonraki halkalar ilkinin içindeyse delik, değilse ayrı poligon (MultiPolygon). Karar 'construction' alanına yazılır."""
    rings = []
    closed_by_pipeline = False
    for ring in coords:
        pts = [(float(p["longitude"]), float(p["latitude"])) for p in ring]
        if len(pts) >= 1 and pts[0] != pts[-1]:
            pts.append(pts[0]); closed_by_pipeline = True
        rings.append(pts)
    if not rings or len(rings[0]) < 4:
        return None, "degenerate", closed_by_pipeline
    if len(rings) == 1:
        return Polygon(rings[0]), "polygon_single_ring", closed_by_pipeline
    outer = Polygon(rings[0])
    holes, extra = [], []
    for r in rings[1:]:
        if len(r) < 4: continue
        pr = Polygon(r)
        (holes if outer.contains(pr.representative_point()) else extra).append(r if outer.contains(pr.representative_point()) else pr)
    if extra:
        polys = [Polygon(rings[0], holes)] + extra
        return MultiPolygon(polys), "multipolygon_disjoint_rings", closed_by_pipeline
    return Polygon(rings[0], holes), "polygon_with_holes", closed_by_pipeline


def stage_file(path: Path, level: str, file_id: str, sha: str, table_id: str | None, batch_id: str):
    raw = path.read_bytes()
    assert sha256_bytes(raw) == sha, f"SOURCE_MUTATION_ALERT: {path} hash değişti"
    doc = json.loads(raw.decode("utf-8"))
    polys = doc.get("polygons") or []
    m = re.match(r"(city|county)_(\d+)(?:_(\d+))?\.json$", path.name)
    file_il = m.group(2); file_ilce = m.group(3)
    rows, quarantined = [], []
    for i, p in enumerate(polys, 1):
        pr = p.get("properties") or {}
        rec = {
            # --- ham (dokunulmamış, metin) ---
            "raw_id": None if p.get("id") is None else str(p["id"]),
            "raw_description": pr.get("description"), "raw_country": pr.get("Country"), "raw_country_id": None if pr.get("CountryId") is None else str(pr.get("CountryId")),
            "raw_city": pr.get("City"), "raw_city_id": None if pr.get("CityId") is None else str(pr.get("CityId")),
            "raw_county": pr.get("County"), "raw_county_id": None if pr.get("CountyId") is None else str(pr.get("CountyId")),
            "raw_district": pr.get("District"), "raw_district_id": None if pr.get("DistrictId") is None else str(pr.get("DistrictId")),
            "raw_population": None if pr.get("Population") is None else str(pr.get("Population")),
            "raw_tooltip": pr.get("tooltipContent"),
            "raw_properties_json": canon(pr),
            "raw_coordinates_json": canon(p.get("coordinates")),
            "raw_extra_keys": canon(sorted(set(p.keys()) - {"id", "coordinates", "properties"})),
            # --- türetilmiş / tipli ---
            "source_level": level,  # ilce (city_*.json) | mahalle (county_*.json)
            "polygon_kind": None, "typed_population": None, "il_kodu": file_il.zfill(2), "file_county_id": file_ilce,
            "geometry_original": None, "geometry_construction": None, "ring_count": len(p.get("coordinates") or []), "vertex_count": None,
            "ring_closed_by_pipeline": False, "geometry_validity_status": None, "geometry_invalid_reason": None,
            "bbox_xmin": None, "bbox_ymin": None, "bbox_xmax": None, "bbox_ymax": None, "centroid_lon": None, "centroid_lat": None, "outside_tr_bbox": None,
            # --- provenance ---
            "source_file_id": file_id, "source_table_id": table_id, "source_row_number": i, "source_object_path": f"$.polygons[{i-1}]",
            "source_file_sha256": sha, "source_row_hash": sha256_bytes(canon(p).encode()), "source_relative_path": str(path.relative_to(SRC_DIR.parent.parent.parent)),
            "acquisition_class": "web_research", "distribution_class": "public", "sensitivity": "none",
            "transformation_id": TRANSFORM_ID, "import_batch_id": batch_id, "pipeline_version": PIPELINE_VERSION, "standard_version": STANDARD_VERSION, "staged_at": now(),
            "review_flags": [],
        }
        # polygon_kind: mahalle dosyasında DistrictId=0 → su/ada (F1); ilçe dosyasında CountyId var → admin
        if level == "mahalle":
            rec["polygon_kind"] = "water_or_island" if rec["raw_district_id"] in (None, "0") else "admin"
            if rec["polygon_kind"] == "water_or_island": rec["review_flags"].append("F1_DISTRICT_ID_ZERO_WATER_OR_ISLAND")
        else:
            rec["polygon_kind"] = "admin"
        if rec["raw_population"] is not None:
            try: rec["typed_population"] = int(rec["raw_population"])
            except ValueError: rec["review_flags"].append("POPULATION_NOT_INTEGER")
        elif rec["polygon_kind"] == "admin" and level == "mahalle":
            rec["review_flags"].append("F3_POPULATION_MISSING")
        if level == "mahalle": rec["review_flags"].append("F7_POPULATION_YEAR_UNKNOWN")
        try:
            geom, construction, closed = rings_to_geometry(p.get("coordinates") or [])
            rec["geometry_construction"] = construction; rec["ring_closed_by_pipeline"] = closed
            if geom is None:
                rec["geometry_validity_status"] = "degenerate"; rec["review_flags"].append("GEOMETRY_DEGENERATE")
            else:
                rec["geometry_original"] = shp_wkb.dumps(geom)
                rec["vertex_count"] = sum(len(r) for r in (p.get("coordinates") or []))
                valid = geom.is_valid
                rec["geometry_validity_status"] = "valid" if valid else "invalid"
                if not valid:
                    rec["geometry_invalid_reason"] = explain_validity(geom)[:300]; rec["review_flags"].append("GEOMETRY_INVALID")
                b = geom.bounds; rec["bbox_xmin"], rec["bbox_ymin"], rec["bbox_xmax"], rec["bbox_ymax"] = b
                c = geom.representative_point(); rec["centroid_lon"], rec["centroid_lat"] = c.x, c.y
                rec["outside_tr_bbox"] = not (TR_BBOX[0] <= c.x <= TR_BBOX[2] and TR_BBOX[1] <= c.y <= TR_BBOX[3])
                if rec["outside_tr_bbox"]: rec["review_flags"].append("GEO_CONFLICT_OUTSIDE_TR")
        except Exception as e:
            # geometri kurulamadı → satır yine staging'e girer (ham koordinat korunur), geometri NULL, karantina kaydı açılır
            rec["geometry_validity_status"] = "construction_failed"; rec["review_flags"].append("GEOMETRY_CONSTRUCTION_FAILED")
            quarantined.append({"source_file_id": file_id, "source_location": f"{path}#{rec['source_object_path']}", "raw_data": canon(p)[:100000], "error_type": "GEOMETRY_CONSTRUCTION_FAILED",
                                "error_message": str(e)[:500], "pipeline_version": PIPELINE_VERSION, "created_at": now(), "note": "satır staging'de geometry_original=NULL ile mevcut"})
        rec["review_flags"] = json.dumps(rec["review_flags"])
        rows.append(rec)
    return rows, quarantined, len(polys)


def to_table(rows: list[dict]) -> pa.Table:
    keys = list(rows[0].keys())
    cols = {}
    for k in keys:
        vals = [r[k] for r in rows]
        if k == "geometry_original": cols[k] = pa.array(vals, pa.binary())
        elif k in ("typed_population", "source_row_number", "ring_count", "vertex_count"): cols[k] = pa.array(vals, pa.int64())
        elif k in ("bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax", "centroid_lon", "centroid_lat"): cols[k] = pa.array(vals, pa.float64())
        elif k in ("ring_closed_by_pipeline", "outside_tr_bbox"): cols[k] = pa.array(vals, pa.bool_())
        else: cols[k] = pa.array(vals, pa.string())
    t = pa.table(cols)
    geo_meta = {"version": "1.1.0", "primary_column": "geometry_original",
                "columns": {"geometry_original": {"encoding": "WKB", "geometry_types": ["Polygon", "MultiPolygon"], "crs": {"type": "GeographicCRS", "name": "WGS 84", "id": {"authority": "EPSG", "code": 4326}},
                                                  "orientation": None, "edges": "planar", "bbox": [min(v for v in cols["bbox_xmin"].to_pylist() if v is not None), min(v for v in cols["bbox_ymin"].to_pylist() if v is not None),
                                                                                                    max(v for v in cols["bbox_xmax"].to_pylist() if v is not None), max(v for v in cols["bbox_ymax"].to_pylist() if v is not None)]}}}
    md = {b"geo": json.dumps(geo_meta).encode(), b"geoprop_pipeline_version": PIPELINE_VERSION.encode(), b"geoprop_standard_version": STANDARD_VERSION.encode()}
    return t.replace_schema_metadata(md)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True); g.add_argument("--dry-run", action="store_true"); g.add_argument("--apply", action="store_true")
    ap.add_argument("--il", type=int, default=None, help="yalnız bu plaka (pilot)")
    ap.add_argument("--exclude-il", type=str, default="", help="virgülle ayrılmış plaka listesi; bunlar atlanır (zaten yazılmış partisyonlar)")
    a = ap.parse_args()
    inv = load_inventory_ids()
    files = sorted(SRC_DIR.glob("city_*.json")) + sorted(SRC_DIR.glob("county_*.json"))
    if a.il: files = [f for f in files if re.match(rf"(city|county)_{a.il}(_|\.)", f.name)]
    excl = {x.strip() for x in a.exclude_il.split(",") if x.strip()}
    if excl: files = [f for f in files if re.match(r"(city|county)_(\d+)", f.name).group(2) not in excl]
    day = dt.datetime.now().strftime("%Y_%m_%d")
    batch_id = f"BATCH_{day}_reference_geography_admin_polygon_{'il' + str(a.il) if a.il else 'all'}"
    t0 = time.time(); total_in = total_staged = total_q = 0; per_file = []; all_rows = []; all_q = []; missing_inv = []
    for f in files:
        p = str(f); fid, sha, tid = inv.get(p, (None, None, None))
        if fid is None: missing_inv.append(p); continue
        level = "ilce" if f.name.startswith("city_") else "mahalle"
        rows, q, n_in = stage_file(f, level, fid, sha, tid, batch_id)
        total_in += n_in; total_staged += len(rows); total_q += len(q)
        per_file.append({"file": f.name, "file_id": fid, "input": n_in, "staged": len(rows), "quarantined": len(q), "unaccounted": n_in - len(rows)})
        all_rows.extend(rows); all_q.extend(q)
    unaccounted = total_in - total_staged  # karantina satırları da staging'de (geometry NULL) olduğundan eşitlik input=staged
    kinds = {}
    for r in all_rows: kinds[(r["source_level"], r["polygon_kind"])] = kinds.get((r["source_level"], r["polygon_kind"]), 0) + 1
    invalid = sum(1 for r in all_rows if r["geometry_validity_status"] == "invalid"); outside = sum(1 for r in all_rows if r["outside_tr_bbox"])
    est_bytes = sum(len(r["raw_coordinates_json"]) for r in all_rows) * 0.45 + sum(len(r["geometry_original"] or b"") for r in all_rows) * 0.8
    report = {"batch_id": batch_id, "mode": "dry-run" if a.dry_run else "apply", "pipeline_version": PIPELINE_VERSION, "files": len(files), "files_missing_in_inventory": missing_inv,
              "input_count": total_in, "successfully_loaded": total_staged, "quarantined_records": total_q, "unaccounted": unaccounted,
              "rows_by_level_kind": {f"{k[0]}/{k[1]}": v for k, v in kinds.items()}, "invalid_geometries": invalid, "outside_tr_bbox": outside,
              "estimated_output_bytes": int(est_bytes), "free_disk_bytes": shutil.disk_usage(OUT_ROOT).free, "seconds": round(time.time() - t0, 1), "at": now()}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    (OUT_ROOT / "validation").mkdir(exist_ok=True)
    (OUT_ROOT / "validation" / f"{batch_id}_balance.json").write_text(json.dumps({**report, "per_file": per_file}, ensure_ascii=False, indent=1))
    if missing_inv: print("HATA: envanterde olmayan dosya var; önce Phase 1 artımlı tarama", file=sys.stderr); sys.exit(2)
    if unaccounted != 0: print("HATA: UNACCOUNTED != 0; batch yazılmadı", file=sys.stderr); sys.exit(3)
    if a.dry_run: return
    if report["free_disk_bytes"] < est_bytes * 3 + 1_000_000_000: print("HATA: disk yetersiz", file=sys.stderr); sys.exit(4)
    # yaz: il_kodu bölümlemesi; var olan bölüm üzerine YAZILMAZ (append-only) → aynı batch tekrar koşarsa hata
    by_il = {}
    for r in all_rows: by_il.setdefault(r["il_kodu"], []).append(r)
    written = []
    for il, rows in sorted(by_il.items()):
        d = STG / f"il_kodu={il}"; d.mkdir(parents=True, exist_ok=True)
        out = d / f"part-{batch_id}.parquet"
        if out.exists(): print(f"HATA: {out} zaten var (append-only); yeni batch id gerekir", file=sys.stderr); sys.exit(5)
        pq.write_table(to_table(rows), out, compression="zstd"); written.append(str(out))
    if all_q:
        qd = OUT_ROOT / "quarantine" / "records"; qd.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(all_q), qd / f"{batch_id}_quarantined_records.parquet", compression="zstd")
    # doğrulama: yazılanı geri oku, say
    con = duckdb.connect(); con.execute("INSTALL spatial; LOAD spatial")
    n_back = sum(con.execute(f"SELECT COUNT(*) FROM '{w}'").fetchone()[0] for w in written)
    ok = n_back == total_staged
    audit = {"timestamp": now(), "operation": "phase2_stage", "family": "reference_geography", "table": "stg_admin_polygon", "batch_id": batch_id, "input": str(SRC_DIR), "output_files": written,
             "records_read": total_in, "records_written": n_back, "records_quarantined": total_q, "readback_ok": ok, "pipeline_version": PIPELINE_VERSION, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with open(OUT_ROOT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
    (OUT_ROOT / "transformations" / f"{TRANSFORM_ID}.json").write_text(json.dumps({"transformation_id": TRANSFORM_ID, "name": "latlon pair rings → WKB polygon", "version": "1",
        "description": "[[{latitude,longitude},…],…] → Polygon/MultiPolygon WKB (EPSG:4326). Koordinat değerleri değiştirilmez; açık halka kapatılırsa ring_closed_by_pipeline=true. >1 halka: içteki=delik, dıştaki=ayrı poligon (geometry_construction). Orijinal koordinat listesi raw_coordinates_json'da değer-birebir (JSON kanonik biçim) korunur; kaynak dosya değişmezdir.",
        "input": "collector/data/poligonlar/*.json", "output": str(STG), "code_hash": audit["code_hash"], "executed_at": now(), "deterministic": True}, ensure_ascii=False, indent=1))
    print(f"YAZILDI: {len(written)} parquet, {n_back} satır, readback_ok={ok}")
    if not ok: sys.exit(6)


if __name__ == "__main__":
    main()
