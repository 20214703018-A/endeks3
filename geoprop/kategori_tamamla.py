#!/usr/bin/env python3
"""
KATEGORİ TAMAMLA — ana ambardaki her işletmeye, Google kategori kimliğinden (gcid listesinin ilki = asıl tür) Google'ın
Türkçe kategori adını yazar. Kaynak: gcid_kategori_cift tablosu (toplayıcı ve gcid_sozluk.py Google yanıtlarından doldurur).
Ham sütunlar (ana_kategori, tum_kategoriler) DEĞİŞMEZ; sonuç ayrı sütunlarda:
  google_kategori_tr  — Google'ın Türkçe asıl tür adı (ör. "Gece kulübü", "Pub", "Noter")
  kategori_kaynagi    — 'gcid_sozluk' (kimlik sözlükten çözüldü) · 'kimlik_yok' (kayıtta gcid yok; yeniden tarama gerekir)
                        · 'sozlukte_yok' (gcid var ama Türkçe adı henüz öğrenilmedi)
Kullanım: kategori_tamamla.py <ambar.sqlite>
"""
import sqlite3, sys, time
db = sys.argv[1]; c = sqlite3.connect(db); T = "google_places_ticari_yogunluk"
for col in ("google_kategori_tr TEXT", "kategori_kaynagi TEXT"):
    try: c.execute(f"ALTER TABLE {T} ADD COLUMN {col}")
    except sqlite3.OperationalError: pass
c.execute("""CREATE TABLE IF NOT EXISTS gcid_kategori_cift (gcid TEXT PRIMARY KEY, kategori_tr TEXT NOT NULL,
             ornek_place_id TEXT, son_gorulme TEXT)""")
t0 = time.time()
c.execute(f"""UPDATE {T} SET
    google_kategori_tr = (SELECT kategori_tr FROM gcid_kategori_cift s WHERE s.gcid = json_extract({T}.gcid_kategoriler, '$[0]')),
    kategori_kaynagi = CASE
        WHEN gcid_kategoriler IS NULL OR gcid_kategoriler NOT LIKE '[%' THEN 'kimlik_yok'
        WHEN EXISTS (SELECT 1 FROM gcid_kategori_cift s WHERE s.gcid = json_extract({T}.gcid_kategoriler, '$[0]')) THEN 'gcid_sozluk'
        ELSE 'sozlukte_yok' END""")
c.commit()
c.execute(f"CREATE INDEX IF NOT EXISTS idx_gp_gkat ON {T}(google_kategori_tr)")
n = c.execute(f"SELECT COUNT(*) FROM {T}").fetchone()[0]
print(f"sözlük: {c.execute('SELECT COUNT(*) FROM gcid_kategori_cift').fetchone()[0]:,} kimlik · {round(time.time()-t0)} s")
for k, m in c.execute(f"SELECT kategori_kaynagi, COUNT(*) FROM {T} GROUP BY 1 ORDER BY 2 DESC"):
    print(f"  {k:14s} {m:>10,}  (%{100*m/n:.1f})")
eski = c.execute(f"SELECT COUNT(*) FROM {T} WHERE (ana_kategori IS NULL OR ana_kategori='Ticari Mekan') AND google_kategori_tr IS NOT NULL").fetchone()[0]
print(f"eskiden 'Ticari Mekan' olup artık türü bilinen: {eski:,}")
for k, m in c.execute(f"SELECT google_kategori_tr, COUNT(*) FROM {T} WHERE google_kategori_tr IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8"):
    print(f"    {m:>8,}  {k}")
c.close()
