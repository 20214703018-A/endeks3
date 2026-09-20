# PHASE 2 DRY-RUN — Aile 1: `reference_geography` (il / ilçe / mahalle)

Durum: **DRY-RUN — hiçbir şey yazılmadı.** Uygulama kullanıcı onayı bekliyor.  
Pipeline: 1.0.0 · Standart: v1.0.0 · Tarih: 2026-09-18

Bu aile neden ilk? Diğer bütün aileler (fiyat, nüfus, POI, ilan, TKGM) "hangi mahalle?" sorusuna bu tablolarla cevap verecek. Omurga yanlışsa her şey yanlış bağlanır (STANDARD §4.1).

---

## 1. Kaynaklar (benzersiz içerik bazında; kopyalar alias olarak bağlanır)

| # | Kaynak (birincil kopya) | İçerik | Kayıt | Benzersiz sha | Kopya sayısı | Sınıf |
|---|---|---|---|---|---|---|
| S1 | `GEOPROP/collector/data/poligonlar/city_{1..81}.json` | 81 il dosyası → **973 ilçe poligonu** (`CityId`, `CountyId`) | 973 | 81 | ×2 (tkgm/extension) | web_research |
| S2 | `GEOPROP/collector/data/poligonlar/county_{il}_{ilce}.json` | 973 ilçe dosyası → **51.178 mahalle-düzeyi poligon** (`DistrictId`, `Population`) | 51.178 | 973 | ×2 | web_research |
| S3 | `GEOPROP/warehouse/product/idari_sinirlar.sqlite::sinir` | 81 il + **1.005 "ilçe"** poligonu (GeoJSON metin, `alan_km2`, bbox) — ID yok, ad var | 1.086 | 1 | ×1 | web_research |
| S4 | `…idari_sinirlar.sqlite::mahalle_koordinat` ≡ `collector/mahalle_koordinatlari.json` | 51.171 mahalle merkez noktası (`resmi_id`=S2 `DistrictId`, slug, lat/lon) — **birebir aynı içerik** | 51.171 | 2 (db+json) | json ×4, db ×1 | web_research |
| S5 | `collector/turkiye_il_ilce_rehberi.json` | il → ilçe adı rehberi (plaka anahtarlı) | 81 il / 973 ilçe | 1 | ×6 | web_research |
| S6 | `VERİLER/…/TUM_TURKIYE_TEK_PAKET_CSV 4/14_mahalleler_listesi.csv` + 15 bölge parçası | mahalle listesi (`district_id, county_id, city_id, district_name, created_at`) | 19.406 (ulusal) + bölgeler | 16 | ×2–3 | web_research |
| S7 | `warehouse/product/bolge_istatistik.sqlite::ref_mahalle` | mahalle referansı | 36.582 | 1 | ×1 | web_research |
| S8 | `tkgm/data/nuts3.json`, `nuts4.json` | 81 il / 973 ilçe poligon (**5 geçersiz geometri**) | 1.054 | 2 | ×1 | web_research |
| S9 | `GEOPROP/warehouse/silver/reference_geography/{province,district,neighbourhood}/*.parquet` | önceki ekibin S1–S7'den türettiği silver (4.860 / 2.844 / 59.029) | 66.733 | 137 | ×2 | derived (GEOPROP) |
| S10 | `warehouse/quarantine_macro/tuik_bolge.sqlite::nufus_mahalle, nufus_ilce` | mahalle/ilçe nüfus (32.254 / 18.344) — önceki ekip karantinaya almış (Q08) | 50.598 | 1 | ×1 | web_research → **karantinada kalır** |

Toplam benzersiz kaynak dosya: **1.214** · ham boyut: ~430 MB (S2 390 MB) · kopyalarla 2.480 dosya.

---

## 2. Bulgular ve çelişkiler (dry-run sırasında ölçüldü)

| # | Bulgu | Sayı | Ne yapılacak |
|---|---|---|---|
| F1 | S2'de `DistrictId = 0` olan poligonlar: göl, baraj, ada, kayalık ("Van Gölü", "Keban Barajı", "Ada_37", "Kız Kulesi") | **540** | Mahalle DEĞİL. `polygon_kind = 'water_or_island'` ile staging'e alınır (silinmez), canonical `geo_entity`'ye mahalle olarak girmez → `REVIEW_REQUIRED` |
| F2 | S2 mahalle poligonu (id≠0) | 50.638 | asıl mahalle omurgası adayı |
| F3 | S2'de `Population` boş | 1.033 (540'ı F1) | ~493 gerçek mahalle nüfussuz; `raw_value=NULL`, doldurulmaz |
| F4 | S2 ∩ S4 (poligon id ↔ koordinat id) | 50.632 ortak · 7 yalnız poligonda · 0 yalnız koordinatta | 7 mahalle için merkez noktası poligon centroid'inden **türetilebilir** (ayrı sütun, `derived`) |
| F5 | S4: 51.171 satır ama 50.632 benzersiz id | 539 satır aynı id'yi paylaşıyor | Phase 2'de slug bazında incelenir; muhtemelen F1 ile aynı kök |
| F6 | S3 "ilçe" = 1.005, S1/S5/S8 = 973 | +32 | S3'te ada/adacık poligonları ilçe seviyesinde ("Çatalada" 0,75 km²…). S3 **ID içermediği** için omurga olamaz; alan ve alternatif geometri olarak saklanır; ad eşleştirmesi Phase 3 |
| F7 | Nüfus toplamı (S2) | 85.001.912 | TÜİK 2024 ADNKS ≈ 85,37 M → makul; kaynağın nüfus yılı bilinmiyor → `period = NULL, raw_date = NULL, observation_kind = measured, REVIEW: yıl belirsiz` |
| F8 | S8 geçersiz geometri | nuts3: 4, nuts4: 1 | `geometry_validity_status='invalid'`, onarım YOK; Phase 3'te `geometry_repaired` ayrı sütun |
| F9 | Mahalle sayıları kaynaklara göre | S2 50.638 · S4 50.632 · S9 59.029 · S7 36.582 · S6 19.406 · S10 32.254 | Farklı kapsam/vintage; **hiçbiri silinmez**, hepsi `neighbourhood_observation` olarak ayrı kaynak satırı; canonical eşleme Phase 3 |
| F10 | **Resmî kod yok** | — | Hiçbir kaynakta TÜİK ilçe kodu / NVİ mahalle kodu yok; `CityId` = plaka (resmî il koduyla örtüşür), `CountyId/DistrictId` web araştırması ID'si. Canonical `GEO_` ID bizim; resmî kod sütunu boş kalır → **veri boşluğu**, §5'te öneri |

---

## 3. Staging tasarımı (`staging/v1.0.0/reference_geography/`)

Her tablo: `raw_*` (dokunulmamış metin) + `typed_*` + provenance (§4 STANDARD). GeoParquet 1.1, EPSG:4326 (kaynak zaten WGS84 lat/lon).

| Tablo | Kaynak | Satır (tahmin) | Ana sütunlar |
|---|---|---|---|
| `stg_admin_polygon` | S1, S2 | 52.151 | `source_level ∈ {ilce, mahalle}`, `polygon_kind ∈ {admin, water_or_island}`, `raw_city_id, raw_county_id, raw_district_id` (metin), `raw_name, raw_city, raw_county, raw_population`, `typed_population` (int), `raw_coordinates_json` (**orijinal koordinat listesi, byte-birebir**), `geometry_original` (WKB, lat/lon çiftlerinden — TRANSFORM_T01), `geometry_validity_status`, `bbox_*`, `vertex_count`, provenance |
| `stg_admin_boundary_alt` | S3, S8 | 2.140 | `raw_level, raw_name, raw_name_norm, raw_il_adi, raw_alan_km2, raw_geojson_text`, `geometry_original`, validity, provenance |
| `stg_neighbourhood_point` | S4 (db + json ayrı satır) | 102.342 | `raw_slug, raw_resmi_id, raw_name, raw_il, raw_ilce, raw_lat, raw_lon, raw_kaynak, raw_guncellenme`, `geometry_original` (POINT), provenance. *Neden iki kopya da satır olarak?* Aynı içerik olsa da iki ayrı dosyadan geldi; provenance her ikisini de göstermeli; canonical'da tek gözlem sayılır (Phase 3 `DUPC_`) |
| `stg_admin_guide` | S5 | 1.054 | `raw_plaka, raw_il, raw_ilce`, provenance |
| `stg_neighbourhood_list` | S6 (16 dosya), S7 | ~68.000 | `raw_district_id, raw_county_id, raw_city_id, raw_name, raw_created_at`, provenance |
| `stg_reference_geography_derived_ref` | S9 | 66.733 | yeniden yazılmaz; **yalnız manifest referansı + satır sayısı doğrulaması** (`mappings/derived_reference.parquet`). *Neden?* Silver zaten Parquet ve önceki pipeline'ın türevi; kaynağı S1–S7. Yeniden staging'e almak çift sayım yaratır |
| `quarantine/…` | S10 | 50.598 | `ACCEPT_AS_QUARANTINE` (önceki karantina gerekçesi korunur; nüfus verisi Phase 2b'de resmî TÜİK ile karşılaştırılınca karar) |

**Dönüşümler (`transformations/`):**
- `T01_LATLON_PAIRS_TO_WKB_V1`: `[{latitude, longitude}, …]` → `POLYGON((lon lat, …))` WKB. Deterministik; koordinat değeri **değiştirilmez, yuvarlanmaz**; ring kapalı değilse kapatılır ve `ring_closed_by_pipeline=true` işaretlenir. Orijinal liste `raw_coordinates_json`'da byte-birebir kalır.
- `T02_GEOJSON_TEXT_TO_WKB_V1`: S3 `geometri` metni → WKB; orijinal metin `raw_geojson_text`.
- `T03_NORMALIZE_TR_PLACE_NAME_V1`: `raw_name` → `name_norm` (küçük harf, Türkçe karakter eşleme, "mah./mahallesi/köyü" eki ayrı sütuna). Yalnız eşleştirme için; `raw_name` kalır.

---

## 4. Sayım denklemi (dry-run tahmini)

```
S1 input 973      → staged 973      + quarantined 0   + accounted 0   → UNACCOUNTED 0
S2 input 51.178   → staged 51.178   (540'ı polygon_kind=water_or_island) + q 0 → 0
S3 input 1.086    → staged 1.086    → 0
S4 input 102.342  → staged 102.342  → 0
S5 input 1.054    → staged 1.054    → 0
S6 input ~19.406 + bölgeler (kopyalar alias) → staged ≈ 68.000 → 0
S8 input 1.054    → staged 1.054 (5 invalid işaretli) → 0
S10 input 50.598  → quarantined 50.598 → 0
```
Her batch sonunda gerçek sayılar `validation/BATCH_*_balance.json`'a yazılır; 0 değilse batch yazılmaz.

---

## 5. Disk / süre / risk

| | |
|---|---|
| Girdi | ~430 MB benzersiz |
| Tahmini çıktı | **~110–150 MB** (GeoParquet ZSTD; raw_coordinates_json ~60 MB, WKB ~40 MB) |
| Geçici alan | < 1 GB (DuckDB spill) |
| Boş disk | 26 GB → güvenli |
| Süre | ~5–10 dk |
| Kaynak yazma | **yok** (SQLite immutable/tmp-copy; JSON salt okuma) |
| Riskler | F10 resmî kod yokluğu (çözüm §6); F6 S3 ad eşleştirmesi Phase 3'e kalır |

---

## 6. Onay gereken kararlar (her biri: ne demek / neden)

| # | Karar | Önerim | Ne demek / neden |
|---|---|---|---|
| D1 | Su/ada poligonları (F1, 540) | `polygon_kind='water_or_island'` ile staging'e al, canonical mahalle yapma | "Silme" seçeneği kural gereği yok. Karantina yerine staging'de tutmak, ileride "göl kıyısı mahalle" gibi analizlerde işe yarar; sadece mahalle sayılmaz. |
| D2 | Omurga ID sistemi | Canonical `GEO_` ID bizim; `CityId/CountyId/DistrictId` `mappings/id_mapping`'de `id_system='web_research_v1'` olarak | Kaynağın ID'sini canonical yapmak yasak (§2). Resmî kod olmadığı için (F10) `official_code` sütunu NULL açılır. |
| D3 | Resmî kod boşluğu (F10) | Phase 2b'de **yeni veri girişi** olarak resmî TÜİK il/ilçe kodu + NVİ/e-Devlet mahalle listesi edinilmesi (§10.1 kapısından geçer) | Satılabilir üründe alıcının kendi verisiyle birleşmesi için resmî kod gerekir; bunu bugünkü dosyalardan üretmek mümkün değil, uydurmak yasak. |
| D4 | S3 (1.005 ilçe, ID'siz) | Alternatif geometri olarak stage et; Phase 3'te ad+IoU ile S1'e eşle | S3'te `alan_km2` ve bbox var (S1'de yok); iki geometri karşılaştırması Phase 3 polygon-matching için hazır veri. |
| D5 | S9 silver kopyaları | Yeniden staging'e alma; manifest referansı + satır doğrulaması | Çift sayım önlenir; provenance zaten S1–S7'ye gider. Eğer S9'da S1–S7'de olmayan satır bulunursa (doğrulama bunu ölçer) o satırlar `REVIEW`. |
| D6 | S10 (`tuik_bolge`, önceki karantina) | `ACCEPT_AS_QUARANTINE` — izlenebilirlik için al, canonical'a alma; Phase 2b'de resmî TÜİK ile karşılaştır | Önceki ekibin gerekçesi bilinmiyor; kör kabul de kör ret de yanlış. |
| D7 | Nüfus yılı belirsizliği (F7) | `period=NULL` + `REVIEW_REQUIRED: population_year_unknown`; toplam TÜİK 2024'e yakın olsa da yıl **yazılmaz** | Yıl uydurmak = veri kaybından kötü. Phase 2b'de resmî nüfusla eşleşince yıl doğrulanır. |

Onay verilirse sırayla: (1) `stg_admin_polygon` tek il ile pilot (Adana, 15 ilçe + ~700 mahalle) → sayım denklemi + görsel kontrol (harita PNG) → (2) 81 il tam batch → (3) diğer tablolar → (4) `validation/…_accuracy_report.md`.
