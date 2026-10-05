#!/usr/bin/env python3
"""
KAPSAMA KONTROLÜ — toplanan her veri dosyası kanonik veritabanına girmiş mi? (salt okunur)

Sınıflar (dosya başına):
  A_kanonik      : raw_manifest file_id'si kanonik tabloların source_file_id'sinde geçiyor
  B_staging      : yalnız staging parquet'lerinin source_file_id'sinde geçiyor
  C_islenmemis   : manifestte var, staging/kanonikte yok (karantina kararı varsa nedeni yazılır)
  D_envanter_disi: manifestte hiç yok (son envanterden sonra gelmiş ya da taranmamış kök) → yeni veri adayı
Girdi: veri kataloğu (katalog.jsonl: yedek arşivlerindeki veri dosyaları), raw_manifest/*.parquet, kanonik DB, staging/.
Çıktı: kapsama_<tarih>.parquet (dosya başına sınıf) + özet Markdown (yedek × kök klasör × sınıf).
"""
import argparse, glob, json, os, time, duckdb
from pathlib import Path

H = Path.home()
# yedek adı → arşiv içi yolların başına eklenecek ÖZGÜN mutlak üst dizin (Mac'teki yerleri; manifest bu yolları tutar)
UST = {"raw_intake": "/Users/acar/Desktop", "raw_intake_ek_20261002": "/Users/acar/Desktop",
       "geoprop_warehouse": "/Users/acar/Desktop/GEOPROP", "geoprop_warehouse_ek_20261002": "/Users/acar/Desktop/GEOPROP",
       "geoprop_veriler_v2": "/Users/acar/Desktop/GEOPROP", "geoprop_collector": "/Users/acar/Desktop/GEOPROP",
       "geoprop_kalan": "/Users/acar/Desktop", "desktop_tkgm_v2": "/Users/acar/Desktop", "desktop_harita": "/Users/acar/Desktop",
       "documents_geoprop_ambar": "/Users/acar/Documents", "downloads_v2": "/Users/acar", "home_warehouse": "/Users/acar",
       "endeks3_warehouse": "/Users/acar/Desktop/endeks3"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--katalog", required=True); ap.add_argument("--canonical", required=True)
    ap.add_argument("--kok", default=str(H / "Desktop/GEOPROP_CONSOLIDATION")); ap.add_argument("--cikti", required=True)
    ap.add_argument("--unstaged", default=None, help="reports/UNSTAGED_FILES.csv (önceki muhasebenin gerekçeleri)")
    a = ap.parse_args(); kok = Path(a.kok); os.makedirs(a.cikti, exist_ok=True)
    c = duckdb.connect(); c.execute("SET threads=4")
    t0 = time.time()
    # 1) manifest: en güncel kayıt (aynı yol birden çok koşuda görülebilir)
    c.execute(f"""CREATE TABLE man AS SELECT * FROM (SELECT file_id, absolute_path, sha256, size_bytes, filename,
                  ROW_NUMBER() OVER (PARTITION BY absolute_path ORDER BY manifest_dosyasi DESC) r
                  FROM read_parquet('{kok}/raw_manifest/manifest_*.parquet', union_by_name=true, filename='manifest_dosyasi')) WHERE r=1""")
    print("manifest dosya:", c.execute("SELECT COUNT(*) FROM man").fetchone()[0], flush=True)
    # 2) kanonikte kullanılan file_id'ler
    c.execute(f"ATTACH '{a.canonical}' AS canon (READ_ONLY)")
    tabs = c.execute("""SELECT c.schema_name, c.table_name FROM duckdb_columns() c
                        JOIN duckdb_tables() t USING (database_name, schema_name, table_name)
                        WHERE c.database_name='canon' AND c.column_name='source_file_id'""").fetchall()
    c.execute("CREATE TABLE kan_fid (fid VARCHAR, tablo VARCHAR)")
    for s, t in tabs:
        try: c.execute(f'INSERT INTO kan_fid SELECT DISTINCT source_file_id, \'{s}.{t}\' FROM canon."{s}"."{t}" WHERE source_file_id IS NOT NULL')
        except Exception as e: print("  atlandı", s, t, e)
    print("kanonik tablo (source_file_id'li):", len(tabs), "· farklı file_id:", c.execute("SELECT COUNT(DISTINCT fid) FROM kan_fid").fetchone()[0], flush=True)
    # 3) staging'de kullanılan file_id'ler (yalnız source_file_id sütunu okunur)
    c.execute("CREATE TABLE stg_fid (fid VARCHAR, aile VARCHAR)")
    for f in glob.glob(f"{kok}/staging/v*/**/*.parquet", recursive=True):
        try:
            cols = [r[0] for r in c.execute(f"DESCRIBE SELECT * FROM read_parquet('{f}')").fetchall()]
            if "source_file_id" in cols:
                aile = Path(f).parent.name
                c.execute(f"INSERT INTO stg_fid SELECT DISTINCT source_file_id, '{aile}' FROM read_parquet('{f}')")
        except Exception:
            pass
    print("staging farklı file_id:", c.execute("SELECT COUNT(DISTINCT fid) FROM stg_fid").fetchone()[0], flush=True)
    # 4) karantina kararları (varsa)
    kar = glob.glob(f"{kok}/quarantine/**/*decision*.parquet", recursive=True) + glob.glob(f"{kok}/quarantine/**/*quarantine*.parquet", recursive=True)
    c.execute("CREATE TABLE kar (fid VARCHAR, neden VARCHAR)")
    for f in kar:
        try:
            cols = [r[0] for r in c.execute(f"DESCRIBE SELECT * FROM read_parquet('{f}')").fetchall()]
            fc = next((x for x in ("file_id", "source_file_id") if x in cols), None)
            nc = next((x for x in ("decision", "reason", "rule_id", "quarantine_reason", "status") if x in cols), None)
            if fc: c.execute(f"INSERT INTO kar SELECT {fc}, {'CAST(' + nc + ' AS VARCHAR)' if nc else repr(Path(f).stem)} FROM read_parquet('{f}')")
        except Exception:
            pass
    # 4b) önceki muhasebenin gerekçeleri: staging dışı bırakılanlar ve birebir kopya grupları
    c.execute("CREATE TABLE uns (fid VARCHAR, neden VARCHAR)")
    if a.unstaged and os.path.exists(a.unstaged):
        c.execute(f"INSERT INTO uns SELECT file_id, reason FROM read_csv_auto('{a.unstaged}', all_varchar=true)")
    c.execute(f"CREATE TABLE dup AS SELECT file_id fid, duplicate_group_id grp FROM read_parquet('{kok}/inventory/duplicates_files.parquet')")
    c.execute("CREATE TABLE islenen AS SELECT fid FROM kan_fid UNION SELECT fid FROM stg_fid")
    c.execute("""CREATE TABLE kopya_kapsanan AS SELECT DISTINCT d1.fid FROM dup d1 JOIN dup d2 ON d1.grp=d2.grp AND d1.fid<>d2.fid
                 WHERE d2.fid IN (SELECT fid FROM islenen)""")
    # 5) katalogdaki veri dosyaları → özgün mutlak yol
    satir = []
    for l in open(a.katalog, encoding="utf-8"):
        r = json.loads(l); ust = UST.get(r["kaynak"])
        if ust: satir.append((r["kaynak"], r["yol"], os.path.join(ust, r["yol"]), r["bayt"], r["tur"]))
    c.execute("CREATE TABLE kat (yedek VARCHAR, arsiv_yolu VARCHAR, yol VARCHAR, bayt BIGINT, tur VARCHAR)")
    c.executemany("INSERT INTO kat VALUES (?,?,?,?,?)", satir)
    c.execute("""CREATE TABLE kapsama AS
        SELECT k.*, m.file_id,
          CASE WHEN m.file_id IS NULL THEN 'D_envanter_disi'
               WHEN EXISTS (SELECT 1 FROM kan_fid x WHERE x.fid=m.file_id) THEN 'A_kanonik'
               WHEN EXISTS (SELECT 1 FROM stg_fid x WHERE x.fid=m.file_id) THEN 'B_staging'
               ELSE 'C_islenmemis' END AS sinif,
          (SELECT string_agg(DISTINCT tablo, ', ') FROM kan_fid x WHERE x.fid=m.file_id) AS kanonik_tablolar,
          (SELECT string_agg(DISTINCT aile, ', ') FROM stg_fid x WHERE x.fid=m.file_id) AS staging_aileleri,
          (SELECT string_agg(DISTINCT neden, ', ') FROM kar x WHERE x.fid=m.file_id) AS karantina,
          (SELECT string_agg(DISTINCT neden, ', ') FROM uns x WHERE x.fid=m.file_id) AS staging_disi_neden,
          m.file_id IN (SELECT fid FROM kopya_kapsanan) AS kopyasi_islenmis
        FROM kat k LEFT JOIN man m ON m.absolute_path = k.yol""")
    # C sınıfını gerekçeye göre ayır
    c.execute("""UPDATE kapsama SET sinif = CASE
        WHEN kopyasi_islenmis THEN 'C1_kopyasi_islenmis'
        WHEN karantina IS NOT NULL THEN 'C2_karantina'
        WHEN staging_disi_neden IS NOT NULL THEN 'C3_gerekceli_kapsam_disi'
        ELSE 'C4_islenmemis' END WHERE sinif = 'C_islenmemis'""")
    tarih = time.strftime("%Y-%m-%d")
    c.execute(f"COPY kapsama TO '{a.cikti}/kapsama_{tarih}.parquet' (FORMAT parquet)")
    # özet
    L = [f"# Kapsama kontrolü — {tarih}", "", "Her veri dosyası: A kanonikte · B yalnız staging · C envanterde ama işlenmemiş · D envanter dışı (yeni).", ""]
    for s, n, b in c.execute("SELECT sinif, COUNT(*), SUM(bayt) FROM kapsama GROUP BY 1 ORDER BY 1").fetchall():
        L.append(f"- **{s}**: {n:,} dosya · {b/1e9:.2f} GB")
    L += ["", "### C gerekçeleri (en büyük)", ""]
    for s_, ne, n, b in c.execute("SELECT sinif, COALESCE(karantina, staging_disi_neden, '-'), COUNT(*), SUM(bayt) FROM kapsama "
                                  "WHERE sinif LIKE 'C%' GROUP BY 1,2 ORDER BY 4 DESC LIMIT 25").fetchall():
        L.append(f"- {s_} · {ne[:80]} · {n:,} dosya · {b/1e9:.2f} GB")
    L += ["", "## Kök klasör × sınıf (en büyükler)", "", "| Yedek | Klasör | Sınıf | Dosya | GB |", "|---|---|---|---:|---:|"]
    for y, kl, s, n, b in c.execute("""SELECT yedek, array_to_string(string_split(arsiv_yolu,'/')[1:3],'/') kl, sinif, COUNT(*), SUM(bayt)
                                       FROM kapsama GROUP BY 1,2,3 ORDER BY 5 DESC LIMIT 80""").fetchall():
        L.append(f"| {y} | `{kl}` | {s} | {n:,} | {b/1e9:.2f} |")
    Path(f"{a.cikti}/kapsama_{tarih}.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:8])); print(f"süre {time.time()-t0:.0f} sn · rapor {a.cikti}/kapsama_{tarih}.md")


if __name__ == "__main__":
    main()
