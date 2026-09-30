#!/usr/bin/env python3
"""Toplu taşıma tarife ambarı v1 — tüm kaynakları tek şemada birleştirir (staging parquet).

Girdi (ham kökler değiştirilmez):
  - Resmî GTFS: İBB İETT otobüs (2026-03), İBB raylı+deniz (2024), İzmir ESHOT/Metro/Tramvay/İZBAN, Konya, Gaziantep,
    FlixBus Türkiye (şehirlerarası)
  - KentKart açık servis ham yanıtları (27 bölge; kentkart_toplu_tasima/<tarih>)
  - OSM hat ilişkileri (GEOPROP/warehouse/product/osm_poi.sqlite: hat, hat_durak, poi) — SAAT YOK, yalnız hat–durak

Çıktı: GEOPROP_CONSOLIDATION/staging/v1.0.0/mobility_logistics/transit_v1/<tablo>.parquet
  transit_feed, transit_route, transit_stop, transit_service, transit_trip, transit_stop_time,
  transit_route_stop, transit_shape_point

Saat alanları: servis gününün gece yarısından itibaren saniye (GTFS kuralı: 24:10:00 = 87000, gece yarısını
aşan sefer). time_source sütunu saatin nereden geldiğini söyler:
  gtfs_given ............ yayımcının dosyasında yazan saat
  gtfs_interpolated ..... yayımcı yalnız bazı duraklara saat vermiş (İETT: ilk/son durak); aradaki duraklar
                          durak sırasına göre doğrusal TAHMİN — ayrı işaretli, resmî saat değildir
  gtfs_frequency ........ yayımcı "şu saatler arası her N dakikada" vermiş (frequencies.txt); tek tek seferlere açıldı
  kentkart_offset ....... ilk durak kalkış saati + işletmecinin verdiği durak süre farkı (KentKart)
Onarımlar (kaynakta bozuk gelen, kanıtla düzeltilen): İETT durak koordinatları Excel binlik ayırıcısıyla bozulmuş
(410.191.700.005.564 → 41.0191700005564); İETT hat adları çift UTF-8 kodlanmış (KADIKÃ–Y → KADIKÖY); İBB raylı
besleme cp1254 kodlu. Her onarım satırda işaretlidir.
"""
from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

RAW = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE"
OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
STG = OUT / "staging" / "v1.0.0" / "mobility_logistics" / "transit_v1"
WORK = OUT / "tmp" / "transit_v1"
OSM_SQLITE = Path.home() / "Desktop" / "GEOPROP" / "warehouse" / "product" / "osm_poi.sqlite"
TODAY = dt.date.today().isoformat()
IBB = RAW / "acikveri_ckan_ibb" / "2026-09-24" / "files"

MODE = {0: "tramvay", 1: "metro", 2: "banliyo_tren", 3: "otobus", 4: "vapur", 5: "teleferik_kablolu",
        6: "teleferik", 7: "funikuler", 11: "trolleybus", 12: "monoray", 200: "sehirlerarasi_otobus",
        700: "otobus", 900: "tramvay", 1000: "vapur", 1400: "funikuler"}

# KentKart bölge adı → il (bölge merkez koordinatıyla kontrol edildi)
KK_IL = {"Alanya": "Antalya", "Ereğli": "Zonguldak", "Alaplı": "Zonguldak", "Lüleburgaz": "Kırklareli",
         "Kirklareli": "Kırklareli", "Safranbolu": "Karabük", "Altınova": "Yalova", "Akçakoca": "Düzce",
         "Çivril": "Denizli"}

# Resmî/yayımcı GTFS zip beslemeleri: (feed_id, il, işletme, zip yolu glob, kaynak url, sorgu önceliği)
GTFS_ZIPS = [
    ("izmir_eshot", "İzmir", "ESHOT", "toplu_tasima_gtfs/*/izmir_eshot_otobus.zip", "https://www.eshot.gov.tr/gtfs/bus-eshot-gtfs.zip"),
    ("izmir_metro", "İzmir", "İzmir Metro", "toplu_tasima_gtfs/*/izmir_metro.zip", "https://www.izmirmetro.com.tr/gtfs/rail-metro-gtfs.zip"),
    ("izmir_tramvay", "İzmir", "İzmir Tramvay", "toplu_tasima_gtfs/*/izmir_tramvay.zip", "https://www.tramizmir.com/gtfs/rail-tramizmir-gtfs.zip"),
    ("izmir_izban", "İzmir", "İZBAN", "toplu_tasima_gtfs/*/izmir_izban.zip", "https://www.izban.com.tr/gtfs/rail-izban-gtfs.zip"),
    ("flixbus_tr", "Türkiye", "FlixBus Türkiye", "toplu_tasima_gtfs/*/flixbus_turkiye.zip", "https://gtfs.gis.flix.tech/gtfs_generic_turkey.zip"),
    ("konya_atus", "Konya", "Konya BB ATUS", "acikveri_ckan_konya/*/files/toplu-tasima-gtfs-verileri_*gtfs*.zip", "https://acikveri.konya.bel.tr (toplu-tasima-gtfs-verileri)"),
    ("gaziantep_gaziulas", "Gaziantep", "Gaziulaş", "acikveri_ckan_gaziantep/*/files/toplu-ulasim-gtfs-verileri_*gtfs.zip", "https://acikveri.gaziantep.bel.tr (toplu-ulasim-gtfs-verileri)"),
]
# İBB CKAN'dan tek tek CSV (zip değil) gelen beslemeler
IBB_FEEDS = {
    "istanbul_iett": dict(il="İstanbul", op="İETT", prefix="iett-gtfs-verisi_", sep=";", enc="utf-8-sig",
                          url="https://data.ibb.gov.tr/dataset/iett-gtfs-verisi"),
    "istanbul_rayli_deniz": dict(il="İstanbul", op="İBB Raylı Sistemler / Şehir Hatları / Marmaray", prefix="public-transport-gtfs-data_",
                                 sep=",", enc="cp1254", url="https://data.ibb.gov.tr/dataset/public-transport-gtfs-data"),
}


def sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def latest(pattern: str) -> Path | None:
    xs = sorted(glob.glob(str(RAW / pattern)))
    return Path(xs[-1]) if xs else None


def fnum(v):
    """KentKart sayı alanı: '37.1' → 37.1; 'null'/''/None → None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def repair_mojibake(s):
    """Çift UTF-8 kodlanmış metni onarır (UTF-8 baytları cp1252/latin-1 karışık okunmuş: 'KADIKÃ–Y', 'ESATPAÅ\x9fA').
    Her karakter cp1252'deki baytına, yoksa (U+0080–U+00FF kontrol/latin-1) kod noktasına çevrilip UTF-8 çözülür;
    çözülemezse metin olduğu gibi kalır."""
    if not isinstance(s, str) or not any(ch in s for ch in "ÃÄÅ"):
        return s, False
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) < 256:
                out.append(ord(ch))
            else:
                return s, False
    try:
        return out.decode("utf-8"), True
    except UnicodeDecodeError:
        return s, False


def repair_excel_coord(v):
    """'410.191.700.005.564' → 41.0191700005564 (Excel binlik ayırıcı bozulması; TR enlem/boylam 2 tam haneli)."""
    if v is None:
        return None, None
    s = str(v).strip()
    if s.count(".") <= 1:
        try:
            return float(s), "none"
        except ValueError:
            return None, "unparsed"
    d = s.replace(".", "")
    if not d.isdigit() or len(d) < 4:
        return None, "unparsed"
    return float(d[:2] + "." + d[2:]), "excel_thousand_sep"


def parse_ego_page(html_text: str) -> dict:
    """EGO hareketsaatleri sayfası → künye, gün tipi başına kalkış listesi (saat, not), sıralı durak listesi."""
    import html as _h
    import re as _re
    txt = lambda x: _re.sub(r"\s+", " ", _h.unescape(_re.sub(r"<[^>]+>", " ", x))).strip()
    out = {"kunye": {}, "kalkis": {}, "duraklar": []}
    i = html_text.find('class="hs-kv"')
    if i >= 0:
        blk = html_text[i:html_text.find("</table>", i)]
        for row in _re.findall(r"<tr[^>]*>(.*?)</tr>", blk, _re.S):
            cells = [txt(c) for c in _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, _re.S)]
            cells = [c for c in cells if c not in ("", ":")]
            if len(cells) >= 2:
                out["kunye"][cells[0]] = cells[1]
    i = html_text.find('class="hs-table"')
    if i >= 0:
        blk = html_text[i:html_text.find("</table>", i)]
        heads = [txt(h) for h in _re.findall(r"<th[^>]*>(.*?)</th>", blk, _re.S)]
        cols = _re.findall(r'<td class="hs-times"[^>]*>(.*?)</td>', blk, _re.S)
        for head, col in zip(heads, cols):
            items = []
            for part in _re.split(r"<br\s*/?>", col):
                t = txt(part)
                m = _re.match(r"(\d{1,2}):(\d{2})\s*(.*)", t)
                if m:
                    note = m.group(3).strip()
                    items.append((int(m.group(1)) * 3600 + int(m.group(2)) * 60, None if note in ("", "-") else note))
            out["kalkis"][head] = items
    i = html_text.find("route-table")
    if i >= 0:
        blk = html_text[i:html_text.find("</table>", i)]
        for row in _re.findall(r"<tr[^>]*>(.*?)</tr>", blk, _re.S):
            cells = [txt(c) for c in _re.findall(r"<td[^>]*>(.*?)</td>", row, _re.S)]
            cells = [c for c in cells if c != ""]
            if len(cells) >= 3 and cells[0].isdigit():
                out["duraklar"].append({"sira": int(cells[0]), "durak_no": cells[1], "ad": cells[2],
                                        "adres": cells[3] if len(cells) > 3 else None})
    return out


class Builder:
    def __init__(self):
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir(parents=True)
        self.c = duckdb.connect(str(WORK / "work.duckdb"))
        self.c.execute("SET memory_limit='2GB'; SET threads=2; SET preserve_insertion_order=false")
        self.c.execute(f"SET temp_directory='{WORK}/spill'")
        self.feeds = []
        self._create()

    def _create(self):
        c = self.c
        c.execute("""CREATE TABLE transit_route (route_uid VARCHAR, feed_id VARCHAR, route_id VARCHAR, route_short_name VARCHAR,
            route_long_name VARCHAR, route_desc VARCHAR, route_type INTEGER, mode VARCHAR, agency_name VARCHAR, route_color VARCHAR,
            il VARCHAR, name_repair VARCHAR)""")
        c.execute("""CREATE TABLE transit_stop (stop_uid VARCHAR, feed_id VARCHAR, stop_id VARCHAR, stop_code VARCHAR, stop_name VARCHAR,
            stop_desc VARCHAR, lat DOUBLE, lon DOUBLE, coord_repair VARCHAR, il VARCHAR)""")
        c.execute("""CREATE TABLE transit_service (service_uid VARCHAR, feed_id VARCHAR, service_id VARCHAR, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER, start_date DATE, end_date DATE,
            service_label VARCHAR)""")
        c.execute("""CREATE TABLE transit_trip (trip_uid VARCHAR, feed_id VARCHAR, route_uid VARCHAR, service_uid VARCHAR, direction_id INTEGER,
            headsign VARCHAR, shape_uid VARCHAR, trip_origin VARCHAR, source_trip_id VARCHAR)""")
        c.execute("""CREATE TABLE transit_stop_time (trip_uid VARCHAR, stop_uid VARCHAR, stop_sequence INTEGER, arrival_sec INTEGER,
            departure_sec INTEGER, time_source VARCHAR)""")
        c.execute("""CREATE TABLE transit_route_stop (route_uid VARCHAR, direction_id INTEGER, pattern_id VARCHAR, stop_sequence INTEGER,
            stop_uid VARCHAR, pattern_source VARCHAR)""")
        c.execute("CREATE TABLE transit_shape_point (shape_uid VARCHAR, seq INTEGER, lat DOUBLE, lon DOUBLE)")

    # ---------------- GTFS ----------------
    def _csv(self, path: Path, name: str, sep=",", enc="utf-8"):
        """GTFS dosyasını tümü metin olarak okur (başlık BOM'u temizlenir)."""
        if not path.exists() or path.stat().st_size == 0:
            return None
        if enc not in ("utf-8", "utf-8-sig") or str(path).endswith(".gz"):
            op = gzip.open if str(path).endswith(".gz") else open
            with op(path, "rb") as f:
                text = f.read().decode(enc)
            path = WORK / f"_{name}_{os.getpid()}.csv"
            path.write_text(text.lstrip("﻿"), encoding="utf-8")
        with open(path, "rb") as f:  # aynı beslemede dosyadan dosyaya ayraç değişebiliyor (İETT agency ',' diğerleri ';')
            header = f.readline().decode("utf-8", "replace")
        sep = ";" if header.count(";") > header.count(",") else ","
        rel =f"read_csv('{path}', delim='{sep}', header=true, all_varchar=true, quote='\"', ignore_errors=false, null_padding=true)"
        tname = f"g_{name}"
        self.c.execute(f"CREATE OR REPLACE TEMP TABLE {tname} AS SELECT * FROM {rel}")
        cols = [r[0] for r in self.c.execute(f"DESCRIBE {tname}").fetchall()]
        for col in cols:  # başlıkta BOM/boşluk
            clean = col.replace("﻿", "").strip()
            if clean != col:
                self.c.execute(f'ALTER TABLE {tname} RENAME COLUMN "{col}" TO "{clean}"')
        return tname

    def _col(self, t, name, default="NULL"):
        cols = {r[0] for r in self.c.execute(f"DESCRIBE {t}").fetchall()}
        return f'"{name}"' if name in cols else default

    def load_gtfs(self, feed_id, il, operator, files: dict, source_url, raw_path, sep=",", enc="utf-8", special=None):
        """files: {'routes': Path, 'stops': Path, ...}"""
        c = self.c
        t = {k: self._csv(p, f"{feed_id}_{k}", sep, enc) for k, p in files.items() if p}
        fid = feed_id
        col = lambda tb, n, d="NULL": self._col(t[tb], n, d)
        # agency adı
        agency = None
        if t.get("agency"):
            agency = c.execute(f"SELECT string_agg(DISTINCT agency_name, ' / ') FROM {t['agency']}").fetchone()[0]
        # hatlar
        ag_col = col("routes", "agency_id")
        agency_expr = (f"coalesce((SELECT a.agency_name FROM {t['agency']} a WHERE a.agency_id = r.{ag_col} LIMIT 1), ?)"
                       if t.get("agency") and ag_col != "NULL" else "?")
        c.execute(f"""INSERT INTO transit_route SELECT '{fid}:' || route_id, '{fid}', route_id, {col('routes','route_short_name')},
            {col('routes','route_long_name')}, {col('routes','route_desc')}, TRY_CAST(route_type AS INTEGER), NULL,
            {agency_expr}, {col('routes','route_color')}, ?, NULL FROM {t['routes']} r""", [agency or operator, il])
        # duraklar
        if special == "iett":
            df = c.execute(f"SELECT stop_id, stop_code, stop_name, stop_desc, stop_lat, stop_lon FROM {t['stops']}").df()
            lat = df["stop_lat"].map(repair_excel_coord)
            lon = df["stop_lon"].map(repair_excel_coord)
            df["lat"] = [x[0] for x in lat]
            df["lon"] = [x[0] for x in lon]
            df["coord_repair"] = [a[1] if a[1] == b[1] else f"{a[1]}|{b[1]}" for a, b in zip(lat, lon)]
            c.register("iett_stops", df)
            c.execute(f"""INSERT INTO transit_stop SELECT '{fid}:' || stop_id, '{fid}', stop_id, stop_code, stop_name, stop_desc,
                lat, lon, coord_repair, ? FROM iett_stops""", [il])
            c.unregister("iett_stops")
        else:
            c.execute(f"""INSERT INTO transit_stop SELECT '{fid}:' || stop_id, '{fid}', stop_id, {col('stops','stop_code')}, stop_name,
                {col('stops','stop_desc')}, TRY_CAST(stop_lat AS DOUBLE), TRY_CAST(stop_lon AS DOUBLE), 'none', ?
                FROM {t['stops']} WHERE coalesce({col('stops','location_type','NULL')}, '0') IN ('0', '')""", [il])
        # servis takvimi
        if t.get("calendar"):
            c.execute(f"""INSERT INTO transit_service SELECT '{fid}:' || service_id, '{fid}', service_id,
                monday::INT, tuesday::INT, wednesday::INT, thursday::INT, friday::INT, saturday::INT, sunday::INT,
                TRY_STRPTIME(start_date, '%Y%m%d')::DATE, TRY_STRPTIME(end_date, '%Y%m%d')::DATE, {col('calendar','service_name')}
                FROM {t['calendar']}""")
        # seferler
        c.execute(f"""INSERT INTO transit_trip SELECT '{fid}:' || trip_id, '{fid}', '{fid}:' || route_id, '{fid}:' || service_id,
            TRY_CAST({col('trips','direction_id')} AS INTEGER), {col('trips','trip_headsign')},
            CASE WHEN {col('trips','shape_id')} IS NULL OR {col('trips','shape_id')} = '' THEN NULL ELSE '{fid}:' || {col('trips','shape_id')} END,
            'gtfs', trip_id FROM {t['trips']}""")
        # durak saatleri (saniye); boş saatler NULL
        c.execute(f"""CREATE OR REPLACE TEMP TABLE st AS SELECT '{fid}:' || trip_id trip_uid, '{fid}:' || stop_id stop_uid,
            TRY_CAST(stop_sequence AS INTEGER) seq,
            CASE WHEN nullif(trim(arrival_time),'') IS NULL THEN NULL ELSE
              TRY_CAST(split_part(trim(arrival_time),':',1) AS INT)*3600 + TRY_CAST(split_part(trim(arrival_time),':',2) AS INT)*60 + TRY_CAST(split_part(trim(arrival_time),':',3) AS INT) END arr,
            CASE WHEN nullif(trim(departure_time),'') IS NULL THEN NULL ELSE
              TRY_CAST(split_part(trim(departure_time),':',1) AS INT)*3600 + TRY_CAST(split_part(trim(departure_time),':',2) AS INT)*60 + TRY_CAST(split_part(trim(departure_time),':',3) AS INT) END dep
            FROM {t['stop_times']}""")
        # boş saatleri aynı seferin önceki/sonraki bilinen saatleri arasında sıraya göre doğrusal doldur (işaretli)
        c.execute("""CREATE OR REPLACE TEMP TABLE st2 AS
            WITH b AS (SELECT *, coalesce(dep, arr) known FROM st),
            w AS (SELECT *,
                last_value(CASE WHEN known IS NOT NULL THEN known END IGNORE NULLS) OVER (PARTITION BY trip_uid ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) pv,
                last_value(CASE WHEN known IS NOT NULL THEN seq END IGNORE NULLS) OVER (PARTITION BY trip_uid ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) ps,
                first_value(CASE WHEN known IS NOT NULL THEN known END IGNORE NULLS) OVER (PARTITION BY trip_uid ORDER BY seq ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) nv,
                first_value(CASE WHEN known IS NOT NULL THEN seq END IGNORE NULLS) OVER (PARTITION BY trip_uid ORDER BY seq ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) ns
              FROM b)
            SELECT trip_uid, stop_uid, seq,
              CASE WHEN known IS NOT NULL THEN coalesce(arr, dep)
                   WHEN pv IS NOT NULL AND nv IS NOT NULL AND ns > ps THEN round(pv + (nv - pv) * (seq - ps) / (ns - ps))::INT END arr,
              CASE WHEN known IS NOT NULL THEN coalesce(dep, arr)
                   WHEN pv IS NOT NULL AND nv IS NOT NULL AND ns > ps THEN round(pv + (nv - pv) * (seq - ps) / (ns - ps))::INT END dep,
              CASE WHEN known IS NOT NULL THEN 'gtfs_given'
                   WHEN pv IS NOT NULL AND nv IS NOT NULL AND ns > ps THEN 'gtfs_interpolated' ELSE 'gtfs_missing' END src
            FROM w""")
        # frekans tabanlı seferler (frequencies.txt): şablon sefer, başlangıç–bitiş arası her headway'de bir sefer
        if t.get("frequencies"):
            c.execute(f"""CREATE OR REPLACE TEMP TABLE fq AS SELECT '{fid}:' || trip_id trip_uid,
                TRY_CAST(split_part(start_time,':',1) AS INT)*3600+TRY_CAST(split_part(start_time,':',2) AS INT)*60+TRY_CAST(split_part(start_time,':',3) AS INT) s,
                TRY_CAST(split_part(end_time,':',1) AS INT)*3600+TRY_CAST(split_part(end_time,':',2) AS INT)*60+TRY_CAST(split_part(end_time,':',3) AS INT) e,
                TRY_CAST(headway_secs AS INT) h FROM {t['frequencies']} WHERE TRY_CAST(headway_secs AS INT) > 0""")
            c.execute("""CREATE OR REPLACE TEMP TABLE fq_dep AS SELECT f.trip_uid, f.trip_uid || '@' || d AS new_trip_uid, d dep0
                FROM fq f, LATERAL (SELECT unnest(range(f.s, f.e, f.h)) d)""")
            c.execute("""CREATE OR REPLACE TEMP TABLE st_base AS SELECT trip_uid, min(coalesce(dep, arr)) t0 FROM st2 GROUP BY 1""")
            c.execute("""INSERT INTO transit_trip SELECT q.new_trip_uid, t.feed_id, t.route_uid, t.service_uid, t.direction_id, t.headsign,
                t.shape_uid, 'gtfs_frequency', t.source_trip_id FROM fq_dep q JOIN transit_trip t ON t.trip_uid = q.trip_uid""")
            c.execute("""INSERT INTO transit_stop_time SELECT q.new_trip_uid, s.stop_uid, s.seq, q.dep0 + s.arr - b.t0, q.dep0 + s.dep - b.t0, 'gtfs_frequency'
                FROM fq_dep q JOIN st2 s ON s.trip_uid = q.trip_uid JOIN st_base b ON b.trip_uid = q.trip_uid""")
            # şablon seferin kendisi frekansla temsil edildiği için saat tablosuna şablon olarak girmez
            c.execute("DELETE FROM st2 WHERE trip_uid IN (SELECT DISTINCT trip_uid FROM fq)")
            c.execute("UPDATE transit_trip SET trip_origin='gtfs_frequency_template' WHERE trip_uid IN (SELECT DISTINCT trip_uid FROM fq)")
        c.execute("INSERT INTO transit_stop_time SELECT trip_uid, stop_uid, seq, arr, dep, src FROM st2")
        # şekil
        if t.get("shapes"):
            c.execute(f"""INSERT INTO transit_shape_point SELECT '{fid}:' || shape_id, TRY_CAST(shape_pt_sequence AS INT),
                TRY_CAST(shape_pt_lat AS DOUBLE), TRY_CAST(shape_pt_lon AS DOUBLE) FROM {t['shapes']}""")
        for k in list(t):
            if t[k]:
                c.execute(f"DROP TABLE IF EXISTS {t[k]}")
        feed_info = {}
        self.feeds.append(dict(feed_id=fid, source_kind="gtfs", il=il, operator=operator, source_url=source_url,
                               raw_path=str(raw_path), **feed_info))

    def gtfs_zip(self, feed_id, il, operator, pattern, url):
        z = latest(pattern)
        if not z:
            print("  YOK:", pattern)
            return
        d = WORK / feed_id
        d.mkdir()
        with zipfile.ZipFile(z) as zf:
            zf.extractall(d)
        files = {k: (d / f"{k}.txt") for k in ("agency", "routes", "stops", "calendar", "trips", "stop_times", "shapes", "frequencies")}
        files = {k: v for k, v in files.items() if v.exists()}
        self.load_gtfs(feed_id, il, operator, files, url, z)
        self.feeds[-1]["raw_sha256"] = sha256(z)
        fi = d / "feed_info.txt"
        if fi.exists():
            r = pd.read_csv(fi, dtype=str).iloc[0].to_dict()
            self.feeds[-1]["feed_start_date"] = r.get("feed_start_date")
            self.feeds[-1]["feed_end_date"] = r.get("feed_end_date")
        shutil.rmtree(d)

    def ibb(self, feed_id, cfg):
        files = {}
        for k in ("agency", "routes", "stops", "calendar", "trips", "shapes", "frequencies", "stop_times"):
            xs = sorted(IBB.glob(f"{cfg['prefix']}*_{k}.csv.gz"))
            if xs:
                files[k] = xs[0]
        if feed_id == "istanbul_iett":
            # CSV sürümü Excel satır sınırında (1.048.576) kesik → tam sürüm zip içinden (virgül ayraçlı)
            z = next(IBB.glob(f"{cfg['prefix']}*_stop_times.zip"))
            d = WORK / "iett_st"
            d.mkdir()
            with zipfile.ZipFile(z) as zf:
                zf.extractall(d)
            st = d / "stop_times.txt"
            tname = self._csv(st, "iett_stop_times", ",", "utf-8")
            files.pop("stop_times")
            # stop_times dışındaki dosyalar ';'
            self._load_split(feed_id, cfg, files, tname, z)
            shutil.rmtree(d)
        else:
            self.load_gtfs(feed_id, cfg["il"], cfg["op"], files, cfg["url"], IBB, sep=cfg["sep"], enc=cfg["enc"])
        self.feeds[-1]["raw_sha256"] = None

    def _load_split(self, feed_id, cfg, files, st_table, zpath):
        # load_gtfs'in stop_times'ı hazır tablo olarak alabilmesi için geçici yol: tabloyu CSV'ye dökmeden kullan
        orig = self._csv

        def patched(path, name, sep=",", enc="utf-8"):
            if name.endswith("_stop_times"):
                return st_table
            return orig(path, name, cfg["sep"], cfg["enc"])
        self._csv = patched
        files = dict(files)
        files["stop_times"] = zpath
        self.load_gtfs(feed_id, cfg["il"], cfg["op"], files, cfg["url"], IBB, special="iett")
        self._csv = orig
        # hat adı onarımı (çift UTF-8)
        df = self.c.execute("SELECT route_uid, route_short_name, route_long_name, route_desc FROM transit_route WHERE feed_id=?", [feed_id]).df()
        flags = [False] * len(df)
        for col in ("route_short_name", "route_long_name", "route_desc"):
            rr = df[col].map(repair_mojibake)
            df[col] = [x[0] for x in rr]
            flags = [f or x[1] for f, x in zip(flags, rr)]
        df["name_repair"] = ["double_utf8" if f else "none" for f in flags]
        self.c.register("rfix", df)
        self.c.execute("""UPDATE transit_route r SET route_short_name=f.route_short_name, route_long_name=f.route_long_name,
            route_desc=f.route_desc, name_repair=f.name_repair FROM rfix f WHERE r.route_uid=f.route_uid""")
        # durak adı ve yön adı da aynı bozulmayı taşıyabilir
        for tb, key, col in (("transit_stop", "stop_uid", "stop_name"), ("transit_trip", "trip_uid", "headsign")):
            d2 = self.c.execute(f"SELECT {key}, {col} FROM {tb} WHERE feed_id=? AND regexp_matches({col}, '[ÃÄÅ]')", [feed_id]).df()
            if len(d2):
                d2[col] = [repair_mojibake(x)[0] for x in d2[col]]
                self.c.register("fix2", d2)
                self.c.execute(f"UPDATE {tb} t SET {col} = f.{col} FROM fix2 f WHERE t.{key} = f.{key}")
                self.c.unregister("fix2")
        self.c.unregister("rfix")

    # ---------------- KentKart ----------------
    def kentkart(self):
        kdir = sorted(glob.glob(str(RAW / "kentkart_toplu_tasima" / "*")))
        if not kdir:
            return
        kdir = Path(kdir[-1])
        run_date = kdir.name
        man = [json.loads(l) for l in (kdir / "manifest.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
        done = {m["region"]: m for m in man if m.get("kind") == "paths"}
        cities = {c["id"]: c for c in json.loads(gzip.open(kdir / "city.json.gz").read())["city"]}
        routes, stops, trips, sts, rstops, shapes, services = [], {}, [], [], [], [], {}
        for reg, m in sorted(done.items()):
            city = cities.get(reg, {}).get("name") or m.get("city")
            il = KK_IL.get(city, city)
            fid = f"kentkart_{reg}"
            find = json.loads(gzip.open(kdir / reg / "find.json.gz").read())
            for s in find.get("stopList") or []:
                stops.setdefault(f"{fid}:{s['stopId']}", dict(stop_uid=f"{fid}:{s['stopId']}", feed_id=fid, stop_id=s["stopId"],
                                 stop_name=s.get("name"), lat=fnum(s.get("lat")), lon=fnum(s.get("lng")), il=il))
            for r in find.get("routeList") or []:
                routes.append(dict(route_uid=f"{fid}:{r['displayRouteCode']}", feed_id=fid, route_id=r.get("routeCode"),
                                   route_short_name=r.get("displayRouteCode"), route_long_name=r.get("name"),
                                   route_type=int(r["routeType"]) if str(r.get("routeType", "")).isdigit() else 3,
                                   agency_name=r.get("agencyName"), route_color=r.get("routeColor"), il=il))
            with gzip.open(kdir / reg / "paths.jsonl.gz", "rt", encoding="utf-8") as f:
                for line in f.read().split("\n"):
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if "response" not in rec:
                        continue
                    ruid = f"{fid}:{rec['displayRouteCode']}"
                    direction = int(rec["direction"])
                    for pi, p in enumerate(rec["response"].get("pathList") or []):
                        pat = f"{ruid}:{direction}:{p.get('path_code') or pi}"
                        offs = []
                        for b in p.get("busStopList") or []:
                            suid = f"{fid}:{b['stopId']}"
                            stops.setdefault(suid, dict(stop_uid=suid, feed_id=fid, stop_id=b["stopId"], stop_name=b.get("stopName"),
                                                        lat=fnum(b.get("lat")), lon=fnum(b.get("lng")), il=il))
                            seq = int(b["seq"])
                            a = int(fnum(b.get("arrival_offset")) or 0)
                            d = int(fnum(b.get("departure_offset")) or a)
                            offs.append((seq, suid, a, d))
                            rstops.append(dict(route_uid=ruid, direction_id=direction, pattern_id=pat, stop_sequence=seq,
                                               stop_uid=suid, pattern_source="kentkart_path"))
                        for pt in p.get("pointList") or []:
                            if fnum(pt.get("lat")) is not None and fnum(pt.get("lng")) is not None:
                                shapes.append(dict(shape_uid=pat, seq=int(pt["seq"]), lat=fnum(pt["lat"]), lon=fnum(pt["lng"])))
                        for sch in p.get("scheduleList") or []:
                            desc = sch.get("description") or ""
                            suid_srv = f"{fid}:{desc}"
                            if suid_srv not in services and len(desc) >= 7:
                                flags = [1 if ch.isupper() else 0 for ch in desc[:7]]
                                services[suid_srv] = dict(service_uid=suid_srv, feed_id=fid, service_id=desc,
                                                          **dict(zip(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"], flags)),
                                                          start_date=run_date, end_date=None, service_label=desc)
                            for tl in sch.get("timeList") or []:
                                hh, mm, ss = (tl["departureTime"].split(":") + ["0", "0"])[:3]
                                t0 = int(hh) * 3600 + int(mm) * 60 + int(ss)
                                tuid = f"{pat}:{desc}:{tl.get('tripId')}:{t0}"
                                trips.append(dict(trip_uid=tuid, feed_id=fid, route_uid=ruid, service_uid=suid_srv, direction_id=direction,
                                                  headsign=p.get("headSign"), shape_uid=pat, trip_origin="kentkart", source_trip_id=tl.get("tripId"),
                                                  t0=t0, pattern_id=pat))
                        if offs:
                            sts.append((pat, offs))
            self.feeds.append(dict(feed_id=fid, source_kind="kentkart_api", il=il, operator=f"KentKart {city}",
                                   source_url="https://service.kentkart.com/rl1/web/pathInfo", raw_path=str(kdir / reg / "paths.jsonl.gz"),
                                   raw_sha256=m.get("sha256"), fetched_at=m.get("fetched_at")))
        c = self.c
        if not routes:
            return
        c.register("kr", pd.DataFrame(routes))
        c.execute("""INSERT INTO transit_route SELECT DISTINCT ON (route_uid) route_uid, feed_id, route_id, route_short_name, route_long_name, NULL,
            route_type, NULL, agency_name, route_color, il, 'none' FROM kr""")
        c.register("ks", pd.DataFrame(list(stops.values())))
        c.execute("INSERT INTO transit_stop SELECT stop_uid, feed_id, stop_id, NULL, stop_name, NULL, lat, lon, 'none', il FROM ks")
        if services:
            sv = pd.DataFrame(list(services.values()))
            c.register("ksv", sv)
            c.execute("""INSERT INTO transit_service SELECT service_uid, feed_id, service_id, monday, tuesday, wednesday, thursday, friday,
                saturday, sunday, start_date::DATE, NULL, service_label FROM ksv""")
        c.register("krs", pd.DataFrame(rstops))
        c.execute("INSERT INTO transit_route_stop SELECT DISTINCT * FROM krs")
        if shapes:
            c.register("ksh", pd.DataFrame(shapes))
            c.execute("INSERT INTO transit_shape_point SELECT DISTINCT * FROM ksh")
        if trips:
            tdf = pd.DataFrame(trips).drop_duplicates("trip_uid")
            c.register("kt", tdf)
            c.execute("""INSERT INTO transit_trip SELECT trip_uid, feed_id, route_uid, service_uid, direction_id, headsign, shape_uid,
                trip_origin, source_trip_id FROM kt""")
            off = pd.DataFrame([(pat, s, su, a, d) for pat, offs in sts for (s, su, a, d) in offs],
                               columns=["pattern_id", "seq", "stop_uid", "a", "d"]).drop_duplicates(["pattern_id", "seq"])
            c.register("koff", off)
            c.execute("""INSERT INTO transit_stop_time SELECT t.trip_uid, o.stop_uid, o.seq, t.t0 + o.a, t.t0 + o.d, 'kentkart_offset'
                FROM kt t JOIN koff o ON o.pattern_id = t.pattern_id""")
        for v in ("kr", "ks", "ksv", "krs", "ksh", "kt", "koff"):
            try:
                c.unregister(v)
            except Exception:
                pass

    # ---------------- Ankara EGO (resmî sayfa) ----------------
    def ego(self):
        import re as _re
        edir = sorted(glob.glob(str(RAW / "ego_ankara_hareket_saatleri" / "*" / "pages.jsonl.gz")))
        if not edir:
            return
        src = Path(edir[-1])
        run_date = src.parent.name
        fid = "ankara_ego"
        # EGO durak no → koordinat: OSM durak katmanı (ref etiketi = EGO durak numarası), yalnız Ankara
        osm_layer = latest("osm_pbf_katmanlar/*/otogarlar_duraklar.parquet")
        ref = self.c.execute(f"""SELECT ref, arg_min(lat, osm_id) lat, arg_min(lon, osm_id) lon, count(*) n
            FROM '{osm_layer}' WHERE il_adi = 'Ankara' AND ref IS NOT NULL AND regexp_matches(ref, '^[0-9]{{4,6}}$')
            GROUP BY ref""").df().set_index("ref")
        days = {"Hafta içi": ("hafta_ici", [1, 1, 1, 1, 1, 0, 0]), "Cumartesi": ("cumartesi", [0, 0, 0, 0, 0, 1, 0]),
                "Pazar": ("pazar", [0, 0, 0, 0, 0, 0, 1])}
        mode_by_list = {"hat_liste_otobus": 3, "hat_liste_metro": 1, "hat_liste_ankaray": 1}
        routes, stops, trips, sts, rstops = [], {}, [], [], []
        pages = errors = 0
        with gzip.open(src, "rt", encoding="utf-8") as f:
            for line in f.read().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                if "html" not in rec:
                    errors += 1
                    continue
                pages += 1
                pg = parse_ego_page(rec["html"])
                hat = rec["hat_no"]
                ruid = f"{fid}:{hat}"
                k = pg["kunye"]
                routes.append(dict(route_uid=ruid, feed_id=fid, route_id=hat, route_short_name=k.get("Hat No") or hat,
                                   route_long_name=k.get("Hat Adı") or rec.get("etiket"), route_desc=json.dumps(k, ensure_ascii=False),
                                   route_type=mode_by_list.get(rec["liste"], 3), agency_name="EGO Genel Müdürlüğü",
                                   route_color=None, il="Ankara"))
                ds = pg["duraklar"]
                for d in ds:
                    suid = f"{fid}:{d['durak_no']}"
                    if suid not in stops:
                        hit = ref.loc[d["durak_no"]] if d["durak_no"] in ref.index else None
                        stops[suid] = dict(stop_uid=suid, feed_id=fid, stop_id=d["durak_no"], stop_code=d["durak_no"],
                                           stop_name=d["ad"], stop_desc=d.get("adres"),
                                           lat=float(hit["lat"]) if hit is not None else None,
                                           lon=float(hit["lon"]) if hit is not None else None,
                                           coord_repair="osm_ref_eslesme" if hit is not None else "koordinat_yok_osm_ref_bulunamadi",
                                           il="Ankara")
                    rstops.append(dict(route_uid=ruid, direction_id=None, pattern_id=ruid, stop_sequence=d["sira"],
                                       stop_uid=suid, pattern_source="ego_route_table"))
                m = _re.search(r"(\d+)", k.get("Süresi") or "")
                dur = int(m.group(1)) * 60 if m else None
                n = len(ds)
                off = [(dur * i / (n - 1)) if (dur and n > 1) else None for i in range(n)]
                idx = {d["durak_no"]: i for i, d in enumerate(ds)}
                for head, items in pg["kalkis"].items():
                    lab, flags = days.get(head, (head, None))
                    if flags is None:
                        continue
                    for t0, note in items:
                        start = 0
                        if note:
                            mm = _re.search(r"(\d{5})\s*NOLU DURAK", note)
                            if mm and mm.group(1) in idx:
                                start = idx[mm.group(1)]
                        tuid = f"{ruid}:{lab}:{t0}:{start}"
                        trips.append(dict(trip_uid=tuid, feed_id=fid, route_uid=ruid, service_uid=f"{fid}:{lab}", direction_id=None,
                                          headsign=note or (ds[-1]["ad"] if ds else None), shape_uid=None, trip_origin="ego_page",
                                          source_trip_id=None))
                        for i in range(start, n):
                            if i == start:
                                sec, srcl = t0, "ego_given"
                            elif off[i] is not None:
                                sec, srcl = int(round(t0 + off[i] - off[start])), "ego_duration_interpolated"
                            else:
                                sec, srcl = None, "ego_missing"
                            sts.append((tuid, f"{fid}:{ds[i]['durak_no']}", ds[i]["sira"], sec, sec, srcl))
        # OSM'de kodu bulunmayan duraklar: aynı hatta koordinatı bilinen en yakın önceki ve sonraki durak arasında sıraya göre
        # doğrusal konum TAHMİNİ (tahmini_hat_sirasi_arasi); en küçük sıra aralığını veren hat seçilir. Hiçbirinde yoksa boş kalır.
        seqs = {}
        for rs in rstops:
            seqs.setdefault(rs["route_uid"], []).append((rs["stop_sequence"], rs["stop_uid"]))
        best = {}
        for ruid, lst in seqs.items():
            lst.sort()
            known = [(i, stops[u]) for i, (_, u) in enumerate(lst) if stops[u]["lat"] is not None]
            for i, (_, u) in enumerate(lst):
                if stops[u]["lat"] is not None or stops[u]["coord_repair"] == "tahmini_hat_sirasi_arasi":
                    continue
                prev = next(((j, st) for j, st in reversed(known) if j < i), None)
                nxt = next(((j, st) for j, st in known if j > i), None)
                if prev and nxt and nxt[0] - prev[0] <= 6:  # en çok 5 ardışık bilinmeyen durak (~1–2 km); daha uzun aralıkta tahmin yapılmaz
                    gap = nxt[0] - prev[0]
                    w = (i - prev[0]) / gap
                    cand = (gap, prev[1]["lat"] + w * (nxt[1]["lat"] - prev[1]["lat"]), prev[1]["lon"] + w * (nxt[1]["lon"] - prev[1]["lon"]))
                    if u not in best or cand[0] < best[u][0]:
                        best[u] = cand
        for u, (gap, la, lo) in best.items():
            stops[u].update(lat=la, lon=lo, coord_repair="tahmini_hat_sirasi_arasi", stop_desc=f"{stops[u]['stop_desc'] or ''} [konum tahmini: bilinen iki durak arası {gap} sıra]".strip())
        c = self.c
        if not routes:
            return
        c.register("er", pd.DataFrame(routes))
        c.execute("""INSERT INTO transit_route SELECT DISTINCT ON (route_uid) route_uid, feed_id, route_id, route_short_name, route_long_name,
            route_desc, route_type, NULL, agency_name, route_color, il, 'none' FROM er""")
        c.register("es", pd.DataFrame(list(stops.values())))
        c.execute("INSERT INTO transit_stop SELECT stop_uid, feed_id, stop_id, stop_code, stop_name, stop_desc, lat, lon, coord_repair, il FROM es")
        svc = pd.DataFrame([dict(service_uid=f"{fid}:{lab}", feed_id=fid, service_id=lab,
                                 **dict(zip(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"], fl)),
                                 start_date=run_date, service_label=lab) for lab, fl in days.values()])
        c.register("esv", svc)
        c.execute("""INSERT INTO transit_service SELECT service_uid, feed_id, service_id, monday, tuesday, wednesday, thursday, friday,
            saturday, sunday, start_date::DATE, NULL, service_label FROM esv""")
        c.register("ers", pd.DataFrame(rstops))
        c.execute("INSERT INTO transit_route_stop SELECT DISTINCT * FROM ers")
        c.register("et", pd.DataFrame(trips).drop_duplicates("trip_uid"))
        c.execute("INSERT INTO transit_trip SELECT * FROM et")
        c.register("est", pd.DataFrame(sts, columns=["trip_uid", "stop_uid", "seq", "a", "d", "src"]).drop_duplicates(["trip_uid", "seq"]))
        c.execute("INSERT INTO transit_stop_time SELECT * FROM est")
        for v in ("er", "es", "esv", "ers", "et", "est"):
            c.unregister(v)
        self.feeds.append(dict(feed_id=fid, source_kind="resmi_web_sayfasi", il="Ankara", operator="EGO Genel Müdürlüğü",
                               source_url="https://www.ego.gov.tr/hareketsaatleri", raw_path=str(src), raw_sha256=sha256(src),
                               fetched_at=run_date, notes=f"{pages} sayfa, {errors} hata; durak koordinatı OSM ref eşleşmesi ({osm_layer})"))

    # ---------------- OSM (saatsiz) ----------------
    def osm(self):
        if not OSM_SQLITE.exists():
            return
        con = sqlite3.connect(f"file:{OSM_SQLITE}?mode=ro", uri=True)
        hat = pd.read_sql("SELECT id, osm_id, tur, ref, ad, operator, from_ad, to_ad, renk FROM hat", con)
        hd = pd.read_sql("""SELECT hd.hat_id, hd.sira, hd.durak_osm_tip, hd.durak_osm_id, p.ad, p.lat, p.lon
            FROM hat_durak hd JOIN poi p ON p.osm_tip = hd.durak_osm_tip AND p.osm_id = hd.durak_osm_id""", con)
        meta = dict(con.execute("SELECT * FROM meta").fetchall())
        con.close()
        fid = "osm_routes"
        tur_rt = {"bus": 3, "tram": 0, "subway": 1, "light_rail": 0, "train": 2, "ferry": 4, "trolleybus": 11, "funicular": 7,
                  "aerialway": 6, "monorail": 12, "share_taxi": 3, "minibus": 3, "coach": 200}
        hat["route_uid"] = fid + ":" + hat["osm_id"].astype(str)
        hat["route_type"] = hat["tur"].map(tur_rt).fillna(3).astype(int)
        hat["long"] = hat["ad"].where(hat["ad"].notna(), hat["from_ad"].fillna("") + " - " + hat["to_ad"].fillna(""))
        self.c.register("oh", hat)
        self.c.execute("""INSERT INTO transit_route SELECT route_uid, 'osm_routes', CAST(osm_id AS VARCHAR), ref, long, tur, route_type, NULL,
            operator, renk, NULL, 'none' FROM oh""")
        hd = hd.merge(hat[["id", "route_uid"]], left_on="hat_id", right_on="id")
        hd["stop_uid"] = fid + ":" + hd["durak_osm_tip"] + "/" + hd["durak_osm_id"].astype(str)
        self.c.register("ohd", hd)
        self.c.execute("""INSERT INTO transit_stop SELECT DISTINCT ON (stop_uid) stop_uid, 'osm_routes', durak_osm_tip || '/' || durak_osm_id,
            NULL, ad, NULL, lat, lon, 'none', NULL FROM ohd""")
        self.c.execute("""INSERT INTO transit_route_stop SELECT route_uid, NULL, route_uid, sira, stop_uid, 'osm_relation' FROM ohd""")
        self.c.unregister("oh")
        self.c.unregister("ohd")
        self.feeds.append(dict(feed_id=fid, source_kind="osm_relation_no_schedule", il="Türkiye", operator="OpenStreetMap katkıcıları (ODbL)",
                               source_url=meta.get("kaynak"), raw_path=str(OSM_SQLITE), fetched_at=meta.get("islenme")))

    # ---------------- İBB saatlik hat bazlı yolcu (doluluk vekili) ----------------
    def ridership_ibb(self):
        """İBB 'Hourly Public Transport Data Set': tarih × saat × hat × aktarma/bilet türü × ilçe → geçiş ve yolcu sayısı.
        Kayıpsız taşınır (tüm sütunlar); yalnız tür dönüşümü. Boş (hata sayfası) aylar atlanır ve rapora yazılır."""
        files = sorted(p for p in IBB.glob("hourly-public-transport-data-set_*_hourly_transportation_*.csv.gz") if p.stat().st_size > 10_000)
        skipped = sorted(p.name for p in IBB.glob("hourly-public-transport-data-set_*.csv.gz") if p.stat().st_size <= 10_000)
        if not files:
            return
        sel = " UNION ALL ".join(
            f"""SELECT TRY_CAST(transition_date AS DATE) transition_date, TRY_CAST(transition_hour AS INT) transition_hour,
                TRY_CAST(transport_type_id AS INT) transport_type_id, road_type, line AS line_long_name, line_name, transfer_type,
                TRY_CAST(number_of_passage AS BIGINT) number_of_passage, TRY_CAST(number_of_passenger AS BIGINT) number_of_passenger,
                product_kind, transaction_type_desc, town, station_poi_desc_cd, '{f.name}' source_file
              FROM read_csv('{f}', all_varchar=true, header=true)""" for f in files)
        STG.mkdir(parents=True, exist_ok=True)
        self.c.execute(f"COPY ({sel}) TO '{STG / 'transit_ridership_hourly.parquet'}' (FORMAT parquet, COMPRESSION zstd)")
        self.ridership_report = dict(files=[f.name for f in files], skipped_empty=skipped,
                                     rows=self.c.execute(f"SELECT count(*) FROM '{STG / 'transit_ridership_hourly.parquet'}'").fetchone()[0])

    # ---------------- son işlemler ----------------
    def finish(self):
        c = self.c
        # GTFS kaynaklarında hat–durak dizisi: her hat/yön/durak dizisi (desen) için bir temsilci sefer
        c.execute("""INSERT INTO transit_route_stop
            WITH pat AS (SELECT t.route_uid, t.direction_id, t.trip_uid, count(*)::VARCHAR || '-' || sum(hash(st.stop_uid || '#' || st.stop_sequence::VARCHAR))::VARCHAR sig
                         FROM transit_trip t JOIN transit_stop_time st USING (trip_uid)
                         WHERE t.trip_origin IN ('gtfs', 'gtfs_frequency') GROUP BY 1,2,3),
                 rep AS (SELECT route_uid, direction_id, sig, min(trip_uid) trip_uid, count(*) n FROM pat GROUP BY 1,2,3)
            SELECT r.route_uid, r.direction_id, r.route_uid || ':' || coalesce(r.direction_id::VARCHAR,'') || ':' || md5(r.sig), st.stop_sequence,
                   st.stop_uid, 'gtfs_trip_pattern'
            FROM rep r JOIN transit_stop_time st ON st.trip_uid = r.trip_uid""")
        # yön adı kaynakta yoksa seferin son durağının adı (headsign_source ile işaretli)
        c.execute("ALTER TABLE transit_trip ADD COLUMN headsign_source VARCHAR")
        c.execute("UPDATE transit_trip SET headsign_source = 'kaynak' WHERE nullif(trim(headsign), '') IS NOT NULL")
        c.execute("""UPDATE transit_trip t SET headsign = x.son, headsign_source = 'son_durak_adi'
            FROM (SELECT st.trip_uid, arg_max(s.stop_name, st.stop_sequence) son FROM transit_stop_time st
                  JOIN transit_stop s USING (stop_uid) GROUP BY 1) x
            WHERE x.trip_uid = t.trip_uid AND nullif(trim(t.headsign), '') IS NULL""")
        c.execute("UPDATE transit_route SET mode = CASE " + " ".join(f"WHEN route_type={k} THEN '{v}'" for k, v in MODE.items())
                  + " WHEN route_type BETWEEN 700 AND 799 THEN 'otobus' WHEN route_type BETWEEN 100 AND 199 THEN 'tren' ELSE 'diger' END")
        c.execute("UPDATE transit_route SET mode='sehirlerarasi_otobus' WHERE feed_id='flixbus_tr'")
        # İBB raylı/deniz beslemesi standart dışı kod kullanıyor: 9 = Minibüs, 10 = Taksi Dolmuş (agency_name ile doğrulandı)
        c.execute("UPDATE transit_route SET mode='minibus' WHERE feed_id='istanbul_rayli_deniz' AND route_type=9")
        c.execute("UPDATE transit_route SET mode='taksi_dolmus' WHERE feed_id='istanbul_rayli_deniz' AND route_type=10")
        # Antalya AntRay T1A/T1B hafif raylı tramvaydır; KentKart routeType=2 (tren) olarak veriyor
        c.execute("UPDATE transit_route SET mode='tramvay' WHERE feed_id='kentkart_026' AND route_type=2 AND route_short_name LIKE 'T%'")
        c.execute("""UPDATE transit_service SET service_label = coalesce(service_label, CASE
            WHEN monday+tuesday+wednesday+thursday+friday=5 AND saturday+sunday=0 THEN 'hafta_ici'
            WHEN monday+tuesday+wednesday+thursday+friday=0 AND saturday=1 AND sunday=0 THEN 'cumartesi'
            WHEN monday+tuesday+wednesday+thursday+friday=0 AND saturday=0 AND sunday=1 THEN 'pazar'
            WHEN monday+tuesday+wednesday+thursday+friday+saturday+sunday=7 THEN 'her_gun'
            ELSE concat_ws('', CASE WHEN monday=1 THEN 'Pt' END, CASE WHEN tuesday=1 THEN 'Sa' END, CASE WHEN wednesday=1 THEN 'Ça' END,
                 CASE WHEN thursday=1 THEN 'Pe' END, CASE WHEN friday=1 THEN 'Cu' END, CASE WHEN saturday=1 THEN 'Ct' END, CASE WHEN sunday=1 THEN 'Pz' END) END)""")
        fe = pd.DataFrame(self.feeds)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        fe["guncellenme_tarihi"] = now
        c.register("fe", fe)
        c.execute("CREATE TABLE transit_feed AS SELECT * FROM fe")
        c.execute("ALTER TABLE transit_feed ADD COLUMN calendar_end DATE")
        c.execute("ALTER TABLE transit_feed ADD COLUMN is_expired BOOLEAN")
        c.execute("UPDATE transit_feed f SET calendar_end = (SELECT max(end_date) FROM transit_service s WHERE s.feed_id=f.feed_id)")
        c.execute(f"UPDATE transit_feed SET is_expired = calendar_end < DATE '{TODAY}'")
        # Sorgu önceliği: aynı ili aynı türde kapsayan birden çok kaynak varsa varsayılan sorgu yalnız 'birincil'i gösterir.
        #   Gaziantep: KentKart canlı servis (çekim günü güncel) birincil; belediye portalındaki GTFS (Gaziulaş, aynı altyapı) ikincil
        #   OSM hatları: saat içermez → yalnız tarifeli kaynağın olmadığı yerde yedek
        c.execute("ALTER TABLE transit_feed ADD COLUMN query_role VARCHAR")
        c.execute("""UPDATE transit_feed SET query_role = CASE
            WHEN feed_id = 'osm_routes' THEN 'yedek_saatsiz'
            WHEN feed_id = 'gaziantep_gaziulas' AND EXISTS (SELECT 1 FROM transit_feed k WHERE k.feed_id = 'kentkart_028') THEN 'ikincil'
            ELSE 'birincil' END""")
        STG.mkdir(parents=True, exist_ok=True)
        rep = {}
        for tb in ("transit_feed", "transit_route", "transit_stop", "transit_service", "transit_trip", "transit_stop_time",
                   "transit_route_stop", "transit_shape_point"):
            c.execute(f"COPY {tb} TO '{STG / (tb + '.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
            rep[tb] = c.execute(f"SELECT count(*) FROM {tb}").fetchone()[0]
        rep["time_source"] = dict(c.execute("SELECT time_source, count(*) FROM transit_stop_time GROUP BY 1").fetchall())
        rep["coord_repair"] = dict(c.execute("SELECT coord_repair, count(*) FROM transit_stop GROUP BY 1").fetchall())
        rep["name_repair"] = dict(c.execute("SELECT coalesce(name_repair,'none'), count(*) FROM transit_route GROUP BY 1").fetchall())
        rep["feeds"] = c.execute("""SELECT f.feed_id, f.il, f.calendar_end, f.is_expired,
            (SELECT count(*) FROM transit_route r WHERE r.feed_id=f.feed_id) hat,
            (SELECT count(*) FROM transit_stop s WHERE s.feed_id=f.feed_id) durak,
            (SELECT count(*) FROM transit_trip t WHERE t.feed_id=f.feed_id AND t.trip_origin NOT LIKE '%template') sefer
            FROM transit_feed f ORDER BY 1""").fetchall()
        rep["ridership"] = getattr(self, "ridership_report", None)
        rep["built_at"] = now
        (STG / "_build_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return rep


def main():
    t0 = time.time()
    b = Builder()
    for fid, cfg in IBB_FEEDS.items():
        print("İBB:", fid, flush=True)
        b.ibb(fid, cfg)
    for fid, il, op, pat, url in GTFS_ZIPS:
        print("GTFS:", fid, flush=True)
        b.gtfs_zip(fid, il, op, pat, url)
    print("KentKart", flush=True)
    b.kentkart()
    print("EGO", flush=True)
    b.ego()
    print("OSM", flush=True)
    b.osm()
    print("İBB saatlik yolcu", flush=True)
    b.ridership_ibb()
    rep = b.finish()
    b.c.close()
    shutil.rmtree(WORK, ignore_errors=True)
    rep["seconds"] = round(time.time() - t0)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
