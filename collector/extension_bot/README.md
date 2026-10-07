# GEOPROP Menü Toplayıcı 2.4

Bu Chrome eklentisi mevcut Google Places ambarındaki uygun restoranları kalıcı bir SQLite kuyruğundan işler. Her mekan için `mekan adı + adres` Google Search sorgusunu açar ve bilgi panelindeki **Menü** düğmesine basar; Maps koordinat araması kullanmaz. İş ilerlemesi tarayıcı veya servis yeniden başlasa da korunur.

## Kapsam kuralları

- İller: 81 il (`GEOPROP_MENU_ILLER="İzmir,Muğla"` ile daraltılabilir)
- Kategori: tüm yiyecek-içecek: restoran, kafe, kahveci, pastane, tatlıcı, büfe (`GEOPROP_MENU_SCOPE=restoran` yalnız restoran tipi)
- En az 10 Google değerlendirmesi
- İlçe nüfusu en az 25.000
- Mahalle nüfusu en az 3.000
- Köy/belde kayıtları kapsam dışı
- Yalnız `Google Maps...` kaynaklı, benzersiz `google_place_id` kayıtları
- `Istanbul`/`İstanbul` gibi shard yazım farkları kanonik il adına dönüştürülür
- İnternet kafe, PlayStation/e-spor ve oyun salonu gibi yemek dışı `Kafe` kayıtları kapsam dışıdır

Nüfus eşleşmesi olmayan kayıtlar varsayımla tamamlanmaz; kapsam dışında bırakılır.

## Tarama sırası

Kuyruk `oncelik` sütununa göre işlenir (büyük önce):

1. Batı büyükşehirleri: İstanbul, İzmir, Bursa, Antalya, Kocaeli, Muğla, Tekirdağ, Balıkesir, Aydın
2. İlk turda taranan diğer iller: Ankara, Konya, Çanakkale, Diyarbakır, Trabzon
3. Kalan iller

Her grupta, daha önce taranan mekanlarda menü bulma oranı yüksek olan kategoriler öne alınır (ör. pizza ~%75, restoran & lokanta ~%23). Bu oranlar sunucu her açıldığında kuyruktaki sonuçlardan yeniden hesaplanır. Böylece saat başına daha çok fiyat toplanır.

## Çalıştırma

Kod `~/Desktop/endeks3` deposundadır; veri ambarı `~/Desktop/GEOPROP/warehouse/product` altında kalır (başka yer için `GEOPROP_DATA_ROOT` ya da `GEOPROP_MENU_DB`). Depo kökünden:

```bash
python3 collector/extension_bot/server.py
```

Chrome'da `chrome://extensions` sayfasını açın, geliştirici modunu etkinleştirin ve **bu klasörü** (`~/Desktop/endeks3/collector/extension_bot`) paketlenmemiş eklenti olarak yükleyin. Eski klasörden (`~/Desktop/GEOPROP/collector/extension_bot`) yüklenmiş eklenti varsa onu kaldırın. Kod güncellendiyse **Yeniden Yükle** düğmesine bir kez basın. Ardından eklenti penceresinden **Taramayı Başlat** seçeneğini kullanın.

Yerel servis durumu:

```bash
curl http://127.0.0.1:5050/status
```

## Veri modeli

- `menu_tarama_kuyrugu`: kalıcı iş/kaldığı yer bilgisi
- `menu_tarama_gozlemleri`: kaynak payload arşivi
- `menu_fiyat_gozlemleri`: append-only fiyat ve sağlayıcı gözlemleri
- `mekan_menu_kalemleri_ve_fiyat_tarihcesi`: aylık kanonik görünüm
- `mekan_menu_gorselleri`: yalnız doğrulanmış menü paneli içindeki görseller

Her fiyat kaydında sağlayıcı, sağlayıcı kanıtı, ham fiyat metni, kaynak URL, yakalama yöntemi ve güven puanı saklanır.

## Dayanıklılık ve sorun giderme

- **Bekçi (watchdog):** İçerik betiği her adımda (sayfa, popüler saatler, menü) "heartbeat" gönderir. Bir mekan 1 dakika boyunca ne sonuç ne heartbeat gönderirse "zaman aşımı" ile kuyruğa geri verilir ve tarama sıradakine geçer. (2.2'de sınır 30 sn'ydi ve popüler saatleri okuyan sağlıklı sayfaları da kesiyordu; bu yüzden başarısız sayılan mekanlar 2.3'e ilk geçişte bir kez yeniden kuyruğa alınır.)
- **Popüler saatler:** Gün sekmelerine tek bir hata ayıklayıcı bağlantısıyla tıklanır; en çok 15 sn ayrılır, süre dolarsa okunan günlerle devam edilir (`mekan_populer_saat_ozeti.gun_sayisi`).
- **Google CAPTCHA:** Bot sekmesi `google.com/sorry/` adresine düşerse tarama durmaz, *duraklar*. Sekmedeki doğrulamayı tamamlayın; Google aynı aramaya geri döner ve tarama kendiliğinden sürer. Eklenti penceresi bu durumu "CAPTCHA bekleniyor" olarak gösterir.
- **Hız / paralel pencere (2.4):** Eklenti penceresinden 1–4 paralel pencere seçilir (varsayılan 2). Her pencere ayrı bir mekan tarar; sunucu her pencereye (`/next?worker=N`) ayrı kiralama verir, aynı mekan iki pencereye verilmez. Pencereler üst üste kaydırılarak açılır; **küçültmeyin ve tamamen örtmeyin** (Chrome gizli pencereleri yavaşlatır). Bir pencerede iki mekan arası rastgele 0,8–2 sn beklenir. Ölçüm: tek pencerede mekan başına ~6 sn (saatte ~500); 2 pencerede yaklaşık iki katı.
- **Otomatik fren:** Herhangi bir pencerede CAPTCHA çıkarsa bütün pencereler durur; doğrulama o pencerede geçilince tarama bir pencere eksik sürer. 30 dk CAPTCHA'sız geçerse pencere sayısı yeniden bir artar (seçilen sayıyı aşmadan).
- **Tekrar kayıtlar:** Google ambarında aynı mekan farklı kimlikle birden çok kez olabilir. Aynı ad + il + ilçe ve ~200 m içindeki kayıtlardan biri taranmışsa diğerleri aranmaz, `excluded` + "Tekrar kayıt: …" nedeniyle işaretlenir (silinmez).
- **Kod güncellendiğinde** `chrome://extensions` sayfasında **Yeniden Yükle**'ye basmak zorunludur; aksi halde eski içerik betiği yeni sayfa adreslerinde çalışmaz ve hiçbir sonuç gelmez. Yeniden yükleme sonrası tarama açıksa kendiliğinden devam eder.
- **Günlükler:** Bot sekmesinde DevTools > Console'da `[GEOPROP]` önekli satırlar adım adım ne olduğunu gösterir (menü düğmesi bulundu mu, kaç fiyat çıktı vb.). Arka plan günlükleri için `chrome://extensions` > eklenti > "Service worker" bağlantısı.
- **Neden başarısız?** `menu_tarama_kuyrugu.son_hata` sütunu artık sayfa başlığı ve ilk 160 karakteri de içerir:
  ```bash
  sqlite3 warehouse/product/restoran_ve_kafe_menuleri.sqlite "SELECT son_hata, COUNT(*) FROM menu_tarama_kuyrugu WHERE son_hata IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
  ```
- **Veritabanı eksik/boş (2.3.1):** Sunucu artık 0 baytlık veya tablosuz veritabanlarında çökmez. Menü veritabanı silinmiş ama `-shm/-wal` kalıntısı varsa sessizce boş veritabanı açmaz, yedekten geri yükleme komutunu yazıp durur (`GEOPROP_MENU_YENI=1` ile bilerek sıfırdan başlatılır). Google mekân ya da nüfus veritabanı okunamazsa mevcut kuyrukla devam eder; kaynağı okunamayan mekanları kuyruktan atmaz. Veri kökü, menü veritabanı gerçekten orada bulunan klasör olarak seçilir (`endeks3/warehouse/product` yalnız görseller için oluşmuşsa seçilmez).
- **Mekan kaynağı:** `warehouse/product/menu_kaynak_google_mekanlari.sqlite` (ya da `GEOPROP_MENU_KAYNAK_DB`) varsa ek kaynak olarak okunur. Farklı ambarlarda aynı mekanın kimliği farklı olabildiğinden yeni mekanlar kuyruktakilerle Google `cid` üzerinden eşleştirilir; taranmış mekan yeniden kuyruğa girmez.
