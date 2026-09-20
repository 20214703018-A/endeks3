# geo_matcher v1 — RAPOR (2026-09-18)

Kaynaklar: web mahalle poligonu 50,638 · TKGM 49,795 · TÜİK 50,437
Adaylar: web↔TKGM 307,298 · web↔TÜİK 77,603 (fiziksel birleştirme yok; hepsi `mappings/geo_candidates_*_v1.parquet`)

## En iyi bire-bir eşleşme (web mahallesi başına)
| Band | web↔TKGM | web↔TÜİK |
|---|---|---|
| virtually_certain | 2,758 | 3,888 |
| very_high | 9,174 | 9,468 |
| high | 8,506 | 13,275 |
| review_recommended | 12,593 | 15,313 |
| no_auto_merge | 14,066 | 5,588 |
| aday yok | 3,541 | 3,106 |

Eşikler: {'virtually_certain': 0.995, 'very_high': 0.98, 'high': 0.95, 'review': 0.85} · Ağırlıklar: TKGM {'name': 0.4, 'iou': 0.45, 'centroid_inside': 0.15} · TÜİK {'name': 0.65, 'population': 0.35} (mappings/geo_matcher_v1_config.json)

## Önerilen kimlikler
`mappings/geo_id_mapping_v1_PROPOSED.parquet`: 50,638 `GEO_` (çapa: web poligonu). TKGM bağlantısı önerilen: 33,031; TÜİK bağlantısı önerilen: 41,944.
Durum: PROPOSED — otomatik uygulanmadı. Golden set: `validation/golden/geo_golden_sample_v1.csv` (250 çift, band başına ≤25) → insan `same_entity(human)` sütununu doldurur → precision/recall/F1.

## Bilinen sınırlar
- TKGM poligonu olmayan 2.776 birim yalnız adla eşlenir (skor tavanı 0,94 → en iyi ihtimalle review).
- TÜİK'te geometri yok; nüfus tutarlılığı 2024↔2025 farkı nedeniyle ±30% toleranslı.
- Aynı ad farklı yer (kadastro köyü ≠ idari mahalle) IoU≈0 ile düşük skor alır; bunlar `no_auto_merge`.
Süre: 836s
