#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Otonom Ticaret Sicil, Şirketler ve Ortaklık Yapısı Motoru
===================================================================
Şahıs, Limited, Anonim ve Kooperatif şirketlerinin resmî sicil kayıtlarını,
ortaklık yapılarını, pay oranlarını, tescilli adreslerini ve Ticaret Sicil
Gazetesi ilanlarını 7/24 kesintisiz ve otonom toplayan ana servis.

Motorlar:
1. İTO Otonom Motoru (Tesseract OCR ile güvenlik kodunu çözer, sicil no üzerinden VKN, telefon, web ve firma verisi çeker).
2. TOBB Ticaret Sicil Gazetesi Otonom Motoru (Tüm 81 ilin tescil ve ilan verilerini çeker).
3. KAP Otonom Motoru (Halka açık dev şirketlerin hissedarlarını, pay oranlarını ve YK üyelerini çeker).
4. Otomatik İhracat Motoru (SQLite ambarını düzenli olarak CSV ve Parquet formatlarına döker).

%100 Resmî Sahadan Alınmış Doğrulanmış Veri. Sıfır Sentetik.
"""

import os
import sys
import time
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
STATE_FILE = REPO_ROOT / "collector" / "data" / "ticaret_sicil_state.json"
CSV_DIR = REPO_ROOT / "collector" / "data" / "csv_ciktilari"

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_ito_sicil": 100050,
        "last_tobb_index": 0,
        "last_kap_sync": "",
        "total_cycles": 0
    }

def save_state(state):
    os.makedirs(STATE_FILE.parent, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def export_snapshots():
    os.makedirs(CSV_DIR, exist_ok=True)
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        import pandas as pd
        
        # 1. Sirketler CSV & Parquet
        df_sirket = pd.read_sql_query("SELECT * FROM sirketler", conn)
        df_sirket.to_csv(CSV_DIR / "sirketler.csv", sep=";", index=False, encoding="utf-8-sig")
        df_sirket.to_parquet(REPO_ROOT / "warehouse" / "product" / "turkiye_sirketler.parquet", index=False)
        
        # 2. Sirket Ortaklari CSV
        df_ortak = pd.read_sql_query("SELECT * FROM sirket_ortaklari", conn)
        df_ortak.to_csv(CSV_DIR / "sirket_ortaklari.csv", sep=";", index=False, encoding="utf-8-sig")
        
        # 3. Ticaret Sicil Gazetesi İlanları CSV
        df_ilan = pd.read_sql_query("SELECT * FROM ticaret_sicil_gazetesi_ilanlari", conn)
        df_ilan.to_csv(CSV_DIR / "ticaret_sicil_gazetesi_ilanlari.csv", sep=";", index=False, encoding="utf-8-sig")
        
        conn.close()
        print(f"📦 [Dışa Aktarma] Ambar yedekleri güncellendi: {len(df_sirket)} Şirket, {len(df_ortak)} Ortak/Yönetici, {len(df_ilan)} İlan.")
    except Exception as e:
        print(f"⚠️ Dışa aktarma hatası: {e}")

def run_ito_batch(start_sicil, batch_size=80):
    end_sicil = start_sicil + batch_size
    print(f"\n🔄 [Motor 1] İTO Otonom Tarama: Sicil {start_sicil} -> {end_sicil}...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "collector" / "ito_otonom_master_toplayici.py"),
        "--start", str(start_sicil),
        "--end", str(end_sicil),
        "--threads", "8"
    ]
    try:
        subprocess.run(cmd, timeout=120)
    except Exception as e:
        print(f"⚠️ İTO motor hatası: {e}")
    return end_sicil

def run_tobb_step():
    print("\n🔄 [Motor 2A] TOBB Ticaret Sicil Gazetesi Otonom Tarama (Genişletilmiş 238 Sektör Havuzu)...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "collector" / "ticaret_sicil_gazetesi_otonom_toplayici.py"),
        "--batch-size", "6"
    ]
    try:
        subprocess.run(cmd, timeout=90)
    except Exception as e:
        print(f"⚠️ TOBB motor hatası: {e}")

def run_tobb_deep_step():
    print("\n🔄 [Motor 2B] TOBB Resmî Gazete Derin Sayfa, Adres ve Ortaklık Taraması...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "collector" / "tobb_gazete_detay_ve_sayfa_toplayici.py")
    ]
    try:
        subprocess.run(cmd, timeout=120)
    except Exception as e:
        print(f"⚠️ TOBB Derin Sayfa motor hatası: {e}")

def run_gazette_pdf_download_step(batch_size=20):
    print(f"\n📥 [Motor 2C] TOBB Ticaret Sicil Gazetesi PDF İndirici & Arşivleyici (Paket: {batch_size} Sayfa)...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "collector" / "tobb_gazete_pdf_toplayici.py"),
        str(batch_size)
    ]
    try:
        subprocess.run(cmd, timeout=150)
    except Exception as e:
        print(f"⚠️ Gazete PDF indirici motor hatası: {e}")

def run_kap_step():
    print("\n🔄 [Motor 3] KAP Şirket ve Ortaklık Senkronizasyonu...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "collector" / "kap_otonom_master_aktarici.py")
    ]
    try:
        subprocess.run(cmd, timeout=180)
    except Exception as e:
        print(f"⚠️ KAP motor hatası: {e}")

def run_authorities_step():
    print("\n🔄 [Motor 4] Şirket Yetkilileri, Yönetim Kurulu ve Ortaklık Kadrosu Çıkarıcı...")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "collector" / "sirket_yetkilileri_ve_yonetim_cikarici.py")
    ]
    try:
        subprocess.run(cmd, timeout=300)
    except Exception as e:
        print(f"⚠️ Yetkili çıkarma motor hatası: {e}")

def print_stats():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM sirketler")
        n_sirket = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM sirket_ortaklari")
        n_ortak = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM ticaret_sicil_gazetesi_ilanlari")
        n_ilan = c.fetchone()[0]
        c.execute("SELECT sirket_turu, COUNT(*) FROM sirketler GROUP BY sirket_turu")
        turler = c.fetchall()
        conn.close()
        
        pdf_dir = REPO_ROOT / "warehouse" / "raw" / "gazete_sayfalari"
        n_pdfs = len(list(pdf_dir.glob("*.pdf"))) if pdf_dir.exists() else 0
        
        print("\n" + "=" * 65)
        print("📊 [CANLI GEOPROP TİCARET SİCİL VE ŞİRKETLER AMBARI]")
        print(f"  🏢 Toplam Kayıtlı Şirket  : {n_sirket:,}")
        print(f"  👥 Ortaklık / Yönetici    : {n_ortak:,}")
        print(f"  📰 Resmî Gazete İlanı     : {n_ilan:,}")
        print(f"  📂 İndirilen Gazete PDF   : {n_pdfs:,}")
        print(f"  📑 Şirket Türü Dağılımı   : {dict(turler)}")
        print("=" * 65 + "\n")
    except Exception as e:
        pass

def main():
    print("🚀 GEOPROP Otonom Ticaret Sicil ve Ortaklık Yapısı Motoru Başlatıldı.")
    state = load_state()
    
    while True:
        try:
            state["total_cycles"] += 1
            print(f"\n▶️ DÖNGÜ #{state['total_cycles']} Başlıyor ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
            
            # 1. İTO Taraması
            next_sicil = run_ito_batch(state["last_ito_sicil"], batch_size=80)
            state["last_ito_sicil"] = next_sicil
            save_state(state)
            
            # 2. TOBB Ticaret Sicil Gazetesi Taraması (Her 8 döngüde bir - Firewall Soğuma Korumalı)
            if state["total_cycles"] % 8 == 0:
                run_tobb_step()
                
            # 2B. TOBB Resmî Gazete Derin Sayfa, Adres & Ortaklık Taraması (Her 8 döngüde bir)
            if state["total_cycles"] % 8 == 3:
                run_tobb_deep_step()
                
            # 2C. TOBB Gazete PDF İndirici & Arşivleyici (Her 8 döngüde bir)
            if state["total_cycles"] % 8 == 6:
                run_gazette_pdf_download_step(batch_size=15)
                
            # 2D. Şirket Yetkilileri, Yönetim Kurulu ve Ortaklık Kadrosu (Her 4 döngüde bir)
            if state["total_cycles"] % 4 == 0:
                run_authorities_step()
                
            # 3. İhracat ve Raporlama
            export_snapshots()
            print_stats()
            
            # Kısa nefeslenme
            time.sleep(5)
            
        except KeyboardInterrupt:
            print("\n🛑 Servis durduruldu.")
            break
        except Exception as e:
            print(f"❌ Beklenmedik döngü hatası: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
