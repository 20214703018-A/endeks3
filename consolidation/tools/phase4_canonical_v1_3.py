#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.3: OSM zaman serisi (harita varlığı) + kaynak-tutarlı turnover.
Kullanıcı kuralı (2026-09-19): "OSM geçmiş verileri tutulmalı; 2025 OSM ile 2026 Google karşılaştırılıp 'yeni yer açılmış' denmemeli — yalnız turnover için, aynı kaynak içinde."

Eklenen tablolar (yerinde yükseltme; v1 taban dosyası dokunulmaz):
  poi_lifecycle_osm     : OSM POI başına harita yaşam döngüsü (ilk/son görülme, aktif/kaldırıldı, ad-marka değişimi, taşınma, geçmiş JSON); mahalle ataması ST_Contains; poi.osm_id ile bağ
  poi_presence_osm      : 6 yıllık kesit × POI varlık gözlemi (2021-01-01 … 2026-09-13) — bitemporal varlık kaydı
  analytics.mahalle_turnover_osm : mahalle × kesit yılı × kategori grubu: mevcut, haritaya eklenen, haritadan kaldırılan, net, churn
Anlam kuralı (STANDARD §6.4): OSM 'ilk görülme' = haritaya eklenme, 'kaldırıldı' = haritadan silinme — işletme açılış/kapanışının VEKİLİDİR, kendisi değil.
2021-01-01 ilk kesit sol-sansürlüdür (o tarihte zaten var olanlar 'eklendi' sayılmaz). Google (yalnız 2026-09) ile OSM kesitleri arasında varlık farkı ASLA açılış/kapanış olarak yorumlanmaz.
"""
import time, datetime as dt, json, hashlib
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
MODEL_VERSION = "canonical_v1.3"; FIRST_SNAPSHOT = "2021-01-01"
GROUP = {"yeme_icme": ("restoran", "kafe", "fast_food", "bar", "firin"), "perakende": ("magaza", "market", "avm", "tekel", "toptanci", "pazar", "hal"),
         "hizmet": ("kuafor_guzellik", "eczane_disi_saglik", "ofis", "zanaat", "banka", "atm", "postane"), "saglik": ("eczane", "hastane", "saglik_ocagi", "klinik", "veteriner"),
         "konaklama": ("otel",), "egitim": ("okul", "ilkokul", "lise", "ortaokul", "anaokulu", "universite", "kutuphane", "ozel_egitim"), "sanayi": ("sanayi_alani", "fabrika", "sanayi_sitesi", "osb")}


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/canon13'")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    mv = c.execute("SELECT value FROM _meta WHERE key='model_version'").fetchone()[0]
    if mv != "canonical_v1.2": raise SystemExit(f"beklenen taban canonical_v1.2, bulunan {mv}")
    rep = {"built_at": now, "base": mv}
    grp_rows = [(alt, g) for g, alts in GROUP.items() for alt in alts]
    c.execute("CREATE TEMP TABLE grp (alt VARCHAR, grp VARCHAR)"); c.executemany("INSERT INTO grp VALUES (?,?)", grp_rows)
    # ---------------- poi_lifecycle_osm ----------------
    print("poi_lifecycle_osm…", flush=True)
    c.execute("DROP TABLE IF EXISTS poi_lifecycle_osm")
    c.execute(f"""CREATE TABLE poi_lifecycle_osm AS
        SELECT left(raw_osm_tip, 1) || raw_osm_id AS osm_ref, raw_osm_tip AS osm_type, raw_osm_id AS osm_id, raw_alt_kategori AS osm_category, coalesce(g.grp, 'diger') AS category_group,
               raw_marka AS brand, raw_ad AS name, TRY_CAST(raw_lon AS DOUBLE) AS lon, TRY_CAST(raw_lat AS DOUBLE) AS lat,
               raw_ilk_gorulme AS first_seen_map, raw_son_gorulme AS last_seen_map, raw_durum AS map_status, TRY_CAST(raw_kesit_sayisi AS INTEGER) AS snapshot_count,
               TRY_CAST(raw_ad_degisim AS INTEGER) AS name_change_count, TRY_CAST(raw_marka_degisim AS INTEGER) AS brand_change_count, TRY_CAST(raw_tasinma AS INTEGER) AS move_count, raw_gecmis AS history_json,
               (raw_ilk_gorulme = '{FIRST_SNAPSHOT}') AS left_censored, (raw_durum = 'aktif') AS right_open,
               'map_presence' AS event_semantics, CAST(NULL AS VARCHAR) AS assigned_geo_id, CAST(NULL AS VARCHAR) AS assigned_ilce_geo_id, CAST(NULL AS VARCHAR) AS poi_id,
               source_file_id, source_row_number, source_row_hash, 'stg_osm_degisim__poi_yasam' AS staging_view, 'official_public' AS acquisition_class, 'public' AS distribution_class, '{now}' AS created_at
        FROM s.stg.poi_business__stg_osm_degisim__poi_yasam y LEFT JOIN grp g ON g.alt = y.raw_alt_kategori""")
    n_life = c.execute("SELECT count(*) FROM poi_lifecycle_osm").fetchone()[0]
    c.execute("""CREATE TEMP TABLE lg AS SELECT l.osm_ref, ge.geo_id, ge.parent_geo_id, row_number() OVER (PARTITION BY l.osm_ref ORDER BY ge.geo_id) rn FROM poi_lifecycle_osm l
                 JOIN geo_entity ge ON ge.level='mahalle' AND ge.geometry IS NOT NULL AND l.lon BETWEEN ge.bbox_xmin AND ge.bbox_xmax AND l.lat BETWEEN ge.bbox_ymin AND ge.bbox_ymax AND ST_Contains(ge.geometry, ST_Point(l.lon, l.lat))
                 WHERE l.lon BETWEEN 25 AND 45.5 AND l.lat BETWEEN 35.5 AND 42.5""")
    c.execute("UPDATE poi_lifecycle_osm SET assigned_geo_id = lg.geo_id, assigned_ilce_geo_id = lg.parent_geo_id FROM lg WHERE lg.osm_ref = poi_lifecycle_osm.osm_ref AND lg.rn = 1")
    c.execute("UPDATE poi_lifecycle_osm SET poi_id = p.poi_id FROM poi p WHERE p.osm_id = poi_lifecycle_osm.osm_ref")
    rep["poi_lifecycle_osm"] = {"rows": n_life, "assigned_mahalle": c.execute("SELECT count(assigned_geo_id) FROM poi_lifecycle_osm").fetchone()[0], "linked_to_poi": c.execute("SELECT count(poi_id) FROM poi_lifecycle_osm").fetchone()[0],
                                "status": dict(c.execute("SELECT map_status, count(*) FROM poi_lifecycle_osm GROUP BY 1").fetchall()), "left_censored": c.execute("SELECT count(*) FROM poi_lifecycle_osm WHERE left_censored").fetchone()[0],
                                "group": dict(c.execute("SELECT category_group, count(*) FROM poi_lifecycle_osm GROUP BY 1 ORDER BY 2 DESC").fetchall())}
    print("  ", rep["poi_lifecycle_osm"], f"({time.time()-t0:.0f}s)", flush=True)
    # ---------------- poi_presence_osm ----------------
    print("poi_presence_osm…", flush=True)
    c.execute("DROP TABLE IF EXISTS poi_presence_osm")
    c.execute(f"""CREATE TABLE poi_presence_osm AS
        SELECT raw_tarih AS snapshot_date, left(raw_osm_tip, 1) || raw_osm_id AS osm_ref, raw_alt_kategori AS osm_category, raw_marka AS brand, raw_ad AS name, TRY_CAST(raw_lon AS DOUBLE) AS lon, TRY_CAST(raw_lat AS DOUBLE) AS lat,
               'map_presence' AS event_semantics, source_file_id, source_row_number, source_row_hash, 'stg_osm_degisim__poi_yillik' AS staging_view, '{now}' AS created_at
        FROM s.stg.poi_business__stg_osm_degisim__poi_yillik""")
    snaps = [r[0] for r in c.execute("SELECT DISTINCT snapshot_date FROM poi_presence_osm ORDER BY 1").fetchall()]
    rep["poi_presence_osm"] = {"rows": c.execute("SELECT count(*) FROM poi_presence_osm").fetchone()[0], "snapshots": snaps}
    # ---------------- analytics.mahalle_turnover_osm ----------------
    print("analytics.mahalle_turnover_osm…", flush=True)
    c.execute("CREATE TEMP TABLE snap (snapshot_date VARCHAR, prev_date VARCHAR)")
    c.executemany("INSERT INTO snap VALUES (?,?)", [(snaps[i], snaps[i-1] if i else None) for i in range(len(snaps))])
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_turnover_osm AS
        WITH base AS (SELECT l.*, s.snapshot_date, s.prev_date FROM poi_lifecycle_osm l CROSS JOIN snap s WHERE l.assigned_geo_id IS NOT NULL),
        agg AS (
          SELECT assigned_geo_id AS geo_id, snapshot_date, prev_date, grp AS category_group,
                 count(*) FILTER (WHERE first_seen_map <= snapshot_date AND last_seen_map >= snapshot_date) AS present_on_map,
                 count(*) FILTER (WHERE first_seen_map = snapshot_date AND NOT left_censored) AS added_to_map,
                 count(*) FILTER (WHERE prev_date IS NOT NULL AND last_seen_map = prev_date AND map_status = 'kaldirildi') AS removed_from_map,
                 count(*) FILTER (WHERE first_seen_map <= snapshot_date AND last_seen_map >= snapshot_date AND name_change_count > 0) AS with_name_change
          FROM (SELECT *, category_group AS grp FROM base UNION ALL SELECT *, 'all' AS grp FROM base) GROUP BY ALL)
        SELECT geo_id, snapshot_date, prev_date, category_group, present_on_map, added_to_map, removed_from_map, added_to_map - removed_from_map AS net_change,
               CASE WHEN prev_date IS NULL THEN NULL WHEN present_on_map + removed_from_map > 0 THEN round((added_to_map + removed_from_map)::DOUBLE / (present_on_map + removed_from_map), 4) END AS map_churn_rate,
               with_name_change, 'OSM_MAP_PRESENCE (harita varlığı; işletme açılış/kapanışının vekili; 2021-01-01 sol-sansürlü; Google ile karşılaştırılmaz)' AS method, ? AS built_at
        FROM agg""", [now])
    rep["turnover"] = {"rows": c.execute("SELECT count(*) FROM analytics.mahalle_turnover_osm").fetchone()[0],
                       "national_all": c.execute("SELECT snapshot_date, sum(present_on_map), sum(added_to_map), sum(removed_from_map) FROM analytics.mahalle_turnover_osm WHERE category_group='all' GROUP BY 1 ORDER BY 1").fetchall(),
                       "national_yeme_icme": c.execute("SELECT snapshot_date, sum(present_on_map), sum(added_to_map), sum(removed_from_map) FROM analytics.mahalle_turnover_osm WHERE category_group='yeme_icme' GROUP BY 1 ORDER BY 1").fetchall(),
                       "sample_muratpasa_sirinyali": c.execute("""SELECT snapshot_date, category_group, present_on_map, added_to_map, removed_from_map, map_churn_rate FROM analytics.mahalle_turnover_osm t JOIN geo_entity g ON g.geo_id=t.geo_id
                                                                 WHERE g.name='Şirinyalı' AND g.il_geo_id='GEO_IL_07' AND category_group IN ('all','yeme_icme') ORDER BY 2,1""").fetchall()}
    # mahalle_intelligence'a OSM harita churn (son kesit, all) sütunu
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_osm_latest AS SELECT geo_id, present_on_map AS osm_poi_on_map_2026, added_to_map AS osm_added_2025_2026, removed_from_map AS osm_removed_2025_2026, map_churn_rate AS osm_map_churn_2025_2026
                 FROM analytics.mahalle_turnover_osm WHERE category_group='all' AND snapshot_date = (SELECT max(snapshot_date) FROM snap)""")
    c.execute("""CREATE OR REPLACE VIEW analytics.mahalle_intelligence AS
        SELECT e.geo_id, e.name AS mahalle, i.name AS ilce, l.name AS il, e.tuik_il_kodu, e.web_district_id, e.tkgm_id, e.tuik_kodu, e.tkgm_link_band, e.tuik_link_band, e.centroid_lon, e.centroid_lat,
               pp.pop_2024_web, pp.pop_2025_tuik, pw.* EXCLUDE (geo_id),
               coalesce(ps.poi_count, 0) poi_count, coalesce(ps.poi_count_coord_verified, 0) poi_count_coord_verified, coalesce(ps.yeme_icme_count, 0) yeme_icme_count,
               coalesce(ps.kuafor_guzellik_count, 0) kuafor_guzellik_count, coalesce(ps.market_count, 0) market_count, ps.poi_avg_rating,
               coalesce(ps.poi_google_osm_count, 0) poi_google_osm_count, coalesce(ps.poi_osm_only_count, 0) poi_osm_only_count,
               om.osm_poi_on_map_2026, om.osm_added_2025_2026, om.osm_removed_2025_2026, om.osm_map_churn_2025_2026, e.mapping_status
        FROM geo_entity e JOIN geo_entity i ON i.geo_id = e.parent_geo_id JOIN geo_entity l ON l.geo_id = e.il_geo_id
        LEFT JOIN analytics.mahalle_pop pp ON pp.geo_id = e.geo_id LEFT JOIN analytics.mahalle_price_wide pw ON pw.geo_id = e.geo_id LEFT JOIN analytics.mahalle_poi_stats ps ON ps.geo_id = e.geo_id
        LEFT JOIN analytics.mahalle_osm_latest om ON om.geo_id = e.geo_id WHERE e.level='mahalle'""")
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_3', ?), ('code_hash_v1_3', ?), ('temporal_rule', 'source-consistent only: turnover from OSM snapshots; Google 2026-09 single-period; cross-source presence != lifecycle event')", [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    rep["integrity"] = {"lifecycle_osm_ref_unique": c.execute("SELECT count(*)=count(DISTINCT osm_ref) FROM poi_lifecycle_osm").fetchone()[0],
                        "presence_accounting(per-snapshot rows = stg kesit counts)": c.execute("SELECT bool_and(n = k) FROM (SELECT snapshot_date, count(*) n FROM poi_presence_osm GROUP BY 1) p JOIN (SELECT raw_tarih, TRY_CAST(raw_poi AS BIGINT) k FROM s.stg.poi_business__stg_osm_degisim__kesit) s2 ON s2.raw_tarih = p.snapshot_date").fetchone()[0],
                        "orphan_assignment": c.execute("SELECT count(*) FROM poi_lifecycle_osm l WHERE assigned_geo_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id=l.assigned_geo_id)").fetchone()[0],
                        "orphan_poi_link": c.execute("SELECT count(*) FROM poi_lifecycle_osm l WHERE poi_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM poi p WHERE p.poi_id=l.poi_id)").fetchone()[0]}
    rep["seconds"] = round(time.time() - t0)
    for t in ("poi_lifecycle_osm",): c.execute(f"COPY (SELECT * FROM {t}) TO '{OUT}/canonical/v1.1/parquet/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM analytics.mahalle_turnover_osm) TO '{OUT}/canonical/v1.1/parquet/mahalle_turnover_osm.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM analytics.mahalle_intelligence) TO '{OUT}/canonical/v1.1/parquet/mahalle_intelligence.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.close()
    (OUT / "canonical" / "v1.1" / "build_report_v1_3.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_3_OSM_HISTORY", "user_rule": "zaman karşılaştırması yalnız aynı kaynak içinde; OSM geçmişi turnover için", "lifecycle": rep["poi_lifecycle_osm"], "presence": rep["poi_presence_osm"], "integrity": rep["integrity"]}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
