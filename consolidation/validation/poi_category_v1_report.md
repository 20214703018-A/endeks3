# POI kategori çıkarımı v1 — RAPOR (2026-09-19)

Girdi: 584,357 benzersiz mekân (google_places_gozlem, son gözlem); "Ticari Mekan": 329,092 (%56.3).
Model: 53 sınıf, eğitim 204,212 / test 51,053; **tutulan veride doğruluk 0.889**; p≥0,85 olan tahminlerde kapsama %68.8, doğruluk 0.964.

| Yöntem | Mekân |
|---|---|
| RULE | 161,647 |
| SEARCH_HINT | 92,473 |
| UNRESOLVED | 70,630 |
| MODEL | 4,342 |

| Band | Mekân |
|---|---|
| high | 162,737 |
| review_recommended | 95,725 |
| no_auto_merge | 70,630 |

## En sık türetilen kategoriler
| Kategori | Mekân |
|---|---|
| Oto Servis & Yedek Parça | 34,522 |
| Giyim & Ayakkabı | 15,741 |
| Elektronik & Telefon | 9,911 |
| Süpermarket & Market | 9,469 |
| Mobilya & Ev Dekorasyon | 8,920 |
| Kuaför & Berber | 7,891 |
| Yapı Malzemesi & Nalbur | 6,867 |
| Kebapçı & Ocakbaşı | 6,640 |
| Restoran & Lokanta | 5,796 |
| Pide & Lahmacun | 5,714 |
| Çiğ Köfteci | 5,415 |
| Pastane & Fırın & Börekçi | 4,135 |
| Güzellik & Kozmetik | 3,838 |
| Tost & Büfe | 3,269 |
| 3. Nesil Kahveci | 3,127 |
| Fotoğraf & Matbaa & Kırtasiye | 2,896 |
| Çiçekçi & Hediyelik | 2,886 |
| Tatlıcı & Baklavacı | 2,448 |
| Hamburgerci & Fast Food | 2,081 |
| Klinik & Muayenehane | 2,063 |
| Kokoreççi & Sakatatçı | 1,987 |
| Balık & Deniz Ürünleri | 1,878 |
| Galeri & Oto Kiralama | 1,870 |
| Akaryakıt & Otopark | 1,833 |
| Spor & Eğlence | 1,816 |

## Model test seti (en büyük 15 sınıf)
| Sınıf | precision | recall | destek |
|---|---|---|---|
| Kuaför & Güzellik | 0.99 | 1.00 | 8,530 |
| Kafe | 0.94 | 0.98 | 6,079 |
| Bakkal & Market | 0.90 | 1.00 | 5,544 |
| Giyim Mağazası | 0.98 | 1.00 | 4,572 |
| Restoran & Lokanta | 0.83 | 0.98 | 2,876 |
| Otomotiv Servisi | 0.98 | 1.00 | 2,582 |
| Fast Food | 0.82 | 0.81 | 2,566 |
| 3. Nesil Kahveci | 0.97 | 1.00 | 2,279 |
| Pastane & Fırın | 0.84 | 0.93 | 2,001 |
| Kebapçı & Ocakbaşı | 0.89 | 0.99 | 1,918 |
| Diğer | 0.47 | 0.61 | 1,718 |
| Eczane & Medikal | 0.95 | 0.99 | 1,173 |
| Türk | 0.44 | 0.25 | 997 |
| Kasap | 0.93 | 0.94 | 699 |
| Tatlı | 0.82 | 0.85 | 611 |

Çıktı: mappings/poi_category_predictions_v1.parquet (ham etiket değişmedi; predicted_* ayrı). Taksonomi: mappings/poi_taxonomy_v1.json.
Golden: validation/golden/poi_category_golden_v1.csv (200 rastgele; `correct(human)` = evet/hayır, yanlışsa doğru kategori).
Sınır: model etiketlerinin bir kısmı toplayıcı kural setinden (kural izi); Google kaynaklı etiketler bağımsız. Süre 120s.
