# GEOPROP VERİ ÜRÜNÜ STANDARDI — v1.0.0

Durum: v1.0.0 — kullanıcı kurallarıyla düzenlendi 2026-09-18 (§12 kararları önerilen değerlerle kapatıldı; kullanıcı itiraz ederse değişir)  
Tarih: 2026-09-18  
Kapsam: Ham veriden satılabilir veri ürününe kadar bütün katmanlar. Bu belge değişirse `standard_version` artar; eski sürüm silinmez (`STANDARD_HISTORY/`).

---

## 0. Değişmez ilkeler (öncelik sırası)

1. Veri bütünlüğü · 2. Kaynak takibi · 3. Tekrarlanabilirlik · 4. Denetlenebilirlik · 5. Doğruluk · 6. Performans · 7. Depolama.

- Ham veri **silinmez, değiştirilmez, yeniden kaydedilmez**. Her işlem append-only.
- `RAW` gerçektir · `STAGING/NORMALIZED` yorumdur · `CANONICAL` modeldir · `ANALYTICS/PRODUCT` sonuçtur. Katmanlar karışmaz.
- Emin olunmayan her şey `REVIEW_REQUIRED`; varsayım yapılmaz.
- `UNACCOUNTED = 0` sağlanmayan hiçbir batch yayınlanmaz.
- **Yeni gelen her veri önce karantinada `NEW_DATA_REVIEW` olarak bekler** (§10.1); incelenmeden staging'e/canonical'a girmez.
- **Eldeki verinin doğruluğu araçlarla sürekli doğrulanır ve raporlanır** (§8.2); "doğru görünüyor" kabul değildir.
- Hedef veritabanı: yüksek kaliteli, bağlantıları (ID/FK) doğru, kullanırken performans sorunu çıkarmayan, ilgili verilerin kolayca sentezlenip hesaplanabildiği yapı (§4.1).
- **Açıklama kuralı:** kullanıcıya sunulan her seçenekte terimin ne olduğu ve neden o seçeneğin önerildiği belirtilir.

---

## 1. Katman mimarisi ve depolama standardı

| Katman | Dizin | Format | Standart | Değişebilirlik |
|---|---|---|---|---|
| L0 RAW | kaynak kökler (dışarıda) + `raw_manifest/` | orijinal dosya | **BagIt 1.0** paketleme (`bagit.txt`, `manifest-sha256.txt`, `bag-info.txt`) — yalnız arşiv/yedek paketinde; kaynak dosyaya dokunulmaz | değişmez |
| L1 STAGING | `staging/v{pipeline}/{family}/` | **Parquet (ZSTD)**, mekânsal: **GeoParquet 1.1** | ham sütunlar `raw_*` VARCHAR + `typed_*` + provenance | append-only, sürüm dizini |
| L2 CANONICAL | `canonical/v{model}/` | Parquet/GeoParquet, **Apache Iceberg** tablo formatı (snapshot geçmişi) | entity + observation modeli (§4) | append-only; snapshot |
| L3 PRODUCT | `exports/{release}/` | Parquet + `datapackage.json` (**Frictionless**), harita: **PMTiles**, isteğe bağlı SQLite/CSV | DCAT katalog kaydı + CHANGELOG | sürümlü release |
| Q QUARANTINE | `quarantine/` | Parquet | §7 | append-only |
| M MAPPINGS | `mappings/` | Parquet | ID eşlemeleri, alias, source_registry, source_priority | sürümlü |

Depolama yeri: yerel disk = çalışma seti; S3-uyumlu nesne deposu (DO Spaces) = system of record için L2/L3 + şifreli L0 yedeği (restic). Yerelde tutulamayan eski sürümler bulutta kalır; yerelde manifest+hash kalır.

---

## 2. Kimlik (ID) standardı

| Nesne | Biçim | Kural |
|---|---|---|
| Dosya | `FILE_00000001` | manifestte sabit; yol değişse de ID değişmez |
| Kaynak tablo/katman/üye | `SOURCE_TABLE_000001` | |
| Kayıt provenance | `source_file_id + source_table_id + source_row_number (+ source_object_path)` | zorunlu |
| Coğrafi varlık | `GEO_000001` | resmî kod ayrı sütunda: `tuik_il_kodu` (2 hane, metin), `ilce_kodu`, `mahalle_kodu` (NVİ) — **hepsi metin, sıfırlar korunur** |
| İşletme / POI / ürün | `BUS_000001`, `POI_000001`, `PRODUCT_000001` | kaynak ID'leri `mappings/id_mapping` içinde |
| Gözlem | `OBS_{family}_{ulid}` | bir kaynak satırı = bir gözlem |
| Duplicate grup | `DUP_FILE_0001` (dosya), `DUPC_{family}_000001` (kayıt adayı) | |
| Karantina | `QREC_000001` | |
| Run / batch | `RUN_YYYY_MM_DD_NNN`, `BATCH_{run}_{family}_{n}` | |

Kaynakların kendi ID'leri asla canonical ID olarak kullanılmaz.

---

## 3. Kaynak sınıflandırma standardı

### 3.1 Edinim sınıfı (`acquisition_class`) — sağlayıcı adı değil, edinim biçimi
| Değer | Tanım |
|---|---|
| `official_public` | Resmî kurum / açık veri (TKGM, TÜİK, YSK, BDDK, MEB, OSM, idari sınırlar) |
| `web_research` | Web araştırmasıyla toplanmış her veri (demografi, seçim, hemşehri, sağlık, fiyat serileri, POI, menü, konaklama, ilan gözlemleri…) |
| `owner_generated` | Kullanıcının kendi modeli/endeksi/toplayıcı çıktısı |
| `derived` | Bu pipeline'ın ürettiği skor/agregat |

### 3.2 Dağıtım sınıfı (`distribution_class`)
| Değer | Tanım |
|---|---|
| `public` | Release paketlerine girebilir |
| `internal` | Canonical'a tam girer, dışarıya yalnız türev/agregat çıkar |

Varsayılan: `official_public`, `owner_generated`, `derived` → `public`; `web_research` → `public` (agregat/türev); ham satır dağıtımı ürün tanımında release bazında belirlenir. Değişiklik `source_registry`'de tek satırla yapılır, veri yeniden işlenmez.

### 3.3 Hassasiyet bayrağı (`sensitivity`) — kullanım kuralı, kaynaktan bağımsız
| Değer | Kural |
|---|---|
| `none` | — |
| `aggregate_small_cell` | Dışa çıkan agregatta hücre `n < 10` ise bastırılır/birleştirilir; canonical'da ham değer kalır |

### 3.4 Kaynak önceliği (`source_priority`) — ayrı tablo, kodda gömülü değil
Başlangıç değerleri (v1.0.0): `official_public` 100 · `owner_generated` 80 · `web_research` 60 · `derived` 30 · bilinmiyor 10. Aynı sınıf içinde güncellik (`observed_at`) ikinci ölçüt.
*Neden:* İki kaynak aynı metrik için farklı değer verdiğinde `preferred_value` seçmek için bir sıra gerekir. Resmî kaynak en yüksek çünkü tanım ve yöntemi belgelidir; kendi modelin web verisinden üstte çünkü yöntemini biliyoruz; türev en altta çünkü zaten başka gözlemlerden hesaplanmıştır. Tablo olduğu için tek satır değiştirerek sıralama değişir, kod değişmez.

---

## 4. Veri modeli standardı (entity + observation, bitemporal)

**Entity tabloları:** `geo_entity`, `business`, `poi`, `product`, `category`, `source`.  
**Observation tabloları:** `population_observation`, `price_observation`, `listing_observation`, `transaction_density_observation`, `poi_snapshot`, `review_observation`, `menu_price_observation`, `spending_observation`, `election_observation`, `origin_distribution_observation`, `health_behaviour_observation`, `mobility_observation`, …

Her observation satırında zorunlu alanlar:

```
observation_id, entity_id, metric, raw_value, parsed_value, unit,
observed_at | period (ISO 8601; belirsizse NULL + raw_date),
valid_from, valid_to (bitemporal geçerlilik), recorded_at (sisteme giriş),
source_file_id, source_table_id, source_row_number, source_row_hash,
acquisition_class, distribution_class, sensitivity, source_priority,
observation_kind ∈ {measured, projected, modeled, derived},
pipeline_version, import_batch_id
```

- Farklı dönemlerin değerleri duplicate değildir; `period` farklıysa iki gözlemdir.
- `preferred_value` hesaplanabilir ama ayrı tabloda: `preferred_value, preferred_source, selection_rule, selection_confidence`. Gözlemlerin yerine geçmez.
- Projeksiyon/model satırları (`observation_kind ≠ measured`) analitikte ayrı gösterilir.

**Geometri:** `geometry_original` (kaynak CRS, WKB, dokunulmamış) + `source_crs` + `geometry_wgs84` (türev, EPSG:4326) + `geometry_validity_status` + (varsa) `geometry_repaired`, `repair_method`. Canonical CRS: **EPSG:4326**; web harita türevi EPSG:3857 yalnız export'ta.  
**Mahalle ataması:** `source_neighbourhood` (kaynağın yazdığı) ve `spatially_assigned_neighbourhood` (ST_Contains) ayrı; çelişki → `GEO_CONFLICT`.

### 4.1 Bağlantı doğruluğu ve performans tasarımı (kullanırken sorun çıkmasın diye)

| Kural | Ne demek | Neden |
|---|---|---|
| Her observation tablosunun `entity_id`'si ilgili entity tablosunda **mutlaka** bulunur (referential integrity testi her batch'te) | "Bağlantı doğru" = kopuk referans yok | Kopuk referans, sentez sorgularında satır kaybettirir ve fark edilmez |
| Ortak omurga: `geo_entity` hiyerarşisi il → ilçe → mahalle (resmî kodlarla) | Bütün aileler aynı mahalle ID'sine bağlanır | Farklı ailelerin (fiyat, nüfus, POI) yan yana gelmesi tek join ile olur |
| Parquet dosyaları **il koduna göre bölümlenir** (`il_kodu=34/…`), büyük seriler ayrıca yıla göre | Bölümleme (partitioning): sorgu yalnız ilgili il/yıl dosyalarını okur | 8 GB RAM'de bütün Türkiye'yi taramadan tek il sorgulanabilir |
| Sık kullanılan birleşimler için **hazır özet tablolar** (`analytics/mahalle_intelligence` vb.) canonical'dan türetilir, ham gözlemin yerine geçmez | Önceden hesaplanmış görünüm | Her sorguda milyonlarca satırı yeniden toplamamak için |
| Sütun tipleri canonical'da kesin (kod=metin, tutar=DECIMAL, tarih=DATE, geometri=WKB); raw katmanda metin | Tip disiplini | Yanlış tip = yanlış join (ör. `01` ≠ `1`) ve yavaş sorgu |
| Geometriler için bbox sütunları (`xmin,ymin,xmax,ymax`) ve GeoParquet bbox metadata | Mekânsal ön filtre | Spatial join'de önce kutu karşılaştırması, sonra pahalı geometri testi |
| Tek dev tablo yok; entity + observation; sütun sayısı tabloda ≤ ~60 | Dar tablolar | Geniş tablo hem yavaş hem bakımı zor |
| Sorgu motoru: DuckDB (yerel/analitik), gerektiğinde Postgres+PostGIS (servis/API) | Aynı Parquet'i ikisi de okur | Motor değişse veri formatı değişmez |

---

## 5. Normalizasyon ve dönüşüm standardı

- `raw_value` asla üzerine yazılmaz; normalize değer ayrı sütun.
- Her dönüşüm kayıtlı: `transformation_id, name, version, description, input, output, code_hash, executed_at` (`transformations/`). Örnek: `NORMALIZE_TR_PLACE_NAME_V1` (küçük harf, Türkçe karakter eşleme, "mah./mahallesi" eki kaldırma — kurallar belgede).
- Deterministik: aynı girdi + aynı sürüm = aynı çıktı; rastgelelik varsa seed kayıtlı.
- Sayısal görünümlü metinler (`00123`, plaka `01`, kodlar) **metin** kalır; tip dönüşümü yalnız açık semantikle ve `typed_*` sütununda.
- Tarih: `raw_date` korunur; normalize ISO 8601; belirsizlik `date_precision ∈ {day, month, year, unknown}`.

---

## 6. Entity resolution standardı

- Önce `duplicate_candidate`, sonra onay; fiziksel merge yok, canonical ID bir üst referans katmanıdır.
- Sinyaller: `name_similarity, address_similarity, phone_match, website_match, lat_lon_distance, category_similarity, brand_match, external_id_match`; tek sinyalle merge yok.
- Skor 0–1; sınıflar: `≥0.995 virtually_certain · 0.980–0.995 very_high · 0.950–0.980 high · 0.850–0.950 review · <0.850 no_auto_merge`. Eşikler `mappings/thresholds.json` içinde, veri tipine göre farklılaşabilir.
- Her eşleşme `reasons[]` ile açıklanır.
- Ayrı matcher'lar: `geo_matcher, business_matcher, poi_matcher, demographic_matcher, price_matcher, product_matcher, restaurant_matcher`.
- `golden_matches` (insan doğrulamalı) → precision / recall / F1 her sürümde raporlanır.
- Polygon eşleşme: `intersection_area, union_area, IoU, coverage_A, coverage_B, centroid_distance, name_similarity`.

---


### 6.4 Kaynak-tutarlı zamansal çıkarım (2026-09-19, kullanıcı kuralı)
- Açılış/kapanış/turnover göstergeleri **yalnız aynı kaynak sisteminin kendi kesitleri** arasından türetilir (OSM 2021→2026 kesitleri; Google kesitleri ileride birikince Google içinde).
- Farklı kaynaklar arasındaki varlık farkı (ör. 2025 OSM'de yok, 2026 Google'da var) **asla** yaşam döngüsü olayı değildir; yalnız `source_coverage` (google_only / osm_only / google+osm) olarak raporlanır.
- OSM olayları `event_semantics='map_presence'` taşır: "ilk görülme" haritaya eklenme, "kaldırıldı" haritadan silinmedir; işletme olayının vekilidir. İlk kesit (2021-01-01) sol-sansürlüdür; oradaki kayıtlar "eklendi" sayılmaz.
- Tarihçe tabloları (`poi_lifecycle_osm`, `poi_presence_osm`) silinmez; yeni OSM kesitleri append edilir.
- POI ürün çıktısı nokta düzeyindedir (her kayıt lon/lat + geometry); mahalle sayıları türevdir.

## 7. Karantina ve hata standardı

- "Rejected" yok; `quarantined_record`: `source_file_id, source_location, raw_data (byte), error_type, error_message, pipeline_version, created_at`.
- Kural seti `tools/quarantine_rules.json` (sürümlü, hash'li). Her yeni dosya bu setten geçer.
- Sentetik/fallback, yüksek parse hata oranı (≥%0,5), format/uzantı çelişkisi, bbox dışı koordinat, kodlama hatası → karantina veya inceleme.
- Karantina kayıtları sayım denklemine dahildir; sistemin resmî parçasıdır.

---

## 8. Kalite standardı (0–100, hesap yöntemi açık)

| Boyut | Hesap |
|---|---|
| completeness | zorunlu alanlarda dolu hücre / toplam hücre |
| uniqueness | 1 − (kayıt-düzeyi duplicate / toplam) |
| validity | tip/aralık/regex kontrolünden geçen / toplam |
| consistency | çapraz kaynak çelişkisi olmayan metrik / toplam |
| timeliness | `observed_at` ≤ SLA (aile bazında tanımlı) olan / toplam |
| referential_integrity | çözümlenen FK / toplam FK |
| geospatial_validity | geçerli + bbox içi geometri / toplam |

Her release'te aile bazında tablo; doğrulama süiti sonucu pakete eklenir.

### 8.2 Doğruluk doğrulama programı (araçla, sürekli, raporlu)

| Yöntem | Ne yapar | Ne zaman |
|---|---|---|
| **Kural testleri** (Great Expectations tarzı, `validation/suites/*.json`) | Tip/aralık/regex/benzersizlik/FK; ör. koordinat Türkiye içinde, fiyat > 0, tarih ≤ bugün | Her batch |
| **Çapraz kaynak tutarlılığı** | Aynı metrik iki kaynakta varsa fark oranı; eşik üstü → `CONFLICT` raporu | Her release |
| **Resmî çapaya karşı test** | Nüfus/ilçe sayısı/il sayısı gibi değerler TÜİK/resmî toplamla karşılaştırılır (81 il, 973 ilçe, mahalle sayısı…) | Her release |
| **Mekânsal doğrulama** | Nokta → mahalle poligonu (ST_Contains) ile kaynağın yazdığı mahalle karşılaştırılır; uyuşmazlık oranı | Her batch (geo) |
| **Golden set** (`golden_matches`, insan doğrulamalı örneklem) | Eşleştirme precision/recall/F1; rastgele örneklem ile canlı kontrol (kullanıcı/ekip web'den bakar) | Her matcher sürümünde |
| **Sentetik/model dedektörleri** | Sabit sütun, üretilmiş ad kalıbı, gelecek tarih, formül kalıntısı (§7 kural seti) | Her batch |
| **Drift izleme** | Yeni batch'in dağılımı (ortalama/medyan/null oranı) önceki batch'ten sapıyorsa uyarı | Her batch |
| **Zaman içinde yeniden doğrulama** | Her release'te rastgele %1 kayıt için kaynaktan/canlı sayfadan tekrar kontrol; sonuç `validation/reverify_{release}.parquet` | Her release |

Çıktı: `validation/{run}_accuracy_report.md` (skorlar, eşikler, başarısız testler, örnek kayıtlar). Skoru düşen aile release'e girmez; `REVIEW_REQUIRED`.

---

## 9. Sürümleme ve yayın standardı

- Pipeline: SemVer (`1.0.0`); canonical model: `v1, v2…` ayrı dizin; dataset release: **CalVer** `YYYY.MM.N`.
- Her release: `datapackage.json` (şema, tipler, kısıtlar, lisans, kaynak sınıfları), `CHANGELOG.md`, `manifest-sha256.txt`, `quality_report.json`, `lineage.json` (W3C PROV uyumlu özet).
- Snapshot büyük dönüşümlerden önce; rollback her zaman raw'a mümkün.
- Audit log her yazma işleminde: `timestamp, operation, input, output, records_read, records_written, records_quarantined, pipeline_version, code_version`.

---

## 10. Sürekli güncelleme standardı

### 10.1 Yeni veri giriş kapısı (zorunlu)
Yeni gelen her dosya/tablo (scraper çıktısı, indirilen paket, elle eklenen dosya):
1. Artımlı Phase 1: hash + format + şema + içerik dedektörleri → manifest'e append.
2. **Karantinada `NEW_DATA_REVIEW` kaydı açılır** — dosya henüz "veri" değil, "aday"dır. Kayıtta: şema özeti, en yakın şema kümesi, bilinen ailelerle benzerlik, kural eşleşmeleri, örnek satırlar.
3. İnceleme raporu kullanıcıya sunulur: "bu dosya X ailesine benziyor (%92), 3 kural tetiklendi, 1.240 satır bbox dışı" gibi.
4. Kullanıcı kararı: `ACCEPT` (staging'e alınır) · `ACCEPT_AS_QUARANTINE` (izlenebilirlik için alınır, canonical'a girmez) · `HOLD` (bekler). Karar `quarantine/new_data_decisions.parquet`'e yazılır (kim, ne zaman, gerekçe).
5. Kabul edilen dosya ancak ondan sonra §10 döngüsüne girer.
*Neden:* Bir scraper'ın bozuk/fallback çıktısı fark edilmeden canonical'a girerse temizlemek çok pahalıdır; kapı, hatayı en ucuz noktada yakalar.

### 10.2 Döngü
1. Kabul edilen dosya → artımlı Phase 1 (yalnız yeni/değişen hash) → manifest append.
2. Karantina kuralı → staging batch (dry-run → onay → uygula).
3. Canonical'a yeni Iceberg snapshot (append; eski snapshot silinmez).
4. Kalite süiti → release adayı → CalVer yayın.
5. Yedek: L0 şifreli restic → nesne deposu; L2/L3 nesne deposu birincil.
6. Toplayıcılar pipeline ile eşzamanlı çalışmaz (8 GB RAM); ya sırayla ya snapshot kopyası ile.

---

## 11. İnsan onayı gerektiren işlemler

Dosya silme/taşıma/yeniden adlandırma, merge kurallarını değiştirme, toplu otomatik eşleştirme, CRS dönüşümüyle kaynak geometrinin yerine geçme, geri dönüşsüz tip dönüşümü, duplicate temizliği, overwrite, kalıcı export/release.

---

## 12. Kararlar (v1.0.0 — önerilen değerlerle kapatıldı; her biri tek satırla değiştirilebilir)

| # | Karar | Değer | Ne demek / neden |
|---|---|---|---|
| 1 | `source_priority` | 100/80/60/30/10 (§3.4) | Çelişen değerlerde hangi kaynağın "tercih edilen" olacağı. Resmî en güvenilir; sıralama tablo, kod değil. |
| 2 | `web_research` ham satırı release'e girer mi | **Varsayılan hayır; agregat/türev girer.** Release bazında `release_manifest.json` ile açılabilir | "Ham satır" = kaynaktan alınan tek tek kayıt; "agregat" = mahalle toplamı/skoru. Ürünün değeri zaten agregatta; ham satırı release bazında açmak esneklik verir. |
| 3 | Iceberg | **Phase 4'te, önce tek tabloyla pilot** | Iceberg = Parquet dosyalarının üstünde "hangi dosyalar hangi sürümde" defteri tutan tablo formatı (zaman yolculuğu, güvenli ekleme). Phase 2–3'te veri sık değişecek; Phase 4'te canonical sabitleşince eklemek daha az iş. O zamana kadar sürüm dizinleri (`v1/`, `v2/`) + manifest aynı işi görür. |
| 4 | Küçük-hücre eşiği | `n < 10` bastırılır | Bir mahallede 3 kişilik bir grup sayısı yayınlamak, o kişileri dolaylı tanımlanabilir kılabilir; 10 istatistik kurumlarının yaygın eşiğidir. Canonical'da ham değer kalır. |
| 5 | Nesne deposu | DO Spaces, bölge `fra1` (Frankfurt), bucket `geoprop-lake` (+ `geoprop-raw-backup` şifreli) | Frankfurt Türkiye'ye en yakın DO bölgesi (gecikme düşük). İki bucket: biri çalışma (L2/L3), biri şifreli ham yedek — ayrı erişim anahtarı, yanlışlıkla silme riski ayrışır. Kurulum kullanıcı onayıyla. |
