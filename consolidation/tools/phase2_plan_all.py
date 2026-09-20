#!/usr/bin/env python3
"""
PHASE 2 — TAM GEÇİŞ PLANI: envanterdeki her veri nesnesi için staging spec üretir.

Kurallar:
- Zaten staging'e alınmış (audit_log readback_ok) (yol, tablo, zip üyesi) tekrar alınmaz.
- Aynı sha256'lı kopyalar: tek birincil kopya staging'e, diğerleri mappings/file_alias (doğrulanmış: hash eşit).
- ZIP üyeleri: üye akışla hash'lenir; envanterde aynı sha'lı açık dosya varsa → alias (doğrulanmış); yoksa tmp'ye açılıp okunur.
- warehouse/bronze|silver Parquet → reference_only (önceki pipeline türevi); *.meta.json (intake meta) → skip; config JSON (kayıt yok) → skip; PBF → deferred.
- Karantina kaydı QUARANTINE olan dosya → mode=quarantine (quarantine/staged altına yazılır).
- Motor: csv/sqlite/parquet/jsonl → duckdb; sqlite WITHOUT ROWID → sqlite_chunked; geo → duckdb_geo; json kayıt dizisi/xlsx/cp125x csv → python.
"""
from __future__ import annotations
import hashlib, json, re, sys, zipfile, datetime as dt
from collections import defaultdict
from pathlib import Path
import duckdb
sys.path.insert(0, str(Path(__file__).parent)); from ziputil import member_sha256

OUT_ROOT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
INV = OUT_ROOT / "inventory"
FAMILY_OF = {
    "administrative_boundaries": "reference_geography", "price": "price_series", "real_estate_listings": "listings", "housing": "listings",
    "cadastre_zoning": "cadastre_zoning", "poi": "poi_business", "restaurants": "poi_business", "restaurant_menu": "poi_business", "reviews": "poi_business", "business": "poi_business",
    "demographics": "demographics_context", "migration": "demographics_context", "elections": "demographics_context", "health": "demographics_context", "socioeconomic": "demographics_context", "education": "education",
    "retail_spending": "economy", "ecommerce_spending": "economy", "finance": "economy", "mobility": "mobility_logistics", "logistics": "mobility_logistics", "vehicles": "vehicles", "jobs": "jobs", "tenders": "unrelated", "ownership": "cadastre_zoning", "unknown": "unclassified",
}
DUCK_ENC = {"utf-8", "utf-8-sig", "ascii", "latin-1", "iso-8859-1", "utf-16", "utf-16le", "utf-16be", None, "unknown"}
ROOT_PREF = {"/Users/acar/Desktop/GEOPROP_RAW_INTAKE": 0, "/Users/acar/Desktop/GEOPROP": 1, "/Users/acar/Desktop/tkgm": 2, "/Users/acar/Downloads": 3, "/Users/acar/Desktop/harita": 4}


def sha256_stream(fobj) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: fobj.read(4 << 20), b""): h.update(chunk)
    return h.hexdigest()


def main():
    c = duckdb.connect()
    inv = {r[0]: dict(zip(["file_id", "absolute_path", "relative_path", "source_root", "filename", "extension", "sha256", "detected_format", "encoding", "size_bytes", "is_data_candidate", "duplicate_group_id"], r))
           for r in c.execute(f"SELECT file_id, absolute_path, relative_path, source_root, filename, extension, sha256, detected_format, encoding, size_bytes, is_data_candidate, duplicate_group_id FROM '{INV}/inventory.parquet'").fetchall()}
    tables = [dict(zip(["table_id", "file_id", "table_name", "member_name", "object_type", "row_count", "column_count", "column_names", "detected_delimiter", "notes", "read_status"], r))
              for r in c.execute(f"SELECT table_id, file_id, table_name, member_name, object_type, row_count, column_count, column_names, detected_delimiter, notes, read_status FROM '{INV}/tables.parquet'").fetchall()]
    cls = {r[0]: r[1] for r in c.execute(f"SELECT file_id, predicted_category FROM '{INV}/classifications.parquet'").fetchall()}
    geo = {(r[0], r[1]): r[2] for r in c.execute(f"SELECT table_id, file_id, crs FROM '{INV}/geo_layers.parquet'").fetchall()}
    qstat = defaultdict(set); qrules = defaultdict(set)
    for fid, st, rule in c.execute(f"SELECT file_id, status, rule_id FROM '{OUT_ROOT}/quarantine/quarantine_registry.parquet'").fetchall():
        qstat[fid].add(st); qrules[fid].add(rule)
    # zaten staging'de olanlar
    staged = set()
    ap = OUT_ROOT / "logs" / "audit_log.jsonl"
    if ap.exists():
        for line in ap.open(encoding="utf-8"):
            try: a = json.loads(line)
            except Exception: continue
            if str(a.get("operation", "")).startswith("phase2_stage") and a.get("readback_ok"):
                staged.add((a.get("input"), a.get("table"), a.get("zip_member")))
    # sha → birincil yol
    by_sha = defaultdict(list)
    for r in inv.values():
        if r["sha256"]: by_sha[r["sha256"]].append(r)
    primary = {}
    for sha, rows in by_sha.items():
        rows.sort(key=lambda r: (ROOT_PREF.get(r["source_root"], 9), r["absolute_path"]))
        primary[sha] = rows[0]["absolute_path"]
    aliases = [{"alias_path": r["absolute_path"], "alias_file_id": r["file_id"], "primary_path": primary[sha], "sha256": sha, "verification": "sha256_equal", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
               for sha, rows in by_sha.items() for r in rows[1:]]
    spec = []; skipped = []; deferred = []; zip_alias = []
    seen_ids = set()
    def sid(base):
        s = re.sub(r"[^0-9A-Za-z_]", "_", base)[:90]; k = s; i = 1
        while k in seen_ids: i += 1; k = f"{s}_{i}"
        seen_ids.add(k); return k
    INTAKE_FAM = [("tkgm_idari_yapi", "reference_geography"), ("google_places", "poi_business"), ("restoran_ve_kafe", "poi_business"), ("yemeksepeti", "poi_business"),
                  ("emlak_ilanlari", "listings"), ("airbnb", "listings"), ("lojistik", "mobility_logistics"), ("darkstore", "mobility_logistics"), ("tuik", "demographics_context")]
    def fam(fid, path):
        if "/GEOPROP_RAW_INTAKE/" in path:
            for key, f in INTAKE_FAM:
                if key in path: return f
            return "official_intake"
        return FAMILY_OF.get(cls.get(fid, "unknown"), "unclassified")
    def base(fid, path, target, kind, table=None, **kw):
        r = inv[fid]
        s = {"source_id": None, "kind": kind, "path": path, "target": target, "engine": kw.pop("engine", "duckdb"),
             "acquisition_class": "official_public" if (("/GEOPROP_RAW_INTAKE/tkgm_idari_yapi/" in path) or re.search(r"(?i)tuik|bddk|kap_|meb_|osm|tkgm", Path(path).name)) else "web_research",
             "distribution_class": "public", "review_flags": sorted(qrules.get(fid, set()))}
        if table: s["table"] = table
        if "QUARANTINE" in qstat.get(fid, set()): s["mode"] = "quarantine"
        s.update(kw); return s
    zip_members_by_zip = defaultdict(list)
    for t in tables:
        r = inv.get(t["file_id"])
        if not r or not r["is_data_candidate"] or t["read_status"] != "ok": continue
        path = r["absolute_path"]; rel = r["relative_path"]
        if r["sha256"] and primary.get(r["sha256"]) != path: continue  # kopya → alias
        if rel.startswith("warehouse/bronze/") or rel.startswith("warehouse/silver/"): deferred.append((path, t["table_name"], "reference_only: önceki pipeline bronze/silver türevi")); continue
        if path.endswith(".meta.json") or r["filename"] in ("fetch_log.jsonl", "hash_cache.jsonl"): skipped.append((path, "intake_metadata")); continue
        if "/yemeksepeti_menu/" in path: skipped.append((path, "live_intake_in_progress (toplayıcı bitince alınacak)")); continue
        if path.endswith(("run.json", "jobs.json", "artifacts.json")) or path.endswith(".log"): skipped.append((path, "intake_metadata")); continue
        if t["member_name"]: zip_members_by_zip[path].append(t); continue
        ot = t["object_type"]; family = fam(r["file_id"], path)
        tname = re.sub(r"[^0-9A-Za-z_]", "_", Path(r["filename"]).stem)[:60]
        if ot == "csv":
            enc = (r["encoding"] or "utf-8")
            if enc.lower() in DUCK_ENC:
                spec.append(base(r["file_id"], path, f"stg_{tname}", "csv", engine="duckdb", delimiter=t["detected_delimiter"] or ",", encoding="utf-8-sig" if enc in ("utf-8-sig", "utf-8") else enc))
            else:
                spec.append(base(r["file_id"], path, f"stg_{tname}", "csv", engine="python", delimiter=t["detected_delimiter"] or ",", encoding=enc))
        elif ot == "sqlite_table":
            wr = "WITHOUT ROWID" in (t["notes"] or "").upper()
            spec.append(base(r["file_id"], path, f"stg_{tname}__{re.sub(r'[^0-9A-Za-z_]', '_', t['table_name'])[:50]}", "sqlite_table", table=t["table_name"], engine="sqlite_chunked" if wr else "duckdb"))
        elif ot == "parquet":
            spec.append(base(r["file_id"], path, f"stg_{tname}", "parquet", engine="duckdb"))
        elif ot == "jsonl":
            spec.append(base(r["file_id"], path, f"stg_{tname}", "jsonl", engine="duckdb"))
        elif ot in ("geo_layer", "geojson"):
            crs = geo.get((t["table_id"], r["file_id"]))
            spec.append(base(r["file_id"], path, f"stg_{tname}", "geojson", engine="duckdb_geo", layer=t["table_name"] if ot == "geo_layer" and r["detected_format"] == "geopackage" else None, crs=crs, inventory_table=t["table_name"]))
        elif ot == "json":
            if (t["row_count"] or 0) <= 1 or (r["size_bytes"] or 0) > 150 * 1024 * 1024:
                skipped.append((path, "json_config_or_document" if (t["row_count"] or 0) <= 1 else "json_too_large_for_python_path")); continue
            spec.append(base(r["file_id"], path, f"stg_{tname}", "json_records", engine="python"))
        elif ot == "xlsx_sheet":
            spec.append(base(r["file_id"], path, f"stg_{tname}__{re.sub(r'[^0-9A-Za-z_]', '_', t['table_name'])[:40]}", "xlsx", table=t["table_name"], engine="python"))
        else:
            skipped.append((path, f"object_type={ot}"))
        if spec and spec[-1]["source_id"] is None:
            spec[-1]["family"] = family; spec[-1]["source_id"] = sid(f"{family}__{tname}__{(t['table_name'] or '')[:30]}")
    # ZIP üyeleri: hash'le → alias ya da stage
    sha_to_primary = {sha: p for sha, p in primary.items()}
    for zpath, members in zip_members_by_zip.items():
        r = inv[[k for k, v in inv.items() if v["absolute_path"] == zpath][0]]
        family = fam(r["file_id"], zpath)
        try:
            z = zipfile.ZipFile(zpath)
        except Exception as e:
            skipped.append((zpath, f"zip_open_failed: {e}")); continue
        member_sha = {}
        for t in members:
            mem = t["member_name"]
            if mem not in member_sha:
                try:
                    member_sha[mem] = member_sha256(zpath, mem, OUT_ROOT / "tmp" / "plan_nested") if "::" in mem else sha256_stream(z.open(mem))
                except Exception as e:
                    member_sha[mem] = None; skipped.append((f"{zpath}::{mem}", f"zip_member_read_failed: {e}"))
            msha = member_sha[mem]
            if "META-INF/" in mem or mem.endswith((".txt", ".log", ".md")):
                skipped.append((f"{zpath}::{mem}", "zip_member_non_data_text")); continue
            if msha and msha in sha_to_primary:
                zip_alias.append({"zip_path": zpath, "member": mem, "member_sha256": msha, "primary_path": sha_to_primary[msha], "verification": "sha256_equal"}); continue
            if msha is None: continue
            ot = t["object_type"]; mname = re.sub(r"[^0-9A-Za-z_]", "_", Path(mem).stem)[:50]
            if ot == "csv":
                s = base(r["file_id"], zpath, f"stg_{mname}", "csv", engine="duckdb", delimiter=t["detected_delimiter"] or ",", encoding="utf-8-sig", zip_member=mem, inventory_table=t["table_name"])
            elif ot == "sqlite_table":
                inner = t["table_name"].split("::")[-1]; wr = "WITHOUT ROWID" in (t["notes"] or "").upper()
                s = base(r["file_id"], zpath, f"stg_{mname}__{re.sub(r'[^0-9A-Za-z_]', '_', inner)[:40]}", "sqlite_table", table=inner, engine="sqlite_chunked" if wr else "duckdb", zip_member=mem, inventory_table=t["table_name"])
            elif ot in ("geojson", "geo_layer"):
                s = base(r["file_id"], zpath, f"stg_{mname}", "geojson", engine="duckdb_geo", zip_member=mem, inventory_table=t["table_name"], crs="EPSG:4326")
            elif ot == "json" and ((t["row_count"] or 0) > 1 or "/poligonlar/" in mem):
                s = base(r["file_id"], zpath, f"stg_{mname}", "json_records", engine="python", zip_member=mem, inventory_table=t["table_name"])
            else:
                skipped.append((f"{zpath}::{mem}", f"zip_member_object_type={ot}")); continue
            s["family"] = family; s["source_id"] = sid(f"{family}__zip__{mname}__{(t['table_name'].split('::')[-1])[:20]}"); s["member_sha256"] = msha
            spec.append(s)
    # zaten alınmışları çıkar
    before = len(spec)
    spec = [s for s in spec if (s["path"], s.get("table"), s.get("zip_member")) not in staged and (s["path"], s.get("inventory_table"), None) not in staged
            and "/collector/data/poligonlar/" not in s["path"]]  # poligonlar: phase2_stage_reference_geography.py ile alındı (stg_admin_polygon)
    # PBF vb.
    for r in inv.values():
        if r["extension"] == "pbf": deferred.append((r["absolute_path"], None, "deferred: OSM PBF (osmium gerekli)"))
    # yaz
    (OUT_ROOT / "tools" / "specs").mkdir(exist_ok=True)
    fams = defaultdict(list)
    for s in spec: fams[s["family"]].append(s)
    for f, lst in fams.items():
        (OUT_ROOT / "tools" / "specs" / f"all_{f}.json").write_text(json.dumps({"family": f, "standard_version": "1.0.0", "sources": lst}, ensure_ascii=False, indent=1))
    import pyarrow as pa, pyarrow.parquet as pq
    (OUT_ROOT / "mappings").mkdir(exist_ok=True)
    if aliases: pq.write_table(pa.Table.from_pylist(aliases), OUT_ROOT / "mappings" / "file_alias.parquet")
    if zip_alias: pq.write_table(pa.Table.from_pylist(zip_alias), OUT_ROOT / "mappings" / "zip_member_alias.parquet")
    plan = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sources_planned": len(spec), "already_staged_removed": before - len(spec), "families": {f: len(l) for f, l in fams.items()},
            "engines": {e: sum(1 for s in spec if s["engine"] == e) for e in ("duckdb", "sqlite_chunked", "duckdb_geo", "python")}, "quarantine_mode": sum(1 for s in spec if s.get("mode") == "quarantine"),
            "file_aliases": len(aliases), "zip_member_aliases": len(zip_alias), "skipped": skipped, "deferred": deferred}
    (OUT_ROOT / "reports" / "PHASE2_ALL_PLAN.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in plan.items() if k not in ("skipped", "deferred")}, ensure_ascii=False, indent=1))
    print("skipped:", len(skipped), "deferred:", len(deferred))


if __name__ == "__main__":
    main()
