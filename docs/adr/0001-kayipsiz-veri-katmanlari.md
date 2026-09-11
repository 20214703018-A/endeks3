# ADR 0001: Kayıpsız Veri Katmanları ve Kanonik Çözümleme

- Durum: Kabul edildi
- Tarih: 10 Eylül 2026

## Bağlam

Kaynak havuzu fiziksel dosyalar, açılmış kopyalar, iç içe arşivler, CSV,
JSON/GeoJSON ve SQLite tabloları içeriyor. Aynı içeriğin farklı yerlerdeki
kopyaları yanında aynı iş varlığını farklı değerlerle anlatan kayıtlar da var.
Emlakçı, alıcı ve işletme ürünlerinin tek bir kaynağı doğruymuş gibi davranması
veri kaybı ve yanlış güven üretir.

## Karar

Modüler monolit içinde dört veri katmanı kullanılacak:

```text
Kaynak oluşumları -> Katalog/RAW -> Bronze -> Silver -> Gold görünümleri
                         |             |          |
                         +-> invalid   +-> demo   +-> canonical_resolution
                         +-> hash      +-> karantina  +-> data_conflict
```

- RAW/katalog her fiziksel ve arşiv içi oluşumu SHA-256 ile korur.
- Bronze benzersiz içeriği bir kez yazar; bütün oluşum yollarını manifestte tutar.
- Silver her kaynak satırı için deterministik gözlem kimliği, doğal anahtar,
  normalize kayıt, dönem, kalite, politika ve tam lineage üretir.
- `projeksiyon`, `projection`, `projeksiyon_donemi` ve `tahmin_ufku`
  göstergeleri Silver'da satır bazında gözlem/projeksiyon ayrımı yapar.
- Fiyat trendi, fiyat özeti ve yıllık satışlarda dönem kaynak/toplama zamanından
  ilerideyse eksik veya yanlış `projeksiyon=0` bayrağı geçersiz kılınır. Kararın
  gerekçesi, projeksiyon kaynağı ve kullanılan referans zamanı Silver'da saklanır.
- Aynı doğal anahtar ve farklı payload silinmez; `data_conflict` adayıdır.
- Gold fiziksel birleştirme değildir. Seçim kuralı ve sürümü bulunan kanonik
  görünüm, bütün Silver gözlemlerine geri bağlanır.
- `observed`, `projection`, `demo`, `quarantine`, `invalid` ve kısıtlı bağlam
  fiziksel veya politika düzeyinde ayrı tutulur.
- Yerel geliştirmede Parquet kullanılır. Çok kullanıcılı üründe aynı sözleşme
  PostgreSQL/PostGIS'e taşınabilir; şimdiden mikroservise ayrılmaz.

## Sonuçlar ve ödünler

- Kaynak kanıtı ve çatışmalar sorgulanabilir kalır.
- Depolama, yalnız kanonik satırı saklamaya göre daha büyüktür.
- Gold seçim kuralları ayrıca sürümlenmeli ve kaynak güven/lisans incelemesi
  tamamlanmadan ürün iddiası üretmemelidir.
- JSON/GeoJSON belgeleri Silver v1'de içerik referansı olarak yer alır;
  geometriler ayrı GeoParquet adaptöründe normalize edilecektir.
