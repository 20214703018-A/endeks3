#!/usr/bin/env python3
"""
SERVING SNAPSHOT (L1) ÜRETİCİ — kanonik DB'den uygulamanın okuyacağı, okuma için düzenlenmiş, değişmez kopya.

emlak-ai-app docs/DATABASE_PLAN.md ve GEOPROP_DATA_INTEGRATION.md sözleşmesi:
  • yalnız yayındaki veri setleri (urun.* + toplu taşıma), fiziksel tablo olarak; okuma sırasına göre dizilmiş
    (geo → boyut → zaman; DuckDB zone-map'leri tek mahalle/tek seri sorgusunu milisaniyeye indirir)
  • koordinatlı tablolara GEOMETRY + RTREE (+ varsa H3 r7/r9); sınır poligonlarına RTREE
  • çok kaynaklı fiyat gözlemlerinde "ana değer" (ana_deger_v1): kaynak önceliği → en yeni kayıt; tüm kaynaklar ayrıca korunur
  • görünürlük (QDEC_000004): yalnız açık satırlar (gorunurluk_isaretleri boş) ve hassas olmayan sütunlar
  • katalog: srv.katalog.veri_seti / srv.katalog.kolon (uygulama_yayini, geo_anahtari, zaman_anahtari, tur, postgis_kopya, rol)
  • otomatik kontroller (geo_id cografya'da mı, koordinat TR kutusunda mı, anahtar tekrarı) → rapor; hatalı set dışarıda kalır
Kanoniğe YAZMAZ (READ_ONLY). Çıktı: geoprop_serving_<model_version>.duckdb + <aynı>.rapor.json
Kullanım: build_serving_snapshot.py --canonical canon.duckdb --cikti /dizin [--esik-postgis 5000000]
"""
import argparse, hashlib, json, os, re, time, duckdb
from pathlib import Path

TR_KUTU = (25.5, 35.7, 45.0, 42.3)
# veri seti: (kaynak nesne, sıralama, tür, ek notlar)  — sıralama: geo → boyut → zaman (DATABASE_PLAN §4)
VERI_SETLERI = {
    "cografya":            ("urun.cografya", "duzey, geo_id", "sozluk"),
    "mahalle_zeka":        ("urun.mahalle_zeka", "geo_id", "ozet"),
    "ilce_zeka":           ("urun.ilce_zeka", "geo_id", "ozet"),
    "ilce_yeni_kaynaklar": ("urun.ilce_yeni_kaynaklar", "ilce_geo_id", "ozet"),
    "konut_fiyat_gozlem":  ("urun.konut_fiyat_gozlem", "geo_id, kategori, alt_kategori, olcut, gozlem_turu, donem", "seri"),
    "isletme":             ("urun.isletme", "mahalle_geo_id, sektor, kategori", "nokta"),
    "isletme_kaynak":      ("urun.isletme_kaynak", "isletme_id", "gozlem"),
    "gosterge_gozlem":     ("urun.gosterge_gozlem", "geo_id, alan, olcut, kirilim1, kirilim2, donem", "seri"),
    "tuik_seri":           ("urun.tuik_seri", "geo_id, akis_id, seri_id", "sozluk"),
    "tuik_gozlem":         ("urun.tuik_gozlem", "seri_id, donem", "seri"),
    "urun_fiyat_gozlem":   ("urun.urun_fiyat_gozlem", "geo_id, urun_id, kaynak_sistem, donem", "seri"),
    "urun_katalogu":       ("urun.urun_katalogu", "urun_id", "sozluk"),
    "eticaret_sitesi":     ("urun.eticaret_sitesi", "il_adi, ilce_adi, site_id", "sozluk"),
    "menu_kalemi":         ("urun.menu_kalemi", "isletme_id, gozlem_zamani", "gozlem"),
    "teslimat_gozlem":     ("urun.teslimat_gozlem", "isletme_id, gozlem_zamani", "gozlem"),
    "sarj_soketi":         ("urun.sarj_soketi", "isletme_id, istasyon_id", "gozlem"),
    # toplu taşıma (main şeması; görünümleri aynı adlarla yeniden kurulur)
    "transit_feed":        ("main.transit_feed", None, "sozluk"),
    "transit_route":       ("main.transit_route", None, "sozluk"),
    "transit_stop":        ("main.transit_stop", None, "nokta"),
    "transit_route_stop":  ("main.transit_route_stop", None, "gozlem"),
    "transit_service":     ("main.transit_service", None, "sozluk"),
    "transit_trip":        ("main.transit_trip", None, "gozlem"),
    "transit_stop_time":   ("main.transit_stop_time", "stop_uid, departure_sec", "seri"),
    "transit_shape_point": ("main.transit_shape_point", "shape_uid, seq", "gozlem"),
    "transit_ridership_hourly": ("main.transit_ridership_hourly", None, "seri"),
}
TRANSIT_GORUNUMLER = ["v_transit_departure", "v_transit_stop_routes", "v_istanbul_hat_saatlik_yolcu"]
# ana değer: konut fiyatında aynı (geo, kategori, alt_kategori, olcut, donem, gozlem_turu) için kaynak önceliği (küçük = önce)
KAYNAK_ONCELIK = ["stg_price_trend_monthly", "stg_price_summary", "stg_ej_region_index"]
KOORD = [("enlem", "boylam"), ("lat", "lon"), ("merkez_enlem", "merkez_boylam"), ("centroid_lat", "centroid_lon"), ("stop_lat", "stop_lon")]
GEO_KOL = ["geo_id", "mahalle_geo_id", "ilce_geo_id", "il_geo_id"]
ZAMAN_KOL = ["donem", "gozlem_zamani", "kayit_zamani", "observed_at", "son_gorulme", "transition_date"]


def rol(kol, tip, ilk):
    k = kol.lower()
    if ilk and (k.endswith("_id") or k == "id"): return "anahtar"
    if k in GEO_KOL or k == "ust_geo_id": return "geo"
    if k in {a for p in KOORD for a in p}: return "koordinat"
    if k in ZAMAN_KOL or k.endswith("_zamani") or k.endswith("_tarihi"): return "zaman"
    if k in ("birim", "para_birimi"): return "birim"
    if "json" in k or tip.upper() in ("JSON", "STRUCT", "MAP") or tip.endswith("[]"): return "json"
    if tip.upper().startswith(("DOUBLE", "FLOAT", "DECIMAL", "BIGINT", "INTEGER", "HUGEINT", "SMALLINT")): return "olcu"
    if k in ("ad", "unvan", "adres", "aciklama", "hakkinda", "urun_adi", "isletme_adi") or k.endswith("_adi"): return "metin"
    if tip.upper() == "GEOMETRY": return "geometri"
    return "boyut"


def sha256_dosya(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", required=True); ap.add_argument("--cikti", required=True)
    ap.add_argument("--esik-postgis", type=int, default=5_000_000); ap.add_argument("--h3", action="store_true", help="H3 eklentisiyle h3_r7/r9")
    a = ap.parse_args(); os.makedirs(a.cikti, exist_ok=True); t0 = time.time()
    k = duckdb.connect(a.canonical, read_only=True)
    surum = dict(k.execute("SELECT key, value FROM _meta").fetchall()).get("model_version", "bilinmiyor"); k.close()
    hedef = Path(a.cikti) / f"geoprop_serving_{surum}.duckdb"
    if hedef.exists(): hedef.unlink()
    # Kanonik ANA bağlantı (salt okunur): urun görünümleri içeride 'main.x' adlarına başvurur; kanonik ikinci bağlanırsa 'main'
    # yeni dosyayı gösterir ve görünümler bozulur. Yazılacak snapshot 'srv' olarak bağlanır.
    c = duckdb.connect(); c.execute("INSTALL spatial; LOAD spatial; SET preserve_insertion_order=true")
    # Bellek: varsayılan %80 RAM, Codespace kapsayıcısında süreç sonlandırılıyordu (çıkış 143) → sınır + diske taşma
    c.execute(f"SET memory_limit='{os.environ.get('DUCKDB_BELLEK', '8GB')}'; SET temp_directory='{a.cikti}/duckdb_tmp'; SET threads={os.cpu_count() or 4}")
    c.execute(f"ATTACH '{a.canonical}' AS kanonik (READ_ONLY)"); c.execute(f"ATTACH '{hedef}' AS srv"); c.execute("USE kanonik")
    h3 = False
    if a.h3:
        try: c.execute("INSTALL h3 FROM community; LOAD h3"); h3 = True
        except Exception as e: print("H3 yok, yalnız RTREE:", str(e)[:80])
    kanonik_ad = c.execute("SELECT current_database()").fetchone()[0]
    hassas = {(r[0], r[1], r[2]) for r in c.execute("SELECT sema, tablo, kolon FROM main.meta_kolon WHERE hassas").fetchall()}
    aciklama = {(r[0], r[1]): r[2] for r in c.execute("SELECT sema, tablo, aciklama FROM main.meta_tablo").fetchall()}
    c.execute("CREATE SCHEMA srv.katalog")
    c.execute("""CREATE TABLE srv.katalog.veri_seti (ad VARCHAR PRIMARY KEY, kaynak VARCHAR, aciklama VARCHAR, tur VARCHAR, satir BIGINT,
                 geo_anahtari VARCHAR, zaman_anahtari VARCHAR, koordinat VARCHAR, siralama VARCHAR, uygulama_yayini BOOLEAN,
                 postgis_kopya BOOLEAN, sozlesme_surumu VARCHAR, kontrol_durumu VARCHAR, kontrol_notu VARCHAR)""")
    c.execute("CREATE TABLE srv.katalog.kolon (veri_seti VARCHAR, kolon VARCHAR, sira INTEGER, tip VARCHAR, rol VARCHAR, aciklama VARCHAR)")
    rapor = {"model_version": surum, "veri_setleri": {}, "baslangic": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for ad, (kaynak, sira, tur) in VERI_SETLERI.items():
        t1 = time.time(); sema, tablo = kaynak.split(".")
        try:
            kols = c.execute(f"DESCRIBE SELECT * FROM {kaynak}").fetchall()
        except Exception as e:
            rapor["veri_setleri"][ad] = {"durum": "YOK", "not": str(e)[:120]}
            print(f"  !! {ad}: kaynak okunamadı — {str(e)[:120]}", flush=True); continue
        secili = [(n, t) for n, t, *_ in kols if (sema, tablo, n) not in hassas]
        adlar = [n for n, _ in secili]
        gor = "gorunurluk_isaretleri" in adlar
        sec_sql = ", ".join(f'"{n}"' for n in adlar if n != "gorunurluk_isaretleri")
        nerede = "WHERE gorunurluk_isaretleri IS NULL OR len(gorunurluk_isaretleri) = 0" if gor else ""
        geo = next((g for g in GEO_KOL if g in adlar), None); zaman = next((z for z in ZAMAN_KOL if z in adlar), None)
        koord = next(((la, lo) for la, lo in KOORD if la in adlar and lo in adlar), None)
        ek = ""
        if koord:
            la, lo = koord
            ek = f', ST_Point(TRY_CAST("{lo}" AS DOUBLE), TRY_CAST("{la}" AS DOUBLE)) AS geom'
            if h3: ek += f', h3_latlng_to_cell("{la}", "{lo}", 7) AS h3_r7, h3_latlng_to_cell("{la}", "{lo}", 9) AS h3_r9'
        if ad == "cografya":
            ek = ", sinir_poligonu AS geom, ST_XMin(sinir_poligonu) bbox_xmin, ST_YMin(sinir_poligonu) bbox_ymin, ST_XMax(sinir_poligonu) bbox_xmax, ST_YMax(sinir_poligonu) bbox_ymax"
        sirala = f"ORDER BY {sira}" if sira else ""
        if koord and tur == "nokta":
            la, lo = koord   # mekânsal yakınlık sırası: enlem/boylam zone-map'leri 1 km sorgusunda satır gruplarını eler
            sirala = (f'ORDER BY ST_Hilbert(ST_Point(TRY_CAST("{lo}" AS DOUBLE), TRY_CAST("{la}" AS DOUBLE)), '
                      f"ST_Extent(ST_MakeEnvelope({TR_KUTU[0]}, {TR_KUTU[1]}, {TR_KUTU[2]}, {TR_KUTU[3]})))")
            sira = "Hilbert(enlem, boylam)"
        c.execute(f"CREATE TABLE srv.main.{ad} AS SELECT {sec_sql}{ek} FROM {kaynak} {nerede} {sirala}")
        n = c.execute(f"SELECT COUNT(*) FROM srv.main.{ad}").fetchone()[0]
        # kontroller
        notlar = []
        if geo and ad != "cografya":
            kopuk = c.execute(f"SELECT COUNT(*) FROM srv.main.{ad} WHERE {geo} IS NOT NULL AND {geo} NOT IN (SELECT geo_id FROM srv.main.cografya)").fetchone()[0]
            if kopuk: notlar.append(f"{kopuk:,} satırın {geo} değeri cografya'da yok")
        if koord:
            dis = c.execute(f'SELECT COUNT(*) FROM srv.main.{ad} WHERE geom IS NOT NULL AND NOT (ST_X(ST_Centroid(geom)) BETWEEN {TR_KUTU[0]} AND {TR_KUTU[2]} AND ST_Y(ST_Centroid(geom)) BETWEEN {TR_KUTU[1]} AND {TR_KUTU[3]})').fetchone()[0]
            if dis: notlar.append(f"{dis:,} koordinat TR kutusu dışında")
        anahtar = adlar[0]
        if anahtar.endswith("_id") and tur in ("sozluk", "ozet", "nokta"):
            tekrar = n - c.execute(f'SELECT COUNT(DISTINCT "{anahtar}") FROM srv.main.{ad}').fetchone()[0]
            if tekrar: notlar.append(f"{anahtar} {tekrar:,} tekrar")
            else:
                try: c.execute(f'CREATE UNIQUE INDEX ix_{ad}_pk ON srv.main.{ad}("{anahtar}")')
                except Exception as e: notlar.append("pk indeks: " + str(e)[:60])
        if geo and tur in ("ozet", "nokta"):
            try: c.execute(f'CREATE INDEX ix_{ad}_geo ON srv.main.{ad}("{geo}")')
            except Exception: pass
        if ad == "cografya":   # poligonlarda RTREE nokta→mahalle'yi 0,6 ms'ye indiriyor; noktalarda Hilbert sırası daha hızlı
            try: c.execute(f"CREATE INDEX ix_{ad}_rtree ON srv.main.{ad} USING RTREE (geom)")
            except Exception as e: notlar.append("rtree: " + str(e)[:60])
        durum = "UYARI" if notlar else "TAMAM"
        c.execute("INSERT INTO srv.katalog.veri_seti VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            ad, kaynak, aciklama.get((sema, tablo)), tur, n, geo or (f"{koord[0]},{koord[1]}" if koord else None), zaman,
            f"{koord[0]},{koord[1]}" if koord else None, sira, True, n <= a.esik_postgis and tur in ("sozluk", "ozet", "nokta"),
            "1.0", durum, "; ".join(notlar) or None))
        for i, (kn, kt) in enumerate((r[0], r[1]) for r in c.execute(f"DESCRIBE srv.main.{ad}").fetchall()):
            c.execute("INSERT INTO srv.katalog.kolon VALUES (?,?,?,?,?,?)", (ad, kn, i, kt, rol(kn, kt, i == 0), None))
        rapor["veri_setleri"][ad] = {"satir": n, "sure_sn": round(time.time() - t1, 1), "durum": durum, "notlar": notlar,
                                     "dislanan_hassas_kolon": [x[2] for x in hassas if x[:2] == (sema, tablo)]}
        print(f"  {ad:26s} {n:>12,} satır · {time.time()-t1:5.1f} sn · {durum} {'; '.join(notlar)[:90]}", flush=True)
    # ana değer (ana_deger_v1): konut fiyatı
    t1 = time.time()
    oncelik = " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(KAYNAK_ONCELIK))
    c.execute(f"""CREATE TABLE srv.main.konut_fiyat_ozet AS
        SELECT geo_id, duzey, kategori, alt_kategori, olcut, donem, gozlem_turu,
               arg_min(deger, sira_anahtari) AS ana_deger, arg_min(kaynak_tablo, sira_anahtari) AS ana_kaynak,
               any_value(birim) AS birim, COUNT(*) AS kaynak_sayisi, COUNT(DISTINCT deger) AS farkli_deger_sayisi,
               CASE WHEN avg(deger) <> 0 THEN (max(deger) - min(deger)) / abs(avg(deger)) END AS kaynaklar_arasi_sapma,
               'ana_deger_v1' AS yontem
        FROM (SELECT *, (CASE kaynak_tablo {oncelik} ELSE 99 END) * 1e13 - COALESCE(epoch(TRY_CAST(kayit_zamani AS TIMESTAMP)), 0) AS sira_anahtari
              FROM srv.main.konut_fiyat_gozlem)
        GROUP BY ALL ORDER BY geo_id, kategori, alt_kategori, olcut, gozlem_turu, donem""")
    n = c.execute("SELECT COUNT(*) FROM srv.main.konut_fiyat_ozet").fetchone()[0]
    c.execute("INSERT INTO srv.katalog.veri_seti VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "konut_fiyat_ozet", "türetim: konut_fiyat_gozlem", "Her (bölge, kategori, ölçüt, dönem, gözlem türü) için tek 'ana değer' "
        "(kaynak önceliği → en yeni kayıt); kaynak sayısı ve kaynaklar arası sapma. Ham kaynaklar konut_fiyat_gozlem'de.",
        "ozet", n, "geo_id", "donem", None, "geo_id, kategori, alt_kategori, olcut, gozlem_turu, donem", True, n <= a.esik_postgis,
        "1.0", "TAMAM", None))
    for i, (kn, kt) in enumerate((r[0], r[1]) for r in c.execute("DESCRIBE srv.main.konut_fiyat_ozet").fetchall()):
        c.execute("INSERT INTO srv.katalog.kolon VALUES (?,?,?,?,?,?)", ("konut_fiyat_ozet", kn, i, kt, rol(kn, kt, False), None))
    rapor["veri_setleri"]["konut_fiyat_ozet"] = {"satir": n, "sure_sn": round(time.time() - t1, 1), "durum": "TAMAM"}
    print(f"  {'konut_fiyat_ozet':26s} {n:>12,} satır · {time.time()-t1:5.1f} sn · ana değer", flush=True)
    for ad_, sql, acik, sira_ in [
        ("konut_fiyat_son", """SELECT geo_id, duzey, kategori, alt_kategori, olcut, arg_max(ana_deger, donem) AS deger,
                max(donem) AS son_donem, arg_max(birim, donem) AS birim, arg_max(ana_kaynak, donem) AS kaynak
                FROM srv.main.konut_fiyat_ozet WHERE gozlem_turu = 'measured' GROUP BY ALL ORDER BY kategori, olcut, duzey, geo_id""",
         "Her bölge × kategori × ölçüt için son GÖZLENEN ana değer (tahmin dahil değil).", "kategori, olcut, duzey, geo_id"),
        ("konut_fiyat_ozet_24ay", """SELECT * FROM srv.main.konut_fiyat_ozet
                WHERE olcut IN ('sale_price_m2', 'rent_price_m2', 'avg_price')
                  AND donem >= strftime(date_trunc('month', current_date) - INTERVAL 24 MONTH, '%Y-%m')
                ORDER BY geo_id, kategori, olcut, gozlem_turu, donem""",
         "Son 24 ay + projeksiyon, ana ölçütler (m² satış/kira, ortalama fiyat): uygulama veritabanına (PostGIS) kopyalanan özet.",
         "geo_id, kategori, olcut, gozlem_turu, donem")]:
        t1 = time.time(); c.execute(f"CREATE TABLE srv.main.{ad_} AS {sql}")
        n = c.execute(f"SELECT COUNT(*) FROM srv.main.{ad_}").fetchone()[0]
        c.execute("INSERT INTO srv.katalog.veri_seti VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            ad_, "türetim: konut_fiyat_ozet", acik, "ozet", n, "geo_id", "son_donem" if ad_.endswith("son") else "donem", None,
            sira_, True, n <= a.esik_postgis, "1.0", "TAMAM", None))
        for i, (kn, kt) in enumerate((r[0], r[1]) for r in c.execute(f"DESCRIBE srv.main.{ad_}").fetchall()):
            c.execute("INSERT INTO srv.katalog.kolon VALUES (?,?,?,?,?,?)", (ad_, kn, i, kt, rol(kn, kt, False), None))
        rapor["veri_setleri"][ad_] = {"satir": n, "sure_sn": round(time.time() - t1, 1), "durum": "TAMAM"}
        print(f"  {ad_:26s} {n:>12,} satır · {time.time()-t1:5.1f} sn", flush=True)
    # toplu taşıma görünümleri: kanonikteki tanımla aynı adlarla
    for ad_, params, tanim in c.execute("""SELECT function_name, parameters, macro_definition FROM duckdb_functions()
                                            WHERE function_type='macro' AND database_name=current_database()""").fetchall():
        try: c.execute(f"CREATE OR REPLACE MACRO srv.main.{ad_}({', '.join(params)}) AS {tanim}")
        except Exception as e: rapor.setdefault("makro_hatalari", {})[ad_] = str(e)[:150]
    for v in TRANSIT_GORUNUMLER:
        try:
            sql = c.execute(f"SELECT sql FROM duckdb_views() WHERE database_name=current_database() AND view_name='{v}'").fetchone()[0]
            c.execute("USE srv"); c.execute(sql)
        except Exception as e:
            rapor.setdefault("gorunum_hatalari", {})[v] = str(e)[:200]
            print(f"  !! görünüm {v}: {str(e)[:160]}", flush=True)
        finally:
            c.execute(f"USE {kanonik_ad}")
    c.execute("CREATE TABLE srv.main._meta AS SELECT * FROM kanonik.main._meta")
    c.execute("INSERT INTO srv.main._meta VALUES ('serving_built_at', ?), ('serving_builder', 'build_serving_snapshot.py v1'), ('kaynak_kanonik', ?)",
              (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), os.path.basename(a.canonical)))
    c.execute(f"USE {kanonik_ad}"); c.execute("CHECKPOINT srv"); c.execute("DETACH srv"); c.close()
    rapor["boyut_bayt"] = hedef.stat().st_size; rapor["sha256"] = sha256_dosya(hedef); rapor["sure_sn"] = round(time.time() - t0)
    json.dump(rapor, open(str(hedef).replace(".duckdb", ".rapor.json"), "w"), ensure_ascii=False, indent=1)
    print(f"bitti: {hedef} · {rapor['boyut_bayt']/1e9:.2f} GB · sha256 {rapor['sha256'][:16]}… · {rapor['sure_sn']} sn")


if __name__ == "__main__":
    main()
