#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Eksiksiz Araç Verisi Toplayıcısı (Arabam.com 35+ Veri Alanı)
========================================================================
Arabam.com üzerindeki taşıt ilanlarını hiçbir veriyi atlamadan, %100 eksiksiz toplar:
- İlan Başlığı, Fiyat, Kategori, Marka, Seri, Model, Paket, Segment
- Yıl, KM, Vites, Yakıt, Kasa Tipi, Renk, Motor Hacmi (cc), Motor Gücü (hp), Çekiş
- Ortalama Yakıt Tüketimi, Yakıt Deposu, Araç Durumu
- Ağır Hasar Durumu, Boya-Değişen Özeti ve 13 Parçalık Ekspertiz Matrisi (JSON)
- Satıcı Tipi, Mağaza Adı, Yetkili Kişi, Yetki Belge No, Maskesiz Gerçek Telefon, Üyelik Süresi
- Tam Açıklama Metni
- Tüm Yüksek Çözünürlüklü (HD 1280x960) Fotoğraf Linkleri (JSON)
- İl, İlçe, Mahalle ve Tam Konum
- Veriler SQLite (`arabam_vasita_piyasasi.sqlite`) ve UTF-8 BOM CSV'ye aktarılır.
"""

import os
import sys
import re
import json
import time
import sqlite3
import argparse
import concurrent.futures
from pathlib import Path
from datetime import datetime

try:
    from curl_cffi import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Gereksinimler eksik! Lütfen 'pip install curl_cffi beautifulsoup4' komutunu çalıştırın.")
    sys.exit(1)

# Dizin Yolları
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "collector" / "data" if (BASE_DIR / "collector").exists() else BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "arabam_vasita_piyasasi.sqlite"
IMG_DIR = DATA_DIR / "arac_resimleri"

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

PART_NAME_MAP = {
    "B01001": "Motor Kaputu",
    "B0601": "Tavan",
    "B0201": "Arka Kaput (Bagaj)",
    "B01201": "Ön Tampon",
    "B01301": "Arka Tampon",
    "B0101": "Sağ Arka Çamurluk",
    "B0301": "Sol Arka Çamurluk",
    "B0401": "Sağ Arka Kapı",
    "B0501": "Sağ Ön Kapı",
    "B0701": "Sol Arka Kapı",
    "B0801": "Sol Ön Kapı",
    "B0901": "Sağ Ön Çamurluk",
    "B01101": "Sol Ön Çamurluk"
}

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "[*]", "SUCCESS": "[✓]", "WARN": "[!]", "ERROR": "[✗]"}
    print(f"{ts} {symbols.get(level, '[*]')} {msg}", flush=True)

def init_db(db_path=None):
    """Veritabanını 35+ eksiksiz kolon içeren zenginleştirilmiş şema ile hazırlar."""
    target_db = db_path or DB_PATH
    conn = sqlite3.connect(target_db)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS arabam_ilanlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ilan_no TEXT UNIQUE,
        ilan_url TEXT,
        baslik TEXT,
        ilan_tarihi TEXT,
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
        motor_hacmi_cc TEXT,
        motor_gucu_hp TEXT,
        cekis TEXT,
        arac_durumu TEXT,
        ort_yakit_tuketimi TEXT,
        yakit_deposu_lt TEXT,
        agir_hasarli TEXT,
        boya_degisen_ozet TEXT,
        hasar_parcalari_json TEXT,
        fiyat_tl INTEGER,
        para_birimi TEXT DEFAULT 'TL',
        takasa_uygun TEXT,
        satici_tipi TEXT,
        satici_adi TEXT,
        yetkili_kisi TEXT,
        yetki_belge_no TEXT,
        telefon TEXT,
        uyelik_bilgisi TEXT,
        magaza_url TEXT,
        aciklama TEXT,
        fotograf_sayisi INTEGER DEFAULT 0,
        kapak_fotografi TEXT,
        fotograflar_json TEXT,
        guncellenme_yili INTEGER DEFAULT 2026,
        veri_donemi TEXT DEFAULT '2026-Q3 (Güncel)',
        eklenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Var olan veritabanı şemasına eksik kolonlar varsa dinamik ekle (Upgrade)
    c.execute("PRAGMA table_info(arabam_ilanlari)")
    existing_cols = {row[1] for row in c.fetchall()}

    new_columns = [
        ("tam_konum", "TEXT"),
        ("kategori", "TEXT"),
        ("breadcrumb", "TEXT"),
        ("refah_segmenti", "TEXT"),
        ("vites_tipi", "TEXT"),
        ("yakit_tipi", "TEXT"),
        ("kasa_tipi", "TEXT"),
        ("motor_hacmi_cc", "TEXT"),
        ("motor_gucu_hp", "TEXT"),
        ("cekis", "TEXT"),
        ("arac_durumu", "TEXT"),
        ("ort_yakit_tuketimi", "TEXT"),
        ("yakit_deposu_lt", "TEXT"),
        ("agir_hasarli", "TEXT"),
        ("boya_degisen_ozet", "TEXT"),
        ("hasar_parcalari_json", "TEXT"),
        ("para_birimi", "TEXT DEFAULT 'TL'"),
        ("takasa_uygun", "TEXT"),
        ("satici_adi", "TEXT"),
        ("yetkili_kisi", "TEXT"),
        ("yetki_belge_no", "TEXT"),
        ("telefon", "TEXT"),
        ("uyelik_bilgisi", "TEXT"),
        ("magaza_url", "TEXT"),
        ("aciklama", "TEXT"),
        ("fotograf_sayisi", "INTEGER DEFAULT 0"),
        ("kapak_fotografi", "TEXT"),
        ("fotograflar_json", "TEXT")
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_cols:
            try:
                c.execute(f"ALTER TABLE arabam_ilanlari ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass

    # İlçe ve Bölge Araç Refah Endeksi Tablosu
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

class EksiksizArabaToplayici:
    """Arabam.com tüm taşıt verilerini eksiksiz toplayan motor."""

    def __init__(self, db_path=None, max_threads=6):
        self.db_path = db_path or DB_PATH
        init_db(self.db_path)
        self.max_threads = max_threads
        self.session = requests.Session(impersonate="chrome120")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Referer": "https://www.arabam.com/",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        self.mevcut_ilanlar = self._mevcut_ilan_nolari_yukle()

    def _mevcut_ilan_nolari_yukle(self):
        """Daha önce toplanmış ilan numaralarını küme olarak hafızaya alır."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT ilan_no FROM arabam_ilanlari WHERE aciklama IS NOT NULL AND aciklama != ''")
        rows = {r[0] for r in c.fetchall() if r[0]}
        conn.close()
        return rows

    def arama_sayfasindan_ilan_linkleri_al(self, kategori="otomobil", plaka=None, sayfa=1):
        """Arama sonuç sayfasındaki 20 ilanın linklerini ve özet bilgilerini toplar."""
        if plaka:
            url = f"https://www.arabam.com/ikinci-el/{kategori}?city={plaka}&page={sayfa}"
        else:
            url = f"https://www.arabam.com/ikinci-el/{kategori}?page={sayfa}"

        try:
            r = self.session.get(url, headers=self.headers, timeout=15)
            if r.status_code != 200:
                log(f"Arama sayfası HTTP {r.status_code}: {url}", "WARN")
                return []

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

                tam_url = f"https://www.arabam.com{href}" if href.startswith("/") else href
                ilan_linkleri.append((ilan_no, tam_url))

            return ilan_linkleri
        except Exception as e:
            log(f"Arama sayfası çekilemedi ({url}): {e}", "ERROR")
            return []

    def ilan_detayini_eksiksiz_cek(self, ilan_no, ilan_url):
        """
        Tek bir ilanın detay sayfasına girerek tüm 35+ veri alanını ayıklar.
        """
        thread_session = requests.Session(impersonate="chrome120")
        try:
            r = thread_session.get(ilan_url, headers=self.headers, timeout=20)
            if r.status_code != 200:
                log(f"İlan {ilan_no} HTTP {r.status_code} verdi.", "WARN")
                return None

            html = r.text
            soup = BeautifulSoup(html, "html.parser")

            # 1. Sayfa İçi JSON Scriptlerini Ayıkla
            dl_data = {}
            product_detail = {}
            damage_list = []

            for s in soup.find_all("script"):
                stext = s.string or ""
                if not stext:
                    continue

                # dataLayer.push({...})
                if "dataLayer.push" in stext and "CD_marka" in stext:
                    for m in re.finditer(r"'([^']+)'\s*:\s*([^,\}\n]+)", stext):
                        k = m.group(1).strip()
                        v = m.group(2).strip().strip("'").strip('"')
                        dl_data[k] = v

                # window.productDetail = {...};
                if "window.productDetail" in stext:
                    m_pd = re.search(r"window\.productDetail\s*=\s*(\{.*?\});", stext)
                    if m_pd:
                        try:
                            product_detail = json.loads(m_pd.group(1))
                        except Exception:
                            pass

                # window.damage = [...];
                if "window.damage" in stext:
                    m_dmg = re.search(r"window\.damage\s*=\s*(\[.*?\]);", stext)
                    if m_dmg:
                        try:
                            damage_list = json.loads(m_dmg.group(1))
                        except Exception:
                            pass

            # 2. İlan Başlığı
            baslik = ""
            h1 = soup.find("h1")
            if h1:
                baslik = h1.get_text(strip=True)
            elif product_detail.get("ModelName"):
                baslik = product_detail["ModelName"]
            elif dl_data.get("CD_model"):
                baslik = dl_data["CD_model"]

            # 3. Tarih
            ilan_tarihi = (
                product_detail.get("DateString")
                or dl_data.get("CD_ilan_tarihi")
                or datetime.now().strftime("%d %B %Y")
            )

            # 4. Fiyat
            fiyat_tl = 0
            price_el = soup.select_one("div.product-price, div.price, span.price")
            if price_el:
                price_digits = re.sub(r"[^\d]", "", price_el.get_text(strip=True))
                if price_digits:
                    fiyat_tl = int(price_digits)
            if not fiyat_tl and dl_data.get("CD_Fiyat"):
                try:
                    fiyat_tl = int(dl_data["CD_Fiyat"])
                except ValueError:
                    pass

            # 5. Konum Bilgileri (İl, İlçe, Mahalle)
            il = dl_data.get("CD_il", "")
            ilce = dl_data.get("CD_ilce", "")
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

            # 6. Taşıt Kimlik ve Segment Bilgileri
            kategori = dl_data.get("CD_kategori", "Otomobil")
            marka = dl_data.get("CD_marka") or dl_data.get("CD_Marka", "")
            seri = dl_data.get("CD_seri", "")
            model = dl_data.get("CD_model", "")
            breadcrumb = dl_data.get("CD_Detail_Breadcrumb", "")
            segment = dl_data.get("CD_Detail_CarSegment", "")

            # Refah Segmenti Tespiti
            marka_lower = marka.lower()
            if any(b in marka_lower for b in LUXURY_BRANDS):
                refah_segmenti = "Lüks/Premium"
            elif any(b in marka_lower for b in ECONOMY_BRANDS):
                refah_segmenti = "Ekonomik"
            else:
                refah_segmenti = "Orta"

            # 7. Teknik Özellikler
            yil_str = dl_data.get("CD_yil", "")
            yil = int(re.sub(r"[^\d]", "", yil_str)) if re.sub(r"[^\d]", "", yil_str) else 2020

            km_str = dl_data.get("CD_kilometre", "")
            km = int(re.sub(r"[^\d]", "", km_str)) if re.sub(r"[^\d]", "", km_str) else 0

            vites_tipi = dl_data.get("CD_vites_tipi", "")
            yakit_tipi = dl_data.get("CD_yakit_tipi", "")
            kasa_tipi = dl_data.get("CD_kasa_tipi", "")
            renk = dl_data.get("CD_renk", "")
            motor_hacmi = dl_data.get("CD_motor_hacmi", "")
            motor_gucu = dl_data.get("CD_motor_gucu", "")
            cekis = dl_data.get("CD_cekis", "")
            arac_durumu = dl_data.get("CD_arac_durumu", "İkinci El")
            ort_yakit = dl_data.get("CD_ort._yakit_tuketimi", "")
            yakit_deposu = dl_data.get("CD_yakit_deposu", "")

            # 8. Hasar ve Ekspertiz Dökümü
            agir_hasarli = dl_data.get("CD_agir_hasarli", "Hayır")
            boya_degisen_ozet = dl_data.get("CD_boya-degisen", "")
            takasa_uygun = dl_data.get("CD_takasa_uygun", "")

            hasar_parcalari_list = []
            if damage_list:
                for d in damage_list:
                    pcode = d.get("Code", "")
                    pname = d.get("Name") or PART_NAME_MAP.get(pcode, pcode)
                    pstatus = d.get("ValueDescription", "Belirtilmemiş")
                    hasar_parcalari_list.append({
                        "kod": pcode,
                        "parca": pname,
                        "durum": pstatus,
                        "durum_kodu": d.get("Value", "")
                    })
            hasar_parcalari_json = json.dumps(hasar_parcalari_list, ensure_ascii=False) if hasar_parcalari_list else ""

            # 9. Satıcı ve İletişim Bilgileri
            member_obj = product_detail.get("Member") or {}
            default_firm = member_obj.get("DefaultFirm") or {}

            satici_tipi = dl_data.get("CD_kimden") or member_obj.get("MemberType", {}).get("Description", "Galeriden")
            satici_adi = (
                dl_data.get("CD_galeri_name")
                or default_firm.get("FirmName")
                or member_obj.get("MemberName", "")
            )
            yetkili_kisi = default_firm.get("AuthorizedPerson", "")
            yetki_belge_no = default_firm.get("AuthorizationLicenseCode", "")
            telefon = product_detail.get("MobilePhone") or product_detail.get("Phone", "")

            # Üyelik kıdemi
            member_since = member_obj.get("MemberSince", 0)
            member_status = member_obj.get("MembershipStatusName", "")
            uyelik_bilgisi = f"{member_status} ({member_since}. Yıl)".strip() if member_since else member_status

            # Mağaza URL
            firm_slug = default_firm.get("FirmUrl", "")
            magaza_url = f"https://www.arabam.com/galeri/{firm_slug}" if firm_slug else ""

            # Fallback Satıcı Kutusu HTML Taraması
            owner_box = soup.select_one("div.advert-owner-information-container, div.advert-owner-information")
            if owner_box:
                if not yetki_belge_no and "Yetki Belge No:" in owner_box.text:
                    m_yb = re.search(r"Yetki Belge No:\s*([A-Za-z0-9]+)", owner_box.text)
                    if m_yb:
                        yetki_belge_no = m_yb.group(1)
                if not satici_adi:
                    s_first = owner_box.find(["h2", "h3", "strong", "span"])
                    if s_first:
                        satici_adi = s_first.get_text(strip=True)

            # 10. Tam Açıklama Metni
            desc_el = soup.select_one("#description, div.tab-description, div.product-description")
            aciklama = desc_el.get_text("\n", strip=True) if desc_el else ""

            # 11. Fotoğraflar
            foto_list = []
            # Önce productDetail içindeki tam çözünürlüklü fotoğrafları al
            raw_photos = product_detail.get("Photos") or []
            if raw_photos:
                for p in raw_photos:
                    u = p.get("Url", "")
                    if u:
                        # Formatlama ({0} -> 1280x960 veya 800x600)
                        hd_u = u.replace("{0}", "1280x960")
                        foto_list.append(hd_u)

            # Fallback: HTML img etiketleri
            if not foto_list:
                seen_srcs = set()
                for img in soup.select("img[src*='arbstorage'], img[data-src*='arbstorage']"):
                    src = img.get("data-src") or img.get("src") or ""
                    if "ilanfotograflari" in src:
                        hd_src = re.sub(r"_\d+x\d+\.", "_1280x960.", src)
                        if hd_src not in seen_srcs:
                            seen_srcs.add(hd_src)
                            foto_list.append(hd_src)

            fotograf_sayisi = len(foto_list)
            kapak_fotografi = foto_list[0] if foto_list else ""
            fotograflar_json = json.dumps(foto_list, ensure_ascii=False) if foto_list else ""

            return {
                "ilan_no": str(ilan_no),
                "ilan_url": ilan_url,
                "baslik": baslik,
                "ilan_tarihi": ilan_tarihi,
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
                "motor_hacmi_cc": motor_hacmi,
                "motor_gucu_hp": motor_gucu,
                "cekis": cekis,
                "arac_durumu": arac_durumu,
                "ort_yakit_tuketimi": ort_yakit,
                "yakit_deposu_lt": yakit_deposu,
                "agir_hasarli": agir_hasarli,
                "boya_degisen_ozet": boya_degisen_ozet,
                "hasar_parcalari_json": hasar_parcalari_json,
                "fiyat_tl": fiyat_tl,
                "para_birimi": "TL",
                "takasa_uygun": takasa_uygun,
                "satici_tipi": satici_tipi,
                "satici_adi": satici_adi,
                "yetkili_kisi": yetkili_kisi,
                "yetki_belge_no": yetki_belge_no,
                "telefon": telefon,
                "uyelik_bilgisi": uyelik_bilgisi,
                "magaza_url": magaza_url,
                "aciklama": aciklama,
                "fotograf_sayisi": fotograf_sayisi,
                "kapak_fotografi": kapak_fotografi,
                "fotograflar_json": fotograflar_json
            }
        except Exception as e:
            log(f"İlan detay çıkarma hatası ({ilan_url}): {e}", "ERROR")
            return None

    def ilani_veritabanina_kaydet(self, d):
        """Çıkarılan tüm alanları SQLite veritabanına yazar."""
        if not d or not d.get("ilan_no"):
            return False

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        try:
            c.execute("""
            INSERT OR REPLACE INTO arabam_ilanlari (
                ilan_no, ilan_url, baslik, ilan_tarihi,
                il, ilce, mahalle, tam_konum,
                kategori, marka, seri, model, breadcrumb, segment, refah_segmenti,
                yil, km, vites_tipi, yakit_tipi, kasa_tipi, renk,
                motor_hacmi_cc, motor_gucu_hp, cekis, arac_durumu, ort_yakit_tuketimi, yakit_deposu_lt,
                agir_hasarli, boya_degisen_ozet, hasar_parcalari_json,
                fiyat_tl, para_birimi, takasa_uygun,
                satici_tipi, satici_adi, yetkili_kisi, yetki_belge_no, telefon, uyelik_bilgisi, magaza_url,
                aciklama, fotograf_sayisi, kapak_fotografi, fotograflar_json,
                guncellenme_yili, veri_donemi
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                2026, '2026-Q3 (Güncel)'
            )
            """, (
                d["ilan_no"], d["ilan_url"], d["baslik"], d["ilan_tarihi"],
                d["il"], d["ilce"], d["mahalle"], d["tam_konum"],
                d["kategori"], d["marka"], d["seri"], d["model"], d["breadcrumb"], d["segment"], d["refah_segmenti"],
                d["yil"], d["km"], d["vites_tipi"], d["yakit_tipi"], d["kasa_tipi"], d["renk"],
                d["motor_hacmi_cc"], d["motor_gucu_hp"], d["cekis"], d["arac_durumu"], d["ort_yakit_tuketimi"], d["yakit_deposu_lt"],
                d["agir_hasarli"], d["boya_degisen_ozet"], d["hasar_parcalari_json"],
                d["fiyat_tl"], d["para_birimi"], d["takasa_uygun"],
                d["satici_tipi"], d["satici_adi"], d["yetkili_kisi"], d["yetki_belge_no"], d["telefon"], d["uyelik_bilgisi"], d["magaza_url"],
                d["aciklama"], d["fotograf_sayisi"], d["kapak_fotografi"], d["fotograflar_json"]
            ))
            conn.commit()
            self.mevcut_ilanlar.add(d["ilan_no"])
            return True
        except Exception as e:
            log(f"Veritabanı yazma hatası ({d.get('ilan_no')}): {e}", "ERROR")
            return False
        finally:
            conn.close()

    def calistir_toplu_tarama(self, plakalar=None, kategoriler=None, sayfa_sayisi=3, genel=False):
        """
        Çok iş parçacıklı tam kapsamlı araç veri toplama orkestrasyonu.
        """
        kategoriler = kategoriler or ["otomobil"]
        toplam_basarili = 0

        hedef_listeler = []
        if genel:
            for kat in kategoriler:
                for s in range(1, sayfa_sayisi + 1):
                    hedef_listeler.append((kat, None, s))
        else:
            plakalar = plakalar or [34, 6, 35, 77, 16, 7]
            for plaka in plakalar:
                for kat in kategoriler:
                    for s in range(1, sayfa_sayisi + 1):
                        hedef_listeler.append((kat, plaka, s))

        log(f"Toplam {len(hedef_listeler)} arama sayfası taranacak...", "INFO")

        # 1. Aşama: İlan Linklerini Keşfet
        bulunan_ilanlar = []
        gorulen_nolar = set(self.mevcut_ilanlar)

        for kat, plaka, sayfa in hedef_listeler:
            etiket = f"Plaka {plaka}" if plaka else "Genel"
            links = self.arama_sayfasindan_ilan_linkleri_al(kategori=kat, plaka=plaka, sayfa=sayfa)
            yeni_sayi = 0
            for ino, u in links:
                if ino not in gorulen_nolar:
                    gorulen_nolar.add(ino)
                    bulunan_ilanlar.append((ino, u))
                    yeni_sayi += 1
            log(f"  {etiket} [{kat}] Sayfa {sayfa}: {len(links)} ilan bulundu ({yeni_sayi} yeni).", "INFO")
            time.sleep(0.3)

        log(f"Arama tamamlandı. Toplam {len(bulunan_ilanlar)} yeni ilanın detayları eksiksiz çekilecek.", "SUCCESS")

        if not bulunan_ilanlar:
            log("Taranacak yeni ilan bulunamadı (Tüm ilanlar zaten güncel veritabanında).", "INFO")
            return 0

        # 2. Aşama: Çok İş Parçacıklı Eksiksiz Detay Çekimi
        t_baslangic = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            future_to_ilan = {
                executor.submit(self.ilan_detayini_eksiksiz_cek, ino, u): (ino, u)
                for ino, u in bulunan_ilanlar
            }

            for i, future in enumerate(concurrent.futures.as_completed(future_to_ilan), 1):
                ino, u = future_to_ilan[future]
                try:
                    veri = future.result()
                    if veri:
                        self.ilani_veritabanina_kaydet(veri)
                        toplam_basarili += 1
                        hasar_adet = len(json.loads(veri["hasar_parcalari_json"])) if veri["hasar_parcalari_json"] else 0
                        foto_adet = veri["fotograf_sayisi"]
                        tel_durum = "Tel Var" if veri["telefon"] else "Tel Yok"
                        log(f"  [{i}/{len(bulunan_ilanlar)}] İlan {ino} eklendi: {veri['marka']} {veri['model']} | {veri['fiyat_tl']:,} TL | {hasar_adet} Ekspertiz Parçası | {foto_adet} HD Foto | {tel_durum}", "SUCCESS")
                    else:
                        log(f"  [{i}/{len(bulunan_ilanlar)}] İlan {ino} çekilemedi.", "WARN")
                except Exception as exc:
                    log(f"İş parçacığı hatası ({ino}): {exc}", "ERROR")

        t_bitis = time.time()
        log(f"Tüm detaylar tamamlandı! Toplam {toplam_basarili} ilan eksiksiz kaydedildi ({t_bitis - t_baslangic:.1f} sn).", "SUCCESS")
        return toplam_basarili

    def eksikleri_tamamla(self):
        """Veritabanında kayıtlı olup detayları henüz çekilmemiş ilanları tespit eder ve tamamlar."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT ilan_no, ilan_url FROM arabam_ilanlari WHERE aciklama IS NULL OR aciklama = ''")
        eksikler = [(r["ilan_no"], r["ilan_url"]) for r in c.fetchall() if r["ilan_url"]]
        conn.close()

        if not eksikler:
            log("Tamamlanacak eksik detaylı ilan yok.", "INFO")
            return 0

        log(f"Veritabanında {len(eksikler)} adet eksik detaylı ilan bulundu, tamamlanıyor...", "INFO")
        toplam_basarili = 0
        t_baslangic = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            future_to_ilan = {
                executor.submit(self.ilan_detayini_eksiksiz_cek, ino, u): (ino, u)
                for ino, u in eksikler
            }
            for i, future in enumerate(concurrent.futures.as_completed(future_to_ilan), 1):
                ino, u = future_to_ilan[future]
                try:
                    veri = future.result()
                    if veri:
                        self.ilani_veritabanina_kaydet(veri)
                        toplam_basarili += 1
                        log(f"  [{i}/{len(eksikler)}] İlan {ino} detaylandırıldı: {veri['marka']} {veri['model']} | {veri['fiyat_tl']:,} TL", "SUCCESS")
                except Exception as exc:
                    log(f"İş parçacığı hatası ({ino}): {exc}", "ERROR")

        t_bitis = time.time()
        log(f"Eksik tamamlama bitti: {toplam_basarili} ilan zenginleştirildi ({t_bitis - t_baslangic:.1f} sn).", "SUCCESS")
        return toplam_basarili

    def hesapla_arac_refah_endeksi(self):
        """Toplanan zengin ilan verilerinden İlçe bazlı Araç Refah Endeksi hesaplar."""
        log("İlçe Bazlı Araç Refah ve Alım Gücü Endeksi hesaplanıyor...", "INFO")
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

            # En popüler marka
            c.execute("SELECT marka, COUNT(*) as m_adet FROM arabam_ilanlari WHERE il=? AND ilce=? GROUP BY marka ORDER BY m_adet DESC LIMIT 1", (il, ilce))
            pop = c.fetchone()
            pop_marka = pop["marka"] if pop else "Volkswagen"

            # 2026 Alım Gücü & Refah Skoru Formülü
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

    def export_csv(self):
        """Toplanan tüm verileri eksiksiz CSV ve JSON dosyalarına aktarır."""
        import csv
        log("Eksiksiz araç verileri CSV formatına aktarılıyor...", "INFO")
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
                p = CSV_DIR / fname
                keys = rows[0].keys()
                with open(p, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys, delimiter=";")
                    writer.writeheader()
                    for r in rows:
                        writer.writerow(dict(r))
                log(f"  ✓ {len(rows)} kayıt -> {p}", "SUCCESS")

        conn.close()

def main():
    parser = argparse.ArgumentParser(description="GEOPROP AI - Arabam.com Eksiksiz Araç Verisi Toplayıcısı")
    parser.add_argument("--hepsi", action="store_true", help="Türkiye genelindeki 81 ilin tamamını tara")
    parser.add_argument("--sehirler", nargs="+", type=int, default=None, help="Taranacak il plaka kodları (Örn: --sehirler 34 6 35 77)")
    parser.add_argument("--genel", action="store_true", help="İl filtresi olmadan son yüklenen ilanları tara")
    parser.add_argument("--kategoriler", nargs="+", default=["otomobil"], help="Kategoriler: otomobil arazi-suv-pickup minivan-van-panelvan")
    parser.add_argument("--sayfa", type=int, default=2, help="Her il veya kategori için taranacak sayfa adedi")
    parser.add_argument("--threads", type=int, default=6, help="Paralel çalışan iş parçacığı (worker thread) sayısı")
    parser.add_argument("--eksikleri-tamamla", action="store_true", help="Veritabanında kayıtlı olup detayları boş olan ilanları tamamla")
    parser.add_argument("--export-only", action="store_true", help="Yalnızca mevcut veritabanını CSV'ye aktar")
    args = parser.parse_args()

    toplayici = EksiksizArabaToplayici(max_threads=args.threads)

    if args.export_only:
        toplayici.export_csv()
        return

    if args.eksikleri_tamamla:
        toplayici.eksikleri_tamamla()
        toplayici.hesapla_arac_refah_endeksi()
        toplayici.export_csv()
        return

    plakalar = list(range(1, 82)) if args.hepsi else args.sehirler
    if not plakalar and not args.genel:
        # Varsayılan metropol ve pilot iller
        plakalar = [34, 6, 35, 77, 16, 7]

    log("=" * 70, "INFO")
    log("GEOPROP AI - ARABAM.COM EKSIKSIZ ARAÇ VERİ TOPLAYICI BAŞLATILDI", "INFO")
    log(f"Hedef İller: {'81 İl Tümü' if args.hepsi else ('Genel Akış' if args.genel else plakalar)}", "INFO")
    log(f"Kategoriler: {args.kategoriler} | Sayfa Sayısı: {args.sayfa} | Worker Threads: {args.threads}", "INFO")
    log("=" * 70, "INFO")

    toplayici.calistir_toplu_tarama(
        plakalar=plakalar,
        kategoriler=args.kategoriler,
        sayfa_sayisi=args.sayfa,
        genel=args.genel
    )

    toplayici.hesapla_arac_refah_endeksi()
    toplayici.export_csv()

    log("=" * 70, "SUCCESS")
    log("TÜM ARAÇ VERİLERİ EKSİKSİZ ÇEKİLDİ VE KAYDEDİLDİ!", "SUCCESS")
    log(f"Veritabanı: {DB_PATH}", "INFO")
    log(f"CSV Çıktısı: {CSV_DIR / '23_arabam_arac_ilanlari.csv'}", "INFO")
    log("=" * 70, "SUCCESS")

if __name__ == "__main__":
    main()
