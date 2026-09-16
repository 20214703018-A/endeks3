# GEOPROP VERİ MADENCİLİĞİ VE ARŞİVLEME KURALLARI (RULES)

Bu proje genelinde çalışan tüm yapay zeka ajanları ve veri toplayıcılar aşağıdaki temel kurallara eksiksiz uymak zorundadır:

1. **Yorumlama Yapma, Sadece Veri Topla ve Arşivle**: Ajan sadece verinin en derin, en kapsamlı şekilde toplanması, temizlenmesi, normalize edilmesi ve depolanmasından sorumludur. Subjektif yorum veya özet katılmaz.
2. **Verinin Doğru Yerden Alındığına Emin Ol**: Veriler kesinlikle sentetik, mock veya varsayılan (fallback) değerler olamaz. Resmî API'ler, açık veri portalları, yerel ambarlar (warehouse) ve doğrulanmış veri setleri kullanılmalıdır.
3. **Veriyi Her Yolla Alabilirsin, Sınır Yok**: Web scraping, açık veri API'leri, Overpass/OSM, yerel SQLite/DuckDB ambarları, CSV/Parquet dökümleri, tersine mühendislik uç noktaları serbestçe kullanılır.
4. **Veriyi Düzgün Şekilde Dosyalara Yerleştir**: Bütün veriler açık, standart şemalara (SQLite, GeoJSON, Parquet, CSV) sahip ambar tablolarına kurallı biçimde yazılır.
5. **Veri Çıktısında Eksik Olursa Tamamla**: Bir kaynakta eksik bir alan varsa (örneğin koordinat, ilçe, kategori), diğer coğrafi ve ambar tablolarıyla cross-check yapılarak tamamlanır, asla boş veya uydurma bırakılmaz.
6. **Verinin Bir Yorum İçin Yeterli Olduğundan Emin Ol**: Toplanan veri; derinlik, sütun çeşitliliği ve örneklem büyüklüğü açısından üçüncü tarafların her türlü ticari, konut ve gayrimenkul analizini yapabilmesine yetecek ayrıntıda olmalıdır.
7. **Mümkün Olduğunca Çok Veri Çıkar**: Örneklem kısıtlaması konulmaz. Mümkün olan en yüksek satır ve kapsama hacmi hedeflenir.
8. **Lokal ve GitHub Actions Çift Motorlu Mimari**: Uzun süren büyük veri çekme işlemleri GitHub Actions matrisinde (40 runner) paralel koşturulur, lokalde ambar birleştirmesi (merge) ve doğrudan madencilik yapılır.
9. **Koordinat, Geo ve Tarih Zorunluluğu**: Kaydedilen her ticari/fiziki kaydın enlem (`lat`), boylam (`lon`), varsa sınır poligonu (`geometri`) ve kayıt/çekim zaman damgası (`guncellenme_tarihi` ISO 8601) eksiksiz yazılır.
10. **Kapsam**: 81 il genelinde makro ve mikro ambar verileri; kısıtlı, yüksek çözünürlüklü ve zor verilerde (ulaşım, otopark, devren, işletme turnover, yaya trafiği) öncelikli hedef Batı Büyükşehirleridir (İstanbul, İzmir, Bursa, Antalya, Kocaeli, Muğla, Tekirdağ, Balıkesir, Aydın).
