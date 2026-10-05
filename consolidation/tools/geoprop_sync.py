#!/usr/bin/env python3
"""
GEOPROP → PostGIS (L2) senkronu — emlak-ai-app docs/DATABASE_PLAN.md §5, §7, §8.

  disa-aktar : serving snapshot'ta katalog.veri_seti.postgis_kopya = true olan setleri GeoParquet'e (WKB geometri) yazar,
               her set için PostgreSQL DDL + indeks planı (kolon rolüne göre) ve manifest (satır, sha256) üretir.
  yukle      : dışa aktarılmış paketi PostGIS'e yükler — platform.<ad>__new → COPY → indeksler → ANALYZE → tek
               transaction'da yeniden adlandırma (sıfır kesinti); eski tablo silinir. psycopg (v3) gerekir.

İndeks kuralları (rol → PostgreSQL): anahtar → PRIMARY KEY · geo → btree · koordinat/geometri → geography(Point,4326) GiST,
poligon → geometry GiST · zaman → btree (küçük tablo) · metin (ad/unvan/adres) → pg_trgm GIN · boyut → (geo, boyut) birleşik.
Görünürlük: serving snapshot zaten yalnız açık satır/hassas olmayan sütun içerir (QDEC_000004); burada ek süzme yok.
"""
import argparse, hashlib, json, os, sys, time, duckdb
from pathlib import Path

PG_SISTEM = {"xmin", "xmax", "cmin", "cmax", "ctid", "tableoid", "oid"}   # PostgreSQL sistem sütunları: çakışırsa "k_" öneki


def pg_ad(k):
    return f"k_{k}" if k.lower() in PG_SISTEM else k


PG_TIP = {"VARCHAR": "text", "BIGINT": "bigint", "INTEGER": "integer", "DOUBLE": "double precision", "FLOAT": "real",
          "BOOLEAN": "boolean", "DATE": "date", "TIMESTAMP": "timestamp", "TIMESTAMP WITH TIME ZONE": "timestamptz",
          "SMALLINT": "smallint", "HUGEINT": "numeric", "GEOMETRY": "geometry"}


def pg_tip(t):
    t = t.upper()
    if t.startswith("DECIMAL"): return "numeric"
    if t.endswith("[]") or t.startswith(("STRUCT", "MAP")) or t == "JSON": return "jsonb"
    return PG_TIP.get(t, "text")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def disa_aktar(a):
    c = duckdb.connect(a.serving, read_only=True); c.execute("LOAD spatial")
    surum = dict(c.execute("SELECT key, value FROM main._meta").fetchall()).get("model_version")
    out = Path(a.cikti) / f"postgis_{surum}"; out.mkdir(parents=True, exist_ok=True)
    setler = c.execute("SELECT ad, tur, geo_anahtari, zaman_anahtari, satir FROM katalog.veri_seti WHERE postgis_kopya ORDER BY ad").fetchall()
    manifest = {"model_version": surum, "kaynak_serving": os.path.basename(a.serving), "setler": {}}
    for ad, tur, geo, zaman, n in setler:
        kol = c.execute("SELECT kolon, tip, rol FROM katalog.kolon WHERE veri_seti = ? ORDER BY sira", [ad]).fetchall()
        sec = ", ".join(f'ST_AsWKB("{k}") AS "{k}"' if t.upper() == "GEOMETRY" else f'"{k}"' for k, t, r in kol)
        f = out / f"{ad}.parquet"
        c.execute(f"COPY (SELECT {sec} FROM main.{ad}) TO '{f}' (FORMAT parquet, COMPRESSION zstd)")
        # DDL + indeks planı
        sut, idx = [], []
        pk = kol[0][0] if kol and kol[0][2] == "anahtar" else None
        for k, t, r in kol:
            if t.upper() == "GEOMETRY":
                gt = "geography(Point,4326)" if tur == "nokta" else "geometry(Geometry,4326)"
                sut.append(f'"{pg_ad(k)}" {gt}')
                idx.append(f'CREATE INDEX ON platform.{ad}__new USING gist ("{k}");')
            else:
                sut.append(f'"{pg_ad(k)}" {pg_tip(t)}')
            if r == "geo": idx.append(f'CREATE INDEX ON platform.{ad}__new ("{k}");')
            if r == "metin" and k in ("ad", "unvan", "adres", "isletme_adi"): idx.append(f'CREATE INDEX ON platform.{ad}__new USING gin ("{k}" gin_trgm_ops);')
            if r == "zaman" and n <= 1_000_000: idx.append(f'CREATE INDEX ON platform.{ad}__new ("{k}");')
        if pk: idx.insert(0, f'ALTER TABLE platform.{ad}__new ADD PRIMARY KEY ("{pk}");')
        ddl = f"CREATE TABLE platform.{ad}__new (\n  " + ",\n  ".join(sut) + "\n);"
        (out / f"{ad}.sql").write_text(ddl + "\n" + "\n".join(idx) + "\n", encoding="utf-8")
        manifest["setler"][ad] = {"satir": n, "tur": tur, "parquet": f.name, "sha256": sha(f), "bayt": f.stat().st_size,
                                  "geometri_kolonlari": [k for k, t, r in kol if t.upper() == "GEOMETRY"], "indeks_sayisi": len(idx)}
        print(f"  {ad:22s} {n:>10,} satır · {f.stat().st_size/1e6:7.1f} MB · {len(idx)} indeks", flush=True)
    json.dump(manifest, open(out / "manifest.json", "w"), ensure_ascii=False, indent=1)
    print(f"paket: {out} · {len(setler)} set")


def yukle(a):
    import psycopg
    paket = Path(a.paket); m = json.load(open(paket / "manifest.json"))
    d = duckdb.connect(); d.execute("LOAD spatial")
    with psycopg.connect(a.dsn, autocommit=False) as pg:
        cur = pg.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE SCHEMA IF NOT EXISTS platform;")
        pg.commit()
        for ad, s in m["setler"].items():
            t0 = time.time(); f = paket / s["parquet"]
            if sha(f) != s["sha256"]: sys.exit(f"{ad}: sha256 uyuşmadı — paket bozuk")
            sql = (paket / f"{ad}.sql").read_text().split("\n"); ddl = []; idx = []
            for satir in sql:
                (idx if satir.startswith(("CREATE INDEX", "ALTER TABLE")) else ddl).append(satir)
            cur.execute(f"DROP TABLE IF EXISTS platform.{ad}__new"); cur.execute("\n".join(ddl))
            kols = [r[0] for r in d.execute(f"DESCRIBE SELECT * FROM read_parquet('{f}')").fetchall()]
            geo = set(s["geometri_kolonlari"])
            # WKB → hex; PostGIS COPY metin kipinde hex-WKB'yi geometry/geography olarak kabul eder
            sec = ", ".join(f'hex("{k}")' if k in geo else f'"{k}"' for k in kols)
            with cur.copy(f"COPY platform.{ad}__new ({', '.join(chr(34)+pg_ad(k)+chr(34) for k in kols)}) FROM STDIN") as cp:
                for row in d.execute(f"SELECT {sec} FROM read_parquet('{f}')").fetchall():
                    cp.write_row(row)
            for i in idx:
                if i.strip(): cur.execute(i)
            cur.execute(f"ANALYZE platform.{ad}__new")
            cur.execute(f"DROP TABLE IF EXISTS platform.{ad}__old")
            cur.execute(f"ALTER TABLE IF EXISTS platform.{ad} RENAME TO {ad}__old")
            cur.execute(f"ALTER TABLE platform.{ad}__new RENAME TO {ad}")
            cur.execute(f"DROP TABLE IF EXISTS platform.{ad}__old")
            pg.commit()
            print(f"  {ad:22s} {s['satir']:>10,} satır yüklendi · {time.time()-t0:.0f} sn", flush=True)
        cur.execute("CREATE TABLE IF NOT EXISTS platform._senkron (model_version text, yuklendi timestamptz default now(), manifest jsonb)")
        cur.execute("INSERT INTO platform._senkron (model_version, manifest) VALUES (%s, %s)", (m["model_version"], json.dumps(m)))
        pg.commit()
    print("yükleme tamam:", m["model_version"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="komut", required=True)
    x = sp.add_parser("disa-aktar"); x.add_argument("--serving", required=True); x.add_argument("--cikti", required=True)
    y = sp.add_parser("yukle"); y.add_argument("--paket", required=True); y.add_argument("--dsn", required=True)
    a = ap.parse_args(); disa_aktar(a) if a.komut == "disa-aktar" else yukle(a)
