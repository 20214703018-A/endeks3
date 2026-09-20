# poi_matcher v1 — Google ↔ OSM (2026-09-19)
Girdi: OSM gıda POI 77,322 (adı olan 67,140) · Google POI (geçerli koordinat) 576,575 · 250 m içinde çift 3,274,348 · aday (ad≥0.75 veya telefon) 38,004
Bire-bir eşleşme: 20,595 · otomatik önerilen (high+): 9,171 (13.7% OSM adlı kayıt) · yüksek bant mesafe medyan 13.0 m, p90 39.0 m
| Band | adet |
|---|---|
| virtually_certain | 12 |
| very_high | 6,216 |
| high | 2,943 |
| review_recommended | 5,156 |
| no_auto_merge | 6,268 |

Bayraklar: {'WEAK_NAME': 4309, 'PHONE_MATCH': 15, 'FAR_gt120m': 3031, 'CATEGORY_CONFLICT_google_nonfood(x0.55)': 2420}
Eşleşmesiz OSM (adlı): 46,545 — Google koleksiyonu kapsamında olmayan ya da farklı yazımlı işletmeler; poi tablosuna **yeni işletme** adayı (v1.2'de source='osm' olarak eklenecek, eşleşme değil).
Durum: PROPOSED; golden set validation/golden/poi_match_golden_review_v1.html (172 çift). Süre 214s. Yapılandırma mappings/poi_matcher_v1_config.json.
