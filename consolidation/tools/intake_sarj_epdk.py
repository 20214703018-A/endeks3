"""EPDK Şarj@TR (Serbest Erişim Platformu) elektrikli araç şarj istasyonu toplayıcısı.

Kaynak: EPDK Şarj@TR mobil uygulamasının herkese açık REST servisi
  liste : https://sarjtr.epdk.gov.tr/sarjet/api/stations
  detay : https://sarjtr.epdk.gov.tr/sarjet/api/stations/id/{id}/{YYYY-MM-DD HH:MM:00}
Detay: adres, telefon, operatör (lisans sahibi şirket) adı/kimliği, lisans durumu, hizmet şekli,
il/ilçe kodu, yeşil enerji, akıllı şarj; soket başına tip/alt tip/güç (kW)/fiyat (TL/kWh)/anlık durum.

Kullanım:
  python3 intake_sarj_epdk.py full      # liste + tüm detaylar (tam envanter + anlık durum)
  python3 intake_sarj_epdk.py status    # yalnız liste (müsaitlik bayrağı) — sık izleme için hafif
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import urllib3

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, fill_admin_nearest, now_iso, repair_gz_jsonl  # noqa: E402

urllib3.disable_warnings()
BASE = "https://sarjtr.epdk.gov.tr/sarjet/api/stations"
H = {"User-Agent": "Dart/3.1 (dart:io)", "Accept": "application/json", "Accept-Encoding": "gzip"}
_local = threading.local()


def sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(H)
        _local.s.verify = False  # sunucu sertifika zinciri eksik
    return _local.s


def stamp():
    t = dt.datetime.now(ZoneInfo("Europe/Istanbul")) - dt.timedelta(minutes=1)
    return t.strftime("%Y-%m-%d %H:%M:00")


def get_json(url, tries=5):
    err = None
    for i in range(tries):
        try:
            r = sess().get(url, timeout=60)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code} {r.text[:150]}"
        except Exception as e:  # noqa: BLE001
            err = repr(e)
        time.sleep(2 * 2 ** i)
    raise RuntimeError(f"{url}: {err}")


def fetch_list(it: Intake):
    lst = get_json(BASE)
    it.save_json("stations_list", lst, source_url=BASE, method="rest_get")
    rows = [{"station_id": s["id"], "lat": s["lat"], "lon": s["lng"], "brand": s.get("brand"),
             "title": s.get("title"), "green": s.get("green"), "available": s.get("available"),
             "socket_count": len(s.get("sockets") or []), "snapshot_at": now_iso()} for s in lst]
    return lst, pd.DataFrame(rows)


def full(it: Intake, threads: int, delay: float):
    lst, df_list = fetch_list(it)
    it.log(f"liste: {len(lst)} istasyon")
    raw = it.dir / "station_details.jsonl.gz"
    done = set()
    repair_gz_jsonl(raw)
    if raw.exists():
        with gzip.open(raw, "rt") as f:
            for l in f:
                try:
                    done.add(json.loads(l)["id"])
                except Exception:  # noqa: BLE001
                    pass
    todo = [s["id"] for s in lst if s["id"] not in done]
    lock = threading.Lock()

    def work(sid):
        time.sleep(delay)
        return get_json(f"{BASE}/id/{sid}/{quote(stamp())}")

    n = 0
    with ThreadPoolExecutor(threads) as ex, gzip.open(raw, "at") as fo:
        futs = {ex.submit(work, sid): sid for sid in todo}
        for f in as_completed(futs):
            try:
                d = f.result()
            except Exception as e:  # noqa: BLE001
                it.log(f"detay hata {futs[f]}: {e}")
                continue
            d["_fetched_at"] = now_iso()
            with lock:
                fo.write(json.dumps(d, ensure_ascii=False) + "\n")
                n += 1
                if n % 1000 == 0:
                    fo.flush()
                    it.log(f"detay {n}/{len(todo)}")
    build(it, df_list)


def build(it: Intake, df_list=None):
    st, so = [], []
    with gzip.open(it.dir / "station_details.jsonl.gz", "rt") as f:
        for l in f:
            try:
                d = json.loads(l)
            except Exception:  # noqa: BLE001
                continue
            socks = d.get("sockets") or []
            st.append({"station_id": d.get("id"), "title": d.get("title"), "address": d.get("address"),
                       "lat": d.get("lat"), "lon": d.get("lng"), "phone": d.get("phone"),
                       "operator_id": d.get("operatorid"), "operator_title": d.get("operatortitle"),
                       "brand": d.get("brand"), "licence_active": d.get("licenceActive"),
                       "licence_status": d.get("licenceStatus"), "station_active": d.get("stationActive"),
                       "service_type": d.get("serviceType"), "green": d.get("green"), "smart": d.get("smart"),
                       "epdk_city_id": d.get("cityid"), "epdk_district_id": d.get("districtid"),
                       "payment_types": ",".join(p.get("name", "") for p in d.get("paymentTypes") or []),
                       "report_url": d.get("reportUrl"), "reservation_url": d.get("reservationUrl"),
                       "socket_count": len(socks),
                       "dc_count": sum(1 for s in socks if s.get("type") == "DC"),
                       "ac_count": sum(1 for s in socks if s.get("type") == "AC"),
                       "max_power_kw": max([s.get("power") or 0 for s in socks], default=None),
                       "total_power_kw": sum(s.get("power") or 0 for s in socks),
                       "guncellenme_tarihi": d.get("_fetched_at")})
            for s in socks:
                av = (s.get("availability") or [{}])[0]
                so.append({"station_id": d.get("id"), "socket_id": s.get("id"), "type": s.get("type"),
                           "sub_type": s.get("subType"), "socket_number": s.get("socketNumber"),
                           "power_kw": s.get("power"), "price_tl_kwh": s.get("price"),
                           "status": av.get("status"), "status_start": av.get("startTime"),
                           "status_end": av.get("endTime"),
                           "prices_json": json.dumps(s.get("prices"), ensure_ascii=False),
                           "guncellenme_tarihi": d.get("_fetched_at")})
    dst, dso = pd.DataFrame(st).drop_duplicates("station_id"), pd.DataFrame(so).drop_duplicates("socket_id")
    dst = fill_admin_nearest(add_admin(dst))
    it.save_parquet("stations", dst, source_url=BASE + "/id/{id}/{ts}", method="rest_get_detail",
                    note="EPDK Şarj@TR; il/ilçe/mahalle koordinattan kanonik poligonlarla eklendi")
    it.save_parquet("sockets", dso, source_url=BASE + "/id/{id}/{ts}", method="rest_get_detail",
                    note="soket anlık durumu çekim anına aittir")
    it.log(f"build: {len(dst)} istasyon, {len(dso)} soket")


def add_admin(df):
    import duckdb
    canon = str(Path.home() / "Desktop/GEOPROP_CONSOLIDATION/canonical/v1.1/geoprop_canonical_v1_1.duckdb")
    c = duckdb.connect()
    c.sql("load spatial")
    c.sql(f"attach '{canon}' as k (read_only)")
    c.register("pts", df)
    out = c.sql("""
        with m as (select geo_id, name, parent_geo_id, il_geo_id, geometry from k.geo_entity where level='mahalle' and geometry is not null)
        select p.*, m.geo_id mahalle_geo_id, m.name mahalle_adi, m.parent_geo_id ilce_geo_id, i.name ilce_adi,
               m.il_geo_id il_geo_id, l.name il_adi
        from pts p left join m on st_contains(m.geometry, st_point(p.lon, p.lat))
        left join k.geo_entity i on i.geo_id=m.parent_geo_id left join k.geo_entity l on l.geo_id=m.il_geo_id""").df()
    return out.drop_duplicates("station_id")


def status(it: Intake):
    lst, df = fetch_list(it)
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M")
    it.save_parquet(f"status/status_{ts}", df, source_url=BASE, method="rest_get")
    it.log(f"status: {len(df)} istasyon, müsait {int(df.available.sum())}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["full", "status", "build"])
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.6)
    a = ap.parse_args()
    it = Intake("sarj_istasyonlari_epdk")
    if a.mode == "full":
        full(it, a.threads, a.delay)
    elif a.mode == "build":
        build(it)
    else:
        status(it)


if __name__ == "__main__":
    main()
