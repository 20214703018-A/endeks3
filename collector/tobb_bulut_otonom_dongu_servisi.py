#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local TOBB status observer.

GitHub's PDF workflow owns automatic chaining and master publication. This
observer must never restart cancelled/canary runs or publish a local master.
"""

import os
import sys
import json
import time
import gzip
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "warehouse" / "product" / "turkiye_ticaret_sicil_ve_sirketler.sqlite"
STATE_PATH = REPO_ROOT / "collector" / "data" / "bulut_otonom_dongu_state.json"
WORKFLOW_FILE = "tobb_gazete_pdf_40_nodes.yml"
REPO_SLUG = "20214703018-A/endeks3"


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_merged_run_id": 0, "restarts_triggered": 0}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_release_buckets():
    """Tüm warehouse-pdf-1..20 kovalarının varlığını ve kapasitesini kontrol eder."""
    try:
        out = subprocess.check_output(
            ["gh", "api", f"/repos/{REPO_SLUG}/releases?per_page=100", "--jq",
             '.[] | select(.tag_name | startswith("warehouse-pdf-")) | "\\(.tag_name):\\(.assets | length)"'],
            stderr=subprocess.DEVNULL, timeout=20
        ).decode("utf-8", errors="ignore").strip().splitlines()

        counts = {}
        for line in out:
            if ":" in line:
                tag, cnt = line.split(":", 1)
                counts[tag.strip()] = int(cnt.strip())

        for i in range(1, 21):
            tag = f"warehouse-pdf-{i}"
            if tag not in counts:
                subprocess.run(
                    ["gh", "release", "create", tag, "-R", REPO_SLUG,
                     "--title", f"GEOPROP TOBB Gazete PDF Havuzu #{i}",
                     "--notes", "Otonom 40-Node PDF Arşiv Kovası"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20
                )
        return counts
    except Exception:
        return {}


def sync_local_master_db_to_cloud():
    """Lokal SQLite veritabanını kilitlemeden yedekler, sıkıştırır ve warehouse-pdf-1'e yükler."""
    # GitHub merge/recovery workflows own the canonical master. Publishing a
    # local snapshot here races with those workflows and can erase newer links.
    print("Local master publication disabled; use the guarded GitHub recovery workflow.", flush=True)
    return False


def merge_completed_run_artifacts(run_id: int):
    """Biten koşunun shard-pdf-update-* artifact'lerini indirip lokal SQLite'a işler."""
    art_dir = REPO_ROOT / "warehouse" / "raw" / f"temp_run_artifacts_{run_id}"
    try:
        art_dir.mkdir(parents=True, exist_ok=True)
        print(f"📥 [Otonom Döngü] Run #{run_id} shard güncellemeleri indiriliyor...", flush=True)
        subprocess.run(
            ["gh", "run", "download", str(run_id), "-R", REPO_SLUG, "-D", str(art_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180
        )
        apply_script = REPO_ROOT / "geoprop" / "apply_shard_pdf_updates.py"
        if apply_script.exists():
            subprocess.run(
                [sys.executable, str(apply_script), str(art_dir), str(DB_PATH)],
                timeout=120
            )
    except Exception as e:
        print(f"⚠️ [Otonom Döngü] Artifact birleştirme uyarısı: {e}", flush=True)
    finally:
        if art_dir.exists():
            shutil.rmtree(art_dir, ignore_errors=True)


def check_and_retrigger_cloud_matrix(state: dict):
    try:
        out = subprocess.check_output(
            ["gh", "run", "list", "-R", REPO_SLUG, "--workflow", WORKFLOW_FILE,
             "--limit", "3", "--json", "databaseId,status,conclusion,createdAt"],
            stderr=subprocess.DEVNULL, timeout=20
        ).decode("utf-8", errors="ignore")
        runs = json.loads(out)
    except Exception as e:
        print(f"⚠️ [Otonom Döngü] GitHub Actions durum sorgusu hatası: {e}", flush=True)
        return

    if not runs:
        print("🚀 [Otonom Döngü] Hiç koşu bulunamadı, 40-Node PDF Matrisi başlatılıyor...", flush=True)
        print("GitHub workflow controls restarts; local watcher is observational.", flush=True)
        return

    active_runs = [r for r in runs if r.get("status") in ("in_progress", "queued", "waiting", "requested", "pending")]
    latest = runs[0]
    latest_id = int(latest["databaseId"])

    counts = ensure_release_buckets()
    total_bundles = sum(v for k, v in counts.items() if k.startswith("warehouse-pdf-"))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if active_runs:
        act_id = active_runs[0]["databaseId"]
        print(
            f"🟢 [{now_str}] 40-Node PDF Matrisi (Run #{act_id}) AKTİF ÇALIŞIYOR | "
            f"Yeni Kova Paketleri: {total_bundles} adet ({counts})",
            flush=True
        )
        return

    # A cancelled/failed/canary run must never be revived by a second writer.
    print(f"Run #{latest_id} finished ({latest.get('conclusion')}); "
          "GitHub workflow controls restart and merge. Local watcher made no changes.", flush=True)


def main():
    print("=" * 78)
    print("♾️  GEOPROP 7/24 OTONOM BULUT & LOKAL KESİNTİSİZ TARAMA DÖNGÜ SERVİSİ BAŞLADI")
    print("=" * 78, flush=True)
    state = load_state()

    while True:
        try:
            check_and_retrigger_cloud_matrix(state)
        except Exception as e:
            print(f"⚠️ [Otonom Döngü] Beklenmeyen hata: {e}", flush=True)
        time.sleep(150)


if __name__ == "__main__":
    main()
