"""OSM işletme geçmişi (yıllık kesitler) → osm_degisim.sqlite::poi_yillik + poi_yasam

Kaynak: Geofabrik **public** yıllık kesitleri (download.geofabrik.de/europe/turkey-YYMMDD.osm.pbf; 1 Ocak; kullanıcı verisi
içermez, ODbL). "internal" sunucudaki dosyalar (kullanıcı adı/ID'li) OSM içi amaçlarla sınırlı → KULLANILMAZ.
Her kesit collector/osm_poi_toplayici.siniflandir ile aynı kategori kurallarından geçer; nokta ve alan (merkez) POI'ler
(osm_tip, osm_id, alt_kategori, marka, ad, lat, lon) olarak `poi_yillik`'e yazılır. Güncel kesit = osm_poi.sqlite (meta.pbf_mtime).
`poi_yasam`: nesne başına ilk görülme / son görülme kesiti, durum (aktif = son kesitte var; kaldırıldı = son kesitte yok),
ad değişimleri (normalize edilmiş ad farklıysa; yalnız yazım düzeltmesi sayılmaz), marka değişimi, taşınma (>150 m).
Dürüstlük: "OSM'de ilk görülme" ≠ açılış tarihi; haritalama tarihi olabilir (toplu import yılları). Kart bunu yazar.
Diske ihtiyaç: kesit dosyası indirilir, işlenir, silinir (~0,5 GB geçici).

  python3.13 collector/osm_poi_gecmis_toplayici.py [--yillar 2021,2022,2023,2024,2025] [--sil]
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import osmium

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "collector"))
from osm_poi_toplayici import siniflandir, marka_bul  # noqa: E402
from geoprop.veri_yardimcilari import normalize_name, haversine_km  # noqa: E402

OUT = os.path.join(REPO, "warehouse", "product", "osm_degisim.sqlite")
POI = os.path.join(REPO, "warehouse", "product", "osm_poi.sqlite")
RAW = os.path.join(REPO, "warehouse", "raw", "osm")
UA = "Mozilla/5.0 (GEOPROP veri toplayici)"


class KesitHandler(osmium.SimpleHandler):
    def __init__(self, c, tarih):
        super().__init__(); self.c = c; self.tarih = tarih; self.buf = []; self.n = 0
        self.wkbfab = osmium.geom.WKBFactory()

    def _emit(self, tip, oid, tags, lat, lon):
        s = siniflandir(tags)
        if not s:
            return
        kat, alt = s
        ad = tags.get("name")
        adn = normalize_name(ad or ""); self.buf.append((self.tarih, tip, oid, alt, marka_bul(adn, tags.get("brand")), ad, adn, lat, lon)); self.n += 1
        if len(self.buf) >= 5000:
            self.flush()

    def flush(self):
        self.c.executemany("INSERT OR REPLACE INTO poi_yillik VALUES (?,?,?,?,?,?,?,?,?)", self.buf); self.buf.clear(); self.c.commit()

    def node(self, n):
        if len(n.tags) and n.location.valid():
            self._emit("node", n.id, dict(n.tags), n.location.lat, n.location.lon)

    def area(self, a):
        if not len(a.tags):
            return
        try:
            from shapely import wkb
            g = wkb.loads(bytes.fromhex(self.wkbfab.create_multipolygon(a)))
            pt = g.representative_point()
            self._emit("way" if a.from_way() else "relation", a.orig_id(), dict(a.tags), pt.y, pt.x)
        except Exception:
            pass


def kesit_isle(c, tarih, pbf):
    h = KesitHandler(c, tarih); h.apply_file(pbf, locations=True); h.flush()
    c.execute("INSERT OR REPLACE INTO kesit VALUES (?,?,?)", (tarih, h.n, datetime.now(timezone.utc).isoformat())); c.commit()
    print(f"  kesit {tarih}: POI {h.n:,}", flush=True)


def guncel_kesit(c):
    src = sqlite3.connect(f"file:{POI}?mode=ro", uri=True)
    tarih = dict(src.execute("SELECT key, value FROM meta")).get("pbf_mtime", "")[:10]
    rows = [(tarih, r[0], r[1], r[2], r[3], r[4], normalize_name(r[4] or ""), r[5], r[6]) for r in src.execute("SELECT osm_tip, osm_id, alt_kategori, marka, ad, lat, lon FROM poi")]
    c.executemany("INSERT OR REPLACE INTO poi_yillik VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.execute("INSERT OR REPLACE INTO kesit VALUES (?,?,?)", (tarih, len(rows), datetime.now(timezone.utc).isoformat())); c.commit()
    print(f"  güncel kesit {tarih}: POI {len(rows):,}"); return tarih


def yasam_hesapla(c):
    kesitler = [r[0] for r in c.execute("SELECT tarih FROM kesit ORDER BY tarih")]
    son = kesitler[-1]
    c.executescript("""DROP TABLE IF EXISTS poi_yasam;
    CREATE TABLE poi_yasam (osm_tip TEXT, osm_id INTEGER, alt_kategori TEXT, marka TEXT, ad TEXT, lat REAL, lon REAL, ilk_gorulme TEXT, son_gorulme TEXT,
        durum TEXT, kesit_sayisi INTEGER, ad_degisim INTEGER, marka_degisim INTEGER, tasinma INTEGER, gecmis TEXT, PRIMARY KEY (osm_tip, osm_id)) WITHOUT ROWID;
    CREATE INDEX idx_poi_yasam_lat ON poi_yasam (lat, lon);
    CREATE INDEX idx_poi_yasam_kapsayan ON poi_yasam (lat, lon, osm_tip, osm_id, alt_kategori, marka, ad, ilk_gorulme, son_gorulme, durum, ad_degisim, marka_degisim, tasinma);""")
    cur = c.execute("SELECT osm_tip, osm_id, tarih, alt_kategori, marka, ad, ad_norm, lat, lon FROM poi_yillik ORDER BY osm_tip, osm_id, tarih")
    grup = []; key = None; out = []

    def bitir(g):
        if not g:
            return
        ilk, sonk = g[0], g[-1]
        ad_deg = sum(1 for a, b in zip(g, g[1:]) if a[6] and b[6] and a[6] != b[6])
        mk_deg = sum(1 for a, b in zip(g, g[1:]) if (a[4] or b[4]) and a[4] != b[4])
        tas = sum(1 for a, b in zip(g, g[1:]) if haversine_km(a[7], a[8], b[7], b[8]) * 1000 > 150)
        gecmis = [{"t": r[2], "ad": r[5], "marka": r[4], "alt": r[3]} for r in g]
        out.append((sonk[0], sonk[1], sonk[3], sonk[4], sonk[5], sonk[7], sonk[8], ilk[2], sonk[2], "aktif" if sonk[2] == son else "kaldirildi",
                    len(g), ad_deg, mk_deg, tas, json.dumps(gecmis, ensure_ascii=False)))
        if len(out) >= 5000:
            c.executemany("INSERT INTO poi_yasam VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out); out.clear()

    for r in cur:
        k = (r[0], r[1])
        if k != key:
            bitir(grup); grup = []; key = k
        grup.append(r)
    bitir(grup)
    if out:
        c.executemany("INSERT INTO poi_yasam VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    c.commit()
    print("poi_yasam:", c.execute("SELECT durum, COUNT(*) FROM poi_yasam GROUP BY 1").fetchall(), "ad değişimli:", c.execute("SELECT COUNT(*) FROM poi_yasam WHERE ad_degisim>0").fetchone()[0])


def main():
    yillar = [int(y) for y in (sys.argv[sys.argv.index("--yillar") + 1] if "--yillar" in sys.argv else "2021,2022,2023,2024,2025").split(",")]
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS poi_yillik (tarih TEXT, osm_tip TEXT, osm_id INTEGER, alt_kategori TEXT, marka TEXT, ad TEXT, ad_norm TEXT, lat REAL, lon REAL,
        PRIMARY KEY (tarih, osm_tip, osm_id)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS kesit (tarih TEXT PRIMARY KEY, poi INTEGER, islenme TEXT);""")
    os.makedirs(RAW, exist_ok=True)
    for y in yillar:
        tarih = f"{y}-01-01"
        if c.execute("SELECT 1 FROM kesit WHERE tarih=?", (tarih,)).fetchone():
            print("  var:", tarih); continue
        ad = f"turkey-{y % 100:02d}0101.osm.pbf"; pbf = os.path.join(RAW, ad)
        if not os.path.exists(pbf):
            print("  indiriliyor:", ad, flush=True)
            r = subprocess.run(["curl", "-sSL", "-m", "3600", "-A", UA, "-o", pbf, f"https://download.geofabrik.de/europe/{ad}"])
            if r.returncode != 0 or not os.path.exists(pbf) or os.path.getsize(pbf) < 10_000_000:
                print("  indirilemedi:", ad); continue
        kesit_isle(c, tarih, pbf)
        if "--sil" in sys.argv:
            os.remove(pbf)
    guncel_kesit(c)
    yasam_hesapla(c)
    c.execute("ANALYZE"); c.commit(); c.close()


if __name__ == "__main__":
    main()
