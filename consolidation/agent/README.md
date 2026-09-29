# GEOPROP Agent — doğal dille soru, kurallı (deterministik) cevap

## Tek cümlede
Soruyu **Claude anlar**, sayıları **kurallı hesap motoru üretir**. Yapay zekâ hiçbir sayıyı kendisi hesaplamaz; bu yüzden aynı soru aynı veriyle her seferinde **birebir aynı sonucu** verir.

```
"İzmir'de kira getirisi en yüksek ilçeler?"
   │  Claude: soruyu anlar, ölçüyü ve yeri bulur
   ▼
İş emri (plan):  {analiz: sirala, olcu: konut_brut_kira_getirisi, seviye: ilce, kapsam: GEO_IL_35}
   │  Motor: ontolojiye göre denetler → DuckDB'de salt okunur sorgu
   ▼
Sonuç: tablo + nötr cümleler + uyarılar + künye (kaynak, dönem, plan kimliği)
   │  Sayı denetimi: Claude'un cevabındaki her sayı sonuçta var mı?
   ▼
Cevap
```

## Parçalar (`geoprop_agent/`)
| Dosya | Ne yapar |
|---|---|
| `ontology.yaml` | **Kavram haritası** — agent'ın bildiği her şey: kavramlar (İl, İlçe, Mahalle, İşletme…), ilişkiler, 107 ölçü (eş anlamlarıyla), birim düzeltmeleri, tuzak kuralları, analiz kataloğu. Yeni ölçü eklemek = buraya bir satır. |
| `ontology.py` | YAML'ı okur; "kira getirisi" → `konut_brut_kira_getirisi` gibi eş anlam çözer; belirsizse aday listesi verir. |
| `veri.py` | Kanonik veritabanını **salt okunur** açar. Birim düzeltmesi ve tekilleştirme sorgu anında yapılır; diske hiçbir şey yazılmaz. |
| `yer.py` | Yer çözücü: "Bostanlı, Karşıyaka" → geo_id; koordinattan mahalle bulur; belirsizse **tahmin etmez**, aday listesi döner. |
| `plan.py` | İş emri şeması (16 analiz tipi). Claude yalnız bu şemaya uyan plan üretebilir. |
| `analizler.py` | 13 temel analizin kurallı hesapları. |
| `analizler_ileri.py` | Uygun bölge skoru, gelecek projeksiyonu, bölge gelecek raporu. |
| `projeksiyon.py` | Deterministik zaman serisi projeksiyonu (sınanmış model topluluğu, ölçülmüş hata aralığı). |
| `profiller.yaml` | Uygun bölge profilleri: her kriterin yönü, ağırlığı ve gerekçesi (varsayımlar). |
| `motor.py` | Tek giriş noktası: plan → doğrula → çalıştır → sonuç. |
| `sonuc.py` | Sonuç biçimi + parmak izleri (`plan_hash`, `sonuc_hash`). |
| `araclar.py` | Claude'a verilen 18 araç (ontoloji_ara, yer_coz + her analiz). MCP ve API aynı araçları kullanır. |
| `yonerge.py` | Claude'un çalışma yönergesi (sayı üretme yok, yorum yok, belirsizlikte sor). |
| `denetim.py` | Sayı denetimi: cevaptaki sayılar araç sonuçlarında yoksa işaretler. |
| `mcp_sunucu.py` | Claude masaüstü/Claude Code bağlantısı (ek paket gerektirmez). |
| `sohbet.py` | Claude API ile kendi sohbet ekranımız (anthropic paketi + API anahtarı gerekir). |

## Analiz kataloğu
| Analiz | Örnek soru |
|---|---|
| `yer_profili` | "Bostanlı'nın profili" — tüm güncel ölçüler + ilçe/il değerleri + işletme kırılımı + okul/durak/eczane sayıları |
| `karsilastir` | "Kadıköy, Karşıyaka ve Çankaya'yı m² fiyatı, kira getirisi ve nüfusla karşılaştır" |
| `sirala` | "İzmir'de kira getirisi en yüksek 10 mahalle (en az 20 ilanı olan)" |
| `zaman_serisi` | "Kadıköy konut m² fiyatı son 2 yılda nasıl değişti" |
| `dagilim` | "Kadıköy mahallelerinde kira m² dağılımı" (medyan, yüzdelikler) |
| `yakin_cevre` | "Şu koordinatın 500 m çevresinde ne var" — işletmeler, en yakın durak/okul/eczane, yaklaşık nüfus |
| `yogunluk` | "İzmir ilçelerinde 1.000 kişiye düşen kafe" |
| `kategori_dagilimi` | "Caferağa'daki işletmelerin kategori dağılımı" |
| `benzer_yerler` | "Bostanlı'ya en çok benzeyen mahalleler" |
| `harita_hareketliligi` | "Karşıyaka'da yeme-içme OSM haritası değişimi" (açılış/kapanış **değil**) |
| `isletme_listesi` | "Caferağa'daki kafelerin koordinatlı listesi" |
| `iliski` | "İlçelerde m² fiyatı ile SES skoru arasındaki korelasyon" |
| `veri_kapsami` | "Kira getirisi verisi hangi seviyede, kaç yerde var" |

## İleri modüller — uygun bölge ve gelecek projeksiyonu (MODEL çıktısı)
Bunlar ölçüm değil **model** çıktısıdır; ama yine deterministiktir ve tüm varsayımlar açıktır.

| Analiz | Örnek soru | Nasıl hesaplanır |
|---|---|---|
| `uygun_bolge` | "Karşıyaka'da kafe açmak için en uygun mahalleler", "Restoran yazılımını İzmir'de hangi ilçelerde pazarlamalı", "Premium ürün için hangi iller" | [`profiller.yaml`](geoprop_agent/profiller.yaml)'daki profil (15 hazır: 6 işletme, 5 yazılım, 4 ürün) ya da özel kriterler. Her kriterde bölgenin adaylar içindeki yüzdelik sırası (rakip/maliyet gibi 'eksi' kriterlerde ters), ağırlıklı ortalama → 0–100 skor. **Sıra aralığı**: her kriter tek tek çıkarıldığında sıranın ne kadar oynadığı (sağlamlık). Ağırlıklar soruda değiştirilebilir. |
| `gelecek_projeksiyonu` | "Kadıköy konut m² fiyatı 12 ay sonra", "Karşıyaka nüfusu 2030" | Birkaç basit model (son 12/36 dönem eğilimi, sönümlü trend, düz; aylık sayımlarda mevsimsel) serinin geçmişinde sınanır; hatası az olan daha çok ağırlık alır. **%80 aralık** modelin geçmişteki gerçek hatalarından. Kaynağın kendi tahmini (varsa) yan yana. |
| `bolge_gelecek_raporu` | "Bostanlı'nın önümüzdeki yılı" | Nüfus, konut/arsa m², kira, satış hacmi projeksiyonları + öncü göstergeler (ruhsat, kullanma izni, göç, OSM akışı). Mahallede seri yoksa ilçe serisi kullanılır ve belirtilir. |

**Ölçülmüş doğruluk (2026-09-28, `tests/projeksiyon_degerlendirme.py`):** seri kesim tarihinde kesilip ileri tahmin edildi, gerçekleşenle karşılaştırıldı. "Düz model" = değişim yok varsayımı. İyi ayarlı %80 aralıkta kapsama ≈ %80 olmalı.

| Ölçü | Seviye | Kesim → ufuk | Seri | Medyan hata | Düz model | %80 aralık kapsaması |
|---|---|---|---|---|---|---|
| Konut m² | il | 2025-08 → 12 ay | 81 | %3,6 | %18,2 | %85,2 ✅ |
| Konut m² | ilçe | 2025-08 → 12 ay | 922 | %9,5 | %21,8 | %72,9 ⚠️ dar |
| Kira m² | ilçe | 2025-08 → 12 ay | 755 | %14,9 | %20,9 | %78,9 ✅ |
| Konut m² | mahalle (İst.+İzm.) | 2025-08 → 12 ay | 1.879 | %14,2 | %23,7 | %63,7 ⚠️ dar |
| Nüfus | ilçe | 2020 → 5 yıl | 970 | %3,2 | %5,1 | %70,2 ⚠️ dar |

Sonuç: model her seviyede "değişim yok" varsayımından daha isabetli; ancak ilçe/mahalle fiyatlarında ve nüfusta %80 aralık olması gerekenden **dar** (gerçek belirsizlik daha büyük). Küçük birimlerde (az ilanlı mahalle) hata belirgin artıyor. Aralık kalibrasyonu sonraki sürümde ayrı bir doğrulama kesimiyle düzeltilecek.

**Web kaynakları nasıl girer?** Claude web'de arar (TCMB enflasyon beklentisi, TÜİK nüfus projeksiyonu, planlanan metro/proje…) ve bulduğunu plana `dis_varsayimlar` olarak **kaynak adı + adres + erişim tarihi + alıntı** ile ekler. Motor yalnız iki anahtarı hesaba katar: `yillik_enflasyon` (reel fiyat dönüşümü) ve `nufus_yillik_buyume` (dış nüfus senaryosu); diğerleri `baglam` olarak künyeye yazılır. Varsayımlar plan parmak izine dahildir → aynı web bilgisi, aynı sonuç. MCP modunda web aramasını Claude uygulamasının kendi aracı, API modunda sunucu tarafı `web_search`/`web_fetch` yapar.

## Kullanım
```bash
cd ~/Desktop/endeks3/consolidation/agent
python3 -m geoprop_agent plan '{"analiz":"sirala","olcu":"kira getirisi","seviye":"ilce","kapsam":"İzmir"}'
python3 -m geoprop_agent yer "Bostanlı, Karşıyaka"
python3 -m geoprop_agent olcu "konut satışı"
python3 -m pytest -q tests/          # 29 altın plan testi (deterministiklik dahil)
python3 tests/projeksiyon_degerlendirme.py konut_satis_m2 ilce 2025-08 12   # geçmiş kesimle tahmin doğruluğu
```

**Claude masaüstü / Claude Code'a bağlamak (MCP):** sunucu komutu
`python3 -m geoprop_agent mcp` (çalışma dizini: bu klasör). Açılışta ~20 sn ısınır (yer adları + poligon alanları), sonra sorular ~0,1–1 sn.

**Kendi sohbet ekranımız (Claude API):** `pip install anthropic`, API anahtarı, sonra `python3 -m geoprop_agent sohbet`. Varsayılan model `claude-opus-5` (`GEOPROP_MODEL` ile değişir); reddedilen istekler sunucu tarafında önerilen yedek modele yönlenir (`fallbacks: "default"`).

## Güvenceler
- **Veritabanı salt okunur**: kanonik dosya `READ_ONLY` bağlanır; sunum katmanı bellekte kurulur.
- **Deterministik**: sabit sıralama + bağ kırıcı (geo_id), 6 anlamlı basamağa yuvarlama; testler her planı iki kez çalıştırıp parmak izini karşılaştırır.
- **Tahmin yok**: belirsiz yer/ölçü → aday listesi; veri yok → "veri yok".
- **Kural 1 (yorum yok)**: çıktılar sayı, tablo ve nötr cümledir; "iyi/kötü/yatırıma uygun" yazılmaz.
- **Kişisel veri yok**: telefon/adres çıktıya girmez.

## Bu sürümde bulunan veri sorunları (düzeltme sorgu anında; kanonik veritabanı değiştirilmedi)
1. **Ölçek karışıklığı**: `stg_ej_region_index` değişim oranlarını **yüzde** (27,58), diğer kaynaklar **kesir** (0,2758) yazıyor; hepsinin birimi "ratio" etiketli. Örnek: GEO_ILCE_000050 konut yıllık değişim 2026-08 → −0,2148 ve −21,48. Motor hepsini yüzdeye çevirir (`olcek_kurallari`).
2. **Kira getirisi** tüm kaynaklarda yüzde (medyan 6,5) ama "ratio" etiketli.
3. **Yinelenen gözlemler**: fiyat tablosunda 706.020 hücrede birden çok satır (birim düzeltmesinden önce 48.063'ünde değer farklı; çoğu yüzde/kesir karışıklığından). Kural: en güncel kaynak; çelişki bayrağı ve min–max aralığı sonuçta gösterilir.
4. **BDDK dönem biçimi**: aynı çeyrek "2024-09" ve "2024-9" diye iki biçimde; motor tek biçime çevirir.
5. **Katalog dışı bırakılanlar** (anlamı/ölçeği doğrulanamadı): arsa "bina yaşı", arsa/tarla stok değişim oranları.

## Sonraki adımlar (onay gerektirir)
- **Sunum veritabanı**: yukarıdaki düzeltmeleri kalıcı ve hızlı bir ayrı dosyaya yazmak (kanonik dosyaya dokunmadan).
- Gece toplanan yeni veriler (market fiyatları, şarj istasyonları, otobüs seferleri, hal fiyatları…) karantina incelemesinden sonra ontolojiye eklenir.
- Altın soru seti: doğal dil → plan eşleşmesini ölçen 100 soruluk test (Claude API gerektirir).
