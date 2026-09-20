# PHASE 1 — DISCOVERY RAPORU

Run ID: `RUN_2026_09_18_003`  
Başlangıç: 2026-09-18T05:24:59+00:00  
Bitiş: 2026-09-18T05:36:18+00:00  
Pipeline: 1.0.0  code_hash: `9c798dd1438eb033`  rules_hash: `3861ae82de481859`

Kaynak kökler (READ-ONLY): `/Users/acar/Desktop/GEOPROP`, `/Users/acar/Desktop/tkgm`, `/Users/acar/Desktop/harita`, `/Users/acar/Downloads`

## A. Genel durum

| Metrik | Değer |
|---|---|
| Taranan dosya | 17,581 |
| Toplam boyut | 24.51 GB |
| Okunabilir (hash alındı) | 17,581 |
| Okunamayan | 0 |
| Veri adayı dosya | 15,888 |
| İçeriği envanterlenen | 15,242 |
| Farklı tespit edilen format | 25 |
| Tablo/katman/sayfa nesnesi | 9,273 |
| Sütun | 133,934 |
| Geo katman | 89 |
| Keşfedilen kayıt (satır+özellik, tekrarlar dahil) | 89,327,811 |
| Kaydedilen parse hatası | 2,832 |
| Hata/uyarı kaydı | 17 |

### Kök bazında

| Kök | Dosya | Boyut | Veri adayı |
|---|---|---|---|
| /Users/acar/Desktop/GEOPROP | 8328 | 15.93 GB | 7963 |
| /Users/acar/Downloads | 1350 | 5.28 GB | 749 |
| /Users/acar/Desktop/tkgm | 7278 | 3.18 GB | 7128 |
| /Users/acar/Desktop/harita | 625 | 0.12 GB | 48 |

### Tespit edilen format (magic bytes; uzantı değil)

| Format | Dosya | Boyut |
|---|---|---|
| parquet | 11585 | 6.22 GB |
| json | 2320 | 0.96 GB |
| text | 868 | 0.09 GB |
| jpeg | 727 | 0.09 GB |
| csv_semicolon | 595 | 1.56 GB |
| pdf | 352 | 0.47 GB |
| png | 307 | 0.60 GB |
| zip | 233 | 1.83 GB |
| binary_unknown | 169 | 6.79 GB |
| sqlite | 86 | 5.02 GB |
| markdown | 81 | 0.00 GB |
| geojson | 52 | 0.83 GB |
| html | 43 | 0.00 GB |
| sql_text | 41 | 0.00 GB |
| csv | 39 | 0.03 GB |
| empty | 29 | 0.00 GB |
| psv | 16 | 0.01 GB |
| xlsx | 15 | 0.00 GB |
| ole_xls_doc | 8 | 0.00 GB |
| docx | 7 | 0.00 GB |
| svg | 3 | 0.01 GB |
| xml | 2 | 0.00 GB |
| pptx | 1 | 0.00 GB |
| jsonl | 1 | 0.00 GB |
| gzip | 1 | 0.00 GB |

Uzantı ↔ içerik uyuşmazlığı: **37** dosya (ayrıntı: quarantine_registry X01).

| file_id | dosya | uzantı | içerik |
|---|---|---|---|
| FILE_00016844 | unblurimageai_resized (7).jpg | jpg | png |
| FILE_00016973 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase/SKILL.md | md | sql_text |
| FILE_00000071 | .claude/skills/geoprop-veri-mimarisi/SKILL.md | md | sql_text |
| FILE_00016977 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/SKILL.md | md | sql_text |
| FILE_00017011 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/security-rls-performance.md | md | sql_text |
| FILE_00017003 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/schema-constraints.md | md | sql_text |
| FILE_00016992 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/lock-deadlock-prevention.md | md | sql_text |
| FILE_00017008 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/schema-primary-keys.md | md | sql_text |
| FILE_00017006 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/schema-lowercase-identifiers.md | md | sql_text |
| FILE_00017005 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/schema-foreign-key-indexes.md | md | sql_text |
| FILE_00017009 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/security-privileges.md | md | sql_text |
| FILE_00017007 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/schema-partitioning.md | md | sql_text |
| FILE_00016991 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/lock-advisory.md | md | sql_text |
| FILE_00016986 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/conn-prepared-statements.md | md | sql_text |
| FILE_00017000 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/query-index-types.md | md | sql_text |
| FILE_00016997 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/monitor-vacuum-analyze.md | md | sql_text |
| FILE_00016981 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/advanced-full-text-search.md | md | sql_text |
| FILE_00016998 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/query-composite-indexes.md | md | sql_text |
| FILE_00017004 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/schema-data-types.md | md | sql_text |
| FILE_00016982 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/advanced-jsonb-indexing.md | md | sql_text |
| FILE_00016995 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/monitor-explain-analyze.md | md | sql_text |
| FILE_00016996 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/monitor-pg-stat-statements.md | md | sql_text |
| FILE_00016990 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/data-upsert.md | md | sql_text |
| FILE_00016985 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/conn-pooling.md | md | sql_text |
| FILE_00017010 | workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/.agents/skills/supabase-postgres-best-practices/references/security-rls-basics.md | md | sql_text |

## B. İçerik kümeleri (tahmini kategori — kesin sınıf DEĞİL)

| Tahmini kategori | Dosya | Ort. güven | Boyut |
|---|---|---|---|
| unknown | 7257 | 0.0 | 8.42 GB |
| administrative_boundaries | 6385 | 0.63 | 1.91 GB |
| price | 428 | 0.62 | 4.29 GB |
| business | 419 | 0.62 | 0.17 GB |
| poi | 270 | 0.6 | 2.00 GB |
| demographics | 197 | 0.57 | 0.57 GB |
| real_estate_listings | 160 | 0.6 | 1.80 GB |
| vehicles | 139 | 0.75 | 0.06 GB |
| elections | 134 | 0.74 | 0.60 GB |
| cadastre_zoning | 112 | 0.64 | 2.51 GB |
| migration | 86 | 0.72 | 0.12 GB |
| housing | 70 | 0.57 | 0.18 GB |
| education | 49 | 0.58 | 0.03 GB |
| retail_spending | 41 | 0.58 | 0.00 GB |
| restaurants | 31 | 0.6 | 0.09 GB |
| health | 22 | 0.63 | 0.01 GB |
| logistics | 21 | 0.69 | 0.01 GB |
| restaurant_menu | 16 | 0.57 | 0.09 GB |
| socioeconomic | 16 | 0.56 | 0.02 GB |
| mobility | 11 | 0.67 | 0.05 GB |
| ecommerce_spending | 6 | 0.65 | 0.00 GB |
| tenders | 5 | 0.61 | 0.00 GB |
| reviews | 5 | 0.55 | 0.01 GB |
| jobs | 3 | 0.6 | 0.00 GB |
| ownership | 3 | 0.57 | 0.00 GB |
| finance | 2 | 0.79 | 0.00 GB |

En büyük tablolar (satır sayısına göre):

| table_id | dosya | tablo | tür | satır | sütun |
|---|---|---|---|---|---|
| SOURCE_TABLE_005698 | warehouse/product/tkgm_alim_satim.sqlite | tkgm_alim_satim_yogunlugu | sqlite_table | 6941129 | 7 |
| SOURCE_TABLE_009095 | tkgm-alim-satim-ambar.zip | tkgm-alim-satim-ambar.zip::tkgm_alim_satim.sqlite::tkgm_alim_satim_yogunlugu | sqlite_table | 6941129 | 7 |
| SOURCE_TABLE_005672 | warehouse/product/osm_degisim.sqlite | poi_yillik | sqlite_table | 2844718 | 9 |
| SOURCE_TABLE_001476 | VERİLER/Öğelerle Yeni Klasör 2/tum_turkiye_arsa_nihai_paket/arsa_piyasa_istihbarati.db | arsa_mahalle_trend | sqlite_table | 2715520 | 22 |
| SOURCE_TABLE_001478 | VERİLER/Öğelerle Yeni Klasör 2/tum_turkiye_arsa_nihai_paket/csv_ciktilari/04_arsa_tarla_mahalle_80_aylik_trend.csv | 04_arsa_tarla_mahalle_80_aylik_trend.csv | csv | 2715520 | 22 |
| SOURCE_TABLE_002998 | warehouse/bronze/csv/9e/9eda049596414c577984d6df0e582d75bb26636e99d6460541f492157216f8af.parquet | 9eda049596414c577984d6df0e582d75bb26636e99d6460541f492157216f8af | parquet | 2715520 | 9 |
| SOURCE_TABLE_005576 | warehouse/bronze/sqlite/db/db6ab7eefe5e9e3e12123dd590f2c25f20bce7c15bc405a5b3355449994ba999-1265e66cc548d3f9.parquet | db6ab7eefe5e9e3e12123dd590f2c25f20bce7c15bc405a5b3355449994ba999-1265e66cc548d3f9 | parquet | 2715520 | 9 |
| SOURCE_TABLE_005609 | warehouse/product/arsa_emsalleri.sqlite | arsa_mahalle_trend | sqlite_table | 2715520 | 25 |
| SOURCE_TABLE_008369 | warehouse/silver/property_market/price_trend/8d36c24c005d60a8ab69a8e9450ec9b2c12338073051e629ab270cd0f61d5317-arsa_mahalle_trend.parquet | 8d36c24c005d60a8ab69a8e9450ec9b2c12338073051e629ab270cd0f61d5317-arsa_mahalle_trend | parquet | 2715520 | 27 |
| SOURCE_TABLE_008383 | warehouse/silver/property_market/price_trend/bd8787b88cf62502838acbf642e32911030db512ca31b7e9b9084073ca8b2342-document.parquet | bd8787b88cf62502838acbf642e32911030db512ca31b7e9b9084073ca8b2342-document | parquet | 2715520 | 27 |
| SOURCE_TABLE_005612 | warehouse/product/bolge_istatistik.sqlite | aylik_fiyat_trendi | sqlite_table | 1529982 | 15 |
| SOURCE_TABLE_000842 | VERİLER/Öğelerle Yeni Klasör 2/TUM_TURKIYE_TEK_PAKET_CSV 4/04_aylik_fiyat_trendi_2021_2026.csv | 04_aylik_fiyat_trendi_2021_2026.csv | csv | 1499422 | 15 |
| SOURCE_TABLE_000055 | VERİLER/Öğelerle Yeni Klasör 2/TUM_TURKIYE_TEK_PAKET_CSV.zip | TUM_TURKIYE_TEK_PAKET_CSV.zip::04_aylik_fiyat_trendi_2021_2026.csv | csv | 1499422 | 15 |
| SOURCE_TABLE_002835 | warehouse/bronze/csv/36/367bdbf3f0bd4026975c17dff4533b5c8a404725f2f8e6c968990771f5bbc41c.parquet | 367bdbf3f0bd4026975c17dff4533b5c8a404725f2f8e6c968990771f5bbc41c | parquet | 1499422 | 9 |
| SOURCE_TABLE_008371 | warehouse/silver/property_market/price_trend/9289c1382880016a8e94faf3f6a46c5ce8de9c34d0e8cfac3e47ca717c42cc85-document.parquet | 9289c1382880016a8e94faf3f6a46c5ce8de9c34d0e8cfac3e47ca717c42cc85-document | parquet | 1499422 | 27 |
| SOURCE_TABLE_005671 | warehouse/product/osm_degisim.sqlite | poi_yasam | sqlite_table | 680481 | 15 |
| SOURCE_TABLE_005681 | warehouse/product/osm_poi.sqlite | poi_rtree_rowid | sqlite_table | 623793 | 2 |
| SOURCE_TABLE_005669 | warehouse/product/osm_degisim.sqlite | poi_kimlik | sqlite_table | 623793 | 7 |
| SOURCE_TABLE_005678 | warehouse/product/osm_poi.sqlite | poi_rtree | sqlite_table | 623793 | 5 |
| SOURCE_TABLE_005677 | warehouse/product/osm_poi.sqlite | poi | sqlite_table | 623793 | 13 |
| SOURCE_TABLE_005645 | warehouse/product/cografi_katmanlar.sqlite | cografi_rtree_rowid | sqlite_table | 580432 | 2 |
| SOURCE_TABLE_005642 | warehouse/product/cografi_katmanlar.sqlite | cografi_rtree | sqlite_table | 580432 | 5 |
| SOURCE_TABLE_005641 | warehouse/product/cografi_katmanlar.sqlite | cografi_ozellikler | sqlite_table | 580432 | 10 |
| SOURCE_TABLE_005621 | warehouse/product/bolge_istatistik.sqlite | poi_ilce | sqlite_table | 501783 | 8 |
| SOURCE_TABLE_001511 | airbnb/astim-shard-2.zip | astim-shard-2.zip::shard_2.sqlite::tkgm_alim_satim_yogunlugu | sqlite_table | 404939 | 7 |

Şema kümeleri (benzer sütun yapısı; Jaccard≥0.8):

| Küme | Üye | Farklı imza | Ortak sütunlar |
|---|---|---|---|
| SCHEMA_CLUSTER_001 | 2853 | 1 | ["classification_reference_time", "collection_time", "domain", "entity", "entity_variant", "natural_key_json", "natural_key_kind", "natural_ |
| SCHEMA_CLUSTER_002 | 2054 | 1 | ["data_class", "domain", "entity", "json_type", "payload_bytes", "raw_payload", "source_content_sha256"] |
| SCHEMA_CLUSTER_003 | 1122 | 1 | ["coordinates", "id", "properties"] |
| SCHEMA_CLUSTER_004 | 795 | 1 | ["data_class", "domain", "entity", "parse_status", "raw_record_json", "row_sha256", "schema_fingerprint", "source_content_sha256", "source_r |
| SCHEMA_CLUSTER_005 | 348 | 1 | ["bolge_adi", "city_id", "county_id", "polygons"] |
| SCHEMA_CLUSTER_006 | 117 | 2 | ["adres", "city_id", "danisman_sayisi", "il_adi", "ilan_sayisi", "ofis_adi", "ofis_id", "slug"] |
| SCHEMA_CLUSTER_007 | 117 | 2 | ["aktif_ilan_sayisi", "city_id", "danisman_adi", "danisman_id", "il_adi", "ofis_adi", "telefon", "unvan"] |
| SCHEMA_CLUSTER_008 | 110 | 2 | ["alt_kategori", "bolge_adi", "city_id", "county_id", "kategori_id", "poi_adi", "poi_id", "slug"] |
| SCHEMA_CLUSTER_009 | 61 | 3 | ["arsa_arazi_satisi", "bolge_adi", "city_id", "county_id", "ipotekli_arsa_satisi", "ipotekli_konut_satisi", "seviye", "toplam_ilan_sayisi",  |
| SCHEMA_CLUSTER_010 | 61 | 3 | ["amortisman_yil", "bolge_adi", "brut_kira_getirisi", "city_id", "county_id", "donem", "guncellenme_tarihi", "ilan_sayisi", "kategori", "kir |
| SCHEMA_CLUSTER_011 | 61 | 3 | ["bolge_adi", "city_id", "county_id", "guncellenme_tarihi", "kisi_sayisi", "kutuk_ili", "seviye"] |
| SCHEMA_CLUSTER_012 | 61 | 4 | ["bolge_adi", "city_id", "county_id", "gecerli_oy", "kayitli_secmen", "kazanan_parti", "kullanilan_oy", "sandik_sayisi", "secim_adi", "secim |
| SCHEMA_CLUSTER_013 | 60 | 2 | ["amortisman_yil", "ay", "bolge_adi", "brut_kira_getirisi", "city_id", "county_id", "district_id", "ilan_sayisi", "kategori", "kiralik_m2_fi |
| SCHEMA_CLUSTER_014 | 60 | 2 | ["amortisman_yil", "bolge_adi", "city_id", "county_id", "dagilim_turu", "district_id", "ilan_sayisi", "kategori", "kiralik_m2_fiyat", "oran" |
| SCHEMA_CLUSTER_015 | 60 | 1 | ["city_id", "city_name", "created_at"] |
| SCHEMA_CLUSTER_016 | 60 | 1 | ["city_id", "county_id", "county_name", "created_at"] |
| SCHEMA_CLUSTER_017 | 59 | 4 | ["arac_sayisi", "arsa_fiyat_m2", "atm_sayisi", "banka_sube_sayisi", "bolge_adi", "city_id", "county_id", "eczane_sayisi", "egitim_ilkokul_or |
| SCHEMA_CLUSTER_018 | 59 | 1 | ["city_id", "county_id", "created_at", "district_id", "district_name"] |
| SCHEMA_CLUSTER_019 | 43 | 1 | ["analiz_tip", "boylam", "enlem", "il_id", "parsel_id", "sayi", "yil"] |
| SCHEMA_CLUSTER_020 | 43 | 1 | ["analiz_tip", "analiz_tip_ad", "cekilme_tarihi", "il_ad", "il_id", "nokta_sayisi", "toplam_islem", "yil"] |

## C. Duplicate analizi

- Exact duplicate (SHA-256 aynı) grup: **7,031**, dosya: **14,448**, fazlalık boyut: **3.43 GB** (silinmedi; yalnız `duplicate_group_id` atandı)

| Grup | Üye | Boyut | Örnek yollar |
|---|---|---|---|
| DUP_FILE_0001 | 2 | 245.7 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/9289c1382880016a8e94faf3f6a46c5ce8de9c34d0e8cfac3e47ca717c42cc85-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/9289c1382880016a8e94faf3f6a46c5ce8de9c34d0e8cfac3e47ca717c42cc85-document.parquet |
| DUP_FILE_0002 | 2 | 158.1 MB | /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/TUM_TURKIYE_TEK_PAKET_CSV 4/04_aylik_fiyat_trendi_2021_2026.csv ⟷ /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/ULUSAL_CSV/04_aylik_fiyat_trendi_2021_2026.csv |
| DUP_FILE_0003 | 2 | 90.1 MB | /Users/acar/Desktop/GEOPROP/warehouse/bronze/csv/36/367bdbf3f0bd4026975c17dff4533b5c8a404725f2f8e6c968990771f5bbc41c.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/bronze/csv/36/367bdbf3f0bd4026975c17dff4533b5c8a404725f2f8e6c968990771f5bbc41c.parquet |
| DUP_FILE_0004 | 2 | 48.6 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/restricted_context/election_results/d421ff28a749a9a4d5dec3fdd6f91e43c2de2b837ee2b8c1a110091dfedb1059-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/restricted_context/election_results/d421ff28a749a9a4d5dec3fdd6f91e43c2de2b837ee2b8c1a110091dfedb1059-document.parquet |
| DUP_FILE_0005 | 2 | 47.1 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/annual_sales/38f7ccbf171670cb1775d9362bb915dae0757b73beb0f986f56fcac2dc6ba969-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/annual_sales/38f7ccbf171670cb1775d9362bb915dae0757b73beb0f986f56fcac2dc6ba969-document.parquet |
| DUP_FILE_0006 | 2 | 42.7 MB | /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/TUM_TURKIYE_TEK_PAKET_CSV 4/07_secim_sonuclari_ve_oylar.csv ⟷ /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/ULUSAL_CSV/07_secim_sonuclari_ve_oylar.csv |
| DUP_FILE_0008 | 2 | 36.5 MB | /Users/acar/Downloads/restoran_ve_kafe_menuleri.sqlite ⟷ /Users/acar/Downloads/shard-db-1/restoran_ve_kafe_menuleri.sqlite |
| DUP_FILE_0009 | 2 | 33.7 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/229ac97bfebf123b89158f9cea41bb2ca293c1e8d18fcbcf029336505b87d8d9-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/229ac97bfebf123b89158f9cea41bb2ca293c1e8d18fcbcf029336505b87d8d9-document.parquet |
| DUP_FILE_0010 | 2 | 32.7 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/beddd5823fd93f4a3f259891222928744994b94d7865c71d87318e0ff7d5d32a-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/beddd5823fd93f4a3f259891222928744994b94d7865c71d87318e0ff7d5d32a-document.parquet |
| DUP_FILE_0011 | 2 | 32.5 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/039e51538cd95519c809cf6ed66e95899f52eab200615352557766843b4e7f85-fiyat_trend.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/039e51538cd95519c809cf6ed66e95899f52eab200615352557766843b4e7f85-fiyat_trend.parquet |
| DUP_FILE_0012 | 2 | 29.3 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/restricted_context/registry_origin_distribution/e927255f6ef71cb2e0a9a0d5abf47ba1b05886dea640c73cf22df1d4be4ad162-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/restricted_context/registry_origin_distribution/e927255f6ef71cb2e0a9a0d5abf47ba1b05886dea640c73cf22df1d4be4ad162-document.parquet |
| DUP_FILE_0007 | 3 | 24.4 MB | /Users/acar/Desktop/tkgm/extension/collector/sektor_01_güneybatı_ege___rodos-marmaris_sahil.geojson ⟷ /Users/acar/Downloads/collector/data/cografya/vektor_sektorler/sektor_01_güneybatı_ege___rodos-marmaris_sahil.geojson ⟷ /Users/acar/Downloads/sektor_01_guneybati_ege.geojson |
| DUP_FILE_0014 | 2 | 23.7 MB | /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/TUM_TURKIYE_TEK_PAKET_CSV 4/02_yillik_satislar_2010_2024.csv ⟷ /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/ULUSAL_CSV/02_yillik_satislar_2010_2024.csv |
| DUP_FILE_0015 | 2 | 23.2 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/546061dc65d9888282595386c112f3b63f9880c87561fab7f7e9538955cdfc38-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/546061dc65d9888282595386c112f3b63f9880c87561fab7f7e9538955cdfc38-document.parquet |
| DUP_FILE_0016 | 2 | 23.2 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/f21ca1a4e3cbfb02de7a6d1b109177cb49b807648d487a53350314e44e4ce451-fiyat_trend.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/f21ca1a4e3cbfb02de7a6d1b109177cb49b807648d487a53350314e44e4ce451-fiyat_trend.parquet |
| DUP_FILE_0017 | 2 | 21.5 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/60998f63fad9783e5cc8119fca8c1e2d51b09e2802d85fecc1614bde1c7e7754-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/60998f63fad9783e5cc8119fca8c1e2d51b09e2802d85fecc1614bde1c7e7754-document.parquet |
| DUP_FILE_0018 | 2 | 21.5 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_trend/9de03e0cd11cdb34ab55a8118ae39c7ba8cbb4c46fbbbfe645857f5c2dbb4a8e-fiyat_trend.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_trend/9de03e0cd11cdb34ab55a8118ae39c7ba8cbb4c46fbbbfe645857f5c2dbb4a8e-fiyat_trend.parquet |
| DUP_FILE_0019 | 2 | 20.9 MB | /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/BOLGE_CSV/bolge_15_dogu_guneydogu/04_aylik_fiyat_trendi_2021_2026.csv ⟷ /Users/acar/Desktop/GEOPROP/VERİLER/Öğelerle Yeni Klasör 2/bolge_15_dogu_guneydogu/data/csv_ciktilari/04_aylik_fiyat_trendi_2021_2026.csv |
| DUP_FILE_0020 | 2 | 19.7 MB | /Users/acar/Desktop/GEOPROP/warehouse/silver/property_market/price_distribution/e77254a2ed01e0b56dc613a1d7751940e07b066138f9bbae0df4f4c222a13739-document.parquet ⟷ /Users/acar/Desktop/tkgm/extension/warehouse/silver/property_market/price_distribution/e77254a2ed01e0b56dc613a1d7751940e07b066138f9bbae0df4f4c222a13739-document.parquet |
| DUP_FILE_0021 | 2 | 19.6 MB | /Users/acar/Downloads/aracrisk-anonim-emsaller-2026-09-10.json ⟷ /Users/acar/Downloads/workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/data/market/aracrisk-anonim-emsaller-2026-09-10.json |

Kökler ARASI (GEOPROP ⟷ tkgm ⟷ Downloads ⟷ harita) duplicate grup sayısı: **6864**


ZIP içerikleri (çıkarılmadı; üye listesi ve CSV/JSON/SQLite üyeleri bellekte envanterlendi):

| ZIP | Üye | Sıkıştırılmamış | Envanterlenen üye | İç içe zip |
|---|---|---|---|---|
| tkgm-alim-satim-ambar.zip | 1 | 633.3 MB | 1 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/TUM_TURKIYE_TEK_PAKET_CSV.zip | 14 | 266.0 MB | 14 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/bolge_15_dogu_guneydogu/paket_15_dogu_guneydogu.zip | 211 | 224.2 MB | 209 | 0 |
| VERİLER/TURKIYE-ARSA-TARLA-ENDEKS-40-MAKINE-PAKETI.zip | 1 | 221.7 MB | 0 | 1 |
| archive.zip | 10 | 213.8 MB | 10 | 0 |
| VERİLER/sektor-40-geojson.zip | 1 | 124.2 MB | 1 | 0 |
| VERİLER/sektor-32-geojson.zip | 1 | 99.0 MB | 1 | 0 |
| VERİLER/sektor-31-geojson.zip | 1 | 98.3 MB | 1 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/bolge_12_orta_anadolu (1)/paket_12_orta_anadolu.zip | 97 | 93.2 MB | 95 | 0 |
| VERİLER/sektor-33-geojson.zip | 1 | 90.0 MB | 1 | 0 |
| VERİLER/sektor-13-geojson.zip | 1 | 84.7 MB | 1 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/bolge_15_dogu_guneydogu.zip | 15 | 73.8 MB | 14 | 1 |
| VERİLER/sektor-8-geojson.zip | 1 | 73.5 MB | 1 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/bolge_01_istanbul/paket_01_istanbul.zip | 57 | 71.0 MB | 55 | 0 |
| VERİLER/sektor-28-geojson.zip | 1 | 69.4 MB | 1 | 0 |
| VERİLER/sektor-30-geojson.zip | 1 | 68.5 MB | 1 | 0 |
| VERİLER/sektor-34-geojson.zip | 1 | 68.0 MB | 1 | 0 |
| VERİLER/sektor-21-geojson.zip | 1 | 66.0 MB | 1 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/bolge_04_bursa_yalova/paket_04_bursa_yalova.zip | 51 | 61.9 MB | 49 | 0 |
| VERİLER/sektor-22-geojson.zip | 1 | 59.8 MB | 1 | 0 |
| VERİLER/sektor-25-geojson.zip | 1 | 58.0 MB | 1 | 0 |
| VERİLER/sektor-39-geojson.zip | 1 | 54.6 MB | 1 | 0 |
| VERİLER/sektor-27-geojson.zip | 1 | 53.7 MB | 1 | 0 |
| VERİLER/sektor-29-geojson.zip | 1 | 52.2 MB | 1 | 0 |
| VERİLER/Öğelerle Yeni Klasör 2/bolge_13_orta_karadeniz.zip | 15 | 51.5 MB | 14 | 1 |

Tahmini kayıt-düzeyi duplicate kümeleri (aynı şema kümesinde birden çok kaynak → Phase 3 adayı):

| Küme | Üye | Toplam satır | Örnek tablolar |
|---|---|---|---|
| SCHEMA_CLUSTER_019 | 43 | 21228442 | astim-shard-19.zip::shard_19.sqlite::tkgm_alim_satim_yogunlugu, astim-shard-21.zip::shard_21.sqlite::tkgm_alim_satim_yogunlugu, astim-shard-26.zip::shard_26.sql |
| SCHEMA_CLUSTER_001 | 2853 | 15142610 | 2633dbf7d07a40a7f95abc871f86a6315dcd76ad97a6f469e6ba07732b032064-document, 01f75731737140992c95323e782198565061f78581b5f054b83d85b84cc92e9b-document, 09eef03021 |
| SCHEMA_CLUSTER_004 | 795 | 15089685 | 02cc0a8be0a1d77b8768ba42f57ac7f0d1afe2df501604206d77ef5c0ad80ca6, 0c825c43de2fe30821af4b05a25850595b576e2cd5bc79108b2275096aa42888, 0cb1b2158f3e00fd564a6a264128 |
| SCHEMA_CLUSTER_013 | 60 | 8667338 | bolge_05_kocaeli_sakarya.zip::data/csv_ciktilari/04_aylik_fiyat_trendi_2021_2026.csv, bolge_06_trakya_guney_marmara.zip::data/csv_ciktilari/04_aylik_fiyat_trend |
| SCHEMA_CLUSTER_064 | 3 | 5434320 | arsa_mahalle_trend, 04_arsa_tarla_mahalle_80_aylik_trend.csv |
| SCHEMA_CLUSTER_113 | 2 | 2844718 | poi_yillik, poi_olay |
| SCHEMA_CLUSTER_008 | 110 | 1755121 | bolge_02_ankara.zip::data/csv_ciktilari/08_ilce_onemli_noktalar_poi.csv, m16_poi_53_58.zip::data/csv_ciktilari/08_ilce_onemli_noktalar_poi.csv, m19_poi_71_76.zi |
| SCHEMA_CLUSTER_009 | 61 | 1751633 | bolge_02_ankara.zip::data/csv_ciktilari/02_yillik_satislar_2010_2024.csv, 02_yillik_satislar_2010_2024.csv, paket_04_bursa_yalova.zip::data/piyasa_verileri.db:: |
| SCHEMA_CLUSTER_012 | 61 | 1702163 | bolge_04_bursa_yalova (1).zip::data/csv_ciktilari/07_secim_sonuclari_ve_oylar.csv, paket_04_bursa_yalova.zip::data/csv_ciktilari/07_secim_sonuclari_ve_oylar.csv |
| SCHEMA_CLUSTER_034 | 15 | 1317929 | rtree_demiryollari_rowid, rtree_sit_alanlari_rowid, gurultu_rtree_rowid, cografi_rtree_rowid, rtree_meskun_rowid, rtree_su_yollari_rowid, rtree_elektrik_rowid,  |
| SCHEMA_CLUSTER_070 | 3 | 1217050 | cografi_rtree, gurultu_rtree, poi_rtree |
| SCHEMA_CLUSTER_011 | 61 | 1049786 | bolge_01_istanbul (1).zip::data/csv_ciktilari/06_hemsehri_kutuk_dagilimi.csv, bolge_08_guney_ege.zip::data/csv_ciktilari/06_hemsehri_kutuk_dagilimi.csv, bolge_1 |
| SCHEMA_CLUSTER_014 | 60 | 677103 | bolge_02_ankara.zip::data/csv_ciktilari/05_oda_yas_kat_isitma_kirilimlari.csv, m05_igdir.zip::data/csv_ciktilari/05_oda_yas_kat_isitma_kirilimlari.csv, paket_01 |
| SCHEMA_CLUSTER_021 | 40 | 666082 | sektor-1-geojson.zip::collector/data/cografya/vektor_sektorler/sektor_01_güneybatı_ege___rodos-marmaris_sahil.geojson, sektor-11-geojson.zip::collector/data/cog |
| SCHEMA_CLUSTER_045 | 6 | 277348 | emlakjet-trend-arsa (2).csv, emlakjet-trend-konut (1).csv, emlakjet-trend-konut (2).csv, emlakjet-trend-arsa.csv, emlakjet-trend-arsa (1).csv, emlakjet-trend-ko |

## D. Veri kalitesi

### Parse / okuma sorunları

| Aşama | Hata türü | Adet | Örnek |
|---|---|---|---|
| probe | xls_legacy_unsupported | 8 | Eski .xls (OLE) için okuyucu yok (xlrd kurulu değil); Phase 2'de eklenecek |
| json_decode | JSONDecodeError | 4 | line 1 col 5525: Extra data |
| content_probe | ValueError | 3 | Worksheet is unsized, use calculate_dimension(force=True) c)     ~~~~~~~~~~~~~~^^^^^^^^^^   File "/Users/acar/Desktop/GE |
| verify | NEW_FILE_IN_SOURCE_DURING_SCAN | 1 | Tarama sırasında kaynak kökte yeni dosya belirdi (dış süreç veya yan dosya): /Users/acar/Desktop/tkgm/extension/collecto |
| csv_probe | retry_parallel_false | 1 | CSV Error on Line: 124  The parallel scanner does not support null_padding in conjunction with quoted new lines. Please  |

Satır bazında kaydedilen parse hatası: **2,832** (ham satır `parse_errors.parquet` içinde korunur; hiçbir satır düşürülmedi)

| file_id | tablo | hata türü | adet |
|---|---|---|---|
| FILE_00005074 | mahalle_bina_2017.csv | INVALID ENCODING | 1414 |
| FILE_00005071 | deprem_senaryosu.csv | INVALID ENCODING | 1414 |
| FILE_00000018 | debug_google.json | JSON_DECODE | 1 |
| FILE_00016955 | bun.lock | JSON_DECODE | 1 |
| FILE_00016027 | obstacles_3d.json | JSON_DECODE | 1 |
| FILE_00016618 | besiktas_goztepe_data.json | JSON_DECODE | 1 |

CSV satır sayısı tutarsızlığı notu olan tablolar (ham satır ≠ yüklenen+reject+başlık; çoğunlukla çok satırlı alıntılı alanlar):

| table_id | tablo | satır | reject | not |
|---|---|---|---|---|
| SOURCE_TABLE_001594 | turkiye_tam_airbnb_ilanlari.csv | 1536 | 0 | LINE_COUNT_MISMATCH raw=1579 accounted=1537 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_008871 | OperaSetup.zip::Opera Installer.app/Contents/Resources/policy.txt | 430 | 0 | LINE_COUNT_MISMATCH raw=702 accounted=431 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_008872 | OperaSetup.zip::Opera Installer.app/Contents/Resources/tos.txt | 397 | 0 | LINE_COUNT_MISMATCH raw=565 accounted=398 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_005750 | deprem_senaryosu.csv | 67 | 1414 | LINE_COUNT_MISMATCH raw=960 accounted=1482 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_005753 | mahalle_bina_2017.csv | 67 | 1414 | LINE_COUNT_MISMATCH raw=960 accounted=1482 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_008870 | OperaSetup.zip::Opera Installer.app/Contents/Resources/eula_desktop.txt | 20 | 0 | LINE_COUNT_MISMATCH raw=42 accounted=21 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_008908 | bozcarrental.com-20260522T155238Z-3-001.zip::bozcarrental.com/llms.txt | 14 | 0 | LINE_COUNT_MISMATCH raw=20 accounted=15 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_009151 | page.tsx | 4 | 0 | LINE_COUNT_MISMATCH raw=7 accounted=4 (çok satırlı alanlar olabilir) |
| SOURCE_TABLE_009152 | page.tsx | 4 | 0 | LINE_COUNT_MISMATCH raw=7 accounted=4 (çok satırlı alanlar olabilir) |

### Encoding

| Encoding | Dosya |
|---|---|
| utf-8 | 3297 |
| utf-8-sig | 629 |
| cp1250 | 2 |
| iso8859_10 | 2 |
| cp1256 | 2 |
| cp1257 | 1 |
| cp1254 | 1 |

### Şema sorunları

- Sıfırla başlayan sayısal görünümlü değer içeren sütunlar (tip zorlanırsa sıfır kaybolur): **50** sütun

| table_id | tablo | sütun | adet | örnek |
|---|---|---|---|---|
| SOURCE_TABLE_001596 | cografi_varliklar | il_plaka | 3982 | ["70", "00", "21", "04", "44"] |
| SOURCE_TABLE_001597 | turkiye_cografi_varliklar_ozet.csv | il_plaka | 3982 | ["74", "00", "71", "77", "73"] |
| SOURCE_TABLE_001592 | turkiye_kargo_ve_kargomatlar | telefon | 3166 | ["03224214008", "03222345792", "03223233313", "05367767215", |
| SOURCE_TABLE_005658 | kargo_ve_teslimat_noktalari | telefon | 3166 | ["05365633149", "03223240018", "03223289403", "03226712003", |
| SOURCE_TABLE_001616 | 18_kargo_ve_teslimat_noktalari.csv | telefon | 3166 | ["03224214008", "03222345792", "03223233313", "05367767215", |
| SOURCE_TABLE_001576 | kargo_ve_teslimat_noktalari | telefon | 3166 | ["05365633149", "03223240018", "03223289403", "03226712003", |
| SOURCE_TABLE_001598 | turkiye_daglar_ve_zirveler | il_plaka | 2951 | ["71", "12", "73", "91", "07"] |
| SOURCE_TABLE_008746 | ham_su_yollari_ve_dereler | il_plaka | 1830 | ["00", "02", "72", "74", "03"] |
| SOURCE_TABLE_008698 | su_yollari_ve_dereler | il_plaka | 1829 | ["00", "02", "72", "74", "03"] |
| SOURCE_TABLE_008697 | su_altyapisi_ve_kaynaklar | il_plaka | 509 | ["65", "43", "10", "38", "71"] |
| SOURCE_TABLE_008748 | dogal_pinarlar_ve_cesmeler.csv | il_plaka | 283 | ["10", "07", "71", "73", "83"] |
| SOURCE_TABLE_008762 | turkiye_dogal_pinarlar_ve_cesmeler | il_plaka | 283 | ["10", "07", "71", "73", "83"] |
| SOURCE_TABLE_008742 | ham_pinarlar_ve_cesmeler | il_plaka | 283 | ["65", "43", "10", "38", "03"] |
| SOURCE_TABLE_008750 | su_kuyulari.csv | il_plaka | 200 | ["12", "83", "74", "59", "26"] |
| SOURCE_TABLE_008768 | turkiye_su_kuyulari | il_plaka | 200 | ["12", "83", "74", "59", "26"] |

- SQLite: bildirilen tip ile gözlenen tip çelişen sütunlar (ör. INTEGER sütunda text):

| table_id | tablo | sütun | bildirilen | gözlenen |
|---|---|---|---|---|

- Sabit (tek değerli) sütunlar: **2844**


### Geo sorunları

| Metrik | Değer |
|---|---|
| Geo katman | 89 |
| Toplam özellik | 797957 |
| Farklı CRS | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}, unspecified(GeoJSON RFC7946 → WGS84 varsayımı yapılmadı), EPSG:4326, EPSG:3857 |
| NULL geometri | 0 |
| Boş geometri | 0 |
| Geçersiz geometri (ST_IsValid=false; ONARILMADI) | 5 |
| Geçerlilik ölçülemeyen katman | 45 |

Geometri türü dağılımı (katman bazında):

| file_id | katman | sürücü | özellik | geometri | CRS | geçersiz | null |
|---|---|---|---|---|---|---|---|
| FILE_00000132 | collector/data/cografya/vektor_sektorler/sektor_40_kafkas_sınırı___ardahan-kars-çıldır.geojson | zip_member_geojson | 47375 | {"LineString": 26153, "Point": 2238, "Polygon": 18 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00008623 | Turkiye_Tam_Fiziksel_ve_Hukuki_Altyapi_Envanteri | GDAL | 38486 | {"POINT": 601, "LINESTRING": 31659, "POLYGON": 622 | EPSG:4326 | 0 | 0 |
| FILE_00008633 | Turkiye_Sektor_34_İstanbul & Boğaz Koridoru / Kocaeli-Yalova | GDAL | 38486 | {"POINT": 601, "LINESTRING": 31659, "POLYGON": 622 | EPSG:4326 | 0 | 0 |
| FILE_00000125 | collector/data/cografya/vektor_sektorler/sektor_34_i̇stanbul_&_boğaz_koridoru___kocaeli-yalova.geojson | zip_member_geojson | 38486 | {"LineString": 31659, "Point": 601, "Polygon": 622 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000124 | collector/data/cografya/vektor_sektorler/sektor_33_trakya___edirne-kırklareli-tekirdağ.geojson | zip_member_geojson | 36768 | {"LineString": 22790, "Point": 1199, "Polygon": 12 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000123 | collector/data/cografya/vektor_sektorler/sektor_32_ağrı_dağı___erzurum_doğu-ağrı-iğdır.geojson | zip_member_geojson | 34117 | {"LineString": 22922, "Point": 2554, "Polygon": 86 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000122 | collector/data/cografya/vektor_sektorler/sektor_31_erzincan_ovası___kaf_erzincan-erzurum.geojson | zip_member_geojson | 32249 | {"LineString": 25042, "Point": 100, "Polygon": 710 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00008622 | turkiye_yollar_ve_gelecek_projeler | GDAL | 28663 | {"LINESTRING": 28663} | EPSG:4326 | 0 | 0 |
| FILE_00000118 | collector/data/cografya/vektor_sektorler/sektor_28_başkent___ankara-kırıkkale.geojson | zip_member_geojson | 23753 | {"LineString": 16490, "Point": 251, "Polygon": 701 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000136 | collector/data/cografya/vektor_sektorler/sektor_08_cizre-silopi-hakkari_güney_sınırı.geojson | zip_member_geojson | 22676 | {"LineString": 17927, "Point": 362, "Polygon": 438 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00008632 | turkiye_tam_yollar_ve_projeler | GDAL | 22155 | {"LINESTRING": 22155} | EPSG:4326 | 0 | 0 |
| FILE_00000121 | collector/data/cografya/vektor_sektorler/sektor_30_sivas_platosu___kaf_tokat_kuşağı.geojson | zip_member_geojson | 21785 | {"LineString": 19135, "Point": 211, "Polygon": 243 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000106 | collector/data/cografya/vektor_sektorler/sektor_17_i̇zmir_körfezi___çeşme-karaburun-manisa.geojson | zip_member_geojson | 20959 | {"LineString": 16829, "Point": 448, "Polygon": 368 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000117 | collector/data/cografya/vektor_sektorler/sektor_27_bilecik-eskişehir___bozüyük_havzası.geojson | zip_member_geojson | 18217 | {"LineString": 13940, "Point": 258, "Polygon": 401 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000130 | collector/data/cografya/vektor_sektorler/sektor_39_rize-artvin___kaçkar_dağları.geojson | zip_member_geojson | 17942 | {"LineString": 9215, "Point": 587, "Polygon": 8140 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000137 | collector/data/cografya/vektor_sektorler/sektor_09_ege_sahili___muğla-aydın-milas.geojson | zip_member_geojson | 17898 | {"LineString": 14900, "Point": 261, "Polygon": 273 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00016899 | Turkiye_Sektor_9_Ege Sahili / Muğla-Aydın-Milas | GDAL | 17898 | {"POINT": 261, "LINESTRING": 14900, "POLYGON": 273 | EPSG:4326 | 0 | 0 |
| FILE_00000133 | collector/data/cografya/vektor_sektorler/sektor_05_çukurova___mersin-adana_sahil.geojson | zip_member_geojson | 17178 | {"LineString": 13658, "Point": 90, "Polygon": 3430 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000115 | collector/data/cografya/vektor_sektorler/sektor_25_kuzey_ege___çanakkale-edremit_körfezi.geojson | zip_member_geojson | 16579 | {"LineString": 10487, "Point": 682, "Polygon": 541 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000102 | collector/data/cografya/vektor_sektorler/sektor_13_toroslar___niğde-kayseri_güneyi.geojson | zip_member_geojson | 15692 | {"LineString": 10690, "Point": 359, "Polygon": 464 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000112 | collector/data/cografya/vektor_sektorler/sektor_22_yukarı_fırat___malatya-elazığ-sivas_güneyi.geojson | zip_member_geojson | 14146 | {"LineString": 9327, "Point": 122, "Polygon": 4697 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000104 | collector/data/cografya/vektor_sektorler/sektor_15_diyarbakır-batman-siirt_koridoru.geojson | zip_member_geojson | 14035 | {"LineString": 11015, "Point": 128, "Polygon": 289 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000937 | turkiye_daglar_ve_zirveler | GDAL | 12525 | {"POINT": 12525} | EPSG:4326 | 0 | 0 |
| FILE_00000099 | collector/data/cografya/vektor_sektorler/sektor_10_i̇ç_batı_anadolu___denizli-burdur.geojson | zip_member_geojson | 11982 | {"LineString": 9603, "Point": 274, "Polygon": 2105 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000111 | collector/data/cografya/vektor_sektorler/sektor_21_kapadokya___nevşehir-kırşehir-kayseri.geojson | zip_member_geojson | 11522 | {"LineString": 9491, "Point": 436, "Polygon": 1595 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000100 | collector/data/cografya/vektor_sektorler/sektor_11_göller_yöresi___isparta-beyşehir.geojson | zip_member_geojson | 11239 | {"LineString": 7602, "Point": 523, "Polygon": 3114 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000119 | collector/data/cografya/vektor_sektorler/sektor_29_orta_anadolu___yozgat-çorum.geojson | zip_member_geojson | 10803 | {"LineString": 7319, "Point": 242, "Polygon": 3242 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00000103 | collector/data/cografya/vektor_sektorler/sektor_14_doğu_anadolu_fayı___k.maraş-malatya-adıyaman.geojson | zip_member_geojson | 10204 | {"LineString": 7935, "Point": 34, "Polygon": 2235} | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |
| FILE_00008631 | turkiye_tam_su_yollari_ve_kanallar | GDAL | 9839 | {"LINESTRING": 7890, "POLYGON": 1949} | EPSG:4326 | 0 | 0 |
| FILE_00000120 | collector/data/cografya/vektor_sektorler/sektor_03_antalya_körfezi___alanya-gazipaşa.geojson | zip_member_geojson | 9784 | {"LineString": 6784, "Point": 793, "Polygon": 2207 | {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}} |  | 0 |

GEO_CONFLICT (Türkiye sınır kutusu dışı koordinat/özellik):

| file_id | tablo | mesaj |
|---|---|---|
| FILE_00000770 | ilanlar | Koordinat Türkiye sınır kutusu dışında. [column=lat,lon hits=7] |
| FILE_00000780 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=2] |
| FILE_00000787 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=4] |
| FILE_00000788 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=15] |
| FILE_00000793 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=1] |
| FILE_00000806 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=11] |
| FILE_00000807 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=7] |
| FILE_00000811 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=3] |
| FILE_00000812 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=3] |
| FILE_00000814 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=2] |
| FILE_00000815 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=1] |
| FILE_00000816 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=5] |
| FILE_00000817 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=4] |
| FILE_00000818 | tkgm_alim_satim_yogunlugu | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=2] |
| FILE_00000904 | turkiye_detayli_diri_faylar | 40/895 özelliğin merkezi Türkiye sınır kutusu dışında (CRS=EPSG:4326). [column=wkb_geometry hits=40] |
| FILE_00000933 | turkiye_tam_airbnb_ilanlari.csv | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=405] |
| FILE_00000934 | turkiye_tam_airbnb_ilanlari | 405/1536 özelliğin merkezi Türkiye sınır kutusu dışında (CRS=EPSG:4326). [column=wkb_geometry hits=405] |
| FILE_00000935 | cografi_varliklar | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=1] |
| FILE_00000936 | turkiye_cografi_varliklar_ozet.csv | Koordinat Türkiye sınır kutusu dışında. [column=enlem,boylam hits=1] |
| FILE_00000937 | turkiye_daglar_ve_zirveler | 1/12525 özelliğin merkezi Türkiye sınır kutusu dışında (CRS=EPSG:4326). [column=wkb_geometry hits=1] |

## E. Karantina / inceleme kaydı (Phase 1: yalnız İŞARETLEME; dosya taşınmadı)

| Durum | Kayıt | Dosya |
|---|---|---|
| NOTE | 2854 | 2854 |
| REVIEW_REQUIRED | 290 | 239 |
| QUARANTINE | 176 | 45 |
| GEO_CONFLICT | 89 | 63 |

Kural bazında:

| Kural | Durum | Kayıt | Dosya | Gerekçe |
|---|---|---|---|---|
| T08 | NOTE | 2854 | 2854 | record_class='quarantine' satırları önceki ekip tarafından işaretlenmiş; sayısı raporlanır. |
| T02 | QUARANTINE | 132 | 22 | bati_buyuksehirler_ticari_toplayici.py sentetik/fallback tabloları (kod 2a8c971 ile silindi, veri kopyaları kalmış olabilir). |
| C03 | REVIEW_REQUIRED | 128 | 87 | 2027-08'e kadar 407.328 projeksiyon satırı gözlem satırlarından ayrı etiketlenmeli (README). [column=ay hits=1032] |
| Q06 | REVIEW_REQUIRED | 90 | 90 | Demo/test/örnek dizini: sabit gösterim verisi olabilir (INCELEME_RAPORU: İstanbul sabit veri demosu). |
| C04 | GEO_CONFLICT | 89 | 63 | 1/12525 özelliğin merkezi Türkiye sınır kutusu dışında (CRS=EPSG:4326). [column=wkb_geometry hits=1] |
| X01 | REVIEW_REQUIRED | 37 | 37 | uzantı=.jpg ama içerik=png |
| R01 | REVIEW_REQUIRED | 13 | 10 | GEOPROP kaynak sicili: source_id=local_commercial_models data_class=mixed_observed_projection entity=socioeconomic_development |
| T07 | REVIEW_REQUIRED | 11 | 6 | Eski sürümde calculate_star_distribution() ve yorum_sayisi=değerlendirme×0.45 ile yıl bazlı bölme formülle üretiliyordu (mock_veri_tespiti.md §2). Ger |
| T06 | QUARANTINE | 8 | 7 | tahmin_ufku='MODEL-2026-2027' sütunu yalnızca sentetik ETBİS/SEGE demo tablolarında vardı. |
| C02 | QUARANTINE | 6 | 3 | Tüm satırlarda aynı KAKS/TAKS değeri = fallback sabiti. (değer=0.35) [column=taks hits=12] |
| T01 | QUARANTINE | 5 | 1 | turkiye_lokasyon_ve_ticari_istihbarat_toplayici.py tarafından sabit örnek/katsayı ile üretilen tablolar. |
| Q04 | QUARANTINE | 4 | 4 | E-Plan başarısızlığında her parsele Konut Alanı, KAKS 1.50, TAKS 0.35, 5 kat, aynı PIN/tarih yazılıyordu; bağımsız bölüm sayısı alan/70 ile üretiliyor |
| Q03 | QUARANTINE | 4 | 4 | Sentetik istihbarat DB'sinin CSV dışa aktarımları (gercek_veri_kurtarma_plani.md 'Silinen Geçersiz Betikler ve Çıktıları' §5). |
| T05 | REVIEW_REQUIRED | 4 | 4 | Sentetik istihbarat betiğinde tanımlanan tablo; yeni kargo_darkstore toplayıcısı gerçek veri yazıyor olabilir — kaynağa göre ayrıştır. |
| Q05 | REVIEW_REQUIRED | 4 | 4 | Aynı sentetik imar motorunun JSON/GeoJSON çıktısı: geometri gerçek olabilir, imar öznitelikleri (kaks/taks/kat) sentetik. Sütun bazında ayrıştırma ger |
| Q08 | QUARANTINE | 4 | 4 | Projenin kendi karantina dizini (warehouse/quarantine_macro): önceki ekip bu dosyaları karantinaya almış. |
| R01 | QUARANTINE | 4 | 2 | GEOPROP kaynak sicili: source_id=parcel_and_zoning_mixed data_class=quarantine entity=parcel_zoning |
| X04 | QUARANTINE | 4 | 4 | JSON_DECODE_ERROR line=1 col=5525: Extra data |
| X03 | REVIEW_REQUIRED | 2 | 2 | reject=1414 / rows=67 |
| T03 | QUARANTINE | 2 | 1 | Sabit KAKS/TAKS/PIN ve alan/70 türetimi (INCELEME_RAPORU P0; tests/test_veri_katalogu.py bu tabloları karantina sınıfına alıyor). |
| C01 | QUARANTINE | 2 | 2 | 'İl 2', 'İl 4' gibi üretilmiş il adları (INCELEME_RAPORU P1). [column=il_adi hits=52] |
| Q07 | REVIEW_REQUIRED | 1 | 1 | Dosya adı sentetik/örnek veri işaret ediyor. |
| Q01 | QUARANTINE | 1 | 1 | Tamamen sentetik: turkiye_lokasyon_ve_ticari_istihbarat_toplayici.py plaka kodundan (p%7, p%11) ETBİS/SEGE/ciro/tütün değerleri üretiyordu (mock_veri_ |

QUARANTINE durumundaki dosyalar (tam liste `quarantine/quarantine_registry.csv`):

| file_id | yol | kurallar |
|---|---|---|
| FILE_00000954 | /Users/acar/Desktop/GEOPROP/collector/data/csv_ciktilari/15_e_ticaret_ve_harcama_kalemleri.csv | T06 |
| FILE_00000922 | /Users/acar/Desktop/GEOPROP/collector/data/eticaret_ve_lojistik.sqlite | T06 |
| FILE_00000969 | /Users/acar/Desktop/GEOPROP/collector/data/imar_ve_parseller/parsel_imar_degisiklikleri.sqlite | R01,Q04,T03,C02 |
| FILE_00000970 | /Users/acar/Desktop/GEOPROP/collector/data/imar_ve_parseller/parsel_imar_ve_degisiklikler.json | C02 |
| FILE_00000971 | /Users/acar/Desktop/GEOPROP/collector/data/imar_ve_parseller/turkiye_parseller_geo.geojson | C02 |
| FILE_00000926 | /Users/acar/Desktop/GEOPROP/collector/data/parsel_imar_degisiklikleri.sqlite | Q04 |
| FILE_00000018 | /Users/acar/Desktop/GEOPROP/debug_google.json | X04 |
| FILE_00002083 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_1.sqlite | T02 |
| FILE_00002084 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_10.sqlite | T02 |
| FILE_00002085 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_11.sqlite | T02 |
| FILE_00002086 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_12.sqlite | T02 |
| FILE_00002087 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_13.sqlite | T02 |
| FILE_00002088 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_14.sqlite | T02 |
| FILE_00002089 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_15.sqlite | T02 |
| FILE_00002090 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_16.sqlite | T02 |
| FILE_00002091 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_17.sqlite | T02 |
| FILE_00002092 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_18.sqlite | T02 |
| FILE_00002093 | /Users/acar/Desktop/GEOPROP/scratch/shards/shard_19.sqlite | T02 |
| FILE_00005067 | /Users/acar/Desktop/GEOPROP/warehouse/quarantine_macro/bddk_finturk_finansal_gostergeler.sqlite | Q08 |
| FILE_00005068 | /Users/acar/Desktop/GEOPROP/warehouse/quarantine_macro/bkm_sektorel_kart_harcama.sqlite | Q08 |
| FILE_00005069 | /Users/acar/Desktop/GEOPROP/warehouse/quarantine_macro/kap_perakende_ve_sube_cirolari.sqlite | Q08 |
| FILE_00005070 | /Users/acar/Desktop/GEOPROP/warehouse/quarantine_macro/tuik_bolge.sqlite | Q08 |
| FILE_00016027 | /Users/acar/Desktop/harita/data/exports/obstacles_3d.json | X04 |
| FILE_00008653 | /Users/acar/Desktop/tkgm/extension/collector/data/csv_ciktilari/15_e_ticaret_ve_harcama_kalemleri.csv | T06 |
| FILE_00008657 | /Users/acar/Desktop/tkgm/extension/collector/data/csv_ciktilari/19_etbis_il_bazli_e_ticaret_hacimleri.csv | Q03,C01,T06 |
| FILE_00008658 | /Users/acar/Desktop/tkgm/extension/collector/data/csv_ciktilari/20_sege_973_ilce_sosyo_ekonomik_kademe.csv | T06,Q03 |
| FILE_00008659 | /Users/acar/Desktop/tkgm/extension/collector/data/csv_ciktilari/21_ciro_potansiyeli_ve_ticari_cekicilik.csv | Q03 |
| FILE_00008660 | /Users/acar/Desktop/tkgm/extension/collector/data/csv_ciktilari/22_tuik_sigara_ve_tutun_tuketim_endeksi.csv | Q03 |
| FILE_00008566 | /Users/acar/Desktop/tkgm/extension/collector/data/eticaret_ve_lojistik.sqlite | T06 |
| FILE_00008674 | /Users/acar/Desktop/tkgm/extension/collector/data/imar_ve_parseller/parsel_imar_degisiklikleri.sqlite | R01,Q04 |
| FILE_00008570 | /Users/acar/Desktop/tkgm/extension/collector/data/parsel_imar_degisiklikleri.sqlite | Q04 |
| FILE_00008577 | /Users/acar/Desktop/tkgm/extension/collector/data/turkiye_makro_ve_mikro_istihbarat.sqlite | T06,Q01,C01,T01 |
| FILE_00016618 | /Users/acar/Downloads/besiktas_goztepe_data.json | X04 |
| FILE_00016824 | /Users/acar/Downloads/shard_1.zip | T02 |
| FILE_00016825 | /Users/acar/Downloads/shard_10.zip | T02 |
| FILE_00016826 | /Users/acar/Downloads/shard_11.zip | T02 |
| FILE_00016827 | /Users/acar/Downloads/shard_12.zip | T02 |
| FILE_00016828 | /Users/acar/Downloads/shard_13.zip | T02 |
| FILE_00016829 | /Users/acar/Downloads/shard_14.zip | T02 |
| FILE_00016830 | /Users/acar/Downloads/shard_15.zip | T02 |
| FILE_00016831 | /Users/acar/Downloads/shard_16.zip | T02 |
| FILE_00016832 | /Users/acar/Downloads/shard_17.zip | T02 |
| FILE_00016833 | /Users/acar/Downloads/shard_18.zip | T02 |
| FILE_00016834 | /Users/acar/Downloads/shard_19.zip | T02 |
| FILE_00016955 | /Users/acar/Downloads/workspace-8c754c97-38a1-4692-b25e-e359bf96932f (1)/bun.lock | X04 |

## F. DATA LOSS REPORT

```text
Source files deleted:                 0
Source files modified:                0
Source files missing after scan:      0
Rows silently dropped:                0
Features silently dropped:            0
Columns silently removed:             0
Unaccounted records:                  0
Verify pass (size+mtime, değişen → re-hash): 17581 dosya kontrol edildi
```

**RUN STATUS: PASSED**
