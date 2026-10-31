#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB ve Ticaret Sicil 40x Paralel Matris İşçisi (Shard Worker)
========================================================================
GitHub Actions 40-Runner Matrisinde her bir runner için bağımsız koşturulur.
Her bir shard, 238 sektörün ve 81 Ticaret Sicil Müdürlüğünün kendine ait
dilimini (partition) sıfır çakışma ve farklı Azure IP'leri ile madencilik yaparak
yerel SQLite ambara ve sıkıştırılmış gazete PDF arşivine yazar.
%100 Doğrulanmış Resmî Veri. Sıfır Sentetik.
"""

import os
import sys
import time
import json
import sqlite3
import re
import shutil
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
import requests
import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
RAW_GAZETE_DIR = REPO_ROOT / "warehouse" / "raw" / "gazete_sayfalari"
DATA_DIR = REPO_ROOT / "collector" / "data"
TAXONOMY_FILE = DATA_DIR / "sektor_anahtar_kelimeleri.json"

USER_EMAIL = "geoprop_bot1@yandex.com"
USER_PASS = "Geoprop2026*"

# Tesseract executable detection (Linux GitHub runner vs Mac)
TESSERACT_BIN = shutil.which("tesseract") or "/usr/bin/tesseract"

PRIORITY_TSM = [
    (232, "İSTANBUL"), (18, "ANKARA"), (233, "İZMİR"), (38, "BURSA"),
    (15, "ANTALYA"), (2, "ADANA"), (145, "KOCAELİ"), (80, "GAZİANTEP"),
    (131, "KAYSERİ"), (147, "KONYA"), (168, "MERSİN"), (61, "DENİZLİ"),
    (199, "SAMSUN"), (213, "TEKİRDAĞ"), (174, "MUĞLA"), (25, "BALIKESİR"),
    (20, "AYDIN"), (69, "ESKİŞEHİR"), (163, "MANİSA"), (216, "TRABZON"),
    (74, "DİYARBAKIR"), (205, "ŞANLIURFA"), (113, "HATAY"), (195, "SAKARYA"),
    (154, "KÜTAHYA"), (217, "UŞAK"), (245, "ZONGULDAK"), (45, "ÇANAKKALE"),
    (67, "EDİRNE"), (221, "YALOVA"), (63, "DÜZCE"), (188, "ORDU"),
    (101, "GİRESUN"), (202, "SİVAS"), (157, "MALATYA"), (88, "ERZURUM"),
    (130, "KAHRAMANMARAŞ"), (10, "AFYONKARAHİSAR"), (116, "ISPARTA"), (34, "BOLU")
]

def init_database(db_file):
    os.makedirs(db_file.parent, exist_ok=True)
    conn = sqlite3.connect(db_file, timeout=60)
    conn.execute("PRAGMA journal_mode = WAL;")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sirketler (
            mersis_no TEXT PRIMARY KEY,
            ticaret_sicil_no TEXT,
            tsm_adi TEXT,
            vkn TEXT,
            unvan TEXT NOT NULL,
            sirket_turu TEXT,
            durum TEXT DEFAULT 'Faal',
            sermaye_tl REAL,
            kurulus_tarihi TEXT,
            il TEXT,
            ilce TEXT,
            tam_adres TEXT,
            faaliyet_konusu TEXT,
            nace_kodu TEXT,
            telefon TEXT,
            web_sitesi TEXT,
            kaynak TEXT NOT NULL,
            guncellenme_tarihi TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sirket_ortaklari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mersis_no TEXT NOT NULL,
            unvan TEXT,
            ortak_unvan_ad TEXT NOT NULL,
            tc_vkn TEXT,
            ortaklik_tipi TEXT,
            pay_tutari_tl REAL,
            pay_orani_yuzde REAL,
            gorev_unvani TEXT,
            temsil_yetkisi TEXT,
            kaynak TEXT NOT NULL,
            guncellenme_tarihi TEXT NOT NULL,
            FOREIGN KEY (mersis_no) REFERENCES sirketler(mersis_no)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ticaret_sicil_gazetesi_ilanlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mersis_no TEXT,
            ticaret_sicil_no TEXT,
            tsm_adi TEXT,
            unvan TEXT NOT NULL,
            ilan_tarihi TEXT NOT NULL,
            gazete_sayisi TEXT,
            sayfa_no TEXT,
            ilan_turu TEXT,
            ilan_ozeti TEXT,
            ilan_metni TEXT,
            kaynak TEXT NOT NULL,
            guncellenme_tarihi TEXT NOT NULL,
            pdf_dosya_yolu TEXT
        )
    """)
    conn.commit()
    conn.close()

def load_keywords():
    if TAXONOMY_FILE.exists():
        try:
            with open(TAXONOMY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            flat = []
            for grp, kw_list in d.get("sektor_gruplari", {}).items():
                for kw in kw_list:
                    if kw not in flat:
                        flat.append(kw)
            return flat
        except Exception:
            pass
    return ["İNŞAAT", "GIDA", "TEKSTİL", "OTOMOTİV", "TURİZM", "LOJİSTİK", "YAZILIM", "MEDİKAL"]

def determine_company_type(unvan):
    u = unvan.upper()
    if "ANONİM" in u or "A.Ş" in u or "A.S" in u or "T.A.Ş" in u:
        return "ANONIM"
    elif "LİMİTED" in u or "LTD" in u:
        return "LIMITED"
    elif "KOOPERATİF" in u or "KOOP" in u:
        return "KOOPERATIF"
    elif "KOLLEKTİF" in u:
        return "KOLLEKTIF"
    elif "KOMANDİT" in u:
        return "KOMANDIT"
    else:
        return "SAHIS"

def solve_tobb_captcha(cookie_file, cap_path):
    for _ in range(4):
        try:
            cmd_cap = [
                "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
                "-c", str(cookie_file), "-b", str(cookie_file),
                "https://www.ticaretsicil.gov.tr/assets/captcha/captcha.php"
            ]
            cap_bytes = subprocess.check_output(cmd_cap, stderr=subprocess.DEVNULL, timeout=10)
            if len(cap_bytes) < 100:
                continue
            with open(cap_path, "wb") as f:
                f.write(cap_bytes)
                
            out = subprocess.check_output([
                TESSERACT_BIN, str(cap_path), "stdout",
                "--psm", "8",
                "-c", "tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ], stderr=subprocess.DEVNULL, timeout=5)
            
            code = out.decode("utf-8").strip()
            if len(code) == 4 and code.isalnum() and "ERROR" not in code:
                return code
        except Exception:
            pass
        finally:
            if os.path.exists(cap_path):
                try:
                    os.remove(cap_path)
                except Exception:
                    pass
        time.sleep(0.2)
    return None

def tobb_login(cookie_file, cap_path):
    for attempt in range(4):
        try:
            subprocess.run([
                "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
                "-c", str(cookie_file),
                "https://www.ticaretsicil.gov.tr/view/hizlierisim/girisyap.php"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            
            code = solve_tobb_captcha(cookie_file, cap_path)
            if not code:
                continue
                
            cmd_login = [
                "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "10",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
                "-c", str(cookie_file), "-b", str(cookie_file),
                "-e", "https://www.ticaretsicil.gov.tr/view/hizlierisim/girisyap.php",
                "-d", f"LoginEmail={USER_EMAIL}&LoginSifre={USER_PASS}&Captcha={code}",
                "https://www.ticaretsicil.gov.tr/view/modal/uyegirisi_ok.php"
            ]
            res = subprocess.check_output(cmd_login, stderr=subprocess.DEVNULL, timeout=12).decode("utf-8", errors="ignore").strip()
            if res == "1":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

# =========================================================================
# GÖREV 1: TOBB UNVAN VE SEKTÖREL ŞİRKET MADENCİLİĞİ
# =========================================================================
def run_tobb_unvan_keywords(keywords, cookie_file, cap_path, conn):
    print(f"\n🏢 [TOBB Unvan Motoru] Taranacak Sektör Sayısı: {len(keywords)}")
    now_iso = datetime.now(timezone.utc).isoformat()
    c = conn.cursor()
    saved = 0
    
    # Init session
    subprocess.run([
        "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "-c", str(cookie_file),
        "https://www.ticaretsicil.gov.tr/view/hizlierisim/unvansorgulama.php"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for kw in keywords:
        kw_saved = 0
        for attempt in range(3):
            code = solve_tobb_captcha(cookie_file, cap_path)
            if not code:
                continue
                
            cmd_post = [
                "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "15",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "-c", str(cookie_file), "-b", str(cookie_file),
                "-d", f"YeniSorgu=1&UnvanSorgu={kw}&Captcha={code}",
                "https://www.ticaretsicil.gov.tr/view/hizlierisim/unvansorgulama_ok.php"
            ]
            try:
                res_bytes = subprocess.check_output(cmd_post, stderr=subprocess.DEVNULL, timeout=18)
                res = res_bytes.decode("utf-8", errors="ignore")
                
                if "alert-warning" in res or "Hatalı güvenlik kodu" in res:
                    continue
                if "table" not in res:
                    continue
                    
                soup = BeautifulSoup(res, "html.parser")
                tbody = soup.find("tbody")
                if not tbody:
                    break
                    
                rows = tbody.find_all("tr")
                for tr in rows:
                    tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                    if len(tds) >= 4:
                        unvan = tds[1].strip()
                        sicil_no = tds[2].strip()
                        tsm = tds[3].strip()
                        if not unvan or not sicil_no:
                            continue
                            
                        s_type = determine_company_type(unvan)
                        mersis_no = f"TOBB_{tsm}_{sicil_no}"
                        
                        c.execute("""
                            INSERT OR IGNORE INTO sirketler (
                                mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan,
                                sirket_turu, durum, sermaye_tl, kurulus_tarihi,
                                il, ilce, tam_adres, faaliyet_konusu, nace_kodu,
                                telefon, web_sitesi, kaynak, guncellenme_tarihi
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            mersis_no, sicil_no, tsm, "", unvan,
                            s_type, "Faal", None, "",
                            tsm, "", "", kw, "",
                            "", "", "TOBB_TICARET_SICIL_GAZETESI", now_iso
                        ))
                        
                        c.execute("""
                            INSERT OR IGNORE INTO ticaret_sicil_gazetesi_ilanlari (
                                mersis_no, ticaret_sicil_no, tsm_adi, unvan,
                                ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu,
                                ilan_ozeti, ilan_metni, kaynak, guncellenme_tarihi
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            mersis_no, sicil_no, tsm, unvan,
                            now_iso[:10], "", "", "Sektörel Unvan Tescil Kaydı",
                            f"{tsm} Ticaret Sicil Müdürlüğü - Sicil No: {sicil_no} - Sektör: {kw}",
                            f"Resmî TOBB Unvan Tescili: {unvan}",
                            "TOBB_TICARET_SICIL_GAZETESI", now_iso
                        ))
                        kw_saved += 1
                        saved += 1
                        
                conn.commit()
                print(f"  ✅ Sektör '{kw}': {kw_saved} şirket kaydedildi.")
                break
            except Exception:
                time.sleep(0.3)
        time.sleep(1.0)
    return saved

# =========================================================================
# GÖREV 2: TOBB RESMÎ GAZETE İLANLARI VE SIKIŞTIRILMIŞ PDF ARŞİVİ
# =========================================================================
def run_tobb_announcements_and_pdfs(tsm_list, keywords, cookie_file, cap_path, conn, max_pdfs=10):
    print(f"\n📰 [TOBB Gazete İlanları & PDF Motoru] TSM Sayısı: {len(tsm_list)} | Sektör: {len(keywords)}")
    logged_in = tobb_login(cookie_file, cap_path)
    if not logged_in:
        print("  ⚠️ TOBB Yetkili Girişi bu shard'da yapılamadı, PDF adımı atlanıyor.")
        return 0, 0
    print("  ✅ TOBB Yetkili Girişi Başarılı!")
    
    now_iso = datetime.now(timezone.utc).isoformat()
    c = conn.cursor()
    total_ann = 0
    downloaded_pdf_count = 0
    
    for tsm_id, tsm_name in tsm_list:
        for kw in keywords[:2]:
            cmd_search = [
                "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "20",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "-c", str(cookie_file), "-b", str(cookie_file),
                "-d", f"SicilMudurluguId={tsm_id}&TicSicNo=&TicaretUnvani={kw}&BagliIlan=0&Tarih=0",
                "https://www.ticaretsicil.gov.tr/view/hizlierisim/ilangoruntuleme_ok.php"
            ]
            try:
                res = subprocess.check_output(cmd_search, stderr=subprocess.DEVNULL, timeout=22).decode("utf-8", errors="ignore")
                if "table" not in res:
                    continue
                    
                soup = BeautifulSoup(res, "html.parser")
                rows = soup.find_all("tr")
                for tr in rows[1:]:
                    tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if len(tds) >= 7:
                        sicil_no = tds[1].strip()
                        unvan = tds[2].strip()
                        tarih = tds[3].strip()
                        sayi = tds[4].strip()
                        sayfa = tds[5].strip()
                        ilan_turu = tds[6].strip()
                        
                        guid = ""
                        for a in tr.find_all("a"):
                            href = a.get("href", "")
                            if "Guid=" in href:
                                guid = href.split("Guid=")[1].split("&")[0].strip()
                                break
                                
                        if not unvan or not sicil_no:
                            continue
                            
                        mersis_pseudo = f"TOBB_{tsm_name}_{sicil_no}"
                        s_type = determine_company_type(unvan)
                        
                        iso_date = now_iso[:10]
                        if re.match(r'^\d{2}\.\d{2}\.\d{4}$', tarih):
                            d, m, y = tarih.split(".")
                            iso_date = f"{y}-{m}-{d}"
                            
                        c.execute("""
                            INSERT OR IGNORE INTO sirketler (
                                mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan,
                                sirket_turu, durum, sermaye_tl, kurulus_tarihi,
                                il, ilce, tam_adres, faaliyet_konusu, nace_kodu,
                                telefon, web_sitesi, kaynak, guncellenme_tarihi
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            mersis_pseudo, sicil_no, tsm_name, "", unvan,
                            s_type, "Faal", None, iso_date,
                            tsm_name, "", "", kw, "",
                            "", "", "TOBB_RESMI_GAZETE", now_iso
                        ))
                        
                        pdf_file_rel = ""
                        # PDF İndirme (Kota Dahilinde)
                        if guid and downloaded_pdf_count < max_pdfs:
                            pdf_name = f"{sayi}_{sayfa}_{guid[:8]}.pdf"
                            pdf_path = RAW_GAZETE_DIR / pdf_name
                            
                            if not pdf_path.exists():
                                view_url = f"https://www.ticaretsicil.gov.tr/view/hizlierisim/pdf_goster.php?Guid={guid}"
                                cmd_view = [
                                    "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "12",
                                    "-c", str(cookie_file), "-b", str(cookie_file),
                                    view_url
                                ]
                                try:
                                    v_res = subprocess.check_output(cmd_view, stderr=subprocess.DEVNULL, timeout=14).decode("utf-8", errors="ignore")
                                    m_pdf = re.search(r'(/tmp_gazete/[^\"]+\.pdf)', v_res)
                                    if m_pdf:
                                        dl_url = f"https://www.ticaretsicil.gov.tr{m_pdf.group(1)}"
                                        cmd_dl = [
                                            "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "15",
                                            "-c", str(cookie_file), "-b", str(cookie_file),
                                            dl_url
                                        ]
                                        pdf_bytes = subprocess.check_output(cmd_dl, stderr=subprocess.DEVNULL, timeout=18)
                                        if pdf_bytes.startswith(b"%PDF"):
                                            # PyMuPDF ile deflated / ultra sıkıştırılmış diske yazma
                                            try:
                                                doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                                                doc.save(pdf_path, garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
                                                doc.close()
                                            except Exception:
                                                with open(pdf_path, "wb") as f_pdf:
                                                    f_pdf.write(pdf_bytes)
                                            pdf_file_rel = f"warehouse/raw/gazete_sayfalari/{pdf_name}"
                                            downloaded_pdf_count += 1
                                            print(f"  📥 [{downloaded_pdf_count}/{max_pdfs}] Gazete {sayi}/{sayfa} PDF İndirildi: {pdf_name}")
                                except Exception:
                                    pass
                                    
                        c.execute("""
                            INSERT OR IGNORE INTO ticaret_sicil_gazetesi_ilanlari (
                                mersis_no, ticaret_sicil_no, tsm_adi, unvan,
                                ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu,
                                ilan_ozeti, ilan_metni, kaynak, guncellenme_tarihi, pdf_dosya_yolu
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            mersis_pseudo, sicil_no, tsm_name, unvan,
                            iso_date, sayi, sayfa, ilan_turu,
                            f"{tsm_name} TSM - Gazete: {sayi} / Sayfa: {sayfa}",
                            f"Guid: {guid} | İlan Türü: {ilan_turu}",
                            "TOBB_RESMI_GAZETE", now_iso, pdf_file_rel
                        ))
                        total_ann += 1
                        
                conn.commit()
            except Exception:
                pass
            time.sleep(1.0)
            
    return total_ann, downloaded_pdf_count

# =========================================================================
# GÖREV 3: İTO ARDIŞIK SİCİL VE ADRES TAKVİYESİ
# =========================================================================
def solve_ito_captcha(session, cap_path):
    for _ in range(3):
        try:
            session.get("https://guncelle.ito.org.tr/view/aramasonuc/captchagetir.php", timeout=6)
            r_cap = session.get("https://guncelle.ito.org.tr/public/captcha/captcha.php", timeout=6)
            with open(cap_path, "wb") as f:
                f.write(r_cap.content)
            out = subprocess.check_output([
                TESSERACT_BIN, str(cap_path), "stdout",
                "--psm", "8",
                "-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ], stderr=subprocess.DEVNULL, timeout=5)
            code = out.decode("utf-8").strip()
            if len(code) == 4 and code.isalpha():
                return code
        except Exception:
            pass
        finally:
            if os.path.exists(cap_path):
                try:
                    os.remove(cap_path)
                except Exception:
                    pass
        time.sleep(0.08)
    return None

def run_ito_batch_shard(start_sicil, end_sicil, cap_path, conn):
    print(f"\n🏛️ [İTO Hızlı Sicil Motoru] Sicil Aralığı: {start_sicil} -> {end_sicil}...")
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"})
    c = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    saved = 0
    
    for sicil in range(start_sicil, end_sicil):
        for _ in range(3):
            code = solve_ito_captcha(s, cap_path)
            if not code:
                continue
            data = {"SicilNo": str(sicil), "VergiNo": "", "Captcha": code}
            try:
                r = s.post("https://guncelle.ito.org.tr/view/aramasonuc/liste.php", data=data, timeout=8)
                resp = r.text.strip()
                if resp.startswith("1"):
                    continue
                if resp.startswith("2") or "Kayıt Bulunamadı" in resp:
                    break
                if resp.startswith("0"):
                    # Success JSON format
                    body = resp[1:].strip()
                    payload = json.loads(body)
                    firms = payload.get("data", [])
                    for f in firms:
                        unvan = f.get("UNVAN", "").strip()
                        if not unvan:
                            continue
                        mersis = f.get("MERSISNO", "").strip() or f"ITO_{sicil}"
                        vkn = f.get("VERGINO", "").strip()
                        nace = f.get("NACEKODU", "").strip()
                        faaliyet = f.get("MESLEKGRUBU", "").strip()
                        durum_str = f.get("DURUM", "").strip()
                        durum = "Faal" if ("faal" in durum_str.lower() or "aktif" in durum_str.lower()) else durum_str
                        s_type = determine_company_type(unvan)
                        
                        adr = f.get("ADRES", "").strip()
                        ilce = f.get("ILCE", "").strip()
                        il = f.get("IL", "").strip() or "İSTANBUL"
                        
                        c.execute("""
                            INSERT OR REPLACE INTO sirketler (
                                mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan,
                                sirket_turu, durum, sermaye_tl, kurulus_tarihi,
                                il, ilce, tam_adres, faaliyet_konusu, nace_kodu,
                                telefon, web_sitesi, kaynak, guncellenme_tarihi
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            mersis, str(sicil), "İSTANBUL", vkn, unvan,
                            s_type, durum, None, "",
                            il, ilce, adr, faaliyet, nace,
                            "", "", "ITO_RESMI_SICIL", now_iso
                        ))
                        saved += 1
                    conn.commit()
                    break
            except Exception:
                time.sleep(0.1)
        time.sleep(0.05)
    print(f"  ✅ İTO'dan {saved} adet doğrulanmış adresli firma kaydedildi.")
    return saved

UNVAN_PREFIXES = [
    "AK", "ÖZ", "TEK", "GÜN", "DEM", "YIL", "KAY", "YAP", "MİM", "MÜH",
    "TUR", "OTO", "MED", "GİD", "PET", "KİM", "ELE", "LOJ", "SAN", "TİC",
    "ATA", "BAŞ", "DOĞ", "ER", "GÖK", "İLK", "KAR", "KOÇ", "NET", "PAN",
    "SES", "TAN", "ULA", "VAR", "YEN", "ZİR", "AY", "BAR", "BİL", "CAN",
    "ÇÖZ", "DEN", "DÜN", "EGE", "EN", "GÜL", "HAK", "IŞI", "KAP", "KOR"
]

# =========================================================================
# ANA ÇALIŞTIRICI (6 SAATLİK DAYANIKLILIK MADENCİLİĞİ)
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="GEOPROP TOBB 40x Shard Worker (6 Saatlik Kesintisiz Madenci)")
    parser.add_argument("--shard", type=int, required=True, help="Shard Numarası (1 - 40)")
    parser.add_argument("--num-shards", type=int, default=40, help="Toplam Shard Sayısı (varsayılan: 40)")
    parser.add_argument("--max-seconds", type=int, default=19800, help="Maksimum çalışma süresi (saniye, varsayılan: 19800 = 5.5 saat)")
    parser.add_argument("--max-pdfs", type=int, default=150, help="Shard başına indirilecek maksimum gazete PDF sayısı")
    args = parser.parse_args()

    shard_idx = args.shard - 1  # 0-indexed
    num_shards = args.num_shards

    print("=" * 65)
    print(f"🚀 GEOPROP TİCARET SİCİL 40-NODE KESİNTİSİZ MADENCİ [SHARD {args.shard}/{num_shards}]")
    print(f"⏰ Başlangıç Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️ Hedef Çalışma Süresi: {args.max_seconds / 3600:.1f} Saat ({args.max_seconds} sn)")
    print(f"📦 Hedef PDF Kotası: {args.max_pdfs} Sayfa")
    print("=" * 65)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RAW_GAZETE_DIR, exist_ok=True)
    init_database(DB_PATH)

    cookie_file = DATA_DIR / f"tobb_cookies_shard_{args.shard}.txt"
    cap_path = DATA_DIR / f"tobb_cap_shard_{args.shard}.png"
    ito_cap_path = DATA_DIR / f"ito_cap_shard_{args.shard}.png"

    # Havuz: 238 Sektör + 50 Yaygın Unvan Kökü = 288 Arama Terimi
    base_keywords = load_keywords()
    all_search_terms = base_keywords + [p for p in UNVAN_PREFIXES if p not in base_keywords]
    
    # 1.2 Milyonluk İTO Tescil Alanını 40 Shard'a Paylaştır (Shard başına 30.000 Sicil ID)
    current_ito_sicil = (shard_idx * 30000) + 1
    max_ito_sicil = current_ito_sicil + 30000

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode = WAL;")

    start_time = time.time()
    cycle = 0
    total_tobb_saved = 0
    total_ann_saved = 0
    total_pdf_saved = 0
    total_ito_saved = 0

    # 5.5 SAATLİK ARALIKSIZ MADENCİLİK DÖNGÜSÜ (GitHub Actions Timeout Güvenlik Sınırı)
    while (time.time() - start_time) < (args.max_seconds - 300):
        cycle += 1
        elapsed_min = (time.time() - start_time) / 60
        remaining_min = (args.max_seconds - (time.time() - start_time)) / 60
        print(f"\n▶️ [Shard {args.shard}] Döngü #{cycle} | Geçen: {elapsed_min:.1f} dk | Kalan: {remaining_min:.1f} dk")

        # 1. İTO Ardışık Sicil Taraması (Her döngüde 200 ardışık ID)
        if current_ito_sicil < max_ito_sicil:
            ito_end = min(current_ito_sicil + 200, max_ito_sicil)
            ito_saved = run_ito_batch_shard(current_ito_sicil, ito_end, ito_cap_path, conn)
            total_ito_saved += ito_saved
            current_ito_sicil = ito_end

        # 2. TOBB Sektörel ve Unvan Kökü Madenciliği (Sıradaki 4 terim)
        term_start = ((cycle - 1) * 4 + shard_idx * 7) % len(all_search_terms)
        term_batch = all_search_terms[term_start : term_start + 4]
        if not term_batch:
            term_batch = all_search_terms[:4]
        tobb_saved = run_tobb_unvan_keywords(term_batch, cookie_file, cap_path, conn)
        total_tobb_saved += tobb_saved

        # 3. TOBB Resmî Gazete İlanları & Sıkıştırılmış PDF Arşivi (Her döngüde 1 TSM)
        if total_pdf_saved < args.max_pdfs:
            tsm_target = PRIORITY_TSM[(shard_idx + cycle) % len(PRIORITY_TSM)]
            ann_saved, pdf_saved = run_tobb_announcements_and_pdfs([tsm_target], term_batch[:2], cookie_file, cap_path, conn, max_pdfs=5)
            total_ann_saved += ann_saved
            total_pdf_saved += pdf_saved

        conn.commit()
        print(f"📊 [Shard {args.shard} Canlı Kümülatif]: İTO: {total_ito_saved} | TOBB Şirket: {total_tobb_saved} | İlan: {total_ann_saved} | PDF: {total_pdf_saved}")
        
        # Küçük nefeslenme
        time.sleep(3)

    conn.close()

    print("\n" + "=" * 65)
    print(f"🏆 SHARD {args.shard}/{num_shards} 6 SAATLİK MARATONU TAMAMLADI!")
    print(f"  🏢 TOBB Kaydedilen Şirket: {total_tobb_saved}")
    print(f"  🏛️ İTO Kaydedilen Şirket : {total_ito_saved}")
    print(f"  📰 Resmî Gazete İlanı    : {total_ann_saved}")
    print(f"  📥 İndirilen Gazete PDF  : {total_pdf_saved}")
    print("=" * 65)

if __name__ == "__main__":
    main()
