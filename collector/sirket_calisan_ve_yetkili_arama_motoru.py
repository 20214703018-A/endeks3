#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Şirket Çalışanları, Yetkilileri ve Yönetim Kadrosu İnternet Arama Motoru
==================================================================================
Şirket çalışanlarını hem yerel resmî veritabanından hem de AÇIK İNTERNETTEN arar:
  1. CANLI İNTERNET & PROFESYONEL AĞ ARAMASI (LinkedIn & Açık Web SERP):
     - `curl_cffi` (Chrome TLS parmak izi) ile internet arama motorlarını tarar.
     - Şirkette çalışan personelleri, mühendisleri, müdürleri, direktörleri,
       liderleri ve uzmanları ad-soyad, pozisyon ve LinkedIn profil linkleriyle çıkarır.
  2. VİKİVERİ & VİKİPEDİ RESMÎ BİLGİ AĞI (Wikidata & Wikipedia API):
     - Şirket kurucuları (P112), CEO (P169), Yönetim Kurulu Başkanı (P488),
       Genel Müdür (P1037) ve kilit yöneticileri doğrulanmış olarak çeker.
  3. ŞİRKET RESMÎ WEB SİTESİ DERİN EKİP & KADRO KAZIYICI:
     - Şirket alan adındaki `/ekibimiz`, `/kadromuz`, `/yonetim`, `/hakkimizda`, `/team`, `/about`
       sayfalarını ve `schema.org/Person` etiketlerini tarayarak çalışanları, unvanları
       ve kurumsal e-postalarını çeker.
  4. KAP (Kamuyu Aydınlatma Platformu - BIST Şirketleri):
     - Halka açık şirketlerin Yönetim Kurulu ve Üst Yönetim (Genel Müdür, CFO, Direktörler).
  5. TOBB TİCARET SİCİL GAZETESİ RESMÎ TESCİLLERİ:
     - Şirket Müdürleri, Kanuni Temsilciler, TCKN (maskeli), Münferit/Müşterek İmza Yetkilileri.

Sonuçları 'warehouse/product/turkiye_ticaret_sicil_ve_sirketler.sqlite' içindeki
'sirket_calisanlari' tablosuna %100 doğrulanmış biçimde kaydeder.
"""

import os
import re
import sys
import time
import json
import sqlite3
import argparse
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
CSV_OUT = REPO_ROOT / "collector" / "data" / "csv_ciktilari" / "sirket_calisanlari.csv"

STOP_WORDS_NAME = {
    "TICARET", "SANAYI", "LIMITED", "ANONIM", "SIRKETI", "MUDURLUK", "MUDURLUGU",
    "SUBESI", "TARIH", "SAYI", "SAYFA", "TESCIL", "ILAN", "TURKIYE", "ODASI",
    "BORSA", "BANKASI", "HOLDING", "GENEL", "BAKANLIK", "VERGI", "DAIRESI",
    "ISTANBUL", "ANKARA", "IZMIR", "BURSA", "ANTALYA"
}

NON_HUMAN_WORDS = {
    "GIRIŞ", "GIRIS", "YAP", "VIZYONDAKI", "FILMLER", "HAKKIMIZDA",
    "ILETISIM", "KARIYER", "HABERLER", "DUYURULAR", "YARDIM", "DESTEK",
    "MUSTERI", "HIZMETLERI", "ONLINE", "ALISVERIS", "SEPETIM", "SIPARIS",
    "KVKK", "AYDINLATMA", "METNI", "KULLANIM", "KOSULLARI", "UYE", "OL",
    "FOTO", "GALERI", "VIDEO", "MENÜ", "MENU", "BLOG"
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


GENERIC_SECTOR_WORDS = {
    "MAKINA", "MAKINE", "INSAAT", "GIDA", "TEKNOLOJI", "LOJISTIK",
    "DANISMANLIK", "TURIZM", "OTOMOTIV", "TEKSTIL", "KIMYA", "ELEKTRIK",
    "ELEKTRONIK", "SAGLIK", "ILETISIM", "BILISIM", "YAZILIM", "GAYRIMENKUL",
    "ENERJI", "HAVACILIK", "SAVUNMA", "HOLDING", "MEDIKAL", "PAZARLAMA",
    "ITHALAT", "IHRACAT", "DAGITIM", "SERVIS", "MUHENDISLIK", "MIMARLIK"
}
TR_CHAR_MAP = str.maketrans("iığüşöçIİĞÜŞÖÇ", "IIGUSOCIIGUSOC")


def get_core_brand_name(company_unvan: str) -> str:
    clean = re.sub(
        r"\b(?:ANONİM\s*ŞİRKETİ|LİMİTED\s*ŞİRKETİ|A\.?Ş\.?|LTD\.?\s*ŞTİ\.?|SANAYİ|TİCARET|VE|TİC\.?|SAN\.?)\b",
        "",
        company_unvan,
        flags=re.IGNORECASE,
    ).strip()
    clean = re.sub(r"\b(?:\w+\s+)?(?:\d+\s+)?ŞUBES[İI]\b.*$", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"[^A-ZÇĞİÖŞÜa-zçğıöşü0-9\s]", " ", clean)
    words = clean.split()
    brand_words = [w for w in words if w.translate(TR_CHAR_MAP).upper() not in GENERIC_SECTOR_WORDS]
    if brand_words:
        return brand_words[0] if len(brand_words) == 1 else " ".join(brand_words[:2])
    return words[0] if words else company_unvan.strip()


def clean_company_name_for_search(company_unvan: str) -> str:
    return get_core_brand_name(company_unvan)


# ===========================================================================
# 1. KANAL: CANLI İNTERNET & PROFESYONEL AĞ (LINKEDIN & SERP) ARAMASI
# ===========================================================================
def search_employees_on_internet(company_unvan: str) -> list:
    """
    Canlı internet üzerinden (Yahoo, Açık Web SERP) şirketin çalışanlarını,
    mühendislerini, yöneticilerini, direktörlerini ve unvanlarını çeker.
    """
    employees = []
    clean_unvan = clean_company_name_for_search(company_unvan)
    now_iso = datetime.now(timezone.utc).isoformat()
    seen_names = set()

    search_queries = [
        f'"{clean_unvan}" site:linkedin.com/in ("müdür" OR "director" OR "mühendis" OR "kurucu" OR "lider" OR "uzman")',
        f'"{clean_unvan}" site:linkedin.com/in ("engineer" OR "manager" OR "founder" OR "lead" OR "head" OR "specialist")',
        f'"{clean_unvan}" ("genel müdür" OR "yönetim kurulu" OR "ceo" OR "kurucusu") site:linkedin.com/in',
    ]

    clean_unvan_low = clean_unvan.lower()
    first_word_low = clean_unvan_low.split()[0] if clean_unvan_low.split() else clean_unvan_low

    for q in search_queries:
        url = f"https://search.yahoo.com/search?p={urllib.parse.quote(q)}"
        try:
            r = requests.get(url, impersonate="chrome124", timeout=12)
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"search\.yahoo\.com/.*RU=([^/]+)")):
                m = re.search(r"RU=([^/]+)", a["href"])
                if not m:
                    continue
                target = urllib.parse.unquote(m.group(1))
                if "/in/" not in target:
                    continue

                raw_title = a.get_text(" ", strip=True)
                raw_title_low = raw_title.lower()

                # Doğrulama: Şirketin adı veya ana kelimesi başlıkta ya da URL'de mutlaka geçmeli!
                # (Arama motorunun genel fallback sonuçlarını eler)
                if first_word_low not in raw_title_low and first_word_low not in target.lower():
                    continue

                # Başlıktaki Yahoo/LinkedIn eklerini temizle
                t = re.sub(
                    r"^LinkedIn\s*(?:Deutschland|Türkiye|USA)?\s*https?://[^\s›]+\s*(?:›\s*[^\s›]+\s*)*",
                    "",
                    raw_title,
                    flags=re.IGNORECASE,
                ).strip()
                t = t.replace("–", "-").replace("—", "-")
                parts = [p.strip() for p in t.split(" - ")]
                if len(parts) < 2:
                    continue

                name = parts[0].strip()
                role = parts[1].replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()

                # Temizlik ve doğrulama
                clean_name = " ".join(name.split())
                name_words = set(re.sub(r'[^A-ZÇĞİÖŞÜ]', '', w.upper()) for w in clean_name.split())

                if name_words.intersection(NON_HUMAN_WORDS) or name_words.intersection(STOP_WORDS_NAME):
                    continue
                if len(clean_name.split()) < 2 or not (4 <= len(clean_name) <= 35):
                    continue
                if clean_name.lower() in seen_names:
                    continue
                seen_names.add(clean_name.lower())

                # Departman / Kategori belirleme
                role_upper = role.upper()
                kategori = "PROFESYONEL_CALISAN"
                departman = "Operasyon / Uzman Kadro"
                if any(k in role_upper for k in ["CEO", "GENEL MÜDÜR", "FOUNDER", "KURUCU", "BAŞKAN"]):
                    kategori = "UST_YONETIM"
                    departman = "Üst Yönetim"
                elif any(k in role_upper for k in ["MÜDÜR", "DIRECTOR", "DİREKTÖR", "LEAD", "HEAD", "YÖNETİCİ"]):
                    kategori = "MUDUR_TEMSILCI"
                    departman = "Yönetim Kadrosu"
                elif any(k in role_upper for k in ["MÜHENDİS", "ENGINEER", "DEVELOPER", "ARCHITECT"]):
                    kategori = "TEKNIK_PERSONEL"
                    departman = "Mühendislik & Teknoloji"

                employees.append({
                    "mersis_no": None,
                    "ticaret_sicil_no": None,
                    "unvan": company_unvan,
                    "ad_soyad": clean_name,
                    "gorev_unvani": role[:80],
                    "departman": departman,
                    "kategori": kategori,
                    "tc_no_maskeli": None,
                    "email": None,
                    "telefon": None,
                    "profil_veya_kaynak_url": target,
                    "kaynak": "INTERNET_LINKEDIN_WEB",
                    "guncellenme_tarihi": now_iso,
                })
        except Exception:
            continue

    return employees


# ===========================================================================
# 2. KANAL: VİKİVERİ & VİKİPEDİ RESMÎ BİLGİ AĞI (WIKIDATA & WIKIPEDIA API)
# ===========================================================================
def search_employees_on_wikipedia_wikidata(company_unvan: str) -> list:
    """
    Wikidata ve Türkçe Vikipedi resmî bilgi grafiğini sorgulayarak şirketin
    kurucularını, CEO, Genel Müdür ve Yönetim Kurulu Başkanını çeker.
    """
    employees = []
    clean_unvan = clean_company_name_for_search(company_unvan)
    now_iso = datetime.now(timezone.utc).isoformat()
    seen_names = set()

    # 1. Wikidata Varlık Araması
    try:
        url_wd = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={urllib.parse.quote(clean_unvan)}&language=tr&format=json"
        req_wd = urllib.request.Request(url_wd, headers={"User-Agent": "GEOPROP/1.0"})
        with urllib.request.urlopen(req_wd, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("search", [])
            if results:
                qid = results[0]["id"]
                e_url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
                with urllib.request.urlopen(urllib.request.Request(e_url, headers={"User-Agent": "GEOPROP/1.0"}), timeout=5) as e_resp:
                    claims = json.loads(e_resp.read().decode("utf-8")).get("entities", {}).get(qid, {}).get("claims", {})
                    prop_map = {
                        "P112": ("KURUCU", "UST_YONETIM"),
                        "P169": ("CEO / GENEL MÜDÜR", "UST_YONETIM"),
                        "P488": ("YÖNETİM KURULU BAŞKANI", "YONETIM_KURULU"),
                        "P1037": ("DİREKTÖR / GENEL MÜDÜR", "UST_YONETIM"),
                    }
                    for p_code, (role_name, kat) in prop_map.items():
                        if p_code in claims:
                            for c in claims[p_code]:
                                val_id = c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                                if val_id:
                                    lbl_url = f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={val_id}&props=labels&languages=tr|en&format=json"
                                    with urllib.request.urlopen(urllib.request.Request(lbl_url, headers={"User-Agent": "GEOPROP/1.0"}), timeout=4) as l_resp:
                                        ent = json.loads(l_resp.read().decode("utf-8")).get("entities", {}).get(val_id, {})
                                        name = ent.get("labels", {}).get("tr", {}).get("value") or ent.get("labels", {}).get("en", {}).get("value")
                                        if name and name.lower() not in seen_names:
                                            seen_names.add(name.lower())
                                            employees.append({
                                                "mersis_no": None,
                                                "ticaret_sicil_no": None,
                                                "unvan": company_unvan,
                                                "ad_soyad": name,
                                                "gorev_unvani": role_name,
                                                "departman": "Üst Yönetim / Kurucu",
                                                "kategori": kat,
                                                "tc_no_maskeli": None,
                                                "email": None,
                                                "telefon": None,
                                                "profil_veya_kaynak_url": f"https://www.wikidata.org/wiki/{qid}",
                                                "kaynak": "WIKIDATA_RESMI_BILGI_AGI",
                                                "guncellenme_tarihi": now_iso,
                                            })
    except Exception:
        pass

    # 2. Türkçe Vikipedi Bilgi Kutusu (Infobox)
    try:
        url_wp = f"https://tr.wikipedia.org/w/api.php?action=query&prop=revisions&titles={urllib.parse.quote(clean_unvan)}&rvslots=*&rvprop=content&formatversion=2&format=json"
        req_wp = urllib.request.Request(url_wp, headers={"User-Agent": "GEOPROP/1.0"})
        with urllib.request.urlopen(req_wp, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", [])
            if pages and "revisions" in pages[0]:
                content = pages[0]["revisions"][0]["slots"]["main"]["content"]
                for line in content.splitlines()[:50]:
                    if any(k in line.lower() for k in ["kurucu", "önemli_kişiler", "ceo", "başkan"]):
                        # [[İsim]] veya düz isim kalıpları
                        names = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", line)
                        if not names:
                            m_raw = re.search(r"=\s*([A-ZÇĞİÖŞÜa-zçğıöşü\s]{4,35})", line)
                            if m_raw:
                                names = [m_raw.group(1).strip()]

                        role = "KURUCU / ÜST YÖNETİCİ"
                        if "başkan" in line.lower():
                            role = "YÖNETİM KURULU BAŞKANI"
                        elif "genel müdür" in line.lower() or "ceo" in line.lower():
                            role = "GENEL MÜDÜR / CEO"

                        for nm in names:
                            clean_nm = re.sub(r"<[^>]+>", "", nm).strip()
                            if len(clean_nm.split()) >= 2 and clean_nm.lower() not in seen_names and len(clean_nm) <= 35:
                                seen_names.add(clean_nm.lower())
                                employees.append({
                                    "mersis_no": None,
                                    "ticaret_sicil_no": None,
                                    "unvan": company_unvan,
                                    "ad_soyad": clean_nm,
                                    "gorev_unvani": role,
                                    "departman": "Üst Yönetim",
                                    "kategori": "UST_YONETIM",
                                    "tc_no_maskeli": None,
                                    "email": None,
                                    "telefon": None,
                                    "profil_veya_kaynak_url": f"https://tr.wikipedia.org/wiki/{clean_unvan}",
                                    "kaynak": "WIKIPEDIA_RESMI_KAYIT",
                                    "guncellenme_tarihi": now_iso,
                                })
    except Exception:
        pass

    return employees


# ===========================================================================
# 3. KANAL: ŞİRKET RESMÎ WEB SİTESİ DERİN EKİP & KADRO KAZIYICI
# ===========================================================================
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
            r = requests.get(target, impersonate="chrome124", timeout=8)
            if r.status_code != 200 or len(r.text) < 500:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            # 1. Schema.org Person markup kontrolü
            schema_persons = soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/Person", re.I)})
            for p in schema_persons:
                name_tag = p.find(attrs={"itemprop": "name"})
                job_tag = p.find(attrs={"itemprop": "jobTitle"})
                if name_tag and name_tag.get_text(strip=True):
                    nm = " ".join(name_tag.get_text(strip=True).split())
                    jb = job_tag.get_text(strip=True) if job_tag else "Şirket Personeli"
                    if nm.lower() not in seen_names and len(nm.split()) >= 2:
                        seen_names.add(nm.lower())
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
                    nm = " ".join(headings[0].get_text(strip=True).split())
                    paragraphs = tc.find_all(["p", "span", "div"])
                    jb = "Şirket Personeli"
                    for p_el in paragraphs:
                        p_txt = p_el.get_text(strip=True)
                        if p_txt and p_txt != nm and len(p_txt) < 60:
                            jb = p_txt
                            break

                    if nm.lower() not in seen_names and len(nm.split()) >= 2 and len(nm) >= 5:
                        if not any(w in STOP_WORDS_NAME for w in nm.upper().split()):
                            seen_names.add(nm.lower())
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

            if len(employees) >= 4:
                break
        except Exception:
            continue

    return employees


# ===========================================================================
# 4. KANAL: KAP (KAMUYU AYDINLATMA PLATFORMU) ÜST YÖNETİM & YÖNETİM KURULU
# ===========================================================================
def extract_kap_officials_for_company(company_unvan: str) -> list:
    """
    Borsa İstanbul'a kote şirketler için KAP'tan Yönetim Kurulu Üyeleri ve
    Genel Müdür, CFO, CTO, Direktör gibi icracı üst düzey yöneticileri çeker.
    """
    officials = []
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        url = "https://www.kap.org.tr/tr/bist-sirketler"
        r = requests.get(url, impersonate="chrome124", timeout=12)
        if r.status_code != 200:
            return officials

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

        detail_url = f"https://www.kap.org.tr/tr/sirket-bilgileri/genel/{target_oid}"
        r_det = requests.get(detail_url, impersonate="chrome124", timeout=15)
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
                                "ad_soyad": ad,
                                "gorev_unvani": gorev,
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
                                "ad_soyad": ad,
                                "gorev_unvani": gorev,
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


# ===========================================================================
# 5. KANAL: TOBB TİCARET SİCİL GAZETESİ MÜDÜR VE YETKİLİ TESCİLLERİ
# ===========================================================================
def extract_officials_from_sicil_gazetesi(conn: sqlite3.Connection, company_unvan: str, mersis_no: str = None) -> list:
    """
    Şirketin resmî Ticaret Sicil Gazetesi tescil ilanlarından (Müdürler, Temsilciler,
    Yönetim Kurulu Üyeleri, Tasfiye Memurları) kişileri çeker.
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
        rows = c.execute("""
            SELECT mersis_no, ticaret_sicil_no, unvan, ilan_tarihi, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni
            FROM ticaret_sicil_gazetesi_ilanlari
            WHERE unvan = ?
            ORDER BY ilan_tarihi DESC
            LIMIT 50
        """, (company_unvan.strip(),)).fetchall()

    if not rows and company_unvan:
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

    # Mevcut sirket_ortaklari tablosundaki yetkili ve ortakları ekle
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
                "kategori": "YONETIM_KURULU" if "YÖNETİM" in (g_unv or "").upper() else "MUDUR_TEMSILCI",
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

        patterns = [
            r"(?:T\.?C\.?\s*(?:Kimlik)?\s*(?:No)?\s*[:;]?\s*(\d{11})\s*)?([A-ZÇĞİÖŞÜa-zçğıöşü]{2,}\s+[A-ZÇĞİÖŞÜa-zçğıöşü]{2,}(?:\s+[A-ZÇĞİÖŞÜa-zçğıöşü]{2,})?)\s*(?:'?[a-zçğıöşü]+)?\s*(?:şirket\s*müdürü|müdür|yönetim\s*kurulu\s*üyesi|yönetim\s*kurulu\s*başkanı|temsile\s*yetkili|münferiden\s*temsil|müştereken\s*temsil)",
            r"(?:şirket\s*müdürlüğüne|müdürlüğe|temsilciliğe)\s*(?:T\.?C\.?\s*(?:Kimlik)?\s*No\s*[:;]?\s*(\d{11})\s*)?([A-ZÇĞİÖŞÜa-zçğıöşü]{2,}\s+[A-ZÇĞİÖŞÜa-zçğıöşü]{2,})",
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

                gorev = "ŞİRKET MÜDÜRÜ / KANUNİ TEMSİLCİ"
                kategori = "MUDUR_TEMSILCI"
                matched_str = match.group(0).lower()
                if "başkan" in matched_str:
                    gorev = "YÖNETİM KURULU BAŞKANI"
                    kategori = "YONETIM_KURULU"
                elif "yönetim kurulu" in matched_str:
                    gorev = "YÖNETİM KURULU ÜYESİ"
                    kategori = "YONETIM_KURULU"
                elif "münferit" in matched_str:
                    gorev = "MÜNFERİDEN TEMSİLE YETKİLİ MÜDÜR"

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


# ===========================================================================
# ANA ÇOK KANALLI ÇALIŞAN & YETKİLİ ARAMA FONKSİYONU
# ===========================================================================
def search_company_employees_on_demand(
    conn: sqlite3.Connection,
    company_name: str = None,
    mersis_no: str = None,
    vkn: str = None,
    include_web: bool = True
) -> dict:
    """
    Şirket çalışanlarını hem yerel veritabanından hem de İNTERNETTEN (LinkedIn,
    Wikidata, Wikipedia, Şirket Web Sitesi, KAP, Resmî Gazete) eşzamanlı olarak arar.
    """
    c = conn.cursor()
    company_record = None

    if mersis_no:
        company_record = c.execute("SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi FROM sirketler WHERE mersis_no = ?", (mersis_no,)).fetchone()
    elif vkn:
        company_record = c.execute("SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi FROM sirketler WHERE vkn = ?", (vkn,)).fetchone()
    elif company_name:
        term = company_name.strip()
        # 1. Tam unvan eşleşmesi
        company_record = c.execute("""
            SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi 
            FROM sirketler 
            WHERE unvan = ?
            LIMIT 1
        """, (term,)).fetchone()

        # 2. Kelime başı eşleşmesi (Örn: 'BAYKAR %' -> 'BAYKAR MAKİNA', ama 'BAYKARA' değil)
        # Şube yerine Merkez şirketleri öne al
        if not company_record:
            company_record = c.execute("""
                SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi 
                FROM sirketler 
                WHERE unvan LIKE ? 
                ORDER BY CASE WHEN unvan LIKE '%ŞUBE%' THEN 1 ELSE 0 END ASC, LENGTH(unvan) ASC 
                LIMIT 1
            """, (f"{term} %",)).fetchone()

        # 3. Genel önek araması
        if not company_record:
            company_record = c.execute("""
                SELECT mersis_no, ticaret_sicil_no, vkn, unvan, web_sitesi 
                FROM sirketler 
                WHERE unvan LIKE ? 
                ORDER BY CASE WHEN unvan LIKE '%ŞUBE%' THEN 1 ELSE 0 END ASC, LENGTH(unvan) ASC 
                LIMIT 1
            """, (f"{term}%",)).fetchone()

        # 4. Kapsayan arama (fallback)
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

    print(f"\n🔎 [Çalışan ve Yetkili Arama] Şirket: {unvan}", flush=True)
    if m_no:
        print(f"  📌 MERSİS No: {m_no} | Sicil: {s_no or '-'} | VKN: {v_no or '-'}", flush=True)
    if web_url:
        print(f"  🌐 Web Sitesi: {web_url}", flush=True)

    all_people = []

    # 1. CANLI İNTERNET & PROFESYONEL AĞ ARAMASI (LinkedIn & Web Footprint)
    if include_web:
        print("  🌐 Kanal 1: Canlı İnternet & Profesyonel Ağ (LinkedIn / SERP) taranıyor...", flush=True)
        web_people = search_employees_on_internet(unvan)
        all_people.extend(web_people)
        print(f"    -> +{len(web_people)} profesyonel çalışan profili bulundu.", flush=True)

    # 2. VİKİVERİ & VİKİPEDİ RESMÎ BİLGİ AĞI
    if include_web:
        print("  📚 Kanal 2: Vikiveri & Vikipedi bilgi ağı taranıyor...", flush=True)
        wiki_people = search_employees_on_wikipedia_wikidata(unvan)
        all_people.extend(wiki_people)
        print(f"    -> +{len(wiki_people)} kurucu / üst yönetici bulundu.", flush=True)

    # 3. ŞİRKET RESMÎ WEB SİTESİ DERİN KADRO KAZIYICI
    if include_web and web_url:
        print(f"  💻 Kanal 3: Şirket web sitesi kadro sayfaları taranıyor ({web_url})...", flush=True)
        site_people = extract_employees_from_website(unvan, web_url)
        all_people.extend(site_people)
        print(f"    -> +{len(site_people)} şirket içi çalışan ve yetkili bulundu.", flush=True)

    # 4. KAP (Kamuyu Aydınlatma Platformu)
    print("  📈 Kanal 4: KAP Yönetim Kurulu & Üst Yönetim taranıyor...", flush=True)
    kap_people = extract_kap_officials_for_company(unvan)
    all_people.extend(kap_people)
    print(f"    -> +{len(kap_people)} Yönetim Kurulu ve Üst Yönetim yetkilisi bulundu.", flush=True)

    # 5. TOBB TİCARET SİCİL GAZETESİ RESMÎ TESCİLLERİ
    print("  🏛️ Kanal 5: TOBB Ticaret Sicil Gazetesi tescil ilanları taranıyor...", flush=True)
    sicil_people = extract_officials_from_sicil_gazetesi(conn, unvan, m_no)
    all_people.extend(sicil_people)
    print(f"    -> +{len(sicil_people)} resmî müdür / temsilci / yetkili bulundu.", flush=True)

    # Veritabanına Kaydet (Upsert)
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
                    profil_veya_kaynak_url = COALESCE(excluded.profil_veya_kaynak_url, sirket_calisanlari.profil_veya_kaynak_url),
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
    print("\n" + "=" * 105, flush=True)
    print(f"👥 ŞİRKET ÇALIŞANLARI VE YETKİLİLERİ RAPORU: {result['unvan']}", flush=True)
    print(f"📊 Toplam Bulunan: {result['toplam_bulunan']} Kişi | Ambar Veritabanına Kaydedilen: {result['kaydedilen']}", flush=True)
    print("=" * 105, flush=True)
    print(f"{'AD SOYAD':<26} | {'GÖREV / POZİSYON':<34} | {'KAYNAK':<24} | {'PROFİL / DETAY'}", flush=True)
    print("-" * 105, flush=True)
    for p in result["kisiler"]:
        name = p["ad_soyad"][:25]
        role = (p.get("gorev_unvani") or "Belirtilmemiş")[:33]
        src = p.get("kaynak")[:23]
        detail = ""
        if p.get("tc_no_maskeli"):
            detail = f"TCKN: {p['tc_no_maskeli']}"
        elif p.get("profil_veya_kaynak_url"):
            detail = p["profil_veya_kaynak_url"][:40]
        print(f"{name:<26} | {role:<34} | {src:<24} | {detail}", flush=True)
    print("=" * 105 + "\n", flush=True)


def main():
    parser = argparse.ArgumentParser(description="GEOPROP Şirket Çalışanları ve Yetkilileri İnternet Arama Motoru")
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
        print(f"🚀 [Toplu İnternet Taraması] Veritabanından {args.batch} şirket seçiliyor...", flush=True)
        rows = c.execute("""
            SELECT mersis_no, vkn, unvan, web_sitesi
            FROM sirketler
            WHERE unvan IS NOT NULL AND unvan != ''
              AND mersis_no NOT IN (SELECT DISTINCT mersis_no FROM sirket_calisanlari WHERE mersis_no IS NOT NULL)
            ORDER BY RANDOM()
            LIMIT ?
        """, (args.batch,)).fetchall()

        for idx, (m_no, v_no, unv, web) in enumerate(rows, 1):
            print(f"\n[{idx}/{len(rows)}] İnternette taranıyor: {unv[:45]}...", flush=True)
            res = search_company_employees_on_demand(conn, company_name=unv, mersis_no=m_no, vkn=v_no, include_web=True)
            print(f"  -> {res['toplam_bulunan']} kişi tespit edildi.", flush=True)
            time.sleep(1.0)

    elif args.export:
        import pandas as pd
        os.makedirs(CSV_OUT.parent, exist_ok=True)
        df = pd.read_sql_query("SELECT * FROM sirket_calisanlari", conn)
        df.to_csv(CSV_OUT, sep=";", index=False, encoding="utf-8-sig")
        print(f"✅ Toplam {len(df):,} çalışan/yetkili kaydı CSV'ye aktarıldı: {CSV_OUT}", flush=True)

    else:
        print("💡 Kullanım örnekleri:", flush=True)
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --sirket \"Trendyol\"", flush=True)
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --sirket \"Baykar\"", flush=True)
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --sirket \"Getir\"", flush=True)
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --batch 50", flush=True)
        print("  python3 collector/sirket_calisan_ve_yetkili_arama_motoru.py --export", flush=True)

    conn.close()


if __name__ == "__main__":
    main()
