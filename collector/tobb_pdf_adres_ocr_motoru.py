#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB Ticaret Sicil Gazetesi PDF OCR Adres, MERSİS, VKN ve Ortaklık Çıkarıcı Motor
===========================================================================================
Hem lokaldeki PDF sayfalarını (warehouse/raw/gazete_sayfalari/*.pdf) hem de GitHub Releases
üzerindeki 3.355+ adet .tar.gz arşiv paketini (167.750+ tekil gazete sayfası) ve yeni inen
tüm PDF sayfalarını Tesseract Türkçe OCR (140 DPI) ile okur:
  - Tam Adres (`Adres:`, `Adresi:`, `İş Adresi:`, `Merkezi:` - çok satırlı birleştirme ile)
  - İlçe ve İl (`Kadıköy/İstanbul`, `Turgutlu/Manisa` vb.)
  - 16 Haneli Resmî MERSİS No (`0xxxxxxxxxxxxxxx`) ve buradan türetilen 10 Haneli VKN
  - Ticaret Sicil / Dosya No
  - Sermaye (TL)

Çıkarılan verileri `warehouse/product/turkiye_ticaret_sicil_ve_sirketler.sqlite` içindeki
`sirketler` tablosuna anında yazar.
"""

import os
import re
import sys
import glob
import time
import shutil
import sqlite3
import tarfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
LOCAL_PDF_DIR = REPO_ROOT / "warehouse" / "raw" / "gazete_sayfalari"
TEMP_OCR_DIR = REPO_ROOT / "collector" / "data" / "ocr_work"
PROCESSED_BUNDLES_LOG = REPO_ROOT / "collector" / "data" / "processed_ocr_bundles.txt"

TESSERACT_BIN = (
    shutil.which("tesseract")
    or ("/opt/homebrew/bin/tesseract" if os.path.exists("/opt/homebrew/bin/tesseract") else "/usr/bin/tesseract")
)

STOP_ADDR_KEYWORDS = (
    "yukarıda", "yukarida", "ticaret sicili", "ilan sıra", "ilan sira",
    "mersis no", "mersi̇s no", "ticaret sicil", "yeni kayıt", "yeni kayit",
    "unvanı", "unvani", "kuruluş", "kurulus", "sermayesi", "temsil",
    "müdürlüğünden", "mudurlugunden", "tescil", "eski ticaret"
)


def clean_ocr_line(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def parse_il_ilce_from_address(addr: str):
    """
    'Caferağa Mahallesi Soner Sk. No: 49/1 Kadıköy/İstanbul' -> ('Kadıköy', 'İSTANBUL')
    """
    m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü]{2,25})\s*/\s*([A-ZÇĞİÖŞÜa-zçğıöşü]{3,20})\s*\.?$", addr.strip())
    if m:
        ilce = m.group(1).strip()
        il = m.group(2).strip().upper()
        return ilce, il
    return None, None


def parse_gazete_ocr_text(txt: str) -> list:
    """
    Bir Ticaret Sicil Gazetesi sayfasının OCR metninden birden fazla şirketin
    (mersis_no, vkn, sicil_no, tam_adres, ilce, il, sermaye_tl) kayıtlarını çıkarır.
    """
    lines = [clean_ocr_line(ln) for ln in (txt or "").splitlines()]
    records = []

    current = {
        "mersis_no": None,
        "vkn": None,
        "sicil_no": None,
        "tam_adres": None,
        "ilce": None,
        "il": None,
        "sermaye_tl": None,
    }

    def flush_current():
        nonlocal current
        if current["tam_adres"] or current["mersis_no"] or current["sicil_no"]:
            if current["mersis_no"] and len(current["mersis_no"]) == 16 and current["mersis_no"].startswith("0"):
                current["vkn"] = current["mersis_no"][1:11]
            if current["tam_adres"]:
                ilce, il = parse_il_ilce_from_address(current["tam_adres"])
                if ilce and not current["ilce"]:
                    current["ilce"] = ilce
                if il and not current["il"]:
                    current["il"] = il
            records.append(dict(current))
        current = {
            "mersis_no": None,
            "vkn": None,
            "sicil_no": None,
            "tam_adres": None,
            "ilce": None,
            "il": None,
            "sermaye_tl": None,
        }

    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        low = ln.lower()

        # Yeni duyuru başlığı ("... TİCARET SİCİLİ MÜDÜRLÜĞÜNDEN")
        if "müdürlüğünden" in low or "mudurlugunden" in low:
            if current["tam_adres"] or current["mersis_no"]:
                flush_current()

        # 1. MERSİS No (16 hane)
        m_mersis = re.search(r"(?:mers[iİı]s\s*no|mersis)\s*[:;.\-]?\s*(\d{16})", ln, flags=re.IGNORECASE)
        if not m_mersis:
            m_mersis = re.search(r"\b(0\d{11}000\d)\b", ln)
        if m_mersis:
            if current["mersis_no"] and current["mersis_no"] != m_mersis.group(1) and current["tam_adres"]:
                flush_current()
            current["mersis_no"] = m_mersis.group(1)

        # 2. Ticaret Sicil / Dosya No
        m_sicil = re.search(
            r"(?:ticaret\s*sicil[/\s]*dosya\s*no|ticaret\s*sicili\s*no|sicil\s*numaras[ıi])\s*[:;.\-©]*\s*([0-9A-Za-z\-/]{2,20})",
            ln,
            flags=re.IGNORECASE,
        )
        if m_sicil:
            cand = m_sicil.group(1).strip(" .,-:")
            if any(ch.isdigit() for ch in cand):
                current["sicil_no"] = cand
        elif ("sicil/dosya" in low or "sicildosya" in low) and i + 1 < n:
            m_next = re.match(r"^([0-9]{3,10}(?:-[0-9]+)?)\b", lines[i + 1])
            if m_next:
                current["sicil_no"] = m_next.group(1)

        # 3. Tam Adres (Çok satırlı birleştirme - No:/Kat:/Daire: içeren alt satırları kesmez)
        m_addr = re.search(
            r"\b(?:adres[iİ]?|i̇ş\s*adresi|is\s*adresi|merkezi)\s*:\s*(.*)$",
            ln,
            flags=re.IGNORECASE,
        )
        if m_addr:
            first_part = m_addr.group(1).strip()
            addr_parts = [first_part] if first_part else []
            j = i + 1
            while j < min(i + 6, n):
                nxt = lines[j]
                nxt_low = nxt.lower()
                if not nxt:
                    break
                if any(nxt_low.startswith(k) or (k in nxt_low) for k in STOP_ADDR_KEYWORDS):
                    break
                if re.match(r"^\d+\.\s+", nxt):
                    break
                # Yeni bir alan başlığı mı? (No:, Kat:, Daire:, Blok:, İç Kapı No: hariç)
                m_field = re.match(r"^([A-Za-zÇĞİÖŞÜçğıöşü\s]{3,22}):", nxt)
                if m_field:
                    lbl = m_field.group(1).strip().lower()
                    if not any(a_k in lbl for a_k in ("no", "kat", "daire", "blok", "kapı", "kapi", "pk", "posta")):
                        break
                addr_parts.append(nxt)
                # Eğer 'İlçe/İl' kalıbıyla bitiyorsa adresi tamamla
                if re.search(r"[A-ZÇĞİÖŞÜa-zçğıöşü]{2,20}\s*/\s*[A-ZÇĞİÖŞÜa-zçğıöşü]{3,20}\s*\.?$", nxt):
                    break
                j += 1
            full_addr = clean_ocr_line(" ".join(addr_parts)).strip(" -;.")
            if len(full_addr) >= 10:
                current["tam_adres"] = full_addr

        # 4. Sermaye
        if "sermaye" in low:
            m_ser = re.search(r"([0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{2})?)\s*(?:tl|türk\s*lirası)", ln, flags=re.IGNORECASE)
            if m_ser:
                try:
                    val = float(m_ser.group(1).replace(".", "").replace(",", "."))
                    if val >= 1000:
                        current["sermaye_tl"] = val
                except Exception:
                    pass

        i += 1

    flush_current()
    return records


def extract_records_from_pdf_bytes(pdf_bytes: bytes, temp_png_path: Path) -> list:
    """
    PDF byte dizisini 140 DPI PNG'ye çevirip Tesseract Türkçe OCR ile okur ve
    tüm şirket adres/MERSİS/Sicil kayıtlarını döndürür.
    """
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if len(doc) == 0:
            doc.close()
            return []
        pix = doc[0].get_pixmap(dpi=140)
        pix.save(str(temp_png_path))
        doc.close()

        out = subprocess.check_output(
            [TESSERACT_BIN, str(temp_png_path), "stdout", "-l", "tur", "--psm", "3"],
            stderr=subprocess.DEVNULL,
            timeout=25,
        )
        txt = out.decode("utf-8", errors="ignore")
        return parse_gazete_ocr_text(txt)
    except Exception:
        return []
    finally:
        if temp_png_path.exists():
            try:
                temp_png_path.unlink()
            except Exception:
                pass


def backfill_vkn_from_mersis(conn: sqlite3.Connection) -> int:
    """
    sirketler tablosunda 16 haneli gerçek MERSİS numarası ('0' ile başlayan tüzel kişi)
    olup VKN sütunu boş olan tüm şirketlerin 10 haneli VKN'sini (SUBSTR(mersis_no, 2, 10))
    anında doldurur. Also parses ilce from ilan_metni/tam_adres where possible.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE sirketler
        SET vkn = SUBSTR(mersis_no, 2, 10)
        WHERE (vkn IS NULL OR vkn = '')
          AND mersis_no GLOB '0[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
    """)
    updated_vkn = cur.rowcount
    conn.commit()
    return updated_vkn


def apply_extracted_records_to_db(conn: sqlite3.Connection, records: list, sayi: str = None, sayfa: str = None) -> int:
    """
    OCR'dan çıkan (mersis_no, vkn, sicil_no, tam_adres, ilce, il, sermaye_tl) kayıtlarını
    hem doğrudan eşleşen sirketler satırlarına hem de aynı gazete_sayisi + sayfa_no'daki
    şirketlere uygular.
    """
    if not records:
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    updated = 0

    # Aynı gazete sayfasındaki ilanların sicil_no listesini al (tek ilan varsa doğrudan eşleştir)
    page_announcements = []
    if sayi and sayfa:
        page_announcements = cur.execute("""
            SELECT DISTINCT ticaret_sicil_no, tsm_adi, unvan
            FROM ticaret_sicil_gazetesi_ilanlari
            WHERE gazete_sayisi = ? AND sayfa_no = ?
        """, (str(sayi), str(sayfa))).fetchall()

    for idx, rec in enumerate(records):
        addr = rec.get("tam_adres")
        mersis = rec.get("mersis_no")
        vkn = rec.get("vkn")
        sicil = rec.get("sicil_no")
        ilce = rec.get("ilce")
        sermaye = rec.get("sermaye_tl")

        if not addr and not mersis and not sermaye:
            continue

        matched = False

        # 1. MERSİS No ile kesin eşleşme
        if mersis:
            cur.execute("""
                UPDATE sirketler
                SET tam_adres = COALESCE(?, tam_adres),
                    ilce = COALESCE(?, ilce),
                    vkn = COALESCE(?, vkn),
                    sermaye_tl = COALESCE(?, sermaye_tl),
                    son_guncelleme = ?
                WHERE mersis_no = ?
            """, (addr, ilce, vkn, sermaye, now_iso, mersis))
            if cur.rowcount > 0:
                updated += cur.rowcount
                matched = True

        # 2. Ticaret Sicil No ile eşleşme
        if not matched and sicil:
            cur.execute("""
                UPDATE sirketler
                SET tam_adres = COALESCE(?, tam_adres),
                    ilce = COALESCE(?, ilce),
                    mersis_no = CASE WHEN mersis_no LIKE 'TSM-%' AND ? IS NOT NULL THEN ? ELSE mersis_no END,
                    vkn = COALESCE(?, vkn),
                    sermaye_tl = COALESCE(?, sermaye_tl),
                    son_guncelleme = ?
                WHERE ticaret_sicil_no = ?
            """, (addr, ilce, mersis, mersis, vkn, sermaye, now_iso, sicil))
            if cur.rowcount > 0:
                updated += cur.rowcount
                matched = True

        # 3. Sayfadaki ilanlarla eşleşme (sayfada tek şirket varsa veya sırayla eşleşiyorsa)
        if not matched and page_announcements and addr:
            target_ann = page_announcements[0] if len(page_announcements) == 1 else (
                page_announcements[idx] if idx < len(page_announcements) else None
            )
            if target_ann:
                ann_sicil, ann_tsm, ann_unvan = target_ann
                if ann_sicil:
                    cur.execute("""
                        UPDATE sirketler
                        SET tam_adres = COALESCE(?, tam_adres),
                            ilce = COALESCE(?, ilce),
                            mersis_no = CASE WHEN mersis_no LIKE 'TSM-%' AND ? IS NOT NULL THEN ? ELSE mersis_no END,
                            vkn = COALESCE(?, vkn),
                            sermaye_tl = COALESCE(?, sermaye_tl),
                            son_guncelleme = ?
                        WHERE ticaret_sicil_no = ? AND tsm_adi = ?
                    """, (addr, ilce, mersis, mersis, vkn, sermaye, now_iso, ann_sicil, ann_tsm))
                    if cur.rowcount > 0:
                        updated += cur.rowcount
                        matched = True

    return updated


def process_single_pdf_item(item):
    """Thread worker: (worker_id, pdf_bytes, sayi, sayfa)"""
    worker_id, pdf_bytes, sayi, sayfa = item
    tmp_png = TEMP_OCR_DIR / f"ocr_worker_{worker_id}_{os.getpid()}.png"
    recs = extract_records_from_pdf_bytes(pdf_bytes, tmp_png)
    return sayi, sayfa, recs


def run_continuous_ocr_daemon():
    os.makedirs(TEMP_OCR_DIR, exist_ok=True)
    os.makedirs(PROCESSED_BUNDLES_LOG.parent, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")

    print("=" * 80)
    print("🚀 GEOPROP TOBB Gazete PDF OCR Adres & VKN & MERSİS Çıkarıcı Motor Başlatıldı")
    print("=" * 80, flush=True)

    # Adım 0: 16 haneli gerçek MERSİS numaralarından 10 haneli VKN'leri anında doldur
    vkn_filled = backfill_vkn_from_mersis(conn)
    if vkn_filled > 0:
        print(f"⚡ [VKN Backfill] 16 haneli MERSİS No'dan +{vkn_filled:,} şirketin 10 haneli Vergi No (VKN) bilgisi dolduruldu!", flush=True)

    processed_Set = set()
    if PROCESSED_BUNDLES_LOG.exists():
        processed_Set = set(PROCESSED_BUNDLES_LOG.read_text(encoding="utf-8").splitlines())

    # Adım 1: Lokalde bulunan 702+ PDF dosyasını paralel OCR ile tara
    local_pdfs = sorted(glob.glob(str(LOCAL_PDF_DIR / "*.pdf")))
    unprocessed_local = [p for p in local_pdfs if Path(p).name not in processed_Set]
    if unprocessed_local:
        print(f"📂 Lokalde {len(unprocessed_local)} adet işlenmemiş PDF sayfası bulundu, 4 çekirdek OCR ile adresler çıkarılıyor...", flush=True)
        batch_items = []
        for idx, p in enumerate(unprocessed_local):
            p_path = Path(p)
            parts = p_path.stem.split("_")
            sayi = parts[0] if len(parts) >= 2 else None
            sayfa = parts[1] if len(parts) >= 2 else None
            try:
                b = p_path.read_bytes()
                batch_items.append((idx % 4, b, sayi, sayfa, p_path.name))
            except Exception:
                pass

        total_addr_found = 0
        total_db_updated = 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(process_single_pdf_item, (it[0], it[1], it[2], it[3])): it[4]
                for it in batch_items
            }
            done_cnt = 0
            for fut in as_completed(futures):
                fname = futures[fut]
                done_cnt += 1
                try:
                    sayi, sayfa, recs = fut.result()
                    addrs = [r for r in recs if r.get("tam_adres")]
                    total_addr_found += len(addrs)
                    total_db_updated += apply_extracted_records_to_db(conn, recs, sayi, sayfa)
                    with open(PROCESSED_BUNDLES_LOG, "a", encoding="utf-8") as lf:
                        lf.write(fname + "\n")
                    processed_Set.add(fname)
                except Exception:
                    pass
                if done_cnt % 25 == 0 or done_cnt == len(batch_items):
                    conn.commit()
                    print(
                        f"  🔍 [Lokal PDF OCR] {done_cnt}/{len(batch_items)} sayfa okundu | "
                        f"Çıkarılan Adres: +{total_addr_found:,} | Güncellenen Şirket: +{total_db_updated:,}",
                        flush=True,
                    )

    # Adım 2: Buluttaki (GitHub Releases: warehouse-pdf-1..12 ve warehouse-latest) paketleri akış halinde indir, OCR yap ve sil
    buckets = [
        "warehouse-pdf-1", "warehouse-pdf-2", "warehouse-pdf-3",
        "warehouse-pdf-4", "warehouse-pdf-5", "warehouse-pdf-6",
        "warehouse-pdf-7", "warehouse-pdf-8", "warehouse-pdf-9",
        "warehouse-pdf-10", "warehouse-pdf-11", "warehouse-pdf-12",
        "warehouse-latest",
    ]

    while True:
        any_new_bundle = False
        for bucket in buckets:
            try:
                out = subprocess.check_output(
                    ["gh", "release", "view", bucket, "--json", "assets", "-q", ".assets[].name"],
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                ).decode("utf-8", errors="ignore")
                asset_names = [a.strip() for a in out.splitlines() if a.strip().endswith(".tar.gz") and "gazete_pdf_shard_" in a]
            except Exception:
                continue

            pending_assets = [a for a in asset_names if f"{bucket}/{a}" not in processed_Set]
            if not pending_assets:
                continue

            print(f"☁️ [{bucket}] {len(pending_assets)} yeni PDF paketi bulundu, akış halinde OCR adres çıkarımı başlıyor...", flush=True)
            for asset in pending_assets:
                any_new_bundle = True
                key = f"{bucket}/{asset}"
                tmp_tar = TEMP_OCR_DIR / asset
                try:
                    subprocess.run(
                        ["gh", "release", "download", bucket, "-p", asset, "-D", str(TEMP_OCR_DIR), "--clobber"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=90,
                        check=True,
                    )
                    if not tmp_tar.exists():
                        continue

                    pdf_tasks = []
                    with tarfile.open(tmp_tar, "r:gz") as tar:
                        for idx, member in enumerate(tar.getmembers()):
                            if member.isfile() and member.name.endswith(".pdf"):
                                fobj = tar.extractfile(member)
                                if fobj:
                                    b = fobj.read()
                                    stem = Path(member.name).stem
                                    parts = stem.split("_")
                                    sayi = parts[0] if len(parts) >= 2 else None
                                    sayfa = parts[1] if len(parts) >= 2 else None
                                    pdf_tasks.append((idx % 4, b, sayi, sayfa))

                    bundle_addrs = 0
                    bundle_updated = 0
                    with ThreadPoolExecutor(max_workers=4) as ex:
                        for sayi, sayfa, recs in ex.map(process_single_pdf_item, pdf_tasks):
                            bundle_addrs += sum(1 for r in recs if r.get("tam_adres"))
                            bundle_updated += apply_extracted_records_to_db(conn, recs, sayi, sayfa)

                    conn.commit()
                    with open(PROCESSED_BUNDLES_LOG, "a", encoding="utf-8") as lf:
                        lf.write(key + "\n")
                    processed_Set.add(key)

                    print(
                        f"  ✅ [{key}] {len(pdf_tasks)} sayfa OCR tamamlandı -> "
                        f"+{bundle_addrs} tam adres çıkarıldı, +{bundle_updated} şirket güncellendi!",
                        flush=True,
                    )
                except Exception as e:
                    print(f"  ⚠️ [{key}] OCR paket işleme uyarısı: {e}", flush=True)
                finally:
                    if tmp_tar.exists():
                        try:
                            tmp_tar.unlink()
                        except Exception:
                            pass

        if not any_new_bundle:
            time.sleep(60)


if __name__ == "__main__":
    run_continuous_ocr_daemon()
