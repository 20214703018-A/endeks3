#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.5 (yerinde yükseltme; taban canonical_v1.4)
A) indicator_observation: resmî bağlam serileri (hepsi web ile doğrulandı: validation/quarantine_evidence/README.md)
   - TÜİK MEDAS ilçe konut satış (aylık 2013→2026, güncel revize seri) · TÜİK bülten xls ilçe konut satış (yıllık 2015→2025, revizyon ÖNCESİ seri; ayrı series_version)
   - TÜİK yapı izin (il çeyrek/yıl; ilçe yıl; ruhsat/kullanma daire) · yapı ruhsatı kullanım amacı m² (ilçe yıl) · iller arası göç (il, yıl) · SES 2023 (il/ilçe) · hanehalkı 2021 (il)
   - BKM sektörel kart harcaması (Türkiye geneli, ay/yıl) · BDDK FinTürk (il, dönem: kredi/mevduat/bireysel/sektörel/şube)
   Uzun biçim: (geo_id, level, domain, metric, dim1, dim2, period, period_kind, raw_value, parsed_value, unit, series_version, provenance). Kaynaklar arası değer birleştirilmez.
B) poi: source_id_kind · POI kategori v3 (yalnız high bant uygulanır; review → poi_category_candidate) · sector_hint (ölçülmüş doğrulukla) · bozuk koordinat kurtarma (aynı place_id'nin geçerli gözlemi) · yeniden mekânsal atama
C) analytics: il/ilçe düzeyi POI sayımı (ilçe ataması ≥0.9, il çelişkisi hariç) · ilce_intelligence (konut satış son 12 ay, yapı ruhsatı, nüfus, POI) · mahalle_intelligence korunur
Kural: hiçbir satır silinmez; geo_entity'ye ulusal varlık GEO_TR eklenir (BKM için).
"""
import time, datetime as dt, json, hashlib
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
MODEL_VERSION = "canonical_v1.5"; CUTOFF = "2026-08"

# (staging view, level, geo join kind, domain, period expr, period_kind, series_version, dims, [(metric, col, unit)], extra_where)
SRC = [
 ("demographics_context__stg_tuik_bolge__konut_satis_ilce", "ilce", "tuik_kodu", "housing_sales", "raw_yil || '-' || lpad(raw_ay, 2, '0')", "month", "tuik_medas_2025_revision", {}, [("sales_count", "raw_satis", "count")], "raw_satis IS NOT NULL"),
 ("demographics_context__stg_tuik_bolge__yapi_izin_il", "il", "il_name", "construction_permit", "CASE WHEN raw_ceyrek='yil' THEN raw_yil ELSE raw_yil || '-Q' || CASE raw_ceyrek WHEN 'I' THEN '1' WHEN 'II' THEN '2' WHEN 'III' THEN '3' ELSE '4' END END", "CASE WHEN raw_ceyrek='yil' THEN 'year' ELSE 'quarter' END", "tuik_medas", {"dim1": "raw_belge"}, [("dwelling_units", "raw_daire", "count")], "raw_daire IS NOT NULL"),
 ("demographics_context__stg_tuik_bolge__yapi_izin_ilce", "ilce", "tuik_kodu", "construction_permit", "raw_yil", "year", "tuik_medas", {"dim1": "raw_belge"}, [("dwelling_units", "raw_daire", "count")], "raw_daire IS NOT NULL"),
 ("demographics_context__stg_tuik_bolge__yapi_ruhsat_amac_ilce", "ilce", "tuik_kodu", "construction_permit", "raw_yil", "year", "tuik_medas", {"dim1": "'ruhsat'", "dim2": "raw_amac_kodu || ' ' || raw_amac"}, [("permit_floor_area_m2", "raw_yuzolcumu_m2", "m2")], "raw_yuzolcumu_m2 IS NOT NULL"),
 ("demographics_context__stg_tuik_bolge__goc_il", "il", "il_name", "migration", "split_part(raw_donem, '-', 2)", "year", "tuik_adnks", {"dim1": "raw_donem"}, [("in_migration", "raw_aldigi", "person"), ("out_migration", "raw_verdigi", "person"), ("net_migration", "raw_net", "person"), ("net_migration_rate_per_1000", "raw_net_hiz_binde", "per_1000"), ("population_reference", "raw_nufus", "person")], "TRUE"),
 ("demographics_context__stg_tuik_bolge__ses_ilce", "il", "il_name", "socio_economic", "raw_yil", "year", "tuik_ses_2023", {}, [("ses_score", "raw_ses_skor", "index"), ("share_upper", "raw_ust", "pct"), ("share_upper_middle", "raw_ust_alti", "pct"), ("share_middle", "raw_orta", "pct"), ("share_lower", "raw_alt", "pct"), ("share_lowest", "raw_en_alt", "pct")], "raw_ilce = '(il toplamı)'"),
 ("demographics_context__stg_tuik_bolge__ses_ilce", "ilce", "il_ilce_name", "socio_economic", "raw_yil", "year", "tuik_ses_2023", {}, [("ses_score", "raw_ses_skor", "index"), ("share_upper", "raw_ust", "pct"), ("share_upper_middle", "raw_ust_alti", "pct"), ("share_middle", "raw_orta", "pct"), ("share_lower", "raw_alt", "pct"), ("share_lowest", "raw_en_alt", "pct")], "raw_ilce <> '(il toplamı)'"),
 ("demographics_context__stg_tuik_bolge__hanehalki_il", "il", "il_name", "household", "'2021'", "year", "tuik_nks_2021", {}, [("households", "raw_hanehalki", "count"), ("avg_household_size", "raw_ortalama_buyukluk", "person")], "TRUE"),
 ("economy__stg_bkm_sektorel_kart_harcama__bkm_aylik_sektorel_harcama", "ulke", "national", "card_spending", "raw_donem", "month", "bkm", {"dim1": "raw_sektor_adi"}, [("credit_card_txn_count", "raw_kredi_karti_islem_adedi", "count"), ("debit_card_txn_count", "raw_banka_karti_islem_adedi", "count"), ("total_txn_count", "raw_toplam_islem_adedi", "count"), ("credit_card_amount", "raw_kredi_karti_tutar_milyon_tl", "million_TL"), ("debit_card_amount", "raw_banka_karti_tutar_milyon_tl", "million_TL"), ("total_amount", "raw_toplam_tutar_milyon_tl", "million_TL")], "TRUE"),
 ("economy__stg_bkm_sektorel_kart_harcama__bkm_yillik_sektor_ozet", "ulke", "national", "card_spending", "raw_yil", "year", "bkm", {"dim1": "raw_sektor_adi"}, [("total_txn_count", "raw_yillik_toplam_islem_adedi", "count"), ("total_amount", "raw_yillik_toplam_tutar_milyon_tl", "million_TL"), ("sector_share_pct", "raw_sektor_payi_yuzde", "pct")], "TRUE"),
 ("economy__stg_bddk_finturk_finansal_gostergeler__bddk_il_kredi_ve_mevduat", "il", "il_name", "banking", "raw_donem", "month", "bddk_finturk", {}, [("total_cash_loans", "raw_toplam_nakdi_kredi_bin_tl", "thousand_TL"), ("cash_loans", "raw_nakdi_kredi_bin_tl", "thousand_TL"), ("npl", "raw_takipteki_alacaklar_bin_tl", "thousand_TL"), ("non_cash_loans", "raw_gayrinakdi_krediler_bin_tl", "thousand_TL"), ("savings_deposit_tl", "raw_tasarruf_mevduati_tl_bin_tl", "thousand_TL"), ("savings_deposit_fx", "raw_tasarruf_mevduati_dth_bin_tl", "thousand_TL"), ("savings_deposit_total", "raw_toplam_tasarruf_mevduati_bin_tl", "thousand_TL")], "TRUE"),
 ("economy__stg_bddk_finturk_finansal_gostergeler__bddk_il_bireysel_finans", "il", "il_name", "banking", "raw_donem", "month", "bddk_finturk", {}, [("consumer_credit_card", "raw_bireysel_kredi_karti_bin_tl", "thousand_TL"), ("housing_loans", "raw_konut_kredisi_bin_tl", "thousand_TL"), ("vehicle_loans", "raw_tasit_kredisi_bin_tl", "thousand_TL"), ("overdraft", "raw_kredili_mevduat_hesabi_bin_tl", "thousand_TL"), ("other_consumer_loans", "raw_diger_tuketici_kredileri_bin_tl", "thousand_TL"), ("npl_credit_card", "raw_takipteki_kredi_karti_bin_tl", "thousand_TL"), ("npl_housing_loans", "raw_takipteki_konut_kredisi_bin_tl", "thousand_TL")], "TRUE"),
 ("economy__stg_bddk_finturk_finansal_gostergeler__bddk_il_sektorel_krediler", "il", "il_name", "banking", "raw_donem", "month", "bddk_finturk", {}, [("loans_tourism", "raw_turizm_kredisi_bin_tl", "thousand_TL"), ("loans_wholesale", "raw_toptan_ticaret_kredisi_bin_tl", "thousand_TL"), ("loans_construction", "raw_insaat_kredisi_bin_tl", "thousand_TL"), ("loans_food_beverage", "raw_gida_mesrubat_kredisi_bin_tl", "thousand_TL"), ("loans_textile", "raw_tekstil_kredisi_bin_tl", "thousand_TL"), ("loans_agriculture", "raw_ziraat_kredisi_bin_tl", "thousand_TL"), ("loans_energy", "raw_enerji_kredisi_bin_tl", "thousand_TL")], "TRUE"),
 ("economy__stg_bddk_finturk_finansal_gostergeler__bddk_il_sube_ve_likidite", "il", "il_name", "banking", "raw_donem", "month", "bddk_finturk", {}, [("branch_count", "raw_sube_sayisi", "count"), ("population_per_branch", "raw_subeye_dusen_nufus", "person"), ("cash_loans_per_capita", "raw_kisi_basi_nakdi_kredi_tl", "TL"), ("npl_per_capita", "raw_kisi_basi_takipteki_alacak_tl", "TL"), ("savings_per_capita", "raw_kisi_basi_tasarruf_mevduati_tl", "TL"), ("deposits_per_capita", "raw_kisi_basi_toplam_mevduat_tl", "TL")], "TRUE"),
]


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/canon15'")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    mv = c.execute("SELECT value FROM _meta WHERE key='model_version'").fetchone()[0]
    if mv != "canonical_v1.4": raise SystemExit(f"beklenen taban canonical_v1.4, bulunan {mv}")
    rep = {"built_at": now, "base": mv, "indicator_sources": {}}
    c.execute("CREATE OR REPLACE MACRO norm(x) AS regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9]+', '', 'g')")
    # ulusal varlık
    if not c.execute("SELECT 1 FROM geo_entity WHERE geo_id='GEO_TR'").fetchone():
        c.execute('''INSERT INTO geo_entity BY NAME SELECT 'GEO_TR' AS geo_id, 'ulke' AS "level", 'Türkiye' AS "name", 'turkiye' AS name_norm, 'tuik' AS geometry_source, 'national' AS anchor_source, 'APPROVED (tanım)' AS mapping_status, ? AS model_version, ? AS created_at''', [MODEL_VERSION, now])
    # ---------------- A) indicator_observation ----------------
    c.execute("""CREATE TABLE IF NOT EXISTS indicator_observation (observation_id VARCHAR, geo_id VARCHAR, level VARCHAR, domain VARCHAR, metric VARCHAR, dim1 VARCHAR, dim2 VARCHAR, period VARCHAR, period_kind VARCHAR,
        raw_value VARCHAR, parsed_value DOUBLE, unit VARCHAR, series_version VARCHAR, acquisition_class VARCHAR, distribution_class VARCHAR, source_priority INTEGER,
        source_file_id VARCHAR, source_row_number BIGINT, source_row_hash VARCHAR, staging_view VARCHAR, recorded_at VARCHAR)""")
    c.execute("DELETE FROM indicator_observation")  # yeniden kurulumda idempotent (tek sürümde üretilir)
    c.execute("CREATE TEMP TABLE lk_il AS SELECT name_norm k, geo_id FROM geo_entity WHERE level='il'")
    c.execute("CREATE TEMP TABLE lk_ilce_kod AS SELECT tuik_kodu k, geo_id FROM geo_entity WHERE level='ilce' AND tuik_kodu IS NOT NULL")
    c.execute("CREATE TEMP TABLE lk_ilce_ad AS SELECT l.name_norm il_n, e.name_norm ilce_n, e.geo_id FROM geo_entity e JOIN geo_entity l ON l.geo_id=e.il_geo_id WHERE e.level='ilce'")
    for view, level, gj, domain, period, pkind, sver, dims, metrics, where in SRC:
        v = f"s.stg.{view}"; short = view.split("__", 1)[1]
        geo = {"tuik_kodu": "(SELECT geo_id FROM lk_ilce_kod WHERE k = CAST(x.raw_tuik_kodu AS VARCHAR))", "il_name": "(SELECT geo_id FROM lk_il WHERE k = norm(x.raw_il))",
               "il_ilce_name": "(SELECT geo_id FROM lk_ilce_ad WHERE il_n = norm(x.raw_il) AND ilce_n = norm(x.raw_ilce))", "national": "'GEO_TR'"}[gj]
        d1 = dims.get("dim1", "CAST(NULL AS VARCHAR)"); d2 = dims.get("dim2", "CAST(NULL AS VARCHAR)"); pkind_sql = pkind if pkind.upper().startswith("CASE") else f"'{pkind}'"
        c.execute(f"CREATE OR REPLACE TEMP TABLE src AS SELECT x.*, {geo} AS geo_id_, {period} AS period_, {pkind_sql} AS pkind_, {d1} AS dim1_, {d2} AS dim2_ FROM {v} x WHERE {where}")
        n_in = c.execute("SELECT count(*) FROM src").fetchone()[0]; n_unl = c.execute("SELECT count(*) FROM src WHERE geo_id_ IS NULL").fetchone()[0]
        written = 0
        for metric, col, unit in metrics:
            c.execute(f"""INSERT INTO indicator_observation SELECT 'OBS_IND_' || md5('{short}|{metric}|' || source_row_hash || '|' || coalesce(dim1_,'') || coalesce(dim2_,'')), geo_id_, '{level}', '{domain}', '{metric}', dim1_, dim2_, period_, pkind_,
                CAST({col} AS VARCHAR), TRY_CAST({col} AS DOUBLE), '{unit}', '{sver}', 'official_public', 'public', 100, source_file_id, source_row_number, source_row_hash, '{short}', ?
                FROM src WHERE geo_id_ IS NOT NULL AND {col} IS NOT NULL AND CAST({col} AS VARCHAR) <> ''""", [now])
            written += c.execute(f"SELECT count(*) FROM indicator_observation WHERE staging_view='{short}' AND metric='{metric}' AND level='{level}'").fetchone()[0]
        rep["indicator_sources"][f"{short}[{level}]"] = {"rows_in": n_in, "rows_unlinked": n_unl, "observations": written}
        print(f"  {short}[{level}]: {n_in:,} satır → {written:,} gözlem (bağlanamayan {n_unl})", flush=True)
    # revizyon ÖNCESİ TÜİK xls (yıl ileri doldurma; il+ilçe ad eşleme)
    c.execute("""CREATE OR REPLACE TEMP TABLE xl AS SELECT *, max(TRY_CAST(TRY_CAST(raw_Y_l_Year AS DOUBLE) AS INTEGER)) OVER (ORDER BY source_row_number ROWS UNBOUNDED PRECEDING) AS yil,
                 (SELECT geo_id FROM lk_ilce_ad WHERE il_n = norm(x.raw__l_Province) AND ilce_n = norm(x.raw__l_e_Districts)) AS geo_id_
                 FROM s.stg.demographics_context__stg_tuik_konut_satis_ilce_resmi x WHERE raw__l_e_Districts IS NOT NULL AND raw_Toplam_Total IS NOT NULL""")
    n_in = c.execute("SELECT count(*) FROM xl").fetchone()[0]; n_unl = c.execute("SELECT count(*) FROM xl WHERE geo_id_ IS NULL").fetchone()[0]
    for metric, col in (("sales_count", "raw_Toplam_Total"), ("sales_mortgaged", "raw__potekli_sat___2__Mortgaged_sale_2_"), ("sales_other", "raw_Di_er_sat___3__Other_sale_3_"), ("sales_first_hand", "raw__lk_el_sat___4__First_hand_sale_4_"), ("sales_second_hand", "raw__kinci_el_sat___5__Second_hand_sale_5_")):
        c.execute(f"""INSERT INTO indicator_observation SELECT 'OBS_IND_' || md5('tuik_xls_prerev|{metric}|' || source_row_hash), geo_id_, 'ilce', 'housing_sales', '{metric}', NULL, NULL, CAST(yil AS VARCHAR), 'year',
            CAST({col} AS VARCHAR), TRY_CAST({col} AS DOUBLE), 'count', 'tuik_bulletin_54146_pre_2025_revision', 'official_public', 'public', 100, source_file_id, source_row_number, source_row_hash, 'stg_tuik_konut_satis_ilce_resmi', ?
            FROM xl WHERE geo_id_ IS NOT NULL AND {col} IS NOT NULL""", [now])
    rep["indicator_sources"]["tuik_konut_satis_ilce_resmi[ilce, pre-revision]"] = {"rows_in": n_in, "rows_unlinked": n_unl, "unlinked_examples": c.execute("SELECT list(DISTINCT raw__l_Province || '/' || raw__l_e_Districts)[:8] FROM xl WHERE geo_id_ IS NULL").fetchone()[0]}
    print("  xls pre-revision:", n_in, "satır, bağlanamayan", n_unl, flush=True)
    # ---------------- B) poi ----------------
    print("poi güncellemeleri…", flush=True)
    for col, typ in (("source_id_kind", "VARCHAR"), ("sector_hint", "VARCHAR"), ("sector_hint_precision", "DOUBLE"), ("sector_hint_source", "VARCHAR"), ("coord_recovery", "VARCHAR")):
        c.execute(f"ALTER TABLE poi ADD COLUMN IF NOT EXISTS {col} {typ}")
    c.execute("""UPDATE poi SET source_id_kind = CASE WHEN primary_source='google' AND source_place_id LIKE 'ChIJ%' THEN 'google_place_id' WHEN primary_source='google' AND regexp_matches(source_place_id, '^[0-9a-f]{24}$') THEN 'collector_hash(name+lat+lon)'
        WHEN primary_source='osm' THEN 'osm_ref' WHEN primary_source='yemeksepeti' THEN 'yemeksepeti_venue_code' ELSE 'other' END""")
    # v3 kategori: high uygulanır; review adaylara
    c.execute(f"CREATE OR REPLACE TEMP TABLE v3 AS SELECT * FROM '{OUT}/mappings/poi_category_predictions_v3.parquet'")
    c.execute("""CREATE TABLE IF NOT EXISTS poi_category_candidate (poi_id VARCHAR, category VARCHAR, sector VARCHAR, method VARCHAR, confidence DOUBLE, band VARCHAR, evidence VARCHAR, flags VARCHAR, version VARCHAR, created_at VARCHAR, status VARCHAR)""")
    c.execute("DELETE FROM poi_category_candidate WHERE version='POI_CATEGORY_V3'")
    c.execute("INSERT INTO poi_category_candidate SELECT poi_id, predicted_category, predicted_sector, method, confidence, band, evidence, flags, version, created_at, CASE WHEN band='high' THEN 'APPLIED' ELSE 'PROPOSED (insan onayı bekliyor)' END FROM v3")
    c.execute("""UPDATE poi SET predicted_category=v.predicted_category, predicted_sector=v.predicted_sector, category_method=v.method, category_confidence=v.confidence, category_band='high'
                 FROM v3 v WHERE poi.poi_id=v.poi_id AND v.band='high' AND poi.predicted_category IS NULL""")
    n_cat = c.execute("SELECT count(*) FROM poi WHERE category_method IN ('OSM_SPATIAL','CHAIN_SPATIAL','DISTRICT_NAME','NAME_MODEL_V3','PLACE_ID(restoran_ve_kafe)')").fetchone()[0]
    c.execute(f"UPDATE poi SET sector_hint=h.sector_hint, sector_hint_precision=h.hint_precision, sector_hint_source='search_term:' || h.search_term FROM '{OUT}/mappings/poi_sector_hint_v3.parquet' h WHERE poi.poi_id=h.poi_id")
    # koordinat kurtarma: aynı place_id'nin geçerli (ondalıklı, Türkiye içi) gözlemi
    c.execute("""CREATE OR REPLACE TEMP TABLE rec AS SELECT p.poi_id, any_value(g.lon) lon, any_value(g.lat) lat FROM poi p JOIN (
                   SELECT raw_google_place_id pid, TRY_CAST(raw_lon AS DOUBLE) lon, TRY_CAST(raw_lat AS DOUBLE) lat FROM s.stg.poi_business__stg_google_places_ve_yogunluk__google_places_gozlem) g ON g.pid = p.source_place_id
                 WHERE p.coord_validity IN ('INTEGER_DEGREE','LON_EQ_LAT','OUT_OF_TURKEY') AND g.lon BETWEEN 25 AND 45.5 AND g.lat BETWEEN 35.5 AND 42.5 AND g.lon <> round(g.lon) AND g.lat <> round(g.lat) AND g.lon <> g.lat GROUP BY 1""")
    n_rec = c.execute("SELECT count(*) FROM rec").fetchone()[0]
    c.execute("""UPDATE poi SET lon=r.lon, lat=r.lat, geometry=ST_Point(r.lon, r.lat), coord_validity='valid', coord_recovery='RECOVERED_FROM_OTHER_OBSERVATION' FROM rec r WHERE poi.poi_id=r.poi_id""")
    c.execute("""CREATE OR REPLACE TEMP TABLE rg AS SELECT r.poi_id, g.geo_id, g.parent_geo_id, row_number() OVER (PARTITION BY r.poi_id ORDER BY g.geo_id) rn FROM rec r JOIN geo_entity g ON g.level='mahalle' AND g.geometry IS NOT NULL
                 AND r.lon BETWEEN g.bbox_xmin AND g.bbox_xmax AND r.lat BETWEEN g.bbox_ymin AND g.bbox_ymax AND ST_Contains(g.geometry, ST_Point(r.lon, r.lat))""")
    c.execute("""UPDATE poi SET spatial_geo_id=rg.geo_id, assigned_geo_id=rg.geo_id, assigned_ilce_geo_id=rg.parent_geo_id, assignment_method='ST_Contains(web_polygon)', assignment_confidence=CASE WHEN poi.context_geo_id=rg.geo_id THEN 0.99 ELSE 0.95 END,
                 ilce_assignment_confidence=0.99, assignment_flags=concat_ws(',', 'COORD_RECOVERED', CASE WHEN poi.context_il_norm IS NOT NULL AND poi.context_il_norm <> (SELECT name_norm FROM geo_entity i WHERE i.geo_id=(SELECT il_geo_id FROM geo_entity m WHERE m.geo_id=rg.geo_id)) THEN 'CONFLICT_coord_il_vs_context_il' END)
                 FROM rg WHERE rg.poi_id=poi.poi_id AND rg.rn=1""")
    c.execute("UPDATE poi SET assignment_confidence=0.5 WHERE coord_recovery IS NOT NULL AND assignment_flags LIKE '%CONFLICT_coord_il%'")
    rep["poi"] = {"categories_applied_v3": n_cat, "sector_hints": c.execute("SELECT count(*) FROM poi WHERE sector_hint IS NOT NULL").fetchone()[0], "coords_recovered": n_rec,
                  "recovered_assigned": c.execute("SELECT count(*) FROM poi WHERE coord_recovery IS NOT NULL AND assigned_geo_id IS NOT NULL AND assignment_method='ST_Contains(web_polygon)'").fetchone()[0],
                  "source_id_kind": dict(c.execute("SELECT source_id_kind, count(*) FROM poi GROUP BY 1").fetchall()), "coord_validity": dict(c.execute("SELECT coord_validity, count(*) FROM poi GROUP BY 1").fetchall())}
    print("  ", rep["poi"], flush=True)
    # ---------------- C) analytics ----------------
    print("analytics…", flush=True)
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_poi_stats AS SELECT assigned_geo_id geo_id,
        count(*) AS poi_count, count(*) FILTER (WHERE assignment_method='ST_Contains(web_polygon)') AS poi_count_coord_verified,
        count(*) FILTER (WHERE coalesce(predicted_sector,'')='Yeme-İçme' OR raw_category IN ('Kafe','Restoran & Lokanta','Fast Food','3. Nesil Kahveci','Kebapçı & Ocakbaşı','Pastane & Fırın','Tatlı','Türk')) AS yeme_icme_count,
        count(*) FILTER (WHERE raw_category='Kuaför & Güzellik' OR predicted_category IN ('Kuaför & Berber','Güzellik & Kozmetik')) AS kuafor_guzellik_count,
        count(*) FILTER (WHERE raw_category='Bakkal & Market' OR predicted_category IN ('Süpermarket & Market','Ucuzluk & İndirim Market')) AS market_count,
        round(avg(rating) FILTER (WHERE rating IS NOT NULL), 2) AS poi_avg_rating,
        count(*) FILTER (WHERE source_coverage LIKE '%google%' AND source_coverage LIKE '%osm%') AS poi_google_osm_count, count(*) FILTER (WHERE source_coverage='osm_only') AS poi_osm_only_count,
        count(*) FILTER (WHERE source_coverage LIKE '%yemeksepeti%') AS poi_yemeksepeti_count, count(*) FILTER (WHERE predicted_category IS NULL AND raw_category='Ticari Mekan') AS poi_type_unknown_count
        FROM poi WHERE assigned_geo_id IS NOT NULL AND assignment_confidence >= 0.6 GROUP BY 1""")
    c.execute("""CREATE OR REPLACE TABLE analytics.ilce_poi_stats AS SELECT assigned_ilce_geo_id geo_id,
        count(*) AS poi_count, count(*) FILTER (WHERE assignment_method='ST_Contains(web_polygon)') AS poi_count_coord_verified,
        count(*) FILTER (WHERE coalesce(predicted_sector,'')='Yeme-İçme' OR raw_category IN ('Kafe','Restoran & Lokanta','Fast Food','3. Nesil Kahveci','Kebapçı & Ocakbaşı','Pastane & Fırın','Tatlı','Türk') OR raw_category LIKE 'osm:%' OR raw_category='yemeksepeti:restaurant') AS yeme_icme_count,
        count(*) FILTER (WHERE predicted_category='Restoran & Lokanta' OR raw_category IN ('Restoran & Lokanta','Türk','Kebapçı & Ocakbaşı','Dönerci','Pide & Lahmacun','Balık Restoranı','osm:restaurant')) AS restoran_count,
        count(*) FILTER (WHERE predicted_category IS NULL AND raw_category='Ticari Mekan') AS poi_type_unknown_count, count(*) FILTER (WHERE sector_hint='Yeme-İçme') AS yeme_icme_hint_count,
        round(avg(rating) FILTER (WHERE rating IS NOT NULL), 2) AS poi_avg_rating
        FROM poi WHERE assigned_ilce_geo_id IS NOT NULL AND coalesce(ilce_assignment_confidence,0) >= 0.9 AND coalesce(assignment_flags,'') NOT LIKE '%CONFLICT_coord_il%' GROUP BY 1""")
    c.execute(f"""CREATE OR REPLACE TABLE analytics.ilce_indicators AS SELECT e.geo_id,
        (SELECT sum(parsed_value) FROM indicator_observation o WHERE o.geo_id=e.geo_id AND o.domain='housing_sales' AND o.metric='sales_count' AND o.period_kind='month' AND o.series_version='tuik_medas_2025_revision' AND o.period BETWEEN '2025-09' AND '{CUTOFF}') AS konut_satis_son12ay,
        (SELECT sum(parsed_value) FROM indicator_observation o WHERE o.geo_id=e.geo_id AND o.domain='housing_sales' AND o.metric='sales_count' AND o.period_kind='month' AND o.series_version='tuik_medas_2025_revision' AND o.period BETWEEN '2025-01' AND '2025-12') AS konut_satis_2025,
        (SELECT sum(parsed_value) FROM indicator_observation o WHERE o.geo_id=e.geo_id AND o.domain='housing_sales' AND o.metric='sales_count' AND o.period_kind='month' AND o.series_version='tuik_medas_2025_revision' AND o.period BETWEEN '2024-01' AND '2024-12') AS konut_satis_2024,
        (SELECT parsed_value FROM indicator_observation o WHERE o.geo_id=e.geo_id AND o.domain='construction_permit' AND o.metric='dwelling_units' AND o.dim1='ruhsat' AND o.period='2025' LIMIT 1) AS yapi_ruhsati_daire_2025,
        (SELECT parsed_value FROM indicator_observation o WHERE o.geo_id=e.geo_id AND o.domain='construction_permit' AND o.metric='dwelling_units' AND o.dim1='kullanma' AND o.period='2025' LIMIT 1) AS kullanma_izni_daire_2025,
        (SELECT parsed_value FROM indicator_observation o WHERE o.geo_id=e.geo_id AND o.domain='socio_economic' AND o.metric='ses_score' LIMIT 1) AS ses_skor_2023
        FROM geo_entity e WHERE e.level='ilce'""")
    c.execute("""CREATE OR REPLACE VIEW analytics.ilce_intelligence AS SELECT e.geo_id, e.name AS ilce, l.name AS il, e.tuik_kodu, e.web_county_id,
        (SELECT parsed_value FROM population_observation o WHERE o.geo_id=e.geo_id AND o.period='2025' LIMIT 1) AS pop_2025_tuik,
        (SELECT parsed_value FROM population_observation o WHERE o.geo_id=e.geo_id AND o.period='2024' LIMIT 1) AS pop_2024_tuik,
        i.konut_satis_son12ay, i.konut_satis_2025, i.konut_satis_2024, i.yapi_ruhsati_daire_2025, i.kullanma_izni_daire_2025, i.ses_skor_2023,
        coalesce(p.poi_count,0) poi_count, coalesce(p.restoran_count,0) restoran_count, coalesce(p.yeme_icme_count,0) yeme_icme_count, coalesce(p.poi_type_unknown_count,0) poi_type_unknown_count, p.poi_avg_rating,
        (SELECT count(*) FROM geo_entity m WHERE m.parent_geo_id=e.geo_id) AS mahalle_sayisi
        FROM geo_entity e JOIN geo_entity l ON l.geo_id=e.il_geo_id LEFT JOIN analytics.ilce_indicators i ON i.geo_id=e.geo_id LEFT JOIN analytics.ilce_poi_stats p ON p.geo_id=e.geo_id WHERE e.level='ilce'""")
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_5', ?), ('code_hash_v1_5', ?), ('identity_rule', 'poi_id bizimdir; source_place_id yalnız kaynak izi (source_id_kind ile); eşleştirme ad+konum ile')", [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    # ---------------- bütünlük + rapor ----------------
    rep["indicator_total"] = c.execute("SELECT count(*) FROM indicator_observation").fetchone()[0]
    rep["indicator_by_domain"] = dict(c.execute("SELECT domain, count(*) FROM indicator_observation GROUP BY 1").fetchall())
    rep["integrity"] = {"orphan_indicator_geo": c.execute("SELECT count(*) FROM indicator_observation o WHERE NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id=o.geo_id)").fetchone()[0],
                        "indicator_id_unique": c.execute("SELECT count(*)=count(DISTINCT observation_id) FROM indicator_observation").fetchone()[0],
                        "indicator_null_parsed": c.execute("SELECT count(*) FROM indicator_observation WHERE parsed_value IS NULL").fetchone()[0],
                        "orphan_poi_assignment": c.execute("SELECT count(*) FROM poi p WHERE assigned_geo_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id=p.assigned_geo_id)").fetchone()[0],
                        "poi_id_unique": c.execute("SELECT count(*)=count(DISTINCT poi_id) FROM poi").fetchone()[0],
                        "housing_sales_national_2025_jan_aug(bulletin 1.020.207)": c.execute("SELECT sum(parsed_value) FROM indicator_observation WHERE domain='housing_sales' AND metric='sales_count' AND period_kind='month' AND series_version='tuik_medas_2025_revision' AND period BETWEEN '2025-01' AND '2025-08'").fetchone()[0]}
    rep["sample_ilce"] = c.execute("SELECT il, ilce, pop_2025_tuik, konut_satis_2025, konut_satis_son12ay, yapi_ruhsati_daire_2025, ses_skor_2023, poi_count, restoran_count FROM analytics.ilce_intelligence WHERE ilce IN ('Muratpaşa','Kadıköy','Çankaya','Nilüfer') ORDER BY il").fetchall()
    rep["seconds"] = round(time.time() - t0)
    for t in ("indicator_observation", "poi_category_candidate"): c.execute(f"COPY (SELECT * FROM {t}) TO '{OUT}/canonical/v1.1/parquet/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * EXCLUDE (geometry) FROM poi) TO '{OUT}/canonical/v1.1/parquet/poi.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM analytics.ilce_intelligence) TO '{OUT}/canonical/v1.1/parquet/ilce_intelligence.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM analytics.mahalle_intelligence) TO '{OUT}/canonical/v1.1/parquet/mahalle_intelligence.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.close()
    (OUT / "canonical" / "v1.1" / "build_report_v1_5.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_5", "indicator_total": rep["indicator_total"], "by_domain": rep["indicator_by_domain"], "poi": rep["poi"], "integrity": rep["integrity"]}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({k: rep[k] for k in ("indicator_total", "indicator_by_domain", "poi", "integrity", "sample_ilce", "seconds")}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
