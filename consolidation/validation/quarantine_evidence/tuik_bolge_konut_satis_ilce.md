# Kanıt kartı — qstg tuik_bolge.konut_satis_ilce (146.496 satır; Q08 quarantine_macro)
**Karar (düzeltildi, 2026-09-20 akşam): GERÇEK — TÜİK MEDAS güncel (revize) serisi; karantinadan çıkarıldı (QDEC_000002).**

> İlk hüküm (DOĞRULANAMADI) hatalıydı: karşılaştırma Aralık 2024 bülteniyle (revizyon öncesi seri) yapılmıştı. TÜİK seriyi revize etmiş (bülten adı 'Konut ve İş Yeri Satış İstatistikleri'). Güncel bülten (Ağustos 2026, Sayı 58346) ile: 2025 Ocak–Ağu 1.020.207 = 1.020.207 · Ağustos 2025 149.440 = 149.440 · 2026 Ocak–Tem 823.119 = 950.529−127.410 = 823.119 → **birebir**.
> Kaynak zinciri: TÜİK MEDAS kn=73 → warehouse/raw/tuik/medas_konut_satis_ilce_*.csv (elle indirme, 14.09.2026) → collector/tuik_bolge_toplayici.py (ayrıştırıcı ham CSV ile doğrulandı: Küçükçekmece 2024 ham=12.787=tablo) → quarantine_macro/tuik_bolge.sqlite.
> İndirilen Aralık 2024 xls'i **revizyon öncesi seri**dir; `series_version='pre-2025-revision'` etiketiyle ayrı tutulur, güncel seriyle karıştırılmaz.

(Aşağısı ilk incelemenin kaydıdır; tarihçe için korunur.)

## Yöntem
Resmî karşılaştırma: TÜİK "Konut Satış İstatistikleri, Aralık 2024" bülteni (Sayı 54146, yayım 21.01.2025) — metin + "İlçelere Göre Konut Satış Sayıları" tablosu (xls; ham kopya GEOPROP_RAW_INTAKE/tuik_konut_satis/2026-09-20/, sha meta.json'da).

## Bulgular
| Ölçü | Karantina tablosu | TÜİK resmî | Fark |
|---|---|---|---|
| Türkiye 2024 | 1.549.760 | 1.478.025 | +%4,9 |
| Türkiye 2023 | 1.330.135 | 1.225.926 | +%8,5 |
| İstanbul 2024 | 266.471 | 239.213 | +%11,4 |
| Ankara 2024 | 135.395 | 134.046 | +%1,0 |
| İzmir 2024 | 83.772 | 80.398 | +%4,2 |
| İlçe düzeyi 2024 (618 resmî ilçe) | birebir eşit **38** · ±%2 260 · ±%10 242 · >%10 78 | | |
| En büyük sapmalar 2024 | Küçükçekmece 12.787 (resmî 7.348) · Çayırova 3.899 (1.348) · Bağcılar 7.138 (4.613) · Tatvan 2.520 (837) | | |
| 2015–2023 birebir eşleşme oranı | %3–8 | | |

Yapısal olarak gerçekçi (aylık, 12 ay, 2026 için yalnız Oca–Tem dolu, yeniden adlandırılan ilçeler iki adla) ama değerler resmî TÜİK ilçe serisinden sistematik biçimde yüksek ve farklı. Olası açıklama: farklı tanım/kaynak (ör. tapu işlem sayısı, tüm taşınmaz türleri) veya modelle dağıtılmış il toplamı. Kaynak betiği bulunamadığı için "sahte" denemez; "TÜİK" iddiası kanıtlanamadı.

## Aynı paketteki diğer tablolar
nufus_* tabloları daha önce ulusal toplamlarla birebir doğrulanmıştı (QDEC_000001; 2024 = 85.664.944) → kabul edilmişti; bu karar geçerli. goc_il, hanehalki_il, yapi_izin_il/ilce, yapi_ruhsat_amac_ilce, ses_ilce: ayrı kartlar (resmî karşılaştırma yapılana kadar DOĞRULANAMADI).

## Yan kazanç
Resmî TÜİK ilçe konut satış tablosu (2015–2025, satış şekli/durumu kırılımlı) indirildi → NEW_DATA_REVIEW NDR_000003 → official_public olarak staging + kanonik (v1.5).
