# Veri Ürün Kullanım Envanteri

Tarih: 11 Eylül 2026

Bu belge, verinin sistemde bulunması ile son kullanıcıya güvenle sunulmasını
birbirinden ayırır. Sayılar Silver gözlemlerini; “kanonik” sayılar ise Gold'un
sürümlü ve açıklanabilir kuralla seçtiği ürün adayını gösterir. Kanonik seçim
ham gözlemi silmez ve tek başına resmî değer doğrulaması değildir.

## Hazırlık durumları

- **Koşullu hazır:** Teknik olarak sorgulanabilir. Kaynak lisansı, güncellik ve
  kullanım izni doğrulanıp kaynak/dönem etiketiyle sunulabilir.
- **Önce düzelt:** Veri var fakat tarih, kimlik, kapsam veya alan eksikliği ürün
  sonucunu yanıltabilir.
- **Yalnız iç kullanım:** Kişisel veri, ayrımcılık veya kullanım amacı riski
  nedeniyle son kullanıcı skoruna ve açık yayına verilmez.
- **Sunulmaz:** Demo, karantina, invalid veya alan dışı veri.

Kullanıcı, sicildeki veri ailelerinin yayın hakkına sahip olduğunu 10 Eylül
2026'da beyan etmiştir. Bu beyan `source-registry-v1.1.0` içinde kaydedilmiş;
2.592 kaynak birimi yayın ve skorlamaya açılmıştır. Ürün yanıtlarında kaynak ve
ilan bağlantısı gösterilmez, fakat içerik karması, kaynak tablosu ve gözlem soy
ağacı iç denetim için korunur. Kişisel/hassas ve kısıtlı bağlam verileri bu
beyandan bağımsız olarak ürün skorlamasına kapalıdır.

## 1. Gayrimenkul piyasa verileri

### Fiyat özeti

- 147.110 Silver gözlemi; 61.849 geçmiş dönem geçici kanonik satır.
- Arsa ve konut; ülke, il, ilçe ve mahalle seviyeleri.
- Dönem aralığı: 2026-08–2027-08.
- Alanlar: satılık/kiralık metrekare fiyatı, min–maks fiyat, ortalama fiyat,
  ortalama metrekare, ilan sayısı, aylık/yıllık değişim, amortisman, brüt kira
  getirisi, bina yaşı, ilanda kalma süresi, endeks ve kaynak/toplanma zamanı.
- 70.260 kayıtta toplanma zamanı var.
- 5.201 adet `2027-08` kaydı projeksiyon sınıfına taşındı; gerçekleşmiş fiyat
  kanoniğine girmez.
- Durum: **Koşullu hazır.** Geçmiş değerler kaynak, tarih ve “bölgesel piyasa
  göstergesi” etiketiyle; ileri değerler yalnız “tahmin” etiketiyle sunulmalı.

### Aylık fiyat trendi

- 4.845.454 Silver gözlemi; 1.535.223 geçmiş dönem geçici kanonik satır.
- Dönem aralığı: 2021-01–2027-08.
- 41.604 kaynakça açıkça işaretlenmiş ve 628.122 tarih kontrolüyle çıkarılmış
  olmak üzere 669.726 satır projeksiyon sınıfındadır.
- Alanlar: satılık/kiralık m² fiyatı, ortalama fiyat, ilan sayısı, amortisman,
  brüt getiri, değişim ve endeks.
- Durum: **Koşullu hazır.** Geçmiş ve projeksiyon serileri ayrı çizilmeli;
  projeksiyonlar yatırım sonucuna gerçekleşmiş fiyat olarak sokulmamalı.

### Fiyat dağılımları

- 379.309 Silver gözlemi; 141.200 geçici kanonik satır.
- Kırılımlar: alan, ısıtma, kat, oda ve bina yaşı.
- Alanlar: segment, oran, satılık/kiralık m² fiyatı, ortalama fiyat, ilan sayısı,
  amortisman ve getiri.
- Durum: **Koşullu hazır.** Kaynak/lisans doğrulaması ve çakışma çözümü sonrası
  bölgesel karşılaştırmada kullanılabilir.

### Yıllık satışlar

- 926.206 Silver gözlemi; 310.051 geçmiş dönem geçici kanonik satır.
- Dönem aralığı: 2010–2027; sabit büyüme katsayılarıyla oluşturulan 54 adet
  2025–2027 kaydı (27 doğal anahtar) projeksiyon sınıfında.
- Alanlar: toplam konut satışı, ipotekli konut satışı, arsa/arazi satışı,
  ipotekli arsa satışı ve toplam ilan sayısı.
- Durum: **Önce düzelt.** Resmî istatistik kaynağı ve dönem kapsamı
  doğrulanmadan “gerçekleşmiş satış” şeklinde sunulmamalı.

## 2. İlan gözlemleri

### Satılık konut

- 12.293 kayıt, 80 il.
- Kayıtların tamamında fiyat, metrekare, URL ve koordinat bulunuyor.
- Alanlar: ilan ID, kategori/tip, il–ilçe–mahalle, mahalle ID, enlem/boylam,
  fiyat, m², birim fiyat, oda sayısı, bulunduğu kat, etiket ve ilan tarihi.
- Durum: **Koşullu hazır.** Yalnız kullanıcıya ait veya yeniden kullanım izni
  bulunan ilanlar açık yayımlanabilir. Diğerleri karşılaştırma/analiz girdisi
  olarak ve kaynak platform şartlarına uygun kullanılmalı.

### Satılık işyeri

- 6.404 kayıt, 79 il.
- Tamamında fiyat, URL ve koordinat; 6.376 kayıtta metrekare var.
- Konut ilanıyla aynı temel alanları taşır.
- Durum: **Koşullu hazır.** Güncellik, ilan sahipliği ve yeniden yayınlama hakkı
  doğrulanmalı.

### Arsa/parsel ilişkili ilan ve kayıtlar

- 50.873 gözlemlenmiş kayıt; bunların 16.087'si açıkça satılık arsa CSV ilanı,
  34.694'ü birleşik ilan veritabanı, 90'ı yerel ilan veritabanı ve 2'si ham
  JSON/GeoJSON belgesidir.
- 14.778 kayıtta imar durumu, 2.174 kayıtta KAKS/emsal, 10.392 kayıtta ada ve
  parsel birlikte bulunuyor.
- Gözlemlenmiş kayıtların hiçbirinde TAKS veya parsel poligonu yok.
- Alanlar arasında fiyat, m², birim fiyat, koordinat, tapu/imar beyanı,
  KAKS/emsal, gabari, kat adedi, yapı nizamı, plan fonksiyonu ve plan süreci var;
  ancak doluluk çok değişken.
- Bu havuzdan yayın hakkı açık, geçerli ve gözlemlenmiş kayıtlar seçilerek
  16.027 benzersiz ilanlık `warehouse/product/arsa_emsalleri.sqlite` ürün indeksi
  üretildi. Aynı ilan kimliğinin eski gözlemleri Silver'da korunur; ürün
  indeksinde yalnız deterministik seçim bulunur ve URL alanı daima boştur.
- Durum: **Emsal analizi için hazır.** İlan metnindeki imar beyanı “resmî imar
  durumu” olarak gösterilmez. TKGM/belediye/E-Plan sonucu ve sorgu zamanı ayrı
  kanıt katmanıdır.

## 3. Parsel, imar ve planlama

- 50.920 toplam Silver kaydı: 50.873 gözlem ve 47 karantina.
- 47 sentetik kayıt sunulmaz: 20 parsel/imar ve 27 bağımsız bölüm kaydı.
- 20 karantina parselinde görünen eksiksiz KAKS, TAKS ve poligon değerleri gerçek
  veri değildir.
- İmar askı/değişiklik tablosunda şu anda 0 kayıt var.
- Bir adet parsel GeoJSON ve bir adet imar/parsel JSON belgesi ham biçimde
  korunuyor.
- TKGM MEGSİS parsel geometrisi statik toplu veri olarak değil, kullanıcı
  sorgusunda canlı sayfadan/API yanıtından alınacaktır. Mevcut akış koordinat
  veya mahalle–ada–parsel ile sorgu yapabiliyor ve depolamasız çalışabiliyor.
- Durum: **Canlı kadastro akışı mevcut; imar alanları kaynak-bağımlı.** TKGM
  yanıtı kaynak ve sorgu zamanıyla gösterilebilir. Belediye/E-Plan yanıtında
  bulunmayan KAKS, TAKS veya plan alanı boş kalmalı; resmî belge olmadan örnek
  proje hesabı kesin yapı hakkı gibi sunulmamalı.

## 4. Coğrafya ve harita verileri

### İl–ilçe–mahalle referansları

- 4.860 il satırı; 81 benzersiz il adı fakat 1.215 farklı `city_id`.
- 2.844 ilçe satırı; 877 benzersiz ilçe adı ve 1.874 farklı `county_id`.
- 59.029 mahalle satırı; 11.258 benzersiz mahalle adı ve 36.582 farklı
  `district_id`.
- Durum: **Önce düzelt.** Paketler arasında kimlikler kaydığı için ad ve resmî
  kod eşlemesi yapılmadan join anahtarı olarak kullanılmamalı.

### İdari sınır poligonları

- 2.050 benzersiz JSON/GeoJSON belge; 2.632 dosya/arşiv oluşumu.
- Dosya adlarına göre 157 il sınırı sürümü, 1.890 ilçe sınırı sürümü ve 3 diğer
  coğrafi belge bulunuyor. İl–ilçe–mahalle referansları ve harita poligonlarının
  mevcut olduğu kullanıcı tarafından teyit edilmiştir.
- Sınırlar ham payload olarak kayıpsız saklanıyor.
- Durum: **Veri mevcut, ürün adaptörü bekliyor.** GeoParquet dönüşümü, geometri
  geçerlilik kontrolü, kanonik konum kimliği ve kaynak atfı tamamlanınca haritada
  doğrudan sunulabilir.

### Coğrafi varlık/gazetteer

- 39.514 gözlem; 79 il kodu.
- Ad, tür/kategori, enlem, boylam ve rakım alanları var.
- Rakımda `-9999` sentinel değeri bulunduğu için eksik değer temizliği gerekiyor.
- Durum: **Koşullu hazır.** Konum etiketi ve genel coğrafi bağlam için
  kullanılabilir; resmî sınır veya parsel geometrisi değildir.

## 5. Önemli noktalar (POI)

- 1.100.392 gözlem; 1.003.566 geçici kanonik satır.
- Başlıca türler: otobüs/minibüs/taksi durakları, zincir marketler, camiler,
  kooperatif/site, kafe, fırın/pastane, ilkokul, ortaokul, lise, eczane,
  restoran, hastane, veteriner ve spor tesisleri.
- Alanlar: il/ilçe kimliği, bölge adı, POI ID, kategori, alt kategori, ad ve slug.
- Statik POI tablolarında enlem/boylam yok; bunlar bölgesel katalog ve adet
  katmanıdır.
- Koordinatlı POI ayrı canlı zenginleştirme katmanından gelir. Mevcut demoda
  OSM/Overpass çağrısı, Haversine mesafesi ve kısa süreli önbellek vardır;
  üretimde birden fazla harita sağlayıcısı kaynak önceliği ve kullanım koşuluyla
  birleştirilecektir.
- Durum: **Statik liste koşullu hazır; canlı mesafe akışı mevcut.** “Eve 500
  metre” sonucu yalnız koordinatlı sağlayıcı yanıtı, sorgu zamanı ve kaynak
  adıyla gösterilmelidir.

## 6. Demografi

- 63.024 kayıt; yalnız 910'u gözlem, 62.114'ü demo.
- 455 yaş/cinsiyet piramidi ve 455 medeni durum/konut yapısı gözlemi.
- Alanlar: nüfus, cinsiyet, yaş grupları, hane, gelir, eğitim, ev sahibi/kiracı,
  konut/ticari mülk sayıları ve bazı bölgesel fiyat alanları.
- Durum: **Yalnız toplulaştırılmış bağlam.** Demo kayıtlar sunulmaz. Demografi;
  konut uygunluğu, müşteri skoru, fiyat düşürme/artırma veya ayrımcı hedeflemede
  kullanılmaz.

## 7. Ticari ve sosyoekonomik göstergeler

### E-ticaret/harcama ve ticari potansiyel

- 2.100 kayıt; tamamı projeksiyon.
- Alanlar: e-ticaret hacmi, kişi başı harcama, uyum endeksi, işletme sayısı,
  ticaret payı, ciro potansiyeli, yeme-içme, market/perakende ve teslimat skoru.
- Durum: **Yalnız “model tahmini/projeksiyon” olarak.** Formül, veri dönemi ve
  belirsizlik açıklanmadan gerçek ciro veya talep olarak sunulamaz.

### SEGE/sosyoekonomik gelişmişlik

- 1.938 kayıt; tamamı 2026 revize projeksiyon olarak ayrıldı.
- Alanlar: sıra, skor, kademe, sınıf, dönem ve tahmin ufku.
- Durum: **Koşullu/projeksiyon.** Resmî SEGE verisiyle karıştırılmamalı; kaynak
  ve revizyon yöntemi doğrulanmalı.

## 8. Sektör rehberleri

### İnşaat/proje şirketleri

- 33.373 gözlem; 2.902 geçici kanonik şirket.
- Yalnız şirket ID, ad ve slug mevcut; proje ve konum ilişkisi yok.
- Durum: **Koşullu hazır.** Şirket adı rehberi olabilir; proje üreticisi veya
  güvenilirlik iddiası üretilemez.

### Emlak ofisleri

- 7.354 gözlem; 2.436 geçici kanonik ofis.
- Ad, şehir, adres, telefon, danışman ve ilan sayısı bulunuyor.
- Tüm kayıtlar kişisel/veri kullanım incelemesi bekliyor.
- Durum: **Yalnız iç kullanım/izinli rehber.** Toplu açık yayın, satış mesajı veya
  CRM aktarımı için hukuki dayanak ve iletişim izni gerekir.

### Gayrimenkul danışmanları

- 7.322 gözlem; 1.203 geçici kanonik danışman.
- Ad, unvan, ofis, telefon ve aktif ilan sayısı bulunuyor.
- Tüm kayıtlar kişisel veri incelemesi bekliyor.
- Durum: **Yalnız iç kullanım/izinli rehber.** Açık yayın veya otomatik iletişim
  yapılmamalı.

## 9. Lojistik

- 32 teslimat/kargo noktası.
- Ad, tip, kod, adres, telefon, çalışma saatleri ve koordinat alanları var.
- Kapsam ürün için çok küçük ve tamamı kişisel/veri kullanım incelemesinde.
- Durum: **Ürün için hazır değil.** Daha geniş ve lisanslı kaynak gerekir.

## 10. Kısıtlı ve sunulmaması gereken veriler

- 895.788 seçim sonucu gözlemi.
- 552.671 hemşehri/kütük dağılımı gözlemi.
- 572 sağlık, sigara, tüketim ve hassas davranış kaydı.
- Bu veriler saklanır fakat konut/arsa/işyeri fiyatlaması, mahalle uygunluğu,
  müşteri hedefleme, sıralama veya reklam kişiselleştirmesinde kullanılmaz.
- 62.114 demo demografi kaydı, 47 sentetik imar kaydı ve iki boş SQLite oluşumu
  son kullanıcıya sunulmaz.
- 240 araç ilanı ve 12 araç/refah kaydı gayrimenkul çekirdeğinin dışındadır;
  sınıflandırması düzeltilene kadar ürün sorgularından çıkarılmalıdır.

## Şu an güvenli biçimde sunulabilecek kapsam

1. Dönem, veri sınıfı ve güven etiketiyle bölgesel fiyat özeti ve geçmiş trend.
2. “Tahmin” etiketiyle fiziksel olarak ayrılmış projeksiyon grafikleri.
3. Oda, alan, kat, yaş ve ısıtma kırılımları.
4. Kaynağı doğrulanmış yıllık satış istatistikleri.
5. Yayın hakkı beyan edilmiş konut, işyeri ve arsa ilanlarından bağlantısız
   karşılaştırma sonuçları.
6. Bölgesel POI listesi ve adetleri; koordinat kaynağı eklenince mesafeler.
7. Hak sicili açık idari sınırlar ve coğrafi varlıklar.
8. Toplulaştırılmış, tarafsız demografi bilgi bölümü; skorlamasız.
9. Formülü ve belirsizliği açıklanan ticari projeksiyonlar.

## Tamamlanan çekirdek işler ve kalan sınırlar

1. **Tamamlandı:** 2.890 kaynak biriminin tamamı hak siciline bağlandı; eşleşme
   ve 9.140.813 satırlık denge tamdır. Kullanıcının yayın hakkı beyanına göre
   2.592 birim yayın/skorlamaya açıktır. 193 kişisel veri inceleme birimi, 103
   kısıtlı bağlam birimi ve 2 iç sistem birimi kapalıdır. Yasal hak sahibi
   adı/unvanı lisans bildiriminde hâlâ doldurulmalıdır.
2. **Tamamlandı:** 633.341 gözlem etiketli gelecek piyasa satırı kayıp olmadan
   projeksiyona taşındı; ayrıca 2025–2026 dönemindeki 36 sabit katsayılı yıllık
   satış satırı projeksiyon olarak ayrıldı. Gold denetiminde geleceğe tarihli
   gözlem anomalisi `0`.
3. **Tamamlandı:** 2.566.651 çakışma grubu; kayıt sınıfı, kaynak yönetişimi,
   kalite, doluluk, toplama zamanı, dönem ve sabit hash sırasıyla çözülüp karar
   tablosuna yazıldı. Alternatif gözlemler silinmedi.
4. **Tamamlandı:** Salt-okunur TKGM canlı kadastro katmanı ve kaynak bulunursa
   E-Plan/belediye imar katmanı aynı sorgu kanıtında tutuluyor. Erişilemeyen
   alanlar boş kalıyor.
5. **Tamamlandı:** Alıcı arsa ekranı, gerçek emsal seçimi, aykırı değer filtresi,
   fiyat aralığı, güven puanı, faktör açıklaması ve yalnız doğrulanmış KAKS/TAKS
   ile örnek proje hesabı sunuyor.
6. **Kalan:** Coğrafi kimlik eşleme; 81 il ve resmî ilçe/mahalle kodlarına
   kanonik bağ.
7. Çoklu harita POI yanıtlarını sağlayıcı, koordinat, sorgu zamanı, lisans/saklama
   politikası ve eşleştirme güveniyle normalize etmek.
8. KVKK ve platform kullanım şartları incelemesi.
9. Her kullanıcı sonucunda dönem, veri sınıfı, güven ve belirsizlik
   göstermek.

## Elimizde bulunmayan veya henüz ürünleşmemiş veriler

- Lisanslı yüksek çözünürlüklü Google uydu görüntüsü ve 10 yıllık mevsimsel arşiv.
- Parsel poligonlarının kalıcı ülke seti; parsel geometrisi TKGM'den istek anında
  canlı alınacaktır.
- Güncel ve resmî TAKS/KAKS/plan notu kapsamı.
- Tapu mülkiyet/sahip bilgileri.
- Parsel bazında yol cephesi, su altyapısı ve bağlantı durumu.
- Parsel bazında zemin/jeoloji, sıvılaşma ve afet uygunluk katmanları.
- Şehrin genişleme yönünü doğrulayacak çok dönemli yapılaşma arazi örtüsü.
- Gelecek kamu/özel projeleri ve plan kararları için güncel internet araştırma
  çıktıları.
- Çoklu harita sağlayıcılarından gelen koordinatlı POI'nin kalıcı Silver
  normalizasyonu; canlı OSM/Overpass akışı bugün mevcuttur.
- Kiralık konut/işyeri ham emsal ilan seti.
- İlan fotoğrafı/video arşivi, 360 tur, derinlik, 3B model ve sosyal medya medya
  çıktıları.
