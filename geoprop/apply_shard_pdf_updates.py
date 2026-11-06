#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - 40 Shard PDF Güncelleme Birleştirici (Fast Batch Updater)
===================================================================
Tüm shard runner'larından gelen shard_*_updates.sqlite dosyalarını
master turkiye_ticaret_sicil_ve_sirketler.sqlite veritabanına
SQLite WAL modunda yıldırım hızında uygular.
"""

import sys
import sqlite3
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def merge_pdf_updates(updates_dir, master_db_path):
    master_path = Path(master_db_path)
    if not master_path.exists():
        print(f"❌ Master DB bulunamadı: {master_path}")
        return False
        
    update_files = list(Path(updates_dir).glob("**/shard_*_updates.sqlite"))
    if not update_files:
        print(f"ℹ️ Güncellenecek shard dosyası bulunamadı ({updates_dir}).")
        return True
        
    print(f"🔄 {len(update_files)} adet shard güncelleme veritabanı birleştiriliyor...")
    conn = sqlite3.connect(master_path, timeout=120)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    c = conn.cursor()
    
    total_updated = 0
    total_addr_updated = 0

    for u_file in sorted(update_files):
        try:
            u_conn = sqlite3.connect(f"file:{u_file}?mode=ro", uri=True)
            rows = u_conn.execute("SELECT id, pdf_dosya_yolu, guncellenme_tarihi FROM gazete_pdf_updates").fetchall()
            addr_rows = []
            try:
                addr_rows = u_conn.execute(
                    "SELECT sayi, sayfa, mersis_no, vkn, sicil_no, tam_adres, ilce, sermaye_tl FROM sirket_adres_updates"
                ).fetchall()
            except Exception:
                pass
            u_conn.close()
            
            if rows:
                pages, legacy = {}, []
                for rid, ref, timestamp in rows:
                    filename = ref.rsplit('#', 1)[-1]
                    match = re.fullmatch(r'(\d+)_(\d+)_[A-Za-z0-9]*_[a-f0-9]+\.pdf', filename)
                    if match:
                        key = match.groups()
                        if key not in pages or timestamp >= pages[key][1]:
                            pages[key] = (ref, timestamp)
                    else:
                        legacy.append((ref, timestamp, rid))
                # Page identity remains stable even when local and cloud DBs
                # assign different announcement IDs. Never bind a PDF to an
                # unrelated announcement solely because its numeric ID matches.
                for (sayi, sayfa), (ref, timestamp) in pages.items():
                    c.execute('''UPDATE ticaret_sicil_gazetesi_ilanlari
                        SET pdf_dosya_yolu=?, guncellenme_tarihi=?
                        WHERE gazete_sayisi=? AND sayfa_no=?''',
                        (ref, timestamp, sayi, sayfa))
                    if c.rowcount == 0:
                        raise ValueError(f'Archived page absent from master: {sayi}/{sayfa}')
                    total_updated += c.rowcount
                c.executemany('''UPDATE ticaret_sicil_gazetesi_ilanlari
                    SET pdf_dosya_yolu=?, guncellenme_tarihi=? WHERE id=?''', legacy)
                total_updated += len(legacy)

            if addr_rows:
                from collector.tobb_pdf_adres_ocr_motoru import apply_extracted_records_to_db
                for ar in addr_rows:
                    rec = {
                        "mersis_no": ar[2],
                        "vkn": ar[3],
                        "sicil_no": ar[4],
                        "tam_adres": ar[5],
                        "ilce": ar[6],
                        "sermaye_tl": ar[7],
                    }
                    total_addr_updated += apply_extracted_records_to_db(conn, [rec], ar[0], ar[1])

            if rows or addr_rows:
                print(f"  ✅ {u_file.name}: +{len(rows)} sayfa bağlandı, +{len(addr_rows)} OCR adres kaydı işlendi.")
        except Exception as e:
            conn.rollback()
            conn.close()
            print(f"❌ {u_file.name} birleştirme hatası; master güncellemeleri geri alındı: {e}")
            return False

    if total_addr_updated:
        from collector.tobb_pdf_adres_ocr_motoru import backfill_vkn_from_mersis
        backfill_vkn_from_mersis(conn)
    conn.commit()
    conn.close()
    print(f"🎉 Toplam {total_updated} gazete PDF yolu ve {total_addr_updated} şirket adresi master ambara işlendi.")
    return True

if __name__ == "__main__":
    up_dir = sys.argv[1] if len(sys.argv) > 1 else "temp_artifacts"
    m_db = sys.argv[2] if len(sys.argv) > 2 else "warehouse/product/turkiye_ticaret_sicil_ve_sirketler.sqlite"
    sys.exit(0 if merge_pdf_updates(up_dir, m_db) else 1)
