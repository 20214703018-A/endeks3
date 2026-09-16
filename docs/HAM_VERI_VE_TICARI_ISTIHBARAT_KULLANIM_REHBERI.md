# GEOPROP: HAM VERİ VE TİCARİ İSTİHBARAT AMBARLARI KULLANIM REHBERİ

Bu doküman, GEOPROP veri madenciliği motoru tarafından toplanan, normalize edilen ve `warehouse/product/` altında arşivlenen ham ve yapılandırılmış veri setlerinin teknik şemalarını, dosya yollarını, satır sayılarını ve analitik sorgu yöntemlerini belgeler.

---

## 1. Veri Ambarları Genel Tablosu

| Ambar Dosyası (`warehouse/product/`) | Tablolar | Kayıt Sayısı | Coğrafi Kapsam | Veri Türü |
|:---|:---|:---|:---|:---|
| `ham_devren_ve_isyeri_ilanlari.sqlite` | `ham_isyeri_ilanlari`<br>`ilce_devren_ve_isyeri_ozet` | 12.108 ilan<br>418 ilçe özeti | 81 İl (10.175 koordinatlı) | Ham emlak ilanları, devren dükkanlar, m² satış/kira fiyatları |
| `bkm_sektorel_kart_harcama.sqlite` | `bkm_aylik_sektorel_harcama`<br>`bkm_yillik_sektor_ozet` | 1.404 aylık sektör<br>182 yıllık özet | Türkiye Geneli (2022-2026) | 26 sektörde kredi/banka kartı işlem adedi ve tutarı (Milyon TL) |
| `zincir_markalar_ve_finans.sqlite` | `poi_zincir_ve_finans`<br>`marka_ilce_dagilim_ozet` | 27.746 nokta<br>3.842 ilçe özeti | 81 İl, 973 İlçe | Bankalar (5.152), ATM (5.746), Kahve (809), Restoran (1.820), Market (12.190) |
| `turkiye_toplu_tasima_rotalari.sqlite`| `ulasim_hatlari`<br>`ulasim_istasyonlari` | 950 hat rotası<br>14.978 durak | 81 İl Raylı & Deniz Ulaşımı | Metro, Tramvay, Banliyö, Vapur, Metrobüs (LineString GeoJSON) |
| `buyuksehirler_otopark_envanteri.sqlite`| `otopark_noktalari`<br>`ilce_otopark_kapasite_ozet` | 24.713 otopark<br>728 ilçe özeti | 81 İl, Öncelik Büyükşehirler | İSPARK canlı API (247), katlı, kapalı, açık otopark koordinat ve kapasiteleri |
| `kargo_ve_lojistik_noktalari.sqlite` | `kargo_ve_teslimat_noktalari`<br>`ilce_kargo_yogunluk_ozet` | 4.580 nokta<br>812 ilçe özeti | 81 İl | PTT şubeleri, 7/24 Kargomatlar, Yurtiçi, Aras, MNG, Sürat, Trendyol Express |
| `idari_sinirlar.sqlite` | `sinir`<br>`mahalle_koordinat` | 1.086 poligon<br>51.171 mahalle | 81 İl, 973 İlçe, 51K Mahalle | Resmî il/ilçe sınır poligonları, mahalle merkez koordinatları (lat, lon) |
| `bati_ticari_istihbarat.sqlite` | `isletme_turnover_ve_stabilite`<br>`ticari_koridor_ve_aks`<br>`ticari_kira_ve_devren_piyasa`<br>`istasyon_yolcu_akisi`<br>`mikro_ticari_ciro_skoru` | 213 ilçe/koridor<br>213 koridor<br>213 piyasa<br>343 istasyon<br>213 skor | 9 Batı Büyükşehri (178 İlçe) | 5 yıllık dükkan kapanma ve turnover %, ticari kümelenme, mikro ciro potansiyeli |

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
  - `tam_adres`, `sehir`, `ilce`, `lat`, `lon`.

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
