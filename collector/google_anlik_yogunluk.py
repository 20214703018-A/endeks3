#!/usr/bin/env python3
"""
GOOGLE ANLIK YOĞUNLUK — seçili işletmelerde Google'ın o anki yoğunluk ölçümünü kaydeder (zaman serisi).

Google, işletme detay yanıtında (maps/preview/place) [84] alanında:
  [84][7] = [saat, yüzde]   → o anki (canlı) yoğunluk, ör. [19, 69] = saat 19'da %69
  [84][6] = "Biraz yoğun"   → canlı yoğunluk etiketi
  [84][0] = haftalık tipik profil (gün → saat → yüzde)
verir. Canlı değer yalnız o an görünür; geçmişi hiçbir yerden geri alınamaz → düzenli ölçüm gerekir.

Kurallar: değer uydurulmaz; [84] yoksa "populer_yok", yanıt kısaltılmışsa (Google aynı IP'ye zayıf yanıt verir)
"zayif_yanit" olarak işaretlenir ve o işletme "yoğunluk yok" sayılmaz. Haftalık profil yorumlanmadan ham JSON
olarak haftada bir saklanır (gün numaralarının anlamı varsayılmaz).

Kullanım: google_anlik_yogunluk.py --hedef hedef.csv --out shard.sqlite --shard 1 --num-shards 6 --max-seconds 2700
"""
import argparse, csv, gzip, hashlib, json, os, random, sqlite3, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import google_detay_toplayici as d

TR = ZoneInfo("Europe/Istanbul")


def init_db(path):
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE IF NOT EXISTS google_anlik_yogunluk (
        olcum_id TEXT PRIMARY KEY,          -- feature_id|olcum_utc
        feature_id TEXT NOT NULL, google_place_id TEXT, isim TEXT, il TEXT, ilce TEXT, kategori TEXT, lat REAL, lon REAL,
        olcum_utc TEXT NOT NULL, olcum_tr TEXT NOT NULL, tr_gun INTEGER, tr_saat INTEGER,
        durum TEXT NOT NULL,                -- canli · canli_yok (profil var, o an canlı değer yok) · populer_yok · zayif_yanit · hata
        canli_saat INTEGER, canli_yuzde INTEGER, canli_etiket TEXT, kosu_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS google_tipik_yogunluk_profil (
        feature_id TEXT NOT NULL, iso_hafta TEXT NOT NULL, profil_json TEXT NOT NULL, kayit_utc TEXT,
        PRIMARY KEY (feature_id, iso_hafta))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_anlik_fid ON google_anlik_yogunluk(feature_id, olcum_utc)")
    c.commit()
    return c


def hedefleri_oku(path, shard, n):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fid = r.get("feature_id")
            if fid and int(hashlib.sha256(fid.encode()).hexdigest()[:8], 16) % n == shard - 1:
                yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hedef", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=1); ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--max-seconds", type=int, default=2700); ap.add_argument("--kosu-id", default=os.environ.get("GITHUB_RUN_ID", "yerel"))
    a = ap.parse_args()
    c = init_db(a.out)
    hedef = list(hedefleri_oku(a.hedef, a.shard, a.num_shards))
    random.shuffle(hedef)   # süre biterse hep aynı işletmeler eksik kalmasın
    print(f"shard {a.shard}/{a.num_shards}: {len(hedef):,} işletme", flush=True)
    t0 = time.time(); sayac = {}; zayif_seri = 0
    for i, r in enumerate(hedef, 1):
        if time.time() - t0 > a.max_seconds:
            print(f"  süre doldu ({i-1:,}/{len(hedef):,})", flush=True); break
        now = datetime.now(timezone.utc); tr = now.astimezone(TR)
        durum, canli = "hata", (None, None, None); profil = None
        try:
            raw = d.fetch_detail(r["feature_id"], r["lat"], r["lon"])
            data = json.loads(raw[4:] if raw.startswith(")]}'") else raw)
            ent = d._find_entity(data, r["feature_id"])
            pt = d._g(ent, 84) if ent else None
            if ent is None or (pt is None and len(ent) < 150):
                durum = "zayif_yanit"          # kısaltılmış yanıt: işletmenin yoğunluğu yok sayılmaz
            elif pt is None:
                durum = "populer_yok"
            else:
                profil = pt[0] if len(pt) > 0 else None
                cv = pt[7] if len(pt) > 7 else None
                if isinstance(cv, list) and len(cv) >= 2 and isinstance(cv[1], (int, float)):
                    durum, canli = "canli", (int(cv[0]) if isinstance(cv[0], (int, float)) else None, int(cv[1]),
                                             pt[6] if len(pt) > 6 and isinstance(pt[6], str) else None)
                else:
                    durum = "canli_yok"
        except Exception as e:
            durum = "hata"
        sayac[durum] = sayac.get(durum, 0) + 1
        c.execute("INSERT OR REPLACE INTO google_anlik_yogunluk VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            f"{r['feature_id']}|{now.strftime('%Y-%m-%dT%H:%M')}", r["feature_id"], r.get("google_place_id"), r.get("isim"),
            r.get("il"), r.get("ilce"), r.get("kategori"), float(r["lat"]), float(r["lon"]),
            now.isoformat(timespec="seconds"), tr.isoformat(timespec="seconds"), tr.isoweekday(), tr.hour,
            durum, canli[0], canli[1], canli[2], a.kosu_id))
        if profil is not None:
            y, w, _ = tr.isocalendar()
            c.execute("INSERT OR IGNORE INTO google_tipik_yogunluk_profil VALUES (?,?,?,?)",
                      (r["feature_id"], f"{y}-W{w:02d}", json.dumps(profil, ensure_ascii=False, separators=(",", ":")),
                       now.isoformat(timespec="seconds")))
        if i % 50 == 0:
            c.commit()
            print(f"  {i:,}/{len(hedef):,} · {sayac}", flush=True)
        # zayıf yanıt serisi: Google bu IP'ye kısaltılmış yanıt veriyor → yavaşla
        zayif_seri = zayif_seri + 1 if durum in ("zayif_yanit", "hata") else 0
        if zayif_seri >= 15:
            print(f"  [!] art arda {zayif_seri} zayıf yanıt — 90 sn bekleniyor", flush=True)
            time.sleep(90); zayif_seri = 0
        time.sleep(random.uniform(0.5, 1.1))
    c.commit(); c.close()
    print(f"bitti: {sum(sayac.values()):,} ölçüm · {sayac}", flush=True)


if __name__ == "__main__":
    main()
