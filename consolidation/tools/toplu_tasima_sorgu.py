#!/usr/bin/env python3
"""Bir noktadan / duraktan / mahalleden geçen toplu taşıma hatları ve geçiş saatleri.

Örnekler:
  python3 toplu_tasima_sorgu.py --lat 41.0082 --lon 28.9784                 # 300 m içindeki duraklar, bugün
  python3 toplu_tasima_sorgu.py --lat 38.4192 --lon 27.1287 --yaricap 500 --gun cumartesi --saat 07:00-10:00
  python3 toplu_tasima_sorgu.py --durak "Kızılay" --il Ankara
  python3 toplu_tasima_sorgu.py --mahalle "Caferağa" --il İstanbul --json > cikti.json

Saat işaretleri: '~' = yayımcı o durak için saat vermemiş, sefer içi doğrusal tahmin (time_source=gtfs_interpolated);
'+' = gece yarısından sonra (ertesi gün). Süresi dolmuş tarifeler (ör. İZBAN 2025-01-01) uyarıyla gösterilir.
Tarifeli kaynak yoksa OSM hat–durak bilgisi (saatsiz) gösterilir.
Veritabanı salt okunur açılır.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import duckdb

CAN = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION" / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
GUN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
GUN_TR = {"pazartesi": 0, "sali": 1, "salı": 1, "carsamba": 2, "çarşamba": 2, "persembe": 3, "perşembe": 3, "cuma": 4,
          "cumartesi": 5, "pazar": 6, "hafta_ici": 0, "haftaici": 0}


def parse_gun(g: str | None):
    if not g or g == "bugun":
        d = dt.date.today()
        return d, d.weekday()
    g2 = g.lower()
    if g2 in GUN_TR:
        return None, GUN_TR[g2]
    d = dt.date.fromisoformat(g)
    return d, d.weekday()


def parse_saat(s: str | None):
    if not s:
        return None, None
    a, b = s.split("-")
    f = lambda x: int(x.split(":")[0]) * 3600 + int(x.split(":")[1]) * 60
    return f(a), f(b)


def fmt(sec, src):
    h, m = divmod(sec // 60, 60)
    mark = "+" if h >= 24 else ""
    return f"{h % 24:02d}:{m:02d}{mark}{'~' if src and 'interpolated' in src else ''}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--yaricap", type=float, default=300, help="metre (varsayılan 300)")
    ap.add_argument("--durak", help="durak adında geçen metin")
    ap.add_argument("--mahalle", help="mahalle adı (durağın fiziken bulunduğu)")
    ap.add_argument("--il", help="il adı süzgeci")
    ap.add_argument("--gun", help="bugun | hafta_ici | cumartesi | pazar | pazartesi.. | YYYY-MM-DD (varsayılan bugün)")
    ap.add_argument("--saat", help="HH:MM-HH:MM aralığı")
    ap.add_argument("--tum-kaynaklar", action="store_true", help="ikincil (yinelenen) kaynakları da göster")
    ap.add_argument("--en-fazla-durak", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    c = duckdb.connect(str(CAN), read_only=True)
    # Türkçe büyük/küçük harf: 'KIZILAY' ile 'Kızılay' eşleşsin (I→ı, İ→i)
    c.execute("CREATE TEMP MACRO TRL(x) AS lower(replace(replace(x, 'İ', 'i'), 'I', 'ı'))")
    where, params = ["s.lat IS NOT NULL"], []
    dist_expr = "NULL"
    if a.lat is not None and a.lon is not None:
        dlat = a.yaricap / 111_320
        dlon = a.yaricap / (111_320 * math.cos(math.radians(a.lat)))
        where.append("s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?")
        params += [a.lat - dlat, a.lat + dlat, a.lon - dlon, a.lon + dlon]
        dist_expr = (f"2 * 6371000 * asin(sqrt(pow(sin(radians(s.lat - {a.lat}) / 2), 2) + cos(radians({a.lat})) * cos(radians(s.lat)) "
                     f"* pow(sin(radians(s.lon - {a.lon}) / 2), 2)))")
    if a.durak:
        where.append("TRL(s.stop_name) LIKE '%' || TRL(?) || '%'")
        params.append(a.durak)
    if a.mahalle:
        where.append("TRL(s.mahalle_mekansal) = TRL(?)")
        params.append(a.mahalle)
    if a.il:
        where.append("(TRL(s.il_mekansal) = TRL(?) OR TRL(s.il) = TRL(?))")
        params += [a.il, a.il]
    if len(where) == 1:
        sys.exit("konum (--lat/--lon), --durak veya --mahalle verin")
    roles = {"birincil", "ikincil"} if a.tum_kaynaklar else {"birincil"}
    stops = c.execute(f"""SELECT s.stop_uid, s.stop_name, s.lat, s.lon, s.il_mekansal, s.ilce_mekansal, s.mahalle_mekansal,
            f.feed_id, f.query_role, {dist_expr} AS mesafe_m
        FROM transit_stop s JOIN transit_feed f USING (feed_id)
        WHERE {' AND '.join(where)}""", params).fetchall()
    if a.lat is not None:
        stops = [x for x in stops if x[9] is not None and x[9] <= a.yaricap]
        stops.sort(key=lambda x: x[9])
    tarifeli = [x for x in stops if x[8] in roles][: a.en_fazla_durak]
    osm = [x for x in stops if x[8] == "yedek_saatsiz"]

    tarih, wd = parse_gun(a.gun)
    s0, s1 = parse_saat(a.saat)
    out = {"sorgu": vars(a), "gun": GUN[wd], "tarih": str(tarih) if tarih else None, "duraklar": []}
    for st in tarifeli:
        ids = [st[0]]
        q = f"""SELECT route_short_name, route_long_name, mode, headsign, direction_id, departure_sec, time_source,
                    is_expired, calendar_end, service_label, feed_id, start_date, end_date
                FROM v_transit_departure WHERE stop_uid = ? AND {GUN[wd]} = 1"""
        prm = ids[:]
        if tarih:
            q += " AND (is_expired OR start_date IS NULL OR start_date <= ?) AND (is_expired OR end_date IS NULL OR end_date >= ?)"
            prm += [tarih, tarih]
        if s0 is not None:
            q += " AND (departure_sec % 86400) BETWEEN ? AND ?"
            prm += [s0, s1]
        rows = c.execute(q + " ORDER BY route_short_name, direction_id, departure_sec", prm).fetchall()
        hatlar = {}
        for r in rows:
            k = (r[0], r[1], r[2], r[3], r[4])
            h = hatlar.setdefault(k, {"hat": r[0], "ad": r[1], "tur": r[2], "yon": r[3], "yon_id": r[4], "saatler": [],
                                      "tahmini_saat_var": False, "suresi_dolmus_tarife": bool(r[7]),
                                      "tarife_bitis": str(r[8]) if r[8] else None, "kaynak": r[10]})
            h["saatler"].append(fmt(r[5], r[6]))
            h["tahmini_saat_var"] |= 'interpolated' in (r[6] or '')
        for h in hatlar.values():  # aynı dakikada yinelenen (farklı servis kimliği) kalkışları tekille
            h["saatler"] = list(dict.fromkeys(h["saatler"]))
            h["sefer_sayisi"] = len(h["saatler"])
        # saat bulunamadıysa: bu duraktan geçen hatlar ve hattın kaynakta tarifesi olup olmadığı
        saatsiz = []
        if not hatlar:
            for r in c.execute("""SELECT DISTINCT r.route_short_name, r.route_long_name, r.mode,
                        EXISTS (SELECT 1 FROM transit_trip t WHERE t.route_uid = r.route_uid AND t.trip_origin <> 'gtfs_frequency_template') tarifeli
                    FROM transit_route_stop rs JOIN transit_route r ON r.route_uid = rs.route_uid WHERE rs.stop_uid = ?
                    ORDER BY 1""", ids).fetchall():
                saatsiz.append({"hat": r[0], "ad": r[1], "tur": r[2],
                                "durum": "secilen_gun_saatte_sefer_yok" if r[3] else "kaynak_tarife_yayimlamiyor"})
        out["duraklar"].append({"durak": st[1], "hatlar_saatsiz": saatsiz, "durak_id": st[0], "lat": st[2], "lon": st[3], "il": st[4], "ilce": st[5],
                                "mahalle": st[6], "mesafe_m": round(st[9]) if st[9] is not None else None,
                                "kaynak": st[7], "hatlar": list(hatlar.values())})
    if not any(d["hatlar"] for d in out["duraklar"]) and osm:
        ids = [x[0] for x in osm[: a.en_fazla_durak]]
        rows = c.execute(f"""SELECT stop_uid, stop_name, route_short_name, route_long_name, mode
            FROM v_transit_stop_routes WHERE stop_uid IN ({','.join('?' * len(ids))})""", ids).fetchall()
        byst = {}
        for r in rows:
            byst.setdefault((r[0], r[1]), []).append({"hat": r[2], "ad": r[3], "tur": r[4]})
        out["osm_saatsiz"] = [{"durak": k[1], "durak_id": k[0], "hatlar": v} for k, v in byst.items()]
    c.close()

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    gun_tr = dict(zip(GUN, ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]))
    print(f"Gün: {gun_tr[out['gun']]}{' (' + out['tarih'] + ')' if out['tarih'] else ''}"
          + (f", saat {a.saat}" if a.saat else "") + f" — {len(out['duraklar'])} tarifeli durak")
    for d in out["duraklar"]:
        yer = " / ".join(x for x in (d["mahalle"], d["ilce"], d["il"]) if x)
        print(f"\n■ {d['durak']}  [{d['kaynak']}]" + (f"  {d['mesafe_m']} m" if d["mesafe_m"] is not None else "") + f"  ({yer})")
        for h in d["hatlar_saatsiz"]:
            dur = "seçilen gün/saatte sefer yok" if h["durum"] == "secilen_gun_saatte_sefer_yok" else "kaynak bu hat için sefer saati yayımlamıyor"
            print(f"   {h['tur']:<12} {h['hat'] or '':<8} {h['ad'] or ''}  — {dur}")
        for h in d["hatlar"]:
            uyar = f"  ⚠ tarife {h['tarife_bitis']} tarihinde sona ermiş (kaynak güncellenmemiş)" if h["suresi_dolmus_tarife"] else ""
            print(f"   {h['tur']:<12} {h['hat'] or '':<8} {h['ad'] or ''} → {h['yon'] or ''}  ({h['sefer_sayisi']} sefer){uyar}")
            print("      " + " ".join(h["saatler"]))
    if out.get("osm_saatsiz"):
        print("\nTarifeli kaynak bulunamadı; OSM'ye göre bu duraklardan geçen hatlar (saat bilgisi yok):")
        for d in out["osm_saatsiz"]:
            print(f"■ {d['durak']}: " + ", ".join(f"{h['hat'] or '?'} {h['ad'] or ''}".strip() for h in d["hatlar"]))
    print("\n~ = yayımcı bu durak için saat vermemiş, sefer içi tahmin;  + = gece yarısından sonra")


if __name__ == "__main__":
    main()
