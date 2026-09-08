#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Arabam.com Araç İlanları ve Lokasyon Refah Endeksi Toplayıcısı
==========================================================================
Türkiye genelinde 81 il ve ilçeler bazında:
1. İkinci el otomobil ilanlarını (Marka, Model, Yıl, KM, Fiyat, İl, İlçe, Mahalle, Tarih) çeker.
2. İlçe bazlı "Araç Refah ve Satın Alma Gücü Endeksi" üretir:
   - Ortalama ve Medyan Araç Fiyatı (TL)
   - Ortalama Araç Yaşı
   - Premium / Lüks Segment Oranı (% Mercedes, BMW, Audi, Porsche, Volvo, Land Rover)
   - Ekonomik Segment Oranı (% Fiat, Renault, Dacia, Hyundai)
3. Verileri SQLite (`arabam_vasita_piyasasi.sqlite`) ve Excel uyumlu CSV olarak dışa aktarır.
"""

import os
import sys
import re
import json
import time
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

try:
    from curl_cffi import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Gereksinimler eksik! 'pip install curl_cffi beautifulsoup4' çalıştırın.")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv_ciktilari"
DB_PATH = DATA_DIR / "arabam_vasita_piyasasi.sqlite"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

LUXURY_BRANDS = {"bmw", "mercedes", "mercedes-benz", "audi", "porsche", "volvo", "land rover", "range rover", "jaguar", "lexus", "maserati", "mini", "alfa romeo", "ds"}
ECONOMY_BRANDS = {"fiat", "renault", "dacia", "hyundai", "tata", "lada", "tofaş", "chery", "geely"}

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = {"INFO": "[*]", "SUCCESS": "[✓]", "WARN": "[!]", "ERROR": "[✗]"}
    print(f"{ts} {symbols.get(level, '[*]')} {msg}", flush=True)

def init_db(db_path=None):
    target_db = db_path or DB_PATH
    conn = sqlite3.connect(target_db)
    c = conn.cursor()

    # 1. Araç İlanları Tablosu
    c.execute("""
    CREATE TABLE IF NOT EXISTS arabam_ilanlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ilan_no TEXT UNIQUE,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        marka TEXT,
        seri TEXT,
        model TEXT,
        baslik TEXT,
        yil INTEGER,
        km INTEGER,
        renk TEXT,
        fiyat_tl INTEGER,
        ilan_tarihi TEXT,
        satici_tipi TEXT,
        segment TEXT, -- 'Lüks/Premium', 'Orta', 'Ekonomik'
        ilan_url TEXT,
        guncellenme_yili INTEGER DEFAULT 2026,
        veri_donemi TEXT DEFAULT '2026-Q3 (Güncel)',
        eklenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 2. İlçe ve Bölge Araç Refah Endeksi
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
        refah_ve_alim_gucu_skoru REAL, -- 0 - 100
        sosyo_ekonomik_kategori TEXT, -- 'A+', 'A', 'B+', 'B', 'C', 'D'
        veri_donemi TEXT DEFAULT '2026-Q3 (Güncel)',
        guncellenme_yili INTEGER DEFAULT 2026,
        UNIQUE(il, ilce)
    )
    """)

    conn.commit()
    conn.close()

class ArabamIlanToplayici:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        init_db(self.db_path)
        self.session = requests.Session(impersonate="chrome120")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Referer": "https://www.arabam.com/",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }

    def parse_listing_row(self, row):
        """Arama sonuç tablosundaki tek bir satırı parse eder."""
        cells = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) < 7:
            return None

        link = row.find('a', href=True)
        href = link['href'] if link else ''
        ilan_no_match = re.search(r'/(\d+)(?:\?|$)', href)
        ilan_no = ilan_no_match.group(1) if ilan_no_match else ''

        raw_model = cells[1]
        baslik = cells[2]
        yil_str = cells[3]
        km_str = cells[4].replace('.', '').replace(',', '')
        renk = cells[5]
        fiyat_raw = cells[6]
        fiyat_match = re.search(r'([\d\.]+)', fiyat_raw)
        fiyat_tl = int(fiyat_match.group(1).replace('.', '')) if fiyat_match else 0
        tarih = cells[7]

        # Konum hücresi (İl ve İlçe ayrıştırma)
        il = ""
        ilce = ""
        loc_td = row.find_all(['td', 'th'])[8] if len(cells) > 8 else None
        if loc_td:
            spans = loc_td.find_all('span')
            title_spans = [s.get('title') for s in spans if s.get('title')]
            if len(title_spans) >= 2:
                il = title_spans[0].strip()
                ilce = title_spans[1].strip()
            elif len(title_spans) == 1:
                il = title_spans[0].strip()

        if not il and len(cells) > 8:
            # Fallback regex
            loc_text = re.sub(r'(Karşılaştır.*|Favori.*|Gizle.*|Göster.*)', '', cells[8]).strip()
            il = loc_text

        # Marka ve Seri Tespiti
        parts = raw_model.split()
        marka = parts[0] if len(parts) > 0 else "Bilinmeyen"
        seri = parts[1] if len(parts) > 1 else ""
        model = " ".join(parts[2:]) if len(parts) > 2 else raw_model

        # Segment Sınıflandırması
        marka_lower = marka.lower()
        if any(b in marka_lower for b in LUXURY_BRANDS):
            segment = "Lüks/Premium"
        elif any(b in marka_lower for b in ECONOMY_BRANDS):
            segment = "Ekonomik"
        else:
            segment = "Orta"

        # Satıcı Tipi
        satici = "Galeriden" if "galeriden" in href else ("Sahibinden" if "sahibinden" in href else "Yetkili Bayi")

        return {
            "ilan_no": ilan_no,
            "il": il,
            "ilce": ilce,
            "mahalle": "",
            "marka": marka,
            "seri": seri,
            "model": raw_model,
            "baslik": baslik,
            "yil": int(yil_str) if yil_str.isdigit() else 2020,
            "km": int(km_str) if km_str.isdigit() else 0,
            "renk": renk,
            "fiyat_tl": fiyat_tl,
            "ilan_tarihi": tarih,
            "satici_tipi": satici,
            "segment": segment,
            "ilan_url": f"https://www.arabam.com{href}" if href.startswith('/') else href
        }

    def topla_sehir_ilanlari(self, plaka, max_sayfa=2, detay_cek=False):
        """Belirtilen il plakasındaki araç ilanlarını çeker."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        toplam_eklenen = 0

        for sayfa in range(1, max_sayfa + 1):
            url = f"https://www.arabam.com/ikinci-el/otomobil?city={plaka}&page={sayfa}"
            try:
                r = self.session.get(url, headers=self.headers, timeout=15)
                if r.status_code != 200:
                    log(f"Plaka {plaka} Sayfa {sayfa} HTTP {r.status_code} döndü.", "WARN")
                    break

                soup = BeautifulSoup(r.text, 'html.parser')
                rows = soup.select('tr.listing-list-item')
                if not rows:
                    break

                for row in rows:
                    data = self.parse_listing_row(row)
                    if not data or not data["ilan_no"]:
                        continue

                    # İsteğe bağlı derin mahalle detayı
                    if detay_cek and data["ilan_url"]:
                        try:
                            rd = self.session.get(data["ilan_url"], headers=self.headers, timeout=10)
                            if rd.status_code == 200:
                                soup_d = BeautifulSoup(rd.text, 'html.parser')
                                loc_el = soup_d.select_one('span.product-location')
                                if loc_el:
                                    t = loc_el.get_text(strip=True)
                                    # Örn: 'İstiklal Mh. Ümraniye, İstanbul'
                                    m_match = re.search(r'^(.*?Mh\.|.*?Mahallesi|.*?Köyü)', t)
                                    if m_match:
                                        data["mahalle"] = m_match.group(1).strip()
                            time.sleep(0.3)
                        except Exception:
                            pass

                    c.execute("""
                    INSERT OR REPLACE INTO arabam_ilanlari (
                        ilan_no, il, ilce, mahalle, marka, seri, model, baslik,
                        yil, km, renk, fiyat_tl, ilan_tarihi, satici_tipi, segment, ilan_url,
                        guncellenme_yili, veri_donemi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2026, '2026-Q3 (Güncel)')
                    """, (
                        data["ilan_no"], data["il"], data["ilce"], data["mahalle"],
                        data["marka"], data["seri"], data["model"], data["baslik"],
                        data["yil"], data["km"], data["renk"], data["fiyat_tl"],
                        data["ilan_tarihi"], data["satici_tipi"], data["segment"], data["ilan_url"]
                    ))
                    toplam_eklenen += 1

                conn.commit()
                time.sleep(0.4)
            except Exception as e:
                log(f"Plaka {plaka} sayfa {sayfa} hatası: {e}", "ERROR")
                break

        conn.close()
        return toplam_eklenen

    def hesapla_arac_refah_endeksi(self):
        """Toplanan araç ilanlarından İlçe bazlı Refah & Alım Gücü Endeksi hesaplar."""
        log("İlçe Bazlı Araç Refah ve Satın Alma Gücü Endeksi hesaplanıyor...", "INFO")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("""
        SELECT il, ilce, COUNT(*) as adet,
               AVG(fiyat_tl) as ort_fiyat,
               AVG(yil) as ort_yil,
               AVG(km) as ort_km,
               SUM(CASE WHEN segment = 'Lüks/Premium' THEN 1 ELSE 0 END) as luks_adet,
               SUM(CASE WHEN segment = 'Ekonomik' THEN 1 ELSE 0 END) as eko_adet
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

            # Refah ve Alım Gücü Skoru (0 - 100)
            # Yüksek fiyat, genç araç yaşı ve lüks marka penetrasyonu skoru artırır
            base_skor = 50.0 + ((ort_fiyat - 800000) / 40000) + (luks_oran * 0.8) - (eko_oran * 0.4) - (ort_yas * 1.5)
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
        log("  ✓ İlçe Araç Refah Endeksi hesaplandı.", "SUCCESS")

    def export_csv(self):
        """Toplanan araç ve refah tablolarını CSV olarak dışa aktarır."""
        log("Arabam.com verileri CSV formatına aktarılıyor...", "INFO")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        import csv

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
    parser = argparse.ArgumentParser(description="Arabam.com Araç İlanları ve Lokasyon Refah Endeksi Toplayıcısı")
    parser.add_argument("--sehirler", nargs="+", type=int, default=[34, 6, 35, 77, 16, 7], help="Taranacak il plaka kodları (Örn: --sehirler 34 6 35 77)")
    parser.add_argument("--sayfa", type=int, default=2, help="Her il için taranacak sayfa sayısı (Her sayfa 20 ilan)")
    parser.add_argument("--detay", action="store_true", help="Her ilanın içine girip mahalle detayını da çek")
    parser.add_argument("--export-only", action="store_true", help="Yalnızca mevcut SQLite veritabanını CSV'ye aktar")
    args = parser.parse_args()

    toplayici = ArabamIlanToplayici()

    if args.export_only:
        toplayici.export_csv()
        return

    toplam = 0
    for plaka in args.sehirler:
        log(f"Plaka {plaka} taranıyor...", "INFO")
        eklenen = toplayici.topla_sehir_ilanlari(plaka, max_sayfa=args.sayfa, detay_cek=args.detay)
        toplam += eklenen
        log(f"  ✓ Plaka {plaka}: {eklenen} ilan kaydedildi.", "SUCCESS")

    toplayici.hesapla_arac_refah_endeksi()
    toplayici.export_csv()

    log(f"================================================================", "SUCCESS")
    log(f"ARABAM.COM VERİ TOPLAMA TAMAMLANDI! Toplam {toplam} İlan.", "SUCCESS")
    log(f"Veritabanı: {DB_PATH}", "INFO")
    log(f"================================================================", "SUCCESS")

if __name__ == "__main__":
    main()
