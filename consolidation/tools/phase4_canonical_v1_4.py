#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.4: Yemeksepeti (çevrimiçi sipariş platformu) üçüncü POI kaynağı + menü/teslimat gözlemleri.
Kaynak: stg.poi_business__stg_yemeksepeti_records (venue_page 513 [420 JSON-LD+geo], menu_item 29.340). Toplayıcı kesiti 2026-09-19 (canlı değil).

  1. ys_venue: JSON-LD'den ad, koordinat, adres, mutfak, fiyat aralığı, puan; metrics'ten min sepet, teslimat süresi/ücreti (platform varsayılan konumu için tahmin — not korunur).
  2. Eşleştirme (poi_matcher v1 ile aynı kanıt modeli, 250 m): ad benzerliği (JW/token) + mesafe; telefon YS'de yok. high+ (≥0.95) → mevcut poi'ye bağlanır, source_coverage'a '+yemeksepeti' eklenir;
     review → ayrı kayıt + link_status='review'; eşleşmeyen → poi'ye 'yemeksepeti_only' yeni kayıt (mahalle ST_Contains).
  3. menu_item_observation (29.340): poi_id ↔ kalem (başlık, açıklama, fiyat/indirimli, tükendi, kategori, gözlem zamanı).
  4. venue_delivery_observation (513): puan, puan sayısı, min sepet, teslimat süresi alt/üst, teslimat ücreti (toplam/orijinal), sağlayıcı, sayfa türü, gözlem zamanı.
Zaman kuralı (§6.4): YS varlığı 2026-09 tek kesittir; açılış/kapanış çıkarımı yapılmaz.
"""
import time, datetime as dt, json, hashlib, re
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
MODEL_VERSION = "canonical_v1.4"; V = "POI_MATCHER_V1_YS"; MERGE_BANDS = ("virtually_certain", "very_high", "high")
TH = {"virtually_certain": 0.995, "very_high": 0.980, "high": 0.950, "review": 0.850}


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/canon14'")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    mv = c.execute("SELECT value FROM _meta WHERE key='model_version'").fetchone()[0]
    if mv != "canonical_v1.3": raise SystemExit(f"beklenen taban canonical_v1.3, bulunan {mv}")
    rep = {"built_at": now, "base": mv}
    c.execute("CREATE OR REPLACE MACRO norm(x) AS trim(regexp_replace(regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9 ]+', ' ', 'g'), ' +', ' ', 'g'))")
    # ---- 1. ys_venue ----
    c.execute("""CREATE TEMP TABLE ysv AS
        SELECT raw_venue_code AS venue_code, raw_url AS url, raw_fetched_at AS fetched_at, raw_page_sha256 AS page_sha256, raw_city_norm AS city_norm,
               j->>'name' AS name, TRY_CAST(j->'geo'->>'latitude' AS DOUBLE) AS lat, TRY_CAST(j->'geo'->>'longitude' AS DOUBLE) AS lon,
               j->'address'->>'streetAddress' AS address, j->'address'->>'addressLocality' AS locality, j->>'servesCuisine' AS cuisine_json, j->>'priceRange' AS price_range,
               TRY_CAST(j->'aggregateRating'->>'ratingValue' AS DOUBLE) AS jsonld_rating, TRY_CAST(j->'aggregateRating'->>'ratingCount' AS INTEGER) AS jsonld_rating_count, j->>'description' AS description,
               TRY_CAST(m->>'rating_value' AS DOUBLE) AS rating_value, TRY_CAST(m->>'rating_count' AS INTEGER) AS rating_count, TRY_CAST(m->>'minimum_order_value' AS DOUBLE) AS minimum_order_value,
               TRY_CAST(m->>'delivery_time_lower_min' AS INTEGER) AS delivery_time_lower_min, TRY_CAST(m->>'delivery_time_upper_min' AS INTEGER) AS delivery_time_upper_min,
               TRY_CAST(m->>'delivery_fee_total' AS DOUBLE) AS delivery_fee_total, TRY_CAST(m->>'delivery_fee_original' AS DOUBLE) AS delivery_fee_original, m->>'delivery_provider' AS delivery_provider,
               m->>'page_kind' AS page_kind, m->>'note' AS metrics_note, m->>'delivery_hours' AS delivery_hours_json,
               source_file_id, source_row_number, source_row_hash
        FROM (SELECT *, TRY_CAST(raw_venue_jsonld AS JSON) j, TRY_CAST(raw_metrics AS JSON) m FROM s.stg.poi_business__stg_yemeksepeti_records WHERE raw_record_kind='venue_page')""")
    c.execute("CREATE TEMP TABLE ysu AS SELECT * FROM ysv QUALIFY row_number() OVER (PARTITION BY venue_code ORDER BY fetched_at DESC) = 1")  # aynı restoranın son kesiti
    n_v = c.execute("SELECT count(*), count(*) FILTER (WHERE lat IS NOT NULL), count(DISTINCT venue_code) FROM ysv").fetchone()
    # ---- 2. eşleştirme ----
    c.execute("""CREATE TEMP TABLE cand AS
        SELECT y.venue_code, p.poi_id, y.name ys_name, p.name g_name, p.source_coverage,
               2*6371000*asin(sqrt(pow(sin(radians(p.lat-y.lat)/2),2)+cos(radians(y.lat))*cos(radians(p.lat))*pow(sin(radians(p.lon-y.lon)/2),2))) AS dist_m,
               jaro_winkler_similarity(norm(y.name), norm(p.name)) AS jw,
               len(list_intersect(string_split(norm(y.name),' '), string_split(norm(p.name),' ')))::DOUBLE / greatest(1, len(list_distinct(string_split(norm(y.name),' ') || string_split(norm(p.name),' ')))) AS tok_jacc
        FROM ysu y JOIN poi p ON p.coord_validity='valid' AND p.lon BETWEEN y.lon-0.004 AND y.lon+0.004 AND p.lat BETWEEN y.lat-0.003 AND y.lat+0.003
        WHERE y.lat IS NOT NULL AND p.name IS NOT NULL""")
    c.execute("DELETE FROM cand WHERE dist_m > 250")
    c.execute(f"""CREATE TEMP TABLE sc AS SELECT *, greatest(jw, CASE WHEN tok_jacc >= 0.5 THEN 0.80 + 0.20*tok_jacc ELSE 0 END) AS name_sim FROM cand""")
    c.execute("""CREATE TEMP TABLE sc2 AS SELECT *,
        CASE WHEN name_sim >= 0.80 THEN 0.92*(name_sim-0.80)/0.20 WHEN name_sim >= 0.75 THEN 0.10 ELSE 0 END AS name_ev,
        CASE WHEN dist_m <= 25 THEN 0.85 WHEN dist_m <= 60 THEN 0.70 WHEN dist_m <= 120 THEN 0.50 WHEN dist_m <= 200 THEN 0.25 ELSE 0.10 END AS dist_ev,
        0.30 AS cat_ev  -- YS = gıda; aday tablosundaki poi gıda mı bakılır (aşağıda)
        FROM sc WHERE name_sim >= 0.75""")
    c.execute("""CREATE TEMP TABLE sc3 AS SELECT s2.*,
        (coalesce(p.predicted_sector,'') IN ('Yeme-İçme','Gıda Perakende') OR p.raw_category LIKE 'osm:%' OR regexp_matches(lower(coalesce(p.raw_category,'')||' '||coalesce(p.predicted_category,'')), 'dondurma|kafe|cafe|restoran|lokanta|kebap|pide|döner|börek|pastane|fırın|tatlı|çay|kahve|büfe|bar|pub|balık|köfte|çiğ|kokoreç|mantı|waffle|kumpir|gıda|yemek|kahvaltı|simit|künefe|baklava|çorba|ocakbaşı|izgara|tantuni|pizza|burger|pilav|bowl|tavuk')) AS p_food,
        (p.raw_category='Ticari Mekan' AND p.predicted_category IS NULL) AS p_unknown
        FROM sc2 s2 JOIN poi p ON p.poi_id = s2.poi_id""")
    c.execute("""CREATE TEMP TABLE sc4 AS SELECT *, round(CASE WHEN NOT p_food AND NOT p_unknown THEN 0.55 ELSE 1.0 END * (1 - (1-name_ev)*(1-dist_ev)*(1-CASE WHEN p_food THEN 0.30 ELSE 0 END)), 4) AS score,
        format('name_ev={:.2f}(sim={:.3f}); dist_ev={:.2f}({:.0f}m); cat={}', name_ev, name_sim, dist_ev, dist_m, CASE WHEN p_food THEN 'food+0.30' WHEN p_unknown THEN 'unknown' ELSE 'nonfood x0.55' END) AS reasons FROM sc3""")
    rows = c.execute("SELECT venue_code, poi_id, score FROM sc4 ORDER BY score DESC, dist_m").fetchall()
    uv, up, best = set(), set(), []
    for v_, p_, s_ in rows:
        if v_ in uv or p_ in up: continue
        uv.add(v_); up.add(p_); best.append((v_, p_))
    c.execute("CREATE TEMP TABLE best (venue_code VARCHAR, poi_id VARCHAR)"); c.executemany("INSERT INTO best VALUES (?,?)", best)
    c.execute(f"""CREATE TEMP TABLE m AS SELECT s.*, CASE WHEN score >= {TH['virtually_certain']} THEN 'virtually_certain' WHEN score >= {TH['very_high']} THEN 'very_high' WHEN score >= {TH['high']} THEN 'high' WHEN score >= {TH['review']} THEN 'review_recommended' ELSE 'no_auto_merge' END AS band FROM sc4 s JOIN best b USING (venue_code, poi_id)""")
    c.execute(f"COPY (SELECT *, '{V}' AS matcher_version, '{now}' AS created_at FROM m) TO '{OUT}/mappings/poi_match_ys_v1_PROPOSED.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    bands = dict(c.execute("SELECT band, count(*) FROM m GROUP BY 1").fetchall())
    # ---- 3. poi güncelle / ekle ----
    for col, typ in (("ys_venue_code", "VARCHAR"), ("ys_name", "VARCHAR"), ("ys_url", "VARCHAR"), ("ys_cuisine_json", "VARCHAR"), ("ys_price_range", "VARCHAR"), ("ys_lon", "DOUBLE"), ("ys_lat", "DOUBLE"), ("ys_match_score", "DOUBLE"), ("ys_match_band", "VARCHAR"), ("ys_source_row_hash", "VARCHAR")):
        c.execute(f"ALTER TABLE poi ADD COLUMN IF NOT EXISTS {col} {typ}")
    c.execute(f"""UPDATE poi SET source_coverage = replace(poi.source_coverage, '_only', '') || '+yemeksepeti', ys_venue_code=y.venue_code, ys_name=y.name, ys_url=y.url, ys_cuisine_json=y.cuisine_json, ys_price_range=y.price_range, ys_lon=y.lon, ys_lat=y.lat,
                  ys_match_score=m.score, ys_match_band=m.band, ys_source_row_hash=y.source_row_hash
                  FROM m JOIN ysu y ON y.venue_code=m.venue_code WHERE poi.poi_id=m.poi_id AND m.band IN {MERGE_BANDS}""")
    c.execute(f"""INSERT INTO poi_source_link SELECT 'LNK_' || md5('ys|' || m.venue_code || '|' || m.poi_id), m.poi_id, 'yemeksepeti', m.venue_code, y.source_file_id, y.source_row_hash, m.score, m.band, '{V}',
                  CASE WHEN m.band IN {MERGE_BANDS} THEN 'merged' ELSE 'review' END, m.reasons, ? FROM m JOIN ysu y ON y.venue_code=m.venue_code WHERE m.band <> 'no_auto_merge'""", [now])
    c.execute(f"CREATE TEMP TABLE merged_v AS SELECT venue_code FROM m WHERE band IN {MERGE_BANDS}")
    c.execute("""CREATE TEMP TABLE newv AS SELECT y.*, (y.lon BETWEEN 25 AND 45.5 AND y.lat BETWEEN 35.5 AND 42.5) in_tr FROM ysu y WHERE y.venue_code NOT IN (SELECT venue_code FROM merged_v)""")
    c.execute("""CREATE TEMP TABLE newv_geo AS SELECT n.venue_code, g.geo_id, g.parent_geo_id, row_number() OVER (PARTITION BY n.venue_code ORDER BY g.geo_id) rn FROM newv n
                 JOIN geo_entity g ON g.level='mahalle' AND g.geometry IS NOT NULL AND n.lon BETWEEN g.bbox_xmin AND g.bbox_xmax AND n.lat BETWEEN g.bbox_ymin AND g.bbox_ymax AND ST_Contains(g.geometry, ST_Point(n.lon, n.lat)) WHERE n.in_tr""")
    c.execute("""INSERT INTO poi BY NAME SELECT 'POI_YS_' || md5(n.venue_code) AS poi_id, n.venue_code AS source_place_id, n.name AS name, 'yemeksepeti:restaurant' AS raw_category, 'Restoran & Lokanta' AS predicted_category, 'Yeme-İçme' AS predicted_sector,
        'PLATFORM_TYPE' AS category_method, 0.90 AS category_confidence, 'high' AS category_band, n.rating_value AS rating, n.rating_count AS rating_count, n.address AS address_raw,
        n.lon AS lon, n.lat AS lat, CASE WHEN n.lat IS NOT NULL THEN ST_Point(n.lon, n.lat) END AS geometry, 'none' AS source_il_reliability,
        ng.geo_id AS assigned_geo_id, CASE WHEN ng.geo_id IS NOT NULL THEN 'ST_Contains(web_polygon)' END AS assignment_method, CASE WHEN ng.geo_id IS NOT NULL THEN 0.95 END AS assignment_confidence,
        ng.geo_id AS spatial_geo_id, ng.parent_geo_id AS assigned_ilce_geo_id, CASE WHEN ng.geo_id IS NOT NULL THEN 0.99 END AS ilce_assignment_confidence,
        CASE WHEN n.lat IS NULL THEN 'MISSING' WHEN NOT n.in_tr THEN 'OUT_OF_TURKEY' ELSE 'valid' END AS coord_validity,
        concat_ws(',', CASE WHEN n.lat IS NULL THEN 'NO_JSONLD_GEO' END, CASE WHEN n.in_tr AND ng.geo_id IS NULL THEN 'VALID_COORD_OUTSIDE_ALL_POLYGONS' END) AS assignment_flags,
        1 AS observation_count, n.fetched_at AS last_observed_at, n.source_file_id, n.source_row_number, n.source_row_hash, 'stg_yemeksepeti_records' AS staging_view, 'web_research' AS acquisition_class, 'internal' AS distribution_class, ? AS created_at,
        'yemeksepeti_only' AS source_coverage, 'yemeksepeti' AS primary_source,
        n.venue_code AS ys_venue_code, n.name AS ys_name, n.url AS ys_url, n.cuisine_json AS ys_cuisine_json, n.price_range AS ys_price_range, n.lon AS ys_lon, n.lat AS ys_lat, n.source_row_hash AS ys_source_row_hash
        FROM newv n LEFT JOIN newv_geo ng ON ng.venue_code=n.venue_code AND ng.rn=1""", [now])
    c.execute("UPDATE poi SET assignment_flags = NULL WHERE assignment_flags = ''")
    c.execute("""INSERT INTO poi_source_link SELECT 'LNK_' || md5('ys|' || ys_venue_code), poi_id, 'yemeksepeti', ys_venue_code, source_file_id, source_row_hash, NULL, NULL, NULL, 'primary', 'kaynak kaydın kendisi (yemeksepeti_only)', ? FROM poi WHERE primary_source='yemeksepeti'""", [now])
    # ---- 4. gözlem tabloları ----
    c.execute("CREATE TEMP TABLE vc2poi AS SELECT ys_venue_code venue_code, poi_id FROM poi WHERE ys_venue_code IS NOT NULL")
    c.execute("DROP TABLE IF EXISTS venue_delivery_observation")
    c.execute("""CREATE TABLE venue_delivery_observation AS SELECT 'OBS_DLV_' || md5(v.venue_code || v.fetched_at) AS observation_id, vp.poi_id, v.venue_code, 'yemeksepeti' AS platform, v.fetched_at AS observed_at, v.page_kind,
        v.rating_value, v.rating_count, v.minimum_order_value, v.delivery_time_lower_min, v.delivery_time_upper_min, v.delivery_fee_total, v.delivery_fee_original, v.delivery_provider, v.delivery_hours_json, v.metrics_note,
        v.jsonld_rating, v.jsonld_rating_count, v.price_range, v.cuisine_json, 'TL' AS currency, 'web_research' AS acquisition_class, 'internal' AS distribution_class, v.source_file_id, v.source_row_number, v.source_row_hash, 'stg_yemeksepeti_records' AS staging_view, ? AS created_at
        FROM ysv v LEFT JOIN vc2poi vp ON vp.venue_code = v.venue_code""", [now])
    c.execute("DROP TABLE IF EXISTS menu_item_observation")
    c.execute("""CREATE TABLE menu_item_observation AS SELECT 'OBS_MENU_' || md5(r.raw_venue_code || coalesce(r.raw_product_ref,'') || coalesce(r.raw_title,'') || r.raw_fetched_at || r.source_row_hash) AS observation_id, vp.poi_id, r.raw_venue_code AS venue_code, 'yemeksepeti' AS platform,
        r.raw_fetched_at AS observed_at, r.raw_product_ref AS product_ref, r.raw_title AS title, r.raw_description AS description, r.raw_category_title AS category_title, r.raw_category_ref AS category_ref,
        r.raw_original_price AS raw_original_price, TRY_CAST(r.raw_original_price AS DOUBLE) AS original_price, r.raw_discounted_price AS raw_discounted_price, TRY_CAST(r.raw_discounted_price AS DOUBLE) AS discounted_price,
        CASE WHEN lower(coalesce(r.raw_is_sold_out,'')) IN ('true','1') THEN TRUE WHEN lower(coalesce(r.raw_is_sold_out,'')) IN ('false','0') THEN FALSE END AS is_sold_out, 'TL' AS currency, r.raw_parse_error AS parse_error,
        'web_research' AS acquisition_class, 'internal' AS distribution_class, r.source_file_id, r.source_row_number, r.source_row_hash, 'stg_yemeksepeti_records' AS staging_view, ? AS created_at
        FROM s.stg.poi_business__stg_yemeksepeti_records r LEFT JOIN vc2poi vp ON vp.venue_code = r.raw_venue_code WHERE r.raw_record_kind='menu_item'""", [now])
    # analitik yenile (poi_stats aynı tanım + yemeksepeti sayacı)
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_poi_stats AS SELECT assigned_geo_id geo_id,
        count(*) AS poi_count, count(*) FILTER (WHERE assignment_method='ST_Contains(web_polygon)') AS poi_count_coord_verified,
        count(*) FILTER (WHERE coalesce(predicted_sector,'')='Yeme-İçme' OR raw_category IN ('Kafe','Restoran & Lokanta','Fast Food','3. Nesil Kahveci','Kebapçı & Ocakbaşı','Pastane & Fırın','Tatlı','Türk')) AS yeme_icme_count,
        count(*) FILTER (WHERE raw_category='Kuaför & Güzellik' OR predicted_category IN ('Kuaför & Berber','Güzellik & Kozmetik')) AS kuafor_guzellik_count,
        count(*) FILTER (WHERE raw_category='Bakkal & Market' OR predicted_category IN ('Süpermarket & Market','Ucuzluk & İndirim Market')) AS market_count,
        round(avg(rating) FILTER (WHERE rating IS NOT NULL), 2) AS poi_avg_rating,
        count(*) FILTER (WHERE source_coverage LIKE '%google%' AND source_coverage LIKE '%osm%') AS poi_google_osm_count, count(*) FILTER (WHERE source_coverage='osm_only') AS poi_osm_only_count,
        count(*) FILTER (WHERE source_coverage LIKE '%yemeksepeti%') AS poi_yemeksepeti_count
        FROM poi WHERE assigned_geo_id IS NOT NULL AND assignment_confidence >= 0.6 GROUP BY 1""")
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_4', ?), ('code_hash_v1_4', ?)", [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    rep["ys_venue"] = {"pages": n_v[0], "with_geo": n_v[1], "distinct_venues": n_v[2]}
    rep["match_bands"] = bands
    rep["poi_by_coverage"] = dict(c.execute("SELECT source_coverage, count(*) FROM poi GROUP BY 1 ORDER BY 2 DESC").fetchall())
    rep["observations"] = {"venue_delivery": c.execute("SELECT count(*), count(poi_id) FROM venue_delivery_observation").fetchone(), "menu_item": c.execute("SELECT count(*), count(poi_id), count(original_price) FROM menu_item_observation").fetchone()}
    rep["integrity"] = {"poi_id_unique": c.execute("SELECT count(*)=count(DISTINCT poi_id) FROM poi").fetchone()[0], "ys_code_unique_in_poi": c.execute("SELECT count(ys_venue_code)=count(DISTINCT ys_venue_code) FROM poi").fetchone()[0],
                        "ys_accounting(venues = merged + ys_only)": c.execute("SELECT (SELECT count(*) FROM ysu) = (SELECT count(*) FROM poi WHERE ys_venue_code IS NOT NULL)").fetchone()[0],
                        "menu_items_all_linked": c.execute("SELECT count(*) - count(poi_id) FROM menu_item_observation").fetchone()[0], "orphan_link": c.execute("SELECT count(*) FROM poi_source_link l WHERE NOT EXISTS (SELECT 1 FROM poi p WHERE p.poi_id=l.poi_id)").fetchone()[0]}
    rep["sample_merged"] = c.execute("SELECT name, ys_name, source_coverage, round(ys_match_score,3), rating FROM poi WHERE source_coverage LIKE '%yemeksepeti%' AND source_coverage <> 'yemeksepeti_only' LIMIT 6").fetchall()
    rep["sample_menu"] = c.execute("SELECT p.name, m.title, m.original_price, m.category_title FROM menu_item_observation m JOIN poi p USING (poi_id) WHERE m.original_price IS NOT NULL LIMIT 4").fetchall()
    rep["seconds"] = round(time.time() - t0)
    for t in ("venue_delivery_observation", "menu_item_observation"): c.execute(f"COPY (SELECT * FROM {t}) TO '{OUT}/canonical/v1.1/parquet/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * EXCLUDE (geometry) FROM poi) TO '{OUT}/canonical/v1.1/parquet/poi.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM poi_source_link) TO '{OUT}/canonical/v1.1/parquet/poi_source_link.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.close()
    (OUT / "canonical" / "v1.1" / "build_report_v1_4.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_4_YEMEKSEPETI", "bands": bands, "coverage": rep["poi_by_coverage"], "observations": rep["observations"], "integrity": rep["integrity"]}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
