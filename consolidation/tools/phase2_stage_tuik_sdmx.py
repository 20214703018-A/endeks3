#!/usr/bin/env python3
"""
PHASE 2 — TÜİK SDMX akışları (databrowser2) staging.
Neden ayrı araç: SDMX-JSON dosyaları satır yönelimli değil; tek bir JSON belgesi içinde
`dataSets[0].series` sözlüğü (seri anahtarı → gözlemler) ve `structure.dimensions` (kod → ad) durur.
DuckDB read_json bu dosyaları tek dev satır olarak okuyup belleği tüketiyordu (giren 0 satır).
Burada dosyalar tek tek akıtılarak GÖZLEM düzeyine açılır: her satır = bir seri × bir dönem.

İki tablo yazılır (yer israfını önlemek için): SERİ tablosu seri anahtarı → boyut sözlüğü (~2 KB metin, seri başına bir kez),
GÖZLEM tablosu seri anahtarı + dönem + değer. İkisi (raw_akis_id, raw_seri_anahtari) ile birleştirilir; hiçbir alan atılmaz.
Kayıp yok kuralı: her dosyanın beklenen gözlem sayısı (series içindeki observations toplamı)
sayılır ve yazılan satırla karşılaştırılır; fark varsa rapora `unaccounted` olarak yazılır.
Kullanım: python3 tools/phase2_stage_tuik_sdmx.py --list | --apply [--max-files N]
"""
import argparse, datetime as dt, gzip, hashlib, json, os, sys, time
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
INTAKE = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE"
STG_ROOT = OUT / "staging" / "v1.0.0"
PIPELINE_VERSION = "1.0.0"; STANDARD_VERSION = "1.0.0"
MIN_FREE_BYTES = 1_500_000_000
GLOB = "tuik_sdmx/*/data/*.json.gz"
FAMILY = "demographics_context"; TARGET = "stg_tuik_sdmx_gozlem"; TARGET_SERI = "stg_tuik_sdmx_seri"
ACQ = "official_public"; DIST = "public"
AREA_DIMS = ("REF_AREA", "GEO", "IL", "ILLER", "BOLGE", "TR_ISTATISTIKI_BOLGE", "ADRESE_DAYALI_IL")

PROV = [("family", pa.string()), ("staging_table", pa.string()), ("acquisition_class", pa.string()),
        ("distribution_class", pa.string()), ("sensitivity", pa.string()), ("transformation_id", pa.string()),
        ("import_batch_id", pa.string()), ("pipeline_version", pa.string()), ("standard_version", pa.string()),
        ("staged_at", pa.string()), ("review_flags", pa.string())]

SCHEMA_SERI = pa.schema([
    ("raw_akis_id", pa.string()), ("raw_akis_adi", pa.string()), ("raw_akis_aciklama", pa.string()),
    ("raw_seri_anahtari", pa.string()), ("raw_boyutlar", pa.string()),
    ("raw_ref_alan_kodu", pa.string()), ("raw_ref_alan_adi", pa.string()),
    ("raw_hazirlanma", pa.string()), ("raw_gozlem_sayisi", pa.int64()),
    ("source_row_hash", pa.string()), ("source_path", pa.string()),
] + PROV)

SCHEMA = pa.schema([
    ("raw_akis_id", pa.string()), ("raw_seri_anahtari", pa.string()),
    ("raw_zaman_kodu", pa.string()), ("raw_zaman_adi", pa.string()),
    ("raw_deger", pa.float64()), ("raw_deger_metin", pa.string()), ("raw_nitelikler", pa.string()),
    ("source_row_number", pa.int64()), ("source_row_hash", pa.string()), ("source_path", pa.string()),
] + PROV)


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def dim_values(dims):
    """[{id,name,values:[{id,name}]}] → kolay erişim listesi."""
    out = []
    for d in dims:
        vals = [(v.get("id"), v.get("name") or (v.get("names") or {}).get("tr") or (v.get("names") or {}).get("en")) for v in d.get("values", [])]
        out.append((d.get("id"), d.get("name") or d.get("id"), vals))
    return out


def flatten(path, batch_meta):
    """Bir SDMX dosyasını gözlem satırlarına açar. (satırlar, beklenen_gozlem) döner."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    meta = doc.get("meta") or {}
    data = doc.get("data") or {}
    struct = data.get("structure") or {}
    dims_s = dim_values(((struct.get("dimensions") or {}).get("series")) or [])
    dims_o = dim_values(((struct.get("dimensions") or {}).get("observation")) or [])
    attrs_o = dim_values(((struct.get("attributes") or {}).get("observation")) or [])
    akis_id = Path(path).name.replace(".json.gz", "")
    akis_adi = struct.get("name") or (struct.get("names") or {}).get("tr") or (struct.get("names") or {}).get("en") or ""
    akis_acik = struct.get("description") or (struct.get("descriptions") or {}).get("tr") or (struct.get("descriptions") or {}).get("en") or ""
    hazirlanma = meta.get("prepared") or ""
    area_idx = next((i for i, (did, _, _) in enumerate(dims_s) if (did or "").upper() in AREA_DIMS), None)
    seri_rows = []; rows = []; expected = 0
    for ds in (data.get("dataSets") or []):
        for skey, sobj in (ds.get("series") or {}).items():
            idx = [int(x) for x in skey.split(":")] if skey else []
            boyut = {}
            for i, (did, dname, vals) in enumerate(dims_s):
                if i < len(idx) and idx[i] < len(vals):
                    kod, ad = vals[idx[i]]
                else:
                    kod, ad = None, None
                boyut[did or f"DIM{i}"] = {"ad": dname, "kod": kod, "deger": ad}
            ref_kod = ref_ad = None
            if area_idx is not None and area_idx < len(idx) and idx[area_idx] < len(dims_s[area_idx][2]):
                ref_kod, ref_ad = dims_s[area_idx][2][idx[area_idx]]
            boyut_json = json.dumps(boyut, ensure_ascii=False, sort_keys=True)
            obs = sobj.get("observations") or {}
            expected += len(obs)
            seri_rows.append((akis_id, akis_adi, akis_acik, skey, boyut_json, ref_kod, ref_ad, hazirlanma, len(obs),
                              hashlib.sha256("\x1f".join([akis_id, skey, boyut_json]).encode()).hexdigest()))
            for okey, oval in obs.items():
                oi = int(str(okey).split(":")[0])
                zkod = zad = None
                if dims_o and oi < len(dims_o[0][2]):
                    zkod, zad = dims_o[0][2][oi]
                deger = oval[0] if isinstance(oval, list) and oval else None
                dnum = None
                if isinstance(deger, (int, float)):
                    dnum = float(deger)
                else:
                    try: dnum = float(str(deger).replace(",", "."))
                    except Exception: dnum = None
                nit = {}
                if isinstance(oval, list) and len(oval) > 1:
                    for j, aidx in enumerate(oval[1:]):
                        if j < len(attrs_o) and isinstance(aidx, int) and aidx < len(attrs_o[j][2]):
                            akod, aad = attrs_o[j][2][aidx]
                            nit[attrs_o[j][0] or f"ATTR{j}"] = {"kod": akod, "deger": aad}
                h = hashlib.sha256("\x1f".join([akis_id, skey, str(okey), str(deger)]).encode()).hexdigest()
                rows.append((akis_id, skey, zkod, zad, dnum, None if deger is None else str(deger),
                             json.dumps(nit, ensure_ascii=False) if nit else None, h))
    return seri_rows, rows, expected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-files", type=int, default=0)
    a = ap.parse_args()
    files = sorted(str(p) for p in INTAKE.glob(GLOB))
    if a.max_files: files = files[:a.max_files]
    print(f"TÜİK SDMX: {len(files)} dosya ({GLOB})", flush=True)
    if a.list or not a.apply:
        return
    out_dir = STG_ROOT / FAMILY / TARGET; out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "part-NIGHT_tuik_sdmx_gozlem.parquet"
    out_seri_dir = STG_ROOT / FAMILY / TARGET_SERI; out_seri_dir.mkdir(parents=True, exist_ok=True)
    out_seri = out_seri_dir / "part-NIGHT_tuik_sdmx_seri.parquet"
    if out.exists() or out_seri.exists():
        print(f"{out.name} zaten var (append-only) — durduruldu"); return
    if free_bytes(OUT) < MIN_FREE_BYTES:
        print("DİSK KORUMASI (< 1,5 GB boş) — durduruldu"); return
    batch = f"BATCH_{dt.datetime.now().strftime('%Y_%m_%d')}_night_tuik_sdmx"
    staged = now()
    writer = pq.ParquetWriter(out, SCHEMA, compression="zstd")
    writer_seri = pq.ParquetWriter(out_seri, SCHEMA_SERI, compression="zstd")
    t0 = time.time(); total = 0; expected_total = 0; failed = []; rowno = 0; buf = []
    const = [FAMILY, TARGET, ACQ, DIST, "none", "T01_SDMX_FLATTEN_V1", batch, PIPELINE_VERSION, STANDARD_VERSION, staged, '["NIGHT_INTAKE_2026-09-24"]']
    const_seri = [FAMILY, TARGET_SERI, ACQ, DIST, "none", "T01_SDMX_FLATTEN_V1", batch, PIPELINE_VERSION, STANDARD_VERSION, staged, '["NIGHT_INTAKE_2026-09-24"]']
    buf_seri = []; total_seri = 0

    def _write(w, schema, buf):
        if not buf: return
        cols = list(zip(*buf))
        arrays = [pa.array(cols[i], type=schema.field(i).type) for i in range(len(schema))]
        w.write_table(pa.Table.from_arrays(arrays, schema=schema))

    def flush(buf): _write(writer, SCHEMA, buf)

    for n, path in enumerate(files, 1):
        try:
            seri_rows, rows, expected = flatten(path, None)
        except Exception as e:
            failed.append({"file": path, "error": f"{type(e).__name__}: {str(e)[:200]}"}); continue
        expected_total += expected
        for r in rows:
            rowno += 1
            buf.append(r[:7] + (rowno, r[7], path) + tuple(const))
        for sr in seri_rows:
            buf_seri.append(sr + (path,) + tuple(const_seri))
        total += len(rows); total_seri += len(seri_rows)
        if len(buf_seri) >= 50_000:
            _write(writer_seri, SCHEMA_SERI, buf_seri); buf_seri = []
        if len(buf) >= 200_000:
            flush(buf); buf = []
            if free_bytes(OUT) < MIN_FREE_BYTES:
                print("DİSK KORUMASI — yazma durduruldu (kısmi çıktı silinecek)")
                writer.close(); writer_seri.close(); out.unlink(missing_ok=True); out_seri.unlink(missing_ok=True); return
        if n % 50 == 0:
            print(f"  {n}/{len(files)} dosya · {total:,} gözlem · {round(time.time()-t0)}s", flush=True)
    flush(buf); _write(writer_seri, SCHEMA_SERI, buf_seri); writer.close(); writer_seri.close()
    size_mb = round((out.stat().st_size + out_seri.stat().st_size) / 1e6, 1)
    rec = {"source": "tuik_sdmx", "status": "OK" if total == expected_total and not failed else "SATIR_FARKI",
           "files": len(files), "files_failed": len(failed), "rows_expected": expected_total, "rows_written": total,
           "unaccounted": expected_total - total, "rows_series": total_seri, "output": str(out), "output_series": str(out_seri), "size_mb": size_mb,
           "acquisition_class": ACQ, "distribution_class": DIST, "seconds": round(time.time() - t0)}
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    if failed: print("okunamayan dosyalar:", json.dumps(failed[:10], ensure_ascii=False, indent=1))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now(), "operation": "phase2_stage_tuik_sdmx", **rec, "failed_files": failed,
                            "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False) + "\n")
    (OUT / "reports" / "PHASE2_TUIK_SDMX.json").write_text(json.dumps({"at": now(), **rec, "failed_files": failed}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
