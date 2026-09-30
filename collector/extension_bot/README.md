# GEOPROP Menü Toplayıcı 2.0

Bu Chrome eklentisi mevcut Google Places ambarındaki uygun restoranları kalıcı bir SQLite kuyruğundan işler. Her mekan için `mekan adı + adres` Google Search sorgusunu açar ve bilgi panelindeki **Menü** düğmesine basar; Maps koordinat araması kullanmaz. İş ilerlemesi tarayıcı veya servis yeniden başlasa da korunur.

## Kapsam kuralları

- İller: Antalya, Bursa, Ankara, Konya, İzmir, İstanbul, Aydın, Çanakkale, Diyarbakır, Trabzon
- En az 10 Google değerlendirmesi
- İlçe nüfusu en az 25.000
- Mahalle nüfusu en az 3.000
- Köy/belde kayıtları kapsam dışı
- Yalnız `Google Maps...` kaynaklı, benzersiz `google_place_id` kayıtları
- `Istanbul`/`İstanbul` gibi shard yazım farkları kanonik il adına dönüştürülür
- İnternet kafe, PlayStation/e-spor ve oyun salonu gibi yemek dışı `Kafe` kayıtları kapsam dışıdır

Nüfus eşleşmesi olmayan kayıtlar varsayımla tamamlanmaz; kapsam dışında bırakılır.

## Çalıştırma

Proje kökünden:

```bash
python3 collector/extension_bot/server.py
```

Chrome'da `chrome://extensions` sayfasını açın, geliştirici modunu etkinleştirin ve bu klasörü paketlenmemiş eklenti olarak yükleyin. Kod güncellendiyse **Yeniden Yükle** düğmesine bir kez basın. Ardından eklenti penceresinden **Taramayı Başlat** seçeneğini kullanın.

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

- **Bekçi (watchdog):** Bir mekanın sayfası 1,5 dakika içinde sonuç ya da hata bildirmezse mekan "zaman aşımı" ile kuyruğa geri verilir ve tarama sıradakine geçer. Tarama artık tek bir sayfada sessizce takılı kalmaz.
- **Google CAPTCHA:** Bot sekmesi `google.com/sorry/` adresine düşerse tarama durmaz, *duraklar*. Sekmedeki doğrulamayı tamamlayın; Google aynı aramaya geri döner ve tarama kendiliğinden sürer. Eklenti penceresi bu durumu "CAPTCHA bekleniyor" olarak gösterir.
- **Hız:** İki mekan arasında rastgele 3–6,5 sn beklenir (CAPTCHA riskini azaltmak için).
- **Kod güncellendiğinde** `chrome://extensions` sayfasında **Yeniden Yükle**'ye basmak zorunludur; aksi halde eski içerik betiği yeni sayfa adreslerinde çalışmaz ve hiçbir sonuç gelmez. Yeniden yükleme sonrası tarama açıksa kendiliğinden devam eder.
- **Günlükler:** Bot sekmesinde DevTools > Console'da `[GEOPROP]` önekli satırlar adım adım ne olduğunu gösterir (menü düğmesi bulundu mu, kaç fiyat çıktı vb.). Arka plan günlükleri için `chrome://extensions` > eklenti > "Service worker" bağlantısı.
- **Neden başarısız?** `menu_tarama_kuyrugu.son_hata` sütunu artık sayfa başlığı ve ilk 160 karakteri de içerir:
  ```bash
  sqlite3 warehouse/product/restoran_ve_kafe_menuleri.sqlite "SELECT son_hata, COUNT(*) FROM menu_tarama_kuyrugu WHERE son_hata IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
  ```
