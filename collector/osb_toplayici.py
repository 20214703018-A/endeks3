"""OSBÜK resmî OSB listesi → onemli_tesisler.sqlite::osb
osbuk.org/view/sayilarlaosb/osbliste.php: il, ünvan, tür (KARMA/İHTİSAS/TDİOSB), fiili durum, alan (ha), parsel sayısı.
Koordinat/poligon: OSM osb poligonu (osm_poi, aynı il + ad benzerliği) → poligon + merkez; yoksa geocode (ad + il, yaklaşık).
"""
from __future__ import annotations
import difflib, html, json, os, re, sqlite3, sys
from datetime import datetime, timezone
import urllib.request
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, tr_title  # noqa: E402
from geoprop.idari import AdminLookup  # noqa: E402
OUT = os.path.join(REPO, "warehouse", "product", "onemli_tesisler.sqlite")


def main():
    h = urllib.request.urlopen(urllib.request.Request("https://osbuk.org/view/sayilarlaosb/osbliste.php", headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode("utf-8", "ignore")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        cells = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", td))).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 8 and cells[0].isdigit(): rows.append(cells[:8])
    c = sqlite3.connect(OUT, timeout=60); now = datetime.now(timezone.utc).isoformat()
    c.executescript("""CREATE TABLE IF NOT EXISTS osb (id INTEGER PRIMARY KEY, il TEXT, ilce TEXT, ad TEXT, ad_norm TEXT, tur TEXT, durum TEXT, bakanlik TEXT, alan_ha REAL, parsel_sayisi INTEGER,
                       lat REAL, lon REAL, geometri TEXT, koordinat_kaynagi TEXT, guncellenme TEXT, UNIQUE (il, ad)); CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);""")
    for _, il, ad, tur, durum, bak, alan, parsel in rows:
        c.execute("INSERT OR IGNORE INTO osb (il, ad, ad_norm, tur, durum, bakanlik, alan_ha, parsel_sayisi, guncellenme) VALUES (?,?,?,?,?,?,?,?,?)",
                  (il.upper().replace("I", "I"), ad + " OSB", normalize_name(ad), tur, durum, bak, float(alan.replace(",", ".")) if alan else None, int(parsel) if parsel.isdigit() else None, now))
    c.commit(); print("OSBÜK satır:", len(rows))
    A = AdminLookup(os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite"))
    o = sqlite3.connect(f"file:{os.path.join(REPO, 'warehouse', 'product', 'osm_poi.sqlite')}?mode=ro", uri=True)
    osm = [(ad, normalize_name(re.sub(r"Organize Sanayi Bölgesi|OSB|Organize Sanayii", " ", ad or "", flags=re.I)), lat, lon, g) for ad, lat, lon, g in o.execute("SELECT ad, lat, lon, geometri FROM poi WHERE alt_kategori='osb'")]
    from collector.geocode import adres_kodla
    n_osm = n_geo = 0
    for oid, il, ad, adn in c.execute("SELECT id, il, ad, ad_norm FROM osb WHERE lat IS NULL").fetchall():
        il_n = normalize_name(il)
        pool = [x for x in osm if x[0] and (A.lookup(x[2], x[3])[0] or "") and normalize_name(A.lookup(x[2], x[3])[0]) == il_n]
        names = [x[1] for x in pool]; best = difflib.get_close_matches(adn, names, n=1, cutoff=0.6)
        if not best:   # il adı OSB adında geçmeyebilir: ad kelimeleri OSM adında
            cand = [x for x in pool if adn.split()[0] in x[1]] if adn else []
            best = [cand[0][1]] if len(cand) == 1 else []
        if best:
            x = pool[names.index(best[0])]; il2, ilce2 = A.lookup(x[2], x[3])
            c.execute("UPDATE osb SET lat=?, lon=?, geometri=?, koordinat_kaynagi=?, ilce=? WHERE id=?", (x[2], x[3], x[4], "osm: poligon (ad eşleşmesi)", tr_title(ilce2) if ilce2 else None, oid)); n_osm += 1
            continue
        hit = adres_kodla("", il, None, ad.replace(" OSB", " Organize Sanayi Bölgesi"))
        if hit:
            il2, ilce2 = A.lookup(hit[0], hit[1])
            c.execute("UPDATE osb SET lat=?, lon=?, koordinat_kaynagi=?, ilce=? WHERE id=?", (hit[0], hit[1], hit[2], tr_title(ilce2) if ilce2 else None, oid)); n_geo += 1
        else:
            c.execute("UPDATE osb SET koordinat_kaynagi='bulunamadı' WHERE id=?", (oid,))
        c.commit()
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('osb', ?, ?, 'OSBÜK osbliste.php')", (c.execute("SELECT COUNT(*) FROM osb").fetchone()[0], now)); c.commit()
    print("OSM poligon:", n_osm, "| geocode:", n_geo, "| koordinatlı:", c.execute("SELECT SUM(lat IS NOT NULL), COUNT(*) FROM osb").fetchone())


if __name__ == "__main__":
    main()
