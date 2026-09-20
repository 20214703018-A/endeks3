#!/usr/bin/env python3
"""
geo_matcher SKOR v2 — v1 adaylarının sinyallerini (name_similarity, iou, centroid_inside, same_ilce, population_consistency) noisy-OR ile birleştirir.
Neden: v1 toplamsal ağırlık, birbirini ikame eden kanıtları (ad birebir + merkez içinde) IoU düşük diye cezalandırıyordu.
score = 1 − Π(1 − e_i); çelişki cezaları çarpımsal. Sinyaller yeniden hesaplanmaz; v1 aday dosyaları değişmez.
Çıktı: mappings/geo_candidates_*_v2.parquet, mappings/geo_id_mapping_v2_PROPOSED.parquet, validation/geo_matcher_v2_report.md, validation/golden/geo_golden_sample_v2.csv
"""
import json, random, csv, collections, datetime as dt, hashlib
from pathlib import Path
import duckdb, pyarrow as pa, pyarrow.parquet as pq

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
V = "GEO_MATCHER_V2"
TH = {"virtually_certain": 0.995, "very_high": 0.980, "high": 0.950, "review": 0.850}
random.seed(20260918)


def band(s):
    for k, t in TH.items():
        if s >= t: return k if k != "review" else "review_recommended"
    return "no_auto_merge"


def score_tk(r):
    """r: name_similarity, iou (None=geometri yok), centroid_inside, same_ilce"""
    name, iou, inside, same = r["name_similarity"], r["iou"], r["centroid_inside"] or 0.0, r["same_ilce"]
    ev = []; reasons = []
    name_ev = 0.90 * max(0.0, (name - 0.80) / 0.20) if same else 0.60 * max(0.0, (name - 0.80) / 0.20)
    if name_ev > 0: ev.append(name_ev); reasons.append(f"name_ev={name_ev:.2f}(jw={name:.3f},same_ilce={same})")
    if inside: ev.append(0.80); reasons.append("centroid_inside_ev=0.80")
    if iou is not None:
        iou_ev = 0.0 if iou < 0.20 else min(0.985, iou); ev.append(iou_ev); reasons.append(f"iou_ev={iou_ev:.3f}")
    p = 1.0
    for e in ev: p *= (1 - e)
    s = 1 - p
    flags = []
    if iou is not None and iou < 0.20 and not inside and name >= 0.95:
        s *= 0.60; flags.append("CONFLICT_same_name_disjoint_geometry")
    if iou is None:
        s = min(s, 0.94); flags.append("TKGM_NO_GEOMETRY_name_only")
    if name < 0.70 and s >= 0.95:
        s = min(s, 0.985); flags.append("NAME_MISMATCH_geometry_match(renamed?)")
    return round(s, 4), "; ".join(reasons), ",".join(flags)


def score_tuik(r):
    name, pc, same = r["name_similarity"], r["population_consistency"], True
    ev = []; reasons = []
    name_ev = 0.90 * max(0.0, (name - 0.80) / 0.20)
    if name_ev > 0: ev.append(name_ev); reasons.append(f"name_ev={name_ev:.2f}(jw={name:.3f})")
    if pc is not None:
        pc_ev = 0.85 * pc; ev.append(pc_ev); reasons.append(f"pop_ev={pc_ev:.2f}(consistency={pc:.2f})")
    p = 1.0
    for e in ev: p *= (1 - e)
    s = 1 - p; flags = []
    if pc is not None and pc == 0.0 and name >= 0.95:
        s *= 0.70; flags.append("CONFLICT_population_ratio_out_of_30pct")
    if pc is None: flags.append("NO_POPULATION_web_side")
    return round(s, 4), "; ".join(reasons), ",".join(flags)


def main():
    c = duckdb.connect()
    ct = [dict(zip(["web_district_id", "tkgm_id", "web_name", "tkgm_name", "il_n", "web_ilce_n", "tkgm_ilce_n", "name_similarity", "iou", "centroid_inside"], r))
          for r in c.execute("SELECT web_district_id, tkgm_id, web_name, tkgm_name, il_n, web_ilce_n, tkgm_ilce_n, name_similarity, iou, centroid_inside FROM 'mappings/geo_candidates_web_tkgm_v1.parquet'").fetchall()]
    cu = [dict(zip(["web_district_id", "tuik_kodu", "web_name", "tuik_name", "tuik_tur", "il_n", "ilce_n", "web_pop_2024", "tuik_pop_2025", "name_similarity", "population_consistency"], r))
          for r in c.execute("SELECT web_district_id, tuik_kodu, web_name, tuik_name, tuik_tur, il_n, ilce_n, web_pop_2024, tuik_pop_2025, name_similarity, population_consistency FROM 'mappings/geo_candidates_web_tuik_v1.parquet'").fetchall()]
    # ad tekilliği (il+ilçe içinde) ek kanıt
    for x in ct:
        x["same_ilce"] = (x["web_ilce_n"] == x["tkgm_ilce_n"])
        x["score"], x["reasons"], x["flags"] = score_tk(x); x["band"] = band(x["score"]); x["matcher_version"] = V
    for x in cu:
        x["score"], x["reasons"], x["flags"] = score_tuik(x); x["band"] = band(x["score"]); x["matcher_version"] = V
    def best(cands, a, b):
        ua, ub, out = set(), set(), []
        for x in sorted(cands, key=lambda z: -z["score"]):
            if x[a] in ua or x[b] in ub: continue
            ua.add(x[a]); ub.add(x[b]); out.append(x)
        return out
    bt = {x["web_district_id"]: x for x in best(ct, "web_district_id", "tkgm_id")}
    bu = {x["web_district_id"]: x for x in best(cu, "web_district_id", "tuik_kodu")}
    m1 = [dict(zip(["geo_id", "level", "il_n", "ilce_n", "web_district_id", "web_name"], r)) for r in c.execute("SELECT geo_id, level, il_n, ilce_n, web_district_id, web_name FROM 'mappings/geo_id_mapping_v1_PROPOSED.parquet' ORDER BY geo_id").fetchall()]
    mapping = []
    for m in m1:
        t = bt.get(m["web_district_id"]); u = bu.get(m["web_district_id"])
        mapping.append({**m, "anchor_source": "web_polygon",
                        "tkgm_id": t["tkgm_id"] if t and t["band"] != "no_auto_merge" else None, "tkgm_band": t["band"] if t else "no_candidate", "tkgm_score": t["score"] if t else None, "tkgm_flags": t["flags"] if t else None,
                        "tuik_kodu": u["tuik_kodu"] if u and u["band"] != "no_auto_merge" else None, "tuik_band": u["band"] if u else "no_candidate", "tuik_score": u["score"] if u else None, "tuik_flags": u["flags"] if u else None,
                        "status": "PROPOSED (insan onayı bekliyor)", "matcher_version": V, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    pq.write_table(pa.Table.from_pylist(ct), OUT / "mappings" / "geo_candidates_web_tkgm_v2.parquet")
    pq.write_table(pa.Table.from_pylist(cu), OUT / "mappings" / "geo_candidates_web_tuik_v2.parquet")
    pq.write_table(pa.Table.from_pylist(mapping), OUT / "mappings" / "geo_id_mapping_v2_PROPOSED.parquet")
    (OUT / "mappings" / "geo_matcher_v2_config.json").write_text(json.dumps({"matcher_version": V, "thresholds": TH, "combination": "noisy-OR: 1-Π(1-e_i)",
        "evidence": {"name": "0.90×(jw−0.80)/0.20 aynı ilçede (0.60 farklı ilçede); jw<0.80 → 0", "centroid_inside": 0.80, "iou": "iou (≥0.20; <0.20 → 0; tavan 0.985)", "population_consistency": "0.85×(1−min(1,|oran−1|/0.30))"},
        "penalties": {"CONFLICT_same_name_disjoint_geometry": "×0.60 (ad≥0.95, IoU<0.20, merkez dışarıda)", "CONFLICT_population_ratio_out_of_30pct": "×0.70", "TKGM_NO_GEOMETRY": "tavan 0.94", "NAME_MISMATCH_geometry_match": "tavan 0.985 + bayrak"},
        "seed": 20260918, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "signals_from": "GEO_MATCHER_V1 aday dosyaları (yeniden hesaplanmadı)"}, ensure_ascii=False, indent=1))
    b_tk = collections.Counter(x["band"] for x in bt.values()); b_tu = collections.Counter(x["band"] for x in bu.values())
    fl = collections.Counter(f for x in bt.values() for f in x["flags"].split(",") if f)
    gold = []
    for name, pool in (("web_tkgm", list(bt.values())), ("web_tuik", list(bu.values()))):
        by = collections.defaultdict(list)
        for x in pool: by[x["band"]].append(x)
        for bnd, lst in by.items():
            for x in random.sample(lst, min(25, len(lst))):
                gold.append({"pair_type": name, **{k: v for k, v in x.items() if k != "matcher_version"}, "same_entity(human)": "", "verified_by": "", "verified_at": "", "note": ""})
    with open(OUT / "validation" / "golden" / "geo_golden_sample_v2.csv", "w", newline="", encoding="utf-8") as f:
        keys = sorted({k for g in gold for k in g}, key=lambda k: (k not in ("pair_type", "band", "score"), k)); w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); [w.writerow(g) for g in gold]
    n = len(mapping)
    rep = f"""# geo_matcher v2 — RAPOR ({dt.datetime.now().date()})
Sinyaller v1'den (307.298 web↔TKGM, 77.603 web↔TÜİK aday); birleştirme noisy-OR (mappings/geo_matcher_v2_config.json).

| Band | web↔TKGM | web↔TÜİK |
|---|---|---|
""" + "\n".join(f"| {b} | {b_tk.get(b,0):,} | {b_tu.get(b,0):,} |" for b in ("virtually_certain", "very_high", "high", "review_recommended", "no_auto_merge")) + f"""
| aday yok | {n-len(bt):,} | {n-len(bu):,} |

Otomatik önerilen bağlantı (high+): TKGM {sum(1 for b in b_tk.elements() if b in ('virtually_certain','very_high','high')):,} / {n:,} · TÜİK {sum(1 for b in b_tu.elements() if b in ('virtually_certain','very_high','high')):,} / {n:,}
Bayraklar (TKGM en iyi eşleşme): {dict(fl.most_common())}
Golden set v2: validation/golden/geo_golden_sample_v2.csv ({len(gold)} çift). Durum: PROPOSED; uygulanmadı.
"""
    (OUT / "validation" / "geo_matcher_v2_report.md").write_text(rep, encoding="utf-8"); print(rep)


if __name__ == "__main__":
    main()
