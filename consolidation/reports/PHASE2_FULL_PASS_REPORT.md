# PHASE 2 — TAM GEÇİŞ RAPORU (staging v1.0.0)

Tarih: 2026-09-18 · Pipeline 1.0.0 · Standart v1.0.0  
Sorgulanabilir DB: `staging/geoprop_staging.duckdb` (1.099 view, 2 MB; veri Parquet'te — `stg.<aile>__<tablo>`, `qstg.*` karantina, `main.catalog`, `main.inventory`, `main.file_alias`, `main.zip_member_alias`, `main.quarantine_registry`)

## 1. Sonuç
| | |
|---|---|
| Staging + karantina satırı | **52.417.122** (4,16 GB Parquet/GeoParquet, ZSTD) |
| Kaynak nesne (dosya/tablo/ZIP üyesi) | 2.743 + 1.048 poligon dosyası |
| Aile | 11 (+ karantina) |
| Sayım denklemi | Her batch: input = staged + quarantined; **UNACCOUNTED 0**; CSV reject 0 (cp1254/cp1257 İBB dosyaları doğru kodlamayla okundu) |
| Kaynak dosya değişimi | 0 (her batch'te sha256 yeniden doğrulandı) |
| Hash-doğrulanmış alias | 7.417 kopya dosya + 634 ZIP üyesi (aynı içerik ikinci kez yazılmadı; provenance `mappings/`) |

## 2. Aile bazında
| Aile | View | Satır | Boyut | Not |
|---|---|---|---|---|
| cadastre_zoning | 103 | 21.385.046 | 1,37 GB | TKGM alım-satım yoğunluğu 6,9 M + tkgm-alim-satim-ambar ZIP (farklı içerik) + parsel/imar (karantina 47 satır) |
| price_series | 93 | 15.926.007 | 1,07 GB | bkz. price_series_v1_accuracy_report.md |
| poi_business | 126 | 8.948.986 | 577 MB | OSM POI/değişim, Google Places, Yemeksepeti, menüler, işletme dizinleri |
| demographics_context | 335 | 2.428.924 | 145 MB | demografi, seçim, hemşehri, sağlık (Endeksa/Emlakjet türevi → web_research); 357.368 satır karantinada (T02 shard tabloları) |
| listings | 41 | 1.798.290 | 87 MB | ilan gözlemleri (turkiye_tum_ilanlar, ilanlar.db+WAL, Airbnb, ilçe atlası) |
| reference_geography | 170 | 1.194.069 | 827 MB | web poligon + TKGM resmî + TÜİK nüfus + sektör GeoJSON'ları (ZIP'ten) |
| mobility_logistics | 26 | 513.086 | 29 MB | ulaşım/GPS, kargo, darkstore |
| education | 50 | 122.089 | 9 MB | MEB okul, LGS, üniversite |
| unclassified | 57 | 68.659 | 8 MB | Downloads'daki ilgisiz/örnek veriler (Chinook, FED serileri…) — REVIEW |
| vehicles | 18 | 24.740 | 8 MB | aracrisk emsaller |
| economy | 80 | 7.226 | 1 MB | BDDK/KAP/BKM (6.780 satır karantinada: bkm) |

## 3. Sıfır-kayıp muhasebesi (Phase 1 keşfi → Phase 2)
```
Phase 1 keşfedilen kayıt (tables.row_count toplamı):   89.568.651
  staging'e alınan (envanter sayımıyla)                  52.226.814
  + poligon dosyaları (özel betik)                            52.057
  reference_only: warehouse/bronze|silver Parquet          30.239.789   ← önceki pipeline'ın türevi; kaynağı zaten staging'de (D5)
  alias (ZIP üyesi, sha256 = açık kopya)                    6.854.695
  atlanan: metin/SQL/markdown/HTML (tablo değil)              188.926
  atlanan: config JSON / veri-dışı                              5.304
  atlanan: intake meta (.meta.json)                             1.065
  view / boş tablo                                                  0
  UNACCOUNTED                                                       0
Staging gerçek satır: 52.417.122 (+190.308: Phase 1'in tek kayıt saydığı ZIP içi bölge poligon JSON'ları
  274 dosya → satır bazında açıldı; İBB CSV'lerinin 2.828 reddedilmiş satırı doğru kodlamayla kurtarıldı)
Ertelenen: 11 OSM PBF (6,4 GB) — osmium gerekli; ham dosya duruyor.
```

## 4. Olaylar (şeffaflık)
| # | Olay | Etki | Düzeltme |
|---|---|---|---|
| 1 | emlakjet CSV yanlış ayırıcı (`,`/`;`) | 20 batch geçersiz; sayım denklemi tuttu (yanıltıcı) | `staging/_invalidated/`'a alındı (audit), `DELIMITER_SUSPECT` koruması, yeniden yazıldı |
| 2 | Python yolu ilk hatada aileyi durduruyordu | poi_business ve unclassified 1. turda yarım | try/except; 2. turda tamamlandı |
| 3 | `.json` adlı gzip dosya | 1 kaynak | gzip algılama |
| 4 | SQLite INTEGER sütunda metin (`ilan_id='ej_…'`) | 1 tablo | `sqlite_all_varchar=true` (kuralımız zaten "her şey metin") |
| 5 | 0 satırlık tablo (`osm_degisim.poi_olay`) | dosya yazılmadı | EMPTY_SOURCE_TABLE audit kaydı |
| 6 | e-kitap ZIP'lerindeki `calibre_bookmarks.txt` | koruma reddetti | veri-dışı olarak kalıcı atlama |
| 7 | 2. turda eski spec'ler yeniden koştu | 843 "zaten var" reddi (yazmadı) | planlayıcı artık eski spec'leri siliyor |

## 5. Karantina (staged, canonical'a girmez)
| Aile | Satır | Gerekçe |
|---|---|---|
| demographics_context | 357.368 | T02: shard dosyalarındaki `bati_buyuksehirler` sentetik/fallback tabloları |
| reference_geography | 68.862 | tuik_bolge kopyaları (kabul edilen sürüm staging'de; kayıt append-only) |
| mobility_logistics | 23.865 | T02/T05 (kargo/istasyon sentetik) |
| economy | 6.780 | bkm_sektorel_kart_harcama (ulusal veri il verisi gibi) |
| cadastre_zoning | 47 | parsel_imar_kayitlari / bagimsiz_bolumler (KAKS 1,5 sabit) |
| price_series | 9 | Q rule |

## 6. Bilinen sınırlar
- `unclassified` ailesi kullanıcı Downloads'ındaki ilgisiz dosyaları içerir (Chinook örnek DB, FED serileri, FastSAM dosyaları) — Phase 3 öncesi `REVIEW`; ürün kapsamı dışında tutulacak.
- `stg_ej_*` (tarayıcı dışa aktarımları) ve `listings` içindeki ilçe-atlası CSV'leri ID değil ad taşıyor → Phase 3 ad eşleme.
- Sektör GeoJSON'larının 38'i yalnız ZIP'te vardı; 2'si açık kopya. Hepsi `stg_sektor_*` view'larında (EPSG:4326/CRS84).
- Bronze/silver referans satırlarının %30'u ZIP içi DB tablolarına işaret ediyor (D5 REVIEW); ZIP üyeleri artık staging'de olduğundan Phase 3'te hash ile bağlanabilir.

## 7. Sonraki adım — Phase 3 (entity resolution)
1. `geo_matcher`: web `DistrictId` ↔ TÜİK `tuik_kodu` ↔ TKGM `id` → `GEO_` canonical + `mappings/id_mapping`. Sinyaller: ad (normalize), IoU, centroid-içinde, nüfus tutarlılığı (2024 web ↔ 2025 TÜİK).
2. `listing_matcher`: aynı ilanın farklı çekimleri → gözlem serisi (duplicate değil); aynı çekimde tekrar → kopya.
3. `poi_matcher`: OSM ↔ Google Places ↔ Yemeksepeti (ad + mesafe + telefon + kategori).
4. Golden set: her matcher için 200 rastgele çift insan doğrulamasına.
