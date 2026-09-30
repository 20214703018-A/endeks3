#!/usr/bin/env python3
"""
PHASE 4 — CANONICAL v1.8: Toplu taşıma tarife ambarı (Türkiye geneli).
Kaynak: staging/v1.0.0/mobility_logistics/transit_v1/*.parquet (tools/build_toplu_tasima_v1.py çıktısı)
  transit_feed ........ kaynak (besleme) başına bir satır: il, işletme, ham dosya, takvim bitişi, süresi dolmuş mu, sorgu rolü
  transit_route ....... hat (kısa kod, uzun ad, tür: otobüs/metro/tramvay/vapur/...)
  transit_stop ........ durak + koordinat + mekânsal eşleştirmeyle il / ilçe / mahalle geo_id (geo_entity poligonları)
  transit_service ..... çalışma günleri (Pzt..Paz) ve geçerlilik aralığı
  transit_trip ........ tek tek seferler
  transit_stop_time ... sefer × durak × varış/kalkış (saniye; time_source ile saatin kökeni)
  transit_route_stop .. hat/yön başına sıralı durak dizisi (OSM dahil; saat bilgisi olmayan hatlar da burada)
  transit_shape_point . güzergâh çizgisi noktaları
Görünümler:
  v_transit_departure . durak × hat × kalkış saati (HH:MM) — "buradan hangi hat, saat kaçta geçer" sorgusunun tabanı
  v_transit_stop_routes durak × hat (saatsiz; OSM yedeği dahil)
Satırlar değiştirilmeden taşınır; yalnız mekânsal geo_id ekleri ve metin saat sütunları eklenir.
"""
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path

import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
SRC = OUT / "staging" / "v1.0.0" / "mobility_logistics" / "transit_v1"
MODEL_VERSION = "canonical_v1.8"
MIN_FREE = 1_200_000_000
TABLES = ["transit_feed", "transit_route", "transit_stop", "transit_service", "transit_trip", "transit_stop_time",
          "transit_route_stop", "transit_shape_point"]


def free_bytes(p):
    st = os.statvfs(p)
    return st.f_bavail * st.f_frsize


def main():
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if free_bytes(OUT) < MIN_FREE:
        raise SystemExit("DİSK KORUMASI")
    c = duckdb.connect(str(CAN))
    c.execute("LOAD spatial")
    c.execute("SET memory_limit='2GB'; SET threads=2; SET preserve_insertion_order=false")
    (OUT / "tmp" / "canon18").mkdir(parents=True, exist_ok=True)
    c.execute(f"SET temp_directory='{OUT}/tmp/canon18'")
    base = c.execute("SELECT value FROM _meta WHERE key='model_version'").fetchone()[0]
    if base not in ("canonical_v1.7", "canonical_v1.8"):
        raise SystemExit(f"taban sürüm beklenmiyor: {base}")
    rep = {"built_at": now, "stage": "v1.8", "base": base}

    c.execute("DROP VIEW IF EXISTS v_transit_departure")
    c.execute("DROP VIEW IF EXISTS v_transit_stop_routes")
    for tb in TABLES:
        order = " ORDER BY stop_uid, departure_sec" if tb == "transit_stop_time" else ""  # durak sorgusunda blok atlama (zonemap)
        c.execute(f"CREATE OR REPLACE TABLE {tb} AS SELECT * FROM '{SRC / (tb + '.parquet')}'{order}")
        rep[tb] = c.execute(f"SELECT count(*) FROM {tb}").fetchone()[0]
        rep[tb + "_kaynak_esit"] = c.execute(f"SELECT (SELECT count(*) FROM '{SRC / (tb + '.parquet')}') = {rep[tb]}").fetchone()[0]
    rp = SRC / "transit_ridership_hourly.parquet"
    if rp.exists():
        c.execute(f"CREATE OR REPLACE TABLE transit_ridership_hourly AS SELECT * FROM '{rp}' ORDER BY line_name, transition_date, transition_hour")
        rep["transit_ridership_hourly"] = c.execute("SELECT count(*) FROM transit_ridership_hourly").fetchone()[0]
    print("  tablolar yüklendi", flush=True)

    # durak → mahalle / ilçe / il (nokta poligon içinde); poligon dışı kalan (kıyı/iskele) duraklar için en yakın mahalle ≤ 300 m
    c.execute("ALTER TABLE transit_stop ADD COLUMN IF NOT EXISTS geometry GEOMETRY")
    c.execute("UPDATE transit_stop SET geometry = ST_Point(lon, lat) WHERE lat IS NOT NULL AND lon IS NOT NULL")
    for col in ("mahalle_geo_id", "ilce_geo_id", "il_geo_id", "geo_method", "il_mekansal", "ilce_mekansal", "mahalle_mekansal"):
        c.execute(f"ALTER TABLE transit_stop ADD COLUMN IF NOT EXISTS {col} VARCHAR")
    c.execute("""CREATE OR REPLACE TEMP TABLE m AS SELECT geo_id, parent_geo_id, il_geo_id, geometry, bbox_xmin, bbox_xmax, bbox_ymin, bbox_ymax
        FROM geo_entity WHERE level='mahalle' AND geometry IS NOT NULL""")
    c.execute("""CREATE OR REPLACE TEMP TABLE sm AS
        SELECT s.stop_uid, any_value(m.geo_id) mah, any_value(m.parent_geo_id) ilce, any_value(m.il_geo_id) il
        FROM transit_stop s JOIN m ON ST_Intersects(m.geometry, s.geometry)
        WHERE s.geometry IS NOT NULL GROUP BY 1""")
    c.execute("""UPDATE transit_stop t SET mahalle_geo_id = sm.mah, ilce_geo_id = sm.ilce, il_geo_id = sm.il, geo_method = 'poligon_icinde'
        FROM sm WHERE sm.stop_uid = t.stop_uid""")
    c.execute("""CREATE OR REPLACE TEMP TABLE nn AS
        SELECT s.stop_uid, arg_min(m.geo_id, ST_Distance_Sphere(ST_Centroid(m.geometry), s.geometry)) mah
        FROM transit_stop s JOIN m ON s.lon BETWEEN m.bbox_xmin - 0.004 AND m.bbox_xmax + 0.004
                                   AND s.lat BETWEEN m.bbox_ymin - 0.004 AND m.bbox_ymax + 0.004
        WHERE s.geometry IS NOT NULL AND s.mahalle_geo_id IS NULL AND ST_Distance_Sphere(ST_Centroid(m.geometry), s.geometry) < 5000
          AND ST_Distance(m.geometry, s.geometry) < 0.003
        GROUP BY 1""")
    c.execute("""UPDATE transit_stop t SET mahalle_geo_id = g.geo_id, ilce_geo_id = g.parent_geo_id, il_geo_id = g.il_geo_id,
        geo_method = 'en_yakin_mahalle_~300m' FROM nn JOIN geo_entity g ON g.geo_id = nn.mah WHERE nn.stop_uid = t.stop_uid""")
    # durağın fiziken bulunduğu il/ilçe/mahalle adı ('il' sütunu = işletmenin ili; ör. İETT'nin Gebze durakları il=İstanbul, il_mekansal=Kocaeli)
    c.execute("""UPDATE transit_stop t SET il_mekansal = gi.name, ilce_mekansal = gc.name, mahalle_mekansal = gm.name
        FROM geo_entity gm, geo_entity gc, geo_entity gi
        WHERE gm.geo_id = t.mahalle_geo_id AND gc.geo_id = t.ilce_geo_id AND gi.geo_id = t.il_geo_id""")
    # il adı boş olan kaynaklar (OSM, FlixBus) için mekânsal il
    c.execute("""UPDATE transit_stop t SET il = g.name FROM geo_entity g
        WHERE g.geo_id = t.il_geo_id AND (t.il IS NULL OR t.il = 'Türkiye')""")
    c.execute("""UPDATE transit_route r SET il = x.il FROM (
            SELECT rs.route_uid, mode(s.il) il FROM transit_route_stop rs JOIN transit_stop s USING (stop_uid)
            WHERE s.il IS NOT NULL GROUP BY 1) x
        WHERE x.route_uid = r.route_uid AND (r.il IS NULL OR r.il = 'Türkiye') AND r.feed_id <> 'flixbus_tr'""")
    rep["durak_geo"] = dict(c.execute("SELECT coalesce(geo_method, CASE WHEN lat IS NULL THEN 'koordinat_yok' ELSE 'turkiye_disi_veya_eslesmedi' END), count(*) FROM transit_stop GROUP BY 1").fetchall())
    rep["durak_il_tutarsiz"] = c.execute("""SELECT count(*) FROM transit_stop s JOIN geo_entity g ON g.geo_id = s.il_geo_id
        WHERE s.feed_id NOT IN ('osm_routes', 'flixbus_tr') AND s.il IS NOT NULL
          AND lower(replace(replace(g.name,'İ','i'),'I','ı')) <> lower(replace(replace(s.il,'İ','i'),'I','ı'))""").fetchone()[0]
    print("  mekânsal eşleştirme bitti", flush=True)

    c.execute("CREATE OR REPLACE MACRO hhmm(sec) AS CASE WHEN sec IS NULL THEN NULL ELSE lpad((sec // 3600)::VARCHAR, 2, '0') || ':' || lpad(((sec % 3600) // 60)::VARCHAR, 2, '0') END")
    c.execute("""CREATE VIEW v_transit_departure AS
        SELECT s.stop_uid, s.stop_name, s.lat, s.lon, s.il AS isletme_il, s.il_mekansal, s.ilce_mekansal, s.mahalle_mekansal,
               s.il_geo_id, s.ilce_geo_id, s.mahalle_geo_id,
               r.route_uid, r.route_short_name, r.route_long_name, r.mode, r.agency_name,
               t.trip_uid, t.direction_id, t.headsign, st.stop_sequence,
               st.departure_sec, hhmm(st.departure_sec) AS departure_hhmm, st.arrival_sec, hhmm(st.arrival_sec) AS arrival_hhmm,
               (st.departure_sec >= 86400) AS gece_yarisi_sonrasi, st.time_source,
               (st.time_source LIKE '%interpolated%') AS saat_tahmini,
               CASE WHEN st.time_source = 'gtfs_interpolated' THEN 'sira_dogrusal_v1 (ilk/son resmî saat arası; bkz. reports/TOPLU_TASIMA_YONTEM.md)'
                    WHEN st.time_source = 'ego_duration_interpolated' THEN 'sure_dogrusal_v1 (ilk kalkış + EGO toplam süresi; bkz. reports/TOPLU_TASIMA_YONTEM.md)'
               END AS tahmin_yontemi,
               sv.service_uid, sv.service_label, sv.monday, sv.tuesday, sv.wednesday, sv.thursday, sv.friday, sv.saturday, sv.sunday,
               sv.start_date, sv.end_date, f.feed_id, f.source_kind, f.query_role, f.is_expired, f.calendar_end
        FROM transit_stop_time st
        JOIN transit_trip t USING (trip_uid)
        JOIN transit_route r ON r.route_uid = t.route_uid
        JOIN transit_stop s ON s.stop_uid = st.stop_uid
        LEFT JOIN transit_service sv ON sv.service_uid = t.service_uid
        JOIN transit_feed f ON f.feed_id = t.feed_id
        WHERE t.trip_origin <> 'gtfs_frequency_template'""")
    c.execute("""CREATE VIEW v_transit_stop_routes AS
        SELECT DISTINCT s.stop_uid, s.stop_name, s.lat, s.lon, s.il AS isletme_il, s.il_mekansal, s.ilce_mekansal, s.mahalle_mekansal,
               s.il_geo_id, s.ilce_geo_id, s.mahalle_geo_id,
               r.route_uid, r.route_short_name, r.route_long_name, r.mode, r.agency_name, rs.direction_id,
               f.feed_id, f.source_kind, f.query_role, f.is_expired
        FROM transit_route_stop rs JOIN transit_stop s USING (stop_uid) JOIN transit_route r ON r.route_uid = rs.route_uid
        JOIN transit_feed f ON f.feed_id = r.feed_id""")

    # İstanbul: hat × saat yolcu (İBB kart geçişleri) ve aynı hattın o saatteki planlı sefer sayısı (İETT tarifesi, hafta içi).
    # sefer_basina_ortalama_yolcu TÜRETİLMİŞ bir orandır (yolcu / planlı sefer); tarife 2026, yolcu verisi 2024 — dönem farkı sütunda belirtilir.
    if rp.exists():
        c.execute("DROP VIEW IF EXISTS v_istanbul_hat_saatlik_yolcu")
        c.execute("""CREATE VIEW v_istanbul_hat_saatlik_yolcu AS
            WITH y AS (SELECT line_name, any_value(line_long_name) line_long_name, any_value(road_type) road_type, transition_date, transition_hour,
                              sum(number_of_passage) gecis, sum(number_of_passenger) yolcu
                       FROM transit_ridership_hourly GROUP BY line_name, transition_date, transition_hour),
                 p AS (SELECT r.route_short_name, (st.departure_sec // 3600) % 24 AS saat, count(DISTINCT t.trip_uid) planli_sefer
                       FROM transit_trip t JOIN transit_route r ON r.route_uid = t.route_uid
                       JOIN transit_service sv ON sv.service_uid = t.service_uid
                       JOIN transit_stop_time st ON st.trip_uid = t.trip_uid AND st.stop_sequence = 1
                       WHERE t.feed_id = 'istanbul_iett' AND sv.monday = 1 GROUP BY 1, 2)
            SELECT y.*, isodow(y.transition_date) <= 5 AS hafta_ici, p.planli_sefer AS planli_sefer_hafta_ici_2026,
                   CASE WHEN isodow(y.transition_date) <= 5 AND p.planli_sefer > 0 THEN round(y.yolcu / p.planli_sefer, 1) END
                       AS sefer_basina_ortalama_yolcu_turetilmis,
                   'yolcu: İBB 2024 kart geçişi; sefer: İETT GTFS 2026 tarifesi' AS donem_notu
            FROM y LEFT JOIN p ON p.route_short_name = y.line_name AND p.saat = y.transition_hour""")
    rep["il_kapsama"] = c.execute("""SELECT r.il,
            count(DISTINCT r.route_uid) FILTER (WHERE f.query_role <> 'yedek_saatsiz') AS tarifeli_hat,
            count(DISTINCT r.route_uid) FILTER (WHERE f.query_role = 'yedek_saatsiz') AS osm_saatsiz_hat
        FROM transit_route r JOIN transit_feed f USING (feed_id) WHERE r.il IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 3 DESC""").fetchall()
    c.execute("UPDATE _meta SET value=? WHERE key='model_version'", [MODEL_VERSION])
    c.execute("DELETE FROM _meta WHERE key IN ('built_at_v1_8','code_hash_v1_8')")
    c.execute("INSERT INTO _meta VALUES ('built_at_v1_8', ?), ('code_hash_v1_8', ?)",
              [now, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
    c.execute("CHECKPOINT")
    c.close()
    rep["seconds"] = round(time.time() - t0)
    rep["free_disk_gb"] = round(free_bytes(OUT) / 1e9, 1)
    rep["db_gb"] = round(CAN.stat().st_size / 1e9, 2)
    (OUT / "canonical" / "v1.1" / "build_report_v1_8.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "CANONICAL_V1_8_TOPLU_TASIMA",
                            **{k: v for k, v in rep.items() if k != "il_kapsama"}}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
