#!/usr/bin/env python3
"""
POI KATEGORİ v3 — çözülemeyen Google işletmeleri için ÇAPRAZ KAYNAK taraması (kullanıcı talimatı 2026-09-20).
Hedef: canonical poi'de primary_source='google', raw_category='Ticari Mekan', predicted_category IS NULL (88.976).
Kanıt sırası (yüksekten düşüğe; çelişkide üstteki kazanır, çelişki bayraklanır):
  1. PLACE_ID   : restoran_ve_kafe_menuleri.isletme_tarihsel (google_place_id birebir) → ana_kategori
  2. OSM_SPATIAL: OSM tüm POI (623 K) ≤100 m + ad benzerliği ≥0.85 (JW) veya normalize ad eşit → alt_kategori
  3. CHAIN      : zincir_markalar (≤100 m + ad ≥0.85) → kategori
  4. DISTRICT_NAME: piyasa_verileri__poi_noktalari / 08_ilce_onemli_noktalar (aynı ilçe, normalize ad birebir, tek kategori) → alt_kategori
  5. NAME_MODEL_V3: TF-IDF karakter n-gram + SGD; eğitim = v2 kural etiketli Google (238 K) + OSM adlı POI (623 K, eşlenmiş) + restoran ana_kategori (38 K); p ≥ 0.85
Çıktı: mappings/poi_category_predictions_v3.parquet, mappings/poi_category_v3_config.json, validation/poi_category_v3_report.md, validation/golden/poi_category_golden_review_v3.html
Kanonik güncellemesi ayrı adımdır (v1.5); bu betik hiçbir tabloyu değiştirmez.
"""
import json, random, html, collections, datetime as dt, hashlib
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
V = "POI_CATEGORY_V3"; random.seed(20260920)

RESTORAN = {"Kahve Dükkanı (3. Nesil / Roastery)": "3. Nesil Kahveci", "Restoran & Geleneksel": "Restoran & Lokanta", "Dönerci": "Dönerci", "Meyhane, Pub & Bar": "Bar & Lounge", "Kebapçı & Ocakbaşı": "Kebapçı & Ocakbaşı",
            "Pide & Lahmacun Salonu": "Pide & Lahmacun", "Kasap & Et Galerisi": "Kasap", "Geleneksel Ev Yemekleri & Lokanta": "Restoran & Lokanta", "Kafe & Çay Ocağı": "Çay Ocağı & Kahvehane", "Balık & Deniz Ürünleri Restoranı": "Balık & Deniz Ürünleri",
            "Hamburgerci & Fast Food": "Hamburgerci & Fast Food", "Pizzacı": "Hamburgerci & Fast Food", "Pastane & Fırın & Börekçi": "Pastane & Fırın & Börekçi", "Tostçu & Büfe": "Tost & Büfe", "Steakhouse & Et Restoranı": "Restoran & Lokanta",
            "Çorbacı": "Restoran & Lokanta", "Kokoreççi & Sakatatçı": "Kokoreççi & Sakatatçı", "Tatlıcı & Baklavacı": "Tatlıcı & Baklavacı", "Uzakdoğu & Asya Mutfağı": "Restoran & Lokanta", "Çiğ Köfteci": "Çiğ Köfteci"}
OSM = {"magaza": "Mağaza (Genel Perakende)", "market": "Süpermarket & Market", "eczane": "Eczane", "restoran": "Restoran & Lokanta", "kafe": "3. Nesil Kahveci", "fast_food": "Hamburgerci & Fast Food", "bar": "Bar & Lounge", "firin": "Pastane & Fırın & Börekçi",
       "kuafor_guzellik": "Kuaför & Berber", "otel": "Konaklama", "benzin_istasyonu": "Akaryakıt & Otopark", "otopark": "Akaryakıt & Otopark", "banka": "Finans & Sigorta", "atm": "Finans & Sigorta", "ofis": "Şirket & Ofis (sektörü belirsiz)", "zanaat": "Fabrika & Üretim",
       "okul": "Eğitim & Kurs", "ilkokul": "Eğitim & Kurs", "lise": "Eğitim & Kurs", "ortaokul": "Eğitim & Kurs", "anaokulu": "Eğitim & Kurs", "universite": "Eğitim & Kurs", "kutuphane": "Kamu & Kurum", "ozel_egitim": "Eğitim & Kurs",
       "hastane": "Klinik & Muayenehane", "klinik": "Klinik & Muayenehane", "saglik_ocagi": "Klinik & Muayenehane", "veteriner": "Klinik & Muayenehane", "eczane_disi_saglik": "Sağlık Kabini & Eczane",
       "cami": "Kamu & Dini Tesis", "ibadethane": "Kamu & Dini Tesis", "belediye": "Kamu & Kurum", "karakol": "Kamu & Kurum", "postane": "Kamu & Kurum", "adliye": "Kamu & Kurum", "itfaiye": "Kamu & Kurum",
       "park": "Park & Bahçe & Rekreasyon", "muze": "Müze & Tarihi Yer & Turizm", "tiyatro": "Spor & Eğlence", "sinema": "Spor & Eğlence", "spor_salonu": "Spor & Eğlence", "saha": "Spor & Eğlence", "yuzme_havuzu": "Spor & Eğlence", "stadyum": "Spor & Eğlence",
       "avm": "AVM & Alışveriş Merkezi", "pazar": "Pazar Yeri & Çarşı", "hal": "Pazar Yeri & Çarşı", "tekel": "Tekel Bayii", "toptanci": "Toptan & Ticaret", "fabrika": "Fabrika & Üretim", "sanayi_alani": "Fabrika & Üretim", "sanayi_sitesi": "Fabrika & Üretim", "osb": "Fabrika & Üretim"}
CHAIN = {"ZINCIR_MARKET": "Süpermarket & Market", "ATM": "Finans & Sigorta", "BANKA": "Finans & Sigorta", "PERAKENDE_MAGAZA": "Mağaza (Genel Perakende)", "RESTORAN_ZINCIRI": "Restoran & Lokanta", "KAHVE_ZINCIRI": "3. Nesil Kahveci"}
PIYASA = {"Kafe": "3. Nesil Kahveci", "Fırın/Pastane": "Pastane & Fırın & Börekçi", "Eczane": "Eczane", "Lokanta/Restoran": "Restoran & Lokanta", "Zincir Marketler": "Süpermarket & Market", "Fast Food": "Hamburgerci & Fast Food", "Bar": "Bar & Lounge",
          "Lokanta/Deniz Ürünleri": "Balık & Deniz Ürünleri", "Lokanta/Yöresel Yemekler": "Restoran & Lokanta", "Pizza": "Hamburgerci & Fast Food", "Spor Merkezi": "Spor & Eğlence", "Spor Salonu": "Spor & Eğlence", "Spor Kulübü": "Spor & Eğlence", "Spor Tesisi": "Spor & Eğlence",
          "Veteriner": "Klinik & Muayenehane", "Hastane": "Klinik & Muayenehane", "Meyhane": "Bar & Lounge", "Spa Merkezi": "Güzellik & Kozmetik", "Müze": "Müze & Tarihi Yer & Turizm", "Lokanta/Sakatat": "Kokoreççi & Sakatatçı",
          "Kolej": "Eğitim & Kurs", "İlkokul": "Eğitim & Kurs", "Lise": "Eğitim & Kurs", "Ortaokul": "Eğitim & Kurs", "Okul Öncesi": "Eğitim & Kurs", "Yüksekokul/Akademi": "Eğitim & Kurs", "Çarşı ve Pasajlar": "Pazar Yeri & Çarşı", "Cami": "Kamu & Dini Tesis", "Kütüphane": "Kamu & Kurum", "Pazar Alanı": "Pazar Yeri & Çarşı"}


def main():
    t0 = dt.datetime.now(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/cat3'")
    c.execute(f"ATTACH '{CAN}' AS k (READ_ONLY)"); c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    c.execute("CREATE MACRO norm(x) AS trim(regexp_replace(regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9 ]+', ' ', 'g'), ' +', ' ', 'g'))")
    sector = {r["category"]: r["sector"] for r in json.load(open(OUT / "mappings" / "poi_taxonomy_v2.json"))["rules_in_order"]}
    sector.update({"Kasap": "Gıda Perakende", "Eczane": "Sağlık", "Konaklama": "Hizmet", "Kamu & Kurum": "Hizmet", "Kamu & Dini Tesis": "Hizmet"})
    for name, d in (("m_rest", RESTORAN), ("m_osm", OSM), ("m_chain", CHAIN), ("m_piy", PIYASA)):
        c.execute(f"CREATE TABLE {name} (k VARCHAR, cat VARCHAR)"); c.executemany(f"INSERT INTO {name} VALUES (?,?)", list(d.items()))
    # ---- hedef küme ----
    c.execute("""CREATE TABLE u AS SELECT p.poi_id, p.source_place_id, p.name, norm(p.name) n, p.lon, p.lat, p.assigned_ilce_geo_id, g.web_county_id, p.coord_validity
                 FROM k.poi p LEFT JOIN k.geo_entity g ON g.geo_id = p.assigned_ilce_geo_id
                 WHERE p.primary_source='google' AND p.raw_category='Ticari Mekan' AND p.predicted_category IS NULL""")
    n_u = c.execute("SELECT count(*) FROM u").fetchone()[0]
    # 1. PLACE_ID
    c.execute("""CREATE TABLE e1 AS SELECT DISTINCT u.poi_id, m.cat, 'PLACE_ID(restoran_ve_kafe)' AS method, 0.97 AS conf, r.raw_ana_kategori AS evidence
                 FROM u JOIN s.stg.poi_business__stg_restoran_ve_kafe_menuleri__isletme_tarihsel_yasam_dongusu r ON r.raw_google_place_id = u.source_place_id JOIN m_rest m ON m.k = r.raw_ana_kategori""")
    # 2. OSM spatial+name
    c.execute("""CREATE TABLE osm AS SELECT raw_ad AS name, norm(raw_ad) AS n, raw_alt_kategori AS alt, TRY_CAST(raw_lat AS DOUBLE) lat, TRY_CAST(raw_lon AS DOUBLE) lon,
                 CAST(floor(TRY_CAST(raw_lon AS DOUBLE)/0.0015) AS INTEGER) cx, CAST(floor(TRY_CAST(raw_lat AS DOUBLE)/0.0015) AS INTEGER) cy
                 FROM s.stg.poi_business__stg_osm_poi__poi WHERE raw_ad IS NOT NULL AND length(raw_ad) >= 3""")
    c.execute("CREATE TABLE ug AS SELECT *, CAST(floor(lon/0.0015) AS INTEGER) cx, CAST(floor(lat/0.0015) AS INTEGER) cy FROM u WHERE coord_validity='valid'")
    c.execute("""CREATE TABLE e2 AS SELECT poi_id, cat, 'OSM_SPATIAL' AS method, round(0.80 + 0.15*name_sim, 3) AS conf, alt || ' @' || round(dist_m) || 'm sim=' || round(name_sim,2) AS evidence FROM (
        SELECT u.poi_id, m.cat, o.alt, greatest(jaro_winkler_similarity(u.n, o.n), (u.n = o.n)::INT) AS name_sim,
               2*6371000*asin(sqrt(pow(sin(radians(o.lat-u.lat)/2),2)+cos(radians(u.lat))*cos(radians(o.lat))*pow(sin(radians(o.lon-u.lon)/2),2))) AS dist_m,
               row_number() OVER (PARTITION BY u.poi_id ORDER BY greatest(jaro_winkler_similarity(u.n, o.n), (u.n = o.n)::INT) DESC, 2*6371000*asin(sqrt(pow(sin(radians(o.lat-u.lat)/2),2)+cos(radians(u.lat))*cos(radians(o.lat))*pow(sin(radians(o.lon-u.lon)/2),2)))) rn
        FROM ug u JOIN osm o ON o.cx BETWEEN u.cx-1 AND u.cx+1 AND o.cy BETWEEN u.cy-1 AND u.cy+1 JOIN m_osm m ON m.k = o.alt) WHERE rn = 1 AND dist_m <= 100 AND name_sim >= 0.85""")
    # 3. CHAIN
    c.execute("""CREATE TABLE ch AS SELECT coalesce(raw_sube_adi, raw_marka) AS name, norm(coalesce(raw_sube_adi, raw_marka)) AS n, norm(raw_marka) AS bn, raw_kategori AS kat, TRY_CAST(raw_lat AS DOUBLE) lat, TRY_CAST(raw_lon AS DOUBLE) lon,
                 CAST(floor(TRY_CAST(raw_lon AS DOUBLE)/0.0015) AS INTEGER) cx, CAST(floor(TRY_CAST(raw_lat AS DOUBLE)/0.0015) AS INTEGER) cy FROM s.stg.poi_business__stg_zincir_markalar_ve_finans__poi_zincir_ve_finans""")
    c.execute("""CREATE TABLE e3 AS SELECT poi_id, cat, 'CHAIN_SPATIAL' AS method, 0.93 AS conf, kat || ' ' || name AS evidence FROM (
        SELECT u.poi_id, m.cat, o.kat, o.name, row_number() OVER (PARTITION BY u.poi_id ORDER BY 2*6371000*asin(sqrt(pow(sin(radians(o.lat-u.lat)/2),2)+cos(radians(u.lat))*cos(radians(o.lat))*pow(sin(radians(o.lon-u.lon)/2),2)))) rn,
               2*6371000*asin(sqrt(pow(sin(radians(o.lat-u.lat)/2),2)+cos(radians(u.lat))*cos(radians(o.lat))*pow(sin(radians(o.lon-u.lon)/2),2))) dist_m
        FROM ug u JOIN ch o ON o.cx BETWEEN u.cx-1 AND u.cx+1 AND o.cy BETWEEN u.cy-1 AND u.cy+1 AND (jaro_winkler_similarity(u.n, o.n) >= 0.85 OR (length(o.bn) >= 3 AND u.n LIKE '%' || o.bn || '%')) JOIN m_chain m ON m.k = o.kat) WHERE rn = 1 AND dist_m <= 100""")
    # 4. DISTRICT_NAME (aynı ilçe, normalize ad birebir, tek kategori)
    c.execute("""CREATE TABLE piy AS SELECT DISTINCT raw_county_id county, norm(raw_poi_adi) n, raw_alt_kategori alt FROM (
                 SELECT raw_county_id, raw_poi_adi, raw_alt_kategori FROM s.stg.poi_business__stg_piyasa_verileri__poi_noktalari UNION ALL SELECT raw_county_id, raw_poi_adi, raw_alt_kategori FROM s.stg.poi_business__stg_08_ilce_onemli_noktalar_poi) WHERE raw_poi_adi IS NOT NULL""")
    c.execute("""CREATE TABLE e4 AS SELECT u.poi_id, any_value(m.cat) cat, 'DISTRICT_NAME' AS method, 0.88 AS conf, any_value(p.alt) evidence
                 FROM u JOIN piy p ON p.county = u.web_county_id AND p.n = u.n AND length(u.n) >= 5 JOIN m_piy m ON m.k = p.alt GROUP BY u.poi_id HAVING count(DISTINCT m.cat) = 1""")
    # ---- birleştir (öncelik) ----
    c.execute("""CREATE TABLE ev AS SELECT *, 1 pr FROM e1 UNION ALL SELECT *, 2 FROM e2 UNION ALL SELECT *, 3 FROM e3 UNION ALL SELECT *, 4 FROM e4""")
    c.execute("""CREATE TABLE res AS SELECT poi_id, cat, method, conf, evidence, n_sources, distinct_cats,
                 CASE WHEN distinct_cats > 1 THEN 'CROSS_SOURCE_CONFLICT(' || distinct_cats || ')' END flags
                 FROM (SELECT *, count(*) OVER (PARTITION BY poi_id) n_sources, count(DISTINCT cat) OVER (PARTITION BY poi_id) distinct_cats, row_number() OVER (PARTITION BY poi_id ORDER BY pr, conf DESC) rn FROM ev) WHERE rn = 1""")
    c.execute("UPDATE res SET conf = round(least(0.99, conf + 0.02), 3) WHERE n_sources >= 2 AND distinct_cats = 1")
    c.execute("UPDATE res SET conf = round(conf * 0.85, 3) WHERE distinct_cats > 1")
    stats = dict(c.execute("SELECT method, count(*) FROM res GROUP BY 1").fetchall()); n_res = sum(stats.values())
    print("çapraz kaynak:", stats, "| toplam", n_res, "/", n_u, flush=True)
    # ---- 5. NAME_MODEL_V3 ----
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.model_selection import train_test_split
    import numpy as np
    train = c.execute("""SELECT n, cat FROM (
        SELECT norm(name) n, predicted_category cat FROM k.poi WHERE primary_source='google' AND category_method='RULE' AND predicted_category IS NOT NULL
        UNION ALL SELECT o.n, m.cat FROM osm o JOIN m_osm m ON m.k = o.alt
        UNION ALL SELECT norm(raw_mekan_adi), m.cat FROM s.stg.poi_business__stg_restoran_ve_kafe_menuleri__isletme_tarihsel_yasam_dongusu r JOIN m_rest m ON m.k = r.raw_ana_kategori
        UNION ALL SELECT norm(coalesce(raw_sube_adi, raw_marka)), m.cat FROM s.stg.poi_business__stg_zincir_markalar_ve_finans__poi_zincir_ve_finans z JOIN m_chain m ON m.k = z.raw_kategori
        ) WHERE length(n) >= 3""").fetchall()
    X = [r[0] for r in train]; y = [r[1] for r in train]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.1, random_state=20260920, stratify=y)
    model = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, sublinear_tf=True, max_features=400000), SGDClassifier(loss="log_loss", alpha=2e-6, max_iter=25, random_state=20260920, n_jobs=4))
    model.fit(Xtr, ytr); proba = model.predict_proba(Xte); cls = model.classes_; pred = cls[proba.argmax(1)]; pmax = proba.max(1)
    acc = float((pred == np.array(yte)).mean()); hi = pmax >= 0.85; acc_hi = float((pred[hi] == np.array(yte)[hi]).mean()) if hi.any() else None; cov_hi = float(hi.mean())
    print(f"ad modeli v3: eğitim {len(Xtr):,} · test doğruluk {acc:.3f} · p≥0.85: doğruluk {acc_hi:.3f}, kapsam {cov_hi:.2%}", flush=True)
    rest = c.execute("SELECT poi_id, n FROM u WHERE poi_id NOT IN (SELECT poi_id FROM res) AND length(n) >= 3").fetchall()
    if rest:
        pr = model.predict_proba([r[1] for r in rest]); pm = pr.max(1); pc = cls[pr.argmax(1)]
        rows = [(rest[i][0], str(pc[i]), "NAME_MODEL_V3", round(float(pm[i]), 3), f"p={pm[i]:.2f}", 1, 1, None) for i in range(len(rest)) if pm[i] >= 0.85]
        c.executemany("INSERT INTO res VALUES (?,?,?,?,?,?,?,?)", rows); stats["NAME_MODEL_V3"] = len(rows)
    c.execute("CREATE TABLE sec (cat VARCHAR, sector VARCHAR)"); c.executemany("INSERT INTO sec VALUES (?,?)", list(sector.items()))
    c.execute(f"""CREATE TABLE final AS SELECT r.poi_id, u.name, r.cat AS predicted_category, s2.sector AS predicted_sector, r.method, r.conf AS confidence,
                  CASE WHEN r.conf >= 0.95 THEN 'high' WHEN r.conf >= 0.85 THEN 'review_recommended' ELSE 'no_auto' END AS band, r.evidence, r.n_sources, r.flags, '{V}' AS version, '{now}' AS created_at
                  FROM res r JOIN u USING (poi_id) LEFT JOIN sec s2 ON s2.cat = r.cat""")
    c.execute(f"COPY final TO '{OUT}/mappings/poi_category_predictions_v3.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    tot = c.execute("SELECT count(*) FROM final").fetchone()[0]; bands = dict(c.execute("SELECT band, count(*) FROM final GROUP BY 1").fetchall())
    bym = c.execute("SELECT method, band, count(*) FROM final GROUP BY 1,2 ORDER BY 1,2").fetchall(); conflicts = c.execute("SELECT count(*) FROM final WHERE flags IS NOT NULL").fetchone()[0]
    topcat = c.execute("SELECT predicted_category, count(*) FROM final GROUP BY 1 ORDER BY 2 DESC LIMIT 12").fetchall()
    # golden 200
    gold = []
    for m in ("PLACE_ID(restoran_ve_kafe)", "OSM_SPATIAL", "CHAIN_SPATIAL", "DISTRICT_NAME", "NAME_MODEL_V3"):
        rs = c.execute("SELECT poi_id, name, predicted_category, method, confidence, evidence, flags FROM final WHERE method = ?", [m]).fetchall(); gold += random.sample(rs, min(40, len(rs)))
    rows_html = "".join(f"<tr><td>{i+1}</td><td>{html.escape(str(r[1]))}</td><td><b>{html.escape(str(r[2]))}</b></td><td><small>{html.escape(r[3])} · {r[4]:.2f}<br>{html.escape(str(r[5] or ''))} {html.escape(str(r[6] or ''))}</small></td><td><label><input type=radio name=q{i} value=yes> doğru</label> <label><input type=radio name=q{i} value=no> yanlış</label> <label><input type=radio name=q{i} value=unsure> emin değilim</label></td></tr>" for i, r in enumerate(gold))
    page = f"""<!doctype html><html lang=tr><meta charset=utf-8><title>POI kategori v3 golden set</title><style>body{{font-family:system-ui;margin:16px}}table{{border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #ddd;padding:4px 6px;vertical-align:top}}small{{color:#666}}#out{{width:100%;height:120px}}</style>
<h1>İşletme türü — çapraz kaynak çözümü v3 ({len(gold)} örnek)</h1><p>Her satırda işletme adı ve önerilen tür var; doğru/yanlış işaretleyip "Dışa aktar" metnini bana gönderin.</p>
<table><tr><th>#</th><th>İşletme</th><th>Önerilen tür</th><th>Kanıt</th><th>Karar</th></tr>{rows_html}</table><p><button onclick="ex()">Dışa aktar</button></p><textarea id=out></textarea>
<script>const ids={json.dumps([r[0] for r in gold])};function ex(){{let s='poi_id,verdict\\n';ids.forEach((p,i)=>{{const v=document.querySelector('input[name=q'+i+']:checked');s+=p+','+(v?v.value:'')+'\\n'}});document.getElementById('out').value=s}}</script></html>"""
    (OUT / "validation" / "golden" / "poi_category_golden_review_v3.html").write_text(page, encoding="utf-8")
    (OUT / "mappings" / "poi_category_v3_config.json").write_text(json.dumps({"version": V, "maps": {"restoran": RESTORAN, "osm": OSM, "chain": CHAIN, "piyasa": PIYASA}, "priority": ["PLACE_ID", "OSM_SPATIAL(≤100m, sim≥0.85)", "CHAIN_SPATIAL", "DISTRICT_NAME(aynı ilçe, ad birebir, tek kategori)", "NAME_MODEL_V3(p≥0.85)"],
        "model": {"train_rows": len(Xtr), "test_accuracy": acc, "acc_p85": acc_hi, "coverage_p85": cov_hi}, "seed": 20260920, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, indent=1))
    rep = f"""# POI kategori v3 — çapraz kaynak çözümü ({dt.datetime.now().date()})
Hedef: {n_u:,} çözülemeyen Google işletmesi. Çözülen: **{tot:,}** ({tot/n_u:.1%}) · kalan {n_u-tot:,}
| Yöntem | high | review | no_auto |
|---|---|---|---|
""" + "\n".join(f"| {m} | {sum(n for mm,b,n in bym if mm==m and b=='high'):,} | {sum(n for mm,b,n in bym if mm==m and b=='review_recommended'):,} | {sum(n for mm,b,n in bym if mm==m and b=='no_auto'):,} |" for m in dict.fromkeys(mm for mm,_,_ in bym)) + f"""

Bantlar: {bands} · kaynaklar arası çelişki: {conflicts:,} (üst öncelik seçildi, güven ×0,85, bayraklı)
Ad modeli v3: eğitim {len(Xtr):,} etiketli ad (Google kural {c.execute("SELECT count(*) FROM k.poi WHERE primary_source='google' AND category_method='RULE'").fetchone()[0]:,} + OSM + restoran + zincir) · test doğruluk {acc:.3f} · p≥0,85 doğruluk {acc_hi:.3f} (kapsam {cov_hi:.0%})
En sık türler: {topcat}
Golden set: validation/golden/poi_category_golden_review_v3.html ({len(gold)}). Kanoniğe uygulama: v1.5 (yalnız high bant otomatik; review insan onayı). Süre {(dt.datetime.now()-t0).seconds}s.
"""
    (OUT / "validation" / "poi_category_v3_report.md").write_text(rep, encoding="utf-8"); print(rep)
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f: f.write(json.dumps({"at": now, "action": "POI_CATEGORY_V3", "target": n_u, "resolved": tot, "bands": bands, "methods": stats, "conflicts": conflicts}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
