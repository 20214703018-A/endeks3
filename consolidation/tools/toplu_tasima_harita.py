#!/usr/bin/env python3
"""Toplu taşıma harita uygulaması (yerel): haritada bir noktaya tıkla → çap içindeki duraklar, geçen hatlar,
hatların güzergâh çizgileri ve seçilen gün/saat aralığındaki geçiş saatleri.

Çalıştırma:  python3 toplu_tasima_harita.py [--port 8765]   → http://localhost:8791
Veri: kanonik veritabanı (salt okunur). Uç nokta: /api/nokta?lat=..&lon=..&yaricap=250&gun=hafta_ici&saat=07:00-10:00

Güzergâh çizgisi: yayımcının yol geometrisi (GTFS shapes / KentKart pointList) varsa o; yoksa (İETT, EGO, İzmir raylı)
hattın duraklarını sırayla birleştiren çizgi — cevapta cizgi_turu='durak_sirasi' olarak işaretlenir.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import duckdb

CAN = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION" / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
HTML = Path(__file__).with_name("web") / "toplu_tasima_harita.html"
GUN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
GUN_TR = {"hafta_ici": 0, "pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}

_con = None
_lock = threading.Lock()


def con():
    global _con
    if _con is None:
        _con = duckdb.connect(str(CAN), read_only=True)
    return _con


def hms(sec):
    h, m = divmod(int(sec) // 60, 60)
    return f"{h % 24:02d}:{m:02d}" + ("+" if h >= 24 else "")


def nokta(q: dict) -> dict:
    lat, lon = float(q["lat"]), float(q["lon"])
    r = min(float(q.get("yaricap", 250)), 2000)
    gun = q.get("gun", "bugun")
    tarih = dt.date.today() if gun == "bugun" else None
    wd = tarih.weekday() if tarih else GUN_TR.get(gun, 0)
    s0 = s1 = None
    if q.get("saat"):
        a, b = q["saat"].split("-")
        s0 = int(a[:2]) * 3600 + int(a[3:5]) * 60
        s1 = int(b[:2]) * 3600 + int(b[3:5]) * 60
    roller = ("birincil", "ikincil") if q.get("tum") == "1" else ("birincil",)
    dlat = r / 111_320
    dlon = r / (111_320 * math.cos(math.radians(lat)))
    c = con()
    dist = (f"2*6371000*asin(sqrt(pow(sin(radians(s.lat-{lat})/2),2)+cos(radians({lat}))*cos(radians(s.lat))"
            f"*pow(sin(radians(s.lon-{lon})/2),2)))")
    stops = c.execute(f"""SELECT s.stop_uid, s.stop_name, s.lat, s.lon, {dist} m, s.il_mekansal, s.ilce_mekansal, s.mahalle_mekansal,
            s.feed_id, f.query_role, s.coord_repair
        FROM transit_stop s JOIN transit_feed f USING (feed_id)
        WHERE s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?""",
                      [lat - dlat, lat + dlat, lon - dlon, lon + dlon]).fetchall()
    stops = [x for x in stops if x[4] <= r]
    tarifeli = [x for x in stops if x[9] in roller]
    osm = [x for x in stops if x[9] == "yedek_saatsiz"]
    use = tarifeli if tarifeli else osm  # tarifeli kaynak varsa OSM yedeği gösterilmez
    out = {"merkez": [lat, lon], "yaricap": r, "gun": GUN[wd], "tarih": str(tarih) if tarih else None,
           "duraklar": [dict(id=x[0], ad=x[1], lat=x[2], lon=x[3], mesafe_m=round(x[4]), il=x[5], ilce=x[6], mahalle=x[7],
                             kaynak=x[8], konum_tahmini=(x[10] or "").startswith("tahmini")) for x in use],
           "hatlar": [], "osm_yedegi": not tarifeli and bool(osm)}
    if not use:
        return out
    ids = [x[0] for x in use]
    dmap = {x[0]: x for x in use}
    ph = ",".join("?" * len(ids))
    # hat-yön listesi: çember içindeki duraklardan geçen tüm hat/yönler (saatsiz olanlar dahil)
    rs = c.execute(f"""SELECT DISTINCT rs.route_uid, rs.direction_id, rs.stop_uid, r.route_short_name, r.route_long_name, r.mode,
            r.route_color, r.feed_id, f.is_expired, f.calendar_end
        FROM transit_route_stop rs JOIN transit_route r ON r.route_uid = rs.route_uid JOIN transit_feed f ON f.feed_id = r.feed_id
        WHERE rs.stop_uid IN ({ph})""", ids).fetchall()
    hat = {}
    for ru, di, su, sn, ln, md, col, fe, exp, cend in rs:
        k = (ru, di)
        h = hat.setdefault(k, dict(route_uid=ru, yon_id=di, hat=sn, ad=ln, tur=md, renk=col, kaynak=fe,
                                   suresi_dolmus=bool(exp), tarife_bitis=str(cend) if cend else None,
                                   duraklar=set(), yon=None, saatler=[], tahmini=False))
        h["duraklar"].add(su)
    # saatler: her hat-yön için çembere en yakın durakta
    q2 = f"""SELECT route_uid, direction_id, headsign, stop_uid, departure_sec, time_source
        FROM v_transit_departure WHERE stop_uid IN ({ph}) AND {GUN[wd]} = 1"""
    prm = list(ids)
    if tarih:
        q2 += " AND (is_expired OR start_date IS NULL OR start_date <= ?) AND (is_expired OR end_date IS NULL OR end_date >= ?)"
        prm += [tarih, tarih]
    if s0 is not None:
        q2 += " AND (departure_sec % 86400) BETWEEN ? AND ?"
        prm += [s0, s1]
    dep = c.execute(q2 + " ORDER BY departure_sec", prm).fetchall()
    enyakin = {}
    for ru, di, hs, su, sec, src in dep:
        k = (ru, di)
        if k not in hat:
            continue
        if k not in enyakin or dmap[su][4] < dmap[enyakin[k]][4]:
            enyakin[k] = su
    for ru, di, hs, su, sec, src in dep:
        k = (ru, di)
        if enyakin.get(k) != su:
            continue
        h = hat[k]
        h["yon"] = h["yon"] or hs
        h["saatler"].append(int(sec))
        h["tahmini"] |= "interpolated" in (src or "")
    # hattın hiç seferi var mı (saat yayımlanmıyor mu, yoksa seçilen aralıkta mı yok)
    rids = list({k[0] for k in hat})
    tarifeli_hat = {x[0] for x in c.execute(
        f"SELECT DISTINCT route_uid FROM transit_trip WHERE route_uid IN ({','.join('?' * len(rids))}) AND trip_origin <> 'gtfs_frequency_template'",
        rids).fetchall()}
    # güzergâh çizgileri
    shp = c.execute(f"""SELECT route_uid, direction_id, mode(shape_uid) FROM transit_trip
        WHERE route_uid IN ({','.join('?' * len(rids))}) AND shape_uid IS NOT NULL GROUP BY 1, 2""", rids).fetchall()
    shape_of = {(a, b): s for a, b, s in shp}
    need = list({s for s in shape_of.values()})
    pts = {}
    if need:
        for su, la, lo in c.execute(f"""SELECT shape_uid, lat, lon FROM transit_shape_point WHERE shape_uid IN ({','.join('?' * len(need))})
                ORDER BY shape_uid, seq""", need).fetchall():
            pts.setdefault(su, []).append([round(la, 6), round(lo, 6)])
    # yol geometrisi yoksa: en uzun durak dizisi
    eksik = [k for k in hat if not pts.get(shape_of.get(k) or shape_of.get((k[0], None)) or "")]
    seq_line = {}
    if eksik:
        er = list({k[0] for k in eksik})
        rows = c.execute(f"""WITH p AS (SELECT route_uid, direction_id, pattern_id, count(*) n FROM transit_route_stop
                    WHERE route_uid IN ({','.join('?' * len(er))}) GROUP BY 1, 2, 3),
                b AS (SELECT route_uid, direction_id, arg_max(pattern_id, n) pat FROM p GROUP BY 1, 2)
            SELECT b.route_uid, b.direction_id, s.lat, s.lon FROM b JOIN transit_route_stop rs ON rs.pattern_id = b.pat
                AND rs.route_uid = b.route_uid AND rs.direction_id IS NOT DISTINCT FROM b.direction_id
            JOIN transit_stop s ON s.stop_uid = rs.stop_uid WHERE s.lat IS NOT NULL ORDER BY b.route_uid, b.direction_id, rs.stop_sequence""",
                         er).fetchall()
        for ru, di, la, lo in rows:
            seq_line.setdefault((ru, di), []).append([round(la, 6), round(lo, 6)])
    for k, h in hat.items():
        sid = shape_of.get(k) or shape_of.get((k[0], None))
        if sid and pts.get(sid):
            h["cizgi"], h["cizgi_turu"] = pts[sid], "yol_geometrisi"
        else:
            h["cizgi"], h["cizgi_turu"] = seq_line.get(k) or seq_line.get((k[0], None)) or [], "durak_sirasi"
        h["durum"] = ("saatli" if h["saatler"] else ("secilen_aralikta_sefer_yok" if k[0] in tarifeli_hat
                                                     else "kaynak_saat_yayimlamiyor"))
        h["en_yakin_durak"] = dmap[enyakin[k]][1] if k in enyakin else dmap[min(h["duraklar"], key=lambda s: dmap[s][4])][1]
        h["duraklar"] = sorted(dmap[s][1] for s in h["duraklar"])
        h["sefer_sayisi"] = len(h["saatler"])
    # aynı kaynak + hat numarası + yön adındaki varyantları (İETT her güzergâh varyantını ayrı hat yayımlıyor) birleştir
    oncelik = {"saatli": 0, "secilen_aralikta_sefer_yok": 1, "kaynak_saat_yayimlamiyor": 2}
    birlesik = {}
    for h in hat.values():
        k = (h["kaynak"], h["hat"], h["yon"] or h["ad"])
        b = birlesik.get(k)
        if b is None:
            birlesik[k] = dict(h, varyant=1, saatler=list(h["saatler"]))
            continue
        b["varyant"] += 1
        b["saatler"] += h["saatler"]
        b["tahmini"] |= h["tahmini"]
        b["duraklar"] = sorted(set(b["duraklar"]) | set(h["duraklar"]))
        if oncelik[h["durum"]] < oncelik[b["durum"]]:
            b["durum"] = h["durum"]
        if len(h["cizgi"]) > len(b["cizgi"]) and (h["cizgi_turu"] == b["cizgi_turu"] or h["cizgi_turu"] == "yol_geometrisi"):
            b["cizgi"], b["cizgi_turu"] = h["cizgi"], h["cizgi_turu"]
    for b in birlesik.values():
        b["saatler"] = [hms(x) for x in sorted(set(b["saatler"]))]
        b["sefer_sayisi"] = len(b["saatler"])
        b["route_uid"] = b["route_uid"] + ("+" if b["varyant"] > 1 else "")
    out["hatlar"] = sorted(birlesik.values(), key=lambda h: (h["durum"] != "saatli", -h["sefer_sayisi"], h["hat"] or ""))
    return out


class H(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/nokta":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                with _lock:
                    res = nokta(q)
                return self._send(200, json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except Exception as e:  # hata ayrıntısı arayüzde görünsün
                return self._send(400, json.dumps({"hata": repr(e)}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        self._send(404, b"yok", "text/plain")

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    a = ap.parse_args()
    print(f"http://localhost:{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
