# GEOPROP - 30 Büyükşehir & Mahalle Ölçekli Kusursuz Veri Madenciliği Planı (Güncellenmiş)

İstediğiniz tüm eksiksiz veri derinliğini sisteme nasıl entegre edeceğimizin kesin planı aşağıdadır. Hiçbir veriyi sentezlemiyoruz; önce %100 ham veri olarak çekiyor, ardından analiz ediyoruz.

## 1. Teslimat Ekosistemi (Yemeksepeti, Getir, Trendyol GO)
*   **Kapsam:** Sadece restoranlar DEĞİL. Sistemdeki **Market, Manav, Kasap, Petshop, Su, Çiçekçi ve Kozmetik** dâhil her sekme eksiksiz çekilecek.
*   **Detay:** Her işletmenin "Minimum Sepet Tutarı", "Gönderim Ücreti", "Teslimat Süresi", "Kurye/Hız Puanı" ve **tüm menü kalemleri/fiyatları** toplanacak.
*   **Duygu Analizi (Sentiment):** Restoran/Marketin son yorumları çekilerek NLP ile duygu analizine sokulacak (Örn: "Ürünler taze", "Kurye gecikti").

## 2. Fiziki Restoran Menüleri (Teslimat Fiyatlarından Bağımsız)
*   Yemeksepeti/Getir fiyatlarında komisyon kaynaklı enflasyon olduğu için (örn: fiziki 100 TL, pakette 140 TL), fiziki fiyatları **ayrı bir botla** çekeceğiz.
*   **Kaynaklar:** İşletmelerin resmi web siteleri, Instagram menü linkleri, Google menü sekmeleri ve restoran içi Fiziki QR Menü servis sağlayıcıları (FineDine, QrMenu vb.).
*   İşletmenin fiziki menü fiyatları ile paket servis fiyatları arasındaki uçurum "Komisyon/Baskı Marjı" endeksi olarak saklanacak.
*   **Duygu Analizi:** Google Places yorumları da ayrı bir NLP taramasından geçirilecek.

## 3. Dark Store ve Lojistik Tesisleri (Tüm Markalar)
Elimizdeki kargo toplayıcısı (sadece PTT'yi alan) devasa bir lojistik botuna dönüştürülecek:
*   **Hızlı Ticaret (Dark Stores):** Getir Depoları, Yemeksepeti Market depoları, Trendyol GO Hub'ları, İstegelsin.
*   **Kargo ve Teslimat:** Aras, Yurtiçi, MNG, Sürat şubelerinin yanı sıra **Trendyol Gel-Al Noktaları, Amazon Teslimat Dolapları (Lockers), HepsiJET dolapları**. 
*   **Kargo Rotaları:** Lojistik firmalarının mahalleye geliş sıklıkları ve dağıtım rotaları.

## 4. Zincir Markalar (Yerel, Ulusal ve Uluslararası)
`zincir_marka_ve_finans_toplayici.py` dosyamız aktif, ancak bunu genişleteceğiz:
*   Sadece banka veya kahve zincirleri değil; Giyim, Süpermarket (BİM, A101, Şok, Migros), Fast Food, Teknoloji, Kozmetik mağazalarının tamamının (Türkiye çapındaki tüm şubeleri) lokasyonları, çalışma saatleri ve özellikleri eksiksiz listelenecek.

## 5. Google Places & Yoğunluk (Tek Seferde)
*   Google taraması parça parça yapılmayacak. 
*   30 Büyükşehirin 16.000 mahallesi için grid taraması yapılacak. Arama esnasında mekana ait **İletişim, Saatler, Canlı Yoğunluk (Popular Times), Yorumlar ve Fiziki Menü** tek bir API isteğiyle/botla eşzamanlı olarak ambarlanacak.

## 6. Ulaşım, Yaya, GPS ve Raylı Sistemler
Elimizdeki ulaşım betiklerine (İBB Trafik, Yaya Rotaları) ek olarak:
*   **Geçiş ve Kullanım Oranları:** İBB Açık Veri Portalı (veya ESHOT, EGO vb.) üzerinden Raylı Sistem, Metro, Metrobüs, Otobüs istasyonlarının **Günlük Turnike Kullanım Oranları** (kaç kişi bindi/indi) resmi veriden çekilecek.
*   Yaya hareketliliği (GPS yoğunluğu) günün 4 ayrı saat diliminde çekilerek "Gündüz/Gece Popülasyon Farkı" (Sirkülasyon) hesaplanacak.

## 7. ETBİS ve SEGE (Saf ve Ham Veri)
Sildiğimiz "sentezlenmiş/uydurulmuş" versiyonlar yerine:
*   **ETBİS (E-Ticaret):** Sadece Ticaret Bakanlığı raporlarındaki ve Emlakjet Demografi API'sindeki *ham haliyle* indirilecek. 
*   **SEGE (Gelişmişlik):** Sanayi Bakanlığı raporu Excel'den *ham veri* olarak çekilip hiçbir formüle sokulmadan veritabanına konulacak.
*   *(Daha sonrasında gerekirse başka bir katmanda çaprazlama yaparız, ancak ambarımızda veri %100 ham ve gerçek kalacak).*
