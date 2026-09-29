#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.6 C aşaması: soket/e-ticaret tabloları, ilçe analitiği, sürüm yükseltme ve parquet dışa aktarım.
  charging_socket ....... EPDK şarj soketleri (35.891) — istasyon POI'sine bağlı
  etbis_site ............ ETBİS e-ticaret siteleri (60.188 tekil site) + profil alanları (MERSİS/vergi/KEP = restricted)
  analytics.ilce_yeni_kaynaklar ... ilçe düzeyi market şubesi / şarj istasyonu / turizm tesisi sayıları
  _meta.model_version → canonical_v1.6 · parquet dışa aktarım
"""
import time, datetime as dt, json, hashlib, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
PQ = OUT / "canonical" / "v1.1" / "parquet"
MODEL_VERSION = "canonical_v1.6"; MIN_FREE = 1_200_000_000


def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if free_bytes(OUT) < MIN_FREE: raise SystemExit("DİSK KORUMASI")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute("SET threads=2")
    c.execute(f"SET temp_directory='{OUT}/tmp/canon16'"); c.execute("SET preserve_insertion_order=false")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    rep = {"built_at": now, "stage": "v1.6C"}

    # ---- charging_socket ----
    c.execute("""CREATE OR REPLACE TABLE charging_socket AS
        SELECT 'SOC_' || md5(concat_ws('|', CAST(k.raw_station_id AS VARCHAR), CAST(k.raw_socket_id AS VARCHAR))) socket_id,
            l.poi_id, CAST(k.raw_station_id AS VARCHAR) station_id, CAST(k.raw_socket_id AS VARCHAR) source_socket_id,
            k.raw_type socket_type, k.raw_sub_type socket_sub_type, CAST(k.raw_socket_number AS VARCHAR) socket_number,
            TRY_CAST(k.raw_power_kw AS DOUBLE) power_kw, TRY_CAST(k.raw_price_tl_kwh AS DOUBLE) price_tl_kwh,
            CAST(k.raw_status AS VARCHAR) status, CAST(k.raw_status_start AS VARCHAR) status_start,
            CAST(k.raw_status_end AS VARCHAR) status_end, CAST(k.raw_prices_json AS VARCHAR) prices_json,
            CAST(k.raw_guncellenme_tarihi AS VARCHAR) observed_at, 'official_public' acquisition_class, 'public' distribution_class,
            k.source_row_number, k.source_row_hash, 'stg_epdk_sarj_soket' staging_view, k.source_path, ? created_at
        FROM s.stg.poi_business__stg_epdk_sarj_soket k
        LEFT JOIN poi_source_link l ON l.source_system='epdk_sarj' AND l.source_record_id = CAST(k.raw_station_id AS VARCHAR)
             AND l.link_status IN ('merged','primary')""", [now])
    rep["charging_socket"] = c.execute("SELECT count(*), count(poi_id), (SELECT count(*) FROM s.stg.poi_business__stg_epdk_sarj_soket) FROM charging_socket").fetchone()

    # ---- etbis_site ----
    c.execute("""CREATE OR REPLACE TABLE etbis_site AS
        WITH k AS (SELECT raw_site_id site_id, any_value(raw_unvan) unvan, any_value(raw_site) site_url, any_value(raw_mobil) mobil_uygulama,
                     any_value(raw_il_adi) il_adi, any_value(raw_ilce_adi) ilce_adi, any_value(raw_sektor) sektor,
                     count(*) sayfa_kaydi, min(raw_cekim_zamani) ilk_gorulme, max(raw_cekim_zamani) son_gorulme,
                     any_value(source_row_hash) source_row_hash, any_value(source_path) source_path
                   FROM s.stg.poi_business__stg_etbis_site_kaydi GROUP BY 1),
             p AS (SELECT raw_site_id site_id, raw_isletme_adi, raw_mersis_no, raw_vergi_no, raw_isletme_turu,
                     raw_etbis_kayit_tarihi, raw_kep_adresleri, raw_hakkinda, raw_mal_hizmetler, raw_odeme_turleri,
                     raw_diger_siteler, raw_cekim_zamani profil_cekim, source_row_hash profil_row_hash
                   FROM s.stg.poi_business__stg_etbis_site_profil
                   QUALIFY row_number() OVER (PARTITION BY raw_site_id ORDER BY raw_cekim_zamani DESC) = 1)
        SELECT coalesce(k.site_id, p.site_id) site_id, k.unvan, k.site_url, k.mobil_uygulama, k.il_adi, k.ilce_adi, k.sektor,
               k.sayfa_kaydi, k.ilk_gorulme, k.son_gorulme,
               p.raw_isletme_adi isletme_adi, p.raw_isletme_turu isletme_turu, p.raw_etbis_kayit_tarihi etbis_kayit_tarihi,
               p.raw_hakkinda hakkinda, p.raw_mal_hizmetler mal_hizmetler, p.raw_odeme_turleri odeme_turleri,
               p.raw_diger_siteler diger_siteler, p.profil_cekim profil_cekim_zamani,
               p.raw_mersis_no restricted_mersis_no, p.raw_vergi_no restricted_vergi_no, p.raw_kep_adresleri restricted_kep_adresleri,
               (p.site_id IS NOT NULL) profil_var, 'official_public' acquisition_class,
               'public' distribution_class, 'restricted' restricted_column_policy,
               coalesce(k.source_row_hash, p.profil_row_hash) source_row_hash, k.source_path, ? created_at
        FROM k FULL OUTER JOIN p USING (site_id)""", [now])
    rep["etbis_site"] = {"satir": c.execute("SELECT count(*) FROM etbis_site").fetchone()[0],
        "profil_var": c.execute("SELECT count(*) FROM etbis_site WHERE profil_var").fetchone()[0],
        "kaynak_tekil_site": c.execute("SELECT count(DISTINCT raw_site_id) FROM s.stg.poi_business__stg_etbis_site_kaydi").fetchone()[0],
        "kaynak_profil": c.execute("SELECT count(*) FROM s.stg.poi_business__stg_etbis_site_profil").fetchone()[0],
        "il_dolu": c.execute("SELECT count(il_adi) FROM etbis_site").fetchone()[0]}

    # ---- analytics: ilçe düzeyi yeni kaynak sayıları ----
    c.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    c.execute("""CREATE OR REPLACE TABLE analytics.ilce_yeni_kaynaklar AS
        SELECT e.geo_id ilce_geo_id, e.name ilce_adi, l.name il_adi,
          count(*) FILTER (WHERE p.primary_source='marketfiyati' OR p.source_coverage LIKE '%marketfiyati%') market_sube_sayisi,
          count(*) FILTER (WHERE p.primary_source='epdk_sarj' OR p.source_coverage LIKE '%epdk_sarj%') sarj_istasyonu_sayisi,
          count(*) FILTER (WHERE p.primary_source='ktb' OR p.source_coverage LIKE '%ktb%') turizm_tesisi_sayisi,
          count(*) FILTER (WHERE p.primary_source='google') google_poi_sayisi, count(*) toplam_poi
        FROM geo_entity e JOIN geo_entity l ON l.geo_id = e.il_geo_id
        LEFT JOIN poi p ON p.assigned_ilce_geo_id = e.geo_id
        WHERE e.level='ilce' GROUP BY 1,2,3""")
    c.execute("""CREATE OR REPLACE TABLE analytics.ilce_urun_fiyat_ozet AS
        SELECT geo_id ilce_geo_id, source_system, product_id, count(*) gozlem,
               round(avg(price_value), 4) ort_fiyat, min(period) ilk_donem, max(period) son_donem
        FROM product_price_observation WHERE level='ilce' AND price_value IS NOT NULL GROUP BY 1,2,3""")
    rep["analytics"] = {"ilce_yeni_kaynaklar": c.execute("SELECT count(*) FROM analytics.ilce_yeni_kaynaklar").fetchone()[0],
                        "ilce_urun_fiyat_ozet": c.execute("SELECT count(*) FROM analytics.ilce_urun_fiyat_ozet").fetchone()[0]}

    # ---- sürüm ----
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("DELETE FROM _meta WHERE key IN ('built_at_v1_6','code_hash_v1_6')")
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_6', ?), ('code_hash_v1_6', ?)",
              [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    rep["poi_by_coverage"] = dict(c.execute("SELECT source_coverage, count(*) FROM poi GROUP BY 1 ORDER BY 2 DESC").fetchall())
    rep["poi_total"] = c.execute("SELECT count(*) FROM poi").fetchone()[0]
    rep["integrity"] = {
        "poi_id_unique": c.execute("SELECT count(*)=count(DISTINCT poi_id) FROM poi").fetchone()[0],
        "orphan_link": c.execute("SELECT count(*) FROM poi_source_link l WHERE NOT EXISTS (SELECT 1 FROM poi p WHERE p.poi_id=l.poi_id)").fetchone()[0],
        "price_obs_orphan_product": c.execute("SELECT count(*) FROM product_price_observation o WHERE NOT EXISTS (SELECT 1 FROM product p WHERE p.product_id=o.product_id)").fetchone()[0],
        "price_obs_no_geo": c.execute("SELECT count(*) FROM product_price_observation WHERE geo_id IS NULL").fetchone()[0],
        "socket_no_poi": c.execute("SELECT count(*) FROM charging_socket WHERE poi_id IS NULL").fetchone()[0]}
    c.execute("CHECKPOINT")

    # ---- parquet dışa aktarım ----
    exported = {}
    tables = ["product", "charging_socket", "etbis_site", "analytics.ilce_yeni_kaynaklar", "analytics.ilce_urun_fiyat_ozet",
              "poi_source_link"]
    # 24 M satırlık ürün fiyatı tablosunun parquet kopyası 1,4 GB tutuyor; disk dar olduğu için varsayılan olarak
    # yalnız DuckDB içinde tutulur. İstenirse: BIG_PARQUET=1 python3 tools/phase4_canonical_v1_6c.py
    if os.environ.get("BIG_PARQUET") == "1": tables.append("product_price_observation")
    for t in tables:
        if free_bytes(OUT) < MIN_FREE: exported[t] = "DISK_GUARD"; continue
        f = PQ / (t.split(".")[-1] + ".parquet")
        c.execute(f"COPY (SELECT * FROM {t}) TO '{f}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 200000)")
        exported[t] = round(f.stat().st_size / 1e6, 1)
    if free_bytes(OUT) >= MIN_FREE:
        f = PQ / "poi.parquet"
        c.execute(f"COPY (SELECT * EXCLUDE (geometry) FROM poi) TO '{f}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        exported["poi"] = round(f.stat().st_size / 1e6, 1)
    rep["parquet_mb"] = exported
    rep["seconds"] = round(time.time() - t0)
    rep["free_disk_gb"] = round(free_bytes(OUT) / 1e9, 1)
    (OUT / "canonical" / "v1.1" / "build_report_v1_6c.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_6C", **rep,
                            "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    c.close()


if __name__ == "__main__":
    main()
