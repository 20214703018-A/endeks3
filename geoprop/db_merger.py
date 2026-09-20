"""
GEOPROP shard birleştirici.

40 makineden gelen SQLite dosyalarını (temp_dbs/shard-db-N/xyz.sqlite) dosya adına göre
gruplar ve her grubu out_dir/xyz.sqlite ana ambarına aktarır. Ana ambar zaten varsa
(önceki koşudan indirilmişse) üzerine ekler; yoksa ilk shard'ı kopyalayıp başlar.

Önemli kural — AUTOINCREMENT id sütunları aktarılmaz:
  Her shard kendi tablosunda id=1,2,3... üretir. Eski sürüm `INSERT OR REPLACE ... SELECT *`
  yaptığı için shard 2'nin id=1 satırı shard 1'in id=1 satırını (bambaşka bir işletmeyi)
  siliyordu; 40 shard birleşince verinin büyük kısmı kayboluyordu. Artık tablo başka bir
  UNIQUE anahtara sahipse rowid takma adı olan `id` sütunu SELECT listesinden çıkarılır ve
  ana ambar kendi id'sini üretir; çakışma yalnızca gerçek iş anahtarında (google_place_id,
  ilan_id, arama_terimi ...) olur.
"""
import sys
import os
import sqlite3
import glob
import shutil


def _table_columns(cur, schema, table):
    return cur.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()  # cid,name,type,notnull,dflt,pk


def _has_other_unique_key(cur, schema, table, pk_cols):
    """Tabloda (rowid dışında) tekil bir iş anahtarı var mı?"""
    if len(pk_cols) > 1:
        return True
    for _seq, name, unique, _origin, _partial in cur.execute(f'PRAGMA {schema}.index_list("{table}")').fetchall():
        if unique:
            cols = [r[2] for r in cur.execute(f'PRAGMA {schema}.index_info("{name}")').fetchall()]
            if cols and cols != pk_cols:
                return True
    return False


def _transfer_columns(cur, table):
    """Shard -> master aktarımında kullanılacak ortak sütunlar (rowid takma adı hariç)."""
    master_cols = _table_columns(cur, "main", table)
    shard_cols = {c[1] for c in _table_columns(cur, "shard_db", table)}
    pk_cols = [c[1] for c in master_cols if c[5]]
    rowid_alias = None
    if len(pk_cols) == 1:
        col = next(c for c in master_cols if c[1] == pk_cols[0])
        if col[2].upper() == "INTEGER":
            rowid_alias = col[1]
    skip = set()
    if rowid_alias and _has_other_unique_key(cur, "main", table, pk_cols):
        skip.add(rowid_alias)
    return [c[1] for c in master_cols if c[1] in shard_cols and c[1] not in skip]


def merge_databases(temp_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    shard_dbs = sorted(glob.glob(os.path.join(temp_dir, "**", "*.sqlite"), recursive=True))
    if not shard_dbs:
        print("Birleştirilecek hiçbir Shard veritabanı bulunamadı.")
        return

    master_dbs = {}
    for shard_path in shard_dbs:
        master_dbs.setdefault(os.path.basename(shard_path), []).append(shard_path)

    for db_name, shard_list in master_dbs.items():
        master_path = os.path.join(out_dir, db_name)
        print(f"\n[🔄] {db_name}: {len(shard_list)} shard ana ambara birleştiriliyor...")

        if not os.path.exists(master_path):
            shutil.copy2(shard_list[0], master_path)
            shard_list = shard_list[1:]
            print(f"     (ana ambar yoktu; ilk shard başlangıç olarak kopyalandı)")

        master_conn = sqlite3.connect(master_path)
        cur = master_conn.cursor()
        toplam_eklenen = 0

        for shard_file in shard_list:
            try:
                cur.execute("ATTACH DATABASE ? AS shard_db", (shard_file,))
                tables = [r[0] for r in cur.execute(
                    "SELECT name FROM shard_db.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    try:
                        schema = cur.execute(
                            "SELECT sql FROM shard_db.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                        cur.execute(schema)  # master'da yoksa oluştur
                    except sqlite3.OperationalError:
                        pass
                    try:
                        cols = _transfer_columns(cur, table)
                        if not cols:
                            continue
                        col_list = ", ".join(f'"{c}"' for c in cols)
                        before = master_conn.total_changes
                        cur.execute(f'INSERT OR REPLACE INTO "{table}" ({col_list}) SELECT {col_list} FROM shard_db."{table}"')
                        toplam_eklenen += master_conn.total_changes - before
                    except sqlite3.OperationalError as e:
                        print(f"     [!] Tablo uyuşmazlığı atlandı ({table}): {e}")
                master_conn.commit()
                cur.execute("DETACH DATABASE shard_db")
            except Exception as e:
                print(f"     [!] Shard bağlanamadı {shard_file}: {e}")
                try:
                    cur.execute("DETACH DATABASE shard_db")
                except Exception:
                    pass

        # Ana ambar indeksleri (arama geçmişi ve ilan anahtarı) mevcut tablo tanımından gelir; VACUUM ile sıkıştır.
        try:
            master_conn.execute("VACUUM")
        except Exception:
            pass
        master_conn.close()
        print(f"  ✓ {db_name} senkronize edildi (+{toplam_eklenen:,} satır işlendi, {os.path.getsize(master_path)/1048576:.1f} MB).")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Kullanım: python db_merger.py <temp_dbs_klasoru> <out_klasoru>")
        sys.exit(1)
    merge_databases(sys.argv[1], sys.argv[2])
    print("\n✅ TOPTAN SENKRONİZASYON VE MERGE İŞLEMİ TAMAMLANDI!")
