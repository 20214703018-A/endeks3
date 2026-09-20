#!/usr/bin/env python3
"""
POI MATCHER v1 — Google Places (canonical poi) ↔ OSM gıda işletmeleri (stg_osm_food_poi): aynı işletme eşleştirme (STANDARD §6).
Aday üretimi: 250 m içindeki çiftler (ızgara bloklama, DuckDB). Sinyaller: ad benzerliği (JW, normalize), mesafe, kategori uyumu, telefon.
Skor: noisy-OR 1−Π(1−e_i); ad kanıtı yoksa (jw<0.75) ve telefon yoksa aday olamaz (yakınlık tek başına kanıt değildir — 25 m içinde birden çok işletme olur).
Çıktı: mappings/poi_match_google_osm_v1_candidates.parquet, mappings/poi_match_google_osm_v1_PROPOSED.parquet, mappings/poi_matcher_v1_config.json,
       validation/poi_matcher_v1_report.md, validation/golden/poi_match_golden_review_v1.html (+csv). Hiçbir kaynak/kanonik tablo değiştirilmez.
"""
import json, random, csv, collections, datetime as dt, hashlib, html
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"; STG = OUT / "staging" / "geoprop_staging.duckdb"
V = "POI_MATCHER_V1"; RADIUS_M = 250
TH = {"virtually_certain": 0.995, "very_high": 0.980, "high": 0.950, "review": 0.850}
FOOD_AMENITY = ("restaurant", "cafe", "fast_food", "bar", "pub", "food_court", "ice_cream", "biergarten")
FOOD_SHOP = ("bakery", "pastry", "confectionery", "coffee", "tea", "deli", "butcher", "seafood", "greengrocer", "convenience", "supermarket", "kiosk")
GOOGLE_FOOD_RAW = ("Kafe", "Restoran & Lokanta", "Fast Food", "3. Nesil Kahveci", "Kebapçı & Ocakbaşı", "Pastane & Fırın", "Tatlı", "Türk", "Bar & Pub", "Dönerci", "Pideci & Lahmacun", "Çiğköfte", "Kokoreç", "Balık Restoranı", "Kahvaltı & Börek", "Bakkal & Market")
random.seed(20260919)


def band(s):
    for k, t in TH.items():
        if s >= t: return k if k != "review" else "review_recommended"
    return "no_auto_merge"


def main():
    t0 = dt.datetime.now(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = duckdb.connect(); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute(f"SET temp_directory='{OUT}/tmp/poim'")
    c.execute(f"ATTACH '{CAN}' AS k (READ_ONLY)"); c.execute(f"ATTACH '{STG}' AS s (READ_ONLY)")
    cols = [r[0] for r in c.execute("DESCRIBE s.stg.poi_business__stg_osm_food_poi").fetchall()]
    def pick(*names):
        for n in names:
            for col in cols:
                if col.lower() == n.lower(): return f'"{col}"'
        return "NULL"
    name_c, amen_c, shop_c, cuis_c = pick("raw_name"), pick("raw_amenity"), pick("raw_shop"), pick("raw_cuisine")
    phone_c = f"coalesce({pick('raw_phone')}, {pick('raw_contact:phone', 'raw_contact_phone')})"
    web_c = f"coalesce({pick('raw_website')}, {pick('raw_contact:website', 'raw_contact_website')})"
    fid_c = pick("_feature_id", "raw__feature_id"); geom_c = "geometry" if "geometry" in cols else pick("geometry_original")
    c.execute("CREATE MACRO norm(x) AS trim(regexp_replace(regexp_replace(translate(lower(replace(replace(coalesce(x,''),'İ','i'),'I','ı')), 'çğıöşüâîû', 'cgiosuaiu'), '[^a-z0-9 ]+', ' ', 'g'), ' +', ' ', 'g'))")
    c.execute("CREATE MACRO digits(x) AS right(regexp_replace(coalesce(x,''), '[^0-9]', '', 'g'), 10)")
    # ---- OSM tarafı (ad zorunlu; centroid) ----
    c.execute(f"""CREATE TABLE osm AS SELECT {fid_c} AS osm_id, {name_c} AS name, norm({name_c}) AS n, {amen_c} AS amenity, {shop_c} AS shop, {cuis_c} AS cuisine,
        digits({phone_c}) AS ph, {web_c} AS website, ST_X(ST_Centroid({geom_c})) lon, ST_Y(ST_Centroid({geom_c})) lat, source_file_id, source_row_number, source_row_hash
        FROM s.stg.poi_business__stg_osm_food_poi WHERE {geom_c} IS NOT NULL""")
    n_osm_all = c.execute("SELECT count(*) FROM osm").fetchone()[0]
    c.execute("DELETE FROM osm WHERE name IS NULL OR length(n) < 2")
    n_osm = c.execute("SELECT count(*) FROM osm").fetchone()[0]
    # ---- Google tarafı (geçerli koordinat) ----
    c.execute(f"""CREATE TABLE g AS SELECT poi_id, name, norm(name) n, raw_category, predicted_category, predicted_sector, digits(phone) ph, lon, lat, assigned_geo_id,
        (coalesce(predicted_sector,'') IN ('Yeme-İçme','Gıda Perakende') OR raw_category IN {GOOGLE_FOOD_RAW}
         OR regexp_matches(lower(coalesce(raw_category,'')||' '||coalesce(predicted_category,'')), 'dondurma|kafe|cafe|restoran|lokanta|kebap|pide|döner|börek|pastane|fırın|tatlı|çay|kahve|büfe|bar|pub|meyhane|balık|köfte|çiğ|kokoreç|mantı|waffle|kumpir|gıda|market|bakkal|şarküteri|kuruyemiş|kasap|manav|yemek|kahvaltı|simit|künefe|baklava|çorba|ocakbaşı|izgara|tantuni|kantin|yiyecek|içecek')) AS is_food, (raw_category='Ticari Mekan' AND predicted_category IS NULL) AS cat_unknown
        FROM k.poi WHERE coord_validity='valid' AND lon IS NOT NULL""")
    n_g = c.execute("SELECT count(*) FROM g").fetchone()[0]
    # ---- ızgara bloklama: 0.0025° (~250 m) hücreler, 3×3 komşuluk ----
    c.execute("CREATE TABLE gg AS SELECT *, CAST(floor(lon/0.0025) AS INTEGER) cx, CAST(floor(lat/0.0025) AS INTEGER) cy FROM g")
    c.execute("CREATE TABLE og AS SELECT *, CAST(floor(lon/0.0025) AS INTEGER) cx, CAST(floor(lat/0.0025) AS INTEGER) cy FROM osm")
    c.execute(f"""CREATE TABLE cand AS
        SELECT o.osm_id, g.poi_id, o.name osm_name, g.name g_name, o.n on_, g.n gn, o.amenity, o.shop, o.cuisine, g.raw_category, g.predicted_category, g.is_food, g.cat_unknown,
               o.ph oph, g.ph gph, o.lon olon, o.lat olat, g.lon glon, g.lat glat, g.assigned_geo_id,
               2*6371000*asin(sqrt(pow(sin(radians(g.lat-o.lat)/2),2)+cos(radians(o.lat))*cos(radians(g.lat))*pow(sin(radians(g.lon-o.lon)/2),2))) AS dist_m,
               jaro_winkler_similarity(o.n, g.n) AS jw,
               CASE WHEN length(o.n)>0 AND length(g.n)>0 THEN len(list_intersect(string_split(o.n,' '), string_split(g.n,' ')))::DOUBLE / greatest(1, len(list_distinct(string_split(o.n,' ') || string_split(g.n,' ')))) ELSE 0 END AS tok_jacc
        FROM og o JOIN gg g ON g.cx BETWEEN o.cx-1 AND o.cx+1 AND g.cy BETWEEN o.cy-1 AND o.cy+1
        WHERE 2*6371000*asin(sqrt(pow(sin(radians(g.lat-o.lat)/2),2)+cos(radians(o.lat))*cos(radians(g.lat))*pow(sin(radians(g.lon-o.lon)/2),2))) <= {RADIUS_M}""")
    n_pairs = c.execute("SELECT count(*) FROM cand").fetchone()[0]
    # ---- skor (noisy-OR) ----
    c.execute("""CREATE TABLE scored AS SELECT *,
        greatest(jw, CASE WHEN tok_jacc >= 0.5 THEN 0.80 + 0.20*tok_jacc ELSE 0 END) AS name_sim,
        (length(oph)=10 AND oph=gph) AS phone_match,
        CASE WHEN amenity IN ('restaurant','cafe','fast_food','bar','pub','food_court','ice_cream','biergarten') OR shop IN ('bakery','pastry','confectionery','coffee','tea','deli','butcher','seafood','greengrocer','convenience','supermarket','kiosk') THEN TRUE ELSE FALSE END AS osm_food
        FROM cand""")
    c.execute("""CREATE TABLE sc2 AS SELECT *,
        CASE WHEN name_sim >= 0.80 THEN 0.92*(name_sim-0.80)/0.20 WHEN name_sim >= 0.75 THEN 0.10 ELSE 0 END AS name_ev,
        CASE WHEN dist_m <= 25 THEN 0.85 WHEN dist_m <= 60 THEN 0.70 WHEN dist_m <= 120 THEN 0.50 WHEN dist_m <= 200 THEN 0.25 ELSE 0.10 END AS dist_ev,
        CASE WHEN phone_match THEN 0.95 ELSE 0 END AS phone_ev,
        CASE WHEN osm_food AND is_food THEN 0.30 WHEN osm_food AND cat_unknown THEN 0.0 WHEN osm_food AND NOT is_food THEN -1 ELSE 0.0 END AS cat_ev
        FROM scored WHERE name_sim >= 0.75 OR phone_match""")
    c.execute("""CREATE TABLE sc3 AS SELECT *,
        round(CASE WHEN cat_ev < 0 THEN 0.55 ELSE 1.0 END * (1 - (1-name_ev)*(1-dist_ev)*(1-phone_ev)*(1-greatest(cat_ev,0))), 4) AS score,
        concat_ws(',', CASE WHEN cat_ev < 0 THEN 'CATEGORY_CONFLICT_google_nonfood(x0.55)' END, CASE WHEN dist_m > 120 THEN 'FAR_gt120m' END, CASE WHEN name_sim < 0.85 THEN 'WEAK_NAME' END, CASE WHEN phone_match THEN 'PHONE_MATCH' END) AS flags,
        format('name_ev={:.2f}(sim={:.3f}); dist_ev={:.2f}({:.0f}m); phone_ev={:.2f}; cat_ev={:.2f}', name_ev, name_sim, dist_ev, dist_m, phone_ev, cat_ev) AS reasons
        FROM sc2""")
    # ---- bire-bir en iyi eşleşme (açgözlü, skor sırası) ----
    rows = c.execute("SELECT osm_id, poi_id, score FROM sc3 ORDER BY score DESC, dist_m ASC").fetchall()
    uo, ug, best = set(), set(), []
    for o, g_, s_ in rows:
        if o in uo or g_ in ug: continue
        uo.add(o); ug.add(g_); best.append((o, g_))
    c.execute("CREATE TABLE best (osm_id VARCHAR, poi_id VARCHAR)"); c.executemany("INSERT INTO best VALUES (?,?)", best)
    c.execute(f"""CREATE TABLE mapping AS SELECT s.*, CASE WHEN s.score >= {TH['virtually_certain']} THEN 'virtually_certain' WHEN s.score >= {TH['very_high']} THEN 'very_high' WHEN s.score >= {TH['high']} THEN 'high'
        WHEN s.score >= {TH['review']} THEN 'review_recommended' ELSE 'no_auto_merge' END AS band, 'PROPOSED (insan onayı bekliyor)' AS status, '{V}' AS matcher_version, '{now}' AS created_at
        FROM sc3 s JOIN best b USING (osm_id, poi_id)""")
    c.execute(f"COPY (SELECT * EXCLUDE (on_, gn) FROM sc3) TO '{OUT}/mappings/poi_match_google_osm_v1_candidates.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    c.execute(f"COPY (SELECT * EXCLUDE (on_, gn) FROM mapping) TO '{OUT}/mappings/poi_match_google_osm_v1_PROPOSED.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    bands = dict(c.execute("SELECT band, count(*) FROM mapping GROUP BY 1").fetchall())
    flags = dict(c.execute("SELECT f, count(*) FROM (SELECT unnest(string_split(flags, ',')) f FROM mapping WHERE flags <> '') GROUP BY 1").fetchall())
    n_best = len(best); high = sum(bands.get(b, 0) for b in ("virtually_certain", "very_high", "high"))
    dist_stats = c.execute("SELECT round(median(dist_m)), round(quantile_cont(dist_m, 0.9)) FROM mapping WHERE band IN ('virtually_certain','very_high','high')").fetchone()
    # OSM tarafında ad olup Google'da hiç aday çıkmayanlar (Google kapsamı dışı veya koordinat farkı)
    n_osm_nomatch = c.execute("SELECT count(*) FROM osm WHERE osm_id NOT IN (SELECT osm_id FROM mapping)").fetchone()[0]
    # golden set: her banttan 40
    gold = []
    for b in ("virtually_certain", "very_high", "high", "review_recommended", "no_auto_merge"):
        rs = c.execute(f"SELECT osm_id, poi_id, osm_name, g_name, amenity, shop, raw_category, predicted_category, round(dist_m), round(name_sim,3), phone_match, score, band, flags, reasons, olon, olat, glon, glat FROM mapping WHERE band='{b}'").fetchall()
        gold += random.sample(rs, min(40, len(rs)))
    (OUT / "validation" / "golden").mkdir(parents=True, exist_ok=True)
    with open(OUT / "validation" / "golden" / "poi_match_golden_sample_v1.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["osm_id", "poi_id", "osm_name", "google_name", "amenity", "shop", "google_raw_category", "google_predicted", "dist_m", "name_sim", "phone_match", "score", "band", "flags", "reasons", "osm_lon", "osm_lat", "g_lon", "g_lat", "same_entity(human)", "note"])
        for r in gold: w.writerow(list(r) + ["", ""])
    rows_html = "".join(f"<tr class='{html.escape(r[12])}'><td>{i+1}</td><td>{html.escape(str(r[2]))}<br><small>{html.escape(str(r[4] or r[5] or ''))}</small></td><td>{html.escape(str(r[3]))}<br><small>{html.escape(str(r[7] or r[6] or ''))}</small></td>"
                        f"<td>{r[8]:.0f} m</td><td>{r[9]:.3f}</td><td>{'✓' if r[10] else ''}</td><td><b>{r[11]:.3f}</b><br><small>{html.escape(r[12])}</small></td><td><small>{html.escape(r[13] or '')}</small></td>"
                        f"<td><a target=_blank href='https://www.openstreetmap.org/?mlat={r[16]}&mlon={r[15]}#map=19/{r[16]}/{r[15]}'>OSM</a> · <a target=_blank href='https://www.google.com/maps/search/?api=1&query={r[18]},{r[17]}'>Google</a></td>"
                        f"<td><label><input type=radio name=q{i} value=yes> evet</label> <label><input type=radio name=q{i} value=no> hayır</label> <label><input type=radio name=q{i} value=unsure> emin değilim</label></td></tr>" for i, r in enumerate(gold))
    page = f"""<!doctype html><html lang=tr><meta charset=utf-8><title>POI eşleşme golden set v1</title>
<style>body{{font-family:system-ui;margin:16px}}table{{border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #ddd;padding:4px 6px;vertical-align:top}}tr.virtually_certain td{{background:#eef9ee}}tr.review_recommended td{{background:#fff7e6}}tr.no_auto_merge td{{background:#fdeeee}}small{{color:#666}}#out{{width:100%;height:120px}}</style>
<h1>Google ↔ OSM işletme eşleşmesi — golden set v1 ({len(gold)} çift)</h1>
<p>Her satır için "aynı işletme mi?" cevabını işaretleyin; bitince <b>Dışa aktar</b> ile CSV'yi kopyalayıp bana gönderin. Bantlar: yeşil=otomatik öneri, sarı=inceleme, kırmızı=eşleşme önerilmedi (kontrol için).</p>
<table><tr><th>#</th><th>OSM</th><th>Google</th><th>mesafe</th><th>ad benz.</th><th>tel</th><th>skor / bant</th><th>bayrak</th><th>harita</th><th>aynı işletme?</th></tr>{rows_html}</table>
<p><button onclick="ex()">Dışa aktar</button></p><textarea id=out></textarea>
<script>const ids={json.dumps([[r[0], r[1]] for r in gold])};function ex(){{let s='osm_id,poi_id,same_entity\\n';ids.forEach((p,i)=>{{const v=document.querySelector('input[name=q'+i+']:checked');s+=p[0]+','+p[1]+','+(v?v.value:'')+'\\n'}});document.getElementById('out').value=s}}</script></html>"""
    (OUT / "validation" / "golden" / "poi_match_golden_review_v1.html").write_text(page, encoding="utf-8")
    cfg = {"matcher_version": V, "radius_m": RADIUS_M, "thresholds": TH, "combination": "noisy-OR 1-Π(1-e_i); kategori çelişkisi ×0.55",
           "evidence": {"name": "0.92×(sim−0.80)/0.20 (sim≥0.80); 0.10 (0.75–0.80); sim=max(JW, 0.80+0.20×tokenJaccard[≥0.5])", "distance": "≤25m 0.85 · ≤60m 0.70 · ≤120m 0.50 · ≤200m 0.25 · else 0.10", "phone": "0.95 (son 10 hane eşit)", "category": "OSM gıda & Google gıda +0.30; Google gıda-dışı → ×0.55"},
           "candidacy": "name_sim ≥ 0.75 veya telefon eşleşmesi (yakınlık tek başına yetmez)", "assignment": "bire-bir açgözlü (skor↓, mesafe↑)", "seed": 20260919, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "inputs": {"canonical": str(CAN), "osm_view": "stg.poi_business__stg_osm_food_poi"}}
    (OUT / "mappings" / "poi_matcher_v1_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1))
    rep = f"""# poi_matcher v1 — Google ↔ OSM ({dt.datetime.now().date()})
Girdi: OSM gıda POI {n_osm_all:,} (adı olan {n_osm:,}) · Google POI (geçerli koordinat) {n_g:,} · 250 m içinde çift {n_pairs:,} · aday (ad≥0.75 veya telefon) {c.execute('SELECT count(*) FROM sc3').fetchone()[0]:,}
Bire-bir eşleşme: {n_best:,} · otomatik önerilen (high+): {high:,} ({high/max(1,n_osm):.1%} OSM adlı kayıt) · yüksek bant mesafe medyan {dist_stats[0]} m, p90 {dist_stats[1]} m
| Band | adet |
|---|---|
""" + "\n".join(f"| {b} | {bands.get(b,0):,} |" for b in ("virtually_certain", "very_high", "high", "review_recommended", "no_auto_merge")) + f"""

Bayraklar: {flags}
Eşleşmesiz OSM (adlı): {n_osm_nomatch:,} — Google koleksiyonu kapsamında olmayan ya da farklı yazımlı işletmeler; poi tablosuna **yeni işletme** adayı (v1.2'de source='osm' olarak eklenecek, eşleşme değil).
Durum: PROPOSED; golden set validation/golden/poi_match_golden_review_v1.html ({len(gold)} çift). Süre {(dt.datetime.now()-t0).seconds}s. Yapılandırma mappings/poi_matcher_v1_config.json.
"""
    (OUT / "validation" / "poi_matcher_v1_report.md").write_text(rep, encoding="utf-8"); print(rep)
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "POI_MATCHER_V1", "osm_named": n_osm, "google": n_g, "pairs": n_pairs, "best": n_best, "high_plus": high, "bands": bands, "status": "PROPOSED"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
