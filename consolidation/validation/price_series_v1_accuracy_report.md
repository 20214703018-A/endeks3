# DOĞRULUK RAPORU — Aile 2 `price_series` (staging v1.0.0)

Tarih: 2026-09-18 · Standart §8.2 · Motor: DuckDB akış (satırlar belleğe alınmadan), `arsa_emsalleri` için parça-parça sqlite

## 1. Sayım denklemi
| Hedef tablo | Kaynak dosya | Satır | Projeksiyon/kesim-sonrası |
|---|---|---|---|
| stg_price_trend_monthly (konut+arsa aylık m², ilçe/mahalle) | 28 (ulusal CSV, 21 bölge CSV, bolge_istatistik DB, 4 piyasa DB) | 4.729.546 | 709.440 |
| stg_land_trend_monthly (arsa/tarla mahalle 80 ay) | 4 (nihai DB, ürün DB, CSV, eski ext DB) | 8.149.840 | 1.222.476 |
| stg_price_breakdown (oda/yaş/kat/ısıtma kırılımı) | 28 | 369.286 | 0 |
| stg_land_summary | 5 | 248.880 | 0 |
| stg_ej_trend / stg_ej_region_index / stg_ej_breakdown (tarayıcı dışa aktarımları) | 6 / 8 / 6 | 277.348 / 116.918 / 30.050 | 41.604 / 0 / 0 |
| stg_price_summary | 28 | 72.542 | 5.319 |
| stg_land_area_segments | 5 | 14.255 | 0 |
| stg_land_price_heatmap | 2 (boş tablolar) | 0 | 0 |
| **Toplam** | 120 kaynak | **14.008.665 = 14.008.665 yüklenen, UNACCOUNTED 0**, CSV reject 0 | |

Çıktı 797 MB Parquet (ZSTD). Kaynak dosya hash'i staging öncesi = sonrası: değişen 0.

**Olay kaydı:** İlk çalışmada 19 emlakjet CSV'si yanlış ayırıcıyla (`,` yerine `;`) tek sütun olarak okundu; sayım denklemi tuttuğu için fark edilmedi, sütun sayısı kontrolünde yakalandı. 20 batch `staging/_invalidated/` altına alındı (audit kaydı), motora **DELIMITER_SUSPECT** koruması eklendi (tek sütun + başlıkta ayırıcı → yazmaz), doğru ayırıcıyla yeniden yazıldı. Ders: sayım denklemi gerekli ama yeterli değil; şema-şekil testi de zorunlu (§8.2'ye eklenecek).

## 2. Kaynaklar arası tutarlılık
| Test | Sonuç |
|---|---|
| T1 Ulusal paket (1.499.422) ⊆ 21 bölge CSV birleşimi (1.529.982 distinct)? | **Evet, %100**: bütün anahtarlar (kategori, seviye, il, ilçe, mahalle, ay) bölge birleşiminde var ve `satilik_m2_fiyat` **birebir eşit**. Ulusalda tekrar eden anahtar 0. |
| T2 Ulusal ↔ `bolge_istatistik.sqlite` (önceki ekibin birleştirme ürünü) | 1.499.422 anahtar eşleşti, değer eşitliği %100; DB'de **30.560 fazla satır** = ulusal pakette olmayan 5 il. |
| İl kapsamı | Ulusal CSV **76 il** (INCELEME_RAPORU P0 doğrulandı); bölge CSV birleşimi ve bolge_istatistik **81 il**. → Canonical için kaynak: bölge birleşimi (bolge_istatistik ile aynı). |
| T5 Arsa/tarla trendi 3 kopya (nihai DB, ürün DB, CSV) | 2.715.520 satır; (mahalle, kategori, ay) bazında değerler **birebir eşit**. Eski `extension` kopyası 3.280 satır (kısmi) — alias. |
| T6 Projeksiyon etiketi | Kaynağın `projeksiyon=1` etiketi ile "ay > 2026-08" kuralı **%100 örtüşüyor** (arsa 407.328 = 407.328; emlakjet 41.604 = 41.604; tutarsız 0). Kesim tarihi: 2026-08 (gözlem), 2026-09→2027-08 projeksiyon (12 ay). |

## 3. Makullük (ulusal, konut/mahalle, 1.343.982 satır)
- `satilik_m2_fiyat` NULL: 51.840 (%3,9) — boş ay/mahalle; doldurulmadı. `≤0`: **0**. Kira < 0: 0. İlan sayısı < 0: 0.
- m² fiyatı q01 / medyan / q99 = 1.263 / 15.386 / 116.901 TL — Türkiye 2021–2026 aralığı için makul.

## 4. Omurga bağlantısı (referential integrity, §4.1)
- Ulusal mahalle `district_id` (16.800 benzersiz) → **%100** `stg_admin_polygon` web DistrictId'de var.
- `county_id` 923 → 923 omurgada (973'ün 923'ü; eksik 50 ilçe = 5 eksik il).
- Bu aile web ID sistemine bağlı; TÜİK/TKGM resmî kodlara geçiş Phase 3 eşleme tablosuyla.

## 5. Kalite skorları (aile, staging düzeyi)
| Boyut | Skor | Hesap |
|---|---|---|
| completeness | 96,1 | m² fiyatı dolu 1.292.142 / 1.343.982 |
| uniqueness | 100 | anahtar tekrar 0 (kopya dosyalar alias; canonical'da tek gözlem) |
| validity | 100 | ≤0 / negatif değer 0 |
| consistency | 100 | 3 kaynak grubunda değer eşitliği %100 |
| timeliness | 100 | son gözlem 2026-08; projeksiyonlar ayrı etiketli |
| referential_integrity | 100 | district/county → omurga %100 |
| geospatial_validity | n/a | geometri yok (heatmap tabloları boş) |

## 6. Açık kalanlar / Phase 3-4 girdileri
- Canonical `price_observation`: kaynak = bölge birleşimi (81 il); ulusal CSV ve bolge_istatistik alias; `observation_kind = projected` for `projeksiyon=1`.
- Aynı (mahalle, ay, kategori) için ulusal/bölge/DB üçlüsü tek gözlemdir (DUPC adayı, fiziksel merge yok).
- `stg_ej_*` tarayıcı dışa aktarımları: il/ilçe/mahalle **adıyla** (ID yok) → Phase 3 ad eşleme.
- 51.840 NULL m² hücre: gözlem yok; canonical'da satır açılmaz (NULL gözlem üretilmez), kaynak satır staging'de kalır.
