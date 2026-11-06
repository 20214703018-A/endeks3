"""Lossless PDF optimization and durable bundle publication for TOBB workers.

No image transcoding, OCR, downsampling, metadata stripping or font removal.
Only PDF container/Flate compression changes are permitted. A candidate is
accepted only after comparing the complete reachable document object graph.
"""

import hashlib
import io
import json
import os
import tarfile
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pikepdf


def _has_signature(pdf):
    pending, seen = list(pdf.objects), set()
    while pending:
        obj = pending.pop()
        if not isinstance(obj, pikepdf.Object):
            continue
        if obj.is_indirect:
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            if ('/ByteRange' in obj or obj.get('/FT') == pikepdf.Name('/Sig')
                    or obj.get('/Type') == pikepdf.Name('/Sig')):
                return True
            pending.extend(obj[k] for k in obj.keys())
        elif isinstance(obj, pikepdf.Array):
            pending.extend(obj)
    return False


def _syntax_issues(pdf):
    # Specialized image decoders are intentionally unnecessary: compressed
    # image payloads are compared verbatim, never decoded/re-encoded here.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='pikepdf is missing some specialized decoders.*')
        return pdf.check_pdf_syntax()


@dataclass(frozen=True)
class OptimizedPDF:
    content: bytes
    original_bytes: int
    original_sha256: str
    stored_sha256: str
    status: str


def _same_document(left, right):
    """Compare graphs independently of object numbers and lossless encoding.

Generalized decoding leaves JPEG/JPX/JBIG2/CCITT streams untouched. For
unsupported filters compare raw bytes AND the original filter parameters.
The PDF writer's generated file ID/xref/Length are container bookkeeping.
"""
    pending = [(left.Root, right.Root), (left.docinfo, right.docinfo)]
    bookkeeping = {'/Root', '/Info', '/Size', '/ID', '/Prev', '/XRefStm'}
    # A PDF 1.5 xref-stream dictionary is exposed as the trailer as well.
    if left.trailer.get('/Type') == pikepdf.Name('/XRef') or right.trailer.get('/Type') == pikepdf.Name('/XRef'):
        bookkeeping |= {'/Type', '/W', '/Index', '/Length', '/Filter', '/DecodeParms'}
    left_keys = set(left.trailer.keys()) - bookkeeping
    right_keys = set(right.trailer.keys()) - bookkeeping
    if left_keys != right_keys:
        return False
    pending.extend((left.trailer[k], right.trailer[k]) for k in left_keys)
    visited = set()
    while pending:
        a, b = pending.pop()
        if isinstance(a, pikepdf.Object) and isinstance(b, pikepdf.Object):
            if a.is_indirect and b.is_indirect:
                pair = (a.objgen, b.objgen)
                if pair in visited:
                    continue
                visited.add(pair)
        if isinstance(a, pikepdf.Stream):
            if not isinstance(b, pikepdf.Stream):
                return False
            # Do not decode image codecs: their compressed payload must match.
            filters_a = a.get('/Filter')
            filters_b = b.get('/Filter')
            generalized = {'/FlateDecode', '/LZWDecode', '/ASCII85Decode',
                           '/ASCIIHexDecode', '/RunLengthDecode'}
            filters = [] if filters_a is None else (
                list(filters_a) if isinstance(filters_a, pikepdf.Array) else [filters_a]
            )
            filters2 = [] if filters_b is None else (
                list(filters_b) if isinstance(filters_b, pikepdf.Array) else [filters_b]
            )
            if all(str(f) in generalized for f in filters + filters2):
                try:
                    if a.read_bytes() != b.read_bytes():
                        return False
                    ignored = {'/Length', '/Filter', '/DecodeParms'}
                except pikepdf.PdfError:
                    if a.read_raw_bytes() != b.read_raw_bytes():
                        return False
                    ignored = {'/Length'}
            else:
                if a.read_raw_bytes() != b.read_raw_bytes():
                    return False
                ignored = {'/Length'}
            keys_a, keys_b = set(a.keys()) - ignored, set(b.keys()) - ignored
            if keys_a != keys_b:
                return False
            pending.extend((a[k], b[k]) for k in keys_a)
        elif isinstance(a, pikepdf.Dictionary):
            if not isinstance(b, pikepdf.Dictionary) or set(a.keys()) != set(b.keys()):
                return False
            pending.extend((a[k], b[k]) for k in a.keys())
        elif isinstance(a, pikepdf.Array):
            if not isinstance(b, pikepdf.Array) or len(a) != len(b):
                return False
            pending.extend(zip(a, b))
        elif isinstance(a, pikepdf.String):
            if not isinstance(b, pikepdf.String) or bytes(a) != bytes(b):
                return False
        elif a != b:
            return False
    return True


def preserve_pdf(data):
    """Validate a source PDF and retain its exact bytes without rewriting it."""
    with pikepdf.Pdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        if not pdf.pages or _syntax_issues(pdf):
            raise ValueError('PDF failed structural validation')
    digest = hashlib.sha256(data).hexdigest()
    return OptimizedPDF(data, len(data), digest, digest, 'original')


def optimize_pdf(data):
    original_hash = hashlib.sha256(data).hexdigest()

    def original(status):
        return OptimizedPDF(data, len(data), original_hash, original_hash, status)

    # Unparseable/truncated downloads must be retried, not marked archived.
    with pikepdf.Pdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        if pdf.is_encrypted:
            return original('encrypted_original')
        if not pdf.pages or _syntax_issues(pdf):
            raise ValueError('PDF failed structural validation')
        if '/Prev' in pdf.trailer:
            return original('incremental_original')
        if _has_signature(pdf):
            return original('signed_original')
        try:
            output = io.BytesIO()
            # Object streams require PDF 1.5. Preserve older versions (and
            # PDF/A-1) rather than upgrading their compatibility requirements.
            mode = (pikepdf.ObjectStreamMode.generate if
                    tuple(map(int, pdf.pdf_version.split('.'))) >= (1, 5)
                    else pikepdf.ObjectStreamMode.preserve)
            pdf.save(output, compress_streams=True, recompress_flate=True,
                     object_stream_mode=mode,
                     normalize_content=False, fix_metadata_version=False)
            candidate = output.getvalue()
            if len(candidate) >= len(data):
                return original('already_small')
            with pikepdf.Pdf.open(io.BytesIO(candidate), attempt_recovery=False) as checked:
                if _syntax_issues(checked) or not _same_document(pdf, checked):
                    return original('verification_original')
            return OptimizedPDF(candidate, len(data), original_hash,
                                hashlib.sha256(candidate).hexdigest(), 'optimized')
        except Exception:
            # Optimization failure must never discard a valid source PDF.
            return original('optimization_original')


def write_pdf(path, optimized):
    path = Path(path)
    temp = path.with_suffix('.pdf.part')
    with temp.open('wb') as stream:
        stream.write(optimized.content)
    os.replace(temp, path)
    metadata = {
        'file': path.name, 'original_bytes': optimized.original_bytes,
        'stored_bytes': len(optimized.content),
        'original_sha256': optimized.original_sha256,
        'stored_sha256': optimized.stored_sha256, 'status': optimized.status,
    }
    path.with_suffix('.pdf.json').write_text(json.dumps(metadata), encoding='utf-8')
    return metadata


def publish_bundle(items, source_dir, bundles_dir, shard_id, uploader):
    """Runs off the download thread. Files remain until upload is confirmed."""
    name = f'gazete_pdf_shard_{shard_id:02d}_{uuid.uuid4().hex}.tar.gz'
    path = Path(bundles_dir) / name
    partial = path.with_suffix('.part')
    manifest = []
    with tarfile.open(partial, 'w:gz', compresslevel=9) as archive:
        for _, filename, sayi, sayfa in items:
            source = Path(source_dir) / filename
            meta = json.loads(source.with_suffix('.pdf.json').read_text(encoding='utf-8'))
            if hashlib.sha256(source.read_bytes()).hexdigest() != meta['stored_sha256']:
                raise ValueError(f'PDF checksum mismatch: {filename}')
            archive.add(source, arcname=filename, recursive=False)
            manifest.append(dict(meta, gazete_sayisi=sayi, sayfa_no=sayfa))
        raw = json.dumps({'version': 1, 'files': manifest}, ensure_ascii=False).encode('utf-8')
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    os.replace(partial, path)
    ref = uploader(path, shard_id)
    if not ref.startswith('release://'):
        raise RuntimeError(f'Bundle upload unconfirmed; retained locally: {path}')
    return items, ref, path


def record_published_bundle(result, master, updates, source_dir):
    """Only confirmed release references enter the mergeable update database."""
    items, ref, path = result
    timestamp = datetime.now(timezone.utc).isoformat()
    rows = []
    for rep_id, filename, sayi, sayfa in items:
        ids = master.execute(
            'SELECT id FROM ticaret_sicil_gazetesi_ilanlari WHERE gazete_sayisi=? AND sayfa_no=?',
            (sayi, sayfa)).fetchall() or [(rep_id,)]
        rows.extend((rid, f'{ref}#{filename}', timestamp) for (rid,) in ids)
    updates.executemany(
        'INSERT OR REPLACE INTO gazete_pdf_updates VALUES (?, ?, ?)', rows)
    updates.commit()
    # The source master is read-only. This also avoids index churn/large writes.
    for _, filename, _, _ in items:
        (Path(source_dir) / filename).unlink(missing_ok=True)
        (Path(source_dir) / filename).with_suffix('.pdf.json').unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    return len(rows)
