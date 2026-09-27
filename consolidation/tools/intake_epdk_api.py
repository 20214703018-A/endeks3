"""EPDK resmî REST web servisleri (apigateway.epdk.gov.tr) toplayıcısı.

Belge: https://www.epdk.gov.tr/Detay/Icerik/3-0-226/web-servisler (herkese açık; GET + JSON gövde)
  sarjIstasyonlari               → EPDK Şarj İstasyonları Listesi (lisans no, işletmeci, dağıtım şirketi, adres,
                                   koordinat, soketler) — tek istek
  petrolBayiSatisFiyatBulten     → Günlük akaryakıt bayi satış fiyatı bülteni (Türkiye ağırlıklı ortalama, yakıt türü)
  lpgBayiSatisFiyatBultenGunluk  → Günlük LPG bayi satış fiyatı bülteni
  diğer lisans sorgu servisleri  → yetki isteyenler (401) atlanır, durum manifest'e yazılır
Servisin istek kotası dar (429) — istekler arası ~12 sn; kaldığı yerden devam eder; en yeni günden geriye gider.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, fill_admin_nearest, now_iso  # noqa: E402

B = "https://apigateway.epdk.gov.tr/"
LICENCE_SERVICES = ["sarjAgiIsletmeciLisansiSorgula", "elektrikUretimLisansiSorgula", "elektrikDagitimLisansiSorgula",
                    "elektrikTedarikLisansiSorgula", "elektrikOSBDagitimLisansiSorgula", "dogalgazDagitimLisansiSorgula",
                    "petrolDagiticiLisansSorgula", "lpgDagiticiLisansiSorgula", "petrolBayilikLisansiSorgula",
                    "lpgBayilikLisansiSorgula", "petrolDepolamaLisansSorgula", "lpgDepolamaLisansSorgula"]


def call(it, svc, body, max_wait=6 * 3600):  # kota (ERR-227) dolunca 10 dk aralıklarla bekler
    waited = 0
    while True:
        try:
            r = it.get(B + svc, method="GET", data=json.dumps(body), timeout=180, tries=2,
                       headers={"Content-Type": "application/json", "Accept": "application/json"})
        except RuntimeError:  # 429/5xx yeniden denemeleri tükendi → bekle
            if waited >= max_wait:
                raise
            time.sleep(600)
            waited += 600
            continue
        if r.status_code == 429 and waited < max_wait:
            it.log(f"{svc}: kota dolu (429), 10 dk bekleniyor")
            time.sleep(600)
            waited += 600
            continue
        return r


def main():
    it = Intake("epdk_api", rate=12.0)
    it.s.headers["User-Agent"] = "Mozilla/5.0"
    # 1) şarj istasyonları listesi
    if not (it.dir / "sarj_istasyonlari_epdk_liste.parquet").exists():
        r = call(it, "sarjIstasyonlari", {})
        if r.status_code == 200:
            j = r.json()
            it.save_json("raw/sarjIstasyonlari.json", j, source_url=B + "sarjIstasyonlari", method="rest_get_json_body",
                         rows=j.get("numRows"))
            rows = []
            for d in j.get("data") or []:
                socks = d.get("soketler") or []
                base = {k: v for k, v in d.items() if k != "soketler"}
                base.update({"lat": d.get("enlem"), "lon": d.get("boylam"), "soket_sayisi": len(socks),
                             "dc_soket": sum(1 for s in socks if s.get("soketTipi") == "DC"),
                             "ac_soket": sum(1 for s in socks if s.get("soketTipi") == "AC"),
                             "toplam_guc_kw": sum(float(s.get("soketGucu") or 0) for s in socks),
                             "soketler_json": json.dumps(socks, ensure_ascii=False), "guncellenme_tarihi": now_iso()})
                rows.append(base)
            df = pd.DataFrame(rows)
            from intake_sarj_epdk import add_admin
            df = fill_admin_nearest(add_admin(df.rename(columns={"sarjIstasyonuNo": "station_id"})))
            it.save_parquet("sarj_istasyonlari_epdk_liste", df, source_url=B + "sarjIstasyonlari", method="rest_get_json_body",
                            note="EPDK resmî şarj istasyonları listesi; il/ilçe/mahalle koordinattan")
            it.log(f"şarj listesi: {len(df)} istasyon")
        else:
            it.log(f"şarj listesi HTTP {r.status_code}")
    # 2) lisans servisleri (yetki gerekip gerekmediğini kaydet; gerekmiyorsa tam listeyi al)
    status_file = it.dir / "licence_services_status.json"
    if not status_file.exists():
        st = {}
        for svc in LICENCE_SERVICES:
            r = call(it, svc, {}, max_wait=1200)
            st[svc] = r.status_code
            if r.status_code == 200:
                it.save_bytes(f"raw/{svc}.json", r.content, source_url=B + svc, method="rest_get_json_body",
                              rows=(r.json() or {}).get("numRows"))
            it.log(f"{svc}: HTTP {r.status_code}")
        status_file.write_text(json.dumps(st, indent=1))
    # 3) günlük fiyat bültenleri (en yeniden geriye)
    for svc, start in (("petrolBayiSatisFiyatBulten", dt.date(2015, 1, 1)), ("lpgBayiSatisFiyatBultenGunluk", dt.date(2015, 1, 1))):
        out = it.dir / f"{svc}.jsonl"
        done = set()
        if out.exists():
            done = {json.loads(l)["date"] for l in out.read_text().split("\n") if l.strip()}
        d = dt.date.today()
        while d >= start:
            if d.isoformat() not in done:
                r = call(it, svc, {"raporTarihi": d.strftime("%d.%m.%Y")})
                rec = {"date": d.isoformat(), "status": r.status_code, "at": now_iso()}
                if r.status_code == 200:
                    rec["data"] = (r.json() or {}).get("data")
                with out.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if d.day == 1:
                    it.log(f"{svc}: {d} HTTP {r.status_code}")
            d -= dt.timedelta(days=1)
        recs = []
        for l in out.read_text().split("\n"):
            if not l.strip():
                continue
            x = json.loads(l)
            for row in x.get("data") or []:
                recs.append({"bulten_tarihi": x["date"], "tarih": row.get("Tarih"), "yakit": row.get("Yakıt"),
                             "olcu_birimi": row.get("Ölçü Birimi"), "fiyat_tl": row.get("Fiyat"),
                             "kapsam": "Türkiye ağırlıklı ortalama (EPDK bülteni)", "guncellenme_tarihi": x["at"]})
        it.save_parquet(svc, pd.DataFrame(recs), source_url=B + svc, method="rest_get_json_body_daily")
        it.log(f"{svc}: {len(recs)} satır")


if __name__ == "__main__":
    main()
