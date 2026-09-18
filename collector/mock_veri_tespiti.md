# Sentetik (Mock) ve Varsayılan Veri Üreten Betikler Raporu (Güncellendi)

İncelemelerim sonucunda, Kural 2'yi ("Kesinlikle sentetik, mock veya varsayılan (fallback) değerler olamaz") açıkça ihlal eden dosyalar:

## 1. `turkiye_lokasyon_ve_ticari_istihbarat_toplayici.py` (Tamamen Sentetik)
Bu dosya tamamen **sentetik veri üretmek üzerine** tasarlanmış. 
- Plaka koduna göre matematiksel işlemler (`p % 7`, `p % 11`) yaparak sahte e-ticaret hacimleri (ETBİS tablosu) üretiyor.
- Rastgele hesaplamalarla sahte SEGE gelişmişlik skorları üretiyor.
- Huff Gravity Modeli diyerek ciro ve çekim potansiyelini sahte formüllerle uyduruyor.

## 2. `restoran_menu_ve_fiyat_toplayici.py` (Kritik Sentetik İhlal!)
Menü toplayıcı betiğinde gözünüzden kaçan çok ciddi bir matematiksel uydurma fonksiyonu buldum:
- `calculate_star_distribution(puan, deg_sayisi)` fonksiyonu, gelen `puan` ortalamasına ve `deg_sayisi`'na (yorum sayısı) bakarak **kaç kişinin 1, 2, 3, 4 ve 5 yıldız verdiğini matematiksel bir katsayı (`w5`, `w4`, `rem`) formülü ile tamamen uyduruyor!** Üstelik puan yoksa `4.1` olarak (fallback) kabul edip buna göre yıldız dağılımı üretiyor. (Satır 174-190)

## 3. `bati_buyuksehirler_ticari_toplayici.py`
- **İBB ve İzmir API:** Yolcu hacimlerini gerçek API'den çekmek yerine koda gömülü sabit listelerden (`45000, 42000, 87000`) dolduruyor.
- **Fallback (Varsayılan) Değerler:** Devir/turnover hesaplanamadığında `%8.5` atıyor. E-ticaret hacmi okunamadığında `50.0` atıyor.
- **Uydurma İlanlar:** Emlak ilan verisi bulamazsa, o bölgedeki banka sayısını 3 ile çarparak sahte ilan sayısı üretiyor.

## 4. `bkm_sektorel_kart_toplayici.py`
Bu dosya aslında BKM'nin resmi sitesinden Türkiye geneli gerçek verileri (gerçek sektörel hacimler ve işlem adetleri) çekiyor. Ancak bu Türkiye geneli veriyi kaydederken ciddi bir ihlal yapıyor:
- Elde ettiği Türkiye geneli rakamını (örneğin 2024-06 ayındaki yüzdelik dilimleri) sanki ilin kendi rakamıymış gibi ("İstanbul", "İzmir", "Bursa" vs.) `bati_ticari_istihbarat.sqlite` ambarına yazıyor. Ayrıca yıllık büyüme oranını da tüm iller için sabit `18.4` (Satır 218) olarak kodun içine gömmüş. 

## 5. `cografi_ve_altyapi_motoru.py`
- Yükseklik API isteği başarısız olursa, hesaplamaları sürdürmek için o koordinatı dümdüz bir ova gibi kabul edip `[100.0] * 9` şeklinde bir yükseklik matrisi atıyor.

## 6. `gercek_uydu_haritasi_olusturucu.py` 
- Eğer harita fotoğrafı indirilemezse siyah/koyu bir kare (`Image.new(...)`) üretiyor. Görselleştirme amaçlı olsa da teknik olarak fallback'tir.

---

### Temiz Gözüken Betikler:
- **`bddk_finturk_toplayici.py`**: Bu dosya tamamen temiz. Fintürk verilerini doğrudan okuyor, formüllere veya uydurma katsayılara başvurmuyor.
- **`kap_perakende_ciro_toplayici.py`**: Temiz, bilançoları API'den gerçek verilerle okuyor.
- **`restoran_mahalle_birlestirici.py`**: Eski sentetik menü verilerini tespit edip siliyor/karantinaya alıyor, dolayısıyla kurallara çok uygun çalışıyor.
