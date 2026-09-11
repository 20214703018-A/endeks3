# GEOPROP veri platformu ve alıcı arsa analizi

Bu checkout artık dört birlikte çalışan çekirdek modül içerir:

1. 9.140.813 Silver gözlemden kayıpsız veri soy ağacı ve 2.566.651 veri
   çatışması için açıklanabilir Gold kararı,
2. 16.027 benzersiz ve yayımlanabilir arsa ilanından linksiz emsal indeksi,
3. istek anında salt-okunur TKGM kadastro ve kaynak bulunursa E-Plan/belediye
   imar katmanı,
4. alıcı için emsal, güven, fiyat aralığı, faktörler ve doğrulanmış KAKS/TAKS
   varsa örnek proje kapasitesi gösteren arsa analiz ekranı.

Yerel alıcı ekranını açmak için:

```bash
python3 demo/server.py
```

Ardından `http://localhost:8088/` adresine gidin. Ürün API'si
`POST /api/v1/arsa/analiz`, canlı parsel API'si `GET /api/v1/parsel/canli`,
durum API'si `GET /api/v1/veri-durumu` adresindedir. Kullanıcıya kaynak veya
ilan bağlantısı verilmez; iç kaynak karması ve veri soy ağacı denetim için
korunur.

## Veri ürün indeksini yeniden üretme

Bu komut yalnız açıkça `--calistir` verildiğinde çalışır ve Silver arsa
bölümünden atomik bir SQLite ürün görünümü üretir:

```bash
python3 tools/arsa_emsal_indeksi.py --calistir \
  --silver-root warehouse/silver \
  --source-registry reports/kaynak-sicili.json \
  --output-database warehouse/product/arsa_emsalleri.sqlite
```

Çıktı ilan URL'si içermez. Aynı ilan kimliğinin farklı gözlemleri kalite,
güncellik ve sabit kaynak karmasıyla tekilleştirilir; geçmiş sürümler Silver'da
silinmez.

## Eski toplayıcı kapsamı

> Bu klasör; Chrome eklentisi, Python toplayıcıları, demo arayüzleri ve çok
> sayıda parçalı veri paketini birlikte içerir. Mevcut kapsam ve güvenilirlik
> sınırları için [`docs/INCELEME_RAPORU.md`](docs/INCELEME_RAPORU.md), tam
> makine-okunur dosya/ZIP dökümü için
> [`reports/veri-envanteri.json`](reports/veri-envanteri.json) dosyasına bakın.

Bu araç; Emlakjet ve Endeksa açık API uçlarından (**Demografi**, **Hemşehri/Kütük Dağılımı**, **Seçim Sonuçları**, **Coğrafi Poligonlar** ve **Fiyat Endeksi**) verileri hiyerarşik (Türkiye → İl → İlçe) olarak toplayıp yerel SQLite veritabanına (`data/piyasa_verileri.db`) ve Excel uyumlu CSV dosyalarına kaydeder.

---

## Özellikler

1. **Kesintiye Dayanıklı (Resumable):** Tarama sırasında internet kesilse, bilgisayar kapansa veya `CTRL+C` ile durdursanız bile `durum.json` üzerinden kaydedilir. Yeniden çalıştırıldığında tamamlanan il ve ilçeler atlanır, kaldığı yerden devam eder.
2. **Akıllı Hız Sınırlayıcı (Rate Limit Koruması):** API sunucularının aşırı yüklenmemesi için istekler arasında bekleme süresi uygular, 429/503 yanıtlarında otomatik bekleyerek tekrar dener.
3. **Excel Uyumlu CSV Dışa Aktarma:** `UTF-8 with BOM` kodlaması sayesinde Türkçe karakterler (ç, ğ, ı, ö, ş, ü) Excel'de bozulmadan açılır.

---

## Kurulum

Python 3 yüklü olması yeterlidir. Ekstra bir kütüphane (`pip install`) gerekmez, tamamen Python standart kütüphaneleriyle çalışır.

```bash
cd extension/collector
```

---

## Kullanım Örnekleri

### 1. Test veya Belirli İller İçin Çalıştırma
Örneğin sadece **İstanbul (34)** veya **Yalova (77)** için çalıştırmak:
```bash
python3 collector.py --iller 77
python3 collector.py --iller 34,6,35
```

### 2. Sadece İl Düzeyi (İlçelere İnmeden Hızlı Tarama)
81 ilin genel özetini 1–2 dakika içinde çekmek için:
```bash
python3 collector.py --sadece-il
```

### 3. Tüm Türkiye'yi Tarama (81 İl + ~973 İlçe)
```bash
python3 collector.py
```

### 4. Sadece Belirli Modülleri Çekme
Varsayılan olarak `demografi,hemsehri,secim,fiyat,poligon` modüllerinin hepsi çekilir. İsterseniz sadece belirli modülleri seçebilirsiniz:
```bash
python3 collector.py --iller 34 --moduller demografi,hemsehri
```

### 5. İstek Hızını Ayarlama
İstekler arası bekleme süresini (saniye cinsinden) değiştirebilirsiniz (Varsayılan: 1.0 saniye):
```bash
python3 collector.py --iller 34 --hiz 0.8
```

### 6. Sıfırdan Başlama
Önceki ilerlemeyi sıfırlayıp baştan başlamak için:
```bash
python3 collector.py --sifirla
```

---

## Verileri CSV / Excel Olarak Dışa Aktarma

Veritabanındaki tüm tabloları tek komutla CSV formatına dökmek için:
```bash
python3 export_csv.py
```
Oluşturulan CSV dosyaları `data/csv_ciktilari/` klasörüne kaydedilir:
- `demografi.csv`: Nüfus, yaş piramidi, eğitim, SES skoru, hane geliri ve harcama kalemleri.
- `yillik_satislar_2010_2024.csv`: 2010'dan 2024'e kadar her yılın konut, ipotekli satış ve arsa satış sayıları.
- `hemsehri_dagilimi.csv`: İlçe bazında vatandaşların kütük (nüfusa kayıtlı oldukları il) dağılımı.
- `secim_sonuclari.csv`: Seçim sandık, seçmen, oy oranları ve kazanan partiler.
- `fiyat_ozet_konut_arsa.csv`: Satılık/kiralık m² fiyatları, amortisman, getiri ve bina yaşı.
- `poligonlar/`: İlçe ve mahalle sınırlarının koordinat poligonları (`.json`).
