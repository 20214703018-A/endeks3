#!/usr/bin/env python3
"""
YENİ VERİ İNCELEME (STANDARD §10.1 "yeni veri giriş kapısı") — aday kaynakları kanoniğe almadan önce profiller.

Her kaynak dosyanın her tablosu için: satır/sütun, tipler, koordinat sütunu varsa Türkiye kutusu içi oranı ve mahalle
poligonuna düşme oranı (örneklem), kimlik adaylarında boş/tekrar oranı, tarih aralığı, kişisel veri şüphesi taşıyan
sütunlar. Hiçbir kaynağa yazmaz (salt okunur). Çıktı: Markdown rapor + JSON (quarantine/ altına) + NEW_DATA_REVIEW kayıtları.

Kullanım: yeni_veri_inceleme.py --kaynaklar kaynaklar.json --canonical canon.duckdb --cikti quarantine/
  kaynaklar.json: [{"ad": "google_mekan", "yol": "/tmp/.../x.sqlite", "aciklama": "...", "hedef": "poi + poi_snapshot"}, ...]
"""
import argparse, json, os, re, time, duckdb
from pathlib import Path

KOORD = [("lat", "lon"), ("enlem", "boylam"), ("latitude", "longitude"), ("lat", "lng"), ("y", "x")]
KIMLIK_RX = re.compile(r"(^id$|_id$|^ilan_id|place_id|feature_id|^cid$|observation_id|olcum_id|yorum_id)", re.I)
TARIH_RX = re.compile(r"(tarih|date|zaman|_at$|^ay$|period|donem|observed|guncel|kayit)", re.I)
HASSAS_RX = re.compile(r"(yazar|author|reviewer|ad_?soyad|full_?name|telefon|phone|e_?mail|eposta|tc_?kimlik|iban|adres_?detay|kullanici)", re.I)
TR_KUTU = (25.5, 35.7, 45.0, 42.3)   # lon_min, lat_min, lon_max, lat_max


def baglan(yol):
    c = duckdb.connect()
    c.execute("INSTALL spatial; LOAD spatial; INSTALL sqlite; LOAD sqlite;")
    return c


def tablolar(c, yol):
    s = str(yol).lower()
    if s.endswith((".sqlite", ".sqlite3", ".db")):
        c.execute(f"ATTACH '{yol}' AS k (TYPE sqlite, READ_ONLY)")
        return [(t, f'k."{t}"') for (t,) in c.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name='k' AND table_name NOT LIKE 'sqlite_%' "
            "AND table_name NOT LIKE '%rtree%' ORDER BY 1").fetchall()]
    if s.endswith(".duckdb"):
        c.execute(f"ATTACH '{yol}' AS k (READ_ONLY)")
        return [(f"{a}.{b}", f'k."{a}"."{b}"') for a, b in c.execute(
            "SELECT table_schema, table_name FROM k.information_schema.tables WHERE table_type='BASE TABLE'").fetchall()]
    if os.path.isdir(yol) or s.endswith(".parquet"):
        desen = f"{yol}/**/*.parquet" if os.path.isdir(yol) else str(yol)
        return [(Path(yol).name, f"read_parquet('{desen}', union_by_name=true)")]
    if s.endswith(".geojson") or s.endswith(".geojson.gz"):
        return [(Path(yol).name, f"(SELECT *, ST_X(ST_Centroid(geom)) AS lon, ST_Y(ST_Centroid(geom)) AS lat FROM ST_Read('{yol}'))")]
    if s.endswith(".xlsx"):
        return [(Path(yol).name, f"read_xlsx('{yol}', all_varchar=true, ignore_errors=true)")]
    if s.endswith(".xls"):
        import pandas as pd
        df = pd.read_excel(yol, sheet_name=0, dtype=str); ad = "xls_" + str(abs(hash(yol)))
        c.register(ad, df); return [(Path(yol).name, ad)]
    if s.endswith(".zip"):
        import zipfile, tempfile
        z = zipfile.ZipFile(yol); uyeler = sorted((i for i in z.infolist() if i.filename.lower().endswith(
            (".csv", ".xlsx", ".xls", ".geojson", ".json", ".jsonl", ".parquet", ".sqlite", ".db"))), key=lambda i: -i.file_size)[:3]
        d = tempfile.mkdtemp(dir=os.environ.get("ZIP_TMP", "/tmp")); sonuc = []
        for u in uyeler:
            f = z.extract(u, d)
            sonuc += [(f"{Path(yol).name}!{u.filename}/{a}", r) for a, r in tablolar(c, f)]
        return sonuc
    if s.endswith((".csv", ".csv.gz")):
        return [(Path(yol).name, f"read_csv_auto('{yol}', sample_size=-1, ignore_errors=true)")]
    if s.endswith((".jsonl", ".ndjson", ".jsonl.gz", ".json")):
        return [(Path(yol).name, f"read_json_auto('{yol}', ignore_errors=true)")]
    raise ValueError(f"tanınmayan biçim: {yol}")


def profil(c, ref, geo_c):
    p = {}
    kol = c.execute(f"DESCRIBE SELECT * FROM {ref}").fetchall()
    p["satir"] = c.execute(f"SELECT COUNT(*) FROM {ref}").fetchone()[0]
    p["sutunlar"] = [(k[0], k[1]) for k in kol]
    adlar = {k[0].lower(): k[0] for k in kol}
    # koordinat
    for a, b in KOORD:
        if a in adlar and b in adlar:
            la, lo = adlar[a], adlar[b]
            q = (f"SELECT COUNT(*) n, COUNT(TRY_CAST({la} AS DOUBLE)) dolu, "
                 f"SUM(CASE WHEN TRY_CAST({lo} AS DOUBLE) BETWEEN {TR_KUTU[0]} AND {TR_KUTU[2]} AND TRY_CAST({la} AS DOUBLE) BETWEEN {TR_KUTU[1]} AND {TR_KUTU[3]} THEN 1 ELSE 0 END) ic FROM {ref}")
            n, dolu, ic = c.execute(q).fetchone()
            p["koordinat"] = {"sutunlar": [la, lo], "dolu_oran": round(dolu / n, 4) if n else None, "tr_kutusu_ic_oran": round((ic or 0) / n, 4) if n else None}
            if geo_c and ic:
                ornek = min(20000, ic)
                eslesen = c.execute(f"""
                    WITH o AS (SELECT TRY_CAST({lo} AS DOUBLE) x, TRY_CAST({la} AS DOUBLE) y FROM {ref}
                               WHERE TRY_CAST({lo} AS DOUBLE) BETWEEN {TR_KUTU[0]} AND {TR_KUTU[2]}
                                 AND TRY_CAST({la} AS DOUBLE) BETWEEN {TR_KUTU[1]} AND {TR_KUTU[3]} USING SAMPLE {ornek} ROWS)
                    SELECT COUNT(*) FROM o WHERE EXISTS (SELECT 1 FROM mah m WHERE o.x BETWEEN m.bbox_xmin AND m.bbox_xmax
                        AND o.y BETWEEN m.bbox_ymin AND m.bbox_ymax AND ST_Contains(m.geometry, ST_Point(o.x, o.y)))""").fetchone()[0]
                p["koordinat"]["mahalle_eslesme_orani_ornek"] = round(eslesen / ornek, 4)
                p["koordinat"]["ornek_buyuklugu"] = ornek
            break
    # kimlik adayları
    p["kimlik"] = {}
    for k in [k[0] for k in kol if KIMLIK_RX.search(k[0])][:4]:
        n, bos, tekil = c.execute(f'SELECT COUNT(*), COUNT(*) - COUNT("{k}"), COUNT(DISTINCT "{k}") FROM {ref}').fetchone()
        p["kimlik"][k] = {"bos_oran": round(bos / n, 4) if n else None, "tekrar_oran": round(1 - tekil / max(n - bos, 1), 4)}
    # tarih aralığı
    p["tarih"] = {}
    for k in [k[0] for k in kol if TARIH_RX.search(k[0])][:4]:
        try:
            mn, mx = c.execute(f'SELECT MIN(CAST("{k}" AS VARCHAR)), MAX(CAST("{k}" AS VARCHAR)) FROM {ref} WHERE "{k}" IS NOT NULL').fetchone()
            p["tarih"][k] = [mn[:25] if mn else None, mx[:25] if mx else None]
        except Exception:
            pass
    p["hassas_supheli"] = [k[0] for k in kol if HASSAS_RX.search(k[0])]
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kaynaklar", required=True); ap.add_argument("--canonical", required=True); ap.add_argument("--cikti", required=True)
    a = ap.parse_args()
    kaynaklar = json.load(open(a.kaynaklar)); os.makedirs(a.cikti, exist_ok=True)
    tarih = time.strftime("%Y-%m-%d"); sonuc = []
    for kay in kaynaklar:
        t0 = time.time(); print(f"== {kay['ad']}: {kay['yol']}", flush=True)
        c = baglan(kay["yol"])
        c.execute(f"ATTACH '{a.canonical}' AS canon (READ_ONLY)")
        c.execute("CREATE TEMP TABLE mah AS SELECT geo_id, geometry, bbox_xmin, bbox_xmax, bbox_ymin, bbox_ymax "
                  "FROM canon.geo_entity WHERE level='mahalle' AND geometry IS NOT NULL")
        kayit = {**kay, "boyut_bayt": sum(f.stat().st_size for f in Path(kay["yol"]).rglob("*") if f.is_file()) if os.path.isdir(kay["yol"]) else os.path.getsize(kay["yol"]),
                 "tablolar": {}}
        try:
            for ad, ref in tablolar(c, kay["yol"]):
                try:
                    kayit["tablolar"][ad] = profil(c, ref, True)
                    print(f"   {ad}: {kayit['tablolar'][ad]['satir']:,} satır", flush=True)
                except Exception as e:
                    kayit["tablolar"][ad] = {"hata": str(e)[:200]}
        except Exception as e:
            kayit["hata"] = str(e)[:300]
        kayit["sure_sn"] = round(time.time() - t0, 1); sonuc.append(kayit); c.close()
    json.dump(sonuc, open(f"{a.cikti}/new_data_review_{tarih}.json", "w"), ensure_ascii=False, indent=1, default=str)
    # Markdown rapor
    L = [f"# Yeni veri inceleme raporu — {tarih}", "",
         "STANDARD §10.1 yeni veri giriş kapısı. Her kaynak için karar: **ACCEPT** (staging→kanonik) · "
         "**ACCEPT_AS_QUARANTINE** (izlenebilirlik için alınır, kanoniğe girmez) · **HOLD** (bekler).", ""]
    for k in sonuc:
        L += [f"## {k['ad']}", f"- Dosya: `{k['yol']}` ({k['boyut_bayt']/1e6:,.0f} MB) · hedef: {k.get('hedef','?')}", f"- {k.get('aciklama','')}"]
        if k.get("hata"): L.append(f"- ⚠️ okunamadı: {k['hata']}")
        L += ["", "| Tablo | Satır | Sütun | Koordinat (TR içi / mahalleye düşen) | Kimlik tekrar | Tarih aralığı | Kişisel veri şüphesi |", "|---|---:|---:|---|---|---|---|"]
        for t, p in k["tablolar"].items():
            if "hata" in p: L.append(f"| `{t}` | hata | | {p['hata'][:60]} | | | |"); continue
            kd = p.get("koordinat"); ks = ", ".join(f"{a}: %{v['tekrar_oran']*100:.1f}" for a, v in p["kimlik"].items())
            koord = f"%{kd['tr_kutusu_ic_oran']*100:.1f} / %{kd.get('mahalle_eslesme_orani_ornek', 0)*100:.1f}" if kd and kd.get("tr_kutusu_ic_oran") is not None else "—"
            tr = "; ".join(f"{a}: {v[0]} → {v[1]}" for a, v in p["tarih"].items())[:90]
            L.append(f"| `{t}` | {p['satir']:,} | {len(p['sutunlar'])} | {koord} | {ks or '—'} | {tr or '—'} | {', '.join(p['hassas_supheli']) or '—'} |")
        L.append("")
    Path(f"{a.cikti}/new_data_review_{tarih}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"rapor: {a.cikti}/new_data_review_{tarih}.md")


if __name__ == "__main__":
    main()
