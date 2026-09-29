#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.6 (yerinde yükseltme; taban canonical_v1.5)
A) Üç yeni POI kaynağı:
   1. Market Fiyatı şube kataloğu (14.791 şube: a101/bim/şok/migros/tarım kredi/carrefour/hakmar) — koordinatlı, mahalle geo_id kaynakta hazır
   2. EPDK şarj istasyonları (13.059) — koordinatlı, lisans/operatör/soket bilgileri
   3. KTB belgeli turizm tesisleri (24.723) — KOORDİNAT YOK; yalnız il+ilçe+ad. Ad+ilçe ile mevcut POI'ye bağlanır; bağlanamayan
      kayıt ilçe düzeyinde coord_validity='MISSING' ile saklanır (koordinat uydurulmaz).
   Eşleştirme kanıt modeli v1 ile aynı (ad benzerliği + mesafe, noisy-OR, tek-tek atama). high+ (≥0.95) birleşir, 0.85-0.95 review.
B) Ürün fiyatı katmanı (yeni, gayrimenkul price_observation'dan AYRI grain):
   - product (ürün sözlüğü: market ürün kataloğu 30.446 + Opet 8 akaryakıt + hal ürünleri)
   - product_price_observation: market şube×ürün (9.41 M), Opet ilçe×gün×akaryakıt (9.08 M), HKS ulusal hal (1.39 M),
     İzmir hal (0.86 M), market ürün fiyat geçmişi il düzeyi (3.21 M)
C) charging_socket (35.891 soket) · etbis_site (60.188 e-ticaret sitesi + 60.027 profil; MERSİS/vergi/KEP = restricted)
D) analytics: ilçe düzeyi market şubesi / şarj istasyonu / turizm tesisi sayıları
Kural: kaynaklar arası DEĞER birleştirilmez; her satır kendi kaynağının izini taşır; hiçbir satır silinmez.
"""
import time, datetime as dt, json, hashlib, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
MODEL_VERSION = "canonical_v1.6"; BASE = "canonical_v1.5"
V = "POI_MATCHER_V1_MULTI"; MERGE_BANDS = ("virtually_certain", "very_high", "high")
TH = {"virtually_certain": 0.995, "very_high": 0.980, "high": 0.950, "review": 0.850}
MIN_FREE = 1_200_000_000
MARKET_ADI = {"a101": "A101", "bim": "BİM", "sok": "ŞOK", "migros": "Migros", "tarim_kredi": "Tarım Kredi Kooperatif Market",
              "carrefour": "CarrefourSA", "hakmar": "Hakmar"}


def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def reset_tag(c, tag, sysname):
    """Yeniden çalıştırmada aynı kaynağın önceki eklemelerini geri alır (idempotent)."""
    n1 = c.execute(f"SELECT count(*) FROM poi WHERE primary_source='{tag}'").fetchone()[0]
    c.execute(f"DELETE FROM poi WHERE primary_source='{tag}'")
    c.execute(f"DELETE FROM poi_source_link WHERE source_system='{sysname}'")
    c.execute(f"""UPDATE poi SET source_coverage = CASE WHEN r LIKE '%+%' OR r LIKE '%\_only' THEN r ELSE r || '_only' END
        FROM (SELECT poi_id pid, regexp_replace(source_coverage, '\+{tag}', '', 'g') r FROM poi WHERE source_coverage LIKE '%+{tag}%') t
        WHERE poi.poi_id = t.pid""")
    if n1: print(f"  ({tag}: önceki {n1:,} kayıt geri alındı)", flush=True)


def match_coord_source(c, now, tag, prefix, sysname, src_sql, cat, pcat, sector, acq, dist, view, rep, max_dist_m=200, cat_bonus=0.0):
    """Koordinatlı bir kaynağı mevcut poi ile eşleştirir; eşleşenleri bağlar, eşleşmeyeni yeni poi olarak ekler."""
    reset_tag(c, tag, sysname)
    c.execute(f"CREATE OR REPLACE TEMP TABLE src AS {src_sql}")
    n_src = c.execute("SELECT count(*), count(*) FILTER (WHERE lat IS NOT NULL) FROM src").fetchone()
    c.execute(f"""CREATE OR REPLACE TEMP TABLE cand AS
        SELECT x.sid, p.poi_id, x.name x_name, p.name p_name,
          2*6371000*asin(sqrt(pow(sin(radians(p.lat-x.lat)/2),2)+cos(radians(x.lat))*cos(radians(p.lat))*pow(sin(radians(p.lon-x.lon)/2),2))) dist_m,
          jaro_winkler_similarity(norm(x.match_name), norm(p.name)) jw,
          len(list_intersect(string_split(norm(x.match_name),' '), string_split(norm(p.name),' ')))::DOUBLE
            / greatest(1, len(list_distinct(string_split(norm(x.match_name),' ') || string_split(norm(p.name),' ')))) tok
        FROM src x JOIN poi p ON p.coord_validity='valid' AND p.lon BETWEEN x.lon-0.004 AND x.lon+0.004 AND p.lat BETWEEN x.lat-0.003 AND x.lat+0.003
        WHERE x.lat IS NOT NULL AND p.name IS NOT NULL""")
    c.execute(f"DELETE FROM cand WHERE dist_m > {max_dist_m}")
    c.execute("""CREATE OR REPLACE TEMP TABLE sc AS SELECT *, greatest(jw, CASE WHEN tok >= 0.5 THEN 0.80 + 0.20*tok ELSE 0 END) name_sim FROM cand""")
    c.execute(f"""CREATE OR REPLACE TEMP TABLE sc2 AS SELECT *,
        CASE WHEN name_sim >= 0.80 THEN 0.92*(name_sim-0.80)/0.20 WHEN name_sim >= 0.75 THEN 0.10 ELSE 0 END name_ev,
        CASE WHEN dist_m <= 25 THEN 0.85 WHEN dist_m <= 60 THEN 0.70 WHEN dist_m <= 120 THEN 0.50 WHEN dist_m <= {max_dist_m} THEN 0.25 ELSE 0.10 END dist_ev
        FROM sc WHERE name_sim >= 0.75""")
    c.execute(f"""CREATE OR REPLACE TEMP TABLE sc3 AS SELECT *, round(1 - (1-name_ev)*(1-dist_ev)*(1-{cat_bonus}), 4) score,
        format('name_ev={{:.2f}}(sim={{:.3f}}); dist_ev={{:.2f}}({{:.0f}}m); src={tag}', name_ev, name_sim, dist_ev, dist_m) reasons FROM sc2""")
    rows = c.execute("SELECT sid, poi_id, score FROM sc3 ORDER BY score DESC, dist_m").fetchall()
    us, up, best = set(), set(), []
    for s_, p_, sc_ in rows:
        if s_ in us or p_ in up: continue
        us.add(s_); up.add(p_); best.append((s_, p_))
    c.execute("CREATE OR REPLACE TEMP TABLE best (sid VARCHAR, poi_id VARCHAR)")
    if best: c.executemany("INSERT INTO best VALUES (?,?)", best)
    c.execute(f"""CREATE OR REPLACE TEMP TABLE m AS SELECT s.*, CASE WHEN score >= {TH['virtually_certain']} THEN 'virtually_certain'
        WHEN score >= {TH['very_high']} THEN 'very_high' WHEN score >= {TH['high']} THEN 'high'
        WHEN score >= {TH['review']} THEN 'review_recommended' ELSE 'no_auto_merge' END band FROM sc3 s JOIN best b USING (sid, poi_id)""")
    c.execute(f"COPY (SELECT *, '{V}' matcher_version, '{now}' created_at FROM m) TO '{OUT}/mappings/poi_match_{tag}_v1_PROPOSED.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    bands = dict(c.execute("SELECT band, count(*) FROM m GROUP BY 1").fetchall())
    # birleşen kayıtlar: kapsam etiketi + bağlantı
    c.execute(f"""UPDATE poi SET source_coverage = replace(poi.source_coverage, '_only', '') || '+{tag}'
                  FROM m WHERE poi.poi_id = m.poi_id AND m.band IN {MERGE_BANDS}""")
    c.execute(f"""INSERT INTO poi_source_link SELECT 'LNK_' || md5('{tag}|' || m.sid || '|' || m.poi_id), m.poi_id, '{sysname}', m.sid,
        NULL, x.source_row_hash, m.score, m.band, '{V}', CASE WHEN m.band IN {MERGE_BANDS} THEN 'merged' ELSE 'review' END, m.reasons, ?
        FROM m JOIN src x ON x.sid = m.sid WHERE m.band <> 'no_auto_merge'""", [now])
    c.execute(f"CREATE OR REPLACE TEMP TABLE merged_s AS SELECT sid FROM m WHERE band IN {MERGE_BANDS}")
    c.execute("""CREATE OR REPLACE TEMP TABLE newv AS SELECT x.*, (x.lon BETWEEN 25 AND 45.5 AND x.lat BETWEEN 35.5 AND 42.5) in_tr
                 FROM src x WHERE x.sid NOT IN (SELECT sid FROM merged_s)""")
    c.execute("""CREATE OR REPLACE TEMP TABLE newv_geo AS SELECT n.sid, g.geo_id, g.parent_geo_id,
        row_number() OVER (PARTITION BY n.sid ORDER BY g.geo_id) rn FROM newv n
        JOIN geo_entity g ON g.level='mahalle' AND g.geometry IS NOT NULL AND n.lon BETWEEN g.bbox_xmin AND g.bbox_xmax
          AND n.lat BETWEEN g.bbox_ymin AND g.bbox_ymax AND ST_Contains(g.geometry, ST_Point(n.lon, n.lat)) WHERE n.in_tr""")
    c.execute(f"""INSERT INTO poi BY NAME SELECT '{prefix}' || md5(n.sid) poi_id, n.sid source_place_id, n."name" AS "name", '{cat}' raw_category,
        '{pcat}' predicted_category, '{sector}' predicted_sector, 'SOURCE_TYPE' category_method, 0.95 category_confidence, 'high' category_band,
        n.phone phone, n.address address_raw, n.lon lon, n.lat lat, CASE WHEN n.lat IS NOT NULL THEN ST_Point(n.lon, n.lat) END geometry,
        'none' source_il_reliability,
        coalesce(n.src_mahalle_geo_id, ng.geo_id) assigned_geo_id,
        CASE WHEN n.src_mahalle_geo_id IS NOT NULL THEN 'source_geo_id' WHEN ng.geo_id IS NOT NULL THEN 'ST_Contains(web_polygon)' END assignment_method,
        CASE WHEN coalesce(n.src_mahalle_geo_id, ng.geo_id) IS NOT NULL THEN 0.95 END assignment_confidence,
        ng.geo_id spatial_geo_id, coalesce(n.src_ilce_geo_id, ng.parent_geo_id) assigned_ilce_geo_id,
        CASE WHEN coalesce(n.src_ilce_geo_id, ng.parent_geo_id) IS NOT NULL THEN 0.99 END ilce_assignment_confidence,
        CASE WHEN n.lat IS NULL THEN 'MISSING' WHEN NOT n.in_tr THEN 'OUT_OF_TURKEY' ELSE 'valid' END coord_validity,
        nullif(concat_ws(',', CASE WHEN n.lat IS NULL THEN 'NO_SOURCE_COORD' END,
               CASE WHEN n.in_tr AND coalesce(n.src_mahalle_geo_id, ng.geo_id) IS NULL THEN 'VALID_COORD_OUTSIDE_ALL_POLYGONS' END), '') assignment_flags,
        1 observation_count, n.observed_at last_observed_at, CAST(NULL AS VARCHAR) source_file_id, n.source_row_number, n.source_row_hash,
        '{view}' staging_view, '{acq}' acquisition_class, '{dist}' distribution_class, ? created_at,
        '{tag}_only' source_coverage, '{tag}' primary_source, 'source_native_id' source_id_kind, n.source_path source_path
        FROM newv n LEFT JOIN newv_geo ng ON ng.sid = n.sid AND ng.rn = 1""", [now])
    c.execute(f"""INSERT INTO poi_source_link SELECT 'LNK_' || md5('{tag}|' || source_place_id), poi_id, '{sysname}', source_place_id,
        NULL, source_row_hash, NULL, NULL, NULL, 'primary', 'kaynak kaydın kendisi ({tag}_only)', ? FROM poi WHERE primary_source='{tag}'""", [now])
    n_new = c.execute(f"SELECT count(*) FROM poi WHERE primary_source='{tag}'").fetchone()[0]
    rep[tag] = {"kaynak_satir": n_src[0], "koordinatli": n_src[1], "bantlar": bands,
                "birlesen": sum(v for k, v in bands.items() if k in MERGE_BANDS), "yeni_poi": n_new}
    print(f"  {tag}: kaynak {n_src[0]:,} · birleşen {rep[tag]['birlesen']:,} · yeni POI {n_new:,}", flush=True)


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if free_bytes(OUT) < MIN_FREE: raise SystemExit("DİSK KORUMASI: 1,2 GB'den az boş alan var")
    (OUT / "tmp" / "canon16").mkdir(parents=True, exist_ok=True)
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute("SET threads=2")
    c.execute(f"SET temp_directory='{OUT}/tmp/canon16'"); c.execute("SET preserve_insertion_order=false")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    mv = c.execute("SELECT value FROM _meta WHERE key='model_version'").fetchone()[0]
    if mv != BASE: raise SystemExit(f"beklenen taban {BASE}, bulunan {mv}")
    rep = {"built_at": now, "base": mv}
    c.execute("CREATE OR REPLACE MACRO norm(x) AS trim(regexp_replace(regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9 ]+', ' ', 'g'), ' +', ' ', 'g'))")
    c.execute("ALTER TABLE poi ADD COLUMN IF NOT EXISTS source_path VARCHAR")

    # ---------------- A1) Market şubeleri ----------------
    mk = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in MARKET_ADI.items())
    match_coord_source(c, now, "marketfiyati", "POI_MF_", "marketfiyati",
        f"""SELECT raw_depot_id sid,
             (CASE raw_market {mk} ELSE upper(raw_market) END) || CASE WHEN nullif(raw_depot_name,'') IS NOT NULL THEN ' ' || raw_depot_name ELSE '' END AS "name",
             (CASE raw_market {mk} ELSE upper(raw_market) END) AS match_name,
             TRY_CAST(raw_lat AS DOUBLE) lat, TRY_CAST(raw_lon AS DOUBLE) lon, CAST(NULL AS VARCHAR) phone, raw_depot_name address,
             nullif(raw_mahalle_geo_id,'') src_mahalle_geo_id, nullif(raw_ilce_geo_id,'') src_ilce_geo_id,
             raw_guncellenme_tarihi observed_at, source_row_number, source_row_hash, source_path
           FROM s.stg.price_series__stg_marketfiyati_depot""",
        "market:zincir_sube", "Süpermarket & Market", "Gıda Perakende", "web_research", "internal",
        "stg_marketfiyati_depot", rep, max_dist_m=150, cat_bonus=0.30)

    # ---------------- A2) EPDK şarj istasyonları ----------------
    match_coord_source(c, now, "epdk_sarj", "POI_EV_", "epdk_sarj",
        """SELECT CAST(raw_station_id AS VARCHAR) sid, raw_title AS "name", coalesce(nullif(raw_brand,''), raw_title) AS match_name,
             TRY_CAST(raw_lat AS DOUBLE) lat, TRY_CAST(raw_lon AS DOUBLE) lon, raw_phone phone, raw_address address,
             CAST(NULL AS VARCHAR) src_mahalle_geo_id, CAST(NULL AS VARCHAR) src_ilce_geo_id, raw_guncellenme_tarihi observed_at,
             source_row_number, source_row_hash, source_path
           FROM s.stg.poi_business__stg_epdk_sarj_istasyon""",
        "sarj:istasyon", "Elektrikli Şarj İstasyonu", "Ulaşım & Enerji", "official_public", "public",
        "stg_epdk_sarj_istasyon", rep, max_dist_m=120, cat_bonus=0.0)

    # ---------------- A3) KTB turizm tesisleri (koordinatsız) ----------------
    reset_tag(c, "ktb", "ktb_belgeli_tesis")
    c.execute("""CREATE OR REPLACE TEMP TABLE ktb AS SELECT
        md5(raw_Sehir || '|' || raw_Ilce || '|' || raw_TesisAdi || '|' || coalesce(raw_BelgeNo,'')) sid,
        raw_TesisAdi ad, raw_TesisTur tur, raw_TesisSinif sinif, raw_BelgeTuru belge_turu, raw_BelgeNo belge_no,
        raw_Sehir il_adi, raw_Ilce ilce_adi, raw_guncellenme_tarihi observed_at, source_row_number, source_row_hash, source_path
        FROM s.stg.tourism__stg_ktb_belgeli_tesis""")
    c.execute("""CREATE OR REPLACE TEMP TABLE ktb_geo AS SELECT k.sid, e.geo_id ilce_geo_id, e.il_geo_id
        FROM ktb k JOIN geo_entity l ON l.level='il' AND l.name_norm = norm(k.il_adi)
        JOIN geo_entity e ON e.level='ilce' AND e.il_geo_id = l.geo_id AND e.name_norm = norm(k.ilce_adi)""")
    c.execute("""CREATE OR REPLACE TEMP TABLE ktb_cand AS SELECT k.sid, p.poi_id, jaro_winkler_similarity(norm(k.ad), norm(p.name)) jw
        FROM ktb k JOIN ktb_geo g ON g.sid=k.sid JOIN poi p ON p.assigned_ilce_geo_id = g.ilce_geo_id
        WHERE p.name IS NOT NULL AND norm(p.name) <> '' AND jaro_winkler_similarity(norm(k.ad), norm(p.name)) >= 0.93""")
    rows = c.execute("SELECT sid, poi_id, jw FROM ktb_cand ORDER BY jw DESC").fetchall()
    us, up, best = set(), set(), []
    for s_, p_, j_ in rows:
        if s_ in us or p_ in up: continue
        us.add(s_); up.add(p_); best.append((s_, p_, j_))
    c.execute("CREATE OR REPLACE TEMP TABLE ktb_best (sid VARCHAR, poi_id VARCHAR, jw DOUBLE)")
    if best: c.executemany("INSERT INTO ktb_best VALUES (?,?,?)", best)
    c.execute("""UPDATE poi SET source_coverage = replace(poi.source_coverage,'_only','') || '+ktb'
                 FROM ktb_best b WHERE poi.poi_id = b.poi_id AND b.jw >= 0.97""")
    c.execute("""INSERT INTO poi_source_link SELECT 'LNK_' || md5('ktb|' || b.sid || '|' || b.poi_id), b.poi_id, 'ktb_belgeli_tesis', b.sid,
        NULL, k.source_row_hash, b.jw, CASE WHEN b.jw >= 0.97 THEN 'high' ELSE 'review_recommended' END, ?,
        CASE WHEN b.jw >= 0.97 THEN 'merged' ELSE 'review' END,
        format('ad benzerliği jw={:.3f}; aynı ilçe; koordinat yok (KTB kaydında koordinat verilmiyor)', b.jw), ?
        FROM ktb_best b JOIN ktb k ON k.sid = b.sid""", [V, now])
    c.execute("""INSERT INTO poi BY NAME SELECT 'POI_KTB_' || md5(k.sid) poi_id, k.belge_no source_place_id, k.ad AS "name",
        'ktb:' || lower(coalesce(k.tur,'tesis')) raw_category, 'Konaklama Tesisi' predicted_category, 'Turizm & Konaklama' predicted_sector,
        'SOURCE_TYPE' category_method, 0.95 category_confidence, 'high' category_band,
        g.ilce_geo_id assigned_ilce_geo_id, 0.99 ilce_assignment_confidence, 'MISSING' coord_validity,
        'registry_district' assignment_method, CAST(NULL AS VARCHAR) assigned_geo_id,
        'NO_SOURCE_COORD,DISTRICT_LEVEL_ONLY' assignment_flags, 1 observation_count, k.observed_at last_observed_at,
        CAST(NULL AS VARCHAR) source_file_id, k.source_row_number, k.source_row_hash, 'stg_ktb_belgeli_tesis' staging_view,
        'official_public' acquisition_class, 'public' distribution_class, ? created_at, 'ktb_only' source_coverage,
        'ktb' primary_source, 'source_native_id' source_id_kind, k.source_path source_path
        FROM ktb k LEFT JOIN ktb_geo g ON g.sid = k.sid
        WHERE k.sid NOT IN (SELECT sid FROM ktb_best WHERE jw >= 0.97)
        QUALIFY row_number() OVER (PARTITION BY k.sid ORDER BY k.source_row_number) = 1""", [now])
    c.execute("""INSERT INTO poi_source_link SELECT 'LNK_' || md5('ktb|' || k.sid || '|' || k.source_row_hash), p.poi_id,
        'ktb_belgeli_tesis', coalesce(k.belge_no, k.sid), NULL, k.source_row_hash, NULL, NULL, NULL, 'primary',
        'kaynak kaydın kendisi (ktb_only); belge türü=' || coalesce(k.belge_turu,'?') || '; tesis türü=' || coalesce(k.tur,'?'), ?
        FROM ktb k JOIN poi p ON p.poi_id = 'POI_KTB_' || md5(k.sid) WHERE p.primary_source='ktb'""", [now])
    rep["ktb"] = {"kaynak_satir": c.execute("SELECT count(*) FROM ktb").fetchone()[0],
                  "ilce_eslesen": c.execute("SELECT count(*) FROM ktb_geo").fetchone()[0],
                  "mevcut_poi_ile_birlesen": c.execute("SELECT count(*) FROM ktb_best WHERE jw>=0.97").fetchone()[0],
                  "review": c.execute("SELECT count(*) FROM ktb_best WHERE jw<0.97").fetchone()[0],
                  "yeni_poi": c.execute("SELECT count(*) FROM poi WHERE primary_source='ktb'").fetchone()[0],
                  "tekil_tesis_anahtari": c.execute("SELECT count(DISTINCT sid) FROM ktb").fetchone()[0],
                  "kaynak_satir_bagli": c.execute("SELECT count(*) FROM poi_source_link WHERE source_system='ktb_belgeli_tesis'").fetchone()[0]}
    print(f"  ktb: {rep['ktb']}", flush=True)
    c.execute("CHECKPOINT")
    (OUT / "canonical" / "v1.1" / "build_report_v1_6.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    print(f"A aşaması bitti · {round(time.time()-t0)}s · boş disk {free_bytes(OUT)/1e9:.1f} GB")
    c.close()


if __name__ == "__main__":
    main()
