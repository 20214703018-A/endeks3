#!/usr/bin/env python3
"""
ZAMAN SERİSİ DELTA / DURUM — açık depoda toplama, özel depoda birikim (16 Eki 2026).

Toplama endeks3'te (açık depo, GitHub'ın ücretsiz makineleri) yapılır; birikimli veri yalnız özel depoda (geoprop-ambar release zaman-serisi) durur.
Açık depodaki iş birikimi göremez; bu yüzden:
  durum  : her sqlite'ın tablo başına SON 200 bin satırı (kısa ömürlü iş çıktısı) — toplayıcılar "kaldığı yerden" devam edebilsin
           (kur/deprem max(tarih), hisse kod başına son gün, uçuş ekranı önceki görüntü).
  delta  : bu koşuda EKLENEN satırlar (rowid > koşu başındaki en büyük rowid). Özel depodaki birleştirici yalnız bunları ekler.
Komutlar:
  delta.py taban  <db_dizini> <taban.json>                         koşu başında tablo başına en büyük rowid
  delta.py ayir   <db_dizini> <taban.json> <delta_dizini> <durum_dizini> [--tut 200000]
  delta.py ekle   <birikim.sqlite> <delta.sqlite>                   (özel depo) delta satırlarını birikime ekler; birincil anahtar/benzersizlik
                                                                    varsa INSERT OR REPLACE, yoksa INSERT. Döner: eklenen satır sayısı.
"""
import glob, json, os, sqlite3, sys


def tablolar(c, sema="main"):
    return [(n, s) for n, s in c.execute(f"SELECT name, sql FROM {sema}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]


def taban(dizin, cikti):
    t = {}
    for f in sorted(glob.glob(os.path.join(dizin, "*.sqlite"))):
        c = sqlite3.connect(f); t[os.path.basename(f)] = {n: (c.execute(f'SELECT max(rowid) FROM "{n}"').fetchone()[0] or 0) for n, _ in tablolar(c)}; c.close()
    json.dump(t, open(cikti, "w")); print(json.dumps({k: sum(v.values()) for k, v in t.items()}))


def kopya_sema(kaynak, hedef, ad):
    for tip, n, s in kaynak.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND (tbl_name = ? OR name = ?) ORDER BY type DESC", (ad, ad)).fetchall():
        try: hedef.execute(s.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1).replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                           .replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1))
        except sqlite3.Error: pass


def ayir(dizin, taban_json, delta_dizin, durum_dizin, tut=200_000):
    t = json.load(open(taban_json)) if os.path.exists(taban_json) and os.path.getsize(taban_json) > 0 else {}
    os.makedirs(delta_dizin, exist_ok=True); os.makedirs(durum_dizin, exist_ok=True); ozet = {}
    for f in sorted(glob.glob(os.path.join(dizin, "*.sqlite"))):
        ad = os.path.basename(f); c = sqlite3.connect(f); ozet[ad] = {}
        for hedef_dizin, kosul in ((delta_dizin, "delta"), (durum_dizin, "durum")):
            h = os.path.join(hedef_dizin, ad)
            if os.path.exists(h): os.remove(h)
            o = sqlite3.connect(h); o.execute(f"ATTACH '{f}' AS k")
            for n, _ in tablolar(c):
                kopya_sema(c, o, n)
                if kosul == "delta":
                    alt = t.get(ad, {}).get(n, 0)
                    o.execute(f'INSERT INTO main."{n}" SELECT * FROM k."{n}" WHERE rowid > ?', (alt,))
                    ozet[ad][n] = o.execute(f'SELECT count(*) FROM main."{n}"').fetchone()[0]
                else:
                    o.execute(f'INSERT INTO main."{n}" SELECT * FROM k."{n}" WHERE rowid > (SELECT coalesce(max(rowid), 0) - ? FROM k."{n}")', (tut,))
            o.commit(); o.execute("DETACH k"); o.close()
        c.close()
        if not any(ozet[ad].values()): os.remove(os.path.join(delta_dizin, ad))   # yeni satır yoksa delta dosyası yok
    print(json.dumps(ozet, ensure_ascii=False))


def ekle(birikim, delta):
    o = sqlite3.connect(birikim); o.execute(f"ATTACH '{delta}' AS d"); d = sqlite3.connect(delta); n_top = 0
    for n, _ in tablolar(d):
        kopya_sema(d, o, n)
        anahtar = any(r[5] for r in o.execute(f'PRAGMA main.table_info("{n}")')) or \
                  any(r[2] for r in o.execute(f'PRAGMA main.index_list("{n}")'))   # birincil anahtar ya da benzersiz indeks
        kol = [r[1] for r in d.execute(f'PRAGMA table_info("{n}")')]
        hedef_kol = {r[1] for r in o.execute(f'PRAGMA main.table_info("{n}")')}
        for k in kol:
            if k not in hedef_kol: o.execute(f'ALTER TABLE main."{n}" ADD COLUMN "{k}"')   # toplayıcı yeni sütun eklediyse
        liste = ", ".join(f'"{k}"' for k in kol)
        once = o.total_changes
        o.execute(f'INSERT {"OR REPLACE " if anahtar else ""}INTO main."{n}" ({liste}) SELECT {liste} FROM d."{n}"')
        n_top += o.total_changes - once
    o.commit(); o.execute("DETACH d"); o.close(); d.close()
    print(n_top); return n_top


if __name__ == "__main__":
    k = sys.argv[1]
    if k == "taban": taban(sys.argv[2], sys.argv[3])
    elif k == "ayir": ayir(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[7]) if len(sys.argv) > 7 and sys.argv[6] == "--tut" else 200_000)
    elif k == "ekle": ekle(sys.argv[2], sys.argv[3])
