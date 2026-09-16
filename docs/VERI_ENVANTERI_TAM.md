# GEOPROP — Tam Veri Envanteri (2026-09-15)

Bu belge repodaki **her veri setini** tek tek listeler: nerede duruyor (dosya/tablo), ne içeriyor (sütunlar, satır sayısı, kapsam,
dönem), nereden geldi (kaynak + toplayıcı betiği), hangi motor okuyor, arayüzde hangi kartta görünüyor, nasıl tazelenir, lisans/gizlilik
sınıfı ve dürüstlük notu. Ambar dosyaları `warehouse/` altındadır ve **git'e girmez** (`.gitignore`); yalnız toplayıcılar ve motorlar
repodadır. Boyutlar ve satır sayıları 2026-09-15 durumudur.

Veri sınıfları: **açık** (herkes), **kısıtlı** (paket × sorgu tipi × admin; `geoprop/erisim_politikasi.py`), **PII** (asla API'de dönmez).
Tüm ürün tablolarında `source_url` boştur (kaynak linki dışarı sızmaz); `*_norm` sütunları `geoprop/veri_yardimcilari.normalize_name`
ile indeksli ad eşleşmesi içindir.

---

## 0) Dizin yapısı

| Yol | İçerik |
|---|---|
| `warehouse/product/*.sqlite` | Ürün ambarı (açık sınıf) — motorlar salt-okunur açar (`file:…?mode=ro`) |
| `warehouse/restricted/*.sqlite` | Kısıtlı + PII (dosya izni 0600) |
| `warehouse/raw/<kaynak>/` | Ham indirilen dosyalar (xls/csv/pdf/pbf); yeniden işleme için tutulur |
| `warehouse/bronze/`, `silver/`, `gold/` | İlan/istatistik boru hattının eski ara katmanları (bronze ham JSON/CSV/sqlite; silver alan bazlı; gold DuckDB) — `collector/collector.py`, `derle_demo_verisi.py` |
| `collector/` | Toplayıcılar (her kaynak için ayrı betik) |
| `geoprop/` | Motorlar; `land_analysis.py::LandAnalysisEngine.analyze()` hepsini birleştirir |
| `demo/server.py` (8088) + `demo/alici_arsa_analiz.{html,js,css}` | API + alıcı arsa analizi arayüzü |
| `.github/workflows/` | Periyodik toplayıcılar (OSM haftalık, MEB 40-makine, TKGM, imar, ilan) |

API çıktısı (`POST /api/v1/arsa/analiz`) anahtarları: `market_index, market_index_history, area_price_segments, declared_zoning,
transaction_density, geographic_context, parcel_shape, city_expansion, district_development, neighborhood_building, investment_profile,
bolge_profili, zemin_jeoloji, arazi, yakin_noktalar, okullar, yurtlar, universiteler, pazarlar, tuik_bolge, hava_kalitesi, resmi_gazete,
deprem_senaryo, gurultu, trafik_saatlik, afet_vekili, comparable_statistics, valuation, timings_ms`. Uydu değişimi ayrı uç noktadan
(`GET /api/v1/uydu/degisim?lat&lon`), kısıtlı setler `POST /api/v1/bolge/kisitli`.

---

## 1) Piyasa ve ilan verisi

### 1.1 `warehouse/product/arsa_emsalleri.sqlite` (1.054 MB) — açık
| Tablo | Satır | Sütunlar (özet) | Not |
|---|---|---|---|
| `ilanlar` | 63.082 | listing_key, provider, kategori, tip, il/ilce/mahalle, fiyat_tl, m2, birim_m2_fiyat, ilan_tarihi, enlem/boylam, ada_no, parsel_no, imar_durumu (ilan beyanı), kaks_emsal, quality_score | Arsa/tarla ilanları; satıcı telefon/ad **yok** (PII ayrı) |
| `arsa_mahalle_ozet` | 49.776 | il/ilce/mahalle, donem, satilik_m2_fiyat, min/max, fiyat_endeksi, aylık/yıllık değişim, ilan_sayisi, ilanda_kalma_suresi_gun, stok değişimi | Mahalle özet (Emlakjet id şeması: city_id/county_id/district_id) |
| `arsa_mahalle_trend` | 2.715.520 | mahalle × ay fiyat/endeks serisi | "Mahalle endeks geçmişi" grafiği |
| `arsa_alan_segmentleri` | 2.851 | ilçe × alan bandı (m²) fiyat/ilan | "Alan segmenti" kartı |
Kaynak: ilan portalları (bronze→silver→gold hattı; `collector/collector.py`, `ham_json_donusturucu.py`, `derle_demo_verisi.py`). Motor: `LandAnalysisEngine._load_land_listings/_load_market_index/_load_area_segments`. Kart: endeks aralığı, mahalle endeksi, emsal listesi (3 km).

### 1.2 `warehouse/product/emlakjet_bolge_endeks.sqlite` (11 MB) — açık
`bolge_endeks` 40.068: tip (konut/arsa/işyeri), seviye (il/ilçe/mahalle), dönem, m2_fiyat, aylık/yıllık değişim, amortisman, getiri, ort_bina_yasi, kira_m2_fiyat… Kaynak: Emlakjet bölge endeksi (`collector/emlakjet_bolge_endeks_toplayici.py`). Motor: market index geçmişi.

### 1.3 `warehouse/product/mahalle_yapilasma.sqlite` (3 MB) — açık
`mahalle_yapilasma_ozet` 9.523: mahalle konut ilanlarından ort. brüt m², medyan net m², ort/maks kat, oda dağılımı. Motor: `_load_neighborhood_building` (kart: mahalle yapılaşma).

### 1.4 `warehouse/product/tkgm_alim_satim.sqlite` (573 MB) — açık
| Tablo | Satır | İçerik |
|---|---|---|
| `tkgm_alim_satim_yogunlugu` | 6.941.129 | analiz_tip, yil, parsel_id, il_id (TKGM il id — Emlakjet id'den **farklı**), enlem, boylam, sayi (işlem) |
| `tkgm_analiz_kapsama` | 484 | il × yıl × analiz tipi nokta/işlem sayısı, çekilme tarihi |
Kaynak: TKGM alım-satım yoğunluk servisi (`collector/tkgm_alim_satim_yogunlugu_toplayici.py`, 40-makine workflow). Motor: `_load_transaction_density` (covering bbox indeksi, 14 ms). Kart: "Satış yoğunluğu".

### 1.5 Canlı (ambara yazılmaz)
- **TKGM parsel** (ada/parsel/koordinat → parsel poligonu, alan, nitelik): `geoprop/live_parcel_gateway.py`; `parcel_evidence`.
- **E-Plan imar** (KAKS/TAKS/fonksiyon; en iyi çaba): `declared_zoning`; önbellek fikri ertelendi (bkz. `docs/KULLANILMAYAN_VERI_ENVANTERI.md §5`).

---

## 2) Bölge istatistikleri — `warehouse/product/bolge_istatistik.sqlite` (445 MB) — açık
Kaynak: bölge istihbarat paketi (bronze zip'ler; `collector/bolge_istatistik_toplayici.py`). Anahtar: Emlakjet `city_id/county_id/district_id`; mahalle eşleşmesi `(county_id, mahalle_norm)`.

| Tablo | Satır | İçerik |
|---|---|---|
| `demografi` | 20.797 | il/ilçe/mahalle: nüfus (toplam/E/K), hane sayısı, ort. hane geliri, ev sahibi/kiracı oranı, SES A+/A/B/C/D sayı ve oran, eğitim oranları, yaş grupları, konut/kira/arsa/tarla/ticari m² fiyatı, eczane/ATM/banka/araç sayısı |
| `yillik_satis` | 310.051 | bölge × yıl: toplam/ipotekli konut satışı, arsa-arazi satışı, ilan sayısı |
| `fiyat_ozet` | 23.523 | kategori × seviye × dönem: satılık/kiralık m², amortisman, brüt getiri, ort. bina yaşı, kalma süresi |
| `aylik_fiyat_trendi` | 1.529.982 | kategori × bölge × ay serisi (+projeksiyon) |
| `dagilim_kirilim` | 116.754 | oda sayısı / bina yaşı / kat vb. dağılım oranları |
| `poi_ilce` | 501.783 | ilçe POI listesi (koordinatsız), `poi_ilce_ozet` 28.432 ilçe×alt kategori sayısı |
| `insaat_sirketi` 1.451, `emlak_ofisi` 1.620 | ofis adı/adres/danışman-ilan sayısı (danışman kişi verisi PII'de) |
| `eticaret_harcama` 446, `medeni_stok` 446, `yas_piramidi` 446 | il/ilçe düzeyi harcama kalemleri, medeni durum, konut/ticari stok, 5'er yaş piramidi |
| `ref_il` 81, `ref_ilce` 951, `ref_mahalle` 36.582 | referans idari listeler (Emlakjet id) |
Motor: `geoprop/bolge_istatistik.py::RegionStatsEngine.profil` → `bolge_profili` (5 kart: demografi, SES, satış, harcama, POI özeti).

---

## 3) Kısıtlı ve kişisel veriler — `warehouse/restricted/` (0600)
| Dosya | Tablo | Satır | Sınıf | Erişim |
|---|---|---|---|---|
| `kisitli_istatistik.sqlite` (109 MB) | `secim_sonuclari` | 299.743 | kısıtlı | seçim kodu/adı, sandık, seçmen, kazanan parti (mahalle) — paket×sorgu tipi×admin |
| | `hemsehri_kutuk` | 185.092 | kısıtlı | bölge × kütük ili kişi sayısı |
| `kisisel_veriler.sqlite` | `danisman` 1.620, `iletisim` 1.640 | PII | API'de asla dönmez; yalnız iç eşleştirme/admin |
| `erisim_denetim.sqlite` | `erisim_denetim` | 12 | denetim | her kısıtlı okuma kaydı (zaman, kullanıcı, paket, sorgu tipi, izin) |
Politika: `geoprop/erisim_politikasi.py` (`KISITLI_SETLER`, `kisitli_erisim`), motor `geoprop/kisitli_veri.py`, uç nokta `POST /api/v1/bolge/kisitli` (`GEOPROP_ADMIN_TOKEN`, demo paket başlığı yalnız `GEOPROP_DEMO_KIMLIK=1`). Fiyatlamaya girmez.

---

## 4) Coğrafya, zemin, arazi

### 4.1 `warehouse/product/cografi_katmanlar.sqlite` (810 MB) — açık
`cografi_ozellikler` 580.432 (id, katman, ad, alt_tur, sektor, geom_tip, rep_lat/lon, geometry GeoJSON, ozellikler) + `cografi_kapsama`. Katmanlar: SU_YOLU_DERE 182.355 · MEVCUT_YOL 176.369 · ORMAN_ALANI 101.839 · GOL_BARAJ_HAZNE 35.902 · DEMIRYOLU 23.647 · PLANLANAN_YOL_PROJESI 16.093 · ELEKTRIK_HATTI 13.092 · CESME_ICME_SUYU 11.218 · SAHIL_SERIDI 9.607 · SIT_VE_KORUNAN_ALAN 4.405 · DOGAL_PINAR 2.809 · DİRİ_FAY_HATTI 1.379 · SU_KUYUSU 1.007 · SU_BORU_HATTI 710.
Kaynak: Türkiye vektör çıkarımı (`collector/cografi_ve_orman_toplayici.py`, workflow). Motor: `geoprop/geographic_context.py` (katman başına yarıçap: fay 20 km, demiryolu/planlanan yol 5,1 km, su 2,1 km, dere 1,2 km, hat/orman/sit/sahil 60 m) → `geographic_context.findings` + `en_yakin_dere_m`, `en_yakin_gol_baraj_m`. Kart: "Coğrafi uyarılar".

### 4.2 `warehouse/product/idari_sinirlar.sqlite` (12 MB) — açık
`sinir` 1.086: seviye (il/ilce), ad, il_adi, merkez lat/lon, bbox, geometri (GeoJSON), **alan_km2**. Kaynak: OSM admin_level 4/6 (`collector/idari_sinir_toplayici.py`). Motor: `geoprop/idari.py::AdminLookup.lookup(lat, lon) → (il, ilçe)`; kampüs ilçesi, hal/OSB ilçesi, TÜİK il/ilçe, yoğunluk oranı (ilçe alanı) bunu kullanır.

### 4.3 Canlı zemin/arazi (önbelleksiz)
- **MTA jeolojik birim**: `geoprop/jeoloji.py` — WMS `mtayenicbs-geoserver.mta.gov.tr/geoserver/mta/wms`, katman `mta:PORTALFORM` (1/500.000); birim kodu/simge/açıklama/yaş, Kuvaterner bayrağı, 3 km kırpılmış poligon. `GEOPROP_JEOLOJI_CANLI=0` kapatır. Kart "Zemin ve jeolojik birim".
- **DEM eğim/bakı/rakım**: `geoprop/arazi.py` — Terrarium (AWS) 30 m sınıfı, 90 m parsel / 300 m çevre pencereleri. Kart "Arazi ve eğim (3D)" (MapLibre). EEA-10 (10 m) CDSE CCM 403 — bekliyor.
- **Sentinel-2 uydu değişimi**: `geoprop/uydu.py` — CDSE STAC + `/vsis3/eodata` pencere okuma (B04/B08/B11/SCL), 1 km NDVI/NDBI → yapılı/bitki payı, şimdi vs 1 yıl / 3 yıl. Önbellek `warehouse/product/uydu_onbellek.sqlite::uydu_hucre` (0,01° hücre, 30 gün). Uç nokta `GET /api/v1/uydu/degisim`. Kimlik `.env` CDSE_S3_*. Kart "Uydu değişimi".
- **Afet vekilleri**: `geoprop/afet_vekili.py` — DEM + dere mesafesi + jeolojiden taşkın/heyelan sınıfı (resmî değil). Kart "Taşkın ve heyelan (vekil gösterge)".

---

## 5) OSM tabanlı yakın çevre

### 5.1 `warehouse/product/osm_poi.sqlite` (204 MB) — açık (ODbL, atıf zorunlu)
| Tablo | Satır | İçerik |
|---|---|---|
| `poi` | 623.793 | osm_tip/osm_id, kategori, alt_kategori, marka (zincir), ad, ad_norm, lat/lon, etiketler (JSON), geometri (seçili kategoriler: üniversite, OSB, sanayi sitesi/alanı, stadyum, park, AVM, hastane, otogar, liman, havalimanı), alan_m2 |
| `hat` 2.996 / `hat_durak` 69.851 | otobüs/metro/tramvay hatları ve durak sırası |
| `kapsama` 64 / `meta` 5 | kategori sayımları; pbf_mtime, islenme |
Kategoriler (poi sayısı): altyapi 185.961 (baz_istasyonu 8.513, trafo 4.322, yuksek_gerilim_diregi 173.126) · ulasim 82.486 · alisveris 80.380 (market, mağaza, fırın, tekel, AVM, pazar, hal, toptancı) · ibadet 47.698 (cami 46.5K) · spor 46.825 · yeme_icme 38.578 · saglik 30.046 · kultur 26.366 · egitim 20.141 · is 19.197 (ofis, zanaat) · kamu 15.863 · sanayi 13.984 · konaklama 10.298 · hizmet 5.970.
Kaynak: Geofabrik `turkey-latest.osm.pbf` (`warehouse/raw/osm/`, 646 MB) → `collector/osm_poi_toplayici.py` (pyosmium; KATEGORILER, MARKALAR, `"*"` etiket-var kuralı). Tazeleme: `.github/workflows/osm_poi_haftalik.yml` (pazartesi). Motor: `geoprop/yakin_noktalar.py::NearbyPoiEngine` → `yakin_noktalar` (hedefler, 500m/1km/3km sayım, hatlar, zincirler, OSRM yol mesafesi, resmî liste birleştirme, yoğunluk oranı, geçmiş). Kart "Yakın önemli noktalar".

### 5.2 `warehouse/product/osm_degisim.sqlite` (664 MB) — açık
| Tablo | Satır | İçerik |
|---|---|---|
| `poi_yillik` | 2.844.718 | kesit (2021/22/23/24/25-01-01 + 2026-09-13) × POI (alt_kategori, marka, ad, ad_norm, lat/lon) |
| `poi_yasam` | 680.481 | nesne başına ilk_gorulme, son_gorulme, durum (aktif 623.793 / kaldırıldı 56.688), kesit_sayisi, ad_degisim, marka_degisim, tasinma, gecmis (JSON) — kapsayan indeks |
| `kesit` | 6 | kesit tarihleri ve POI sayıları |
| `poi_kimlik` | 623.793 | son haftalık kesit kimlik kümesi (haftalık fark tabanı) |
| `poi_olay` | 0 (henüz) | haftalık eklendi/silindi/taşındı olayları |
| `ilce_sayim` | 26.832 | kesit × il/ilçe × alt_kategori POI sayısı (yoğunluk oranı paydası) |
| `anlik` | 1 | haftalık kesit özeti |
Kaynak: Geofabrik **public** yıllık kesitleri (`collector/osm_poi_gecmis_toplayici.py`, indirilip işlenip silinir) + haftalık fark (`collector/osm_poi_degisim_toplayici.py`). "Internal" (kullanıcı ID'li) dosyalar kullanılmaz. Motor: `NearbyPoiEngine._gecmis / _degisim / _yogunluk`. Kartta: 5 yıl eklenen/kaldırılan grafikleri, ad değiştirenler, işletme yoğunluğu / ilçe ortalaması.

---

## 6) Resmî listeler (koordinatlı)

### 6.1 `warehouse/product/resmi_egitim.sqlite` (21 MB) — açık
| Tablo | Satır | İçerik |
|---|---|---|
| `okul` | 55.115 | kurum_kodu, il/ilçe, ad, tur (anaokulu, ilkokul, ortaokul, imam hatip, anadolu/fen/sosyal/meslek lisesi, bilsem, özel eğitim…), host, lat/lon (54.870), koordinat_kaynagi (harita.php / adres / OSM / Nominatim "yaklaşık"), derslik/ogretmen/ogrenci (54.075); **telefon/adres sütunları boş (toplanmıyor — karar)** |
| `lgs_taban` | 3.154 | LGS 2026 taban puanı (ilk/nakil), ulusal sıra/yüzdelik, il/tür sırası, kontenjan; kurum_kodu eşleşmesi |
Kaynak: meb.gov.tr okul listesi, `<host>.meb.k12.tr/tema/harita.php`, okul ana sayfası; e-okul LGS (yalnız Türkiye IP). Toplayıcı `collector/meb_okul_toplayici.py` (+ `.github/workflows/meb_okul_40_makine.yml`, güvenilmez). Motor `geoprop/okullar.py`. Kart "Okullar ve eğitim kalitesi".

### 6.2 `warehouse/product/yurtlar.sqlite` — açık
`yurt` 2.323 (kaynak kyk/ozel, il/ilçe, ad, tip, kapasite + kiz/erkek, kapasite_kaynagi, lat/lon 2.307, koordinat_kaynagi) · `kykyurt_com` 839 (resmî olmayan yardımcı kaynak, etiketli). Kaynak: GSB KYK Ajax + özel barınma portalı + KYGM 2021-22 kapasite PDF. Toplayıcı `collector/yurt_toplayici.py`. Motor `geoprop/yurtlar.py`. Kart "Öğrenci yurtları".

### 6.3 `warehouse/product/universite.sqlite` (3 MB) — açık
| Tablo | Satır | İçerik |
|---|---|---|
| `universite` | 205 | YÖK öğrenci sayıları 13 grup × E/K/T (önlisans/lisans/YL/Dr × örgün/ikinci/uzaktan/açık), aof_toplam, uzaktan_toplam, kampus_toplam (AÖF hariç) |
| `universite_ilce` | 858 | YÖK Tablo 102 (2025-26) il/ilçe kırılımı (kullanıcının verdiği PDF) |
| `kampus` | 963 | OSM kampüs poligon/nokta, üniversite eşleşmesi, ilçe (idari), ilçe öğrenci, bina_parcasi |
| `birim` 8.035, `ilce_merkez` 1.082, `urap` 198 (URAP 2025-26 genel sıralama) | |
Toplayıcılar: `collector/urap_toplayici.py` (URAP). **Eksik:** `universite`, `universite_ilce`, `birim`, `kampus`, `ilce_merkez` tabloları oturum içi geçici betiklerle üretildi (YÖK portal Excel'leri + T102 PDF `pypdf` layout çözümü + OSM kampüs eşleşmesi); kalıcı `collector/universite_toplayici.py` henüz yazılmadı → yeniden üretim için ilk iş. Motor `geoprop/universiteler.py`. Kart "Üniversite kampüsleri".

### 6.4 `warehouse/product/pazarlar.sqlite` — açık
`pazar` 4.174 (kaynak: hks 2.198 / ibb_acik_veri 457 / izbb_acik_veri 186 / osm 1.333; mahalle, ad, günler, kapalı, tip, lat/lon) · `hks_pazar` 2.742 (HKS kayıt listesi + adres kodlaması). Toplayıcılar `collector/pazar_toplayici.py`, `collector/hks_pazar_toplayici.py`. Ham: `warehouse/raw/pazar/`. Motor `geoprop/pazarlar.py`. Kart "Semt pazarları".

### 6.5 `warehouse/product/onemli_tesisler.sqlite` — açık
| Tablo | Satır | Kaynak / toplayıcı | Koordinat |
|---|---|---|---|
| `hal` | 177 | HKS hal.gov.tr — `collector/hks_hal_toplayici.py` | 128 (OSM eşleşme, adres, şube ilçe merkezi; etiketli) |
| `osb` | 419 | OSBÜK — `collector/osb_toplayici.py` (tür, durum, alan ha, parsel) | 367 (OSM poligon 238 + geocode) |
| `hastane_ozel` | 572 | SB SHGM faal özel hastane — `collector/sb_hastane_toplayici.py` | 460 |
| `liman` | 193 | UAB ISPS liman tesisleri xls 02.08.2022 (`warehouse/raw/liman/`) — `collector/uab_liman_toplayici.py` | 182 |
Motor: `NearbyPoiEngine` `RESMI_LISTELER` (OSM adayıyla 400 m / ad eşleşmesi; liman 60 m). Kartta "resmî" rozeti.

### 6.6 Ortak geocoder
`collector/geocode.py` — Photon → Nominatim, ≤1 istek/sn, **tek süreç**, idari ad doğrulaması; etiketler "sokak/mahalle/köy merkezi (yaklaşık)", "ad eşleşmesi (yaklaşık)".

---

## 7) TÜİK bölge dinamikleri — `warehouse/product/tuik_bolge.sqlite` (42 MB) — açık
Ham: `warehouse/raw/tuik/` (xls + MEDAS pivot CSV). Toplayıcı `collector/tuik_bolge_toplayici.py`. Motor `geoprop/tuik_bolge.py::TuikRegionEngine` → `tuik_bolge`. Kart "Bölge dinamikleri (TÜİK)".

| Tablo | Satır | İçerik | Dönem |
|---|---|---|---|
| `nufus_ilce` | 18.344 | ADNKS ilçe nüfusu (tuik_kodu) | 2007–2025 |
| `nufus_mahalle` | 32.254 | mahalle (belediye) nüfusu | 2025 |
| `nufus_koy` | 18.183 | köy nüfusu | 2025 |
| `yapi_izin_ilce` | 31.552 | ilçe × yıl ruhsat / kullanma izni daire sayısı (Binalar) | 2010–2025 |
| `yapi_ruhsat_amac_ilce` | 140.436 | ilçe × yıl × kullanım amacı yüzölçümü m² (11 konut; 121 otel, 122 ofis, 123 ticaret, 124 ulaşım, 125 sanayi/depo, 126 kamu/eğitim/sağlık, 127 diğer; 12 konut dışı toplam) | 2010–2025 |
| `yapi_izin_il` | 13.284 | il × yıl/çeyrek daire | 2010–2026-II |
| `konut_satis_ilce` | 146.496 | ilçe × ay konut satışı | 2013-01 → 2026-07 |
| `goc_il` | 1.377 | il aldığı/verdiği/net göç, net göç hızı ‰ | 2007-08 → 2023-24 |
| `ses_ilce` | 1.054 | Sosyoekonomik Seviye skoru + A/B/C/D/E payları (il + ilçe) | 2023 |
| `hanehalki_il` 81, `nufus_projeksiyon_il` 81 | hanehalkı; 2023→2030 projeksiyon | |
Not: mahalle düzeyinde ruhsat verisi TÜİK'te yok (ilçe en ince düzey).

---

## 8) Çevre ve risk — `warehouse/product/cevre.sqlite` (31 MB) — açık
| Tablo | Satır | Kaynak / toplayıcı | Motor / kart |
|---|---|---|---|
| `hava_istasyon` 338, `hava_gunluk` 122.733 | ÇŞB SİM UHKİA (günlük PM10/PM2.5/SO2/NO2, son 365 gün) — `collector/sim_hava_kalitesi_toplayici.py` | `EnvironmentEngine.hava` → `hava_kalitesi`; kart "Hava kalitesi" |
| `trafik_saatlik` 117.725 | İBB Saatlik Trafik Yoğunluk (geohash-6 × hafta içi/sonu × saat: ort araç, ort hız; 2024-02…2025-01) — `collector/ibb_trafik_yogunluk_toplayici.py` (akış, ham diske yazılmaz) | `EnvironmentEngine.trafik` → `trafik_saatlik`; kart "Saatlik yoğunluk (İBB trafik)" — yalnız İstanbul |
| `gurultu_hat` 12.825 (+R-tree) | İBB Stratejik Gürültü Haritası 2024, 55/65/75 dB eş-gürültü **çizgileri** Lgündüz/Lgece — `collector/ibb_gurultu_toplayici.py` | `EnvironmentEngine.gurultu` → `gurultu`; kart "Deprem senaryosu ve gürültü (İBB)" |
| `deprem_senaryo_mahalle` 959 | İBB 7,5 Mw gece senaryosu + 2017 bina stoku (`warehouse/raw/ibb/`) — `collector/ibb_deprem_toplayici.py` | `EnvironmentEngine.deprem_senaryo` → `deprem_senaryo`; aynı kart |

### `warehouse/product/resmi_gazete.sqlite` — açık
`karar` 310 (tarih, RG sayı, karar no, başlık, kategori [kentsel_donusum, sit_koruma, turizm_bolgesi, sanayi_bolgesi, rayli_sistem, yol, enerji_hatti, universite, liman_havalimani, maden_ruhsat, su_yapisi, kamulastirma_diger], islem [acele_kamulastirma/kamulastirma], OCR metni) · `karar_yer` 583 (il/ilçe/mahalle; kaynak metin/harita) · `taranan_gun` 401 (2025-08-15 → 2026-09-14). Ham PDF'ler `warehouse/raw/resmi_gazete/` (399 MB). Toplayıcı `collector/resmi_gazete_toplayici.py` (fihrist + pymupdf/tesseract OCR; `--yer-yenile`). Motor `EnvironmentEngine.resmi_gazete` → `resmi_gazete`; kart "Resmî Gazete kararları". Ada/parsel listeleri saklanmaz (OCR güvenilmez).

---

## 9) Ham dosyalar (`warehouse/raw/`)
| Klasör | İçerik |
|---|---|
| `osm/` | `turkey-latest.osm.pbf` (646 MB, 2026-09-13) |
| `tuik/` | yapi_izin_il_daire.xls, goc_il.xls, sosyoekonomik_il_ilce_2023.xls, hanehalki_il.xls, nufus_artis_il.xls, medas_*.csv (ilçe nüfus, mahalle/köy nüfus, yapı izin, kullanım amacı ×4, konut satış ×4), `_decode.py`, `_savecsv.py` |
| `resmi_gazete/` | 308 karar PDF'i |
| `pazar/` | İBB balıkçı olan/olmayan pazar xlsx, İzmir semt pazarı xlsx |
| `ibb/` | deprem_senaryosu.csv, mahalle_bina_2017.csv (gürültü GeoJSON'ları işlendikten sonra silindi) |
| `liman/` | safeports02082022.xls |
| `urap/` | urap_turkiye_2025_2026.pdf |

---

## 10) Bilinen boşluklar ve dürüstlük notları
- Kamu hastanesi resmî listesi (SB 404) → OSM; Diyanet cami listesi yok → OSM (%50 kapsam); güncel liman listesi yalnız JPG (2022 xls).
- Mahalle bazlı ruhsat/arz verisi yok (TÜİK ilçe); rayiç bedel belediye başına ayrı sistem; mikrobölgeleme/sıvılaşma açık veride yok; DSİ taşkın / AFAD heyelan resmî haritaları TUCBS API anahtarı istiyor → vekil göstergeler.
- Saatlik kişi yoğunluğu (GPS) açık kaynakta yok → İBB araç trafiği vekil; İstanbul dışı yok.
- OSM "ilk görülme ≠ açılış", "kaldırılma ≠ kapanış"; koordinatı adres kodlamasıyla bulunan kayıtlar "yaklaşık" etiketli; RG karar yerleri ilçe/mahalle düzeyi.
- 30 m DEM parsel içi eğimi ölçmez; Sentinel-2 1 km pencere çevre göstergesidir.
- Yeniden üretilebilirlik açığı: `universite.sqlite` toplayıcısı repoda yok (yukarıda); `bolge_istatistik.sqlite` bronze zip'lerden `collector/bolge_istatistik_toplayici.py` ile.

## 11) Tazeleme takvimi (öneri)
| Set | Sıklık | Komut / workflow |
|---|---|---|
| OSM POI + haftalık fark | haftalık | `osm_poi_haftalik.yml` → `osm_poi_toplayici.py` → `osm_poi_degisim_toplayici.py` |
| OSM yıllık kesit | yılda 1 (Ocak) | `osm_poi_gecmis_toplayici.py --yillar <yıl>` (ham kesit silinmez) |
| TÜİK | yıllık (ADNKS Şubat, yapı izin çeyreklik, konut satış aylık) | tarayıcıdan MEDAS CSV + `tuik_bolge_toplayici.py` |
| SİM hava | aylık | `sim_hava_kalitesi_toplayici.py --gun 365` |
| Resmî Gazete | günlük/haftalık | `resmi_gazete_toplayici.py --gun 7` |
| MEB / LGS | yıllık (Eylül) | `meb_okul_toplayici.py` (LGS yerelden) |
| Yurt, üniversite, URAP, hal, OSB, hastane, liman, pazar | yıllık | ilgili toplayıcı |
| TKGM alım-satım | yıllık | `tkgm_alim_satim_toplayici.yml` |
| İBB trafik/gürültü/deprem | İBB yayınladıkça | `ibb_*_toplayici.py` |
