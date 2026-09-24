"""OpenStreetMap (Overpass API) ulaşım/lojistik katmanları toplayıcısı — Türkiye.

Kaynak: https://overpass-api.de/api/interpreter (yedek: overpass.kumi.systems). Lisans: ODbL (atıf gerekir).
Her katman ham Overpass JSON (out geom / out center; tüm etiketlerle) olarak .json.gz yazılır,
ayrıca düz parquet (osm tipi/id, lat/lon (merkez), ad, tüm etiketler JSON, geometri WKT) üretilir.

Kullanım: python3 intake_osm_overpass.py [katman ...]   (boşsa hepsi)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter"]
AREA = 'area["ISO3166-1"="TR"][admin_level=2]->.tr;'

LAYERS = {
    # liman / iskele / feribot
    "limanlar": 'nwr(area.tr)[harbour];nwr(area.tr)[landuse=port];nwr(area.tr)[industrial=port];'
                'nwr(area.tr)["seamark:type"~"harbour|port"];nwr(area.tr)[amenity=ferry_terminal];'
                'nwr(area.tr)[leisure=marina];',
    # demiryolu hatları (tüm hizmet türleri; usage=main/branch/industrial/freight etiketiyle)
    "demiryolu_hatlari": 'way(area.tr)[railway~"^(rail|light_rail|subway|tram|narrow_gauge|construction|proposed|disused|abandoned)$"];',
    "demiryolu_istasyonlari": 'nwr(area.tr)[railway~"^(station|halt|yard|stop|junction|freight_station|goods)$"];'
                              'nwr(area.tr)[public_transport=station][train=yes];nwr(area.tr)[landuse=railway];',
    "demiryolu_guzergahlari": 'relation(area.tr)[type=route][route~"^(train|railway|freight)$"];',
    # şehirlerarası otobüs: otogarlar + OSM'deki otobüs güzergâh ilişkileri (şehirlerarası ağ etiketliler dahil)
    "otogarlar": 'nwr(area.tr)[amenity=bus_station];nwr(area.tr)[public_transport=station][bus=yes];',
    "otobus_guzergahlari": 'relation(area.tr)[type=route][route~"^(bus|coach|trolleybus)$"];',
    # lojistik / sanayi
    "lojistik": 'nwr(area.tr)[industrial~"logistics|warehouse|depot|port"];nwr(area.tr)[building~"^(warehouse|industrial)$"][name];'
                'nwr(area.tr)[landuse=industrial][name~"[Ll]ojistik|[Aa]ntrepo|[Gg]ümrük|[Ss]erbest [Bb]ölge"];'
                'nwr(area.tr)[office=logistics];nwr(area.tr)[amenity=customs];nwr(area.tr)[shop=wholesale];',
    "sanayi_bolgeleri": 'nwr(area.tr)[landuse=industrial][name];nwr(area.tr)[industrial][name];',
    # karayolu yük taşımacılığı altyapısı
    "tir_altyapisi": 'nwr(area.tr)[highway~"^(rest_area|services)$"];nwr(area.tr)[amenity=parking][hgv~"yes|designated"];'
                     'nwr(area.tr)[amenity=parking][parking~"truck|hgv"];nwr(area.tr)[amenity=fuel][hgv~"yes|designated"];'
                     'nwr(area.tr)[highway=weighbridge];nwr(area.tr)[amenity=weighbridge];nwr(area.tr)[barrier=toll_booth];',
    "akaryakit_istasyonlari": 'nwr(area.tr)[amenity=fuel];',
    "sinir_kapilari": 'nwr(area.tr)[barrier=border_control];nwr(area.tr)[amenity=border_control];',
    "havalimanlari": 'nwr(area.tr)[aeroway~"^(aerodrome|terminal|heliport)$"];',
    "sarj_istasyonlari_osm": 'nwr(area.tr)[amenity=charging_station];',
    # ana yol ağı (otoyol/devlet yolu/il yolu) — tır rotalarının omurgası
    "ana_yol_agi": 'way(area.tr)[highway~"^(motorway|motorway_link|trunk|trunk_link|primary)$"];',
    "karayolu_guzergahlari": 'relation(area.tr)[type=route][route=road];',
    "otoparklar": 'nwr(area.tr)[amenity=parking];',
    "toptanci_hal_pazar": 'nwr(area.tr)[amenity=marketplace];nwr(area.tr)[shop=greengrocer][wholesale];',
}
GEOM = {"demiryolu_hatlari", "ana_yol_agi", "sanayi_bolgeleri", "limanlar", "havalimanlari", "demiryolu_guzergahlari",
        "otobus_guzergahlari", "karayolu_guzergahlari"}


def run_query(it: Intake, body: str, out: str) -> dict:
    q = f"[out:json][timeout:1500][maxsize:2000000000];{AREA}({body});out {out};"
    last = None
    for ep in ENDPOINTS * 2:
        try:
            r = it.get(ep, method="POST", data={"data": q}, timeout=1800, tries=1)
            if r.status_code == 200 and r.content[:1] == b"{":
                return {"endpoint": ep, "query": q, "json": r.json(), "bytes": len(r.content)}
            last = f"{ep} HTTP {r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last = f"{ep} {e!r}"
        it.log(f"yeniden deneme: {last}")
        time.sleep(30)
    raise RuntimeError(last)


def flatten(elems):
    rows = []
    for e in elems:
        tags = e.get("tags") or {}
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        wkt = None
        if e.get("geometry"):
            pts = [(p["lon"], p["lat"]) for p in e["geometry"] if p]
            if pts:
                closed = len(pts) > 3 and pts[0] == pts[-1]
                wkt = ("POLYGON((" if closed else "LINESTRING(") + ",".join(f"{x} {y}" for x, y in pts) + ("))" if closed else ")")
                if lat is None:
                    lat = sum(p[1] for p in pts) / len(pts)
                    lon = sum(p[0] for p in pts) / len(pts)
        elif e.get("bounds") and lat is None:
            b = e["bounds"]
            lat, lon = (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
        members = e.get("members")
        rows.append({"osm_type": e["type"], "osm_id": e["id"], "lat": lat, "lon": lon,
                     "name": tags.get("name"), "name_tr": tags.get("name:tr"),
                     "operator": tags.get("operator"), "ref": tags.get("ref"),
                     "main_tag": next((f"{k}={tags[k]}" for k in ("harbour", "railway", "amenity", "highway", "landuse",
                                                                    "industrial", "aeroway", "barrier", "route", "leisure",
                                                                    "building", "shop", "office", "public_transport")
                                        if k in tags), None),
                     "tags_json": json.dumps(tags, ensure_ascii=False),
                     "member_count": len(members) if members else None,
                     "members_json": json.dumps(members, ensure_ascii=False)[:200000] if members else None,
                     "geometry_wkt": wkt})
    return rows


def main():
    layers = sys.argv[1:] or list(LAYERS)
    it = Intake("osm_ulasim_lojistik", rate=5)
    it.s.headers["User-Agent"] = "GEOPROP-data-intake/1.0 (python-requests)"  # Overpass politikası: tanımlayıcı UA
    for name in layers:
        if (it.dir / f"{name}.parquet").exists():
            it.log(f"{name}: zaten var, atlandı")
            continue
        out = "geom" if name in GEOM else "center"
        if name.endswith("guzergahlari"):
            out = "body geom"
        it.log(f"{name}: sorgu başlıyor (out {out})")
        try:
            res = run_query(it, LAYERS[name], out)
        except Exception as e:  # noqa: BLE001
            it.log(f"{name}: BAŞARISIZ {e}")
            continue
        j = res["json"]
        elems = j.get("elements", [])
        it.save_json(f"raw/{name}.json", j, source_url=res["endpoint"], method="overpass_post",
                     rows=len(elems), extra={"query": res["query"], "osm_base": (j.get("osm3s") or {}).get("timestamp_osm_base")})
        df = pd.DataFrame(flatten(elems))
        df["osm_base_timestamp"] = (j.get("osm3s") or {}).get("timestamp_osm_base")
        df["guncellenme_tarihi"] = now_iso()
        it.save_parquet(name, df, source_url=res["endpoint"], method="overpass_post",
                        note="OpenStreetMap katkıcıları, ODbL")
        it.log(f"{name}: {len(df)} öğe, {res['bytes'] / 1e6:.1f} MB ham")
        time.sleep(20)


if __name__ == "__main__":
    main()
