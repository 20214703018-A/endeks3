# GEOPROP VERİ PROJESİ — DURUM VE PLAN (sade anlatım)
Güncelleme: 2026-09-20 (öğleden sonra) · Bu dosya her büyük adımdan sonra güncellenir. Teknik ayrıntı isteyen için: STANDARD.md, validation/, reports/.

---
## 1. Ne yapmaya çalışıyoruz? (tek cümle)
Elinizdeki dağınık ~20 GB veriyi (Türkiye mahalle/işletme/fiyat/nüfus), **hiçbir şeyi silmeden ve her şeyin nereden geldiğini kaydederek**, hem kendi kullanacağınız hem de satabileceğiniz **tek, güvenilir bir veritabanına** dönüştürmek.

## 2. Depoyu nasıl kurduk? (üç oda benzetmesi)
Veriyi üç "oda"dan geçiriyoruz. Benzetme: bir arşiv binası.

| Oda | Adı | Ne var | Kural |
|---|---|---|---|
| 1. oda | **HAM** (raw) | Sizin dosyalarınız, olduğu gibi (Desktop/GEOPROP, tkgm, harita, Downloads, GEOPROP_RAW_INTAKE) | **Dokunulmaz.** Silinmez, değiştirilmez. Her dosyanın parmak izi (sha256) alındı. |
| 2. oda | **STAGING** (hazırlık) | Her dosya okunup satır satır tabloya çevrildi; her satırın yanında "hangi dosyadan, kaçıncı satırdan" bilgisi var | Değer olduğu gibi tutulur (ham metin) + yanında tipli hali. Okunamayan satır **karantinaya** gider, atılmaz. |
| 3. oda | **KANONİK** (birleşik) | Aynı şeyler tek kimlik altında birleşti: her mahallenin tek kimliği var, her işletmenin tek kimliği var; sayılar (nüfus, fiyat, puan) o kimliğe bağlı "gözlem" olarak duruyor | Ürünün çekirdeği. Her bağ skorlu ve geri alınabilir. |

Ayrıca bir **KARANTİNA** odası var: sahte/şüpheli olduğunu bildiğimiz veya henüz doğrulayamadığımız dosyalar. Kanoniğe girmez ama silinmez.

> "Bronze / Silver / Gold" klasörleri (GEOPROP/warehouse altında) **sizin değil, önceki bir otomasyonun** açtığı klasörler. Ben bunları "ara ürün, atlanabilir" saymıştım — **yanlıştı**. Kontrol ettim: içlerinde elimizdeki dosyaların **daha eski sürümleri** var (1.019 dosya). Bkz. §6.

## 3. Bugüne kadar ne yapıldı? (özet)
1. **Envanter (1. oda):** 20.867 dosya tarandı, 19.104'ü veri. Her birinin parmak izi, biçimi (gerçek içeriğe bakılarak), satır sayısı, sütunları kaydedildi. 7.394 dosya birbirinin birebir kopyası çıktı (kopyalar silinmedi, "asıl şu" diye bağlandı).
2. **Hazırlık (2. oda):** 3.097 asıl dosya → 1.108 tablo, 55,5 milyon satır. Muhasebe her dosyada tuttu: giren satır = yüklenen + karantinaya giden (açıklanamayan 0).
3. **Birleşik veritabanı (3. oda), sürüm 1.4:**
   - **Coğrafya:** 81 il, 973 ilçe, 50.638 mahalle (poligonlarıyla). Mahalleler TKGM ve TÜİK kayıtlarına skorla bağlandı.
   - **Nüfus:** TÜİK 2007–2025 ilçe serisi, TÜİK 2025 mahalle/köy, web 2024 mahalle.
   - **Fiyat:** 29,7 milyon gözlem — konut ve arsa; satılık/kiralık m², ilan sayısı, kira getirisi, min/max, endeks… 33.095 mahallede en az bir seri (kalan ~17 bin birim küçük köyler; ilan yok).
   - **İşletmeler:** 652.965 nokta (koordinatlı). Üç kaynak: Google (575 bin), OSM haritası (68 bin yalnız OSM'de), Yemeksepeti (485). Aynı işletme kesinse tek kayıt (9.171 Google+OSM), değilse ayrı ve etiketli.
   - **İşletme tarihçesi:** OSM haritasında 2021→2026 altı kesit; hangi işletme ne zaman haritaya girdi/çıktı (680 bin kayıt). Kural: bu "haritaya eklenme"dir, "açılış" değildir; farklı kaynaklar arası fark asla açılış/kapanış sayılmaz.
   - **Menü/teslimat:** 29.340 menü kalemi fiyatıyla, 513 restoran için min sepet, teslimat süresi/ücreti, puan.
4. **Doğruluk kontrolleri (ölçülerek, tahminle değil):** web nüfusu TÜİK'le 752/973 ilçede birebir → yıl 2024 olarak kesinleşti · mahalle eşleşmeleri IoU medyan 0,89 · işletme koordinatlarının 7.782'si bozuk bulundu (yer tutucu "41,35" gibi) ve işaretlendi · TÜİK köy/mahalle kod çakışmasından doğan 2.729 yanlış nüfus satırı yakalanıp düzeltildi · v1'de 1.752 işletme yanlış mahalleye düşmüştü, iptal edildi.
5. **Yeni veri toplandı (sizin izninizle):** TKGM resmî il/ilçe/mahalle sınırları (49.795 mahalle), Yemeksepeti (485 restoran, engel politikası nedeniyle durdu), OSM gıda işletmeleri çıkarımı, GitHub koşusu sonuçları (2,99 M satır).

## 4. Elimizde ne var? (kaynak türüne göre; ayrıntı: reports/VERI_KATALOGU.csv)
| Konu | Kaynak (nasıl elde edildi) | Durum |
|---|---|---|
| İdari sınırlar (il/ilçe/mahalle poligonları) | web araştırması (52 K) + TKGM resmî API (50 K) | ✅ birleşik DB'de |
| Nüfus | TÜİK (resmî, 2007–2025) + web 2024 | ✅ |
| Konut/arsa fiyat serileri (aylık 2021→2027) | web araştırması, 5 paket | ✅ |
| İşletmeler (Google Places gözlemleri) | web araştırması | ✅ (kategori tahmini onay bekliyor) |
| OSM işletmeler + 6 yıllık tarihçe | OpenStreetMap (açık lisans, atıf gerekir) | ✅ |
| Yemeksepeti restoran/menü/teslimat | web araştırması (durdu) | ✅ kısmi (485/130 bin) |
| Seçim sonuçları, hemşehri kütük dağılımı, oda-yaş-kat kırılımları | web araştırması, paketler | ⏳ hazırlık odasında, birleşik DB'de değil |
| Yıllık konut satışları 2010–2024, ilan tabloları | web araştırması | ⏳ hazırlık odasında |
| TKGM alım-satım yoğunluğu (13,9 M satır) | web araştırması | ⏳ hazırlık odasında |
| Trafik, hava, GPS, toplu taşıma, okullar, BKM/BDDK/KAP | web araştırması | ⏳ hazırlık odasında |
| TÜİK konut satış (ilçe×ay 2013–2026), yapı izin/ruhsat, göç, SES, hanehalkı (tuik_bolge paketi) | TÜİK MEDAS'tan elle indirme (resmî) | ✅ doğrulandı (güncel bültenle birebir), karantinadan çıktı; birleşik DB'ye v1.5'te |
| ETBİS/SEGE/ciro/tütün endeksleri, "istihbarat" tabloları | betiklerden üretilmiş | 🔴 karantinada — **sahte olduğu kanıtlı** (betik plaka kodundan sayı üretiyor) |
| Parsel imar bilgileri | betikten üretilmiş | 🔴 karantinada — imar değerleri sahte, geometri ayrı değerlendirilebilir |
| Araç ilanları/risk | web araştırması | ⏸ ayrı ürün, kapsam dışı |

## 5. Neler eksik / yarım? (dürüst liste)
1. ~~İç içe zip'ler~~ **ÇÖZÜLDÜ (20 Eyl):** tarama aracı artık zip içindeki zip'e iniyor. 31 zip / 1.417 iç dosya envantere girdi; içeriği zaten elimizde olan 1.097'si bağlandı, yeni olan 1.181 kaynak hazırlığa alındı (2.342.275 satır, açıklanamayan 0). Bunlar bölge paketlerinin 11 Eylül sürümleri (poligonlar + bölgesel piyasa veritabanları) — "paket sürümü" bayrağıyla duruyor.
2. ~~Bronze eski sürümler~~ **ÇÖZÜLDÜ:** 1.019 farklı içerik tamamen (1)'deki iç zip'lerden geliyormuş; silver'ın 2.537 kaynağının hepsi bronze'a bağlı → bronze/silver'da başka yerde olmayan ham veri yok (kanıt: validation/warehouse_lineage_evidence.md).
3. ~~Gold~~ **ÇÖZÜLDÜ:** 18 görünüm taşınmış klasöre baktığı için boş; 2 gerçek tablo (önceki otomasyonun kaynak kayıt defteri, 2.890 dosya × lisans/sınıf) hazırlığa alındı.
4. **Karantina kanıtları:** 118 tablonun tamamı için kart yazıldı (validation/quarantine_evidence/README.md). Sonuç: SAHTE 4 grup (betik satırlarıyla kanıtlı) · GERÇEK 1 grup (TÜİK nüfus) · KISMEN 1 (e-ticaret: ham gerçek, 2026 sütunları üretilmiş) · DOĞRULANAMADI 4 grup (TÜİK'e benzeyen ama uyuşmayan konut satış; BKM, BDDK, KAP — resmî spot kontrol bekliyor). **Düzeltme (akşam):** "TÜİK konut satış" tablosunun uyuşmaması benim hatamdı — eski (revizyon öncesi) bültenle kıyaslamıştım; güncel bültenle birebir çıktı → tablo GERÇEK, tuik_bolge paketinin tamamı karantinadan çıkarıldı. İndirilen eski tablo 'revizyon öncesi seri' etiketiyle ayrı duruyor.
5. **Hazırlık odasında olup birleşik DB'ye girmemiş veriler:** §4'teki ⏳ satırları (satırların ~%66'sı). Sırayla alınacak.
6. **İnsan onayı bekleyen 3 liste** (mahalle eşleşmeleri, işletme kategorileri, Google↔OSM eşleşmeleri): validation/golden/*.html. Bunlar onaylanana kadar bağlar "ÖNERİ" (PROPOSED) etiketli.
7. **Sınıflandırma** ("resmî / web araştırması / üretilmiş", "herkese açık / dahili") her dosyada var ama dosya adına göre otomatik yapıldı; kaynak kanıtı olmayanlar "web araştırması" sayıldı. Zip içindekiler dahil yeniden gözden geçirilecek.
8. **Ürün çıktısı** (harita katmanı, satılabilir paket) henüz üretilmedi (Phase 5).

## 6. Bronze/Silver/Gold ne, ne yapacağız?
Önceki otomasyon kendi üç odasını kurmuş: bronze = ham dosyaların parquet'e çevrilmiş kopyaları (parmak iziyle adlandırılmış), silver = onlardan türetilmiş tablolar, gold = son ürün DB'leri. Bizim üç odamızla aynı fikir ama **onların içeriğini doğrulamadan güvenemeyiz**. Yaklaşım: bronze'daki her dosyanın parmak izi bizim envanterle karşılaştırıldı → 1.519'u zaten bizde, 1.019'u eski sürüm (iç zip'lerden). Silver/gold için: tablo tablo "bizde eşdeğeri var mı, satır sayısı tutuyor mu" kontrolü yapılacak; farklı olanlar "önceki otomasyon türevi" etiketiyle hazırlığa alınacak, ürün için kullanılmayacak.

## 7. Karantina: "doğruluğunu kanıtlama" nasıl olacak?
Her karantina tablosu için bir kanıt kartı: (a) veriyi üreten betik bulunduysa ve betik sayı uyduruyorsa → **SAHTE** (kanıt: betik satırı); (b) resmî bir toplamla karşılaştırılabiliyorsa (ör. TÜİK Türkiye toplam konut satışı 2024 = 1.478.025) ve tutuyorsa → **GERÇEK** (kanıt: karşılaştırma tablosu); (c) ikisi de yapılamıyorsa → **DOĞRULANAMADI** (karantinada kalır, ürüne girmez). Sonuçlar validation/quarantine_evidence/ altına yazılacak.

## 8. Plan (sıra ve süre tahmini)
| # | İş | Neden | Süre |
|---|---|---|---|
| 1 | ~~İç içe zip~~ | tamamlandı 20 Eyl | ✅ |
| 2 | ~~Gold/silver kontrolü~~ | tamamlandı | ✅ |
| 3 | ~~Karantina kanıt kartları~~ (BKM/BDDK/TÜİK yapı izin spot kontrolleri açık) | tamamlandı; 4 grup resmî karşılaştırma bekliyor | ✅/⏳ |
| 4 | Sınıflandırmanın gözden geçirilmesi + ayrıntılı veri kataloğu | "neyimiz var" sorusunun tam cevabı | ~2 saat |
| 5 | v1.5: seçim/hemşehri/oda-yaş bağlamı, TKGM alım-satım, yıllık satışlar, ilanlar, ek işletme kaynakları → birleşik DB | değerli veriler dışarıda kalmasın | ~1 gün |
| 6 | Sizin onaylarınız (3 liste) → bağlar kesinleşir | insan kontrolü | sizin zamanınız |
| 7 | Phase 5: ürün paketi (harita katmanı + tablolar, sürüm numarası, lisans/atıf notları) | satış | ~1 gün |

## 9. Sizden beklenen kararlar (sade)
0. **Türü çözülemeyen ~48.500 işletme:** (a) Google'ın ücretli servisinden tür sorgulamak (~1.400 $), (b) böyle bırakmak (tür 'bilinmiyor', sektör ipucu varsa gösterilir), (c) örneklem üzerinden elle bakmak. Önerim (b) + ileride bütçe olursa (a).
1. **Golden listeler:** üç HTML sayfasında "evet/hayır" işaretleyip bana yapıştırmanız. Zorunlu değil ama onaysız bağlar "öneri" olarak kalır.
2. **Yemeksepeti toplayıcısı:** engel politikası nedeniyle durdu. Yeniden başlatayım mı (yavaş modda, 25–35 sn arayla)? Başlatırsam ~30 günde 130 bin restoranın önemli kısmı gelir.
3. **Claude uygulamasının 11 GB VM dosyası:** disk için hâlâ en büyük kalem; silmek uygulamanın tercihi, size bırakıyorum.

## 10. Kelime sözlüğü (bu dosyada geçenler)
- **sha256 / parmak izi:** dosyanın içeriğinden üretilen benzersiz kod; iki dosya aynıysa kodları aynıdır.
- **staging / hazırlık odası:** dosyaların tabloya çevrildiği ara katman.
- **kanonik / birleşik DB:** tek kimlikli, temiz ana veritabanı.
- **karantina:** şüpheli/hatalı verinin bekletildiği yer; silinmez.
- **gözlem (observation):** bir değerin (nüfus, fiyat) kaynağı ve tarihiyle birlikte kaydı.
- **band / skor:** iki kaydın aynı şey olduğuna ne kadar güvendiğimiz (0,95 üstü "otomatik birleştir", 0,85–0,95 "insan baksın").
- **PROPOSED / öneri:** makine önerdi, insan onayı bekliyor.
- **golden set:** insanın elle kontrol ettiği küçük örnek; makinenin başarısı buna göre ölçülür.

## 11. Dürüst hata defteri ve açık riskler (2026-09-20)
### Benim yaptığım hatalar (hepsi kayıtlı; durumları)
| # | Hata | Etkisi | Durum |
|---|---|---|---|
| 1 | Büyük harfli Ç/Ş/Ğ ile başlayan adları bozan ad-normalleştirme | 5.280 mahalle adı yanlış normalize; bağlar id ile kurulduğu için sonuç etkilenmedi | düzeltildi (v1.1) |
| 2 | TÜİK köy ve mahalle kod alanlarının çakıştığını görmedim | 2.729 nüfus satırı yanlış yere yapıştı (146 kişilik köye 45.079) | düzeltildi (v1.1); tutarsızlık 609→0 |
| 3 | Bozuk koordinatlı (ör. "41,35") işletmeleri süzmedim | 1.752 işletme yanlış mahalleye atandı | düzeltildi; koordinat geçerlilik bayrağı eklendi |
| 4 | İlk birleşik DB'ye 5 fiyat tablosundan yalnız 1'ini aldım | "fiyatlı mahalle %32" yanlış izlenimi | düzeltildi (v1.1: %66, gerçek tavan) |
| 5 | Bronze/silver/gold'u kanıtsız "atlanabilir" saydım | 1.019 eski sürüm dosya gözden kaçacaktı | kanıtla kapatıldı (iç zip'lerden geliyormuş) |
| 6 | Tarama aracı zip içindeki zip'e inmiyordu | 1.417 dosya / 3,1 GB hiç görülmemişti | düzeltildi; 1.181 kaynak alındı |
| 7 | İç içe JSON alanlarını hazırlığa JSON yerine Python metni olarak yazdım | Yemeksepeti kayıtlarında ham sadakat kaybı | düzeltildi; hatalı parti geçersiz kılındı |
| 8 | Bazı CSV'ler "düz metin" sanılıp atlandı; .jsonl "json" sanıldı | 1 gerçek dosya (bölge-13 demografi) eksik kaldı | düzeltildi |
| 9 | TÜİK konut satış tablosunu **eski** (revizyon öncesi) bültenle kıyaslayıp "uyuşmuyor" dedim | yanlış "doğrulanamadı" hükmü | düzeltildi aynı gün; güncel bültenle birebir |
| 10 | İşletme eşleştiricinin ilk sürümünde ad-benzerliği hatası ve dar "gıda" tanımı | tüm çiftler aday olmuştu / 2.221 haklı çift ceza yemişti | kullanılmadan düzeltildi |
| 11 | Adres-bağlamı atamasına 0,60 güven verdim; ölçünce 0,33 çıktı | 9.493 işletme mahalle sayımına girecekti | ölçülen değere çekildi |
| 12 | Disk dolunca kurulum durdu, bir kopya bozuk yazıldı | zaman kaybı; v1 dosyası sağlam kaldı | temizlendi; disk koruması eklendi |
| 13 | Açıklamalarım fazla teknikti | anlaşılmadı | bu dosya + sözlük |
| 14 | Yemeksepeti'nde ilk denemede hızlı gidince engel yedim | toplayıcı 485 restoranda durdu | yavaş mod yazıldı; yeniden başlatma sizin kararınız |

### Açık eksikler (yapılacak)
- Hazırlık odasındaki satırların ~%66'sı henüz birleşik DB'de değil (seçim/hemşehri/oda-yaş, yıllık satışlar, TKGM alım-satım 13,9 M, ilanlar, ek işletme kaynakları, trafik/hava/okul). → v1.5/v1.6
- ~~Karantina spot kontrolleri~~ **yapıldı (20 Eyl):** TÜİK yapı izin (2019–2025 ulusal + 81 il birebir), göç (2024 toplam + 13 il birebir), BKM (Kasım 2024 sektör satırları birebir), BDDK (Adana 2024-12 üç değer birebir) → hepsi karantinadan çıktı. Kalan: KAP (55 satır, birim tutarsız) karantinada.
- Sınıflandırma (resmî / web / üretilmiş; açık / dahili) dosya adına göre otomatik yapıldı; gözden geçirme ve ayrıntılı veri kataloğu (VERI_KATALOGU.csv) yapılmadı.
- ~~kisisel_veriler~~ **karar uygulandı:** emlak danışmanı adı/telefonu (1.620) ve iletişim listesi (1.640) DB'de; kişisel alanlar için sütun düzeyi 'kısıtlı' kayıt defteri (mappings/column_sensitivity_v1.json, 20 tablo) — ürün paketine girmez, dahili kullanımda açık (standart §3.3).
- Üç insan onayı listesi bekliyor; bağlar "öneri" etiketli.
- İşletme türü çapraz taraması **yapıldı (v3):** 88.976 açık kayıttan 7.088 çözüldü (OSM koordinat+ad, ilçe içi ad, zincir, yeni ad modeli); 33.330'una ölçülmüş 'sektör ipucu' (%67–84 doğruluk) verildi; ~48.500 tamamen çözümsüz — adı bilgi vermiyor, başka kaynakta yok. Seçenekler §9'da. 7.782 işletmenin gerçek koordinatı yok.
- Web sitesi menü pilotu düşük verimli (300 sitede 2 menü); Yemeksepeti 485/130 bin.
- Ürün paketi (harita katmanı, sürüm, lisans notu) üretilmedi.

### Riskler (karar/önlem gerektirir)
0. **Google kayıtlarının kimliği (20 Eyl bulgusu):** toplayıcı 560 bin kaydın kimliğini Google'dan değil ad+koordinattan üretmiş (yalnız 16 bin gerçek Google kimliği). Google'a kimlikle soru sorulamaz; eşleştirme ad+konumla yapılmaya devam eder (zaten öyle). Bozuk koordinatların (7.782) kök nedeni de aynı betik: ondalık kontrolü olmayan yedek ayrıştırıcı (validation/google_collector_coordinate_defect.md).
1. **Yedek yok.** GEOPROP_CONSOLIDATION (hazırlık + birleşik DB, ~9 GB) tek diskte. Disk arızasında ham dosyalar durur ama 3 günlük işlem tekrar gerekir. Öneri: harici disk ya da bulut yedeği (sizin kararınız).
2. **Kullanım hakları.** Google Places ve emlak sitelerinden çekilmiş veriler "web araştırması" sınıfında; Google'ın kullanım şartları bu verinin yeniden dağıtımına izin vermez. Satılacak üründe bu veriler ya "dahili" kalmalı ya da yalnız türev/agregat (mahalle özeti) olarak verilmeli. Şu an dağıtım sınıfı bunu tam yansıtmıyor — sınıflandırma gözden geçirmesinde düzeltilecek; ürün öncesi hukuki kontrol gerekir.
3. **OSM tarihçesi önceki otomasyonun türevi.** 6 yıllık kesitleri biz ham PBF'lerden yeniden üretmedik; önceki otomasyonun `osm_degisim` çıktısını kullandık. Ham PBF'ler elimizde; doğrulama için bir yılı yeniden türetmek planda.
4. **Web nüfusunun yılı çıkarımla (2024) belirlendi** — 752/973 ilçe birebir; kesin ama %100 değil.
5. **Disk:** 5 GB boş; 11 GB'lık Claude VM dosyası sizin kararınızda.

## 12. Gece verisinin ambara girişi ve birleşik DB v1.6 / v1.7 (2026-09-29)

24 Eylül gece turunda toplanan kaynaklar ambara alındı ve birleşik veritabanı iki adım büyütüldü.
Her adımda "giren satır = yazılan satır" kontrolü yapıldı; hepsinde fark 0.

**Önce düzeltilen iki sessiz hata**
- *TÜİK SDMX 0 satır*: 432 dosya okunmaya çalışılırken bellek yetmemiş ve kaynak boş görünmüştü. Bu dosyalar
  satır satır değil, tek bir büyük JSON belgesi. Ayrı bir okuyucu yazıldı (`tools/phase2_stage_tuik_sdmx.py`):
  **17.921.552 gözlem + 1.762.120 seri**, kayıp 0.
- *"Sayfa"yı kayıt sanmak*: ETBİS'te her satır bir arama sayfası (içinde 10 site), Market Fiyatı şube
  dosyalarında her satır bir şube (içinde ~1.500 ürün fiyatı). Sayfalar kayıt sanıldığı için veri az görünüyordu.
  `tools/phase2_explode_nested.py` bunları gerçek kayda açtı: ETBİS **402.461 site kaydı + 60.027 site profili**,
  market **9.409.203 şube×ürün fiyatı**.

**v1.6 — üç yeni işletme kaynağı + ürün fiyatı katmanı**
- Market şubeleri (14.791): 4.087'si mevcut POI ile birleşti, 10.704'ü yeni POI.
- EPDK şarj istasyonları (13.059): 157 birleşti, 12.902 yeni POI. Ayrıca 35.891 soket (güç, fiyat) tablosu.
- KTB belgeli turizm tesisleri (24.723): kaynakta **koordinat yok**; 24.695'i il+ilçe ile eşleşti,
  1.294'ü ad benzerliğiyle mevcut POI'ye bağlandı, 23.238'i ilçe düzeyinde `coord_validity='MISSING'` ile saklandı.
  Koordinat uydurulmadı.
- `product` (31.687 ürün) ve `product_price_observation` (**23.947.278** satır): market şube×ürün, Opet ilçe×gün×akaryakıt
  (2015→2026), HKS ulusal hal, İzmir hal, market il günlük fiyat geçmişi. Beş kaynağın da coğrafi bağlanma oranı %100.
- ETBİS: 60.188 e-ticaret sitesi (MERSİS, vergi no, KEP adresi ayrı `restricted_` sütunlarında; ürün paketine girmez).

**v1.7 — TÜİK resmî seri ambarı (SDMX)**
- 432 akış, 1.762.120 seri, 17.921.552 gözlem. REF_AREA kodlarının **tamamı** (1.093) coğrafyaya bağlandı:
  ülke 1, NUTS1 12, NUTS2 26, il 81, ilçe 973 — eşleşmeyen 0.
- `geo_entity`'ye 12 NUTS1 ve 26 NUTS2 bölgesi eklendi; illere NUTS3 kodu (`nuts_code`) yazıldı.

**Birleşik DB şu an**: `canonical_v1.7`, 76,1 milyon kanonik satır (POI 699.809, gayrimenkul fiyatı 29,7 M,
ürün fiyatı 23,9 M, TÜİK serisi 17,9 M, gösterge 332.363).

**Disk uyarısı**: veritabanı 7,3 GB'a çıktı, diskte 2,5 GB kaldı. 24 milyon satırlık ürün fiyatı tablosunun
1,4 GB'lık parquet kopyası silindi (veri DB içinde duruyor; gerekirse `BIG_PARQUET=1` ile yeniden üretilir).
Karar bekleyen: eski `canonical/v1` klasörü (1,3 GB, v1.7 tarafından tamamen kapsanıyor, yeniden üretilebilir) silinsin mi?

## 13. Veritabanı yapılandırma (2026-09-30)

**Market şubelerinin tamamı girdi.** GitHub zinciri 8 koşuda 14.791 şubenin hepsini topladı (10.438 dosya, 722 MB).
Ambar araçlarına **artımlı mod** eklendi (`--incremental`): daha önce işlenmiş dosyalar atlanıyor, yalnız yeni gelenler
ek parça olarak yazılıyor — bundan sonra veri geldikçe aynı komut tekrar çalıştırılacak.
Şube×ürün fiyatı 9,4 M → **21,9 M** satıra çıktı; ürün fiyatı tablosu toplam **36.407.222** satır (muhasebe tam).
Ayrıca `intake_marketfiyati.py` içindeki tip hatası düzeltildi (eksik değer NaN olarak metin sütununa gidip çöküyordu).

**Yapılandırma (structure_v1)** — `tools/phase5_structure_v1.py`, veri eklemez/silmez, yalnız yapı kurar:
- **24 indeks**: coğrafya, işletme, ürün, seri tablolarının sık birleştirilen sütunlarına. 17-36 milyon satırlık
  olgu tablolarına bilerek indeks konmadı; DuckDB sütunlu tarama zaten hızlı, indeks yer harcardı.
- **`urun` şeması — satışa dönük 16 görünüm** (Türkçe sütun adlarıyla): isletme, isletme_kaynak, cografya,
  konut_fiyat_gozlem, urun_katalogu, urun_fiyat_gozlem, gosterge_gozlem, tuik_seri, tuik_gozlem, sarj_soketi,
  eticaret_sitesi, menu_kalemi, teslimat_gozlem, mahalle_zeka, ilce_zeka, ilce_yeni_kaynaklar.
  **Kişisel/kimlik sütunları bu katmanda yok** (telefon, MERSİS, vergi no, KEP e-postası) — makineyle doğrulandı: 0 sızıntı.
  Bu sütunlar `main` şemasında durmaya devam ediyor, yalnız bize açık.
- **Veri sözlüğü**: `meta_tablo` + `meta_kolon` tabloları ve `reports/VERI_SOZLUGU.html` / `.csv`.
  53 tablo, 890 sütun — **hepsinin açıklaması var** (kısıtlı sütunlar sarı işaretli).
- **Analitik**: `analytics.mahalle_market_fiyat` (5.583 mahallede şube/ürün/ortalama fiyat) ve ilçe ürün fiyatı özeti yenilendi.
