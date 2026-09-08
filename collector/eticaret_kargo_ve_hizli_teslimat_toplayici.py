#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - E-Ticaret, Kargo ve Hızlı Teslimat Veri Toplayıcısı
------------------------------------------------------------------
Türkiye genelinde İl, İlçe ve Mahalle düzeyinde:
1. E-Ticaret Hacimleri & Tüketim Harcamaları (Trendyol, Hepsiburada, Giyim, Elektronik, Bahis, Seyahat)
2. Hanehalkı Harcama Kalemleri (Gıda, Barınma, Restoran, Sigara & Alkol, Sağlık, Tasarruf)
3. PTT Kargo Şubeleri & 7/24 Kargomat Akıllı Kargo Dolapları (Birebir GPS koordinatları)
4. Mahalle Lojistik ve E-Ticaret Penetrasyon İndeksleri
verilerini toplayarak SQLite, CSV ve GeoJSON olarak dışa aktarır.
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
from concurrent.futures import ThreadPoolExecutor

try:
    from curl_cffi import requests
except ImportError:
    import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "eticaret_ve_lojistik.sqlite"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

REHBER_FILE = BASE_DIR / "turkiye_il_ilce_rehberi.json"

PTT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://enyakinptt.ptt.gov.tr/",
    "Origin": "https://enyakinptt.ptt.gov.tr",
    "Accept": "*/*"
}

EMLAKJET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.emlakjet.com/"
}

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "[*]", "SUCCESS": "[✓]", "WARN": "[!]", "ERROR": "[✗]"}
    print(f"{ts} {symbols.get(level, '[*]')} {msg}", flush=True)

def init_db(db_path=None):
    target_db = db_path or DB_PATH
    conn = sqlite3.connect(target_db)
    c = conn.cursor()

    # 1. E-Ticaret ve Harcama Kalemleri Tablosu (2026 ve Sonrası)
    c.execute("""
    CREATE TABLE IF NOT EXISTS eticaret_ve_harcama_kalemleri (
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
        guncel_2026_kira_barinma_tl REAL,
        guncel_2026_gida_tl REAL,
        guncel_2026_alkol_tutun_tl REAL,
        guncel_2026_hanehalki_geliri_tl REAL,
        e_ticaret_harcama_endeksi_2026 REAL,
        veri_donemi TEXT DEFAULT '2026-Q3 (Güncel)',
        guncellenme_yili INTEGER DEFAULT 2026,
        tahmin_ufku TEXT DEFAULT '2026-2027 Projeksiyonu',
        guncellenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(seviye, city_id, county_id, district_id)
    )
    """)

    # 2026 Kolon Göçü (Migration)
    c.execute("PRAGMA table_info(eticaret_ve_harcama_kalemleri)")
    cols = [r[1] for r in c.fetchall()]
    new_cols = [
        ("guncel_2026_toplam_harcama_tl", "REAL"),
        ("guncel_2026_online_pazaryeri_tl", "REAL"),
        ("guncel_2026_kira_barinma_tl", "REAL"),
        ("guncel_2026_gida_tl", "REAL"),
        ("guncel_2026_alkol_tutun_tl", "REAL"),
        ("guncel_2026_hanehalki_geliri_tl", "REAL"),
        ("e_ticaret_harcama_endeksi_2026", "REAL"),
        ("veri_donemi", "TEXT DEFAULT '2026-Q3 (Güncel)'"),
        ("guncellenme_yili", "INTEGER DEFAULT 2026"),
        ("tahmin_ufku", "TEXT DEFAULT '2026-2027 Projeksiyonu'")
    ]
    for col_name, col_type in new_cols:
        if col_name not in cols:
            c.execute(f"ALTER TABLE eticaret_ve_harcama_kalemleri ADD COLUMN {col_name} {col_type}")

    # 2. Kargo Şubeleri ve Kargomatlar Tablosu
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
        guncellenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tip, ad, adres)
    )
    """)

    conn.commit()
    conn.close()

class EticaretVeLojistikToplayici:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        init_db(self.db_path)
        self.session = requests.Session()

    def decode_b64(self, raw_text):
        """PTT API Base64 cevabını UTF-8 JSON'a çözer."""
        try:
            cleaned = raw_text.strip().strip('"')
            decoded_bytes = base64.b64decode(cleaned)
            return json.loads(decoded_bytes.decode("utf-8"))
        except Exception:
            try:
                return json.loads(raw_text)
            except Exception:
                return []

    # =========================================================================
    # BÖLÜM 1: PTT KARGO ŞUBELERİ VE 7/24 KARGOMAT AKILLI TESLİMAT DOLAPLARI
    # =========================================================================
    def fetch_ptt_iller(self):
        """PTT il listesini döner."""
        url = "https://enyakinptt.ptt.gov.tr/api/il"
        try:
            r = self.session.get(url, headers=PTT_HEADERS, verify=False, timeout=15)
            if r.status_code == 200:
                return self.decode_b64(r.text)
        except Exception as e:
            log(f"PTT İl listesi alınamadı: {e}", "ERROR")
        return []

    def fetch_ptt_lojistik_il(self, il_kod, il_ad):
        """Belirtilen ildeki tüm PTT şubelerini ve Kargomatları çeker ve DB'ye yazar."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        sube_sayisi = 0
        kargomat_sayisi = 0

        # 1. PTT Şubeleri
        url_sube = "https://enyakinptt.ptt.gov.tr/api/Isyerleri"
        try:
            r_sube = self.session.post(
                url_sube,
                data={"ilID": il_kod, "ilceID": 0, "mahKoyID": 0},
                headers=PTT_HEADERS,
                verify=False,
                timeout=20
            )
            if r_sube.status_code == 200:
                subeler = self.decode_b64(r_sube.text)
                for s in subeler:
                    ad = s.get("Ad", "")
                    adres = s.get("Adres", "")
                    tel = s.get("Telefon", "")
                    lat = float(s.get("Lat")) if s.get("Lat") else None
                    lon = float(s.get("Lon")) if s.get("Lon") else None
                    h_ici = s.get("HaftaIci", "")
                    cts = s.get("Cumartesi", "")
                    paz = s.get("Pazar", "")
                    kod = str(s.get("Sira", ""))

                    if ad and adres:
                        c.execute("""
                        INSERT OR REPLACE INTO kargo_ve_teslimat_noktalari
                        (tip, kod, ad, adres, telefon, il_kod, il_ad, lat, lon, hafta_ici, cumartesi, pazar)
                        VALUES ('PTT_SUBE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (kod, ad, adres, tel, il_kod, il_ad, lat, lon, h_ici, cts, paz))
                        sube_sayisi += 1
        except Exception as e:
            log(f"{il_ad} PTT şubeleri çekilirken hata: {e}", "WARN")

        # 2. 7/24 Kargomat Akıllı Kargo Dolapları
        url_kargomat = "https://enyakinptt.ptt.gov.tr/api/Kargomat"
        try:
            r_kargomat = self.session.post(
                url_kargomat,
                data={"ilID": il_kod, "ilceID": 0, "mahKoyID": 0},
                headers=PTT_HEADERS,
                verify=False,
                timeout=20
            )
            if r_kargomat.status_code == 200:
                kargomatlar = self.decode_b64(r_kargomat.text)
                for k in kargomatlar:
                    ad = k.get("Ad", "")
                    adres = k.get("Adres", "")
                    lat = float(k.get("Lat")) if k.get("Lat") else None
                    lon = float(k.get("Lon")) if k.get("Lon") else None
                    kod = str(k.get("Sira", ""))

                    if ad and adres:
                        c.execute("""
                        INSERT OR REPLACE INTO kargo_ve_teslimat_noktalari
                        (tip, kod, ad, adres, telefon, il_kod, il_ad, lat, lon, hafta_ici, cumartesi, pazar)
                        VALUES ('PTT_KARGOMAT', ?, ?, ?, '', ?, ?, ?, ?, '7/24 Açık', '7/24 Açık', '7/24 Açık')
                        """, (kod, ad, adres, il_kod, il_ad, lat, lon))
                        kargomat_sayisi += 1
        except Exception as e:
            log(f"{il_ad} Kargomatlar çekilirken hata: {e}", "WARN")

        conn.commit()
        conn.close()
        return sube_sayisi, kargomat_sayisi

    def topla_tum_turkiye_kargo(self, il_filtre=None):
        """Tüm Türkiye'deki PTT kargo ve Kargomat noktalarını toplar."""
        log("PTT Kargo ve 7/24 Kargomat Akıllı Teslimat Ağı taranıyor...", "INFO")
        iller = self.fetch_ptt_iller()
        if not iller:
            log("İl listesi boş döndü.", "ERROR")
            return

        toplam_sube = 0
        toplam_kargomat = 0

        for item in iller:
            il_kod = item.get("Kod")
            il_ad = item.get("Ad", "").strip().upper()

            if il_filtre and il_ad not in [i.strip().upper() for i in il_filtre]:
                continue

            sube_sayisi, kargomat_sayisi = self.fetch_ptt_lojistik_il(il_kod, il_ad)
            toplam_sube += sube_sayisi
            toplam_kargomat += kargomat_sayisi
            log(f"  ✓ {il_ad}: {sube_sayisi} Şube, {kargomat_sayisi} Kargomat kaydedildi.", "SUCCESS")
            time.sleep(0.15)

        log(f"[✔] PTT Lojistik Ağı Tamamlandı! Toplam {toplam_sube} Şube, {toplam_kargomat} Kargomat.", "SUCCESS")

    # =========================================================================
    # BÖLÜM 2: E-TİCARET HACİMLERİ VE TÜKETİM HARCAMA KALEMLERİ
    # =========================================================================
    def fetch_demografi_ecom(self, level, city_id, county_id=0, district_id=0):
        """Emlakjet/Endeksa API'sinden e-ticaret ve tüketim verilerini çeker."""
        url = f"https://www.emlakjet.com/api/demography?CountryId=1&Level={level}&CityId={city_id}"
        if level >= 2:
            url += f"&CountyId={county_id}"
        if level >= 3:
            url += f"&DistrictId={district_id}"

        try:
            r = self.session.get(url, headers=EMLAKJET_HEADERS, timeout=15)
            if r.status_code == 200:
                res = r.json()
                return res.get("Demography")
        except Exception:
            pass
        return None

    def save_ecom_row(self, conn, seviye, city_id, county_id, district_id, bolge_adi, il, ilce, mahalle, d):
        if not d:
            return
        c = conn.cursor()

        raw_total = float(d.get("ExpenseTotal") or 15000.0)
        raw_pazaryeri = float(d.get("OnlineRetailOnlyMarketplace") or 1200000.0)
        raw_shelter = float(d.get("ExpenseShelter") or 3500.0)
        raw_food = float(d.get("ExpenseFood") or 2500.0)
        raw_alcohol = float(d.get("ExpenseAlcoholAndSmoking") or 350.0)
        raw_income = float(d.get("HouseIncomeTotal") or d.get("HouseIncome") or 45000.0)
        ecom_density = float(d.get("ECommerceDensity") or 10.0)

        # 2026 ve Sonrası Makroekonomik ve Alım Gücü Endekslemesi
        g_total = round(raw_total * 4.25, 2)
        g_pazar = round(raw_pazaryeri * 4.75, 2)
        g_shelter = round(raw_shelter * 4.60, 2)
        g_food = round(raw_food * 4.15, 2)
        g_alcohol = round(raw_alcohol * 3.90, 2)
        g_income = round(raw_income * 4.30, 2)
        ecom_skor_2026 = min(99.8, max(25.0, round(52.0 + (ecom_density * 2.4) + min(28.0, (raw_pazaryeri / 180000.0) * 1.5), 1)))

        c.execute("""
        INSERT OR REPLACE INTO eticaret_ve_harcama_kalemleri (
            seviye, city_id, county_id, district_id, bolge_adi, il, ilce, mahalle,
            e_ticaret_kullanici_sayisi, e_ticaret_yogunluk,
            online_pazaryeri_tl, online_tatil_seyahat_tl, online_yasal_bahis_tl,
            online_elektronik_tl, online_giyim_ayakkabi_tl, ev_dekorasyon_tl, online_eglence_kultur_tl,
            aylik_gida_harcamasi, aylik_barinma_kira_harcamasi, aylik_ulasim_harcamasi,
            aylik_restoran_yeme_icme, aylik_giyim_harcamasi, aylik_saglik_harcamasi,
            aylik_egitim_harcamasi, aylik_eglence_kultur, aylik_alkol_tutun_sigara,
            aylik_toplam_harcama, toplam_tasarruf, hanehalki_geliri,
            guncel_2026_toplam_harcama_tl, guncel_2026_online_pazaryeri_tl, guncel_2026_kira_barinma_tl,
            guncel_2026_gida_tl, guncel_2026_alkol_tutun_tl, guncel_2026_hanehalki_geliri_tl,
            e_ticaret_harcama_endeksi_2026, veri_donemi, guncellenme_yili, tahmin_ufku
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-Q3 (Güncel)', 2026, '2026-2027 Projeksiyonu')
        """, (
            seviye, city_id, county_id, district_id, bolge_adi, il, ilce, mahalle,
            d.get("ECommerceCount"),
            d.get("ECommerceDensity"),
            d.get("OnlineRetailOnlyMarketplace"),
            d.get("OnlineVacationTravel"),
            d.get("OnlineLegalBetting"),
            d.get("MultichannelRetailElectronics"),
            d.get("MultichannelRetailClothingShoes"),
            d.get("MultichannelRetailHomeDecoration"),
            d.get("MultichannelRetailEntertainmentCulture"),
            d.get("ExpenseFood"),
            d.get("ExpenseShelter"),
            d.get("ExpenseTransportation"),
            d.get("ExpenseRestaurant"),
            d.get("ExpenseClothing"),
            d.get("ExpenseHealth"),
            d.get("ExpenseEducation"),
            d.get("ExpenseEntertainment"),
            d.get("ExpenseAlcoholAndSmoking"),
            d.get("ExpenseTotal"),
            d.get("SavingTotal"),
            d.get("HouseIncomeTotal") or d.get("HouseIncome"),
            g_total, g_pazar, g_shelter, g_food, g_alcohol, g_income, ecom_skor_2026
        ))
        conn.commit()

    def topla_tum_turkiye_eticaret(self, il_filtre=None, mahalle_topla=True):
        """İl, İlçe ve Mahalle düzeyinde e-ticaret harcamalarını çeker."""
        if not REHBER_FILE.exists():
            log(f"Rehber dosyası bulunamadı: {REHBER_FILE}", "ERROR")
            return

        with open(REHBER_FILE, "r", encoding="utf-8") as f:
            rehber = json.load(f)

        conn = sqlite3.connect(self.db_path)
        log("E-Ticaret ve Hanehalkı Tüketim Harcamaları taranıyor...", "INFO")

        for city_id_str, city_info in rehber.items():
            city_id = int(city_id_str)
            city_name = city_info.get("city_name", "")

            if il_filtre and city_name.strip().lower() not in [i.strip().lower() for i in il_filtre]:
                continue

            # 1. İl Seviyesi
            d_il = self.fetch_demografi_ecom(level=1, city_id=city_id)
            if d_il:
                self.save_ecom_row(conn, "il", city_id, 0, 0, city_name, city_name, "", "", d_il)
                ecom_tl = d_il.get("OnlineRetailOnlyMarketplace", 0) or 0
                log(f"🏛️ {city_name} İl Geneli: {d_il.get('ECommerceCount', 0):,} E-Ticaret Kullanıcısı, {ecom_tl:,.0f} TL Online Pazaryeri", "SUCCESS")

            # 2. İlçe Seviyesi
            ilceler_list = city_info.get("ilceler", [])
            if not ilceler_list:
                try:
                    url_ilce = f"https://www.emlakjet.com/api/endeksa/dynamictrend?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=1&CityId={city_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=true&Trend=false&Types=false&Wkt="
                    r_ilce = self.session.get(url_ilce, headers=EMLAKJET_HEADERS, timeout=12)
                    if r_ilce.status_code == 200:
                        static_items = r_ilce.json().get("Static", [])
                        ilceler_list = [{"county_id": int(item.get("CountyId")), "county_name": item.get("CountyName") or item.get("DisplayName", "")} for item in static_items if item.get("CountyId")]
                except Exception:
                    pass

            for ilce in ilceler_list:
                county_id = ilce.get("county_id")
                county_name = ilce.get("county_name", "")
                if not county_id:
                    continue
                d_ilce = self.fetch_demografi_ecom(level=2, city_id=city_id, county_id=county_id)
                if d_ilce:
                    bolge = f"{city_name} - {county_name}"
                    self.save_ecom_row(conn, "ilce", city_id, county_id, 0, bolge, city_name, county_name, "", d_ilce)
                    log(f"  📍 {county_name}: {d_ilce.get('ECommerceCount', 0):,} Kullanıcı, Sigara/Alkol: {d_ilce.get('ExpenseAlcoholAndSmoking', 0):.0f} TL", "INFO")
                time.sleep(0.1)

                # 3. Mahalle Seviyesi
                if mahalle_topla:
                    url_mah = f"https://www.emlakjet.com/api/endeksa/dynamictrend?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=2&CityId={city_id}&CountyId={county_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=true&Trend=false&Types=false&Wkt="
                    try:
                        r_mah = self.session.get(url_mah, headers=EMLAKJET_HEADERS, timeout=12)
                        if r_mah.status_code == 200:
                            m_list = r_mah.json().get("Static", [])
                            for m in m_list:
                                dist_id = int(m.get("DistrictId", 0))
                                dist_name = m.get("DistrictName") or m.get("DisplayName", "")
                                if not dist_id:
                                    continue
                                d_mah = self.fetch_demografi_ecom(level=3, city_id=city_id, county_id=county_id, district_id=dist_id)
                                if d_mah:
                                    bolge_mah = f"{city_name} - {county_name} - {dist_name}"
                                    self.save_ecom_row(conn, "mahalle", city_id, county_id, dist_id, bolge_mah, city_name, county_name, dist_name, d_mah)
                                    log(f"    🏘️ {dist_name}: {d_mah.get('ECommerceCount', 0)} e-ticaret alıcısı, 2026 Endeksli Pazaryeri: {d_mah.get('OnlineRetailOnlyMarketplace', 0)*4.75:,.0f} TL", "INFO")
                                time.sleep(0.05)
                    except Exception:
                        pass

        conn.close()
        log("[✔] E-Ticaret ve Harcama Kalemleri Toplama Tamamlandı!", "SUCCESS")

    # =========================================================================
    # BÖLÜM 3: DIŞA AKTARIM (CSV VE GEOJSON)
    # =========================================================================
    def export_all(self):
        """Toplanan verileri CSV ve GeoJSON olarak dışa aktarır."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # 1. CSV: 15_e_ticaret_ve_harcama_kalemleri.csv
        c.execute("SELECT * FROM eticaret_ve_harcama_kalemleri ORDER BY seviye, city_id, county_id, district_id")
        rows = c.fetchall()
        if rows:
            csv_path = CSV_DIR / "15_e_ticaret_ve_harcama_kalemleri.csv"
            keys = rows[0].keys()
            import csv
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys, delimiter=";")
                writer.writeheader()
                for r in rows:
                    writer.writerow(dict(r))
            log(f"  ✓ {len(rows)} satır -> {csv_path}", "SUCCESS")

        # 2. CSV: 18_kargo_ve_teslimat_noktalari.csv
        c.execute("SELECT * FROM kargo_ve_teslimat_noktalari ORDER BY il_ad, tip, ad")
        rows_kargo = c.fetchall()
        if rows_kargo:
            csv_kargo = CSV_DIR / "18_kargo_ve_teslimat_noktalari.csv"
            keys_kargo = rows_kargo[0].keys()
            import csv
            with open(csv_kargo, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys_kargo, delimiter=";")
                writer.writeheader()
                for r in rows_kargo:
                    writer.writerow(dict(r))
            log(f"  ✓ {len(rows_kargo)} nokta -> {csv_kargo}", "SUCCESS")

        # 3. GeoJSON: turkiye_kargo_ve_kargomatlar.geojson
        features = []
        for r in rows_kargo:
            lat = r["lat"]
            lon = r["lon"]
            if lat and lon:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "tip": r["tip"],
                        "ad": r["ad"],
                        "adres": r["adres"],
                        "il": r["il_ad"],
                        "telefon": r["telefon"],
                        "calisma_saati": r["hafta_ici"]
                    }
                })

        if features:
            geojson_path = DATA_DIR / "turkiye_kargo_ve_kargomatlar.geojson"
            with open(geojson_path, "w", encoding="utf-8") as f:
                json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, indent=2)
            log(f"  ✓ {len(features)} GeoJSON noktası -> {geojson_path}", "SUCCESS")

        conn.close()

def main():
    parser = argparse.ArgumentParser(description="E-Ticaret, Kargo ve Hızlı Teslimat Veri Toplayıcısı")
    parser.add_argument("--kargo-yalnizca", action="store_true", help="Yalnızca PTT şube ve Kargomat noktalarını toplar")
    parser.add_argument("--eticaret-yalnizca", action="store_true", help="Yalnızca e-ticaret harcama verilerini toplar")
    parser.add_argument("--iller", nargs="+", help="Yalnızca belirli illeri çek (Örn: --iller Istanbul Ankara Izmir)")
    parser.add_argument("--mahalle-atla", action="store_true", help="Yalnızca il ve ilçe seviyesini çek, mahalleleri atla")
    parser.add_argument("--export", action="store_true", help="Mevcut SQLite verilerini CSV ve GeoJSON'a aktar")
    args = parser.parse_args()

    toplayici = EticaretVeLojistikToplayici()

    if args.export:
        toplayici.export_all()
        return

    if not args.eticaret_yalnizca:
        toplayici.topla_tum_turkiye_kargo(il_filtre=args.iller)

    if not args.kargo_yalnizca:
        toplayici.topla_tum_turkiye_eticaret(il_filtre=args.iller, mahalle_topla=not args.mahalle_atla)

    toplayici.export_all()

if __name__ == "__main__":
    main()
