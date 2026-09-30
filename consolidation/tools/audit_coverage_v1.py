#!/usr/bin/env python3
"""
DENETİM — "çektiğimiz her şey veritabanına girdi mi?" sorusunun ölçülmüş cevabı.
Üç halkayı ayrı ayrı karşılaştırır ve nerede kayıp/eksik varsa dosya ve klasör adıyla yazar:
  1) HAM (envanter: 50.062 dosya) → AMBAR (staging): hangi dosyalar ambara girdi, hangileri girmedi, neden
  2) AMBAR → BİRLEŞİK DB (canonical): hangi ambar tabloları veritabanında karşılık buldu
  3) KARANTİNA: doğrulama bekleyen kayıtlar
Çıktı: reports/AMBAR_KAPSAM_DENETIMI.json + .md
"""
import json, datetime as dt, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
STG = OUT / "staging" / "v1.0.0"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
INV = OUT / "inventory" / "inventory.parquet"
BATCH = 150


def lst(paths): return "[" + ", ".join("'" + str(p).replace("'", "''") + "'" for p in paths) + "]"


def main():
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(); c.execute("SET memory_limit='1800MB'"); c.execute("SET threads=2")
    c.execute("SET preserve_insertion_order=false"); c.execute(f"SET temp_directory='{OUT}/tmp/audit'")
    (OUT / "tmp" / "audit").mkdir(parents=True, exist_ok=True)
    rep = {"at": now}

    files = sorted(STG.rglob("*.parquet"))
    c.execute("CREATE TABLE cov_fid (file_id VARCHAR)"); c.execute("CREATE TABLE cov_path (p VARCHAR)")
    hata = []
    for i in range(0, len(files), BATCH):
        b = files[i:i + BATCH]
        for col, tgt in (("source_file_id", "cov_fid"), ("source_path", "cov_path")):
            try:
                c.execute(f"INSERT INTO {tgt} SELECT DISTINCT {col} FROM read_parquet({lst(b)}, union_by_name=true) WHERE {col} IS NOT NULL")
            except Exception as e:
                if "not found" not in str(e).lower(): hata.append(f"{col}@{i}: {str(e)[:120]}")
        if (i // BATCH) % 5 == 0: print(f"  ambar taraması {i}/{len(files)}", flush=True)
    c.execute("CREATE TABLE cov AS SELECT DISTINCT file_id FROM cov_fid")
    c.execute(f"""INSERT INTO cov SELECT DISTINCT i.file_id FROM cov_path p
        JOIN '{INV}' i ON i.absolute_path = p.p WHERE i.file_id NOT IN (SELECT file_id FROM cov)""")
    n_cov = c.execute("SELECT count(DISTINCT file_id) FROM cov").fetchone()[0]

    c.execute(f"""CREATE TABLE inv AS SELECT file_id, source_root, absolute_path, relative_path, detected_format,
        size_bytes, is_data_candidate, read_status, error_status,
        regexp_replace(relative_path, '/[^/]*$', '') AS klasor,
        (file_id IN (SELECT file_id FROM cov)) AS ambarda FROM '{INV}'""")
    tot = c.execute("SELECT count(*), round(sum(size_bytes)/1e9, 2) FROM inv").fetchone()
    veri = c.execute("SELECT count(*), round(sum(size_bytes)/1e9, 2) FROM inv WHERE is_data_candidate").fetchone()
    inb = c.execute("SELECT count(*), round(sum(size_bytes)/1e9, 2) FROM inv WHERE ambarda").fetchone()
    rep["envanter"] = {"dosya": tot[0], "gb": tot[1], "veri_adayi": veri[0], "veri_adayi_gb": veri[1],
                       "ambara_giren_dosya": inb[0], "ambara_giren_gb": inb[1],
                       "kapsam_yuzde_veri_adayi": round(100.0 * inb[0] / max(1, veri[0]), 1)}
    rep["ambara_girmeyen_format"] = c.execute("""SELECT detected_format, count(*) dosya, round(sum(size_bytes)/1e6) mb
        FROM inv WHERE NOT ambarda AND is_data_candidate GROUP BY 1 ORDER BY 3 DESC LIMIT 20""").fetchall()
    rep["ambara_girmeyen_klasor"] = c.execute("""SELECT source_root, klasor, count(*) dosya, round(sum(size_bytes)/1e6) mb,
        any_value(detected_format) ornek_format FROM inv WHERE NOT ambarda AND is_data_candidate
        GROUP BY 1,2 ORDER BY 4 DESC LIMIT 40""").fetchall()
    rep["veri_olmayan_dosya"] = c.execute("""SELECT detected_format, count(*) FROM inv WHERE NOT is_data_candidate
        GROUP BY 1 ORDER BY 2 DESC LIMIT 12""").fetchall()
    rep["okuma_hatasi"] = c.execute("SELECT read_status, count(*) FROM inv WHERE read_status <> 'ok' GROUP BY 1").fetchall()

    # 2) ambar → birleşik db
    k = duckdb.connect(str(CAN), read_only=True)
    stg_views = k.execute("SELECT view_name, row_count, family FROM (SELECT * FROM main.catalog)").fetchall() if False else None
    s2 = duckdb.connect(str(OUT / "staging" / "geoprop_staging.duckdb"), read_only=True)
    cat = s2.execute("SELECT view_name, row_count, family FROM main.catalog").fetchall()
    kullanilan = set()
    for t in [r[0] for r in k.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'").fetchall()]:
        cols = {r[0] for r in k.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{t}'").fetchall()}
        if "staging_view" in cols:
            for (v,) in k.execute(f'SELECT DISTINCT staging_view FROM "{t}" WHERE staging_view IS NOT NULL').fetchall():
                kullanilan.add(v)
    def kisalt(v): return v.split("__", 1)[1] if "__" in v else v
    kul_kisa = {kisalt(v) for v in kullanilan} | kullanilan
    dis = [(v, n, f) for v, n, f in cat if kisalt(v) not in kul_kisa and v not in kul_kisa]
    rep["ambar_tablosu"] = {"toplam": len(cat), "toplam_satir": sum(n or 0 for _, n, _ in cat),
                            "db_de_kullanilan": len(cat) - len(dis),
                            "db_de_kullanilmayan": len(dis),
                            "kullanilmayan_satir": sum(n or 0 for _, n, _ in dis)}
    rep["db_ye_girmemis_ambar_tablolari"] = sorted(((n or 0), v, f) for v, n, f in dis)[::-1][:40]
    rep["aile_bazinda_kullanilmayan"] = {}
    for _, n, f in dis:
        rep["aile_bazinda_kullanilmayan"][f or "?"] = rep["aile_bazinda_kullanilmayan"].get(f or "?", 0) + (n or 0)

    # 3) karantina
    qf = sorted((OUT / "quarantine").rglob("*.parquet"))
    qn = 0
    if qf:
        try: qn = c.execute(f"SELECT count(*) FROM read_parquet({lst(qf)}, union_by_name=true)").fetchone()[0]
        except Exception as e: qn = f"okunamadı: {str(e)[:80]}"
    rep["karantina"] = {"dosya": len(qf), "satir": qn}
    rep["canonical"] = {t: k.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in
                        ("poi", "price_observation", "product_price_observation", "indicator_observation",
                         "indicator_sdmx_observation", "geo_entity", "etbis_site", "charging_socket")}
    rep["hata"] = hata
    (OUT / "reports" / "AMBAR_KAPSAM_DENETIMI.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({kk: vv for kk, vv in rep.items() if kk not in ("ambara_girmeyen_klasor", "db_ye_girmemis_ambar_tablolari")},
                     ensure_ascii=False, indent=1, default=str))
    print("\n— ambara girmemiş en büyük klasörler:")
    for r in rep["ambara_girmeyen_klasor"][:25]: print("   ", r)
    print("\n— DB'ye girmemiş en büyük ambar tabloları:")
    for r in rep["db_ye_girmemis_ambar_tablolari"][:25]: print("   ", r)


if __name__ == "__main__":
    main()
