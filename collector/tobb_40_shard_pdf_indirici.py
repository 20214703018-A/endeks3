#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TOBB 40-runner PDF worker with bounded asynchronous archival.

Eight existing download workers feed one archive/upload worker. Every PDF is
losslessly optimized and verified before publication. Only confirmed Release
uploads are recorded as completed; failed archives remain recoverable.
"""

import os
import sys
import time
import random
import re
import shutil
import sqlite3
import subprocess
import argparse
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from pathlib import Path

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings()

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collector.tobb_pdf_archive import optimize_pdf, write_pdf, publish_bundle, record_published_bundle

DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
DATA_DIR = REPO_ROOT / "collector" / "data"

USER_EMAIL = "geoprop_bot1@yandex.com"
USER_PASS = "Geoprop2026*"
BUNDLE_SIZE = 100  # Fewer Release calls; one uploader, at most two pending bundles.
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_preferred_release_bucket = {}

TESSERACT_BIN = (
    shutil.which("tesseract")
    or ("/opt/homebrew/bin/tesseract" if os.path.exists("/opt/homebrew/bin/tesseract") else "/usr/bin/tesseract")
)


class TOBBSessionManager:
    """Yüksek hızlı persistent Keep-Alive TOBB oturum yöneticisi."""

    def __init__(self, user_email, user_pass, tesseract_bin):
        self.user_email = user_email
        self.user_pass = user_pass
        self.tesseract_bin = tesseract_bin
        self.lock = threading.Lock()
        self.session = self._create_session()
        self.is_authenticated = False

    def _create_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
        })
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=Retry(total=3, backoff_factor=0.2))
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    def solve_ocr_captcha(self, captcha_url):
        for _ in range(5):
            try:
                sep = "&" if "?" in captcha_url else "?"
                url = f"{captcha_url}{sep}{int(time.time() * 1000)}_{random.randint(100, 999)}"
                r = self.session.get(url, verify=False, timeout=8)
                if not r.content or len(r.content) < 100:
                    continue
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    tf.write(r.content)
                    tmp_p = tf.name
                try:
                    out = subprocess.check_output([
                        self.tesseract_bin, tmp_p, "stdout",
                        "--psm", "8",
                        "-c", "tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    ], stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", errors="ignore").strip()
                    if len(out) == 4 and out.isalnum() and "ERROR" not in out:
                        return out
                finally:
                    if os.path.exists(tmp_p):
                        try:
                            os.unlink(tmp_p)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(0.1)
        return None

    def login(self):
        with self.lock:
            for attempt in range(8):
                try:
                    self.session.get("https://www.ticaretsicil.gov.tr/view/hizlierisim/girisyap.php", verify=False, timeout=8)
                    code = self.solve_ocr_captcha("https://www.ticaretsicil.gov.tr/assets/captcha/captcha.php")
                    if not code:
                        continue
                    r_login = self.session.post(
                        "https://www.ticaretsicil.gov.tr/view/modal/uyegirisi_ok.php",
                        data={"LoginEmail": self.user_email, "LoginSifre": self.user_pass, "Captcha": code},
                        verify=False, timeout=8
                    )
                    if r_login.text.strip() == "1":
                        self.session.get("https://www.ticaretsicil.gov.tr/index.php", verify=False, timeout=8)
                        self.session.headers["Referer"] = "https://www.ticaretsicil.gov.tr/view/hizlierisim/ilangoruntuleme.php"
                        self.is_authenticated = True
                        print("  ✅ TOBB Turbo Oturumu Başarıyla Açıldı!", flush=True)
                        return True
                except Exception:
                    pass
                time.sleep(0.3)
            return False

    def solve_ilan_captcha(self):
        with self.lock:
            for _ in range(6):
                code = self.solve_ocr_captcha("https://www.ticaretsicil.gov.tr/captcha/captcha.php")
                if not code:
                    continue
                try:
                    r = self.session.post(
                        "https://www.ticaretsicil.gov.tr/view/hizlierisim/guvenlikkodudogrula.php",
                        data={"CaptchaIlan": code},
                        verify=False, timeout=8
                    )
                    if r.text.strip() == "1":
                        return True
                except Exception:
                    pass
                time.sleep(0.1)
            return False

    def download_pdf(self, guid):
        view_url = f"https://www.ticaretsicil.gov.tr/view/hizlierisim/pdf_goster.php?Guid={guid}"
        for attempt in range(4):
            try:
                r_view = self.session.get(view_url, verify=False, timeout=12)
                raw_resp = r_view.text
            except Exception:
                time.sleep(0.15)
                continue

            m_pdf = re.search(r'embed\s+src="([^"]+\.pdf)"', raw_resp) or re.search(r'src="(/tmp_gazete/[^"]+\.pdf)"', raw_resp)
            if m_pdf:
                pdf_rel = m_pdf.group(1)
                pdf_url = pdf_rel if pdf_rel.startswith("http") else f"https://www.ticaretsicil.gov.tr{pdf_rel}"
                try:
                    r_pdf = self.session.get(pdf_url, verify=False, timeout=15)
                    if r_pdf.content and r_pdf.content.startswith(b"%PDF"):
                        return r_pdf.content, "OK"
                except Exception:
                    time.sleep(0.15)
                    continue

            if "girisyap.php" in r_view.url or "girisyap.php" in raw_resp:
                self.login()
                continue

            if 'id="CaptchaIlan"' in raw_resp or '<form id="FormGuvenlikKodu">' in raw_resp:
                if self.solve_ilan_captcha():
                    continue
                self.login()
                continue

            if r_view.status_code == 200:
                return None, "NO_PDF"

        return None, "TIMEOUT"


def get_release_bucket_for_shard(shard_id: int) -> list:
    all_buckets = [f"warehouse-pdf-{i}" for i in range(1, 21)]
    primary_idx = (shard_id - 1) % len(all_buckets)
    return [all_buckets[primary_idx]] + [b for i, b in enumerate(all_buckets) if i != primary_idx]


def upload_bundle_to_release(bundle_path: Path, shard_id: int) -> str:
    buckets = get_release_bucket_for_shard(shard_id)
    preferred = _preferred_release_bucket.get(shard_id)
    if preferred in buckets:
        buckets.remove(preferred)
        buckets.insert(0, preferred)
    for bucket in buckets:
        try:
            res = subprocess.run(
                ["gh", "release", "upload", bucket, str(bundle_path), "--clobber"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120
            )
            if res.returncode == 0:
                _preferred_release_bucket[shard_id] = bucket
                print(f"  ☁️ [{bucket}] {bundle_path.name} kalıcı olarak yüklendi!", flush=True)
                return f"release://{bucket}/{bundle_path.name}"
        except Exception:
            pass
    return f"bundle://{bundle_path.name}"


def get_shard_unique_page_tasks(
    conn: sqlite3.Connection,
    shard_id: int,
    num_shards: int,
    seen_pages: set,
    cursor_state: dict,
    limit: int = 150
):
    if not 1 <= shard_id <= num_shards:
        raise ValueError("shard must be between 1 and num_shards")
    c = conn.cursor()
    unique_tasks = []

    while len(unique_tasks) < limit and cursor_state["pass"] <= 2:
        current_pass = cursor_state["pass"]
        last_id = cursor_state["last_id"]

        if current_pass == 1:
            query = f"""
                SELECT id, tsm_adi, unvan, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni, ilan_tarihi
                FROM ticaret_sicil_gazetesi_ilanlari
                WHERE ((CAST(gazete_sayisi AS INT) * 31 + CAST(sayfa_no AS INT)) % {num_shards}) = ?
                  AND (pdf_dosya_yolu IS NULL OR pdf_dosya_yolu = '')
                  AND ilan_metni LIKE '%Guid:%'
                  AND id < ?
                  AND CAST(gazete_sayisi AS INT) >= 4950
                ORDER BY id DESC
                LIMIT 800
            """
        else:
            query = f"""
                SELECT id, tsm_adi, unvan, gazete_sayisi, sayfa_no, ilan_turu, ilan_metni, ilan_tarihi
                FROM ticaret_sicil_gazetesi_ilanlari
                WHERE ((CAST(gazete_sayisi AS INT) * 31 + CAST(sayfa_no AS INT)) % {num_shards}) = ?
                  AND (pdf_dosya_yolu IS NULL OR pdf_dosya_yolu = '')
                  AND ilan_metni LIKE '%Guid:%'
                  AND id < ?
                  AND CAST(gazete_sayisi AS INT) < 4950
                ORDER BY id DESC
                LIMIT 800
            """

        c.execute(query, ((shard_id - 1), last_id))
        rows = c.fetchall()

        if not rows:
            cursor_state["pass"] += 1
            cursor_state["last_id"] = 999999999
            continue

        for row in rows:
            cursor_state["last_id"] = row[0]
            sayi = str(row[3]).strip()
            sayfa = str(row[4]).strip()
            page_key = (sayi, sayfa)
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            unique_tasks.append(row)
            if len(unique_tasks) >= limit:
                break

    return unique_tasks


def ensure_shard_index(db_path, num_shards):
    # Build only when absent; then keep the large master read-only throughout
    # downloads. Existing 40-way indexes remain usable without rebuilding.
    with sqlite3.connect(db_path, timeout=90) as conn:
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_gazete_shard_hash_{num_shards}
            ON ticaret_sicil_gazetesi_ilanlari(
                ((CAST(gazete_sayisi AS INT) * 31 + CAST(sayfa_no AS INT)) % {num_shards}), id)
            WHERE pdf_dosya_yolu IS NULL OR pdf_dosya_yolu = ''
        """ if num_shards != 40 else """
            CREATE INDEX IF NOT EXISTS idx_gazete_shard_hash
            ON ticaret_sicil_gazetesi_ilanlari(
                ((CAST(gazete_sayisi AS INT) * 31 + CAST(sayfa_no AS INT)) % 40), id)
            WHERE pdf_dosya_yolu IS NULL OR pdf_dosya_yolu = ''
        """)


def main():
    parser = argparse.ArgumentParser(description="GEOPROP TOBB 40x Turbo Shard Gazete PDF İndirici (verified lossless archive)")
    parser.add_argument("--shard", type=int, required=True, help="Shard ID (1 - 40)")
    parser.add_argument("--num-shards", type=int, default=40, help="Toplam Shard Sayısı")
    parser.add_argument("--max-seconds", type=int, default=19000, help="Maksimum çalışma süresi (sn)")
    parser.add_argument("--max-pdfs", type=int, default=50000, help="Bu shard'ın indirebileceği maksimum tekil PDF sayfası")
    parser.add_argument("--threads", type=int, default=8, help="Paralel indirme iş parçacığı sayısı (Varsayılan: 8)")
    parser.add_argument("--bundle-size", type=int, default=BUNDLE_SIZE)
    args = parser.parse_args()

    if not 1 <= args.shard <= args.num_shards or min(args.threads, args.bundle_size, args.max_pdfs, args.max_seconds) < 1:
        parser.error("shard/num-shards and limits must be positive and valid")

    shard_id = args.shard
    num_shards = args.num_shards
    start_time = time.time()
    deadline = start_time + args.max_seconds

    print("=" * 78)
    print(f"🚀 GEOPROP TOBB Turbo PDF Shard İşçisi #{shard_id}/{num_shards} ({args.threads} downloads + asynchronous archive)")
    print(f"📍 Zaman Limiti: {args.max_seconds/3600:.1f} saat | Hedef: {args.max_pdfs:,} Sayfa")
    print("=" * 78, flush=True)

    temp_bundle_dir = REPO_ROOT / "warehouse" / "raw" / f"temp_bundle_{shard_id}"
    bundles_dir = REPO_ROOT / "warehouse" / "raw" / "bundles"
    shard_update_db_path = REPO_ROOT / "warehouse" / "product" / f"shard_{shard_id}_updates.sqlite"

    os.makedirs(temp_bundle_dir, exist_ok=True)
    os.makedirs(bundles_dir, exist_ok=True)

    # Shard update DB
    update_conn = sqlite3.connect(shard_update_db_path, timeout=90)
    update_conn.execute("""
        CREATE TABLE IF NOT EXISTS gazete_pdf_updates (
            id INTEGER PRIMARY KEY,
            pdf_dosya_yolu TEXT NOT NULL,
            guncellenme_tarihi TEXT NOT NULL
        )
    """)
    update_conn.commit()

    if not DB_PATH.exists():
        print(f"⚠️ Master SQLite bulunamadı: {DB_PATH}", flush=True)
        sys.exit(1)

    ensure_shard_index(DB_PATH, num_shards)
    master_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=90)
    # An index on (gazete_sayisi, sayfa_no) already supports page-to-announcement
    # lookups. Read the master without changing its 40-runner snapshot.

    mgr = TOBBSessionManager(USER_EMAIL, USER_PASS, TESSERACT_BIN)
    if not mgr.login():
        print(f"❌ [Shard {shard_id}] TOBB Yetkili Girişi açılamadı, çıkılıyor.", flush=True)
        sys.exit(1)

    total_downloaded = 0
    total_announcements_covered = 0
    current_bundle_files = []
    current_bundle_bytes = 0
    pending_bundles = []
    archive_errors = []
    bytes_original = 0
    bytes_stored = 0
    download_errors = 0

    def collect_bundle(future):
        nonlocal total_announcements_covered
        try:
            total_announcements_covered += record_published_bundle(
                future.result(), master_conn, update_conn, temp_bundle_dir)
            elapsed = max(0.001, time.time() - start_time)
            print(f"☁️ Confirmed archive: {total_announcements_covered:,} announcements; "
                  f"{total_downloaded*3600/elapsed:.0f} pages/hour; "
                  f"PDF bytes saved={bytes_original-bytes_stored:,}", flush=True)
        except Exception as exc:
            archive_errors.append(str(exc))
            print(f"❌ Archive retained; DB not marked complete: {exc}", flush=True)

    def queue_bundle(executor):
        nonlocal current_bundle_bytes
        if not current_bundle_files:
            return
        # Bounded backpressure: at most two bundles queued, not an entire run.
        if len(pending_bundles) >= 2:
            collect_bundle(pending_bundles.pop(0))
        items = list(current_bundle_files)
        current_bundle_files.clear()
        current_bundle_bytes = 0
        pending_bundles.append(executor.submit(
            publish_bundle, items, temp_bundle_dir, bundles_dir,
            shard_id, upload_bundle_to_release))

    def process_page_task(row):
        rep_id, tsm_adi, unvan, sayi, sayfa, ilan_turu, ilan_metni, ilan_tarihi = row
        m_guid = re.search(r'Guid:\s*([a-f0-9\-]+)', ilan_metni or "")
        if not m_guid:
            return None
        guid = m_guid.group(1).strip()

        if time.time() >= deadline:
            return None
        t0 = time.time()
        pdf_bytes, status = mgr.download_pdf(guid)
        if status != "OK" or not pdf_bytes:
            return None

        safe_tsm = re.sub(r'[^a-zA-Z0-9]', '', tsm_adi or "TSM")
        pdf_filename = f"{sayi}_{sayfa}_{safe_tsm}_{guid[:8]}.pdf"
        local_pdf_path = temp_bundle_dir / pdf_filename

        optimized = optimize_pdf(pdf_bytes)
        write_pdf(local_pdf_path, optimized)
        ms = (time.time() - t0) * 1000
        return (rep_id, pdf_filename, str(sayi), str(sayfa), ms,
                len(optimized.content), (unvan or "")[:28], len(pdf_bytes))

    seen_pages = set()
    cursor_state = {"pass": 1, "last_id": 999999999}

    fatal_error = None
    # Pools live for the whole run; packing/uploading does not block downloads.
    with ThreadPoolExecutor(max_workers=args.threads) as downloads, ThreadPoolExecutor(max_workers=1) as archives:
        backlog = deque()
        pending_downloads = set()
        exhausted = False
        try:
            while pending_downloads or (not exhausted and time.time() < deadline and total_downloaded < args.max_pdfs):
                # Refill as each request finishes; one slow PDF no longer holds
                # the next 200-page batch behind it. Bound queued work to 2x
                # the worker count, including the final/deadline drain.
                while (not archive_errors and time.time() < deadline
                       and len(pending_downloads) < args.threads * 2
                       and total_downloaded + len(pending_downloads) < args.max_pdfs):
                    if not backlog and not exhausted:
                        tasks = get_shard_unique_page_tasks(
                            master_conn, shard_id, num_shards, seen_pages, cursor_state,
                            limit=min(200, args.max_pdfs - total_downloaded))
                        backlog.extend(tasks)
                        exhausted = not tasks
                    if not backlog:
                        break
                    pending_downloads.add(downloads.submit(process_page_task, backlog.popleft()))
                if not pending_downloads:
                    break
                completed, pending_downloads = wait(pending_downloads, return_when=FIRST_COMPLETED)
                for fut in completed:
                    try:
                        res = fut.result()
                    except Exception as exc:
                        download_errors += 1
                        print(f"⚠️ PDF validation/download failed; will retry next run: {exc}", flush=True)
                        continue
                    if res:
                        rep_id, pdf_fn, s_sayi, s_sayfa, ms, sz, unv, original_sz = res
                        current_bundle_files.append((rep_id, pdf_fn, s_sayi, s_sayfa))
                        current_bundle_bytes += sz
                        total_downloaded += 1
                        bytes_original += original_sz
                        bytes_stored += sz
                        if total_downloaded % 10 == 0 or total_downloaded == 1:
                            print(f"  [Shard {shard_id}|#{total_downloaded:,}] {s_sayi}/{s_sayfa} "
                                  f"({original_sz/1024:.1f} -> {sz/1024:.1f} KB, {ms:.0f} ms) {unv}", flush=True)
                        if (len(current_bundle_files) >= args.bundle_size or
                                current_bundle_bytes >= MAX_BUNDLE_BYTES):
                            queue_bundle(archives)
                    else:
                        download_errors += 1
                    while pending_bundles and pending_bundles[0].done():
                        collect_bundle(pending_bundles.pop(0))
                if archive_errors and not pending_downloads:
                    raise RuntimeError("Archive publication failed; stopping downloads with files retained")
        except Exception as exc:
            fatal_error = exc
            print(f"❌ Shard #{shard_id}: {exc}", flush=True)
        finally:
            queue_bundle(archives)
            for future in pending_bundles:
                collect_bundle(future)
            master_conn.close()
            update_conn.close()
            elapsed = max(0.001, time.time() - start_time)
            print(f"🏁 Shard #{shard_id}: {total_downloaded:,} pages, "
                  f"{total_announcements_covered:,} announcements, "
                  f"{bytes_original:,} -> {bytes_stored:,} PDF bytes, "
                  f"{total_downloaded*3600/elapsed:.0f} pages/hour; "
                  f"download_errors={download_errors}, archive_errors={len(archive_errors)}", flush=True)
    if fatal_error or archive_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
