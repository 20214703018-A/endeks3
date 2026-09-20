# Google toplayıcı — koordinat ve kimlik kusuru (kök neden, 2026-09-20)
Betik: GEOPROP/collector/google_places_ve_yogunluk_toplayici.py · Koşu: 2026-09-18 (tüm 1.544.193 gözlem) · Kaynak etiketi 'Google Maps PB'

## Belirti
7.782 işletme kaydında koordinat geçersiz: tam sayı derece (7.285; ör. 40,46 / 35,41), lon=lat (96), Türkiye dışı (401). Ham gözlemde de aynı (raw_lat='38.0', raw_lon='44.0'); yani staging/kanonik hatası değil, kaynak hatası.

## Kök neden (betik satır 498–511, `extract_props`)
Birincil yol `v14[9] = [None, None, lat, lon]` bulunamayınca yedek yol tüm cevabı gezip **ilk uygun sayı çiftini** koordinat sayıyor: `if 35.0 <= f0 <= 43.0 and 25.0 <= f1 <= 46.0: lat, lon = f0, f1`. Skaler dalda `len(str(fval)) > 6` (ondalık) kontrolü var, **liste-çifti dalında yok** → sayaç/indeks tam sayıları koordinat oldu.

## İkinci kusur: kimlik
Aynı yedek yol kimliği `sha256(f"{name}_{lat}_{lon}")[:24]` olarak üretiyor (satır 538). Kanonikte Google kayıtlarının **560.290 / 576.575'i sha24**, yalnız 16.178'i gerçek Google place_id. Sonuç: kimlik Google'a sorulamaz; aynı işletme farklı koordinatla tekrar çekilirse farklı kimlik alır; Google-içi tekrar kesit karşılaştırması bu kimlikle güvenilmez (§6.4'e not).

## Etki ve çare
- 360 place_id'nin başka gözleminde geçerli koordinat var → v1.5'te kurtarılır (yöntem etiketi RECOVERED_FROM_OTHER_OBSERVATION).
- Kalan ~7.400: adres bağlamı mahalle merkezi ile yaklaşık konum (coord_validity='APPROX_CONTEXT_CENTROID', ±mahalle) ya da yeniden çekim.
- Places API planı: kimlik yerine **ad+koordinat ile Text Search (IDs only, ücretsiz) → Place Details Essentials (types, ~5 $/1.000)**.
- Toplayıcı düzeltmesi (ileride yeni çekim yapılırsa): liste-çifti dalına ondalık/hassasiyet kontrolü; kimlik için gerçek place_id (v14[78]) zorunlu, yoksa kayıt 'kimliksiz' bayrağıyla.
