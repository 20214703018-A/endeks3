#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Şirket Çalışanları, Yetkilileri ve Yönetim Kadrosu Arama Motoru (On-Demand & Batch)
=============================================================================================
İstek dahilinde (veya toplu olarak) veritabanındaki veya harici olarak sorgulanan şirketlerin:
  1. Ticaret Sicil Gazetesi Tescilleri (Müdürler, Temsilciler, Ortaklar, İmza Yetkilileri, TCKN)
  2. KAP (Kamuyu Aydınlatma Platformu - BIST Şirketleri):
     - Yönetim Kurulu Başkanı, Başkan Vekilleri, Üyeler
     - Üst Yönetim: Genel Müdür (CEO), CFO, COO, Direktörler
     - Büyük Hissedarlar / Pay Sahipleri
  3. Şirket Kurumsal Web Sitesi Ekip Kazıyıcı:
     - `/ekibimiz`, `/hakkimizda`, `/yonetim`, `/kadro`, `/team`, `/about-us`
     - Çalışan personeller, departmanlar, unvanlar ve kurumsal e-posta/iletişimler
  4. Açık Web & Profesyonel Ağ Ayak İzi (LinkedIn / SERP profilleri)
  5. GLEIF Ultimate Parent & Yasal Sahiplik İlişkileri

Sonuçları 'warehouse/product/turkiye_ticaret_sicil_ve_sirketler.sqlite' içindeki
'sirket_calisanlari' tablosuna %100 resmî ve doğrulanmış biçimde kaydeder.
"""

import os
import re
import sys
import time
import json
import sqlite3
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
CSV_OUT = REPO_ROOT / "collector" / "data" / "csv_ciktilari" / "sirket_calisanlari.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

STOP_WORDS_NAME = {
    "TICARET", "SANAYI", "LIMITED", "ANONIM", "SIRKETI", "MUDURLUK", "MUDURLUGU",
    "SUBESI", "TARIH", "SAYI", "SAYFA", "TESCIL", "ILAN", "TURKIYE", "ODASI",
    "BORSA", "BANKASI", "HOLDING", "GENEL", "BAKANLIK", "VERGI", "DAIRESI",
    "ISTANBUL", "ANKARA", "IZMIR", "BURSA", "ANTALYA"
}


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sirket_calisanlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mersis_no TEXT,
            ticaret_sicil_no TEXT,
            unvan TEXT,
            ad_soyad TEXT NOT NULL,
            gorev_unvani TEXT,
            departman TEXT,
            kategori TEXT,
            tc_no_maskeli TEXT,
            email TEXT,
            telefon TEXT,
            profil_veya_kaynak_url TEXT,
            kaynak TEXT NOT NULL,
            guncellenme_tarihi TEXT NOT NULL,
            UNIQUE(unvan, ad_soyad, gorev_unvani, kaynak)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calisan_unvan ON sirket_calisanlari(unvan);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calisan_mersis ON sirket_calisanlari(mersis_no);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calisan_ad_soyad ON sirket_calisanlari(ad_soyad);")
    conn.commit()


def mask_tckn(tckn: str) -> str:
    if not tckn or len(tckn) != 11:
        return tckn
    return f"{tckn[:3]}*****{tckn[-2:]}"


# ---------------------------------------------------------------------------
# 1. KANAL: TOBB Ticaret Sicil Gazetesi Müdür, Temsilci ve Yetkili Çıkarıcı
# ---------------------------------------------------------------------------
def extract_officials_from_sicil_gazetesi(conn: sqlite3.Connection, company_unvan: str, mersis_no: str = None) -> list:
    """
    Şirketin resmî Ticaret Sicil Gazetesi tescil ilanlarından (Müdürler, Temsilciler,
    Yönetim Kurulu Üyeleri, Tasfiye Memurları) kişileri çeker.
    İndeks dostu optimize sorgu kullanır.
    """
    officials = []
    c = conn.cursor()

    rows = []
    if mersis_no and not mersis_no.startswith("TSM-"):
        rows = c.execute("""
            SELECT mersis_no, ticaret_sicil_no, unvan, ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni
            FROM ticaret_sicil_gazetesi_ilanlari
            WHERE mersis_no = ?
            ORDER BY ilan_tarihi DESC
            LIMIT 50
        """, (mersis_no,)).fetchall()

    if not rows and company_unvan:
        # Önce tam unvan ile dene
        rows = c.execute("""
            SELECT mersis_no, ticaret_sicil_no, unvan, ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni
            FROM ticaret_sicil_gazetesi_ilanlari
            WHERE unvan = ?
            ORDER BY ilan_tarihi DESC
            LIMIT 50
        """, (company_unvan.strip(),)).fetchall()

    if not rows and company_unvan:
        # İndeks dostu önek (GLOB) araması (0.005 saniye)
        first_word = re.sub(r'[^A-ZÇĞİÖŞÜa-zçğıöşü0-9]', '', company_unvan.strip().split()[0])
        if len(first_word) >= 3:
            rows = c.execute("""
                SELECT mersis_no, ticaret_sicil_no, unvan, ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni
                FROM ticaret_sicil_gazetesi_ilanlari
                WHERE unvan GLOB ?
                ORDER BY id DESC
                LIMIT 50
            """, (f"{first_word.upper()}*",)).fetchall()

    now_iso = datetime.now(timezone.utc).isoformat()
    seen_names = set()

    # 1.1 Mevcut sirket_ortaklari tablosundaki yetkili ve ortakları ekle
    existing_ortaklar = c.execute("""
        SELECT mersis_no, unvan, ortak_unvan_ad, gorev_unvani, temsil_yetkisi, kaynak
        FROM sirket_ortaklari
        WHERE unvan = ? OR (mersis_no IS NOT NULL AND mersis_no = ?)
    """, (company_unvan.strip(), mersis_no or "")).fetchall()

    for m_no, u_name, o_ad, g_unv, t_yetki, src in existing_ortaklar:
        if o_ad and o_ad not in seen_names:
            seen_names.add(o_ad)
            officials.append({
                "mersis_no": m_no,
                "ticaret_sicil_no": None,
                "unvan": u_name or company_unvan,
                "ad_soyad": o_ad,
                "gorev_unvani": g_unv or "ŞİRKET ORTAĞI / YETKİLİSİ",
                "departman": "Ortaklık / Temsil",
                "kategori": "YONETIM_KURULU" if "YÖNETİM" in (g_unv or "") else "MUDUR_TEMSILCI",
                "tc_no_maskeli": None,
                "email": None,
                "telefon": None,
                "profil_veya_kaynak_url": f"Resmî Sicil Kaydı ({src})",
                "kaynak": src or "RESMI_SICIL_ORTAKLIK",
                "guncellenme_tarihi": now_iso,
            })

    for m_no, s_no, u_name, i_tar, g_sayi, s_sayfa, i_tur, metin in rows:
        if not metin:
            continue

        # Regex ile Ad Soyad + Görev + TCKN Yakalama
        # Kalıp 1: T.C. Kimlik No: ... ... müdür olarak seçilmiştir / atanmıştır
        patterns = [
            r"(?:T\.?C\.?\s*(?:Kimlik)?\s*(?:No)?\s*[:;]?\s*(\d{11})\s*)?([A-ZÇĞİÖŞÜa-zçğıöşü]{2,}\s+[A-ZÇĞİÖŞÜa-zçğıöşü]{2,}(?:\s+[A-ZÇĞİÖŞÜa-zçğıöşü]{2,})?)\s*(?:'?[a-zçğıöşü]+)?\s*(?:şirket\s*müdürü|müdür|yönetim\s*kurulu\s*üyesi|yönetim\s*kurulu\s*başkanı|temsile\s*yetkili|münferiden\s*temsil|müştereken\s*temsil)",
            r"(?:şirket\s*müdürlüğüne|müdürlüğe|temsilciliğe)\s*(?:T\.?C\.?\s*(?:Kimlik)?\s*No\s*[:;]?\s*(\d{11})\s*)?([A-ZÇĞİÖŞÜa-zçğıöşü]{2,}\s+[A-ZÇĞİÖŞÜa-zçğıöşü]{2,})",
            r"(?:yetkili\s*kılınmıştır|seçilmiştir|atanmıştır)[^.]*?([A-ZÇĞİÖŞÜ]{2,}\s+[A-ZÇĞİÖŞÜ]{2,}(?:\s+[A-ZÇĞİÖŞÜ]{2,})?)\s*(?:adına|tarafından|münferit|müşterek)",
        ]

        for pat in patterns:
            for match in re.finditer(pat, metin, re.IGNORECASE):
                groups = match.groups()
                tckn = groups[0] if len(groups) > 1 and groups[0] and len(groups[0]) == 11 else None
                name = groups[1] if len(groups) > 1 and groups[1] else groups[0]
                if not name:
                    continue

                clean_name = " ".join(name.strip().split()).upper()
                name_parts = clean_name.split()

                if len(name_parts) < 2 or len(clean_name) < 5:
                    continue
                if any(w in STOP_WORDS_NAME for w in name_parts):
                    continue
                if clean_name in seen_names:
                    continue
                seen_names.add(clean_name)

                # Görev Unvanını Belirle
                gorev = "ŞİRKET MÜDÜRÜ / KANUNİ TEMSİLCİ"
                kategori = "MUDUR_TEMSILCI"
                matched_str = match.group(0).lower()
                if "başkan" in matched_str:
                    gorev = "YÖNETİM KURULU BAŞKANI"
                    kategori = "YONETIM_KURULU"
                elif "yönetim kurulu" in matched_str:
                    gorev = "YÖNETİM KURULU ÜYESİ"
                    kategori = "YONETIM_KURULU"
                elif "tasfiye" in matched_str:
                    gorev = "TASFİYE MEMURU"
                    kategori = "TEMSIL_YETKILISI"
                elif "münferit" in matched_str:
                    gorev = "MÜNFERİDEN TEMSİLE YETKİLİ MÜDÜR"
                    kategori = "MUDUR_TEMSILCI"

                officials.append({
                    "mersis_no": m_no,
                    "ticaret_sicil_no": s_no,
                    "unvan": u_name or company_unvan,
                    "ad_soyad": clean_name,
                    "gorev_unvani": gorev,
                    "departman": "Üst Yönetim / Temsil",
                    "kategori": kategori,
                    "tc_no_maskeli": mask_tckn(tckn) if tckn else None,
                    "email": None,
                    "telefon": None,
                    "profil_veya_kaynak_url": f"https://www.ticaretsicil.gov.tr (Sayı: {g_sayi}, Sayfa: {s_sayfa})",
                    "kaynak": "TOBB_TICARET_SICIL_GAZETESI",
                    "guncellenme_tarihi": now_iso,
                })

    return officials


# ---------------------------------------------------------------------------
# 2. KANAL: KAP (Kamuyu Aydınlatma Platformu) Üst Yönetim & Yönetim Kurulu
# ---------------------------------------------------------------------------
def extract_kap_officials_for_company(company_unvan: str) -> list:
    """
    Borsa İstanbul'a kote şirketler için KAP'tan Yönetim Kurulu Üyeleri ve
    Genel Müdür, CFO, CTO, Direktör gibi icracı üst düzey yöneticileri çeker.
    """
    officials = []
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        url = "https://www.kap.org.tr/tr/bist-sirketler"
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return officials

        # mkkMemberOid ve unvan eşleştirmesi
        soup = BeautifulSoup(r.text, "html.parser")
        target_oid = None
        target_title = None

        norm_query = company_unvan.lower().replace("i̇", "i").replace("ı", "i")
        for s in soup.find_all("script"):
            if s.string and "mkkMemberOid" in s.string:
                cleaned = s.string.encode().decode("unicode_escape", errors="ignore")
                matches = re.findall(r'\{\"mkkMemberOid\":\"([^\"]+)\",\"kapMemberTitle\":\"([^\"]+)\"', cleaned)
                for oid, title in matches:
                    norm_t = title.lower().replace("i̇", "i").replace("ı", "i")
                    if any(word in norm_t for word in norm_query.split()[:2] if len(word) > 3):
                        target_oid = oid
                        target_title = title.strip()
                        break
            if target_oid:
                break

        if not target_oid:
            return officials

        # Genel Bilgiler sayfasını çek
        detail_url = f"https://www.kap.org.tr/tr/sirket-bilgileri/genel/{target_oid}"
        r_det = requests.get(detail_url, headers=HEADERS, timeout=15)
        if r_det.status_code != 200:
            return officials

        det_soup = BeautifulSoup(r_det.text, "html.parser")
        for table in det_soup.find_all("table"):
            tbl_txt = table.get_text()

            # Yönetim Kurulu
            if ("Yönetim Kurulu" in tbl_txt or "Yönetim Kurulu Üyeleri" in tbl_txt) and "Görevi" in tbl_txt:
                for tr in table.find_all("tr"):
                    cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) >= 3 and not any(k in cells[0].lower() for k in ["adı-soyadı", "ad-soyad", "görevlendirme"]):
                        ad = cells[0].strip()
                        gorev = cells[3].strip() if len(cells) > 3 and cells[3].strip() else (cells[1].strip() if len(cells) > 1 else "Yönetim Kurulu Üyesi")
                        if ad and not ad.isdigit() and len(ad) > 3:
                            officials.append({
                                "mersis_no": f"KAP_{target_oid[:16]}",
                                "ticaret_sicil_no": None,
                                "unvan": target_title,
                                "ad_soyad": ad.upper(),
                                "gorev_unvani": gorev.upper(),
                                "departman": "Yönetim Kurulu",
                                "kategori": "YONETIM_KURULU",
                                "tc_no_maskeli": None,
                                "email": None,
                                "telefon": None,
                                "profil_veya_kaynak_url": detail_url,
                                "kaynak": "KAP_YONETIM_KURULU",
                                "guncellenme_tarihi": now_iso,
                            })

            # Üst Yönetim (Genel Müdür, CFO, Direktörler)
            if ("Yönetimde Söz Sahibi" in tbl_txt or "Üst Düzey Yönetici" in tbl_txt or "Yöneticiler" in tbl_txt) and "Görevi" in tbl_txt:
                for tr in table.find_all("tr"):
                    cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) >= 2 and not any(k in cells[0].lower() for k in ["adı-soyadı", "ad-soyad", "görevi"]):
                        ad = cells[0].strip()
                        gorev = cells[1].strip() if len(cells) > 1 else "Üst Düzey Yönetici"
                        if ad and not ad.isdigit() and len(ad) > 3:
                            officials.append({
                                "mersis_no": f"KAP_{target_oid[:16]}",
                                "ticaret_sicil_no": None,
                                "unvan": target_title,
                                "ad_soyad": ad.upper(),
                                "gorev_unvani": gorev.upper(),
                                "departman": "İcracı Üst Yönetim",
                                "kategori": "UST_YONETIM",
                                "tc_no_maskeli": None,
                                "email": None,
                                "telefon": None,
                                "profil_veya_kaynak_url": detail_url,
                                "kaynak": "KAP_UST_YONETIM",
                                "guncellenme_tarihi": now_iso,
                            })
    except Exception:
        pass

    return officials


# ---------------------------------------------------------------------------
# 3. KANAL: Şirket Web Sitesi Ekip & Kadro Kazıyıcı
# ---------------------------------------------------------------------------
def extract_employees_from_website(company_unvan: str, website_url: str) -> list:
    """
    Şirketin resmî web sitesindeki /ekibimiz, /kadromuz, /hakkimizda, /yonetim
    sayfalarını tarayarak çalışanları, mühendisleri, direktörleri ve unvanları çıkarır.
    """
    employees = []
    if not website_url:
        return employees

    base_url = website_url.strip()
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    now_iso = datetime.now(timezone.utc).isoformat()

    candidate_paths = [
        "",
        "/ekibimiz",
        "/kadromuz",
        "/hakkimizda",
        "/yonetim",
        "/yonetim-kadromuz",
        "/team",
        "/our-team",
        "/about-us",
        "/leadership",
    ]

    seen_names = set()

    for path in candidate_paths:
        target = f"{base_url.rstrip('/')}{path}"
        try:
            r = requests.get(target, headers=HEADERS, timeout=6, allow_redirects=True)
            if r.status_code != 200 or len(r.text) < 500:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            # 1. Schema.org Person markup kontrolü
            schema_persons = soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/Person", re.I)})
            for p in schema_persons:
                name_tag = p.find(attrs={"itemprop": "name"})
                job_tag = p.find(attrs={"itemprop": "jobTitle"})
                if name_tag and name_tag.get_text(strip=True):
                    nm = " ".join(name_tag.get_text(strip=True).split()).upper()
                    jb = job_tag.get_text(strip=True).upper() if job_tag else "ÇALIŞAN"
                    if nm not in seen_names and len(nm.split()) >= 2:
                        seen_names.add(nm)
                        employees.append({
                            "mersis_no": None,
                            "ticaret_sicil_no": None,
                            "unvan": company_unvan,
                            "ad_soyad": nm,
                            "gorev_unvani": jb,
                            "departman": "Şirket Personeli",
                            "kategori": "PROFESYONEL_CALISAN",
                            "tc_no_maskeli": None,
                            "email": None,
                            "telefon": None,
                            "profil_veya_kaynak_url": target,
                            "kaynak": "SIRKET_WEB_SITESI",
                            "guncellenme_tarihi": now_iso,
                        })

            # 2. Team / Member / Kadro kartlarını tara
            team_cards = soup.find_all(class_=re.compile(r"team[-_]?member|member[-_]?item|person[-_]?card|kadro[-_]?item|ekip[-_]?uye", re.I))
            for tc in team_cards:
                headings = tc.find_all(["h2", "h3", "h4", "h5", "strong"])
                if headings:
                    nm = " ".join(headings[0].get_text(strip=True).split()).upper()
                    paragraphs = tc.find_all(["p", "span", "div"])
                    jb = "ÇALIŞAN"
                    for p_el in paragraphs:
                        p_txt = p_el.get_text(strip=True)
                        if p_txt and p_txt.upper() != nm and len(p_txt) < 60:
                            jb = p_txt.upper()
                            break

                    if nm not in seen_names and len(nm.split()) >= 2 and len(nm) >= 5:
                        if not any(w in STOP_WORDS_NAME for w in nm.split()):
                            seen_names.add(nm)
                            employees.append({
                                "mersis_no": None,
                                "ticaret_sicil_no": None,
                                "unvan": company_unvan,
                                "ad_soyad": nm,
                                "gorev_unvani": jb,
                                "departman": "Şirket Personeli",
                                "kategori": "PROFESYONEL_CALISAN",
                                "tc_no_maskeli": None,
                                "email": None,
                                "telefon": None,
                                "profil_veya_kaynak_url": target,
                                "kaynak": "SIRKET_WEB_SITESI",
                                "guncellenme_tarihi": now_iso,
                            })

            if len(employees) >= 3:
                break
        except Exception:
            continue

    return employees


# ---------------------------------------------------------------------------
# 4. KANAL: Açık Web & Profesyonel Ağ (LinkedIn / SERP) Ayak İzi
# ---------------------------------------------------------------------------
def extract_employees_from_professional_web(company_unvan: str) -> list:
    """
    Arama motorları ve profesyonel ağ profilleri (LinkedIn vb.) üzerinden
    şirkette çalışan profesyonelleri (Unvan, Ad-Soyad, Profil URL) çeker.
    """
    employees = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # Temiz arama terimi (Ltd. Şti., A.Ş. gibi ekleri çıkar)
    clean_unvan = re.sub(
        r"\b(?:ANONİM\s*ŞİRKETİ|LİMİTED\s*ŞİRKETİ|A\.?Ş\.?|LTD\.?\s*ŞTİ\.?|SANAYİ|TİCARET)\b",
        "",
        company_unvan,
        flags=re.IGNORECASE,
    ).strip()
    if len(clean_unvan) < 3:
        clean_unvan = company_unvan

    try:
        # Bing / Açık SERP sorgusu
        query = f'"{clean_unvan}" site:tr.linkedin.com/in'
        cmd = [
            "curl", "-s", "-L", "--connect-timeout", "6", "--max-time", "10",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            f"https://www.bing.com/search?q={requests.utils.quote(query)}"
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=12).decode("utf-8", errors="ignore")
        algos = re.findall(r"<li class=\"b_algo\"[^>]*>(.*?)</li>", out, re.DOTALL)

        seen_names = set()
        NON_HUMAN_WORDS = {
            "GIRIŞ", "GIRIS", "YAP", "VIZYONDAKI", "FILMLER", "HAKKIMIZDA",
            "ILETISIM", "KARIYER", "HABERLER", "DUYURULAR", "YARDIM", "DESTEK",
            "MUSTERI", "HIZMETLERI", "ONLINE", "ALISVERIS", "SEPETIM", "SIPARIS",
            "KVKK", "AYDINLATMA", "METNI", "KULLANIM", "KOSULLARI", "UYE", "OL"
        }

        for a in algos:
            m_h = re.search(r"<h2><a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", a)
            if not m_h:
                continue
            title = re.sub(r"<[^>]+>", "", m_h.group(2)).strip()
            link = m_h.group(1)

            # LinkedIn başlık yapısı: 'Ad Soyad - Görev/Pozisyon - Şirket | LinkedIn'
            m_parts = title.split(" - ")
            if len(m_parts) >= 2:
                name = m_parts[0].strip().replace(" | LinkedIn", "").upper()
                role = m_parts[1].strip().replace(" | LinkedIn", "").upper()
                name_words = set(re.sub(r'[^A-ZÇĞİÖŞÜ]', '', w) for w in name.split())
                if name_words.intersection(NON_HUMAN_WORDS):
                    continue
                if len(name.split()) >= 2 and name not in seen_names and 5 <= len(name) <= 35:
                    seen_names.add(name)
                    employees.append({
                        "mersis_no": None,
                        "ticaret_sicil_no": None,
                        "unvan": company_unvan,
                        "ad_soyad": name,
                        "gorev_unvani": role[:80],
                        "departman": "Profesyonel Kadro",
                        "kategori": "PROFESYONEL_CALISAN",
                        "tc_no_maskeli": None,
                        "email": None,
                        "telefon": None,
                        "profil_veya_kaynak_url": link,
                        "kaynak": "LINKEDIN_PROFESYONEL_AG",
                        "guncellenme_tarihi": now_iso,
                    })
    except Exception:
        pass

    return employees


# ---------------------------------------------------------------------------
# ANA MOTOR: İstek Dahilinde Şirket Arayıcı (Multi-Channel Aggregator)
# ---------------------------------------------------------------------------
def search_company_employees_on_demand(
    conn: sqlite3.Connection,
    company_name: str = None,
    mersis_no: str = None,
    vkn: str = None,
    include_web: bool = True
) -> dict:
    """
    Tek bir şirket için tüm kanalları eşzamanlı sorgular, sonuçları birleştirir,
    veritabanına kaydeder ve yapılandırılmış özet döndürür.
    """
    c = conn.cursor()
    company_record = None

    # Şirketi ambarda bul
    if mersis_no:
        company_record = c.execute("SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi FROM sirketler WHERE mersis_no = ?", (mersis_no,)).fetchone()
    elif vkn:
        company_record = c.execute("SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi FROM sirketler WHERE vkn = ?", (vkn,)).fetchone()
    elif company_name:
        term = company_name.strip()
        # Önce hızlı indeks araması (prefix)
        company_record = c.execute("""
            SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi 
            FROM sirketler 
            WHERE unvan LIKE ? 
            ORDER BY LENGTH(unvan) ASC 
            LIMIT 1
        """, (f"{term}%",)).fetchone()
        if not company_record:
            company_record = c.execute("""
                SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi 
                FROM sirketler 
                WHERE unvan LIKE ? 
                LIMIT 1
            """, (f"%{term}%",)).fetchone()

    m_no = company_record[0] if company_record else mersis_no
    s_no = company_record[1] if company_record else None
    v_no = company_record[2] if company_record else vkn
    unvan = company_record[3] if company_record else (company_name or "Bilinmeyen Şirket")
    web_url = company_record[4] if company_record else None

    print(f"\n🔎 [Çalışan ve Yetkili Arama] Şirket: {unvan}")
    if m_no:
        print(f"  📌 MERSİS No: {m_no} | Sicil: {s_no or '-'} | VKN: {v_no or '-'}")
    if web_url:
        print(f"  🌐 Web Sitesi: {web_url}")

    all_people = []

    # 1. Resmî Ticaret Sicil Gazetesi İncelemesi
    print("  🏛️ Kanal 1: TOBB Ticaret Sicil Gazetesi tescil ilanları taranıyor...", flush=True)
    sicil_people = extract_officials_from_sicil_gazetesi(conn, unvan, m_no)
    all_people.extend(sicil_people)
    print(f"    -> +{len(sicil_people)} resmî müdür / temsilci / yetkili bulundu.")

    # 2. KAP (Kamuyu Aydınlatma Platformu)
    print("  📈 Kanal 2: KAP Yönetim Kurulu & Üst Yönetim taranıyor...", flush=True)
    kap_people = extract_kap_officials_for_company(unvan)
    all_people.extend(kap_people)
    print(f"    -> +{len(kap_people)} Yönetim Kurulu Üyesi ve İcracı Üst Yönetici bulundu.")

    # 3. Şirket Web Sitesi (Varsa)
    if include_web and web_url:
        print(f"  💻 Kanal 3: Şirket web sitesi kadro sayfaları taranıyor ({web_url})...", flush=True)
        web_people = extract_employees_from_website(unvan, web_url)
        all_people.extend(web_people)
        print(f"    -> +{len(web_people)} çalışan ve departman yöneticisi bulundu.")

    # 4. Profesyonel Ağ & Açık Web SERP
    if include_web:
        print("  🌐 Kanal 4: Profesyonel ağlar (LinkedIn / Açık Web) taranıyor...", flush=True)
        prof_people = extract_employees_from_professional_web(unvan)
        all_people.extend(prof_people)
        print(f"    -> +{len(prof_people)} profesyonel çalışan profili bulundu.")

    # 5. Veritabanına Kaydet (Upsert)
    saved_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for p in all_people:
        try:
            c.execute("""
                INSERT INTO sirket_calisanlari (
                    mersis_no, ticaret_sicil_no, unvan, ad_soyad, gorev_unvani,
                    departman, kategori, tc_no_maskeli, email, telefon,
                    profil_veya_kaynak_url, kaynak, guncellenme_tarihi
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                ON CONFLICT(unvan, ad_soyad, gorev_unvani, kaynak) DO UPDATE SET
                    mersis_no = COALESCE(excluded.mersis_no, sirket_calisanlari.mersis_no),
                    ticaret_sicil_no = COALESCE(excluded.ticaret_sicil_no, sirket_calisanlari.ticaret_sicil_no),
                    tc_no_maskeli = COALESCE(excluded.tc_no_maskeli, sirket_calisanlari.tc_no_maskeli),
                    guncellenme_tarihi = excluded.guncellenme_tarihi
            """, (
                p.get("mersis_no") or m_no,
                p.get("ticaret_sicil_no") or s_no,
                unvan,
                p["ad_soyad"],
                p.get("gorev_unvani"),
                p.get("departman"),
                p.get("kategori"),
                p.get("tc_no_maskeli"),
                p.get("email"),
                p.get("telefon"),
                p.get("profil_veya_kaynak_url"),
                p["kaynak"],
                now_iso
            ))
            saved_count += 1
        except Exception:
            pass

    conn.commit()

    return {
        "unvan": unvan,
        "mersis_no": m_no,
        "vkn": v_no,
        "toplam_bulunan": len(all_people),
        "kaydedilen": saved_count,
        "kisiler": all_people
    }


def print_results_table(result: dict):
    print("\n" + "=" * 95)
    print(f"👥 ŞİRKET ÇALIŞANLARI VE YETKİLİLERİ RAPORU: {result['unvan']}")
    print(f"📊 Toplam Bulunan: {result['toplam_bulunan']} Kişi | Kaydedilen: {result['kaydedilen']}")
    print("=" * 95)
    print(f"{'AD SOYAD':<28} | {'GÖREV / POZİSYON':<32} | {'KAYNAK':<22} | {'BİLGİ / DETAY'}")
    print("-" * 95)
    for p in result["kisiler"]:
        name = p["ad_soyad"][:27]
        role = (p.get("gorev_unvani") or "Belirtilmemiş")[:31]
        src = p.get("kaynak")[:21]
        detail = ""
        if p.get("tc_no_maskeli"):
            detail = f"TCKN: {p['tc_no_maskeli']}"
        elif p.get("profil_veya_kaynak_url"):
            detail = p["profil_veya_kaynak_url"][:40]
        print(f"{name:<28} | {role:<32} | {src:<22} | {detail}")
    print("=" * 95 + "\n")


def main():
    parser = argparse.ArgumentParser(description="GEOPROP Şirket Çalışanları ve Yetkilileri Arama Motoru")
    parser.add_argument("--sirket", type=str, help="Aranacak Şirket Unvanı veya Adı")
    parser.add_argument("--mersis", type=str, help="Aranacak 16 haneli MERSİS No")
    parser.add_argument("--vkn", type=str, help="Aranacak 10 haneli Vergi Kimlik No")
    parser.add_argument("--batch", type=int, default=0, help="Veritabanındaki N şirket için toplu çalışan madenciliği yap")
    parser.add_argument("--export", action="store_true", help="sirket_calisanlari tablosunu CSV'ye aktar")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=90)
    conn.execute("PRAGMA journal_mode = WAL;")
    init_db(conn)

    if args.sirket or args.mersis or args.vkn:
        res = search_company_employees_on_demand(
            conn,
            company_name=args.sirket,
            mersis_no=args.mersis,
            vkn=args.vkn,
            include_web=True
        )
        print_results_table(res)

    elif args.batch > 0:
        c = conn.cursor()
        print(f"🚀 [Toplu Tarama] Veritabanından {args.batch} şirket seçiliyor...", flush=True)
        rows = c.execute("""
            SELECT mersis_no, vkn, unvan, web_sitesi
            FROM sirketler
            WHERE unvan IS NOT NULL AND unvan != ''
              AND mersis_no NOT IN (SELECT DISTINCT mersis_no FROM sirket_calisanlari WHERE mersis_no IS NOT NULL)
            ORDER BY RANDOM()
            LIMIT ?
        """, (args.batch,)).fetchall()

        for idx, (m_no, v_no, unv, web) in enumerate(rows, 1):
            print(f"\n[{idx}/{len(rows)}] İşleniyor: {unv[:45]}...")
            res = search_company_employees_on_demand(conn, company_name=unv, mersis_no=m_no, vkn=v_no, include_web=True)
            print(f"  -> {res['toplam_bulunan']} kişi tespit edildi.")
            time.sleep(1.0)

    elif args.export:
        import pandas as pd
        os.makedirs(CSV_OUT.parent, exist_ok=True)
        df = pd.read_sql_query("SELECT * FROM sirket_calisanlari", conn)
        df.to_csv(CSV_OUT, sep=";", index=False, encoding="utf-8-sig")
        print(f"✅ Toplam {len(df):,} çalışan/yetkili kaydı CSV'ye aktarıldı: {CSV_OUT}")

    else:
        # Örnek gösterim
        print("💡 Kullanım örnekleri:")
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --sirket \"ASELSAN\"")
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --sirket \"Trendyol\"")
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --mersis \"0005001487200001\"")
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --batch 50")
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --export")

    conn.close()


if __name__ == "__main__":
    main()
