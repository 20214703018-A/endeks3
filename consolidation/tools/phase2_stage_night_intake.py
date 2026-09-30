#!/usr/bin/env python3
"""
PHASE 2 — 24 Eylül gece turu kaynakları için toplu staging (parquet / jsonl(.gz) yığınları).
Neden ayrı araç: bu kaynaklar tek dosya değil dosya YIĞINI (ör. 1.539 şube jsonl.gz, 865 TÜİK akışı) ve toplam satır 30 M+;
phase2_stage_generic dosya başına çalıştığından yavaş kalır. Burada DuckDB glob ile okunur, provenance dosya bazında korunur
(source_file_id envanterden filename eşlemesiyle; ayrıca source_path her satırda).

Kurallar korunur: ham değerler raw_* olarak metin saklanır (parquet kaynaklarda tip korunur + raw_json), satır sayısı muhasebesi
(giren = yazılan + atlanan), append-only (aynı hedef varsa hata), audit kaydı, disk koruması.
Kullanım: python3 tools/phase2_stage_night_intake.py --list | --apply [--only kaynak1,kaynak2] [--dry-run]
"""
import argparse, datetime as dt, hashlib, json, os, sys, time
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
INTAKE = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE"
STG_ROOT = OUT / "staging" / "v1.0.0"
PIPELINE_VERSION = "1.0.0"; STANDARD_VERSION = "1.0.0"
MIN_FREE_BYTES = 1_500_000_000

# kaynak adı: (aile, hedef tablo, glob, okuyucu, edinim sınıfı, dağıtım sınıfı, notlar)
SOURCES = {
 "marketfiyati_depots":   ("price_series", "stg_marketfiyati_depot", "marketfiyati/*/depots.parquet", "parquet", "web_research", "internal", "market şube kataloğu (koordinat + mahalle/ilçe/il geo_id)"),
 "marketfiyati_branch_items": ("price_series", "stg_marketfiyati_sube_urun", "marketfiyati/*/branches/*.jsonl.gz", "jsonl", "web_research", "internal", "şube × ürün fiyatı (GHA 40 makine koşuları)"),
 "marketfiyati_history":  ("price_series", "stg_marketfiyati_fiyat_gecmisi", "marketfiyati/*/history/*.parquet", "parquet", "web_research", "internal", "ürün fiyat geçmişi (90 gün, depot bazlı)"),
 "opet_fuel":             ("price_series", "stg_opet_ilce_akaryakit_fiyat", "akaryakit_opet_fiyat_arsivi/*/opet_ilce_gunluk_fiyat.parquet", "parquet", "web_research", "internal", "ilçe × gün × ürün akaryakıt fiyatı"),
 "hal_ulusal":            ("price_series", "stg_hal_ulusal_gunluk", "hal_fiyatlari/*/hks_ulusal_gunluk.parquet", "parquet", "official_public", "public", "HKS ulusal hal günlük fiyat"),
 "hal_izmir":             ("price_series", "stg_hal_izmir_gunluk", "hal_fiyatlari/*/izmir_*.parquet", "parquet", "official_public", "public", "İzmir hal günlük fiyat"),
 "sarj_stations":         ("poi_business", "stg_epdk_sarj_istasyon", "sarj_istasyonlari_epdk/*/stations.parquet", "parquet", "official_public", "public", "EPDK şarj istasyonları (koordinatlı)"),
 "sarj_sockets":          ("poi_business", "stg_epdk_sarj_soket", "sarj_istasyonlari_epdk/*/sockets.parquet", "parquet", "official_public", "public", "şarj soketleri (güç, fiyat)"),
 "tga_tesis":             ("tourism", "stg_ktb_belgeli_tesis", "turizm_tga_belgeli_tesisler/*/ktb_belgeli_konaklama_tesisleri.parquet", "parquet", "official_public", "public", "KTB belgeli konaklama tesisleri"),
 "tobb_kapasite":         ("poi_business", "stg_tobb_sanayi_kapasite", "tobb_sanayi_kapasite/*/responses.jsonl", "jsonl", "official_public", "public", "TOBB sanayi kapasite raporları"),
 "etbis_siteler":         ("poi_business", "stg_etbis_eticaret_sitesi", "etbis_eticaret_siteleri/*/all_rows_*.jsonl", "jsonl", "official_public", "public", "ETBİS kayıtlı e-ticaret siteleri"),
 "eticaret_adres":        ("poi_business", "stg_eticaret_sirket_adresi", "eticaret_sirket_adresleri/*/*.jsonl", "jsonl", "web_research", "internal", "e-ticaret sitelerinin künye adresleri"),
 "otobus_seferleri":      ("mobility_logistics", "stg_otobus_seferleri", "otobus_seferleri_enuygun/*/*.jsonl*", "jsonl", "web_research", "internal", "şehirlerarası otobüs seferleri/fiyatları"),
 "tuik_sdmx":             ("demographics_context", "stg_tuik_sdmx_seri", "tuik_sdmx/*/data/*.json.gz", "json_sdmx", "official_public", "public", "TÜİK databrowser2 akışları"),
 "cimri_katalog":         ("price_series", "stg_cimri_katalog_url", "cimri_fiyat_karsilastirma/*/catalog_urls.jsonl.gz", "jsonl", "web_research", "internal", "Cimri ürün katalog URL'leri (fiyat çekilemedi: Cloudflare)"),
 "pazarama_satici":       ("poi_business", "stg_pazarama_satici", "eticaret_pazarama_satici/*/*.jsonl*", "jsonl", "web_research", "restricted", "Pazarama satıcı listesi (KİŞİSEL VERİ: unvan/adres → restricted)"),
}


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def free_bytes(p: Path) -> int:
    st = os.statvfs(p); return st.f_bavail * st.f_frsize


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--list", action="store_true"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--only", default=None); ap.add_argument("--max-rows", type=int, default=0)
    ap.add_argument("--incremental", action="store_true", help="hedefte zaten işlenmiş source_path'leri atla, yalnız yeni dosyaları ek parça olarak yaz")
    a = ap.parse_args()
    names = [n.strip() for n in a.only.split(",")] if a.only else list(SOURCES)
    con = duckdb.connect(); con.execute("SET memory_limit='2GB'"); con.execute("SET threads=3"); con.execute(f"SET temp_directory='{OUT}/tmp/night_stage'")
    inv = {}
    for fid, path in con.execute(f"SELECT file_id, absolute_path FROM '{OUT}/inventory/inventory.parquet'").fetchall(): inv[path] = fid
    report = []
    for name in names:
        fam, target, glob_pat, reader, acq, dist, note = SOURCES[name]
        files = sorted(str(p) for p in INTAKE.glob(glob_pat))
        if not files:
            report.append({"source": name, "status": "DOSYA_YOK", "glob": glob_pat}); print(f"  {name}: dosya yok ({glob_pat})", flush=True); continue
        n_inv = sum(1 for f in files if f in inv)
        out_dir = STG_ROOT / fam / target; out = out_dir / f"part-NIGHT_{name}.parquet"
        if a.incremental:
            parts = sorted(str(x) for x in out_dir.glob("part-*.parquet")) if out_dir.exists() else []
            if parts:
                plist = "[" + ", ".join("'" + x.replace("'", "''") + "'" for x in parts) + "]"
                done = {r[0] for r in con.execute(f"SELECT DISTINCT source_path FROM read_parquet({plist}, union_by_name=true)").fetchall()}
                before = len(files); files = [f for f in files if f not in done]
                print(f"  {name}: {before:,} dosyanın {before - len(files):,} tanesi zaten ambarda", flush=True)
            if not files:
                print(f"  {name}: yeni dosya yok — güncel", flush=True); report.append({"source": name, "status": "GUNCEL"}); continue
            out = out_dir / f"part-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}_{name}.parquet"
        if a.list or a.dry_run:
            print(f"  {name:26s} {len(files):>5} dosya (envanterde {n_inv}) → {fam}/{target}  [{reader}]  {note}", flush=True)
            report.append({"source": name, "files": len(files), "in_inventory": n_inv, "target": f"{fam}/{target}", "reader": reader}); continue
        if out.exists():
            print(f"  {name}: {out.name} zaten var (append-only) — atlandı", flush=True); report.append({"source": name, "status": "ZATEN_VAR"}); continue
        if free_bytes(OUT) < MIN_FREE_BYTES:
            print(f"  {name}: DİSK KORUMASI (boş alan < 1,5 GB) — durduruldu", flush=True); report.append({"source": name, "status": "DISK_GUARD"}); break
        t0 = time.time(); out_dir.mkdir(parents=True, exist_ok=True)
        batch = f"BATCH_{dt.datetime.now().strftime('%Y_%m_%d')}_night_{name}"
        flist = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
        if reader == "parquet":
            rel = f"read_parquet({flist}, union_by_name=true, filename=true)"
        elif reader == "jsonl":
            rel = f"read_json({flist}, format='newline_delimited', union_by_name=true, ignore_errors=true, maximum_object_size=268435456, sample_size=-1, filename=true)"
        elif reader == "json_sdmx":
            rel = f"read_json({flist}, union_by_name=true, ignore_errors=true, maximum_object_size=268435456, sample_size=-1, filename=true)"
        else:
            raise SystemExit(f"bilinmeyen okuyucu {reader}")
        try:
            cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel} LIMIT 1").fetchall()]
        except Exception as e:
            print(f"  {name}: OKUNAMADI {type(e).__name__}: {str(e)[:160]}", flush=True); report.append({"source": name, "status": "READ_FAILED", "error": str(e)[:300]}); continue
        data_cols = [c for c in cols if c != "filename"]
        def q(c): return '"' + c.replace('"', '""') + '"'
        raw_sel = ", ".join(f"{q(c)} AS {q('raw_' + c)}" for c in data_cols)
        hash_expr = "sha256(concat_ws(chr(31), " + ", ".join(f"coalesce(CAST({q(c)} AS VARCHAR), chr(0))" for c in data_cols) + "))"
        limit = f" LIMIT {a.max_rows}" if a.max_rows else ""
        sql = f"""COPY (SELECT {raw_sel}, row_number() OVER () AS source_row_number, {hash_expr} AS source_row_hash,
            filename AS source_path, '{fam}' AS family, '{target}' AS staging_table, '{acq}' AS acquisition_class, '{dist}' AS distribution_class,
            'none' AS sensitivity, 'T00_RAW_COPY_V1' AS transformation_id, '{batch}' AS import_batch_id, '{PIPELINE_VERSION}' AS pipeline_version,
            '{STANDARD_VERSION}' AS standard_version, '{now()}' AS staged_at, '["NIGHT_INTAKE_2026-09-24"]' AS review_flags
            FROM {rel}{limit}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)"""
        try:
            con.execute(sql)
        except Exception as e:
            print(f"  {name}: YAZILAMADI {type(e).__name__}: {str(e)[:200]}", flush=True); report.append({"source": name, "status": "WRITE_FAILED", "error": str(e)[:300]}); continue
        n_in = con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
        n_out = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
        n_files_out = con.execute(f"SELECT count(DISTINCT source_path) FROM '{out}'").fetchone()[0]
        size_mb = round(out.stat().st_size / 1e6, 1)
        rec = {"source": name, "status": "OK" if n_in == n_out else "SATIR_FARKI", "files": len(files), "files_in_output": n_files_out,
               "rows_in": n_in, "rows_written": n_out, "unaccounted": n_in - n_out, "columns": len(data_cols), "output": str(out), "size_mb": size_mb,
               "acquisition_class": acq, "distribution_class": dist, "seconds": round(time.time() - t0)}
        report.append(rec); print(f"  {name}: {n_in:,} satır → {n_out:,} yazıldı ({size_mb} MB, {rec['seconds']}s) {'✅' if n_in == n_out else '⚠️ FARK'}", flush=True)
        with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
            f.write(json.dumps({"at": now(), "operation": "phase2_stage_night_intake", **rec, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False) + "\n")
    (OUT / "reports" / "PHASE2_NIGHT_INTAKE.json").write_text(json.dumps({"at": now(), "report": report}, ensure_ascii=False, indent=1))
    tot_in = sum(r.get("rows_in", 0) for r in report); tot_out = sum(r.get("rows_written", 0) for r in report)
    print(f"\nTOPLAM giren {tot_in:,} → yazılan {tot_out:,} · açıklanamayan {tot_in - tot_out}")


if __name__ == "__main__":
    main()
