#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB 40x Paralel Gazete PDF İndirici (Shard Worker)
============================================================
GitHub Actions 40-Runner Matrisinde her bir bağımsız Microsoft Azure IP'sinde çalışır.
2000 yılından bugüne kadar (Faz 1) yayımlanmış ~400.000 resmî ticaret sicil gazetesinin
kuruluş, yönetim/müdür ve hisse devri PDF sayfalarını sıfır çakışma (id % 40) ile çeker.
PyMuPDF ile sıkıştırır, 50'şerli tar.gz paketleri yapar ve doğrudan GitHub Release'e aktarır.

%100 Doğrulanmış Resmî Veri. Sıfır Sentetik.
"""

import os
import sys
import time
import random
import re
import shutil
import sqlite3
import tarfile
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path
import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
DATA_DIR = REPO_ROOT / "collector" / "data"

USER_EMAIL = "geoprop_bot1@yandex.com"
USER_PASS = "Geoprop2026*"
BUNDLE_SIZE = 50  # Her 50 PDF sayfasında bir tar.gz paketi yap

TESSERACT_BIN = shutil.which("tesseract") or "/usr/bin/tesseract" or "/opt/homebrew/bin/tesseract"

def solve_captcha(cap_path, cookie_file):
    for _ in range(4):
        try:
            cmd_cap = [
                "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
                "-c", str(cookie_file), "-b", str(cookie_file),
                "https://www.ticaretsicil.gov.tr/assets/captcha/captcha.php"
            ]
            cap_bytes = subprocess.check_output(cmd_cap, stderr=subprocess.DEVNULL)
            with open(cap_path, "wb") as f:
                f.write(cap_bytes)
                
            out = subprocess.check_output([
                TESSERACT_BIN, str(cap_path), "stdout",
                "--psm", "8",
                "-c", "tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ], stderr=subprocess.DEVNULL)
            
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

def ensure_authenticated(cookie_file, cap_path):
    cmd_check = [
        "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
        "-c", str(cookie_file), "-b", str(cookie_file),
        "https://www.ticaretsicil.gov.tr/index.php"
    ]
    try:
        res = subprocess.check_output(cmd_check, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        if "cikis.php" in res.lower() or "geoprop" in res.lower():
            return True
    except Exception:
        pass
        
    print(f"🔑 [TOBB Oturumu] Giriş yapılıyor...")
    subprocess.run([
        "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
        "-c", str(cookie_file), "https://www.ticaretsicil.gov.tr/view/hizlierisim/girisyap.php"
    ], stdout=subprocess.DEVNULL)
    
    for attempt in range(5):
        code = solve_captcha(cap_path, cookie_file)
        if not code:
            continue
            
        cmd_login = [
            "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "10",
            "-c", str(cookie_file), "-b", str(cookie_file),
            "-d", f"LoginEmail={USER_EMAIL}&LoginSifre={USER_PASS}&Captcha={code}",
            "https://www.ticaretsicil.gov.tr/view/modal/uyegirisi_ok.php"
        ]
        try:
            r = subprocess.check_output(cmd_login, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            if r == "1":
                print("  ✅ TOBB Oturumu Başarıyla Açıldı!")
                return True
        except Exception:
            pass
        time.sleep(0.5)
        
    return False

def download_page_pdf(guid, cookie_file):
    view_url = f"https://www.ticaretsicil.gov.tr/view/hizlierisim/pdf_goster.php?Guid={guid}"
    cmd_view = [
        "curl", "-k", "-s", "--connect-timeout", "8", "--max-time", "15",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "-H", "Referer: https://www.ticaretsicil.gov.tr/view/hizlierisim/ilangoruntuleme.php",
        "-c", str(cookie_file), "-b", str(cookie_file),
        view_url
    ]
    try:
        html = subprocess.check_output(cmd_view, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        m_pdf = re.search(r'embed\s+src="([^"]+\.pdf)"', html) or re.search(r'src="(/tmp_gazete/[^"]+\.pdf)"', html)
        if not m_pdf:
            return None
            
        pdf_url = f"https://www.ticaretsicil.gov.tr{m_pdf.group(1)}"
        cmd_dl = [
            "curl", "-k", "-s", "--connect-timeout", "8", "--max-time", "15",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "-c", str(cookie_file), "-b", str(cookie_file),
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
        comp = doc.tobytes(garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
        doc.close()
        return comp
    except Exception:
        return pdf_bytes

def upload_bundle_to_release(bundle_path):
    print(f"  ☁️ [GitHub Release] {bundle_path.name} yükleniyor...")
    try:
        res = subprocess.run([
            "gh", "release", "upload", "warehouse-latest", str(bundle_path), "--clobber"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        if res.returncode == 0:
            print(f"  ✅ {bundle_path.name} kalıcı olarak GitHub Release'e kaydedildi!")
            return True
        else:
            print(f"  ⚠️ Yükleme uyarısı: {res.stderr.strip()}")
    except Exception as e:
        print(f"  ⚠️ Yükleme istisnası: {e}")
    return False

def get_shard_tasks(conn, shard_id, num_shards, limit=2000):
    c = conn.cursor()
    # Faz 1: 2000 yılı ve sonrası (ilan_tarihi >= '2000-01-01' veya gazete_sayisi >= 4950)
    # Shard modülü: (id % num_shards) = (shard_id - 1)
    # Öncelik: Kuruluş > Müdür/Yetkili > Temsil > Hisse > Sermaye > Diğer
    target_mod = shard_id - 1
    query = """
        SELECT id, mersis_no, tsm_adi, unvan, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni, ilan_tarihi
        FROM ticaret_sicil_gazetesi_ilanlari
        WHERE (pdf_dosya_yolu IS NULL OR pdf_dosya_yolu = '')
          AND ilan_metni LIKE '%Guid:%'
          AND (ilan_tarihi >= '2000-01-01' OR CAST(gazete_sayisi AS INT) >= 4950)
          AND (id % ?) = ?
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
    return c.execute(query, (num_shards, target_mod, limit)).fetchall()

def init_shard_update_db(db_path):
    os.makedirs(db_path.parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gazete_pdf_updates (
            id INTEGER PRIMARY KEY,
            pdf_dosya_yolu TEXT NOT NULL,
            guncellenme_tarihi TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="GEOPROP TOBB 40x Shard Gazete PDF İndirici")
    parser.add_argument("--shard", type=int, required=True, help="Shard ID (1 - 40)")
    parser.add_argument("--num-shards", type=int, default=40, help="Toplam Shard Sayısı")
    parser.add_argument("--max-seconds", type=int, default=19000, help="Maksimum çalışma süresi (sn)")
    parser.add_argument("--max-pdfs", type=int, default=5000, help="Bu shard'ın indirebileceği maksimum PDF")
    args = parser.parse_args()

    shard_id = args.shard
    num_shards = args.num_shards
    start_time = time.time()
    deadline = start_time + args.max_seconds

    print("=" * 75)
    print(f"🚀 GEOPROP TOBB Gazete PDF Shard İşçisi #{shard_id}/{num_shards}")
    print(f"📍 Zaman Limiti: {args.max_seconds/3600:.1f} saat | Hedef: 2000+ Resmî Gazeteler")
    print("=" * 75)

    os.makedirs(DATA_DIR, exist_ok=True)
    cookie_file = DATA_DIR / f"tobb_shard_{shard_id}.cookies"
    cap_path = DATA_DIR / f"tobb_shard_{shard_id}_cap.png"
    temp_bundle_dir = REPO_ROOT / "warehouse" / "raw" / f"temp_bundle_{shard_id}"
    bundles_dir = REPO_ROOT / "warehouse" / "raw" / "bundles"
    shard_update_db_path = REPO_ROOT / "warehouse" / "product" / f"shard_{shard_id}_updates.sqlite"

    os.makedirs(temp_bundle_dir, exist_ok=True)
    os.makedirs(bundles_dir, exist_ok=True)
    init_shard_update_db(shard_update_db_path)

    # Master SQLite kontrolü
    if not DB_PATH.exists():
        print(f"⚠️ Master SQLite bulunamadı: {DB_PATH}")
        sys.exit(1)

    master_conn = sqlite3.connect(DB_PATH, timeout=60)
    update_conn = sqlite3.connect(shard_update_db_path, timeout=60)

    # TOBB Oturumu aç
    if not ensure_authenticated(cookie_file, cap_path):
        print("❌ TOBB Yetkili Girişi yapılamadı.")
        sys.exit(1)

    total_downloaded = 0
    current_bundle_files = []
    bundle_counter = int(time.time()) % 10000

    try:
        while time.time() < deadline and total_downloaded < args.max_pdfs:
            tasks = get_shard_tasks(master_conn, shard_id, num_shards, limit=200)
            if not tasks:
                print(f"✅ Shard #{shard_id} için indirilecek başka gazete sayfası kalmadı!")
                break

            print(f"📋 Shard #{shard_id}: {len(tasks)} yeni sayfa görevi alındı.")

            for row in tasks:
                if time.time() >= deadline or total_downloaded >= args.max_pdfs:
                    break

                ilan_id, mersis_no, tsm_adi, unvan, sayi, sayfa, ilan_turu, ilan_metni, ilan_tarihi = row

                m_guid = re.search(r'Guid:\s*([a-f0-9\-]+)', ilan_metni or "")
                if not m_guid:
                    continue
                guid = m_guid.group(1).strip()

                # PDF İndir
                pdf_bytes = download_page_pdf(guid, cookie_file)
                if not pdf_bytes:
                    # Oturum düşmüş olabilir, tekrar kontrol et
                    if not ensure_authenticated(cookie_file, cap_path):
                        time.sleep(10)
                    continue

                # PyMuPDF Sıkıştırma
                compressed = compress_pdf_bytes(pdf_bytes)

                safe_tsm = re.sub(r'[^a-zA-Z0-9]', '', tsm_adi or "TSM")
                pdf_filename = f"{sayi}_{sayfa}_{safe_tsm}_{guid[:8]}.pdf"
                pdf_dest = temp_bundle_dir / pdf_filename

                with open(pdf_dest, "wb") as f_out:
                    f_out.write(compressed)

                current_bundle_files.append((ilan_id, pdf_filename))
                total_downloaded += 1

                t_str = ilan_tarihi or "Tarih-Yok"
                print(f"  📥 [Shard {shard_id}|#{total_downloaded}] [{t_str}] Sayı: {sayi} / Sayfa: {sayfa} ({safe_tsm}) - {len(compressed)/1024:.1f} KB -> {unvan[:35]}...")

                # 50 dosya olduysa paketle ve yükle
                if len(current_bundle_files) >= BUNDLE_SIZE:
                    bundle_name = f"gazete_pdf_shard_{shard_id:02d}_{int(time.time())}_{bundle_counter:04d}.tar.gz"
                    bundle_path = bundles_dir / bundle_name

                    print(f"\n📦 [Shard {shard_id}] {len(current_bundle_files)} sayfa paketleniyor: {bundle_name}...")
                    with tarfile.open(bundle_path, "w:gz") as tar:
                        for _, p_name in current_bundle_files:
                            fp = temp_bundle_dir / p_name
                            if fp.exists():
                                tar.add(fp, arcname=p_name)

                    # GitHub Release'e aktar
                    uploaded = upload_bundle_to_release(bundle_path)
                    now_iso = datetime.now(timezone.utc).isoformat()
                    bundle_ref = f"release://warehouse-latest/{bundle_name}" if uploaded else f"bundle://{bundle_name}"

                    u_cur = update_conn.cursor()
                    m_cur = master_conn.cursor()
                    for i_id, p_name in current_bundle_files:
                        ref_val = f"{bundle_ref}#{p_name}"
                        u_cur.execute("INSERT OR REPLACE INTO gazete_pdf_updates (id, pdf_dosya_yolu, guncellenme_tarihi) VALUES (?, ?, ?)", (i_id, ref_val, now_iso))
                        m_cur.execute("UPDATE ticaret_sicil_gazetesi_ilanlari SET pdf_dosya_yolu = ?, guncellenme_tarihi = ? WHERE id = ?", (ref_val, now_iso, i_id))
                    
                    update_conn.commit()
                    master_conn.commit()

                    # Yerel geçici dosyaları temizle (DİSKİ KORU)
                    for _, p_name in current_bundle_files:
                        fp = temp_bundle_dir / p_name
                        if fp.exists():
                            try:
                                fp.unlink()
                            except Exception:
                                pass
                    if uploaded and bundle_path.exists():
                        try:
                            bundle_path.unlink()
                        except Exception:
                            pass

                    current_bundle_files.clear()
                    bundle_counter += 1
                    print(f"🧹 [Shard {shard_id}] Disk temizlendi, sıradaki pakete geçiliyor.\n")

                # İnsansı Gecikme (8.0 - 13.0 sn rastgele bekleme)
                # 40 makine ile toplamda saniyede 3-5 sayfa eder!
                time.sleep(random.uniform(8.0, 13.0))

        # Kalan varsa son paketi yükle
        if current_bundle_files:
            bundle_name = f"gazete_pdf_shard_{shard_id:02d}_{int(time.time())}_final.tar.gz"
            bundle_path = bundles_dir / bundle_name
            with tarfile.open(bundle_path, "w:gz") as tar:
                for _, p_name in current_bundle_files:
                    fp = temp_bundle_dir / p_name
                    if fp.exists():
                        tar.add(fp, arcname=p_name)
            uploaded = upload_bundle_to_release(bundle_path)
            now_iso = datetime.now(timezone.utc).isoformat()
            bundle_ref = f"release://warehouse-latest/{bundle_name}" if uploaded else f"bundle://{bundle_name}"
            u_cur = update_conn.cursor()
            m_cur = master_conn.cursor()
            for i_id, p_name in current_bundle_files:
                ref_val = f"{bundle_ref}#{p_name}"
                u_cur.execute("INSERT OR REPLACE INTO gazete_pdf_updates (id, pdf_dosya_yolu, guncellenme_tarihi) VALUES (?, ?, ?)", (i_id, ref_val, now_iso))
                m_cur.execute("UPDATE ticaret_sicil_gazetesi_ilanlari SET pdf_dosya_yolu = ?, guncellenme_tarihi = ? WHERE id = ?", (ref_val, now_iso, i_id))
            update_conn.commit()
            master_conn.commit()
            for _, p_name in current_bundle_files:
                fp = temp_bundle_dir / p_name
                if fp.exists():
                    try:
                        fp.unlink()
                    except Exception:
                        pass
            if uploaded and bundle_path.exists():
                try:
                    bundle_path.unlink()
                except Exception:
                    pass

    except Exception as e:
        print(f"⚠️ Shard #{shard_id} hatası: {e}")
    finally:
        master_conn.close()
        update_conn.close()
        print(f"🏁 Shard #{shard_id} tamamlandı. Toplam İndirilen Sayfa: {total_downloaded}")

if __name__ == "__main__":
    main()
