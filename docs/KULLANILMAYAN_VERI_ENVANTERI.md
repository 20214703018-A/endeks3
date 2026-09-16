# GEOPROP — Kullanılmayan Veri Envanteri

Tarih: 2026-09-12 (güncelleme: aynı gün, akşam). Amaç: elimizde OLAN ama arayüzde/analizde HENÜZ kullanılmayan tüm veriyi listelemek ve ürün vizyonundaki maddelerle eşlemek.

## 0) 2026-09-12 akşam güncellemesi — ambara ALINANLAR
`collector/bolge_istatistik_toplayici.py` (skill: `geoprop-veri-mimarisi`) ile ulusal + 22 bölge/il CSV paketi ve 13 POI zip'i sınıflandırılarak işlendi:

| Sınıf | DB | Tablo | Satır | Kullanım |
|---|---|---|---|---|
| açık | warehouse/product/bolge_istatistik.sqlite | demografi | 20.797 (81/81 il, 951 ilçe, 19.765 mahalle) | "Nüfus ve sosyoekonomi" kartı |
| açık | " | yillik_satis | 310.051 | "Yıllık tapu satışları" kartı |
| açık | " | fiyat_ozet | 23.523 | "Konut ve arsa piyasası özeti" kartı |
| açık | " | aylik_fiyat_trendi | 1.529.982 | (yüklü; konut endeks geçmişi için hazır, kart yok) |
| açık | " | dagilim_kirilim | 116.754 | "Konut stoku kırılımı" kartı |
| açık | " | poi_ilce / poi_ilce_ozet | **501.783** / 28.432 | "İlçe donanımı (POI)" kartı — koordinatsız, ilçe sayımı; 90 ilçe 2.000 kayıt kaynak sınırında (alt sınır olarak işaretli) |
| açık | " | yas_piramidi, medeni_stok, eticaret_harcama | 446'şar (yalnız 6 m0X ili) | demografi kartı ek satırları |
| açık | " | emlak_ofisi, insaat_sirketi | 1.620 / 1.451 | henüz kart yok (ofis/danışman verisi şablon görünümlü: 20 slug × 76 il) |
| açık | " | ref_il / ref_ilce / ref_mahalle | 81 / 951 / 36.582 | isim→id çözümleme hub'ı (`ad_norm` indeksli) |
| kısıtlı | warehouse/restricted/kisitli_istatistik.sqlite (0600) | secim_sonuclari | 299.743 | `RestrictedDataEngine` → yalnız uye_kurumsal/admin × dukkan_analiz/bolge_raporu |
| kısıtlı | " | hemsehri_kutuk | 185.092 | aynı kural |
| kısıtlı | collector/data/…istihbarat.sqlite | tuik_tutun_ve_sigara | 8 | uye_pro+/admin × dukkan_analiz; `/api/bolge-istihbarat`tan kaldırıldı |
| PII | warehouse/restricted/kisisel_veriler.sqlite (0600) | iletisim (danışman adı/telefon) | ~580K | yalnız admin × admin_eslestirme |

Mimari notlar: mahalle satırlarında `district_id` ilçe içi **yerel sıra** olduğundan mahalle kapsamı `(county_id, mahalle_norm)` ile çözülür (türetilmiş indeksli sütun). `13_ilceler` her ilçe için ikinci bir ölü id taşır → hub veri taşıyan id'lere budanır. Analiz süresi 2,3 s → 26–52 ms (sıcak): kapsayıcı bbox indeksi (TKGM 6,9M satır), `*_norm` sütunları (fonksiyonlu WHERE kaldırıldı), emsal bbox ön elemesi, katman grubu başına arama yarıçapı.

## 1) Şu an KULLANILAN veriler
| Kaynak | Tablo | Kullanım |
|---|---|---|
| warehouse/product/arsa_emsalleri.sqlite | ilanlar (63.082) | 1 km emsaller |
| " | arsa_mahalle_ozet (49.776) | endeks + bölge dinamikleri |
| " | arsa_mahalle_trend (2.715.520) | endeks geçmişi grafiği |
| " | arsa_alan_segmentleri (2.851) | alan bandı m² kırılımı |
| tkgm_alim_satim.sqlite | tkgm_alim_satim_yogunlugu (6.941.129) | satış ısı haritası |
| cografi_katmanlar.sqlite | cografi_ozellikler (580.432) | fay/elektrik/su/dere/orman… |
| mahalle_yapilasma.sqlite | mahalle_yapilasma_ozet (9.523) | konut m² / kat |
| emlakjet_bolge_endeks.sqlite | bolge_endeks (40.068) | amortisman/getiri/kira/bina yaşı (konut) |
| turkiye_makro_ve_mikro_istihbarat.sqlite | sege_973_ilce_gelismislik (969) | ilçe gelişmişlik (SEGE) |

Not: `demo/imar_canli_test.html` ayrıca `/api/bolge-istihbarat` ile ciro/etbis/tütün/kargo kullanır; **alıcı arsa analizi** bunları kullanmaz.

## 2) KULLANILMAYAN veriler (öncelik sırasıyla)

### A. İstihbarat ambarı — `collector/data/turkiye_makro_ve_mikro_istihbarat.sqlite` + `eticaret_ve_lojistik.sqlite`
| Tablo | Satır | İçerik (kolonlar) | Vizyon |
|---|---|---|---|
| **eticaret_ve_harcama_kalemleri** | 101 (il/ilçe) | e-ticaret kullanıcı/yoğunluk, online pazaryeri/tatil/elektronik/giyim harcaması, aylık gıda/**barınma-kira**/ulaşım/restoran/giyim/sağlık/eğitim/eğlence/alkol-tütün harcaması, **hanehalki geliri**, tasarruf, 2026 projeksiyonları | Dükkan 2/8, Konut demografi |
| **ciro_ve_ticari_potansiyel_endeksi** | 969 (ilçe) | ciro potansiyeli skoru, yeme-içme/market/e-ticaret skorları, sosyo-ekonomik derece | Dükkan 1/4 |
| **tuik_tutun_ve_sigara_istatistikleri** | 8 (bölge) | erkek/kadın/toplam sigara oranı | Dükkan 9 |
| **etbis_81_il_e_ticaret** | 81 (il) | il e-ticaret hacmi | Dükkan 8 |
| kargo_ve_teslimat_noktalari | 16 | kargo noktaları (az) | Dükkan 8 |
| **cografi_varliklar** (cografya DB) | 19.757 | dağ/zirve/orman: ad, tür, **enlem/boylam/rakım** | Arsa zemin eğimi/rakım |

### B. VERİLER CSV paketleri — `VERİLER/Öğelerle Yeni Klasör 2/`
**01–11, 15–17 ambara ALINDI (bkz. bölüm 0).** Aşağıdaki tabloda kalın satırlar artık kullanımda; kalan açık işler: 04 aylık konut trendi kartı, emlak ofisi/inşaat şirketi kartları, `turkiye_isyeri_ayrintili.csv`, `emlakjet-dagilim/trend-*.csv`.

| Dosya | ~Satır | İçerik | Vizyon |
|---|---|---|---|
| **01_demografi_ve_nufus** | 20.404 | nüfus (toplam/erkek/kadın), hane sayısı, **ortalama hane geliri**, **ev sahibi/kiracı oranı**, **SES A+/A/B/C** | Arsa 21, Konut 2, Dükkan 2 |
| **17_detayli_yas_piramidi_47_grup** | 20.404 | 5'er yaş grubu × cinsiyet **yaş piramidi** | Arsa 21, Konut 2 |
| **16_medeni_durum_ve_gayrimenkul_stoku** | 20.404 | evli/bekar/boşanmış/dul, **toplam konut sayısı**, ticari mülk, yazlık, sahibinden/emlakçı ilan sayısı | Konut sıkışıklığı/stok |
| **05_oda_yas_kat_isitma_kirilimlari** | 115.060 | oda/yaş/kat/ısıtma **kırılımı**: segment, oran, satılık & **kiralık m² fiyatı**, **amortisman_yıl** | Konut 7, amortisman |
| **02_yillik_satislar_2010_2024** | 304.310 | **2010-2024 yıllık satış adetleri** | Satış geçmişi/trend |
| **04_aylik_fiyat_trendi_2021_2026** | 1.499.422 | aylık fiyat trendi (konut+arsa) | Endeks (konut) |
| **08_ilce_onemli_noktalar_poi** | 22.929+ | **POI**: hastane/AVM/okul/üniversite vb. (kategori, ad, konum) | Arsa 23, Konut 9, Dükkan 3 |
| **06_hemsehri_kutuk_dagilimi** | 181.406 | **hemşehri/kütük dağılımı** | Dükkan 11 |
| **07_secim_sonuclari_ve_oylar** | 294.432 | **seçim sonuçları/oy dağılımı** | Dükkan 10 |
| **09/10_emlak_ofisleri & danışmanları** | 1.520 | emlak ofisleri, danışmanlar (rehber) | Emlakçı paneli |
| **11_insaat_ve_proje_sirketleri** | 1.451 | inşaat/proje şirketleri | Arsa 13 (proje) |
| **turkiye_konut_ayrintili.csv** | 132.739 | konut ilan detayı (kat, m², oda) — kısmen mahalle_yapilasma'ya alındı | Konut |
| **turkiye_isyeri_ayrintili.csv** | 12.108 | işyeri ilan detayı | Dükkan |
| **emlakjet-dagilim-*.csv / emlakjet-trend-*.csv** | ~yüz binler | oda/ısıtma/cephe dağılımı + aylık trend (konut/arsa) | Konut/arsa endeks derinliği |

### C. Ürün ambarında olup arayüzde eksik kalan alanlar
- `arsa_mahalle_trend`: projeksiyon (407.328 kayıt) grafik üstünde var ama aylık değişim/stok trendi ayrıca gösterilebilir.
- `arsa_alan_segmentleri`: `ilanda_kalma_suresi` (band bazında satış hızı) gösterilmiyor.
- `parsel_imar_degisiklikleri.sqlite`: parsel_imar_kayitlari (20), bagimsiz_bolumler (27) — çok az, canlı E-Plan tercih ediliyor.

## 3) BOŞ / kullanılamaz
- `mahalle_tuketim_ve_harcama` (0 satır) — ama aynı veri `eticaret_ve_harcama_kalemleri`'nde dolu.
- `08_arsa_koordinat_fiyat_isi_haritasi.csv` (0), `kargo_ve_teslimat_noktalari` (istihbarat DB'de 0).
- `turkiye_tum_ilanlar.sqlite` `bina_yasi` kolonu boş — ama Emlakjet `ort_bina_yasi` dolu (kullanıldı).

## 3.5) ZIP İÇİNDE — HENÜZ ÇIKARILMAMIŞ (önemli!)
`VERİLER/Öğelerle Yeni Klasör 2/` altında çok sayıda paket zip var; içerikleri ambara alınmadı:

| Zip | İçerik | Not |
|---|---|---|
| **TUM_TURKIYE_TAMAMI_TEK_PAKET.zip** | İç zip → **14 ulusal CSV'nin TAM sürümü**: 01_demografi (4.9MB), 02_yıllık satışlar (23.7MB), 03_fiyat özet, **04_aylık fiyat trendi (158MB)**, 05_oda/yaş/kat/ısıtma (11.9MB), 06_hemşehri (17.9MB), 07_seçim (42.7MB), 08_POI, 09/10/11 ofis/danışman/inşaat, 12/13/14 il/ilçe/mahalle listeleri | Türkiye geneli en eksiksiz sürüm |
| **m07–m20_poi_*.zip** (13 paket; m11 yok) | `data/piyasa_verileri.db` → `poi_noktalari` (500.110) | **ALINDI → poi_ilce.** Düzeltme: koordinat YOK (yalnız city_id/county_id/kategori/ad/slug); yakınlık değil ilçe sayımı. `emlak_ofisleri`/`danismanlar` bu zip'lerde boş |
| **m21–m40_ofis_*.zip** (20 paket) | `emlak_ofisleri`, `danismanlar` + CSV | Emlakçı paneli |
| **bolge_01–15_*.zip** (15 bölge) | `data/csv_ciktilari/01–14*.csv` (bölgesel) + iç `paket_XX.zip` + `piyasa_verileri.db` | Bölgesel; ulusal paket zaten kapsıyor |
| **TURKIYE-TUM-ILANLAR-40-MAKINE-PAKETI.zip** | İç zip → `tum_turkiye_nihai_paket/turkiye_tum_ilanlar.sqlite` (101MB) + ayrıntılı CSV | **Zaten çıkarılmış** (mahalle_yapilasma kaynağı) |
| **TURKIYE-ARSA-TARLA-ENDEKS-40-MAKINE-PAKETI.zip** (211MB) | İç zip → arsa/tarla tam endeks veriseti | arsa_emsalleri.sqlite kaynağı olabilir |
| **sektor-1..40-geojson.zip** | Coğrafi vektör katmanları | **Zaten işlendi** (cografi_katmanlar) |

~~En kritik açılmamışlar: POI ve ulusal 01–07~~ → **alındı (2026-09-12).** Kalan: m21–m40 ofis zip'leri, ARSA-TARLA-ENDEKS paketi (arsa_emsalleri kaynağı olarak doğrulanmalı), `cografi_varliklar` rakım.

## 4) Önerilen öncelik (arsa + konut için en yüksek değer)
1. **08 POI** → arsa/konut için hastane/AVM/okul/üniversite/OSB yakınlığı (vizyonun birçok maddesi).
2. **01 demografi + 17 yaş piramidi + 16 stok** → bölge nüfus/yaş/SES/konut stoku (arsa 21, konut demografi).
3. **05 oda/yaş/kat/ısıtma + kiralık m² + amortisman** → konut analizi çekirdeği.
4. **cografi_varliklar rakım** → arsa zemin eğimi/rakım (fay ambarıyla birlikte).
5. **eticaret_ve_harcama + ciro + tütün + seçim + hemşehri** → dükkan analizi çekirdeği.

## 5) Ertelenen fikir — İmar sorgularını gezildikçe önbelleğe almak (2026-09-13, not)
E-Plan nokta sorgusu yalnız öznitelik döner (~0,5 KB, geometri yok; POLYGON filtresi çalışmıyor, LINESTRING çalışıyor). Tahmin: nokta+öznitelik ~1–2 KB/kayıt (1M sorgu ≈ 1,5 GB); TKGM poligonuyla ~4–6 KB (1M ≈ 5 GB); raster tile kopyası 16–25 GB ve sorgulanamaz → önerilmez.
Plan: `warehouse/product/imar_onbellek.sqlite`, parsel anahtarı + plan_record_id + zlib JSON + sha256; içerik değişince yeni sürüm → imar değişikliği tarihçesi; TTL 30 gün; toplu tarama yok. Mevcut yarım altyapı: `parsel_imar_kayitlari` / `process_parsel(persist=True)` (sunucu şu an `init_storage=False`).

## 6) Eklendi — Zemin/jeolojik birim (MTA WMS canlı nokta sorgusu, 2026-09-13)
`geoprop/jeoloji.py` → `analyze()["zemin_jeoloji"]`, arayüzde "Zemin ve jeolojik birim" kartı (birim, yaş, simge/kod + ambardan fay mesafesi). Kaynak: `https://mtayenicbs-geoserver.mta.gov.tr/geoserver/mta/wms`, katman `mta:PORTALFORM` (1/500.000), GetFeatureInfo ~120–200 ms. Poligon yalnız parsel çevresi ~3 km pencereyle kırpılıp sadeleştirilerek (≈0,6 KB) sorgu haritasında gösterilir; ambara YAZILMAZ, toplu tarama YOK; kaynak ibaresiyle. `GEOPROP_JEOLOJI_CANLI=0` ile kapatılır. Heyelan (`YERHEYELAN`) ve magmatik katmanlar GetFeatureInfo'da boş döndü — kullanılmadı. Vizyon 8) "Zemin durumu" kısmen (jeolojik birim; AFAD zemin sınıfı değil).

## 7) Eklendi — Arazi ve eğim (3D) (2026-09-13)
`geoprop/arazi.py::TerrainEngine` → `analyze()["arazi"]`: Terrarium (AWS, Copernicus/SRTM 30 m sınıfı) pencere okuma; parsel 90 m ve çevre 300 m pencerelerde rakım/eğim/bakı/sınıf. **Dürüst etiket:** "bölgesel"; 500 m² parselin iç eğimi 30 m DEM ile ölçülmez. UI kartı "Arazi ve eğim (3D)": MapLibre GL, Terrarium terrain (1.4×), Esri uydu, TKGM parsel poligonu arazi üstünde, 3D/2D/Döndür.
Sonraki adım: Copernicus **EEA-10 (10 m)** — CCM kaydı yapıldı, S3 anahtarı `.env`'de (gitignore), `eodata/CCM/COP-DEM_EEA-10-INSP/...` yolları katalogdan bulunuyor ama 403 (kategori ataması bekleniyor; gerekirse yeni S3 anahtarı). Açılınca `rasterio /vsis3/` pencere okuma ile aynı motor 10 m'ye geçer (`kapsam: parsel_yaklasik`). 5 m için HGM SYM5 (ücretli).

## 8) Eklendi — OSM Türkiye POI ambarı + yakın noktalar (2026-09-13)
`collector/osm_poi_toplayici.py`: Geofabrik `turkey-latest.osm.pbf` (646 MB, günlük) → `warehouse/product/osm_poi.sqlite` (**106 MB**, 364K POI, 2.996 hat, 69.851 hat-durak). 50+ alt kategori (hastane/ASM/eczane, ilkokul/ortaokul/lise/üniversite, tren/metro/tramvay/otobüs durağı/otogar/liman/havalimanı, market/AVM/pazar/hal/tekel, kafe/restoran/fast food, OSB/sanayi sitesi/fabrika, stadyum/spor salonu/park, karakol/itfaiye/belediye…), `brand` + ad eşlemesiyle **zincir/marka** (BİM/A101/ŞOK/Migros, Starbucks, Burger King…). Askeri havaalanları dışlandı; hal/ASM ad kurallı.
Motor `geoprop/yakin_noktalar.py` → `analyze()["yakin_noktalar"]`: kategori başına en yakın N (`(alt_kategori, lat, lon)` indeksi, 42 ms), 500 m/1 km/3 km sayımları, 500 m'deki durakların **hatları**, 1 km zincir sayımı, ana hedeflere **OSRM** araç mesafe/süre (public sunucu, ~0,3–1 s; kesilirse kuş uçuşu). Kart "Yakın önemli noktalar" (harita + 8 grup + hat çipleri + zincirler).
Tazeleme: `.github/workflows/osm_poi_haftalik.yml` (pazartesi, artifact). Bilinen boşluklar: OSM'de **hal 43** (HKS resmî listesi eklenmeli), okul ~17K/65K (MEB listesi), OSB 216/~360 (OSBÜK), belediye GTFS ile hat/durak zenginleştirme.

## 9) Eklendi — MEB resmî okullar + LGS puanlaması (2026-09-13)
`collector/meb_okul_toplayici.py` → `warehouse/product/resmi_egitim.sqlite` (21 MB): **55.115 kurum** (meb.gov.tr listesi), koordinat `harita.php` (52.5K) + adres/köy → Nominatim "yaklaşık" tamamlama, ana sayfadan **derslik/öğretmen/öğrenci** (52.5K; 3 tema uyumlu; telefon/adres toplanmaz — karar), **LGS 2026 taban puanları** e-okul tercih listesinden (3.154 program / 2.444 lise; yalnız sınavla alan liseler) → ulusal sıra/yüzdelik, il sırası, tür sırası. Motor `geoprop/okullar.py` → `analyze()["okullar"]`; kart "Okullar ve eğitim kalitesi" (5 km'de en iyi liseler, kademe başına en yakın + kapasite, ≈ etiketi).
Yurt dışı IP: e-okul 403 (LGS yerelden); meb.k12.tr GitHub'dan çalıştı (#2), sonraki koşuda yanıtsız kaldı (geçici kısıtlama) → aylık tazeleme için Türkiye IP'si/self-hosted runner daha güvenli. Veri kalitesi: MEB sayfalarında hatalı giriş (713 öğr/1 derslik) → oran 3–80 aralığı dışında gizlenir.
Kalan resmî listeler: SB hastane/ASM, OSBÜK OSB, HKS hal, UAB liman, YÖK+URAP üniversite.

## 10) Eklendi — Öğrenci yurtları (KYK + özel) (2026-09-13)
`collector/yurt_toplayici.py` → `warehouse/product/yurtlar.sqlite`: **862 KYK** (kygm.gsb.gov.tr Ajax, 81 il; kapasite yalnız KYGM işletme PDF'inden 197'si) + **1.461 özel yurt** (GSB Özel Barınma portalı, 77 il; kapasite tam: 234K, 743 erkek / 718 kız). Doluluk yayımlanmıyor → yalnız kapasite (kart bunu yazar). Koordinat: `collector/geocode.py` (Photon → Nominatim yedek, sokak/mahalle/köy düzeyi, idari doğrulama, ≤1 istek/sn, "yaklaşık" etiketi). Motor `geoprop/yurtlar.py` → `analyze()["yurtlar"]`; kart "Öğrenci yurtları" (1 km/3 km sayı + kız/erkek kapasite, en yakın 5, harita).
Ders: Nominatim'e iki süreç paralel gidince 429 → tek süreç zinciri (yurt → okul tamamlama).

## 11) Eklendi — Camiler, üniversite kampüs poligonları, ilçe bazlı öğrenci (AÖF hariç) (2026-09-14)
- **Cami**: OSM `amenity=place_of_worship` + `religion=muslim` → `alt_kategori='cami'` (**46.454**; Diyanet ~90K → kapsam ~%50, etiketli). "Yakın önemli noktalar" kartında en yakın 3 + 1 km sayısı.
- **Kampüs poligonları**: OSM `amenity=university` alanları GeoJSON (`poi.geometri`, ~5 m sadeleştirme; 742 poligon, 263 km²) → `universite.sqlite::kampus` (775 kampüs; 578 üniversiteye eşli: ad içerme / ters içerme / YÖK birim listesi; kampüs içi bina/birimler `bina_parcasi`). İlçe: en yakın MEB okulunun ilçesi.
- **Öğrenci sayısı**: YÖK **Tablo 102 (2025-26) "Öğrenim gördüğü il ve ilçelere göre öğrenci sayıları"** PDF'i (kullanıcı verdi) sayfa-başı sütun haritasıyla çözüldü: 205 üniversite × 867 ilçe satırı, 13 grup (önlisans/lisans/YL/Dr × örgün/ikinci/uzaktan/açık), ilçe toplamları üniversite toplamıyla birebir. **AÖF hariç = toplam − (önlisans açık + lisans açık)**; uzaktan dahil. Türkiye AÖF-hariç 4,05 M. Kampüs kartında ilçe öğrenci sayısı (aynı ilçede N kampüs varsa ortak, bölünmez). YÖK portal Excel'leri (üniversite×öğrenim türü; 8.038 birim + birim ili) de `universite.sqlite`'de.
- Motor `geoprop/universiteler.py` (poligon mesafesi/içinde), kart "Üniversite kampüsleri".
- **İdari sınırlar** (2026-09-14): `collector/idari_sinir_toplayici.py` → `idari_sinirlar.sqlite` (81 il, 1.005 ilçe poligonu, OSM admin_level 4/6); `geoprop/idari.py::AdminLookup` koordinat→il/ilçe. Kampüs ilçeleri buna göre (199 düzeltme). Üniversite eşleşmesi "adda en önce geçen üniversite" kuralı (RTE Yerleşkesi ≠ RTE Üniversitesi).
- **İlçe kampüsleri kapsamı:** T102'nin 840 üniversite-ilçe satırından 393'ü OSM kampüs poligon/noktasıyla, 433'ü **ilçe merkezi fallback** ("kampüs poligonu yok, ≈ ilçe merkezi") ile işaretli; kalan ~10 KKTC (Türkiye sınır verisi dışında).

## 12) Eklendi — Semt pazarları, resmî hal/OSB/liman/özel hastane listeleri, URAP (2026-09-14)
- **Semt pazarları** `collector/pazar_toplayici.py` + `collector/hks_pazar_toplayici.py` → `warehouse/product/pazarlar.sqlite::pazar`: OSM (1.333) + İBB açık veri (457; mahalle/gün/kapalı) + İzmir BB (186) + **HKS pazar yeri kayıtları** (hal.gov.tr, iki adımlı ASP.NET postback; 2.742 kayıt, il/ilçe/mahalle/gün; adres kodlamasıyla koordinat "mahalle/sokak merkezi (yaklaşık)"; 150 m içinde belediye/OSM kaydı varsa eklenmez). Motor `geoprop/pazarlar.py::MarketEngine` → `analyze()["pazarlar"]`; kart "Semt pazarları" (1 km/3 km sayı, kapalı, gün dağılımı, en yakın 6, ≈ etiketi).
- **Resmî listeler** → `warehouse/product/onemli_tesisler.sqlite`, `NearbyPoiEngine` içinde OSM adaylarıyla birleştirilir (400 m çakışma → OSM satırı "resmî" işaretli + resmî alanlar; çakışmayan → ayrı madde, konum kaynağı etiketli):
  - `hal` (HKS 177 toptancı hal; koordinat: OSM ad/ilçe eşleşmesi, adres kodlaması, şube ilçe merkezi; balık/et/çiçek halleri OSM eşleşmesinde dışlanır) — `collector/hks_hal_toplayici.py` (`--sadece-osm`, `--belde`).
  - `osb` (OSBÜK 419; tür/durum/alan ha/parsel; OSM OSB poligonu 238 + geocode 129) — `collector/osb_toplayici.py`.
  - `hastane_ozel` (SB SHGM faal özel hastane 572; tip; OSM ilçe içi ad eşleşmesi → geocode) — `collector/sb_hastane_toplayici.py`. Kamu hastanesi listesi SB'de 404 → OSM.
  - `liman` (UAB Denizcilik GM ISPS liman tesisleri, makine okunur son sürüm **02.08.2022** xls, 193 tesis; faaliyet/işletici; derece-dakika koordinat ≈1 km → ad benzer/tek OSM nesnesine oturtma; PFSO irtibat bilgileri alınmaz) — `collector/uab_liman_toplayici.py`. Güncel liste sitede yalnız görsel (JPG).
- **URAP 2025-2026 Türkiye genel sıralaması** (Tablo 10, 198 üniversite) → `universite.sqlite::urap`; `collector/urap_toplayici.py`; kampüs kartında "URAP n. / 198" rozeti (sıralanmayanlar "sırası yok").
- Testler: `tests/test_pazarlar.py`, `tests/test_yakin_noktalar.py::test_resmi_liste_birlestirme` (68 test).

## 13) Eklendi — TÜİK bölge dinamikleri (2026-09-14)
`collector/tuik_bolge_toplayici.py` → `warehouse/product/tuik_bolge.sqlite` (ham dosyalar `warehouse/raw/tuik/`; TÜİK veri portalı ve MEDAS curl'e "Erişim engellendi" verir → tarayıcı oturumundan `fetch`; MEDAS raporları 50.000 hücre sınırıyla parça parça `pivot.csv`).
- `nufus_ilce` (ADNKS 2007–2025, 996 ilçe), `nufus_mahalle` (32.254 mahalle, 2025), `nufus_koy` (18.183 köy, 2025)
- `yapi_izin_ilce` (ruhsat + kullanma izni daire sayısı, ilçe × yıl 2010–2025), `yapi_izin_il` (yıl/çeyrek → 2026-II)
- `konut_satis_ilce` (ilçe × ay, 2013-01 → 2026-07; 127K dolu hücre)
- `goc_il` (17 dönem), `ses_ilce` (Sosyoekonomik Seviye 2023, 973 ilçe + il), `hanehalki_il`, `nufus_projeksiyon_il` (2030)
Motor `geoprop/tuik_bolge.py::TuikRegionEngine` → `analyze()["tuik_bolge"]` (11 ms): ilçe nüfusu + 1/5/10 yıl değişim, mahalle nüfusu (kadastro mahallesi eşleşmezse kullanıcının yazdığı idari mahalle denenir), il göç hızı/projeksiyon/hanehalkı, ruhsat–kullanma serisi + 5 yıl ortalamasına göre ruhsat + 1000 kişiye ruhsat, son 12 ay satış + değişim + 1000 kişiye satış, SES skoru + il/Türkiye sırası + seviye dağılımı. Kart "Bölge dinamikleri (TÜİK)" (2 çubuk grafik + SES şeridi). Test `tests/test_tuik_bolge.py`.

## 14) Eklendi — Hava kalitesi, Resmî Gazete izleyici, OSM değişim, Sentinel-2 uydu değişimi (2026-09-14)
- **Hava kalitesi (ÇŞB SİM UHKİA)** `collector/sim_hava_kalitesi_toplayici.py` → `cevre.sqlite` (`hava_istasyon` 338 istasyon koordinatlı; `hava_gunluk` 90K gün: PM10/PM2.5/SO2/NO2 günlük ort., son 365 gün). Kaynak `POST /Services/GetAirQualityStations` (Content-Length:0 şart) + `/STN/STN_Report/StationDataDownloadNewData` (antiforgery token + çerez; form-encoded; DataPeriods=16 günlük). Motor `geoprop/cevre.py::EnvironmentEngine.hava` → `analyze()["hava_kalitesi"]`: en yakın 3 istasyon (≤40 km, temsil gücü), yıllık ortalamalar, DSÖ 2021 kat sayısı, AB PM10>50 aşım günü, aylık seri; kart "Hava kalitesi (son 12 ay)".
- **Resmî Gazete izleyici** `collector/resmi_gazete_toplayici.py` → `resmi_gazete.sqlite` (`karar`, `karar_yer`, `taranan_gun`): fihrist (curl; urllib sertifika hatası) → başlık sınıflama (konu × işlem: kentsel dönüşüm/riskli alan, sit, turizm, OSB/serbest bölge/TGB, raylı, yol, enerji hattı, üniversite, liman, maden, su; acele kamulaştırma/kamulaştırma) → karar PDF'i gömülü fontlu (metin çıkmaz) → **pymupdf + tesseract(tur) OCR** ilk 2 sayfa → "X İli, Y İlçesi, Z Mahallesi" kalıpları; kalıp yoksa güzergâh haritası etiketlerinden il/ilçe (idari liste ile, `kaynak='harita'`, düşük güven). Ada/parsel listeleri OCR'da güvenilmez → saklanmaz. Motor `EnvironmentEngine.resmi_gazete` → `analyze()["resmi_gazete"]` (il/ilçe/mahalle eşleşme düzeyi, son 18 ay); kart "Resmî Gazete kararları".
- **OSM değişim** `collector/osm_poi_degisim_toplayici.py` → `osm_degisim.sqlite` (`poi_kimlik` son kesit, `poi_olay` eklendi/silindi/taşındı, `ilce_sayim` ilçe×kategori zaman serisi, `anlik`). İlk kesit 2026-09-13 tabanı; haftalık kesitten sonra `NearbyPoiEngine` `degisim_1km` (son 90 gün) doldurur; kart "Yakın önemli noktalar" içinde satır. Not: OSM'den kaldırılma ≠ kapanış.
- **Uydu değişimi** `geoprop/uydu.py::SatelliteChangeEngine` (canlı, önbellekli): CDSE STAC → Sentinel-2 L2A B04/B08/B11/SCL `/vsis3/` pencere okuma (rasterio AWSSession, eodata uç noktası; `.env` CDSE_S3_*), 1 km pencerede NDVI/NDBI → yapılaşmış/çıplak ve bitki payı, şimdi vs 1 yıl / 3 yıl önce (aynı mevsim ±60 gün, bulut ≤40, SCL geçerli ≥%70). İlk hesap ~60 sn → `GET /api/v1/uydu/degisim?lat&lon` ayrı uç nokta (kilitli), `uydu_onbellek.sqlite` 0,01° hücre 30 gün; kart "Uydu değişimi (Sentinel-2)" analizden sonra asenkron dolar. `GEOPROP_UYDU_CANLI=0` kapatır.
- Testler: `tests/test_cevre.py` (70 test).

## 15) Eklendi — İBB deprem senaryosu (mahalle) + stratejik gürültü haritası; OSM altyapı kategorileri (2026-09-14)
- `collector/ibb_deprem_toplayici.py` → `cevre.sqlite::deprem_senaryo_mahalle` (959 mahalle; 7,5 Mw gece senaryosu: çok ağır/ağır/orta/hafif hasarlı bina, can kaybı, yaralı, altyapı hasarı, geçici barınma + 2017 bina stoku yaş grupları → ağır hasar oranı, ilçe/İstanbul ortalaması ve sırası). CSV Windows-1254.
- `collector/ibb_gurultu_toplayici.py` → `cevre.sqlite::gurultu_hat` (+R-tree; 12.825 eş-gürültü **çizgisi** 55/65/75 dB, Lgündüz/Lgece; EPSG:3857→4326, 2 m sadeleştirme). Harita çizgi olduğundan banda kesin atama yapılmaz → seviye başına en yakın çizgi mesafesi (400 m) + sınıf ("75 dB hattı bitişik" ≤30 m). Yerel sokak gürültüsü haritada yok (yalnız ana karayolu/raylı/sanayi).
- Motor `EnvironmentEngine.deprem_senaryo / gurultu` → `analyze()["deprem_senaryo"|"gurultu"]`; kart "Deprem senaryosu ve gürültü (İBB)" (yalnız İstanbul; diğer illerde `not_covered`).
- Resmî Gazete yer çıkarımı yeniden yazıldı: "A, B ve C İlçeleri" listeleri, parantezli mahalle adları, idari liste doğrulaması; harita etiketi kaynaklı il-geneli eşleşmeleri karttan çıkarıldı (gürültülü). `--yer-yenile` ile OCR'sız yeniden çıkarım. 400 gün: 310 karar (308 OCR), 260'ında yer.
- OSM toplayıcıya `altyapi` kategorisi: `baz_istasyonu` (man_made mast/tower + tower:type communication / telecom etiketi), `yuksek_gerilim_diregi`, `trafo` — BTK resmî listesi yok, OSM kısmi; yeniden işleme başlatıldı.
- Rayiç bedel: merkezî kaynak yok; her belediye kendi e-belediye sorgusu (antiforgery/oturum, ilçe→mahalle→sokak kademeli) → belediye başına ayrı kazıyıcı gerekir; **ertelendi**.

## 16) Eklendi — Yapı ruhsatı kullanım amacı, İBB saatlik trafik yoğunluğu, taşkın/heyelan vekilleri (2026-09-14)
- **TÜİK kullanım amacı kırılımı** (MEDAS kn=135, yüzölçümü m²; 11 konut, 121 otel, 122 ofis, 123 ticaret, 124 ulaşım, 125 sanayi/depo, 126 kamu/eğitim/sağlık, 127 diğer; ilçe×yıl 2010–2025, 4 parça CSV) → `tuik_bolge.sqlite::yapi_ruhsat_amac_ilce` (140K satır). Motor: `konut_arzi.kullanim_amaci` (son 3 yıl payları, konut dışı %); kartta pay şeridi.
- **İBB saatlik trafik yoğunluğu** (`hourly-traffic-density-data-set`, aylık ~140 MB CSV, geohash-6 hücre × saat araç sayısı/hız; 2024-02…2025-01 son 12 yayımlanmış ay) → akış halinde toplanıp (`collector/ibb_trafik_yogunluk_toplayici.py`, ham veri diske yazılmaz) `cevre.sqlite::trafik_saatlik` (117.725 hücre×gün tipi×saat). Motor `EnvironmentEngine.trafik` → `analyze()["trafik_saatlik"]`: parsel hücresi (≤1,5 km) hafta içi/sonu 24 saat profili, zirve saat, en yavaş saat, İstanbul ortalamasına göre; kart "Saatlik yoğunluk (İBB trafik)". Kişi GPS verisi açık değil (Google/telekom ücretli, KVKK) → araç trafiği yaya/ticari yoğunluk vekili olarak sunulur. Ek vekil adayları: İBB saatlik toplu taşıma yolcu (hat/durak) ve İSPARK — henüz alınmadı.
- **Taşkın / heyelan vekilleri** `geoprop/afet_vekili.py` → `analyze()["afet_vekili"]`: DSİ taşkın ve AFAD heyelan haritaları TUCBS API anahtarı ("Policy Falsified") / yalnız PDF olduğundan **resmî harita yok**; 30 m DEM (300 m pencere taban kotuna göre yükseklik) + en yakın dere yatağı (coğrafi katmanlar, yarıçap 1,2 km'ye çıkarıldı: `en_yakin_dere_m`, `en_yakin_gol_baraj_m`) + MTA birimi (Kuvaterner/alüvyon duyarlı) ile sınıf (yüksek/orta/düşük/uzak). Kart "Taşkın ve heyelan (vekil gösterge)" — "resmî değil" rozeti.
- Bekleyenler: OSM 5 yıllık geçmiş (Geofabrik iç sunucu → kullanıcı OSM girişi bekleniyor), baz istasyonu/trafo (OSM yeniden işleme disk dolu — 2,6 GB; ~/Library/Caches temizliği onayı), rayiç bedel (belediye bazlı kazıyıcı), mikrobölgeleme (açık veride bulunamadı).

## 17) Eklendi — OSM 5 yıllık işletme geçmişi, işletme yoğunluk oranı, altyapı/ofis/mağaza kategorileri (2026-09-15)
- **Lisans kararı:** Geofabrik *internal* kesitleri/geçmiş dosyası (kullanıcı adı/ID'li) "yalnız OSM içi kullanım" şartlı → kullanılmadı; kullanıcı indirmiş olsa da işlenmedi. Bunun yerine **public** yıllık kesitler (`turkey-YY0101.osm.pbf`, 2021→2025, ODbL) indirilip işlendi ve silindi.
- `collector/osm_poi_gecmis_toplayici.py` → `osm_degisim.sqlite::poi_yillik` (kesit×POI: 2021 358K, 2022 385K, 2023 428K, 2024 506K, 2025 543K, 2026-09 624K) + `poi_yasam` (nesne başına ilk/son görülme, durum aktif/kaldırıldı, ad-marka değişimi, taşınma, geçmiş JSON; kapsayan indeks). Sonuç: 624K aktif, 57K kaldırıldı, 16K ad değişimi (motor yazım düzeltmelerini benzerlik <0,6 kuralıyla eler).
- `NearbyPoiEngine.gecmis_1km`: 1 km'de 2021 tabanı, yıllara göre eklenen/kaldırılan, ad/marka değiştirenler, son olay listeleri; kartta iki mini grafik + listeler. Dürüstlük notu kartta ("ilk görülme ≠ açılış; kaldırılma ≠ kapanış").
- `NearbyPoiEngine.yogunluk_1km`: 1 km km² başına işletme / ilçe km² başına işletme (yeme-içme, perakende, iş/ofis, sanayi); ilçe alanı `idari_sinirlar.sinir.alan_km2` (eklendi).
- OSM toplayıcı yeni kategoriler: `altyapi` (baz_istasyonu 8.513, trafo 4.322, yuksek_gerilim_diregi 173K), `is/ofis` (office=*), `alisveris/magaza` (shop=*), `hizmet/kuafor_guzellik`, `is/zanaat`; `"*"` = etiket var kuralı. Ambar 624K POI (204 MB).
- Disk: silinebilir önbellekler temizlendi (2,6 → 5,4 GB); OSM yeniden işleme diske takılmıştı.
