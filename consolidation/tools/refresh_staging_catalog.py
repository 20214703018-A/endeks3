#!/usr/bin/env python3
"""Staging kataloğunu yeniler: staging/v1.0.0/<aile>/<tablo>/**.parquet için stg.<aile>__<tablo> görünümü yoksa ekler, main.catalog satırını (parquet sayısı, satır sayısı) günceller.
Karantina staging'i (quarantine/staged) için qstg.* aynı şekilde. İdempotent; hiçbir parquet dosyasına dokunmaz."""
import duckdb, datetime as dt, json, sys
from pathlib import Path
OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"; DB = OUT / "staging" / "geoprop_staging.duckdb"

def main():
    c = duckdb.connect(str(DB)); c.execute("LOAD spatial"); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    existing = {(r[0], r[1]) for r in c.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema IN ('stg','qstg')").fetchall()}
    added, updated = [], 0
    for schema, root in (("stg", OUT / "staging" / "v1.0.0"), ("qstg", OUT / "quarantine" / "staged")):
        if not root.exists(): continue
        for fam in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
            for tbl in sorted(p for p in fam.iterdir() if p.is_dir()):
                files = list(tbl.rglob("*.parquet"))
                if not files: continue
                view = f"{fam.name}__{tbl.name}"; glob = str(tbl / "**" / "*.parquet").replace("'", "''")
                n = c.execute(f"SELECT count(*) FROM read_parquet('{glob}', union_by_name=true, hive_partitioning=false)").fetchone()[0]
                if (schema, view) not in existing:
                    c.execute(f"CREATE VIEW {schema}.\"{view}\" AS SELECT * FROM read_parquet('{glob}', union_by_name=true, hive_partitioning=false)"); added.append(f"{schema}.{view}")
                if schema == "stg":
                    r = c.execute("SELECT row_count FROM main.catalog WHERE schema_name=? AND view_name=?", [schema, view]).fetchone()
                    if r is None: c.execute("INSERT INTO main.catalog VALUES (?,?,?,?,?,?)", [schema, view, len(files), n, fam.name, now])
                    elif r[0] != n: c.execute("UPDATE main.catalog SET parquet_files=?, row_count=?, created_at=? WHERE schema_name=? AND view_name=?", [len(files), n, now, schema, view]); updated += 1
    total = c.execute("SELECT sum(row_count) FROM main.catalog").fetchone()[0]
    c.close()
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f: f.write(json.dumps({"at": now, "action": "STAGING_CATALOG_REFRESH", "views_added": added, "rows_updated": updated, "catalog_total_rows": total}) + "\n")
    print(f"eklenen görünüm: {added} | güncellenen satır sayısı: {updated} | katalog toplam satır: {total:,}")

if __name__ == "__main__": main()
