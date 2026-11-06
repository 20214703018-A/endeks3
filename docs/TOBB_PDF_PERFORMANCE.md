# TOBB hızlı ve toplu PDF indirme

6 Kasım 2026. Kullanıcının güncel tercihi gereği üretimde PDF küçültme kapalıdır.
PDF dosyaları yeniden yazılmadan, kaynaktan gelen byte dizisiyle saklanır.
PDF bütünlüğü pikepdf ile kontrol edilir; özgün ve saklanan SHA-256 eşittir.
Tar/gzip yalnızca özgün dosyaları bir arşiv paketine koyar.

## İndirme akışı

- 40 runner; runner başına 1/2/4/8 eşzamanlı dosya indirme seçilebilir
  (varsayılan 8). Aynı oturumda PDF hazırlayan görüntüleyici isteği tek sırada
  ilerler; PDF dosyaları ayrı, işçiye özel bağlantı havuzlarıyla paralel indirilir.
  Dosya yanıtlarının çerezleri görüntüleyicinin yetkili oturumunu değiştirmez.
- Eski 200 isteğin tamamlanmasını bekleyen bariyer kaldırıldı. Her tamamlanan
  isteğin yerine hemen yeni iş verilir; kuyruk işçi sayısının iki katıyla sınırlıdır.
- Ağ zaman aşımında aynı PDF üzerinde dört kez beklenmez; diğer PDF'lere geçilir.
  Başarısız dosya DB'de tamamlandı sayılmaz ve sonraki koşuda yeniden seçilebilir.
- Her görüntüleyici/PDF yanıtı, yönlendirmeleri ve parça parça gelen içeriği
  kapsayan 90 saniyelik bütçeye tabidir. TCP bağlantısı için 5 saniye, görüntüleyici
  yanıtı için 45 saniye, PDF veri okuması için 15 saniye soket süreleri ayrıdır.
  Alttaki HTTP adaptöründe tekrar yoktur. Görüntüleyici için veri bekleme süresi
  12'den 45 saniyeye çıkarılmıştır; hazırlanmakta olan PDF'ye ait istek art arda
  yarıda bırakılmaz. Son blok okuması soket zaman aşımı kadar
  uzayabilir. Koşu bitiş zamanı da yeni istekleri ve içerik okumalarını sınırlar.
- Görüntüleyici başlangıçları arasında en az 1 saniye bırakılır; dosya aktarımı
  bu aralık tarafından serileştirilmez. TCP bağlantısı kurulamıyorsa hemen, diğer
  bağlantı hatalarında üç ardışık başarısızlık sonrasında 30 saniye beklenir; ardından tek
  istekle yeniden denenir. Sonraki hata penceresinde 60 saniye beklenir. Üçüncü
  hata penceresi de başarısızsa kaynak erişilemez olarak durulur. Bekleyen işçiler
  yüzlerce yeni istek göndermez. Başarılı yanıt hata sayaçlarını sıfırlar.
- Oturum yenilemeleri ortak sürüm sayacıyla birleştirilir; yenilemeden önce
  mevcut görüntüleyici istekleri boşaltılır. İlan doğrulamasının normal form
  gönderimi ve başarılı yönlendirmesi tamamlanır.
- Görüntüleyicinin HTTP 200 içinde döndürdüğü görünür “Giriş yapmalısınız”
  bildirimi de oturum yenilemesi başlatır. Sadece menüdeki giriş bağlantısı,
  ortak giriş modalı veya script içindeki metin oturum kaybı sayılmaz.
  Yenileme başarısızsa `AUTH_REQUIRED` raporlanır; `NO_PDF` olarak gizlenmez.
- Açık 429/403 yanıtında shard durur; erişim kontrolü nedeniyle başarısız koşu
  kendiliğinden yeniden başlatılmaz.

## Toplu arşivleme

- Paketleme/yükleme ayrı işçide indirmelerle paralel yürür; en fazla iki paket
  bekler. İndirme işçileri her yüklemenin tamamlanmasını beklemez.
- Eski 25 yerine 100 PDF/paket: aynı PDF sayısında yaklaşık %75 daha az
  Release yükleme çağrısı. 128 MiB'de paket erken kapatılır.
- İlk 10 PDF erken yüklenir. Kaynak durursa eldeki dosyalar 30 saniye sonra
  küçük paketle arşivlenir. Çalışan Release kovası sonraki pakette önce denenir.
- Master DB indirmelerde salt okunur; küçük shard DB'leri sonradan birleştirilir.
  Gazete/sayfa kimliği kullanılır; farklı snapshot'lardaki sayısal ID'lere güvenilmez.
- Yükleme onayından önce tamamlandı kaydı yazılmaz. Onaylı paketler kaynak
  beklerken de yaklaşık beş saniyelik kontrol aralığında DB'ye işlenir.
- Başarısız yerel PDF/paketler korunur; kurtarma artifact'leri 30 gün saklanır.
  Bu saklama süresi kalıcı Release arşivinin yerine geçmez.

## Canlı geçiş kanıtı

- Eski `42127049888` koşusunun 2.100 onaylı PDF bağlantısı master'a kurtarıldı.
  Kesilen sonraki iki koşudan 171 + 759 = 930 ek PDF kalıcı yayımlandı.
  Bunlar bütün arşivin toplam sayısı değildir.
- `42145868137`: iki runner, sekizer işçi; 27 + 19 = 46 PDF kalıcı arşivlendi
  ve master'a birleştirildi. Son loglar 237 ve 178 PDF/saat; arşivleme hatası 0.
- `42191862827`: iki runner, birer işçi; 14 + 14 = 28 PDF arşivlendi ve
  master'a birleştirildi. Son loglar 139 ve 238 PDF/saat; arşivleme hatası 0.
- Bu ölçümler yeni tek ağ denemesi/yanıt bütçesi değişikliğinden öncedir.
  Daha az paralel işçi tek başına belirgin hız kazandırmadı. TOBB yanıtlarının
  bir süre sonra yavaşlaması belirleyiciydi; sürdürülebilir 40 runner hızı bu
  kısa ölçümleri kırkla çarparak doğrulanmış sayılamaz.
- Yeni `be0f2c6` sürümünün `42193542274` kısa kontrolü: 15 + 12 = 27 özgün
  PDF, 38 ilan bağlantısı kalıcı arşivlenip master'a birleştirildi. Arşivleme
  hatası 0; son işçi logları 438 ve 356 PDF/saat. Canlı paketten 10 PDF'nin
  SHA-256 değerleri manifestteki özgün/saklanan değerlerle birebir doğrulandı.
- `42193540997` GitHub CI koşusunda 37 test geçti. `42194105241` tam 40 shard
  koşusu aynı `be0f2c6` sürümüyle başlatıldı. 09:23 UTC anında 5 PDF işi aktif,
  35'i kuyruktaydı; ayrı Google KARO koşusu 34 runner kullanıyordu. Tam 40
  runner'ın sürdürülebilir toplam hızı henüz ölçülmedi. Google koşusunun ara
  çıktıları yalnızca normal tamamlanmada yüklendiği için izinsiz iptal edilmedi.

Yerel eski döngü servisi durduruldu ve kodu yalnızca gözlem yapar. Böylece eski
commit'i yeniden başlatıp master'ı eski yerel snapshot ile ezmez. PDF'lere runner
kapasitesi ayırmak için metadata toplama matrisi geçici olarak duraklatıldı.

## Doğrulama

```sh
python -m pytest -q tests/test_tobb_pdf_archive.py
```

50 test: özgün byte/hash koruması, bozuk PDF reddi, yükleme hatasında dosya koruma,
manifest checksum, shard dağılımı, master rollback ve sayfa kimliği, eşzamanlı
indirme/yükleme, oturum yenilemelerini birleştirme, ağ yanıtı/yönlendirme bütçesi,
koşu bitişi ve açık kaynak erişim sınırları. Önceki isteğe bağlı kayıpsız PDF
optimizasyonunun testleri de regresyon amacıyla bulunur; üretim işçisi bu
optimizasyonu çağırmaz. Testler TOBB'ye bağlanmaz veya Release'e dosya yüklemez.
GitHub CI aynı paketi Python 3.11 Ubuntu üzerinde çalıştırır.

## 6 Kasım akşam bağlantı düzeltmesi

- Eksik PDF bağlantılı 895.124 ilan kaydının GUID biçimi tarandı; tamamı geçerli
  UUID idi. Sayı tekil PDF sayısı değil, incelenen ilan kaydı sayısıdır.
- Eski `42228495645` koşusu durduruldu; birleştirme işi `128110503929` başarıyla
  tamamlandı. Kurtarılmamış PDF artifact'i yoktu. Master 19:38:13 UTC'de yayımlandı.
- `afca571` sürümü için `42282414201` CI koşusunda 42 test geçti.
- Aynı işçi istemcisiyle anonim ana sayfa kontrolü HTTP 200 / 0,27 saniyeydi.
  Genel site kesintisi veya IP engeli teşhisi konulmadı. Oturum/görüntüleyici
  erişim kontrolünü aşan hesap, IP veya istemci kimliği değiştirme eklenmedi.

- İlk seri görüntüleyici kontrolü `42282416927`: 10 + 12 = 22 PDF arşivlendi.
  `ConnectTimeout` hatalarının 45 saniyelik birleşik timeout, iki DNS adresi ve
  adaptör tekrarı nedeniyle yaklaşık 180 saniyeye uzadığı logda görüldü. Bu
  kontrol, sürekli indirme sorununu çözmüş sayılmadı.
- `52a4fdd`: bağlantı/okuma süreleri ayrıldı; adaptör tekrarları kaldırıldı,
  koşu bitişi oturum bakımını da sınırlar. `ea5b5db`: görüntüleyici hazırlığı
  saniyede birle sınırlı; dosya aktarımları paralel. `42284507569` CI geçti
  (47 test).
- `42284507548` canlı kontrolü: 11 + 12 = 23 PDF, 30 ilan bağlantısı kalıcı
  arşivlendi. Arşivleme hatası 0. İki runner da ilk dosyalardan sonra TCP
  bağlantısı kuramadı ve üç toparlanma penceresi sonunda `SOURCE_UNAVAILABLE`
  ile durdu. Birleştirme işi `128123657478` başarılı; genel koşu başarısızdır.
  Kısa loglardaki 280 PDF/saat sürdürülebilir hız veya 40 runner tahmini değildir.
- Tekil, anonim GUID kontrolünde resmî görüntüleyici HTTP 200 ile “Giriş
  yapmalısınız” bildirimi döndürdü. GUID tek başına PDF erişimi sağlamadı.
  Bu yanıtı `NO_PDF` sayan oturum sınıflandırması düzeltildi; üç yeni test
  başarılı yenilemeyi, başarısız yenilemeyi ve ortak menü/script ayrımını kapsar.
  Bu düzeltme TCP kesintisinin çözülmüş olduğunu göstermez.
- Aynı hatayla tekrar 40 runner başlatılmaması için PDF workflow'u
  `disabled_manually` durumuna alındı. Sürekli toplu indirme sorunu açıktır;
  doğrulanmış bir CAPTCHA/erişim kontrolü aşımı veya sınırsız indirme iddiası yoktur.
