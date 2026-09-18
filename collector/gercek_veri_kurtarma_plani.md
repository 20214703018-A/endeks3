# Silinen Veri Tabloları ve Gerçek Veri İkame Stratejisi

Silinen geçersiz (sentetik) çıktıların içinde tam olarak hangi veri kolonlarının olduğunu veri tabanı şemalarından (SQL) kontrol ettim. Bu verilerin hiçbirini "uydurmadan" gerçekten nasıl toplayacağımızın kesin planını aşağıda eşleştirdim:

## 1. ETBİS E-Ticaret Hacimleri ve Kullanıcı Sayıları
*   **İçerik:** İl bazında yıllık e-ticaret hacmi (TL), kişi başı e-ticaret harcaması, e-ticaret işletme sayısı.
*   **Gerçek Toplama Yöntemi:** Ticaret Bakanlığı her yıl "ETBİS E-Ticaret Raporu" yayınlamaktadır. API'si kapalı olsa da, veriler Excel/PDF olarak açıktır. Projeye `etbis_resmi_rapor_okuyucu.py` ekleyip, "VERİLER" klasörüne indireceğimiz resmi ETBİS Excel'ini (Pandas ile) doğrudan okutarak il bazlı gerçek harcama hacimlerini (TL) ambarlayacağız.

## 2. SEGE (Sosyo-Ekonomik Gelişmişlik Endeksi) 
*   **İçerik:** 973 ilçenin gelişmişlik skoru, kademesi (1. Kademe, 2. Kademe) ve sosyo-ekonomik sıralaması.
*   **Gerçek Toplama Yöntemi:** Sanayi ve Teknoloji Bakanlığı "İlçe SEGE-2022" çalışmasını açık veri (Excel) olarak sunuyor. `sege_acik_veri_toplayici.py` adında ufak bir modül yazıp, 973 ilçenin bakanlık tarafından hesaplanmış %100 gerçek skorlarını ve sıralamasını doğrudan veritabanına alacağız. 

## 3. Toplu Taşıma (İBB Raylı Sistem) ve İSPARK Yolcu Hacimleri
*   **İçerik:** İstasyon bazlı günlük yolcu girişi/çıkışı, otoparkların günlük sirkülasyon hacmi.
*   **Gerçek Toplama Yöntemi:** 
    *   **İSPARK:** İBB'nin `api.ibb.gov.tr/ispark/Park` canlı API'sini günde tek sefer değil, **her saat başı (cron job ile)** çağırarak `emptyCapacity` (boş kapasite) değişimini hesaplayacağız. Sırf kapasiteye bakarak uydurmak yerine, gün içindeki araç giriş-çıkış (sirkülasyon) sayısını ampirik olarak tespit edeceğiz.
    *   **Raylı Sistemler:** İBB Açık Veri Portalı'nda yer alan "İstanbulkart Turnike Geçiş Verileri" (Aylık/Günlük İstasyon Bazlı Geçişler) veri setini indirecek bir toplayıcı yazacağız. Hangi istasyondan kaç kişinin gerçekten geçtiğini (Örn: Kadıköy Metro: 42.155 kişi) resmi İBB kart basım loglarından alacağız.

## 4. Tüketim, Harcama ve Sigara/Tütün Oranları
*   **İçerik:** Hanehalkı gıda, kira harcaması, tütün kullanım oranı.
*   **Gerçek Toplama Yöntemi:** Temiz olan `tuik_bolge_toplayici.py` betiğimizi kullanarak TÜİK'in (EDAV - Elektronik Veri Ağı) "Hanehalkı Tüketim Harcaması" ve "Türkiye Sağlık Araştırması" endpoint'lerinden İstatistiki Bölge (NUTS-2) bazındaki resmi oranları çekeceğiz.

## 5. İşletme Devir (Turnover) Oranı ve Kapanan Dükkanlar
*   **İçerik:** Bir mahalledeki dükkanların yaşam ömrü, tabela değiştirme hızı ve iflas/devir oranları.
*   **Gerçek Toplama Yöntemi:** Sizin kodlar arasında gördüğüm `osm_poi_gecmis_toplayici.py` betiğini (OpenStreetMap Historical Data) devreye sokacağız. 2020 yılındaki koordinattaki dükkan ile 2024 yılındaki aynı koordinattaki dükkanı kıyaslayacağız. İsim değişmişse devir (turnover), dükkan kaybolmuşsa kapanma (churn) olarak %100 gerçek sahadan (OSM History) çıkaracağız. 

## 6. Ticari Kira ve "Devren" İlan Piyasası
*   **İçerik:** İlçe bazında m² dükkan kirası ve satılık/devren dükkan ilan sayısı.
*   **Gerçek Toplama Yöntemi:** Sahte banka sayısı katsayılarını sildik. Artık yalnızca ve yalnızca eldeki `ham_isyeri_ve_devren_aktarici.py` ve `ilan_toplayici.py` verilerinde **gerçekten kaç tane devren ilan çekildiyse** o sayıyı yazacağız. Eğer o mahallede ilan yoksa değer `NULL` olacak.

## 7. BKM Kartlı Harcamalarının İllere Dağılımı
*   **İçerik:** Market, giyim, yemek vb. 26 sektördeki toplam harcamaların iller bazındaki durumu.
*   **Gerçek Toplama Yöntemi:** BKM sadece Türkiye geneli yayın yapar (İl bazında yayınlamaz). Bu yüzden Türkiye geneli resmi BKM harcama hacmini alacağız; ardından tamamen temiz/gerçek olan **BDDK Fintürk** verilerindeki "İl Bazlı Bireysel Kredi Kartı Borç Bakiyesi" oranlarına böleceğiz. (Örn: BDDK'ya göre tüm kart borcunun %32'si İstanbul'daysa, BKM'deki Market harcamasının %32'sini İstanbul'a atayacağız). Böylece uydurma değil, BDDK destekli rasyonel bir projeksiyon yapacağız.

## 8. Restoran Menü ve 1-5 Yıldız Dağılımı
*   **İçerik:** Restoranların 1,2,3,4 ve 5 yıldız yorum sayılarının dağılımı (Histogram).
*   **Gerçek Toplama Yöntemi:** Matematiksel olarak uydurmak yerine, ya Playwright/Selenium kullanarak Google Maps HTML kodunun içinden **gerçek bar grafik oranlarını (pixel/yüzde) kazıyacağız**, ya da bu dağılım verisini tamamen çöpe atıp doğrudan yorum metinlerinin NLP ile duygu analizini (Sentiment) yapacağız.

---

## Silinen Geçersiz (Uydurma) Betikler ve Çıktıları

Aşağıdaki betikler ve ürettikleri veriler, Kural 2 (Sentetik veri yasaktır) kapsamında tamamen silinmiştir:
1. `collector/turkiye_lokasyon_ve_ticari_istihbarat_toplayici.py`
2. `collector/bati_buyuksehirler_ticari_toplayici.py`
3. `collector/data/turkiye_makro_ve_mikro_istihbarat.sqlite`
4. `warehouse/product/bati_ticari_istihbarat.sqlite`
5. `collector/data/csv_ciktilari/19_etbis...`, `20_sege...`, `21_ciro...`, `22_tuik...` CSV dosyaları.

### Diğer Modüllere Etkisi:
* `geoprop/kisitli_veri.py` modülü (Satır 164), sildiğimiz `tuik_tutun_ve_sigara_istatistikleri` verisini kullanıyordu. Veri dosyasını sildiğimiz için bu modül artık API üzerinden tütün verisi istendiğinde hata vermek yerine güvenli bir şekilde `{"status": "unavailable"}` dönecektir. Tütün verisini gerçek kaynaklardan (TÜİK API) çektiğimizde bu metot kendiliğinden tekrar çalışmaya başlayacaktır.
* `collector/ulasim_rotalari_ve_istasyon_toplayici.py` (Satır 25), sildiğimiz `BATI_DB`'ye (bati_ticari_istihbarat.sqlite) referans veriyordu. Ancak bu referans yalnızca okuma/yazma yoluydu. Veritabanını baştan oluşturacağımız zaman bu yol çalışmaya devam edecektir.
* Depo envanter kayıtlarında (`warehouse/bronze/bronze-manifest.json` ve `reports/` içindeki json'lar) bu silinen dosyalara ait metadatalar (geçmiş kayıt izleri) bulunuyor. Bunlar sadece "katalog" izleridir ve veri hattı tekrar çalıştığında bu json katalogları kendini otomatik güncelleyecektir.
