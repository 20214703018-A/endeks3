"""Semt pazarları → warehouse/product/pazarlar.sqlite

Kaynaklar:
  1. OSM (osm_poi.sqlite, amenity=marketplace): ad, koordinat; kapalı/açık = building etiketi veya adda "kapalı";
     kurulum günü = addaki gün ("Cuma Pazarı") veya opening_hours (Mo..Su).
  2. İBB Açık Veri "İstanbul İli Semt Pazarları" (balıkçı olan 2025 + olmayan 2024): ilçe, ad, koordinat, MAHALLE, gün, tip (Kapalı/Alan/Küçük)
  3. İzmir Büyükşehir Açık Veri "Semt Pazar Yerleri": ilçe, MAHALLE, ad, koordinat, açıklama (gün)
Belediye kayıtları OSM kaydıyla 150 m içinde aynı gün/adsa tekilleştirilir (belediye kaydı esas). İl/ilçe: idari sınır poligonu.
Tablo: pazar(id, kaynak, il, ilce, mahalle, ad, gunler (virgüllü), kapali (1/0/NULL), tip, lat, lon, guncellenme)
"""
from __future__ import annotations
import json, os, re, sqlite3, sys
from datetime import datetime, timezone
import openpyxl
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, haversine_km, tr_title, tr_upper  # noqa: E402
from geoprop.idari import AdminLookup  # noqa: E402
OUT = os.path.join(REPO, "warehouse", "product", "pazarlar.sqlite")
RAW = os.path.join(REPO, "warehouse", "raw", "pazar")
GUNLER = ["pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar"]
OSM_GUN = {"Mo": "pazartesi", "Tu": "sali", "We": "carsamba", "Th": "persembe", "Fr": "cuma", "Sa": "cumartesi", "Su": "pazar"}


def gunler_bul(*metinler) -> list[str]:
    out = []
    for m in metinler:
        n = normalize_name(m or "")
        for g in GUNLER:
            if re.search(rf"\b{g}\b", n) and g not in out and not (g == "pazar" and "pazar" in n and not re.search(r"\bpazar gun", n)):
                out.append(g)
        for k, v in OSM_GUN.items():   # opening_hours
            if re.search(rf"\b{k}\b", m or "") and v not in out: out.append(v)
    return out


def main():
    A = AdminLookup(os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite"))
    if os.path.exists(OUT): os.remove(OUT)
    c = sqlite3.connect(OUT); now = datetime.now(timezone.utc).isoformat()
    c.executescript("""CREATE TABLE pazar (id INTEGER PRIMARY KEY, kaynak TEXT, il TEXT, ilce TEXT, mahalle TEXT, ad TEXT, gunler TEXT, kapali INTEGER, tip TEXT,
                       lat REAL, lon REAL, guncellenme TEXT); CREATE INDEX idx_pazar_lat ON pazar (lat, lon);
                       CREATE TABLE kapsama (kaynak TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT);""")
    rows = []
    # 2) İBB
    for fn, tip_col in (("ibb_balikci_olan_2025.xlsx", 7), ("ibb_balikci_olmayan_2024.xlsx", 7)):
        ws = openpyxl.load_workbook(os.path.join(RAW, fn), read_only=True).worksheets[0]
        for r in list(ws.iter_rows(values_only=True))[1:]:
            if not r or not r[2]: continue
            m = re.match(r"\s*([\d\.]+)\s*,\s*([\d\.]+)", str(r[2]))
            if not m: continue
            tip = str(r[tip_col] or "").strip()
            rows.append(("ibb_acik_veri", "İSTANBUL", tr_title(r[0]), tr_title(re.sub(r"\s*(mh|mah|mahallesi)\.?\s*$", "", str(r[3] or ""), flags=re.I)), tr_title(r[1]) + " Pazarı",
                         ",".join(gunler_bul(str(r[6] or ""))), 1 if "kapal" in tip.lower() else (0 if tip else None), tip or None, float(m.group(1)), float(m.group(2))))
    # 3) İzmir
    ws = openpyxl.load_workbook(os.path.join(RAW, "izbb_semt_pazar_yerleri.xlsx"), read_only=True).worksheets[0]
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if not r or r[2] is None: continue
        ad = str(r[6] or ""); rows.append(("izbb_acik_veri", "İZMİR", tr_title(r[0]), tr_title(r[5]), ad, ",".join(gunler_bul(str(r[3] or ""), ad)),
                                             1 if "kapal" in normalize_name(ad) else None, None, float(r[2]), float(r[7])))
    # 1) OSM (belediye kaydı 150 m içinde varsa atla)
    bel = [(x[8], x[9]) for x in rows]
    o = sqlite3.connect(f"file:{os.path.join(REPO, 'warehouse', 'product', 'osm_poi.sqlite')}?mode=ro", uri=True)
    atlanan = 0
    for ad, et, lat, lon in o.execute("SELECT ad, etiketler, lat, lon FROM poi WHERE alt_kategori='pazar'"):
        if any(abs(lat - la) < 0.002 and abs(lon - lo) < 0.003 and haversine_km(lat, lon, la, lo) < 0.15 for la, lo in bel):
            atlanan += 1; continue
        t = json.loads(et); il, ilce = A.lookup(lat, lon)
        kapali = 1 if (t.get("building") in ("yes", "roof", "retail", "commercial") or "kapali" in normalize_name(ad or "")) else None
        mah = t.get("addr:district") or t.get("addr:suburb")
        rows.append(("osm", tr_upper(il) if il else None, tr_title(ilce) if ilce else None, tr_title(mah) if mah else None, ad, ",".join(gunler_bul(ad, t.get("opening_hours"))), kapali, None, lat, lon))
    c.executemany("INSERT INTO pazar (kaynak, il, ilce, mahalle, ad, gunler, kapali, tip, lat, lon, guncellenme) VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (now,) for r in rows])
    for k in ("ibb_acik_veri", "izbb_acik_veri", "osm"):
        c.execute("INSERT INTO kapsama VALUES (?,?,?)", (k, sum(1 for r in rows if r[0] == k), now))
    c.commit(); c.close()
    print(f"pazar: {len(rows)} (OSM'de belediye kaydıyla çakışan {atlanan} atlandı) → {OUT}")


if __name__ == "__main__":
    main()
