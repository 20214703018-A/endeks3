import hashlib
import io
import json
import sqlite3
import sys
import tarfile
import threading
from types import SimpleNamespace
from pathlib import Path

import pikepdf
import pytest

from collector import tobb_40_shard_pdf_indirici as worker
from collector.tobb_pdf_archive import (
    _same_document, optimize_pdf, publish_bundle, record_published_bundle, write_pdf,
)
from geoprop.apply_shard_pdf_updates import merge_pdf_updates


def make_pdf(signature=False):
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page()
        page.Contents = pdf.make_stream(b'q 1 0 0 1 0 0 cm Q\n' * 3000)
        pdf.docinfo['/Title'] = 'TOBB archive validation'
        pdf.attachments['evidence.txt'] = b'Original attachment\x00\xff'
        if signature:
            pdf.Root.TestSignature = pikepdf.Dictionary(
                FT=pikepdf.Name('/Sig'), ByteRange=pikepdf.Array([0, 1, 2, 3]))
        out = io.BytesIO()
        pdf.save(out, compress_streams=False)
        return out.getvalue()


def database(path=':memory:'):
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE ticaret_sicil_gazetesi_ilanlari (
        id INTEGER PRIMARY KEY, tsm_adi TEXT, unvan TEXT, gazete_sayisi TEXT,
        sayfa_no TEXT, ilan_turu TEXT, ilan_metni TEXT, ilan_tarihi TEXT,
        pdf_dosya_yolu TEXT, guncellenme_tarihi TEXT)''')
    return conn


def insert(conn, rid, sayi='5000', page='1', text=None):
    conn.execute('INSERT INTO ticaret_sicil_gazetesi_ilanlari VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                 (rid, 'TSM', 'Company', sayi, page, '', text, '', '', ''))
    conn.commit()


def updates_db():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE gazete_pdf_updates (id INTEGER PRIMARY KEY, pdf_dosya_yolu TEXT, guncellenme_tarihi TEXT)')
    return conn


def test_lossless_smaller_keeps_graph_and_attachment():
    data = make_pdf()
    result = optimize_pdf(data)
    assert result.status == 'optimized'
    assert len(result.content) < len(data) / 2
    with pikepdf.open(io.BytesIO(data)) as before, pikepdf.open(io.BytesIO(result.content)) as after:
        assert _same_document(before, after)
        assert after.attachments['evidence.txt'].get_file().read_bytes() == b'Original attachment\x00\xff'
        after.docinfo['/Title'] = 'CHANGED'
        assert not _same_document(before, after)
    assert result.original_sha256 == hashlib.sha256(data).hexdigest()


def test_signed_and_already_small_preserve_original_bytes():
    data = make_pdf(signature=True)
    assert optimize_pdf(data).content == data
    assert optimize_pdf(data).status == 'signed_original'
    data = optimize_pdf(make_pdf()).content
    assert len(optimize_pdf(data).content) <= len(data)


def test_malformed_pdf_is_not_accepted():
    with pytest.raises((pikepdf.PdfError, ValueError)):
        optimize_pdf(b'%PDF-1.4\ntruncated')


def test_failed_equivalence_preserves_original(monkeypatch):
    monkeypatch.setattr('collector.tobb_pdf_archive._same_document', lambda a, b: False)
    data = make_pdf()
    result = optimize_pdf(data)
    assert result.status == 'verification_original'
    assert result.content == data


def test_upload_failure_retains_all_files_and_marks_nothing(tmp_path):
    source = tmp_path / 'pdfs'
    source.mkdir()
    write_pdf(source / 'page.pdf', optimize_pdf(make_pdf()))
    with pytest.raises(RuntimeError, match='upload unconfirmed'):
        publish_bundle([(1, 'page.pdf', '5000', '1')], source, tmp_path, 1,
                       lambda path, shard: 'bundle://unpublished')
    assert (source / 'page.pdf').exists()
    assert (source / 'page.pdf.json').exists()
    assert len(list(tmp_path.glob('*.tar.gz'))) == 1


def test_uploaded_bundle_hash_manifest_and_all_announcement_links(tmp_path):
    source = tmp_path / 'pdfs'
    source.mkdir()
    optimized = optimize_pdf(make_pdf())
    write_pdf(source / 'page.pdf', optimized)
    result = publish_bundle([(1, 'page.pdf', '5000', '1')], source, tmp_path, 1,
                            lambda path, shard: f'release://warehouse-pdf-1/{path.name}')
    with tarfile.open(result[2]) as archive:
        manifest = json.load(archive.extractfile('manifest.json'))
        archived = archive.extractfile('page.pdf').read()
        assert manifest['files'][0]['stored_sha256'] == hashlib.sha256(archived).hexdigest()
        assert archived == optimized.content
    master, updates = database(), updates_db()
    insert(master, 1)
    insert(master, 2)
    assert record_published_bundle(result, master, updates, source) == 2
    assert updates.execute('SELECT count(*) FROM gazete_pdf_updates').fetchone()[0] == 2
    assert master.execute("SELECT count(*) FROM ticaret_sicil_gazetesi_ilanlari WHERE pdf_dosya_yolu='' ").fetchone()[0] == 2
    assert not result[2].exists()
    assert not list(source.iterdir())


def test_changed_pdf_cannot_be_uploaded(tmp_path):
    write_pdf(tmp_path / 'page.pdf', optimize_pdf(make_pdf()))
    (tmp_path / 'page.pdf').write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum mismatch'):
        publish_bundle([(1, 'page.pdf', '5000', '1')], tmp_path, tmp_path, 1,
                       lambda *args: pytest.fail('must not upload corrupt PDF'))


def test_shard_coverage_and_usable_representative():
    conn = database()
    insert(conn, 1, text=None)
    insert(conn, 2, text='Guid: abcdef01')
    insert(conn, 3, sayi='4000', page='2', text='Guid: abcdef02')
    insert(conn, 4, text=None)  # newer unusable duplicate must not hide id=2
    ids = []
    for shard in range(1, 4):
        seen, cursor = set(), {'pass': 1, 'last_id': 999999999}
        while tasks := worker.get_shard_unique_page_tasks(conn, shard, 3, seen, cursor, limit=1):
            ids.extend(row[0] for row in tasks)
    assert sorted(ids) == [2, 3]


def test_worker_overlaps_downloads_and_uploads_and_merges_confirmed_only(tmp_path, monkeypatch):
    product = tmp_path / 'warehouse/product'
    product.mkdir(parents=True)
    db = product / 'master.sqlite'
    conn = database(db)
    for rid in range(1, 7):
        insert(conn, rid, page=str(rid), text=f'Guid: abcdef0{rid}')
    conn.close()
    data = make_pdf()
    downloading_while_uploading = threading.Event()
    upload_started = threading.Event()
    calls = []

    class Session:
        def __init__(self, *args):
            pass

        def login(self):
            return True

        def download_pdf(self, guid):
            calls.append(guid)
            if len(calls) >= 3:
                assert upload_started.wait(5)
                downloading_while_uploading.set()
            return data, 'OK'

    def upload(path, shard):
        upload_started.set()
        assert downloading_while_uploading.wait(5)
        return f'release://warehouse-pdf-1/{path.name}'

    monkeypatch.setattr(worker, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(worker, 'DB_PATH', db)
    monkeypatch.setattr(worker, 'TOBBSessionManager', Session)
    monkeypatch.setattr(worker, 'upload_bundle_to_release', upload)
    monkeypatch.setattr(sys, 'argv', ['worker', '--shard', '1', '--num-shards', '1',
                                    '--threads', '1', '--bundle-size', '2', '--max-pdfs', '6'])
    assert worker.main() == 0
    with sqlite3.connect(product / 'shard_1_updates.sqlite') as updates:
        assert updates.execute('SELECT count(*) FROM gazete_pdf_updates').fetchone()[0] == 6
        assert updates.execute("SELECT count(*) FROM gazete_pdf_updates WHERE pdf_dosya_yolu LIKE 'release://%' ").fetchone()[0] == 6
    assert downloading_while_uploading.is_set()


def test_worker_returns_failure_and_retains_unpublished_data(tmp_path, monkeypatch):
    product = tmp_path / 'warehouse/product'
    product.mkdir(parents=True)
    db = product / 'master.sqlite'
    conn = database(db)
    insert(conn, 1, text='Guid: abcdef01')
    conn.close()
    data = make_pdf()
    class Session:
        def __init__(self, *args):
            pass
        def login(self):
            return True
        def download_pdf(self, guid):
            return data, 'OK'
    monkeypatch.setattr(worker, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(worker, 'DB_PATH', db)
    monkeypatch.setattr(worker, 'TOBBSessionManager', Session)
    monkeypatch.setattr(worker, 'upload_bundle_to_release', lambda path, shard: 'bundle://failed')
    monkeypatch.setattr(sys, 'argv', ['worker', '--shard', '1', '--num-shards', '1', '--max-pdfs', '1'])
    assert worker.main() == 1
    with sqlite3.connect(product / 'shard_1_updates.sqlite') as updates:
        assert updates.execute('SELECT count(*) FROM gazete_pdf_updates').fetchone()[0] == 0
    assert list((tmp_path / 'warehouse/raw/temp_bundle_1').glob('*.pdf'))
    assert list((tmp_path / 'warehouse/raw/bundles').glob('*.tar.gz'))


def test_pdf_only_merge_needs_no_ocr_and_rolls_back_invalid_shard(tmp_path):
    master = tmp_path / 'master.sqlite'
    conn = database(master)
    insert(conn, 1)
    conn.close()
    good = tmp_path / 'shard_1_updates.sqlite'
    with sqlite3.connect(good) as updates:
        updates.execute('CREATE TABLE gazete_pdf_updates (id INTEGER, pdf_dosya_yolu TEXT, guncellenme_tarihi TEXT)')
        updates.execute('INSERT INTO gazete_pdf_updates VALUES (1, ?, ?)',
                        ('release://warehouse-pdf-1/page.tar.gz#page.pdf', '2026-11-06'))
    assert merge_pdf_updates(tmp_path, master)
    with sqlite3.connect(master) as conn:
        assert conn.execute('SELECT pdf_dosya_yolu FROM ticaret_sicil_gazetesi_ilanlari').fetchone()[0].startswith('release://')
        conn.execute("UPDATE ticaret_sicil_gazetesi_ilanlari SET pdf_dosya_yolu='' ")
    (tmp_path / 'shard_2_updates.sqlite').write_bytes(b'invalid database')
    assert not merge_pdf_updates(tmp_path, master)
    with sqlite3.connect(master) as conn:
        assert conn.execute('SELECT pdf_dosya_yolu FROM ticaret_sicil_gazetesi_ilanlari').fetchone()[0] == ''


def test_reuses_working_bucket_instead_of_retrying_full_buckets(monkeypatch, tmp_path):
    calls = []
    worker._preferred_release_bucket.clear()
    def run(command, **kwargs):
        calls.append(command[3])
        return SimpleNamespace(returncode=0 if command[3] == 'warehouse-pdf-2' else 1)
    monkeypatch.setattr(worker.subprocess, 'run', run)
    assert worker.upload_bundle_to_release(tmp_path / '1.tar.gz', 1).startswith('release://')
    assert worker.upload_bundle_to_release(tmp_path / '2.tar.gz', 1).startswith('release://')
    assert calls == ['warehouse-pdf-1', 'warehouse-pdf-2', 'warehouse-pdf-2']


def test_slow_pdf_does_not_block_next_200_page_batch(tmp_path, monkeypatch):
    product = tmp_path / 'warehouse/product'
    product.mkdir(parents=True)
    db = product / 'master.sqlite'
    conn = database(db)
    for rid in range(1, 202):
        insert(conn, rid, page=str(rid), text=f'Guid: {rid:08x}')
    conn.close()
    data = optimize_pdf(make_pdf()).content
    next_batch_started = threading.Event()
    slow_finished = threading.Event()
    class Session:
        def __init__(self, *args):
            pass
        def login(self):
            return True
        def download_pdf(self, guid):
            if int(guid, 16) == 201:
                assert next_batch_started.wait(5), 'slow PDF blocked next batch'
                slow_finished.set()
            if int(guid, 16) == 1:
                assert not slow_finished.is_set()
                next_batch_started.set()
            return data, 'OK'
    monkeypatch.setattr(worker, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(worker, 'DB_PATH', db)
    monkeypatch.setattr(worker, 'TOBBSessionManager', Session)
    monkeypatch.setattr(worker, 'upload_bundle_to_release', lambda path, shard: f'release://warehouse-pdf-1/{path.name}')
    monkeypatch.setattr(sys, 'argv', ['worker', '--shard', '1', '--num-shards', '1', '--threads', '2', '--max-pdfs', '201'])
    assert worker.main() == 0
    assert next_batch_started.is_set()
    assert slow_finished.is_set()
    with sqlite3.connect(product / 'shard_1_updates.sqlite') as updates:
        assert updates.execute('SELECT count(*) FROM gazete_pdf_updates').fetchone()[0] == 201


def test_stale_auth_responses_do_not_repeat_completed_session_renewal(monkeypatch):
    manager = worker.TOBBSessionManager('', '', '')
    manager.is_authenticated = True
    manager.session_generation = 2
    def forbidden(*args, **kwargs):
        pytest.fail('stale response must not trigger network/session renewal')
    monkeypatch.setattr(manager.session, 'get', forbidden)
    monkeypatch.setattr(manager.session, 'post', forbidden)
    monkeypatch.setattr(manager, 'solve_ocr_captcha', forbidden)
    assert manager.login(expected_generation=1)
    assert manager.solve_ilan_captcha(expected_generation=1)
    assert manager.session_generation == 2


def test_successful_login_advances_generation_for_waiting_workers(monkeypatch):
    manager = worker.TOBBSessionManager('', '', '')
    calls = []
    monkeypatch.setattr(manager.session, 'get', lambda *a, **k: None)
    monkeypatch.setattr(manager, 'solve_ocr_captcha', lambda *a: 'fixture')
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(text='1')
    monkeypatch.setattr(manager.session, 'post', post)
    assert manager.login(expected_generation=0)
    assert manager.session_generation == 1
    # A second worker received its redirect before the first renewed.
    assert manager.login(expected_generation=0)
    assert calls == [1]
