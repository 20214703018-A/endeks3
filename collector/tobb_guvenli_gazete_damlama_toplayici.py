#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB Güvenli Gazete PDF Damlama Toplayıcı (Stealth Trickle Harvester)
=============================================================================
Radara yakalanmadan, insansı rastgele aralıklarla (15 - 30 saniye) TOBB'dan
öncelikli şirketlerin (Kuruluş, Müdür Seçimi, Hisse Devri) resmî gazete PDF suretlerini çeker.
PyMuPDF ile ultra-sıkıştırır, 50'şerli tar.gz paketleri halinde kalıcı GitHub Release'e
yükler ve yerel disk alanını korumak için geçici dosyaları anında temizler.

%100 Resmî Hükümet Belgesi. Sıfır Sentetik.
"""

import os
import sys
import time
import random
import re
import json
import sqlite3
import tarfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
COOKIE_FILE = REPO_ROOT / "collector" / "data" / "tobb_auth_cookies.txt"
TEMP_BUNDLE_DIR = REPO_ROOT / "warehouse" / "raw" / "temp_bundle"
BUNDLE_ARCHIVE_DIR = REPO_ROOT / "warehouse" / "raw" / "bundles"

USER_EMAIL = "geoprop_bot1@yandex.com"
USER_PASS = "Geoprop2026*"
BUNDLE_SIZE = 50  # Her 50 sayfada bir paket yap ve GitHub'a yükle

def ensure_authenticated():
    cmd = [
        "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
        "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
        "https://www.ticaretsicil.gov.tr/index.php"
    ]
    try:
        res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        if "cikis.php" in res.lower() or "geoprop" in res.lower():
            return True
    except Exception:
        pass
        
    print("🔑 [Damlama Motoru] TOBB Oturumu yenileniyor...")
    from collector.tobb_turkiye_geneli_hizli_toplayici import solve_captcha
    cap_path = REPO_ROOT / "collector" / "data" / "tobb_login_cap_trickle.png"
    subprocess.run(["curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8", "-c", str(COOKIE_FILE), "https://www.ticaretsicil.gov.tr/view/hizlierisim/girisyap.php"], stdout=subprocess.DEVNULL)
    
    for attempt in range(5):
        code = solve_captcha(cap_path)
        if not code:
            continue
            
        cmd_login = [
            "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "10",
            "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
            "-d", f"LoginEmail={USER_EMAIL}&LoginSifre={USER_PASS}&Captcha={code}",
            "https://www.ticaretsicil.gov.tr/view/modal/uyegirisi_ok.php"
        ]
        try:
            r = subprocess.check_output(cmd_login, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            if r == "1":
                print("✅ TOBB Oturumu Açıldı!")
                return True
        except Exception:
            pass
        time.sleep(0.5)
        
    return False

def get_next_priority_batch(conn, limit=200):
    c = conn.cursor()
    # Faz 1 Öncelik: 2000 Yılı ve Sonrası (Kullanıcı Direktifi: ilan_tarihi >= '2000-01-01' veya Sayı >= 4950)
    # İç Öncelik: Kuruluş > Müdür / Temsil > Hisse Devri > Sermaye Artırımı > Diğerleri
    query = """
        SELECT id, mersis_no, tsm_adi, unvan, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni, ilan_tarihi
        FROM ticaret_sicil_gazetesi_ilanlari
        WHERE (pdf_dosya_yolu IS NULL OR pdf_dosya_yolu = '')
          AND ilan_metni LIKE '%Guid:%'
          AND (ilan_tarihi >= '2000-01-01' OR CAST(gazete_sayisi AS INT) >= 4950)
        ORDER BY 
            CASE 
                WHEN UPPER(ilan_turu) LIKE '%KURULUŞ%' THEN 1
                WHEN UPPER(ilan_turu) LIKE '%MÜDÜR%' THEN 2
                WHEN UPPER(ilan_turu) LIKE '%TEMSİL%' THEN 3
                WHEN UPPER(ilan_turu) LIKE '%HİSSE%' THEN 4
                WHEN UPPER(ilan_turu) LIKE '%SERMAYE%' THEN 5
                ELSE 6
            END,
            id DESC
        LIMIT ?
    """
    return c.execute(query, (limit,)).fetchall()

def download_page_pdf(guid):
    view_url = f"https://www.ticaretsicil.gov.tr/view/hizlierisim/pdf_goster.php?Guid={guid}"
    cmd_view = [
        "curl", "-k", "-s", "--connect-timeout", "8", "--max-time", "20",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "-H", "Referer: https://www.ticaretsicil.gov.tr/view/hizlierisim/ilangoruntuleme.php",
        "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
        view_url
    ]
    try:
        html = subprocess.check_output(cmd_view, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        m_pdf = re.search(r'embed\s+src="([^"]+\.pdf)"', html) or re.search(r'src="(/tmp_gazete/[^"]+\.pdf)"', html)
        if not m_pdf:
            return None
            
        pdf_url = f"https://www.ticaretsicil.gov.tr{m_pdf.group(1)}"
        cmd_dl = [
            "curl", "-k", "-s", "--connect-timeout", "8", "--max-time", "20",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
            pdf_url
        ]
        pdf_bytes = subprocess.check_output(cmd_dl, stderr=subprocess.DEVNULL)
        if pdf_bytes.startswith(b"%PDF"):
            return pdf_bytes
    except Exception:
        pass
    return None

def compress_pdf_bytes(pdf_bytes):
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        # Deflate ve çöp toplama ile sıkıştır
        comp_bytes = doc.tobytes(garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
        doc.close()
        return comp_bytes
    except Exception:
        return pdf_bytes

def upload_bundle_to_github_release(bundle_path):
    print(f"☁️ [Kalıcı Bulut Depolama] {bundle_path.name} GitHub Release 'warehouse-latest' içine yükleniyor...")
    try:
        res = subprocess.run([
            "gh", "release", "upload", "warehouse-latest", str(bundle_path), "--clobber"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        if res.returncode == 0:
            print(f"✅ {bundle_path.name} başarıyla GitHub Release'e yüklendi!")
            return True
        else:
            print(f"⚠️ Yükleme hatası: {res.stderr.strip()}")
    except Exception as e:
        print(f"⚠️ GitHub Release bağlantı hatası: {e}")
    return False

def main():
    print("=" * 70)
    print("🥷 GEOPROP TOBB Güvenli Gazete PDF Damlama Toplayıcı (Stealth)")
    print("=" * 70)
    print("📍 Strateji: 15-30 sn rastgele insansı gecikme, PyMuPDF sıkıştırma, GitHub Release")
    
    os.makedirs(TEMP_BUNDLE_DIR, exist_ok=True)
    os.makedirs(BUNDLE_ARCHIVE_DIR, exist_ok=True)
    
    if not ensure_authenticated():
        print("❌ TOBB Yetkili Girişi Yapılamadı.")
        return
        
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode = WAL;")
    
    total_downloaded = 0
    current_bundle_files = []
    bundle_counter = int(time.time()) % 100000
    
    try:
        while True:
            batch = get_next_priority_batch(conn, limit=100)
            if not batch:
                print("ℹ️ Şu an indirilmeyi bekleyen kilit sayfa kalmadı. 60 sn bekleniyor...")
                time.sleep(60)
                continue
                
            for row in batch:
                ilan_id, mersis_no, tsm_adi, unvan, sayi, sayfa, ilan_turu, ilan_metni, ilan_tarihi = row
                
                m_guid = re.search(r'Guid:\s*([a-f0-9\-]+)', ilan_metni or "")
                if not m_guid:
                    continue
                guid = m_guid.group(1).strip()
                
                # İndir
                pdf_bytes = download_page_pdf(guid)
                if not pdf_bytes:
                    # Oturum düşmüş olabilir, kontrol et
                    if not ensure_authenticated():
                        time.sleep(10)
                    continue
                    
                # PyMuPDF ile sıkıştır
                compressed = compress_pdf_bytes(pdf_bytes)
                
                # Dosya adı: {sayi}_{sayfa}_{tsm}_{guid[:8]}.pdf
                safe_tsm = re.sub(r'[^a-zA-Z0-9]', '', tsm_adi or "TSM")
                pdf_filename = f"{sayi}_{sayfa}_{safe_tsm}_{guid[:8]}.pdf"
                pdf_dest = TEMP_BUNDLE_DIR / pdf_filename
                
                with open(pdf_dest, "wb") as f_out:
                    f_out.write(compressed)
                    
                current_bundle_files.append((ilan_id, pdf_filename))
                total_downloaded += 1
                
                tarih_str = ilan_tarihi if ilan_tarihi else "Tarih-Bilinmiyor"
                print(f"  📥 [#{total_downloaded}] [{tarih_str}] Sayı: {sayi} / Sayfa: {sayfa} ({safe_tsm}) - {len(compressed)/1024:.1f} KB -> {unvan[:40]}...")
                
                # Paket doldu mu? (50 dosya)
                if len(current_bundle_files) >= BUNDLE_SIZE:
                    bundle_name = f"gazete_pdf_bundle_{int(time.time())}_{bundle_counter:04d}.tar.gz"
                    bundle_path = BUNDLE_ARCHIVE_DIR / bundle_name
                    
                    print(f"\n📦 {len(current_bundle_files)} sayfa paketleniyor: {bundle_name}...")
                    with tarfile.open(bundle_path, "w:gz") as tar:
                        for _, p_name in current_bundle_files:
                            full_p = TEMP_BUNDLE_DIR / p_name
                            if full_p.exists():
                                tar.add(full_p, arcname=p_name)
                                
                    # GitHub Release'e yükle
                    uploaded = upload_bundle_to_github_release(bundle_path)
                    
                    # Veritabanında güncelle
                    c = conn.cursor()
                    now_iso = datetime.now(timezone.utc).isoformat()
                    bundle_ref = f"release://warehouse-latest/{bundle_name}" if uploaded else f"local://{bundle_path}"
                    
                    for i_id, p_name in current_bundle_files:
                        c.execute("""
                            UPDATE ticaret_sicil_gazetesi_ilanlari
                            SET pdf_dosya_yolu = ?, guncellenme_tarihi = ?
                            WHERE id = ?
                        """, (f"{bundle_ref}#{p_name}", now_iso, i_id))
                    conn.commit()
                    
                    # Yerel geçici PDF'leri temizle (DİSKİ KORU)
                    for _, p_name in current_bundle_files:
                        full_p = TEMP_BUNDLE_DIR / p_name
                        if full_p.exists():
                            try:
                                full_p.unlink()
                            except Exception:
                                pass
                    if uploaded and bundle_path.exists():
                        try:
                            bundle_path.unlink()  # Release'e gitti, yerel diski sıfır tut
                        except Exception:
                            pass
                            
                    current_bundle_files.clear()
                    bundle_counter += 1
                    print(f"🧹 Yerel disk temizlendi. Sıradaki pakete geçiliyor.\n")
                    
                # Güvenli İnsansı Gecikme (15 ila 30 saniye rastgele bekleme)
                stealth_wait = random.uniform(15.0, 30.0)
                time.sleep(stealth_wait)
                
    except KeyboardInterrupt:
        print("\n🛑 Kullanıcı tarafından durduruldu.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
