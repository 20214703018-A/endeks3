"""Hal (toptancı hal) fiyatları toplayıcısı — geçmiş + izleme.

Kaynaklar:
  hks     — T.C. Ticaret Bakanlığı Hal Kayıt Sistemi, "Fiyat İstatistikleri"
            http://www.hal.gov.tr/Sayfalar/FiyatDetaylari.aspx (günlük ulusal bülten:
            ürün adı/cinsi/türü, ortalama fiyat, işlem hacmi, birim). Veri 2017'den beri.
  izmir   — İzmir Büyükşehir Belediyesi açık API
            https://openapi.izmir.bel.tr/api/ibb/halfiyatlari/{sebzemeyve|balik}/{YYYY-MM-DD}
            (asgari/azami/ortalama ücret; 2008'den beri günlük).

Kullanım:
  python3 intake_hal.py hks   [--start 2017-01-01] [--end bugün]
  python3 intake_hal.py izmir [--start 2008-01-01]
  python3 intake_hal.py track   # son 10 günü tüm kaynaklardan çek (günlük izleme için)
Çıktı: GEOPROP_RAW_INTAKE/hal_fiyatlari/<çalışma tarihi>/ ; ara dosyalar gün gün, sonunda parquet.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import json
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso, repair_gz_jsonl  # noqa: E402

HKS_URL = "http://www.hal.gov.tr/Sayfalar/FiyatDetaylari.aspx"
HKS_P = "ctl00$ctl37$g_7e86b8d6_3aea_47cf_b1c1_939799a091e0$"
IZMIR_URL = "https://openapi.izmir.bel.tr/api/ibb/halfiyatlari/{kind}/{d}"


def daterange(a: dt.date, b: dt.date):
    while a <= b:
        yield a
        a += dt.timedelta(days=1)


def _form(soup):
    f = {i.get("name"): i.get("value", "") for i in soup.select("input")
         if i.get("name") and i.get("type") in ("hidden", "text")}
    for k in list(f):
        if "InputKeywords" in k:
            f[k] = ""
    return f


def hks(it: Intake, start: dt.date, end: dt.date, build: bool = True):
    parts = it.dir / f"hks_parts_{start.isoformat()}.jsonl.gz"  # paralel süreçler ayrı dosyaya yazar
    done = set()
    repair_gz_jsonl(parts)
    for pf in it.dir.glob("hks_parts*.jsonl.gz"):
        with gzip.open(pf, "rt") as f:
            for l in f:
                try:
                    done.add(json.loads(l)["query_date"])
                except Exception:  # noqa: BLE001
                    pass
    days = [d for d in daterange(start, end) if d.isoformat() not in done]
    it.log(f"hks: {len(days)} gün çekilecek ({len(done)} tamam)")
    it.rate = 0.7
    soup = BeautifulSoup(it.get(HKS_URL).text, "html.parser")
    n_ok = 0
    with gzip.open(parts, "at") as fo:
        for i, d in enumerate(days):
            try:
                f = _form(soup)
                f[HKS_P + "dateControl$dateControlDate"] = d.strftime("%d.%m.%Y")
                f[HKS_P + "btnGet"] = "Fiyat Bul"
                r = it.get(HKS_URL, method="POST", data=f)
                soup = BeautifulSoup(r.text, "html.parser")
                rows, bulten = [], None
                if soup.find("table", id=re.compile("gvFiyatlar")):
                    f = _form(soup)
                    f[HKS_P + "btnExcel"] = "Excel'e Çıkar"
                    f[HKS_P + "rblExcelOptions"] = "2"  # tüm sayfalar
                    x = it.get(HKS_URL, method="POST", data=f)
                    txt = x.content.decode("utf-16", errors="replace")
                    tabs = pd.read_html(io.StringIO(txt), thousands=None, decimal=",", converters={i: str for i in range(6)})
                    t = tabs[0]
                    bulten = str(t.iloc[0, 0])
                    hdr = [str(c) for c in t.iloc[1].tolist()]
                    for rr in t.iloc[2:].itertuples(index=False):
                        rows.append(dict(zip(hdr, [str(v) for v in rr])))
                    # Excel yanıtı sayfa döndürmez; sonraki gün aynı arama sayfasının formuyla sorgulanır
                fo.write(json.dumps({"query_date": d.isoformat(), "bulten": bulten, "rows": rows,
                                     "at": now_iso()}, ensure_ascii=False) + "\n")
                n_ok += 1
                if i % 50 == 0:
                    fo.flush()
                    it.log(f"hks: {d} {len(rows)} satır ({i + 1}/{len(days)})")
            except Exception as e:  # noqa: BLE001
                it.log(f"hks hata {d}: {e}")
                soup = BeautifulSoup(it.get(HKS_URL).text, "html.parser")
    if build:
        build_hks(it)


def _num(s):
    if s is None:
        return None
    s = str(s).strip()
    if "," in s:  # 1.234,56 biçimi
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def build_hks(it: Intake):
    recs = []
    lines = []
    for pf in sorted(it.dir.glob("hks_parts*.jsonl.gz")):
        with gzip.open(pf, "rt") as f:
            lines += f.readlines()
    seen = set()
    for l in lines:
        if True:
            try:
                d = json.loads(l)
            except Exception:  # noqa: BLE001
                continue
            if d["query_date"] in seen:
                continue
            seen.add(d["query_date"])
            m = re.search(r"\((\d{2}\.\d{2}\.\d{4}) Tarihli", d.get("bulten") or "")
            veri_tarihi = dt.datetime.strptime(m.group(1), "%d.%m.%Y").date().isoformat() if m else None
            for r in d["rows"]:
                recs.append({"bulten_tarihi": d["query_date"], "veri_tarihi": veri_tarihi,
                             "urun_adi": r.get("Ürün Adı"), "urun_cinsi": r.get("Ürün Cinsi"),
                             "urun_turu": r.get("Ürün Türü"), "ortalama_fiyat_raw": r.get("Ortalama Fiyat"),
                             "ortalama_fiyat_tl": _num(r.get("Ortalama Fiyat")),
                             "islem_hacmi_raw": r.get("İşlem Hacmi"), "islem_hacmi": _num(r.get("İşlem Hacmi")),
                             "birim": r.get("Birim Adı"), "kapsam": "Türkiye (HKS ulusal bülten)",
                             "guncellenme_tarihi": d["at"]})
    df = pd.DataFrame(recs)
    it.save_parquet("hks_ulusal_gunluk", df, source_url=HKS_URL, method="aspnet_postback_excel",
                    note="Ticaret Bakanlığı HKS fiyat istatistikleri; veri_tarihi = bültende kullanılan önceki gün")
    it.log(f"hks: {len(df)} satır, {df.bulten_tarihi.nunique() if len(df) else 0} gün")


def izmir(it: Intake, start: dt.date, end: dt.date):
    it.rate = 0.25
    for kind in ("sebzemeyve", "balik"):
        parts = it.dir / f"izmir_{kind}_parts.jsonl.gz"
        done = set()
        repair_gz_jsonl(parts)
        if parts.exists():
            with gzip.open(parts, "rt") as f:
                for l in f:
                    try:
                        done.add(json.loads(l)["query_date"])
                    except Exception:  # noqa: BLE001
                        pass
        days = [d for d in daterange(start, end) if d.isoformat() not in done]
        it.log(f"izmir {kind}: {len(days)} gün")
        with gzip.open(parts, "at") as fo:
            for i, d in enumerate(days):
                url = IZMIR_URL.format(kind=kind, d=d.isoformat())
                try:
                    r = it.get(url)
                    body = r.json() if r.status_code == 200 and r.content else None
                except Exception as e:  # noqa: BLE001
                    it.log(f"izmir hata {url}: {e}")
                    continue
                fo.write(json.dumps({"query_date": d.isoformat(), "status": r.status_code, "body": body,
                                     "at": now_iso()}, ensure_ascii=False) + "\n")
                if i % 500 == 0:
                    fo.flush()
                    it.log(f"izmir {kind}: {d} ({i + 1}/{len(days)})")
        recs = []
        with gzip.open(parts, "rt") as f:
            for l in f:
                try:
                    d = json.loads(l)
                except Exception:  # noqa: BLE001
                    continue
                b = d.get("body") or {}
                for x in b.get("HalFiyatListesi") or []:
                    recs.append({"sorgu_tarihi": d["query_date"], "bulten_tarihi": (b.get("BultenTarihi") or "")[:10],
                                 "mal_id": x.get("MalId"), "mal_adi": x.get("MalAdi"), "mal_tip_id": x.get("MalTipId"),
                                 "mal_tip_adi": x.get("MalTipAdi"), "hal_turu": x.get("HalTuru"), "birim": x.get("Birim"),
                                 "asgari_ucret": x.get("AsgariUcret"), "azami_ucret": x.get("AzamiUcret"),
                                 "ortalama_ucret": x.get("OrtalamaUcret"), "il": "İzmir",
                                 "hal": "İzmir BB " + ("Sebze-Meyve Hali" if kind == "sebzemeyve" else "Balık Hali"),
                                 "guncellenme_tarihi": d["at"]})
        df = pd.DataFrame(recs)
        # aynı bülten birden çok sorgu gününde dönebilir (tatil) → bülten tarihine göre tekilleştir
        if len(df):
            df = df.drop_duplicates(subset=["bulten_tarihi", "mal_id", "birim", "ortalama_ucret"])
        it.save_parquet(f"izmir_{kind}_gunluk", df, source_url=IZMIR_URL.format(kind=kind, d="{tarih}"),
                        method="rest_get_daily", note="İzmir BB açık API; tatil günleri önceki bülteni döndürür (tekilleştirildi)")
        it.log(f"izmir {kind}: {len(df)} satır")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=["hks", "izmir", "build_hks", "track"])
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today()
    it = Intake("hal_fiyatlari")
    if a.source == "hks":
        hks(it, dt.date.fromisoformat(a.start or "2017-01-01"), end, build=not a.no_build)
    elif a.source == "build_hks":
        build_hks(it)
    elif a.source == "izmir":
        izmir(it, dt.date.fromisoformat(a.start or "2008-01-01"), end)
    elif a.source == "track":
        st = end - dt.timedelta(days=10)
        hks(it, st, end)
        izmir(it, st, end)


if __name__ == "__main__":
    main()
