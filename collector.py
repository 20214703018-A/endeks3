#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tüm Türkiye Gayrimenkul, Mahalle, Demografi, Seçim ve Piyasa Veri Toplayıcısı
-------------------------------------------------------------------------------
İl, İlçe ve MAHALLE düzeyinde tüm açık API uçlarını (Demografi, Satışlar,
Hemşehri, Seçim, Fiyat Trendi, Kırılımlar, POI Donatıları, Emlak Ofisleri ve
Harita Poligonları) eksiksiz olarak toplar.
"""

import os
import sys
import json
import time
import random
import sqlite3
import urllib.request
import urllib.error
import argparse
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
POLYGONS_DIR = DATA_DIR / "poligonlar"
DB_PATH = DATA_DIR / "piyasa_verileri.db"
STATE_PATH = DATA_DIR / "durum.json"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
]

API_EJ_TREND = "https://www.emlakjet.com/api/endeksa/dynamictrend"
API_EJ_DEMO = "https://www.emlakjet.com/api/demography"
API_EJ_POLYGONS = "https://www.emlakjet.com/api/geo/polygons"
API_EJ_POI = "https://api.emlakjet.com/e6t/v1/location/district/{county_id}/poi"
API_EJ_OFFICES = "https://api.emlakjet.com/atlas-real-estate-office/v1/listing/emlak-ofisleri"
API_EJ_AGENTS = "https://api.emlakjet.com/atlas-real-estate-office/v1/listing/danismanlar"
API_EJ_COMPANIES = "https://api.emlakjet.com/e6t/v1/project/companies"
API_END_FELLOW = "https://api-valuation.endeksa.com/fellowcountryman"
API_END_ELECTION = "https://api-valuation.endeksa.com/election"

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    colors = {
        "INFO": "\033[94m[*]\033[0m",
        "SUCCESS": "\033[92m[✓]\033[0m",
        "WARN": "\033[93m[!]\033[0m",
        "ERROR": "\033[91m[✗]\033[0m",
    }
    print(f"{timestamp} {colors.get(level, '[*]')} {msg}", flush=True)

class PoliteHTTP:
    def __init__(self, base_delay=1.0, max_retries=5):
        self.base_delay = base_delay
        self.max_retries = max_retries

    def _headers(self, extra=None):
        h = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.emlakjet.com/"
        }
        if extra: h.update(extra)
        return h

    def get(self, url):
        req = urllib.request.Request(url, headers=self._headers())
        return self._fetch(req)

    def post_json(self, url, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers({"Content-Type": "application/json"}))
        return self._fetch(req)

    def _fetch(self, req):
        time.sleep(self.base_delay + random.uniform(0.1, 0.3))
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    if not raw: return None
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):
                    wait = (6 * attempt) + random.uniform(1, 3)
                    ra = e.headers.get("Retry-After")
                    if ra and ra.isdigit(): wait = max(wait, int(ra) + 1)
                    log(f"Hız limiti (HTTP {e.code}). {wait:.1f} sn dinleniliyor (Deneme {attempt}/{self.max_retries})...", "WARN")
                    time.sleep(wait)
                elif e.code in (404, 400):
                    return None
                else:
                    if attempt == self.max_retries: return None
                    time.sleep(2 * attempt)
            except Exception as e:
                if attempt == self.max_retries: return None
                time.sleep(2 * attempt)
        return None

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    cur.execute("""CREATE TABLE IF NOT EXISTS iller (city_id INTEGER PRIMARY KEY, city_name TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ilceler (county_id INTEGER PRIMARY KEY, city_id INTEGER, county_name TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS mahalleler (district_id INTEGER PRIMARY KEY, county_id INTEGER, city_id INTEGER, district_name TEXT, created_at TEXT)""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS demografi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT,
        nufus_toplam INTEGER, nufus_erkek INTEGER, nufus_kadin INTEGER, hane_sayisi INTEGER,
        ortalama_hane_geliri REAL, toplam_hane_geliri REAL, ev_sahibi_orani REAL, kiraci_orani REAL,
        ses_a_plus INTEGER, ses_a INTEGER, ses_b INTEGER, ses_c INTEGER, ses_d INTEGER,
        ses_a_plus_oran REAL, ses_a_oran REAL, ses_b_oran REAL, ses_c_oran REAL, ses_d_oran REAL,
        egitim_universite_oran REAL, egitim_lise_oran REAL, egitim_ortaokul_oran REAL, egitim_ilkokul_oran REAL,
        yas_genc_oran REAL, yas_orta_oran REAL, yas_yasli_oran REAL,
        konut_fiyat_m2 REAL, kira_fiyat_m2 REAL, arsa_fiyat_m2 REAL, tarla_fiyat_m2 REAL, ticari_fiyat_m2 REAL,
        eczane_sayisi INTEGER, atm_sayisi INTEGER, banka_sube_sayisi INTEGER, arac_sayisi INTEGER,
        ham_json TEXT, guncellenme_tarihi TEXT,
        UNIQUE(seviye, city_id, county_id, district_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS yillik_satislar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT, yil INTEGER,
        toplam_konut_satisi INTEGER, ipotekli_konut_satisi INTEGER,
        arsa_arazi_satisi INTEGER, ipotekli_arsa_satisi INTEGER, toplam_ilan_sayisi INTEGER,
        UNIQUE(seviye, city_id, county_id, district_id, yil)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hemsehri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT,
        kutuk_ili TEXT, kisi_sayisi INTEGER, guncellenme_tarihi TEXT,
        UNIQUE(seviye, city_id, county_id, district_id, kutuk_ili)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS secim_sonuclari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT,
        secim_kodu TEXT, secim_adi TEXT, sandik_sayisi INTEGER, kayitli_secmen INTEGER,
        kullanilan_oy INTEGER, gecerli_oy INTEGER, kazanan_parti TEXT, parti_sonuclari_json TEXT,
        UNIQUE(seviye, city_id, county_id, district_id, secim_kodu)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fiyat_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT, seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT, donem TEXT,
        satilik_m2_fiyat REAL, kiralik_m2_fiyat REAL, ortalama_fiyat REAL,
        amortisman_yil REAL, brut_kira_getirisi REAL, ortalama_bina_yasi REAL,
        satilik_kalma_suresi_gun REAL, kiralik_kalma_suresi_gun REAL,
        ilan_sayisi INTEGER, yillik_fiyat_degisim REAL, guncellenme_tarihi TEXT,
        UNIQUE(kategori, seviye, city_id, county_id, district_id, donem)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fiyat_trend (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT, seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT, ay TEXT,
        satilik_m2_fiyat REAL, kiralik_m2_fiyat REAL, ortalama_fiyat REAL,
        ilan_sayisi INTEGER, amortisman_yil REAL, brut_kira_getirisi REAL, projeksiyon INTEGER,
        UNIQUE(kategori, seviye, city_id, county_id, district_id, ay)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fiyat_dagilim (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT, seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER DEFAULT 0, bolge_adi TEXT,
        dagilim_turu TEXT, segment TEXT, oran REAL,
        satilik_m2_fiyat REAL, kiralik_m2_fiyat REAL, ortalama_fiyat REAL,
        ilan_sayisi INTEGER, amortisman_yil REAL,
        UNIQUE(kategori, seviye, city_id, county_id, district_id, dagilim_turu, segment)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS poi_noktalari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_id INTEGER, county_id INTEGER, bolge_adi TEXT,
        poi_id INTEGER, kategori_id INTEGER, alt_kategori TEXT, poi_adi TEXT, slug TEXT,
        UNIQUE(county_id, poi_id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS emlak_ofisleri (id INTEGER PRIMARY KEY AUTOINCREMENT, city_id INTEGER, il_adi TEXT, ofis_id INTEGER, ofis_adi TEXT, slug TEXT, adres TEXT, telefon TEXT, danisman_sayisi INTEGER, ilan_sayisi INTEGER, UNIQUE(city_id, ofis_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS danismanlar (id INTEGER PRIMARY KEY AUTOINCREMENT, city_id INTEGER, il_adi TEXT, danisman_id TEXT, danisman_adi TEXT, unvan TEXT, ofis_adi TEXT, telefon TEXT, aktif_ilan_sayisi INTEGER, UNIQUE(city_id, danisman_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS sirketler (id INTEGER PRIMARY KEY AUTOINCREMENT, sirket_id INTEGER UNIQUE, sirket_adi TEXT, slug TEXT)""")

    conn.commit()
    conn.close()

def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {"tamamlanan_iller": [], "tamamlanan_ilceler": [], "tamamlanan_mahalleler": [], "sirketler_alindi": False}

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def save_demografi(conn, seviye, city_id, county_id, district_id, bolge_adi, data):
    if not data or not isinstance(data, dict): return
    demo = data.get("Demography") or {}
    if not demo: return

    now = datetime.now().isoformat()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO demografi (
        seviye, city_id, county_id, district_id, bolge_adi,
        nufus_toplam, nufus_erkek, nufus_kadin, hane_sayisi,
        ortalama_hane_geliri, toplam_hane_geliri, ev_sahibi_orani, kiraci_orani,
        ses_a_plus, ses_a, ses_b, ses_c, ses_d,
        ses_a_plus_oran, ses_a_oran, ses_b_oran, ses_c_oran, ses_d_oran,
        egitim_universite_oran, egitim_lise_oran, egitim_ortaokul_oran, egitim_ilkokul_oran,
        yas_genc_oran, yas_orta_oran, yas_yasli_oran,
        konut_fiyat_m2, kira_fiyat_m2, arsa_fiyat_m2, tarla_fiyat_m2, ticari_fiyat_m2,
        eczane_sayisi, atm_sayisi, banka_sube_sayisi, arac_sayisi,
        ham_json, guncellenme_tarihi
    ) VALUES (
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?
    )""", (
        seviye, city_id, county_id, district_id, bolge_adi,
        demo.get("PopulationTotal"), demo.get("PopulationMale"), demo.get("PopulationFemale"), demo.get("HouseholdCount"),
        demo.get("HouseIncome"), demo.get("HouseIncomeTotal"), demo.get("OwnerShare"), demo.get("RentedShare"),
        demo.get("SesGroupAPlus"), demo.get("SesGroupA"), demo.get("SesGroupB"), demo.get("SesGroupC"), demo.get("SesGroupD"),
        demo.get("SesGroupAPlusRatio"), demo.get("SesGroupARatio"), demo.get("SesGroupBRatio"), demo.get("SesGroupCRatio"), demo.get("SesGroupDRatio"),
        demo.get("EduLicenseDegreeRatio"), demo.get("EduHighSchoolratio"), demo.get("EduMiddleSchoolRatio"), demo.get("EduPrimarySchoolRatio"),
        demo.get("PopulationYoungRatio"), demo.get("PopulationMiddleRatio"), demo.get("PopulationElderRatio"),
        demo.get("HouseUnitPriceForSale"), demo.get("HouseUnitPriceForRent"), demo.get("PlotUnitPriceForSale"), demo.get("LandUnitPriceForSale"), demo.get("CommercialUnitPriceForSale"),
        demo.get("PharmacyCount"), demo.get("AtmCount"), demo.get("BankBranchCount"), demo.get("VehicleCount") or demo.get("CarCount"),
        json.dumps(demo, ensure_ascii=False), now
    ))

    for yil in range(2010, 2025):
        satis = demo.get(f"Total_BB_Sale_{yil}")
        ipotekli = demo.get(f"Total_BBMortgaged_Sale_{yil}")
        arsa = demo.get(f"Total_AT_Sale_{yil}")
        ipotekli_arsa = demo.get(f"Total_ATMorgaged_Sale_{yil}")
        ilan = demo.get(f"Total_Listing_{yil}")
        if any(v is not None for v in (satis, ipotekli, arsa, ipotekli_arsa, ilan)):
            cur.execute("""
            INSERT OR REPLACE INTO yillik_satislar (
                seviye, city_id, county_id, district_id, bolge_adi, yil,
                toplam_konut_satisi, ipotekli_konut_satisi, arsa_arazi_satisi, ipotekli_arsa_satisi, toplam_ilan_sayisi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (seviye, city_id, county_id, district_id, bolge_adi, yil, satis, ipotekli, arsa, ipotekli_arsa, ilan))
    conn.commit()

def save_hemsehri(conn, seviye, city_id, county_id, district_id, bolge_adi, data):
    if not data or not isinstance(data, dict): return
    liste = data.get("FellowCountryman")
    if not isinstance(liste, list): return
    now = datetime.now().isoformat()
    cur = conn.cursor()
    for item in liste:
        sehir = item.get("CitizenCity")
        adet = item.get("CountOf")
        if sehir and adet is not None:
            cur.execute("""
            INSERT OR REPLACE INTO hemsehri (
                seviye, city_id, county_id, district_id, bolge_adi, kutuk_ili, kisi_sayisi, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (seviye, city_id, county_id, district_id, bolge_adi, sehir, adet, now))
    conn.commit()

def save_secim(conn, seviye, city_id, county_id, district_id, bolge_adi, data):
    if not data or not isinstance(data, list): return
    cur = conn.cursor()
    for s in data:
        kod = s.get("Code")
        baslik = s.get("Title")
        secenekler = s.get("Secenekler") or []
        kazanan = None
        if secenekler:
            sirali = sorted(secenekler, key=lambda x: x.get("OySayisi") or 0, reverse=True)
            if sirali: kazanan = sirali[0].get("Secenek")
        if kod:
            cur.execute("""
            INSERT OR REPLACE INTO secim_sonuclari (
                seviye, city_id, county_id, district_id, bolge_adi,
                secim_kodu, secim_adi, sandik_sayisi, kayitli_secmen,
                kullanilan_oy, gecerli_oy, kazanan_parti, parti_sonuclari_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                seviye, city_id, county_id, district_id, bolge_adi,
                kod, baslik, s.get("SandikSayisi"), s.get("KayitliSecmen"),
                s.get("KullanilanOy"), s.get("GecerliOy"), kazanan,
                json.dumps(secenekler, ensure_ascii=False)
            ))
    conn.commit()

def save_fiyat_ve_kirilimlar(conn, kategori, seviye, city_id, county_id, district_id, bolge_adi, data):
    if not data or not isinstance(data, dict): return
    now = datetime.now().isoformat()
    cur = conn.cursor()

    genel = (data.get("General") or [{}])[0] if isinstance(data.get("General"), list) and data.get("General") else {}
    if not genel:
        trend = data.get("Trend") or []
        if trend: genel = trend[-1]

    if genel:
        donem = f"{genel.get('PropertyYear')}-{str(genel.get('PropertyMonth', '')).zfill(2)}"
        cur.execute("""
        INSERT OR REPLACE INTO fiyat_ozet (
            kategori, seviye, city_id, county_id, district_id, bolge_adi, donem,
            satilik_m2_fiyat, kiralik_m2_fiyat, ortalama_fiyat,
            amortisman_yil, brut_kira_getirisi, ortalama_bina_yasi,
            satilik_kalma_suresi_gun, kiralik_kalma_suresi_gun,
            ilan_sayisi, yillik_fiyat_degisim, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            kategori, seviye, city_id, county_id, district_id, bolge_adi, donem,
            genel.get("UnitPriceForSale"), genel.get("UnitPriceForRent"), genel.get("PriceForSale"),
            genel.get("Amortization"), genel.get("Yield"), genel.get("AverageAgeForSale"),
            genel.get("ListingPeriodForSale"), genel.get("ListingPeriodForRent"),
            genel.get("CountForSale"), genel.get("UnitPriceSaleAnnualChange"), now
        ))

    for r in data.get("Trend") or []:
        y, m = r.get("PropertyYear"), r.get("PropertyMonth")
        if not y or not m: continue
        ay = f"{y}-{str(m).zfill(2)}"
        cur.execute("""
        INSERT OR REPLACE INTO fiyat_trend (
            kategori, seviye, city_id, county_id, district_id, bolge_adi, ay,
            satilik_m2_fiyat, kiralik_m2_fiyat, ortalama_fiyat,
            ilan_sayisi, amortisman_yil, brut_kira_getirisi, projeksiyon
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            kategori, seviye, city_id, county_id, district_id, bolge_adi, ay,
            r.get("UnitPriceForSale"), r.get("UnitPriceForRent"), r.get("PriceForSale"),
            r.get("CountForSale"), r.get("Amortization"), r.get("Yield"),
            1 if r.get("AnalysisType") == "Projection" else 0
        ))

    kirilim_haritasi = {"oda": data.get("HouseType"), "yas": data.get("Age"), "kat": data.get("FloorSergment"), "isitma": data.get("Heating")}
    for dagilim_turu, liste in kirilim_haritasi.items():
        for r in (liste or []):
            segment = r.get("ListingType") or r.get("DisplayName")
            if not segment: continue
            cur.execute("""
            INSERT OR REPLACE INTO fiyat_dagilim (
                kategori, seviye, city_id, county_id, district_id, bolge_adi,
                dagilim_turu, segment, oran,
                satilik_m2_fiyat, kiralik_m2_fiyat, ortalama_fiyat,
                ilan_sayisi, amortisman_yil
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kategori, seviye, city_id, county_id, district_id, bolge_adi,
                dagilim_turu, str(segment), r.get("CountForSaleRatio"),
                r.get("UnitPriceForSale"), r.get("UnitPriceForRent"), r.get("PriceForSale"),
                r.get("CountForSale"), r.get("Amortization")
            ))
    conn.commit()

def save_poi(conn, city_id, county_id, bolge_adi, data):
    if not data or not isinstance(data, dict): return
    pois = data.get("result") or []
    cur = conn.cursor()
    for p in pois:
        pid = p.get("id")
        if not pid: continue
        cur.execute("INSERT OR REPLACE INTO poi_noktalari (city_id, county_id, bolge_adi, poi_id, kategori_id, alt_kategori, poi_adi, slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (city_id, county_id, bolge_adi, pid, p.get("categoryId"), p.get("subCategoryName"), p.get("name"), p.get("slug")))
    conn.commit()

def save_ofis_ve_danismanlar(conn, city_id, il_adi, ofis_data, danisman_data):
    cur = conn.cursor()
    if ofis_data and isinstance(ofis_data, dict):
        for o in ofis_data.get("Results") or []:
            oid = o.get("Id")
            if not oid: continue
            cur.execute("INSERT OR REPLACE INTO emlak_ofisleri (city_id, il_adi, ofis_id, ofis_adi, slug, adres, telefon, danisman_sayisi, ilan_sayisi) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (city_id, il_adi, oid, o.get("Name"), o.get("Slug"), o.get("LocationSummary"), o.get("PhoneNumber"), o.get("RegionCount"), o.get("ListingCount")))

    if danisman_data and isinstance(danisman_data, dict):
        for d in danisman_data.get("Results") or []:
            did = str(d.get("Id", ""))
            if not did: continue
            cur.execute("INSERT OR REPLACE INTO danismanlar (city_id, il_adi, danisman_id, danisman_adi, unvan, ofis_adi, telefon, aktif_ilan_sayisi) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (city_id, il_adi, did, f"{d.get('Name', '')} {d.get('Surname', '')}".strip(), d.get("DistrictName"), d.get("OfficeName"), d.get("PhoneNumber"), d.get("Last1MonthListingCount")))
    conn.commit()

def save_sirketler(conn, data):
    if not data or not isinstance(data, dict): return
    cur = conn.cursor()
    for s in data.get("companyList") or []:
        sid = s.get("id")
        if sid: cur.execute("INSERT OR IGNORE INTO sirketler (sirket_id, sirket_adi, slug) VALUES (?, ?, ?)", (sid, s.get("name"), s.get("slug")))
    conn.commit()

def save_polygons(city_id, county_id, bolge_adi, data):
    if not data or not isinstance(data, dict): return
    polygons = data.get("polygons")
    if not polygons: return
    dosya_adi = f"city_{city_id}.json" if county_id == 0 else f"county_{city_id}_{county_id}.json"
    hedef = POLYGONS_DIR / dosya_adi
    with open(hedef, "w", encoding="utf-8") as f:
        json.dump({"city_id": city_id, "county_id": county_id, "bolge_adi": bolge_adi, "polygons": polygons}, f, ensure_ascii=False)

def main():
    parser = argparse.ArgumentParser(description="Tüm Türkiye İl, İlçe ve Mahalle Veri Toplayıcısı")
    parser.add_argument("--iller", type=str, default="hepsi", help="İl plakaları/id (örn: 34,6,35 veya 'hepsi')")
    parser.add_argument("--hiz", type=float, default=1.0, help="İstekler arası temel bekleme (saniye)")
    parser.add_argument("--mahalle", action="store_true", default=True, help="Mahalle detaylarına (Level=3) in (Varsayılan: Açık)")
    parser.add_argument("--mahallesiz", dest="mahalle", action="store_false", help="Mahalle detaylarını atla, sadece ilçe ve il topla")
    parser.add_argument("--sadece-il", action="store_true", help="Sadece 81 il geneli özetlerini çeker")
    parser.add_argument("--sifirla", action="store_true", help="Önceki tarama geçmişini sıfırlar")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POLYGONS_DIR.mkdir(parents=True, exist_ok=True)
    if args.sifirla:
        if STATE_PATH.exists(): STATE_PATH.unlink()
        if DB_PATH.exists(): DB_PATH.unlink()
        log("İlerleme durumu ve eski veritabanı sıfırlandı.", "WARN")

    init_db(DB_PATH)
    state = load_state()
    http = PoliteHTTP(base_delay=args.hiz)
    conn = sqlite3.connect(DB_PATH)

    log(f"Toplayıcı başlatıldı | Mahalle detayları: {'AÇIK' if args.mahalle else 'KAPALI'} | Bekleme: {args.hiz}s", "INFO")

    # 1. Şirketler
    if not state.get("sirketler_alindi"):
        log("İnşaat şirketleri listesi alınıyor...", "INFO")
        sirket_data = http.get(API_EJ_COMPANIES)
        if sirket_data:
            save_sirketler(conn, sirket_data)
            state["sirketler_alindi"] = True
            save_state(state)

    # 2. 81 İl Keşfi
    log("Türkiye il listesi alınıyor...", "INFO")
    ulke_res = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=9&PropertyCategory=1&PropertyType=4&Rooms=5&Static=true&Trend=false&Types=false&Wkt=")
    if not ulke_res or not ulke_res.get("Static"):
        log("İl listesi alınamadı.", "ERROR")
        return

    iller = sorted(ulke_res["Static"], key=lambda x: int(x.get("CityId", 0)))
    cur = conn.cursor()
    now_iso = datetime.now().isoformat()
    for il in iller:
        cur.execute("INSERT OR IGNORE INTO iller (city_id, city_name, created_at) VALUES (?, ?, ?)", (il["CityId"], il["CityName"], now_iso))
    conn.commit()

    if args.iller and args.iller != "hepsi":
        secili_id = set(int(x.strip()) for x in args.iller.split(",") if x.strip().isdigit())
        iller = [il for il in iller if int(il["CityId"]) in secili_id]
        log(f"Filtre uygulandı: Sadece {len(iller)} il taranacak.", "INFO")

    toplam_il = len(iller)
    for il_idx, il in enumerate(iller, 1):
        city_id = int(il["CityId"])
        city_name = il["CityName"]
        il_anahtar = f"il_{city_id}"

        log(f"[{il_idx}/{toplam_il}] {city_name} (ID: {city_id}) taranıyor...", "INFO")

        # A) İL GENELİ
        if il_anahtar not in state["tamamlanan_iller"]:
            d = http.get(f"{API_EJ_DEMO}?CountryId=1&Level=1&CityId={city_id}")
            if d: save_demografi(conn, "il", city_id, 0, 0, city_name, d)

            h = http.post_json(API_END_FELLOW, {"CountryId": 1, "Level": 1, "CityId": city_id})
            if h: save_hemsehri(conn, "il", city_id, 0, 0, city_name, h)

            s = http.post_json(API_END_ELECTION, {"CountryId": 1, "Level": 1, "CityId": city_id})
            if s: save_secim(conn, "il", city_id, 0, 0, city_name, s)

            f_konut = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=1&CityId={city_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=false&Trend=true&Types=true&Wkt=")
            if f_konut: save_fiyat_ve_kirilimlar(conn, "konut", "il", city_id, 0, 0, city_name, f_konut)

            f_arsa = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=1&CityId={city_id}&PropertyCategory=3&PropertyType=1&Rooms=5&Static=false&Trend=true&Types=true&Wkt=")
            if f_arsa: save_fiyat_ve_kirilimlar(conn, "arsa", "il", city_id, 0, 0, city_name, f_arsa)

            ofisler = http.get(f"{API_EJ_OFFICES}?city={city_id}")
            danismanlar = http.get(f"{API_EJ_AGENTS}?city={city_id}")
            save_ofis_ve_danismanlar(conn, city_id, city_name, ofisler, danismanlar)

            poly = http.get(f"{API_EJ_POLYGONS}?level=1&cityId={city_id}")
            if poly: save_polygons(city_id, 0, city_name, poly)

            state["tamamlanan_iller"].append(il_anahtar)
            save_state(state)
            log(f"  ✓ {city_name} il geneli tamamlandı.", "SUCCESS")

        if args.sadece_il: continue

        # B) İLÇE DÜZEYİ
        ilce_res = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=1&CityId={city_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=true&Trend=false&Types=false&Wkt=")
        ilceler = ilce_res.get("Static", []) if ilce_res else []
        log(f"  {city_name}: {len(ilceler)} ilçe taranıyor...", "INFO")

        for ilce in ilceler:
            county_id = int(ilce.get("CountyId", 0))
            county_name = ilce.get("CountyName") or ilce.get("DisplayName", "")
            if not county_id: continue

            cur.execute("INSERT OR IGNORE INTO ilceler (county_id, city_id, county_name, created_at) VALUES (?, ?, ?, ?)", (county_id, city_id, county_name, now_iso))
            conn.commit()

            ilce_anahtar = f"ilce_{city_id}_{county_id}"
            bolge_tam = f"{city_name} - {county_name}"

            if ilce_anahtar not in state["tamamlanan_ilceler"]:
                d = http.get(f"{API_EJ_DEMO}?CountryId=1&Level=2&CityId={city_id}&CountyId={county_id}")
                if d: save_demografi(conn, "ilce", city_id, county_id, 0, bolge_tam, d)

                h = http.post_json(API_END_FELLOW, {"CountryId": 1, "Level": 2, "CityId": city_id, "CountyId": county_id})
                if h: save_hemsehri(conn, "ilce", city_id, county_id, 0, bolge_tam, h)

                s = http.post_json(API_END_ELECTION, {"CountryId": 1, "Level": 2, "CityId": city_id, "CountyId": county_id})
                if s: save_secim(conn, "ilce", city_id, county_id, 0, bolge_tam, s)

                f_konut = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=2&CityId={city_id}&CountyId={county_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=false&Trend=true&Types=true&Wkt=")
                if f_konut: save_fiyat_ve_kirilimlar(conn, "konut", "ilce", city_id, county_id, 0, bolge_tam, f_konut)

                f_arsa = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=2&CityId={city_id}&CountyId={county_id}&PropertyCategory=3&PropertyType=1&Rooms=5&Static=false&Trend=true&Types=true&Wkt=")
                if f_arsa: save_fiyat_ve_kirilimlar(conn, "arsa", "ilce", city_id, county_id, 0, bolge_tam, f_arsa)

                poi_data = http.get(API_EJ_POI.format(county_id=county_id))
                if poi_data: save_poi(conn, city_id, county_id, bolge_tam, poi_data)

                poly = http.get(f"{API_EJ_POLYGONS}?level=2&cityId={city_id}&countyId={county_id}")
                if poly: save_polygons(city_id, county_id, bolge_tam, poly)

                state["tamamlanan_ilceler"].append(ilce_anahtar)
                save_state(state)
                log(f"    ✓ {county_name} ilçe geneli tamamlandı.", "SUCCESS")

            # C) MAHALLE DÜZEYİ (Level=3)
            # İlçenin içindeki tüm mahalle listesini alıyoruz
            mahalle_res = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=2&CityId={city_id}&CountyId={county_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=true&Trend=false&Types=false&Wkt=")
            mahalleler = mahalle_res.get("Static", []) if mahalle_res else []

            for m in mahalleler:
                dist_id = int(m.get("DistrictId", 0))
                dist_name = m.get("DistrictName") or m.get("DisplayName", "")
                if not dist_id: continue

                cur.execute("INSERT OR IGNORE INTO mahalleler (district_id, county_id, city_id, district_name, created_at) VALUES (?, ?, ?, ?, ?)",
                            (dist_id, county_id, city_id, dist_name, now_iso))
                conn.commit()

                # Anlık mahalle fiyatını Static satırından da hemen fiyat_ozet'e kaydedelim (ekstra istek gerekmeden)
                now_t = datetime.now().isoformat()
                donem_t = f"{m.get('PropertyYear')}-{str(m.get('PropertyMonth', '')).zfill(2)}" if m.get('PropertyYear') else "guncel"
                cur.execute("""
                INSERT OR REPLACE INTO fiyat_ozet (
                    kategori, seviye, city_id, county_id, district_id, bolge_adi, donem,
                    satilik_m2_fiyat, kiralik_m2_fiyat, ortalama_fiyat,
                    amortisman_yil, brut_kira_getirisi, ortalama_bina_yasi,
                    satilik_kalma_suresi_gun, kiralik_kalma_suresi_gun,
                    ilan_sayisi, yillik_fiyat_degisim, guncellenme_tarihi
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "konut", "mahalle", city_id, county_id, dist_id, f"{bolge_tam} - {dist_name}", donem_t,
                    m.get("UnitPriceForSale"), m.get("UnitPriceForRent"), m.get("PriceForSale"),
                    m.get("Amortization"), m.get("Yield"), m.get("AverageAgeForSale"),
                    m.get("ListingPeriodForSale"), m.get("ListingPeriodForRent"),
                    m.get("CountForSale"), m.get("UnitPriceSaleAnnualChange"), now_t
                ))
                conn.commit()

                if not args.mahalle:
                    continue

                mahalle_anahtar = f"mah_{city_id}_{county_id}_{dist_id}"
                if mahalle_anahtar in state.get("tamamlanan_mahalleler", []):
                    continue

                mahalle_tam = f"{bolge_tam} - {dist_name}"

                # Mahalle Demografi (Nüfus, SES, Gelir)
                d_mah = http.get(f"{API_EJ_DEMO}?CountryId=1&Level=3&CityId={city_id}&CountyId={county_id}&DistrictId={dist_id}")
                if d_mah: save_demografi(conn, "mahalle", city_id, county_id, dist_id, mahalle_tam, d_mah)

                # Mahalle Fiyat & Trend Serisi
                f_mah = http.get(f"{API_EJ_TREND}?BuildYear=5&CountryId=1&Details=true&FloorNumber=5&HeatType=3&Level=3&CityId={city_id}&CountyId={county_id}&DistrictId={dist_id}&PropertyCategory=1&PropertyType=4&Rooms=5&Static=false&Trend=true&Types=true&Wkt=")
                if f_mah: save_fiyat_ve_kirilimlar(conn, "konut", "mahalle", city_id, county_id, dist_id, mahalle_tam, f_mah)

                # Mahalle Hemşehri Dağılımı
                h_mah = http.post_json(API_END_FELLOW, {"CountryId": 1, "Level": 3, "CityId": city_id, "CountyId": county_id, "DistrictId": dist_id})
                if h_mah: save_hemsehri(conn, "mahalle", city_id, county_id, dist_id, mahalle_tam, h_mah)

                # Mahalle Seçim Sonuçları
                s_mah = http.post_json(API_END_ELECTION, {"CountryId": 1, "Level": 3, "CityId": city_id, "CountyId": county_id, "DistrictId": dist_id})
                if s_mah: save_secim(conn, "mahalle", city_id, county_id, dist_id, mahalle_tam, s_mah)

                if "tamamlanan_mahalleler" not in state: state["tamamlanan_mahalleler"] = []
                state["tamamlanan_mahalleler"].append(mahalle_anahtar)
                save_state(state)
                log(f"      • {dist_name} mahallesi detayları kaydedildi.", "INFO")

    conn.close()
    log("Tüm hedeflenen tarama başarıyla tamamlandı!", "SUCCESS")
    log(f"Veritabanı konumu: {DB_PATH}", "INFO")

if __name__ == "__main__":
    main()
