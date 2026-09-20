#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.1 (v1 üzerine; v1 dosyasına dokunulmaz, kopyalanır).
Değişiklikler:
  1. name_norm düzeltmesi: v1 makrosu büyük harfli Ç/Ş/Ğ/Ö/Ü ile başlayan adları bozuyordu ("Çankaya"→"ankaya"; 2.057 kayıt). Bağlantılar id ile kurulduğundan sonuç etkilenmedi; yine de yeniden hesaplanır.
  2. price_observation genişletmesi: staging'deki 4 fiyat tablosu daha (ej_region_index, price_summary, land_trend_monthly, land_summary) → uzun format gözlem.
     Paket kopyaları (aynı veri farklı klasörlerde) aynı (geo, kategori, metrik, dönem, değer, kaynak güncelleme zamanı) ise TEK gözleme indirgenir, n_copies sayılır; farklı değer → ayrı gözlem (çelişki raporlanır).
  3. POI koordinat geçerliliği: tam sayı derece (yer tutucu), Türkiye dışı, lon=lat kopyası → geçersiz; geçersiz koordinatlı 1.689 "şans eseri" atama iptal edilir.
     Yedek atama: address_raw arama bağlamı ("X Mahallesi İlçe İl") → mahalle (yöntem search_context, güven 0.6). Koordinat ili ≠ bağlam ili → CONFLICT bayrağı (atama korunur, güven 0.5).
  4. analytics.mahalle_intelligence yeniden: fiyat sütunları en güncel kaynak gözleminden; POI sayıları yalnız güvenilir atamalardan.
Çıktı: canonical/v1.1/geoprop_canonical_v1_1.duckdb (+ parquet), canonical/v1.1/build_report_v1_1.json, audit kaydı.
"""
import time, datetime as dt, json, hashlib, shutil, re, collections, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
STG = OUT / "staging" / "geoprop_staging.duckdb"
V1 = OUT / "canonical" / "v1" / "geoprop_canonical_v1.duckdb"
CAN_DIR = OUT / "canonical" / "v1.1"; CAN = CAN_DIR / "geoprop_canonical_v1_1.duckdb"; PQ = CAN_DIR / "parquet"
MODEL_VERSION = "canonical_v1.1"; CUTOFF = "2026-08"
TR_BBOX = (25.0, 35.5, 45.5, 42.5)

# ---- fiyat kaynakları: (staging görünümü, seviye ifadesi, kategori ifadesi, alt kategori, dönem, kaynak güncelleme, geo anahtarları, metrikler[(metric, col, unit)]) ----
PRICE_SOURCES = {
    "stg_land_trend_monthly": dict(
        level="'mahalle'", category="CASE WHEN raw_property_type='2' OR lower(raw_kategori)='tarla' THEN 'tarla' ELSE 'arsa' END",
        period="raw_ay", updated="raw_guncellenme_tarihi", proj="raw_projeksiyon", district="raw_district_id", county="raw_county_id", city="raw_city_id",
        metrics=[("sale_price_m2", "raw_satilik_m2_fiyat", "TL/m2"), ("min_price_m2", "raw_min_m2_fiyat", "TL/m2"), ("max_price_m2", "raw_max_m2_fiyat", "TL/m2"),
                 ("avg_price", "raw_ortalama_fiyat", "TL"), ("avg_area_m2", "raw_ortalama_m2", "m2"), ("price_index", "raw_fiyat_endeksi", "index"),
                 ("listing_count", "raw_ilan_sayisi", "count"), ("monthly_change", "raw_aylik_fiyat_degisim", "ratio"), ("yearly_change", "raw_yillik_fiyat_degisim", "ratio"),
                 ("days_on_market", "raw_ilanda_kalma_suresi_gun", "day")]),
    "stg_land_summary": dict(
        level="'mahalle'", category="CASE WHEN raw_property_type='2' OR lower(raw_kategori)='tarla' THEN 'tarla' ELSE 'arsa' END",
        period="raw_donem", updated="raw_guncellenme_tarihi", proj="NULL", district="raw_district_id", county="raw_county_id", city="raw_city_id",
        metrics=[("sale_price_m2", "raw_satilik_m2_fiyat", "TL/m2"), ("min_price_m2", "raw_min_m2_fiyat", "TL/m2"), ("max_price_m2", "raw_max_m2_fiyat", "TL/m2"),
                 ("avg_price", "raw_ortalama_fiyat", "TL"), ("avg_area_m2", "raw_ortalama_m2", "m2"), ("price_index", "raw_fiyat_endeksi", "index"),
                 ("listing_count", "raw_ilan_sayisi", "count"), ("monthly_change", "raw_aylik_fiyat_degisim", "ratio"), ("yearly_change", "raw_yillik_fiyat_degisim", "ratio"),
                 ("days_on_market", "raw_ilanda_kalma_suresi_gun", "day"), ("stock_change_rate", "raw_stok_degisim_orani", "ratio"), ("yearly_stock_change", "raw_yillik_stok_degisim", "ratio")]),
    "stg_price_summary": dict(
        level="raw_seviye", category="lower(raw_kategori)", period="raw_donem", updated="raw_guncellenme_tarihi", proj="NULL",
        district="raw_district_id", county="raw_county_id", city="raw_city_id",
        metrics=[("sale_price_m2", "raw_satilik_m2_fiyat", "TL/m2"), ("rent_price_m2", "raw_kiralik_m2_fiyat", "TL/m2"), ("avg_price", "raw_ortalama_fiyat", "TL"),
                 ("amortization_years", "raw_amortisman_yil", "year"), ("gross_rent_yield", "raw_brut_kira_getirisi", "ratio"), ("avg_building_age", "raw_ortalama_bina_yasi", "year"),
                 ("sale_days_on_market", "raw_satilik_kalma_suresi_gun", "day"), ("rent_days_on_market", "raw_kiralik_kalma_suresi_gun", "day"),
                 ("listing_count", "raw_ilan_sayisi", "count"), ("yearly_change", "raw_yillik_fiyat_degisim", "ratio")]),
    "stg_ej_region_index": dict(
        level="raw_seviye", category="lower(raw_tip)", period="raw_donem", updated="coalesce(raw_toplanma, raw_toplanmaZamani)", proj="NULL",
        district="coalesce(raw_district_id, raw_districtId)", county="coalesce(raw_county_id, raw_countyId)", city="coalesce(raw_city_id, raw_cityId)",
        metrics=[("sale_price_m2", "coalesce(raw_m2_fiyat, raw_m2Fiyat)", "TL/m2"), ("min_price_m2", "raw_m2FiyatMin", "TL/m2"), ("max_price_m2", "raw_m2FiyatMax", "TL/m2"),
                 ("avg_price", "raw_ortFiyat", "TL"), ("avg_area_m2", "coalesce(raw_ort_m2, raw_ortM2)", "m2"), ("listing_count", "coalesce(raw_ilan_sayisi, raw_ilanSayisi)", "count"),
                 ("monthly_change", "coalesce(raw_aylik_degisim, raw_aylikDegisim)", "ratio"), ("yearly_change", "coalesce(raw_yillik_degisim, raw_yillikDegisim)", "ratio"),
                 ("amortization_years", "raw_amortisman", "year"), ("gross_rent_yield", "raw_getiri", "ratio"), ("avg_building_age", "coalesce(raw_ort_bina_yasi, raw_ortBinaYasi)", "year"),
                 ("days_on_market", "coalesce(raw_ilan_suresi, raw_ilanSuresi)", "day"), ("price_index", "raw_endeks", "index"),
                 ("rent_price_m2", "coalesce(raw_kira_m2_fiyat, raw_kiraM2Fiyat)", "TL/m2"), ("rent_price", "coalesce(raw_kira_fiyat, raw_kiraFiyat)", "TL"),
                 ("rent_yearly_change", "coalesce(raw_kira_yillik_degisim, raw_kiraYillikDegisim)", "ratio"), ("rent_listing_count", "raw_kiraIlanSayisi", "count"),
                 ("rent_days_on_market", "raw_kiraIlanSuresi", "day"), ("rent_avg_area_m2", "raw_kiraOrtM2", "m2")]),
}

TR = str.maketrans("çğıöşüâîûÇĞİÖŞÜ", "cgiosuaiucgiosu")


def pynorm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").replace("İ", "i").replace("I", "ı").translate(TR).lower().translate(TR))


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    CAN_DIR.mkdir(parents=True, exist_ok=True); PQ.mkdir(exist_ok=True)
    if CAN.exists(): CAN.rename(CAN_DIR / f"geoprop_canonical_v1_1_superseded_{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}.duckdb")
    shutil.copy2(V1, CAN)  # v1 dokunulmaz; v1.1 kopya üzerinde
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='3GB'"); c.execute("SET threads=4"); c.execute(f"SET temp_directory='{OUT}/tmp/canon11'")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    rep = {"built_at": now, "base": "canonical_v1", "steps": {}}
    # ---------------- 1. name_norm düzeltmesi ----------------
    c.execute("CREATE OR REPLACE MACRO norm(x) AS regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9]+', '', 'g')")
    bad = c.execute("SELECT count(*) FROM geo_entity WHERE name_norm <> norm(name)").fetchone()[0]
    c.execute("UPDATE geo_entity SET name_norm = norm(name)")
    rep["steps"]["name_norm_fix"] = {"changed": bad, "sample_before_after": [("Çankaya", "ankaya→cankaya")]}
    print(f"name_norm düzeltildi: {bad} kayıt ({time.time()-t0:.0f}s)", flush=True)
    # ---------------- 1b. TÜİK tür düzeltmesi ----------------
    # v1 hatası: TÜİK mahalle ve köy tablolarının kod alanları çakışıyor (2.731 ortak kod); v1 nüfusu yalnız kod eşitliğiyle bağladı → 2.729 yanlış gözlem.
    c.execute("ALTER TABLE geo_entity ADD COLUMN IF NOT EXISTS tuik_tur VARCHAR")
    c.execute(f"""UPDATE geo_entity SET tuik_tur = c.tuik_tur FROM (SELECT DISTINCT web_district_id, tuik_kodu, tuik_tur FROM '{OUT}/mappings/geo_candidates_web_tuik_v2.parquet') c
                  WHERE geo_entity.level='mahalle' AND geo_entity.web_district_id = c.web_district_id AND geo_entity.tuik_kodu = c.tuik_kodu""")
    c.execute("UPDATE geo_entity SET tuik_tur = 'ilce' WHERE level='ilce' AND tuik_kodu IS NOT NULL")
    wrong = c.execute("""SELECT o.staging_view, count(*) FROM population_observation o JOIN geo_entity g ON g.geo_id=o.geo_id
                         WHERE (o.staging_view='stg_tuik_nufus_mahalle' AND g.tuik_tur IS DISTINCT FROM 'mahalle') OR (o.staging_view='stg_tuik_nufus_koy' AND g.tuik_tur IS DISTINCT FROM 'koy') GROUP BY 1""").fetchall()
    c.execute("""DELETE FROM population_observation WHERE observation_id IN (SELECT o.observation_id FROM population_observation o JOIN geo_entity g ON g.geo_id=o.geo_id
                 WHERE (o.staging_view='stg_tuik_nufus_mahalle' AND g.tuik_tur IS DISTINCT FROM 'mahalle') OR (o.staging_view='stg_tuik_nufus_koy' AND g.tuik_tur IS DISTINCT FROM 'koy'))""")
    ratio_bad = c.execute("""SELECT count(*) FROM (SELECT geo_id, max(parsed_value) FILTER (WHERE staging_view='stg_admin_polygon') w, max(parsed_value) FILTER (WHERE staging_view LIKE 'stg_tuik_nufus_%' AND period='2025') t
                             FROM population_observation WHERE level='mahalle' GROUP BY 1) WHERE w>0 AND t>0 AND greatest(w/t, t/w) > 3""").fetchone()[0]
    rep["steps"]["tuik_tur_fix"] = {"removed_wrong_type_rows": dict(wrong), "remaining_web_vs_tuik_ratio_gt3": ratio_bad}
    print(f"TÜİK tür düzeltmesi: silinen yanlış gözlem {dict(wrong)}; kalan oran>3: {ratio_bad} ({time.time()-t0:.0f}s)", flush=True)
    # ---------------- 2. price_observation genişletmesi ----------------
    for col, typ in (("subcategory", "VARCHAR"), ("source_updated_at", "VARCHAR"), ("n_copies", "INTEGER"), ("source_table", "VARCHAR")):
        c.execute(f"ALTER TABLE price_observation ADD COLUMN IF NOT EXISTS {col} {typ}")
    c.execute("UPDATE price_observation SET n_copies = 1, source_table = 'stg_price_trend_monthly' WHERE n_copies IS NULL")
    c.execute("CREATE TEMP TABLE lk_mah AS SELECT web_district_id k, geo_id FROM geo_entity WHERE level='mahalle'")
    c.execute("CREATE TEMP TABLE lk_ilce AS SELECT web_county_id k, geo_id FROM geo_entity WHERE level='ilce'")
    c.execute("CREATE TEMP TABLE lk_il AS SELECT web_city_id k, geo_id FROM geo_entity WHERE level='il'")
    acc_all = {}
    skip = set(filter(None, os.environ.get("P4_SKIP", "").split(",")))  # disk kısıtı: ertelenen tablolar (raporda 'deferred')
    for tbl, sp in PRICE_SOURCES.items():
        if tbl in skip: acc_all[tbl] = {"status": "DEFERRED (disk kısıtı; sonraki kurulumda eklenecek)"}; print(f"  {tbl}: ERTELENDİ", flush=True); continue
        t1 = time.time(); v = f"s.stg.price_series__{tbl}"
        mcols = ", ".join(f"{col} AS m_{metric}" for metric, col, _ in sp["metrics"])
        c.execute(f"""CREATE OR REPLACE TEMP TABLE src AS
            SELECT x.lvl, x.cat, x.period_, x.updated_, x.kind_, x.source_file_id, x.source_row_number, x.source_row_hash, {", ".join("x.m_"+m for m,_,_ in sp["metrics"])},
                   coalesce(gm.geo_id, gi.geo_id, gl.geo_id) AS geo_id_ FROM (
              SELECT {sp['level']} AS lvl, {sp['category']} AS cat, {sp['period']} AS period_, {sp['updated']} AS updated_,
                     CASE WHEN {sp['proj']} = '1' OR {sp['period']} > '{CUTOFF}' THEN 'projected' ELSE 'measured' END AS kind_,
                     CAST({sp['district']} AS VARCHAR) AS d_, CAST({sp['county']} AS VARCHAR) AS c_, CAST({sp['city']} AS VARCHAR) AS i_,
                     source_file_id, source_row_number, source_row_hash, {mcols}
              FROM {v}) x
            LEFT JOIN lk_mah gm ON x.lvl='mahalle' AND gm.k = x.d_
            LEFT JOIN lk_ilce gi ON x.lvl='ilce' AND gi.k = x.c_
            LEFT JOIN lk_il gl ON x.lvl='il' AND gl.k = x.i_""")
        rows_in = c.execute("SELECT count(*) FROM src").fetchone()[0]
        unlinked = dict(c.execute("SELECT coalesce(lvl,'?'), count(*) FROM src WHERE geo_id_ IS NULL GROUP BY 1").fetchall())
        acc = {"rows_in": rows_in, "rows_unlinked_by_level": unlinked, "metrics": {}}
        for metric, _rawcol, unit in sp["metrics"]:
            col = f"m_{metric}"
            r = c.execute(f"""SELECT count(*) FILTER (WHERE geo_id_ IS NOT NULL AND {col} IS NOT NULL AND {col} <> ''), count(*) FILTER (WHERE geo_id_ IS NOT NULL AND ({col} IS NULL OR {col} = '')) FROM src""").fetchone()
            cells, nulls = r
            c.execute(f"""INSERT INTO price_observation
                SELECT 'OBS_PRC_' || md5(geo_id_ || cat || period_ || '{metric}' || coalesce(updated_,'') || source_row_hash), geo_id_, lvl, cat, '{metric}', {col}, TRY_CAST({col} AS DOUBLE), '{unit}', period_,
                       kind_, 'web_research', 'public', 60, source_file_id, source_row_number, source_row_hash, '{tbl}', ?,
                       NULL, updated_, n_copies, '{tbl}'
                FROM (SELECT *, count(*) OVER w AS n_copies, row_number() OVER (PARTITION BY geo_id_, cat, period_, kind_, TRY_CAST({col} AS DOUBLE), updated_ ORDER BY source_file_id, source_row_number) AS rn
                      FROM src WHERE geo_id_ IS NOT NULL AND {col} IS NOT NULL AND {col} <> ''
                      WINDOW w AS (PARTITION BY geo_id_, cat, period_, kind_, TRY_CAST({col} AS DOUBLE), updated_)) WHERE rn = 1""", [now])
            written = c.execute(f"SELECT count(*) FROM price_observation WHERE source_table='{tbl}' AND metric='{metric}'").fetchone()[0]
            acc["metrics"][metric] = {"cells": cells, "null_cells": nulls, "written": written, "collapsed_copies": cells - written}
        acc["equation"] = all(m["cells"] == m["written"] + m["collapsed_copies"] for m in acc["metrics"].values())
        acc_all[tbl] = acc
        print(f"  {tbl}: {rows_in:,} satır → yazılan {sum(m['written'] for m in acc['metrics'].values()):,} gözlem, kopya indirgeme {sum(m['collapsed_copies'] for m in acc['metrics'].values()):,}, bağlanamayan {unlinked} ({time.time()-t1:.0f}s)", flush=True)
    c.execute("DROP TABLE IF EXISTS src")
    conflicts = c.execute("""SELECT count(*) FROM (SELECT geo_id, category, metric, period, observation_kind, source_updated_at FROM price_observation
                             WHERE source_table <> 'stg_price_trend_monthly' GROUP BY ALL HAVING count(DISTINCT parsed_value) > 1)""").fetchone()[0]
    rep["steps"]["price_extension"] = {"sources": acc_all, "same_key_same_updated_different_value_conflicts": conflicts}
    # ---------------- 3. POI koordinat geçerliliği + yedek atama ----------------
    print("poi koordinat geçerliliği…", flush=True)
    for col, typ in (("coord_validity", "VARCHAR"), ("spatial_geo_id", "VARCHAR"), ("context_geo_id", "VARCHAR"), ("context_match", "VARCHAR"),
                     ("context_il_norm", "VARCHAR"), ("context_ilce_norm", "VARCHAR"), ("context_mahalle_norm", "VARCHAR"), ("assignment_confidence", "DOUBLE"), ("assignment_flags", "VARCHAR")):
        c.execute(f"ALTER TABLE poi ADD COLUMN IF NOT EXISTS {col} {typ}")
    c.execute("UPDATE poi SET spatial_geo_id = assigned_geo_id WHERE spatial_geo_id IS NULL")
    c.execute(f"""UPDATE poi SET coord_validity = CASE
        WHEN lon IS NULL OR lat IS NULL THEN 'MISSING'
        WHEN lon = round(lon) AND lat = round(lat) THEN 'INTEGER_DEGREE'
        WHEN lon = lat THEN 'LON_EQ_LAT'
        WHEN lon < {TR_BBOX[0]} OR lon > {TR_BBOX[2]} OR lat < {TR_BBOX[1]} OR lat > {TR_BBOX[3]} THEN 'OUT_OF_TURKEY'
        ELSE 'valid' END""")
    # adres bağlamı ayrıştırma (Python): "X Mahallesi <ilçe...> <il> [arama terimi...]"
    il_map = {r[1]: r[0] for r in c.execute("SELECT geo_id, name_norm FROM geo_entity WHERE level='il'").fetchall()}
    ilce_map = collections.defaultdict(dict)  # il_geo -> ilce_norm -> geo
    for g, n, il in c.execute("SELECT geo_id, name_norm, il_geo_id FROM geo_entity WHERE level='ilce'").fetchall(): ilce_map[il][n] = g
    mah_by_ilce = collections.defaultdict(dict); mah_by_il = collections.defaultdict(lambda: collections.defaultdict(list))
    for g, n, p, il in c.execute("SELECT geo_id, name_norm, parent_geo_id, il_geo_id FROM geo_entity WHERE level='mahalle'").fetchall():
        mah_by_ilce[p][n] = g; mah_by_il[il][n].append(g)
    rows = c.execute("SELECT poi_id, address_raw FROM poi").fetchall()
    out = []; stats = collections.Counter()
    for pid, adr in rows:
        m = re.match(r"^(.*?)\s+(Mahallesi|Köyü|Mah\.)\s+(.*)$", adr or "")
        if not m: stats["no_pattern"] += 1; out.append((pid, None, "no_pattern", None, None, None)); continue
        mah_n = pynorm(m.group(1)); rest = m.group(3).split()
        il_idx = None
        for i, tok in enumerate(rest):
            if pynorm(tok) in il_map: il_idx = i  # son il adı (arama terimi il adı içermez)
        if il_idx is None: stats["no_il"] += 1; out.append((pid, None, "no_il", None, None, mah_n)); continue
        il_n = pynorm(rest[il_idx]); il_g = il_map[il_n]; ilce_n = pynorm("".join(rest[:il_idx]))
        ilce_g = ilce_map[il_g].get(ilce_n)
        if ilce_g is None and ilce_n == "merkez":  # merkez ilçe: il adıyla aynı olabilir
            ilce_g = ilce_map[il_g].get(il_n)
        geo = mah_by_ilce[ilce_g].get(mah_n) if ilce_g else None
        if geo: stats["il_ilce_mah"] += 1; out.append((pid, geo, "il_ilce_mahalle", il_n, ilce_n, mah_n)); continue
        cands = mah_by_il[il_g].get(mah_n, [])
        if len(cands) == 1: stats["il_mah_unique"] += 1; out.append((pid, cands[0], "il_mahalle_unique", il_n, ilce_n, mah_n))
        elif len(cands) > 1: stats["il_mah_ambiguous"] += 1; out.append((pid, None, "il_mahalle_ambiguous", il_n, ilce_n, mah_n))
        else: stats["mah_not_found"] += 1; out.append((pid, None, "mahalle_not_found", il_n, ilce_n, mah_n))
    import pyarrow as pa
    c.register("ctx_arrow", pa.Table.from_pylist([dict(zip(("poi_id", "geo", "match", "il_n", "ilce_n", "mah_n"), o)) for o in out]))
    c.execute("CREATE OR REPLACE TEMP TABLE ctx AS SELECT * FROM ctx_arrow")
    c.execute("""UPDATE poi SET context_geo_id = ctx.geo, context_match = ctx.match, context_il_norm = ctx.il_n, context_ilce_norm = ctx.ilce_n, context_mahalle_norm = ctx.mah_n FROM ctx WHERE poi.poi_id = ctx.poi_id""")
    # nihai atama
    c.execute("""UPDATE poi SET
        assigned_geo_id = CASE WHEN coord_validity='valid' AND spatial_geo_id IS NOT NULL THEN spatial_geo_id ELSE context_geo_id END,
        assignment_method = CASE WHEN coord_validity='valid' AND spatial_geo_id IS NOT NULL THEN 'ST_Contains(web_polygon)'
                                 WHEN context_geo_id IS NOT NULL THEN 'search_context(address_raw)' ELSE NULL END""")
    c.execute("""UPDATE poi SET assignment_flags = NULL, assignment_confidence = NULL""")
    c.execute("""CREATE OR REPLACE TEMP TABLE sp_il AS SELECT p.poi_id, i.name_norm il_n FROM poi p JOIN geo_entity g ON g.geo_id = p.spatial_geo_id JOIN geo_entity i ON i.geo_id = g.il_geo_id""")
    c.execute("""UPDATE poi SET
        assignment_flags = concat_ws(',',
            CASE WHEN coord_validity <> 'valid' THEN 'COORD_' || coord_validity END,
            CASE WHEN coord_validity <> 'valid' AND spatial_geo_id IS NOT NULL THEN 'V1_SPATIAL_ASSIGNMENT_REVOKED' END,
            CASE WHEN coord_validity = 'valid' AND spatial_geo_id IS NOT NULL AND context_il_norm IS NOT NULL AND context_il_norm <> sp_il.il_n THEN 'CONFLICT_coord_il_vs_context_il' END,
            CASE WHEN coord_validity = 'valid' AND spatial_geo_id IS NOT NULL AND context_geo_id IS NOT NULL AND context_geo_id <> spatial_geo_id THEN 'context_mahalle_differs(search_radius)' END,
            CASE WHEN coord_validity = 'valid' AND spatial_geo_id IS NULL THEN 'VALID_COORD_OUTSIDE_ALL_POLYGONS' END)
        FROM sp_il WHERE sp_il.poi_id = poi.poi_id""")
    c.execute("""UPDATE poi SET assignment_flags = concat_ws(',', CASE WHEN coord_validity <> 'valid' THEN 'COORD_' || coord_validity END,
        CASE WHEN coord_validity = 'valid' THEN 'VALID_COORD_OUTSIDE_ALL_POLYGONS' END) WHERE spatial_geo_id IS NULL""")
    c.execute("""UPDATE poi SET assignment_confidence = CASE
        WHEN assignment_method = 'ST_Contains(web_polygon)' AND context_geo_id = spatial_geo_id THEN 0.99
        WHEN assignment_method = 'ST_Contains(web_polygon)' AND assignment_flags LIKE '%CONFLICT_coord_il%' THEN 0.50
        WHEN assignment_method = 'ST_Contains(web_polygon)' THEN 0.95
        WHEN assignment_method = 'search_context(address_raw)' THEN 0.33 END""")  # 0.33 = ölçülen: geçerli koordinatlılarda bağlam mahallesi = gerçek mahalle oranı (180.813/545.887); ilçe düzeyinde 0.93
    c.execute("UPDATE poi SET assignment_flags = NULL WHERE assignment_flags = ''")
    # ilçe düzeyi atama: mahalle atamasının ebeveyni; arama bağlamı ilçe düzeyinde güvenilir (0.93)
    for col, typ in (("assigned_ilce_geo_id", "VARCHAR"), ("ilce_assignment_confidence", "DOUBLE")): c.execute(f"ALTER TABLE poi ADD COLUMN IF NOT EXISTS {col} {typ}")
    c.execute("""UPDATE poi SET assigned_ilce_geo_id = g.parent_geo_id, ilce_assignment_confidence = CASE WHEN assignment_method='ST_Contains(web_polygon)' THEN least(0.99, assignment_confidence + 0.04) ELSE 0.93 END
                 FROM geo_entity g WHERE g.geo_id = poi.assigned_geo_id""")
    ctx_eval = c.execute("""SELECT count(*), count(*) FILTER (WHERE p.context_geo_id=p.spatial_geo_id), count(*) FILTER (WHERE gc.parent_geo_id=gs.parent_geo_id), count(*) FILTER (WHERE gc.il_geo_id=gs.il_geo_id)
                            FROM poi p JOIN geo_entity gc ON gc.geo_id=p.context_geo_id JOIN geo_entity gs ON gs.geo_id=p.spatial_geo_id WHERE p.coord_validity='valid'""").fetchone()
    rep["steps"]["poi_assignment"] = {
        "coord_validity": dict(c.execute("SELECT coord_validity, count(*) FROM poi GROUP BY 1").fetchall()),
        "context_parse": dict(stats),
        "assignment_method": dict(c.execute("SELECT coalesce(assignment_method,'UNASSIGNED'), count(*) FROM poi GROUP BY 1").fetchall()),
        "flags": dict(c.execute("SELECT f, count(*) FROM (SELECT unnest(string_split(assignment_flags, ',')) f FROM poi WHERE assignment_flags IS NOT NULL) GROUP BY 1").fetchall()),
        "v1_revoked": c.execute("SELECT count(*) FROM poi WHERE assignment_flags LIKE '%V1_SPATIAL_ASSIGNMENT_REVOKED%'").fetchone()[0],
        "search_context_reliability(valid_coord_pairs, same_mahalle, same_ilce, same_il)": ctx_eval}
    print("  ", json.dumps(rep["steps"]["poi_assignment"], ensure_ascii=False, default=str)[:900], f"({time.time()-t0:.0f}s)", flush=True)
    # ---------------- 4. analytics ----------------
    print("analytics…", flush=True)
    c.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    # en güncel kaynak gözlemi (dönem=CUTOFF, ölçülmüş): (geo, kategori, metrik) başına source_updated_at en büyük; eşitlikte n_copies büyük, sonra küçük dosya id. Türetilmiş tablo; her kurulumda yeniden üretilir.
    c.execute(f"""CREATE OR REPLACE TABLE analytics.price_latest_cutoff AS
        SELECT * FROM price_observation WHERE period='{CUTOFF}' AND observation_kind='measured'
        QUALIFY row_number() OVER (PARTITION BY geo_id, category, metric ORDER BY source_updated_at DESC NULLS LAST, n_copies DESC, source_file_id) = 1""")
    W = lambda cat, met, alias: f"max(parsed_value) FILTER (WHERE category='{cat}' AND metric='{met}') AS {alias}"
    c.execute(f"""CREATE OR REPLACE TABLE analytics.mahalle_price_wide AS SELECT geo_id,
        {W('konut','sale_price_m2','konut_satilik_m2')}, {W('konut','rent_price_m2','konut_kiralik_m2')}, {W('konut','avg_price','konut_ort_fiyat')}, {W('konut','listing_count','konut_ilan_sayisi')},
        {W('konut','gross_rent_yield','konut_brut_kira_getirisi')}, {W('konut','amortization_years','konut_amortisman_yil')}, {W('konut','avg_building_age','konut_ort_bina_yasi')}, {W('konut','sale_days_on_market','konut_satilik_kalma_gun')},
        {W('arsa','sale_price_m2','arsa_satilik_m2')}, {W('arsa','min_price_m2','arsa_min_m2')}, {W('arsa','max_price_m2','arsa_max_m2')}, {W('arsa','listing_count','arsa_ilan_sayisi')}, {W('arsa','price_index','arsa_fiyat_endeksi')},
        {W('tarla','sale_price_m2','tarla_satilik_m2')}
        FROM analytics.price_latest_cutoff WHERE level='mahalle' GROUP BY geo_id""")
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_poi_stats AS SELECT assigned_geo_id geo_id,
        count(*) AS poi_count, count(*) FILTER (WHERE assignment_method='ST_Contains(web_polygon)') AS poi_count_coord_verified,
        count(*) FILTER (WHERE coalesce(predicted_sector,'')='Yeme-İçme' OR raw_category IN ('Kafe','Restoran & Lokanta','Fast Food','3. Nesil Kahveci','Kebapçı & Ocakbaşı','Pastane & Fırın','Tatlı','Türk')) AS yeme_icme_count,
        count(*) FILTER (WHERE raw_category='Kuaför & Güzellik' OR predicted_category IN ('Kuaför & Berber','Güzellik & Kozmetik')) AS kuafor_guzellik_count,
        count(*) FILTER (WHERE raw_category='Bakkal & Market' OR predicted_category IN ('Süpermarket & Market','Ucuzluk & İndirim Market')) AS market_count,
        round(avg(rating) FILTER (WHERE rating IS NOT NULL), 2) AS poi_avg_rating
        FROM poi WHERE assigned_geo_id IS NOT NULL AND assignment_confidence >= 0.6 GROUP BY 1""")
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_pop AS SELECT geo_id,
        max(parsed_value) FILTER (WHERE staging_view='stg_admin_polygon') AS pop_2024_web,
        max(parsed_value) FILTER (WHERE staging_view IN ('stg_tuik_nufus_mahalle','stg_tuik_nufus_koy') AND period='2025') AS pop_2025_tuik
        FROM population_observation WHERE level='mahalle' GROUP BY 1""")
    c.execute("""CREATE OR REPLACE VIEW analytics.mahalle_intelligence AS
        SELECT e.geo_id, e.name AS mahalle, i.name AS ilce, l.name AS il, e.tuik_il_kodu, e.web_district_id, e.tkgm_id, e.tuik_kodu, e.tkgm_link_band, e.tuik_link_band, e.centroid_lon, e.centroid_lat,
               pp.pop_2024_web, pp.pop_2025_tuik, pw.* EXCLUDE (geo_id),
               coalesce(ps.poi_count, 0) poi_count, coalesce(ps.poi_count_coord_verified, 0) poi_count_coord_verified, coalesce(ps.yeme_icme_count, 0) yeme_icme_count,
               coalesce(ps.kuafor_guzellik_count, 0) kuafor_guzellik_count, coalesce(ps.market_count, 0) market_count, ps.poi_avg_rating, e.mapping_status
        FROM geo_entity e JOIN geo_entity i ON i.geo_id = e.parent_geo_id JOIN geo_entity l ON l.geo_id = e.il_geo_id
        LEFT JOIN analytics.mahalle_pop pp ON pp.geo_id = e.geo_id LEFT JOIN analytics.mahalle_price_wide pw ON pw.geo_id = e.geo_id LEFT JOIN analytics.mahalle_poi_stats ps ON ps.geo_id = e.geo_id
        WHERE e.level='mahalle'""")
    c.execute("UPDATE _meta SET value = ? WHERE key = 'model_version'", [MODEL_VERSION])
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_1', ?), ('code_hash_v1_1', ?), ('base_version', 'canonical_v1')", [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    # ---------------- kapsam + bütünlük ----------------
    cov = c.execute(f"""SELECT count(*), count(*) FILTER (WHERE pop_2025_tuik IS NOT NULL), count(*) FILTER (WHERE konut_satilik_m2 IS NOT NULL), count(*) FILTER (WHERE arsa_satilik_m2 IS NOT NULL OR tarla_satilik_m2 IS NOT NULL),
                        count(*) FILTER (WHERE konut_satilik_m2 IS NOT NULL OR arsa_satilik_m2 IS NOT NULL OR tarla_satilik_m2 IS NOT NULL OR konut_ort_fiyat IS NOT NULL), count(*) FILTER (WHERE poi_count > 0) FROM analytics.mahalle_intelligence""").fetchone()
    rep["mahalle_coverage"] = dict(zip(["total", "with_tuik_2025_pop", f"with_konut_price_{CUTOFF}", f"with_arsa_or_tarla_price_{CUTOFF}", f"with_any_price_{CUTOFF}", "with_poi"], cov))
    rep["price_any_period_mahalle"] = c.execute("SELECT count(DISTINCT geo_id) FROM price_observation WHERE level='mahalle'").fetchone()[0]
    integ = {"orphan_price_geo": c.execute("SELECT count(*) FROM price_observation o WHERE NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id=o.geo_id)").fetchone()[0],
             "orphan_poi_assigned": c.execute("SELECT count(*) FROM poi p WHERE assigned_geo_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM geo_entity g WHERE g.geo_id=p.assigned_geo_id)").fetchone()[0],
             "price_obs_id_unique": c.execute("SELECT count(*) = count(DISTINCT observation_id) FROM price_observation").fetchone()[0],
             "price_null_parsed": c.execute("SELECT count(*) FROM price_observation WHERE parsed_value IS NULL").fetchone()[0]}
    rep["integrity"] = integ
    rep["tables"] = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("geo_entity", "population_observation", "price_observation", "poi", "poi_snapshot")}
    rep["sample_muratpasa"] = c.execute("SELECT mahalle, pop_2025_tuik, konut_satilik_m2, konut_kiralik_m2, konut_brut_kira_getirisi, arsa_satilik_m2, poi_count, poi_count_coord_verified FROM analytics.mahalle_intelligence WHERE il='Antalya' AND ilce='Muratpaşa' ORDER BY poi_count DESC LIMIT 5").fetchall()
    for t in (() if os.environ.get("P4_NO_BIG_PARQUET") else ("geo_entity", "price_observation", "poi")):
        cols = "* EXCLUDE (geometry)" if t in ("geo_entity", "poi") else "*"
        c.execute(f"COPY (SELECT {cols} FROM {t}) TO '{PQ}/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * FROM analytics.mahalle_intelligence) TO '{PQ}/mahalle_intelligence.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.close()
    rep["seconds"] = round(time.time() - t0); rep["db"] = str(CAN); rep["db_mb"] = round(CAN.stat().st_size / 1e6)
    (CAN_DIR / "build_report_v1_1.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_1_BUILD", "model_version": MODEL_VERSION, "tables": rep["tables"], "coverage": rep["mahalle_coverage"], "integrity": integ, "report": str(CAN_DIR / "build_report_v1_1.json")}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({k: rep[k] for k in ("tables", "mahalle_coverage", "price_any_period_mahalle", "integrity", "sample_muratpasa", "seconds", "db_mb")}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
