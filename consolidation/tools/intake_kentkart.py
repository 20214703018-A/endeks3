#!/usr/bin/env python3
"""KentKart toplu taşıma hat/durak/sefer saati toplayıcı (Türkiye geneli, KentKart kullanan tüm bölgeler).

Kaynak: KentKart'ın kendi mobil/web uygulamasının kullandığı açık uç noktalar (kimlik doğrulaması yok):
  şehir listesi ... https://service.kentkart.com/rl1/api/v2.0/city
  hat listesi ..... https://service.kentkart.com/rl1/web/nearest/find?region=R&lang=tr
  hat ayrıntısı ... https://service.kentkart.com/rl1/web/pathInfo?region=R&lang=tr&direction=D&displayRouteCode=C&resultType=101110
                    (resultType bit sırası: güzergâh noktaları, canlı araç [ALINMAZ], durak listesi,
                     tarife tablosu, gün tipine göre kalkış saatleri, ayrılmış)
Ham yanıtlar değiştirilmeden saklanır: <RAW>/kentkart_toplu_tasima/<tarih>/{city.json.gz, R/find.json.gz, R/paths.jsonl.gz}
Yeniden başlatılınca tamamlanmış bölgeleri (manifest'te paths kaydı olan) atlar.

Kullanım: python3 intake_kentkart.py [bölge_kodu ...]   (boşsa tüm bölgeler)
"""
from __future__ import annotations

import json
import sys

from intake_common import Intake, now_iso

BASE = "https://service.kentkart.com/rl1"
RESULT_TYPE = "101110"  # canlı araç konumu (busList) hariç her şey


def done_regions(it: Intake) -> set[str]:
    out = set()
    if it.manifest.exists():
        for line in it.manifest.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                rec = json.loads(line)
                if rec.get("kind") == "paths":
                    out.add(rec["region"])
    return out


def main():
    it = Intake("kentkart_toplu_tasima", rate=0.4)
    r = it.get(f"{BASE}/api/v2.0/city")
    cities = r.json()["city"]
    if not (it.dir / "city.json.gz").exists():
        it.save_bytes("city.json", r.content, source_url=r.url, rows=len(cities), extra={"kind": "city"})
    wanted = sys.argv[1:] or [c["id"] for c in cities]
    names = {c["id"]: c["name"] for c in cities}
    skip = done_regions(it)
    for reg in wanted:
        if reg in skip:
            it.log(f"{reg} {names.get(reg)}: zaten tamam, atlandı")
            continue
        r = it.get(f"{BASE}/web/nearest/find", params={"region": reg, "lang": "tr"})
        find = r.json()
        routes = find.get("routeList") or []
        it.save_bytes(f"{reg}/find.json", r.content, source_url=r.url, rows=len(routes),
                      extra={"kind": "find", "region": reg, "city": names.get(reg)})
        recs, empty, errors = [], 0, 0
        for rt in routes:
            code = rt.get("displayRouteCode")
            for direction in ("0", "1"):
                params = {"region": reg, "lang": "tr", "direction": direction,
                          "displayRouteCode": code, "resultType": RESULT_TYPE}
                try:
                    pr = it.get(f"{BASE}/web/pathInfo", params=params, timeout=30)
                    body = pr.json()
                except Exception as e:  # tek hat hatası tüm bölgeyi durdurmasın; hata da kayda geçer
                    errors += 1
                    recs.append({"region": reg, "displayRouteCode": code, "direction": direction,
                                 "fetched_at": now_iso(), "error": repr(e)[:300]})
                    continue
                if not body.get("pathList"):
                    empty += 1
                recs.append({"region": reg, "displayRouteCode": code, "direction": direction,
                             "routeCode": rt.get("routeCode"), "fetched_at": now_iso(),
                             "http_status": pr.status_code, "response": body})
        it.save_jsonl(f"{reg}/paths", recs, source_url=f"{BASE}/web/pathInfo?region={reg}",
                      method="http_get_per_route_direction",
                      extra={"kind": "paths", "region": reg, "city": names.get(reg),
                             "routes": len(routes), "empty_directions": empty, "errors": errors})
        it.log(f"{reg} {names.get(reg)}: {len(routes)} hat, {len(recs)} yön isteği, boş {empty}, hata {errors}")


if __name__ == "__main__":
    main()
