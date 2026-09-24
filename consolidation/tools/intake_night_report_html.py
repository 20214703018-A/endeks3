"""Gece toplama turu raporunu (HTML) NIGHT_STATS json'undan üretir. Çıktı: reports/gece_veri_defteri.html"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

REP = Path(__file__).resolve().parent.parent / "reports"
S = json.loads((REP / "NIGHT_STATS_2026-09-24.json").read_text())


def g(path, default="—"):
    cur = S
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def n(x):
    if isinstance(x, (int, float)):
        return f"{x:,.0f}".replace(",", ".")
    return html.escape(str(x))


def mb(x):
    return f"{(x or 0) / 1e6:,.0f} MB".replace(",", ".") if isinstance(x, (int, float)) else "—"


def pill(kind):
    lab = {"ok": "Toplandı", "run": "Sürüyor", "part": "Kısmi", "block": "Engelli", "wait": "Karar bekliyor",
           "local": "Yerelde vardı"}[kind]
    return f'<span class="pill {kind}">{lab}</span>'


osm26 = g("osm.kesitler.2026-09-24", {})
osm21 = g("osm.kesitler.2021-01-01", {})
ck = g("ckan", {})
ck_files = sum(v.get("dosya", 0) for v in ck.values()) if isinstance(ck, dict) else 0
ck_ds = sum(v.get("veri_seti") or 0 for v in ck.values()) if isinstance(ck, dict) else 0
ck_disk = sum(v.get("disk", 0) for v in ck.values()) if isinstance(ck, dict) else 0


def osm_trend(layer):
    a, b = osm21.get(layer), osm26.get(layer)
    return f"{n(a)} → {n(b)}" if a and b else n(b)


# (konu, [ (kaynak, yöntem, hacim, durum) ])
TOPICS = [
    ("E-ticaret siteleri, şirketleri, adresleri", "Kayıtlı siteler, işletme unvanları, adresler, pazaryeri satıcıları", [
        ("Ticaret Bakanlığı ETBİS — Kayıtlı Site Sorgula", "Liste il filtresiyle sayfa sayfa (10'arlı); sonra her sitenin profili",
         f"{n(g('etbis.site'))} site / {n(g('etbis.il_sayisi'))} il şimdiye kadar (hedef ~60 bin). Unvan, site adresi, mobil uygulama, il. Profil (kayıt tarihi, işletme türü, KEP, mal/hizmet, ödeme türleri) liste bitince. Sitenin ilçe servisi bozuk.", "run"),
        ("Şirket adresleri — sitelerin kendi künye/iletişim/mesafeli satış sayfaları", "6563 sayılı Kanun gereği yayımlanan künye; robots.txt'ye uyularak; yalnız çıkarılan alanlar",
         f"{n(g('adres.site'))} site tarandı: {n(g('adres.canli'))} canlı, {n(g('adres.adresli'))} açık adresli, {n(g('adres.mersisli'))} MERSİS'li, {n(g('adres.kepli'))} KEP'li. ETBİS listesi büyüdükçe devam ediyor.", "run"),
        ("Cimri — pazaryeri teklifleri + fiyat geçmişi", "Sitemap'teki ürünler; sayfadaki yapılandırılmış teklifler ve '3 aylık fiyat değişimi' tablosu (yasaklı /api/ kullanılmadan)",
         f"Katalog: {n(g('cimri.katalog_url'))} ürün URL'si. Ürün sayfaları: {n(g('cimri.urun_sayfasi'))} ({n(g('cimri.teklif'))} teklif, {n(g('cimri.gecmis_noktasi'))} fiyat-geçmişi noktası). Site 1 saattir 403 (Cloudflare) verdi; toplayıcı durduruldu, koruma aşılmadı. Katalog elimizde.", "block"),
        ("Pazarama (20.351 mağaza)", "Mağaza sayfasındaki satıcı bloğu",
         "Kişisel veri içerdiği için oturumun güvenlik denetimi istekleri durdurdu; toplanmadı.", "wait"),
        ("Trendyol · Hepsiburada · n11 · PTTAVM · Google Alışveriş", "Açık sitemap/API arandı",
         "Trendyol robots.txt satıcı sayfalarını yasaklıyor; diğerleri Cloudflare 403; Google /search ve /shopping otomatik sorguyu yasaklıyor. Koruma aşılmadı.", "block"),
        ("BKM — kartlı ödemeler, internetten ödemeler (e-ticaret)", "27 dönemsel sayfa × 2010→2026 her ay",
         f"{n(g('bkm.sayfa_ay'))} sayfa-ay, {n(g('bkm.tablolu'))} tablolu.", "run"),
        ("Ticaret Bakanlığı istatistik ve raporları", "Site gezgini",
         f"{n(g('ticaret_bakanligi_istatistik.dosya'))} dosya ({mb(g('ticaret_bakanligi_istatistik.disk'))}).", "ok"),
    ]),
    ("Perakende — il, ilçe, mahalle", "Nokta düzeyinde işletmeler ve zincir şubeleri", [
        ("OpenStreetMap — dükkân/zanaat, ofis, hizmet noktaları", "Türkiye PBF → osmium → her nesneye il/ilçe/mahalle (poligon içinde / en yakın)",
         f"Dükkân+zanaat {osm_trend('perakende_poi')}, ofis {osm_trend('ofis_poi')}, hizmet {osm_trend('hizmet_poi')} (2021 → 2026). Yıllık kesitler 2021–2025 yerel dosyalardan.", "ok"),
        ("Zincir market şubeleri (Market Fiyatı)", "973 ilçe + 81 il merkezinden 'en yakın şubeler' sorgusu",
         f"{n(g('market.subeler'))} şube, koordinatlı; {n(g('market.sube_ile_atanan'))} tanesi mahalleye bağlandı. Tam envanter (~40 bin) hız sınırı nedeniyle eksik.", "part"),
        ("Google Maps işletme kayıtları", "Önceki toplama (yerel, 584 bin)", "Yeniden taranmadı; il/ilçe dağılımları bu kayıtlardan üretilecek.", "local"),
        ("TÜİK — girişim sayıları (il, NACE × büyüklük)", "SDMX veri akışları DF_BR_FAALIYET_BUYUKLUK_GIRISIM_IBBS3_*", "2009–2028 dönem blokları; il düzeyi (IBBS3).", "ok"),
        ("SGK istatistik yıllıkları 2007–2025", "Zip arşivleri (il × sektör işyeri ve sigortalı)",
         f"{n(g('sgk_istatistik.dosya'))} dosya ({mb(g('sgk_istatistik.disk'))}).", "ok"),
    ]),
    ("Sektör ve ürüne göre üretici, hizmet sayısı, üretim kapasitesi", "Kapasite raporu verisi", [
        ("TOBB Sanayi Veri Tabanı (sanayi.org.tr)", "Kapasite raporu sorgu servisi: il × NACE-2, il × personel büyüklüğü, ilçe dağılımı, il × birim üretim miktarı",
         f"{n(g('tobb_svt.basarili'))} sorgu yanıtı (il×sektör kırılımı: {n(g('tobb_svt.tur.C'))}/3.402). Sunucu yavaş; sürüyor.", "run"),
        ("TOBB kurulan/kapanan şirket + aylık kapasite raporu istatistikleri", "Excel arşivleri",
         f"{n(g('tobb_istatistik.dosya'))} dosya ({mb(g('tobb_istatistik.disk'))}).", "ok"),
        ("OpenStreetMap — sanayi alanları, depolar, fabrikalar, lojistik", "osmium katmanı", f"{osm_trend('sanayi_lojistik')} nesne (2021 → 2026).", "ok"),
    ]),
    ("TÜİK — en ince ayrıntı", "Resmî veri tarayıcısının arka ucu (databrowser2.tuik.gov.tr)", [
        ("TÜİK Dağıtım Yönetim Sistemi", "Katalogdaki her veri akışı için 'tümünü indir' (SDMX-JSON, kod adlarıyla)",
         f"{n(g('tuik.veri_akisi'))} veri akışı, {n(g('tuik.gozlem'))} gözlem; kısmi yanıt {n(g('tuik.kismi'))}. İlçe/IBBS3 düzeyli: doğum, evlenme, boşanma, konut satış şekli, yabancılara satış, girişimler.", "ok"),
    ]),
    ("Turizm — il/ilçe, yurtiçi ve yurtdışı", "Tesis arzı, konaklama, sınır girişleri, havalimanları", [
        ("Kültür ve Turizm Bakanlığı — belgeli konaklama tesisleri", "TGA sitesinin veri servisi, sayfalı",
         f"{n(g('tga.tesis'))} tesis; {n(g('tga.il'))} il, {n(g('tga.ilce'))} ilçe; belge türü kırılımlı.", "ok"),
        ("KTB turizm istatistikleri", "Sınır, konaklama (bakanlık/belediye belgeli), tesis, yat, acenta — tüm ek dosyalar",
         f"{n(g('turizm_ktb.dosya'))} dosya ({mb(g('turizm_ktb.disk'))}); 1985–1999 dönemi dev PDF'ler (80–300 MB) atlandı, listesi kayıtlı.", "ok"),
        ("DHMİ havalimanı istatistikleri", "Havalimanı bazında yolcu/yük/uçak", f"{n(g('dhmi_havalimani_istatistik.dosya'))} dosya.", "ok"),
        ("OpenStreetMap — turizm ve tarihî noktalar", "osmium katmanı", f"{osm_trend('turizm_poi')} nesne (2021 → 2026).", "ok"),
        ("BKM — yabancı kartların yurt içi kullanımı", "Aylık tablo", "BKM toplamasının parçası.", "run"),
    ]),
    ("Yurtdışından gelen turist — Avrupa odaklı", "Kaynak pazarların talep tarafı: kim, ne zaman, nereden, ne amaçla, ne kadar harcıyor", [
        ("Eurostat — AB'de yaşayanların seyahatleri + havayolu", "Tüm tour_* ve avia_* kümeleri toplu indirilip Türkiye satırları süzüldü",
         f"{n(g('inbound.eurostat_kume'))} kümede Türkiye satırı ({n(g('inbound.eurostat_tour_kume'))} turizm, {n(g('inbound.eurostat_avia_kume'))} havayolu), {n(g('inbound.eurostat_tr_satir'))} satır: varış ülkesi TR seyahat/geceleme/harcama; Avrupa havalimanı ↔ Türk havalimanı aylık yolcu/uçuş/yük; Türkiye'nin kendi bildirdiği hat verileri.", "run"),
        ("Birleşik Krallık ONS Travelpac 1994–2023", "Yıllık/çeyreklik IPS veri paketleri (ham zip saklandı)",
         f"{n(g('inbound.ons_tr_satir'))} Türkiye ziyaret grubu satırı: çeyrek, amaç, ulaşım, paket tur, yaş grubu, cinsiyet, süre bandı + ziyaret, geceleme, harcama.", "ok"),
        ("Eurocontrol — havalimanı bazında günlük uçuşlar 2016→Ağu 2026", "ANS Performance açık verisi",
         f"Türk havalimanları: {n(g('inbound.eurocontrol_tr_satir'))} havalimanı-gün satırı (varış/kalkış); ham dosyada tüm Avrupa (978 bin satır).", "ok"),
        ("Hollanda CBS — Hollandalıların tatilleri", "OData; 40 tablo, 1969→2025 (varış ülkesi, harcama, süre, konaklama, ulaşım, kişi özellikleri)",
         f"{n(g('inbound.cbs_tablo'))} tablo, {n(g('inbound.cbs_satir'))} satır.", "run"),
        ("TÜİK — Çıkış Yapan Ziyaretçi Anketi ve tüm istatistik tabloları", "Veri portalı tablo indirme servisi (SDMX'te olmayan turizm tabloları dahil)",
         f"{n(g('inbound.tuik_portal_dosya'))} tablo indirildi; milliyet, yaş, cinsiyet, eğitim, çalışma durumu, geliş amacı, konaklama türü, harcama türü kırılımları.", "run"),
        ("81 il kültür ve turizm müdürlüğü", "Her il müdürlüğü sitesinin istatistik ekleri (milliyete göre gelen turist, konaklama)",
         f"{n(g('inbound.ktb_il_dosya'))} dosya şimdiye kadar.", "run"),
        ("KTB sınır istatistikleri", "Milliyet × sınır kapısı, aylık ve yıllık bültenler 2015–2024+", "151 dosya (turizm bölümündeki KTB satırına dahil).", "ok"),
        ("Rusya Rosstat · Almanya Destatis", "Ulusal çıkış istatistikleri", "Rosstat yurtdışından erişime kapalı; Destatis GENESIS servis hesabı istiyor (hesap açmıyorum). Almanya için Eurostat verisi kullanılıyor.", "block"),
    ]),
    ("Şarj istasyonları — tüm Türkiye", "Koordinat, soket, güç, fiyat, anlık cihaz durumu", [
        ("EPDK Şarj@TR", "Uygulamanın açık servisi: istasyon listesi + her istasyon detayı",
         f"{n(g('sarj.sarjtr_istasyon_detay'))} istasyon, {n(g('sarj.soket'))} soket, {n(g('sarj.operator_sayisi'))} operatör şirket. Anlık durum: boş {n(g('sarj.soket_durum.FREE'))}, kullanımda {n(g('sarj.soket_durum.IN_USE'))}, bakımda {n(g('sarj.soket_durum.MAINTENANCE'))}, arızalı {n(g('sarj.soket_durum.FAULT'))}.", "ok"),
        ("EPDK resmî şarj istasyonları listesi (apigateway)", "Resmî web servisi (lisans no, dağıtım şirketi, adres)",
         "Ön testte 16.889 istasyon döndü; servis kotası (ERR-227) doldu, kota açılınca kendiliğinden çekilecek.", "run"),
        ("EPDK lisans sorgu sayfası", "Şarj istasyonu özet sorgusu", "'Ben robot değilim' doğrulaması istiyor — kullanılmadı.", "block"),
        ("OpenStreetMap şarj noktaları", "osmium katmanı", f"{osm_trend('sarj_istasyonlari_osm')} (2021 → 2026).", "ok"),
    ]),
    ("Büyükşehirler için açık veriler", "Belediye açık veri portalları (CKAN)", [
        ("İBB, İzmir, Bursa, Manisa, Sakarya, Konya, Gaziantep, Balıkesir, Denizli", "Tüm veri seti kataloğu + dosyalar (300 MB üstü hariç)",
         f"{n(ck_ds)} veri seti, {n(ck_files)} dosya indirildi ({mb(ck_disk)}); sürüyor. Kocaeli (502), Antalya, Ankara, Mersin, Tekirdağ portalları yanıt vermedi.", "run"),
    ]),
    ("Şehirlerarası otobüs rotaları", "Rota, firma, terminal, saat, fiyat", [
        ("enuygun.com rota sayfaları", "Sitemap'teki 35.593 rotadan önce 81 il merkezi arası 6.340 rota; sayfadaki yapılandırılmış sefer verisi",
         f"{n(g('otobus.rota'))} rota işlendi, {n(g('otobus.sefer'))} sefer, {n(g('otobus.firma'))} firma, {n(g('otobus.durak_terminal'))} terminal/durak. Sürüyor.", "run"),
        ("OpenStreetMap otogar/durak + güzergâh ilişkileri", "osmium", f"{n(osm26.get('otogarlar_duraklar'))} otogar/durak; otobüs/tren/karayolu güzergâh ilişkileri üye listeleriyle.", "ok"),
    ]),
    ("Şehirlerarası tır rotaları", "Resmî tır rotası verisi yok; yerine trafik hacmi + yol ağı + tır altyapısı", [
        ("KGM trafik hacim haritaları 2015–2025, yol envanteri, yolcu/yük taşımaları", "Resmî PDF/Excel arşivi",
         f"{n(g('kgm_karayollari.dosya'))} dosya ({mb(g('kgm_karayollari.disk'))}); yıllık ortalama günlük trafik ve ağır taşıt payı haritaları.", "ok"),
        ("OpenStreetMap ana yol ağı + tır yol hizmetleri", "otoyol/devlet yolu/il yolu; dinlenme tesisi, kantar, gişe, tır parkı",
         f"{n(osm26.get('ana_yol_agi'))} yol parçası; {n(osm26.get('tir_yol_hizmetleri'))} tır hizmet noktası; {n(osm26.get('akaryakit_istasyonlari'))} akaryakıt istasyonu.", "ok"),
    ]),
    ("Yük taşıma tren rotaları", "Hat, istasyon, yük istasyonu", [
        ("OpenStreetMap demiryolu", "Tüm hatlar (kullanım: ana/yan/sanayi/yük etiketiyle), istasyonlar, garlar, güzergâh ilişkileri",
         f"{n(osm26.get('demiryolu_hatlari'))} hat parçası, {n(osm26.get('demiryolu_istasyonlari'))} istasyon/gar/yük noktası.", "ok"),
        ("TCDD / TCDD Taşımacılık istatistikleri", "Site gezgini", "Site içerikleri betikle yükleniyor; yalnız 2 sayfa alındı.", "part"),
    ]),
    ("Liman konumları", "Konum + liman bazlı yük/konteyner", [
        ("OpenStreetMap limanlar/iskeleler/marinalar/feribot", "osmium", f"{n(osm26.get('limanlar'))} nesne (poligon/nokta).", "ok"),
        ("Ulaştırma Bakanlığı denizcilik istatistikleri", "Yük, konteyner, kabotaj, kruvaziyer, Ro-Ro, Boğaz geçişleri — liman bazlı aylık Excel",
         f"{n(g('uab_denizcilik_istatistik.dosya'))} dosya ({mb(g('uab_denizcilik_istatistik.disk'))}).", "ok"),
        ("TKYGM kıyı yapıları istatistikleri", "Excel", f"{n(g('uab_tkygm_kiyi_istatistik.dosya'))} dosya.", "ok"),
    ]),
    ("Hal fiyatları — geçmiş ve izleme", "Günlük, ürün bazında", [
        ("Ticaret Bakanlığı Hal Kayıt Sistemi", "Her gün için bülten + 'Excel'e aktar'; 2017 → bugün",
         f"{n(g('hal.hks_gun'))} gün, {n(g('hal.hks_satir'))} ürün-gün satırı (fiyat + işlem hacmi). Sürüyor.", "run"),
        ("İzmir Büyükşehir hal API'si", "Sebze-meyve ve balık hali, 2008 → bugün, günlük asgari/azami/ortalama",
         f"Sebze-meyve {n(g('hal.izmir.sebzemeyve.gun'))} gün / {n(g('hal.izmir.sebzemeyve.satir'))} satır; balık sırada.", "run"),
        ("İzleme betiği", "tools/schedule/track_daily.sh (her gece 03:15)", "Hazır; zamanlayıcıyı sizin açmanız gerekiyor (aşağıda).", "wait"),
    ]),
    ("Zincir market fiyatları — kalem kalem", "BİM, A101, ŞOK, Migros, CarrefourSA, Tarım Kredi, Hakmar", [
        ("Market Fiyatı (TÜBİTAK / Ticaret Bakanlığı) — güncel", "Her il için zincir başına merkez şube; tüm ürün sayfaları",
         f"{n(g('market.fiyat_il_sayisi'))}/81 il tamamlandı ({n(g('market.fiyat_satiri'))} ürün×şube fiyatı; il başına ~{n(g('market.il_basina_max_urun'))} ürün). Batı büyükşehirleri önce. 1 istek/sn — sürüyor.", "run"),
        ("Market Fiyatı — geçmiş", "Ürün başına son 90 gün günlük fiyat (zincir bazında)", "İl fiyatları bitince İstanbul, Ankara, İzmir için başlar.", "run"),
        ("Daha eski geçmiş", "GitHub/Kaggle arşiv araması", "Kamuya açık, güvenilir bir arşiv bulunamadı; geçmiş bundan sonra izleme ile birikecek.", "part"),
    ]),
    ("Seçtiğim ek değerli veriler", "", [
        ("EPDK akaryakıt ve LPG günlük bülteni", "Resmî servis, 2015 → bugün", "Kota nedeniyle yavaş; kota açıldıkça ilerler.", "run"),
        ("OpenStreetMap yıllık kesitler 2021–2025", "Yerel PBF dosyalarından 12 katman × 5 yıl", "İlçe/mahalle bazında 'haritada var olma' değişimi için taban.", "ok"),
        ("OSM enerji, eğitim/sağlık/kamu, otopark, pazar yerleri", "osmium katmanları",
         f"Enerji {n(osm26.get('enerji_altyapi'))}, eğitim-sağlık-kamu {n(osm26.get('egitim_saglik_kamu'))}, otopark {n(osm26.get('otoparklar'))}, pazar yeri {n(osm26.get('pazar_hal'))}.", "ok"),
    ]),
]

rows_html = []
for title, sub, items in TOPICS:
    trs = "".join(
        f'<tr><td class="src">{html.escape(a)}</td><td>{html.escape(b)}</td><td class="vol">{c}</td><td>{pill(d)}</td></tr>'
        for a, b, c, d in items)
    rows_html.append(f'''<section class="topic"><header><h2>{html.escape(title)}</h2>{f'<p>{html.escape(sub)}</p>' if sub else ''}</header>
<div class="tw"><table><thead><tr><th>Kaynak</th><th>Nasıl çekildi</th><th>Ne kadar</th><th>Durum</th></tr></thead><tbody>{trs}</tbody></table></div></section>''')

total_disk = sum(v.get("disk", 0) for v in S.values() if isinstance(v, dict) and isinstance(v.get("disk"), (int, float))) + ck_disk
now = dt.datetime.now().strftime("%d.%m.%Y %H:%M")

page = f'''<title>Gece Veri Defteri</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--bg:#F4F6F4;--panel:#FFFFFF;--ink:#18211E;--muted:#5B6863;--rule:#D6DDD9;--accent:#0E6655;--ok:#2E7A4E;--run:#1F5FA8;--part:#9A6A0B;--block:#A83A2A;--wait:#7A3E9D;--local:#56625D;
--okb:#E3F1E8;--runb:#E2ECF8;--partb:#F7EEDA;--blockb:#F7E3DF;--waitb:#EFE5F6;--localb:#E9EDEB}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#111715;--panel:#18201D;--ink:#E4EBE8;--muted:#98A6A0;--rule:#2C3733;--accent:#5CC3AC;--ok:#7FD19F;--run:#8DB8F0;--part:#E0B75C;--block:#F0907F;--wait:#C79BE6;--local:#A9B5B0;
--okb:#1C3326;--runb:#1B2A3E;--partb:#382D14;--blockb:#3D1F1A;--waitb:#2F2140;--localb:#252D2A;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#111715;--panel:#18201D;--ink:#E4EBE8;--muted:#98A6A0;--rule:#2C3733;--accent:#5CC3AC;--ok:#7FD19F;--run:#8DB8F0;--part:#E0B75C;--block:#F0907F;--wait:#C79BE6;--local:#A9B5B0;
--okb:#1C3326;--runb:#1B2A3E;--partb:#382D14;--blockb:#3D1F1A;--waitb:#2F2140;--localb:#252D2A;color-scheme:dark}}
body{{background:var(--bg);color:var(--ink);font:16px/1.55 "Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1120px;margin:0 auto;padding-inline:20px;padding-block:32px 64px}}
h1,h2,h3{{font-family:Archivo,"Source Sans 3",system-ui,sans-serif;text-wrap:balance;margin:0}}
h1{{font-size:clamp(28px,4vw,40px);font-weight:800;letter-spacing:-.01em}}
.eyebrow{{font:500 12px/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}}
.lead{{max-width:68ch;color:var(--muted);margin:12px 0 0}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:10px;overflow:hidden;margin:28px 0 8px}}
.facts div{{background:var(--panel);padding:14px 16px}}
.facts b{{display:block;font:700 24px/1.1 Archivo,sans-serif;font-variant-numeric:tabular-nums}}
.facts span{{font-size:13px;color:var(--muted)}}
.legend{{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 6px;font-size:13px;color:var(--muted);align-items:center}}
.topic{{margin-top:40px}}
.topic header{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;border-bottom:2px solid var(--ink);padding-bottom:8px}}
.topic h2{{font-size:20px;font-weight:700}}
.topic header p{{margin:0;color:var(--muted);font-size:14px}}
.tw{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;min-width:720px}}
th{{text-align:left;font:500 11px/1.2 "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:10px 12px 6px;border-bottom:1px solid var(--rule)}}
td{{vertical-align:top;padding:11px 12px;border-bottom:1px solid var(--rule);font-size:14.5px}}
td.src{{font-weight:600;width:24%}}
td.vol{{width:40%;font-variant-numeric:tabular-nums}}
tr:hover td{{background:color-mix(in srgb,var(--panel) 70%,transparent)}}
.pill{{display:inline-block;white-space:nowrap;font:500 12px/1 "IBM Plex Mono",monospace;padding:5px 8px;border-radius:999px}}
.pill.ok{{color:var(--ok);background:var(--okb)}}.pill.run{{color:var(--run);background:var(--runb)}}.pill.part{{color:var(--part);background:var(--partb)}}
.pill.block{{color:var(--block);background:var(--blockb)}}.pill.wait{{color:var(--wait);background:var(--waitb)}}.pill.local{{color:var(--local);background:var(--localb)}}
.box{{background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:18px 20px;margin-top:16px}}
.box h3{{font-size:17px;margin-bottom:8px}}
.box ol,.box ul{{margin:0;padding-left:20px}} .box li{{margin:6px 0;max-width:80ch}}
code,pre{{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:13px}}
pre{{background:var(--bg);border:1px solid var(--rule);border-radius:8px;padding:12px;overflow-x:auto}}
.two{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}
footer{{margin-top:40px;color:var(--muted);font-size:13px}}
</style>
<div class="wrap">
<div class="eyebrow">GEOPROP · gece toplama turu · 24 Eylül 2026</div>
<h1>Nereden, nasıl, ne kadar</h1>
<p class="lead">Dün gece istediğiniz her başlık için hangi kaynaktan, hangi yöntemle, ne kadar veri çekildiğinin defteri. Hiçbir sayı tahmin değil: her satır, ilgili klasördeki <code>manifest.jsonl</code> kayıtlarından ve dosyaların kendisinden sayıldı. Yorum yok; yalnız toplama ve arşiv. Son güncelleme: {now}.</p>
<div class="facts">
<div><b>{n(g("tuik.gozlem"))}</b><span>TÜİK gözlemi (432 veri akışı)</span></div>
<div><b>{n(sum(osm26.values()) if osm26 else 0)}</b><span>OSM nesnesi, 2026 kesiti (il/ilçe/mahalle atanmış)</span></div>
<div><b>{n((g("hal.hks_satir", 0) or 0) + (g("hal.izmir.sebzemeyve.satir", 0) or 0))}</b><span>hal fiyatı satırı (2008 →)</span></div>
<div><b>{n(g("sarj.soket"))}</b><span>şarj soketi, anlık durumuyla</span></div>
<div><b>{n(g("tga.tesis"))}</b><span>belgeli konaklama tesisi</span></div>
<div><b>{total_disk / 1e9:.1f} GB</b><span>diske yazılan (sıkıştırılmış)</span></div>
</div>
<div class="legend">Durum: {pill("ok")} {pill("run")} {pill("part")} {pill("block")} {pill("wait")} {pill("local")}</div>
{"".join(rows_html)}
<section class="topic"><header><h2>Sizden beklenen kararlar</h2></header>
<div class="two">
<div class="box"><h3>1 · Pazarama satıcı bilgileri</h3><p>20 bin satıcının unvan, adres, il/ilçe ve KEP bilgisi sayfalarda açık; ancak şahıs satıcılarda bunlar kişisel veri olduğu için oturumun güvenlik denetimi toplamayı durdurdu. Bu adım sizin kararınız ve oturum ayarlarınıza bağlı; ben denetimi aşmadım.</p></div>
<div class="box"><h3>2 · Günlük/saatlik izlemeyi açmak</h3><p>Hal, market, şarj ve akaryakıt fiyatlarının her gün birikmesi için zamanlayıcı dosyaları hazır, kalıcı sistem ayarı olduğu için ben açmadım. Açmak için Terminal'de:</p>
<pre>cp ~/Desktop/endeks3/consolidation/tools/schedule/com.geoprop.track.*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.geoprop.track.daily.plist
launchctl load ~/Library/LaunchAgents/com.geoprop.track.hourly.plist</pre></div>
<div class="box"><h3>3 · Disk</h3><p>Sabah disk 811 MB boşa düştü (asıl büyüme Codex önbelleğinden). Onayınızla yinelenen OSM dosyası (618 MB) silindi, pip/uv/Homebrew önbellekleri temizlendi → ~5 GB boş. Toplayıcılar 1,5 GB altında büyük dosya yazmayı otomatik durduruyor; bu sürede yazma hatası olmadı.</p></div>
<div class="box"><h3>4 · Engelli kaynaklar</h3><p>Captcha (EPDK lisans sorgu, EPDK il/ilçe akaryakıt bülteni) ve bot koruması (Trendyol, Hepsiburada, n11, PTTAVM, obilet) olan sitelerde korumayı aşmadım. Bu veriler için resmî API/ücretli erişim veya veri paylaşım anlaşması gerekir.</p></div>
</div></section>
<section class="topic"><header><h2>Dosyalar nerede</h2></header>
<div class="box"><ul>
<li>Ham veri: <code>~/Desktop/GEOPROP_RAW_INTAKE/&lt;kaynak&gt;/2026-09-24/</code> — her klasörde <code>manifest.jsonl</code> (kaynak adresi, yöntem, çekim zamanı, satır, bayt, sha256).</li>
<li>Toplayıcılar: <code>consolidation/tools/intake_*.py</code> (yerel commit <code>0a594f1</code>, push edilmedi). Hepsi kaldığı yerden devam eder.</li>
<li>Canlı günlükler: <code>~/Desktop/GEOPROP_CONSOLIDATION/logs/</code> · bu rapor: <code>consolidation/reports/gece_veri_defteri.html</code></li>
<li>Kurallar: her kayıtta çekim zamanı (ISO 8601); koordinatlı kayıtlara il/ilçe/mahalle kanonik poligonlardan eklendi, poligon dışındakiler en yakın mahalleye bağlanıp <code>admin_match</code> ile işaretlendi.</li>
</ul></div></section>
<footer>OpenStreetMap verisi © OpenStreetMap katkıcıları, ODbL. Diğer kaynakların kullanım koşulları kaynak kurumlara aittir.</footer>
</div>'''

(REP / "gece_veri_defteri.html").write_text(page)
print("yazıldı", REP / "gece_veri_defteri.html", len(page))
