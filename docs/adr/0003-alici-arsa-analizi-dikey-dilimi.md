# ADR-0003: Alıcı Arsa Analizi Dikey Dilimi

- Durum: Kabul edildi
- Tarih: 11 Eylül 2026

## Bağlam

Platform gelecekte emlakçı, alıcı ve ticari işletme kullanıcılarına ayrı ürünler
sunacaktır. İlk çalışan dilim alıcının arsa kararını uçtan uca desteklemeli;
çakışmalı ham veri, ilan emsali, canlı kadastro ve kaynak-bağımlı imar bilgisini
aynı kullanıcı sonucunda birleştirmelidir.

## Karar

- Ortak veri çekirdeği modüler monolit olarak kalır; kullanıcı tipleri ayrı ürün
  modülleridir.
- Gold çatışma kararları fiziksel veri silmez ve açıklanabilir kanıt taşır.
- Arsa emsalleri Silver'dan atomik üretilen küçük SQLite ürün indeksinden okunur.
- Emsal seçimi mahalle/mesafe, ilçe/mesafe, ilçe ve il kapsamlarını kademeli
  genişletir; MAD aykırı değer filtresi ve ağırlıklı medyan kullanır.
- Canlı kadastro sorgusu salt okunurdur. Canlı alan ile kullanıcı alanı yüzde
  beşten fazla ayrışırsa canlı alan kullanılır ve uyarı gösterilir.
- İmar kaynağında bulunmayan alanlar tahmin edilmez. Örnek proje yalnız canlı
  imar kanıtında hem KAKS hem TAKS bulunduğunda hesaplanır.
- Son kullanıcıya kaynak veya ilan URL'si dönülmez; iç kanıt karmaları ürün
  indeksinde korunur.

## Uç noktalar

- `POST /api/v1/arsa/analiz`
- `GET /api/v1/parsel/canli`
- `GET /api/v1/veri-durumu`

## Sınırlar

Sonuç istatistiksel ön değerlemedir; gerçekleşmiş satış fiyatı, ekspertiz,
ruhsat veya kazanılmış imar hakkı değildir. İlk performans hedefi sıcak yerel
isteklerde 500 ms altı API yanıtıdır; bu hedef yük testi yapılmış üretim SLO'su
değildir.
