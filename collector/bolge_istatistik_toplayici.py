"""Bölge istatistik CSV paketlerini (ulusal + 15 bölge + 6 il paketi) sınıflandırarak ambara işler.

Kaynaklar (';' ayraçlı, BOM'lu):
  VERİLER/Öğelerle Yeni Klasör 2/ULUSAL_CSV/*.csv           (01–14, 76 il)
  VERİLER/Öğelerle Yeni Klasör 2/BOLGE_CSV/<paket>/*.csv     (bolge_01–15 + m01–m06; m0X'te 15/16/17 de var)

Tüm dosyalar city_id/county_id/district_id ile anahtarlı (arsa_mahalle_ozet ile AYNI id evreni,
kanonik il id'si = plaka kodu 1–81). Paketler arası `id` çakıştığı için tablolar **doğal
anahtarla** kurulur; aynı satır birden çok pakette gelirse INSERT OR REPLACE tekilleştirir.

Sınıflandırma (bkz. .claude/skills/geoprop-veri-mimarisi/references/guvenlik-siniflandirma.md):
  acik    → warehouse/product/bolge_istatistik.sqlite
  kisitli → warehouse/restricted/kisitli_istatistik.sqlite (seçim, hemşehri)      chmod 600
  pii     → warehouse/restricted/kisisel_veriler.sqlite    (ofis/danışman telefon-ad) chmod 600

Ürün DB'sine idari referans (ref_il/ref_ilce/ref_mahalle, ad_norm indeksli) yazılır: isimden
id'ye tek noktadan, indeks destekli çözümleme (join hub). 12_iller kirlidir (her il 15 id);
yalnız kanonik 1–81 alınır.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, normalize_neighbourhood  # noqa: E402

BASE = os.path.join(REPO, "VERİLER", "Öğelerle Yeni Klasör 2")
DEFAULT_SRCS = [os.path.join(BASE, "ULUSAL_CSV")] + sorted(glob.glob(os.path.join(BASE, "BOLGE_CSV", "*")))
PRODUCT_DB = os.path.join(REPO, "warehouse", "product", "bolge_istatistik.sqlite")
RESTRICTED_DB = os.path.join(REPO, "warehouse", "restricted", "kisitli_istatistik.sqlite")
PII_DB = os.path.join(REPO, "warehouse", "restricted", "kisisel_veriler.sqlite")
# m07–m20 POI zip'leri: data/piyasa_verileri.db → poi_noktalari (ilçe düzeyi, koordinatsız; 500K satır).
POI_ZIP_GLOB = os.path.join(BASE, "m*_poi_*.zip")

ID_COLS = ("city_id", "county_id", "district_id")
LOK = ("seviye", "city_id", "county_id", "district_id")
# dosya_kökü → (tablo, sınıf, doğal anahtar, PII kolonları)
FILES = {
    "01_demografi_ve_nufus":            ("demografi",          "acik",    LOK, ()),
    "02_yillik_satislar_2010_2024":     ("yillik_satis",       "acik",    LOK + ("yil",), ()),
    "03_fiyat_ozet_konut_arsa":         ("fiyat_ozet",         "acik",    ("kategori",) + LOK + ("donem",), ()),
    "04_aylik_fiyat_trendi_2021_2026":  ("aylik_fiyat_trendi", "acik",    ("kategori",) + LOK + ("ay",), ()),
    "05_oda_yas_kat_isitma_kirilimlari":("dagilim_kirilim",    "acik",    ("kategori",) + LOK + ("dagilim_turu", "segment"), ()),
    "08_ilce_onemli_noktalar_poi":      ("poi_ilce",           "acik",    ("city_id", "county_id", "poi_id"), ()),
    "09_emlak_ofisleri_rehberi":        ("emlak_ofisi",        "acik",    ("city_id", "slug"), ("telefon",)),
    "11_insaat_ve_proje_sirketleri":    ("insaat_sirketi",     "acik",    ("sirket_id",), ()),
    "15_e_ticaret_ve_harcama_kalemleri":("eticaret_harcama",   "acik",    LOK, ()),
    "16_medeni_durum_ve_gayrimenkul_stoku": ("medeni_stok",    "acik",    LOK, ()),
    "17_detayli_yas_piramidi_47_grup":  ("yas_piramidi",       "acik",    LOK, ()),
    "06_hemsehri_kutuk_dagilimi":       ("hemsehri_kutuk",     "kisitli", LOK + ("kutuk_ili",), ()),
    "07_secim_sonuclari_ve_oylar":      ("secim_sonuclari",    "kisitli", LOK + ("secim_kodu",), ()),
    "10_gayrimenkul_danismanlari":      ("danisman",           "pii",     ("city_id", "danisman_adi", "ofis_adi"), ("danisman_adi", "telefon")),
}


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f, delimiter=";")


def _num(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _header(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [c.strip() for c in next(csv.reader(f, delimiter=";"))]


def _infer_types(paths, cols, sample=400):
    numeric = {c: True for c in cols}
    seen = {c: 0 for c in cols}
    for path in paths[:3]:
        for i, row in enumerate(_read(path)):
            if i >= sample:
                break
            for c in cols:
                v = (row.get(c) or "").strip()
                if not v:
                    continue
                seen[c] += 1
                if _num(v) is None:
                    numeric[c] = False
    return {c: ("INTEGER" if c in ID_COLS else "REAL" if (numeric[c] and seen[c]) else "TEXT") for c in cols}


def _open(path, restricted=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if restricted:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


def load_reference(product, srcs):
    product.executescript(
        """
        DROP TABLE IF EXISTS ref_il; DROP TABLE IF EXISTS ref_ilce; DROP TABLE IF EXISTS ref_mahalle;
        CREATE TABLE ref_il (city_id INTEGER PRIMARY KEY, ad TEXT, ad_norm TEXT);
        CREATE TABLE ref_ilce (county_id INTEGER PRIMARY KEY, city_id INTEGER, ad TEXT, ad_norm TEXT);
        CREATE TABLE ref_mahalle (district_id INTEGER PRIMARY KEY, county_id INTEGER, city_id INTEGER, ad TEXT, ad_norm TEXT);
        CREATE INDEX idx_ref_il_norm ON ref_il(ad_norm);
        CREATE INDEX idx_ref_ilce_norm ON ref_ilce(city_id, ad_norm);
        CREATE INDEX idx_ref_mah_norm ON ref_mahalle(county_id, ad_norm);
        """
    )
    for src in srcs:
        p = os.path.join(src, "12_iller_listesi.csv")
        if os.path.exists(p):
            for r in _read(p):
                cid = int(float(r["city_id"]))
                if 1 <= cid <= 81:  # kanonik plaka kodu; kirli ek id'ler atılır
                    product.execute("INSERT OR IGNORE INTO ref_il VALUES (?,?,?)",
                                    (cid, r["city_name"], normalize_name(r["city_name"])))
        p = os.path.join(src, "13_ilceler_listesi.csv")
        if os.path.exists(p):
            for r in _read(p):
                product.execute("INSERT OR IGNORE INTO ref_ilce VALUES (?,?,?,?)",
                                (int(float(r["county_id"])), int(float(r["city_id"])), r["county_name"], normalize_name(r["county_name"])))
        p = os.path.join(src, "14_mahalleler_listesi.csv")
        if os.path.exists(p):
            for r in _read(p):
                product.execute("INSERT OR IGNORE INTO ref_mahalle VALUES (?,?,?,?,?)",
                                (int(float(r["district_id"])), int(float(r["county_id"])), int(float(r["city_id"])),
                                 r["district_name"], normalize_neighbourhood(r["district_name"])))
    product.commit()
    prune_dead_reference_ids(product)


def prune_dead_reference_ids(product: sqlite3.Connection) -> None:
    """13_ilceler de 12_iller gibi kirlidir: her ilçe için ikinci bir (ölü) id taşır (ör. Seyhan
    874 ve 1104; istatistik tabloları yalnız 1104 kullanır). Ölü id'ler hub'da kalırsa isim→id
    çözümü yanlış id'ye düşer ve ilçe/mahalle kapsamı sessizce il düzeyine geriler. Hub, gerçekten
    veri taşıyan id'lere indirgenir."""
    used = " UNION ".join(
        f"SELECT county_id FROM {t}" for t in ("demografi", "fiyat_ozet", "yillik_satis", "poi_ilce", "ref_mahalle")
        if product.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
    )
    if used:
        product.execute(f"DELETE FROM ref_ilce WHERE county_id NOT IN ({used})")
        product.execute("DELETE FROM ref_mahalle WHERE county_id NOT IN (SELECT county_id FROM ref_ilce)")
    product.commit()


def load_table(conn, pii, paths, table, sinif, natural_key, pii_cols, batch=5000):
    cols = _header(paths[0])
    for p in paths[1:]:  # başlık farklıysa birleşim
        for c in _header(p):
            if c not in cols:
                cols.append(c)
    types = _infer_types(paths, cols)
    keep = [c for c in cols if c != "id" and (sinif == "pii" or c not in pii_cols)]
    q = lambda c: '"' + c + '"'
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(
        f'CREATE TABLE "{table}" ({", ".join(q(c) + " " + types[c] for c in keep)}, '
        f'PRIMARY KEY ({", ".join(q(k) for k in natural_key)}))'
    )
    lok = [c for c in ID_COLS if c in keep]
    if lok and tuple(natural_key[:1]) != tuple(lok[:1]):
        conn.execute(f'CREATE INDEX "idx_{table}_lok" ON "{table}" ({", ".join(lok)})')
    if pii is not None and pii_cols and sinif != "pii":
        pii.execute("""CREATE TABLE IF NOT EXISTS iletisim (
            kaynak_tablo TEXT, kayit_key TEXT, alan TEXT, deger_sha256 TEXT, deger TEXT,
            PRIMARY KEY (kaynak_tablo, kayit_key, alan))""")
    insert = f'INSERT OR REPLACE INTO "{table}" ({", ".join(q(c) for c in keep)}) VALUES ({", ".join("?" for _ in keep)})'
    rows, n = [], 0
    for path in paths:
        for r in _read(path):
            vals = []
            for c in keep:
                raw = (r.get(c) or "").strip()
                if c in ID_COLS:
                    x = _num(raw); vals.append(int(x) if x is not None else 0)   # boş id → 0 (PK için)
                elif types[c] == "REAL":
                    vals.append(_num(raw))
                else:
                    vals.append(raw or None)
            rows.append(vals)
            if pii is not None and pii_cols and sinif != "pii":
                key = "|".join(str((r.get(k) or "").strip()) for k in natural_key)
                for pc in pii_cols:
                    pv = (r.get(pc) or "").strip()
                    if pv:
                        pii.execute("INSERT OR REPLACE INTO iletisim VALUES (?,?,?,?,?)",
                                    (table, key, pc, hashlib.sha256(pv.encode()).hexdigest(), pv))
            if len(rows) >= batch:
                conn.executemany(insert, rows); n += len(rows); rows = []
    if rows:
        conn.executemany(insert, rows); n += len(rows)
    conn.commit()
    if pii is not None:
        pii.commit()
    add_mahalle_norm(conn, table)
    return n


def add_mahalle_norm(conn: sqlite3.Connection, table: str) -> None:
    """Mahalle satırlarında `district_id` ilçe içi yerel sıra numarasıdır (1..N), 14_mahalleler'in
    küresel id'siyle eşleşmez; mahalle adı yalnız `bolge_adi`nin son parçasında ("İl - İlçe - Mahalle")
    bulunur. Türetilmiş, indeksli `mahalle_norm` sütunu mahalle kapsamını (county_id, mahalle_norm)
    ile fonksiyonsuz WHERE üzerinden çözer. Tek geçişli UPDATE (satır başına Python fonksiyonu);
    `WHERE bolge_adi=?` ile ad ad güncellemek 1.5M satırlık tabloda binlerce tam tarama demektir."""
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if not {"seviye", "county_id", "bolge_adi"} <= cols:
        return
    if "mahalle_norm" not in cols:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN mahalle_norm TEXT')
    conn.create_function("geoprop_mahalle_norm", 1,
                         lambda b: normalize_neighbourhood(b.rsplit(" - ", 1)[-1]) if b else None, deterministic=True)
    conn.execute(f'UPDATE "{table}" SET mahalle_norm=geoprop_mahalle_norm(bolge_adi) '
                 f"WHERE seviye='mahalle' AND mahalle_norm IS NULL")
    conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_mah" ON "{table}" (county_id, mahalle_norm)')
    conn.commit()


def load_poi_zips(product: sqlite3.Connection, zip_glob: str = POI_ZIP_GLOB) -> int:
    """POI zip paketlerindeki `poi_noktalari` tablosunu `poi_ilce` ile birleştirir (aynı kolonlar,
    doğal anahtar city_id+county_id+poi_id). Koordinat yoktur; bu bir *ilçe kategori sayımı*
    setidir, yakınlık seti değildir. `emlak_ofisleri`/`danismanlar` tabloları bu zip'lerde boştur.
    Sonra `poi_ilce_ozet` (ilçe × alt_kategori sayısı) önceden hesaplanır: istek yolunda 500K
    satırı gruplamak yerine küçük özet tablo okunur."""
    import tempfile
    import zipfile
    if not product.execute("SELECT 1 FROM sqlite_master WHERE name='poi_ilce'").fetchone():
        return 0
    n = 0
    tmp = tempfile.mkdtemp(prefix="geoprop_poi_")
    for zp in sorted(glob.glob(zip_glob)):
        with zipfile.ZipFile(zp) as z:
            member = next((m for m in z.namelist() if m.endswith("piyasa_verileri.db")), None)
            if not member:
                continue
            z.extract(member, tmp)
        src = sqlite3.connect(os.path.join(tmp, member))
        rows = src.execute("SELECT city_id, county_id, bolge_adi, poi_id, kategori_id, alt_kategori, poi_adi, slug "
                           "FROM poi_noktalari WHERE city_id BETWEEN 1 AND 81").fetchall()
        src.close()
        product.executemany("INSERT OR IGNORE INTO poi_ilce (city_id, county_id, bolge_adi, poi_id, kategori_id, "
                            "alt_kategori, poi_adi, slug) VALUES (?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
        print(f"    poi zip {os.path.basename(zp):22} {len(rows):>7,}")
    product.executescript("""
        DROP TABLE IF EXISTS poi_ilce_ozet;
        CREATE TABLE poi_ilce_ozet (city_id INTEGER, county_id INTEGER, alt_kategori TEXT, sayi INTEGER,
                                    PRIMARY KEY (county_id, alt_kategori)) WITHOUT ROWID;
        INSERT INTO poi_ilce_ozet SELECT city_id, county_id, alt_kategori, COUNT(*)
            FROM poi_ilce WHERE alt_kategori IS NOT NULL GROUP BY city_id, county_id, alt_kategori;
        CREATE INDEX IF NOT EXISTS idx_poi_ilce_ilce ON poi_ilce (county_id, alt_kategori);
    """)
    product.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Bölge istatistik toplayıcısı (ulusal+bölge+il paketleri)")
    ap.add_argument("--srcs", nargs="*", default=DEFAULT_SRCS)
    ap.add_argument("--only", default="", help="virgüllü dosya kökü filtresi (test)")
    args = ap.parse_args()
    srcs = [s for s in args.srcs if os.path.isdir(s)]
    if not srcs:
        print("Kaynak klasör yok"); return 1
    print(f"{len(srcs)} kaynak paket")

    product = _open(PRODUCT_DB)
    restricted = _open(RESTRICTED_DB, restricted=True)
    pii = _open(PII_DB, restricted=True)
    for c in (product, restricted, pii):
        c.execute("CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, sinif TEXT, paket_sayisi INTEGER, islenme TEXT)")

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    print("Referans (12/13/14, kanonik il 1–81)…"); load_reference(product, srcs)
    for stem, (table, sinif, nk, pii_cols) in FILES.items():
        if only and stem not in only:
            continue
        paths = [os.path.join(s, stem + ".csv") for s in srcs if os.path.exists(os.path.join(s, stem + ".csv"))]
        if not paths:
            print(f"  atla (yok): {stem}"); continue
        target = {"acik": product, "kisitli": restricted, "pii": pii}[sinif]
        n = load_table(target, pii if sinif == "acik" else None, paths, table, sinif, nk, pii_cols)
        tekil = target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        target.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?,?,?)",
                       (table, tekil, sinif, len(paths), datetime.now(timezone.utc).isoformat()))
        target.commit()
        print(f"  {sinif:8} {table:20} {n:>9,} okundu → {tekil:>9,} tekil ({len(paths)} paket)")
    if not only or "08_ilce_onemli_noktalar_poi" in only:
        print("POI zip paketleri (m07–m20)…")
        n = load_poi_zips(product)
        tekil = product.execute("SELECT COUNT(*) FROM poi_ilce").fetchone()[0]
        product.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?,?,?)",
                        ("poi_ilce", tekil, "acik", 1 + len(glob.glob(POI_ZIP_GLOB)), datetime.now(timezone.utc).isoformat()))
        product.commit()
        print(f"  acik     poi_ilce (zip)       {n:>9,} okundu → {tekil:>9,} tekil")
    for c in (product, restricted, pii):
        c.execute("ANALYZE"); c.commit(); c.close()
    for p in (RESTRICTED_DB, PII_DB):
        try: os.chmod(p, 0o600)
        except OSError: pass
    print(f"\nürün: {PRODUCT_DB}\nkısıtlı: {RESTRICTED_DB}\nPII: {PII_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
