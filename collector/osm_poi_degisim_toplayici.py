"""OSM POI değişim izleyici → warehouse/product/osm_degisim.sqlite

Her yeni osm_poi.sqlite (haftalık Geofabrik kesiti) işlendiğinde çalıştırılır; bir önceki anlık görüntünün kimlik
kümesiyle karşılaştırıp iki şey üretir:
  poi_olay      : eklenen / silinen / taşınan (>150 m) POI olayları (tarih, alt_kategori, marka, ad, lat, lon) — "1 km'de son 90 günde
                  açılan-kapanan işletme" göstergesinin kaynağı. Ad değişikliği olay değildir (OSM düzenlemesi olabilir).
  ilce_sayim    : anlık görüntü başına il/ilçe × alt_kategori POI sayısı (idari sınır poligonuyla) — ilçe düzeyi zaman serisi.
  poi_kimlik    : son anlık görüntünün kimlik kümesi (bir sonraki karşılaştırma için; üzerine yazılır).
İlk çalıştırma yalnız taban oluşturur (olay yok). OSM'de "silinme" gerçek kapanış olmayabilir (haritacı düzeltmesi) → arayüz
"OSM'den kaldırıldı" der, "kapandı" demez.

  python3.13 collector/osm_poi_degisim_toplayici.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import haversine_km  # noqa: E402

POI = os.path.join(REPO, "warehouse", "product", "osm_poi.sqlite")
OUT = os.path.join(REPO, "warehouse", "product", "osm_degisim.sqlite")
TASINMA_M = 150


def main():
    src = sqlite3.connect(f"file:{POI}?mode=ro", uri=True)
    tarih = dict(src.execute("SELECT key, value FROM meta")).get("pbf_mtime", datetime.now(timezone.utc).isoformat())[:10]
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS poi_kimlik (osm_tip TEXT, osm_id INTEGER, alt_kategori TEXT, marka TEXT, ad TEXT, lat REAL, lon REAL, PRIMARY KEY (osm_tip, osm_id)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS poi_olay (tarih TEXT, olay TEXT, osm_tip TEXT, osm_id INTEGER, alt_kategori TEXT, marka TEXT, ad TEXT, lat REAL, lon REAL,
        PRIMARY KEY (tarih, osm_tip, osm_id)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_poi_olay_lat ON poi_olay (lat, lon);
    CREATE TABLE IF NOT EXISTS ilce_sayim (tarih TEXT, il TEXT, ilce TEXT, alt_kategori TEXT, sayi INTEGER, PRIMARY KEY (tarih, il, ilce, alt_kategori)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS anlik (tarih TEXT PRIMARY KEY, poi INTEGER, eklenen INTEGER, silinen INTEGER, tasinan INTEGER, islenme TEXT);
    """)
    if c.execute("SELECT 1 FROM anlik WHERE tarih=?", (tarih,)).fetchone():
        print("bu anlık görüntü zaten işlenmiş:", tarih); return
    yeni = {(r[0], r[1]): r[2:] for r in src.execute("SELECT osm_tip, osm_id, alt_kategori, marka, ad, lat, lon FROM poi")}
    eski = {(r[0], r[1]): r[2:] for r in c.execute("SELECT osm_tip, osm_id, alt_kategori, marka, ad, lat, lon FROM poi_kimlik")}
    n_ek = n_sil = n_tas = 0
    if eski:
        for k, v in yeni.items():
            if k not in eski:
                c.execute("INSERT OR REPLACE INTO poi_olay VALUES (?,?,?,?,?,?,?,?,?)", (tarih, "eklendi", *k, *v)); n_ek += 1
            else:
                e = eski[k]
                if haversine_km(e[3], e[4], v[3], v[4]) * 1000 > TASINMA_M:
                    c.execute("INSERT OR REPLACE INTO poi_olay VALUES (?,?,?,?,?,?,?,?,?)", (tarih, "tasindi", *k, *v)); n_tas += 1
        for k, e in eski.items():
            if k not in yeni:
                c.execute("INSERT OR REPLACE INTO poi_olay VALUES (?,?,?,?,?,?,?,?,?)", (tarih, "silindi", *k, *e)); n_sil += 1
    c.execute("DELETE FROM poi_kimlik")
    c.executemany("INSERT INTO poi_kimlik VALUES (?,?,?,?,?,?,?)", [(*k, *v) for k, v in yeni.items()])
    # ilçe sayımı (idari poligon) — AdminLookup nokta-poligon; 364K nokta ~ birkaç dakika
    from geoprop.idari import AdminLookup
    A = AdminLookup(os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite"))
    sayim: dict[tuple, int] = {}
    for (tip, oid), (alt, _m, _a, lat, lon) in yeni.items():
        il, ilce = A.lookup(lat, lon)
        if il:
            sayim[(il, ilce or "", alt)] = sayim.get((il, ilce or "", alt), 0) + 1
    c.executemany("INSERT OR REPLACE INTO ilce_sayim VALUES (?,?,?,?,?)", [(tarih, il, ilce, alt, n) for (il, ilce, alt), n in sayim.items()])
    c.execute("INSERT INTO anlik VALUES (?,?,?,?,?,?)", (tarih, len(yeni), n_ek, n_sil, n_tas, datetime.now(timezone.utc).isoformat()))
    c.commit(); c.execute("ANALYZE"); c.commit(); c.close()
    print(f"anlık {tarih}: poi {len(yeni):,} | eklenen {n_ek:,} silinen {n_sil:,} taşınan {n_tas:,} | ilçe-kategori satırı {len(sayim):,}")


if __name__ == "__main__":
    main()
