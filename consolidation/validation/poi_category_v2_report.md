# POI kategori çıkarımı v2 — RAPOR (2026-09-19)
"Ticari Mekan": 329,092 / 584,357. Taksonomi: 72 kategori (v1.3 + 31 veri-güdümlü ek; ticaret dışı: park, müze/tarihi yer, kamu, çay ocağı, AVM, fabrika, toptan…).
İsim modeli: doğruluk 0.891; p≥0,85'te kapsama %70, doğruluk 0.963. **Yorum modeli** (eğitim 130,063 yorumlu etiketli mekân): doğruluk 0.565; p≥0,85'te kapsama %5, doğruluk 0.997808619430241.

| Yöntem | Mekân |
|---|---|
| RULE | 238,844 |
| UNRESOLVED | 47,498 |
| SEARCH_HINT | 36,742 |
| REVIEW_MODEL | 3,462 |
| NAME_MODEL | 2,546 |

| Band | Mekân |
|---|---|
| high | 240,116 |
| no_auto_merge | 47,498 |
| review_recommended | 41,478 |

## En sık türetilen kategoriler
| Kategori | Mekân |
|---|---|
| Oto Servis & Yedek Parça | 49,029 |
| Yöresel & Gıda Ürünleri | 14,122 |
| Restoran & Lokanta | 13,953 |
| Mağaza (Genel Perakende) | 13,258 |
| 3. Nesil Kahveci | 13,178 |
| Kuaför & Berber | 12,750 |
| Giyim & Ayakkabı | 10,648 |
| Mobilya & Ev Dekorasyon | 9,305 |
| Süpermarket & Market | 9,143 |
| Ev Aletleri & Beyaz Eşya | 8,973 |
| Toptan & Ticaret | 8,107 |
| Yapı & İnşaat Firması | 7,252 |
| AVM & Alışveriş Merkezi | 5,952 |
| Kebapçı & Ocakbaşı | 5,237 |
| Elektronik & Telefon | 5,138 |
| Çiğ Köfteci | 5,130 |
| Elektrikçi & Elektrik Malzeme | 5,103 |
| Bar & Lounge | 4,515 |
| Tuhafiye & Çeyiz & Bebek | 4,136 |
| Pide & Lahmacun | 4,056 |
| Çay Ocağı & Kahvehane | 3,931 |
| Park & Bahçe & Rekreasyon | 3,651 |
| Yapı Malzemesi & Nalbur | 3,226 |
| Pazar Yeri & Çarşı | 3,133 |
| Kasap | 2,851 |
| Güzellik & Kozmetik | 2,784 |
| Tekel Bayii | 2,749 |
| Tost & Büfe | 2,740 |
| Fotoğraf & Matbaa & Kırtasiye | 2,420 |
| Fabrika & Üretim | 2,349 |

v1.3 ile karşılaştırma: çözülemeyen 70.630 → 47,498; yalnız sektör ipucu 92.473 → 36,742.
Çıktı: mappings/poi_category_predictions_v2.parquet · golden: validation/golden/poi_category_golden_review_v2.html (200). Süre 110s.
