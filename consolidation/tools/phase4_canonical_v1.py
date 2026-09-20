#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1 (PROPOSED eşlemelerle). Kaynak: staging/geoprop_staging.duckdb (salt okunur) + mappings/.
Çıktı: canonical/v1/geoprop_canonical_v1.duckdb (+ canonical/v1/parquet/*.parquet)

Tablolar (STANDARD §4):
  geo_entity            : il / ilçe / mahalle; GEO_ kimliği; web CityId/CountyId/DistrictId, TKGM id, TÜİK kodu bağlantıları (band+skor ile); geometri (web poligonu, EPSG:4326) + centroid
  population_observation: web 2024 (period resmî çapa testiyle atandı), TÜİK 2025 mahalle/köy, TÜİK ilçe serisi 2007–2025
  price_observation     : bölge paketleri birleşimi (81 il): konut/arsa aylık m² (satılık, kiralık), ort. fiyat, ilan sayısı; projeksiyon ayrı
  poi                   : Google Places benzersiz mekân (son gözlem) + türetilmiş kategori (v2) + mekânsal mahalle ataması (ST_Contains web poligonu)
  poi_snapshot          : her gözlem satırı (1,54 M) → poi_id
  analytics.mahalle_intelligence : sentez görünümü
Her satırda provenance (source_file_id, source_row_number, source_row_hash, staging_view). Hiçbir staging satırı silinmez/değiştirilmez.
"""
import time, datetime as dt, json, hashlib, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
STG = OUT / "staging" / "geoprop_staging.duckdb"
CAN_DIR = OUT / "canonical" / "v1"; CAN = CAN_DIR / "geoprop_canonical_v1.duckdb"; PQ = CAN_DIR / "parquet"
MODEL_VERSION = "canonical_v1"; MAPPING_STATUS = "PROPOSED (geo_matcher v2; golden set doğrulaması bekliyor)"
CUTOFF = "2026-08"


def main():
    t0 = time.time(); CAN_DIR.mkdir(parents=True, exist_ok=True); PQ.mkdir(exist_ok=True)
    if CAN.exists(): CAN.rename(CAN_DIR / f"geoprop_canonical_v1_superseded_{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}.duckdb")  # append-only: eski dosya silinmez
    c = duckdb.connect(str(CAN)); c.execute("INSTALL spatial; LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/canon'")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    c.execute(f"CREATE VIEW m_geo AS SELECT * FROM '{OUT}/mappings/geo_id_mapping_v2_PROPOSED.parquet'")
    c.execute(f"CREATE VIEW m_cat AS SELECT * FROM '{OUT}/mappings/poi_category_predictions_v2.parquet'")
    c.execute("CREATE MACRO norm(x) AS regexp_replace(lower(replace(replace(replace(replace(replace(replace(replace(replace(replace(coalesce(x,''),'İ','i'),'I','ı'),'ç','c'),'ğ','g'),'ı','i'),'ö','o'),'ş','s'),'ü','u'),'â','a')), '[^a-z0-9]+', '', 'g')")
    # ---------------- geo_entity ----------------
    print("geo_entity…", flush=True)
    c.execute("""CREATE TABLE geo_entity (
        geo_id VARCHAR PRIMARY KEY, level VARCHAR, name VARCHAR, name_norm VARCHAR, parent_geo_id VARCHAR, il_geo_id VARCHAR,
        tuik_il_kodu VARCHAR, web_city_id VARCHAR, web_county_id VARCHAR, web_district_id VARCHAR, tkgm_id VARCHAR, tuik_kodu VARCHAR,
        tkgm_link_band VARCHAR, tkgm_link_score DOUBLE, tuik_link_band VARCHAR, tuik_link_score DOUBLE, link_flags VARCHAR,
        geometry_source VARCHAR, geometry GEOMETRY, centroid_lon DOUBLE, centroid_lat DOUBLE, bbox_xmin DOUBLE, bbox_ymin DOUBLE, bbox_xmax DOUBLE, bbox_ymax DOUBLE,
        anchor_source VARCHAR, source_file_id VARCHAR, source_row_number BIGINT, source_row_hash VARCHAR, staging_view VARCHAR, mapping_status VARCHAR, model_version VARCHAR, created_at VARCHAR)""")
    # il: TKGM resmî (81) + web CityId (ad ile) + plaka
    c.execute("""CREATE TEMP TABLE web_il AS SELECT DISTINCT raw_city_id cid, raw_city city, norm(raw_city) n FROM s.stg.reference_geography__stg_admin_polygon WHERE source_level='ilce'""")
    c.execute("""INSERT INTO geo_entity SELECT 'GEO_IL_' || lpad(w.cid, 2, '0'), 'il', t.raw_text, norm(t.raw_text), NULL, 'GEO_IL_' || lpad(w.cid, 2, '0'),
        lpad(w.cid, 2, '0'), w.cid, NULL, NULL, t.raw_id, NULL, 'name_exact', 1.0, NULL, NULL, NULL,
        'tkgm_official', t.geometry_original, ST_X(ST_Centroid(t.geometry_original)), ST_Y(ST_Centroid(t.geometry_original)), ST_XMin(t.geometry_original), ST_YMin(t.geometry_original), ST_XMax(t.geometry_original), ST_YMax(t.geometry_original),
        'tkgm_il', t.source_file_id, t.source_row_number, t.source_row_hash, 'stg_tkgm_il', ?, ?, ?
        FROM s.stg.reference_geography__stg_tkgm_il t JOIN web_il w ON w.n = norm(t.raw_text)""", [MAPPING_STATUS, MODEL_VERSION, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")])
    n_il = c.execute("SELECT count(*) FROM geo_entity WHERE level='il'").fetchone()[0]
    # ilçe: web poligonu çapa; TKGM ilçe (il+ad) ve TÜİK ilçe (il+ad) bağlantısı
    c.execute("""CREATE TEMP TABLE tk_ilce AS SELECT t.raw_id id, t.raw_text ad, norm(t.raw_text) n, norm(i.raw_text) il_n FROM s.stg.reference_geography__stg_tkgm_ilce t
                 JOIN s.stg.reference_geography__stg_tkgm_il i ON i.raw_id = regexp_extract(t.source_relative_path, 'ilceListe_(\\d+)', 1)""")
    c.execute("""CREATE TEMP TABLE tuik_ilce AS SELECT DISTINCT raw_tuik_kodu kod, norm(raw_il) il_n, norm(raw_ilce) n FROM s.stg.reference_geography__stg_tuik_nufus_ilce""")
    c.execute("""INSERT INTO geo_entity SELECT 'GEO_ILCE_' || lpad(CAST(row_number() OVER (ORDER BY p.raw_city_id, p.raw_county) AS VARCHAR), 6, '0'), 'ilce', p.raw_county, norm(p.raw_county),
        'GEO_IL_' || lpad(p.raw_city_id, 2, '0'), 'GEO_IL_' || lpad(p.raw_city_id, 2, '0'), lpad(p.raw_city_id, 2, '0'), p.raw_city_id, p.raw_county_id, NULL, tk.id, tu.kod,
        CASE WHEN tk.id IS NULL THEN 'no_candidate' ELSE 'name_exact_same_il' END, CASE WHEN tk.id IS NULL THEN NULL ELSE 0.98 END,
        CASE WHEN tu.kod IS NULL THEN 'no_candidate' ELSE 'name_exact_same_il' END, CASE WHEN tu.kod IS NULL THEN NULL ELSE 0.98 END, NULL,
        'web_polygon', p.geometry_original, p.centroid_lon, p.centroid_lat, p.bbox_xmin, p.bbox_ymin, p.bbox_xmax, p.bbox_ymax,
        'web_polygon', p.source_file_id, p.source_row_number, p.source_row_hash, 'stg_admin_polygon', ?, ?, ?
        FROM s.stg.reference_geography__stg_admin_polygon p
        LEFT JOIN tk_ilce tk ON tk.il_n = norm(p.raw_city) AND tk.n = norm(p.raw_county)
        LEFT JOIN tuik_ilce tu ON tu.il_n = norm(p.raw_city) AND tu.n = norm(p.raw_county)
        WHERE p.source_level='ilce'""", [MAPPING_STATUS, MODEL_VERSION, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")])
    # mahalle: mapping v2 (web DistrictId çapa)
    c.execute("""INSERT INTO geo_entity SELECT m.geo_id, 'mahalle', p.raw_district, norm(p.raw_district), e.geo_id, 'GEO_IL_' || lpad(p.raw_city_id, 2, '0'), lpad(p.raw_city_id, 2, '0'),
        p.raw_city_id, p.raw_county_id, p.raw_district_id, m.tkgm_id, m.tuik_kodu, m.tkgm_band, m.tkgm_score, m.tuik_band, m.tuik_score, concat_ws(';', m.tkgm_flags, m.tuik_flags),
        'web_polygon', p.geometry_original, p.centroid_lon, p.centroid_lat, p.bbox_xmin, p.bbox_ymin, p.bbox_xmax, p.bbox_ymax,
        'web_polygon', p.source_file_id, p.source_row_number, p.source_row_hash, 'stg_admin_polygon', ?, ?, ?
        FROM m_geo m JOIN s.stg.reference_geography__stg_admin_polygon p ON p.raw_district_id = m.web_district_id AND p.source_level='mahalle' AND p.polygon_kind='admin'
        LEFT JOIN geo_entity e ON e.level='ilce' AND e.web_county_id = p.raw_county_id""", [MAPPING_STATUS, MODEL_VERSION, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")])
    counts = dict(c.execute("SELECT level, count(*) FROM geo_entity GROUP BY 1").fetchall()); print("  ", counts, f"({time.time()-t0:.0f}s)", flush=True)
    # ---------------- population_observation ----------------
    print("population_observation…", flush=True)
    c.execute("""CREATE TABLE population_observation (observation_id VARCHAR, geo_id VARCHAR, level VARCHAR, metric VARCHAR, raw_value VARCHAR, parsed_value BIGINT, unit VARCHAR, period VARCHAR, period_assignment VARCHAR,
        observation_kind VARCHAR, acquisition_class VARCHAR, distribution_class VARCHAR, source_priority INTEGER, source_file_id VARCHAR, source_row_number BIGINT, source_row_hash VARCHAR, staging_view VARCHAR, recorded_at VARCHAR)""")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c.execute("""INSERT INTO population_observation SELECT 'OBS_POP_' || md5(e.geo_id || 'web2024'), e.geo_id, 'mahalle', 'population', p.raw_population, p.typed_population, 'person', '2024', 'official_anchor_test (752/973 ilçe TÜİK 2024 ile birebir; validation/reference_geography_v1_accuracy_report.md)',
        'measured', 'web_research', 'public', 60, p.source_file_id, p.source_row_number, p.source_row_hash, 'stg_admin_polygon', ?
        FROM geo_entity e JOIN s.stg.reference_geography__stg_admin_polygon p ON p.raw_district_id = e.web_district_id AND p.source_level='mahalle' AND p.polygon_kind='admin' WHERE e.level='mahalle' AND p.typed_population IS NOT NULL""", [now])
    c.execute("""INSERT INTO population_observation SELECT 'OBS_POP_' || md5(e.geo_id || 'tuik' || t.raw_yil), e.geo_id, 'mahalle', 'population', t.raw_nufus, TRY_CAST(t.raw_nufus AS BIGINT), 'person', t.raw_yil, 'source_declared',
        'measured', 'official_public', 'public', 100, t.source_file_id, t.source_row_number, t.source_row_hash, 'stg_tuik_nufus_mahalle', ?
        FROM geo_entity e JOIN s.stg.reference_geography__stg_tuik_nufus_mahalle t ON t.raw_tuik_kodu = e.tuik_kodu WHERE e.level='mahalle' AND e.tuik_kodu IS NOT NULL""", [now])
    c.execute("""INSERT INTO population_observation SELECT 'OBS_POP_' || md5(e.geo_id || 'tuikkoy' || t.raw_yil), e.geo_id, 'mahalle', 'population', t.raw_nufus, TRY_CAST(t.raw_nufus AS BIGINT), 'person', t.raw_yil, 'source_declared',
        'measured', 'official_public', 'public', 100, t.source_file_id, t.source_row_number, t.source_row_hash, 'stg_tuik_nufus_koy', ?
        FROM geo_entity e JOIN s.stg.reference_geography__stg_tuik_nufus_koy t ON t.raw_tuik_kodu = e.tuik_kodu WHERE e.level='mahalle' AND e.tuik_kodu IS NOT NULL""", [now])
    c.execute("""INSERT INTO population_observation SELECT 'OBS_POP_' || md5(e.geo_id || 'tuikilce' || t.raw_yil), e.geo_id, 'ilce', 'population', t.raw_nufus, TRY_CAST(t.raw_nufus AS BIGINT), 'person', t.raw_yil, 'source_declared',
        'measured', 'official_public', 'public', 100, t.source_file_id, t.source_row_number, t.source_row_hash, 'stg_tuik_nufus_ilce', ?
        FROM geo_entity e JOIN s.stg.reference_geography__stg_tuik_nufus_ilce t ON t.raw_tuik_kodu = e.tuik_kodu AND e.level='ilce'""", [now])
    print("  ", c.execute("SELECT staging_view, count(*) FROM population_observation GROUP BY 1").fetchall(), f"({time.time()-t0:.0f}s)", flush=True)
    # ---------------- price_observation ----------------
    print("price_observation…", flush=True)
    c.execute("""CREATE TABLE price_observation (observation_id VARCHAR, geo_id VARCHAR, level VARCHAR, category VARCHAR, metric VARCHAR, raw_value VARCHAR, parsed_value DOUBLE, unit VARCHAR, period VARCHAR,
        observation_kind VARCHAR, acquisition_class VARCHAR, distribution_class VARCHAR, source_priority INTEGER, source_file_id VARCHAR, source_row_number BIGINT, source_row_hash VARCHAR, staging_view VARCHAR, recorded_at VARCHAR)""")
    c.execute("""CREATE TEMP TABLE pt AS SELECT p.*, CASE WHEN raw_seviye='mahalle' THEN (SELECT geo_id FROM geo_entity e WHERE e.level='mahalle' AND e.web_district_id=p.raw_district_id LIMIT 1)
                                                 WHEN raw_seviye='ilce' THEN (SELECT geo_id FROM geo_entity e WHERE e.level='ilce' AND e.web_county_id=p.raw_county_id LIMIT 1)
                                                 WHEN raw_seviye='il' THEN 'GEO_IL_' || lpad(raw_city_id, 2, '0') END AS geo_id,
                 CASE WHEN raw_projeksiyon='1' OR raw_ay > ? THEN 'projected' ELSE 'measured' END kind
                 FROM s.stg.price_series__stg_price_trend_monthly p WHERE source_relative_path LIKE '%/BOLGE_CSV/%'""", [CUTOFF])
    for metric, col, unit in (("sale_price_m2", "raw_satilik_m2_fiyat", "TL/m2"), ("rent_price_m2", "raw_kiralik_m2_fiyat", "TL/m2"), ("avg_price", "raw_ortalama_fiyat", "TL"), ("listing_count", "raw_ilan_sayisi", "count")):
        c.execute(f"""INSERT INTO price_observation SELECT 'OBS_PRC_' || md5(geo_id || raw_kategori || raw_ay || '{metric}' || source_row_hash), geo_id, raw_seviye, raw_kategori, '{metric}', {col}, TRY_CAST({col} AS DOUBLE), '{unit}', raw_ay,
            kind, 'web_research', 'public', 60, source_file_id, source_row_number, source_row_hash, 'stg_price_trend_monthly(BOLGE_CSV)', ?
            FROM pt WHERE geo_id IS NOT NULL AND {col} IS NOT NULL AND {col} <> ''""", [now])
    print("  ", c.execute("SELECT observation_kind, count(*) FROM price_observation GROUP BY 1").fetchall(), "| bağlanamayan satır:", c.execute("SELECT count(*) FROM pt WHERE geo_id IS NULL").fetchone()[0], f"({time.time()-t0:.0f}s)", flush=True)
    # ---------------- poi + poi_snapshot ----------------
    print("poi…", flush=True)
    c.execute("""CREATE TABLE poi (poi_id VARCHAR PRIMARY KEY, source_place_id VARCHAR, name VARCHAR, raw_category VARCHAR, predicted_category VARCHAR, predicted_sector VARCHAR, category_method VARCHAR, category_confidence DOUBLE, category_band VARCHAR,
        rating DOUBLE, rating_count BIGINT, review_count BIGINT, phone VARCHAR, address_raw VARCHAR, lon DOUBLE, lat DOUBLE, geometry GEOMETRY, source_il VARCHAR, source_il_reliability VARCHAR,
        assigned_geo_id VARCHAR, assignment_method VARCHAR, last_observed_at VARCHAR, observation_count BIGINT, source_file_id VARCHAR, source_row_number BIGINT, source_row_hash VARCHAR, staging_view VARCHAR, acquisition_class VARCHAR, distribution_class VARCHAR, created_at VARCHAR)""")
    c.execute("""CREATE TEMP TABLE last AS SELECT raw_google_place_id pid, arg_max(raw_isim, raw_observed_at) isim, arg_max(raw_ana_kategori, raw_observed_at) ana, arg_max(raw_puan, raw_observed_at) puan,
        arg_max(raw_degerlendirme_sayisi, raw_observed_at) deg, arg_max(raw_yorum_sayisi, raw_observed_at) yor, arg_max(raw_telefon, raw_observed_at) tel, arg_max(raw_tam_adres, raw_observed_at) adres,
        arg_max(TRY_CAST(raw_lon AS DOUBLE), raw_observed_at) lon, arg_max(TRY_CAST(raw_lat AS DOUBLE), raw_observed_at) lat, arg_max(raw_il, raw_observed_at) il, max(raw_observed_at) last_at, count(*) n,
        arg_max(source_file_id, raw_observed_at) sf, arg_max(source_row_number, raw_observed_at) sr, arg_max(source_row_hash, raw_observed_at) sh
        FROM s.stg.poi_business__stg_google_places_ve_yogunluk__google_places_gozlem GROUP BY 1""")
    # mekânsal atama: bbox ön filtre + ST_Contains (web mahalle poligonu)
    c.execute("""CREATE TEMP TABLE assign AS SELECT l.pid, e.geo_id FROM last l JOIN geo_entity e ON e.level='mahalle' AND l.lon BETWEEN e.bbox_xmin AND e.bbox_xmax AND l.lat BETWEEN e.bbox_ymin AND e.bbox_ymax
        WHERE l.lon IS NOT NULL AND ST_Contains(e.geometry, ST_Point(l.lon, l.lat))""")
    c.execute("CREATE TEMP TABLE assign1 AS SELECT pid, arg_min(geo_id, geo_id) geo_id, count(*) n FROM assign GROUP BY 1")
    c.execute("""INSERT INTO poi SELECT 'POI_' || lpad(CAST(row_number() OVER (ORDER BY l.pid) AS VARCHAR), 7, '0'), l.pid, l.isim, l.ana,
        CASE WHEN m.band='high' THEN m.predicted_category ELSE NULL END, CASE WHEN m.band='high' THEN m.predicted_sector ELSE NULL END, m.method, m.confidence, m.band,
        TRY_CAST(l.puan AS DOUBLE), TRY_CAST(l.deg AS BIGINT), TRY_CAST(l.yor AS BIGINT), l.tel, l.adres, l.lon, l.lat, CASE WHEN l.lon IS NOT NULL THEN ST_Point(l.lon, l.lat) END, l.il,
        'UNRELIABLE (kaynakta il sütunu çoğunlukla arama kategorisi; NDR_000002)', a.geo_id, CASE WHEN a.geo_id IS NOT NULL THEN 'ST_Contains(web_polygon)' || CASE WHEN a.n>1 THEN ' (çoklu poligon; ilk seçildi)' ELSE '' END ELSE NULL END,
        l.last_at, l.n, l.sf, l.sr, l.sh, 'stg_google_places_gozlem', 'web_research', 'public', ?
        FROM last l LEFT JOIN m_cat m ON m.google_place_id = l.pid LEFT JOIN assign1 a ON a.pid = l.pid""", [now])
    c.execute("""CREATE TABLE poi_snapshot AS SELECT 'OBS_POI_' || md5(g.source_row_hash) observation_id, p.poi_id, g.raw_observed_at observed_at, g.raw_arama_terimi search_term, g.raw_puan raw_rating, g.raw_degerlendirme_sayisi raw_rating_count, g.raw_yorum_sayisi raw_review_count,
        g.raw_calisma_saatleri raw_hours, g.raw_payload_sha256 payload_sha256, g.source_file_id, g.source_row_number, g.source_row_hash, 'stg_google_places_gozlem' staging_view
        FROM s.stg.poi_business__stg_google_places_ve_yogunluk__google_places_gozlem g JOIN poi p ON p.source_place_id = g.raw_google_place_id""")
    print("  poi:", c.execute("SELECT count(*), count(assigned_geo_id), count(predicted_category) FROM poi").fetchone(), "snapshot:", c.execute("SELECT count(*) FROM poi_snapshot").fetchone()[0], f"({time.time()-t0:.0f}s)", flush=True)
    # ---------------- analytics ----------------
    print("analytics…", flush=True)
    c.execute("CREATE SCHEMA analytics")
    c.execute(f"""CREATE VIEW analytics.mahalle_intelligence AS
        SELECT e.geo_id, e.name AS mahalle, i.name AS ilce, l.name AS il, e.tuik_il_kodu, e.web_district_id, e.tkgm_id, e.tuik_kodu, e.tkgm_link_band, e.tuik_link_band, e.centroid_lon, e.centroid_lat,
          (SELECT parsed_value FROM population_observation o WHERE o.geo_id=e.geo_id AND o.staging_view='stg_admin_polygon') AS pop_2024_web,
          (SELECT parsed_value FROM population_observation o WHERE o.geo_id=e.geo_id AND o.staging_view IN ('stg_tuik_nufus_mahalle','stg_tuik_nufus_koy') AND o.period='2025' LIMIT 1) AS pop_2025_tuik,
          (SELECT parsed_value FROM price_observation o WHERE o.geo_id=e.geo_id AND o.category='konut' AND o.metric='sale_price_m2' AND o.period='{CUTOFF}' LIMIT 1) AS konut_satilik_m2_{CUTOFF.replace('-','_')},
          (SELECT parsed_value FROM price_observation o WHERE o.geo_id=e.geo_id AND o.category='konut' AND o.metric='rent_price_m2' AND o.period='{CUTOFF}' LIMIT 1) AS konut_kiralik_m2_{CUTOFF.replace('-','_')},
          (SELECT parsed_value FROM price_observation o WHERE o.geo_id=e.geo_id AND o.category='arsa' AND o.metric='sale_price_m2' AND o.period='{CUTOFF}' LIMIT 1) AS arsa_satilik_m2_{CUTOFF.replace('-','_')},
          (SELECT count(*) FROM poi p WHERE p.assigned_geo_id=e.geo_id) AS poi_count,
          (SELECT count(*) FROM poi p WHERE p.assigned_geo_id=e.geo_id AND coalesce(p.predicted_sector, '') = 'Yeme-İçme' OR (p.assigned_geo_id=e.geo_id AND p.raw_category IN ('Kafe','Restoran & Lokanta','Fast Food','3. Nesil Kahveci','Kebapçı & Ocakbaşı','Pastane & Fırın','Tatlı','Türk'))) AS yeme_icme_count,
          (SELECT count(*) FROM poi p WHERE p.assigned_geo_id=e.geo_id AND (p.raw_category='Kuaför & Güzellik' OR p.predicted_category IN ('Kuaför & Berber','Güzellik & Kozmetik'))) AS kuafor_guzellik_count,
          (SELECT count(*) FROM poi p WHERE p.assigned_geo_id=e.geo_id AND (p.raw_category='Bakkal & Market' OR p.predicted_category IN ('Süpermarket & Market','Ucuzluk & İndirim Market'))) AS market_count,
          (SELECT avg(rating) FROM poi p WHERE p.assigned_geo_id=e.geo_id AND rating IS NOT NULL) AS poi_avg_rating,
          e.mapping_status
        FROM geo_entity e LEFT JOIN geo_entity i ON i.geo_id=e.parent_geo_id LEFT JOIN geo_entity l ON l.geo_id=e.il_geo_id WHERE e.level='mahalle'""")
    # ---------------- export + meta ----------------
    for t in ("geo_entity", "population_observation", "price_observation", "poi", "poi_snapshot"):
        c.execute(f"COPY (SELECT * EXCLUDE (geometry) FROM {t}) TO '{PQ}/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)" if t in ("geo_entity", "poi") else f"COPY {t} TO '{PQ}/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT geo_id, level, ST_AsWKB(geometry) AS geometry_wkb FROM geo_entity WHERE geometry IS NOT NULL) TO '{PQ}/geo_entity_geometry.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute("CREATE TABLE _meta (key VARCHAR, value VARCHAR)")
    for k, v in (("model_version", MODEL_VERSION), ("mapping_status", MAPPING_STATUS), ("standard_version", "1.0.0"), ("built_at", now), ("observation_cutoff", CUTOFF), ("code_hash", hashlib.sha256(Path(__file__).read_bytes()).hexdigest())):
        c.execute("INSERT INTO _meta VALUES (?, ?)", [k, v])
    stats = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("geo_entity", "population_observation", "price_observation", "poi", "poi_snapshot")}
    sample = c.execute("SELECT il, ilce, mahalle, pop_2024_web, pop_2025_tuik, konut_satilik_m2_2026_08, poi_count, yeme_icme_count, round(poi_avg_rating,2) FROM analytics.mahalle_intelligence WHERE il='Antalya' AND ilce='Muratpaşa' ORDER BY poi_count DESC NULLS LAST LIMIT 5").fetchall()
    cov = c.execute("SELECT count(*), count(pop_2025_tuik), count(konut_satilik_m2_2026_08), count(*) FILTER (WHERE poi_count>0) FROM analytics.mahalle_intelligence").fetchone()
    c.close()
    rep = {"built_at": now, "tables": stats, "mahalle_coverage": {"total": cov[0], "with_tuik_2025_pop": cov[1], "with_konut_price_2026_08": cov[2], "with_poi": cov[3]}, "sample_muratpasa": sample, "seconds": round(time.time() - t0), "db": str(CAN), "db_mb": round(os.path.getsize(CAN) / 1e6, 1)}
    (CAN_DIR / "build_report_v1.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as fh: fh.write(json.dumps({"timestamp": now, "operation": "phase4_canonical_build", "output": str(CAN), "tables": stats, "mapping_status": MAPPING_STATUS, "pipeline_version": "1.0.0", "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
