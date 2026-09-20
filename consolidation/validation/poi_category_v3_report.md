# POI kategori v3 — çapraz kaynak çözümü (2026-09-20)
Hedef: 88,976 çözülemeyen Google işletmesi. Çözülen: **7,088** (8.0%) · kalan 81,888
| Yöntem | high | review | no_auto |
|---|---|---|---|
| CHAIN_SPATIAL | 1 | 22 | 0 |
| DISTRICT_NAME | 0 | 1,582 | 0 |
| NAME_MODEL_V3 | 7 | 767 | 0 |
| OSM_SPATIAL | 2,148 | 2,443 | 118 |

Bantlar: {'high': 2156, 'review_recommended': 4814, 'no_auto': 118} · kaynaklar arası çelişki: 118 (üst öncelik seçildi, güven ×0,85, bayraklı)
Ad modeli v3: eğitim 493,533 etiketli ad (Google kural 238,844 + OSM + restoran + zincir) · test doğruluk 0.810 · p≥0,85 doğruluk 0.974 (kapsam 40%)
En sık türler: [('Mağaza (Genel Perakende)', 2095), ('Restoran & Lokanta', 1088), ('Süpermarket & Market', 933), ('3. Nesil Kahveci', 861), ('Pastane & Fırın & Börekçi', 303), ('Pazar Yeri & Çarşı', 210), ('Hamburgerci & Fast Food', 191), ('AVM & Alışveriş Merkezi', 157), ('Eczane', 137), ('Kuaför & Berber', 123), ('Kamu & Dini Tesis', 110), ('Bar & Lounge', 106)]
Golden set: validation/golden/poi_category_golden_review_v3.html (143). Kanoniğe uygulama: v1.5 (yalnız high bant otomatik; review insan onayı). Süre 93s.

## Arama terimi → sektör ipucu (ölçülmüş)
Kural-etiketli işletmelerde (tek arama terimiyle bulunanlar) arama teriminin sektörü tutma oranı: otomotiv servisleri %84,1 (n=50.141) · yapı tesisat %80,1 · kuaför güzellik %74,3 · sağlık medikal %73,7 · marketler %73,4 · manav/kasap %71,0 · restoranlar %68,6 · kafeler %66,9 · **pastaneler %38,1 · giyim %27,9 · elektronikçiler %7,6** (eşik altı; ipucu verilmez). Kategori düzeyinde uyum çok düşük (kafeler→3. Nesil Kahveci %3,6).
Sonuç: 33,330 kalan işletmeye `sector_hint` + `hint_precision` yazıldı (mappings/poi_sector_hint_v3.parquet). Bu bir kategori değildir; sayımlara girmez; üründe "muhtemelen … (güven %…)" olarak gösterilebilir.
Kalan tamamen çözümsüz: ~48,558 işletme — eldeki verilerle güvenilir tür verilemiyor (adı bilgi vermiyor, başka kaynakta yok). Seçenekler: (a) Google Places API 'types' sorgusu (ücretli; işletme başına ~0,017 $ → ~1.400 $), (b) insan incelemesi örneklem, (c) böyle bırakmak (tür='bilinmiyor').
