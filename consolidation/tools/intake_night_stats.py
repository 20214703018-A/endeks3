"""Gece toplama turu — kaynak bazında içerik istatistikleri (rapor için). Çıktı: reports/NIGHT_STATS_2026-09-24.json"""
from __future__ import annotations

import glob
import gzip
import json
from collections import Counter
from pathlib import Path

import pandas as pd

R = Path.home() / "Desktop/GEOPROP_RAW_INTAKE"
D = "2026-09-24"
OUT = Path(__file__).resolve().parent.parent / "reports" / f"NIGHT_STATS_{D}.json"


def jl(p):
    op = gzip.open if str(p).endswith(".gz") else open
    out = []
    try:
        with op(p, "rt") as f:
            for l in f:
                try:
                    out.append(json.loads(l))
                except ValueError:
                    pass
    except (OSError, EOFError):
        pass
    return out


def man(src):
    p = R / src / D / "manifest.jsonl"
    return jl(p) if p.exists() else []


def disk(src, d=D):
    p = R / src / d
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def safe(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"hata": repr(e)[:200]}


S = {}


def market():
    d = R / "marketfiyati" / D
    dep = pd.read_parquet(d / "depots.parquet") if (d / "depots.parquet").exists() else None
    pr = sorted((d / "prices").glob("*.parquet"))
    rows = prods = 0
    ils = []
    for p in pr:
        x = pd.read_parquet(p, columns=["id", "il_adi"])
        rows += len(x); prods = max(prods, x.id.nunique()); ils.append(x.il_adi.iloc[0])
    hist = sorted((d / "history").glob("price_history_*.parquet"))
    hrows = sum(len(pd.read_parquet(h, columns=["id"])) for h in hist)
    live_hist = sum(len(jl(p)) for p in (d / "history").glob("raw_*/part_*.jsonl.gz"))
    return {"subeler": None if dep is None else int(len(dep)),
            "sube_zincir": None if dep is None else dep.market.value_counts().to_dict(),
            "sube_ile_atanan": None if dep is None else int(dep.il_geo_id.notna().sum()),
            "fiyat_il_sayisi": len(pr), "fiyat_iller": ils, "fiyat_satiri": rows, "il_basina_max_urun": prods,
            "gecmis_satir": hrows, "gecmis_canli_urun": live_hist, "disk": disk("marketfiyati")}


def hal():
    d = R / "hal_fiyatlari" / D
    hks = sum(len(jl(p)) for p in d.glob("hks_parts_*.jsonl.gz"))
    hks_rows = sum(len(x.get("rows") or []) for p in d.glob("hks_parts_*.jsonl.gz") for x in jl(p))
    iz = {}
    for k in ("sebzemeyve", "balik"):
        L = jl(d / f"izmir_{k}_parts.jsonl.gz")
        iz[k] = {"gun": len(L), "satir": sum(len((x.get("body") or {}).get("HalFiyatListesi") or []) for x in L)}
    par = {p.name: len(pd.read_parquet(p)) for p in d.glob("*.parquet")}
    return {"hks_gun": hks, "hks_satir": hks_rows, "izmir": iz, "parquet": par, "disk": disk("hal_fiyatlari")}


def sarj():
    d = R / "sarj_istasyonlari_epdk" / D
    det = jl(d / "station_details.jsonl.gz")
    socks = sum(len(x.get("sockets") or []) for x in det)
    st = Counter(s.get("availability", [{}])[0].get("status") for x in det for s in (x.get("sockets") or []) if s.get("availability"))
    ops = Counter(x.get("operatortitle") for x in det)
    e = R / "epdk_api" / D
    lst = e / "sarj_istasyonlari_epdk_liste.parquet"
    return {"sarjtr_istasyon_detay": len(det), "soket": socks, "soket_durum": dict(st.most_common(6)),
            "operator_sayisi": len(ops), "ilk5_operator": ops.most_common(5),
            "epdk_resmi_liste": int(len(pd.read_parquet(lst))) if lst.exists() else None,
            "disk": disk("sarj_istasyonlari_epdk") + disk("epdk_api")}


def tuik():
    m = [x for x in man("tuik_sdmx") if x.get("kind") == "data"]
    ilce = [x["dataset_id"] for x in m if "ILCE" in x["dataset_id"] or "IBBS3" in x["dataset_id"]]
    return {"veri_akisi": len(m), "gozlem": sum(x.get("rows") or 0 for x in m), "kismi": sum(1 for x in m if x.get("partial")),
            "il_ilce_duzeyli_ornek": ilce[:30], "il_ilce_duzeyli_sayi": len(ilce), "disk": disk("tuik_sdmx")}


def tobb():
    L = jl(R / "tobb_sanayi_kapasite" / D / "responses.jsonl")
    ok = [x for x in L if x.get("ok")]
    kinds = Counter(x["key"].split("|")[0] for x in ok)
    return {"sorgu": len(L), "basarili": len(ok), "tur": dict(kinds), "disk": disk("tobb_sanayi_kapasite")}


def osm():
    out = {}
    for d in sorted((R / "osm_pbf_katmanlar").glob("*")):
        if d.is_dir():
            out[d.name] = {p.stem: int(pd.read_parquet(p, columns=["osm_id"]).shape[0]) for p in sorted(d.glob("*.parquet"))}
    return {"kesitler": out, "disk": disk("osm_pbf_katmanlar") + disk("osm_pbf")}


def otobus():
    L = jl(R / "otobus_seferleri_enuygun" / D / "routes.jsonl")
    trips = [t for x in L for t in (x.get("trips") or [])]
    firms = Counter((t.get("provider") or {}).get("name") for t in trips)
    stops = {(t.get("departureBusStop") or {}).get("name") for t in trips} | {(t.get("arrivalBusStop") or {}).get("name") for t in trips}
    return {"rota": len(L), "seferli_rota": sum(1 for x in L if x.get("trips")), "sefer": len(trips),
            "firma": len(firms), "durak_terminal": len(stops), "disk": disk("otobus_seferleri_enuygun")}


def attachments(src):
    m = man(src)
    ext = Counter(Path(x["file"]).name.replace(".gz", "").rsplit(".", 1)[-1].lower() for x in m)
    sk = R / src / D / "skipped_large.tsv"
    return {"dosya": len(m), "tur": dict(ext.most_common(8)), "buyuk_atlanan": sum(1 for _ in open(sk)) if sk.exists() else 0,
            "disk": disk(src)}


def ckan():
    out = {}
    for d in sorted(R.glob("acikveri_ckan_*")):
        m = man(d.name)
        cat = d / D / "catalog.json.gz"
        n_ds = len(json.loads(gzip.open(cat).read())) if cat.exists() else None
        out[d.name.replace("acikveri_ckan_", "")] = {"veri_seti": n_ds, "dosya": sum(1 for x in m if x.get("method") == "ckan_resource_download"),
                                                     "disk": disk(d.name)}
    return out


def bkm():
    L = jl(R / "bkm_donemsel" / D / "pages.jsonl")
    return {"sayfa_ay": len(L), "tablolu": sum(1 for x in L if x.get("tables")),
            "sayfa": len({x["page"] for x in L if x.get("tables")}), "disk": disk("bkm_donemsel")}


def tga():
    p = R / "turizm_tga_belgeli_tesisler" / D / "ktb_belgeli_konaklama_tesisleri.parquet"
    x = pd.read_parquet(p)
    return {"tesis": len(x), "il": int(x.Sehir.nunique()), "ilce": int(x.Ilce.nunique()),
            "belge_turu": x.BelgeTuru.value_counts().to_dict()}


def etbis():
    L = jl(R / "etbis_eticaret_siteleri" / D / "list_rows.jsonl")
    sites = {r["siteId"]: d["city"] for d in L for r in d["rows"] if r.get("siteId")}
    return {"liste_sayfasi": len(L), "site": len(sites), "il_sayisi": len(set(sites.values())),
            "ilk_iller": Counter(sites.values()).most_common(10),
            "profil": len(jl(R / "etbis_eticaret_siteleri" / D / "profiles.jsonl"))}


def adres():
    L = jl(R / "eticaret_sirket_adresleri" / D / "sites.jsonl")
    ok = [x for x in L if x.get("status") == 200]
    has = lambda x, k: any(p.get(k) for p in x.get("pages", []))
    return {"site": len(L), "canli": len(ok),
            "adresli": sum(1 for x in ok if has(x, "addr_mah") or has(x, "addr_label") or has(x, "jsonld")),
            "mersisli": sum(1 for x in ok if has(x, "mersis")), "kepli": sum(1 for x in ok if has(x, "kep")),
            "robots_yasak": sum(1 for x in L if x.get("robots_disallow")), "erisilemeyen": sum(1 for x in L if x.get("error"))}


def cimri():
    d = R / "cimri_fiyat_karsilastirma" / D
    cat = sum(1 for _ in gzip.open(d / "catalog_urls.jsonl.gz", "rt")) if (d / "catalog_urls.jsonl.gz").exists() else 0
    L = jl(d / "offers.jsonl")
    ok = [x for x in L if x.get("product")]
    return {"katalog_url": cat, "urun_sayfasi": len(L), "ayristirilan": len(ok),
            "teklif": sum(len(x["product"].get("offers") or []) for x in ok),
            "gecmis_noktasi": sum(len(x["product"].get("price_history") or []) for x in ok),
            "engel_403": sum(1 for x in L if x.get("status") == 403)}


def main():
    S["etbis"] = safe(etbis)
    S["adres"] = safe(adres)
    S["cimri"] = safe(cimri)
    S["market"] = safe(market)
    S["hal"] = safe(hal)
    S["sarj"] = safe(sarj)
    S["tuik"] = safe(tuik)
    S["tobb_svt"] = safe(tobb)
    S["osm"] = safe(osm)
    S["otobus"] = safe(otobus)
    S["tga"] = safe(tga)
    S["bkm"] = safe(bkm)
    S["ckan"] = safe(ckan)
    for src in ["turizm_ktb", "kgm_karayollari", "uab_denizcilik_istatistik", "sgk_istatistik", "ticaret_bakanligi_istatistik",
                "tobb_istatistik", "dhmi_havalimani_istatistik", "tcdd_demiryolu_istatistik", "uab_tkygm_kiyi_istatistik",
                "btk_iletisim_istatistik", "bkm_donemsel"]:
        S[src] = safe(lambda s=src: attachments(s))
    OUT.write_text(json.dumps(S, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(S, ensure_ascii=False, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
