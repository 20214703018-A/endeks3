#!/usr/bin/env python3
"""Recover missing TOBB page references from already published PDF archives.

Never contacts TOBB, extracts archive paths, rewrites PDFs or replaces an
existing page reference. A catalog avoids downloading the same archive again.
"""
import argparse
import hashlib
import io
import json
import re
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = '20214703018-A/endeks3'
PAGE_NAME = re.compile(r'(\d+)_(\d+)_[A-Za-z0-9]*_[a-f0-9]+\.pdf')


def catalog(path):
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS archives (
            asset_id INTEGER PRIMARY KEY, bucket TEXT, name TEXT,
            byte_size INTEGER, updated_at TEXT, pdf_count INTEGER);
        CREATE TABLE IF NOT EXISTS pages (
            ref TEXT PRIMARY KEY, gazete_sayisi TEXT, sayfa_no TEXT,
            sha256 TEXT, byte_size INTEGER, pdf_pages INTEGER);
    ''')
    return conn


def inventory(master, output, previous=None):
    with sqlite3.connect(f'file:{master}?mode=ro', uri=True) as conn:
        linked = {ref.split('#', 1)[0] for (ref,) in conn.execute(
            "SELECT DISTINCT pdf_dosya_yolu FROM ticaret_sicil_gazetesi_ilanlari WHERE pdf_dosya_yolu LIKE 'release://%'")}
    scanned = set()
    if previous and Path(previous).exists():
        with catalog(previous) as conn:
            scanned = set(conn.execute('SELECT asset_id,byte_size,updated_at FROM archives'))
    assets = []
    for number in range(1, 21):
        bucket = f'warehouse-pdf-{number}'
        release = json.loads(subprocess.check_output(
            ['gh', 'api', f'repos/{REPO}/releases/tags/{bucket}']))
        for asset in release['assets']:
            name = asset['name']
            if not re.fullmatch(r'gazete_pdf_shard_\d+_[A-Za-z0-9_]+\.tar\.gz', name):
                continue
            if (f'release://{bucket}/{name}' in linked or
                    (asset['id'], asset['size'], asset['updated_at']) in scanned):
                continue
            assets.append(dict(asset_id=asset['id'], bucket=bucket, name=name,
                               byte_size=asset['size'], updated_at=asset['updated_at'],
                               url=asset['browser_download_url']))
    Path(output).write_text(json.dumps(assets))
    print(json.dumps(dict(candidate_archives=len(assets), candidate_bytes=sum(a['byte_size'] for a in assets))), flush=True)
    return assets


def index_archive(path, asset, conn):
    import pikepdf
    records = []
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 5000 or sum(m.size for m in members) > 1024**3:
            raise ValueError('Archive exceeds scan budget')
        manifests = [m for m in members if m.name == 'manifest.json']
        expected = {}
        if manifests:
            if len(manifests) != 1 or manifests[0].size > 10 * 1024**2:
                raise ValueError('Invalid manifest')
            expected = {item['file']: item['stored_sha256'] for item in
                        json.load(archive.extractfile(manifests[0]))['files']}
        for member in members:
            if not member.name.lower().endswith('.pdf'):
                continue
            match = PAGE_NAME.fullmatch(member.name)
            if not match or not member.isfile() or not 0 < member.size <= 64 * 1024**2:
                raise ValueError('Unexpected PDF member')
            data = archive.extractfile(member).read()
            digest = hashlib.sha256(data).hexdigest()
            if manifests and expected.get(member.name) != digest:
                raise ValueError('Manifest checksum mismatch')
            with pikepdf.Pdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
                page_count = len(pdf.pages)
                if not page_count:
                    raise ValueError('Empty PDF')
            ref = f"release://{asset['bucket']}/{asset['name']}#{member.name}"
            records.append((ref, *match.groups(), digest, len(data), page_count))
    if not records or len({row[0] for row in records}) != len(records):
        raise ValueError('Empty archive or duplicate PDF names')
    with conn:
        conn.executemany('INSERT OR IGNORE INTO pages VALUES (?,?,?,?,?,?)', records)
        conn.execute('INSERT OR REPLACE INTO archives VALUES (?,?,?,?,?,?)',
                     (asset['asset_id'], asset['bucket'], asset['name'],
                      asset['byte_size'], asset['updated_at'], len(records)))
    return len(records)


def scan(inventory_path, output, shard, shards):
    import requests
    assets = json.loads(Path(inventory_path).read_text())[shard-1::shards]
    summary = dict(shard=shard, planned_archives=len(assets), scanned_archives=0,
                   validated_pdf_files=0, failures=[])
    conn = catalog(output)
    try:
        for asset in assets:
            # These are public assets owned by this repository. No TOBB session
            # or access challenge is involved. Let requests strip auth on redirects.
            expected_url = f"https://github.com/{REPO}/releases/download/{asset['bucket']}/{asset['name']}"
            if asset['url'] != expected_url:
                raise ValueError('Unexpected asset download URL')
            try:
                with tempfile.NamedTemporaryFile(suffix='.tar.gz') as tmp:
                    stop_at, size = time.monotonic() + 120, 0
                    with requests.get(asset['url'], stream=True, timeout=(10, 30)) as response:
                        response.raise_for_status()
                        for chunk in response.iter_content(1024**2):
                            size += len(chunk)
                            if time.monotonic() > stop_at or size > asset['byte_size']:
                                raise ValueError('Asset transfer exceeds budget')
                            tmp.write(chunk)
                    tmp.flush()
                    if size != asset['byte_size']:
                        raise ValueError('Incomplete archive download')
                    count = index_archive(tmp.name, asset, conn)
                summary['scanned_archives'] += 1
                summary['validated_pdf_files'] += count
                print(f"Scanned {summary['scanned_archives']}/{len(assets)} archives; {summary['validated_pdf_files']} validated PDFs", flush=True)
            except Exception as exc:
                summary['failures'].append(dict(asset_id=asset['asset_id'], error=type(exc).__name__))
    finally:
        conn.close()
        Path(str(output)+'.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return not summary['failures']


def merge(indices, master, output):
    conn = catalog(output)
    for path in sorted(Path(indices).rglob('archive_index_*.sqlite')):
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as source:
            with conn:
                conn.executemany('INSERT OR IGNORE INTO archives VALUES (?,?,?,?,?,?)',
                                 source.execute('SELECT * FROM archives'))
                conn.executemany('INSERT OR IGNORE INTO pages VALUES (?,?,?,?,?,?)',
                                 source.execute('SELECT * FROM pages'))
    added_pages = added_links = 0
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(master, timeout=120) as target:
        for ref, sayi, sayfa in conn.execute('SELECT ref,gazete_sayisi,sayfa_no FROM pages ORDER BY ref'):
            match = PAGE_NAME.fullmatch(ref.rsplit('#', 1)[-1])
            if not match or match.groups() != (sayi, sayfa) or not ref.startswith('release://warehouse-pdf-'):
                raise ValueError('Invalid catalog page identity')
            changed = target.execute('''UPDATE ticaret_sicil_gazetesi_ilanlari
                SET pdf_dosya_yolu=?,guncellenme_tarihi=?
                WHERE gazete_sayisi=? AND sayfa_no=?
                  AND (pdf_dosya_yolu IS NULL OR pdf_dosya_yolu='')''', (ref, now, sayi, sayfa)).rowcount
            added_links += changed
            added_pages += bool(changed)
        if target.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Master integrity check failed')
    summary = dict(added_unique_pages=added_pages, added_announcement_links=added_links,
                   scanned_archives=conn.execute('SELECT count(*) FROM archives').fetchone()[0],
                   validated_catalog_pages=conn.execute('SELECT count(*) FROM pages').fetchone()[0])
    conn.close()
    print(json.dumps(summary), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('inventory')
    p.add_argument('master'); p.add_argument('output'); p.add_argument('--previous')
    p = sub.add_parser('scan')
    p.add_argument('inventory'); p.add_argument('output')
    p.add_argument('--shard', type=int, required=True); p.add_argument('--shards', type=int, default=40)
    p = sub.add_parser('merge')
    p.add_argument('indices'); p.add_argument('master'); p.add_argument('output'); p.add_argument('--summary', required=True)
    args = parser.parse_args()
    if args.mode == 'inventory':
        inventory(args.master, args.output, args.previous)
    elif args.mode == 'scan':
        if not 1 <= args.shard <= args.shards:
            parser.error('Invalid shard')
        raise SystemExit(0 if scan(args.inventory, args.output, args.shard, args.shards) else 1)
    else:
        Path(args.summary).write_text(json.dumps(merge(args.indices, args.master, args.output), indent=2))
