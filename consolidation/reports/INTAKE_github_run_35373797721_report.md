# YENİ VERİ GİRİŞİ RAPORU — GitHub Actions run 35373797721 (endeks3, 18 Eylül 2026, zamanlanmış)

## Run durumu
- 41 iş: **40 shard başarılı**, 1 başarısız ("Bütün Shard'ları Birleştir ve Ana Ambara Aktar" → adım "Değişiklikleri Ana Repoya Commit Et").
- **Neden:** `warehouse/` `.gitignore`'da → `git add warehouse/product/*.sqlite` "ignored path" hatasıyla `exit 1`. Birleştirme adımı başarılı ama sonucu ne repoya giriyor ne artifact'e yükleniyor. Karar: workflow düzeltilmeyecek; birleştirme yerelde, kayıt-düzeyinde (Phase 3). Öneri: commit adımını kaldır, istersen birleşik DB'yi `upload-artifact` ile yükle.
- 40 artifact indirildi (`gh run download`), 506,6 MB → 200 SQLite (1,5 GB): `GEOPROP_RAW_INTAKE/github_actions/endeks3/run_35373797721/` (+ run/jobs/artifacts JSON + başarısız iş log'u).

## İçerik (§10.1 inceleme, NDR_000002 → ACCEPT bayraklı)
| Tablo | Satır | Not |
|---|---|---|
| google_places_gozlem | 1.542.706 | **584.101 benzersiz mekân**; hepsi 2026-09-18 tarihli tek günlük anlık görüntü; eski çekimde yalnız 739 mekân vardı → **583.618 yeni mekân** |
| google_places_ticari_yogunluk | 635.949 | |
| google_places_yorumlar_ve_niyet | 422.851 | |
| google_places_arama_gecmisi | 221.194 | |
| emlak_ilanlari.ilanlar | 35.046 | ilan ID, il/ilçe/mahalle(+id), koordinat, fiyat, m² |
| airbnb_ilanlar | 2.982 | |
| mekan_menu_kalemleri / fiziksel_lojistik_noktalari | **0** | iki toplayıcı bu run'da çıktı üretmemiş (EMPTY_COLLECTOR_OUTPUT) |

## Dedektörler
- Eski sentetik formül izi (yorum = değerlendirme×0,45): **0 satır**.
- Türkiye kutusu dışı koordinat: 8.012 (%0,5) → `GEO_CONFLICT`.
- **`il` sütunu güvenilmez:** çoğu satırda il yerine arama kategorisi ("giyim", "otomotiv", "kuaför") → alan kayması. İl/ilçe/mahalle bu kaynaktan alınmayacak; koordinatla spatial join (§4). Toplayıcıda düzeltilmesi önerilir.
- Aynı mekân + aynı `observed_at` shard çakışması: 196; `payload_sha256` birebir tekrar: 0 (farklı arama terimleri).

## Staging
- 249 kaynak → poi_business 2.940.433 · listings 38.028 · mobility_logistics 17.147 satır; **UNACCOUNTED 0**. Katalog güncellendi: staging toplam **55.412.730** satır.
- Sınıf: `web_research`; dağıtım agregat/türev.

## Olay
- Artımlı Phase 1 `SOURCE_MUTATION_ALERT` verdi: canlı Yemeksepeti toplayıcısının kendi kayıt dosyasına ekleme yapması (kaynak veride değişim yok). Çözüm: `P1_EXCLUDE` ile canlı intake dizini taramadan hariç tutuluyor; toplayıcı bitince taranacak.
- Boş tablo çıktısında bir yazdırma hatası (motor) düzeltildi.
