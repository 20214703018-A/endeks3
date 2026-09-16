"""Emlakjet bölge endeks CSV'lerini ürün ambarına işler.

Kaynak: VERİLER/.../emlakjet-bolge-*.csv (konut/arsa/tümü). İl ve ilçe düzeyinde
m² fiyat, amortisman (yıl), getiri (kira getirisi %), kira m² fiyatı/kira fiyatı,
ortalama bina yaşı, ilan süresi vb. içerir. Konut/işyeri için amortisman ve kira
doludur; arsa için (kira olmadığından) çoğu boştur.

Çıktı: warehouse/product/emlakjet_bolge_endeks.sqlite → bolge_endeks
Aynı (tip, seviye, cityId, countyId, donem) için en yeni toplanma kaydı tutulur.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sqlite3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GLOB = os.path.join(REPO, "VERİLER", "Öğelerle Yeni Klasör 2", "emlakjet-bolge-*.csv")
DEFAULT_OUT = os.path.join(REPO, "warehouse", "product", "emlakjet_bolge_endeks.sqlite")

NUMERIC = {
    "cityId", "countyId", "districtId", "m2Fiyat", "m2FiyatMin", "m2FiyatMax",
    "ortFiyat", "ortM2", "ilanSayisi", "aylikDegisim", "yillikDegisim",
    "amortisman", "getiri", "ortBinaYasi", "ilanSuresi", "endeks",
    "kiraM2Fiyat", "kiraFiyat", "kiraOrtM2", "kiraIlanSayisi",
    "kiraYillikDegisim", "kiraIlanSuresi",
}
KEEP = [
    "donem", "tip", "seviye", "il", "ilce", "mahalle", "cityId", "countyId", "districtId",
    "m2Fiyat", "ortM2", "ilanSayisi", "aylikDegisim", "yillikDegisim",
    "amortisman", "getiri", "ortBinaYasi", "ilanSuresi", "endeks",
    "kiraM2Fiyat", "kiraFiyat", "kiraYillikDegisim", "toplanmaZamani",
]


def _num(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def build(pattern: str, out: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f"CSV bulunamadı: {pattern}")
    # (tip, seviye, cityId, countyId, donem) → en yeni toplanma kaydı
    best: dict[tuple, dict] = {}
    for path in files:
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                key = (row.get("tip"), row.get("seviye"), row.get("cityId"),
                       row.get("countyId"), row.get("districtId"), row.get("donem"))
                prev = best.get(key)
                if prev is None or (row.get("toplanmaZamani") or "") > (prev.get("toplanmaZamani") or ""):
                    best[key] = row

    conn = sqlite3.connect(out)
    conn.executescript(
        """
        DROP TABLE IF EXISTS bolge_endeks;
        CREATE TABLE bolge_endeks (
            tip TEXT, seviye TEXT, il TEXT, ilce TEXT, mahalle TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER,
            donem TEXT, m2_fiyat REAL, ort_m2 REAL, ilan_sayisi REAL,
            aylik_degisim REAL, yillik_degisim REAL,
            amortisman REAL, getiri REAL, ort_bina_yasi REAL, ilan_suresi REAL, endeks REAL,
            kira_m2_fiyat REAL, kira_fiyat REAL, kira_yillik_degisim REAL,
            toplanma TEXT,
            PRIMARY KEY (tip, seviye, city_id, county_id, district_id, donem)
        );
        CREATE INDEX idx_bolge_lokasyon ON bolge_endeks(tip, il, ilce);
        """
    )
    written = 0
    for row in best.values():
        conn.execute(
            "INSERT OR REPLACE INTO bolge_endeks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row.get("tip"), row.get("seviye"), row.get("il"), row.get("ilce"), row.get("mahalle"),
                _num(row.get("cityId")), _num(row.get("countyId")), _num(row.get("districtId")), row.get("donem"),
                _num(row.get("m2Fiyat")), _num(row.get("ortM2")), _num(row.get("ilanSayisi")),
                _num(row.get("aylikDegisim")), _num(row.get("yillikDegisim")),
                _num(row.get("amortisman")), _num(row.get("getiri")), _num(row.get("ortBinaYasi")),
                _num(row.get("ilanSuresi")), _num(row.get("endeks")),
                _num(row.get("kiraM2Fiyat")), _num(row.get("kiraFiyat")), _num(row.get("kiraYillikDegisim")),
                row.get("toplanmaZamani"),
            ),
        )
        written += 1
    conn.commit()
    dist = conn.execute("SELECT tip, seviye, COUNT(*) FROM bolge_endeks GROUP BY tip, seviye").fetchall()
    conn.close()
    print(f"{written:,} kayıt → {out}")
    for tip, seviye, n in dist:
        print(f"  {tip}/{seviye}: {n}")


def main() -> int:
    p = argparse.ArgumentParser(description="Emlakjet bölge endeks toplayıcısı")
    p.add_argument("--glob", default=DEFAULT_GLOB)
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args()
    build(args.glob, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
