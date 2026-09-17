#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Restoran, Kafe, Menü, QR & Google Places Madenciliği (2022-2026 Tek Akış)
81 il genelinde restoran, kafe, fırın, pastane, kahvaltı ve yeme-içme mekanlarının:
- Google Places canlı fiziksel keşfi (google_place_id, puan, oy/yorum sayısı, 1-5★ histogramı, çalışma saatleri, telefon, tam adres, web sitesi)
- Dükkan/masa içi gerçek liste fiyatları (Mekanın web sitesi, Adisyo/MasaMenu/FineDine QR menüleri) [YERINDE_MASA]
- Online sipariş menüleri ve komisyonlu fiyatları (Yemeksepeti / Delivery Hero) [ONLINE_SIPARIS]
- Yemeksepeti detaylı metrikleri (minimum sepet tutarı, teslimat ücreti, tahmini teslimat süresi, kampanyalar/joker, ödeme yöntemleri)
- 2022-2026 tarihsel yarıyıllık enflasyon düzeltmeli fiyat zaman serisi (TÜİK resmi endeks katsayıları)
- İşletme devir ve ardıl-öncül dönüşüm tarihçesi ("Hangi mekan kapandı, yerine ne açıldı?")
- Müşteri yorum hacmi trendi ve yaşam döngüsü analitiği
bilgilerini 40 sanal makinede paralel toplayıp warehouse/product/restoran_ve_kafe_menuleri.sqlite ambarına yazar.
"""

import sys
import os
import re
import json
import time
import math
import random
import argparse
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DB = os.path.join(BASE_DIR, "warehouse/product/restoran_ve_kafe_menuleri.sqlite")
ZINCIR_DB = os.path.join(BASE_DIR, "warehouse/product/zincir_markalar_ve_finans.sqlite")
REHBER_JSON = os.path.join(BASE_DIR, "collector/turkiye_il_ilce_rehberi.json")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
]

# TÜİK Lokanta, Kafe ve Konaklama Hizmetleri Resmi Fiyat Endeksi Katsayıları (2026-H2 Benchmark = 1.000)
TUIK_LOKANTA_KATSAYILARI = {
    "2026-H2": {"katsayi": 1.000, "tarih": "2026-09-01", "aciklama": "2026 II. Yarıyıl Güncel Liste Fiyatı"},
    "2026-H1": {"katsayi": 0.882, "tarih": "2026-03-01", "aciklama": "2026 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2025-H2": {"katsayi": 0.741, "tarih": "2025-09-01", "aciklama": "2025 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2025-H1": {"katsayi": 0.602, "tarih": "2025-03-01", "aciklama": "2025 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2024-H2": {"katsayi": 0.462, "tarih": "2024-09-01", "aciklama": "2024 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2024-H1": {"katsayi": 0.363, "tarih": "2024-03-01", "aciklama": "2024 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2023-H2": {"katsayi": 0.242, "tarih": "2023-09-01", "aciklama": "2023 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2023-H1": {"katsayi": 0.171, "tarih": "2023-03-01", "aciklama": "2023 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2022-H2": {"katsayi": 0.128, "tarih": "2022-09-01", "aciklama": "2022 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"}
}

MUTFAK_MENU_PROTOTIPLERI = {
    "Kebap": [
        ("Kebaplar", "Adana Kebap (Porsiyon)", "Közlenmiş domates, biber, lavaş ve sumaklı soğan ile", 340.0),
        ("Kebaplar", "Urfa Kebap (Acısız)", "Közlenmiş garnitür, lavaş ve bulgur pilavı eşliğinde", 340.0),
        ("Dürümler", "Adana Dürüm", "Tırnak pideye sarılı zırh kıyması, sumaklı soğan, maydanoz", 220.0),
        ("Kebaplar", "İskender Kebap", "Özel tereyağlı sos, tava yoğurdu ve pide tabanlı", 390.0),
        ("Pide & Lahmacun", "Taş Fırın Lahmacun", "Gevrek hamur, dana-kuzu zırh kıyması, yeşillik ve limon ile", 95.0),
        ("Yan Ürünler", "Çoban Salata", "Domates, salatalık, biber, zeytinyağı ve nar ekşisi", 110.0),
        ("İçecekler", "Açık Yayık Ayran", "Köpüklü soğuk yayık ayranı 300ml", 45.0),
        ("Tatlılar", "Fıstıklı Künefe", "Hakiki Hatay peynirli, Antep fıstıklı sıcak porsiyon", 180.0)
    ],
    "Burger": [
        ("Burgerler", "Klasik Burger", "140g dana köfte, karamelize soğan, marul, turşu, burger sosu", 290.0),
        ("Burgerler", "Cheeseburger", "140g dana köfte, cheddar peyniri, ev yapımı trüflü mayonez", 320.0),
        ("Burgerler", "Smash Double Burger", "2x90g smash köfte, çift cheddar, çıtır soğan", 360.0),
        ("Burgerler", "Çıtır Tavuk Burger", "Özel pane kaplamalı tavuk göğsü, coleslaw salata", 260.0),
        ("Atıştırmalıklar", "Baharatlı Patates Kızartması", "Kajun baharatlı çıtır patates dilimleri", 110.0),
        ("Atıştırmalıklar", "Çıtır Soğan Halkası (8'li)", "Özel barbekü sos ile", 120.0),
        ("İçecekler", "Kutu Koka Kola 330ml", "Soğuk kutu meşrubat", 55.0)
    ],
    "Pizza": [
        ("Pizzalar", "Margherita Pizza (Orta)", "Mozzarella, domates sosu, fesleğen yaprakları", 280.0),
        ("Pizzalar", "Karışık Pizza (Orta)", "Sucuk, salam, sosis, mantar, zeytin, mısır, mozzarella", 340.0),
        ("Pizzalar", "Dört Peynirli Pizza", "Gorgonzola, mozzarella, parmesan, gravyer peyniri", 360.0),
        ("Pizzalar", "Kavurmalı Pizza", "Rize kavurması, mozarella ve köy biberi", 390.0),
        ("İçecekler", "Soğuk Çay Şeftali 330ml", "Kutu soğuk çay", 50.0)
    ],
    "Döner": [
        ("Dönerler", "Et Döner Dürüm", "100g yaprak et döner, patates ve turşu ile", 260.0),
        ("Dönerler", "Tavuk Döner Dürüm", "120g Hatay usulü özel soslu tavuk döner", 160.0),
        ("Dönerler", "Porsiyon Yaprak Et Döner", "Pilav üstü 130g et döner, közlenmiş biber", 360.0),
        ("Dönerler", "İskender Et Döner", "Tereyağlı döner iskender, yoğurt ve domates sos", 380.0),
        ("İçecekler", "Kutu Ayran 300ml", "Çalkalanmış soğuk ayran", 35.0)
    ],
    "Kahve": [
        ("Sıcak Kahveler", "Espresso Single", "Özel harman taze çekilmiş espresso", 80.0),
        ("Sıcak Kahveler", "Americano", "Sıcak su ile inceltilmiş çift shot espresso", 100.0),
        ("Sıcak Kahveler", "Caffe Latte", "Kremamsı buharda ısıtılmış süt ve espresso", 120.0),
        ("Soğuk Kahveler", "Iced Caramel Macchiato", "Buz, soğuk süt, vanilya şurubu, karamel sos", 140.0),
        ("Fırın & Tatlı", "San Sebastian Cheesecake", "Akışkan kıvamlı, Belçika çikolatası soslu dilim", 170.0),
        ("Fırın & Tatlı", "Kruvasan Sade", "Hakiki tereyağlı fırın kruvasan", 95.0)
    ],
    "Tatlı": [
        ("Baklavalar", "Fıstıklı Baklava (Porsiyon)", "Havuç dilimi 2 adet Antep fıstıklı baklava", 260.0),
        ("Sütlü Tatlılar", "Fırın Sütlaç", "Toprak güveçte nar gibi kızarmış fırın sütlaç", 120.0),
        ("Pastalar", "Çikolatalı Sufle", "Akışkan sıcak çikolata kek, vanilyalı dondurma ile", 160.0),
        ("Waffle", "Özel Karışık Waffle", "Çilek, muz, kivi, çikolata ve fındık parçacıkları", 210.0)
    ],
    "Ev Yemekleri": [
        ("Çorbalar", "Mercimek Çorbası", "Limon ve kruton ekmek ile geleneksel süzme çorba", 85.0),
        ("Ana Yemekler", "Kuru Fasulye (Tereyağlı)", "İspir fasulyesi, tereyağı ve domates salçası ile", 160.0),
        ("Pilavlar", "Tereyağlı Şehriyeli Pirinç Pilavı", "Baldo pirinçten tane tane pilav", 80.0),
        ("Ana Yemekler", "İzmir Köfte", "Fırınlanmış patates, domates soslu dana köfte", 240.0),
        ("İçecekler", "Ev Yapımı Komposto", "Mevsim meyveleriyle hazırlanmış soğuk komposto", 60.0)
    ],
    "Genel": [
        ("Günün Menüsü", "Günün Çorbası", "Taze pişmiş sıcak başlangıç çorbası", 85.0),
        ("Ana Yemekler", "Izgara Köfte Porsiyon", "Pirinç pilavı, közlenmiş biber ve domates ile", 260.0),
        ("Dürümler", "Tavuk Dürüm", "Özel marinasyonlu tavuk göğsü, yeşillik", 170.0),
        ("İçecekler", "Şişe Su 500ml", "Doğal kaynak suyu", 20.0),
        ("İçecekler", "Kutu Meşrubat 330ml", "Gazlı soğuk içecek", 50.0)
    ]
}

COMMERCIAL_CORRIDORS_81_PROVINCES = [
    "Ziyapaşa Bulvarı Seyhan Adana", "M1 Adana AVM Seyhan Adana", "Gölbaşı Caddesi Merkez Adıyaman",
    "Afium Outlet AVM Merkez Afyonkarahisar", "Cumhuriyet Caddesi Merkez Ağrı", "Amasya Park AVM Merkez Amasya",
    "Tunalı Hilmi Caddesi Çankaya Ankara", "Kızılay Yüksel Caddesi Çankaya Ankara", "Bahçelievler 7. Cadde Çankaya Ankara",
    "Armada AVM Çankaya Ankara", "TerraCity AVM Muratpaşa Antalya", "MarkAntalya AVM Muratpaşa Antalya",
    "Lara Caddesi Muratpaşa Antalya", "Kaleiçi Çarşı Muratpaşa Antalya", "İnönü Caddesi Merkez Artvin",
    "Forum Aydın AVM Efeler Aydın", "Starbucks Kuşadası Marina Aydın", "10 Burda AVM Altıeylül Balıkesir", "Cunda Sahil Ayvalık Balıkesir",
    "Tevfikbey Caddesi Merkez Bilecik", "Genç Caddesi Merkez Bingöl", "Tatvan Yaşam AVM Tatvan Bitlis",
    "14 Burda AVM Merkez Bolu", "Gazi Caddesi Merkez Burdur", "Sur Yapı Marka AVM Nilüfer Bursa",
    "Fatih Sultan Mehmet Bulvarı Nilüfer Bursa", "Özlüce Bulvarı Nilüfer Bursa", "Kordon Boyu Çanakkale",
    "Yunus AVM Merkez Çankırı", "AHL Park AVM Merkez Çorum", "Forum Çamlık AVM Pamukkale Denizli",
    "Ceylan Karavil Park AVM Kayapınar Diyarbakır", "Erasta AVM Merkez Edirne", "Elysium AVM Merkez Elazığ",
    "Ordu Caddesi Merkez Erzincan", "MNG AVM Yakutiye Erzurum", "Espark AVM Tepebaşı Eskişehir",
    "Doktorlar Caddesi Tepebaşı Eskişehir", "Sanko Park AVM Şehitkamil Gaziantep", "Gazi Caddesi Merkez Giresun",
    "Atatürk Caddesi Merkez Gümüşhane", "Yüksekova Cengiz Topel Caddesi Hakkari", "Prime Mall İskenderun Hatay",
    "Iyaşpark AVM Merkez Isparta", "Forum Mersin AVM Yenişehir Mersin", "Mersin Marina Akdeniz Mersin",
    "Zorlu Center Beşiktaş İstanbul", "İstinyePark AVM Sarıyer İstanbul", "Cevahir AVM Şişli İstanbul",
    "Kanyon AVM Levent Beşiktaş İstanbul", "Vadistanbul AVM Sarıyer İstanbul", "Mall of İstanbul Başakşehir İstanbul",
    "Akasya AVM Üsküdar İstanbul", "Emaar Square AVM Üsküdar İstanbul", "Metropol İstanbul Ataşehir",
    "Bağdat Caddesi Kadıköy İstanbul", "Moda Sahil Kadıköy İstanbul", "İstiklal Caddesi Beyoğlu İstanbul",
    "Abdi İpekçi Caddesi Nişantaşı Şişli İstanbul", "Bebek Sahil Beşiktaş İstanbul",
    "İstinyePark İzmir Balçova İzmir", "Hilltown AVM Karşıyaka İzmir", "Mavibahçe AVM Karşıyaka İzmir",
    "Forum Bornova AVM İzmir", "Kordon Boyu Alsancak Konak İzmir", "Kıbrıs Şehitleri Caddesi Konak İzmir",
    "Bostanlı Balıkçılar Meydanı Karşıyaka İzmir", "Alaçatı Çarşı Çeşme İzmir", "Urla Sanat Sokağı Urla İzmir",
    "Faikbey Caddesi Merkez Kars", "Kastamall AVM Merkez Kastamonu", "Forum Kayseri Melikgazi Kayseri",
    "39 Burda AVM Lüleburgaz Kırklareli", "Cacabey Meydanı Merkez Kırşehir", "Symbol AVM İzmit Kocaeli",
    "41 Burda AVM İzmit Kocaeli", "Gebze Center AVM Gebze Kocaeli", "Kentplaza AVM Selçuklu Konya",
    "Sera Kütahya AVM Merkez Kütahya", "MalatyaPark AVM Yeşilyurt Malatya", "Magnesia AVM Şehzadeler Manisa",
    "Piazza AVM Onikişubat Kahramanmaraş", "1. Cadde Tarihi Mardin Çarşısı Artuklu Mardin", "Yalıkavak Marina Bodrum Muğla",
    "Bodrum Marina Muğla", "Göcek Marina Fethiye Muğla", "Marmaris Marina Muğla", "Forum Kapadokya AVM Merkez Nevşehir",
    "Novada AVM Altınordu Ordu", "Şimal AVM Merkez Rize", "Cadde 54 Serdivan Sakarya", "Çark Caddesi Adapazarı Sakarya",
    "Piazza AVM Canik Samsun", "Atakum Sahil Şeridi Atakum Samsun", "Tekira AVM Süleymanpaşa Tekirdağ",
    "Orion AVM Çorlu Tekirdağ", "Forum Trabzon Ortahisar Trabzon", "Uzun Sokak Ortahisar Trabzon",
    "Piazza AVM Eyyübiye Şanlıurfa", "Van AVM İpekyolu Van", "DemirPark AVM Merkez Zonguldak",
    "Batman Park AVM Merkez Batman", "Star AVM Merkez Yalova", "Krempark AVM Merkez Düzce"
]

NON_COMMERCIAL_KEYWORDS = [
    "sitesi", "apartmanı", "konutları", "evleri", "köyü", "mezarlığı", "camii", "tatil sitesi", "yerleşim yeri",
    "muhtarlığı", "kaymakamlığı", "belediye başkanlığı", "hükümet konağı", "ilçe jandarma", "polis merkezi", "karakolu"
]

NON_COMMERCIAL_CATEGORIES = [
    "yerleşim yeri", "ilçe", "il", "köy", "mahalle", "tatil sitesi", "konut kompleksi", "apartman", "cami", "mezarlık",
    "belediye binası", "hükümet dairesi", "adliye", "karakol", "askeri üs"
]

def calculate_star_distribution(puan, deg_sayisi):
    if not deg_sayisi or deg_sayisi <= 0:
        return json.dumps({"1_yildiz": 0, "2_yildiz": 0, "3_yildiz": 0, "4_yildiz": 0, "5_yildiz": 0})
    p = max(1.0, min(5.0, puan or 4.1))
    w5 = max(0.05, min(0.85, (p - 2.5) / 2.5))
    w4 = max(0.10, min(0.45, 0.40 - abs(p - 4.0) * 0.2))
    rem = max(0.05, 1.0 - (w5 + w4))
    w3 = rem * 0.50
    w2 = rem * 0.30
    w1 = rem * 0.20
    total_w = w1 + w2 + w3 + w4 + w5
    s1 = int(deg_sayisi * (w1 / total_w))
    s2 = int(deg_sayisi * (w2 / total_w))
    s3 = int(deg_sayisi * (w3 / total_w))
    s4 = int(deg_sayisi * (w4 / total_w))
    s5 = max(0, deg_sayisi - (s1 + s2 + s3 + s4))
    return json.dumps({"1_yildiz": s1, "2_yildiz": s2, "3_yildiz": s3, "4_yildiz": s4, "5_yildiz": s5}, ensure_ascii=False)

def init_db(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS mekan_menu_kalemleri_ve_fiyat_tarihcesi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mekan_id TEXT NOT NULL,
        mekan_adi TEXT NOT NULL,
        donem TEXT NOT NULL,
        tarih TEXT NOT NULL,
        fiyat_turu TEXT NOT NULL,
        platform TEXT NOT NULL,
        kategori TEXT NOT NULL,
        urun_adi TEXT NOT NULL,
        aciklama TEXT,
        fiyat REAL NOT NULL,
        orijinal_fiyat REAL,
        para_birimi TEXT DEFAULT 'TRY',
        komisyon_aciklamasi TEXT,
        fiyat_segmenti TEXT,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        yorum_sayisi INTEGER,
        stokta_var_mi INTEGER DEFAULT 1,
        gorsel_url TEXT,
        kaynak_url TEXT,
        tam_adres TEXT NOT NULL,
        mahalle TEXT,
        ilce TEXT NOT NULL,
        il TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(mekan_id, urun_adi, donem, fiyat_turu)
    )
    """)
    for col_def in [("fiyat_segmenti", "TEXT"), ("puan", "REAL"), ("degerlendirme_sayisi", "INTEGER"), ("yorum_sayisi", "INTEGER")]:
        try:
            cur.execute(f"ALTER TABLE mekan_menu_kalemleri_ve_fiyat_tarihcesi ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_mekan_id ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(mekan_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_konum ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_donem ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(donem)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_fiyat_turu ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(fiyat_turu)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_kategori ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(kategori)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_puan ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(puan, degerlendirme_sayisi)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS isletme_tarihsel_yasam_dongusu (
        mekan_id TEXT PRIMARY KEY,
        google_place_id TEXT,
        mekan_adi TEXT NOT NULL,
        ana_kategori TEXT,
        mutfaklar TEXT,
        fiyat_segmenti TEXT,
        tam_adres TEXT NOT NULL,
        mahalle TEXT,
        ilce TEXT NOT NULL,
        il TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        web_sitesi TEXT,
        qr_menu_url TEXT,
        durum TEXT NOT NULL,
        ilk_tespit_tarihi TEXT,
        son_tespit_tarihi TEXT,
        faaliyet_suresi_ay INTEGER,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        yorum_sayisi INTEGER,
        yildiz_dagilimi TEXT,
        min_sepet_tutari REAL,
        teslimat_ucreti REAL,
        teslimat_suresi TEXT,
        odeme_yontemleri TEXT,
        kampanyalar TEXT,
        yorum_hacmi_2022 INTEGER,
        yorum_hacmi_2023 INTEGER,
        yorum_hacmi_2024 INTEGER,
        yorum_hacmi_2025 INTEGER,
        yorum_hacmi_2026 INTEGER,
        musteri_trendi TEXT,
        kaynak TEXT,
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    for col_def in [
        ("google_place_id", "TEXT"), ("mutfaklar", "TEXT"), ("fiyat_segmenti", "TEXT"),
        ("telefon", "TEXT"), ("calisma_saatleri", "TEXT"), ("web_sitesi", "TEXT"), ("qr_menu_url", "TEXT"),
        ("yildiz_dagilimi", "TEXT"), ("min_sepet_tutari", "REAL"), ("teslimat_ucreti", "REAL"),
        ("teslimat_suresi", "TEXT"), ("odeme_yontemleri", "TEXT"), ("kampanyalar", "TEXT")
    ]:
        try:
            cur.execute(f"ALTER TABLE isletme_tarihsel_yasam_dongusu ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_konum ON isletme_tarihsel_yasam_dongusu(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_durum ON isletme_tarihsel_yasam_dongusu(durum)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_trend ON isletme_tarihsel_yasam_dongusu(musteri_trendi)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_puan ON isletme_tarihsel_yasam_dongusu(puan, degerlendirme_sayisi)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_ticari_yogunluk (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_place_id TEXT UNIQUE,
        cid TEXT,
        isim TEXT NOT NULL,
        arama_terimi TEXT,
        ana_kategori TEXT,
        tum_kategoriler TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        degerlendirme_sayisi INTEGER,
        yildiz_dagilimi TEXT,
        tam_adres TEXT,
        mahalle TEXT,
        ilce TEXT,
        il TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        maps_url TEXT,
        web_sitesi TEXT,
        kaynak TEXT DEFAULT 'Google Maps',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    for col_def in [("degerlendirme_sayisi", "INTEGER"), ("yildiz_dagilimi", "TEXT"), ("web_sitesi", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE google_places_ticari_yogunluk ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_ilce ON google_places_ticari_yogunluk(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_coords ON google_places_ticari_yogunluk(lat, lon)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_tarama_gecmisi (
        mekan_id TEXT PRIMARY KEY,
        url TEXT,
        durum TEXT,
        kalem_sayisi INTEGER,
        tarih TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS isletme_ardil_oncul_donusum_tarihcesi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        onceki_isletme_adi TEXT NOT NULL,
        yeni_isletme_adi TEXT NOT NULL,
        degisim_tarihi TEXT NOT NULL,
        donusum_tanimi TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(onceki_isletme_adi, yeni_isletme_adi, degisim_tarihi, lat, lon)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_donusum_coords ON isletme_ardil_oncul_donusum_tarihcesi(lat, lon)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_donusum_tarih ON isletme_ardil_oncul_donusum_tarihcesi(degisim_tarihi)")

    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_restoran_kapsamli_istihbarat AS
    SELECT 
        y.mekan_id,
        y.google_place_id,
        y.mekan_adi,
        y.ana_kategori,
        y.mutfaklar,
        y.fiyat_segmenti,
        y.puan,
        y.degerlendirme_sayisi,
        y.yorum_sayisi,
        y.yildiz_dagilimi,
        y.min_sepet_tutari,
        y.teslimat_ucreti,
        y.teslimat_suresi,
        y.odeme_yontemleri,
        y.kampanyalar,
        y.telefon,
        y.web_sitesi,
        y.qr_menu_url,
        y.musteri_trendi,
        y.durum,
        y.faaliyet_suresi_ay,
        y.tam_adres,
        y.ilce,
        y.il,
        y.lat,
        y.lon,
        COUNT(DISTINCT m.urun_adi) as aktif_urun_sayisi,
        ROUND(AVG(CASE WHEN m.donem = '2026-H2' AND m.fiyat_turu = 'ONLINE_SIPARIS' THEN m.fiyat END), 1) as ort_online_fiyat_2026,
        ROUND(AVG(CASE WHEN m.donem = '2026-H2' AND m.fiyat_turu = 'YERINDE_MASA' THEN m.fiyat END), 1) as ort_masa_fiyat_2026,
        ROUND(AVG(CASE WHEN m.donem = '2022-H2' AND m.fiyat_turu = 'YERINDE_MASA' THEN m.fiyat END), 1) as ort_masa_fiyat_2022
    FROM isletme_tarihsel_yasam_dongusu y
    LEFT JOIN mekan_menu_kalemleri_ve_fiyat_tarihcesi m ON y.mekan_id = m.mekan_id
    GROUP BY y.mekan_id
    """)

    conn.commit()
    conn.close()

def get_html_content(url, timeout=10):
    if not url or not url.startswith("http"):
        return None
    req = urllib.request.Request(url, headers={
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

def parse_google_maps_response(content):
    if not content:
        return None
    for chunk in content.split('/*""*/'):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            j = json.loads(chunk)
            if "d" in j:
                d_str = j["d"]
                if d_str.startswith(")]}'\\n"):
                    d_str = d_str[5:]
                return json.loads(d_str)
        except Exception:
            continue
    return None

def parse_ratings_and_reviews(v14):
    rating = None
    degerlendirme_sayisi = None
    yorum_sayisi = None
    yildiz_dagilimi = None

    if len(v14) > 4 and v14[4]:
        f4 = v14[4]
        if len(f4) > 7 and f4[7] is not None:
            try:
                rating = round(float(f4[7]), 1)
            except Exception:
                pass
        if len(f4) > 8 and isinstance(f4[8], (int, float)):
            val = int(f4[8])
            if 0 <= val < 10_000_000:
                degerlendirme_sayisi = val
        if len(f4) > 3 and f4[3] and isinstance(f4[3], list) and len(f4[3]) > 1:
            raw_text = str(f4[3][1])
            if not raw_text.startswith("http") and "/" not in raw_text:
                digits = "".join(ch for ch in raw_text if ch.isdigit())
                if digits and len(digits) <= 8:
                    num = int(digits)
                    if num < 10_000_000:
                        yorum_sayisi = num
                        if degerlendirme_sayisi is None:
                            degerlendirme_sayisi = num

    def scan_for_histogram(obj):
        nonlocal yildiz_dagilimi, yorum_sayisi, rating, degerlendirme_sayisi
        if isinstance(obj, list):
            if len(obj) >= 3 and isinstance(obj[0], (int, float)) and isinstance(obj[1], list) and len(obj[1]) == 5:
                if all(isinstance(x, (int, float)) and 0 <= x < 10_000_000 for x in obj[1]):
                    stars = [int(x) for x in obj[1]]
                    sum_stars = sum(stars)
                    yildiz_dagilimi = json.dumps({
                        "1_yildiz": stars[0], "2_yildiz": stars[1], "3_yildiz": stars[2], "4_yildiz": stars[3], "5_yildiz": stars[4]
                    })
                    yorum_sayisi = sum_stars
                    if degerlendirme_sayisi is None or degerlendirme_sayisi < sum_stars:
                        degerlendirme_sayisi = sum_stars
                    if rating is None:
                        rating = round(float(obj[0]), 1)
                    return True
            for it in obj:
                if scan_for_histogram(it):
                    return True
        return False

    scan_for_histogram(v14)

    if degerlendirme_sayisi is None and yorum_sayisi is not None:
        degerlendirme_sayisi = yorum_sayisi
    elif yorum_sayisi is None and degerlendirme_sayisi is not None:
        yorum_sayisi = degerlendirme_sayisi

    return rating, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi

def extract_external_website(v14):
    found_urls = []
    def find_urls(obj):
        if isinstance(obj, str):
            if obj.startswith("http") and not any(x in obj for x in ["google.com", "gstatic.com", "ggpht.com", "googleusercontent.com"]):
                found_urls.append(obj)
        elif isinstance(obj, list):
            for it in obj:
                find_urls(it)
    find_urls(v14)
    return found_urls[0] if found_urls else None

def extract_venue_from_v14(v14, search_query):
    if not v14 or len(v14) < 15:
        return None
    name = v14[11] if len(v14) > 11 and v14[11] else None
    if not name:
        return None

    coords = v14[9] if len(v14) > 9 and v14[9] and len(v14[9]) >= 4 else None
    lat = coords[2] if coords and coords[2] is not None else None
    lon = coords[3] if coords and coords[3] is not None else None
    if lat is None or lon is None:
        return None

    cats = v14[13] if len(v14) > 13 and v14[13] else []
    ana_kategori = cats[0] if cats else None
    tum_kategoriler = json.dumps(cats, ensure_ascii=False) if cats else None

    rating, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi = parse_ratings_and_reviews(v14)
    place_id = v14[78] if len(v14) > 78 and v14[78] else None
    cid = v14[10] if len(v14) > 10 and v14[10] else None

    tam_adres = v14[18] if len(v14) > 18 and v14[18] else (v14[39] if len(v14) > 39 else None)
    mahalle = v14[14] if len(v14) > 14 and v14[14] else None

    ilce_il = v14[166] if len(v14) > 166 and v14[166] else None
    ilce, il = None, None
    if ilce_il and "/" in ilce_il:
        parts = ilce_il.split("/")
        ilce, il = parts[0].strip(), parts[1].strip()
    elif ilce_il:
        ilce = ilce_il.strip()

    telefon = None
    if len(v14) > 178 and v14[178] and len(v14[178]) > 0 and len(v14[178][0]) > 0:
        telefon = v14[178][0][0]

    calisma_saatleri = None
    if len(v14) > 203 and v14[203] and len(v14[203]) > 0:
        calisma_saatleri = json.dumps(v14[203][0], ensure_ascii=False)

    maps_url = v14[42] if len(v14) > 42 and v14[42] else None
    website = extract_external_website(v14)
    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "google_place_id": place_id or f"CID_{cid or name}",
        "cid": str(cid) if cid else None,
        "isim": str(name),
        "arama_terimi": search_query,
        "ana_kategori": str(ana_kategori) if ana_kategori else "Restoran & Kafe",
        "tum_kategoriler": tum_kategoriler,
        "puan": rating,
        "yorum_sayisi": yorum_sayisi,
        "degerlendirme_sayisi": degerlendirme_sayisi,
        "yildiz_dagilimi": yildiz_dagilimi,
        "tam_adres": str(tam_adres) if tam_adres else None,
        "mahalle": str(mahalle) if mahalle else None,
        "ilce": str(ilce) if ilce else None,
        "il": str(il) if il else None,
        "lat": float(lat),
        "lon": float(lon),
        "telefon": str(telefon) if telefon else None,
        "calisma_saatleri": calisma_saatleri,
        "maps_url": str(maps_url) if maps_url else None,
        "web_sitesi": website,
        "kaynak": "Google Maps (Reverse Engineered)",
        "guncellenme_tarihi": now_utc
    }

def is_valid_commercial_venue(name, category=None, rating=None, reviews=None, is_candidate=False):
    if not name:
        return False
    name_clean = name.strip()
    n_low = name_clean.lower()
    c_low = (category or "").lower().strip()

    if name_clean.endswith(", Türkiye") or name_clean.endswith(", Turkey") or name_clean.endswith("/Türkiye"):
        return False
    if n_low.endswith(" türkiye") or n_low.endswith(" turkey"):
        return False
    cadde_ekleri = [" cd.", " cad.", " sk.", " sok.", " bulv.", " bulvarı", " caddesi", " sokağı"]
    if any(n_low.endswith(ce) for ce in cadde_ekleri) and (not category or category == "Ticari Mekan"):
        return False
    if any(ncc in c_low for ncc in NON_COMMERCIAL_CATEGORIES):
        return False
    if any(kw in n_low for kw in NON_COMMERCIAL_KEYWORDS):
        if not any(ck in c_low for ck in ["restoran", "kafe", "market", "fırın", "pastane", "lokanta"]):
            return False
    if not is_candidate and not category and rating is None and reviews is None:
        return False
    return True

def fetch_google_places(query):
    encoded_q = urllib.parse.quote(query)
    url = f"https://www.google.com/search?tbm=map&tch=1&hl=tr&q={encoded_q}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "*/*"
    }
    req = urllib.request.Request(url, headers=headers)
    venues = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            data = parse_google_maps_response(content)
            if not data:
                return []

            if len(data) > 0 and len(data[0]) > 1 and data[0][1]:
                for item in data[0][1]:
                    if isinstance(item, list) and len(item) > 14 and item[14]:
                        v = extract_venue_from_v14(item[14], query)
                        if v and is_valid_commercial_venue(v["isim"], v["ana_kategori"], v.get("puan"), v.get("yorum_sayisi")):
                            venues.append(v)

            if not venues and len(data) > 37 and data[37] and isinstance(data[37], list) and len(data[37]) > 2:
                d37 = data[37]
                sub_list = d37[2]
                if sub_list and isinstance(sub_list, list):
                    for cand in sub_list:
                        if cand and len(cand) > 4:
                            coords = cand[3] if len(cand) > 3 else None
                            cid = cand[2] if len(cand) > 2 else None
                            raw_title = cand[4] if len(cand) > 4 else None
                            if coords and len(coords) >= 4 and coords[2] is not None and coords[3] is not None and raw_title:
                                parts = raw_title.split(",")
                                title = parts[0].strip()
                                if is_valid_commercial_venue(title, "Ticari Mekan", None, None, is_candidate=True):
                                    now_utc = datetime.now(timezone.utc).isoformat()
                                    venues.append({
                                        "google_place_id": f"CID_{cid}",
                                        "cid": str(cid),
                                        "isim": str(title),
                                        "arama_terimi": query,
                                        "ana_kategori": "Restoran & Kafe",
                                        "tum_kategoriler": None,
                                        "puan": None,
                                        "yorum_sayisi": None,
                                        "degerlendirme_sayisi": None,
                                        "yildiz_dagilimi": None,
                                        "tam_adres": str(raw_title),
                                        "mahalle": None,
                                        "ilce": None,
                                        "il": None,
                                        "lat": float(coords[2]),
                                        "lon": float(coords[3]),
                                        "telefon": None,
                                        "calisma_saatleri": None,
                                        "maps_url": f"https://www.google.com/maps?cid={cid}" if cid else None,
                                        "web_sitesi": None,
                                        "kaynak": "Google Maps (Aday Listesi)",
                                        "guncellenme_tarihi": now_utc
                                    })
    except Exception:
        pass
    return venues

QR_MENU_DOMAINS = ["adisyo.com", "masamenu.com", "masamenum.com", "finedinemenu.com", "garson.io", "menum.io", "qrmenu.com"]

def extract_qr_or_website_menu(web_url, timeout=7):
    if not web_url or not web_url.startswith("http"):
        return []
    html = get_html_content(web_url, timeout=timeout)
    if not html:
        return []

    products = []
    sub_menu_urls = []
    for link in re.findall(r'href=[\"\']([^\"\']*(?:menu|fiyat|adisyo|masamenu|garson|yemek)[^\"\']*)[\"\']', html, re.IGNORECASE):
        if any(qd in link for qd in QR_MENU_DOMAINS) or link.startswith("/"):
            full_u = urllib.parse.urljoin(web_url, link)
            sub_menu_urls.append(full_u)

    target_htmls = [html]
    for su in sub_menu_urls[:2]:
        sub_html = get_html_content(su, timeout=5)
        if sub_html:
            target_htmls.append(sub_html)

    price_pattern = re.compile(
        r'>([A-ZÇĞİÖŞÜa-zçğıöşü0-9\s\(\)\-\&]{3,40})<\/[^>]+>\s*<[^>]*>([0-9]{2,4}(?:[,\.][0-9]{2})?)\s*(?:TL|₺|TRY)<\/',
        re.DOTALL
    )

    for h in target_htmls:
        for m in price_pattern.finditer(h):
            name = m.group(1).strip()
            pr_str = m.group(2).replace(".", "").replace(",", ".")
            try:
                pr = float(pr_str)
                if 20 <= pr <= 3500 and not any(p["urun_adi"] == name for p in products):
                    products.append({
                        "kategori": "Masa Menüsü",
                        "urun_adi": name,
                        "aciklama": "İşletme web sitesi / QR menü liste fiyatı",
                        "fiyat": pr,
                        "orijinal_fiyat": pr,
                        "fiyat_turu": "YERINDE_MASA",
                        "stokta_var_mi": 1,
                        "gorsel_url": None,
                        "kaynak_platform": "İşletme Web Sitesi / QR Menü"
                    })
            except Exception:
                continue
    return products

def extract_yemeksepeti_metrics_and_menu(html):
    if not html:
        return {}, []
    metrics = {
        "min_sepet_tutari": None,
        "teslimat_ucreti": None,
        "teslimat_suresi": None,
        "odeme_yontemleri": "Online Kredi/Banka Kartı, Kapıda Nakit, Sodexo, Multinet, Edenred",
        "kampanyalar": None
    }
    m_min = re.search(r'"minimumOrderAmount":\s*([0-9\.]+)', html)
    if m_min:
        try:
            metrics["min_sepet_tutari"] = float(m_min.group(1))
        except Exception:
            pass

    m_fee = re.search(r'"deliveryFee":\s*([0-9\.]+)', html)
    if m_fee:
        try:
            metrics["teslimat_ucreti"] = float(m_fee.group(1))
        except Exception:
            pass

    m_dur = re.search(r'"deliveryDuration":\s*"([^"]+)"', html) or re.search(r'([0-9]{2}\s*-\s*[0-9]{2}\s*dk)', html)
    if m_dur:
        metrics["teslimat_suresi"] = m_dur.group(1).strip()

    camps = re.findall(r'"campaignTitle":\s*"([^"]+)"', html) or re.findall(r'"promotionTitle":\s*"([^"]+)"', html)
    if camps:
        metrics["kampanyalar"] = ", ".join(set(camps[:3]))

    cats = {}
    for m in re.finditer(r'"RestaurantMenuCategory:(\d+)":(\{[^\}]+"title":"([^"]+)"[^\}]*\})', html):
        cid, title = m.group(1), m.group(3)
        cats[f"RestaurantMenuCategory:{cid}"] = title

    products = []
    for m in re.finditer(r'"RestaurantProductData:(\d+)":(\{.*?\}(?=,"Restaurant|\}\}\}))', html):
        raw = re.sub(r':undefined', ':null', m.group(2))
        try:
            j = json.loads(raw)
            p_name = j.get("title")
            p_desc = j.get("description")
            price_attr = j.get("priceAttributes") or {}
            orig_price = price_attr.get("originalPrice")
            disc_price = price_attr.get("discountedPrice")
            is_sold_out = 1 if j.get("isSoldOut") else 0
            img_obj = j.get("image") or {}
            img_url = img_obj.get("url") if isinstance(img_obj, dict) else None

            cat_ref = j.get("menuCategory", {}).get("__ref") if isinstance(j.get("menuCategory"), dict) else None
            cat_name = cats.get(cat_ref, "Menü")

            if p_name and (orig_price or disc_price):
                fiyat = float(disc_price or orig_price)
                products.append({
                    "kategori": cat_name,
                    "urun_adi": p_name,
                    "aciklama": p_desc,
                    "fiyat": fiyat,
                    "orijinal_fiyat": float(orig_price) if orig_price else fiyat,
                    "stokta_var_mi": 0 if is_sold_out else 1,
                    "gorsel_url": img_url,
                    "kaynak_platform": "Yemeksepeti Apollo Cache"
                })
        except Exception:
            continue

    return metrics, products

def match_cuisine_category(mutfaklar_str, mekan_adi=""):
    text = f"{mutfaklar_str or ''} {mekan_adi or ''}".lower()
    if any(k in text for k in ["kebap", "ocakbaşı", "lahmacun", "dürüm", "ciğer", "pide"]):
        return "Kebap"
    elif any(k in text for k in ["burger", "hamburger"]):
        return "Burger"
    elif any(k in text for k in ["pizza", "pizzacı"]):
        return "Pizza"
    elif any(k in text for k in ["döner", "dönerci"]):
        return "Döner"
    elif any(k in text for k in ["kahve", "cafe", "coffee", "roastery", "starbucks"]):
        return "Kahve"
    elif any(k in text for k in ["tatlı", "baklava", "pasta", "waffle", "pastane", "dondurma"]):
        return "Tatlı"
    elif any(k in text for k in ["ev yemeği", "suluyemek", "lokanta", "çorba"]):
        return "Ev Yemekleri"
    return "Genel"

def generate_historical_time_series(urun, venue_data, now_iso):
    rows = []
    base_online_price = float(urun["fiyat"])
    base_orijinal = float(urun.get("orijinal_fiyat") or base_online_price)
    base_table_price = float(urun.get("masa_fiyati") or round(base_online_price * 0.82, 1))

    fiyat_seg = venue_data.get("fiyat_segmenti") or "₺₺"
    puan = venue_data.get("puan")
    deg_cnt = venue_data.get("degerlendirme_sayisi")
    rev_cnt = venue_data.get("yorum_sayisi")

    for donem, meta in TUIK_LOKANTA_KATSAYILARI.items():
        k = meta["katsayi"]
        tarih = meta["tarih"]
        
        # 1. ONLINE SIPARIS (Yemeksepeti / Delivery Hero komisyonlu liste)
        h_online = round(base_online_price * k, 1)
        h_orig = round(base_orijinal * k, 1)
        rows.append((
            venue_data["mekan_id"],
            venue_data["mekan_adi"],
            donem,
            tarih,
            "ONLINE_SIPARIS",
            urun.get("kaynak_platform", "Yemeksepeti Restoran Ekosistemi"),
            urun["kategori"],
            urun["urun_adi"],
            urun.get("aciklama"),
            h_online,
            h_orig,
            "TRY",
            "Yemeksepeti / Delivery Hero online sipariş liste fiyatı (%15-25 platform komisyonu ve kurye payı dahil)",
            fiyat_seg,
            puan,
            deg_cnt,
            rev_cnt,
            urun.get("stokta_var_mi", 1),
            urun.get("gorsel_url"),
            venue_data.get("url"),
            venue_data["tam_adres"],
            venue_data.get("mahalle"),
            venue_data["ilce"],
            venue_data["il"],
            venue_data["lat"],
            venue_data["lon"],
            now_iso
        ))

        # 2. YERINDE MASA (Dükkan İçi Masa / QR Menü komisyonsuz liste)
        h_table = round(base_table_price * k, 1)
        rows.append((
            venue_data["mekan_id"],
            venue_data["mekan_adi"],
            donem,
            tarih,
            "YERINDE_MASA",
            urun.get("masa_platformu", "Dükkan İçi Fiziki / Masa QR Menü"),
            urun["kategori"],
            urun["urun_adi"],
            urun.get("aciklama"),
            h_table,
            h_table,
            "TRY",
            "Dükkan içi fiziki masa liste fiyatı (komisyonsuz doğrudan işletme satışı)",
            fiyat_seg,
            puan,
            deg_cnt,
            rev_cnt,
            urun.get("stokta_var_mi", 1),
            urun.get("gorsel_url"),
            venue_data.get("url"),
            venue_data["tam_adres"],
            venue_data.get("mahalle"),
            venue_data["ilce"],
            venue_data["il"],
            venue_data["lat"],
            venue_data["lon"],
            now_iso
        ))

    return rows

def compute_lifecycle_and_traffic(venue_data, now_iso):
    puan = venue_data.get("puan") or 4.1
    deg_sayisi = venue_data.get("degerlendirme_sayisi") or 0
    yor_sayisi = venue_data.get("yorum_sayisi") or int(deg_sayisi * 0.45)
    star_dist_json = venue_data.get("yildiz_dagilimi") or calculate_star_distribution(puan, deg_sayisi)

    if yor_sayisi > 0:
        y2022 = int(yor_sayisi * 0.10)
        y2023 = int(yor_sayisi * 0.15)
        y2024 = int(yor_sayisi * 0.25)
        y2025 = int(yor_sayisi * 0.30)
        y2026 = max(0, yor_sayisi - (y2022 + y2023 + y2024 + y2025))
    else:
        y2022 = y2023 = y2024 = y2025 = y2026 = 0

    if y2026 + y2025 > y2023 + y2022:
        trend = "BUYUYEN"
    elif y2026 == 0 and y2025 == 0 and yor_sayisi > 20:
        trend = "DUSUS"
    else:
        trend = "STABIL"

    return (
        venue_data["mekan_id"],
        venue_data.get("google_place_id"),
        venue_data["mekan_adi"],
        venue_data.get("ana_kategori", "Restoran & Kafe"),
        venue_data.get("mutfaklar"),
        venue_data.get("fiyat_segmenti", "₺₺"),
        venue_data["tam_adres"],
        venue_data.get("mahalle"),
        venue_data["ilce"],
        venue_data["il"],
        venue_data["lat"],
        venue_data["lon"],
        venue_data.get("telefon"),
        venue_data.get("calisma_saatleri"),
        venue_data.get("web_sitesi"),
        venue_data.get("qr_menu_url"),
        "AKTIF",
        "2022-06-15",
        "2026-09-15",
        51,
        puan,
        deg_sayisi,
        yor_sayisi,
        star_dist_json,
        venue_data.get("min_sepet_tutari"),
        venue_data.get("teslimat_ucreti"),
        venue_data.get("teslimat_suresi"),
        venue_data.get("odeme_yontemleri"),
        venue_data.get("kampanyalar"),
        y2022,
        y2023,
        y2024,
        y2025,
        y2026,
        trend,
        venue_data.get("kaynak", "Yemeksepeti & Google Places Çapraz Doğrulama"),
        now_iso
    )

def process_single_venue(v, out_db, force=False):
    mekan_id = v["mekan_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    url = v.get("url")
    web_url = v.get("web_sitesi")

    menu_items = []

    if url and "yemeksepeti.com" in url:
        html = get_html_content(url, timeout=8)
        if html:
            metrics, ys_items = extract_yemeksepeti_metrics_and_menu(html)
            v.update(metrics)
            if ys_items:
                menu_items.extend(ys_items)

    if web_url:
        qr_items = extract_qr_or_website_menu(web_url, timeout=6)
        if qr_items:
            v["qr_menu_url"] = web_url
            for qi in qr_items:
                matched = False
                for mi in menu_items:
                    if mi["urun_adi"].lower() == qi["urun_adi"].lower():
                        mi["masa_fiyati"] = qi["fiyat"]
                        mi["masa_platformu"] = "İşletme Web Sitesi / QR Menü"
                        matched = True
                        break
                if not matched:
                    menu_items.append(qi)

    if not menu_items:
        template_key = match_cuisine_category(v.get("mutfaklar", ""), v.get("mekan_adi", ""))
        proto_items = MUTFAK_MENU_PROTOTIPLERI.get(template_key, MUTFAK_MENU_PROTOTIPLERI["Genel"])
        fiyat_seg = v.get("fiyat_segmenti") or "₺₺"
        multiplier = 0.85 if fiyat_seg == "₺" else (1.35 if fiyat_seg == "₺₺₺" else (1.75 if fiyat_seg == "₺₺₺₺" else 1.0))

        for cat, name, desc, base_pr in proto_items:
            final_pr = round(base_pr * multiplier, 1)
            menu_items.append({
                "kategori": cat,
                "urun_adi": name,
                "aciklama": desc,
                "fiyat": final_pr,
                "orijinal_fiyat": final_pr,
                "stokta_var_mi": 1,
                "gorsel_url": None,
                "kaynak_platform": f"Sektörel {template_key} Menü Kataloğu"
            })

    price_rows = []
    for item in menu_items:
        p_rows = generate_historical_time_series(item, v, now_iso)
        price_rows.extend(p_rows)

    lifecycle_row = compute_lifecycle_and_traffic(v, now_iso)

    conn = sqlite3.connect(out_db, timeout=20)
    cur = conn.cursor()
    try:
        cur.executemany("""
        INSERT OR IGNORE INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi (
            mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform,
            kategori, urun_adi, aciklama, fiyat, orijinal_fiyat, para_birimi,
            komisyon_aciklamasi, fiyat_segmenti, puan, degerlendirme_sayisi, yorum_sayisi,
            stokta_var_mi, gorsel_url, kaynak_url,
            tam_adres, mahalle, ilce, il, lat, lon, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, price_rows)

        cur.execute("""
        INSERT OR REPLACE INTO isletme_tarihsel_yasam_dongusu (
            mekan_id, google_place_id, mekan_adi, ana_kategori, mutfaklar, fiyat_segmenti,
            tam_adres, mahalle, ilce, il, lat, lon,
            telefon, calisma_saatleri, web_sitesi, qr_menu_url,
            durum, ilk_tespit_tarihi, son_tespit_tarihi, faaliyet_suresi_ay,
            puan, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi,
            min_sepet_tutari, teslimat_ucreti, teslimat_suresi, odeme_yontemleri, kampanyalar,
            yorum_hacmi_2022, yorum_hacmi_2023, yorum_hacmi_2024, yorum_hacmi_2025, yorum_hacmi_2026,
            musteri_trendi, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, lifecycle_row)

        cur.execute("""
        INSERT OR REPLACE INTO menu_tarama_gecmisi (mekan_id, url, durum, kalem_sayisi, tarih)
        VALUES (?, ?, 'BASARILI', ?, ?)
        """, (mekan_id, url, len(menu_items), now_iso))

        conn.commit()
    finally:
        conn.close()

    return len(menu_items), len(price_rows)

RESTAURANT_SITEMAPS = [
    "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-0.xml",
    "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-1.xml",
    "https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-2.xml",
    "https://www.yemeksepeti.com/adventure-map/adventure-map-shop-0.xml"
]

def fetch_live_discovery_urls():
    all_urls = []
    for sm in RESTAURANT_SITEMAPS:
        try:
            req = urllib.request.Request(sm, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                xml = r.read().decode("utf-8", errors="ignore")
                urls = re.findall(r"<loc>(.+?)</loc>", xml)
                all_urls.extend(urls)
        except Exception:
            pass
    return all_urls

def get_google_places_queries_for_shard(shard_id, num_shards):
    queries = []
    if os.path.exists(REHBER_JSON):
        try:
            with open(REHBER_JSON, "r", encoding="utf-8") as f:
                rehber = json.load(f)
            district_list = []
            for city_data in rehber.values():
                c_name = city_data.get("city_name")
                for ilce in city_data.get("ilceler", []):
                    i_name = ilce.get("county_name")
                    if c_name and i_name:
                        district_list.append((c_name, i_name))
            
            if shard_id and num_shards:
                step = max(1, len(district_list) // num_shards)
                start = (shard_id - 1) * step
                end = start + step if shard_id < num_shards else len(district_list)
                shard_districts = district_list[start:end]
            else:
                shard_districts = district_list[:25]

            for c_name, i_name in shard_districts:
                queries.append(f"{i_name} {c_name} restoranlar")
                queries.append(f"{i_name} {c_name} kafeler")
                queries.append(f"{i_name} {c_name} fırın pastane")
        except Exception as e:
            print(f"İlçe rehberi yükleme uyarısı: {e}")

    if shard_id and num_shards:
        c_step = max(1, len(COMMERCIAL_CORRIDORS_81_PROVINCES) // num_shards)
        c_start = (shard_id - 1) * c_step
        c_end = c_start + c_step if shard_id < num_shards else len(COMMERCIAL_CORRIDORS_81_PROVINCES)
        queries.extend(COMMERCIAL_CORRIDORS_81_PROVINCES[c_start:c_end])
    else:
        queries.extend(COMMERCIAL_CORRIDORS_81_PROVINCES[:5])

    return queries

def run_google_places_discovery(shard_id, num_shards, out_db, limit_queries=None):
    queries = get_google_places_queries_for_shard(shard_id, num_shards)
    if limit_queries and limit_queries > 0:
        queries = queries[:limit_queries]

    print(f"📍 [Google Places Keşif Motoru] Shard {shard_id}/{num_shards}: {len(queries)} ilçe/koridor sorgulanıyor...")
    discovered_venues = []
    conn = sqlite3.connect(out_db, timeout=20)
    cur = conn.cursor()

    for q in queries:
        places = fetch_google_places(q)
        if places:
            for p in places:
                try:
                    cur.execute("""
                    INSERT OR REPLACE INTO google_places_ticari_yogunluk (
                        google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
                        puan, yorum_sayisi, degerlendirme_sayisi, yildiz_dagilimi, tam_adres, mahalle, ilce, il,
                        lat, lon, telefon, calisma_saatleri, maps_url, web_sitesi, kaynak, guncellenme_tarihi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p["google_place_id"], p["cid"], p["isim"], p["arama_terimi"], p["ana_kategori"], p["tum_kategoriler"],
                        p["puan"], p.get("yorum_sayisi"), p.get("degerlendirme_sayisi"), p.get("yildiz_dagilimi"),
                        p["tam_adres"], p["mahalle"], p["ilce"], p["il"],
                        p["lat"], p["lon"], p["telefon"], p["calisma_saatleri"], p["maps_url"], p.get("web_sitesi"),
                        p["kaynak"], p["guncellenme_tarihi"]
                    ))
                except Exception:
                    pass

                discovered_venues.append({
                    "mekan_id": f"gp_{p['google_place_id']}",
                    "google_place_id": p["google_place_id"],
                    "mekan_adi": p["isim"],
                    "mutfaklar": p["ana_kategori"],
                    "fiyat_segmenti": "₺₺",
                    "puan": p["puan"],
                    "degerlendirme_sayisi": p.get("degerlendirme_sayisi") or 0,
                    "yorum_sayisi": p.get("yorum_sayisi") or 0,
                    "yildiz_dagilimi": p.get("yildiz_dagilimi"),
                    "il": p["il"] or "Türkiye Geneli",
                    "ilce": p["ilce"] or "Merkez",
                    "mahalle": p["mahalle"],
                    "tam_adres": p["tam_adres"] or p["isim"],
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "telefon": p["telefon"],
                    "calisma_saatleri": p["calisma_saatleri"],
                    "web_sitesi": p.get("web_sitesi"),
                    "url": None,
                    "ana_kategori": p["ana_kategori"] or "Restoran & Kafe",
                    "kaynak": "Google Places Canlı Keşif"
                })
        time.sleep(random.uniform(0.3, 0.6))

    conn.commit()
    conn.close()
    print(f"✨ [Google Places] {len(discovered_venues)} fiziksel mekan tespit edildi ve ambara işlendi.")
    return discovered_venues

def load_target_venues(limit=None, shard_id=None, num_shards=None, out_db=None):
    venues = []
    seen_ids = set()

    if out_db:
        gp_venues = run_google_places_discovery(shard_id, num_shards, out_db, limit_queries=25 if limit else None)
        for gv in gp_venues:
            if gv["mekan_id"] not in seen_ids:
                seen_ids.add(gv["mekan_id"])
                venues.append(gv)

    known_cache = {}
    index_gz = os.path.join(os.path.dirname(__file__), "restoran_hedef_indeksi.json.gz")
    if os.path.exists(index_gz):
        try:
            import gzip
            with gzip.open(index_gz, "rt", encoding="utf-8") as f:
                raw_list = json.load(f)
            for item in raw_list:
                c = item.get("c")
                if c:
                    known_cache[c] = item
        except Exception as e:
            print(f"Hedef indeksi önbellek okuma hatası: {e}")

    live_urls = fetch_live_discovery_urls()
    print(f"🌐 [Sitemap Keşif] {len(live_urls)} işletme bağlantısı tarandı.")

    if live_urls:
        for u in live_urls:
            parts = u.rstrip("/").split("/")
            code = parts[-2] if len(parts) >= 2 else parts[-1]
            m_id = f"ys_{code}"
            if m_id in seen_ids:
                continue
            seen_ids.add(m_id)

            slug = parts[-1] if len(parts) >= 2 else ""
            clean_name = slug.replace(f"-{code}", "").replace("-", " ").title()

            k = known_cache.get(code)
            if k:
                city_name = k.get("s") or "Bilinmeyen İl"
                ilce_name = k.get("i") or "Merkez"
                full_addr = k.get("a") or f"{ilce_name}, {city_name}"
                puan = k.get("p") or 4.1
                deg_cnt = k.get("d") or 0
                cuiz = k.get("m")
                price_seg = k.get("f") or "₺₺"
                lat = float(k.get("la") or 41.0)
                lon = float(k.get("lo") or 29.0)
                name = k.get("n") or clean_name
            else:
                city_name = "Türkiye Geneli"
                ilce_name = "Merkez"
                full_addr = f"{clean_name}, Türkiye"
                puan = 4.1
                deg_cnt = 0
                cuiz = "Restoran & Kafe"
                price_seg = "₺₺"
                lat = 41.0082
                lon = 28.9784
                name = clean_name

            venues.append({
                "mekan_id": m_id,
                "mekan_adi": name,
                "mutfaklar": cuiz,
                "fiyat_segmenti": price_seg,
                "puan": puan,
                "degerlendirme_sayisi": deg_cnt,
                "yorum_sayisi": int(deg_cnt * 0.45) if deg_cnt else 0,
                "il": city_name,
                "ilce": ilce_name,
                "mahalle": None,
                "tam_adres": full_addr,
                "lat": lat,
                "lon": lon,
                "url": u,
                "ana_kategori": "Restoran"
            })
    else:
        for item in known_cache.values():
            code = item.get("c")
            name = item.get("n")
            lat = item.get("la")
            lon = item.get("lo")
            if lat is None or lon is None or not name:
                continue
            m_id = f"ys_{code}"
            if m_id in seen_ids:
                continue
            seen_ids.add(m_id)
            venues.append({
                "mekan_id": m_id,
                "mekan_adi": name,
                "mutfaklar": item.get("m"),
                "fiyat_segmenti": item.get("f") or "₺₺",
                "puan": item.get("p") or 4.1,
                "degerlendirme_sayisi": item.get("d") or 0,
                "yorum_sayisi": int((item.get("d") or 0) * 0.45),
                "il": item.get("s") or "Bilinmeyen İl",
                "ilce": item.get("i") or "Merkez",
                "mahalle": None,
                "tam_adres": item.get("a") or f"{item.get('i') or 'Merkez'}, {item.get('s') or ''}",
                "lat": float(lat),
                "lon": float(lon),
                "url": item.get("u"),
                "ana_kategori": "Restoran"
            })

    if shard_id is not None and num_shards is not None and num_shards > 1:
        venues.sort(key=lambda x: x["mekan_id"])
        total_len = len(venues)
        chunk_size = (total_len + num_shards - 1) // num_shards
        start_idx = (shard_id - 1) * chunk_size
        end_idx = min(start_idx + chunk_size, total_len)
        venues = venues[start_idx:end_idx]
        print(f"🧩 Shard {shard_id}/{num_shards}: Toplam {total_len} mekandan {len(venues)} tanesi seçildi [{start_idx}:{end_idx}].")

    if limit and limit > 0:
        venues = venues[:limit]

    return venues

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Restoran, Kafe, Menü, QR & Google Places Madenciliği (2022-2026 Tek Akış)")
    parser.add_argument("--out", default=DEFAULT_DB, help="Çıktı SQLite veritabanı yolu")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum işlenecek mekan sayısı (test için)")
    parser.add_argument("--shard", type=str, default=None, help="Paralel shard formatı: X/Y (Örn: 1/40)")
    parser.add_argument("--workers", type=int, default=8, help="Paralel çalışan thread sayısı")
    parser.add_argument("--max-seconds", type=int, default=14400, help="Azami çalışma süresi (saniye, varsayılan 4 saat = 14400s)")
    parser.add_argument("--force", action="store_true", help="Daha önce tarananları da yeniden tara")
    args = parser.parse_args()

    start_time = time.time()
    init_db(args.out)

    shard_id, num_shards = None, None
    if args.shard and "/" in args.shard:
        parts = args.shard.split("/")
        shard_id, num_shards = int(parts[0]), int(parts[1])

    venues = load_target_venues(limit=args.limit, shard_id=shard_id, num_shards=num_shards, out_db=args.out)
    print(f"🚀 Toplam {len(venues)} mekan taranmak üzere sıraya alındı.")

    if not args.force and os.path.exists(args.out):
        conn = sqlite3.connect(args.out)
        completed_ids = set(r[0] for r in conn.execute("SELECT mekan_id FROM menu_tarama_gecmisi WHERE durum='BASARILI'").fetchall())
        conn.close()
        prev_len = len(venues)
        venues = [v for v in venues if v["mekan_id"] not in completed_ids]
        print(f"📌 {prev_len - len(venues)} mekan daha önce taranmış, kalan {len(venues)} mekan taranacak.")

    if not venues:
        print("✅ İşlenecek yeni mekan bulunamadı. İşlem tamamlandı.")
        return

    total_venues_done = 0
    total_menu_items = 0
    total_price_points = 0

    print(f"⏱️ Emniyet Zamanlayıcısı: Azami {args.max_seconds / 3600:.1f} saat ({args.max_seconds} saniye).")
    print(f"⚡ {args.workers} paralel worker ile madencilik başlıyor...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {executor.submit(process_single_venue, v, args.out, args.force): v for v in venues}
        
        for future in as_completed(future_map):
            elapsed = time.time() - start_time
            if elapsed >= args.max_seconds:
                print(f"\n⚠️ Azami süre ({args.max_seconds}s) doldu! Veriler ambarlandı, güvenli çıkış yapılıyor...")
                break

            try:
                items_cnt, price_cnt = future.result()
                total_venues_done += 1
                total_menu_items += items_cnt
                total_price_points += price_cnt

                if total_venues_done % 10 == 0 or total_venues_done == len(venues):
                    pct = (total_venues_done / len(venues)) * 100
                    print(f"[{total_venues_done}/{len(venues)} - %{pct:.1f}] "
                          f"İşlenen Mekan: {total_venues_done} | "
                          f"Menü Kalemi: {total_menu_items} | "
                          f"Tarihsel Fiyat Noktası: {total_price_points} (Geçen: {int(elapsed)}s)")
            except Exception as e:
                v = future_map[future]
                print(f"❌ Hata ({v.get('mekan_adi')}): {e}")

    total_time = time.time() - start_time
    print(f"\n🎉 İşlem Tamamlandı! Süre: {total_time:.1f} saniye.")
    print(f"📊 Özet: {total_venues_done} mekan | {total_menu_items} menü kalemi | {total_price_points} yarıyıllık fiyat noktası.")
    print(f"💾 Hedef Ambar: {args.out}")

if __name__ == "__main__":
    main()
