"""İBB açık veri — 7,5 Mw gece deprem senaryosu mahalle sonuçları + 2017 mahalle bina stoku → cevre.sqlite::deprem_senaryo_mahalle

Kaynak: data.ibb.gov.tr "Deprem Senaryosu Analiz Sonuçları" (İBB / Boğaziçi Ü. Kandilli; ilçe, mahalle, UAVT, çok ağır / ağır / orta /
hafif hasarlı bina, can kaybı, yaralı, altyapı hasarı, geçici barınma) ve "2017 Yılı Mahalle Bazlı Bina Sayıları" (yapım yılı ve kat
grubu). Windows-1254 kodlu CSV. Türetilen: toplam bina = 2017 stoku; ağır+çok ağır hasar oranı (%). Yalnız İstanbul.

  python3.13 collector/ibb_deprem_toplayici.py
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, tr_title  # noqa: E402

RAW = os.path.join(REPO, "warehouse", "raw", "ibb")
OUT = os.path.join(REPO, "warehouse", "product", "cevre.sqlite")


def _oku(ad):
    with open(os.path.join(RAW, ad), encoding="cp1254", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _i(v):
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def main():
    sen, bina = _oku("deprem_senaryosu.csv"), _oku("mahalle_bina_2017.csv")
    stok = {r["mahalle_uavt"]: r for r in bina}
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS deprem_senaryo_mahalle (uavt TEXT PRIMARY KEY, il TEXT, ilce TEXT, mahalle TEXT, il_norm TEXT, ilce_norm TEXT, mahalle_norm TEXT,
        cok_agir INTEGER, agir INTEGER, orta INTEGER, hafif INTEGER, can_kaybi INTEGER, agir_yarali INTEGER, hastane INTEGER, hafif_yarali INTEGER,
        dogalgaz_hasar INTEGER, icme_suyu_hasar INTEGER, atik_su_hasar INTEGER, gecici_barinma INTEGER,
        bina_toplam INTEGER, bina_1980_oncesi INTEGER, bina_1980_2000 INTEGER, bina_2000_sonrasi INTEGER, agir_hasar_orani REAL, guncellenme TEXT);
    CREATE INDEX IF NOT EXISTS idx_deprem_mahalle_ad ON deprem_senaryo_mahalle (ilce_norm, mahalle_norm);
    CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
    """)
    now = datetime.now(timezone.utc).isoformat(); n = 0
    for r in sen:
        u = r["mahalle_koy_uavt"]; s = stok.get(u, {})
        top = sum(x for x in (_i(s.get("1980_oncesi")), _i(s.get("1980-2000_arasi")), _i(s.get("2000_sonrasi"))) if x is not None) if s else None
        ca, a = _i(r["cok_agir_hasarli_bina_sayisi"]), _i(r["agir_hasarli_bina_sayisi"])
        oran = round((ca + a) / top * 100, 1) if top and ca is not None and a is not None else None
        c.execute("INSERT OR REPLACE INTO deprem_senaryo_mahalle VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (u, "İstanbul", tr_title(r["ilce_adi"]), tr_title(r["mahalle_adi"]), "istanbul", normalize_name(r["ilce_adi"]), normalize_name(r["mahalle_adi"]),
                   ca, a, _i(r["orta_hasarli_bina_sayisi"]), _i(r["hafif_hasarli_bina_sayisi"]), _i(r["can_kaybi_sayisi"]), _i(r["agir_yarali_sayisi"]),
                   _i(r["hastanede_tedavi_sayisi"]), _i(r["hafif_yarali_sayisi"]), _i(r["dogalgaz_boru_hasari"]), _i(r["icme_suyu_boru_hasari"]),
                   _i(r["atik_su_boru_hasari"]), _i(r["gecici_barinma"]), top, _i(s.get("1980_oncesi")), _i(s.get("1980-2000_arasi")), _i(s.get("2000_sonrasi")), oran, now)); n += 1
    c.execute("INSERT OR REPLACE INTO kapsama VALUES ('deprem_senaryo_mahalle', ?, ?, 'İBB açık veri: 7,5 Mw gece deprem senaryosu (mahalle) + 2017 bina stoku')", (n, now))
    c.commit(); c.close(); print("mahalle:", n)


if __name__ == "__main__":
    main()
