#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TOBB 40-runner PDF worker with bounded asynchronous archival.

Eight existing download workers feed one archive/upload worker. Every PDF is
losslessly optimized and verified before publication. Only confirmed Release
uploads are recorded as completed; failed archives remain recoverable.
"""

import os
import json
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
from urllib.parse import urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings()

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collector.tobb_pdf_archive import preserve_pdf, write_pdf, publish_bundle, record_published_bundle

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
        self.session_generation = 0
        self.request_gate = threading.Condition()
        self.active_views = 0
        self.renewing = False
        self.auth_deadline = None
        self.failed_renewal = None
        self.reported_view_states = set()
        self.download_deadline = None

    def _download_response(self, url, timeout):
        # A socket read timeout alone does not bound a trickling response or
        # a redirect chain. Bound the whole response and release its socket.
        stop_at = time.monotonic() + 45
        if self.download_deadline is not None:
            stop_at = min(stop_at, self.download_deadline)
        for _ in range(6):
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout('Download response budget exhausted')
            response = self.session.get(url, verify=False, stream=True,
                                        allow_redirects=False, timeout=min(timeout, remaining))
            try:
                if response.is_redirect:
                    url = requests.compat.urljoin(url, response.headers['Location'])
                    continue
                chunks = []
                for chunk in response.iter_content(65536):
                    if time.monotonic() >= stop_at:
                        raise requests.Timeout('Download response budget exhausted')
                    chunks.append(chunk)
                response._content = b''.join(chunks)
                response._content_consumed = True
                return response
            finally:
                response.close()
        raise requests.TooManyRedirects('Download redirect budget exhausted')

    def _get_view(self, url):
        with self.request_gate:
            self.request_gate.wait_for(lambda: not self.renewing)
            generation = self.session_generation
            self.active_views += 1
        try:
            return self._download_response(url, timeout=12), generation
        finally:
            with self.request_gate:
                self.active_views -= 1
                self.request_gate.notify_all()

    def _renew_session(self, generation, ilan=False):
        # Drain viewer requests before renewing. Concurrent viewer requests can
        # otherwise change server-side session state during renewal.
        with self.request_gate:
            self.request_gate.wait_for(lambda: not self.renewing)
            if generation != self.session_generation:
                return True
            if (self.failed_renewal is not None and self.failed_renewal[0] == generation
                    and time.monotonic() - self.failed_renewal[1] < 60):
                return False
            self.renewing = True
            self.request_gate.wait_for(lambda: self.active_views == 0)
        try:
            self.auth_deadline = time.monotonic() + 60
            started = time.monotonic()
            print(f"Session maintenance started: {'ilan' if ilan else 'login'}", flush=True)
            if ilan:
                result = self.solve_ilan_captcha(expected_generation=generation)
            else:
                result = self.login(expected_generation=generation)
            if not result:
                self.failed_renewal = (generation, time.monotonic())
            return result
        finally:
            print(f"Session maintenance ended after {time.monotonic()-started:.1f}s", flush=True)
            with self.request_gate:
                self.renewing = False
                self.request_gate.notify_all()

    def _create_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
        })
        # Outer loops already retry requests. Retrying slow reads again inside
        # urllib3 multiplied renewal stalls into many minutes per PDF.
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20,
                              max_retries=Retry(total=1, connect=1, read=0, status=0, backoff_factor=0.2))
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    def solve_ocr_captcha(self, captcha_url, psm=8):
        for _ in range(5):
            if self.auth_deadline and time.monotonic() >= self.auth_deadline:
                return None
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
                        "--psm", str(psm),
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

    def login(self, expected_generation=None):
        with self.lock:
            # Another worker may have renewed the session while this worker
            # was waiting for the lock with an older HTTP response.
            if (expected_generation is not None and self.is_authenticated
                    and expected_generation != self.session_generation):
                return True
            if self.auth_deadline is None:
                self.auth_deadline = time.monotonic() + 60
            for attempt in range(8):
                if time.monotonic() >= self.auth_deadline:
                    return False
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
                        self.session_generation += 1
                        print("  ✅ TOBB Turbo Oturumu Başarıyla Açıldı!", flush=True)
                        return True
                except Exception:
                    pass
                time.sleep(0.3)
            return False

    def solve_ilan_captcha(self, expected_generation=None):
        with self.lock:
            if (expected_generation is not None and self.is_authenticated
                    and expected_generation != self.session_generation):
                return True
            for _ in range(6):
                if self.auth_deadline and time.monotonic() >= self.auth_deadline:
                    return False
                code = self.solve_ocr_captcha("https://www.ticaretsicil.gov.tr/captcha/captcha.php", psm=7)
                if not code:
                    continue
                try:
                    r = self.session.post(
                        "https://www.ticaretsicil.gov.tr/view/hizlierisim/guvenlikkodudogrula.php",
                        files={"CaptchaIlan": (None, code)},
                        headers={"X-Requested-With": "XMLHttpRequest"},
                        verify=False, timeout=8
                    )
                    if r.text.strip() == "1":
                        # Match the official form's successful redirect before
                        # resuming viewer requests against the renewed session.
                        self.session.get("https://www.ticaretsicil.gov.tr/view/hizlierisim/ilangoruntuleme.php",
                                         verify=False, timeout=8)
                        self.session_generation += 1
                        return True
                except Exception:
                    pass
                time.sleep(0.1)
            return False

    def download_pdf(self, guid):
        view_url = f"https://www.ticaretsicil.gov.tr/view/hizlierisim/pdf_goster.php?Guid={guid}"
        last_status = "RETRY_EXHAUSTED"
        for attempt in range(4):
            try:
                r_view, generation = self._get_view(view_url)
                raw_resp = r_view.text
            except requests.Timeout:
                return None, "VIEW_TIMEOUT"
            except requests.RequestException:
                return None, "VIEW_NETWORK_ERROR"

            if r_view.status_code == 429:
                return None, "SOURCE_THROTTLED"
            if r_view.status_code == 403:
                return None, "ACCESS_DENIED"
            last_status = f"VIEW_HTTP_{r_view.status_code}"

            m_pdf = re.search(r'embed\s+src="([^"]+\.pdf)"', raw_resp) or re.search(r'src="(/tmp_gazete/[^"]+\.pdf)"', raw_resp)
            if m_pdf:
                pdf_rel = m_pdf.group(1)
                pdf_url = pdf_rel if pdf_rel.startswith("http") else f"https://www.ticaretsicil.gov.tr{pdf_rel}"
                try:
                    r_pdf = self._download_response(pdf_url, timeout=15)
                    if r_pdf.status_code == 429:
                        return None, "SOURCE_THROTTLED"
                    if r_pdf.status_code == 403:
                        return None, "ACCESS_DENIED"
                    if r_pdf.content and r_pdf.content.startswith(b"%PDF"):
                        return r_pdf.content, "OK"
                    last_status = f"PDF_HTTP_{r_pdf.status_code}"
                except requests.Timeout:
                    return None, "PDF_TIMEOUT"
                except requests.RequestException:
                    return None, "PDF_NETWORK_ERROR"

            # Attribute order, whitespace and quote style vary between pages.
            ilan_form = re.search(r'\b(?:id|name)\s*=\s*[\"\'](?:CaptchaIlan|FormGuvenlikKodu)[\"\']', raw_resp, re.I)
            form_ids = tuple(re.findall(r'<form\b[^>]*\bid\s*=\s*[\"\']([^\"\']+)', raw_resp, re.I))
            fields = tuple(re.findall(r'<input\b[^>]*\bname\s*=\s*[\"\']([^\"\']+)', raw_resp, re.I)[:15])
            signature = (r_view.status_code, form_ids, fields)
            if signature not in self.reported_view_states and len(self.reported_view_states) < 5:
                self.reported_view_states.add(signature)
                # Only structural names are logged; never cookies, form values,
                # challenge answers, credentials or the response HTML itself.
                print('Viewer state: ' + json.dumps(dict(http=r_view.status_code,
                      path=urlparse(r_view.url).path, forms=form_ids, fields=fields,
                      ilan_form=bool(ilan_form))), flush=True)
            if ilan_form:
                if self._renew_session(generation, ilan=True):
                    continue
                return None, "AUTH_REQUIRED"

            # Menu/footer links do not mean the user's session has ended.
            if (urlparse(r_view.url).path.lower().endswith('/girisyap.php') or
                    re.search(r'(?:window\.)?location(?:\.href)?\s*=\s*[\"\'][^\"\']*girisyap\.php', raw_resp, re.I)):
                self._renew_session(generation)
                continue

            if r_view.status_code == 200:
                return None, "NO_PDF"

        return None, last_status


def get_release_bucket_for_shard(shard_id: int) -> list:
    all_buckets = [f"warehouse-pdf-{i}" for i in range(1, 21)]
    primary_idx = (shard_id - 1) % len(all_buckets)
    return [all_buckets[primary_idx]] + [b for i, b in enumerate(all_buckets) if i != primary_idx]


def upload_bundle_to_release(bundle_path: Path, shard_id: int) -> str:
    upload_deadline = time.monotonic() + 300
    buckets = get_release_bucket_for_shard(shard_id)
    preferred = _preferred_release_bucket.get(shard_id)
    if preferred in buckets:
        buckets.remove(preferred)
        buckets.insert(0, preferred)
    for bucket in buckets:
        remaining = upload_deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            res = subprocess.run(
                ["gh", "release", "upload", bucket, str(bundle_path), "--clobber"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=min(90, remaining)
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
    parser = argparse.ArgumentParser(description="GEOPROP TOBB 40x Turbo Shard Gazete PDF İndirici (original PDF archive)")
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
    mgr.download_deadline = time.monotonic() + max(0, deadline - time.time())

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
            return {"status": status, "page": f"{sayi}/{sayfa}"}

        safe_tsm = re.sub(r'[^a-zA-Z0-9]', '', tsm_adi or "TSM")
        pdf_filename = f"{sayi}_{sayfa}_{safe_tsm}_{guid[:8]}.pdf"
        local_pdf_path = temp_bundle_dir / pdf_filename

        optimized = preserve_pdf(pdf_bytes)
        write_pdf(local_pdf_path, optimized)
        ms = (time.time() - t0) * 1000
        return (rep_id, pdf_filename, str(sayi), str(sayfa), ms,
                len(optimized.content), (unvan or "")[:28], len(pdf_bytes))

    seen_pages = set()
    cursor_state = {"pass": 1, "last_id": 999999999}

    fatal_error = None
    source_statuses = {}
    auth_blocked = False
    first_bundle_queued = False
    last_bundle_time = time.monotonic()
    # Pools live for the whole run; packing/uploading does not block downloads.
    with ThreadPoolExecutor(max_workers=args.threads) as downloads, ThreadPoolExecutor(max_workers=1) as archives:
        backlog = deque()
        pending_downloads = set()
        exhausted = False
        try:
            while pending_downloads or (not exhausted and time.time() < deadline and total_downloaded < args.max_pdfs):
                # Commit confirmed uploads even when the source is stalled.
                while pending_bundles and pending_bundles[0].done():
                    collect_bundle(pending_bundles.pop(0))
                # Refill as each request finishes; one slow PDF no longer holds
                # the next 200-page batch behind it. Bound queued work to 2x
                # the worker count, including the final/deadline drain.
                while (not archive_errors and not auth_blocked and time.time() < deadline
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
                completed, pending_downloads = wait(pending_downloads, timeout=5, return_when=FIRST_COMPLETED)
                if not completed and current_bundle_files and time.monotonic()-last_bundle_time >= 30:
                    queue_bundle(archives)
                    first_bundle_queued = True
                    last_bundle_time = time.monotonic()
                for fut in completed:
                    try:
                        res = fut.result()
                    except Exception as exc:
                        download_errors += 1
                        print(f"⚠️ PDF validation/download failed; will retry next run: {exc}", flush=True)
                        continue
                    if isinstance(res, dict):
                        download_errors += 1
                        status = res['status']
                        source_statuses[status] = source_statuses.get(status, 0) + 1
                        if source_statuses[status] <= 3 or source_statuses[status] % 10 == 0:
                            print(f"Source status {status}: {res['page']} (count={source_statuses[status]})", flush=True)
                        if (status in ('SOURCE_THROTTLED', 'ACCESS_DENIED') or
                                status == 'AUTH_REQUIRED' and source_statuses[status] >= 3):
                            auth_blocked = True
                            fatal_error = RuntimeError(f'TOBB access unavailable ({status}); PDFs retained')
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
                        if ((not first_bundle_queued and len(current_bundle_files) >= 10) or
                                len(current_bundle_files) >= args.bundle_size or
                                current_bundle_bytes >= MAX_BUNDLE_BYTES):
                            queue_bundle(archives)
                            first_bundle_queued = True
                            last_bundle_time = time.monotonic()
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
            print(f"Source status totals: {source_statuses}", flush=True)
    if fatal_error or archive_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
