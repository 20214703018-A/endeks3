# Veri Sınıflandırma ve Kayıpsız Birleştirme Durumu

Tarih: 11 Eylül 2026

## Sonuç

İki kaynak kökü salt okunur tarandı:

- `/Users/acar/Downloads/extension/collector/data`
- `/Users/acar/Downloads/Öğelerle Yeni Klasör 2`

Fiziksel dosyalar, açılmış kopyalar, ZIP üyeleri ve iç içe ZIP üyeleri ayrı
oluşumlar olarak kaydedildi. Hiçbir kaynak taşınmadı, silinmedi, yeniden
adlandırılmadı veya yerinde değiştirilmedi.

### Tam katalog

| Ölçü | Sonuç |
| --- | ---: |
| Fiziksel veri dosyası oluşumu | 1.407 |
| Arşiv içi veri oluşumu | 2.448 |
| SQLite tablo varlığı | 78 |
| Tam içerik tekrarı grubu | 674 |
| CSV satır oluşumu, tekrarlar dahil | 16.282.936 |
| Bozuk kolon sayılı CSV satırı | 0 |
| Sınıflandırılmamış varlık | 0 |
| Invalid fiziksel oluşum | 2 boş SQLite dosyası |

İki boş SQLite dosyası aynı boş içerik hash’ine sahiptir; tek benzersiz invalid
içerik ve iki ayrı oluşum olarak korunur:

- `collector/data/emlakjet_veri.db`
- `collector/data/parsel_imar_degisiklikleri.sqlite`

### İçerik-adresli Bronze katmanı

Tam kopyalar satır verisini tekrar çoğaltmadan tek benzersiz içerik olarak
yazıldı; bütün dosya ve arşiv yolları katalog üzerinden aynı içeriğe bağlıdır.

| Ölçü | Sonuç |
| --- | ---: |
| Benzersiz veri varlığı | 2.529 |
| CSV içeriği | 412 |
| JSON/GeoJSON belgesi | 2.054 |
| SQLite/DB içeriği | 62 |
| Kaynak mantıksal satır/belge | 9.140.813 |
| Yazılan ve doğrulanan satır/belge | 9.140.813 |
| Parquet dosyası | 2.889 |
| Teknik yazım hatası | 0 |
| Satır dengesi | Tam |
| Sıkıştırılmış Bronze boyutu | Yaklaşık 703 MB |

Bağımsız doğrulamada 2.889 Parquet dosyasının tamamı açıldı; manifest satır
sayılarıyla eşleşti, `rules_v2` sınıflandırma metadata’sını taşıdı ve geçici
dosya kalmadı.

Mantıksal kayıtların sınıf dağılımı (SQLite içerikleri tablo sınıfıyla sayılır):

| Sınıf | Kayıt/belge |
| --- | ---: |
| `observed` | 4.229.050 |
| `mixed_observed_projection` | 4.849.602 |
| `demo` | 62.114 |
| `quarantine` | 47 |

`data_container` yalnız dosya/veritabanı taşıyıcı seviyesinde kullanılır;
geçerli mantıksal kayıtlardan hiçbiri bu genel sınıfta bırakılmamıştır.

## Veri sınıfları ve kullanım politikası

| Sınıf | Örnek | Ürün davranışı |
| --- | --- | --- |
| `observed` | kaynakta görülen fiyat, POI, idari kayıt | kalite ve lisans doğrulamasından sonra kullanılabilir |
| `mixed_observed_projection` | aynı tabloda gerçekleşen ve projeksiyon satırları | Silver’da satır bazında fiziksel olarak ayrılmalı |
| `demo` | arayüz veya model örneği | gerçek kullanıcı kararına katılmaz |
| `quarantine` | bilinen sentetik parsel/bağımsız bölüm kayıtları | kanonik sonuçlardan tamamen dışlanır |
| `invalid` | boş veya okunamayan kaynak | oluşumu korunur, veri sonucu üretmez |

Alan sınıfları:

- `property_market`
- `listing_observation`
- `cadastre_planning`
- `geospatial`
- `reference_geography`
- `points_of_interest`
- `demographics`
- `commercial_intelligence`
- `industry_directory`
- `logistics`
- `restricted_context`
- `auxiliary_non_property`
- `operational_metadata`
- `data_container`

`restricted_context` içindeki seçim, hemşehri/kütük ve sağlık davranışı
istatistikleri saklanır fakat konut uygunluğu, fiyatlama, müşteri hedefleme veya
ayrımcı karar skoru için kullanılamaz. Danışman/ofis telefonu veya adresi içeren
varlıklar ayrıca kişisel veri incelemesi gerektirir.

## Silver katmanı

Bronze satırları ortak, sürümlü gözlem sözleşmesine taşındı. Özgün kayıt Bronze'da
kalırken Silver'da normalize kolon adları, deterministik gözlem kimliği, doğal
anahtar, dönem, satır sınıfı, kalite, politika ve kaynak bağlantısı tutulur.

| Ölçü | Sonuç |
| --- | ---: |
| Silver kaynak birimi | 2.890 |
| Silver Parquet dosyası | 2.889 |
| Kaynak mantıksal satır/belge | 9.140.813 |
| Yazılan ve doğrulanan satır/belge | 9.140.813 |
| Doğal anahtarlı kayıt | 9.138.746 |
| Lineage anahtarlı/uzman adaptör bekleyen | 2.067 |
| Teknik hata | 0 |
| Sıkıştırılmış Silver boyutu | Yaklaşık 1,5 GB |

Silver satır sınıfları:

| Sınıf | Kayıt |
| --- | ---: |
| `observed` | 8.399.523 |
| `projection` | 679.129 |
| `demo` | 62.114 |
| `quarantine` | 47 |

2.067 inceleme kaydının 2.054'ü JSON/GeoJSON belgesidir ve kayıp değildir;
özgün payload Bronze'da durur. Bunlar uzman GeoParquet/doküman adaptörüne kadar
Silver'da içerik referansı olarak tutulur. Kalan 13 satır doğal anahtar incelemesi
gerektirir.

## Gold sorgu katmanı

`warehouse/gold/gold.duckdb` Silver dosyalarını kopyalamadan sorgulayan görünümler
içerir. `canonical_resolution` fiziksel silme yapmaz ve seçtiği her satırı kaynak
oluşumlarına geri bağlar.

| Görünüm/ölçü | Sonuç |
| --- | ---: |
| Tüm Silver gözlemleri | 9.140.813 |
| Ürün için ilk elemeden geçen gözlem | 6.947.626 |
| Açıklanabilir kanonik satır | 3.209.792 |
| Farklı payload içeren doğal anahtar | 2.566.651 |
| Karar tablosuna yazılan çözülmüş çatışma | 2.566.651 |
| Birebir payload tekrarı grubu | 178.726 |
| Projeksiyon görünümü | 679.129 |
| Piyasa projeksiyonu | 674.981 |
| Geleceğe tarihli gözlem anomalisi | 0 |
| Karantina görünümü | 47 |
| Doğrulama incelemesi | 2.067 |
| Sicile bağlanan kaynak birimi | 2.890 / 2.890 |
| Sicil eşleme kaybı | 0 |
| Açık yayına açık kanonik satır | 3.203.251 |
| Değerleme/skorlamaya açık kanonik satır | 3.203.251 |

Kullanıcının özgün istatistiksel modelleri, model çıktıları ve veri tabanı
düzeni `PROPRIETARY-DATA-1.0` sahiplik beyanına bağlandı. Kullanıcı ayrıca veri
ailelerinin yayın hakkına sahip olduğunu beyan etti. Kaynak sicili bu beyanı
`owner_rights_declared` / `user_publication_rights_declared` olarak kaydeder;
ürün yanıtları açık kaynak veya ilan bağlantısı göstermez. Yasal ad/unvan alanı
lisans bildiriminde hâlâ doldurulmalıdır.

Çakışmalar `canonical_v4_explainable_conflict_resolution` kuralıyla; kayıt
sınıfı, yönetişim, kalite, doluluk, toplama zamanı, dönem ve sabit hash sırasına
göre deterministik çözülmüştür. Seçilen ve elenen adayların tamamı kanıt
görünümlerinde kalır. Bu teknik çözüm yine de kesin fiyat veya resmî imar
doğrulaması değildir.

## Üretilen dosyalar

- `reports/veri-katalogu-tam.json`: bütün oluşumlar, hash’ler, şemalar,
  sınıflar, tekrar grupları ve teknik durumlar.
- `reports/veri-katalogu-tam.csv`: Excel uyumlu insan-okunur katalog özeti.
- `warehouse/bronze/bronze-manifest.json`: benzersiz içeriklerin Bronze çıktı
  dengesi ve bütün oluşum bağlantıları.
- `warehouse/bronze/{csv,json,sqlite}/`: içerik SHA-256 ile adreslenen,
  Zstandard sıkıştırılmış Parquet çıktıları.
- `reports/kaynak-sicili.json`: her Silver biriminin kaynak, hak, lisans ve ürün
  kullanım kapısı.
- `reports/kaynak-sicili-ozet.csv`: kaynak ailesi bazında insan-okunur özet.
- `LICENSE-DATA.md`: kullanıcı modeli ve özgün veri tabanı düzeni için kapalı
  lisans taslağı; yasal ad/unvan girilmeden nihai değildir.
- `warehouse/product/arsa_emsalleri.sqlite`: 16.027 benzersiz arsa ilanından,
  açık bağlantı içermeyen hızlı emsal ürün indeksi.

Araçlar:

- `tools/veri_katalogu.py`
- `tools/kayipsiz_bronze.py`
- `tools/silver_olustur.py`
- `tools/gold_gorunumleri.py`
- `tools/kaynak_sicili.py`
- `tools/arsa_emsal_indeksi.py`
- `tests/test_veri_katalogu.py`
- `tests/test_kayipsiz_bronze.py`
- `tests/test_silver_olustur.py`
- `tests/test_gold_gorunumleri.py`
- `tests/test_kaynak_sicili.py`
- `tests/test_arsa_emsal_indeksi.py`
- `tests/test_land_analysis.py`
- `tests/test_live_parcel_gateway.py`

Her iki üretim aracı da açık `--calistir` bayrağı ister. Çıktılar önce geçici
dosyaya yazılır; satır/metadata doğrulamasından sonra atomik olarak yayımlanır.
Kaynak kataloglandıktan sonra değişmişse SHA-256 uyuşmazlığı nedeniyle reddedilir.

## Kayıpsızlık sözleşmesi

Bronze için:

```text
benzersiz_geçerli_kaynak_satırı = yazılan_satır + yeniden_kullanılan_satır
```

Tam ingest için:

```text
kaynak_oluşumu = başarılı + karantina + invalid + açıklanmış_teknik_hata
```

Tam kopya tespiti veri silme değildir. İçerik bir kez saklanır; tüm fiziksel ve
arşiv içi oluşum yolları, konteyner hash’i ve oluşum kimliği korunur. Aynı doğal
anahtara sahip farklı değerler ise tekrar sayılmaz; Silver’da `data_conflict`
olarak birlikte tutulur.

## Sıradaki işler

1. Hak sahibinin yasal adı/unvanını lisans bildirimine yazmak.
2. İl/ilçe/mahalle referans eşlemesini kanonik coğrafya kimliklerine bağlamak.
3. JSON/GeoJSON belgelerini geometrileri koruyan GeoParquet'e dönüştürmek.
4. Alıcı arsa diliminden sonra aynı veri sözleşmesiyle konut ve işyeri ürün
   görünümlerini oluşturmak.
5. Belediye bazında canlı imar adaptör kapsamını genişletmek; kaynakta olmayan
   KAKS/TAKS/plan notlarını boş bırakmayı sürdürmek.
6. Arsa analiz motorunu etiketli ekspertiz/satış verisi geldikçe zaman ayrımlı
   geri test ve kalibrasyon raporuyla sürümlemek.

## Disk güvenliği

11 Eylül son doğrulamasında APFS veri bölümünde yaklaşık 7,9 GiB boş alan vardır.
Yarım kalan sorgunun 1,1 GB geçici DuckDB çalışma klasörü kaldırılmıştır. Yeni
materializasyon veya ağır çakışma analizi öncesinde en az 10 GiB
güvenli boş alan önerilir. Alan açılırken kaynak paketler, Git çalışmaları,
Bronze, veritabanları veya kullanıcı belgeleri silinmemelidir; önce yalnız
yeniden üretilebilir önbellek ve geçici çıktılar ölçülmelidir.
