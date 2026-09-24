"""ETBİS (Ticaret Bakanlığı Elektronik Ticaret Bilgi Sistemi) kayıtlı e-ticaret siteleri — il/ilçe bazında.

Kaynak: https://etbis.ticaret.gov.tr/tr/SiteSorgulama (herkese açık "Kayıtlı Site Sorgula"; ~6.021 sayfa × 10)
  liste  : ?page=N&cityId=&districtId=&sector=&isItCrossBorder=  → işletme unvanı, e-ticaret sitesi, mobil uygulama, siteId
  ilçeler: /tr/SiteSorgulama/GetDistricts?cityId=...
  profil : /tr/SiteSorgulamaSonuc?siteId=...  → MERSİS/vergi no (Bakanlık maskeli yayımlar), işletme adı, ETBİS kayıt
           tarihi, işletme türü, KEP adresleri, hakkında, mal ve hizmetler, ödeme türleri, işletmenin diğer siteleri
Aşamalar:
  lists    — il × ilçe filtreli liste (il/ilçe ataması); il toplamı ilçelerin toplamını tutmazsa ilçesiz il turu
  sectors  — sektör filtreli liste (site → ETBİS sektör etiketi)
  profiles — her site profili (Batı büyükşehirleri önce)
Maskeli kimlikler olduğu gibi saklanır; maske çözülmeye çalışılmaz. KEP/şahıs unvanı → 'restricted' sütun (STANDARD §3.3).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import UA, Intake, now_iso  # noqa: E402

B = "https://etbis.ticaret.gov.tr"
LIST = B + "/tr/SiteSorgulama"
PRI = ["İstanbul", "İzmir", "Bursa", "Antalya", "Kocaeli", "Muğla", "Tekirdağ", "Balıkesir", "Aydın", "Ankara"]
_local = threading.local()
lock = threading.Lock()


def sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
        _local.last = 0.0
    return _local.s


def get(url, params=None, delay=1.5, tries=4):
    err = None
    for i in range(tries):
        w = delay - (time.time() - getattr(_local, "last", 0))
        if w > 0:
            time.sleep(w)
        _local.last = time.time()
        try:
            r = sess().get(url, params=params, timeout=90)
            if r.status_code == 200:
                return r
            err = f"HTTP {r.status_code}"
            if r.status_code in (403, 429):
                time.sleep(120)
        except requests.RequestException as e:
            err = repr(e)[:150]
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url} {params}: {err}")


def parse_list(html):
    s = BeautifulSoup(html, "html.parser")
    rows = []
    t = s.find("table")
    if t:
        for tr in t.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            a = tr.find("a", href=re.compile("siteId="))
            rows.append({"unvan": tds[0].get_text(" ", strip=True), "site": tds[1].get_text(" ", strip=True),
                         "mobil": tds[2].get_text(" ", strip=True),
                         "siteId": re.search(r"siteId=([\w-]+)", a["href"]).group(1) if a else None})
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", html)]
    return rows, (max(pages) if pages else 1)


def lists(it: Intake, workers: int):
    s = BeautifulSoup(get(LIST).text, "html.parser")
    cities = [(o.get("value"), o.text.strip()) for o in s.select("select[name=cityId] option") if o.get("value")]
    cities.sort(key=lambda c: (PRI.index(c[1]) if c[1] in PRI else 99, c[1]))
    it.save_json("cities.json", cities, source_url=LIST, method="html_select")
    out = it.dir / "list_rows.jsonl"
    done = set()
    if out.exists():
        for l in out.read_text().splitlines():
            try:
                d = json.loads(l)
                done.add((d["cityId"], d["districtId"], d["page"]))
            except ValueError:
                pass

    def crawl(city, district, cname, dname):
        p1 = (city, district or "", 1)
        params = {"page": 1, "url": "", "cityId": city, "districtId": district or "", "sector": "", "isItCrossBorder": ""}
        r = get(LIST, params)
        rows, maxp = parse_list(r.text)
        rec = lambda pg, rs: {"cityId": city, "city": cname, "districtId": district or "", "district": dname,
                              "page": pg, "maxPage": maxp, "rows": rs, "at": now_iso()}
        n = len(rows)
        if p1 not in done:
            with lock, out.open("a") as f:
                f.write(json.dumps(rec(1, rows), ensure_ascii=False) + "\n")
        for pg in range(2, maxp + 1):
            if (city, district or "", pg) in done:
                continue
            rs, _ = parse_list(get(LIST, {**params, "page": pg}).text)
            n += len(rs)
            with lock, out.open("a") as f:
                f.write(json.dumps(rec(pg, rs), ensure_ascii=False) + "\n")
        return maxp, n

    def do_city(city, cname):
        try:  # 2026-09-24: sitenin GetDistricts servisi 302 → PageNotFound (kendi arayüzünde de bozuk)
            dres = get(LIST + "/GetDistricts", {"cityId": city}, tries=1).json()
            dists = [(d.get("id"), d.get("name") or d.get("text") or d.get("districtName")) for d in dres]
        except Exception:  # noqa: BLE001
            dists = []
        city_max, _ = parse_list(get(LIST, {"page": 1, "url": "", "cityId": city, "districtId": "", "sector": "",
                                            "isItCrossBorder": ""}).text)[1], None
        tot_d_pages = 0
        for did, dn in dists:
            mp, _ = crawl(city, did, cname, dn)
            tot_d_pages += mp
        # ilçesi girilmemiş kayıt olabilir → il sayfa sayısı ilçe toplamından büyükse ilçesiz il turu
        if city_max > tot_d_pages - len(dists) + 1 or not dists:
            crawl(city, "", cname, None)
        return cname, len(dists), city_max

    with ThreadPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(do_city, c, n) for c, n in cities]):
            try:
                cname, nd, cm = f.result()
                it.log(f"liste: {cname} bitti ({nd} ilçe, il {cm} sayfa)")
            except Exception as e:  # noqa: BLE001
                it.log(f"liste hata: {e}")
    it._record(out, source_url=LIST, method="html_paginated_city_district", rows=sum(1 for _ in out.open()),
               note="her satır bir liste sayfası (≤10 site); il/ilçe filtre değerleriyle")


def allpages(it: Intake):
    """Filtresiz tam liste (il bilgisi girilmemiş siteler dahil); il turuyla karşılaştırmak için."""
    out = it.dir / "all_rows.jsonl"
    done = set()
    if out.exists():
        for l in out.read_text().splitlines():
            try:
                done.add(json.loads(l)["page"])
            except ValueError:
                pass
    params = {"page": 1, "url": "", "cityId": "", "districtId": "", "sector": "", "isItCrossBorder": ""}
    rows, maxp = parse_list(get(LIST, params).text)
    it.log(f"tam liste: {maxp} sayfa, {len(done)} tamam")
    for pg in range(1, maxp + 1):
        if pg in done:
            continue
        rs = rows if pg == 1 else parse_list(get(LIST, {**params, "page": pg}).text)[0]
        with lock, out.open("a") as f:
            f.write(json.dumps({"page": pg, "maxPage": maxp, "rows": rs, "at": now_iso()}, ensure_ascii=False) + "\n")
        if pg % 500 == 0:
            it.log(f"tam liste: {pg}/{maxp}")
    it._record(out, source_url=LIST, method="html_paginated_unfiltered", rows=sum(1 for _ in out.open()))


def sectors(it: Intake, workers: int):
    s = BeautifulSoup(get(LIST).text, "html.parser")
    secs = [(o.get("value"), o.text.strip()) for o in s.select("select[name=sector] option") if o.get("value")]
    out = it.dir / "sector_rows.jsonl"
    done = set()
    if out.exists():
        done = {(json.loads(l)["sectorId"], json.loads(l)["page"]) for l in out.read_text().splitlines() if l.strip()}

    def do(sec, sname):
        params = {"page": 1, "url": "", "cityId": "", "districtId": "", "sector": sec, "isItCrossBorder": ""}
        rows, maxp = parse_list(get(LIST, params).text)
        for pg in range(1, maxp + 1):
            if (sec, pg) in done:
                continue
            rs = rows if pg == 1 else parse_list(get(LIST, {**params, "page": pg}).text)[0]
            with lock, out.open("a") as f:
                f.write(json.dumps({"sectorId": sec, "sector": sname, "page": pg, "maxPage": maxp, "rows": rs,
                                    "at": now_iso()}, ensure_ascii=False) + "\n")
        return sname, maxp

    with ThreadPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(do, a, b) for a, b in secs]):
            try:
                it.log(f"sektör bitti: {f.result()}")
            except Exception as e:  # noqa: BLE001
                it.log(f"sektör hata: {e}")
    it._record(out, source_url=LIST, method="html_paginated_sector", rows=sum(1 for _ in out.open()))


def parse_profile(html):
    s = BeautifulSoup(html, "html.parser")
    t = (s.find("main") or s).get_text("\n", strip=True)
    lines = t.split("\n")
    rec = {}
    for key in ("Mersis No:", "Vergi No:", "İşletme Adı:", "ETBİS’e Kayıt Tarihi:", "İşletme Türü:", "Kep Adresleri:"):
        if key in lines:
            i = lines.index(key)
            v = lines[i + 1] if i + 1 < len(lines) else None
            rec[key.rstrip(":")] = None if v and v.endswith(":") else v

    def section(title, stop):
        if title not in lines:
            return []
        i = lines.index(title, lines.index(title) + 1) if lines.count(title) > 1 else lines.index(title)
        out = []
        for x in lines[i + 1:]:
            if x in stop:
                break
            out.append(x)
        return out
    stops = {"Hakkında", "Mal ve Hizmetler", "Ödeme Türleri", "Bu İşletmeye Ait", "E-TİCARET AKADEMİSİ", "Genel Bilgiler"}
    rec["hakkinda"] = " ".join(section("Hakkında", stops))
    rec["mal_hizmetler"] = section("Mal ve Hizmetler", stops)
    rec["odeme_turleri"] = section("Ödeme Türleri", stops)
    rec["diger_siteler"] = [a["href"] for a in s.find_all("a", href=True) if a["href"].startswith("http") and "ticaret.gov.tr" not in a["href"]][:50]
    return rec


def profiles(it: Intake, workers: int):
    order, seen = [], set()
    src = it.dir / "list_rows.jsonl"
    if src.exists():
        rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
        rows.sort(key=lambda d: (PRI.index(d["city"]) if d["city"] in PRI else 99))
        for d in rows:
            for r in d["rows"]:
                if r.get("siteId") and r["siteId"] not in seen:
                    seen.add(r["siteId"]); order.append(r["siteId"])
    extra = it.dir / "all_rows.jsonl"  # il bilgisi olmayanlar
    if extra.exists():
        for l in extra.read_text().splitlines():
            try:
                for r in json.loads(l)["rows"]:
                    if r.get("siteId") and r["siteId"] not in seen:
                        seen.add(r["siteId"]); order.append(r["siteId"])
            except ValueError:
                pass
    out = it.dir / "profiles.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["siteId"] for l in out.read_text().splitlines() if l.strip()}
    todo = [x for x in order if x not in done]
    it.log(f"profil: {len(order)} site, {len(todo)} kaldı")

    def do(sid):
        r = get(B + "/tr/SiteSorgulamaSonuc", {"siteId": sid}, delay=1.5)
        return sid, parse_profile(r.text)

    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(do, s) for s in todo]):
            try:
                sid, rec = f.result()
            except Exception as e:  # noqa: BLE001
                it.log(f"profil hata: {e}")
                continue
            with lock, out.open("a") as fo:
                fo.write(json.dumps({"siteId": sid, "at": now_iso(), **rec}, ensure_ascii=False) + "\n")
            n += 1
            if n % 1000 == 0:
                it.log(f"profil: {n}/{len(todo)}")
    it._record(out, source_url=B + "/tr/SiteSorgulamaSonuc", method="html_profile", rows=len(done) + n,
               note="MERSİS/VKN Bakanlıkça maskeli; KEP/şahıs unvanı restricted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["lists", "allpages", "sectors", "profiles", "all"])
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    it = Intake("etbis_eticaret_siteleri")
    if a.phase in ("lists", "all"):
        lists(it, a.workers)
    if a.phase in ("allpages", "all"):
        allpages(it)
    if a.phase in ("sectors", "all"):
        sectors(it, a.workers)
    if a.phase in ("profiles", "all"):
        profiles(it, a.workers)


if __name__ == "__main__":
    main()
