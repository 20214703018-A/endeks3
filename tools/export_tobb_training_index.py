#!/usr/bin/env python3
"""Export a bulk-download index for the PDFs already confirmed in the master."""
import argparse
import gzip
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import quote


def export(master, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    pages_path = directory / 'tobb_training_pages.jsonl.gz'
    archives_path = directory / 'tobb_training_archives.jsonl.gz'
    counts = Counter()
    pages = 0
    with sqlite3.connect(f'file:{master}?mode=ro', uri=True) as conn, gzip.open(pages_path, 'wt', encoding='utf-8', compresslevel=1) as out:
        rows = conn.execute('''SELECT gazete_sayisi,sayfa_no,MIN(pdf_dosya_yolu)
            FROM ticaret_sicil_gazetesi_ilanlari WHERE pdf_dosya_yolu LIKE 'release://%'
            GROUP BY gazete_sayisi,sayfa_no ORDER BY gazete_sayisi,sayfa_no''')
        for sayi, sayfa, ref in rows:
            match = re.fullmatch(r'release://(warehouse-(?:pdf-\d+|latest))/([A-Za-z0-9_.-]+\.tar\.gz)#([A-Za-z0-9_.-]+\.pdf)', ref)
            if not match:
                raise ValueError('Unrecognized archive reference')
            bucket, filename, member = match.groups()
            identity = re.fullmatch(r'(\d+)_(\d+)_[A-Za-z0-9]*_[a-f0-9]+\.pdf', member)
            if not identity or identity.groups() != (str(sayi), str(sayfa)):
                raise ValueError('Page metadata differs from PDF member identity')
            url = f'https://github.com/20214703018-A/endeks3/releases/download/{quote(bucket)}/{quote(filename)}'
            out.write(json.dumps(dict(gazete_sayisi=str(sayi), sayfa_no=str(sayfa), archive=url, member=member), ensure_ascii=False)+'\n')
            counts[url] += 1
            pages += 1
    with gzip.open(archives_path, 'wt', encoding='utf-8', compresslevel=1) as out:
        for url, count in sorted(counts.items()):
            out.write(json.dumps(dict(archive=url, pdf_records=count))+'\n')
    result = dict(unique_page_records=pages, unique_archives=len(counts), pages_index=pages_path.name, archives_index=archives_path.name)
    (directory / 'tobb_training_index_summary.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('master', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.master, args.directory)))
