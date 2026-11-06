#!/usr/bin/env python3
"""Read local TOBB PDFs; measure size and verify every page's rendered pixels.

Does not change source PDFs or contact TOBB/GitHub. Requires pikepdf + PyMuPDF.
"""
import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.tobb_pdf_archive import optimize_pdf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--step', type=int, default=1)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--dpi', type=int, default=150)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if min(args.step, args.limit, args.dpi) < 1:
        parser.error('step, limit and dpi must be positive')
    files = sorted(args.source.rglob('*.pdf'))[::args.step][:args.limit]
    if not files:
        parser.error('no source PDFs')
    results, statuses = [], Counter()
    for path in files:
        raw = path.read_bytes()
        started = time.perf_counter()
        result = optimize_pdf(raw)
        optimization_ms = (time.perf_counter() - started) * 1000
        hashes = []
        with pymupdf.open(stream=raw, filetype='pdf') as before, pymupdf.open(stream=result.content, filetype='pdf') as after:
            assert len(before) == len(after), f'Page count changed: {path}'
            for left, right in zip(before, after):
                a, b = left.get_pixmap(dpi=args.dpi), right.get_pixmap(dpi=args.dpi)
                assert (a.width, a.height, a.n) == (b.width, b.height, b.n), str(path)
                assert a.samples == b.samples, f'Pixels changed: {path}'
                assert left.get_text() == right.get_text(), f'Text changed: {path}'
                hashes.append(hashlib.sha256(a.samples).hexdigest())
        statuses[result.status] += 1
        results.append(dict(file=str(path), original_bytes=len(raw), stored_bytes=len(result.content),
                            original_sha256=result.original_sha256, stored_sha256=result.stored_sha256,
                            status=result.status, optimization_ms=round(optimization_ms, 3),
                            identical_render_sha256=hashes))
    original = sum(r['original_bytes'] for r in results)
    stored = sum(r['stored_bytes'] for r in results)
    summary = dict(files=len(results), original_bytes=original, stored_bytes=stored,
                   saved_bytes=original-stored, saved_percent=round((1-stored/original)*100, 5),
                   statuses=dict(statuses), rendered_pages=sum(len(r['identical_render_sha256']) for r in results),
                   render_dpi=args.dpi, render_and_text_identical=True,
                   mean_optimization_ms=round(sum(r['optimization_ms'] for r in results)/len(results), 3))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(dict(summary=summary, files=results), indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
