# İÇERİ ALINAMAYAN VERİLER — KATMAN KATMAN MUHASEBE (2026-09-19)

Kaynak listeler: reports/UNSTAGED_FILES.csv (dosya bazında neden), reports/CANONICAL_COVERAGE_OF_STAGING.json (görünüm bazında).

## A. Ham envanter → staging (19.104 veri adayı dosya)
| Durum | Dosya | MB | Açıklama |
|---|---|---|---|
| Staged (stg) | 3.097 | — | Asıl kopyalar (bölge-13 demografi bugün eklendi) |
| Karantina-staged (qstg) | 25 | — | Q-kurallı sentetik/şüpheli kaynaklar; ayrı şemada, kanoniğe girmez |
| KOPYA (sha aynı, asıl staged) | 7.394 | 3.321 | file_alias ile asıla bağlı; tekrar okunmaz |
| REFERENCE_ONLY bronze/silver | 5.704 | 3.893 | Önceki pipeline'ın ara katmanı; product/ham eşdeğerleri staged (ör. silver price_trend = land_trend) |
| ZIP (üyeleri staged/alias) | 51 | 502 | İç içe zip'ler dahil — 3 büyük paket açıldı, tümü staged kopyaların birebiri |
| Meta/küçük (json/txt <20 KB) | 1.134 | 1 | meta.json, state, durum dosyaları |
| Kod/araç çıktısı | 614 | 14 | workspace, tool-results, .py/.js |
| Canlı toplayıcı ham sayfa | 923 | 11 | yemeksepeti extract/pages, website_menu (parse sonucu records staged) |
| Karantina kuralı Q06/Q07 | 72 | 15 | demo/test dizinleri (İstanbul sabit veri demosu) |
| Uygulama/ikili | 25 | 6.466 | **OSM ham PBF'ler** (turkey-internal.osh.pbf 1,36 GB + 2021…2025 yıllık .osm.pbf) — ham kaynak; türevleri (osm_poi 623 K, osm_degisim kesitleri) staged. Diğerleri: FastSAM .pt, .apk, dmg |
| İncelenecek (gerçek veri?) | 67 → **0** | 410 | Hepsi incelendi: 3 paket zip = kopya · exported_all_db CSV'leri = staged sqlite'ın dışa aktarımı (+1 view) · medas_konut_satis_ilce CSV'leri = TÜİK web matris çıktısı (aynı veri qstg tuik_bolge.konut_satis_ilce 146.496 satır) · 08_arsa_koordinat / 81_il_resmi_satis = yalnız başlık (boş) · 23_arabam (120) = vehicles'ta staged · airbnb bodrum (18 satır, Kolombiya ilanları — çöp) · epub/mp4/png/apk = veri değil |

Planner "object_type=text" atlamaları (140): 8'i gerçek CSV; bunlardan yalnız **bolge_13/01_demografi_ve_nufus.csv** eksikti → staged (1.961 satır). Neden: Phase 1 probu dosyayı 'text' saymış (BOM+; ayraç). Diğer 7'si boş/kopya.

## B. Staging → kanonik (1.108 görünüm, 55,5 M satır)
Kanonikte kullanılan: ~19 M satır (%34). Aileler:
| Aile | Kanonikte | Dışarıda (en büyükler) | Değerlendirme |
|---|---|---|---|
| reference_geography | il/ilçe/mahalle poligon, TKGM, TÜİK nüfus | neighbourhood_point/list (102 K/76 K), sektör paketleri (kopya envanterler) | sektör paketleri = aynı verinin bölgesel kesitleri; neighbourhood_* → v1.5 (ad/koordinat doğrulama) |
| price_series | 5 tablo (29,7 M gözlem) | price_breakdown 369 K, ej_trend 277 K, price_summary(ilçe/il), cografi_katmanlar | **v1.5 adayı**: price_breakdown (oda/yaş kırılımı), ej_trend |
| poi_business | Google gözlem, OSM food, OSM tarihçe, YS | google_places_ve_yogunluk 666 K (GitHub shard'ları — NDR_000002), 08_ilce_onemli_noktalar 562 K, piyasa_verileri__poi 509 K, onemli_tesisler (hal/liman/osb/hastane) | **v1.5 adayı**: GitHub shard Google gözlemleri (poi_snapshot'a ek kesit), önemli tesisler (kamu POI) |
| demographics_context | — | seçim sonuçları 595 K, hemşehri kütük 367 K, oda-yaş-kat 115 K, TÜİK yapı ruhsatı/izin, SES | **v1.5 adayı**: mahalle/ilçe bağlamı (kimliksiz, agregat; n<10 hücre bastırma §3) |
| listings | — | yıllık satışlar 2010–2024 615 K, bölge poi_ilce 502 K, ilan tabloları (ilce_atlasi, emlak_ilanlari, satılık konut/arsa/işyeri) | **v1.5 adayı**: listing_matcher (ilan → mahalle, fiyat gözlemi) |
| mobility_logistics | — | trafik saatlik 235 K, hava günlük 123 K, GPS, toplu taşıma rotaları | v1.6 |
| education | — | resmi okul 55 K, üniversite birim, LGS taban | v1.6 (kamu POI ile birlikte) |
| economy | — | BKM/BDDK/KAP (küçük, il düzeyi) | v1.6; shard_* ekonomi tabloları NDR_000002'de "0 satır / türev" bayraklı |
| vehicles | — | aracrisk defter/emsal | ürün kapsamı dışı (ayrı ürün) |
| cadastre_zoning | — | **tkgm_alim_satim_yogunlugu 13,9 M + shard kopyaları** | v1.5 adayı (TKGM alım-satım yoğunluğu → mahalle işlem hacmi); parsel_imar_* karantinada (Q04 sentetik) |
| unclassified | — | pivot1/2, veri kataloğu, tsconfig | veri değil / türev |

## C. Karantinada inceleme bekleyen (qstg, 118 görünüm)
- tuik_bolge (konut_satis_ilce 146 K, göç, hanehalkı, yapı izin/ruhsat, SES) — QDEC_000001 yalnız nüfus tablolarını kabul etti; diğerleri NEW_DATA_REVIEW bekliyor (TÜİK resmî toplamlarla doğrulanınca stg'ye alınır)
- turkiye_makro_ve_mikro_istihbarat, 19_etbis/20_sege/21_ciro/22_tuik, bati_ticari (Q01–Q03 sentetik — kalır)
- parsel_imar_degisiklikleri (Q04 sentetik imar; kadastro geometrisi ayrı değerlendirilebilir)
- economy shard_1..19 (GitHub; 0 satır/türev bayraklı)
- 15_e_ticaret, eticaret_ve_lojistik (kaynağı doğrulanmadı)

## D. Toplayıcı tarafında yarım kalanlar
- Yemeksepeti: 485/130.670 hedef (engel politikası ile durdu) · website menü pilotu: 300 site, 2 sitede yapısal menü · Migros/Trendyol Go/Getir: robots/API engeli (alınmadı)
