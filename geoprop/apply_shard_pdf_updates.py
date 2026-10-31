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
import glob
import sqlite3
from pathlib import Path

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
    for u_file in sorted(update_files):
        try:
            u_conn = sqlite3.connect(u_file)
            rows = u_conn.execute("SELECT id, pdf_dosya_yolu, guncellenme_tarihi FROM gazete_pdf_updates").fetchall()
            u_conn.close()
            
            if rows:
                c.executemany("""
                    UPDATE ticaret_sicil_gazetesi_ilanlari
                    SET pdf_dosya_yolu = ?, guncellenme_tarihi = ?
                    WHERE id = ?
                """, [(r[1], r[2], r[0]) for r in rows])
                total_updated += len(rows)
                print(f"  ✅ {u_file.name}: +{len(rows)} sayfa bağlandı.")
        except Exception as e:
            print(f"  ⚠️ {u_file.name} okuma hatası: {e}")
            
    conn.commit()
    conn.close()
    print(f"🎉 Toplam {total_updated} gazete PDF yolu master ambara kalıcı olarak işlendi.")
    return True

if __name__ == "__main__":
    up_dir = sys.argv[1] if len(sys.argv) > 1 else "temp_artifacts"
    m_db = sys.argv[2] if len(sys.argv) > 2 else "warehouse/product/turkiye_ticaret_sicil_ve_sirketler.sqlite"
    merge_pdf_updates(up_dir, m_db)
