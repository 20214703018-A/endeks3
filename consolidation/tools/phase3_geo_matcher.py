#!/usr/bin/env python3
"""
PHASE 3 — geo_matcher v1: il / ilçe / mahalle kimlik sistemlerini eşler (FİZİKSEL BİRLEŞTİRME YOK).

Kaynaklar (staging v1.0.0):
  WEB  : stg_admin_polygon  (DistrictId, poligon, nüfus 2024)          — fiyat/ilan ailelerinin bağlı olduğu ID
  TKGM : stg_tkgm_mahalle   (resmî kadastro id, poligon)               — alım-satım/parsel verilerinin ID'si
  TUIK : stg_tuik_nufus_mahalle + nufus_koy (tuik_kodu, nüfus 2025)    — resmî nüfus

Sinyaller: name_similarity (Jaro-Winkler, normalize ad) · iou (poligon örtüşmesi) · centroid_inside · population_consistency (2024 vs 2025)
Skor 0–1; sınıflar STANDARD §6. Çıktı: mappings/geo_candidates_v1.parquet (tüm adaylar+gerekçe), mappings/geo_id_mapping_v1_PROPOSED.parquet,
validation/geo_matcher_v1_report.md, validation/golden/geo_golden_sample_v1.csv (insan doğrulaması için).
"""
from __future__ import annotations
import json, re, unicodedata, random, time, datetime as dt, hashlib
from pathlib import Path
import duckdb, pyarrow as pa, pyarrow.parquet as pq
from shapely import wkb
from shapely.strtree import STRtree
from shapely.validation import make_valid

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
S = OUT / "staging" / "v1.0.0" / "reference_geography"
MATCHER_VERSION = "GEO_MATCHER_V1"
THRESHOLDS = {"virtually_certain": 0.995, "very_high": 0.980, "high": 0.950, "review": 0.850}  # < review → no_auto_merge
WEIGHTS_TK = {"name": 0.40, "iou": 0.45, "centroid_inside": 0.15}
WEIGHTS_TUIK = {"name": 0.65, "population": 0.35}
random.seed(20260918)


def norm(s):
    if s is None: return None
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(str.maketrans("çğıöşüâîû", "cgiosuaiu"))
    s = unicodedata.normalize("NFKD", s); s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\((.*?)\)", " ", s)
    s = re.sub(r"\b(mah|mahallesi|mahalle|koyu|koy|beldesi|belde|bld|osb)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def main():
    t0 = time.time()
    c = duckdb.connect(); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'")
    c.create_function("norm", norm, [str], str)
    # --- yükle ---
    web = c.execute(f"""SELECT raw_district_id id, raw_district ad, norm(raw_district) n, norm(raw_city) il_n, norm(raw_county) ilce_n, raw_county_id county_id, typed_population pop, ST_AsWKB(geometry_original) g
                        FROM '{S}/stg_admin_polygon/*/*.parquet' WHERE source_level='mahalle' AND polygon_kind='admin'""").fetchall()
    c.execute(f"CREATE TABLE tk_il AS SELECT raw_id id, norm(raw_text) n, raw_text ad FROM '{S}/stg_tkgm_il/*.parquet'")
    c.execute(f"CREATE TABLE tk_ilce AS SELECT raw_id id, norm(raw_text) n, raw_text ad, regexp_extract(source_relative_path,'ilceListe_(\\d+)',1) il_id FROM '{S}/stg_tkgm_ilce/*.parquet'")
    tk = c.execute(f"""SELECT m.raw_id id, m.raw_text ad, norm(m.raw_text) n, i.n il_n, t.n ilce_n, t.id ilce_id, ST_AsWKB(m.geometry_original) g
                       FROM '{S}/stg_tkgm_mahalle/*.parquet' m JOIN tk_il i ON i.id=regexp_extract(m.source_relative_path,'mahalleListe_(\\d+)_(\\d+)',1)
                       JOIN tk_ilce t ON t.id=regexp_extract(m.source_relative_path,'mahalleListe_(\\d+)_(\\d+)',2)""").fetchall()
    tuik = c.execute(f"""SELECT raw_tuik_kodu kod, raw_mahalle ad, norm(raw_mahalle) n, norm(raw_il) il_n, norm(raw_ilce) ilce_n, CAST(raw_nufus AS BIGINT) pop, 'mahalle' tur FROM '{S}/stg_tuik_nufus_mahalle/*.parquet'
                         UNION ALL SELECT raw_tuik_kodu, raw_koy, norm(raw_koy), norm(raw_il), norm(raw_ilce), CAST(raw_nufus AS BIGINT), 'koy' FROM '{S}/stg_tuik_nufus_koy/*.parquet'""").fetchall()
    print(f"web={len(web):,} tkgm={len(tk):,} tuik={len(tuik):,}  ({time.time()-t0:.0f}s)")
    # --- geometriler ---
    web_geom = {}; tk_geom = {}
    for r in web:
        if r[7] is not None:
            try: web_geom[r[0]] = make_valid(wkb.loads(bytes(r[7])))
            except Exception: pass
    for r in tk:
        if r[6] is not None:
            try: tk_geom[r[0]] = make_valid(wkb.loads(bytes(r[6])))
            except Exception: pass
    tk_by_il = {}
    for r in tk: tk_by_il.setdefault(r[3], []).append(r)
    trees = {}
    for il, rows in tk_by_il.items():
        geoms = [tk_geom[r[0]] for r in rows if r[0] in tk_geom]; ids = [r[0] for r in rows if r[0] in tk_geom]
        if geoms: trees[il] = (STRtree(geoms), ids, geoms)
    tk_index = {r[0]: r for r in tk}
    tk_name_index = {}
    for r in tk: tk_name_index.setdefault((r[3], r[4]), []).append(r)
    tuik_index = {}
    for r in tuik: tuik_index.setdefault((r[3], r[4]), []).append(r)
    # --- Jaro-Winkler via DuckDB (vektörize) yerine Python: küçük aday listeleri ---
    def jw(a, b):
        return c.execute("SELECT jaro_winkler_similarity(?, ?)", [a or "", b or ""]).fetchone()[0]
    # --- adaylar WEB↔TKGM ---
    cand_tk = []
    for i, (wid, wad, wn, il_n, ilce_n, county_id, pop, g) in enumerate(web, 1):
        wg = web_geom.get(wid); cands = {}
        # ad adayları (aynı il+ilçe)
        for r in tk_name_index.get((il_n, ilce_n), []):
            s = jw(wn, r[2])
            if s >= 0.80: cands[r[0]] = {"name": s, "how": "name_same_ilce"}
        # mekânsal adaylar (aynı il, centroid içeren veya kesişen)
        if wg is not None and il_n in trees:
            tree, ids, geoms = trees[il_n]; cpt = wg.representative_point()
            for j in tree.query(cpt, predicate="within"):
                cands.setdefault(ids[j], {"name": jw(wn, tk_index[ids[j]][2]), "how": "spatial"})["centroid_inside"] = 1.0
            for j in tree.query(wg, predicate="intersects"):
                cands.setdefault(ids[j], {"name": jw(wn, tk_index[ids[j]][2]), "how": "spatial"})
        for tid, sig in cands.items():
            tg = tk_geom.get(tid); iou = None; inside = sig.get("centroid_inside", 0.0)
            if wg is not None and tg is not None:
                try:
                    u = wg.union(tg).area; iou = (wg.intersection(tg).area / u) if u > 0 else 0.0
                    if not inside and tg.contains(wg.representative_point()): inside = 1.0
                except Exception: iou = None
            name = sig["name"]; iou_s = iou if iou is not None else 0.0
            score = WEIGHTS_TK["name"] * name + WEIGHTS_TK["iou"] * iou_s + WEIGHTS_TK["centroid_inside"] * inside
            if tg is None:  # TKGM poligonu yok → yalnız ad; en fazla 'review'
                score = min(score / (WEIGHTS_TK["name"]) * 0.90, 0.94) if name >= 0.9 else name * 0.8
            reasons = [f"name_similarity={name:.3f}", f"iou={iou:.3f}" if iou is not None else "iou=n/a(no_geometry)", f"centroid_inside={int(inside)}", f"candidate_via={sig['how']}", f"same_ilce={tk_index[tid][4]==ilce_n}"]
            cand_tk.append({"web_district_id": wid, "tkgm_id": tid, "web_name": wad, "tkgm_name": tk_index[tid][1], "il_n": il_n, "web_ilce_n": ilce_n, "tkgm_ilce_n": tk_index[tid][4],
                            "name_similarity": round(name, 4), "iou": None if iou is None else round(iou, 4), "centroid_inside": inside, "score": round(score, 4), "reasons": "; ".join(reasons)})
        if i % 5000 == 0: print(f"  web↔tkgm {i:,}/{len(web):,} aday={len(cand_tk):,} ({time.time()-t0:.0f}s)", flush=True)
    # --- adaylar WEB↔TÜİK ---
    cand_tuik = []
    for wid, wad, wn, il_n, ilce_n, county_id, pop, g in web:
        for r in tuik_index.get((il_n, ilce_n), []):
            s = jw(wn, r[2])
            if s < 0.80: continue
            pc = None
            if pop and r[5]:
                ratio = pop / r[5]; pc = max(0.0, 1.0 - min(1.0, abs(ratio - 1.0) / 0.30))  # ±30% dışı → 0
            score = WEIGHTS_TUIK["name"] * s + WEIGHTS_TUIK["population"] * (pc if pc is not None else 0.5)
            cand_tuik.append({"web_district_id": wid, "tuik_kodu": r[0], "web_name": wad, "tuik_name": r[1], "tuik_tur": r[6], "il_n": il_n, "ilce_n": ilce_n, "web_pop_2024": pop, "tuik_pop_2025": r[5],
                              "name_similarity": round(s, 4), "population_consistency": None if pc is None else round(pc, 4), "score": round(score, 4),
                              "reasons": f"name_similarity={s:.3f}; pop_ratio={'n/a' if not (pop and r[5]) else round(pop/r[5],3)}; population_consistency={'n/a' if pc is None else round(pc,3)}"})
    print(f"aday: web↔tkgm={len(cand_tk):,} web↔tuik={len(cand_tuik):,} ({time.time()-t0:.0f}s)")
    # --- en iyi eşleşme (greedy bire-bir) ---
    def best_one_to_one(cands, a_key, b_key):
        used_a, used_b, out = set(), set(), []
        for x in sorted(cands, key=lambda z: -z["score"]):
            if x[a_key] in used_a or x[b_key] in used_b: continue
            used_a.add(x[a_key]); used_b.add(x[b_key]); out.append(x)
        return out
    def band(s):
        if s >= THRESHOLDS["virtually_certain"]: return "virtually_certain"
        if s >= THRESHOLDS["very_high"]: return "very_high"
        if s >= THRESHOLDS["high"]: return "high"
        if s >= THRESHOLDS["review"]: return "review_recommended"
        return "no_auto_merge"
    best_tk = best_one_to_one(cand_tk, "web_district_id", "tkgm_id"); best_tuik = best_one_to_one(cand_tuik, "web_district_id", "tuik_kodu")
    for x in cand_tk: x["band"] = band(x["score"]); x["matcher_version"] = MATCHER_VERSION
    for x in cand_tuik: x["band"] = band(x["score"]); x["matcher_version"] = MATCHER_VERSION
    bt = {x["web_district_id"]: x for x in best_tk}; bu = {x["web_district_id"]: x for x in best_tuik}
    # --- önerilen GEO_ kimlikleri (web poligonu çapa; TKGM/TÜİK bağlantıları band ile) ---
    mapping = []; geo_no = 0
    for wid, wad, wn, il_n, ilce_n, county_id, pop, g in sorted(web, key=lambda r: (r[3], r[4], r[0])):
        geo_no += 1; gid = f"GEO_{geo_no:06d}"
        t = bt.get(wid); u = bu.get(wid)
        mapping.append({"geo_id": gid, "level": "mahalle", "il_n": il_n, "ilce_n": ilce_n, "anchor_source": "web_polygon", "web_district_id": wid, "web_name": wad,
                        "tkgm_id": t["tkgm_id"] if t and t["band"] not in ("no_auto_merge",) else None, "tkgm_band": t["band"] if t else "no_candidate", "tkgm_score": t["score"] if t else None,
                        "tuik_kodu": u["tuik_kodu"] if u and u["band"] not in ("no_auto_merge",) else None, "tuik_band": u["band"] if u else "no_candidate", "tuik_score": u["score"] if u else None,
                        "status": "PROPOSED (insan onayı bekliyor)", "matcher_version": MATCHER_VERSION, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    (OUT / "mappings").mkdir(exist_ok=True); (OUT / "validation" / "golden").mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(cand_tk), OUT / "mappings" / "geo_candidates_web_tkgm_v1.parquet")
    pq.write_table(pa.Table.from_pylist(cand_tuik), OUT / "mappings" / "geo_candidates_web_tuik_v1.parquet")
    pq.write_table(pa.Table.from_pylist(mapping), OUT / "mappings" / "geo_id_mapping_v1_PROPOSED.parquet")
    (OUT / "mappings" / "geo_matcher_v1_config.json").write_text(json.dumps({"matcher_version": MATCHER_VERSION, "thresholds": THRESHOLDS, "weights_web_tkgm": WEIGHTS_TK, "weights_web_tuik": WEIGHTS_TUIK,
        "name_normalization": "lower, Türkçe harf eşleme, parantez içi ve mah/mahallesi/köyü/beldesi ekleri atılır, alfasayısal dışı silinir", "candidate_generation": "aynı il+ilçe & JW≥0.80; TKGM için ayrıca aynı ilde centroid-içinde/kesişen poligonlar",
        "population_consistency": "1 - min(1, |web2024/tuik2025 - 1| / 0.30)", "seed": 20260918, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, indent=1))
    # --- rapor ---
    import collections
    bands_tk = collections.Counter(x["band"] for x in best_tk); bands_tuik = collections.Counter(x["band"] for x in best_tuik)
    no_tk = len(web) - len(best_tk); no_tuik = len(web) - len(best_tuik)
    # golden örneklem: her banttan 40 (tkgm) + 40 (tuik) → 200'e tamamla
    gold = []
    for name, pool in (("web_tkgm", best_tk), ("web_tuik", best_tuik)):
        by = collections.defaultdict(list)
        for x in pool: by[x["band"]].append(x)
        for b, lst in by.items():
            for x in random.sample(lst, min(25, len(lst))):
                gold.append({"pair_type": name, "band": b, **{k: v for k, v in x.items() if k not in ("matcher_version",)}, "same_entity(human)": "", "verified_by": "", "verified_at": "", "note": ""})
    import csv
    with open(OUT / "validation" / "golden" / "geo_golden_sample_v1.csv", "w", newline="", encoding="utf-8") as f:
        keys = sorted({k for g in gold for k in g.keys()}, key=lambda k: (k not in ("pair_type", "band", "score"), k))
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); [w.writerow(g) for g in gold]
    rep = f"""# geo_matcher v1 — RAPOR ({dt.datetime.now().date()})

Kaynaklar: web mahalle poligonu {len(web):,} · TKGM {len(tk):,} · TÜİK {len(tuik):,}
Adaylar: web↔TKGM {len(cand_tk):,} · web↔TÜİK {len(cand_tuik):,} (fiziksel birleştirme yok; hepsi `mappings/geo_candidates_*_v1.parquet`)

## En iyi bire-bir eşleşme (web mahallesi başına)
| Band | web↔TKGM | web↔TÜİK |
|---|---|---|
""" + "\n".join(f"| {b} | {bands_tk.get(b,0):,} | {bands_tuik.get(b,0):,} |" for b in ("virtually_certain", "very_high", "high", "review_recommended", "no_auto_merge")) + f"""
| aday yok | {no_tk:,} | {no_tuik:,} |

Eşikler: {THRESHOLDS} · Ağırlıklar: TKGM {WEIGHTS_TK} · TÜİK {WEIGHTS_TUIK} (mappings/geo_matcher_v1_config.json)

## Önerilen kimlikler
`mappings/geo_id_mapping_v1_PROPOSED.parquet`: {len(mapping):,} `GEO_` (çapa: web poligonu). TKGM bağlantısı önerilen: {sum(1 for m in mapping if m['tkgm_id']):,}; TÜİK bağlantısı önerilen: {sum(1 for m in mapping if m['tuik_kodu']):,}.
Durum: PROPOSED — otomatik uygulanmadı. Golden set: `validation/golden/geo_golden_sample_v1.csv` ({len(gold)} çift, band başına ≤25) → insan `same_entity(human)` sütununu doldurur → precision/recall/F1.

## Bilinen sınırlar
- TKGM poligonu olmayan 2.776 birim yalnız adla eşlenir (skor tavanı 0,94 → en iyi ihtimalle review).
- TÜİK'te geometri yok; nüfus tutarlılığı 2024↔2025 farkı nedeniyle ±30% toleranslı.
- Aynı ad farklı yer (kadastro köyü ≠ idari mahalle) IoU≈0 ile düşük skor alır; bunlar `no_auto_merge`.
Süre: {time.time()-t0:.0f}s
"""
    (OUT / "validation" / "geo_matcher_v1_report.md").write_text(rep, encoding="utf-8"); print(rep)


if __name__ == "__main__":
    main()
