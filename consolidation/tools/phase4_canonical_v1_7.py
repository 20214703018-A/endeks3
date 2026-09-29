#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.7: TÜİK SDMX (databrowser2) resmî seri ambarı.
Kaynak: 432 akış → 1.762.120 seri, 17.921.552 gözlem (gece turu 2026-09-24, satır muhasebesi tam).
  geo_entity ......... NUTS1 (12 bölge) ve NUTS2 (26 alt bölge) varlıkları eklenir; il kayıtlarına NUTS3 kodu (nuts_code) yazılır
  sdmx_geo_map ....... SDMX REF_AREA kodu → geo_id (TR=ülke, TRx=NUTS1, TRxx=NUTS2, 4 haneli sayı=ilçe TÜİK kodu, TRxxx=il)
  indicator_sdmx_series ....... seri anahtarı + boyut sözlüğü (JSON) + geo bağlantısı
  indicator_sdmx_observation .. seri × dönem × değer
Not: TÜİK'in kendi boyut adları korunur (çeviri/yorum yapılmaz); değerler başka kaynakla birleştirilmez.
"""
import time, datetime as dt, json, hashlib, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
SERI = OUT / "staging" / "v1.0.0" / "demographics_context" / "stg_tuik_sdmx_seri" / "part-NIGHT_tuik_sdmx_seri.parquet"
GOZ = OUT / "staging" / "v1.0.0" / "demographics_context" / "stg_tuik_sdmx_gozlem" / "part-NIGHT_tuik_sdmx_gozlem.parquet"
MODEL_VERSION = "canonical_v1.7"; MIN_FREE = 1_200_000_000


def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if free_bytes(OUT) < MIN_FREE: raise SystemExit("DİSK KORUMASI")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute("SET threads=2")
    c.execute(f"SET temp_directory='{OUT}/tmp/canon16'"); c.execute("SET preserve_insertion_order=false")
    c.execute("CREATE OR REPLACE MACRO norm(x) AS trim(regexp_replace(regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9 ]+', ' ', 'g'), ' +', ' ', 'g'))")
    rep = {"built_at": now, "stage": "v1.7"}
    c.execute("ALTER TABLE geo_entity ADD COLUMN IF NOT EXISTS nuts_code VARCHAR")

    c.execute(f"CREATE OR REPLACE TEMP TABLE ref AS SELECT DISTINCT raw_ref_alan_kodu kod, raw_ref_alan_adi ad FROM '{SERI}' WHERE raw_ref_alan_kodu IS NOT NULL AND raw_ref_alan_kodu <> '_Z'")
    # NUTS1 / NUTS2 varlıkları (TÜİK istatistiki bölge birimleri; geometri yok, tanım gereği onaylı)
    c.execute("""INSERT INTO geo_entity BY NAME SELECT 'GEO_NUTS1_' || r.kod geo_id, 'nuts1' AS "level", r.ad AS "name", norm(r.ad) name_norm,
        'tuik_nuts' geometry_source, 'tuik_sdmx' anchor_source, 'APPROVED (tanım: TÜİK NUTS kod listesi)' mapping_status,
        ? model_version, ? created_at, r.kod nuts_code
        FROM ref r WHERE length(r.kod)=3 AND r.kod LIKE 'TR%'
          AND NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id = 'GEO_NUTS1_' || r.kod)""", [MODEL_VERSION, now])
    c.execute("""INSERT INTO geo_entity BY NAME SELECT 'GEO_NUTS2_' || r.kod geo_id, 'nuts2' AS "level", r.ad AS "name", norm(r.ad) name_norm,
        'tuik_nuts' geometry_source, 'tuik_sdmx' anchor_source, 'APPROVED (tanım: TÜİK NUTS kod listesi)' mapping_status,
        ? model_version, ? created_at, r.kod nuts_code, 'GEO_NUTS1_' || substr(r.kod, 1, 3) parent_geo_id
        FROM ref r WHERE length(r.kod)=4 AND r.kod LIKE 'TR%'
          AND NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id = 'GEO_NUTS2_' || r.kod)""", [MODEL_VERSION, now])
    # il kayıtlarına NUTS3 kodu
    c.execute("""UPDATE geo_entity SET nuts_code = r.kod FROM ref r
        WHERE geo_entity.level='il' AND length(r.kod)=5 AND r.kod LIKE 'TR%' AND geo_entity.name_norm = norm(r.ad)""")

    c.execute("""CREATE OR REPLACE TABLE sdmx_geo_map AS
        SELECT r.kod ref_area_code, r.ad ref_area_name, 'GEO_TR' geo_id, 'ulke' geo_level, 'sabit(TR)' AS map_method FROM ref r WHERE r.kod='TR'
        UNION ALL SELECT r.kod, r.ad, 'GEO_NUTS1_' || r.kod, 'nuts1', 'nuts1_kod' FROM ref r WHERE length(r.kod)=3 AND r.kod LIKE 'TR%'
        UNION ALL SELECT r.kod, r.ad, 'GEO_NUTS2_' || r.kod, 'nuts2', 'nuts2_kod' FROM ref r WHERE length(r.kod)=4 AND r.kod LIKE 'TR%'
        UNION ALL SELECT r.kod, r.ad, e.geo_id, 'ilce', 'tuik_ilce_kodu' FROM ref r JOIN geo_entity e ON e.level='ilce' AND e.tuik_kodu = r.kod WHERE length(r.kod)=4 AND r.kod NOT LIKE 'TR%'
        UNION ALL SELECT r.kod, r.ad, e.geo_id, 'il', 'nuts3_ad' FROM ref r JOIN geo_entity e ON e.level='il' AND e.name_norm = norm(r.ad) WHERE length(r.kod)=5 AND r.kod LIKE 'TR%'""")
    rep["sdmx_geo_map"] = dict(c.execute("SELECT geo_level, count(*) FROM sdmx_geo_map GROUP BY 1").fetchall())
    rep["eslesmeyen_ref_area"] = c.execute("SELECT count(*) FROM ref r WHERE NOT EXISTS (SELECT 1 FROM sdmx_geo_map m WHERE m.ref_area_code = r.kod)").fetchone()[0]

    c.execute(f"""CREATE OR REPLACE TABLE indicator_sdmx_series AS
        SELECT 'SDS_' || md5(concat_ws('|', s.raw_akis_id, s.raw_seri_anahtari)) series_id, s.raw_akis_id dataflow_id,
            s.raw_akis_adi dataflow_name, s.raw_akis_aciklama dataflow_description, s.raw_seri_anahtari series_key,
            s.raw_boyutlar dimensions_json, s.raw_ref_alan_kodu ref_area_code, s.raw_ref_alan_adi ref_area_name,
            m.geo_id, m.geo_level, s.raw_gozlem_sayisi observation_count, s.raw_hazirlanma source_prepared_at,
            'official_public' acquisition_class, 'public' distribution_class, s.source_row_hash, 'stg_tuik_sdmx_seri' staging_view,
            s.source_path, ? created_at
        FROM '{SERI}' s LEFT JOIN sdmx_geo_map m ON m.ref_area_code = s.raw_ref_alan_kodu""", [now])
    print("  seri tablosu hazır", flush=True)
    c.execute(f"""CREATE OR REPLACE TABLE indicator_sdmx_observation AS
        SELECT 'SDS_' || md5(concat_ws('|', o.raw_akis_id, o.raw_seri_anahtari)) series_id, o.raw_akis_id dataflow_id,
            o.raw_seri_anahtari series_key, o.raw_zaman_kodu period, o.raw_zaman_adi period_label, o.raw_deger AS "value",
            o.raw_deger_metin raw_value, o.raw_nitelikler attributes_json, o.source_row_hash
        FROM '{GOZ}' o""")
    rep["seri"] = c.execute("SELECT count(*), count(geo_id), count(DISTINCT dataflow_id) FROM indicator_sdmx_series").fetchone()
    rep["gozlem"] = c.execute("SELECT count(*), count(value) FROM indicator_sdmx_observation").fetchone()
    rep["muhasebe"] = {
        "seri_kaynak_esit": c.execute(f"SELECT (SELECT count(*) FROM '{SERI}') = (SELECT count(*) FROM indicator_sdmx_series)").fetchone()[0],
        "gozlem_kaynak_esit": c.execute(f"SELECT (SELECT count(*) FROM '{GOZ}') = (SELECT count(*) FROM indicator_sdmx_observation)").fetchone()[0],
        "gozlem_seri_bagli": c.execute("SELECT count(*) FROM indicator_sdmx_observation o WHERE NOT EXISTS (SELECT 1 FROM indicator_sdmx_series s WHERE s.series_id=o.series_id)").fetchone()[0]}
    rep["gozlem_geo_duzeyi"] = dict(c.execute("""SELECT coalesce(s.geo_level,'(geo yok: _Z / ülke dışı boyut)'), count(*)
        FROM indicator_sdmx_observation o JOIN indicator_sdmx_series s USING (series_id) GROUP BY 1 ORDER BY 2 DESC""").fetchall())
    rep["ornek_akislar"] = c.execute("SELECT dataflow_id, any_value(dataflow_name), count(*) FROM indicator_sdmx_series GROUP BY 1 ORDER BY 3 DESC LIMIT 8").fetchall()
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("DELETE FROM _meta WHERE key IN ('built_at_v1_7','code_hash_v1_7')")
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_7', ?), ('code_hash_v1_7', ?)", [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    c.execute("CHECKPOINT")
    rep["seconds"] = round(time.time() - t0); rep["free_disk_gb"] = round(free_bytes(OUT) / 1e9, 1)
    rep["db_gb"] = round(CAN.stat().st_size / 1e9, 2)
    (OUT / "canonical" / "v1.1" / "build_report_v1_7.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_7_TUIK_SDMX", **rep,
                            "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    c.close()


if __name__ == "__main__":
    main()
