#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.2: POI kaynak birleştirme (Google ↔ OSM). Kullanıcı kararı (2026-09-19): "tamamen aynı yerleri birleştir, yalnız bir kaynakta olanları ayrı belirt".
Disk kısıtı nedeniyle v1.1 dosyası yerinde yükseltilir (v1 taban dosyası dokunulmaz; v1.1 betiği deterministik → yeniden üretilebilir).

Kurallar:
  - Birleştirme yalnız poi_matcher v1 high+ bantları (skor ≥ 0.95). Google kaydı birincil (poi_id korunur); OSM alanları yanına eklenir. Bağ poi_source_link'te (geri alınabilir).
  - review_recommended (0.85–0.95): birleştirilmez; iki kayıt ayrı, bağ link_status='review'.
  - Google'da karşılığı olmayan OSM işletmeleri poi'ye yeni kayıt: poi_id 'POI_OSM_<md5(osm_id)>', source_coverage='osm_only', kategori OSM etiketinden (OSM_TAG_MAP), mahalle ataması ST_Contains.
  - source_coverage ∈ {google_only, osm_only, google+osm}; hiçbir satır silinmez.
"""
import time, datetime as dt, json, hashlib
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
MAP = OUT / "mappings" / "poi_match_google_osm_v1_PROPOSED.parquet"
MODEL_VERSION = "canonical_v1.2"; MERGE_BANDS = ("virtually_certain", "very_high", "high")
OSM_CAT = {"restaurant": ("Restoran & Lokanta", "Yeme-İçme"), "cafe": ("Kafe", "Yeme-İçme"), "fast_food": ("Fast Food", "Yeme-İçme"), "bar": ("Bar & Pub", "Yeme-İçme"), "pub": ("Bar & Pub", "Yeme-İçme"),
           "food_court": ("Restoran & Lokanta", "Yeme-İçme"), "ice_cream": ("Dondurma", "Yeme-İçme"), "biergarten": ("Bar & Pub", "Yeme-İçme"),
           "bakery": ("Pastane & Fırın", "Gıda Perakende"), "pastry": ("Pastane & Fırın", "Gıda Perakende"), "confectionery": ("Tatlı", "Gıda Perakende"), "coffee": ("3. Nesil Kahveci", "Yeme-İçme"),
           "tea": ("Kafe", "Yeme-İçme"), "deli": ("Şarküteri", "Gıda Perakende"), "butcher": ("Kasap", "Gıda Perakende"), "seafood": ("Balık Restoranı", "Yeme-İçme"), "greengrocer": ("Manav", "Gıda Perakende"),
           "convenience": ("Bakkal & Market", "Gıda Perakende"), "supermarket": ("Süpermarket & Market", "Gıda Perakende"), "kiosk": ("Büfe", "Gıda Perakende")}


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/canon12'")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    mv = c.execute("SELECT value FROM _meta WHERE key='model_version'").fetchone()[0]
    if mv != "canonical_v1.1": raise SystemExit(f"beklenen taban canonical_v1.1, bulunan {mv} (yeniden çalıştırma engellendi — idempotentlik)")
    rep = {"built_at": now, "base": mv, "policy": "merge high+ (>=0.95) per user decision 2026-09-19; review kept separate"}
    # ---- 1. şema ----
    for col, typ in (("source_coverage", "VARCHAR"), ("primary_source", "VARCHAR"), ("osm_id", "VARCHAR"), ("osm_name", "VARCHAR"), ("osm_amenity", "VARCHAR"), ("osm_shop", "VARCHAR"), ("osm_cuisine", "VARCHAR"),
                     ("osm_phone", "VARCHAR"), ("osm_website", "VARCHAR"), ("osm_opening_hours", "VARCHAR"), ("osm_lon", "DOUBLE"), ("osm_lat", "DOUBLE"), ("osm_source_row_hash", "VARCHAR"), ("osm_match_score", "DOUBLE"), ("osm_match_band", "VARCHAR")):
        c.execute(f"ALTER TABLE poi ADD COLUMN IF NOT EXISTS {col} {typ}")
    c.execute("UPDATE poi SET source_coverage='google_only', primary_source='google' WHERE source_coverage IS NULL")
    c.execute("""CREATE TABLE IF NOT EXISTS poi_source_link (link_id VARCHAR, poi_id VARCHAR, source_system VARCHAR, source_record_id VARCHAR, source_file_id VARCHAR, source_row_hash VARCHAR,
                 match_score DOUBLE, match_band VARCHAR, matcher_version VARCHAR, link_status VARCHAR, reasons VARCHAR, created_at VARCHAR)""")
    c.execute("""INSERT INTO poi_source_link SELECT 'LNK_' || md5('google|' || poi_id), poi_id, 'google', source_place_id, source_file_id, source_row_hash, NULL, NULL, NULL, 'primary', 'kaynak kaydın kendisi', ? FROM poi WHERE primary_source='google' AND NOT EXISTS (SELECT 1 FROM poi_source_link l WHERE l.poi_id=poi.poi_id AND l.source_system='google')""", [now])
    # ---- 2. OSM kaynak tablosu ----
    cols = [r[0] for r in c.execute("DESCRIBE s.stg.poi_business__stg_osm_food_poi").fetchall()]
    oh = '"raw_opening_hours"' if "raw_opening_hours" in cols else "NULL"
    c.execute(f"""CREATE TEMP TABLE osm AS SELECT raw__feature_id AS osm_id, raw_name AS name, raw_amenity AS amenity, raw_shop AS shop, raw_cuisine AS cuisine, coalesce(raw_phone, raw_contact_phone) AS phone, raw_website AS website, {oh} AS opening_hours,
        ST_X(ST_Centroid(geometry_original)) AS lon, ST_Y(ST_Centroid(geometry_original)) AS lat, source_file_id, source_row_number, source_row_hash, review_flags
        FROM s.stg.poi_business__stg_osm_food_poi WHERE geometry_original IS NOT NULL""")
    # ---- 3. birleştirme (high+) ----
    c.execute(f"CREATE TEMP TABLE m AS SELECT * FROM '{MAP}'")
    c.execute(f"""UPDATE poi SET source_coverage='google+osm', osm_id=m.osm_id, osm_name=o.name, osm_amenity=o.amenity, osm_shop=o.shop, osm_cuisine=o.cuisine, osm_phone=o.phone, osm_website=o.website, osm_opening_hours=o.opening_hours,
                  osm_lon=o.lon, osm_lat=o.lat, osm_source_row_hash=o.source_row_hash, osm_match_score=m.score, osm_match_band=m.band
                  FROM m JOIN osm o ON o.osm_id=m.osm_id WHERE poi.poi_id=m.poi_id AND m.band IN {MERGE_BANDS}""")
    n_merged = c.execute("SELECT count(*) FROM poi WHERE source_coverage='google+osm'").fetchone()[0]
    c.execute(f"""INSERT INTO poi_source_link SELECT 'LNK_' || md5('osm|' || m.osm_id || '|' || m.poi_id), m.poi_id, 'osm', m.osm_id, o.source_file_id, o.source_row_hash, m.score, m.band, m.matcher_version,
                  CASE WHEN m.band IN {MERGE_BANDS} THEN 'merged' ELSE 'review' END, m.reasons, ? FROM m JOIN osm o ON o.osm_id=m.osm_id WHERE m.band <> 'no_auto_merge'""", [now])
    # ---- 4. osm_only yeni kayıtlar (birleşmeyen tüm OSM: review bandı dahil, adsızlar dahil) ----
    c.execute(f"CREATE TEMP TABLE merged_osm AS SELECT osm_id FROM m WHERE band IN {MERGE_BANDS}")
    cat_rows = [(k, v[0], v[1]) for k, v in OSM_CAT.items()]
    c.execute("CREATE TEMP TABLE catmap (tag VARCHAR, cat VARCHAR, sector VARCHAR)"); c.executemany("INSERT INTO catmap VALUES (?,?,?)", cat_rows)
    c.execute("""CREATE TEMP TABLE newp AS SELECT o.*, coalesce(ca.cat, cs.cat) cat, coalesce(ca.sector, cs.sector) sector,
                 (o.lon BETWEEN 25 AND 45.5 AND o.lat BETWEEN 35.5 AND 42.5) in_tr
                 FROM osm o LEFT JOIN catmap ca ON ca.tag=o.amenity LEFT JOIN catmap cs ON cs.tag=o.shop WHERE o.osm_id NOT IN (SELECT osm_id FROM merged_osm)""")
    # mekânsal atama: bbox ön-filtre + ST_Contains (v1 ile aynı yöntem)
    c.execute("""CREATE TEMP TABLE newp_geo AS SELECT n.osm_id, g.geo_id, g.parent_geo_id, row_number() OVER (PARTITION BY n.osm_id ORDER BY g.geo_id) rn FROM newp n
                 JOIN geo_entity g ON g.level='mahalle' AND g.geometry IS NOT NULL AND n.lon BETWEEN g.bbox_xmin AND g.bbox_xmax AND n.lat BETWEEN g.bbox_ymin AND g.bbox_ymax
                 AND ST_Contains(g.geometry, ST_Point(n.lon, n.lat)) WHERE n.in_tr""")
    c.execute("""INSERT INTO poi BY NAME SELECT 'POI_OSM_' || md5(n.osm_id) AS poi_id, n.osm_id AS source_place_id, n.name AS name, 'osm:' || coalesce(n.amenity, 'shop=' || n.shop) AS raw_category,
        n.cat AS predicted_category, n.sector AS predicted_sector, CASE WHEN n.cat IS NOT NULL THEN 'OSM_TAG_MAP' END AS category_method, CASE WHEN n.cat IS NOT NULL THEN 0.90 END AS category_confidence, CASE WHEN n.cat IS NOT NULL THEN 'high' END AS category_band,
        n.phone AS phone, n.lon AS lon, n.lat AS lat, ST_Point(n.lon, n.lat) AS geometry, 'none' AS source_il_reliability,
        ng.geo_id AS assigned_geo_id, CASE WHEN ng.geo_id IS NOT NULL THEN 'ST_Contains(web_polygon)' END AS assignment_method, CASE WHEN ng.geo_id IS NOT NULL THEN 0.95 END AS assignment_confidence,
        ng.geo_id AS spatial_geo_id, ng.parent_geo_id AS assigned_ilce_geo_id, CASE WHEN ng.geo_id IS NOT NULL THEN 0.99 END AS ilce_assignment_confidence,
        CASE WHEN NOT n.in_tr THEN 'OUT_OF_TURKEY' ELSE 'valid' END AS coord_validity,
        concat_ws(',', CASE WHEN n.name IS NULL THEN 'NAME_MISSING' END, CASE WHEN n.in_tr AND ng.geo_id IS NULL THEN 'VALID_COORD_OUTSIDE_ALL_POLYGONS' END, 'OSM_ODBL_ATTRIBUTION_REQUIRED') AS assignment_flags,
        1 AS observation_count, n.source_file_id, n.source_row_number, n.source_row_hash, 'stg_osm_food_poi' AS staging_view, 'official_public' AS acquisition_class, 'public' AS distribution_class, ? AS created_at,
        'osm_only' AS source_coverage, 'osm' AS primary_source, n.osm_id AS osm_id, n.name AS osm_name, n.amenity AS osm_amenity, n.shop AS osm_shop, n.cuisine AS osm_cuisine, n.phone AS osm_phone, n.website AS osm_website, n.opening_hours AS osm_opening_hours,
        n.lon AS osm_lon, n.lat AS osm_lat, n.source_row_hash AS osm_source_row_hash
        FROM newp n LEFT JOIN newp_geo ng ON ng.osm_id=n.osm_id AND ng.rn=1""", [now])
    c.execute("UPDATE poi SET assignment_flags = NULL WHERE assignment_flags = ''")
    c.execute("""INSERT INTO poi_source_link SELECT 'LNK_' || md5('osm|' || osm_id), poi_id, 'osm', osm_id, source_file_id, source_row_hash, NULL, NULL, NULL, 'primary', 'kaynak kaydın kendisi (osm_only)', ? FROM poi WHERE primary_source='osm'""", [now])
    # ---- 5. analitik yenileme (v1.1 ile aynı tanımlar) ----
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_poi_stats AS SELECT assigned_geo_id geo_id,
        count(*) AS poi_count, count(*) FILTER (WHERE assignment_method='ST_Contains(web_polygon)') AS poi_count_coord_verified,
        count(*) FILTER (WHERE coalesce(predicted_sector,'')='Yeme-İçme' OR raw_category IN ('Kafe','Restoran & Lokanta','Fast Food','3. Nesil Kahveci','Kebapçı & Ocakbaşı','Pastane & Fırın','Tatlı','Türk')) AS yeme_icme_count,
        count(*) FILTER (WHERE raw_category='Kuaför & Güzellik' OR predicted_category IN ('Kuaför & Berber','Güzellik & Kozmetik')) AS kuafor_guzellik_count,
        count(*) FILTER (WHERE raw_category='Bakkal & Market' OR predicted_category IN ('Süpermarket & Market','Ucuzluk & İndirim Market')) AS market_count,
        round(avg(rating) FILTER (WHERE rating IS NOT NULL), 2) AS poi_avg_rating,
        count(*) FILTER (WHERE source_coverage='google+osm') AS poi_google_osm_count, count(*) FILTER (WHERE source_coverage='osm_only') AS poi_osm_only_count
        FROM poi WHERE assigned_geo_id IS NOT NULL AND assignment_confidence >= 0.6 GROUP BY 1""")
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_2', ?), ('code_hash_v1_2', ?), ('poi_merge_policy', 'high+ (>=0.95) merged per user decision; review separate')", [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    # ---- rapor / bütünlük ----
    rep["poi_by_coverage"] = dict(c.execute("SELECT source_coverage, count(*) FROM poi GROUP BY 1").fetchall())
    rep["poi_source_link_by_status"] = dict(c.execute("SELECT source_system || ':' || link_status, count(*) FROM poi_source_link GROUP BY 1").fetchall())
    rep["osm_only"] = {"named": c.execute("SELECT count(*) FROM poi WHERE source_coverage='osm_only' AND name IS NOT NULL").fetchone()[0],
                       "unnamed": c.execute("SELECT count(*) FROM poi WHERE source_coverage='osm_only' AND name IS NULL").fetchone()[0],
                       "assigned_mahalle": c.execute("SELECT count(*) FROM poi WHERE source_coverage='osm_only' AND assigned_geo_id IS NOT NULL").fetchone()[0],
                       "category_mapped": c.execute("SELECT count(*) FROM poi WHERE source_coverage='osm_only' AND predicted_category IS NOT NULL").fetchone()[0]}
    rep["integrity"] = {"poi_id_unique": c.execute("SELECT count(*)=count(DISTINCT poi_id) FROM poi").fetchone()[0],
                        "osm_id_unique_in_poi": c.execute("SELECT count(osm_id)=count(DISTINCT osm_id) FROM poi WHERE osm_id IS NOT NULL").fetchone()[0],
                        "orphan_link": c.execute("SELECT count(*) FROM poi_source_link l WHERE NOT EXISTS (SELECT 1 FROM poi p WHERE p.poi_id=l.poi_id)").fetchone()[0],
                        "orphan_assignment": c.execute("SELECT count(*) FROM poi p WHERE assigned_geo_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id=p.assigned_geo_id)").fetchone()[0],
                        "osm_accounting(osm_in = merged + osm_only)": c.execute("SELECT (SELECT count(*) FROM osm) = (SELECT count(*) FROM poi WHERE source_coverage='google+osm') + (SELECT count(*) FROM poi WHERE source_coverage='osm_only')").fetchone()[0]}
    rep["tables"] = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("poi", "poi_source_link", "poi_snapshot")}
    rep["sample_merged"] = c.execute("SELECT name, osm_name, raw_category, osm_amenity, osm_cuisine, osm_website, round(osm_match_score,3) FROM poi WHERE source_coverage='google+osm' AND osm_website IS NOT NULL LIMIT 5").fetchall()
    rep["seconds"] = round(time.time() - t0)
    c.execute(f"COPY (SELECT * EXCLUDE (geometry) FROM poi) TO '{OUT}/canonical/v1.1/parquet/poi.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM poi_source_link) TO '{OUT}/canonical/v1.1/parquet/poi_source_link.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM analytics.mahalle_intelligence) TO '{OUT}/canonical/v1.1/parquet/mahalle_intelligence.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.close()
    (OUT / "canonical" / "v1.1" / "build_report_v1_2.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_2_POI_MERGE", "user_decision": "tamamen aynı yerleri birleştir; tek kaynaklı olanları ayrı belirt (2026-09-19)", "merge_bands": MERGE_BANDS, "result": rep["poi_by_coverage"], "integrity": rep["integrity"]}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
