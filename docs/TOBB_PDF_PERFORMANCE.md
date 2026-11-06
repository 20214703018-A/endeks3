# TOBB PDF indirme ve kayıpsız arşivleme

6 Kasım 2026 doğrulaması. Mevcut 40 runner ve runner başına 8 indirme işçisi korunur.

## Hız değişiklikleri

- 200 isteğin tamamlanmasını bekleyen döngü kaldırıldı. Tamamlanan isteklerin
  yerine hemen yeni işler verilir; en fazla işçi sayısının iki katı istek sıradadır.
- Paketleme/yükleme ayrı bir işçide indirmelerle paralel yürür. En fazla iki
  paket bekletilir; bellek/disk kuyruğu sınırsız büyümez.
- 25 yerine 100 PDF paketlenir: aynı sayıda PDF için yaklaşık %75 daha az
  Release yükleme çağrısı. 128 MiB dosya toplamında paket erken kapatılır.
- Başarıyla çalışan Release kovası sonraki pakette önce denenir. Mevcut 20
  kovalı düzen korunur. Her pakette dolu kovaları tekrar dolaşma maliyeti azalır.
- Master DB, indirme sırasında salt okunur kullanılır. Her PDF için büyük
  master üzerinde UPDATE/index değişimi yapılmaz; küçük shard DB'si birleştirilir.
- Aynı eski HTTP yanıtına bağlı oturum yenilemeleri sürüm sayacı ile birleştirilir;
  bekleyen işçi, başka işçi oturumu yenilediyse yeniden giriş başlatmaz.
- Gerçek süreye göre sayfa/saat ve kaydedilen byte miktarı her onaylı pakette loglanır.

Bunlar doğrulanmış akış değişiklikleridir. Toplam canlı hız için TOBB yanıt süreleri,
erişim kontrolleri, GitHub runner kuyruğu ve Release yükleme süreleri belirleyicidir.
Mevcut çalışmalar eski commit ile devam eder; kod değişikliği çalışan runner'a
kendiliğinden uygulanmaz. Tamamlanma süresi veya tüm arşivin saatler içinde
biteceği henüz canlı ölçümle kanıtlanmış değildir.

İlk 10 PDF erken arşivlenir. Kaynak yanıtları durursa eldeki tamamlanmış PDF'ler
30 saniye sonra ayrıca arşivlenir. Bakım sırasında yeni viewer istekleri durur;
oturum yenileme için nominal 60 saniyelik toplam deneme bütçesi uygulanır. İç içe
ağ okuma tekrarları kaldırılmıştır. Genel menüdeki giriş bağlantısı oturum
kapanması olarak değerlendirilmez.

## İçerik koruması

Görüntü yeniden kodlama, DPI düşürme, OCR, font/ek/metadata silme uygulanmaz.
pikepdf ile yalnızca kayıpsız Flate/container sıkıştırması yapılır. PDF 1.5 ve
üzerinde object-stream sıkıştırması kullanılabilir; eski PDF sürümü yükseltilmez.
İmzalı ve artımlı geçmiş içeren belgeler özgün halleriyle korunur.

Küçülen aday tekrar açılarak belge nesne grafiği karşılaştırılır: içerik akışları,
ham sıkıştırılmış görüntüler, ekler, metadata, alanlar ve sayfa kaynakları.
Doğrulama başarısızsa veya aday büyükse geçerli kaynak PDF saklanır.
Bozuk/kesik indirmeler tamamlanmış sayılmaz. PDF dosyasının byte dizisi
değişebilir; belge içeriğinin korunması ile byte düzeyinde özgünlük farklıdır.
Özgün ve saklanan SHA-256 değerleri paket içindeki `manifest.json` dosyasındadır.

Release yüklemesi başarıyla onaylanmadan shard DB'sine tamamlandı kaydı yazılmaz.
Başarısız paketler ve yerel PDF'ler korunur; workflow kurtarma artifact'lerini
30 gün tutar. Bu süre kalıcı arşiv yerine geçmez. Shard hatası varsa otomatik
sonraki koşu başlatılmaz. Hatalı shard birleştirmesi master güncellemelerini geri
alır ve işlem hata koduyla durur; başarısız master yüklemeleri başarı sayılmaz.

## Gerçek dosya ölçümü

Yerel `warehouse/raw/gazete_sayfalari` klasöründen dosya adına göre sıralı her
16. PDF seçilerek 100 gerçek TOBB belgesi incelendi (129 sayfa).

| Ölçüm | Sonuç |
| --- | ---: |
| Kaynak PDF toplamı | 30.624.786 byte |
| Saklanacak PDF toplamı | 30.608.835 byte |
| Tasarruf | 15.951 byte (%0,05209) |
| Küçülen PDF | 18 |
| Özgün hali korunan PDF | 82 |
| 150 DPI piksel karşılaştırması | 129/129 aynı |
| Çıkarılan metin karşılaştırması | 129/129 aynı |

PDF'lerde JPEG2000, JPEG, JBIG2 ve CCITT görüntü sıkıştırması zaten mevcut.
Her PDF'nin anlamlı oranda kayıpsız küçülmesi garanti edilemez. Bu ölçüm PDF
dosyalarının toplamıdır; tar/gzip paket boyutu veya bütün arşiv için oran değildir.
Özgün dosyalar bu ölçüm sırasında değiştirilmedi.

## Tekrar doğrulama

```sh
python -m pytest -q tests/test_tobb_pdf_archive.py
python tools/benchmark_tobb_pdf_archive.py warehouse/raw/gazete_sayfalari \
  --step 16 --limit 100 --dpi 150 --report /tmp/tobb-pdf-benchmark.json
```

21 test; yükleme hatası, bozuk PDF, imza, ek/metadata koruması, checksum,
shard dağılımı, master rollback, indirme/yükleme örtüşmesi, çalışan kova tercihi
oturum yenileme tekrarlarının önlenmesi ve yavaş PDF'nin sonraki 200 görevlik grubu engellememesini kapsar.
Benchmark için pikepdf ve PyMuPDF, testler için pikepdf/requests/pytest gerekir.
Yeni CI kontrolü Python 3.11 Ubuntu üzerinde aynı testleri çalıştırır; TOBB'ye
bağlanmaz ve Release arşivine dosya yüklemez.

Kayıpsız sıkıştırma ayarları:
[pikepdf resmi save belgeleri](https://pikepdf.readthedocs.io/en/stable/api/main.html#pikepdf.Pdf.save).
