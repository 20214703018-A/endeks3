#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Türkiye Makro & Mikro Ticari Lokasyon MODELİ
========================================================================
Bu betik sabit örnekler ve deterministik katsayılarla demo/model veri üretir.
Üretilen değerler ETBİS, SEGE, TÜİK veya başka bir kurumun doğrulanmış güncel
verisi değildir ve karar verisi olarak kullanılmamalıdır.
"""

import os
import sys
import json
import time
import base64
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

try:
    from curl_cffi import requests
except ImportError:
    import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "turkiye_makro_ve_mikro_istihbarat.sqlite"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

REHBER_FILE = BASE_DIR / "turkiye_il_ilce_rehberi.json"

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "[*]", "SUCCESS": "[✓]", "WARN": "[!]", "ERROR": "[✗]"}
    print(f"{ts} {symbols.get(level, '[*]')} {msg}", flush=True)

def init_master_db(db_path=None):
    target_db = db_path or DB_PATH
    conn = sqlite3.connect(target_db)
    c = conn.cursor()

    # 1. ETBİS benzeri il e-ticaret model tablosu
    c.execute("""
    CREATE TABLE IF NOT EXISTS etbis_81_il_e_ticaret (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plaka INTEGER UNIQUE,
        il_adi TEXT UNIQUE,
        bolge TEXT,
        yillik_e_ticaret_hacmi_milyar_tl REAL,
        kisi_basi_e_ticaret_harcamasi_tl REAL,
        e_ticaret_uyum_endeksi_skoru REAL,
        uyum_siralamasi INTEGER,
        tescilli_e_ticaret_isletme_sayisi INTEGER,
        genel_ticaret_icindeki_pay_yuzde REAL,
        alis_satis_karsilama_orani REAL,
        veri_donemi TEXT DEFAULT 'MODEL-2026-Q3',
        guncellenme_yili INTEGER DEFAULT 2026,
        tahmin_ufku TEXT DEFAULT 'MODEL-2026-2027'
    )
    """)
    # Kolon göçü (Migration)
    c.execute("PRAGMA table_info(etbis_81_il_e_ticaret)")
    existing_cols = [r[1] for r in c.fetchall()]
    if "veri_donemi" not in existing_cols:
        c.execute("ALTER TABLE etbis_81_il_e_ticaret ADD COLUMN veri_donemi TEXT DEFAULT 'MODEL-2026-Q3'")
    if "tahmin_ufku" not in existing_cols:
        c.execute("ALTER TABLE etbis_81_il_e_ticaret ADD COLUMN tahmin_ufku TEXT DEFAULT 'MODEL-2026-2027'")

    # 2. SEGE benzeri ilçe sosyo-ekonomik model tablosu
    c.execute("""
    CREATE TABLE IF NOT EXISTS sege_973_ilce_gelismislik (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il_plaka INTEGER,
        il_adi TEXT,
        ilce_adi TEXT,
        sege_siralamasi INTEGER,
        sege_skoru REAL,
        gelismislik_kademesi INTEGER, -- 1 (En Zengin/Gelişmiş) - 6 (En Düşük)
        kademe_tanimi TEXT,
        sosyo_ekonomik_sinif TEXT, -- 'A+', 'A', 'B+', 'B', 'C', 'D'
        veri_donemi TEXT DEFAULT 'MODEL-2026',
        guncellenme_yili INTEGER DEFAULT 2026,
        tahmin_ufku TEXT DEFAULT '2026-2027',
        UNIQUE(il_adi, ilce_adi)
    )
    """)
    c.execute("PRAGMA table_info(sege_973_ilce_gelismislik)")
    existing_sege = [r[1] for r in c.fetchall()]
    if "veri_donemi" not in existing_sege:
        c.execute("ALTER TABLE sege_973_ilce_gelismislik ADD COLUMN veri_donemi TEXT DEFAULT 'MODEL-2026'")
    if "guncellenme_yili" not in existing_sege:
        c.execute("ALTER TABLE sege_973_ilce_gelismislik ADD COLUMN guncellenme_yili INTEGER DEFAULT 2026")
    if "tahmin_ufku" not in existing_sege:
        c.execute("ALTER TABLE sege_973_ilce_gelismislik ADD COLUMN tahmin_ufku TEXT DEFAULT '2026-2027'")

    # 5. TÜİK Sağlık Araştırması Tütün & Sigara Tüketim Endeksi (2026)
    c.execute("""
    CREATE TABLE IF NOT EXISTS tuik_tutun_ve_sigara_istatistikleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bolge_adi TEXT UNIQUE,
        erkek_gunluk_sigara_orani REAL,
        kadin_gunluk_sigara_orani REAL,
        toplam_sigara_orani REAL,
        tuketim_seviyesi TEXT,
        aciklama TEXT,
        veri_yili INTEGER DEFAULT 2026,
        veri_donemi TEXT DEFAULT 'MODEL-2026'
    )
    """)
    c.execute("PRAGMA table_info(tuik_tutun_ve_sigara_istatistikleri)")
    existing_tutun = [r[1] for r in c.fetchall()]
    if "veri_yili" not in existing_tutun:
        c.execute("ALTER TABLE tuik_tutun_ve_sigara_istatistikleri ADD COLUMN veri_yili INTEGER DEFAULT 2026")
    if "veri_donemi" not in existing_tutun:
        c.execute("ALTER TABLE tuik_tutun_ve_sigara_istatistikleri ADD COLUMN veri_donemi TEXT DEFAULT 'MODEL-2026'")

    # 6. Maptriks / 2026-2027 Ciro Potansiyeli ve Lokasyon Çekicilik Skoru
    c.execute("""
    CREATE TABLE IF NOT EXISTS ciro_ve_ticari_potansiyel_endeksi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seviye TEXT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        ciro_potansiyeli_skoru REAL, -- 0 - 100 (2026-2027)
        yeme_icme_kafe_skoru REAL,   -- 0 - 100
        market_perakende_skoru REAL, -- 0 - 100
        e_ticaret_teslimat_skoru REAL, -- 0 - 100
        sosyo_ekonomik_derece TEXT, -- 'A+', 'A', 'B+', 'B', 'C', 'D'
        degerlendirme_ozeti TEXT,
        projeksiyon_donemi TEXT DEFAULT 'MODEL-2026-2027',
        hesaplanma_yili INTEGER DEFAULT 2026,
        UNIQUE(seviye, il, ilce, mahalle)
    )
    """)
    c.execute("PRAGMA table_info(ciro_ve_ticari_potansiyel_endeksi)")
    existing_ciro = [r[1] for r in c.fetchall()]
    if "projeksiyon_donemi" not in existing_ciro:
        c.execute("ALTER TABLE ciro_ve_ticari_potansiyel_endeksi ADD COLUMN projeksiyon_donemi TEXT DEFAULT 'MODEL-2026-2027'")
    if "hesaplanma_yili" not in existing_ciro:
        c.execute("ALTER TABLE ciro_ve_ticari_potansiyel_endeksi ADD COLUMN hesaplanma_yili INTEGER DEFAULT 2026")

    # 3. Kargo Şubeleri ve 7/24 Kargomatlar (PTT Canlı CBS)
    c.execute("""
    CREATE TABLE IF NOT EXISTS kargo_ve_teslimat_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tip TEXT, -- 'PTT_SUBE' veya 'PTT_KARGOMAT'
        kod TEXT,
        ad TEXT,
        adres TEXT,
        telefon TEXT,
        il_kod INTEGER,
        il_ad TEXT,
        ilce_kod INTEGER,
        ilce_ad TEXT,
        mahalle_ad TEXT,
        lat REAL,
        lon REAL,
        hafta_ici TEXT,
        cumartesi TEXT,
        pazar TEXT,
        aktiflik INTEGER DEFAULT 1,
        guncellenme_yili INTEGER DEFAULT 2026,
        UNIQUE(tip, ad, adres)
    )
    """)

    # 4. İl, İlçe ve Mahalle E-Ticaret ve Harcama Kalemleri (2026 Enflasyon ve Alım Gücü Endeksli)
    c.execute("""
    CREATE TABLE IF NOT EXISTS mahalle_tuketim_ve_harcama (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seviye TEXT,
        city_id INTEGER,
        county_id INTEGER,
        district_id INTEGER,
        bolge_adi TEXT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        e_ticaret_kullanici_sayisi INTEGER,
        e_ticaret_yogunluk INTEGER,
        online_pazaryeri_tl REAL,
        online_tatil_seyahat_tl REAL,
        online_yasal_bahis_tl REAL,
        online_elektronik_tl REAL,
        online_giyim_ayakkabi_tl REAL,
        ev_dekorasyon_tl REAL,
        online_eglence_kultur_tl REAL,
        aylik_gida_harcamasi REAL,
        aylik_barinma_kira_harcamasi REAL,
        aylik_ulasim_harcamasi REAL,
        aylik_restoran_yeme_icme REAL,
        aylik_giyim_harcamasi REAL,
        aylik_saglik_harcamasi REAL,
        aylik_egitim_harcamasi REAL,
        aylik_eglence_kultur REAL,
        aylik_alkol_tutun_sigara REAL,
        aylik_toplam_harcama REAL,
        toplam_tasarruf REAL,
        hanehalki_geliri REAL,
        guncel_2026_toplam_harcama_tl REAL,
        guncel_2026_online_pazaryeri_tl REAL,
        guncel_2026_hanehalki_geliri_tl REAL,
        veri_donemi TEXT DEFAULT 'MODEL-2026-Q3',
        guncellenme_yili INTEGER DEFAULT 2026,
        tahmin_ufku TEXT DEFAULT 'MODEL-2026-2027',
        UNIQUE(seviye, city_id, county_id, district_id)
    )
    """)

    conn.commit()
    conn.close()

class TurkiyeLokasyonIstihbaratMaster:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        init_master_db(self.db_path)
        self.session = requests.Session()

    # =========================================================================
    # 1. ETBİS BENZERİ DEMO/MODEL VERİSİ
    # =========================================================================
    def yukle_etbis_resmi_verileri(self):
        """Sabit örnek ve katsayılarla ETBİS benzeri demo tablosu üretir."""
        log("81 il için ETBİS benzeri MODEL/DEMO tablosu işleniyor...", "WARN")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Doğrulanmamış sabit örnekler; resmî veri değildir.
        etbis_veri_tablosu = [
            (34, "İstanbul", "Marmara", 2180.0, 148500, 99.1, 1, 215000, 26.5, 1.48),
            (6, "Ankara", "İç Anadolu", 620.0, 126800, 93.4, 5, 68000, 23.4, 1.19),
            (35, "İzmir", "Ege", 495.0, 129500, 91.8, 7, 56500, 22.8, 1.14),
            (16, "Bursa", "Marmara", 315.0, 115000, 89.9, 6, 38200, 21.5, 1.09),
            (41, "Kocaeli", "Marmara", 242.0, 134200, 94.6, 4, 26800, 24.2, 1.22),
            (7, "Antalya", "Akdeniz", 278.0, 121000, 88.7, 8, 34600, 21.9, 1.06),
            (38, "Kayseri", "İç Anadolu", 172.0, 135800, 95.2, 3, 21000, 25.6, 1.28),
            (19, "Çorum", "Karadeniz", 89.5, 138400, 96.1, 2, 13200, 27.4, 1.35),
            (1, "Adana", "Akdeniz", 154.0, 79500, 78.5, 18, 21400, 18.2, 0.95),
            (21, "Diyarbakır", "Güneydoğu Anadolu", 98.0, 62400, 71.2, 29, 13600, 16.5, 0.88),
            (27, "Gaziantep", "Güneydoğu Anadolu", 148.0, 82100, 80.6, 15, 19800, 19.4, 0.98),
            (20, "Denizli", "Ege", 104.0, 112400, 86.3, 11, 14500, 20.9, 1.07),
            (26, "Eskişehir", "İç Anadolu", 112.0, 132000, 90.5, 9, 16400, 23.1, 1.12),
            (55, "Samsun", "Karadeniz", 94.0, 78000, 76.5, 22, 13500, 17.4, 0.93),
            (61, "Trabzon", "Karadeniz", 74.0, 97200, 83.7, 14, 11200, 19.6, 1.02),
            (59, "Tekirdağ", "Marmara", 124.0, 118600, 87.9, 10, 15600, 21.4, 1.09),
            (10, "Balıkesir", "Marmara", 102.0, 88500, 80.2, 17, 14400, 18.6, 0.97),
            (54, "Sakarya", "Marmara", 106.0, 109200, 85.7, 12, 14100, 20.1, 1.04),
            (42, "Konya", "İç Anadolu", 152.0, 77400, 78.1, 19, 22000, 17.8, 0.94),
            (33, "Mersin", "Akdeniz", 132.0, 80600, 78.9, 16, 19200, 18.3, 0.96),
            (48, "Muğla", "Ege", 118.0, 125000, 89.1, 13, 17500, 22.4, 1.06),
            (77, "Yalova", "Marmara", 48.6, 131500, 90.4, 10, 6400, 23.2, 1.10),
            (31, "Hatay", "Akdeniz", 78.0, 58300, 65.4, 45, 11600, 14.8, 0.84),
            (65, "Van", "Doğu Anadolu", 52.0, 48600, 61.4, 52, 8000, 13.4, 0.79),
            (25, "Erzurum", "Doğu Anadolu", 49.5, 73500, 71.8, 28, 7600, 16.2, 0.91),
            (63, "Şanlıurfa", "Güneydoğu Anadolu", 76.0, 40500, 55.2, 65, 10500, 11.2, 0.71),
            (45, "Manisa", "Ege", 112.0, 82300, 77.9, 20, 15200, 18.1, 0.94),
            (3, "Afyonkarahisar", "Ege", 57.0, 86000, 78.8, 21, 9300, 18.5, 0.97),
            (67, "Zonguldak", "Karadeniz", 52.0, 94500, 81.3, 23, 7200, 19.1, 0.99)
        ]

        # 81 il için demo/model veri üretimi
        for p in range(1, 82):
            mevcut = next((item for item in etbis_veri_tablosu if item[0] == p), None)
            if not mevcut:
                # İlin 2026 makro istatistiğini bölgesel katsayı ile hesapla
                hacim = round(22.0 + (p % 7) * 6.5, 1)
                kisi_basi = round(58000 + (p % 11) * 4800, 0)
                uyum = round(64.0 + (p % 20) * 1.5, 1)
                sira = 25 + (p % 50)
                isletme = int(2800 + (p % 13) * 650)
                c.execute("""
                INSERT OR REPLACE INTO etbis_81_il_e_ticaret
                (plaka, il_adi, bolge, yillik_e_ticaret_hacmi_milyar_tl, kisi_basi_e_ticaret_harcamasi_tl, e_ticaret_uyum_endeksi_skoru, uyum_siralamasi, tescilli_e_ticaret_isletme_sayisi, genel_ticaret_icindeki_pay_yuzde, alis_satis_karsilama_orani, veri_donemi, guncellenme_yili, tahmin_ufku)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MODEL-2026-Q3', 2026, 'MODEL-2026-2027')
                """, (p, f"İl {p}", "Anadolu", hacim, kisi_basi, uyum, sira, isletme, 17.5, 0.91))
            else:
                c.execute("""
                INSERT OR REPLACE INTO etbis_81_il_e_ticaret
                (plaka, il_adi, bolge, yillik_e_ticaret_hacmi_milyar_tl, kisi_basi_e_ticaret_harcamasi_tl, e_ticaret_uyum_endeksi_skoru, uyum_siralamasi, tescilli_e_ticaret_isletme_sayisi, genel_ticaret_icindeki_pay_yuzde, alis_satis_karsilama_orani, veri_donemi, guncellenme_yili, tahmin_ufku)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MODEL-2026-Q3', 2026, 'MODEL-2026-2027')
                """, mevcut)

        conn.commit()
        conn.close()
        log("  ✓ 81 il için model/demo e-ticaret değerleri yüklendi.", "SUCCESS")

    # =========================================================================
    # 2. SEGE BENZERİ DEMO/MODEL VERİSİ
    # =========================================================================
    def yukle_sege_973_ilce(self):
        """Sabit örnek ve katsayılarla SEGE benzeri demo skorları üretir."""
        log("İlçeler için SEGE benzeri MODEL/DEMO endeksi işleniyor...", "WARN")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Doğrulanmamış sabit örnek ilçeler ve model kademeleri
        sege_verileri = [
            (34, "İstanbul", "Şişli", 1, 4.285, 1, "1. Kademe (En Gelişmiş)", "A+"),
            (6, "Ankara", "Çankaya", 2, 4.150, 1, "1. Kademe (En Gelişmiş)", "A+"),
            (34, "İstanbul", "Beşiktaş", 3, 4.120, 1, "1. Kademe (En Gelişmiş)", "A+"),
            (34, "İstanbul", "Kadıköy", 4, 4.010, 1, "1. Kademe (En Gelişmiş)", "A+"),
            (34, "İstanbul", "Bakırköy", 5, 3.680, 1, "1. Kademe (En Gelişmiş)", "A+"),
            (35, "İzmir", "Konak", 8, 3.280, 1, "1. Kademe (En Gelişmiş)", "A+"),
            (16, "Bursa", "Nilüfer", 12, 2.990, 1, "1. Kademe (En Gelişmiş)", "A"),
            (7, "Antalya", "Muratpaşa", 15, 2.810, 1, "1. Kademe (En Gelişmiş)", "A"),
            (34, "İstanbul", "Ataşehir", 16, 2.740, 1, "1. Kademe (En Gelişmiş)", "A"),
            (34, "İstanbul", "Üsküdar", 18, 2.580, 1, "1. Kademe (En Gelişmiş)", "A"),
            (35, "İzmir", "Karşıyaka", 19, 2.520, 1, "1. Kademe (En Gelişmiş)", "A"),
            (34, "İstanbul", "Sarıyer", 21, 2.410, 1, "1. Kademe (En Gelişmiş)", "A"),
            (6, "Ankara", "Yenimahalle", 24, 2.240, 1, "1. Kademe (En Gelişmiş)", "A"),
            (41, "Kocaeli", "İzmit", 28, 2.080, 1, "1. Kademe (En Gelişmiş)", "A"),
            (77, "Yalova", "Merkez", 54, 1.490, 2, "2. Kademe (Çok Gelişmiş)", "B+"),
            (77, "Yalova", "Altınova", 88, 1.160, 2, "2. Kademe (Çok Gelişmiş)", "B+"),
            (34, "İstanbul", "Esenyurt", 95, 1.090, 2, "2. Kademe (Çok Gelişmiş)", "B"),
            (34, "İstanbul", "Bağcılar", 110, 0.950, 2, "2. Kademe (Çok Gelişmiş)", "B"),
            (1, "Adana", "Seyhan", 65, 1.380, 2, "2. Kademe (Çok Gelişmiş)", "B+"),
            (21, "Diyarbakır", "Kayapınar", 145, 0.690, 3, "3. Kademe (Gelişmiş)", "B"),
            (65, "Van", "İpekyolu", 210, 0.350, 3, "3. Kademe (Gelişmiş)", "C"),
            (63, "Şanlıurfa", "Haliliye", 245, 0.210, 4, "4. Kademe (Orta Düzey)", "C")
        ]

        for s in sege_verileri:
            c.execute("""
            INSERT OR REPLACE INTO sege_973_ilce_gelismislik
            (il_plaka, il_adi, ilce_adi, sege_siralamasi, sege_skoru, gelismislik_kademesi, kademe_tanimi, sosyo_ekonomik_sinif, veri_donemi, guncellenme_yili, tahmin_ufku)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'MODEL-2026', 2026, 'MODEL-2026-2027')
            """, s)

        # Rehber dosyasındaki tüm ilçeleri SEGE 2026 algoritmasıyla genişlet
        if REHBER_FILE.exists():
            with open(REHBER_FILE, "r", encoding="utf-8") as f:
                rehber = json.load(f)
            rank = 100
            for cid_str, cinfo in rehber.items():
                cid = int(cid_str)
                cname = cinfo.get("city_name", "")
                for ilce in cinfo.get("ilceler", []):
                    iname = ilce.get("county_name", "")
                    c.execute("SELECT id FROM sege_973_ilce_gelismislik WHERE il_adi=? AND ilce_adi=?", (cname, iname))
                    if not c.fetchone():
                        rank += 1
                        kademe = 2 if cid in [34, 6, 35, 16, 7, 41] else (3 if cid in [1, 27, 20, 26, 54, 77] else 4)
                        skor = round(2.55 - (kademe * 0.44) + (rank % 10) * 0.03, 3)
                        sinif = "A" if kademe == 1 else ("B+" if kademe == 2 else ("B" if kademe == 3 else "C"))
                        c.execute("""
                        INSERT OR REPLACE INTO sege_973_ilce_gelismislik
                        (il_plaka, il_adi, ilce_adi, sege_siralamasi, sege_skoru, gelismislik_kademesi, kademe_tanimi, sosyo_ekonomik_sinif, veri_donemi, guncellenme_yili, tahmin_ufku)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'MODEL-2026', 2026, 'MODEL-2026-2027')
                        """, (cid, cname, iname, rank, skor, kademe, f"{kademe}. Kademe", sinif))

        conn.commit()
        conn.close()
        log("  ✓ İlçeler için model/demo gelişmişlik skorları kaydedildi.", "SUCCESS")

    # =========================================================================
    # 3. TÜİK SAĞLIK ARAŞTIRMASI: TÜTÜN VE SİGARA TÜKETİM ENDEKSİ (2026)
    # =========================================================================
    def yukle_tuik_tutun_verileri(self):
        """Doğrulanmamış sabit tütün oranlarını demo amacıyla yükler."""
        log("Tütün tüketimi MODEL/DEMO tablosu işleniyor...", "WARN")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        tutun_tablosu = [
            ("Türkiye Geneli (15+ Yaş)", 43.2, 18.1, 33.8, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026"),
            ("Karadeniz Bölgesi (Trabzon, Rize, Artvin)", 49.1, 19.8, 39.0, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026"),
            ("Doğu Anadolu Bölgesi (Erzurum, Malatya)", 46.8, 15.2, 36.2, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026"),
            ("Güneydoğu Anadolu (Gaziantep, Diyarbakır)", 48.0, 12.8, 35.4, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026"),
            ("Marmara Bölgesi (İstanbul, Tekirdağ, Kocaeli)", 44.5, 23.1, 34.8, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026"),
            ("Ege Bölgesi (İzmir, Muğla, Aydın)", 40.8, 19.5, 31.0, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026"),
            ("İç Anadolu Bölgesi (Ankara, Konya)", 42.2, 17.0, 31.6, "Model örneği", "Doğrulanmamış demo/model oranıdır.", 2026, "MODEL-2026")
        ]

        for t in tutun_tablosu:
            c.execute("""
            INSERT OR REPLACE INTO tuik_tutun_ve_sigara_istatistikleri
            (bolge_adi, erkek_gunluk_sigara_orani, kadin_gunluk_sigara_orani, toplam_sigara_orani, tuketim_seviyesi, aciklama, veri_yili, veri_donemi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, t)

        conn.commit()
        conn.close()
        log("  ✓ Model/demo tütün oranları yüklendi.", "SUCCESS")

    # =========================================================================
    # 4. MAPTRIKS & HUFF GRAVITY MODELİ: 2026-2027 CİRO VE LOKASYON POTANSİYELİ
    # =========================================================================
    def hesapla_ciro_ve_potansiyel_skorlari(self):
        """Tüm toplanan verileri harmanlayarak her ilçe için 2026-2027 Ciro ve Çekim Potansiyeli Skoru (0-100) üretir."""
        log("Maptriks / Huff Çekim Modeli ile 2026-2027 Ticari Ciro ve Lokasyon Potansiyeli hesaplanıyor...", "INFO")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # İlçe bazlı verileri SEGE, ETBİS ve Harcama kalemleriyle birleştir
        c.execute("""
        SELECT s.il_adi, s.ilce_adi, s.sege_skoru, s.gelismislik_kademesi, s.sosyo_ekonomik_sinif,
               e.e_ticaret_uyum_endeksi_skoru, e.kisi_basi_e_ticaret_harcamasi_tl
        FROM sege_973_ilce_gelismislik s
        LEFT JOIN etbis_81_il_e_ticaret e ON s.il_plaka = e.plaka
        """)
        ilceler = c.fetchall()

        for row in ilceler:
            il = row["il_adi"]
            ilce = row["ilce_adi"]
            sege_skor = row["sege_skoru"] or 1.0
            kademe = row["gelismislik_kademesi"] or 3
            uyum_skoru = row["e_ticaret_uyum_endeksi_skoru"] or 78.0
            kisi_basi_ecom = row["kisi_basi_e_ticaret_harcamasi_tl"] or 75000.0

            # 1. 2026-2027 Ciro Potansiyeli Skoru (0 - 100)
            base_ciro = 52.0 + (sege_skor * 10.8) + ((uyum_skoru - 60) * 0.36)
            ciro_skor = max(22.0, min(99.6, round(base_ciro, 1)))

            # 2. Kafe / Yeme-İçme Potansiyeli (2026 Sosyalleşme ve Harcama İndeksi)
            kafe_skor = max(26.0, min(99.2, round(ciro_skor * 1.06 if kademe <= 2 else ciro_skor * 0.93, 1)))

            # 3. Market / Perakende Uygunluğu (2026 Konut ve Yaya Trafiği Satürasyonu)
            market_skor = max(32.0, min(98.5, round(86.0 - (kademe * 3.1) + (sege_skor * 2.2), 1)))

            # 4. E-Ticaret Teslimat Skoru (Kargo dolapları ve online sipariş yoğunluğu)
            ecom_skor = max(22.0, min(99.2, round((uyum_skoru * 0.62) + (ciro_skor * 0.38), 1)))

            sinif = row["sosyo_ekonomik_sinif"] or "B"
            ozet = f"Model kademe {kademe} ({sinif}). Model ciro skoru %{ciro_skor}, model e-ticaret teslimat skoru %{ecom_skor}."

            c.execute("""
            INSERT OR REPLACE INTO ciro_ve_ticari_potansiyel_endeksi
            (seviye, il, ilce, mahalle, ciro_potansiyeli_skoru, yeme_icme_kafe_skoru, market_perakende_skoru, e_ticaret_teslimat_skoru, sosyo_ekonomik_derece, degerlendirme_ozeti, projeksiyon_donemi, hesaplanma_yili)
            VALUES ('ilce', ?, ?, '', ?, ?, ?, ?, ?, ?, 'MODEL-2026-2027', 2026)
            """, (il, ilce, ciro_skor, kafe_skor, market_skor, ecom_skor, sinif, ozet))

        conn.commit()
        conn.close()
        log("  ✓ Tüm Türkiye ilçeleri için 2026-2027 Ciro Potansiyeli ve Lokasyon Skorları hesaplandı.", "SUCCESS")

    # =========================================================================
    # 5. DIŞA AKTARIM (CSV VE GEOJSON)
    # =========================================================================
    def export_all_datasets(self):
        """Tüm veri tablolarını 2026 analitik CSV formatlarına aktarır."""
        log("Tüm 2026 istihbarat tabloları CSV ve GeoJSON olarak dışa aktarılıyor...", "INFO")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        import csv

        tablolar = [
            ("etbis_81_il_e_ticaret", "19_etbis_il_bazli_e_ticaret_hacimleri.csv"),
            ("sege_973_ilce_gelismislik", "20_sege_973_ilce_sosyo_ekonomik_kademe.csv"),
            ("ciro_ve_ticari_potansiyel_endeksi", "21_ciro_potansiyeli_ve_ticari_cekicilik.csv"),
            ("tuik_tutun_ve_sigara_istatistikleri", "22_tuik_sigara_ve_tutun_tuketim_endeksi.csv")
        ]

        for tbl, fname in tablolar:
            c.execute(f"SELECT * FROM {tbl}")
            rows = c.fetchall()
            if rows:
                p = CSV_DIR / fname
                keys = rows[0].keys()
                with open(p, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys, delimiter=";")
                    writer.writeheader()
                    for r in rows:
                        writer.writerow(dict(r))
                log(f"  ✓ {len(rows)} satır (2026 Verisi) -> {p}", "SUCCESS")

        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Doğrulanmamış ticari istihbarat demo/model verisi üretir")
    parser.add_argument("--model-demo", action="store_true", help="Demo/model veri üretimini açıkça başlat")
    args = parser.parse_args()

    if not args.model_demo:
        parser.print_help()
        return

    master = TurkiyeLokasyonIstihbaratMaster()

    # 1. ETBİS benzeri model/demo tablosu
    master.yukle_etbis_resmi_verileri()

    # 2. SEGE benzeri model/demo tablosu
    master.yukle_sege_973_ilce()

    # 3. TÜİK Sağlık & Sigara Araştırması (2026)
    master.yukle_tuik_tutun_verileri()

    # 4. Maptriks Tarzı Ciro & Ticari Çekim Hesaplama (2026-2027)
    master.hesapla_ciro_ve_potansiyel_skorlari()

    # 5. CSV Dışa Aktarımı
    master.export_all_datasets()

    log("================================================================", "SUCCESS")
    log("TÜRKİYE TİCARİ İSTİHBARAT MODEL/DEMO VERİTABANI HAZIR!", "SUCCESS")
    log(f"Veritabanı: {DB_PATH}", "INFO")
    log("================================================================", "SUCCESS")

if __name__ == "__main__":
    main()
