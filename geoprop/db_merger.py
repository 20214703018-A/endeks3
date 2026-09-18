import sys
import os
import sqlite3
import glob

def merge_databases(temp_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    
    # 40 makineden gelen tüm SQLite dosyalarını bul (Örn: temp_dbs/shard-db-1/xyz.sqlite)
    shard_dbs = glob.glob(os.path.join(temp_dir, "**", "*.sqlite"), recursive=True)
    
    if not shard_dbs:
        print("Birleştirilecek hiçbir Shard veritabanı bulunamadı.")
        return

    # Master veritabanı gruplaması (Dosya ismine göre)
    master_dbs = {}
    for shard_path in shard_dbs:
        db_name = os.path.basename(shard_path)
        if db_name not in master_dbs:
            master_dbs[db_name] = []
        master_dbs[db_name].append(shard_path)
        
    for db_name, shard_list in master_dbs.items():
        master_path = os.path.join(out_dir, db_name)
        print(f"\n[🔄] {db_name} Master Ambarına {len(shard_list)} adet shard birleştiriliyor...")
        
        # Eğer master yoksa, ilk shard'ı direkt master olarak kopyala
        if not os.path.exists(master_path):
            import shutil
            shutil.copy2(shard_list[0], master_path)
            shard_list = shard_list[1:]
            
        master_conn = sqlite3.connect(master_path)
        master_cur = master_conn.cursor()
        
        for shard_file in shard_list:
            try:
                # Attach shard DB
                master_cur.execute(f"ATTACH DATABASE '{shard_file}' AS shard_db")
                
                # Shard içindeki tabloları bul
                master_cur.execute("SELECT name FROM shard_db.sqlite_master WHERE type='table';")
                tables = [row[0] for row in master_cur.fetchall() if not row[0].startswith('sqlite_')]
                
                for table in tables:
                    try:
                        # Eğer tablo master'da yoksa oluştur (schema copy)
                        master_cur.execute(f"SELECT sql FROM shard_db.sqlite_master WHERE type='table' AND name='{table}'")
                        schema = master_cur.fetchone()[0]
                        master_cur.execute(schema)
                    except sqlite3.OperationalError:
                        pass # Tablo zaten var
                        
                    # Verileri aktar (INSERT OR REPLACE)
                    try:
                        master_cur.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM shard_db.{table}")
                    except sqlite3.OperationalError as e:
                        print(f"     [!] Tablo uyuşmazlığı atlandı ({table}): {e}")
                        
                master_conn.commit()
                master_cur.execute("DETACH DATABASE shard_db")
            except Exception as e:
                print(f"     [!] Shard bağlanamadı {shard_file}: {e}")
                
        master_conn.close()
        print(f"  ✓ {db_name} başarıyla senkronize edildi.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Kullanım: python db_merger.py <temp_dbs_klasoru> <out_klasoru>")
        sys.exit(1)
        
    temp_directory = sys.argv[1]
    out_directory = sys.argv[2]
    merge_databases(temp_directory, out_directory)
    print("\n✅ TOPTAN SENKRONİZASYON VE MERGE İŞLEMİ TAMAMLANDI!")
