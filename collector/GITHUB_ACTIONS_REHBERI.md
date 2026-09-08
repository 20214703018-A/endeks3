# GitHub Actions ile Tüm Verileri 15 Sanal Makinede Paralel Toplama Kılavuzu

Bu sistem, normalde tek bir bilgisayarda **71 saat** sürecek olan tüm Türkiye'nin il, ilçe ve mahalle verilerini, GitHub Actions üzerinde **15 ayrı sanal makineyi aynı anda (paralel)** çalıştırarak **yaklaşık 2.5 – 3 saatte** toplar ve otomatik olarak tek bir ZIP paketinde birleştirir.

---

## 1. Kodları GitHub'a Yükleme / Güncelleme

Eğer deponuzu yeni oluşturuyorsanız veya güncelliyorsanız:

```bash
cd /Users/acar/Desktop/tkgm/extension

git add .
git commit -m "15 paralel sanal makine ve otomatik CSV birlestirici hazırlandı"
git push origin main
```

*(Not: GitHub Student Pack üyeliğiniz olduğu için 40 paralel makine ve 3.000 dakika hakkınız bulunmaktadır. Deponuz ister Public ister Private olsun bu işlem rahatlıkla kotanıza sığar).*

---

## 2. GitHub Actions'ta Taramayı Başlatma

1. GitHub'da reponuzun sayfasına gidin.
2. Üst menüden **"Actions"** sekmesine tıklayın.
3. Sol menüden **"Tüm Türkiye Hızlı Veri Toplayıcı (15 Sanal Makine Paralel)"** iş akışını seçin.
4. Sağ üstteki mavi **"Run workflow"** butonuna tıklayın:

### Seçenekler:
* **Çalışma Modu:**
  * `tum_turkiye_paralel_15_makine` *(Varsayılan)*: 15 sanal makine aynı saniyede açılır, 81 ilin tüm mahallelerini paralel toplar ve sonda tek pakette birleştirir.
  * `ozel_il_secimi`: Sadece tek makinede belirteceğiniz illeri (Örn: `34` veya `34,6,35`) toplar.
* **Hız:** `1.0` *(İstekler arası güvenli bekleme)*
* **Mahalle:** `true` *(Mahalle düzeyindeki demografi, trend, seçim ve satış sonuçları açık)*
* **Run workflow** butonuna basarak başlatın.

---

## 3. 15 Sanal Makinenin Bölge Dağılımı

Tetiklediğiniz anda GitHub şu 15 makineyi aynı anda başlatır:

1. **01 - İstanbul** (34)
2. **02 - Ankara** (6)
3. **03 - İzmir** (35)
4. **04 - Bursa, Yalova, Bilecik** (16, 77, 11)
5. **05 - Kocaeli, Sakarya, Bolu, Düzce** (41, 54, 14, 81)
6. **06 - Trakya & Güney Marmara** (10, 17, 22, 39, 59)
7. **07 - Antalya & Göller Yöresi** (7, 15, 32)
8. **08 - Güney Ege** (9, 20, 48, 64)
9. **09 - Kuzey Ege & İç Batı** (3, 43, 45)
10. **10 - Çukurova & Doğu Akdeniz** (1, 31, 33, 80)
11. **11 - Konya, Karaman, Aksaray** (42, 68, 70)
12. **12 - Orta Anadolu** (26, 38, 40, 50, 51, 66, 71)
13. **13 - Orta Karadeniz & İç Kuzey** (5, 18, 19, 28, 37, 52, 55, 57, 58, 60)
14. **14 - Doğu & Batı Karadeniz** (8, 29, 53, 61, 67, 69, 74, 78)
15. **15 - Doğu & Güneydoğu Anadolu** (2, 4, 12, 13, 21, 23, 24, 25, 27, 30, 36, 44, 46, 47, 49, 56, 62, 63, 65, 72, 73, 75, 76, 79)

---

## 4. Verileri İndirme (Artifacts)

* **Bölgesel İndirme:** Herhangi bir makine işini tamamladığında (örn: İstanbul 2 saat sonra bittiğinde) o bölgenin ZIP paketini hemen indirebilirsiniz.
* **Otomatik Birleştirme (Master Paket):** 15 makinenin tamamı bittiğinde devreye giren `🏆 Tüm Türkiye Verilerini Tek Pakette Birleştir` adımı, tüm bölgelerin CSV'lerini tek bir ana klasörde birleştirir ve **`TUM_TURKIYE_TAMAMI_TEK_PAKET`** adıyla yayınlar.
