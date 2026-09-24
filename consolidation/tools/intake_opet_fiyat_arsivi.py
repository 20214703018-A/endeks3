"""Opet akaryakıt fiyat arşivi — il/ilçe bazında günlük pompa fiyatları, 2015 → bugün, tüm ürünler.

Kaynak: https://www.opet.com.tr/fiyat-arsivi sayfasının herkese açık servisi (robots.txt izinli)
  iller   : https://api.opet.com.tr/api/fuelprices/provinces
  ilçeler : https://api.opet.com.tr/api/fuelprices/provinces/{il}/districts   (kod, ad, enlem/boylam, merkez mi)
  arşiv   : https://api.opet.com.tr/api/fuelprices/prices/archive?DistrictCode=&StartDate=&EndDate=&IncludeAllProducts=true
            (en fazla 3 yıllık aralık)
Değerler kayıpsız, sade biçimde saklanır: ilçe × gün × ürün kodu × fiyat (TL/L); ürün adları ayrı sözlükte.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

B = "https://api.opet.com.tr/api"
WINDOWS = [("2015-01-01", "2017-12-31"), ("2018-01-01", "2020-12-31"), ("2021-01-01", "2023-12-31"),
           ("2024-01-01", dt.date.today().isoformat())]


def main():
    it = Intake("akaryakit_opet_fiyat_arsivi", rate=1.0)
    it.s.headers.update({"Channel": "Web", "Accept": "application/json", "Origin": "https://www.opet.com.tr",
                         "Referer": "https://www.opet.com.tr/fiyat-arsivi"})
    provs = it.get(B + "/fuelprices/provinces").json()
    it.save_json("provinces.json", provs, source_url=B + "/fuelprices/provinces", method="rest_get")
    dists = []
    for p in provs:
        ds = it.get(B + f"/fuelprices/provinces/{p['code']}/districts").json()
        for d in ds:
            dists.append({**d, "province_code": p["code"], "province_name": p["name"]})
    it.save_json("districts.json", dists, source_url=B + "/fuelprices/provinces/{il}/districts", method="rest_get")
    it.log(f"{len(provs)} il, {len(dists)} ilçe")
    out = it.dir / "prices_compact.jsonl.gz"
    done = set()
    if out.exists():
        from intake_common import repair_gz_jsonl
        repair_gz_jsonl(out)
        with gzip.open(out, "rt") as f:
            for l in f:
                try:
                    x = json.loads(l)
                    done.add((x["district"], x["window"]))
                except ValueError:
                    pass
    products = {}
    n = 0
    with gzip.open(out, "at") as fo:
        for d in dists:
            for a, b in WINDOWS:
                if (d["code"], a) in done:
                    continue
                try:
                    r = it.get(B + "/fuelprices/prices/archive", timeout=120,
                               params={"DistrictCode": d["code"], "StartDate": a + "T00:00:00.000Z",
                                       "EndDate": b + "T23:59:59.000Z", "IncludeAllProducts": "true"})
                    days = r.json() if r.status_code == 200 else None
                except Exception as e:  # noqa: BLE001
                    it.log(f"hata {d['code']} {a}: {e}")
                    continue
                rows = []
                for day in days or []:
                    for pr in day.get("prices") or []:
                        products.setdefault(pr.get("productCode"), (pr.get("productName"), pr.get("productShortName")))
                        rows.append([pr.get("priceDate", day.get("day"))[:10], pr.get("productCode"), pr.get("amount")])
                fo.write(json.dumps({"district": d["code"], "window": a, "status": r.status_code, "at": now_iso(),
                                     "rows": rows}, ensure_ascii=False) + "\n")
                n += 1
                if n % 200 == 0:
                    fo.flush()
                    it.log(f"{n} ilçe-dönem ({d['province_name']}/{d['name']})")
    it.save_json("products.json", {k: {"name": v[0], "short": v[1]} for k, v in products.items()},
                 source_url=B + "/fuelprices/prices/archive", method="derived")
    # düz tablo
    meta = {d["code"]: d for d in dists}
    recs = []
    with gzip.open(out, "rt") as f:
        for l in f:
            try:
                x = json.loads(l)
            except ValueError:
                continue
            m = meta.get(x["district"], {})
            for day, code, amt in x["rows"]:
                recs.append((x["district"], m.get("name"), m.get("province_code"), m.get("province_name"),
                             day, code, amt))
    df = pd.DataFrame(recs, columns=["ilce_kodu", "ilce_adi", "il_kodu", "il_adi", "tarih", "urun_kodu", "fiyat_tl"])
    df = df.drop_duplicates()
    it.save_parquet("opet_ilce_gunluk_fiyat", df, source_url=B + "/fuelprices/prices/archive",
                    method="rest_get_windows", note="Opet pompa fiyatı; ilçe kodları Opet'e özgü (ilçe adı+il ile eşlenir)")
    it.log(f"bitti: {len(df)} satır")


if __name__ == "__main__":
    main()
