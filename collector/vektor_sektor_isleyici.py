"""VERİLER/sektor-*.zip coğrafi vektör katmanlarını ürün ambarına (sqlite) işler.

Her zip, bir coğrafi sektörün GeoJSON'ıdır ve `katman` alanıyla farklı katman
türlerini birlikte taşır: DİRİ_FAY_HATTI, ELEKTRIK_HATTI, SU_KUYUSU, SU_YOLU_DERE,
MEVCUT_YOL, ORMAN_ALANI, GOL_BARAJ_HAZNE, SIT_VE_KORUNAN_ALAN, CESME_ICME_SUYU,
DOGAL_PINAR, SAHIL_SERIDI vb.

Her feature; katmanı, adı, geometrisi (GeoJSON metni), sınır kutusu (bbox) ve
temsili noktasıyla saklanır. Mekânsal sorgu için R-tree indeksi kurulur; böylece
arsa analizi "3 km içindeki fay/elektrik/dere/kuyu" ya da "orman/sit içinde mi"
sorularını hızlı yanıtlayabilir.

Ayrı standalone ambar: warehouse/product/cografi_katmanlar.sqlite (gitignore).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import time
import zipfile

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO_DIR, "warehouse", "product", "cografi_katmanlar.sqlite")
DEFAULT_ZIPS = os.path.join(REPO_DIR, "VERİLER", "sektor-*.zip")


def init_schema(connection: sqlite3.Connection, reset: bool = False) -> None:
    if reset:
        connection.executescript(
            "DROP TABLE IF EXISTS cografi_ozellikler;"
            "DROP TABLE IF EXISTS cografi_rtree;"
            "DROP TABLE IF EXISTS cografi_kapsama;"
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS cografi_ozellikler (
            id INTEGER PRIMARY KEY,
            katman TEXT NOT NULL,
            ad TEXT,
            alt_tur TEXT,
            sektor TEXT,
            geom_tip TEXT,
            rep_lat REAL,
            rep_lon REAL,
            geometry TEXT,
            ozellikler TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cog_katman ON cografi_ozellikler(katman);
        CREATE VIRTUAL TABLE IF NOT EXISTS cografi_rtree
            USING rtree(id, min_lat, max_lat, min_lon, max_lon);
        CREATE TABLE IF NOT EXISTS cografi_kapsama (
            sektor TEXT PRIMARY KEY,
            feature_sayisi INTEGER,
            islenme_tarihi TEXT
        );
        """
    )
    connection.commit()


def _walk_coords(coords, box):
    """GeoJSON koordinat ağacını gezerek bbox'ı [minlon,minlat,maxlon,maxlat] günceller."""
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        lon, lat = coords[0], coords[1]
        if lon < box[0]:
            box[0] = lon
        if lat < box[1]:
            box[1] = lat
        if lon > box[2]:
            box[2] = lon
        if lat > box[3]:
            box[3] = lat
        return
    for part in coords:
        _walk_coords(part, box)


def bbox_of(geometry):
    box = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    if geometry and geometry.get("coordinates") is not None:
        _walk_coords(geometry["coordinates"], box)
    if box[0] == float("inf"):
        return None
    return box  # [min_lon, min_lat, max_lon, max_lat]


def sektor_name(filename: str) -> str:
    base = os.path.basename(filename)
    base = re.sub(r"\.geojson$", "", base, flags=re.IGNORECASE)
    return base


def process_zip(connection, zip_path: str, already: set, batch_size: int = 2000) -> int:
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".geojson")]
        if not names:
            return 0
        member = names[0]
        sektor = sektor_name(member)
        if sektor in already:
            print(f"atla (işlenmiş): {sektor}")
            return 0
        data = json.load(archive.open(member))
    features = data.get("features", [])
    next_id = connection.execute("SELECT COALESCE(MAX(id), 0) FROM cografi_ozellikler").fetchone()[0]
    rows_feat, rows_rtree = [], []
    written = 0

    def flush():
        nonlocal rows_feat, rows_rtree, written
        if rows_feat:
            connection.executemany(
                "INSERT INTO cografi_ozellikler "
                "(id,katman,ad,alt_tur,sektor,geom_tip,rep_lat,rep_lon,geometry,ozellikler) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows_feat,
            )
            connection.executemany(
                "INSERT INTO cografi_rtree (id,min_lat,max_lat,min_lon,max_lon) VALUES (?,?,?,?,?)",
                rows_rtree,
            )
            written += len(rows_feat)
            rows_feat, rows_rtree = [], []

    for feature in features:
        geometry = feature.get("geometry") or {}
        box = bbox_of(geometry)
        if box is None:
            continue
        props = dict(feature.get("properties") or {})
        katman = props.pop("katman", None) or "BILINMEYEN"
        ad = props.pop("ad", None)
        alt_tur = props.pop("alt_tur", None)
        next_id += 1
        rep_lat = (box[1] + box[3]) / 2
        rep_lon = (box[0] + box[2]) / 2
        rows_feat.append((
            next_id, katman, ad, alt_tur, sektor, geometry.get("type"),
            rep_lat, rep_lon, json.dumps(geometry, separators=(",", ":")),
            json.dumps(props, ensure_ascii=False, separators=(",", ":")) if props else None,
        ))
        rows_rtree.append((next_id, box[1], box[3], box[0], box[2]))
        if len(rows_feat) >= batch_size:
            flush()
    flush()
    connection.execute(
        "INSERT OR REPLACE INTO cografi_kapsama (sektor, feature_sayisi, islenme_tarihi) "
        "VALUES (?,?,datetime('now'))",
        (sektor, written),
    )
    connection.commit()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Coğrafi vektör sektör işleyici")
    parser.add_argument("--zips", default=DEFAULT_ZIPS, help="sektor zip glob deseni")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--reset", action="store_true", help="Tabloları sıfırla")
    parser.add_argument("--limit", type=int, default=0, help="İlk N zip (test)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    connection = sqlite3.connect(args.db)
    init_schema(connection, reset=args.reset)
    already = {row[0] for row in connection.execute("SELECT sektor FROM cografi_kapsama")}

    zips = sorted(glob.glob(args.zips), key=lambda p: int(re.search(r"(\d+)", os.path.basename(p)).group(1))
                  if re.search(r"(\d+)", os.path.basename(p)) else 0)
    if args.limit:
        zips = zips[:args.limit]
    if not zips:
        print(f"Uyarı: '{args.zips}' desenine uyan zip yok.")
        return 1

    print(f"{len(zips)} sektör zip işlenecek → {args.db}")
    t0 = time.time()
    grand = 0
    for i, zip_path in enumerate(zips, 1):
        try:
            n = process_zip(connection, zip_path, already)
            grand += n
            print(f"[{i}/{len(zips)}] {os.path.basename(zip_path)}: {n:,} feature "
                  f"(toplam {grand:,})")
        except Exception as exc:
            print(f"[{i}/{len(zips)}] HATA {os.path.basename(zip_path)}: {exc}")

    print("ANALYZE...")
    connection.execute("ANALYZE")
    connection.commit()
    summary = connection.execute(
        "SELECT katman, COUNT(*) FROM cografi_ozellikler GROUP BY katman ORDER BY COUNT(*) DESC"
    ).fetchall()
    connection.close()
    print(f"\nBitti. {grand:,} feature, {time.time() - t0:.0f} sn. Katman dağılımı:")
    for katman, count in summary:
        print(f"  {katman}: {count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
