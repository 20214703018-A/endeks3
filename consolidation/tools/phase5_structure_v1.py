#!/usr/bin/env python3
"""
PHASE 5 — VERİTABANI YAPILANDIRMA v1 (canonical_v1.7 üzerinde; veri eklemez/silmez, yalnız yapı kurar)
  A) İndeksler .... sık birleştirilen sütunlara (coğrafya, POI, ürün, seri). 17-36 M satırlık olgu tablolarına
                    bilerek indeks konmaz: DuckDB sütunlu tarama + bölge haritası zaten hızlı, indeks yer harcar.
  B) urun şeması .. satışa dönük görünümler: Türkçe sütun adları, KİŞİSEL/KİMLİK sütunları YOK
                    (telefon, MERSİS, vergi no, KEP e-posta ürün katmanında görünmez; main şemasında durmaya devam eder).
  C) Veri sözlüğü . meta_tablo + meta_kolon tabloları (her tablo/sütun ne anlama geliyor, hangi kaynaktan,
                    hassas mı) + reports/VERI_SOZLUGU.html ve .csv
  D) Analitik ..... ilçe/mahalle düzeyi market fiyatı özetleri yeni hacme göre tazelenir.
"""
import time, datetime as dt, json, hashlib, csv, os
from pathlib import Path
import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
CAN = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
MIN_FREE = 1_000_000_000

INDEXES = [
    ("geo_entity", "level"), ("geo_entity", "il_geo_id"), ("geo_entity", "parent_geo_id"),
    ("geo_entity", "name_norm"), ("geo_entity", "tuik_kodu"), ("geo_entity", "nuts_code"),
    ("poi", "assigned_geo_id"), ("poi", "assigned_ilce_geo_id"), ("poi", "primary_source"),
    ("poi", "source_coverage"), ("poi", "predicted_sector"),
    ("poi_source_link", "poi_id"), ("poi_source_link", "source_record_id"),
    ("product", "product_code"), ("product", "source_system"),
    ("indicator_sdmx_series", "geo_id"), ("indicator_sdmx_series", "dataflow_id"), ("indicator_sdmx_series", "series_id"),
    ("sdmx_geo_map", "ref_area_code"), ("indicator_observation", "geo_id"), ("indicator_observation", "metric"),
    ("etbis_site", "site_id"), ("charging_socket", "poi_id"), ("charging_socket", "station_id"),
]

# ürün katmanı görünümleri: (görünüm adı, kaynak, SELECT gövdesi)
URUN_VIEWS = [
 ("isletme", """SELECT p.poi_id isletme_id, p.name ad, p.raw_category kaynak_kategori, p.predicted_category kategori,
      p.predicted_sector sektor, p.category_method kategori_yontemi, p.category_confidence kategori_guven,
      p.rating puan, p.rating_count puan_sayisi, p.review_count yorum_sayisi, p.address_raw adres,
      p.lat enlem, p.lon boylam, p.coord_validity koordinat_gecerliligi, p.assigned_geo_id mahalle_geo_id,
      p.assigned_ilce_geo_id ilce_geo_id, p.assignment_method atama_yontemi, p.assignment_confidence atama_guveni,
      p.source_coverage kaynak_kapsami, p.primary_source ana_kaynak, p.osm_amenity osm_tur, p.osm_shop osm_dukkan,
      p.osm_cuisine osm_mutfak, p.osm_website web_sitesi, p.osm_opening_hours calisma_saatleri,
      p.ys_cuisine_json platform_mutfak, p.ys_price_range platform_fiyat_araligi, p.sector_hint sektor_ipucu,
      p.last_observed_at son_gorulme, p.observation_count gozlem_sayisi, p.acquisition_class edinim_sinifi,
      p.distribution_class dagitim_sinifi, p.source_row_hash kaynak_satir_ozeti
   FROM poi p"""),
 ("isletme_kaynak", """SELECT link_id baglanti_id, poi_id isletme_id, source_system kaynak_sistem,
      source_record_id kaynak_kayit_id, match_score eslesme_skoru, match_band eslesme_bandi,
      link_status baglanti_durumu, reasons gerekce, created_at olusturulma FROM poi_source_link"""),
 ("cografya", """SELECT geo_id, level duzey, name ad, parent_geo_id ust_geo_id, il_geo_id, tuik_kodu,
      nuts_code nuts_kodu, centroid_lat merkez_enlem, centroid_lon merkez_boylam, geometry sinir_poligonu,
      geometry_source sinir_kaynagi, mapping_status eslesme_durumu FROM geo_entity"""),
 ("konut_fiyat_gozlem", """SELECT observation_id gozlem_id, geo_id, level duzey, category kategori,
      subcategory alt_kategori, metric olcut, parsed_value deger, unit birim, period donem,
      observation_kind gozlem_turu, source_table kaynak_tablo, acquisition_class edinim_sinifi,
      distribution_class dagitim_sinifi, recorded_at kayit_zamani FROM price_observation"""),
 ("urun_katalogu", """SELECT product_id urun_id, source_system kaynak_sistem, product_code urun_kodu, name ad,
      brand marka, unit birim, quantity_unit miktar_birimi, category_main ana_kategori, category_menu alt_kategori,
      image_url gorsel, first_seen_at ilk_gorulme FROM product"""),
 ("urun_fiyat_gozlem", """SELECT o.observation_id gozlem_id, o.source_system kaynak_sistem, o.poi_id isletme_id,
      o.geo_id, o.level duzey, o.product_id urun_id, o.product_code urun_kodu, o.price_kind fiyat_turu,
      o.price_value fiyat, o.price_min asgari_fiyat, o.price_max azami_fiyat, o.unit_price_value birim_fiyat,
      o.unit_price_text birim_fiyat_metni, o.volume_value islem_hacmi, o.unit birim, o.currency para_birimi,
      o.discount_flag indirim, o.promo_text promosyon, o.period donem, o.period_kind donem_turu,
      o.observed_at gozlem_zamani, o.acquisition_class edinim_sinifi, o.distribution_class dagitim_sinifi
   FROM product_price_observation o"""),
 ("gosterge_gozlem", """SELECT observation_id gozlem_id, geo_id, level duzey, domain alan, metric olcut,
      dim1 kirilim1, dim2 kirilim2, period donem, period_kind donem_turu, parsed_value deger, unit birim,
      series_version seri_surumu, acquisition_class edinim_sinifi, distribution_class dagitim_sinifi
   FROM indicator_observation"""),
 ("tuik_seri", """SELECT series_id seri_id, dataflow_id akis_id, dataflow_name akis_adi,
      dataflow_description akis_aciklama, series_key seri_anahtari, dimensions_json boyutlar,
      ref_area_code bolge_kodu, ref_area_name bolge_adi, geo_id, geo_level geo_duzeyi,
      observation_count gozlem_sayisi, source_prepared_at kaynak_hazirlanma FROM indicator_sdmx_series"""),
 ("tuik_gozlem", """SELECT series_id seri_id, dataflow_id akis_id, period donem, period_label donem_etiketi,
      "value" deger, attributes_json nitelikler FROM indicator_sdmx_observation"""),
 ("sarj_soketi", """SELECT socket_id soket_id, poi_id isletme_id, station_id istasyon_id, socket_type soket_tipi,
      socket_sub_type soket_alt_tipi, power_kw guc_kw, price_tl_kwh fiyat_tl_kwh, status durum,
      observed_at gozlem_zamani FROM charging_socket"""),
 ("eticaret_sitesi", """SELECT site_id, unvan, site_url site_adresi, mobil_uygulama, il_adi, ilce_adi, sektor,
      isletme_adi, isletme_turu, etbis_kayit_tarihi, hakkinda, mal_hizmetler, odeme_turleri, diger_siteler,
      ilk_gorulme, son_gorulme FROM etbis_site"""),
 ("menu_kalemi", """SELECT observation_id gozlem_id, poi_id isletme_id, platform, title urun_adi,
      description aciklama, category_title kategori, original_price fiyat, discounted_price indirimli_fiyat,
      is_sold_out tukendi, currency para_birimi, observed_at gozlem_zamani FROM menu_item_observation"""),
 ("teslimat_gozlem", """SELECT observation_id gozlem_id, poi_id isletme_id, platform, rating_value puan,
      rating_count puan_sayisi, minimum_order_value min_sepet, delivery_time_lower_min teslimat_alt_dk,
      delivery_time_upper_min teslimat_ust_dk, delivery_fee_total teslimat_ucreti, price_range fiyat_araligi,
      cuisine_json mutfak, observed_at gozlem_zamani FROM venue_delivery_observation"""),
 ("mahalle_zeka", "SELECT * FROM analytics.mahalle_intelligence"),
 ("ilce_zeka", "SELECT * FROM analytics.ilce_intelligence"),
 ("ilce_yeni_kaynaklar", "SELECT * FROM analytics.ilce_yeni_kaynaklar"),
]

TABLO_ACIKLAMA = {
 "poi": "İşletme/mekân kayıtları. Kimlik bizim (poi_id); Google, OSM, Yemeksepeti, market şubesi, EPDK şarj ve KTB turizm kaynakları ad+konum ile eşleştirilip tek kayda bağlanır. Kaynaklar arası DEĞER birleştirilmez; her kaynağın kendi sütunları durur.",
 "poi_source_link": "Bir işletme kaydının hangi kaynak kaydından geldiği/eşleştiği. Eşleşme skoru, bandı ve gerekçesi burada; 'review' bandındakiler onay bekler.",
 "geo_entity": "Coğrafya sözlüğü: mahalle, ilçe, il, NUTS2/NUTS1 bölge ve ülke. Sınır poligonları, merkez noktası, TÜİK ve NUTS kodları.",
 "price_observation": "GAYRİMENKUL fiyat gözlemleri (mahalle/ilçe düzeyinde satılık-kiralık m² fiyatı vb.), uzun biçim.",
 "product_price_observation": "ÜRÜN fiyat gözlemleri: market şubesi×ürün rafı, Opet ilçe×gün akaryakıt, HKS ulusal hal, İzmir hal, market il günlük geçmiş. Gayrimenkul fiyatından ayrı tablodur (farklı grain).",
 "product": "Ürün sözlüğü: market ürün kataloğu, akaryakıt ürünleri, hal ürünleri. Fiyat gözlemleri buraya bağlanır.",
 "indicator_observation": "Resmî bağlam göstergeleri (TÜİK konut satış/yapı izni/göç/SES, BKM kart harcaması, BDDK il finans) uzun biçimde.",
 "indicator_sdmx_series": "TÜİK SDMX serilerinin tanımı: akış, seri anahtarı, boyut sözlüğü ve bağlandığı coğrafya.",
 "indicator_sdmx_observation": "TÜİK SDMX serilerinin dönem×değer gözlemleri (17,9 milyon).",
 "sdmx_geo_map": "TÜİK SDMX bölge kodu (REF_AREA) → bizim geo_id eşlemesi ve hangi yöntemle eşlendiği.",
 "charging_socket": "EPDK şarj istasyonu soketleri: tip, güç, kWh fiyatı, durum. İstasyon POI'sine bağlı.",
 "etbis_site": "ETBİS'e kayıtlı e-ticaret siteleri ve profilleri. MERSİS/vergi no/KEP e-postası restricted_ ön ekli sütunlarda ve ürün katmanına çıkmaz.",
 "menu_item_observation": "Çevrimiçi sipariş platformu menü kalemleri (ad, fiyat, indirimli fiyat, tükendi bilgisi).",
 "venue_delivery_observation": "Platform teslimat/puan gözlemleri: puan, minimum sepet, teslimat süresi ve ücreti.",
 "population_observation": "Nüfus gözlemleri (TÜİK ADNKS, mahalle/ilçe/il).",
 "poi_lifecycle_osm": "OSM anlık görüntülerinden türeyen işletme yaşam döngüsü (açılış/kapanış izleri).",
 "poi_presence_osm": "OSM anlık görüntülerinde bir işletmenin görülüp görülmediği.",
 "poi_snapshot": "POI kaynak anlık görüntüleri.",
 "poi_category_candidate": "Modelin önerdiği ama otomatik uygulanmayan kategori adayları (insan onayı bekler).",
 "m_cat": "Kategori eşleme sözlüğü.", "m_geo": "Coğrafya eşleme sözlüğü.",
 "_meta": "Veritabanı sürümü ve yapım bilgileri.",
 "meta_tablo": "Bu veri sözlüğünün tablo açıklamaları.", "meta_kolon": "Bu veri sözlüğünün sütun açıklamaları.",
}

KOLON_KALIP = [
 ("geo_id", "Coğrafya kimliği (geo_entity.geo_id)"), ("_geo_id", "Coğrafya kimliği (geo_entity.geo_id)"),
 ("poi_id", "İşletme kimliği (poi.poi_id)"), ("product_id", "Ürün kimliği (product.product_id)"),
 ("series_id", "Seri kimliği (indicator_sdmx_series.series_id)"),
 ("observation_id", "Gözlem kimliği (tekil)"), ("source_row_hash", "Kaynak satırın içerik özeti (sha256) — izlenebilirlik"),
 ("source_row_number", "Kaynak dosyadaki satır sırası"), ("source_file_id", "Kaynak dosya kimliği (envanter)"),
 ("source_path", "Kaynak dosya yolu"), ("staging_view", "Hangi ambar (staging) tablosundan geldiği"),
 ("acquisition_class", "Veri nasıl edinildi: official_public (resmî açık) / web_research (web'den) "),
 ("distribution_class", "Dağıtım sınıfı: public (paylaşılabilir) / internal (iç kullanım) / restricted (kısıtlı)"),
 ("created_at", "Bu satırın veritabanına yazılma zamanı (UTC)"), ("recorded_at", "Bu satırın veritabanına yazılma zamanı (UTC)"),
 ("period", "Dönem (gün/ay/çeyrek/yıl)"), ("period_kind", "Dönemin türü: day/month/quarter/year"),
 ("level", "Coğrafi düzey: mahalle/ilce/il/nuts2/nuts1/ulke"), ("unit", "Ölçü birimi"),
 ("name_norm", "Adın karşılaştırma için sadeleştirilmiş hâli (küçük harf, aksansız)"),
 ("match_score", "Eşleşme skoru 0-1"), ("match_band", "Eşleşme bandı (virtually_certain/very_high/high/review)"),
 ("restricted_", "KISITLI: kimlik/iletişim bilgisi — ürün katmanına çıkmaz, yalnız iç kullanım"),
 ("raw_", "Kaynaktan geldiği gibi, dokunulmamış değer"),
]

# tablo adından bağımsız, sütun adına göre açıklamalar (Türkçe ürün katmanı takma adları dâhil)
KOLON_ACIK = {
 "name": "Ad", "ad": "Ad", "mahalle": "Mahalle adı", "ilce": "İlçe adı", "il": "İl adı",
 "ilce_adi": "İlçe adı", "il_adi": "İl adı", "mahalle_adi": "Mahalle adı", "duzey": "Coğrafi düzey",
 "source_place_id": "Kaynağın kendi kayıt kimliği (bizim kimliğimiz değil; source_id_kind türünü söyler)",
 "source_id_kind": "Kaynak kimliğin türü: gerçek platform kimliği mi, toplayıcının ürettiği yapay kimlik mi",
 "predicted_category": "Modelin/kuralın belirlediği kategori", "kategori": "Kategori (bizim sınıflandırmamız)",
 "predicted_sector": "Üst sektör (Yeme-İçme, Gıda Perakende, …)", "sektor": "Üst sektör",
 "kaynak_kategori": "Kaynakta yazan ham kategori metni", "raw_category": "Kaynakta yazan ham kategori metni",
 "category_method": "Kategori hangi yolla bulundu (kaynak türü, mekânsal eşleşme, ad modeli…)",
 "kategori_yontemi": "Kategori hangi yolla bulundu", "category_confidence": "Kategori güveni 0-1",
 "kategori_guven": "Kategori güveni 0-1", "category_band": "Kategori güven bandı (high/review/low)",
 "rating": "Puan (kaynağın kendi ölçeği)", "puan": "Puan", "rating_count": "Puan veren sayısı",
 "puan_sayisi": "Puan veren sayısı", "review_count": "Yorum sayısı", "yorum_sayisi": "Yorum sayısı",
 "phone": "KISITLI: telefon numarası — ürün katmanına çıkmaz", "osm_phone": "KISITLI: OSM'deki telefon — ürün katmanına çıkmaz",
 "address_raw": "Kaynaktaki adres metni", "adres": "Adres metni",
 "lon": "Boylam (WGS84)", "lat": "Enlem (WGS84)", "boylam": "Boylam (WGS84)", "enlem": "Enlem (WGS84)",
 "geometry": "Sınır/nokta geometrisi (WGS84)", "sinir_poligonu": "Sınır poligonu (WGS84)",
 "source_il": "Kaynağın belirttiği il", "source_il_reliability": "Kaynak il bilgisinin güvenilirliği",
 "assignment_method": "Coğrafyaya hangi yöntemle bağlandı (poligon içi, kaynak geo_id, ad eşleşmesi…)",
 "atama_yontemi": "Coğrafyaya hangi yöntemle bağlandı", "assignment_confidence": "Coğrafya atama güveni 0-1",
 "atama_guveni": "Coğrafya atama güveni 0-1", "ilce_assignment_confidence": "İlçe atama güveni 0-1",
 "assignment_flags": "Atama uyarıları (koordinat yok, poligon dışı, kaynakla çelişki…)",
 "coord_validity": "Koordinat geçerliliği: valid / MISSING / OUT_OF_TURKEY / bozuk",
 "koordinat_gecerliligi": "Koordinat geçerliliği", "coord_recovery": "Bozuk koordinatın nasıl kurtarıldığı",
 "last_observed_at": "Kaynakta en son görüldüğü an", "son_gorulme": "En son görüldüğü an",
 "ilk_gorulme": "İlk görüldüğü an", "observation_count": "Kaç gözlemden oluştuğu", "gozlem_sayisi": "Gözlem sayısı",
 "source_coverage": "Bu kaydı hangi kaynakların gördüğü (google_only, google+osm, marketfiyati_only…)",
 "kaynak_kapsami": "Bu kaydı hangi kaynakların gördüğü", "primary_source": "Kaydın ana kaynağı",
 "ana_kaynak": "Kaydın ana kaynağı", "context_match": "Kaynağın yazdığı adres ile poligon atamasının uyumu",
 "context_il_norm": "Kaynak adresinden çıkan il (sadeleştirilmiş)", "context_ilce_norm": "Kaynak adresinden çıkan ilçe (sadeleştirilmiş)",
 "context_mahalle_norm": "Kaynak adresinden çıkan mahalle (sadeleştirilmiş)",
 "spatial_geo_id": "Yalnız koordinatın düştüğü poligon", "context_geo_id": "Yalnız adres metninden bulunan coğrafya",
 "osm_id": "OpenStreetMap nesne kimliği", "osm_name": "OSM'deki ad", "osm_amenity": "OSM amenity etiketi",
 "osm_shop": "OSM shop etiketi", "osm_cuisine": "OSM mutfak etiketi", "osm_website": "OSM'deki web sitesi",
 "osm_opening_hours": "OSM'deki çalışma saatleri", "osm_lon": "OSM boylamı", "osm_lat": "OSM enlemi",
 "osm_match_score": "OSM eşleşme skoru 0-1", "osm_match_band": "OSM eşleşme bandı",
 "osm_source_row_hash": "Eşleşen OSM satırının içerik özeti", "osm_tur": "OSM tür etiketi", "osm_dukkan": "OSM dükkân etiketi",
 "osm_mutfak": "OSM mutfak etiketi", "web_sitesi": "Web sitesi", "calisma_saatleri": "Çalışma saatleri",
 "ys_venue_code": "Sipariş platformundaki mekân kodu", "ys_name": "Platformdaki ad", "ys_url": "Platform sayfası",
 "ys_cuisine_json": "Platformdaki mutfak listesi", "ys_price_range": "Platform fiyat aralığı",
 "ys_lon": "Platform boylamı", "ys_lat": "Platform enlemi", "ys_match_score": "Platform eşleşme skoru",
 "ys_match_band": "Platform eşleşme bandı", "ys_source_row_hash": "Eşleşen platform satırının özeti",
 "platform_mutfak": "Platformdaki mutfak listesi", "platform_fiyat_araligi": "Platform fiyat aralığı",
 "sector_hint": "Arama terimlerinden ölçülmüş sektör ipucu", "sektor_ipucu": "Arama terimlerinden ölçülmüş sektör ipucu",
 "sector_hint_precision": "Sektör ipucunun ölçülmüş isabeti", "sector_hint_source": "Sektör ipucunun dayandığı kaynak",
 "tuik_il_kodu": "TÜİK il kodu", "tuik_kodu": "TÜİK idari birim kodu", "tuik_tur": "TÜİK kod uzayı (mahalle/köy ayrımı)",
 "nuts_code": "NUTS bölge kodu (TR1 / TR10 / TR100)", "nuts_kodu": "NUTS bölge kodu",
 "web_city_id": "Kaynak sitedeki il kimliği", "web_county_id": "Kaynak sitedeki ilçe kimliği",
 "web_district_id": "Kaynak sitedeki mahalle kimliği", "tkgm_id": "TKGM (tapu-kadastro) kimliği",
 "tkgm_link_band": "TKGM eşleşme bandı", "tkgm_link_score": "TKGM eşleşme skoru",
 "tuik_link_band": "TÜİK eşleşme bandı", "tuik_link_score": "TÜİK eşleşme skoru", "link_flags": "Eşleşme uyarıları",
 "geometry_source": "Sınır poligonunun kaynağı", "sinir_kaynagi": "Sınır poligonunun kaynağı",
 "centroid_lon": "Merkez noktası boylamı", "centroid_lat": "Merkez noktası enlemi",
 "merkez_enlem": "Merkez noktası enlemi", "merkez_boylam": "Merkez noktası boylamı",
 "bbox_xmin": "Sınırlayıcı kutu batı boylamı", "bbox_xmax": "Sınırlayıcı kutu doğu boylamı",
 "bbox_ymin": "Sınırlayıcı kutu güney enlemi", "bbox_ymax": "Sınırlayıcı kutu kuzey enlemi",
 "anchor_source": "Bu coğrafya kaydının çıpa kaynağı", "mapping_status": "Eşleme durumu (PROPOSED/APPROVED)",
 "eslesme_durumu": "Eşleme durumu", "model_version": "Kaydı üreten model sürümü",
 "parent_geo_id": "Üst coğrafya kimliği", "ust_geo_id": "Üst coğrafya kimliği", "il_geo_id": "Bağlı olduğu il kimliği",
 "source_system": "Kaynak sistem", "kaynak_sistem": "Kaynak sistem", "kaynak_kayit_id": "Kaynaktaki kayıt kimliği",
 "source_record_id": "Kaynaktaki kayıt kimliği", "link_status": "Bağlantı durumu (merged/review/primary)",
 "baglanti_durumu": "Bağlantı durumu", "reasons": "Eşleşme gerekçesi (skor bileşenleri)", "gerekce": "Eşleşme gerekçesi",
 "eslesme_skoru": "Eşleşme skoru 0-1", "eslesme_bandi": "Eşleşme bandı", "matcher_version": "Eşleştirici sürümü",
 "olusturulma": "Kayıt oluşturulma zamanı (UTC)", "kayit_zamani": "Kayıt zamanı (UTC)",
 "metric": "Ölçülen büyüklük", "olcut": "Ölçülen büyüklük", "domain": "Gösterge alanı (konut satış, banka, göç…)",
 "alan": "Gösterge alanı", "dim1": "Kırılım 1 (kaynağın kendi boyutu)", "dim2": "Kırılım 2",
 "kirilim1": "Kırılım 1", "kirilim2": "Kırılım 2", "parsed_value": "Sayıya çevrilmiş değer",
 "deger": "Değer", "raw_value": "Kaynaktaki değerin ham metni", "series_version": "Seri sürümü (revizyon ayrımı)",
 "seri_surumu": "Seri sürümü", "source_priority": "Kaynak önceliği (çakışmada hangisi üstte)",
 "source_table": "Bu satırın türediği ambar tablosu", "n_copies": "Aynı değerin kaç kaynak kopyasında görüldüğü",
 "source_updated_at": "Kaynaktaki güncellenme zamanı", "observation_kind": "Gözlem türü", "gozlem_turu": "Gözlem türü",
 "category": "Kategori", "subcategory": "Alt kategori", "alt_kategori": "Alt kategori", "kategori_kodu": "Kategori kodu",
 "product_code": "Kaynaktaki ürün kodu", "urun_kodu": "Kaynaktaki ürün kodu", "brand": "Marka", "marka": "Marka",
 "quantity_unit": "Miktar birimi", "miktar_birimi": "Miktar birimi", "category_main": "Ana kategori",
 "ana_kategori": "Ana kategori", "category_menu": "Menü/alt kategori", "categories_json": "Kaynaktaki kategori listesi",
 "image_url": "Ürün görseli", "gorsel": "Ürün görseli", "first_seen_at": "İlk görülme zamanı",
 "price_kind": "Fiyat türü: raf (shelf), pompa listesi, hal ortalaması…", "fiyat_turu": "Fiyat türü",
 "price_value": "Fiyat", "fiyat": "Fiyat", "price_min": "Asgari fiyat", "asgari_fiyat": "Asgari fiyat",
 "price_max": "Azami fiyat", "azami_fiyat": "Azami fiyat", "unit_price_value": "Birim fiyat (kg/lt başına)",
 "birim_fiyat": "Birim fiyat (kg/lt başına)", "unit_price_text": "Birim fiyatın kaynaktaki metni",
 "birim_fiyat_metni": "Birim fiyatın kaynaktaki metni", "volume_value": "İşlem hacmi (hal bültenleri)",
 "islem_hacmi": "İşlem hacmi", "currency": "Para birimi", "para_birimi": "Para birimi",
 "discount_flag": "İndirimli mi", "indirim": "İndirimli mi", "discount_ratio": "İndirim oranı",
 "promo_text": "Promosyon metni", "promosyon": "Promosyon metni", "observed_at": "Gözlem anı",
 "gozlem_zamani": "Gözlem anı", "birim": "Ölçü birimi", "donem": "Dönem", "donem_turu": "Dönemin türü",
 "dataflow_id": "TÜİK veri akışı kimliği", "akis_id": "TÜİK veri akışı kimliği", "dataflow_name": "Akışın adı",
 "akis_adi": "Akışın adı", "dataflow_description": "Akışın açıklaması", "akis_aciklama": "Akışın açıklaması",
 "series_key": "Seri anahtarı (boyut indeksleri)", "seri_anahtari": "Seri anahtarı",
 "dimensions_json": "Serinin tüm boyutları (kod + TÜİK'in yazdığı ad)", "boyutlar": "Serinin tüm boyutları",
 "ref_area_code": "TÜİK bölge kodu (REF_AREA)", "bolge_kodu": "TÜİK bölge kodu", "ref_area_name": "Bölge adı",
 "bolge_adi": "Bölge adı", "geo_level": "Bağlandığı coğrafi düzey", "geo_duzeyi": "Bağlandığı coğrafi düzey",
 "map_method": "Eşleme yöntemi", "source_prepared_at": "TÜİK'in veriyi hazırladığı an",
 "kaynak_hazirlanma": "TÜİK'in veriyi hazırladığı an", "period_label": "Dönemin okunur etiketi",
 "donem_etiketi": "Dönemin okunur etiketi", "attributes_json": "Gözlem nitelikleri (kaynağın bayrakları)",
 "nitelikler": "Gözlem nitelikleri", "socket_id": "Soket kimliği", "soket_id": "Soket kimliği",
 "station_id": "EPDK istasyon kimliği", "istasyon_id": "EPDK istasyon kimliği", "socket_type": "Soket tipi",
 "soket_tipi": "Soket tipi", "socket_sub_type": "Soket alt tipi", "soket_alt_tipi": "Soket alt tipi",
 "socket_number": "Soket numarası", "power_kw": "Güç (kW)", "guc_kw": "Güç (kW)",
 "price_tl_kwh": "kWh başına fiyat (TL)", "fiyat_tl_kwh": "kWh başına fiyat (TL)", "status": "Durum", "durum": "Durum",
 "status_start": "Durumun başlangıcı", "status_end": "Durumun bitişi", "prices_json": "Kaynaktaki fiyat listesi",
 "site_id": "ETBİS site kimliği", "unvan": "Ticaret unvanı (tüzel kişi adı)", "site_url": "Site adresi",
 "site_adresi": "Site adresi", "mobil_uygulama": "Mobil uygulama adı", "sayfa_kaydi": "Kaç arama sayfasında görüldüğü",
 "isletme_adi": "İşletme adı (ETBİS profili)", "isletme_turu": "İşletme türü (limited, anonim, şahıs…)",
 "etbis_kayit_tarihi": "ETBİS'e kayıt tarihi", "hakkinda": "Site hakkında metni",
 "mal_hizmetler": "Satılan mal/hizmet listesi", "odeme_turleri": "Kabul edilen ödeme türleri",
 "diger_siteler": "Aynı işletmenin diğer siteleri", "profil_var": "Profil sayfası çekilebildi mi",
 "profil_cekim_zamani": "Profilin çekildiği an", "restricted_column_policy": "Kısıtlı sütun politikası",
 "platform": "Platform adı", "title": "Başlık", "urun_adi": "Ürün adı", "description": "Açıklama",
 "aciklama": "Açıklama", "category_title": "Kategori başlığı", "original_price": "Liste fiyatı",
 "discounted_price": "İndirimli fiyat", "indirimli_fiyat": "İndirimli fiyat", "is_sold_out": "Tükendi mi",
 "tukendi": "Tükendi mi", "product_ref": "Platformdaki ürün referansı", "category_ref": "Platformdaki kategori referansı",
 "rating_value": "Puan", "minimum_order_value": "Minimum sepet tutarı", "min_sepet": "Minimum sepet tutarı",
 "delivery_time_lower_min": "Teslimat süresi alt sınırı (dk)", "teslimat_alt_dk": "Teslimat süresi alt sınırı (dk)",
 "delivery_time_upper_min": "Teslimat süresi üst sınırı (dk)", "teslimat_ust_dk": "Teslimat süresi üst sınırı (dk)",
 "delivery_fee_total": "Teslimat ücreti", "teslimat_ucreti": "Teslimat ücreti", "delivery_fee_original": "İndirimsiz teslimat ücreti",
 "delivery_provider": "Teslimatı yapan", "delivery_hours_json": "Teslimat saatleri", "page_kind": "Sayfa türü",
 "price_range": "Fiyat aralığı", "fiyat_araligi": "Fiyat aralığı", "cuisine_json": "Mutfak listesi", "mutfak": "Mutfak listesi",
 "jsonld_rating": "Sayfa işaretlemesindeki puan", "jsonld_rating_count": "Sayfa işaretlemesindeki puan sayısı",
 "metrics_note": "Ölçüm notu (platform varsayılan konumu vb.)", "venue_code": "Platform mekân kodu",
 "parse_error": "Ayrıştırma hatası notu", "isletme_id": "İşletme kimliği (poi.poi_id)", "gozlem_id": "Gözlem kimliği (tekil)",
 "urun_id": "Ürün kimliği (product.product_id)", "seri_id": "Seri kimliği", "baglanti_id": "Bağlantı kimliği",
 "edinim_sinifi": "Veri nasıl edinildi (resmî açık / web)", "dagitim_sinifi": "Dağıtım sınıfı (public/internal/restricted)",
 "kaynak_satir_ozeti": "Kaynak satırın içerik özeti (sha256)",
 # analitik (mahalle/ilçe zekâsı)
 "pop_2024_web": "2024 nüfus (web kaynağı)", "pop_2025_tuik": "2025 nüfus (TÜİK ADNKS)", "pop_2024_tuik": "2024 nüfus (TÜİK ADNKS)",
 "konut_satilik_m2": "Konut satılık m² fiyatı", "konut_kiralik_m2": "Konut kiralık m² fiyatı",
 "konut_ort_fiyat": "Ortalama konut ilan fiyatı", "konut_ilan_sayisi": "Konut ilan sayısı",
 "konut_brut_kira_getirisi": "Brüt kira getirisi (%)", "konut_amortisman_yil": "Amortisman süresi (yıl)",
 "konut_ort_bina_yasi": "Ortalama bina yaşı", "konut_satilik_kalma_gun": "İlanın satılana kadar kalma süresi (gün)",
 "arsa_satilik_m2": "Arsa satılık m² fiyatı", "arsa_min_m2": "Arsa en düşük m² fiyatı", "arsa_max_m2": "Arsa en yüksek m² fiyatı",
 "arsa_ilan_sayisi": "Arsa ilan sayısı", "arsa_fiyat_endeksi": "Arsa fiyat endeksi", "tarla_satilik_m2": "Tarla satılık m² fiyatı",
 "poi_count": "İşletme sayısı", "poi_count_coord_verified": "Koordinatı poligonla doğrulanmış işletme sayısı",
 "yeme_icme_count": "Yeme-içme işletmesi sayısı", "kuafor_guzellik_count": "Kuaför/güzellik işletmesi sayısı",
 "market_count": "Market sayısı", "restoran_count": "Restoran sayısı", "poi_avg_rating": "Ortalama işletme puanı",
 "poi_google_osm_count": "Hem Google hem OSM'de görülen işletme sayısı", "poi_osm_only_count": "Yalnız OSM'de görülen işletme sayısı",
 "poi_yemeksepeti_count": "Sipariş platformunda görülen işletme sayısı", "poi_type_unknown_count": "Türü çözülememiş işletme sayısı",
 "osm_poi_on_map_2026": "2026 OSM anlık görüntüsündeki işletme sayısı", "osm_added_2025_2026": "2025→2026 haritaya eklenen",
 "osm_removed_2025_2026": "2025→2026 haritadan kalkan", "osm_map_churn_2025_2026": "Harita devir oranı (eklenen+kalkan)",
 "konut_satis_son12ay": "Son 12 ayda konut satışı (TÜİK)", "konut_satis_2025": "2025 konut satışı (TÜİK)",
 "konut_satis_2024": "2024 konut satışı (TÜİK)", "yapi_ruhsati_daire_2025": "2025 yapı ruhsatı daire sayısı",
 "kullanma_izni_daire_2025": "2025 yapı kullanma izni daire sayısı", "ses_skor_2023": "TÜİK SES 2023 sosyoekonomik skor",
 "mahalle_sayisi": "Mahalle sayısı", "market_sube_sayisi": "Market şubesi sayısı",
 "sarj_istasyonu_sayisi": "Şarj istasyonu sayısı", "turizm_tesisi_sayisi": "Belgeli turizm tesisi sayısı",
 "google_poi_sayisi": "Google kaynaklı işletme sayısı", "toplam_poi": "Toplam işletme sayısı",
 "urun_cesidi": "Farklı ürün sayısı", "sube": "Şube sayısı", "gozlem": "Gözlem sayısı", "ort_fiyat": "Ortalama fiyat",
 "ilk_gun": "İlk gözlem günü", "son_gun": "Son gözlem günü", "ilk_donem": "İlk dönem", "son_donem": "Son dönem",
 "katman": "Katman", "sema": "Şema", "tablo": "Tablo", "kolon": "Sütun", "satir_sayisi": "Satır sayısı",
 "kolon_sayisi": "Sütun sayısı", "sira": "Sütun sırası", "tip": "Veri tipi", "otomatik": "Açıklama kalıptan mı üretildi",
 "hassas": "Kısıtlı (kişisel/kimlik) sütun mu", "guncellenme": "Sözlüğün üretim zamanı",
 "key": "Anahtar", "value": "Değer",
 # OSM yaşam döngüsü / anlık görüntü
 "snapshot_date": "Anlık görüntünün tarihi", "prev_date": "Karşılaştırılan önceki anlık görüntünün tarihi",
 "category_group": "Kategori grubu (yeme-içme, perakende…)", "present_on_map": "O tarihte haritada var mıydı",
 "added_to_map": "Haritaya eklenen sayısı", "removed_from_map": "Haritadan kalkan sayısı",
 "net_change": "Net değişim (eklenen − kalkan)", "map_churn_rate": "Harita devir oranı",
 "with_name_change": "Adı değişenlerin sayısı", "method": "Kullanılan yöntem", "built_at": "Üretim zamanı (UTC)",
 "osm_ref": "OSM nesne referansı (tip+kimlik)", "osm_type": "OSM nesne tipi (node/way/relation)",
 "osm_category": "OSM kategori etiketi", "first_seen_map": "Haritada ilk görüldüğü anlık görüntü",
 "last_seen_map": "Haritada son görüldüğü anlık görüntü", "map_status": "Harita durumu (halen var / kalkmış)",
 "snapshot_count": "Kaç anlık görüntüde göründüğü", "name_change_count": "Ad değişikliği sayısı",
 "brand_change_count": "Marka değişikliği sayısı", "move_count": "Konum değişikliği sayısı",
 "history_json": "Anlık görüntü geçmişi (ham)", "left_censored": "İlk anlık görüntüden önce de var olabilir",
 "right_open": "Son anlık görüntüde hâlâ var (kapanış gözlenmedi)",
 "event_semantics": "UYARI: bu satır haritadaki değişimi anlatır; işletmenin gerçekten açıldığı/kapandığı anlamına gelmez",
 # eşleme sözlükleri ve adaylar
 "il_n": "İl adı (sadeleştirilmiş)", "ilce_n": "İlçe adı (sadeleştirilmiş)", "web_name": "Kaynak sitedeki ad",
 "tkgm_band": "TKGM eşleşme bandı", "tkgm_score": "TKGM eşleşme skoru", "tkgm_flags": "TKGM eşleşme uyarıları",
 "tuik_band": "TÜİK eşleşme bandı", "tuik_score": "TÜİK eşleşme skoru", "tuik_flags": "TÜİK eşleşme uyarıları",
 "google_place_id": "Kaynağın yer kimliği", "has_reviews": "Yorumu var mı", "confidence": "Güven 0-1",
 "band": "Güven bandı", "classifier_version": "Sınıflandırıcı sürümü", "version": "Sürüm",
 "sector": "Sektör", "evidence": "Kararın dayandığı kanıt", "flags": "Uyarı bayrakları",
 "search_term": "Toplamada kullanılan arama terimi", "payload_sha256": "Kaynak yanıtının içerik özeti",
 "period_assignment": "Dönemin hangi kurala göre atandığı", "link_id": "Bağlantı kimliği (tekil)",
 "kaynak_tablo": "Bu satırın türediği ambar tablosu", "source_socket_id": "Kaynaktaki soket kimliği",
 "yeme_icme_hint_count": "Sektör ipucuna göre yeme-içme sayılan işletme sayısı",
}

TABLO_ACIKLAMA_EK = {
 "mahalle_intelligence": "Mahalle düzeyi birleşik gösterge tablosu: nüfus, konut ve arsa fiyatları, işletme sayıları, OSM harita devri.",
 "ilce_intelligence": "İlçe düzeyi birleşik gösterge tablosu: nüfus, konut satışı, yapı ruhsatı, SES skoru, işletme sayıları.",
 "mahalle_poi_stats": "Mahalle başına işletme sayıları ve ortalama puan.",
 "ilce_poi_stats": "İlçe başına işletme sayıları ve ortalama puan.",
 "mahalle_price_wide": "Mahalle başına konut/arsa fiyat göstergeleri geniş biçimde (her gösterge bir sütun).",
 "mahalle_pop": "Mahalle nüfusu.", "mahalle_osm_latest": "Mahallenin en son OSM anlık görüntüsü özeti.",
 "mahalle_turnover_osm": "OSM anlık görüntülerinden mahalle bazlı işletme devir (açılış/kapanış) göstergeleri.",
 "price_latest_cutoff": "Kesim tarihine göre her mahalle-gösterge için en güncel fiyat gözlemi.",
 "ilce_indicators": "İlçe düzeyi resmî gösterge özetleri.",
 "ilce_urun_fiyat_ozet": "İlçe × kaynak × ürün için gözlem sayısı ve ortalama fiyat.",
 "ilce_yeni_kaynaklar": "İlçe başına market şubesi, şarj istasyonu ve turizm tesisi sayıları.",
 "mahalle_market_fiyat": "Mahalle başına market rafı fiyat gözlemleri: şube sayısı, ürün çeşidi, ortalama fiyat, tarih aralığı.",
 "isletme": "ÜRÜN KATMANI — işletme kayıtları (kişisel sütunlar çıkarılmış).",
 "isletme_kaynak": "ÜRÜN KATMANI — işletmenin hangi kaynak kayıtlarıyla eşleştiği.",
 "cografya": "ÜRÜN KATMANI — mahalle/ilçe/il/bölge sözlüğü ve sınırları.",
 "konut_fiyat_gozlem": "ÜRÜN KATMANI — gayrimenkul fiyat gözlemleri.",
 "urun_katalogu": "ÜRÜN KATMANI — ürün sözlüğü.", "urun_fiyat_gozlem": "ÜRÜN KATMANI — ürün fiyat gözlemleri.",
 "gosterge_gozlem": "ÜRÜN KATMANI — resmî bağlam göstergeleri.",
 "tuik_seri": "ÜRÜN KATMANI — TÜİK SDMX seri tanımları.", "tuik_gozlem": "ÜRÜN KATMANI — TÜİK SDMX gözlemleri.",
 "sarj_soketi": "ÜRÜN KATMANI — şarj soketleri.",
 "eticaret_sitesi": "ÜRÜN KATMANI — ETBİS e-ticaret siteleri (MERSİS/vergi/KEP çıkarılmış).",
 "menu_kalemi": "ÜRÜN KATMANI — menü kalemleri ve fiyatları.",
 "teslimat_gozlem": "ÜRÜN KATMANI — teslimat ve puan gözlemleri.",
 "mahalle_zeka": "ÜRÜN KATMANI — mahalle düzeyi birleşik göstergeler.",
 "ilce_zeka": "ÜRÜN KATMANI — ilçe düzeyi birleşik göstergeler.",
}


def free_bytes(p): st = os.statvfs(p); return st.f_bavail * st.f_frsize


def kolon_acikla(tablo, kolon):
    if kolon in KOLON_ACIK: return KOLON_ACIK[kolon], False
    for k, v in KOLON_KALIP:
        if kolon == k or (k.endswith("_") and kolon.startswith(k)) or (k.startswith("_") and kolon.endswith(k)):
            return v, True
    return "", False


def main():
    t0 = time.time(); now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if free_bytes(OUT) < MIN_FREE: raise SystemExit("DİSK KORUMASI")
    c = duckdb.connect(str(CAN)); c.execute("LOAD spatial"); c.execute("SET memory_limit='2GB'"); c.execute("SET threads=2")
    c.execute(f"SET temp_directory='{OUT}/tmp/canon16'")
    rep = {"built_at": now, "stage": "structure_v1"}

    # ---- A) indeksler ----
    ok, fail = [], []
    for t, col in INDEXES:
        nm = f"idx_{t}_{col}"
        try:
            c.execute(f'CREATE INDEX IF NOT EXISTS {nm} ON {t} ("{col}")'); ok.append(nm)
        except Exception as e:
            fail.append({nm: str(e)[:120]})
    rep["indeks"] = {"kurulan": len(ok), "hata": fail}
    print(f"  indeks: {len(ok)} kuruldu, {len(fail)} hata", flush=True)

    # ---- B) ürün katmanı ----
    c.execute("CREATE SCHEMA IF NOT EXISTS urun")
    for nm, body in URUN_VIEWS:
        c.execute(f"CREATE OR REPLACE VIEW urun.{nm} AS {body}")
    rep["urun_gorunum"] = {nm: c.execute(f"SELECT count(*) FROM urun.{nm}").fetchone()[0] for nm, _ in URUN_VIEWS}
    # ürün katmanında kişisel/kimlik sütunu kalmadığının kanıtı
    yasak = ("phone", "telefon", "mersis", "vergi_no", "kep", "email", "e_posta")
    sizinti = c.execute(f"""SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema='urun' AND ({' OR '.join(f"lower(column_name) LIKE '%{y}%'" for y in yasak)})""").fetchall()
    rep["urun_kisisel_sizinti"] = sizinti
    print(f"  urun şeması: {len(URUN_VIEWS)} görünüm, kişisel sütun sızıntısı: {len(sizinti)}", flush=True)

    # ---- C) veri sözlüğü ----
    c.execute("""CREATE OR REPLACE TABLE meta_tablo (sema VARCHAR, tablo VARCHAR, satir_sayisi BIGINT,
        kolon_sayisi INTEGER, aciklama VARCHAR, katman VARCHAR, guncellenme VARCHAR)""")
    c.execute("""CREATE OR REPLACE TABLE meta_kolon (sema VARCHAR, tablo VARCHAR, kolon VARCHAR, sira INTEGER,
        tip VARCHAR, aciklama VARCHAR, otomatik BOOLEAN, hassas BOOLEAN)""")
    tabs = c.execute("""SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_catalog='geoprop_canonical_v1_1' AND table_type IN ('BASE TABLE','VIEW') ORDER BY 1,2""").fetchall()
    for sch, tab in tabs:
        try: n = c.execute(f'SELECT count(*) FROM "{sch}"."{tab}"').fetchone()[0]
        except Exception: n = None
        cols = c.execute("""SELECT column_name, ordinal_position, data_type FROM information_schema.columns
            WHERE table_schema=? AND table_name=? ORDER BY ordinal_position""", [sch, tab]).fetchall()
        katman = {"main": "kanonik", "analytics": "analitik", "urun": "ürün (satışa dönük)"}.get(sch, sch)
        c.execute("INSERT INTO meta_tablo VALUES (?,?,?,?,?,?,?)",
                  [sch, tab, n, len(cols), TABLO_ACIKLAMA.get(tab, TABLO_ACIKLAMA_EK.get(tab, "")), katman, now])
        for cn, pos, typ in cols:
            acik, oto = kolon_acikla(tab, cn)
            hassas = cn.startswith("restricted_") or cn in ("phone", "osm_phone")
            c.execute("INSERT INTO meta_kolon VALUES (?,?,?,?,?,?,?,?)", [sch, tab, cn, pos, typ, acik, oto, hassas])
    rep["sozluk"] = {"tablo": c.execute("SELECT count(*) FROM meta_tablo").fetchone()[0],
                     "kolon": c.execute("SELECT count(*) FROM meta_kolon").fetchone()[0],
                     "aciklamasiz_tablo": c.execute("SELECT count(*) FROM meta_tablo WHERE aciklama=''").fetchone()[0],
                     "aciklamasiz_kolon": c.execute("SELECT count(*) FROM meta_kolon WHERE aciklama=''").fetchone()[0]}

    # ---- D) analitik tazeleme ----
    c.execute("""CREATE OR REPLACE TABLE analytics.mahalle_market_fiyat AS
        SELECT geo_id mahalle_geo_id, count(*) gozlem, count(DISTINCT product_id) urun_cesidi,
               count(DISTINCT poi_id) sube, round(avg(price_value), 2) ort_fiyat,
               min(period) ilk_gun, max(period) son_gun
        FROM product_price_observation WHERE source_system='marketfiyati_branch' AND geo_id IS NOT NULL GROUP BY 1""")
    c.execute("""CREATE OR REPLACE TABLE analytics.ilce_urun_fiyat_ozet AS
        SELECT geo_id ilce_geo_id, source_system, product_id, count(*) gozlem,
               round(avg(price_value), 4) ort_fiyat, min(period) ilk_donem, max(period) son_donem
        FROM product_price_observation WHERE level='ilce' AND price_value IS NOT NULL GROUP BY 1,2,3""")
    rep["analitik"] = {"mahalle_market_fiyat": c.execute("SELECT count(*) FROM analytics.mahalle_market_fiyat").fetchone()[0],
                       "ilce_urun_fiyat_ozet": c.execute("SELECT count(*) FROM analytics.ilce_urun_fiyat_ozet").fetchone()[0]}
    c.execute("DELETE FROM _meta WHERE key IN ('structure_version','structure_built_at')")
    c.execute("INSERT INTO _meta VALUES ('structure_version', 'structure_v1'), ('structure_built_at', ?)", [now])
    c.execute("CHECKPOINT")

    # ---- sözlük dışa aktarım ----
    rows = c.execute("""SELECT t.katman, t.sema, t.tablo, t.satir_sayisi, t.aciklama, k.kolon, k.tip, k.aciklama, k.hassas
        FROM meta_tablo t JOIN meta_kolon k ON k.sema=t.sema AND k.tablo=t.tablo ORDER BY t.sema, t.tablo, k.sira""").fetchall()
    csvp = OUT / "reports" / "VERI_SOZLUGU.csv"
    with open(csvp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["katman", "sema", "tablo", "satir_sayisi", "tablo_aciklama", "kolon", "tip", "kolon_aciklama", "hassas"])
        w.writerows(rows)
    tab_rows = c.execute("SELECT katman, sema, tablo, satir_sayisi, kolon_sayisi, aciklama FROM meta_tablo ORDER BY satir_sayisi DESC NULLS LAST").fetchall()
    def esc(x): return (str(x) if x is not None else "").replace("&", "&amp;").replace("<", "&lt;")
    html = ["<!doctype html><meta charset='utf-8'><title>GEOPROP veri sözlüğü</title>",
            "<style>body{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:24px;max-width:1100px}",
            "h1{font-size:22px}h2{font-size:17px;margin-top:28px}table{border-collapse:collapse;width:100%;margin:8px 0}",
            "th,td{border:1px solid #ddd;padding:5px 7px;text-align:left;vertical-align:top}th{background:#f4f4f4}",
            "td.n{text-align:right;font-variant-numeric:tabular-nums}.h{background:#fff3cd}small{color:#666}</style>",
            f"<h1>GEOPROP veri sözlüğü</h1><p><small>Üretim: {esc(now)} · sürüm canonical_v1.7 / structure_v1 · "
            "sarı satır = kısıtlı (kişisel/kimlik) sütun, ürün katmanına çıkmaz</small></p>",
            "<h2>Tablolar</h2><table><tr><th>Katman</th><th>Tablo</th><th>Satır</th><th>Sütun</th><th>Açıklama</th></tr>"]
    for katman, sema, tab, n, kn, ac in tab_rows:
        html.append(f"<tr><td>{esc(katman)}</td><td><a href='#{esc(sema)}_{esc(tab)}'>{esc(sema)}.{esc(tab)}</a></td>"
                    f"<td class='n'>{n:,}</td><td class='n'>{kn}</td><td>{esc(ac)}</td></tr>".replace(",", "."))
    html.append("</table>")
    cur = None
    for katman, sema, tab, n, tac, kol, tip, kac, hassas in rows:
        if (sema, tab) != cur:
            if cur: html.append("</table>")
            cur = (sema, tab)
            html.append(f"<h2 id='{esc(sema)}_{esc(tab)}'>{esc(sema)}.{esc(tab)}</h2><p>{esc(tac)}</p>"
                        "<table><tr><th>Sütun</th><th>Tip</th><th>Açıklama</th></tr>")
        html.append(f"<tr{' class=h' if hassas else ''}><td>{esc(kol)}</td><td><small>{esc(tip)}</small></td><td>{esc(kac)}</td></tr>")
    html.append("</table>")
    htmlp = OUT / "reports" / "VERI_SOZLUGU.html"
    htmlp.write_text("\n".join(html), encoding="utf-8")
    rep["cikti"] = {"csv": str(csvp), "html": str(htmlp)}
    rep["seconds"] = round(time.time() - t0); rep["db_gb"] = round(CAN.stat().st_size / 1e9, 2)
    (OUT / "canonical" / "v1.1" / "build_report_structure_v1.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    with open(OUT / "logs" / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({"at": now, "action": "PHASE5_STRUCTURE_V1", **rep,
                            "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    c.close()


if __name__ == "__main__":
    main()
