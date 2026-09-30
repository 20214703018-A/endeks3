# Toplu taşıma tarife ambarı — kaynaklar ve tahmin yöntemleri

Sürüm: canonical v1.8 (2026-09-30). Kayıt: `quarantine/new_data_decisions.jsonl` → NDR_000004.

## Ne sorulabilir?
"Şu noktadan / duraktan / mahalleden hangi hatlar geçiyor, hangi gün ve saatte?"
Araç: `tools/toplu_tasima_sorgu.py` · Tablo: `v_transit_departure` (durak × hat × kalkış saati).

## Saatler nereden geliyor? (`time_source` sütunu)

| Değer | Anlamı | Resmî mi? |
|---|---|---|
| `gtfs_given` | Yayımcının tarife dosyasında o durak için yazan saat | Evet |
| `ego_given` | EGO sayfasında yazan ilk durak kalkış saati | Evet |
| `kentkart_offset` | İlk kalkış saati + işletmecinin verdiği "ilk duraktan kaç saniye sonra" farkı | Evet (işletmeci verisi) |
| `gtfs_frequency` | Yayımcı "şu saatler arası her N dakikada bir" demiş; tek tek seferlere açıldı | Evet (kural yayımcının) |
| `gtfs_interpolated` | **TAHMİN** — yöntem 1 | Hayır |
| `ego_duration_interpolated` | **TAHMİN** — yöntem 2 | Hayır |

`v_transit_departure.saat_tahmini = true` tahmini saatleri ayırır; `tahmin_yontemi` hangi yöntem olduğunu yazar.
Sorgu çıktısında tahmini saatlerin yanında `~` işareti vardır.

## Yöntem 1 — `sira_dogrusal_v1` (İstanbul İETT)
**Neden gerekti:** İETT her sefer için yalnızca ilk ve son durağın saatini yayımlıyor (sefer başına ortanca 2 resmî saat, 44 durak).
**Nasıl:** Aradaki durağın saati, ilk ve son saat arasına **durak sırasına göre eşit aralıkla** yerleştirilir.
Örnek: 06:40'ta kalkan, 29. durağa 07:01'de varan seferde 15. durak ≈ 06:40 + 21 dk × (14/28) = 06:50.
**Varsayım:** Duraklar arası süreler birbirine yakındır (trafik, uzun duraklar arası mesafe hesaba katılmaz).

## Yöntem 2 — `sure_dogrusal_v1` (Ankara EGO)
**Neden gerekti:** EGO sayfası yalnızca ilk duraktan kalkış saatlerini ve hattın toplam süresini ("Süresi: 40 dakika") veriyor.
**Nasıl:** Aradaki durağın saati = ilk kalkış + toplam süre × (durak sırası / durak sayısı). Sefer, notunda belirtilen
ara duraktan başlıyorsa ("57268 NOLU DURAK ... KALKAN"), saatler o duraktan itibaren hesaplanır.

## Tahminin doğruluğu (ölçüldü)
Tüm duraklara resmî saat veren üç tarifede (İzmir ESHOT, Konya, Gaziantep) ara saatleri gizleyip aynı yöntemle
tahmin ettik ve gerçek saatle karşılaştırdık (4,5 milyon ara durak):

| Tarife | Ortanca sapma | Sapmaların %90'ı en fazla | 5 dk içinde kalan |
|---|---|---|---|
| İzmir ESHOT | 2,4 dk | 8,2 dk | %75,8 |
| Konya | 2,0 dk | 5,0 dk | %89,9 |
| Gaziantep | 1,7 dk | 9,0 dk | %80,3 |

Yani İETT ve EGO'daki tahmini saatler için beklenen hata tipik olarak ±2 dakika, uzun hatlarda 5–9 dakikaya çıkabilir.

## Konum tahmini — `tahmini_hat_sirasi_arasi` (Ankara EGO durakları)
EGO durak koordinatı vermiyor; koordinatlar OpenStreetMap'teki EGO durak numarasından (ref) alındı (6.173 durak).
Bulunamayan duraklardan, aynı hatta koordinatı bilinen önceki ve sonraki durak arasında en çok 5 bilinmeyen durak
olanlara sıraya göre ara konum verildi (705 durak, `transit_stop.coord_repair`). Daha uzun boşlukta tahmin yapılmadı:
3.922 durak koordinatsız kaldı. Bu duraklar ad veya hat ile sorgulanabilir, konumla sorgulanamaz.

## Kaynakta bozuk gelip onarılanlar (tahmin değil, düzeltme)
- İETT durak koordinatları Excel binlik ayırıcıyla bozulmuş: `410.191.700.005.564` → `41.0191700005564` (15.386 durak; hepsi İstanbul sınırında). 4 durak onarılamadı.
- İETT durak–saat CSV dosyası Excel satır sınırında kesik (1.048.576) → tam sürüm zip dosyasından alındı.
- İETT hat/durak adlarında çift kodlanmış Türkçe karakterler (`KADIKÃ–Y` → `KADIKÖY`), `name_repair` ile işaretli.
- İBB raylı/deniz dosyası cp1254 kodlu; minibüs (9) ve taksi-dolmuş (10) standart dışı kodları adlandırıldı.
- ESHOT seferlerinde yön adı yok → seferin son durağının adı (`headsign_source = son_durak_adi`).

## Doluluk
Hiçbir kurum araç doluluk oranı yayımlamıyor. Vekil: İBB saatlik hat bazlı kart geçişi/yolcu (`transit_ridership_hourly`,
şu an 2024-08 ve 2024-10-01..18). `v_istanbul_hat_saatlik_yolcu.sefer_basina_ortalama_yolcu_turetilmis` = o saatteki yolcu ÷
o saatteki planlı sefer (İETT 2026 hafta içi tarifesi). Yolcu 2024, tarife 2026 olduğu için dönem farkı vardır; türetilmiş bir orandır.

## Kapsam (2026-09-30)
- Saatli: İstanbul, Ankara, İzmir, Konya, Gaziantep + KentKart: Adana, Kocaeli, Muğla, Antalya, Mardin, Ordu, Düzce, Erzurum, Osmaniye,
  Kastamonu, Niğde, Burdur, Çanakkale, Karabük, Kırklareli, Zonguldak (Ereğli), Tokat, Denizli (Çivril); şehirlerarası FlixBus.
- Hat + durak, saat yok (kaynak yayımlamıyor): Sivas, Edirne, Alanya, Akçakoca.
- Yalnız OSM hat–durak (saatsiz): Kayseri, Bursa, Balıkesir, Manisa, Tekirdağ, Samsun, Eskişehir, Trabzon ve diğerleri.
- Süresi dolmuş tarife (kaynak güncellenmemiş): İZBAN (2025-01-01), İBB raylı/deniz (2024-12-31).
