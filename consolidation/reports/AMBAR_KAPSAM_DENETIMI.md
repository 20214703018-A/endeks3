# AMBAR / VERİTABANI KAPSAM DENETİMİ

Üretim: 2026-09-30T17:28:53+00:00 · araç: `tools/audit_coverage_v1.py`

## Kısa cevap

Veritabanına **giren her şey doğru girdi** (satır muhasebesi tam, bütünlük kontrolleri temiz), 
ama **çektiğimiz her şey henüz girmedi**. Üç halkada durum:

| Halka | Giren | Girmeyen |
|---|---|---|
| 1. Ham dosya → ambar | 3,630 dosya / 11.55 GB | 27,923 dosya / 22.2 GB |
| 2. Ambar → birleşik DB | 32 tablo / 76,149,509 satır | 1,118 tablo / 43,779,009 satır |
| 3. Karantina (doğrulama bekleyen) | — | 460,333 satır / 107 dosya |

## 2. Ambarda olup DB'ye girmemiş en büyük kaynaklar

| Satır | Kaynak |
|---:|---|
| 13,882,258 | `cadastre_zoning__stg_tkgm_alim_satim__tkgm_alim_satim_yogunlugu` |
| 6,941,245 | `cadastre_zoning__stg_tkgm_alim_satim_yogunlugu` |
| 3,124,581 | `price_series__stg_cimri_katalog_url` |
| 2,844,718 | `poi_business__stg_osm_degisim__poi_yillik` |
| 1,631,256 | `demographics_context__stg_piyasa_verileri__fiyat_trend` |
| 680,481 | `poi_business__stg_osm_degisim__poi_yasam` |
| 665,862 | `poi_business__stg_google_places_ve_yogunluk__google_places_ticari_yogunluk` |
| 623,793 | `poi_business__stg_osm_degisim__poi_kimlik` |
| 623,793 | `poi_business__stg_osm_poi__poi` |
| 615,318 | `listings__stg_02_yillik_satislar_2010_2024` |
| 595,190 | `demographics_context__stg_07_secim_sonuclari_ve_oylar` |
| 580,432 | `price_series__stg_cografi_katmanlar__cografi_ozellikler` |
| 561,698 | `poi_business__stg_08_ilce_onemli_noktalar_poi` |
| 509,403 | `poi_business__stg_piyasa_verileri__poi_noktalari` |
| 501,783 | `listings__stg_bolge_istatistik__poi_ilce` |
| 422,858 | `poi_business__stg_google_places_ve_yogunluk__google_places_yorumlar_ve_niyet` |
| 369,286 | `price_series__stg_price_breakdown` |
| 367,079 | `demographics_context__stg_06_hemsehri_kutuk_dagilimi` |
| 331,425 | `demographics_context__stg_piyasa_verileri__yillik_satislar` |
| 318,653 | `demographics_context__stg_piyasa_verileri__secim_sonuclari` |

## 1. Hiç ambara girmemiş en büyük klasörler (indirildi, ayrıştırılmadı)

| MB | Dosya | Biçim | Klasör |
|---:|---:|---|---|
| 2,932 | 6 | binary_unknown | `GEOPROP/warehouse/raw/osm` |
| 2,348 | 861 | gzip | `GEOPROP_RAW_INTAKE/acikveri_ckan_ibb/2026-09-24/files` |
| 1,913 | 352 | gzip | `GEOPROP_RAW_INTAKE/kgm_karayollari/2026-09-24/ekler` |
| 1,764 | 53 | parquet | `GEOPROP/warehouse/silver/property_market/price_trend` |
| 1,361 | 1 | binary_unknown | `Downloads/turkey-internal.osh.pbf` |
| 802 | 51 | parquet | `tkgm/extension/warehouse/silver/property_market/price_trend` |
| 784 | 310 | xlsx | `GEOPROP_RAW_INTAKE/btk_iletisim_istatistik/2026-09-24/ekler` |
| 620 | 1 | binary_unknown | `Downloads/turkey-250101-internal.osm.pbf` |
| 588 | 1 | binary_unknown | `Downloads/turkey-240101-internal.osm.pbf` |
| 480 | 1 | binary_unknown | `Downloads/turkey-230101-internal.osm.pbf` |
| 434 | 629 | gzip | `GEOPROP_RAW_INTAKE/turizm_ktb/2026-09-24/ekler` |
| 422 | 1 | binary_unknown | `Downloads/turkey-220101-internal.osm.pbf` |
| 391 | 1,054 | json | `tkgm/extension/collector/data/poligonlar` |
| 313 | 2,635 | gzip | `GEOPROP_RAW_INTAKE/tuik_portal_tablolar/2026-09-24/files` |
| 276 | 5 | zip | `GEOPROP/VERİLER` |
| 266 | 14 | csv_semicolon | `GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/ULUSAL_CSV` |
| 259 | 445 | xlsx | `GEOPROP_RAW_INTAKE/acikveri_ckan_izmir/2026-09-24/files` |
| 247 | 642 | gzip | `GEOPROP_RAW_INTAKE/turizm_ktb_il_mudurlukleri/2026-09-24/ekler` |
| 192 | 3 | parquet | `GEOPROP/warehouse/bronze/csv/9e` |
| 192 | 4 | parquet | `GEOPROP/warehouse/bronze/sqlite/db` |

## Not

- `_rtree`, `_rowid` gibi 12 tablo (2,5 M satır) SQLite'ın iç indeksleridir, veri değildir; sayıma katılmadı.
- Aynı verinin iki kopyası olan klasörler var (`GEOPROP/warehouse` ve `tkgm/extension/warehouse`); biri ambarda ise diğeri 'girmedi' görünür.
- OSM `.pbf` dosyaları (3,5 GB) ham harita arşividir; bunlardan türetilen POI/yaşam döngüsü tabloları ambarda vardır.