# GEOPROP: HAM VERİ VE TİCARİ İSTİHBARAT AMBARLARI KULLANIM REHBERİ

Bu doküman, GEOPROP veri madenciliği motoru tarafından toplanan, normalize edilen ve `warehouse/product/` altında arşivlenen ham ve yapılandırılmış veri setlerinin teknik şemalarını, dosya yollarını, satır sayılarını ve analitik sorgu yöntemlerini belgeler.

---

## 1. Veri Ambarları Genel Tablosu

| Ambar Dosyası (`warehouse/product/`) | Tablolar | Kayıt Sayısı | Coğrafi Kapsam | Veri Türü |
|:---|:---|:---|:---|:---|
| `ham_devren_ve_isyeri_ilanlari.sqlite` | `ham_isyeri_ilanlari`<br>`ilce_devren_ve_isyeri_ozet` | 12.108 ilan<br>418 ilçe özeti | 81 İl (10.175 koordinatlı) | Ham emlak ilanları, devren dükkanlar, m² satış/kira fiyatları |
| `ulasim_ve_hareketlilik_gps.sqlite` | `arac_gps_koridor_profili`<br>`arac_gps_saatlik_akis`<br>`yayalastirilmis_ticari_yollar`<br>`yaya_sayim_sensor_istasyonlari`<br>`istasyon_yaya_tahliye_hacmi` | 2.464 arter profili<br>16.840 saatlik akış<br>11.678 yaya aksı<br>2.562 sensör ölçümü<br>14.504 istasyon debisi | 30 Büyükşehir (İstanbul, İzmir, Ankara, Bursa, Antalya, Kocaeli vb.) | 1M Araç Floating Car Data, İzmir arter hızları, 30 Büyükşehir yaya ağları, metro tahliye debileri |
| `bddk_finturk_finansal_gostergeler.sqlite` | `bddk_il_kredi_ve_mevduat`<br>`bddk_il_bireysel_finans`<br>`bddk_il_sektorel_krediler`<br>`bddk_il_sube_ve_likidite` | 972 satır/tablo<br>(12 Çeyrek x 81 İl, Toplam: 3.888) | 81 İl (2023-12'den 2026-6'ya kadar 12 dönem) | Resmî BDDK FİNTÜRK kredi, mevduat, bireysel kredi kartı ve sektörel krediler |
| `kap_perakende_ve_sube_cirolari.sqlite` | `kap_sirket_profili`<br>`kap_mali_tablo_ve_hasilat`<br>`kap_sube_basi_ciro_gostergeleri` | 7 ana çıpa marka<br>29 çeyreklik bilanço<br>7 şube ciro çarpanı | Borsa İstanbul (BIST) Halka Açık Zincirler | Denetlenmiş KAP hasılatı, mağaza sayısı, şube başı yıllık/aylık/günlük ciro |
| `google_places_ve_yogunluk.sqlite` | `google_places_ticari_yogunluk` | 29.175 ticari mekan & AVM<br>(1.13M+ gerçek Google yorumu) | 81 İl, 599 İlçe | Google Maps tersine mühendislik, AVM çıpaları, ulusal zincirler, müşteri hacmi, puan |
| `yemek_ve_market_teslimat_ekosistemi.sqlite` | `teslimat_depolari_darkstore`<br>`uye_restoranlar_ve_hacim`<br>`mahalle_esnaf_noktalari` | 138 darkstore/hub<br>38.663 üye restoran<br>30.581 mahalle esnafı | 81 İl (Tüm Türkiye) | Yemeksepeti Market, Getir, Trendyol Go depoları, restoran mutfakları ve mahalle esnaf ağı |
| `bkm_sektorel_kart_harcama.sqlite` | `bkm_aylik_sektorel_harcama`<br>`bkm_yillik_sektor_ozet` | 1.404 aylık sektör<br>182 yıllık özet | Türkiye Geneli (2022-2026) | 26 sektörde kredi/banka kartı işlem adedi ve tutarı (Milyon TL) |
| `zincir_markalar_ve_finans.sqlite` | `poi_zincir_ve_finans`<br>`marka_ilce_dagilim_ozet` | 27.746 nokta<br>3.842 ilçe özeti | 81 İl, 973 İlçe | Bankalar (5.152), ATM (5.746), Kahve (809), Restoran (1.820), Market (12.190) |
| `turkiye_toplu_tasima_rotalari.sqlite`| `ulasim_hatlari`<br>`ulasim_istasyonlari` | 950 hat rotası<br>14.978 durak | 81 İl Raylı & Deniz Ulaşımı | Metro, Tramvay, Banliyö, Vapur, Metrobüs (LineString GeoJSON) |
| `buyuksehirler_otopark_envanteri.sqlite`| `otopark_noktalari`<br>`ilce_otopark_kapasite_ozet` | 24.713 otopark<br>728 ilçe özeti | 81 İl, Öncelik Büyükşehirler | İSPARK canlı API (247), katlı, kapalı, açık otopark koordinat ve kapasiteleri |
| `kargo_ve_lojistik_noktalari.sqlite` | `kargo_ve_teslimat_noktalari`<br>`ilce_kargo_yogunluk_ozet` | 4.580 nokta<br>812 ilçe özeti | 81 İl | PTT şubeleri, 7/24 Kargomatlar, Yurtiçi, Aras, MNG, Sürat, Trendyol Express |
| `idari_sinirlar.sqlite` | `sinir`<br>`mahalle_koordinat` | 1.086 poligon<br>51.171 mahalle | 81 İl, 973 İlçe, 51K Mahalle | Resmî il/ilçe sınır poligonları, mahalle merkez koordinatları (lat, lon) |
| `bati_ticari_istihbarat.sqlite` | `isletme_turnover_ve_stabilite`<br>`ticari_koridor_ve_aks`<br>`ticari_kira_ve_devren_piyasa`<br>`istasyon_yolcu_akisi`<br>`mikro_ticari_ciro_skoru`<br>`google_places_ticari_yogunluk` | 213 ilçe/koridor<br>213 koridor<br>213 piyasa<br>343 istasyon<br>213 skor<br>29.318 ticari mekan | 9 Batı Büyükşehri (178 İlçe) | 5 yıllık dükkan kapanma ve turnover %, ticari kümelenme, mikro ciro potansiyeli |

---

## 2. Tablo Şemaları ve Alan Tanımları

### 2.1. `ham_devren_ve_isyeri_ilanlari.sqlite`
* **Tablo: `ham_isyeri_ilanlari`**
  - `ilan_no`: Portal üzerindeki tekil ilan numarası (TEXT).
  - `portal`: İlanın alındığı kaynak platform (TEXT - ör: Emlakjet, Sahibinden).
  - `ilan_basligi`: İlanın orijinal başlığı (TEXT - ham veri).
  - `ana_kategori`: Emlak ana kategorisi (TEXT - İşyeri).
  - `emlak_turu`: Dükkan, Mağaza, Ofis, Akaryakıt İstasyonu, Depo (TEXT).
  - `devren_mi`: Başlığında "devren" geçen işletmeler (INTEGER - 1: Devren, 0: Normal).
  - `il`, `ilce`, `mahalle`: İdari konum bilgileri (TEXT).
  - `ada_no`, `parsel_no`: Tapu kadastro ada ve parsel numaraları (TEXT).
  - `fiyat_tl`: İlan bedeli (REAL).
  - `brut_m2`, `net_m2`: Brüt ve net kullanım alanları (REAL).
  - `birim_m2_fiyat_tl`: m² başına düşen birim fiyat (REAL).
  - `lat`, `lon`: İlanın WGS84 coğrafi koordinatları (REAL).
  - `ilan_tarihi`: İlanın yayımlanma tarihi (TEXT - ISO 8601).
  - `ilan_linki`: İlanın doğrudan web adresi (TEXT).

* **Tablo: `ilce_devren_ve_isyeri_ozet`**
  - `il`, `ilce`: İlçe adı.
  - `toplam_isyeri_ilani`: İlçedeki toplam aktif işyeri ilan adedi.
  - `devren_ilan_sayisi`: İlçedeki devren satılık/kiralık ilan adedi.
  - `devren_ilan_orani_yuzde`: `(devren_ilan_sayisi / toplam_isyeri_ilani) * 100`.
  - `ortalama_m2_fiyat_tl`: İlçedeki ortalama işyeri m² birim satış/kira fiyatı.

### 2.2. `bkm_sektorel_kart_harcama.sqlite`
* **Tablo: `bkm_aylik_sektorel_harcama`**
  - `yil`, `ay`, `donem`: Harcamanın yapıldığı zaman dilimi (ör: `2024-06`).
  - `sektor_adi`: BKM'nin 26 resmî sektörü (MARKET VE ALIŞVERİŞ MERKEZLERİ, YEMEK, GİYİM VE AKSESUAR, BENZİN VE YAKIT İSTASYONLARI, ELEKTRİK-ELEKTRONİK EŞYA vb.).
  - `kredi_karti_islem_adedi`, `banka_karti_islem_adedi`, `toplam_islem_adedi`: İşlem sayıları.
  - `kredi_karti_tutar_milyon_tl`, `banka_karti_tutar_milyon_tl`: Harcama tutarları.
  - `toplam_tutar_milyon_tl`: Sektörün ilgili aydaki toplam cirosu.

* **Tablo: `bkm_yillik_sektor_ozet`**
  - `yil`, `sektor_adi`, `yillik_toplam_islem_adedi`, `yillik_toplam_tutar_milyon_tl`, `sektor_payi_yuzde`: Sektörün tüm Türkiye harcamasındaki yüzdelik payı.

### 2.3. `zincir_markalar_ve_finans.sqlite`
* **Tablo: `poi_zincir_ve_finans`**
  - `kategori`: `BANKA`, `ATM`, `KAHVE_ZINCIRI`, `RESTORAN_ZINCIRI`, `ZINCIR_MARKET`, `PERAKENDE_MAGAZA`.
  - `marka`: Kurumsal marka adı (Starbucks, McDonald's, Ziraat, İş Bankası, BİM, Migros vb.).
  - `sube_adi`: Şube veya ATM adı.
  - `lat`, `lon`: Şubenin GPS koordinatları.
  - `il`, `ilce`, `mahalle`: Konum bilgileri.
  - `osm_id`: OpenStreetMap nesne ID'si.

### 2.4. `turkiye_toplu_tasima_rotalari.sqlite`
* **Tablo: `ulasim_hatlari`**
  - `hat_adi`, `hat_kodu`, `tur` (`subway`, `tram`, `train`, `ferry`, `light_rail`).
  - `operator`: İşletmeci kuruluş (Metro İstanbul, ESHOT, BURULAŞ vb.).
  - `durak_sayisi`: Hatta bulunan istasyon sayısı.
  - `toplam_uzunluk_km`: Hattın toplam rota uzunluğu.
  - `rota_geojson`: Rota çizgisinin GeoJSON `LineString` formatında tam koordinat dizisi.

* **Tablo: `ulasim_istasyonlari`**
  - `hat_adi`, `tur`, `sira_no`, `istasyon_adi`, `lat`, `lon`, `il`, `ilce`.

### 2.5. `buyuksehirler_otopark_envanteri.sqlite`
* **Tablo: `otopark_noktalari`**
  - `otopark_adi`, `operator` (İSPARK, İZELMAN, BURBAK, Belediye / Özel).
  - `tur`: `KAPALI OTOPARK`, `AÇIK OTOPARK`, `YOL ÜSTÜ`, `KATLI OTOPARK`.
  - `kapasite`: Toplam araç kapasitesi.
  - `bos_kapasite`: Canlı boş yer sayısı (İSPARK için).
  - `lat`, `lon`, `il`, `ilce`.

### 2.6. `kargo_ve_lojistik_noktalari.sqlite`
* **Tablo: `kargo_ve_teslimat_noktalari`**
  - `tip`: `PTT_SUBE`, `PTT_KARGOMAT`, `OZEL_KARGO_SUBE`.
  - `marka`: PTT, Yurtiçi Kargo, Aras Kargo, MNG Kargo, Sürat Kargo, Trendyol Express.
  - `sube_adi`, `adres_acik`, `telefon`, `lat`, `lon`, `il`, `ilce`.

### 2.7. `google_places_ve_yogunluk.sqlite`
* **Tablo: `google_places_ticari_yogunluk`**
  - `google_place_id`: Google Haritalar benzersiz yer kimliği (`ChIJ...`).
  - `cid`: Hex formatlı müşteri/yer kimliği.
  - `isim`: İşletme veya mekanın resmî tabelası.
  - `ana_kategori` & `tum_kategoriler`: Google kategori sınıflandırması (JSON dizi).
  - `puan`: Müşteri memnuniyet skoru (1.0 - 5.0).
  - `yorum_sayisi`: Toplam Google değerlendirme hacmi (işletmenin kümülatif müşteri hacmi ve ciro vekili).
  - `lat`, `lon`: Hassas WGS84 koordinatları.
  - `tam_adres`, `mahalle`, `ilce`, `il`: İdari lokasyon hiyerarşisi.
  - `calisma_saatleri`: 7 günlük çalışma saatleri ve gün bazlı zaman çizelgesi.
  - `kaynak`: `Google Maps (Reverse Engineered)`.

### 2.8. `yemek_ve_market_teslimat_ekosistemi.sqlite`
* **Tablo: `teslimat_depolari_darkstore`**
  - `platform`: `YEMEKSEPETI_MARKET`, `GETIR`, `TRENDYOL_GO`, `MIGROS_HEMEN`.
  - `depo_kodu`, `depo_adi`: Dağıtım merkezinin resmî adı.
  - `tam_adres`, `sehir`, `ilce`, `mahalle`: Depo açık lokasyon bilgisi.
  - `lat`, `lon`: Hassas depo koordinatları.
  - `kaynak`: `Resmî Platform Sitemap & JSON-LD / OSM`.
* **Tablo: `uye_restoranlar_ve_hacim`**
  - `platform`: `YEMEKSEPETI`.
  - `restoran_adi`, `mutfaklar` (JSON dizi), `fiyat_segmenti` (₺, ₺₺, ₺₺₺).
  - `puan` (1.0 - 5.0), `degerlendirme_sayisi` (Gerçekleşen sipariş ve yorum hacmi vekili).
### 2.9. `ulasim_ve_hareketlilik_gps.sqlite`
* **Tablo: `arac_gps_koridor_profili`** (İBB 1M+ Araç Floating Car Data)
  - `geohash`: 6 karakterli coğrafi ızgara kimliği (~1.2 km x 0.6 km).
  - `lat`, `lon`: Koridor merkez koordinatları.
  - `gunluk_ortalama_arac`: Günlük ortalama geçen reel araç debisi.
  - `zirve_saat_arac`: Pik saatte ölçülen maksimum araç akışı.
  - `ortalama_akinti_hizi`: Arterdeki ortalama akış hızı (km/s).
  - `sıkısiklik_orani_yuzde`: Hızın 30 km/s altına düştüğü saat oranı (%).
  - `trafik_kategorisi`: `Ana Arter / Çok Yoğun Ticari Görünürlük`, `İkincil Arter`, `Bağlantı Yolu`.
* **Tablo: `yayalastirilmis_ticari_yollar`**
  - `il`, `ilce`, `yol_adi`: Yayalaştırılmış cadde/sokak/kordon adı.
  - `durum`: `YAYALAŞTIRILMIŞ`, `KISMEN YAYALAŞTIRMA`, `YAYA ÖNCELİKLİ`.
  - `yol_turu`: Cadde, Sokak, Gezinti Yolu / Kordon.
  - `geometri_geojson`: Resmî cadde çizgi geometrisi (LineString).
  - `lat`, `lon`: Merkez nokta koordinatları.
  - `kaynak`: İBB Açık Veri Portalı & OpenStreetMap Overpass (Antalya, İzmir, Bursa, Muğla).
* **Tablo: `yaya_sayim_sensor_istasyonlari`**
  - `istasyon_adi`: Mavişehir, Alsancak, Pasaport, Konak, Göztepe, Bostanlı, Turan.
  - `lat`, `lon`: Sensör direği coğrafi koordinatları.
  - `tarih`: Ölçüm günü (YYYY-AA-GG).
  - `gunluk_yaya_giris`, `gunluk_yaya_cikis`, `gunluk_toplam_yaya`: Sensör optik sayım debisi.
* **Tablo: `istasyon_yaya_tahliye_hacmi`**
  - `istasyon_adi`, `hat_adi`, `ilce`: Metro, banliyö veya tramvay istasyonu.
  - `lat`, `lon`: İstasyon çıkış koordinatı.
  - `tarih`: Günlük zaman damgası.
  - `gunluk_yolcu_giris_cikis`, `yolcu_sayisi`: Çevredeki ticari koridorlara boşalan reel yaya akışı.

### 2.10. `bddk_finturk_finansal_gostergeler.sqlite`
* **Tablo: `bddk_il_kredi_ve_mevduat`**
  - `il`, `donem` (ör: `2024-12`), `yil`, `ay`.
  - `toplam_nakdi_kredi_bin_tl`, `nakdi_kredi_bin_tl`: İl geneli ticari ve bireysel krediler.
  - `takipteki_alacaklar_bin_tl`: NPL / Sorunlu alacaklar.
  - `gayrinakdi_krediler_bin_tl`: Teminat mektupları vb.
  - `tasarruf_mevduati_tl_bin_tl`, `tasarruf_mevduati_dth_bin_tl`: TL ve Döviz tasarruf hacmi.
  - `toplam_mevduat_bin_tl`: İldeki toplam bankacılık mevduat gücü.
* **Tablo: `bddk_il_bireysel_finans`**
  - `bireysel_kredi_karti_bin_tl`: İldeki toplam bireysel kredi kartı harcama/borç büyüklüğü.
  - `konut_kredisi_bin_tl`, `tasit_kredisi_bin_tl`, `kredili_mevduat_hesabi_bin_tl`.
* **Tablo: `bddk_il_sektorel_krediler`**
  - `turizm_kredisi_bin_tl`: Turizm sektörü yatırımları ve işletme sermayesi.
  - `toptan_ticaret_kredisi_bin_tl`, `insaat_kredisi_bin_tl`, `gida_mesrubat_kredisi_bin_tl`.
* **Tablo: `bddk_il_sube_ve_likidite`**
  - `sube_sayisi`: İldeki toplam banka şubesi.
  - `subeye_dusen_nufus`: Şube başına düşen nüfus.
  - `kisi_basi_tasarruf_mevduati_tl`, `kisi_basi_toplam_mevduat_tl`: İlin kişi başı likidite gücü.

### 2.11. `kap_perakende_ve_sube_cirolari.sqlite`
* **Tablo: `kap_sirket_profili`**
  - `hisse_kodu`, `sirket_unvani`, `marka`, `sektor`, `perakende_formati`, `denetim_kurulusu`.
* **Tablo: `kap_mali_tablo_ve_hasilat`**
  - `hisse_kodu`, `donem`, `yil`, `donem_tipi`: Çeyreklik ve yıllık denetlenmiş mali tablo.
  - `hasilat_bin_tl`, `hasilat_milyon_tl`: Resmî satış hasılatı.
  - `magaza_sayisi`: Faaliyet raporu mağaza adedi.
* **Tablo: `kap_sube_basi_ciro_gostergeleri`**
  - `hisse_kodu`, `marka`, `sektor`, `magaza_sayisi`, `yillik_hasilat_milyon_tl`.
  - `sube_basi_yillik_ciro_tl`: Bir mağazanın yıllık ortalama cirosu (TL).
  - `sube_basi_aylik_ciro_tl`: Bir mağazanın aylık ortalama cirosu (TL).
  - `sube_basi_gunluk_ciro_tl`: Bir mağazanın günlük ortalama cirosu (TL).

---

## 3. SQL Analitik Sorgu ve Çapraz Birleştirme Şablonları

### 3.1. Bir İlçedeki Çıpa Marka Yoğunluğu ve Bankacılık Altyapısını Çıkarma
```sql
SELECT 
    il, ilce,
    COUNT(CASE WHEN kategori = 'BANKA' THEN 1 END) AS banka_sayisi,
    COUNT(CASE WHEN kategori = 'ATM' THEN 1 END) AS atm_sayisi,
    COUNT(CASE WHEN kategori = 'KAHVE_ZINCIRI' THEN 1 END) AS kahve_zinciri_sayisi,
    COUNT(CASE WHEN kategori = 'RESTORAN_ZINCIRI' THEN 1 END) AS zincir_restoran_sayisi,
    COUNT(CASE WHEN kategori = 'ZINCIR_MARKET' THEN 1 END) AS market_sayisi
FROM poi_zincir_ve_finans
WHERE il = 'İstanbul' AND ilce = 'Kadıköy'
GROUP BY il, ilce;
```

### 3.2. Devren İlan Oranı Yüksek Olan (Baskı Altındaki) İlçeleri Tespit Etme
```sql
SELECT 
    il, ilce, toplam_isyeri_ilani, devren_ilan_sayisi, devren_ilan_orani_yuzde, ortalama_m2_fiyat_tl
FROM ilce_devren_ve_isyeri_ozet
WHERE toplam_isyeri_ilani >= 20
ORDER BY devren_ilan_orani_yuzde DESC
LIMIT 15;
```

### 3.3. Toplu Taşıma İstasyonlarına 500 Metre Mesafedeki Otopark ve Kargo Noktalarını Bulma
*(Haversine mesafe formülü ile)*
```sql
SELECT 
    i.istasyon_adi, i.hat_adi, o.otopark_adi, o.kapasite,
    ROUND(6371 * 2 * ASIN(SQRT(
        POWER(SIN(RADIANS(o.lat - i.lat) / 2), 2) +
        COS(RADIANS(i.lat)) * COS(RADIANS(o.lat)) *
        POWER(SIN(RADIANS(o.lon - i.lon) / 2), 2)
    )) * 1000) AS mesafe_metre
FROM ulasim_istasyonlari i
JOIN otopark_noktalari o 
  ON o.lat BETWEEN i.lat - 0.005 AND i.lat + 0.005
 AND o.lon BETWEEN i.lon - 0.005 AND i.lon + 0.005
WHERE i.il = 'İstanbul' AND i.istasyon_adi LIKE '%Kadıköy%'
ORDER BY mesafe_metre ASC;
```

### 3.4. BKM Sektörel Kart Harcamalarında En Hızlı Büyüyen Sektörleri Bulma
```sql
SELECT 
    sektor_adi,
    SUM(CASE WHEN yil = 2023 THEN toplam_tutar_milyon_tl ELSE 0 END) AS ciro_2023,
    SUM(CASE WHEN yil = 2024 THEN toplam_tutar_milyon_tl ELSE 0 END) AS ciro_2024,
    ROUND(((SUM(CASE WHEN yil = 2024 THEN toplam_tutar_milyon_tl ELSE 0 END) - 
            SUM(CASE WHEN yil = 2023 THEN toplam_tutar_milyon_tl ELSE 0 END)) / 
            NULLIF(SUM(CASE WHEN yil = 2023 THEN toplam_tutar_milyon_tl ELSE 0 END), 0)) * 100, 1) AS yillik_artis_yuzde
FROM bkm_aylik_sektorel_harcama
GROUP BY sektor_adi
ORDER BY yillik_artis_yuzde DESC;
```

### 3.5. Google Haritalar Müşteri Hacmi (Yorum Sayısı) ve Memnuniyet Sıralaması
```sql
SELECT 
    isim, ana_kategori, puan, yorum_sayisi, il, ilce, lat, lon
FROM google_places_ticari_yogunluk
WHERE yorum_sayisi IS NOT NULL
ORDER BY yorum_sayisi DESC
LIMIT 15;
```

### 3.6. İlçe Bazında Darkstore (Hızlı Market Deposu) Yoğunluğu ve Dağılımı
```sql
SELECT 
    COALESCE(sehir, 'Bilinmeyen İl') AS sehir,
    COALESCE(ilce, 'Merkez/Tüm') AS ilce,
    COUNT(*) AS darkstore_sayisi,
    GROUP_CONCAT(DISTINCT platform) AS platformlar
FROM teslimat_depolari_darkstore
GROUP BY sehir, ilce
ORDER BY darkstore_sayisi DESC;
```

### 3.7. Araç GPS Trafik Yoğunluğu ve Ticari Görünürlük Analizi (İBB Floating Car Data)
```sql
SELECT 
    geohash, lat, lon, gunluk_ortalama_arac, zirve_saat_arac,
    ortalama_akinti_hizi, sıkısiklik_orani_yuzde, trafik_kategorisi
FROM arac_gps_koridor_profili
WHERE gunluk_ortalama_arac >= 5000
ORDER BY gunluk_ortalama_arac DESC
LIMIT 15;
```

### 3.8. Yayalaştırılmış Ticari Koridorlar ve Metro Yaya Tahliyesi
```sql
SELECT 
    y.il, y.yol_adi, y.durum, y.yol_turu, y.lat, y.lon,
    (SELECT SUM(i.yolcu_sayisi) 
     FROM istasyon_yaya_tahliye_hacmi i 
     WHERE i.lat BETWEEN y.lat - 0.004 AND y.lat + 0.004
       AND i.lon BETWEEN y.lon - 0.004 AND y.lon + 0.004) AS cevre_istasyon_yaya_debisi
FROM yayalastirilmis_ticari_yollar y
WHERE y.il = 'İstanbul' AND y.durum = 'YAYALAŞTIRILMIŞ'
LIMIT 15;
```

### 3.9. BDDK FİNTÜRK İl Bazı Kredi Kartı ve Turizm Finansmanı
```sql
SELECT 
    k.il,
    ROUND(k.toplam_nakdi_kredi_bin_tl / 1000000.0, 2) AS nakdi_kredi_milyar_tl,
    ROUND(k.toplam_mevduat_bin_tl / 1000000.0, 2) AS mevduat_milyar_tl,
    ROUND(b.bireysel_kredi_karti_bin_tl / 1000000.0, 2) AS kart_harcamasi_milyar_tl,
    ROUND(s.turizm_kredisi_bin_tl / 1000000.0, 2) AS turizm_kredisi_milyar_tl,
    l.sube_sayisi,
    ROUND(l.kisi_basi_tasarruf_mevduati_tl, 0) AS kisi_basi_tasarruf_tl
FROM bddk_il_kredi_ve_mevduat k
JOIN bddk_il_bireysel_finans b ON k.il = b.il AND k.donem = b.donem
JOIN bddk_il_sektorel_krediler s ON k.il = s.il AND k.donem = s.donem
JOIN bddk_il_sube_ve_likidite l ON k.il = l.il AND k.donem = l.donem
WHERE k.donem = '2024-12'
ORDER BY k.toplam_nakdi_kredi_bin_tl DESC
LIMIT 10;
```

### 3.10. KAP Çıpa Perakende Şube Başına Ciro Kıyaslaması (Ampirik Benchmark)
```sql
SELECT 
    marka, sektor, magaza_sayisi, yillik_hasilat_milyon_tl,
    ROUND(sube_basi_aylik_ciro_tl, 0) AS aylik_ortalama_sube_ciro_tl,
    ROUND(sube_basi_gunluk_ciro_tl, 0) AS gunluk_ortalama_sube_ciro_tl
FROM kap_sube_basi_ciro_gostergeleri
ORDER BY sube_basi_aylik_ciro_tl DESC;
```

