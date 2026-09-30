#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.6 B aşaması: ürün fiyatı katmanı (gayrimenkul price_observation'dan ayrı grain).
  product .................. ürün sözlüğü (market kataloğu + Opet akaryakıt + HKS ulusal hal + İzmir hal)
  product_price_observation  market şube×ürün · Opet ilçe×gün×akaryakıt · HKS ulusal hal · İzmir hal · market il günlük geçmiş
Kurallar: kaynaklar arası değer birleştirilmez (her satır source_system ile durur), satır muhasebesi rapora yazılır,
tekrar çalıştırmada ilgili source_system satırları silinip yeniden yazılır (idempotent).
"""
import time, datetime as dt, json, hashlib, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
MIN_FREE = 1_200_000_000


def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if free_bytes(OUT) < MIN_FREE: raise SystemExit("DİSK KORUMASI")
    (OUT / "tmp" / "canon16").mkdir(parents=True, exist_ok=True)
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute("SET threads=2")
    c.execute(f"SET temp_directory='{OUT}/tmp/canon16'"); c.execute("SET preserve_insertion_order=false")
    c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    c.execute("CREATE OR REPLACE MACRO norm(x) AS trim(regexp_replace(regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9 ]+', ' ', 'g'), ' +', ' ', 'g'))")
    rep = {"built_at": now, "stage": "v1.6B"}

    # ---------------- product sözlüğü ----------------
    c.execute("""CREATE TABLE IF NOT EXISTS product (product_id VARCHAR PRIMARY KEY, source_system VARCHAR, product_code VARCHAR,
        name VARCHAR, brand VARCHAR, unit VARCHAR, quantity_unit VARCHAR, category_main VARCHAR, category_menu VARCHAR,
        categories_json VARCHAR, image_url VARCHAR, first_seen_at VARCHAR, source_row_hash VARCHAR, staging_view VARCHAR,
        acquisition_class VARCHAR, distribution_class VARCHAR, created_at VARCHAR)""")
    c.execute("DELETE FROM product")
    c.execute("""INSERT INTO product SELECT 'PRD_MF_' || raw_id, 'marketfiyati', raw_id, raw_title, raw_brand,
        raw_refinedVolumeOrWeight, raw_refinedQuantityUnit, raw_main_category, raw_menu_category, raw_categories, raw_imageUrl,
        raw_first_seen_at, source_row_hash, 'stg_marketfiyati_sube_urun', 'web_research', 'internal', ?
        FROM s.stg.price_series__stg_marketfiyati_sube_urun WHERE raw_depot_id IS NULL AND raw_id IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY raw_id ORDER BY raw_first_seen_at DESC NULLS LAST, source_row_number DESC) = 1""", [now])
    c.execute("""INSERT INTO product SELECT 'PRD_OPET_' || raw_urun_kodu, 'opet', raw_urun_kodu, raw_urun_adi, 'Opet',
        'TL/lt', 'litre', 'Akaryakıt', raw_urun_kisa, NULL, NULL, NULL, source_row_hash, 'stg_opet_urun_katalog',
        'web_research', 'internal', ? FROM s.stg.price_series__stg_opet_urun_katalog""", [now])
    c.execute("""INSERT INTO product SELECT 'PRD_HKS_' || md5(concat_ws('|', raw_urun_adi, coalesce(raw_urun_cinsi,''), coalesce(raw_urun_turu,''))),
        'hks_ulusal', md5(concat_ws('|', raw_urun_adi, coalesce(raw_urun_cinsi,''), coalesce(raw_urun_turu,''))),
        raw_urun_adi, NULL, any_value(raw_birim), NULL, any_value(raw_urun_turu), any_value(raw_urun_cinsi), NULL, NULL, min(raw_veri_tarihi),
        any_value(source_row_hash), 'stg_hal_ulusal_gunluk', 'official_public', 'public', ?
        FROM s.stg.price_series__stg_hal_ulusal_gunluk WHERE raw_urun_adi IS NOT NULL GROUP BY 1,2,3,4""", [now])
    c.execute("""INSERT INTO product SELECT 'PRD_IZH_' || md5(concat_ws('|', CAST(raw_mal_id AS VARCHAR), coalesce(CAST(raw_mal_tip_id AS VARCHAR),''))),
        'izmir_hal', concat_ws('/', CAST(raw_mal_id AS VARCHAR), CAST(raw_mal_tip_id AS VARCHAR)), raw_mal_adi, NULL,
        any_value(raw_birim), NULL, 'Hal', any_value(raw_mal_tip_adi), NULL, NULL, min(raw_bulten_tarihi), any_value(source_row_hash),
        'stg_hal_izmir_gunluk', 'official_public', 'public', ?
        FROM s.stg.price_series__stg_hal_izmir_gunluk WHERE raw_mal_adi IS NOT NULL GROUP BY 1,2,3,4""", [now])
    rep["product"] = dict(c.execute("SELECT source_system, count(*) FROM product GROUP BY 1 ORDER BY 2 DESC").fetchall())
    print("  product:", rep["product"], flush=True)

    # ---------------- product_price_observation ----------------
    c.execute("""CREATE TABLE IF NOT EXISTS product_price_observation (observation_id VARCHAR, source_system VARCHAR, poi_id VARCHAR,
        geo_id VARCHAR, level VARCHAR, product_id VARCHAR, product_code VARCHAR, price_kind VARCHAR, price_value DOUBLE,
        price_min DOUBLE, price_max DOUBLE, unit_price_value DOUBLE, unit_price_text VARCHAR, volume_value DOUBLE, unit VARCHAR,
        currency VARCHAR, discount_flag VARCHAR, discount_ratio VARCHAR, promo_text VARCHAR, observed_at VARCHAR, period VARCHAR,
        period_kind VARCHAR, acquisition_class VARCHAR, distribution_class VARCHAR, source_row_number BIGINT,
        source_row_hash VARCHAR, staging_view VARCHAR, source_path VARCHAR, recorded_at VARCHAR)""")
    counts = {}

    def load(sysname, sql, params=None):
        c.execute(f"DELETE FROM product_price_observation WHERE source_system='{sysname}'")
        t = time.time(); c.execute(sql, params or [])
        n = c.execute(f"SELECT count(*) FROM product_price_observation WHERE source_system='{sysname}'").fetchone()[0]
        counts[sysname] = n; print(f"  {sysname}: {n:,} satır ({round(time.time()-t)}s, boş disk {free_bytes(OUT)/1e9:.1f} GB)", flush=True)
        if free_bytes(OUT) < MIN_FREE: raise SystemExit("DİSK KORUMASI — yükleme durduruldu")

    # 1) market şube × ürün (poi_id: şube POI'si)
    c.execute("""CREATE OR REPLACE TEMP TABLE depot2poi AS
        SELECT l.source_record_id depot_id, l.poi_id, p.assigned_geo_id geo_id, p.assigned_ilce_geo_id ilce_geo_id
        FROM poi_source_link l JOIN poi p USING (poi_id) WHERE l.source_system='marketfiyati' AND l.link_status IN ('merged','primary')
        QUALIFY row_number() OVER (PARTITION BY l.source_record_id ORDER BY l.link_status) = 1""")
    load("marketfiyati_branch", """INSERT INTO product_price_observation SELECT
        'OPP_MF_' || md5(concat_ws('|', f.raw_depot_id, f.raw_urun_id, coalesce(f.raw_fiyat_endeks_zamani,''), CAST(f.raw_fiyat AS VARCHAR), f.source_row_hash, f.source_path)),
        'marketfiyati_branch', d.poi_id, coalesce(d.geo_id, nullif(trim(CAST(f.raw_mahalle_geo_id AS VARCHAR), '"'), 'null')), 'mahalle', 'PRD_MF_' || f.raw_urun_id, f.raw_urun_id,
        'shelf', f.raw_fiyat, NULL, NULL, f.raw_birim_fiyat, f.raw_birim_fiyat_metin, NULL, 'TL', 'TRY',
        f.raw_indirim, f.raw_indirim_orani, f.raw_promosyon_metni, CAST(f.raw_cekim_zamani AS VARCHAR),
        CASE WHEN f.raw_fiyat_endeks_zamani IS NOT NULL THEN strftime(try_strptime(f.raw_fiyat_endeks_zamani, '%d.%m.%Y %H:%M'), '%Y-%m-%d') END,
        'day', 'web_research', 'internal', f.source_row_number, f.source_row_hash, 'stg_marketfiyati_sube_urun_fiyat', f.source_path, ?
        FROM s.stg.price_series__stg_marketfiyati_sube_urun_fiyat f LEFT JOIN depot2poi d ON d.depot_id = f.raw_depot_id""", [now])

    # 2) Opet ilçe × gün × akaryakıt
    # Opet il adları: "İSTANBUL ANADOLU"/"İSTANBUL AVRUPA" = fiyat bölgesi → il İSTANBUL. "MERKEZ"/"ANADOLU_Y_MERKEZ" ilçesi = il merkezi → il düzeyi.
    c.execute("""CREATE OR REPLACE TEMP TABLE opet_pairs AS
        SELECT DISTINCT raw_il_adi il, raw_ilce_adi ilce,
          regexp_replace(raw_il_adi, ' (ANADOLU|AVRUPA)$', '') il_duz,
          raw_ilce_adi IN ('MERKEZ', 'ANADOLU_Y_MERKEZ') merkez_mi
        FROM s.stg.price_series__stg_opet_ilce_akaryakit_fiyat""")
    # elle çözülen iki ad: Opet "ONDOKUZMAYIS" = Samsun "19 Mayıs" ilçesi (aynı ilçenin eski/uzun yazımı);
    # Opet "ALPULLU" = Kırklareli Babaeski ilçesine bağlı Alpullu beldesi (ilçe değil) → ilçe düzeyinde Babaeski'ye yazılır.
    c.execute("""CREATE OR REPLACE TEMP TABLE opet_manual (il VARCHAR, ilce VARCHAR, geo_id VARCHAR, lvl VARCHAR, yontem VARCHAR)""")
    c.execute("""INSERT INTO opet_manual VALUES
        ('SAMSUN', 'ONDOKUZMAYIS', 'GEO_ILCE_000667', 'ilce', 'elle_ad_esleme(19 Mayıs)'),
        ('KIRKLARELİ', 'ALPULLU', 'GEO_ILCE_000450', 'ilce', 'elle_belde_ilce(Alpullu beldesi → Babaeski)')""")
    c.execute("""CREATE OR REPLACE TEMP TABLE opet_geo AS
        SELECT p.il, p.ilce, e.geo_id, 'ilce' AS lvl, 'il_ilce_adi' AS yontem
          FROM opet_pairs p JOIN geo_entity l ON l.level='il' AND l.name_norm = norm(p.il_duz)
          JOIN geo_entity e ON e.level='ilce' AND e.il_geo_id = l.geo_id AND e.name_norm = norm(p.ilce)
          WHERE NOT p.merkez_mi
        UNION ALL
        SELECT p.il, p.ilce, l.geo_id, 'il' AS lvl, 'il_merkezi_kaydi' AS yontem
          FROM opet_pairs p JOIN geo_entity l ON l.level='il' AND l.name_norm = norm(p.il_duz)
          WHERE p.merkez_mi
        UNION ALL SELECT il, ilce, geo_id, lvl, yontem FROM opet_manual""")
    rep["opet_ilce_eslesme"] = {"kaynak_cift": c.execute("SELECT count(*) FROM opet_pairs").fetchone()[0],
        "eslesen": c.execute("SELECT count(*) FROM opet_geo").fetchone()[0],
        "yonteme_gore": dict(c.execute("SELECT yontem, count(*) FROM opet_geo GROUP BY 1").fetchall()),
        "eslesmeyen": c.execute("SELECT count(*) FROM opet_pairs p WHERE NOT EXISTS (SELECT 1 FROM opet_geo g WHERE g.il=p.il AND g.ilce=p.ilce)").fetchone()[0]}
    load("opet_fuel", """INSERT INTO product_price_observation SELECT
        'OPP_OPET_' || md5(concat_ws('|', o.raw_ilce_kodu, o.raw_urun_kodu, o.raw_tarih)), 'opet_fuel', NULL, g.geo_id, coalesce(g.lvl, 'ilce'),
        'PRD_OPET_' || o.raw_urun_kodu, o.raw_urun_kodu, 'pump_list', o.raw_fiyat_tl, NULL, NULL, NULL, NULL, NULL, 'TL/lt', 'TRY',
        NULL, NULL, NULL, o.raw_tarih, o.raw_tarih, 'day', 'web_research', 'internal', o.source_row_number, o.source_row_hash,
        'stg_opet_ilce_akaryakit_fiyat', o.source_path, ?
        FROM s.stg.price_series__stg_opet_ilce_akaryakit_fiyat o LEFT JOIN opet_geo g ON g.il = o.raw_il_adi AND g.ilce = o.raw_ilce_adi""", [now])

    # 3) HKS ulusal hal
    load("hks_ulusal", """INSERT INTO product_price_observation SELECT
        'OPP_HKS_' || md5(concat_ws('|', h.raw_veri_tarihi, h.raw_urun_adi, coalesce(h.raw_urun_cinsi,''), coalesce(h.raw_urun_turu,''))),
        'hks_ulusal', NULL, 'GEO_TR', 'ulke',
        'PRD_HKS_' || md5(concat_ws('|', h.raw_urun_adi, coalesce(h.raw_urun_cinsi,''), coalesce(h.raw_urun_turu,''))),
        md5(concat_ws('|', h.raw_urun_adi, coalesce(h.raw_urun_cinsi,''), coalesce(h.raw_urun_turu,''))), 'wholesale_avg',
        h.raw_ortalama_fiyat_tl, NULL, NULL, NULL, NULL, h.raw_islem_hacmi, h.raw_birim, 'TRY', NULL, NULL, NULL,
        h.raw_guncellenme_tarihi, h.raw_veri_tarihi, 'day', 'official_public', 'public', h.source_row_number, h.source_row_hash,
        'stg_hal_ulusal_gunluk', h.source_path, ? FROM s.stg.price_series__stg_hal_ulusal_gunluk h""", [now])

    # 4) İzmir hal (asgari/azami/ortalama)
    load("izmir_hal", """INSERT INTO product_price_observation SELECT
        'OPP_IZH_' || md5(concat_ws('|', CAST(i.raw_bulten_tarihi AS VARCHAR), CAST(i.raw_mal_id AS VARCHAR), coalesce(CAST(i.raw_mal_tip_id AS VARCHAR),''), coalesce(i.raw_hal,''))),
        'izmir_hal', NULL, e.geo_id, 'il',
        'PRD_IZH_' || md5(concat_ws('|', CAST(i.raw_mal_id AS VARCHAR), coalesce(CAST(i.raw_mal_tip_id AS VARCHAR),''))),
        concat_ws('/', CAST(i.raw_mal_id AS VARCHAR), CAST(i.raw_mal_tip_id AS VARCHAR)), 'wholesale_avg',
        TRY_CAST(i.raw_ortalama_ucret AS DOUBLE), TRY_CAST(i.raw_asgari_ucret AS DOUBLE), TRY_CAST(i.raw_azami_ucret AS DOUBLE),
        NULL, i.raw_hal, NULL, i.raw_birim, 'TRY', NULL, NULL, NULL, i.raw_guncellenme_tarihi, CAST(i.raw_bulten_tarihi AS VARCHAR),
        'day', 'official_public', 'public', i.source_row_number, i.source_row_hash, 'stg_hal_izmir_gunluk', i.source_path, ?
        FROM s.stg.price_series__stg_hal_izmir_gunluk i LEFT JOIN geo_entity e ON e.level='il' AND e.name_norm = norm(i.raw_il)""", [now])

    # 5) market ürün fiyat geçmişi (il düzeyi)
    load("marketfiyati_history", """INSERT INTO product_price_observation SELECT
        'OPP_MFH_' || md5(concat_ws('|', h.raw_il_geo_id, h.raw_market, h.raw_id, CAST(h.raw_date AS VARCHAR))), 'marketfiyati_history',
        NULL, h.raw_il_geo_id, 'il', 'PRD_MF_' || h.raw_id, h.raw_id, 'market_il_gunluk', TRY_CAST(h.raw_price AS DOUBLE),
        NULL, NULL, NULL, h.raw_market, NULL, 'TL', 'TRY', NULL, NULL, NULL, CAST(h.raw_captured_at AS VARCHAR),
        CAST(h.raw_date AS VARCHAR), 'day', 'web_research', 'internal', h.source_row_number, h.source_row_hash,
        'stg_marketfiyati_fiyat_gecmisi', h.source_path, ? FROM s.stg.price_series__stg_marketfiyati_fiyat_gecmisi h""", [now])

    # kaynak kataloğunda bulunmayan ürün kodları (ör. listeden çıkmış ürünlerin geçmiş fiyatı) için yer tutucu kayıt:
    # ad uydurulmaz (NULL), yalnız kod + hangi gözlemden türediği yazılır — böylece gözlem-ürün bağı kopmaz.
    c.execute("""INSERT INTO product SELECT DISTINCT o.product_id, 'marketfiyati', o.product_code, NULL, NULL, NULL, NULL, NULL, NULL,
        NULL, NULL, min(o.period) OVER (PARTITION BY o.product_id), NULL, 'türetildi:product_price_observation', 'web_research',
        'internal', ? FROM product_price_observation o
        WHERE NOT EXISTS (SELECT 1 FROM product p WHERE p.product_id = o.product_id) AND o.product_id IS NOT NULL""", [now])
    rep["product_yer_tutucu"] = c.execute("SELECT count(*) FROM product WHERE staging_view='türetildi:product_price_observation'").fetchone()[0]
    rep["product_price_observation"] = counts
    rep["toplam_satir"] = c.execute("SELECT count(*) FROM product_price_observation").fetchone()[0]
    rep["muhasebe"] = {
        "marketfiyati_branch": c.execute("SELECT (SELECT count(*) FROM s.stg.price_series__stg_marketfiyati_sube_urun_fiyat) = (SELECT count(*) FROM product_price_observation WHERE source_system='marketfiyati_branch')").fetchone()[0],
        "opet_fuel": c.execute("SELECT (SELECT count(*) FROM s.stg.price_series__stg_opet_ilce_akaryakit_fiyat) = (SELECT count(*) FROM product_price_observation WHERE source_system='opet_fuel')").fetchone()[0],
        "hks_ulusal": c.execute("SELECT (SELECT count(*) FROM s.stg.price_series__stg_hal_ulusal_gunluk) = (SELECT count(*) FROM product_price_observation WHERE source_system='hks_ulusal')").fetchone()[0],
        "izmir_hal": c.execute("SELECT (SELECT count(*) FROM s.stg.price_series__stg_hal_izmir_gunluk) = (SELECT count(*) FROM product_price_observation WHERE source_system='izmir_hal')").fetchone()[0],
        "marketfiyati_history": c.execute("SELECT (SELECT count(*) FROM s.stg.price_series__stg_marketfiyati_fiyat_gecmisi) = (SELECT count(*) FROM product_price_observation WHERE source_system='marketfiyati_history')").fetchone()[0]}
    rep["geo_bagli_oran"] = c.execute("""SELECT source_system, round(100.0*count(geo_id)/count(*), 2) FROM product_price_observation GROUP BY 1""").fetchall()
    rep["poi_bagli"] = c.execute("SELECT count(*) FROM product_price_observation WHERE poi_id IS NOT NULL").fetchone()[0]
    c.execute("CHECKPOINT")
    rep["seconds"] = round(time.time() - t0)
    (OUT / "canonical" / "v1.1" / "build_report_v1_6b.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_6B_PRODUCT_PRICE", **rep,
                            "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    c.close()


if __name__ == "__main__":
    main()
