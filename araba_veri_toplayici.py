#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Standartlaştırılmış & Temiz Ayrıştırılmış Araç Verisi Toplayıcısı
=============================================================================
Arabam.com üzerindeki taşıt verilerini:
1. KVKK ve gizlilik standartlarına tam uyumlu olarak (İlan No, URL, Başlık, Satıcı,
   Telefon, Yetki Belgesi, Görseller ve Açıklama Metni OLMADAN) toplar.
2. Tüm verileri daha sonra veri analizi, Excel filtreleme, SQL sorguları ve makine
   öğrenmesi / değerleme modellerinde doğrudan kullanılabilmesi için TİP DÖNÜŞÜMLÜ
   ve STANDARTLAŞTIRILMIŞ olarak ayrıştırır:
   - Tarihler: ISO 'YYYY-MM-DD'
   - Sayısal Alanlar: Temiz INTEGER ve FLOAT (KM, Fiyat, CC, HP, Tüketim, Depo)
   - 13 Ayrı Ekspertiz Kolonu: hasar_kaput, hasar_tavan, hasar_bagaj, vb.
   - Hasar İstatistikleri: boyali_parca_sayisi, degisen_parca_sayisi, tamami_orijinal
3. GitHub Actions 40 sanal makine paralel matrisi ile 81 ili dakikalar içinde tarar.
"""

import os
import sys
import re
import json
import html as html_lib
import time
import sqlite3
import argparse
import hashlib
import concurrent.futures
from pathlib import Path
from datetime import datetime

try:
    from curl_cffi import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Gereksinimler eksik! 'pip install curl_cffi beautifulsoup4' çalıştırın.")
    sys.exit(1)

# Dizin Yolları
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "collector" / "data" if (BASE_DIR / "collector").exists() else BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "arabam_vasita_piyasasi.sqlite"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

LUXURY_BRANDS = {
    "bmw", "mercedes", "mercedes-benz", "audi", "porsche", "volvo", 
    "land rover", "range rover", "jaguar", "lexus", "maserati", "mini", 
    "alfa romeo", "ds", "tesla", "bentley", "ferrari", "lamborghini", "aston martin"
}
ECONOMY_BRANDS = {
    "fiat", "renault", "dacia", "hyundai", "tata", "lada", "tofaş", 
    "chery", "geely", "mg", "citroen", "peugeot"
}

# Desteklenen Tüm Kategoriler ve URL Eşleşmeleri
CATEGORY_MAP = {
    "otomobil": "otomobil",
    "oto": "otomobil",
    "arazi": "arazi-suv-pick-up",
    "suv": "arazi-suv-pick-up",
    "pickup": "arazi-suv-pick-up",
    "pick-up": "arazi-suv-pick-up",
    "arazi-suv-pickup": "arazi-suv-pick-up",
    "arazi-suv-pick-up": "arazi-suv-pick-up",
    "minivan": "minivan-panelvan",
    "panelvan": "minivan-panelvan",
    "van": "minivan-panelvan",
    "minivan-panelvan": "minivan-panelvan",
    "minivan-van-panelvan": "minivan-panelvan",
    "minivan-van_panelvan": "minivan-panelvan",
    "ticari": "ticari-arac",
    "ticari-arac": "ticari-arac",
    "ticari-araclar": "ticari-arac",
    "hepsi": ["otomobil", "arazi-suv-pick-up", "minivan-panelvan"]
}

# Varsayılan olarak tüm binek, SUV ve hafif ticari kategorileri
DEFAULT_CATEGORIES = ["otomobil", "arazi-suv-pick-up", "minivan-panelvan"]

PART_ID_MAP = {
    "B01001": "hasar_kaput",
    "B0601": "hasar_tavan",
    "B0201": "hasar_bagaj",
    "B01201": "hasar_on_tampon",
    "B01301": "hasar_arka_tampon",
    "B0501": "hasar_sag_on_camurluk",
    "B01101": "hasar_sol_on_camurluk",
    "B0401": "hasar_sag_on_kapi",
    "B0801": "hasar_sol_on_kapi",
    "B0301": "hasar_sag_arka_kapi",
    "B0701": "hasar_sol_arka_kapi",
    "B0101": "hasar_sag_arka_camurluk",
    "B0901": "hasar_sol_arka_camurluk"
}

AY_SOZLUGU = {
    'ocak': '01', 'şubat': '02', 'mart': '03', 'nisan': '04', 'mayıs': '05', 'haziran': '06',
    'temmuz': '07', 'ağustos': '08', 'eylül': '09', 'ekim': '10', 'kasım': '11', 'aralık': '12'
}

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "[*]", "SUCCESS": "[✓]", "WARN": "[!]", "ERROR": "[✗]"}
    print(f"{ts} {symbols.get(level, '[*]')} {msg}", flush=True)

# -------------------------------------------------------------
# YARDIMCI VE STANDARTLAŞTIRMA PARSER FONKSİYONLARI
# -------------------------------------------------------------

def parse_iso_tarih(tarih_metni, raw_kod=None):
    """Metin veya sayısal tarihi standart 'YYYY-MM-DD' ISO formatına dönüştürür."""
    if raw_kod and len(str(raw_kod)) == 8 and str(raw_kod).isdigit():
        rk = str(raw_kod)
        return f"{rk[:4]}-{rk[4:6]}-{rk[6:8]}"
    
    parts = str(tarih_metni).strip().split()
    if len(parts) >= 3 and parts[0].isdigit() and parts[2].isdigit():
        gun = f"{int(parts[0]):02d}"
        ay = AY_SOZLUGU.get(parts[1].lower(), "01")
        yil = parts[2]
        return f"{yil}-{ay}-{gun}"
    
    return datetime.now().strftime("%Y-%m-%d")

def parse_integer(val, default=0):
    """Rakam dışındaki karakterleri temizleyerek güvenli tam sayı üretir."""
    if not val:
        return default
    temiz = re.sub(r"[^\d]", "", str(val))
    return int(temiz) if temiz else default

def parse_float(val, default=0.0):
    """Ondalıklı tüketim vb. metinlerini temiz float'a çevirir."""
    if not val:
        return default
    m = re.search(r"([\d]+(?:[\.,]\d+)?)", str(val))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return default

def parse_motor_cc(val):
    """'1598 cc' veya '1401 - 1600 cm3' metinlerinden üst CC değerini alır."""
    if not val:
        return None
    temiz = re.sub(r"(?:cm3|cc|lt|hp|kw)", "", str(val), flags=re.IGNORECASE)
    sayilar = [int(x) for x in re.findall(r"\b\d+\b", temiz) if int(x) > 100]
    if len(sayilar) >= 2:
        return sayilar[-1] # Aralık ise üst sınırı al
    elif len(sayilar) == 1:
        return sayilar[0]
    return None

def parse_motor_hp(val):
    """'120 hp' veya '101 - 125 HP' metinlerinden beygir gücünü tam sayı alır."""
    if not val:
        return None
    temiz = re.sub(r"(?:hp|bg|kw|ps)", "", str(val), flags=re.IGNORECASE)
    sayilar = [int(x) for x in re.findall(r"\b\d+\b", temiz) if int(x) > 10]
    if len(sayilar) >= 2:
        return sayilar[-1]
    elif len(sayilar) == 1:
        return sayilar[0]
    return None

def standardize_kasa(kasa_str):
    """'Hatchback/5' veya 'Sedan' metinlerini standart ana kasa tiplerine normalize eder."""
    if not kasa_str:
        return "Bilinmeyen"
    k = kasa_str.lower()
    if "hatchback" in k: return "Hatchback"
    if "sedan" in k: return "Sedan"
    if "suv" in k or "arazi" in k: return "SUV"
    if "station" in k: return "Station Wagon"
    if "coupe" in k: return "Coupe"
    if "cabrio" in k: return "Cabrio"
    if "minivan" in k or "panelvan" in k: return "Minivan/Van"
    return kasa_str.strip()

def init_db(db_path=None):
    """Veritabanını analitik sorgulamaya hazır, 13 hasar kolonu içeren şemayla kurar."""
    target_db = db_path or DB_PATH
    conn = sqlite3.connect(target_db)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS arabam_ilanlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        arac_id TEXT UNIQUE,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        tam_konum TEXT,
        kategori TEXT,
        marka TEXT,
        seri TEXT,
        model TEXT,
        breadcrumb TEXT,
        segment TEXT,
        refah_segmenti TEXT,
        yil INTEGER,
        km INTEGER,
        vites_tipi TEXT,
        yakit_tipi TEXT,
        kasa_tipi TEXT,
        renk TEXT,
        motor_hacmi_cc INTEGER,
        motor_gucu_hp INTEGER,
        cekis TEXT,
        arac_durumu TEXT,
        ort_yakit_tuketimi REAL,
        yakit_deposu_lt INTEGER,
        agir_hasarli INTEGER DEFAULT 0, -- 1: Evet, 0: Hayır
        tamami_orijinal INTEGER DEFAULT 0, -- 1: Evet, 0: Hayır
        boyali_parca_sayisi INTEGER DEFAULT 0,
        degisen_parca_sayisi INTEGER DEFAULT 0,
        lokal_boyali_parca_sayisi INTEGER DEFAULT 0,
        boya_degisen_ozet TEXT,
        -- 13 Parça Hasar Detayları
        hasar_kaput TEXT DEFAULT 'Belirtilmemiş',
        hasar_tavan TEXT DEFAULT 'Belirtilmemiş',
        hasar_bagaj TEXT DEFAULT 'Belirtilmemiş',
        hasar_on_tampon TEXT DEFAULT 'Belirtilmemiş',
        hasar_arka_tampon TEXT DEFAULT 'Belirtilmemiş',
        hasar_sag_on_camurluk TEXT DEFAULT 'Belirtilmemiş',
        hasar_sol_on_camurluk TEXT DEFAULT 'Belirtilmemiş',
        hasar_sag_on_kapi TEXT DEFAULT 'Belirtilmemiş',
        hasar_sol_on_kapi TEXT DEFAULT 'Belirtilmemiş',
        hasar_sag_arka_kapi TEXT DEFAULT 'Belirtilmemiş',
        hasar_sol_arka_kapi TEXT DEFAULT 'Belirtilmemiş',
        hasar_sag_arka_camurluk TEXT DEFAULT 'Belirtilmemiş',
        hasar_sol_arka_camurluk TEXT DEFAULT 'Belirtilmemiş',
        fiyat_tl INTEGER,
        para_birimi TEXT DEFAULT 'TL',
        takasa_uygun INTEGER DEFAULT 0, -- 1: Evet, 0: Hayır
        satici_tipi TEXT, -- 'Galeriden', 'Sahibinden', 'Yetkili Bayi'
        ilan_tarihi TEXT, -- ISO 'YYYY-MM-DD'
        guncellenme_yili INTEGER DEFAULT 2026,
        veri_donemi TEXT DEFAULT '2026-Q3 (Güncel)',
        eklenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # İlçe Bazlı Araç Refah Endeksi Tablosu
    c.execute("""
    CREATE TABLE IF NOT EXISTS ilce_arac_refah_endeksi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT,
        ilce TEXT,
        toplam_ilan_sayisi INTEGER,
        ortalama_fiyat_tl REAL,
        medyan_fiyat_tl REAL,
        ortalama_arac_yasi REAL,
        ortalama_km REAL,
        luks_arac_orani_yuzde REAL,
        ekonomik_arac_orani_yuzde REAL,
        en_populer_marka TEXT,
        refah_ve_alim_gucu_skoru REAL,
        sosyo_ekonomik_kategori TEXT,
        veri_donemi TEXT DEFAULT '2026-Q3 (Güncel)',
        guncellenme_yili INTEGER DEFAULT 2026,
        UNIQUE(il, ilce)
    )
    """)

    conn.commit()
    conn.close()

class AnonimArabaToplayici:
    """Yalnızca analitik taşıt verilerini temiz ayrıştırarak toplayan motor."""

    def __init__(self, db_path=None, max_threads=6):
        self.db_path = db_path or DB_PATH
        init_db(self.db_path)
        self.max_threads = max_threads
        self.session = requests.Session(impersonate="chrome120")
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Referer": "https://www.arabam.com/",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        self._warmup_session()
        self.mevcut_araclar = self._mevcut_arac_idleri_yukle()

    def _warmup_session(self):
        """Oturumu ana sayfaya bağlanarak başlatır ve Cloudflare / WAF çerezlerini alır."""
        for deneme in range(3):
            try:
                r = self.session.get("https://www.arabam.com/", headers=self.headers, timeout=15)
                if r.status_code == 200:
                    log(f"Oturum ve WAF çerezleri başarıyla yüklendi ({len(self.session.cookies)} adet).", "SUCCESS")
                    return True
                else:
                    log(f"İlk oturum bağlantısı HTTP {r.status_code} (Deneme {deneme+1}/3)", "WARN")
                    time.sleep(1.5 * (deneme + 1))
            except Exception as e:
                log(f"İlk oturum çerez yükleme uyarısı (Deneme {deneme+1}/3): {e}", "WARN")
                time.sleep(1.0)
        return False

    def _mevcut_arac_idleri_yukle(self):
        """Daha önce kaydedilmiş anonim araç ID'lerini küme olarak yükler."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT arac_id FROM arabam_ilanlari WHERE arac_id IS NOT NULL")
        rows = {r[0] for r in c.fetchall() if r[0]}
        conn.close()
        return rows

    def arama_sayfasindan_ilan_linkleri_al(self, kategori="otomobil", plaka=None, sayfa=1):
        """Arama sayfasındaki araç linklerini ve anonim ID'lerini tespit eder (yeniden deneme korumalı)."""
        if plaka:
            url = f"https://www.arabam.com/ikinci-el/{kategori}?city={plaka}&page={sayfa}"
        else:
            url = f"https://www.arabam.com/ikinci-el/{kategori}?page={sayfa}"

        for deneme in range(3):
            try:
                r = self.session.get(url, headers=self.headers, timeout=15)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    rows = soup.select("tr.listing-list-item")
                    ilan_linkleri = []

                    for row in rows:
                        link_el = row.find("a", href=True)
                        if not link_el:
                            continue
                        href = link_el["href"]
                        m = re.search(r"/(\d+)(?:\?|$)", href)
                        ilan_no = m.group(1) if m else ""
                        if not ilan_no:
                            continue

                        arac_id = hashlib.sha256(ilan_no.encode()).hexdigest()[:16]
                        tam_url = f"https://www.arabam.com{href}" if href.startswith("/") else href
                        ilan_linkleri.append((arac_id, tam_url))

                    return ilan_linkleri
                elif r.status_code in (403, 429):
                    log(f"Arama sayfası HTTP {r.status_code} (Deneme {deneme+1}/3): {url}", "WARN")
                    time.sleep(2.0 * (deneme + 1))
                    self._warmup_session()
                else:
                    log(f"Arama sayfası HTTP {r.status_code}: {url}", "WARN")
                    break
            except Exception as e:
                log(f"Arama sayfası çekilemedi ({url}): {e}", "ERROR")
                time.sleep(1.0)
        return []

    def arac_verilerini_cek_ve_parse_et(self, arac_id, ilan_url):
        """
        Detay sayfasından HASSAS BİLGİLERİ DIŞLAR ve verileri analitik formatta parse eder.
        """
        thread_session = requests.Session(impersonate="chrome120")
        try:
            thread_session.cookies.update(self.session.cookies)
        except Exception:
            pass

        html_content = None
        for deneme in range(3):
            try:
                r = thread_session.get(ilan_url, headers=self.headers, timeout=20)
                if r.status_code == 200:
                    html_content = r.text
                    break
                elif r.status_code in (403, 429):
                    time.sleep(1.5 * (deneme + 1))
            except Exception:
                time.sleep(1.0)

        if not html_content:
            return None

        try:
            soup = BeautifulSoup(html_content, "html.parser")

            dl_data = {}
            damage_list = []

            # JSON Scriptlerini tara
            for s in soup.find_all("script"):
                stext = s.string or ""
                if not stext:
                    continue

                if "dataLayer.push" in stext and "CD_marka" in stext:
                    for m in re.finditer(r"'([^']+)'\s*:\s*([^,\}\n]+)", stext):
                        k = m.group(1).strip()
                        v = m.group(2).strip().strip("'").strip('"')
                        dl_data[k] = v

                if "window.damage" in stext:
                    m_dmg = re.search(r"window\.damage\s*=\s*(\[.*?\]);", stext)
                    if m_dmg:
                        try:
                            damage_list = json.loads(m_dmg.group(1))
                        except Exception:
                            pass

            # 1. Konum Ayrıştırma
            il = dl_data.get("CD_il", "").strip()
            ilce = dl_data.get("CD_ilce", "").strip()
            mahalle = ""
            tam_konum = ""

            loc_el = soup.select_one("span.product-location, div.product-location")
            if loc_el:
                tam_konum = loc_el.get_text(strip=True)
                m_mh = re.search(r"^(.*?Mh\.|.*?Mahallesi|.*?Köyü)", tam_konum)
                if m_mh:
                    mahalle = m_mh.group(1).strip()
                if not il or not ilce:
                    parts = tam_konum.split(",")
                    if len(parts) >= 2:
                        il = parts[-1].strip()
                        ilce_part = parts[0].strip()
                        m_ilce = re.search(r"(?:Mh\.|Mahallesi|Köyü)\s*(.*)", ilce_part)
                        if m_ilce:
                            ilce = m_ilce.group(1).strip()

            # 2. Araç Kimlik ve Segment
            kategori = html_lib.unescape(dl_data.get("CD_kategori", "Otomobil")).strip()
            marka = html_lib.unescape(dl_data.get("CD_marka") or dl_data.get("CD_Marka", "")).strip()
            seri = html_lib.unescape(dl_data.get("CD_seri", "")).strip()
            model = html_lib.unescape(dl_data.get("CD_model", "")).strip()
            breadcrumb = html_lib.unescape(dl_data.get("CD_Detail_Breadcrumb", "")).strip()
            segment = html_lib.unescape(dl_data.get("CD_Detail_CarSegment", "")).strip()

            marka_lower = marka.lower()
            if any(b in marka_lower for b in LUXURY_BRANDS):
                refah_segmenti = "Lüks/Premium"
            elif any(b in marka_lower for b in ECONOMY_BRANDS):
                refah_segmenti = "Ekonomik"
            else:
                refah_segmenti = "Orta"

            # 3. Sayısal Alanlar (Yıl, KM, Fiyat)
            yil = parse_integer(dl_data.get("CD_yil"), default=2020)
            km = parse_integer(dl_data.get("CD_kilometre"), default=0)

            fiyat_tl = 0
            price_el = soup.select_one("div.product-price, div.price, span.price")
            if price_el:
                fiyat_tl = parse_integer(price_el.get_text(strip=True))
            if not fiyat_tl and dl_data.get("CD_Fiyat"):
                fiyat_tl = parse_integer(dl_data.get("CD_Fiyat"))

            # 4. Motor ve Şanzıman Alanları
            vites_tipi = dl_data.get("CD_vites_tipi", "").strip()
            yakit_tipi = dl_data.get("CD_yakit_tipi", "").strip()
            kasa_tipi = standardize_kasa(dl_data.get("CD_kasa_tipi"))
            renk = dl_data.get("CD_renk", "").strip()

            motor_cc = parse_motor_cc(dl_data.get("CD_motor_hacmi"))
            motor_hp = parse_motor_hp(dl_data.get("CD_motor_gucu"))
            cekis = dl_data.get("CD_cekis", "").strip()
            arac_durumu = dl_data.get("CD_arac_durumu", "İkinci El").strip()

            ort_yakit = parse_float(dl_data.get("CD_ort._yakit_tuketimi"))
            yakit_deposu = parse_integer(dl_data.get("CD_yakit_deposu"), default=0)

            # 5. Hasar & Ekspertiz Durumu (13 Kolon ve İstatistikler)
            agir_hasarli_raw = dl_data.get("CD_agir_hasarli", "Hayır").strip()
            agir_hasarli = 1 if "evet" in agir_hasarli_raw.lower() else 0

            boya_ozet = dl_data.get("CD_boya-degisen", "").strip()
            boya_ozet_lower = boya_ozet.lower()

            tamami_orijinal = 1 if ("tamamı orjinal" in boya_ozet_lower or "hatasız" in boya_ozet_lower) else 0

            # 13 Parçanın Bireysel Kolon Değerlerini Doldur
            hasar_parcalari = {
                "hasar_kaput": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_tavan": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_bagaj": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_on_tampon": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_arka_tampon": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sag_on_camurluk": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sol_on_camurluk": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sag_on_kapi": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sol_on_kapi": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sag_arka_kapi": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sol_arka_kapi": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sag_arka_camurluk": "Orijinal" if tamami_orijinal else "Belirtilmemiş",
                "hasar_sol_arka_camurluk": "Orijinal" if tamami_orijinal else "Belirtilmemiş"
            }

            boyali_sayisi = 0
            degisen_sayisi = 0
            lokal_sayisi = 0

            if damage_list:
                for d in damage_list:
                    code = d.get("Code", "")
                    col_name = PART_ID_MAP.get(code)
                    status_text = d.get("ValueDescription", "Belirtilmemiş").strip()
                    val_text = d.get("ValueText", "").lower()

                    if col_name:
                        hasar_parcalari[col_name] = status_text

                    if "paint" in val_text or "boyalı" in status_text.lower():
                        if "local" in val_text or "lokal" in status_text.lower():
                            lokal_sayisi += 1
                        else:
                            boyali_sayisi += 1
                    elif "change" in val_text or "değişmiş" in status_text.lower():
                        degisen_sayisi += 1

            # Özette belirtilen sayıları da kontrol et
            if boyali_sayisi == 0:
                m_b = re.search(r"(\d+)\s*boyalı", boya_ozet_lower)
                if m_b: boyali_sayisi = int(m_b.group(1))
            if degisen_sayisi == 0:
                m_d = re.search(r"(\d+)\s*değişen", boya_ozet_lower)
                if m_d: degisen_sayisi = int(m_d.group(1))

            if boyali_sayisi > 0 or degisen_sayisi > 0:
                tamami_orijinal = 0

            # 6. Ticari ve Tarih Alanları
            takas_raw = dl_data.get("CD_takasa_uygun", "").strip().lower()
            takasa_uygun = 1 if "uygun" in takas_raw and "değil" not in takas_raw else 0

            satici_tipi = dl_data.get("CD_kimden") or ("Galeriden" if "galeri" in ilan_url else "Sahibinden")

            iso_tarih = parse_iso_tarih(dl_data.get("CD_ilan_tarihi"), dl_data.get("CD_ilanTarihi"))

            kayit = {
                "arac_id": arac_id,
                "il": il,
                "ilce": ilce,
                "mahalle": mahalle,
                "tam_konum": tam_konum,
                "kategori": kategori,
                "marka": marka,
                "seri": seri,
                "model": model,
                "breadcrumb": breadcrumb,
                "segment": segment,
                "refah_segmenti": refah_segmenti,
                "yil": yil,
                "km": km,
                "vites_tipi": vites_tipi,
                "yakit_tipi": yakit_tipi,
                "kasa_tipi": kasa_tipi,
                "renk": renk,
                "motor_hacmi_cc": motor_cc,
                "motor_gucu_hp": motor_hp,
                "cekis": cekis,
                "arac_durumu": arac_durumu,
                "ort_yakit_tuketimi": ort_yakit,
                "yakit_deposu_lt": yakit_deposu,
                "agir_hasarli": agir_hasarli,
                "tamami_orijinal": tamami_orijinal,
                "boyali_parca_sayisi": boyali_sayisi,
                "degisen_parca_sayisi": degisen_sayisi,
                "lokal_boyali_parca_sayisi": lokal_sayisi,
                "boya_degisen_ozet": boya_ozet,
                "fiyat_tl": fiyat_tl,
                "para_birimi": "TL",
                "takasa_uygun": takasa_uygun,
                "satici_tipi": satici_tipi,
                "ilan_tarihi": iso_tarih
            }
            kayit.update(hasar_parcalari)
            return kayit

        except Exception as e:
            log(f"Ayrıştırma hatası ({arac_id}): {e}", "ERROR")
            return None

    def araci_kaydet(self, d):
        """Ayrıştırılmış temiz kaydı SQLite veritabanına yazar."""
        if not d or not d.get("arac_id"):
            return False

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        try:
            c.execute("""
            INSERT OR REPLACE INTO arabam_ilanlari (
                arac_id, il, ilce, mahalle, tam_konum,
                kategori, marka, seri, model, breadcrumb, segment, refah_segmenti,
                yil, km, vites_tipi, yakit_tipi, kasa_tipi, renk,
                motor_hacmi_cc, motor_gucu_hp, cekis, arac_durumu, ort_yakit_tuketimi, yakit_deposu_lt,
                agir_hasarli, tamami_orijinal, boyali_parca_sayisi, degisen_parca_sayisi, lokal_boyali_parca_sayisi,
                boya_degisen_ozet,
                hasar_kaput, hasar_tavan, hasar_bagaj, hasar_on_tampon, hasar_arka_tampon,
                hasar_sag_on_camurluk, hasar_sol_on_camurluk, hasar_sag_on_kapi, hasar_sol_on_kapi,
                hasar_sag_arka_kapi, hasar_sol_arka_kapi, hasar_sag_arka_camurluk, hasar_sol_arka_camurluk,
                fiyat_tl, para_birimi, takasa_uygun, satici_tipi, ilan_tarihi,
                guncellenme_yili, veri_donemi
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                2026, '2026-Q3 (Güncel)'
            )
            """, (
                d["arac_id"], d["il"], d["ilce"], d["mahalle"], d["tam_konum"],
                d["kategori"], d["marka"], d["seri"], d["model"], d["breadcrumb"], d["segment"], d["refah_segmenti"],
                d["yil"], d["km"], d["vites_tipi"], d["yakit_tipi"], d["kasa_tipi"], d["renk"],
                d["motor_hacmi_cc"], d["motor_gucu_hp"], d["cekis"], d["arac_durumu"], d["ort_yakit_tuketimi"], d["yakit_deposu_lt"],
                d["agir_hasarli"], d["tamami_orijinal"], d["boyali_parca_sayisi"], d["degisen_parca_sayisi"], d["lokal_boyali_parca_sayisi"],
                d["boya_degisen_ozet"],
                d["hasar_kaput"], d["hasar_tavan"], d["hasar_bagaj"], d["hasar_on_tampon"], d["hasar_arka_tampon"],
                d["hasar_sag_on_camurluk"], d["hasar_sol_on_camurluk"], d["hasar_sag_on_kapi"], d["hasar_sol_on_kapi"],
                d["hasar_sag_arka_kapi"], d["hasar_sol_arka_kapi"], d["hasar_sag_arka_camurluk"], d["hasar_sol_arka_camurluk"],
                d["fiyat_tl"], d["para_birimi"], d["takasa_uygun"], d["satici_tipi"], d["ilan_tarihi"]
            ))
            conn.commit()
            self.mevcut_araclar.add(d["arac_id"])
            return True
        except Exception as e:
            log(f"Veritabanı yazma hatası ({d.get('arac_id')}): {e}", "ERROR")
            return False
        finally:
            conn.close()

    def calistir_toplu_tarama(self, plakalar=None, kategoriler=None, sayfa_sayisi=3, genel=False):
        """Toplu tarama orkestrasyonu."""
        kategoriler = kategoriler or ["otomobil"]
        toplam_basarili = 0

        hedef_kombinasyonlar = []
        if genel:
            for kat in kategoriler:
                hedef_kombinasyonlar.append((kat, None))
        else:
            plakalar = plakalar or [34, 6, 35, 77, 16, 7]
            for plaka in plakalar:
                for kat in kategoriler:
                    hedef_kombinasyonlar.append((kat, plaka))

        log(f"Toplam {len(hedef_kombinasyonlar)} il/kategori hedefi taranacak (Max sayfa: {sayfa_sayisi})...", "INFO")

        # 1. Aşama: Link Keşfi (İlan bittiğinde erken sonlanmalı)
        bulunanlar = []
        gorulenler = set(self.mevcut_araclar)

        for kat, plaka in hedef_kombinasyonlar:
            etiket = f"Plaka {plaka}" if plaka else "Genel"
            bos_sayfa_sayisi = 0
            for sayfa in range(1, sayfa_sayisi + 1):
                links = self.arama_sayfasindan_ilan_linkleri_al(kategori=kat, plaka=plaka, sayfa=sayfa)
                if not links:
                    bos_sayfa_sayisi += 1
                    if bos_sayfa_sayisi >= 1: # İlanlar tükendi, sonraki il/kategoriye geç
                        break
                    continue
                bos_sayfa_sayisi = 0
                yeni_adet = 0
                for aid, u in links:
                    if aid not in gorulenler:
                        gorulenler.add(aid)
                        bulunanlar.append((aid, u))
                        yeni_adet += 1
                log(f"  {etiket} [{kat}] Sayfa {sayfa}: {len(links)} araç bulundu ({yeni_adet} yeni).", "INFO")
                time.sleep(0.2)

        if not bulunanlar:
            log("Taranacak yeni araç bulunamadı (Tümü veritabanında mevcut).", "INFO")
            return 0

        log(f"Detay çekimi başlıyor: {len(bulunanlar)} araç ayrıştırılacak (Worker Threads: {self.max_threads})...", "INFO")

        # 2. Aşama: Detay Çekimi ve Temiz Parse
        t_basla = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            future_to_arac = {
                executor.submit(self.arac_verilerini_cek_ve_parse_et, aid, u): (aid, u)
                for aid, u in bulunanlar
            }

            for i, future in enumerate(concurrent.futures.as_completed(future_to_arac), 1):
                aid, u = future_to_arac[future]
                try:
                    veri = future.result()
                    if veri:
                        self.araci_kaydet(veri)
                        toplam_basarili += 1
                        hasar_durum = "Hatasız/Orijinal" if veri["tamami_orijinal"] else f"{veri['boyali_parca_sayisi']} Boya / {veri['degisen_parca_sayisi']} Değişen"
                        log(f"  [{i}/{len(bulunanlar)}] {veri['marka']} {veri['model']} ({veri['yil']}) | {veri['fiyat_tl']:,} TL | {veri['km']:,} KM | {hasar_durum} | {veri['il']}/{veri['ilce']}", "SUCCESS")
                except Exception as exc:
                    log(f"İş parçacığı hatası ({aid}): {exc}", "ERROR")

        t_bitir = time.time()
        log(f"Tarama tamamlandı! {toplam_basarili} araç standart formatta kaydedildi ({t_bitir - t_basla:.1f} sn).", "SUCCESS")
        return toplam_basarili

    def hesapla_arac_refah_endeksi(self):
        """Toplanan araç verilerinden İlçe bazlı Refah ve Alım Gücü Endeksi hesaplar."""
        log("İlçe Bazlı Araç Refah Endeksi hesaplanıyor...", "INFO")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("""
        SELECT il, ilce, COUNT(*) as adet,
               AVG(fiyat_tl) as ort_fiyat,
               AVG(yil) as ort_yil,
               AVG(km) as ort_km,
               SUM(CASE WHEN refah_segmenti = 'Lüks/Premium' THEN 1 ELSE 0 END) as luks_adet,
               SUM(CASE WHEN refah_segmenti = 'Ekonomik' THEN 1 ELSE 0 END) as eko_adet
        FROM arabam_ilanlari
        WHERE ilce IS NOT NULL AND ilce != ''
        GROUP BY il, ilce
        HAVING COUNT(*) >= 2
        """)
        gruplar = c.fetchall()

        for g in gruplar:
            il = g["il"]
            ilce = g["ilce"]
            adet = g["adet"]
            ort_fiyat = round(g["ort_fiyat"] or 0, 0)
            ort_yil = round(g["ort_yil"] or 2018, 1)
            ort_km = round(g["ort_km"] or 150000, 0)
            ort_yas = round(2026.5 - ort_yil, 1)

            luks_oran = round((g["luks_adet"] / adet) * 100, 1)
            eko_oran = round((g["eko_adet"] / adet) * 100, 1)

            c.execute("SELECT marka, COUNT(*) as m_adet FROM arabam_ilanlari WHERE il=? AND ilce=? GROUP BY marka ORDER BY m_adet DESC LIMIT 1", (il, ilce))
            pop = c.fetchone()
            pop_marka = pop["marka"] if pop else "Volkswagen"

            base_skor = 50.0 + ((ort_fiyat - 850000) / 45000) + (luks_oran * 0.8) - (eko_oran * 0.4) - (ort_yas * 1.5)
            refah_skoru = max(25.0, min(99.5, round(base_skor, 1)))

            if refah_skoru >= 85: kat = "A+"
            elif refah_skoru >= 75: kat = "A"
            elif refah_skoru >= 65: kat = "B+"
            elif refah_skoru >= 50: kat = "B"
            else: kat = "C"

            c.execute("""
            INSERT OR REPLACE INTO ilce_arac_refah_endeksi (
                il, ilce, toplam_ilan_sayisi, ortalama_fiyat_tl, medyan_fiyat_tl,
                ortalama_arac_yasi, ortalama_km, luks_arac_orani_yuzde, ekonomik_arac_orani_yuzde,
                en_populer_marka, refah_ve_alim_gucu_skoru, sosyo_ekonomik_kategori,
                veri_donemi, guncellenme_yili
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-Q3 (Güncel)', 2026)
            """, (
                il, ilce, adet, ort_fiyat, ort_fiyat,
                ort_yas, ort_km, luks_oran, eko_oran,
                pop_marka, refah_skoru, kat
            ))

        conn.commit()
        conn.close()
        log("  ✓ İlçe Araç Refah Endeksi güncellendi.", "SUCCESS")

    def export_csv(self, target_dir=None):
        """Toplanan ayrıştırılmış araç verilerini CSV formatına aktarır."""
        import csv
        out_dir = Path(target_dir) if target_dir else CSV_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        log("Araç verileri CSV formatına aktarılıyor...", "INFO")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        tablolar = [
            ("arabam_ilanlari", "23_arabam_arac_ilanlari.csv"),
            ("ilce_arac_refah_endeksi", "24_ilce_ve_mahalle_arac_refah_endeksi.csv")
        ]

        for tbl, fname in tablolar:
            c.execute(f"SELECT * FROM {tbl}")
            rows = c.fetchall()
            if rows:
                p = out_dir / fname
                keys = rows[0].keys()
                with open(p, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys, delimiter=";")
                    writer.writeheader()
                    for r in rows:
                        writer.writerow(dict(r))
                log(f"  ✓ {len(rows)} kayıt -> {p}", "SUCCESS")

        conn.close()

def parse_sehirler(sehir_args):
    """Hem virgüllü '1,2,3' hem de boşluklu 1 2 3 plaka girdilerini ayrıştırır."""
    if not sehir_args:
        return []
    plakalar = []
    for arg in sehir_args:
        for part in str(arg).split(","):
            part = part.strip()
            if part.isdigit():
                plakalar.append(int(part))
    return sorted(list(set(plakalar)))

def resolve_categories(kat_inputs):
    """Kategori girdilerini (otomobil, arazi, suv, minivan, panelvan, hepsi) standart URL sluglarına dönüştürür."""
    cats = []
    for k in (kat_inputs or DEFAULT_CATEGORIES):
        for sub_k in str(k).split(","):
            sub_k = sub_k.strip().lower()
            if not sub_k:
                continue
            resolved = CATEGORY_MAP.get(sub_k, sub_k)
            if isinstance(resolved, list):
                cats.extend(resolved)
            else:
                cats.append(resolved)
    return list(dict.fromkeys(cats))

def main():
    parser = argparse.ArgumentParser(description="GEOPROP AI - Standartlaştırılmış Araç Verisi Toplayıcısı")
    parser.add_argument("--hepsi", action="store_true", help="Türkiye genelindeki 81 ilin tamamını tara")
    parser.add_argument("--sehirler", "--iller", nargs="+", default=None, help="Taranacak il plaka kodları (Örn: --sehirler 34 6 veya '34,6')")
    parser.add_argument("--genel", action="store_true", help="İl filtresi olmadan son yüklenen ilanları tara")
    parser.add_argument("--kategoriler", nargs="+", default=DEFAULT_CATEGORIES, help="Kategoriler: otomobil arazi-suv-pick-up minivan-panelvan (Varsayılan: Tümü)")
    parser.add_argument("--sayfa", type=int, default=3, help="Her il veya kategori için taranacak sayfa adedi")
    parser.add_argument("--threads", type=int, default=6, help="Paralel çalışan iş parçacığı sayısı")
    parser.add_argument("--db", type=str, default=None, help="Özel SQLite veritabanı yolu")
    parser.add_argument("--csv-dir", type=str, default=None, help="Özel CSV çıkış dizini")
    parser.add_argument("--export-only", action="store_true", help="Yalnızca mevcut veritabanını CSV'ye aktar")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    toplayici = AnonimArabaToplayici(db_path=db_path, max_threads=args.threads)

    if args.export_only:
        toplayici.export_csv(target_dir=args.csv_dir)
        return

    plakalar = list(range(1, 82)) if args.hepsi else parse_sehirler(args.sehirler)
    if not plakalar and not args.genel:
        plakalar = [34, 6, 35, 77, 16, 7]

    kategoriler = resolve_categories(args.kategoriler)

    log("=" * 70, "INFO")
    log("GEOPROP AI - STANDARTLAŞTIRILMIŞ ARAÇ VERİ TOPLAYICI (ANALİTİK PARSE)", "INFO")
    log(f"Hedef İller: {'81 İl Tümü' if args.hepsi else ('Genel Akış' if args.genel else plakalar)}", "INFO")
    log(f"Kategoriler: {kategoriler} | Sayfa Sayısı: {args.sayfa} | Worker Threads: {args.threads}", "INFO")
    log("=" * 70, "INFO")

    toplayici.calistir_toplu_tarama(
        plakalar=plakalar,
        kategoriler=kategoriler,
        sayfa_sayisi=args.sayfa,
        genel=args.genel
    )

    toplayici.hesapla_arac_refah_endeksi()
    toplayici.export_csv(target_dir=args.csv_dir)

    log("=" * 70, "SUCCESS")
    log("ARAÇ VERİLERİ BAŞARIYLA AYRIŞTIRILDI VE DIŞA AKTARILDI!", "SUCCESS")
    log(f"Veritabanı: {db_path}", "INFO")
    log("=" * 70, "SUCCESS")

if __name__ == "__main__":
    main()
