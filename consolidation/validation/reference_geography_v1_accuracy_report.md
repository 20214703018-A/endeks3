# DOĞRULUK RAPORU — Aile 1 `reference_geography` (staging v1.0.0)

Tarih: 2026-09-18 · Standart §8.2 · Yöntem: kural testleri + resmî çapa testi + çapraz kaynak tutarlılığı

## 1. Sayım denklemi (her batch)
| Batch | input | staged | quarantined | UNACCOUNTED |
|---|---|---|---|---|
| stg_admin_polygon (81 il, 2 batch) | 52.151 | 52.151 | 0 | **0** |
| 34 yardımcı kaynak (S3–S8, S10) | 250.118 | 250.118 | 0 | **0** |
| Kaynak dosya hash'i tarama-öncesi = sonrası | 1.216 dosya | — | — | değişen **0** |

## 2. Resmî çapa testi — poligon nüfusunun yılı (F7 çözüldü)
S2 (web araştırması, 50.638 mahalle poligonu, `Population`) ilçe bazında toplandı; TÜİK ADNKS ilçe serisi (2007–2025, `stg_tuik_nufus_ilce`) ile il+ilçe adı üzerinden eşlendi (973/973 eşleşti).

| Yıl | Birebir eşit ilçe | Toplam mutlak fark | Fark % |
|---|---|---|---|
| **2024** | **752 / 973** | 672.066 | **0,785** |
| 2023 | 1 | 1.710.929 | 2,004 |
| 2025 | 0 | 1.758.670 | 2,043 |

**Sonuç:** S2 nüfusu = TÜİK ADNKS **2024** (kanıt: 973 ilçenin %77'si birebir). Kalan 221 ilçede S2 her zaman daha düşük; sebep: 493 mahallede `Population` boş (F3). En büyük eksikler: Kapaklı −65.271, Karatay −62.443, Cizre −56.923, Yenimahalle −49.973.
**Uygulama:** `raw_population` değişmez; canonical'da `period=2024`, `period_assignment_method='official_anchor_test'`, `period_confidence=0.99` olarak **ayrı sütunda** yazılacak (Phase 4). `review_flags` F7 → çözüldü olarak işaretlenir (kayıt silinmez).

## 3. Çapraz kaynak tutarlılığı — mahalle düzeyi (Phase 3 girdisi)
- S2 mahalle (50.638) ↔ TÜİK 2025 mahalle+köy (50.437): il+ilçe+ad normalize eşleşmesi **32.001 / 50.638 = %63,2**. Eşleşenlerde nüfus birebir eşit yalnız 759 (%2,4) — beklenen, çünkü S2=2024, TÜİK=2025.
- Eşleşmeme sebepleri (örneklemden): "Köyü"/"Mah." ekleri, TÜİK'te belediye/köy ayrımı, ad farklılıkları → `T03_NORMALIZE_TR_PLACE_NAME` kuralları ve `geo_matcher` (ad + poligon içinde TÜİK merkez noktası) Phase 3'te.
- S2 DistrictId benzersizliği: 50.638 / 50.638 ✓ (kopya ID yok).
- S2 ∩ S4 (poligon ↔ merkez noktası): 50.632 ortak; 7 mahalle poligonunun noktası yok (Phase 3'te centroid'den türetilir, `derived`).
- S5 il-ilçe rehberi 968 ilçe (TÜİK/S1: 973) → 5 eksik ilçe `REVIEW`.
- S3 alternatif sınır 1.005 "ilçe" (32 ada/adacık) — ID'siz; Phase 3 polygon-matching.

## 4. Geometri
| Metrik | Değer |
|---|---|
| Poligon (S1+S2) | 52.151; geçersiz **7** (ring self-intersection, hepsi köy) — işaretli, ONARILMADI |
| Su/ada (DistrictId=0) | 540 → `polygon_kind='water_or_island'` |
| Türkiye bbox dışı | 0 |
| S8 nuts3/nuts4 | 1.054; geçersiz 5 — işaretli |
| S3 sinir | 1.086 (Polygon+MultiPolygon), CRS EPSG:4326 |
| CRS | tüm kaynaklar EPSG:4326 / CRS84; dönüşüm yapılmadı |

## 5. Kalite skorları (aile, staging düzeyi; 0–100; §8 formülleri)
| Boyut | Skor | Hesap |
|---|---|---|
| completeness | 99,0 | S2 admin mahallede `Population` dolu 50.145/50.638 |
| uniqueness | 100 | DistrictId tekil |
| validity | 99,99 | geçersiz geometri 7/52.151 |
| consistency | 77,3 | ilçe nüfusu TÜİK 2024 ile birebir 752/973 (fark %0,79) |
| timeliness | 90 | nüfus 2024 (resmî çapa ile belirlendi); TÜİK 2025 mevcut |
| referential_integrity | 99,99 | poligon↔nokta 50.632/50.638 |
| geospatial_validity | 99,99 | bbox içi 100 %, geçerli 99,99 % |

## 6. Resmî TKGM katmanı ile çapraz doğrulama (yeni veri NDR_000001, 2026-09-18)
Kaynak: TKGM MEGSİS idari yapı (81 il / 973 ilçe / 49.795 kadastro mahallesi; 2.776 birimde poligon yok; 288 geçersiz geometri işaretli).

| Test | Sonuç |
|---|---|
| İl adı eşleşmesi | 81 / 81 |
| İlçe adı eşleşmesi (il+ad) | 972 / 973 ("Kemalpaşa (Artvin)" ad biçimi) |
| İlçe poligon IoU (970 çift) | medyan 0,873; q05 0,723; ≥0,9: 301; <0,5: 1 → web ilçe sınırları basitleştirilmiş, TKGM ayrıntılı |
| Mahalle adı eşleşmesi (il+ilçe+ad, "köyü/mah." ekleri atılarak) | web 41.506 / 50.638 (%82,0) · TKGM 41.493 / 49.795 (%83,3) |
| Mahalle poligon IoU (39.710 çift) | medyan **0,890**; q25 0,661; q95 0,992 · ≥0,8: 24.869 (%63) · 0,5–0,8: 8.715 · 0,3–0,5: 2.985 · <0,3: **3.140 (%8)** |
| IoU<0,3 örüntüsü | aynı ad, farklı yer: "Varlık" ↔ "Varlik Köyü" (kadastro köy birimi ≠ idari mahalle). Ad tek başına eşleştirme için YETERSİZ → Phase 3'te ad + IoU + centroid birlikte |
| Ad-eşleşmeyen web mahalleleri (örneklem 381) | %92,7'sinin merkez noktası bir TKGM poligonunun içinde → mekânsal eşleme çözer |

Çıktı: `validation/reference_geography_v1_tkgm_vs_web_iou.parquet` (çift bazında IoU; golden set adayı olarak IoU≥0,95 çiftler otomatik "virtually certain" değil, insan örneklemiyle doğrulanacak).

## 7. Açık kalanlar
- F10 resmî kod: TÜİK `tuik_kodu` (S10) ve TKGM `id` (NDR_000001) staging'de; web DistrictId ↔ tuik_kodu ↔ tkgm_id üçlü eşlemesi Phase 3 `geo_matcher` (ad + IoU + centroid + nüfus tutarlılığı).
- Mahalle nüfus yılı 2026: TÜİK henüz yayımlamadı (2025 en güncel; 2026 → Şubat 2027).
- S9 silver kopyaları (D5): 66.734 satırın 46.656'sı (%69,9) envanter dosyasına hash ile bağlandı; 20.078 satır ZIP içi `piyasa_verileri.db` tablolarından (Phase 1 ZIP üyelerini hash'lemedi) → `REVIEW_REQUIRED_ZIP_MEMBER_OR_UNKNOWN`, `mappings/derived_reference_silver_reference_geography.parquet`.
