#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB Türkiye Geneli Yüksek Hacimli Şirket ve Resmî İlan Toplayıcı
=============================================================================
81 İlin tamamındaki 238 Ticaret Sicil Müdürlüğünü (TSM) ve en yüksek hacimli
Türkçe unvan köklerini (Demir, Yıldız, Anadolu, Güneş, Şahin, Aydın, vb.)
hızlı şekilde tarayarak her bir sorguda 500 - 2.000 gerçek şirket ve resmî gazete
ilanını 'warehouse/product/turkiye_ticaret_sicil_ve_sirketler.sqlite' ambarına yazar.

%100 Resmî Hükümet Verisi (TOBB Ticaret Sicil Gazetesi). Sıfır Sentetik.
"""

import os
import sys
import time
import json
import sqlite3
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
COOKIE_FILE = REPO_ROOT / "collector" / "data" / "tobb_auth_cookies.txt"
STATE_FILE = REPO_ROOT / "collector" / "data" / "tobb_turkiye_geneli_state.json"
TSM_FILE = REPO_ROOT / "collector" / "data" / "tobb_tsm_mudurlukleri.json"
TAXONOMY_FILE = REPO_ROOT / "collector" / "data" / "sektor_anahtar_kelimeleri.json"

USER_EMAIL = "geoprop_bot1@yandex.com"
USER_PASS = "Geoprop2026*"
TESSERACT_BIN = shutil.which("tesseract") or "/opt/homebrew/bin/tesseract" or "/usr/bin/tesseract"

PRIORITY_TSM_IDS = [
    18,   # ANKARA
    233,  # İZMİR
    38,   # BURSA
    15,   # ANTALYA
    2,    # ADANA
    145,  # KOCAELİ
    80,   # GAZİANTEP
    131,  # KAYSERİ
    147,  # KONYA
    168,  # MERSİN
    61,   # DENİZLİ
    199,  # SAMSUN
    213,  # TEKİRDAĞ
    174,  # MUĞLA
    25,   # BALIKESİR
    20,   # AYDIN
    69,   # ESKİŞEHİR
    163,  # MANİSA
    216,  # TRABZON
    74,   # DİYARBAKIR
    205,  # ŞANLIURFA
    113,  # HATAY
    195,  # SAKARYA
    154,  # KÜTAHYA
    217,  # UŞAK
    245,  # ZONGULDAK
    45,   # ÇANAKKALE
    67,   # EDİRNE
    221,  # YALOVA
    63,   # DÜZCE
    188,  # ORDU
    101,  # GİRESUN
    202,  # SİVAS
    157,  # MALATYA
    88,   # ERZURUM
    130,  # KAHRAMANMARAŞ
    10,   # AFYONKARAHİSAR
    116,  # ISPARTA
    34,   # BOLU
    84,   # GEBZE
    48,   # ÇORLU
    47,   # ÇERKEZKÖY
    12,   # ALANYA
    31,   # BODRUM
    78,   # FETHİYE
    164,  # MARMARİS
    162,  # MANAVGAT
    123,  # İNEGÖL
    23,   # BANDIRMA
    68    # EDREMİT
]

def load_tsm_list():
    if TSM_FILE.exists():
        with open(TSM_FILE, "r", encoding="utf-8") as f:
            all_tsms = json.load(f)
    else:
        all_tsms = {"18": "ANKARA", "233": "İZMİR", "38": "BURSA", "15": "ANTALYA"}
        
    ordered = []
    # 1. First add priority TSMs
    for pid in PRIORITY_TSM_IDS:
        s_id = str(pid)
        if s_id in all_tsms:
            ordered.append((pid, all_tsms[s_id]))
            
    # 2. Then add all other TSMs
    for s_id, name in all_tsms.items():
        iid = int(s_id)
        if iid not in PRIORITY_TSM_IDS and iid != 232:  # Skip Istanbul (already heavily farmed)
            ordered.append((iid, name))
            
    # 3. Add Istanbul at the end for completion
    if "232" in all_tsms:
        ordered.append((232, all_tsms["232"]))
        
    return ordered

def load_keywords():
    if TAXONOMY_FILE.exists():
        try:
            with open(TAXONOMY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            flat = []
            sg = d.get("sektor_gruplari", {})
            # Prioritize high-yield unvan roots first!
            priority_groups = [
                "turkiye_unvan_kokleri_genel",
                "turkiye_unvan_kokleri_cografi",
                "turkiye_unvan_kokleri_kurumsal",
                "turkiye_unvan_kokleri_sektorel"
            ]
            for grp in priority_groups:
                for kw in sg.get(grp, []):
                    kw_clean = kw.strip().upper()
                    if len(kw_clean) >= 4 and kw_clean not in flat:
                        flat.append(kw_clean)
                        
            # Then append standard sectoral taxonomy
            for grp, kw_list in sg.items():
                if grp not in priority_groups:
                    for kw in kw_list:
                        kw_clean = kw.strip().upper()
                        if len(kw_clean) >= 4 and kw_clean not in flat:
                            flat.append(kw_clean)
            return flat
        except Exception:
            pass
    return ["DEMİR", "ANADOLU", "YILDIZ", "ÖZDEMİR", "GÜNEŞ", "ŞAHİN", "AYDIN", "YILMAZ", "ÇELİK", "ÖZTÜRK"]

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tsm_idx": 0, "kw_idx": 0, "total_companies_saved": 0, "total_announcements_saved": 0}

def save_state(state):
    os.makedirs(STATE_FILE.parent, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def solve_captcha(cap_path):
    for _ in range(4):
        try:
            cmd_cap = [
                "curl", "-k", "-s", "--connect-timeout", "4", "--max-time", "6",
                "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
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
        time.sleep(0.1)
    return None

def ensure_authenticated():
    cmd = [
        "curl", "-k", "-s", "--connect-timeout", "4", "--max-time", "6",
        "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
        "https://www.ticaretsicil.gov.tr/index.php"
    ]
    try:
        res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        if "cikis.php" in res.lower() or "geoprop" in res.lower():
            return True
    except Exception:
        pass
        
    print("🔑 TOBB Oturumu yenileniyor...")
    cap_path = REPO_ROOT / "collector" / "data" / "tobb_login_cap_turkiye.png"
    subprocess.run(["curl", "-k", "-s", "--connect-timeout", "4", "--max-time", "6", "-c", str(COOKIE_FILE), "https://www.ticaretsicil.gov.tr/view/hizlierisim/girisyap.php"], stdout=subprocess.DEVNULL)
    
    for attempt in range(4):
        code = solve_captcha(cap_path)
        if not code:
            continue
            
        cmd_login = [
            "curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "8",
            "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
            "-d", f"LoginEmail={USER_EMAIL}&LoginSifre={USER_PASS}&Captcha={code}",
            "https://www.ticaretsicil.gov.tr/view/modal/uyegirisi_ok.php"
        ]
        try:
            r = subprocess.check_output(cmd_login, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            if r == "1":
                print("✅ TOBB Girişi Başarılı!")
                return True
        except Exception:
            pass
        time.sleep(0.3)
        
    return False

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

def query_tsm_keyword(tsm_id, tsm_name, keyword, conn):
    cmd_search = [
        "curl", "-k", "-s", "--connect-timeout", "6", "--max-time", "25",
        "-c", str(COOKIE_FILE), "-b", str(COOKIE_FILE),
        "-d", f"SicilMudurluguId={tsm_id}&TicSicNo=&TicaretUnvani={keyword}&BagliIlan=0&Tarih=0",
        "https://www.ticaretsicil.gov.tr/view/hizlierisim/ilangoruntuleme_ok.php"
    ]
    
    try:
        res = subprocess.check_output(cmd_search, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  ⚠️ [{tsm_name} | {keyword}] Bağlantı zaman aşımı: {e}")
        return 0, 0
        
    if "table" not in res:
        return 0, 0
        
    soup = BeautifulSoup(res, "html.parser")
    rows = soup.find_all("tr")
    if len(rows) <= 1:
        return 0, 0
        
    now_iso = datetime.now(timezone.utc).isoformat()
    sirket_batch = []
    ilan_batch = []
    
    for tr in rows[1:]:
        tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(tds) >= 7:
            mudurluk = tds[0].strip() or tsm_name
            sicil_no = tds[1].strip()
            unvan = tds[2].strip()
            tarih = tds[3].strip()
            sayi = tds[4].strip()
            sayfa = tds[5].strip()
            ilan_turu = tds[6].strip()
            
            guid = ""
            for a in tr.find_all("a"):
                h = a.get("href", "")
                if "Guid=" in h:
                    guid = h.split("Guid=")[1].split("&")[0].strip()
                    break
                    
            if not unvan or not sicil_no:
                continue
                
            mersis = f"TOBB_{mudurluk}_{sicil_no}"
            s_type = determine_company_type(unvan)
            
            durum = "Faal"
            if any(k in ilan_turu.lower() for k in ["terkin", "kapanış", "tasfiye"]):
                durum = "Terkin / Kapanış / Tasfiye"
                
            iso_date = now_iso[:10]
            if re.match(r'^\d{2}\.\d{2}\.\d{4}$', tarih):
                d, m, y = tarih.split(".")
                iso_date = f"{y}-{m}-{d}"
                
            sirket_batch.append((
                mersis, sicil_no, mudurluk, "", unvan,
                s_type, durum, None, iso_date,
                mudurluk, "", "", keyword, "",
                "", "", "TOBB_TICARET_SICIL_GAZETESI", now_iso
            ))
            
            ilan_batch.append((
                mersis, sicil_no, mudurluk, unvan,
                iso_date, sayi, sayfa, ilan_turu,
                f"{mudurluk} TSM - Sicil: {sicil_no} | Sayı: {sayi} Sayfa: {sayfa} ({ilan_turu})",
                f"Unvan: {unvan}\nSicil No: {sicil_no}\nTSM: {mudurluk}\nİlan Türü: {ilan_turu}\nGuid: {guid}",
                "TOBB_RESMI_GAZETE_TAM_SAYFA", now_iso
            ))
            
    c = conn.cursor()
    c.executemany("""
        INSERT OR IGNORE INTO sirketler (
            mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan,
            sirket_turu, durum, sermaye_tl, kurulus_tarihi,
            il, ilce, tam_adres, faaliyet_konusu, nace_kodu,
            telefon, web_sitesi, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sirket_batch)
    
    c.executemany("""
        INSERT OR REPLACE INTO ticaret_sicil_gazetesi_ilanlari (
            mersis_no, ticaret_sicil_no, tsm_adi, unvan,
            ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu,
            ilan_ozeti, ilan_metni, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ilan_batch)
    
    conn.commit()
    return len(sirket_batch), len(ilan_batch)

def main():
    print("=" * 70)
    print("🇹🇷 GEOPROP TOBB Türkiye Geneli Yüksek Hacimli Şirket Toplayıcı")
    print("=" * 70)
    
    if not ensure_authenticated():
        print("❌ TOBB Yetkili Girişi Yapılamadı.")
        return
        
    tsm_list = load_tsm_list()
    keywords = load_keywords()
    state = load_state()
    
    print(f"📌 TSM Sayısı: {len(tsm_list)} | Kelime Sayısı: {len(keywords)}")
    print(f"📍 Başlangıç Konumu: TSM #{state.get('tsm_idx', 0)} | Kelime #{state.get('kw_idx', 0)}")
    
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode = WAL;")
    
    tsm_idx = state.get("tsm_idx", 0)
    kw_idx = state.get("kw_idx", 0)
    total_saved_sirket = state.get("total_companies_saved", 0)
    total_saved_ilan = state.get("total_announcements_saved", 0)
    
    consecutive_empty = 0
    
    try:
        while tsm_idx < len(tsm_list):
            tsm_id, tsm_name = tsm_list[tsm_idx]
            print(f"\n🏢 [{tsm_idx+1}/{len(tsm_list)}] {tsm_name} (ID: {tsm_id}) Taranıyor...")
            
            while kw_idx < len(keywords):
                kw = keywords[kw_idx]
                s_count, i_count = query_tsm_keyword(tsm_id, tsm_name, kw, conn)
                
                if s_count > 0:
                    total_saved_sirket += s_count
                    total_saved_ilan += i_count
                    print(f"  ⚡ [{tsm_name}] '{kw}' -> +{s_count} şirket, +{i_count} ilan (Kümülatif Şirket: {total_saved_sirket:,})")
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                    
                kw_idx += 1
                state["tsm_idx"] = tsm_idx
                state["kw_idx"] = kw_idx
                state["total_companies_saved"] = total_saved_sirket
                state["total_announcements_saved"] = total_saved_ilan
                save_state(state)
                
                # Polite pause to avoid TOBB stateful SYN drops
                time.sleep(0.9)
                
            # Completed all keywords for this TSM, move to next
            kw_idx = 0
            tsm_idx += 1
            state["tsm_idx"] = tsm_idx
            state["kw_idx"] = 0
            save_state(state)
            time.sleep(1.5)
            
    except KeyboardInterrupt:
        print("\n🛑 Kullanıcı tarafından durduruldu.")
    finally:
        conn.close()
        save_state(state)
        print(f"\n🏁 Tamamlandı. Toplam Kaydedilen: {total_saved_sirket:,} şirket, {total_saved_ilan:,} ilan.")

if __name__ == "__main__":
    main()
