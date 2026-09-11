# GEOPROP yerel alıcı arsa analizi

Gerekli Python bağımlılıklarını kurduktan sonra proje kökünde:

```bash
python3 demo/server.py
```

Ana sayfa doğrudan alıcı arsa analizine açılır:
`http://localhost:8088/`. Canlı parsel sorgusu koordinatla veya
mahalle kimliği + ada + parsel ile yapılır. TKGM yanıtı bulunamazsa girilen
bilgilerle istatistiksel analiz sürer; eksik imar alanları tahmin edilmez.

Emsal ürün indeksi yoksa sunucu eski yerel ilan veritabanını kullanır. Güncel
indeksi üretmek için README'deki `arsa_emsal_indeksi.py` komutunu çalıştırın.

Arayüz açılmazsa önce `http://127.0.0.1:8088/api/v1/veri-durumu` adresini
kontrol edin. Tarayıcının yarım açık bağlantısının sunucuyu bloke etmediğini
doğrulayan regresyon testi:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests \
  -p 'test_demo_server_concurrency.py' -v
```

## Piyasa Toplayıcı Chrome eklentisi — kurulum (1 dk)

Chrome / Edge / Brave'de:

1. `chrome://extensions` aç
2. Sağ üst **Geliştirici modu** → açık
3. **Paketlenmemiş öğe yükle** → bu `extension/` klasörünü seç

Emlakjet bölge endeksi için backend gerekmez; kayıtlar eklentinin kendi
yerel veritabanına yazılır. İlan toplama kısmının beklediği Node backend
(`PORT=3001`) bu checkout'ta bulunmadığından o akış şu anda tek başına
çalışmaz. Eksik sözleşme ve birleştirme planı `docs/INCELEME_RAPORU.md`
içinde kayıtlıdır.

---

## 1) Emlakjet bölge endeksi — asıl akış

`emlakjet.com/emlak-piyasasi/satilik-konut` sayfasındaki
Türkiye → il → ilçe → mahalle tablosunun beslendiği veri ucundan tüm
hiyerarşiyi gezer. Bir sayfada tıklaya tıklaya ineceğiniz yeri arka planda
kendisi gezer; sekme açık kalmak zorunda değildir.

Popup'ta **🗺️ Emlakjet bölge endeksi**:

- **Konut / Arsa** — kayıtlar `tip` alanıyla ayrı tutulur, ayrı CSV çıkar
- **Derinlik**
  - *İl* — 81 il + her ilin ilçe özetleri
  - *İlçe* (varsayılan) — + Türkiye'deki tüm mahallelerin özeti
  - *Mahalle* — her mahalleye tek tek girer: aylık seri ve
    yaş / oda / kat / ısıtma dağılımları da düşer (uzun sürer)
- **Bekleme** — istekler arası en az 0,8 sn
- **Başlat / Devam / Duraklat / Durdur / Kuyruğu sıfırla**

Her bölge için kaydedilenler:

| Küme | İçerik |
| --- | --- |
| `bolge` | m² fiyat (min/ort/max), ortalama fiyat, ortalama m², ilan sayısı, aylık ve yıllık değişim, amortisman, getiri, ortalama bina yaşı, ilan süresi, endeks, kira m² fiyatı |
| `trend` | 2021'den bugüne aylık seri; `projeksiyon` sütunu gerçekleşen ile tahmin ayrımını verir |
| `dagilim` | bina yaşı, oda, kat, ısıtma, alan kırılımlarında fiyat / ilan sayısı / pay |

Her satırda `kaynak` (verinin göründüğü Emlak Piyasası sayfası), `donem`,
`toplanmaZamani` ve `olcumKaynagi` bulunur; alıntılanabilir olması için.

**Kesinti dayanıklılığı.** Kuyruk `chrome.storage.local`'da tutulur ve her
bölgeden sonra ilerleme yazılır. Servis askıya alınsa, sekme kapansa,
tarayıcı ya da bilgisayar yeniden başlasa bile `chrome.alarms` ile uyanıp
kaldığı yerden sürer. Yazımlar bölge+dönem anahtarlıdır: yeniden tarama
satır çoğaltmaz, üzerine yazar.

**Verileri görmek/aramak:** popup → **📂 Kayıtları aç · ara · CSV indir**.
İl/ilçe/mahalle adıyla arama, tip ve seviye süzgeci, konut ve arsa için
ayrı CSV indirme buradadır.

---

## 2) İlan toplama (Emlakjet / Sahibinden / Hepsiemlak / Zingat)

Bu kısım `localhost:3001` backend'ine yazar. Bu checkout'ta `package.json`
ve `server.js` bulunmadığı için backend depoya alınana kadar ilan kaydı
kullanılamaz; yalnız Emlakjet bölge endeksi yerel çalışır.

Önizleme ve tek sayfa kaydı otomatik gezinmez. Otomatik kayıt/sayfalama
varsayılan olarak kapalıdır; yalnızca ilgili siteden yazılı veri toplama
izniniz varsa popup'taki izin onayını işaretleyip başlatın.

Desteklenen sayfalar:
- Piyasa/analiz tabloları: Endeksa, Emlakjet
- İlan arama/sonuç sayfaları: Emlakjet, Sahibinden, Hepsiemlak, Zingat
  (fiyat kartları otomatik bulunur: başlık + fiyat + m² + link)
- Kök sayfalar (`/satilik-konut` gibi, URL'de şehir yoksa):
  konum breadcrumb'dan, yoksa her kartın kendi konum yazısından
  çözülür; hiçbiri yoksa popup'taki il/ilçe kutularına yazılır

Piyasa satırları `data/piyasa-ortalamalari.json` ve `.csv`; ilanların son
durumu `data/ilanlar.json` ve `.csv`; zaman içindeki fiyat gözlemleri
`data/ilan-gozlemleri.csv` dosyalarına atomik olarak yazılır.

**İlanlarda sonraki sayfalara geç** sayfa 2, 3… bağlantılarını izler.
Bekleme en az 4 saniyedir; azami sayfa sınırı vardır. Veri çıkmayan sayfa
taramayı bitirmez, atlanır; CAPTCHA, oturum veya erişim engeli görülürse
aşmaya çalışmadan durur. Durum `chrome.storage.local` içinde tutulur ve
**Taramayı durdur** ile kesilebilir.

Sorun çıkarsa: popup'taki **🔍 Teşhis** düğmesi sayfanın ne gördüğünü
(URL, coğrafya, gazete sayıları, ilk 8 satırın il/ilçe/mahalle/m²/oda
durumu) JSON döker — çıktıyı gönderin.

---

## Kurallar

- Hiçbir sunucu sitelere istek atmaz; istekleri sizin tarayıcınız yapar.
- Otomasyon yazılı izin onayı olmadan başlamaz ve varsayılan kapalıdır.
- Bu teknik onay, ilgili sitenin gerçek izninin yerine geçmez.
- Bekleme süreleri alt sınırlıdır; CAPTCHA veya erişim engeli aşılmaz.
