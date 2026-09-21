# Canonical v1.1 — KURULUM ve BÜTÜNLÜK RAPORU (2026-09-19)

DB: canonical/v1.1/geoprop_canonical_v1_1.duckdb (2.790 MB) · taban: v1 (dokunulmadı) · 455 sn · son (tam) kurulum 2026-09-19 ~14:40 UTC · durum PROPOSED

## Neden v1.1
Kullanıcı sorusu: "her mahalle için fiyat vardı, %32 ne demek?" → v1 yalnızca 1 fiyat tablosunu (BOLGE_CSV aylık trend, 17.119 mahalle) almıştı; staging'de 4 tablo daha vardı.
Kullanıcı sorusu: "atanamayan işletmelerin koordinatı yok mu?" → koordinat var ama 7.782'si geçersiz (tam sayı derece 7.285, Türkiye dışı 401, lon=lat 96); v1'de 1.752 geçersiz koordinatlı kayıt şans eseri bir poligona düşüp yanlış atanmıştı.

## Fiyat genişletmesi (muhasebe: hücre = yazılan + kopya indirgeme; hepsi eşit)
| Kaynak | Satır | Yazılan gözlem | Kopya indirgeme | Bağlanamayan |
|---|---|---|---|---|
| stg_land_summary (arsa/tarla özet 2026-08, 12 metrik) | 248.880 | 1.046.844 | 1.570.266 (5 klasör kopyası) | 0 |
| stg_price_summary (konut özet, 10 metrik) | 72.542 | 131.419 | 274.937 | 0 |
| stg_ej_region_index (konut+arsa endeks, 19 metrik) | 116.918 | 558.871 | 706.891 | 10 (ülke seviyesi; geo varlığı yok) |
| stg_land_trend_monthly (arsa/tarla aylık 2021-01→2027-08, 10 metrik) | 8.149.840 | 23.235.337 | 46.413.610 (3 paket kopyası + collector) | 0 |
Aynı anahtar + aynı kaynak güncelleme zamanı + farklı değer çelişkisi: 0. price_observation toplam **29.720.106** (v1: 4.747.635). Arsa aylık seri: 24.386 mahalle × 68 ölçülmüş ay (2021-01→2026-08) + projeksiyon.
Yeni sütunlar: source_table, source_updated_at, n_copies. analytics.price_latest_cutoff = (geo, kategori, metrik) başına en güncel kaynak gözlemi.

## Mahalle kapsamı (50.638)
| | v1 | v1.1 |
|---|---|---|
| TÜİK 2025 nüfus | 46.355 (%91,5) | 46.355 |
| konut fiyatı 2026-08 | 16.452 (%32,5) | **17.264** |
| arsa/tarla fiyatı 2026-08 | — | **31.138** |
| herhangi bir fiyat 2026-08 | 16.452 | **32.384 (%64,0)** |
| herhangi bir dönemde fiyat | | 33.095 |
| ≥1 güvenilir POI | 20.381 | 18.536 (yanlış/zayıf atamalar çıkarıldı) |
Kalan ~17,5 K mahalle hiçbir fiyat kaynağında yok (köy statüsü; ilan yok).

## POI atama (584.357)
- coord_validity: valid 576.575 · INTEGER_DEGREE 7.285 · OUT_OF_TURKEY 401 · LON_EQ_LAT 96
- v1 atamasının iptali (geçersiz koordinat): 1.752
- Adres bağlamı ayrıştırma ("X Mahallesi İlçe İl"): il+ilçe+mahalle eşleşen 554.988 · il+mahalle tekil 392 · bulunamayan 12.563 · kalıp yok 15.482 · il yok 809 · belirsiz 123
- **Bağlam güvenilirliği (geçerli koordinatlılarla ölçüldü, 545.887 çift): aynı mahalle %33,1 · aynı ilçe %93,0 · aynı il %99,3** → search_context ataması mahalle güveni 0,33, ilçe güveni 0,93.
- Sonuç: ST_Contains 574.377 (güven 0,95; bağlam mahallesiyle aynıysa 0,99; il çelişkisi varsa 0,50 — 4.044 kayıt) · search_context 9.493 (0,33; ilçe düzeyinde kullanılabilir) · atanamayan 487
- Yeni sütunlar: coord_validity, spatial_geo_id, context_geo_id, context_match, assignment_confidence, assignment_flags, assigned_ilce_geo_id, ilce_assignment_confidence
- analytics.mahalle_poi_stats yalnız güven ≥ 0,6 atamaları sayar.

## TÜİK nüfus tür düzeltmesi (kullanıcı sorusu sırasında bulundu)
TÜİK mahalle ve köy tablolarının kod alanları çakışıyor (2.731 ortak kod). v1 nüfusu yalnız `tuik_kodu` eşitliğiyle bağladığı için 2.729 gözlem yanlış birime yapışmıştı
(ör. Akçaören Köyü/Patnos: gerçek 138–146 kişi, yanlış 45.079). geo_entity'ye `tuik_tur` eklendi; türle uyuşmayan 975 (mahalle tablosu) + 1.754 (köy tablosu) gözlem kaldırıldı.
Doğrulama: web 2024 / TÜİK 2025 oranı 3 katı aşan mahalle 609 → **0**. population_observation 117.480 → 114.751. Kapsam değişmedi (46.355 mahallede TÜİK 2025).

## Fiyat serisi kapsamı — "neredeyse her mahalle" doğrulaması
| Birim türü (TÜİK) | seri VAR | YOK | yok olanların ort. nüfusu |
|---|---|---|---|
| mahalle | 22.053 | 6.839 | 417 |
| köy | 8.724 | 8.739 | 326 |
| TÜİK bağı yok | 2.475 | 1.808 | 232 |
Fiyatsız 17.386 birimin 14.473'ü <500 kişi, 2.612'si 500–2.000, yalnız 301'i >2.000. Staging dışı kalan tek büyük paket (VERİLER/TURKIYE-ARSA-TARLA-ENDEKS-40-MAKINE-PAKETI.zip, iç içe zip) incelendi: staged arsa paketinin birebir kopyası (yeni veri yok; Phase 1 iç içe zip'e girmiyor → not).

## Bütünlük — GEÇTİ
yetim price geo_id 0 · yetim poi atama 0 · observation_id tekil ✅ · parsed_value NULL 0 · name_norm düzeltmesi 5.280 kayıt (büyük harfli Ç/Ş/Ğ/Ö/Ü/İ)

## Açık
1. ~~land_trend_monthly~~ eklendi (önbellek temizliği sonrası)
2. 12.563 "mahalle bulunamadı" bağlam adresi: TKGM/TÜİK ad eşlemesiyle (geo_matcher) ikinci tur
3. Golden set onayı → PROPOSED→APPROVED

## v1.2 — POI kaynak birleştirme (kullanıcı kararı 2026-09-19: "tamamen aynı yerleri birleştir, tek kaynaklıları ayrı belirt")
Aynı dosya üzerinde yükseltme (disk kısıtı; v1 taban dosyası dokunulmadı). `poi.source_coverage`: **google_only 575.186 · google+osm 9.171 · osm_only 68.151** (adlı 58.061, adsız 10.090; 67.972'si mahalleye atandı; 67.585'i OSM etiketinden kategorilendi).
Birleştirme yalnız poi_matcher v1 high+ (skor ≥ 0,95). Google kaydı birincil (poi_id korunur); OSM alanları (osm_id, amenity, cuisine, telefon, web, çalışma saati, koordinat) yanına eklendi.
`poi_source_link` (666.835): google:primary 584.357 · osm:merged 9.171 · osm:primary 68.151 · osm:review 5.156 (birleştirilmedi; iki kayıt ayrı, bağ saklı — golden set sonrası karar).
Muhasebe: OSM giriş 77.322 = birleşen 9.171 + osm_only 68.151 ✅ · poi_id tekil ✅ · osm_id tekil ✅ · yetim bağ 0 · yetim atama 0. poi toplam 652.508. OSM kayıtları ODbL atıf bayrağı taşır.

## v1.3 — OSM zaman serisi ve kaynak-tutarlı turnover (kullanıcı kuralı 2026-09-19)
Kural: **zaman karşılaştırması yalnız aynı kaynak içinde.** Google verisi tek dönem (2026-09; 1,54 M kesit satırının tamamı aynı ay) → Google'dan turnover türetilmez. 2025 OSM ↔ 2026 Google varlık farkı asla "yeni açıldı/kapandı" değildir (kapsam farkı).
- `poi_lifecycle_osm` 680.481 (aktif 623.793 · kaldırıldı 56.688; 665.827 mahalleye atandı; 70.382'si poi kaydına bağlı). Alanlar: ilk/son görülme, ad/marka değişimi, taşınma, geçmiş JSON, sol-sansür bayrağı (358.315 kayıt 2021-01-01'de zaten vardı).
- `poi_presence_osm` 2.844.718: 6 kesit (2021-01-01…2026-09-13) × POI varlığı; kesit sayıları staging ile birebir.
- `analytics.mahalle_turnover_osm`: mahalle × kesit × kategori grubu (all, yeme_icme, perakende, hizmet, saglik, konaklama, egitim, sanayi, diger): haritada mevcut / eklenen / kaldırılan / net / churn. Yöntem etiketi `OSM_MAP_PRESENCE` — harita varlığı, işletme açılış-kapanışının **vekili** (haritacı eklemesi ≠ açılış tarihi).
- Ulusal örnek (all): 2022 +32.160/−5.749 · 2023 +47.150/−4.579 · 2024 +86.896/−9.899 · 2025 +58.074/−11.778 · 2026-09 +93.788/−13.654 (eklenmelerin büyümesi kısmen haritalama yoğunluğunun artmasıdır — yorumlarken bu belirtilir).
- Şirinyalı (Antalya) yeme-içme: 37→42 haritada; 2022–2026 her yıl +1/+2, kaldırılan 0.
- mahalle_intelligence yeni sütunlar: poi_google_osm_count, poi_osm_only_count, osm_poi_on_map_2026, osm_added_2025_2026, osm_removed_2025_2026, osm_map_churn_2025_2026.
Ürün notu: POI her zaman nokta (lon/lat) düzeyinde sunulur — sayılar türev; poi/poi_lifecycle_osm/poi_presence_osm koordinat taşır.

## v1.4 — Yemeksepeti üçüncü POI kaynağı + menü/teslimat gözlemleri (2026-09-19)
Kaynak: toplayıcı kesiti (politika gereği durmuş; 513 restoran sayfası / 485 farklı restoran, 420'sinde JSON-LD koordinat; 29.340 menü kalemi). Staging'de iç içe JSON alanları `to_json` ile korunur (önceki repr'li batch _invalidated'a taşındı; audit).
Eşleştirme (poi_matcher kanıt modeli, 250 m, ad+mesafe+kategori): very_high 17 · high 11 · review 16 · no_auto 42 → **28 restoran mevcut kayda bağlandı** (google+yemeksepeti 21, osm+yemeksepeti 7); 457 restoran `yemeksepeti_only` yeni kayıt (93'ü koordinatsız — JSON-LD yok, `coord_validity=MISSING`).
Yeni tablolar: `venue_delivery_observation` 513 (puan, puan sayısı, min sepet, teslimat süresi alt/üst, ücret toplam/orijinal, sağlayıcı, saatler; not: platformun varsayılan konum tahmini) · `menu_item_observation` 29.340 (29.332 fiyatlı; başlık, açıklama, kategori, indirimli fiyat, tükendi). Hepsi poi_id'ye bağlı (bağsız 0).
Kapsam etiketi artık çoklu: google_only 575.165 · osm_only 68.144 · google+osm 9.171 · yemeksepeti_only 457 · google+yemeksepeti 21 · osm+yemeksepeti 7. Bütünlük: poi_id tekil ✅ · YS muhasebesi 485 = 28 + 457 ✅ · yetim bağ 0.
Yorum: YS restoranlarının çoğu Google koleksiyonunda yok (sanal mutfak / kategori aramasına girmeyen işletmeler) — kapsam farkı, §6.4 gereği açılış olarak yorumlanmaz.

## v1.5 — Resmî bağlam serileri + POI güncellemeleri (2026-09-21)
`indicator_observation` (yeni, uzun biçim): **332.363 gözlem**, hepsi resmî (official_public, öncelik 100), kaynaklar arası birleştirme yok:
| Alan | Gözlem | Kaynak | Doğrulama |
|---|---|---|---|
| housing_sales | 159.312 | TÜİK MEDAS ilçe×ay 2013→2026-07 (revize seri) + TÜİK bülten xls ilçe yıllık 2015–2025 (revizyon öncesi, ayrı series_version) | 2025 Oca–Ağu toplamı **1.020.207 = bülten** ✅ |
| construction_permit | 124.466 | yapı izin il (çeyrek + yıl satırları, period_kind ayrı) · ilçe yıl · ruhsat kullanım amacı m² | 2019–2025 ulusal + 81 il birebir ✅ |
| banking | 26.244 | BDDK FinTürk il × dönem (4 tablo) | Adana 2024-12 birebir ✅ |
| card_spending | 8.970 | BKM Türkiye geneli sektör × ay/yıl (geo_id GEO_TR) | 2024-11 birebir ✅ |
| migration | 6.885 | TÜİK iller arası göç (il, 2008–2024) | 2024 toplam + 13 il birebir ✅ |
| socio_economic | 6.324 | TÜİK SES 2023 (il + ilçe) | yapısal |
| household | 162 | TÜİK NKS 2021 hanehalkı | yapısal |
Bağlanamayan: yapı izin ilçe 84 + ruhsat amacı 351 satır (bizim 973 ilçe listesinde olmayan TÜİK kodları — kapatılmış/birleşmiş ilçeler; raporda muhasebeleşti).
POI: `source_id_kind` (collector_hash 568.072 · google_place_id 16.178 · osm_ref 68.151 · yemeksepeti 457) · v3 kategori high bandı uygulandı (2.156; review adayları `poi_category_candidate` 4.932) · sektör ipucu 33.330 kayıtta · koordinat kurtarma 0 (kimlik ad+koordinattan türetildiği için aynı kimliğin başka gözlemi de aynı bozuk koordinatı taşıyor — beklenen).
Analitik: `analytics.ilce_intelligence` (nüfus, konut satış 2024/2025/son 12 ay, yapı ruhsatı & kullanma izni 2025, SES, POI/restoran sayıları — il düzeyi güvenilir atamayla) · `ilce_poi_stats` · mahalle_poi_stats'a `poi_type_unknown_count`.
Örnek — Kadıköy: nüfus 458.573 · konut satışı 2025: 12.188 · son 12 ay 11.197 · yapı ruhsatı 2025: 8.661 daire · SES 176,2 · 4.889 işletme (649 restoran).
