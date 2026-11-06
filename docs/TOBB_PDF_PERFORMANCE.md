# TOBB hızlı ve toplu PDF indirme

6 Kasım 2026. Kullanıcının güncel tercihi gereği üretimde PDF küçültme kapalıdır.
PDF dosyaları yeniden yazılmadan, kaynaktan gelen byte dizisiyle saklanır.
PDF bütünlüğü pikepdf ile kontrol edilir; özgün ve saklanan SHA-256 eşittir.
Tar/gzip yalnızca özgün dosyaları bir arşiv paketine koyar.

## İndirme akışı

- 40 runner; runner başına 1/2/4/8 eşzamanlı indirme seçilebilir (varsayılan 8).
- Eski 200 isteğin tamamlanmasını bekleyen bariyer kaldırıldı. Her tamamlanan
  isteğin yerine hemen yeni iş verilir; kuyruk işçi sayısının iki katıyla sınırlıdır.
- Ağ zaman aşımında aynı PDF üzerinde dört kez beklenmez; diğer PDF'lere geçilir.
  Başarısız dosya DB'de tamamlandı sayılmaz ve sonraki koşuda yeniden seçilebilir.
- Her görüntüleyici/PDF yanıtı, yönlendirmeleri ve parça parça gelen içeriği
  kapsayan 45 saniyelik bütçeye tabidir. Son blok okuması soket zaman aşımı kadar
  uzayabilir. Koşu bitiş zamanı da yeni istekleri ve içerik okumalarını sınırlar.
- Oturum yenilemeleri ortak sürüm sayacıyla birleştirilir; yenilemeden önce
  mevcut görüntüleyici istekleri boşaltılır. İlan doğrulamasının normal form
  gönderimi ve başarılı yönlendirmesi tamamlanır.
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

Yerel eski döngü servisi durduruldu ve kodu yalnızca gözlem yapar. Böylece eski
commit'i yeniden başlatıp master'ı eski yerel snapshot ile ezmez. PDF'lere runner
kapasitesi ayırmak için metadata toplama matrisi geçici olarak duraklatıldı.

## Doğrulama

```sh
python -m pytest -q tests/test_tobb_pdf_archive.py
```

37 test: özgün byte/hash koruması, bozuk PDF reddi, yükleme hatasında dosya koruma,
manifest checksum, shard dağılımı, master rollback ve sayfa kimliği, eşzamanlı
indirme/yükleme, oturum yenilemelerini birleştirme, ağ yanıtı/yönlendirme bütçesi,
koşu bitişi ve açık kaynak erişim sınırları. Önceki isteğe bağlı kayıpsız PDF
optimizasyonunun testleri de regresyon amacıyla bulunur; üretim işçisi bu
optimizasyonu çağırmaz. Testler TOBB'ye bağlanmaz veya Release'e dosya yüklemez.
GitHub CI aynı paketi Python 3.11 Ubuntu üzerinde çalıştırır.
