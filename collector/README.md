# Açık API Veri Toplayıcı (Collector)

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
