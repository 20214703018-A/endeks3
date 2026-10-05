#!/usr/bin/env python3
"""
HIZ ÖLÇÜMÜ — uygulamanın tipik sorgularını bir veritabanında (kanonik ya da serving snapshot) ölçer.
Hedefler: emlak-ai-app docs/DATABASE_PLAN.md §1 / §8. Her sorgu 1 ısınma + 7 ölçüm; medyan ve p95 (ms).
Kullanım: hiz_olcum.py <db.duckdb> [--sema kanonik|serving] [--json cikti.json]
"""
import argparse, json, statistics, time, duckdb

SORGULAR = {
  "kanonik": {
    "mahalle_fiyat_serisi": ("SELECT * FROM urun.konut_fiyat_gozlem WHERE geo_id = ? ORDER BY 1", "mah", 5),
    "tum_mahalle_son_m2": ("SELECT geo_id, max(parsed_value) FROM main.price_observation WHERE period = '2026-08' GROUP BY 1", None, 30),
    "nokta_mahalle": ("SELECT geo_id FROM main.geo_entity WHERE level='mahalle' AND ? BETWEEN bbox_xmin AND bbox_xmax AND ? BETWEEN bbox_ymin AND bbox_ymax AND ST_Contains(geometry, ST_Point(?, ?))", "nokta", 5),
    "yakin_isletme_1km": ("SELECT poi_id, name FROM main.poi WHERE lat BETWEEN ?-0.009 AND ?+0.009 AND lon BETWEEN ?-0.012 AND ?+0.012 AND ST_Distance_Sphere(ST_Point(lon,lat), ST_Point(?, ?)) < 1000", "yakin", 15),
    "mahalle_profili": ("SELECT * FROM urun.mahalle_zeka WHERE geo_id = ?", "mah", 3),
  },
}
SORGULAR["serving"] = {   # build_serving_snapshot.py çıktısı: sıralı tablolar, RTREE, ana değer, PK indeksleri
    "mahalle_fiyat_serisi": ("SELECT * FROM main.konut_fiyat_ozet WHERE geo_id = ?", "mah", 5),
    "mahalle_ham_fiyat_tum_kaynak": ("SELECT * FROM main.konut_fiyat_gozlem WHERE geo_id = ?", "mah", 10),
    "tum_mahalle_son_m2": ("SELECT geo_id, deger FROM main.konut_fiyat_son WHERE kategori = 'konut' AND olcut = 'sale_price_m2' AND duzey = 'mahalle'", None, 30),
    "nokta_mahalle": ("SELECT geo_id FROM main.cografya WHERE duzey = 'mahalle' AND ST_Intersects(geom, ST_Point(?, ?))", "nokta2", 5),
    "yakin_isletme_1km": ("SELECT isletme_id, ad FROM main.isletme WHERE enlem BETWEEN ?-0.009 AND ?+0.009 AND boylam BETWEEN ?-0.012 AND ?+0.012 AND ST_Distance_Sphere(geom, ST_Point(?, ?)) < 1000", "yakin", 15),
    "mahalle_profili": ("SELECT * FROM main.mahalle_zeka WHERE geo_id = ?", "mah", 3),
}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("db"); ap.add_argument("--sema", default="kanonik"); ap.add_argument("--json")
    a = ap.parse_args()
    c = duckdb.connect(a.db, read_only=True); c.execute("LOAD spatial")
    if a.sema == "serving":
        mah = c.execute("SELECT geo_id FROM main.konut_fiyat_ozet WHERE duzey='mahalle' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1").fetchone()[0]
    else:
        mah = c.execute("SELECT geo_id FROM main.geo_entity WHERE level='mahalle' AND geo_id IN (SELECT geo_id FROM main.price_observation LIMIT 100000) LIMIT 1").fetchone()[0]
    lon, lat = 29.0256, 40.9903   # Kadıköy, Caferağa civarı
    param = {"mah": [mah], "nokta": [lon, lat, lon, lat], "yakin": [lat, lat, lon, lon, lon, lat], "nokta2": [lon, lat],
             "yakin2": [lon, lat, lon, lat, lon, lat], None: []}
    sonuc = {}
    for ad, (sql, p, hedef) in SORGULAR[a.sema].items():
        try:
            c.execute(sql, param[p]).fetchall()                       # ısınma
            sure = []
            for _ in range(7):
                t = time.perf_counter(); n = len(c.execute(sql, param[p]).fetchall()); sure.append((time.perf_counter() - t) * 1000)
            sonuc[ad] = {"medyan_ms": round(statistics.median(sure), 1), "p95_ms": round(sorted(sure)[-1], 1), "hedef_ms": hedef, "satir": n}
        except Exception as e:
            sonuc[ad] = {"hata": str(e)[:150]}
        r = sonuc[ad]
        print(f"{ad:24s} " + (f"medyan {r['medyan_ms']:>7.1f} ms · en kötü {r['p95_ms']:>7.1f} ms · hedef {r['hedef_ms']} ms · {r['satir']} satır · "
                               f"{'✅' if r['medyan_ms'] <= r['hedef_ms'] else '❌'}" if "hata" not in r else "HATA " + r["hata"]))
    if a.json: json.dump(sonuc, open(a.json, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
