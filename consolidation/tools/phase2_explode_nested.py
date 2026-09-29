#!/usr/bin/env python3
"""
PHASE 2 — iç içe (nested) gece kaynaklarını KAYIT düzeyine açma.
Neden gerekli: bazı gece dosyalarında satır = "sayfa"/"konteyner"; gerçek kayıtlar bir liste alanının içinde duruyor.
  · ETBİS: her satır bir arama sayfası, siteler `rows[]` içinde (sayfa başına 10 site)
  · Market Fiyatı şubeler: her satır bir şube (depot), ürün fiyatları `items[]` içinde (şube başına ~1.500 ürün)
Konteyner tabloları SİLİNMEZ (ham kopya olarak kalır); bu araç yanına açılmış (exploded) tablo yazar.
Satır muhasebesi: beklenen (liste uzunlukları toplamı) = yazılan; fark rapora `unaccounted` yazılır.
Kullanım: python3 tools/phase2_explode_nested.py --list | --apply [--only etbis_siteler,marketfiyati_urun_fiyat]
"""
import argparse, datetime as dt, hashlib, json, os, time
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
INTAKE = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE"
STG = OUT / "staging" / "v1.0.0"
PIPELINE_VERSION = "1.0.0"; STANDARD_VERSION = "1.0.0"
MIN_FREE_BYTES = 1_500_000_000
PROV = "'{fam}' AS family, '{tgt}' AS staging_table, '{acq}' AS acquisition_class, '{dist}' AS distribution_class, '{sens}' AS sensitivity"


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def sources():
    etbis_files = sorted(str(p) for p in INTAKE.glob("etbis_eticaret_siteleri/*/*.jsonl"))
    etbis_list = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in etbis_files) + "]"
    etbis_rel = (f"read_json({etbis_list}, format='newline_delimited', union_by_name=true, ignore_errors=true, "
                 "maximum_object_size=268435456, sample_size=-1, filename=true)")
    opet_files = sorted(str(x) for x in INTAKE.glob("akaryakit_opet_fiyat_arsivi/*/products.json.gz"))
    opet_list = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in opet_files) + "]"
    opet_rel = f"read_json({opet_list}, filename=true)"
    mf_src = STG / "price_series" / "stg_marketfiyati_sube_urun" / "part-NIGHT_marketfiyati_branch_items.parquet"
    return {
        "etbis_siteler": {
            "family": "poi_business", "target": "stg_etbis_site_kaydi",
            "acq": "official_public", "dist": "public", "sens": "company_identity",
            "note": "ETBİS arama sayfaları → site kaydı (sayfa başına 10). unvan = tüzel kişi adı.",
            "files": len(etbis_files),
            "expected": f"SELECT sum(json_array_length(rows)) FROM {etbis_rel} WHERE json_type(rows) = 'ARRAY'",
            "select": f"""SELECT r.unvan AS raw_unvan, r.site AS raw_site, r.mobil AS raw_mobil, r.siteId AS raw_site_id,
                    CAST(page AS VARCHAR) AS raw_sayfa, CAST(maxPage AS VARCHAR) AS raw_sayfa_toplam,
                    CAST("at" AS VARCHAR) AS raw_cekim_zamani, CAST(city AS VARCHAR) AS raw_il_adi,
                    CAST(cityId AS VARCHAR) AS raw_il_id, CAST(district AS VARCHAR) AS raw_ilce_adi,
                    CAST(districtId AS VARCHAR) AS raw_ilce_id, CAST(sector AS VARCHAR) AS raw_sektor,
                    CAST(sectorId AS VARCHAR) AS raw_sektor_id, sira AS source_row_number,
                    sha256(concat_ws(chr(31), coalesce(r.siteId, chr(0)), coalesce(r.site, chr(0)), coalesce(r.unvan, chr(0)))) AS source_row_hash,
                    filename AS source_path
                FROM (SELECT page, maxPage, "at", city, cityId, district, districtId, sector, sectorId, filename,
                        unnest(CAST(rows AS STRUCT(unvan VARCHAR, site VARCHAR, mobil VARCHAR, siteId VARCHAR)[])) AS r,
                        unnest(range(1::BIGINT, CAST(json_array_length(rows) AS BIGINT) + 1)) AS sira
                      FROM {etbis_rel} WHERE json_type(rows) = 'ARRAY' AND json_array_length(rows) > 0)""",
        },
        "etbis_profil": {
            "family": "poi_business", "target": "stg_etbis_site_profil",
            "acq": "official_public", "dist": "internal", "sens": "company_identity",
            "note": "ETBİS site profilleri (MERSİS/vergi no, işletme türü, KEP, mal-hizmet, ödeme türleri) — kimlik alanları kısıtlı",
            "files": len(etbis_files),
            "expected": f"SELECT count(*) FROM {etbis_rel} WHERE rows IS NULL AND siteId IS NOT NULL",
            "select": f"""SELECT CAST(siteId AS VARCHAR) AS raw_site_id, "İşletme Adı" AS raw_isletme_adi,
                    "Mersis No" AS raw_mersis_no, "Vergi No" AS raw_vergi_no, "İşletme Türü" AS raw_isletme_turu,
                    "ETBİS’e Kayıt Tarihi" AS raw_etbis_kayit_tarihi, "Kep Adresleri" AS raw_kep_adresleri,
                    hakkinda AS raw_hakkinda, CAST(mal_hizmetler AS VARCHAR) AS raw_mal_hizmetler,
                    CAST(odeme_turleri AS VARCHAR) AS raw_odeme_turleri, CAST(diger_siteler AS VARCHAR) AS raw_diger_siteler,
                    CAST("at" AS VARCHAR) AS raw_cekim_zamani,
                    row_number() OVER () AS source_row_number,
                    sha256(concat_ws(chr(31), coalesce(CAST(siteId AS VARCHAR), chr(0)), coalesce("Mersis No", chr(0)), coalesce("Vergi No", chr(0)))) AS source_row_hash,
                    filename AS source_path
                FROM {etbis_rel} WHERE rows IS NULL AND siteId IS NOT NULL""",
        },
        "opet_urun_katalog": {
            "family": "price_series", "target": "stg_opet_urun_katalog",
            "acq": "web_research", "dist": "internal", "sens": "none",
            "note": "Opet akaryakıt ürün kodu → ad sözlüğü (products.json.gz; sütun başına bir ürün)",
            "files": len(opet_files),
            "expected": f"SELECT count(*) FROM (SELECT unnest(json_keys(to_json(t))) AS k FROM {opet_rel} t) WHERE k <> 'filename'",
            "select": f"""SELECT k AS raw_urun_kodu, j->k->>'name' AS raw_urun_adi, j->k->>'short' AS raw_urun_kisa,
                    row_number() OVER () AS source_row_number, sha256(k) AS source_row_hash, filename AS source_path
                FROM (SELECT j, filename, unnest(json_keys(j)) AS k
                      FROM (SELECT to_json(t) AS j, filename FROM {opet_rel} t)) WHERE k <> 'filename'""",
        },
        "marketfiyati_urun_fiyat": {
            "family": "price_series", "target": "stg_marketfiyati_sube_urun_fiyat",
            "acq": "web_research", "dist": "internal", "sens": "none",
            "note": "şube × ürün fiyatı (depot satırındaki items[] açıldı)",
            "files": 1 if mf_src.exists() else 0,
            "expected": f"SELECT sum(raw_n_items) FROM '{mf_src}' WHERE raw_depot_id IS NOT NULL",
            "select": f"""SELECT raw_depot_id, raw_market, raw_tur, raw_il_geo_id, raw_ilce_geo_id, raw_mahalle_geo_id,
                    raw_lat, raw_lon, raw_cekim_zamani,
                    it.id AS raw_urun_id, CAST(it.price AS DOUBLE) AS raw_fiyat, it.unitPrice AS raw_birim_fiyat_metin,
                    CAST(it.unitPriceValue AS DOUBLE) AS raw_birim_fiyat, CAST(it.percentage AS DOUBLE) AS raw_yuzde,
                    it.indexTime AS raw_fiyat_endeks_zamani, CAST(it.discount AS VARCHAR) AS raw_indirim,
                    CAST(it.discountRatio AS VARCHAR) AS raw_indirim_orani, it.promotionText AS raw_promosyon_metni,
                    raw_konteyner_satir_no, urun_sira AS source_row_number,
                    sha256(concat_ws(chr(31), coalesce(raw_depot_id, chr(0)), coalesce(it.id, chr(0)), coalesce(CAST(it.price AS VARCHAR), chr(0)), coalesce(it.indexTime, chr(0)))) AS source_row_hash,
                    source_path
                FROM (SELECT raw_depot_id, raw_market, CAST(raw_tur AS VARCHAR) AS raw_tur, raw_il_geo_id, raw_ilce_geo_id,
                        raw_mahalle_geo_id, raw_lat, raw_lon, raw_at AS raw_cekim_zamani,
                        source_row_number AS raw_konteyner_satir_no, source_path,
                        unnest(raw_items) AS it, unnest(range(1, len(raw_items) + 1)) AS urun_sira
                      FROM '{mf_src}' WHERE raw_depot_id IS NOT NULL AND raw_items IS NOT NULL)""",
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true"); ap.add_argument("--apply", action="store_true"); ap.add_argument("--only", default=None)
    a = ap.parse_args()
    S = sources()
    names = [n.strip() for n in a.only.split(",")] if a.only else list(S)
    con = duckdb.connect(); con.execute("SET memory_limit='1200MB'"); con.execute("SET threads=1")
    (OUT / "tmp" / "explode").mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory='{OUT}/tmp/explode'"); con.execute("SET preserve_insertion_order=false")
    report = []
    for name in names:
        s = S[name]
        out_dir = STG / s["family"] / s["target"]; out = out_dir / f"part-NIGHT_{name}.parquet"
        if a.list or not a.apply:
            print(f"  {name:26s} {s['files']:>4} kaynak dosya → {s['family']}/{s['target']}  {s['note']}"); continue
        if s["files"] == 0:
            print(f"  {name}: kaynak yok — atlandı"); report.append({"source": name, "status": "DOSYA_YOK"}); continue
        if out.exists():
            print(f"  {name}: {out.name} zaten var (append-only) — atlandı"); report.append({"source": name, "status": "ZATEN_VAR"}); continue
        if free_bytes(OUT) < MIN_FREE_BYTES:
            print(f"  {name}: DİSK KORUMASI (< 1,5 GB boş)"); report.append({"source": name, "status": "DISK_GUARD"}); break
        t0 = time.time(); out_dir.mkdir(parents=True, exist_ok=True)
        batch = f"BATCH_{dt.datetime.now().strftime('%Y_%m_%d')}_explode_{name}"
        prov = PROV.format(fam=s["family"], tgt=s["target"], acq=s["acq"], dist=s["dist"], sens=s["sens"])
        sql = f"""COPY (SELECT *, {prov}, 'T02_EXPLODE_NESTED_V1' AS transformation_id, '{batch}' AS import_batch_id,
              '{PIPELINE_VERSION}' AS pipeline_version, '{STANDARD_VERSION}' AS standard_version, '{now()}' AS staged_at,
              '["NIGHT_INTAKE_2026-09-24","EXPLODED_FROM_CONTAINER"]' AS review_flags
              FROM ({s['select']})) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 200000)"""
        try:
            expected = con.execute(s["expected"]).fetchone()[0] or 0
            con.execute(sql)
        except Exception as e:
            print(f"  {name}: HATA {type(e).__name__}: {str(e)[:250]}"); report.append({"source": name, "status": "FAILED", "error": str(e)[:300]}); continue
        n_out = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
        size_mb = round(out.stat().st_size / 1e6, 1)
        rec = {"source": name, "status": "OK" if n_out == expected else "SATIR_FARKI", "rows_expected": int(expected),
               "rows_written": n_out, "unaccounted": int(expected) - n_out, "output": str(out), "size_mb": size_mb,
               "acquisition_class": s["acq"], "distribution_class": s["dist"], "seconds": round(time.time() - t0)}
        report.append(rec)
        print(f"  {name}: beklenen {int(expected):,} → yazılan {n_out:,} ({size_mb} MB, {rec['seconds']}s) {'✅' if n_out == expected else '⚠️ FARK'}", flush=True)
        with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
            f.write(json.dumps({"at": now(), "operation": "phase2_explode_nested", **rec,
                                "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False) + "\n")
    if a.apply:
        (OUT / "reports" / "PHASE2_EXPLODE_NESTED.json").write_text(json.dumps({"at": now(), "report": report}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
