"""UAB Denizcilik GM — ISPS Kod kapsamındaki liman tesisleri (safeports xls) → onemli_tesisler.sqlite::liman

Kaynak: denizcilik.uab.gov.tr/isps_tesisler (güncel liste sitede yalnız görsel; makine okunur son sürüm
safeports02082022.xls, 02/08/2022). Alınan alanlar: ülke liman kodu / IMO tesis no, tesis adı, faaliyet türleri,
işletici kuruluş, il/ilçe (adresten), koordinat. PFSO irtibat bilgileri (kişi/telefon) ALINMAZ (PII).
Koordinat: listede derece-dakika(-saniye) → ondalık (dakika hassasiyeti ≈ 1 km). 3 km içinde OSM liman/iskele
nesnesi (ad benzer ya da tek) varsa nokta OSM'den ("osm: yakın liman"), yoksa resmî koordinat "resmî (dakika hassasiyeti, yaklaşık)".
Boylamı eksik satırlar → OSM ad eşleşmesi (aynı il) → yoksa 'bulunamadı'.

  python3.13 collector/uab_liman_toplayici.py --xls warehouse/raw/liman/safeports02082022.xls
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

import xlrd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import haversine_km, normalize_name, tr_title  # noqa: E402
from geoprop.idari import AdminLookup  # noqa: E402

OUT = os.path.join(REPO, "warehouse", "product", "onemli_tesisler.sqlite")
_DMS = re.compile(r"(\d{1,3})\s*[0°]?\s*(\d{1,2})\s*'?\s*(?:(\d{1,2}(?:[.,]\d+)?)\s*\"?)?\s*([NSEW])")
_KOD = re.compile(r"(\d{7})\s*\(?(TR[A-Z]{3}-\d{4})?")


def _dms(text: str) -> tuple[float | None, float | None]:
    """'390 52' N   0260 09' E' → (39.867, 26.15). '390' = 39°(0 ayırıcı); '0260' = 026°."""
    lat = lon = None
    for d, m, s, h in _DMS.findall(text or ""):
        d = d[:-1] if len(d) == 3 and h in "NS" else d          # 390 → 39
        d = d[:-1] if len(d) == 4 else d                         # 0260 → 026
        val = int(d) + int(m) / 60 + (float(s.replace(",", ".")) / 3600 if s else 0)
        if h == "N" and 35 <= val <= 43:
            lat = round(val, 5)
        elif h == "E" and 25 <= val <= 45:
            lon = round(val, 5)
    return lat, lon


def _il_ilce(adres: str) -> tuple[str | None, str | None]:
    m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü\.\s]+?)\s*[/\-]\s*([A-ZÇĞİÖŞÜa-zçğıöşü]+)\s*$", (adres or "").strip())
    if not m:
        return None, None
    return tr_title(m.group(2)), tr_title(m.group(1).split()[-1]) if m.group(1).split() else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--xls", required=True); a = ap.parse_args()
    sh = xlrd.open_workbook(a.xls).sheet_by_index(0)
    rows = []
    for r in range(sh.nrows):
        kod, ad, faaliyet, koord, isletici, adres = (str(sh.cell_value(r, c)).strip() for c in range(1, 7))
        m = _KOD.search(kod)
        if not m or not ad:
            continue
        il, ilce = _il_ilce(adres)
        lat, lon = _dms(koord)
        rows.append((m.group(1), m.group(2), re.sub(r"\s+", " ", ad), re.sub(r"\s+", " ", faaliyet), re.sub(r"\s+", " ", isletici), il, ilce, lat, lon))
    c = sqlite3.connect(OUT, timeout=60); now = datetime.now(timezone.utc).isoformat()
    c.executescript("""CREATE TABLE IF NOT EXISTS liman (id INTEGER PRIMARY KEY, kod TEXT, imo_tesis_no TEXT, ad TEXT, ad_norm TEXT, faaliyet TEXT, isletici TEXT,
                       il TEXT, ilce TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, liste_tarihi TEXT, guncellenme TEXT, UNIQUE (kod, ad));
                       CREATE INDEX IF NOT EXISTS idx_liman_lat ON liman (lat, lon);
                       CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);""")
    A = AdminLookup(os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite"))
    o = sqlite3.connect(f"file:{os.path.join(REPO, 'warehouse', 'product', 'osm_poi.sqlite')}?mode=ro", uri=True)
    osm = [(ad, normalize_name(ad or ""), la, lo) for ad, la, lo in o.execute("SELECT ad, lat, lon FROM poi WHERE alt_kategori IN ('liman','iskele','marina') AND ad IS NOT NULL")]
    n_osm = n_res = n_yok = 0
    for kod, imo, ad, faaliyet, isletici, il, ilce, lat, lon in rows:
        kaynak = None
        if lat is not None and lon is not None:
            il2, ilce2 = A.lookup(lat, lon)
            yakin = [x for x in osm if haversine_km(lat, lon, x[2], x[3]) <= 3]
            best = difflib.get_close_matches(normalize_name(ad), [x[1] for x in yakin], n=1, cutoff=0.5)
            # Ad benzerliği ya da 3 km içinde TEK OSM nesnesi: oturt. Yoğun sanayi kıyısında (Dilovası) ad uyuşmayan
            # en yakın nesneye yapıştırma yanlış tesise götürür → resmî koordinat korunur (yaklaşık).
            if best or len(yakin) == 1:
                x = yakin[[y[1] for y in yakin].index(best[0])] if best else yakin[0]
                lat, lon, kaynak = x[2], x[3], "osm: yakın liman/iskele (resmî koordinat ≤3 km)"; n_osm += 1
            else:
                kaynak = "resmî (derece-dakika, ≈1 km hassasiyet, yaklaşık)"; n_res += 1
            il2, ilce2 = A.lookup(lat, lon)   # koordinat varsa idari sınır adresteki tahmine üstün
            il, ilce = (tr_title(il2) if il2 else il), (tr_title(ilce2) if ilce2 else ilce)
        else:
            adaylar = [x for x in osm if il and (A.lookup(x[2], x[3])[0] or "") and normalize_name(A.lookup(x[2], x[3])[0]) == normalize_name(il)]
            best = difflib.get_close_matches(normalize_name(ad), [x[1] for x in adaylar], n=1, cutoff=0.6)
            if best:
                x = adaylar[[y[1] for y in adaylar].index(best[0])]; lat, lon, kaynak = x[2], x[3], "osm: ad eşleşmesi (il içi)"; n_osm += 1
            else:
                lat = lon = None; kaynak = "bulunamadı"; n_yok += 1
        c.execute("INSERT OR REPLACE INTO liman (kod, imo_tesis_no, ad, ad_norm, faaliyet, isletici, il, ilce, lat, lon, koordinat_kaynagi, liste_tarihi, guncellenme) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (kod, imo, ad, normalize_name(ad), faaliyet, isletici, il, ilce, lat, lon, kaynak, "2022-08-02", now))
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('liman', ?, ?, 'UAB Denizcilik GM ISPS liman tesisleri listesi (02.08.2022)')", (len(rows), now)); c.commit()
    print(f"liman: {len(rows)} | osm: {n_osm} | resmî koordinat: {n_res} | bulunamadı: {n_yok}")


if __name__ == "__main__":
    main()
