#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB Ticaret Sicil Gazetesi 40-Shard OCR & Sayfa İndeksli Şirket/Kişi Veritabanı Motoru
==================================================================================================
1. Eldeki tüm indirilen PDF sayfalarını (`tobb_training_pages.jsonl.gz` - 503.317+ tekil sayfa)
   ve `.tar.gz` arşiv paketlerini 40 GitHub Actions makinesinde paralel olarak (160 vCPU çekirdeği)
   Tesseract Türkçe OCR (`tessdata_fast` + `OMP_THREAD_LIMIT=1`) ile okur.
2. Her sayfanın tam metnini `zlib` (seviye 9) ile sıkıştırarak `gazete_sayfa_arsivi` tablosuna
   sıfır veri kaybı (lossless) ve minimum disk boyutuyla (~1.5 KB/sayfa) kaydeder.
3. Sayfadaki HİÇBİR bilgi ve HİÇBİR isim boşa gitmeyecek şekilde:
   - Sayfadaki tüm ilan bloklarını (devam blokları, ticaret sicili müdürlüğü ve eski memurluk
     ilanları dahil) KESİNTİSİZ TAM METNİYLE `sirket_bilgi_sayfa_indeksi` tablosuna kaydeder.
   - İlan içindeki tüm şahıs isimlerini (ortaklar, kurucular, yönetim kurulu üyeleri, müdürler,
     temsilciler, tasfiye memurları, denetçiler, hisse devreden/devralanlar, sicil müdürleri)
     TCKN, görev, ikamet adresi, pay/sermaye bilgisi ve geçtiği cümle bağlamıyla birlikte
     `gazete_kisi_ve_isim_indeksi` tablosuna indeksler.
4. VKN, MERSİS No, Sicil No, Şirket Unvanı, Kişi Adı-Soyadı veya TCKN ile sorgu yapıldığında
   tüm ticari geçmişi kronolojik olarak listeler ve ilgili PDF sayfasını doğrudan çıkarıp gösterir.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTER_DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"

TESSERACT_BIN = (
    shutil.which("tesseract")
    or ("/opt/homebrew/bin/tesseract" if os.path.exists("/opt/homebrew/bin/tesseract") else "/usr/bin/tesseract")
)

STOPWORD_PHRASES = {
    "TÜRKİYE TİCARET", "TİCARET SİCİLİ", "SİCİLİ GAZETESİ", "ANONİM ŞİRKETİ", "LİMİTED ŞİRKETİ",
    "KOLLEKTİF ŞİRKETİ", "KOMANDİT ŞİRKETİ", "KOOPERATİFİ", "YÖNETİM KURULU", "GENEL KURUL",
    "MÜDÜRLER KURULU", "ESAS SÖZLEŞME", "ANA SÖZLEŞME", "VERGİ DAİRESİ", "TÜRK LİRASI",
    "TİCARET UNVANI", "MERSİS NO", "TİCARET SİCİL", "İLAN SIRA", "ADRESİ", "TÜRKİYE CUMHURİYETİ",
    "KİMLİK NUMARALI", "İKAMET EDEN", "TEMSİLE YETKİLİ", "MÜNFERİDEN TEMSİLE", "MÜŞTEREKEN TEMSİLE",
    "ŞİRKET MÜDÜRÜ", "TASFİYE MEMURU", "TASFİYE HALİNDE", "YENİ KAYIT", "KURULUŞ", "TESCİL TARİHİ",
}


def ensure_page_linked_schema(conn: sqlite3.Connection) -> None:
    """
    1. `gazete_sayfa_arsivi`: Sayfa bazlı sıkıştırılmış eksiksiz tam metin arşivi.
    2. `sirket_bilgi_sayfa_indeksi`: Sayfadaki her ilan bloğunun tam metni, şirket künyesi ve PDF referansı.
    3. `gazete_kisi_ve_isim_indeksi`: Sayfalarda geçen tüm gerçek kişi isimleri, TCKN, görev, adres ve pay kayıtları.
    """
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS gazete_sayfa_arsivi (
            gazete_sayisi TEXT NOT NULL,
            sayfa_no TEXT NOT NULL,
            yayin_tarihi TEXT,
            tsm_adi TEXT,
            archive_url TEXT NOT NULL,
            pdf_member TEXT NOT NULL,
            sayfa_metni_zlib BLOB,
            ocr_durumu INTEGER DEFAULT 0,
            guncellenme_tarihi TEXT,
            PRIMARY KEY (gazete_sayisi, sayfa_no)
        );

        CREATE TABLE IF NOT EXISTS sirket_bilgi_sayfa_indeksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mersis_no TEXT,
            vkn TEXT,
            ticaret_sicil_no TEXT,
            tsm_adi TEXT,
            unvan TEXT,
            ilan_turu TEXT,
            bilgi_ozeti TEXT,
            tam_adres TEXT,
            il TEXT,
            ilce TEXT,
            sermaye_tl REAL,
            yetkililer_json TEXT,
            gazete_sayisi TEXT NOT NULL,
            sayfa_no TEXT NOT NULL,
            yayin_tarihi TEXT,
            archive_url TEXT NOT NULL,
            pdf_member TEXT NOT NULL,
            kaynak_tipi TEXT DEFAULT 'INDEX+OCR',
            guncellenme_tarihi TEXT,
            UNIQUE(gazete_sayisi, sayfa_no, ticaret_sicil_no, mersis_no, unvan)
        );

        CREATE TABLE IF NOT EXISTS gazete_kisi_ve_isim_indeksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kisi_adi_soyadi TEXT NOT NULL,
            tckn_veya_kimlik TEXT DEFAULT '',
            gorevi_veya_rolu TEXT DEFAULT '',
            ikamet_adresi TEXT,
            pay_bilgisi TEXT,
            bagli_unvan TEXT,
            mersis_no TEXT,
            vkn TEXT,
            ticaret_sicil_no TEXT,
            tsm_adi TEXT,
            gazete_sayisi TEXT NOT NULL,
            sayfa_no TEXT NOT NULL,
            yayin_tarihi TEXT,
            archive_url TEXT NOT NULL,
            pdf_member TEXT NOT NULL,
            cumle_baglami TEXT,
            guncellenme_tarihi TEXT,
            UNIQUE(gazete_sayisi, sayfa_no, kisi_adi_soyadi, tckn_veya_kimlik, gorevi_veya_rolu)
        );

        CREATE INDEX IF NOT EXISTS idx_sbsi_vkn ON sirket_bilgi_sayfa_indeksi(vkn);
        CREATE INDEX IF NOT EXISTS idx_sbsi_mersis ON sirket_bilgi_sayfa_indeksi(mersis_no);
        CREATE INDEX IF NOT EXISTS idx_sbsi_sicil ON sirket_bilgi_sayfa_indeksi(ticaret_sicil_no);
        CREATE INDEX IF NOT EXISTS idx_sbsi_unvan ON sirket_bilgi_sayfa_indeksi(unvan);
        CREATE INDEX IF NOT EXISTS idx_sbsi_page ON sirket_bilgi_sayfa_indeksi(gazete_sayisi, sayfa_no);

        CREATE INDEX IF NOT EXISTS idx_gkii_isim ON gazete_kisi_ve_isim_indeksi(kisi_adi_soyadi);
        CREATE INDEX IF NOT EXISTS idx_gkii_tckn ON gazete_kisi_ve_isim_indeksi(tckn_veya_kimlik);
        CREATE INDEX IF NOT EXISTS idx_gkii_unvan ON gazete_kisi_ve_isim_indeksi(bagli_unvan);
        CREATE INDEX IF NOT EXISTS idx_gkii_vkn ON gazete_kisi_ve_isim_indeksi(vkn);
        CREATE INDEX IF NOT EXISTS idx_gkii_page ON gazete_kisi_ve_isim_indeksi(gazete_sayisi, sayfa_no);
    """)
    conn.commit()


def clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _is_valid_person_name(candidate: str) -> bool:
    cand = clean_line(candidate).strip(" ,;.:-/'\"()[]{}")
    if len(cand) < 5 or len(cand) > 65:
        return False
    if any(ch.isdigit() for ch in cand):
        return False
    words = [w for w in cand.split() if len(w) >= 2]
    if len(words) < 2 or len(words) > 5:
        return False
    up = cand.upper()
    for stop in STOPWORD_PHRASES:
        if stop in up:
            return False
    bad_tokens = {
        "ŞİRKETİ", "SIRKETI", "ANONİM", "LİMİTED", "KOOPERATİFİ", "MÜDÜRLÜĞÜ", "MEMURLUĞU",
        "GAZETESİ", "CADDESİ", "SOKAĞI", "MAHALLESİ", "BULVARI", "APARTMANI", "MERKEZİ",
        "SANAYİ", "TİCARET", "İNŞAAT", "TURİZM", "GIDA", "TEKSTİL", "OTOMOTİV", "LOJİSTİK",
        "HİZMETLERİ", "ÜRÜNLERİ", "PAZARLAMA", "İTHALAT", "İHRACAT", "DANIŞMANLIK", "YATIRIM",
        "HOLDİNG", "BANKASI", "ŞUBESİ", "NOTERLİĞİ", "MAHKEMESİ", "DAİRESİ", "BAKANLIĞI",
        "TARİH", "SAYILI", "KARARI", "GEREĞİNCE", "YUKARIDA", "AŞAĞIDA", "TESCİL", "İLAN",
    }
    if any(w.upper().strip(".,;:") in bad_tokens for w in words):
        return False
    return True


def extract_all_people_and_names(block_text: str) -> list:
    """
    Bir ilan bloğu içindeki TÜM kişi isimlerini, T.C. Kimlik numaralarını (maskeli veya açık),
    ikamet adreslerini, görev/rollerini, pay/sermaye bilgilerini ve geçtiği cümle bağlamını çıkarır.
    Hiçbir ismin boşa gitmemesi için 5 farklı dilbilgisel ve yapısal katman kullanır.
    """
    results = []
    seen = set()

    def add_person(name: str, tckn: str = "", role: str = "", addr: str = "", share: str = "", ctx: str = ""):
        clean_nm = clean_line(name).strip(" ,;.:-/'\"()")
        clean_nm = re.sub(
            r"^(?:.*?\b(?:Kimlik\s*(?:Numaral[ıi]|No['’`]?lu)|Uyruklu)\s*,?\s*)+",
            "",
            clean_nm,
            flags=re.I,
        ).strip(" ,;.:-/'\"()")
        if "SERMAYE KARŞILIĞI" in clean_nm.upper() or not _is_valid_person_name(clean_nm):
            return
        norm_nm = clean_nm.upper()
        norm_tckn = clean_line(tckn)
        norm_role = clean_line(role)[:120]
        key = (norm_nm, norm_tckn, norm_role)
        if key in seen:
            return
        seen.add(key)
        results.append({
            "kisi_adi_soyadi": norm_nm,
            "tckn_veya_kimlik": norm_tckn,
            "gorevi_veya_rolu": norm_role or "İLANDA GEÇEN KİŞİ / YETKİLİ / ORTAK",
            "ikamet_adresi": clean_line(addr)[:280] if addr else None,
            "pay_bilgisi": clean_line(share)[:160] if share else None,
            "cumle_baglami": clean_line(ctx)[:450] if ctx else None,
        })

    # Katman 1: Maskeli veya 11 Haneli TCKN + İkamet + İsim + Görev
    # Örn: "123*****456 Kimlik Numaralı, İSTANBUL / KADIKÖY adresinde ikamet eden, AHMET YILMAZ; ..."
    pat_tckn = re.compile(
        r"(\d{3}\*{3,7}\d{2,3}|\b\d{11}\b)\s*(?:Kimlik\s*Numaral[ıi]|T\.?\s*C\.?\s*Kimlik\s*(?:No|Numaras[ıi])(?:lu)?)\s*,?\s*"
        r"(?:([^,\n;]{3,160}?)\s+adresinde\s+(?:ikamet\s+eden|mukim)\s*,?\s*)?"
        r"([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜa-zçğıöşü\.\-\s]{3,60}?)"
        r"(?=\s*[;,\n]|\s+(?:münferiden|müştereken|şirket|müdür|yönetim|temsile|seçil|atan|tarihine|pay|olarak|yetki|görev|üye|başkan|isimli|adlı))",
        re.I
    )
    for m in pat_tckn.finditer(block_text):
        tckn, addr, name = m.group(1), m.group(2) or "", m.group(3)
        start = max(0, m.start() - 30)
        end = min(len(block_text), m.end() + 140)
        ctx = block_text[start:end]
        role = "YETKİLİ / ORTAK"
        low_ctx = ctx.lower()
        if "yönetim kurulu başkan" in low_ctx:
            role = "YÖNETİM KURULU BAŞKANI"
        elif "yönetim kurulu" in low_ctx:
            role = "YÖNETİM KURULU ÜYESİ"
        elif "müdür" in low_ctx:
            role = "ŞİRKET MÜDÜRÜ"
        elif "tasfiye" in low_ctx:
            role = "TASFİYE MEMURU"
        elif "denetçi" in low_ctx or "denetci" in low_ctx:
            role = "DENETÇİ"
        elif "pay" in low_ctx or "sermaye" in low_ctx or "ortak" in low_ctx:
            role = "PAY SAHİBİ / ORTAK"
        elif "temsil" in low_ctx or "imza" in low_ctx:
            role = "TEMSİLE YETKİLİ"
        add_person(name, tckn=tckn, role=role, addr=addr, ctx=ctx)

    # Katman 2: "... adresinde ikamet eden, AD SOYAD" (Özellikle 2000-2019 arası TCKN maskesiz ilanlar)
    pat_addr_person = re.compile(
        r"([^\n;]{5,140}?)\s+adresinde\s+(?:ikamet\s+eden|mukim)\s*,?\s*"
        r"([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜa-zçğıöşü\.\-\s]{3,60}?)"
        r"(?=\s*[;,\n]|\s+(?:münferiden|müştereken|şirket|müdür|yönetim|temsile|seçil|atan|tarihine|pay|olarak|yetki|görev|üye|başkan|'in|'nin|'un|'ün|'a|'e))",
        re.I
    )
    for m in pat_addr_person.finditer(block_text):
        addr, name = m.group(1), m.group(2)
        start = max(0, m.start() - 20)
        end = min(len(block_text), m.end() + 120)
        ctx = block_text[start:end]
        tckn_m = re.search(r"(\d{3}\*{3,7}\d{2,3}|\b\d{11}\b)", ctx)
        add_person(name, tckn=tckn_m.group(1) if tckn_m else "", role="İKAMET KAYITLI YETKİLİ / ORTAK", addr=addr, ctx=ctx)

    # Katman 3: Etiketli Kişi / Görev Satırları (Kurucu, Müdür, Yönetim Kurulu, Tasfiye Memuru, İşletme Sahibi, Sicil Müdürü)
    pat_labeled = re.compile(
        r"(?:Ad[ıi]\s*(?:ve\s*)?Soyad[ıi]|Şirket\s*Müdürü|Müdür(?:ler\s*Kurulu\s*Başkan[ıi])?|"
        r"Yönetim\s*Kurulu\s*(?:Başkan[ıi]|Başkan\s*Vekili|Üyesi|Üyeleri)|Tasfiye\s*Memuru|"
        r"Temsile\s*Yetkili(?:ler)?|Kurucu(?:lar)?|Ortak(?:lar)?|Pay\s*Sahibi|İşletme\s*Sahibi|"
        r"Tacirin\s*Ad[ıi]\s*Soyad[ıi]|Denetçi(?:ler)?|Ticaret\s*Sicili\s*Müdürü(?:\s*Yardımcıs[ıi])?)"
        r"\s*[:;\-]\s*([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜa-zçğıöşü\.\-\s]{3,60})",
        re.I
    )
    for m in pat_labeled.finditer(block_text):
        full_match = m.group(0)
        label = full_match.split(":")[0].split(";")[0].split("-")[0].strip().upper()
        name_cand = re.split(r"[,;(\n]", m.group(1))[0].strip()
        start = max(0, m.start() - 20)
        end = min(len(block_text), m.end() + 100)
        ctx = block_text[start:end]
        add_person(name_cand, role=label, ctx=ctx)

    # Katman 4: Sermaye ve Pay Dağılımı Satırlarında Geçen Ortak İsimleri
    # Örn: "AHMET YILMAZ : Beheri 100,00 Türk Lirası değerinde 500 adet paya karşılık gelen 50.000,00 Türk Lirası"
    pat_share = re.compile(
        r"([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜa-zçğıöşü\s]{4,55}?)\s*[:;\-]?\s*"
        r"((?:Beheri\s+)?\d+[\.,\d]*\s*(?:TL|Türk\s*Liras[ıi]|adet\s*pay)[^\n;]{0,120})",
        re.I
    )
    for m in pat_share.finditer(block_text):
        name_cand = m.group(1).strip()
        share_info = m.group(2).strip()
        start = max(0, m.start() - 10)
        end = min(len(block_text), m.end() + 40)
        add_person(name_cand, role="ORTAK / PAY SAHİBİ", share=share_info, ctx=block_text[start:end])

    # Katman 5: Hisse Devri / Atama / İstifa / Yetki Cümlelerinde Geçen Büyük Harfli Kişi İsimleri
    pat_action = re.compile(
        r"\b([A-ZÇĞİÖŞÜ]{2,22}(?:\s+[A-ZÇĞİÖŞÜ]{2,22}){1,3})\s*"
        r"(?:['’`]\s*(?:in|ın|un|ün|nin|nın|nun|nün|a|e|ya|ye|dan|den|tan|ten)\b|"
        r"\s+(?:münferiden|müştereken|şirket\s+müdürü|müdürlüğe|yönetim\s+kurulu|temsile\s+yetkili|"
        r"seçilmesine|atanmasına|istifas[ıi]|görevden|devretmiştir|devralmıştır|pay\s+devri))"
    )
    for m in pat_action.finditer(block_text):
        name_cand = m.group(1).strip()
        start = max(0, m.start() - 40)
        end = min(len(block_text), m.end() + 100)
        ctx = block_text[start:end]
        add_person(name_cand, role="İLANDA GEÇEN KİŞİ / YETKİLİ", ctx=ctx)

    return results


def parse_gazete_full_page_text(ocr_text: str, default_sayi: str = "", default_sayfa: str = "") -> dict:
    """
    Bir Ticaret Sicil Gazetesi sayfasının OCR metninden:
      - Yayım tarihi (varsa üst bilgiden: '15 ARALIK 2014 SAYI : 8715')
      - Sayfadaki TÜM ilan bloklarını (başlıksız üst sayfa devam bloğu, Ticaret Sicili Müdürlüğü,
        Ticaret Sicili Memurluğu ve aynı müdürlük altındaki çoklu 'İlan Sıra No' blokları dahil)
        KESİNTİSİZ TAM METNİYLE (`bilgi_ozeti` = tam blok metni) ve
      - Sayfadaki TÜM şahıs isimlerini (`people` listesi)
    hiçbir veriyi dışarıda bırakmadan çıkarır.
    """
    lines = [clean_line(ln) for ln in (ocr_text or "").splitlines() if clean_line(ln)]
    header_date = None
    months = {
        "OCAK": "01", "ŞUBAT": "02", "SUBAT": "02", "MART": "03", "NİSAN": "04", "NISAN": "04",
        "MAYIS": "05", "HAZİRAN": "06", "HAZIRAN": "06", "TEMMUZ": "07", "AĞUSTOS": "08", "AGUSTOS": "08",
        "EYLÜL": "09", "EYLUL": "09", "EKİM": "10", "EKIM": "10", "KASIM": "11", "ARALIK": "12",
    }
    for ln in lines[:15]:
        m_dt = re.search(r"\b(\d{1,2})\s+([A-ZÇĞİÖŞÜ]+)\s+(19\d{2}|20\d{2})\b", ln.upper())
        if m_dt and m_dt.group(2) in months:
            header_date = f"{m_dt.group(3)}-{months[m_dt.group(2)]}-{int(m_dt.group(1)):02d}"
            break

    blocks = []
    current_lines = []
    current_tsm = None

    for ln in lines:
        up = ln.upper()
        is_tsm_header = (
            "TİCARET SİCİLİ MÜDÜRLÜĞÜ" in up
            or "TICARET SICILI MUDURLUGU" in up
            or "TİCARET SİCİLİ MEMURLUĞU" in up
            or "TICARET SICILI MEMURLUGU" in up
            or ("MÜDÜRLÜĞÜ" in up and "SİCİL" in up)
            or ("MEMURLUĞU" in up and "SİCİL" in up)
        )
        # Aynı TSM başlığı altında birden fazla ilan varsa "İlan Sıra No:" ile başlar
        is_new_sub_announcement = (
            not is_tsm_header
            and len(current_lines) >= 6
            and bool(re.match(r"^(?:İlan\s*Sıra\s*No|Ilan\s*Sira\s*No)\s*[:;.\-]", ln, re.I))
        )

        if is_tsm_header:
            if current_lines:
                blocks.append((current_tsm, current_lines))
                current_lines = []
            current_tsm = re.sub(r"['’`]?(?:NDEN|NDAN|DEN|DAN).*$", "", up).strip()
        elif is_new_sub_announcement:
            blocks.append((current_tsm, current_lines))
            current_lines = []

        current_lines.append(ln)

    if current_lines:
        blocks.append((current_tsm, current_lines))

    extracted = []
    all_page_people = []

    for block_idx, (tsm_name, blines) in enumerate(blocks, start=1):
        block_text = "\n".join(blines).strip()
        if not block_text:
            continue

        # MERSİS No (16 hane)
        mersis = None
        m_mersis = re.search(r"(?:mers[iİı]s\s*no|mersis\s*numaras[ıi]|mersis)\s*[:;.\-]?\s*(\d{16})", block_text, re.I)
        if not m_mersis:
            m_mersis = re.search(r"\b(0\d{11}000\d)\b", block_text)
        if m_mersis:
            mersis = m_mersis.group(1)

        vkn = mersis[1:11] if (mersis and len(mersis) == 16 and mersis.startswith("0")) else None
        if not vkn:
            m_vkn = re.search(r"(?:vergi\s*(?:kimlik\s*|dairesi\s*[/ve]*\s*)?(?:no|numaras[ıi])|vkn)\s*[:;.\-]?\s*(\d{10})\b", block_text, re.I)
            if m_vkn:
                vkn = m_vkn.group(1)

        # Ticaret Sicil No / Dosya No / İlan Sıra No
        sicil = None
        m_sicil = re.search(
            r"(?:ticaret\s*sicil[/\s]*dosya\s*no|ticaret\s*sicili\s*no|sicil\s*numaras[ıi]|sicil\s*no|dosya\s*no)\s*[:;.\-©]*\s*([0-9A-Za-z\-/]{2,25})",
            block_text, re.I
        )
        if m_sicil and any(c.isdigit() for c in m_sicil.group(1)):
            sicil = m_sicil.group(1).strip(" .,-:")

        if not sicil:
            m_sira = re.search(r"(?:ilan\s*s[ıi]ra\s*no)\s*[:;.\-]*\s*(\d{1,10})", block_text, re.I)
            if m_sira:
                sicil = f"SIRA_{m_sira.group(1)}"

        # Ticaret Unvanı
        unvan = None
        for idx, bln in enumerate(blines):
            if re.search(r"(?:ticaret\s*unvan[ıi]|unvan[ıi]|şirketin\s*unvan[ıi])\s*:", bln, re.I):
                parts = [re.sub(r"^.*?(?:ticaret\s*unvan[ıi]|unvan[ıi]|şirketin\s*unvan[ıi])\s*:\s*", "", bln, flags=re.I).strip()]
                for nxt in blines[idx + 1: idx + 5]:
                    if any(k in nxt.lower() for k in ("adres", "mersis", "sicil", "yukarıda", "kuruluş", "sermaye", "ilan sıra", "görev")):
                        break
                    parts.append(nxt)
                    if any(k in nxt.upper() for k in ("ŞİRKETİ", "SIRKETI", "KOOPERATİFİ", "İŞLETMESİ", "ŞUBESİ", "ORTAKLIĞI")):
                        break
                cand = clean_line(" ".join(p for p in parts if p))
                if len(cand) >= 3:
                    unvan = cand[:300]
                    break
        if not unvan:
            for idx, bln in enumerate(blines[:25]):
                if any(k in bln.upper() for k in (
                    "ANONİM ŞİRKETİ", "LİMİTED ŞİRKETİ", "KOOPERATİFİ", "KOLLEKTİF ŞİRKETİ",
                    "KOMANDİT ŞİRKETİ", "TİCARİ İŞLETMESİ", "VAKFI", "DERNEĞİ", "ŞUBESİ"
                )):
                    prev = blines[idx - 1] if idx > 0 and not any(x in blines[idx - 1].upper() for x in ("SAYFA", "GAZETESİ", "MÜDÜRLÜĞÜ", "MEMURLUĞU", "NO:")) else ""
                    unvan = clean_line(f"{prev} {bln}").strip()[:300]
                    break

        # Adres
        tam_adres = None
        ilce, il = None, None
        m_addr = re.search(
            r"(?:adres[iİ]?|i̇ş\s*adresi|is\s*adresi|merkezi|şirketin\s*merkezi)\s*:\s*([^\n]+(?:\n[^\n]+){0,3})",
            block_text, re.I
        )
        if m_addr:
            raw_addr_lines = m_addr.group(1).splitlines()
            kept = []
            for aln in raw_addr_lines:
                if any(k in aln.lower() for k in ("yukarıda", "mersis", "ticaret sicil", "tescil", "ilan sıra")):
                    break
                kept.append(aln)
                if re.search(r"[A-ZÇĞİÖŞÜa-zçğıöşü]{2,20}\s*/\s*[A-ZÇĞİÖŞÜa-zçğıöşü]{3,20}\s*\.?$", aln):
                    break
            cand_addr = clean_line(" ".join(kept)).strip(" -;.")
            if len(cand_addr) >= 8:
                tam_adres = cand_addr[:400]
                m_loc = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü]{2,25})\s*/\s*([A-ZÇĞİÖŞÜa-zçğıöşü]{3,20})\s*\.?$", tam_adres)
                if m_loc:
                    ilce, il = m_loc.group(1).strip(), m_loc.group(2).strip().upper()

        # Sermaye
        sermaye = None
        m_ser = re.search(r"([0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{2})?)\s*(?:TL|Türk\s*Lirası|YTL)", block_text, re.I)
        if m_ser:
            try:
                val = float(m_ser.group(1).replace(".", "").replace(",", "."))
                if val >= 100:
                    sermaye = val
            except Exception:
                pass

        # İlan Türü Sınıflandırma
        low_b = block_text.lower()
        kinds = []
        if "kuruluş" in low_b or "yeni kayıt" in low_b or "anasözleşme" in low_b or "esas sözleşme" in low_b:
            kinds.append("KURULUŞ")
        if "sermaye" in low_b and ("artırım" in low_b or "artırılması" in low_b or "azaltılması" in low_b or "tadil" in low_b):
            kinds.append("SERMAYE DEĞİŞİKLİĞİ")
        if "pay devri" in low_b or "hisse devri" in low_b or "paylarının" in low_b:
            kinds.append("PAY / HİSSE DEVRİ")
        if "adres" in low_b and ("nakli" in low_b or "değişikliği" in low_b or "taşınması" in low_b):
            kinds.append("ADRES DEĞİŞİKLİĞİ")
        if "temsil" in low_b or "müdür" in low_b or "yönetim kurulu" in low_b or "yetkili" in low_b:
            kinds.append("YÖNETİM / TEMSİL")
        if "tasfiye" in low_b or "terkin" in low_b or "infisah" in low_b or "kapanış" in low_b:
            kinds.append("TASFİYE / TERKİN")
        if "genel kurul" in low_b:
            kinds.append("GENEL KURUL")
        if "birleşme" in low_b or "bölünme" in low_b or "tür değiştirme" in low_b:
            kinds.append("BİRLEŞME / BÖLÜNME / TÜR DEĞİŞİKLİĞİ")
        if "konkordato" in low_b or "iflas" in low_b:
            kinds.append("KONKORDATO / İFLAS")
        if "denetçi" in low_b or "bağımsız denetim" in low_b:
            kinds.append("DENETÇİ SEÇİMİ")
        ilan_turu = " | ".join(kinds) if kinds else "TESCİL İLANI"

        # Blok içindeki TÜM kişi isimlerini, TCKN'leri, görev ve pay bilgilerini çıkar
        people = extract_all_people_and_names(block_text)

        # Eğer blokta ne unvan ne sicil varsa लेकिन şahıs işletmesi veya önceki sayfadan devam eden bloksa
        # hiçbir bilginin boşa gitmemesi için blok numarasıyla kaydet:
        effective_unvan = unvan or (people[0]["kisi_adi_soyadi"] if people else f"[SAYFA DEVAM / İLAN BLOĞU #{block_idx}]")
        effective_sicil = sicil or f"BLOK_{block_idx}"

        for p in people:
            p["bagli_unvan"] = effective_unvan
            p["mersis_no"] = mersis
            p["vkn"] = vkn
            p["ticaret_sicil_no"] = effective_sicil
            p["tsm_adi"] = tsm_name
            p["yayin_tarihi"] = header_date
            all_page_people.append(p)

        # DİKKAT: bilgi_ozeti alanında kısaltma yapılmaz, bloğun TAM METNİ kaydedilir!
        extracted.append({
            "mersis_no": mersis,
            "vkn": vkn,
            "ticaret_sicil_no": effective_sicil,
            "tsm_adi": tsm_name,
            "unvan": effective_unvan,
            "ilan_turu": ilan_turu,
            "bilgi_ozeti": block_text,
            "tam_adres": tam_adres,
            "il": il,
            "ilce": ilce,
            "sermaye_tl": sermaye,
            "yetkililer_json": json.dumps(
                [
                    {
                        "kisi": p["kisi_adi_soyadi"],
                        "tckn": p["tckn_veya_kimlik"],
                        "rol": p["gorevi_veya_rolu"],
                        "adres": p["ikamet_adresi"],
                        "pay": p["pay_bilgisi"],
                    }
                    for p in people
                ],
                ensure_ascii=False,
            ) if people else None,
            "yayin_tarihi": header_date,
        })

    return {"yayin_tarihi": header_date, "records": extracted, "people": all_page_people}


def ocr_pdf_bytes_fast(pdf_bytes: bytes, tessdata_dir: str = None) -> str:
    """
    PDF sayfasında dijital metin katmanı varsa anında okur; yoksa 125 DPI Grayscale PNG
    üretip Tesseract (`OMP_THREAD_LIMIT=1`) ile bellek üzerinden (stdin/stdout) disk G/Ç
    yapmadan yüksek hızda Türkçe OCR uygular.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if len(doc) == 0:
        doc.close()
        return ""
    page = doc[0]
    digital_text = page.get_text("text").strip()
    if len(digital_text) >= 120:
        doc.close()
        return digital_text

    pix = page.get_pixmap(dpi=125, colorspace=pymupdf.csGRAY)
    png_bytes = pix.tobytes("png")
    doc.close()

    cmd = [TESSERACT_BIN, "stdin", "stdout", "-l", "tur", "--psm", "3"]
    if tessdata_dir and os.path.isdir(tessdata_dir):
        cmd.extend(["--tessdata-dir", tessdata_dir])

    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    out = subprocess.check_output(cmd, input=png_bytes, stderr=subprocess.DEVNULL, timeout=35, env=env)
    return out.decode("utf-8", errors="ignore")


def populate_page_index_from_existing_catalog(db_path: Path, pages_jsonl_gz: Path) -> dict:
    """
    `tobb_training_pages.jsonl.gz` içindeki 503.317+ tekil PDF sayfasını
    `gazete_sayfa_arsivi` ve `sirket_bilgi_sayfa_indeksi` tablolarına anında bağlar.
    Böylece OCR tamamlanmadan önce bile mevcut 2.21 milyon ilan ve şirket bilgisi
    doğrudan PDF sayfası (`archive_url` + `pdf_member`) ile sorgulanabilir hale gelir.
    """
    conn = sqlite3.connect(str(db_path))
    ensure_page_linked_schema(conn)
    now_iso = datetime.now(timezone.utc).isoformat()

    page_rows = []
    with gzip.open(str(pages_jsonl_gz), "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            sayi = str(row.get("gazete_sayisi") or "").strip()
            sayfa = str(row.get("sayfa_no") or "").strip()
            arch = str(row.get("archive") or "").strip()
            member = str(row.get("member") or "").strip()
            if sayi and sayfa and arch and member:
                parts = member.rsplit(".", 1)[0].split("_")
                tsm_hint = "_".join(parts[2:-1]) if len(parts) >= 4 else None
                page_rows.append((sayi, sayfa, tsm_hint, arch, member, now_iso))

    conn.executemany("""
        INSERT INTO gazete_sayfa_arsivi (gazete_sayisi, sayfa_no, tsm_adi, archive_url, pdf_member, guncellenme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(gazete_sayisi, sayfa_no) DO UPDATE SET
            archive_url = excluded.archive_url,
            pdf_member = excluded.pdf_member,
            tsm_adi = COALESCE(gazete_sayfa_arsivi.tsm_adi, excluded.tsm_adi)
    """, page_rows)
    conn.commit()

    conn.execute("""
        INSERT OR IGNORE INTO sirket_bilgi_sayfa_indeksi (
            mersis_no, vkn, ticaret_sicil_no, tsm_adi, unvan, ilan_turu,
            bilgi_ozeti, tam_adres, il, ilce, sermaye_tl,
            gazete_sayisi, sayfa_no, yayin_tarihi, archive_url, pdf_member,
            kaynak_tipi, guncellenme_tarihi
        )
        SELECT
            s.mersis_no,
            COALESCE(s.vkn, CASE WHEN s.mersis_no GLOB '0[0-9]*' AND LENGTH(s.mersis_no)=16 THEN SUBSTR(s.mersis_no, 2, 10) END),
            i.ticaret_sicil_no,
            i.tsm_adi,
            i.unvan,
            i.ilan_turu,
            i.ilan_metni,
            s.tam_adres,
            s.il,
            s.ilce,
            s.sermaye_tl,
            i.gazete_sayisi,
            i.sayfa_no,
            i.ilan_tarihi,
            p.archive_url,
            p.pdf_member,
            'MASTER_INDEX',
            ?
        FROM ticaret_sicil_gazetesi_ilanlari i
        INNER JOIN gazete_sayfa_arsivi p
            ON p.gazete_sayisi = i.gazete_sayisi AND p.sayfa_no = i.sayfa_no
        LEFT JOIN sirketler s
            ON s.ticaret_sicil_no = i.ticaret_sicil_no AND s.tsm_adi = i.tsm_adi
    """, (now_iso,))
    conn.commit()

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    total_pages = conn.execute("SELECT COUNT(*) FROM gazete_sayfa_arsivi").fetchone()[0]
    total_linked = conn.execute("SELECT COUNT(*) FROM sirket_bilgi_sayfa_indeksi").fetchone()[0]
    conn.close()
    return {"archived_pages": total_pages, "page_linked_company_events": total_linked}


def run_ocr_shard(
    shard_id: int,
    num_shards: int,
    pages_jsonl_gz: Path,
    processed_archives_file: Path,
    output_sqlite: Path,
    max_seconds: int = 18000,
    workers: int = 4,
    tessdata_dir: str = None,
) -> dict:
    """
    40 GitHub Actions makinesinden `shard_id` numaralı makinenin payına düşen `.tar.gz`
    paketlerini indirir, içindeki PDF sayfalarına paralel Türkçe OCR uygular, sıkıştırılmış
    sayfa metinlerini, tüm ilan bloklarını ve tüm şahıs/yetkili/ortak isimlerini `output_sqlite`
    dosyasına eksiksiz yazar.
    """
    start_ts = time.monotonic()
    deadline = start_ts + max_seconds

    processed_archives = set()
    if processed_archives_file and processed_archives_file.exists():
        for ln in processed_archives_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.strip():
                processed_archives.add(ln.strip())

    shard_archives = {}
    with gzip.open(str(pages_jsonl_gz), "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            arch = str(row.get("archive") or "").strip()
            if not arch or arch in processed_archives:
                continue
            h = int(hashlib.md5(arch.encode("utf-8")).hexdigest()[:8], 16)
            if (h % num_shards) != (shard_id - 1):
                continue
            member = str(row.get("member") or "").strip()
            sayi = str(row.get("gazete_sayisi") or "").strip()
            sayfa = str(row.get("sayfa_no") or "").strip()
            shard_archives.setdefault(arch, []).append((member, sayi, sayfa))

    conn = sqlite3.connect(str(output_sqlite))
    ensure_page_linked_schema(conn)

    pages_ocr_done = 0
    records_extracted = 0
    people_extracted = 0
    archives_done = 0

    print(f"🚀 [OCR Shard {shard_id}/{num_shards}] İşlenecek arşiv paketi: {len(shard_archives):,}", flush=True)

    def process_single_pdf(item):
        member_name, sayi, sayfa, arch_url, pdf_bytes = item
        txt = ocr_pdf_bytes_fast(pdf_bytes, tessdata_dir=tessdata_dir)
        compressed = zlib.compress(txt.encode("utf-8"), level=9) if txt else None
        parsed = parse_gazete_full_page_text(txt, default_sayi=sayi, default_sayfa=sayfa)
        parts = member_name.rsplit(".", 1)[0].split("_")
        tsm_hint = "_".join(parts[2:-1]) if len(parts) >= 4 else None
        return (
            sayi,
            sayfa,
            parsed.get("yayin_tarihi"),
            tsm_hint,
            arch_url,
            member_name,
            compressed,
            parsed.get("records", []),
            parsed.get("people", []),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for arch_url, expected_members in shard_archives.items():
            if time.monotonic() >= deadline:
                break
            try:
                req = urllib.request.Request(arch_url, headers={"User-Agent": "GEOPROP-OCR-Engine/1.0"})
                with urllib.request.urlopen(req, timeout=45) as resp:
                    tar_bytes = resp.read()
            except Exception as exc:
                print(f"⚠️ Arşiv indirilemedi ({arch_url}): {exc}", flush=True)
                continue

            pdf_tasks = []
            member_map = {m: (s, p) for m, s, p in expected_members}
            try:
                with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
                    for m in tar.getmembers():
                        if not m.isfile() or not m.name.endswith(".pdf"):
                            continue
                        base_name = os.path.basename(m.name)
                        if base_name in member_map:
                            sayi, sayfa = member_map[base_name]
                        else:
                            ps = base_name.split("_")
                            if len(ps) >= 2 and ps[0].isdigit() and ps[1].isdigit():
                                sayi, sayfa = ps[0], ps[1]
                            else:
                                continue
                        fobj = tar.extractfile(m)
                        if fobj:
                            pdf_tasks.append((base_name, sayi, sayfa, arch_url, fobj.read()))
            except Exception as exc:
                print(f"⚠️ Tar okuma hatası ({arch_url}): {exc}", flush=True)
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            futures = [pool.submit(process_single_pdf, task) for task in pdf_tasks]
            for fut in as_completed(futures):
                try:
                    sayi, sayfa, y_tarih, tsm_hint, a_url, m_name, comp_blob, recs, ppl = fut.result()
                except Exception:
                    continue

                conn.execute("""
                    INSERT INTO gazete_sayfa_arsivi (
                        gazete_sayisi, sayfa_no, yayin_tarihi, tsm_adi,
                        archive_url, pdf_member, sayfa_metni_zlib, ocr_durumu, guncellenme_tarihi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(gazete_sayisi, sayfa_no) DO UPDATE SET
                        yayin_tarihi = COALESCE(excluded.yayin_tarihi, gazete_sayfa_arsivi.yayin_tarihi),
                        tsm_adi = COALESCE(excluded.tsm_adi, gazete_sayfa_arsivi.tsm_adi),
                        archive_url = excluded.archive_url,
                        pdf_member = excluded.pdf_member,
                        sayfa_metni_zlib = excluded.sayfa_metni_zlib,
                        ocr_durumu = 1,
                        guncellenme_tarihi = excluded.guncellenme_tarihi
                """, (sayi, sayfa, y_tarih, tsm_hint, a_url, m_name, comp_blob, now_iso))
                pages_ocr_done += 1

                for r in recs:
                    conn.execute("""
                        INSERT OR REPLACE INTO sirket_bilgi_sayfa_indeksi (
                            mersis_no, vkn, ticaret_sicil_no, tsm_adi, unvan, ilan_turu,
                            bilgi_ozeti, tam_adres, il, ilce, sermaye_tl, yetkililer_json,
                            gazete_sayisi, sayfa_no, yayin_tarihi, archive_url, pdf_member,
                            kaynak_tipi, guncellenme_tarihi
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OCR_FULLTEXT', ?)
                    """, (
                        r["mersis_no"], r["vkn"], r["ticaret_sicil_no"], r["tsm_adi"] or tsm_hint,
                        r["unvan"], r["ilan_turu"], r["bilgi_ozeti"], r["tam_adres"],
                        r["il"], r["ilce"], r["sermaye_tl"], r["yetkililer_json"],
                        sayi, sayfa, r["yayin_tarihi"] or y_tarih, a_url, m_name, now_iso
                    ))
                    records_extracted += 1

                for p in ppl:
                    conn.execute("""
                        INSERT OR REPLACE INTO gazete_kisi_ve_isim_indeksi (
                            kisi_adi_soyadi, tckn_veya_kimlik, gorevi_veya_rolu,
                            ikamet_adresi, pay_bilgisi, bagli_unvan,
                            mersis_no, vkn, ticaret_sicil_no, tsm_adi,
                            gazete_sayisi, sayfa_no, yayin_tarihi,
                            archive_url, pdf_member, cumle_baglami, guncellenme_tarihi
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p["kisi_adi_soyadi"], p["tckn_veya_kimlik"] or "", p["gorevi_veya_rolu"] or "",
                        p["ikamet_adresi"], p["pay_bilgisi"], p["bagli_unvan"],
                        p["mersis_no"], p["vkn"], p["ticaret_sicil_no"], p["tsm_adi"] or tsm_hint,
                        sayi, sayfa, p["yayin_tarihi"] or y_tarih,
                        a_url, m_name, p["cumle_baglami"], now_iso
                    ))
                    people_extracted += 1

            conn.commit()
            archives_done += 1
            if processed_archives_file:
                with open(processed_archives_file, "a", encoding="utf-8") as pf:
                    pf.write(arch_url + "\n")

            if archives_done % 5 == 0 or archives_done == 1:
                elapsed = max(1.0, time.monotonic() - start_ts)
                rate = pages_ocr_done * 3600 / elapsed
                print(
                    f"  ✅ [OCR Shard {shard_id}] Arşiv={archives_done:,} | "
                    f"Sayfa={pages_ocr_done:,} ({rate:.0f} sayfa/saat) | "
                    f"İlan Bloğu={records_extracted:,} | Kişi/İsim={people_extracted:,}",
                    flush=True
                )

    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    conn.close()
    return {
        "shard_id": shard_id,
        "archives_done": archives_done,
        "pages_ocr_done": pages_ocr_done,
        "records_extracted": records_extracted,
        "people_extracted": people_extracted,
    }


def merge_ocr_shard_databases(shards_dir: Path, master_db_path: Path) -> dict:
    """
    40 OCR shard'ından gelen SQLite veritabanlarını (`ocr_shard_*_updates.sqlite`)
    ana veritabanı (`turkiye_ticaret_sicil_ve_sirketler.sqlite`) ile birleştirir;
    ayrıca `sirketler` tablosundaki eksik VKN, MERSİS, Adres ve Sermaye alanlarını
    OCR'dan çıkan kesin verilerle günceller.
    """
    conn = sqlite3.connect(str(master_db_path))
    ensure_page_linked_schema(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sirketler (
            mersis_no TEXT PRIMARY KEY,
            ticaret_sicil_no TEXT,
            tsm_adi TEXT,
            vkn TEXT,
            unvan TEXT NOT NULL,
            sirket_turu TEXT,
            durum TEXT DEFAULT 'AKTIF',
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
    conn.commit()
    merged_pages = 0
    merged_records = 0
    merged_people = 0

    for shard_db in sorted(shards_dir.rglob("ocr_shard_*_updates.sqlite")):
        try:
            conn.execute("ATTACH DATABASE ? AS sh", (str(shard_db),))
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO gazete_sayfa_arsivi (
                    gazete_sayisi, sayfa_no, yayin_tarihi, tsm_adi,
                    archive_url, pdf_member, sayfa_metni_zlib, ocr_durumu, guncellenme_tarihi
                )
                SELECT
                    gazete_sayisi, sayfa_no, yayin_tarihi, tsm_adi,
                    archive_url, pdf_member, sayfa_metni_zlib, ocr_durumu, guncellenme_tarihi
                FROM sh.gazete_sayfa_arsivi
                WHERE ocr_durumu = 1
                ON CONFLICT(gazete_sayisi, sayfa_no) DO UPDATE SET
                    yayin_tarihi = COALESCE(excluded.yayin_tarihi, gazete_sayfa_arsivi.yayin_tarihi),
                    tsm_adi = COALESCE(excluded.tsm_adi, gazete_sayfa_arsivi.tsm_adi),
                    archive_url = excluded.archive_url,
                    pdf_member = excluded.pdf_member,
                    sayfa_metni_zlib = excluded.sayfa_metni_zlib,
                    ocr_durumu = 1,
                    guncellenme_tarihi = excluded.guncellenme_tarihi
            """)
            sh_pages = max(0, cur.rowcount)

            cur.execute("""
                INSERT OR REPLACE INTO sirket_bilgi_sayfa_indeksi (
                    mersis_no, vkn, ticaret_sicil_no, tsm_adi, unvan, ilan_turu,
                    bilgi_ozeti, tam_adres, il, ilce, sermaye_tl, yetkililer_json,
                    gazete_sayisi, sayfa_no, yayin_tarihi, archive_url, pdf_member,
                    kaynak_tipi, guncellenme_tarihi
                )
                SELECT
                    mersis_no, vkn, ticaret_sicil_no, tsm_adi, unvan, ilan_turu,
                    bilgi_ozeti, tam_adres, il, ilce, sermaye_tl, yetkililer_json,
                    gazete_sayisi, sayfa_no, yayin_tarihi, archive_url, pdf_member,
                    kaynak_tipi, guncellenme_tarihi
                FROM sh.sirket_bilgi_sayfa_indeksi
            """)
            sh_records = max(0, cur.rowcount)

            # Kişi ve isim indeksini birleştir
            has_people_table = cur.execute(
                "SELECT 1 FROM sh.sqlite_master WHERE type='table' AND name='gazete_kisi_ve_isim_indeksi'"
            ).fetchone()
            sh_people = 0
            if has_people_table:
                cur.execute("""
                    INSERT OR REPLACE INTO gazete_kisi_ve_isim_indeksi (
                        kisi_adi_soyadi, tckn_veya_kimlik, gorevi_veya_rolu,
                        ikamet_adresi, pay_bilgisi, bagli_unvan,
                        mersis_no, vkn, ticaret_sicil_no, tsm_adi,
                        gazete_sayisi, sayfa_no, yayin_tarihi,
                        archive_url, pdf_member, cumle_baglami, guncellenme_tarihi
                    )
                    SELECT
                        kisi_adi_soyadi, tckn_veya_kimlik, gorevi_veya_rolu,
                        ikamet_adresi, pay_bilgisi, bagli_unvan,
                        mersis_no, vkn, ticaret_sicil_no, tsm_adi,
                        gazete_sayisi, sayfa_no, yayin_tarihi,
                        archive_url, pdf_member, cumle_baglami, guncellenme_tarihi
                    FROM sh.gazete_kisi_ve_isim_indeksi
                """)
                sh_people = max(0, cur.rowcount)

            # Yeni keşfedilen gerçek şirketleri ve adres/VKN/sermaye bilgilerini sirketler tablosuna işle
            cur.execute("""
                INSERT INTO sirketler (
                    mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan,
                    il, ilce, tam_adres, sermaye_tl, kaynak, guncellenme_tarihi
                )
                SELECT
                    COALESCE(mersis_no, 'OCR_' || COALESCE(tsm_adi, 'TR') || '_' || COALESCE(ticaret_sicil_no, gazete_sayisi || '_' || sayfa_no)),
                    ticaret_sicil_no, tsm_adi, vkn, unvan,
                    il, ilce, tam_adres, sermaye_tl, 'TOBB_PDF_OCR', guncellenme_tarihi
                FROM sh.sirket_bilgi_sayfa_indeksi
                WHERE unvan IS NOT NULL
                  AND unvan NOT LIKE '[SAYFA DEVAM%'
                  AND (mersis_no IS NOT NULL OR (ticaret_sicil_no IS NOT NULL AND ticaret_sicil_no NOT LIKE 'BLOK_%'))
                ON CONFLICT(mersis_no) DO UPDATE SET
                    vkn = COALESCE(sirketler.vkn, excluded.vkn),
                    tam_adres = COALESCE(excluded.tam_adres, sirketler.tam_adres),
                    il = COALESCE(excluded.il, sirketler.il),
                    ilce = COALESCE(excluded.ilce, sirketler.ilce),
                    sermaye_tl = COALESCE(excluded.sermaye_tl, sirketler.sermaye_tl),
                    guncellenme_tarihi = excluded.guncellenme_tarihi
            """)
            conn.commit()
            merged_pages += sh_pages
            merged_records += sh_records
            merged_people += sh_people
            conn.execute("DETACH DATABASE sh")
            try:
                shard_db.unlink()
            except Exception:
                pass
        except Exception as exc:
            print(f"⚠️ Shard merge hatası ({shard_db}): {exc}", flush=True)
            try:
                conn.execute("DETACH DATABASE sh")
            except Exception:
                pass

    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    conn.close()
    return {
        "merged_pages": merged_pages,
        "merged_records": merged_records,
        "merged_people": merged_people,
    }


def query_company_and_pages(db_path: Path, query_str: str, extract_pdf_dir: Path = None) -> dict:
    """
    VKN (10 hane), MERSİS No (16 hane), Sicil No, Şirket Unvanı veya Kişi Adı-Soyadı ile sorgu yapar.
    Şirketin/kişinin künyesini, kronolojik tüm gazete kayıtlarını, hangi bilginin hangi sayfada
    olduğunu ve (istenirse) ilgili PDF sayfasını doğrudan `.tar.gz` arşivinden çıkararak döndürür.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_page_linked_schema(conn)
    q = query_str.strip()

    if q.isdigit() and len(q) == 10:
        where_clause = "s.vkn = ? OR s.mersis_no LIKE ?"
        params = (q, f"0{q}%")
    elif q.isdigit() and len(q) == 16:
        where_clause = "s.mersis_no = ? OR s.vkn = ?"
        params = (q, q[1:11])
    else:
        where_clause = "s.unvan LIKE ? OR s.ticaret_sicil_no = ? OR s.yetkililer_json LIKE ?"
        params = (f"%{q}%", q, f"%{q}%")

    events = [
        dict(r) for r in conn.execute(f"""
            SELECT
                s.id, s.mersis_no, s.vkn, s.ticaret_sicil_no, s.tsm_adi, s.unvan,
                s.ilan_turu, s.bilgi_ozeti, s.tam_adres, s.il, s.ilce, s.sermaye_tl,
                s.yetkililer_json, s.gazete_sayisi, s.sayfa_no, s.yayin_tarihi,
                s.archive_url, s.pdf_member, s.kaynak_tipi,
                p.ocr_durumu, p.sayfa_metni_zlib
            FROM sirket_bilgi_sayfa_indeksi s
            LEFT JOIN gazete_sayfa_arsivi p
                ON p.gazete_sayisi = s.gazete_sayisi AND p.sayfa_no = s.sayfa_no
            WHERE {where_clause}
            ORDER BY COALESCE(s.yayin_tarihi, '') ASC, CAST(s.gazete_sayisi AS INT) ASC, CAST(s.sayfa_no AS INT) ASC
            LIMIT 200
        """, params).fetchall()
    ]

    # Henüz PDF'i indirilmemiş ama ana katalogda (2.21M ilan) bulunan kayıtları da sayfa numarasıyla ekle:
    seen_page_keys = {(str(e.get("gazete_sayisi")), str(e.get("sayfa_no")), str(e.get("ticaret_sicil_no"))) for e in events}
    if q.isdigit() and len(q) in (10, 16):
        cat_where = "s.vkn = ? OR s.mersis_no LIKE ?"
        cat_params = (q if len(q) == 10 else q[1:11], f"0{q}%" if len(q) == 10 else q)
    else:
        cat_where = "i.unvan LIKE ? OR i.ticaret_sicil_no = ?"
        cat_params = (f"%{q}%", q)

    catalog_rows = conn.execute(f"""
        SELECT
            s.mersis_no,
            COALESCE(s.vkn, CASE WHEN s.mersis_no GLOB '0[0-9]*' AND LENGTH(s.mersis_no)=16 THEN SUBSTR(s.mersis_no, 2, 10) END) AS vkn,
            i.ticaret_sicil_no, i.tsm_adi, i.unvan, i.ilan_turu, i.ilan_metni AS bilgi_ozeti,
            s.tam_adres, s.il, s.ilce, s.sermaye_tl,
            i.gazete_sayisi, i.sayfa_no, i.ilan_tarihi AS yayin_tarihi,
            p.archive_url, p.pdf_member, p.ocr_durumu, p.sayfa_metni_zlib
        FROM ticaret_sicil_gazetesi_ilanlari i
        LEFT JOIN sirketler s
            ON s.ticaret_sicil_no = i.ticaret_sicil_no AND s.tsm_adi = i.tsm_adi
        LEFT JOIN gazete_sayfa_arsivi p
            ON p.gazete_sayisi = i.gazete_sayisi AND p.sayfa_no = i.sayfa_no
        WHERE {cat_where}
        ORDER BY COALESCE(i.ilan_tarihi, '') ASC, CAST(i.gazete_sayisi AS INT) ASC
        LIMIT 200
    """, cat_params).fetchall()

    for r in catalog_rows:
        d = dict(r)
        key = (str(d.get("gazete_sayisi")), str(d.get("sayfa_no")), str(d.get("ticaret_sicil_no")))
        if key not in seen_page_keys:
            seen_page_keys.add(key)
            d["id"] = None
            d["yetkililer_json"] = None
            d["kaynak_tipi"] = "KATALOG_SAYFA_KAYDI"
            events.append(d)

    person_matches = [
        dict(r) for r in conn.execute("""
            SELECT
                kisi_adi_soyadi, tckn_veya_kimlik, gorevi_veya_rolu,
                ikamet_adresi, pay_bilgisi, bagli_unvan,
                mersis_no, vkn, ticaret_sicil_no, tsm_adi,
                gazete_sayisi, sayfa_no, yayin_tarihi,
                archive_url, pdf_member, cumle_baglami
            FROM gazete_kisi_ve_isim_indeksi
            WHERE kisi_adi_soyadi LIKE ? OR bagli_unvan LIKE ? OR vkn = ? OR tckn_veya_kimlik = ?
            ORDER BY COALESCE(yayin_tarihi, '') ASC, CAST(gazete_sayisi AS INT) ASC
            LIMIT 200
        """, (f"%{q}%", f"%{q}%", q, q)).fetchall()
    ]

    extracted_pdfs = []
    for ev in events:
        blob = ev.pop("sayfa_metni_zlib", None)
        if blob:
            try:
                ev["sayfa_tam_metni"] = zlib.decompress(blob).decode("utf-8", errors="ignore")
            except Exception:
                ev["sayfa_tam_metni"] = None
        else:
            ev["sayfa_tam_metni"] = None

        if extract_pdf_dir and ev.get("archive_url") and ev.get("pdf_member"):
            extract_pdf_dir.mkdir(parents=True, exist_ok=True)
            out_pdf = extract_pdf_dir / ev["pdf_member"]
            if not out_pdf.exists():
                try:
                    req = urllib.request.Request(ev["archive_url"], headers={"User-Agent": "GEOPROP-Page-Fetcher/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        tar_data = resp.read()
                    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:gz") as tar:
                        for m in tar.getmembers():
                            if os.path.basename(m.name) == ev["pdf_member"]:
                                out_pdf.write_bytes(tar.extractfile(m).read())
                                extracted_pdfs.append(str(out_pdf))
                                break
                except Exception as exc:
                    ev["pdf_indir_hata"] = str(exc)
            else:
                extracted_pdfs.append(str(out_pdf))
            if out_pdf.exists():
                ev["yerel_pdf_dosyasi"] = str(out_pdf)

    conn.close()
    return {
        "sorgu": q,
        "bulunan_sayfa_kayit_sayisi": len(events),
        "bulunan_kisi_isim_kayit_sayisi": len(person_matches),
        "indirilen_pdf_sayfalari": extracted_pdfs,
        "kisi_ve_yetkili_kayitlari": person_matches,
        "kronolojik_gecmis_ve_sayfalar": events,
    }


def main():
    parser = argparse.ArgumentParser(description="TOBB 40-Shard OCR ve Sayfa/Kişi İndeksli Şirket Veritabanı Motoru")
    parser.add_argument("--init-from-catalog", type=str, help="tobb_training_pages.jsonl.gz dosyasından sayfa indeksini kur")
    parser.add_argument("--ocr-shard", type=int, help="Çalıştırılacak OCR shard numarası (1..N)")
    parser.add_argument("--num-shards", type=int, default=40, help="Toplam OCR shard sayısı")
    parser.add_argument("--pages-index", type=str, default="tobb_training_pages.jsonl.gz")
    parser.add_argument("--processed-log", type=str, default="tobb_ocr_processed_archives.txt")
    parser.add_argument("--output-sqlite", type=str, default="ocr_shard_updates.sqlite")
    parser.add_argument("--max-seconds", type=int, default=18000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tessdata-dir", type=str, default="")
    parser.add_argument("--merge-dir", type=str, help="OCR shard SQLite dosyalarının bulunduğu klasörü Master DB ile birleştir")
    parser.add_argument("--db-path", type=str, default=str(MASTER_DB_PATH))
    parser.add_argument("--sorgu", type=str, help="VKN, MERSİS, Sicil No, Unvan veya Kişi Adı-Soyadı sorgula")
    parser.add_argument("--sayfa-indir-klasor", type=str, default="", help="Sorgulanan şirketin PDF sayfalarını bu klasöre çıkar")
    args = parser.parse_args()

    db_path = Path(args.db_path)

    if args.init_from_catalog:
        res = populate_page_index_from_existing_catalog(db_path, Path(args.init_from_catalog))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.ocr_shard is not None:
        res = run_ocr_shard(
            shard_id=args.ocr_shard,
            num_shards=args.num_shards,
            pages_jsonl_gz=Path(args.pages_index),
            processed_archives_file=Path(args.processed_log),
            output_sqlite=Path(args.output_sqlite),
            max_seconds=args.max_seconds,
            workers=args.workers,
            tessdata_dir=args.tessdata_dir or None,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.merge_dir:
        res = merge_ocr_shard_databases(Path(args.merge_dir), db_path)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.sorgu:
        out_dir = Path(args.sayfa_indir_klasor) if args.sayfa_indir_klasor else None
        res = query_company_and_pages(db_path, args.sorgu, extract_pdf_dir=out_dir)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
