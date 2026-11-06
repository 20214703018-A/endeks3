# TOBB hızlı ve toplu PDF indirme

7 Kasım 2026 (Türkiye). Kullanıcının güncel tercihi gereği üretimde PDF küçültme kapalıdır.
PDF dosyaları yeniden yazılmadan, kaynaktan gelen byte dizisiyle saklanır.
PDF bütünlüğü pikepdf ile kontrol edilir; özgün ve saklanan SHA-256 eşittir.
Tar/gzip yalnızca özgün dosyaları bir arşiv paketine koyar.

## İndirme akışı

- 40 shard; runner başına 1/2/4/8 eşzamanlı dosya indirme seçilebilir
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
  uzayabilir. Planlanmış hız sınırı/bekleme süreleri yanıtın ağ bütçesinden ayrıdır;
  başarılı yanıt, öncesindeki bekleme nedeniyle zaman aşımı sayılmaz. Koşu bitiş
  zamanı yeni istekleri ve içerik okumalarını sınırlar.
- Görüntüleyici başlangıçları arasında en az 1 saniye bırakılır; dosya aktarımı
  bu aralık tarafından serileştirilmez. TCP bağlantısı kurulamıyorsa hemen, diğer
  bağlantı hatalarında üç ardışık başarısızlık sonrasında 30 saniye beklenir; ardından tek
  istekle yeniden denenir. Sonraki hata penceresinde 60 saniye beklenir. Üçüncü
  hata penceresi de başarısızsa kısa kontrol kipinde kaynak erişilemez olarak durulur.
  Üretim kipi aşağıdaki sekiz pencere ve kalıcı toparlanma akışını kullanır.
  Bekleyen işçiler yüzlerce yeni istek göndermez. Başarılı yanıt hata sayaçlarını sıfırlar.
- Oturum yenilemeleri ortak sürüm sayacıyla birleştirilir; yenilemeden önce
  mevcut görüntüleyici istekleri boşaltılır. İlan doğrulamasının normal form
  gönderimi ve başarılı yönlendirmesi tamamlanır.
- Görüntüleyicinin HTTP 200 içinde döndürdüğü görünür “Giriş yapmalısınız”
  bildirimi de oturum yenilemesi başlatır. Sadece menüdeki giriş bağlantısı,
  ortak giriş modalı veya script içindeki metin oturum kaybı sayılmaz.
  Yenileme başarısızsa `AUTH_REQUIRED` raporlanır; `NO_PDF` olarak gizlenmez.
- Üretimde `--persistent-source`: işçi bütçesi 19.800 saniye (5,5 saat).
  Geçici bağlantı ve oturum hataları sayfayı yeniden kuyruğa alır. İlk ağ bağlantısı
  kurulamazsa başlangıç girişi tekrar denenir; geçici Release yükleme arızasında
  dosyalar tutulur ve tekrar yüklenir. Bir dakikalık heartbeat ilerlemeyi gösterir.
- Tüm HTTP yolları ve otomatik yönlendirmeler ortak hız sınırını kullanır.
  TCP/okuma hatasında bütün yollar 30 saniye duraklar, istek aralığı artırılır.
  50 başarılı görüntüleyici isteği ve en az 5 dakikalık kararlılıktan sonra
  aralık seçilmiş alt sınıra doğru düşer. Üretim alt sınırı 1 saniye seçilmiştir.
- Sekiz geçici hata penceresinden sonra 10 dakikalık toparlanma aralığı açılır;
  ardından aynı hesap/uç noktayla tek kontrol yapılır. 429 yanıtında `Retry-After`
  uygulanır (yoksa 300 saniye). 403 yanıtında yeni istekler askıya alınır ve koşu
  sonunda erişim hatası raporlanır. Açık kalan işçi, erişim reddinde PDF indirdiği
  anlamına gelmez. Bu akış sağlayıcı erişim denetimini kaldırmaz.

## Toplu arşivleme

- Paketleme/yükleme ayrı işçide indirmelerle paralel yürür; en fazla iki paket
  bekler. İndirme işçileri her yüklemenin tamamlanmasını beklemez.
- Eski 25 yerine 100 PDF/paket: aynı PDF sayısında yaklaşık %75 daha az
  Release yükleme çağrısı. 128 MiB'de paket erken kapatılır.
- İlk 10 PDF erken yüklenir. Son başarılı indirmeden itibaren 30 saniye geçerse
  (normal, yavaş akışta her 30 saniyede bir değil) eldeki dosyalar
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

76 test: hesapların shard'lara sabit ve farklı atanması, yapılandırma hatasında
ortak hesaba dönmeme, günlüklerde kimlik bilgilerinin gizlenmesi; özgün byte/hash koruması, bozuk PDF reddi, yükleme hatasında dosya koruma,
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
  o aşamada `disabled_manually` durumuna alınmıştı. Aşağıdaki yeni sürümle açıldı.

## 7 Kasım: uzun koşu ve mevcut paketlerden kurtarma

- `42287735936`, tüm HTTP istekleri arasında 3 saniye kullanan 15 dakikalık
  tek-runner kontrolüdür: 95 özgün PDF, 120 ilan bağlantısı, 380 PDF/saat,
  arşivleme hatası 0. İlk 64 dosyanın manifest/hash değerleri bağımsız indirilmeyle
  doğrulandı. Sonda koşu bütçesine ulaşan istekler ve bir PDF ağ hatası vardır;
  önceki kalıcı TCP kesintisi bu kontrolde tekrarlanmadı. Bu sonuç 40 ile çarpılarak
  doğrulanmış toplam hız sayılmaz.
- `42290375218` daha hızlı, 1 saniyelik 40-shard geçişidir. Bir işçi dosya ağ
  hatalarından sonra oturum hatasıyla erken durdu. Sonraki `8f23b6a` sürümü dosya
  ve giriş yollarının taşıma hatalarını da ortak toparlanmaya ekledi.
- `eaa8f53`: 5,5 saatlik kalıcı çalışma, geçici hata sonrası aynı sayfayı yeniden
  seçme, kaynak sınırlarına uygun toparlanma ve yükleme tekrarları. Yerelde ve
  `42293236079` GitHub CI koşusunda 66 test geçti. Bu, 5,5 saat boyunca kesintisiz
  veri aktarımının canlı olarak doğrulandığı iddiası değildir.
- `42293357087` bu sürümün 40-shard, 19.800 saniyelik yeni koşusudur.
  Fiziksel eşzamanlı runner sayısı hesabın diğer aktif işleriyle paylaşılan
  GitHub kapasitesine bağlıdır. Kaynak koşularının parametreleri devam koşusuna
  aktarılır; master yayımlamaları kurtarma işlemleriyle aynı kilidi kullanır.
- 6 Kasım 23:04:30 Türkiye zamanlı ana snapshot'ta 183.556 tekil gazete/sayfa
  PDF kaydı ve 310.356 ilan bağlantısı vardı; GUID bulunan eksik tekil sayfa
  sayısı 687.669 idi. Sonraki koşular bu sayıları değiştirmiş olabilir.
- Release envanterinde master'da referansı olmayan 2.117 paket / 12.278.875.512
  byte bulundu. Altı gerçek paketteki 300 PDF'nin 21'i master'da eksik sayılıyordu.
  `tools/index_tobb_release_archives.py` PDF'leri açıp SHA-256/manifesti denetler,
  yalnızca boş bağlantıları doldurur; mevcut PDF referanslarını değiştirmez.
- `42289333954` taramasının tamamlanmış 24 parçası artifact olarak korunmuştur.
  16.958 sayfa / 23.448 ilan bağlantısı bu parçaların birleştirilmesiyle kurtarıldı.
  Kalan 884 paket (4.859.510.028 byte), yerelde sekiz işçiyle tamamlandı; 41.148
  PDF doğrulandı, tarama hatası 0. Bu bölüm 2.633 sayfa / 3.901 bağlantı ekledi.
  macOS kurtarma workflow'u yedek olarak eklendi; canlı tamamlamada kullanılmadı.
  Kalıcı katalog 2.156 paket / 103.624 doğrulanmış PDF referansı içeriyor; bu sayı
  yinelenen PDF'leri kapsar ve tekil master sayısı değildir.

## Yayımlanmış eğitim dizini ve güncel durum

Son yerel birleştirme: 203.652 tekil gazete/sayfa PDF kaydı, 338.416 ilan bağlantısı;
toplu dizinde 5.524 farklı arşiv. Ana DB ve dizin iki aşamalı yayınla yenilenir.

- Kurtarma toplam 19.591 eksik gazete/sayfa PDF bağlantısını doldurdu. Dosyalar
  zaten kalıcı paketlerdeydi; TOBB'den tekrar indirilmedi. 333.938 ilan referansının
  kimlik kontrolünde gazete/sayfa ile PDF dosya adı arasında uyuşmazlık 0 idi.
- 7 Kasım 00:32 Türkiye kontrolü: yerel işçi 40 yeni PDF / 84 ilan bağlantısını
  özgün 3.693.038 byte ile arşivledi. Son hız 293 PDF/saat, bir oturum yenileme
  hatası, arşiv hatası 0. Bu, 5,5 saatlik sürdürülebilir hız ölçümü değildir.
- `42293357087` uzun koşusunda 00:20 Türkiye anına kadar 94 yeni pakette 222
  farklı PDF bağımsız hash kontrolüyle doğrulandı. Çalışan/kuyruktaki işçi sayısı
  anlıktır: 00:34 kontrolünde 12 aktif, 28 kuyruk, tamamlanmış hata 0. Aynı
  hesapta Market Fiyatı 20, Google KARO 8 runner kullanıyordu.
- Toplu indirme dizini, tekil gazete/sayfayı GitHub arşiv URL'si ve paket içindeki
  dosya adıyla eşler. Aynı paket bir kez indirilerek içerdiği PDF'ler alınabilir.
  `tools/export_tobb_training_index.py` bu dizinleri master'dan üretir; yeni
  indirme/birleştirme ve otomatik arşiv uzlaştırma workflow'ları dizini günceller.
- [Sayfa dizini](https://github.com/20214703018-A/endeks3/releases/download/warehouse-pdf-20/tobb_training_pages.jsonl.gz)
- [Toplu paket dizini](https://github.com/20214703018-A/endeks3/releases/download/warehouse-pdf-20/tobb_training_archives.jsonl.gz)
- [Dizin sayıları](https://github.com/20214703018-A/endeks3/releases/download/warehouse-pdf-20/tobb_training_index_summary.json)

5,5 saat, işçinin çalışma bütçesidir; sağlayıcının sürekli veri aktaracağına
garanti değildir. Kaynakta hız/oturum sınırları sürmektedir. Üretim aktarımının
tam 40 runner ile kesintisiz sürdüğü henüz kanıtlanmadı.

7 Kasım 00:42 Türkiye ek kontrolü: 159 yeni GitHub paketi / 63.276.098 byte
taramasında 537 PDF bütünlük kontrolünden geçti, hata 0. Bunların 342 farklı
gazete/sayfası ve 473 ilan bağlantısı master'da eksikti; eklendi. Diğerleri zaten
bağlıydı ve mevcut referanslar korundu. Kalıcı tarama kataloğu 2.315 paket /
104.161 doğrulanmış PDF referansına ulaştı. Bu andaki uzun koşu 20 aktif,
20 kuyruk, tamamlanmış hata 0 idi. `be80a2b` için `42298045992` GitHub CI geçti.

Resmî [abonelik açıklamasında](https://www.ticaretsicil.gov.tr/view/hizlierisim/goster.php?Guid=4fb204d4-8b72-11e9-a292-54e058904e0d)
tam günlük gazete ve web servis seçenekleri abonelik/talep hizmeti olarak
sunuluyor. İncelenen normal sayfalarda ücretsiz tam gazete indirme yolu bulunmadı;
bu, böyle bir yolun hiçbir yerde bulunmadığının kanıtı değildir. Kullanıcı adına
satın alma veya başvuru yapılmadı.

## 7 Kasım: eski hesap düzeninin bulunması ve geri alınması

- `555bfc9` (2 Kasım) işçi başına deterministik ayrı hesaplar kullanmayı
  deniyordu: 40 runner × 4 işçi. `41478934672` koşusunun loglarında 146 işçi
  oturum durumu, 89 AÇIK bildirimi var. Bu sürüm beş başarısız hesap denemesinden
  sonra ortak hesaba dönebildiği için AÇIK bildirimi tek başına ayrı hesabın
  kullanıldığına kanıt değildir. Koşu yaklaşık dokuz dakika sonra iptal edilmişti.
- Sonraki `dc0e035` işçi hesabı yerine doğrudan `FALLBACK_EMAIL` kullanıyor;
  `6ebf681` tekrar tek `USER_EMAIL` yapılandırmasına dönüyor. Mevcut PDF motoru
  bu tek hesaplı düzeni devralmıştı. Kullanıcının eski çok hesaplı düzeni
  hatırlatması üzerine geçmiş ve gerçek hesap erişimi ayrıca incelendi.
- Eski iki hesabın (ham e-posta/şifre kayda yazılmadan) ayrı girişleri 18,1 ve
  21,1 saniyede doğrulandı. Yeni hesap açılmadı, ortak hesap kullanılmadı. Sonraki
  kontrolde toplam 30 farklı eski hesaba giriş doğrulandı. Kalan kontrol sırasında
  curl ve requests aynı normal giriş sayfasına TCP bağlantısı kuramadı; bu durum
  yanlış şifre ya da kesin IP engeli olarak sınıflandırılmadı.
- 40 sabit, farklı hesap `TOBB_ACCOUNTS_JSON` GitHub secret'ına yerleştirildi.
  İlk 30 slot canlı doğrulandı; 31–40 eski loglarda açık görünen diğer hesaplardan
  seçildi ve ayrıca bulut kontrolüne ayrıldı. Tam 40'ı doğrulanmadan 40 canlı
  giriş kanıtı olduğu söylenmez. Gizli değerler yeni kodda veya loglarda yoktur.
- `collector/tobb_accounts.py` bütün havuzu doğrular, aynı hesabın iki shard'a
  atanmasını ve eksik/geçersiz yapılandırmayı reddeder. Üretim workflow'u havuz
  gerektirir; yapılandırma yoksa ortak hesaba dönmez. Hesap koşu boyunca sabittir.
  Kaynak erişim/hız reddinde hesap değiştirme veya otomatik kayıt eklenmedi.
- Soğuk başlangıçta istek aralığı en az 3 saniyedir. Elli başarılı görüntüleyici
  ve beş dakikalık kararlılıktan sonra mevcut geri bildirim döngüsü seçilen
  asgari aralığa doğru hızlanır. Bir saniyelik kısa kontrolde ilk paket hızlı olsa
  da sonra bağlantı/oturum kesintisi görüldüğü için başlangıç doğrudan hızlandırılmaz.
- Ayrı hesaplı iki gerçek PDF kontrolü: shard 17, 12 sayfa / 15 ilan /
  1.348.507 özgün byte, son hız 144 PDF/saat; shard 29, 4 sayfa / 5 ilan /
  368.136 özgün byte, son hız 355 PDF/saat. Arşivleme hatası 0. İlk paketteki
  1.194 PDF/saat kısa patlamadır; toplam 16 PDF'lik bu kontrol sürdürülebilir
  hız artışını kanıtlamaz. Bağlantı ve oturum sorunları devam etti.
- `TOBB Existing Account Checks` yalnızca mevcut sabit hesaplara normal giriş
  kontrolü yapar. Kaynak reddi veya bağlantı kesintisinde kalan hesap kontrolleri
  askıya alınır. Çıktı hesap slotu ve hash içerir; parola/e-posta içermez.
- Çalışan `42293357087` eski commit ile açıldığı için yeni gizli havuzu okumaz.
  Yeniden başlatmadan süreç içindeki hesabı değiştirilemez; bu koşunun kesildiği
  veya yeni havuza geçtiği henüz iddia edilmez.

Sürüm geçişi kontrol noktası (7 Kasım 01:20 Türkiye):

- `5d626b9`: sabit hesap havuzu, kaynak başlangıcında 3 saniyelik aralık ve
  mevcut kararlılık ölçümüne göre hızlanma; yerelde 76 test geçti.
- `42303340191`: yeni sürümün GitHub CI koşusu, kapasite kuyruğundaydı.
- `42303349483`: son 10 hesabın giriş kontrolü, kapasite kuyruğundaydı.
- `42303809207`: 40 sabit hesap / 4 indirme işçisi / 19.800 saniye bütçeli
  yeni dalga, eski dalganın ardına sıraya alındı. İstek alt sınırı 1 saniye
  seçildi; yeni kod önce 3 saniye ile başlar, kararlıysa hızlanır.
- Eski `42293357087`: 32 aktif / 8 kuyruk / tamamlanmış hata 0; hâlâ eski
  tek hesaplı kodla çalışıyor. Kullanıcının 5,5 saat kesilmemesi talimatı korunuyor.
  Hemen geçiş için eski dalgayı veri kurtarmayla kapatma seçimi kullanıcıya soruldu;
  yanıt gelmeden çalışan dalga iptal edilmez.
- İki hesaplı kontroldeki 16 PDF / 20 ilan bağlantısı doğal gazete/sayfa
  anahtarıyla master'a birleştirildi; toplam 203.652 sayfa / 338.416 bağlantı.
  Koşunun canlı olması aktarımın kesintisiz olduğu anlamına gelmez.
