#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - TOBB OCR Master SQLite Kapsamlı Arama Motoru
=======================================================
Tamamlanmış OCR Master SQLite veritabanı (`turkiye_ticaret_sicil_ve_sirketler.sqlite`)
içindeki tüm tablolarda (`sirketler`, `ticaret_sicil_gazetesi_ilanlari`,
`sirket_bilgi_sayfa_indeksi`, `gazete_kisi_ve_isim_indeksi`, `sirket_ortaklari`,
`sirket_calisanlari`) ve `gazete_sayfa_arsivi.sayfa_metni_zlib` üzerindeki
sıkıştırılmış tüm sayfa tam metinlerinde Türkçe karakter uyumlu arama yapar.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_TR_FOLD_MAP = str.maketrans({
    "ç": "C", "Ç": "C",
    "ğ": "G", "Ğ": "G",
    "ı": "I", "İ": "I", "i": "I", "I": "I",
    "ö": "O", "Ö": "O",
    "ş": "S", "Ş": "S",
    "ü": "U", "Ü": "U",
    "â": "A", "Â": "A",
    "î": "I", "Î": "I",
    "û": "U", "Û": "U",
    "\u0307": "",
})

_SECTOR_HINTS = ("METAL", "MADEN", "HADDE")


def fold_tr(text: Optional[str]) -> str:
    """Türkçe karakterleri büyük ASCII karşılıklarına katlar ve boşlukları normalize eder."""
    if not text:
        return ""
    folded = text.translate(_TR_FOLD_MAP).upper()
    return re.sub(r"\s+", " ", folded).strip()


def _token_regex(token: str, strict_right: bool = False) -> str:
    """Bir arama kelimesi için noktalı kısaltma / Türkçe ek toleranslı regex deseni üretir."""
    esc = re.escape(token)
    right = r"(?![A-Z0-9])" if strict_right else r""
    if token.isalpha() and len(token) == 3:
        dotted = r"\s*\.\s*".join(re.escape(ch) for ch in token) + r"\.?"
        return rf"(?<![A-Z0-9])(?:{esc}|{dotted})(?![A-Z0-9])"
    return rf"(?<![A-Z0-9]){esc}{right}"


def build_query_spec(raw_query: str) -> Dict[str, Any]:
    """Kullanıcı sorgusundan katmanlı arama kuralları oluşturur."""
    folded = fold_tr(raw_query)
    tokens = [t for t in re.findall(r"[A-Z0-9]+", folded) if len(t) >= 2]
    if not tokens:
        raise ValueError(f"Geçersiz sorgu: {raw_query!r}")

    anchor = tokens[0]
    strict_anchor = len(anchor) <= 3
    anchor_pat = re.compile(_token_regex(anchor, strict_right=strict_anchor))
    token_pats = [
        (tok, re.compile(_token_regex(tok, strict_right=(idx == 0 and strict_anchor))))
        for idx, tok in enumerate(tokens)
    ]
    sector_pat = re.compile(r"(?<![A-Z0-9])(?:" + "|".join(_SECTOR_HINTS) + r")")

    return {
        "raw": raw_query.strip(),
        "folded": folded,
        "tokens": tokens,
        "anchor": anchor,
        "anchor_pat": anchor_pat,
        "token_pats": token_pats,
        "sector_pat": sector_pat,
    }


def classify_text(folded_text: str, spec: Dict[str, Any], max_window: int = 320) -> Optional[str]:
    """
    Verilen normalize edilmiş metni sorguyla karşılaştırır.
    Dönüş: 'exact_all_tokens' | 'anchor_with_other_token' | 'anchor_with_sector' | 'anchor_only' | None
    """
    if not folded_text:
        return None
    anchor_matches = list(spec["anchor_pat"].finditer(folded_text))
    if not anchor_matches:
        return None

    other_tokens = spec["token_pats"][1:]
    if not other_tokens:
        return "exact_all_tokens"

    # Metin kısaysa (şirket unvanı / blok özeti) tüm metinde değerlendir;
    # tam gazete sayfasında yakınlık penceresi uygula.
    check_full = len(folded_text) <= 2500
    if check_full:
        present = [tok for tok, pat in other_tokens if pat.search(folded_text)]
        if len(present) == len(other_tokens):
            return "exact_all_tokens"
        if present:
            return "anchor_with_other_token"
        if spec["sector_pat"].search(folded_text):
            return "anchor_with_sector"
        return "anchor_only"

    best = "anchor_only"
    for am in anchor_matches:
        start = max(0, am.start() - 120)
        end = min(len(folded_text), am.end() + max_window)
        win = folded_text[start:end]
        present = [tok for tok, pat in other_tokens if pat.search(win)]
        if len(present) == len(other_tokens):
            return "exact_all_tokens"
        if present:
            best = "anchor_with_other_token"
        elif best == "anchor_only" and spec["sector_pat"].search(win):
            best = "anchor_with_sector"
    return best


def extract_snippets(raw_text: str, spec: Dict[str, Any], radius: int = 260, max_snippets: int = 5) -> List[str]:
    """Ham OCR metninden eşleşen kısımların okunabilir bağlam parçalarını çıkarır."""
    clean_raw = re.sub(r"\s+", " ", (raw_text or "").replace("\u0307", "")).strip()
    clean_folded = fold_tr(clean_raw)
    if len(clean_raw) == len(clean_folded):
        snippets = []
        last_end = -1
        for m in spec["anchor_pat"].finditer(clean_folded):
            s = max(0, m.start() - radius)
            e = min(len(clean_raw), m.end() + radius)
            if s < last_end:
                continue
            snippets.append(clean_raw[s:e])
            last_end = e
            if len(snippets) >= max_snippets:
                break
        return snippets

    hits = []
    for ln in (raw_text or "").splitlines():
        if spec["anchor_pat"].search(fold_tr(ln)):
            hits.append(ln.strip())
            if len(hits) >= max_snippets:
                break
    return hits if hits else [clean_raw[:500]]


def _open_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def collect_db_stats(conn: sqlite3.Connection, tables: set) -> Dict[str, Any]:
    """Veritabanındaki temel satır ve kapsama istatistiklerini toplar."""
    stats: Dict[str, Any] = {}
    if "sirketler" in tables:
        row = conn.execute("""
            SELECT
                COUNT(*) AS toplam_sirket,
                SUM(CASE WHEN kaynak = 'TOBB_PDF_OCR' THEN 1 ELSE 0 END) AS ocr_kokenli_sirket,
                COUNT(DISTINCT UPPER(unvan)) AS tekil_unvan,
                SUM(CASE WHEN mersis_no GLOB '0[0-9]*' AND LENGTH(mersis_no)=16 THEN 1 ELSE 0 END) AS gercek_mersis_sayisi,
                SUM(CASE WHEN vkn GLOB '[0-9]*' AND LENGTH(vkn)=10 THEN 1 ELSE 0 END) AS on_haneli_vkn_sayisi,
                SUM(CASE WHEN tam_adres IS NOT NULL AND LENGTH(TRIM(tam_adres)) >= 8 THEN 1 ELSE 0 END) AS tam_adres_dolu,
                SUM(CASE WHEN sermaye_tl IS NOT NULL AND sermaye_tl > 0 THEN 1 ELSE 0 END) AS sermaye_dolu
            FROM sirketler
        """).fetchone()
        stats["sirketler"] = dict(row)

    for tbl in (
        "ticaret_sicil_gazetesi_ilanlari",
        "sirket_bilgi_sayfa_indeksi",
        "gazete_kisi_ve_isim_indeksi",
        "sirket_ortaklari",
        "sirket_calisanlari",
    ):
        if tbl in tables:
            stats[f"{tbl}_toplam"] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]

    if "sirket_bilgi_sayfa_indeksi" in tables:
        stats["sirket_bilgi_sayfa_indeksi_ocr"] = conn.execute(
            "SELECT COUNT(*) FROM sirket_bilgi_sayfa_indeksi WHERE COALESCE(kaynak_tipi, '') != 'MASTER_INDEX'"
        ).fetchone()[0]

    if "gazete_sayfa_arsivi" in tables:
        row = conn.execute("""
            SELECT
                COUNT(*) AS toplam_sayfa,
                SUM(CASE WHEN ocr_durumu = 1 THEN 1 ELSE 0 END) AS ocr_tamamlanan_sayfa,
                SUM(CASE WHEN sayfa_metni_zlib IS NOT NULL THEN 1 ELSE 0 END) AS metni_dolu_sayfa
            FROM gazete_sayfa_arsivi
        """).fetchone()
        stats["gazete_sayfa_arsivi"] = dict(row)

    return stats


def _init_query_buckets(specs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    buckets = {}
    for sp in specs:
        buckets[sp["raw"]] = {
            "sorgu": sp["raw"],
            "tokens": sp["tokens"],
            "sirketler": [],
            "sirketler_anchor_only_sayisi": 0,
            "ticaret_sicil_gazetesi_ilanlari": [],
            "ilanlar_anchor_only_sayisi": 0,
            "sirket_bilgi_sayfa_indeksi": [],
            "sayfa_indeksi_anchor_only_sayisi": 0,
            "gazete_kisi_ve_isim_indeksi": [],
            "kisi_indeksi_anchor_only_sayisi": 0,
            "gazete_sayfa_arsivi_eslesmeleri": [],
            "sayfa_arsivi_anchor_only_sayisi": 0,
            "iliskili_diger_tablolar": [],
        }
    return buckets


def _scan_table_single_pass(
    conn: sqlite3.Connection,
    sql: str,
    text_fields: List[str],
    specs: List[Dict[str, Any]],
    buckets: Dict[str, Dict[str, Any]],
    bucket_key: str,
    count_key: str,
    anchor_cap: int,
    always_keep_anchor_if_short: bool = True,
) -> None:
    cur = conn.execute(sql)
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        row_keys = None
        for row in batch:
            if row_keys is None:
                row_keys = set(row.keys())
            combined_parts = [str(row[f] or "") for f in text_fields if f in row_keys]
            folded = fold_tr(" ".join(combined_parts))
            if not folded:
                continue
            for sp in specs:
                if len(sp["anchor"]) > 3 and sp["anchor"] not in folded:
                    continue
                tier = classify_text(folded, sp)
                if not tier:
                    continue
                b = buckets[sp["raw"]]
                if tier in ("anchor_only", "anchor_with_sector"):
                    if tier == "anchor_only":
                        b[count_key] += 1
                    if (tier == "anchor_only" and not always_keep_anchor_if_short) or len(b[bucket_key]) >= anchor_cap:
                        continue
                d = dict(row)
                d["_eslesme_seviyesi"] = tier
                b[bucket_key].append(d)


def _scan_archive_worker(args: Tuple[str, int, int, List[str], int]) -> Dict[str, Any]:
    """İşçi süreç: kendi rowid dilimindeki zlib OCR sayfa metinlerini tarar."""
    db_path_str, worker_id, num_workers, raw_queries, anchor_cap = args
    specs = [build_query_spec(q) for q in raw_queries]
    conn = _open_ro(Path(db_path_str))
    hits: Dict[str, List[Dict[str, Any]]] = {q: [] for q in raw_queries}
    anchor_only_counts: Dict[str, int] = {q: 0 for q in raw_queries}
    pages_scanned = 0

    cur = conn.execute(
        """
        SELECT gazete_sayisi, sayfa_no, yayin_tarihi, tsm_adi, archive_url, pdf_member, ocr_durumu, sayfa_metni_zlib
        FROM gazete_sayfa_arsivi
        WHERE sayfa_metni_zlib IS NOT NULL AND (rowid % ?) = ?
        """,
        (num_workers, worker_id),
    )
    while True:
        batch = cur.fetchmany(2000)
        if not batch:
            break
        for row in batch:
            pages_scanned += 1
            blob = row["sayfa_metni_zlib"]
            try:
                raw_text = zlib.decompress(blob).decode("utf-8", errors="ignore")
            except Exception:
                continue
            folded = fold_tr(raw_text)
            for sp in specs:
                if len(sp["anchor"]) > 3 and sp["anchor"] not in folded:
                    continue
                tier = classify_text(folded, sp)
                if not tier:
                    continue
                q_key = sp["raw"]
                if tier in ("anchor_only", "anchor_with_sector"):
                    if tier == "anchor_only":
                        anchor_only_counts[q_key] += 1
                    if len(hits[q_key]) >= anchor_cap:
                        continue
                hits[q_key].append({
                    "_eslesme_seviyesi": tier,
                    "gazete_sayisi": row["gazete_sayisi"],
                    "sayfa_no": row["sayfa_no"],
                    "yayin_tarihi": row["yayin_tarihi"],
                    "tsm_adi": row["tsm_adi"],
                    "archive_url": row["archive_url"],
                    "pdf_member": row["pdf_member"],
                    "ocr_durumu": row["ocr_durumu"],
                    "eslesen_parcalar": extract_snippets(raw_text, sp),
                    "sayfa_metni_karakter": len(raw_text),
                    "sayfa_tam_metni": raw_text if (tier in ("exact_all_tokens", "anchor_with_other_token") and len(hits[q_key]) < 50) else None,
                })
    conn.close()
    return {
        "pages_scanned": pages_scanned,
        "hits": hits,
        "anchor_only_counts": anchor_only_counts,
    }


def _enrich_linked_records(
    conn: sqlite3.Connection,
    tables: set,
    buckets: Dict[str, Dict[str, Any]],
) -> None:
    """Bulunan sayfa veya MERSİS/VKN/sicil kayıtlarıyla ilişkili ek satırları toplar."""
    for b in buckets.values():
        page_keys = set()
        company_keys = set()
        for src in (b["sirket_bilgi_sayfa_indeksi"], b["gazete_sayfa_arsivi_eslesmeleri"], b["ticaret_sicil_gazetesi_ilanlari"]):
            for item in src:
                if item.get("_eslesme_seviyesi") in ("exact_all_tokens", "anchor_with_other_token", "anchor_with_sector"):
                    if item.get("gazete_sayisi") and item.get("sayfa_no"):
                        page_keys.add((str(item["gazete_sayisi"]), str(item["sayfa_no"])))
        for item in b["sirketler"] + b["sirket_bilgi_sayfa_indeksi"]:
            if item.get("_eslesme_seviyesi") in ("exact_all_tokens", "anchor_with_other_token", "anchor_with_sector"):
                if item.get("mersis_no") and not str(item["mersis_no"]).startswith(("TOBB_", "ITO_")):
                    company_keys.add(("mersis", str(item["mersis_no"])))
                if item.get("vkn") and len(str(item["vkn"]).strip()) == 10:
                    company_keys.add(("vkn", str(item["vkn"]).strip()))

        page_people = []
        if "gazete_kisi_ve_isim_indeksi" in tables and page_keys:
            seen_ids = {x.get("id") for x in b["gazete_kisi_ve_isim_indeksi"] if x.get("id")}
            for gs, pn in sorted(page_keys)[:80]:
                rows = conn.execute(
                    "SELECT * FROM gazete_kisi_ve_isim_indeksi WHERE gazete_sayisi = ? AND sayfa_no = ?",
                    (gs, pn),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    if d.get("id") not in seen_ids:
                        seen_ids.add(d.get("id"))
                        page_people.append(d)
        b["eslesen_sayfalardaki_kisiler"] = page_people

        for tbl in ("sirket_ortaklari", "sirket_calisanlari"):
            if tbl in tables and company_keys:
                extra_rows = []
                for kind, val in sorted(company_keys)[:40]:
                    col = "mersis_no" if kind == "mersis" else "vkn"
                    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
                    if col in cols:
                        for r in conn.execute(f"SELECT * FROM {tbl} WHERE {col} = ? LIMIT 100", (val,)).fetchall():
                            d = dict(r)
                            d["_kaynak_tablo"] = tbl
                            extra_rows.append(d)
                b["iliskili_diger_tablolar"].extend(extra_rows)


def search_ocr_database(
    db_path: Path,
    raw_queries: List[str],
    include_stats: bool = True,
    scan_full_text: bool = True,
    workers: int = 4,
    anchor_cap: int = 40,
    ocr_only: bool = True,
) -> Dict[str, Any]:
    """OCR Master SQLite veritabanında çok katmanlı arama yürütür."""
    t0 = time.time()
    specs = [build_query_spec(q) for q in raw_queries]
    buckets = _init_query_buckets(specs)

    conn = _open_ro(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    result: Dict[str, Any] = {
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else None,
        "ocr_only": ocr_only,
        "tablolar": sorted(tables),
        "istatistikler": collect_db_stats(conn, tables) if include_stats else None,
        "sorgular": buckets,
        "taranan_sayfa_tam_metni": 0,
    }

    if "sirketler" in tables:
        sirketler_sql = "SELECT * FROM sirketler WHERE kaynak = 'TOBB_PDF_OCR'" if ocr_only else "SELECT * FROM sirketler"
        _scan_table_single_pass(
            conn,
            sirketler_sql,
            ["unvan", "faaliyet_konusu", "tam_adres"],
            specs, buckets, "sirketler", "sirketler_anchor_only_sayisi", anchor_cap,
        )

    if (not ocr_only) and "ticaret_sicil_gazetesi_ilanlari" in tables:
        _scan_table_single_pass(
            conn,
            "SELECT * FROM ticaret_sicil_gazetesi_ilanlari",
            ["unvan", "ilan_metni"],
            specs, buckets, "ticaret_sicil_gazetesi_ilanlari", "ilanlar_anchor_only_sayisi", anchor_cap,
        )

    if "sirket_bilgi_sayfa_indeksi" in tables:
        sbsi_sql = (
            "SELECT * FROM sirket_bilgi_sayfa_indeksi WHERE COALESCE(kaynak_tipi, '') != 'MASTER_INDEX'"
            if ocr_only
            else "SELECT * FROM sirket_bilgi_sayfa_indeksi"
        )
        _scan_table_single_pass(
            conn,
            sbsi_sql,
            ["unvan", "bilgi_ozeti", "yetkililer_json"],
            specs, buckets, "sirket_bilgi_sayfa_indeksi", "sayfa_indeksi_anchor_only_sayisi", anchor_cap,
        )

    if "gazete_kisi_ve_isim_indeksi" in tables:
        _scan_table_single_pass(
            conn,
            "SELECT * FROM gazete_kisi_ve_isim_indeksi",
            ["bagli_unvan", "kisi_adi_soyadi", "cumle_baglami"],
            specs, buckets, "gazete_kisi_ve_isim_indeksi", "kisi_indeksi_anchor_only_sayisi", anchor_cap,
        )

    conn.close()

    if scan_full_text and "gazete_sayfa_arsivi" in tables:
        w = max(1, min(workers, os.cpu_count() or 1))
        if w == 1:
            out = _scan_archive_worker((str(db_path), 0, 1, raw_queries, anchor_cap))
            worker_outs = [out]
        else:
            tasks = [(str(db_path), i, w, raw_queries, anchor_cap) for i in range(w)]
            with ProcessPoolExecutor(max_workers=w) as ex:
                worker_outs = list(ex.map(_scan_archive_worker, tasks))
        for wo in worker_outs:
            result["taranan_sayfa_tam_metni"] += wo["pages_scanned"]
            for q in raw_queries:
                buckets[q]["sayfa_arsivi_anchor_only_sayisi"] += wo["anchor_only_counts"][q]
                for item in wo["hits"][q]:
                    if item["_eslesme_seviyesi"] not in ("anchor_only", "anchor_with_sector") or len(buckets[q]["gazete_sayfa_arsivi_eslesmeleri"]) < anchor_cap:
                        buckets[q]["gazete_sayfa_arsivi_eslesmeleri"].append(item)

        # Sıralama: tam eşleşmeler üstte, sonra kronolojik
        tier_rank = {"exact_all_tokens": 0, "anchor_with_other_token": 1, "anchor_with_sector": 2, "anchor_only": 3}
        for q in raw_queries:
            buckets[q]["gazete_sayfa_arsivi_eslesmeleri"].sort(
                key=lambda x: (
                    tier_rank.get(x.get("_eslesme_seviyesi", "anchor_only"), 9),
                    str(x.get("yayin_tarihi") or ""),
                    str(x.get("gazete_sayisi") or ""),
                    str(x.get("sayfa_no") or ""),
                )
            )

    conn2 = _open_ro(db_path)
    _enrich_linked_records(conn2, tables if not ocr_only else (tables - {"sirket_ortaklari", "sirket_calisanlari"}), buckets)
    conn2.close()

    result["sure_saniye"] = round(time.time() - t0, 2)
    return result


def render_markdown_report(data: Dict[str, Any]) -> str:
    """Arama sonuçlarını okunabilir özet Markdown rapora dönüştürür."""
    lines = ["# TOBB OCR Master SQLite Sorgu Raporu", ""]
    lines.append(f"- Süre: `{data.get('sure_saniye')}s` | Taranan tam OCR sayfası: `{data.get('taranan_sayfa_tam_metni')}`")
    if data.get("istatistikler"):
        lines.append(f"- Veritabanı İstatistikleri: `{json.dumps(data['istatistikler'], ensure_ascii=False)}`")
    lines.append("")

    for q, b in data["sorgular"].items():
        lines.append(f"## Sorgu: `{q}`")
        for key in ("sirketler", "ticaret_sicil_gazetesi_ilanlari", "sirket_bilgi_sayfa_indeksi", "gazete_kisi_ve_isim_indeksi", "gazete_sayfa_arsivi_eslesmeleri"):
            items = b[key]
            exact_cnt = sum(1 for x in items if x.get("_eslesme_seviyesi") == "exact_all_tokens")
            near_cnt = sum(1 for x in items if x.get("_eslesme_seviyesi") == "anchor_with_other_token")
            sec_cnt = sum(1 for x in items if x.get("_eslesme_seviyesi") == "anchor_with_sector")
            anc_cnt = sum(1 for x in items if x.get("_eslesme_seviyesi") == "anchor_only")
            lines.append(f"- `{key}`: exact={exact_cnt}, near={near_cnt}, sector={sec_cnt}, anchor_shown={anc_cnt}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="TOBB OCR Master SQLite Kapsamlı Arama")
    parser.add_argument("--db-path", required=True, help="SQLite veritabanı dosya yolu")
    parser.add_argument("--queries", required=True, help="Virgülle ayrılmış arama terimleri")
    parser.add_argument("--no-stats", action="store_true", help="Genel veritabanı sayılarını hesaplama")
    parser.add_argument("--no-full-text", action="store_true", help="gazete_sayfa_arsivi zlib tam metin taramasını atla")
    parser.add_argument("--include-pre-ocr", action="store_true", help="OCR öncesi HTML katalog kayıtlarını da dahil et (varsayılan: yalnızca OCR verileri)")
    parser.add_argument("--workers", type=int, default=4, help="Tam metin taraması için paralel süreç sayısı")
    parser.add_argument("--anchor-cap", type=int, default=40, help="Yalnızca ilk kelime eşleşmelerinde saklanacak örnek limiti")
    parser.add_argument("--output-json", type=str, default="ocr_sorgu_sonuclari.json")
    parser.add_argument("--output-md", type=str, default="ocr_sorgu_raporu.md")
    args = parser.parse_args()

    raw_queries = [q.strip() for q in re.split(r"[,\n;]+", args.queries) if q.strip()]
    data = search_ocr_database(
        db_path=Path(args.db_path),
        raw_queries=raw_queries,
        include_stats=not args.no_stats,
        scan_full_text=not args.no_full_text,
        workers=args.workers,
        anchor_cap=args.anchor_cap,
        ocr_only=not args.include_pre_ocr,
    )

    Path(args.output_json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_markdown_report(data)
    Path(args.output_md).write_text(md, encoding="utf-8")
    print(md, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
