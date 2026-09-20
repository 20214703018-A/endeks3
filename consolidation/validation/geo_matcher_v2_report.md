# geo_matcher v2 — RAPOR (2026-09-18)
Sinyaller v1'den (307.298 web↔TKGM, 77.603 web↔TÜİK aday); birleştirme noisy-OR (mappings/geo_matcher_v2_config.json).

| Band | web↔TKGM | web↔TÜİK |
|---|---|---|
| virtually_certain | 27,717 | 0 |
| very_high | 11,199 | 13,826 |
| high | 1,357 | 27,207 |
| review_recommended | 4,868 | 5,322 |
| no_auto_merge | 1,890 | 1,180 |
| aday yok | 3,607 | 3,103 |

Otomatik önerilen bağlantı (high+): TKGM 40,273 / 50,638 · TÜİK 41,033 / 50,638
Bayraklar (TKGM en iyi eşleşme): {'TKGM_NO_GEOMETRY_name_only': 1878, 'CONFLICT_same_name_disjoint_geometry': 919, 'NAME_MISMATCH_geometry_match(renamed?)': 780}
Golden set v2: validation/golden/geo_golden_sample_v2.csv (225 çift). Durum: PROPOSED; uygulanmadı.
