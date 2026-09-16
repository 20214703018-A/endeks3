"""Sağlık Bakanlığı faal özel hastane listesi → onemli_tesisler.sqlite::hastane_ozel

Kaynak: shgmozelhasdb.saglik.gov.tr "Özel Hastane Listesi (Faal)" — il, ilçe, ad, kuruluş tipi (genel/dal/…).
Koordinat: OSM hastane noktası (aynı il + ilçe, ad benzerliği; "ÖZEL" ön eki düşülerek) → yoksa geocode (ad + ilçe + il,
yaklaşık) → yoksa 'bulunamadı'. Kamu hastaneleri listesi SB sitesinde 404 (2026-09) — OSM'den gelir.
Nominatim/Photon tek süreç kuralı: başka geocode çalışırken başlatma.
"""
from __future__ import annotations

import difflib
import html
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, tr_title  # noqa: E402
from geoprop.idari import AdminLookup  # noqa: E402

OUT = os.path.join(REPO, "warehouse", "product", "onemli_tesisler.sqlite")
URL = "https://shgmozelhasdb.saglik.gov.tr/TR-53567/ozel-hastane-listesi-faal.html"
_KIRP = re.compile(r"\b(ÖZEL|OZEL|HASTANESİ|HASTANESI|HASTANE|TIP MERKEZİ)\b", re.I)


def _cekirdek(ad: str) -> str:
    return normalize_name(_KIRP.sub(" ", ad or ""))


def main():
    h = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode("utf-8", "ignore")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        cells = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", td))).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 4 and cells[0] and cells[2]:
            rows.append(cells[:4])
    c = sqlite3.connect(OUT, timeout=60); now = datetime.now(timezone.utc).isoformat()
    c.executescript("""CREATE TABLE IF NOT EXISTS hastane_ozel (id INTEGER PRIMARY KEY, il TEXT, ilce TEXT, ad TEXT, ad_norm TEXT, tip TEXT,
                       lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT, UNIQUE (il, ilce, ad));
                       CREATE INDEX IF NOT EXISTS idx_hastane_ozel_lat ON hastane_ozel (lat, lon);
                       CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);""")
    for il, ilce, ad, tip in rows:
        c.execute("INSERT OR IGNORE INTO hastane_ozel (il, ilce, ad, ad_norm, tip, guncellenme) VALUES (?,?,?,?,?,?)",
                  (tr_title(il), tr_title(ilce), re.sub(r"\s+", " ", ad), normalize_name(ad), tip, now))
    c.commit(); print("SB özel hastane satır:", len(rows))

    A = AdminLookup(os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite"))
    o = sqlite3.connect(f"file:{os.path.join(REPO, 'warehouse', 'product', 'osm_poi.sqlite')}?mode=ro", uri=True)
    osm = []
    for ad, lat, lon in o.execute("SELECT ad, lat, lon FROM poi WHERE alt_kategori='hastane' AND ad IS NOT NULL"):
        il2, ilce2 = A.lookup(lat, lon)
        osm.append((normalize_name(il2 or ""), normalize_name(ilce2 or ""), _cekirdek(ad), lat, lon))
    from collector.geocode import adres_kodla
    n_osm = n_geo = 0
    for hid, il, ilce, ad in c.execute("SELECT id, il, ilce, ad FROM hastane_ozel WHERE lat IS NULL").fetchall():
        il_n, ilce_n, cek = normalize_name(il), normalize_name(ilce), _cekirdek(ad)
        pool = [x for x in osm if x[0] == il_n and x[1] == ilce_n]
        best = difflib.get_close_matches(cek, [x[2] for x in pool], n=1, cutoff=0.75)
        if best:
            x = pool[[p[2] for p in pool].index(best[0])]
            c.execute("UPDATE hastane_ozel SET lat=?, lon=?, koordinat_kaynagi='osm: ad eşleşmesi (ilçe içi)' WHERE id=?", (x[3], x[4], hid)); n_osm += 1
        else:
            hit = adres_kodla("", il, ilce, ad)
            if hit:
                c.execute("UPDATE hastane_ozel SET lat=?, lon=?, koordinat_kaynagi=? WHERE id=?", (hit[0], hit[1], hit[2], hid)); n_geo += 1
            else:
                c.execute("UPDATE hastane_ozel SET koordinat_kaynagi='bulunamadı' WHERE id=?", (hid,))
        c.commit()
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('hastane_ozel', ?, ?, 'SB SHGM özel hastane listesi (faal)')",
              (c.execute("SELECT COUNT(*) FROM hastane_ozel").fetchone()[0], now)); c.commit()
    print("OSM:", n_osm, "| geocode:", n_geo, "| koordinatlı:", c.execute("SELECT SUM(lat IS NOT NULL), COUNT(*) FROM hastane_ozel").fetchone())


if __name__ == "__main__":
    main()
