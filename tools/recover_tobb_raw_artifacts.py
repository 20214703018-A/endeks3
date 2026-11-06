#!/usr/bin/env python3
"""Publish validated PDFs recovered from cancelled TOBB runner artifacts."""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.tobb_pdf_archive import publish_bundle, record_published_bundle


def recover_raw(source, master_path, updates_path, bundles, uploader):
    source, bundles, updates_path = Path(source), Path(bundles), Path(updates_path)
    bundles.mkdir(parents=True, exist_ok=True)
    updates_path.parent.mkdir(parents=True, exist_ok=True)
    master = sqlite3.connect(f'file:{master_path}?mode=ro', uri=True)
    updates = sqlite3.connect(updates_path)
    updates.execute('CREATE TABLE IF NOT EXISTS gazete_pdf_updates (id INTEGER PRIMARY KEY, pdf_dosya_yolu TEXT, guncellenme_tarihi TEXT)')
    groups, seen = defaultdict(list), set()
    summary = dict(validated_pdf_files=0, reused_pdf_files=0, published_pdf_files=0, announcement_links=0, skipped_incomplete_files=[])
    try:
        for path in sorted(source.rglob('*.pdf')):
            match = re.fullmatch(r'(\d+)_(\d+)_[A-Za-z0-9]*_[a-f0-9]+\.pdf', path.name)
            if not match:
                raise ValueError(f'Unexpected PDF filename: {path.name}')
            key = match.groups()
            if key in seen:
                continue
            metadata = path.with_suffix('.pdf.json')
            if not metadata.exists():
                summary['skipped_incomplete_files'].append(str(path))
                continue
            meta = json.loads(metadata.read_text())
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta['stored_sha256']:
                raise ValueError(f'Recovery checksum mismatch: {path.name}')
            ids = master.execute('SELECT id,pdf_dosya_yolu FROM ticaret_sicil_gazetesi_ilanlari WHERE gazete_sayisi=? AND sayfa_no=?', key).fetchall()
            if not ids:
                raise ValueError(f'Recovered page absent from master: {key}')
            seen.add(key)
            summary['validated_pdf_files'] += 1
            existing = next((ref for _,ref in ids if ref and ref.startswith('release://')), None)
            if existing:
                now = datetime.now(timezone.utc).isoformat()
                updates.executemany('INSERT OR REPLACE INTO gazete_pdf_updates VALUES (?,?,?)',
                                    [(rid,existing,now) for rid,_ in ids])
                summary['reused_pdf_files'] += 1
                summary['announcement_links'] += len(ids)
            else:
                groups[path.parent].append((ids[0][0], path.name, *key))
        updates.commit()
        for directory, items in groups.items():
            shard = re.search(r'temp_bundle_(\d+)', str(directory))
            shard_id = int(shard.group(1)) if shard else 1
            for start in range(0, len(items), 100):
                chunk = items[start:start+100]
                result = publish_bundle(chunk, directory, bundles, shard_id, uploader)
                summary['announcement_links'] += record_published_bundle(result, master, updates, directory)
                summary['published_pdf_files'] += len(chunk)
        return summary
    finally:
        updates.close()
        master.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('master', type=Path)
    parser.add_argument('updates', type=Path)
    parser.add_argument('--bundles', type=Path, default=Path('recovered_bundles'))
    parser.add_argument('--summary', type=Path, default=Path('raw_recovery_summary.json'))
    args = parser.parse_args()
    from collector.tobb_40_shard_pdf_indirici import upload_bundle_to_release
    result = recover_raw(args.source, args.master, args.updates, args.bundles, upload_bundle_to_release)
    args.summary.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
