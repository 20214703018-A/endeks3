# Canonical v1 — BÜTÜNLÜK ve KAPSAM RAPORU (2026-09-19)

DB: canonical/v1/geoprop_canonical_v1.duckdb (837 MB) + canonical/v1/parquet/*.parquet · model_version=canonical_v1 · STANDARD 1.0.0 · kurulum 103 sn
Durum: **PROPOSED** — mahalle bağlantıları geo_matcher v2 (insan golden set onayı bekliyor), POI kategorileri v2 (aynı).

## 1. Referans bütünlüğü (STANDARD §4.1) — HEPSİ GEÇTİ
| Test | Sonuç |
|---|---|
| geo_id tekil | 51.692 / 51.692 ✅ |
| parent_geo_id yetim | 0 ✅ |
| population_observation.geo_id yetim | 0 / 117.480 ✅ |
| price_observation.geo_id yetim | 0 / 4.747.635 ✅ |
| price tekrarlayan anahtar (geo, kategori, metrik, dönem, tür, dosya) | 0 ✅ |
| price parsed_value NULL | 0 ✅ |
| poi_id tekil | 584.357 / 584.357 ✅ |
| poi_snapshot yetim poi_id | 0 / 1.544.193 ✅ |
| poi.assigned_geo_id yetim | 0 ✅ |

## 2. Kapsam
- Mahalle 50.638: TÜİK 2025 nüfusu olan 46.355 (%91,5) · konut fiyatı 2026-08 olan 16.452 (%32,5) · en az bir POI olan 20.381 (%40,2)
- Nüfus: TÜİK ilçe serisi 2007–2025 (900→973 ilçe/yıl), TÜİK mahalle+köy 2025 (50.057), web 2024 (50.145; period_assignment=official_anchor_test, öncelik 60)
- Fiyat: 7 metrik×tür; ölçülmüş 2021-01…2026-08, projeksiyon 2026-09…2027-08 (observation_kind='projected' — ölçülmüşle asla karışmaz). konut 4.528.935 · arsa 218.700
- POI mekânsal atama: 576.129 ST_Contains (14'ü çoklu poligon → ilk seçildi, bayraklı) · 8.228 atanmadı (2.790 Türkiye bbox dışı koordinat, 5.438 hiçbir mahalle poligonuna düşmüyor — kıyı/boşluk; inceleme listesi)
- POI kategori: RULE high 238.844 · SEARCH_HINT 36.742 · model 4.008 · UNRESOLVED 47.498 · zaten kategorili 255.265

## 3. Mekânsal atamanın doğrulanması
Kaynak `il` sütunu güvenilmez (72 K satırda "otomotiv/kuaför/giyim" gibi kategori adı taşıyor). Gerçek il adı taşıyan 320.210 POI'de mekânsal atama ile kaynak il **%99,12 uyumlu** (2.817 uyumsuz; en sık "İzmir/İstanbul/Ankara → Van" — kaynak arama koşusu etiket hatası). Karar: il/ilçe/mahalle yalnızca koordinattan (ST_Contains) türetilir; kaynak il yalnızca `source_il` olarak saklanır.

## 4. Örnek — Antalya/Muratpaşa (analytics.mahalle_intelligence)
| Mahalle | web 2024 | TÜİK 2025 | konut satılık ₺/m² 2026-08 | POI | yeme-içme | ort. puan |
|---|---|---|---|---|---|---|
| Güzeloba | 36.761 | — (TÜİK bağı review) | 74.677 | 199 | 37 | 4,46 |
| Şirinyalı | 17.353 | 17.355 | 96.119 | 196 | 48 | 4,57 |
| Varlık | 9.634 | 9.570 | 66.515 | 169 | 35 | 4,54 |
| Cumhuriyet | 11.305 | 11.256 | 44.961 | 166 | 27 | 4,50 |
| Yeşilbahçe | 16.023 | 15.980 | 83.202 | 165 | 27 | 4,51 |

## 5. Açık maddeler
1. Golden set onayı → mapping PROPOSED→APPROVED (geo_golden_review_v2.html, poi_category_golden_review_v2.html)
2. 5.438 poligon-dışı POI: en yakın mahalle (mesafe ≤ 250 m) ile "nearest" ataması önerilir — ayrı yöntem etiketiyle
3. 4.283 mahallede TÜİK 2025 nüfusu yok (bağ review/no_auto)
4. listing_matcher, poi_matcher (OSM↔Google↔Yemeksepeti) — canonical v1.1
