"""Market Fiyatı (TÜBİTAK / Ticaret Bakanlığı, marketfiyati.org.tr) toplayıcısı.

Kaynak: https://api.marketfiyati.org.tr (sitenin kendi arka uç servisi, herkese açık).
Zincirler: BİM, A101, ŞOK, Migros, CarrefourSA, Tarım Kredi, Hakmar.

Aşamalar (her biri kaldığı yerden devam eder):
  depots   — 50 bin mahalle merkezinden /v2/nearest → tüm şubeler (id, ad, zincir, koordinat)
  prices   — her il için (zincir başına il merkezine en yakın şube) /v3/searchByCategories
             sayfa sayfa → ürün × şube fiyatı (kalem kalem, ortalama değil)
  history  — her benzersiz ürün için /v3/price-history (son ~90 gün, günlük, zincir bazında)
  track    — izleme modu: yalnız prices aşamasını yeni tarihli klasöre yazar (cron/launchd için)

Kullanım: python3 intake_marketfiyati.py depots|prices|history|all [--threads N]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import UA, Intake, fill_admin_nearest, now_iso  # noqa: E402

API = "https://api.marketfiyati.org.tr/api"
# öncelik: Batı büyükşehirleri (AGENTS.md §10), sonra Ankara ve büyük iller
PRIORITY_ILS = ["GEO_IL_34", "GEO_IL_35", "GEO_IL_16", "GEO_IL_07", "GEO_IL_41", "GEO_IL_48", "GEO_IL_59", "GEO_IL_10",
                "GEO_IL_09", "GEO_IL_06", "GEO_IL_01", "GEO_IL_42", "GEO_IL_27", "GEO_IL_33", "GEO_IL_21", "GEO_IL_38",
                "GEO_IL_26", "GEO_IL_55", "GEO_IL_20", "GEO_IL_45", "GEO_IL_54", "GEO_IL_61", "GEO_IL_31", "GEO_IL_63"]
GEO = Path.home() / "Desktop/GEOPROP_CONSOLIDATION/tmp/geo_centroids.parquet"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
           "Origin": "https://marketfiyati.org.tr", "Referer": "https://marketfiyati.org.tr/"}


class RateLimiter:
    def __init__(self, per_sec: float):
        self.dt = 1.0 / per_sec
        self.lock = threading.Lock()
        self.next = time.time()

    def wait(self):
        with self.lock:
            t = time.time()
            if t < self.next:
                time.sleep(self.next - t)
            self.next = max(t, self.next) + self.dt


_local = threading.local()


class ChunkWriter:
    """Her N kayıtta bir tamamlanmış .jsonl.gz parçası yazar (kesintide en çok N kayıt kaybolur)."""

    def __init__(self, folder: Path, prefix: str, every: int = 300):
        self.folder, self.prefix, self.every = folder, prefix, every
        folder.mkdir(parents=True, exist_ok=True)
        self.buf = []
        self.lock = threading.Lock()

    def add(self, rec):
        with self.lock:
            self.buf.append(rec)
            if len(self.buf) >= self.every:
                self._flush()

    def _flush(self):
        if not self.buf:
            return
        name = self.folder / f"{self.prefix}_{time.strftime('%Y%m%dT%H%M%S')}_{time.time_ns() % 10**9:09d}.jsonl.gz"
        with gzip.open(name, "wt") as f:
            for r in self.buf:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.buf = []

    def close(self):
        with self.lock:
            self._flush()


def read_chunks(folder: Path, prefix: str):
    for pf in sorted(folder.glob(f"{prefix}_*.jsonl.gz")):
        try:
            with gzip.open(pf, "rt") as f:
                for l in f:
                    yield json.loads(l)
        except Exception:  # noqa: BLE001 — bozuk parça atlanır
            continue


def sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(HEADERS)
        _local.s.headers["User-Agent"] = UA  # tek sabit tarayıcı kimliği; engellenirse DURULUR (kimlik değiştirilmez)
    return _local.s


CONSEC_FAIL = {"n": 0}
BLOCK_LIMIT = 25  # art arda bu kadar başarısız istek → sunucu engeli kabul edilir, süreç durur


class Blocked(RuntimeError):
    pass


def post(rl, path, body, tries=3):
    if CONSEC_FAIL["n"] >= BLOCK_LIMIT:
        raise Blocked("sunucu engeli (art arda başarısız istekler) — toplama durduruldu")
    err = None
    for i in range(tries):
        rl.wait()
        try:
            r = sess().post(API + path, json=body, timeout=60)
            if r.status_code == 200:
                CONSEC_FAIL["n"] = 0
                return r.json()
            err = f"HTTP {r.status_code} {r.text[:200]}"
            if r.status_code in (400, 404):
                break
        except Exception as e:  # noqa: BLE001
            err = repr(e)
            _local.__dict__.pop("s", None)  # kopan bağlantı → yeni oturum
        time.sleep(min(60, 2 * 2 ** i))
    CONSEC_FAIL["n"] += 1
    raise RuntimeError(f"{path} {json.dumps(body, ensure_ascii=False)[:200]}: {err}")


# ------------------------------------------------------------------ depots
def _hav(lat1, lon1, lat2, lon2):
    r = math.pi / 180
    dlat, dlon = (lat2 - lat1) * r, (lon2 - lon1) * r
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin(dlon / 2) ** 2
    return 12742000 * math.asin(math.sqrt(x))  # metre


def _cover_radius(res, distance_km):
    """Bir sorgunun 'tam bilinen' yarıçapı: her zincir için 5. en yakın şubenin uzaklığı
    (5'ten az şube döndüyse sorgu yarıçapı); zincirler arası en küçüğü."""
    by = {}
    for x in res:
        by.setdefault(x["marketName"], []).append(x.get("distance") or 0)
    rs = []
    for m in ("bim", "a101", "sok", "migros", "carrefour", "tarim_kredi", "hakmar"):
        d = sorted(by.get(m, []))
        rs.append(d[4] if len(d) >= 5 else distance_km * 1000)
    return min(rs)


def phase_depots(it: Intake, threads: int, rps: float, levels=("mahalle", "ilce", "il")):
    """Uyarlamalı kapsama: mahalle merkezleri sırayla sorgulanır; önceki bir sorgunun 'tam bilinen'
    dairesinin yarısı içinde kalan nokta atlanır (şehirde sık, kırsalda seyrek sorgu)."""
    geo = pd.read_parquet(GEO)
    pts = geo[geo.level.isin(list(levels))][["geo_id", "level", "il_geo_id", "centroid_lat", "centroid_lon"]]
    pts = pts.assign(cell=(pts.centroid_lat / 0.01).round().astype(int).astype(str) + "_" +
                     (pts.centroid_lon / 0.01).round().astype(int).astype(str))
    pri = {g: i for i, g in enumerate(PRIORITY_ILS)}
    pts = pts.assign(_p=pts.il_geo_id.map(pri).fillna(99), _l=pts.level.map({"il": 0, "ilce": 1, "mahalle": 2}))
    pts = pts.drop_duplicates("cell").sort_values(["_l", "_p", "il_geo_id", "centroid_lat", "centroid_lon"])
    raw_dir = it.dir / "depots_raw"
    grid = {}  # 0.1° hücre → [(lat, lon, skip_radius_m)]
    lock = threading.Lock()

    def add_cover(lat, lon, rad):
        gk = (int(lat * 10), int(lon * 10))
        with lock:
            grid.setdefault(gk, []).append((lat, lon, rad))

    def covered(lat, lon):
        gi, gj = int(lat * 10), int(lon * 10)
        with lock:
            for di in (-2, -1, 0, 1, 2):
                for dj in (-3, -2, -1, 0, 1, 2, 3):
                    for (a, b, rad) in grid.get((gi + di, gj + dj), ()):
                        if _hav(lat, lon, a, b) < rad:
                            return True
        return False

    done = set()
    for d in read_chunks(raw_dir, "part"):
        done.add(d["cell"])
        add_cover(d["q"]["latitude"], d["q"]["longitude"], 0.5 * _cover_radius(d["res"], d["q"]["distance"]))
    it.log(f"depots: {len(pts)} aday nokta, {len(done)} sorgu önceden yapılmış")
    rl = RateLimiter(rps)
    cw = ChunkWriter(raw_dir, "part", every=200)
    stats = {"q": 0, "skip": 0, "err": 0}

    def work(row):
        if row.cell in done or covered(row.centroid_lat, row.centroid_lon):
            stats["skip"] += 1
            return
        body = {"latitude": round(row.centroid_lat, 6), "longitude": round(row.centroid_lon, 6), "distance": 25}
        res = post(rl, "/v2/nearest", body)
        add_cover(body["latitude"], body["longitude"], 0.5 * _cover_radius(res, 25))
        cw.add({"cell": row.cell, "geo_id": row.geo_id, "q": body, "at": now_iso(), "res": res})
        stats["q"] += 1
        if stats["q"] % 500 == 0:
            it.log(f"depots: {stats}")

    # küçük havuz + sıralı gönderim → komşu noktalar kapsama bilgisini paylaşır
    rows = list(pts.itertuples())
    with ThreadPoolExecutor(threads) as ex:
        for i in range(0, len(rows), threads * 4):
            for f in [ex.submit(work, r) for r in rows[i:i + threads * 4]]:
                try:
                    f.result()
                except Blocked:
                    raise
                except Exception as e:  # noqa: BLE001
                    stats["err"] += 1
                    it.log(f"depots hata: {e}")
    cw.close()
    it.log(f"depots bitti: {stats}")
    build_depots(it)


def build_depots(it: Intake):
    rows = {}
    for d in read_chunks(it.dir / "depots_raw", "part"):
        if True:
            for x in d["res"]:
                rows[x["id"]] = {"depot_id": x["id"], "market": x["marketName"], "depot_name": x["sellerName"],
                                 "lat": x["location"]["lat"], "lon": x["location"]["lon"], "first_seen_at": d["at"]}
    df = pd.DataFrame(rows.values())
    df = fill_admin_nearest(add_admin(df))
    df["guncellenme_tarihi"] = now_iso()
    it.save_parquet("depots", df, source_url=API + "/v2/nearest", method="api_post_grid",
                    note="mahalle merkezleri 0.01° ızgara; her sorguda zincir başına en yakın 5 şube")
    it.log(f"depots: {len(df)} benzersiz şube; zincir dağılımı {df.market.value_counts().to_dict()}")
    return df


def add_admin(df: pd.DataFrame) -> pd.DataFrame:
    """Şube koordinatını kanonik mahalle poligonlarıyla il/ilçe/mahalleye bağla (nokta-içinde-poligon)."""
    import duckdb
    canon = str(Path.home() / "Desktop/GEOPROP_CONSOLIDATION/canonical/v1.1/geoprop_canonical_v1_1.duckdb")
    c = duckdb.connect()
    c.sql("load spatial")
    c.sql(f"attach '{canon}' as k (read_only)")
    c.register("pts", df)
    out = c.sql("""
        with m as (select geo_id, name, parent_geo_id, il_geo_id, geometry from k.geo_entity where level='mahalle' and geometry is not null)
        select p.*, m.geo_id as mahalle_geo_id, m.name as mahalle_adi,
               i.name as ilce_adi, m.parent_geo_id as ilce_geo_id, l.name as il_adi, m.il_geo_id as il_geo_id
        from pts p
        left join m on st_contains(m.geometry, st_point(p.lon, p.lat))
        left join k.geo_entity i on i.geo_id=m.parent_geo_id
        left join k.geo_entity l on l.geo_id=m.il_geo_id
    """).df()
    return out.drop_duplicates(subset=[df.columns[0]])


# ------------------------------------------------------------------ prices
def pick_il_depots(depots: pd.DataFrame) -> dict:
    """Her il için: il içinde en çok şubenin toplandığı 0.02° hücrenin merkezine her zincirin en yakın şubesi."""
    out = {}
    d = depots.dropna(subset=["il_geo_id"]).copy()
    d["cell"] = (d.lat / 0.02).round().astype(str) + "_" + (d.lon / 0.02).round().astype(str)
    for il, g in d.groupby("il_geo_id"):
        top = g.cell.value_counts().index[0]
        c = g[g.cell == top]
        clat, clon = c.lat.mean(), c.lon.mean()
        g = g.assign(dist=((g.lat - clat) ** 2 + ((g.lon - clon) * math.cos(math.radians(clat))) ** 2) ** 0.5)
        pick = g.sort_values("dist").groupby("market").head(1)
        out[il] = {"il_adi": g.il_adi.iloc[0], "center": [clat, clon], "depots": pick.depot_id.tolist()}
    return out


def flatten(page_content, il_geo_id, il_adi, captured_at):
    rows = []
    for p in page_content:
        base = {k: p.get(k) for k in ("id", "title", "brand", "imageUrl", "refinedVolumeOrWeight",
                                      "refinedQuantityUnit", "main_category", "menu_category")}
        base["categories"] = json.dumps(p.get("categories"), ensure_ascii=False)
        for d in p.get("productDepotInfoList") or []:
            r = dict(base)
            r.update({"depotId": d.get("depotId"), "depotName": d.get("depotName"), "marketAdi": d.get("marketAdi"),
                      "price": d.get("price"), "unitPrice": d.get("unitPrice"), "unitPriceValue": d.get("unitPriceValue"),
                      "discount": d.get("discount"), "discountRatio": d.get("discountRatio"),
                      "promotionText": d.get("promotionText"), "percentage": d.get("percentage"),
                      "lat": d.get("latitude"), "lon": d.get("longitude"), "indexTime": d.get("indexTime"),
                      "il_geo_id": il_geo_id, "il_adi": il_adi, "captured_at": captured_at})
            rows.append(r)
    return rows


def phase_prices(it: Intake, threads: int, rps: float, depots: pd.DataFrame | None = None, only=None):
    if depots is None:
        depots = pd.read_parquet(it.dir / "depots.parquet") if (it.dir / "depots.parquet").exists() \
            else pd.read_parquet(sorted((it.dir.parent).glob("*/depots.parquet"))[-1])
    sets = pick_il_depots(depots)
    (it.dir / "il_depot_sets.json").write_text(json.dumps(sets, ensure_ascii=False, indent=1))
    rl = RateLimiter(rps)
    outdir = it.dir / "prices"
    outdir.mkdir(exist_ok=True)

    def do_il(il):
        target = outdir / f"prices_{il}.parquet"
        if target.exists():
            return il, "skip", 0
        s = sets[il]
        body = {"keywords": "", "pages": 0, "size": 25, "depots": s["depots"], "menuCategory": True}
        first = post(rl, "/v3/searchByCategories", body)
        total = first.get("numberOfFound", 0)
        pages = math.ceil(total / 25)
        cap = now_iso()
        rows = flatten(first["content"], il, s["il_adi"], cap)
        ids = {p["id"] for p in first["content"]}
        for pg in range(1, pages):
            j = post(rl, "/v3/searchByCategories", {**body, "pages": pg})
            rows += flatten(j["content"], il, s["il_adi"], cap)
            ids |= {p["id"] for p in j["content"]}
        df = pd.DataFrame(rows)
        df["guncellenme_tarihi"] = cap
        df.to_parquet(target, compression="zstd", index=False)
        it._record(target, source_url=API + "/v3/searchByCategories", method="api_post_paginated", rows=len(df),
                   extra={"il_geo_id": il, "numberOfFound": total, "unique_products": len(ids), "depots": s["depots"]})
        return il, f"{len(ids)}/{total} ürün", len(df)

    pri = {g: i for i, g in enumerate(PRIORITY_ILS)}
    ils = sorted([il for il in sets if (only is None or il in only)], key=lambda g: (pri.get(g, 99), g))
    it.log(f"prices: {len(ils)} il")
    with ThreadPoolExecutor(threads) as ex:
        for f in as_completed([ex.submit(do_il, il) for il in ils]):
            try:
                il, msg, n = f.result()
                it.log(f"prices {il}: {msg}, {n} satır")
            except Blocked as e:
                it.log(f"prices DURDU: {e}")
                raise
            except Exception as e:  # noqa: BLE001
                it.log(f"prices hata: {e}")


# ------------------------------------------------------------------ history
def phase_history(it: Intake, threads: int, rps: float, sets_for=("GEO_IL_34", "GEO_IL_06", "GEO_IL_35")):
    sets = json.loads((it.dir / "il_depot_sets.json").read_text())
    prices = pd.concat([pd.read_parquet(p, columns=["id", "depotId", "il_geo_id"])
                        for p in (it.dir / "prices").glob("prices_*.parquet")])
    outdir = it.dir / "history"
    outdir.mkdir(exist_ok=True)
    rl = RateLimiter(rps)
    for il in sets_for:
        if il not in sets:
            continue
        # ürünü o ilde taşıyan şubeler (zincir başına tek şube → seri şube/zincir eşleşmesi kesin)
        sub = prices[prices.il_geo_id == il].groupby("id").depotId.apply(lambda s: sorted(set(s))).to_dict()
        hdir = outdir / f"raw_{il}"
        done = {d["uniqueId"] for d in read_chunks(hdir, "part")}
        todo = [(pid, deps) for pid, deps in sub.items() if pid not in done]
        it.log(f"history {il}: {len(sub)} ürün, {len(todo)} kaldı")
        cw = ChunkWriter(hdir, "part", every=200)

        def work(a):
            pid, deps = a
            res = post(rl, "/v3/price-history", {"uniqueId": pid, "depots": deps})
            cw.add({"uniqueId": pid, "depots": deps, "il_geo_id": il, "at": now_iso(), "res": res})
            return 1

        n = 0
        with ThreadPoolExecutor(threads) as ex:
            for f in as_completed([ex.submit(work, a) for a in todo]):
                try:
                    n += f.result()
                except Blocked as e:
                    it.log(f"history DURDU: {e}")
                    break
                except Exception as e:  # noqa: BLE001
                    it.log(f"history hata: {e}")
                if n and n % 1000 == 0:
                    it.log(f"history {il}: {n}/{len(todo)}")
        cw.close()
        # düz tablo
        recs = []
        for d in read_chunks(hdir, "part"):
            if True:
                for s in d["res"] or []:
                    for pt in s.get("series") or []:
                        recs.append({"id": d["uniqueId"], "il_geo_id": il, "market": s.get("name"),
                                     "date": pt.get("name"), "price": pt.get("value"), "captured_at": d["at"]})
        df = pd.DataFrame(recs)
        it.save_parquet(f"history/price_history_{il}", df, source_url=API + "/v3/price-history",
                        method="api_post", note="son ~90 gün günlük; zincir başına o ildeki seçili şube(ler)")
        it.log(f"history {il}: {len(df)} günlük gözlem")


# ------------------------------------------------------------------ branches
DEPOT_CONST = ("depotId", "depotName", "marketAdi", "latitude", "longitude")  # şubeye sabit alanlar (şube kaydında)


def branch_order(depots: pd.DataFrame) -> list:
    """1. tur: her ilçe×zincir için bir temsilci şube (ilçedeki tüm şubelerin ağırlık merkezine en yakın);
    2. tur: kalan tüm şubeler. Her turda öncelik Batı büyükşehirleri (AGENTS.md §10)."""
    d = depots.dropna(subset=["lat", "lon"]).copy()
    d["ilce_key"] = d.ilce_geo_id.fillna("?" + d.il_geo_id.fillna("?"))
    cen = d.groupby("ilce_key")[["lat", "lon"]].transform("mean")
    d["dist"] = ((d.lat - cen.lat) ** 2 + ((d.lon - cen.lon) * d.lat.map(lambda x: math.cos(math.radians(x)))) ** 2) ** 0.5
    rep = d.sort_values("dist").groupby(["ilce_key", "market"]).head(1)
    d["tur"] = 2
    d.loc[rep.index, "tur"] = 1
    pri = {g: i for i, g in enumerate(PRIORITY_ILS)}
    d["pri"] = d.il_geo_id.map(lambda g: pri.get(g, 99))
    d = d.sort_values(["tur", "pri", "il_geo_id", "ilce_key", "market", "depot_id"])
    return d.to_dict("records")


def phase_branches(it: Intake, rps: float, rounds=(1, 2), shard=None, max_minutes=0, depots_file=None, skip_file=None,
                   limit=0):
    """Her şubenin TAM ürün kataloğu + fiyatı (tek şubeli sorgu; çok şubeli sorguda servis ürün başına
    yalnız bir şube döndürdüğü için şube farkı ancak böyle görülür). Sayfa boyu servis tarafında 25 ile sınırlı."""
    depots = pd.read_parquet(depots_file or it.dir / "depots.parquet")
    order = [r for r in branch_order(depots) if r["tur"] in rounds]
    if shard:  # GitHub Actions matrisi: "i/N" → sıralı listeden her N'de bir (her makineye öncelik ve zincir karışımı)
        i, n = (int(x) for x in shard.split("/"))
        order = order[i - 1::n]
    bdir = it.dir / "branches"
    done = {d["depot_id"] for d in read_chunks(bdir, "depot")}
    if skip_file and Path(skip_file).exists():  # önceki koşularda toplanmış şubeler
        done |= {x.strip() for x in Path(skip_file).read_text().split("\n") if x.strip()}
    seen = {p["id"] for p in read_chunks(bdir, "product")}
    todo = [r for r in order if r["depot_id"] not in done]
    if limit:
        todo = todo[:limit]
    it.log(f"branches{' ' + shard if shard else ''}: {len(order)} şube (tur {rounds}), {len(done)} tamam, {len(todo)} kaldı; bilinen ürün {len(seen)}")
    cw_d = ChunkWriter(bdir, "depot", every=10)
    cw_p = ChunkWriter(bdir, "product", every=500)
    rl = RateLimiter(rps)
    n_req = 0
    t0 = time.time()
    try:
        for k, r in enumerate(todo, 1):
            if max_minutes and time.time() - t0 > max_minutes * 60:
                it.log(f"branches: süre sınırı ({max_minutes} dk) — {k - 1}/{len(todo)} şubede duruldu, kalan sonraki koşuda")
                break
            dep = r["depot_id"]
            body = {"keywords": "", "pages": 0, "size": 25, "depots": [dep], "menuCategory": True}
            try:
                first = post(rl, "/v3/searchByCategories", body)
                total = first.get("numberOfFound", 0)
                pages = [first] + [post(rl, "/v3/searchByCategories", {**body, "pages": pg})
                                   for pg in range(1, math.ceil(total / 25))]
            except Blocked:
                raise
            except Exception as e:  # noqa: BLE001 — şube kaydedilmez, sonraki çalıştırmada yeniden denenir
                it.log(f"branches hata {dep}: {e}")
                continue
            n_req += len(pages)
            items, info = {}, {}
            for j in pages:
                for p in j.get("content") or []:
                    for di in p.get("productDepotInfoList") or []:
                        if di.get("depotId") != dep:
                            continue
                        info = {c: di.get(c) for c in DEPOT_CONST} or info
                        items[p["id"]] = {"id": p["id"], **{c: v for c, v in di.items() if c not in DEPOT_CONST}}
                    if p["id"] not in seen:
                        seen.add(p["id"])
                        cw_p.add({**{c: v for c, v in p.items() if c != "productDepotInfoList"}, "first_seen_at": now_iso(),
                                  "first_seen_depot": dep})
            cw_d.add({"depot_id": dep, "market": r["market"], "tur": r["tur"], "il_geo_id": r["il_geo_id"],
                      "ilce_geo_id": r["ilce_geo_id"], "mahalle_geo_id": r["mahalle_geo_id"], "lat": r["lat"], "lon": r["lon"],
                      "numberOfFound": total, "n_items": len(items), "pages": len(pages), "depot_info": info,
                      "at": now_iso(), "items": list(items.values())})
            if k % 50 == 0:
                it.log(f"branches: {k}/{len(todo)} şube, {n_req} istek, bilinen ürün {len(seen)}")
    except Blocked as e:
        it.log(f"branches DURDU: {e}")
    finally:
        cw_d.close()
        cw_p.close()


def build_branches(it: Intake):
    """Parçalardan düz tablolar: branch_products (şube×ürün fiyat), branch_summary (şube başına), products (ürün kataloğu)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    bdir = it.dir / "branches"
    dep = pd.read_parquet(it.dir / "depots.parquet").set_index("depot_id")
    out = bdir / "branch_products.parquet"
    w, n, summ = None, 0, []
    ITEM_COLS = {"price": pa.float64(), "unitPrice": pa.string(), "unitPriceValue": pa.float64(), "percentage": pa.float64(),
                 "indexTime": pa.string(), "discount": pa.bool_(), "discountRatio": pa.float64(), "promotionText": pa.string()}
    schema = pa.schema([(c, pa.float64() if c in ("lat", "lon") else pa.string()) for c in
                        ("depot_id", "market", "depot_name", "lat", "lon", "il_geo_id", "il_adi", "ilce_geo_id", "ilce_adi",
                         "mahalle_geo_id", "mahalle_adi", "guncellenme_tarihi", "product_id")]
                       + list(ITEM_COLS.items()) + [("extra_json", pa.string())])
    got = set()
    for d in read_chunks(bdir, "depot"):
        if d["depot_id"] in got:  # aynı şube birden çok koşuda toplandıysa ilk kayıt
            continue
        got.add(d["depot_id"])
        meta = dep.loc[d["depot_id"]] if d["depot_id"] in dep.index else {}
        common = {"depot_id": d["depot_id"], "market": d["market"], "depot_name": (d["depot_info"] or {}).get("depotName"),
                  "lat": d["lat"], "lon": d["lon"], "il_geo_id": d["il_geo_id"], "il_adi": meta.get("il_adi"),
                  "ilce_geo_id": d["ilce_geo_id"], "ilce_adi": meta.get("ilce_adi"), "mahalle_geo_id": d["mahalle_geo_id"],
                  "mahalle_adi": meta.get("mahalle_adi"), "guncellenme_tarihi": d["at"]}
        summ.append({**common, "tur": d["tur"], "numberOfFound": d["numberOfFound"], "n_items": d["n_items"], "pages": d["pages"]})
        if not d["items"]:
            continue
        rows = []
        for x in d["items"]:
            x = dict(x)
            r = {**common, "product_id": x.pop("id")}
            for c in ITEM_COLS:
                r[c] = x.pop(c, None)
            r["extra_json"] = json.dumps(x, ensure_ascii=False) if x else None  # beklenmeyen alan kaybolmasın
            rows.append(r)
        t = pa.Table.from_pylist(rows, schema=schema)
        if w is None:
            w = pq.ParquetWriter(out, schema, compression="zstd")
        w.write_table(t)
        n += len(rows)
    if w is not None:
        w.close()
        it._record(out, source_url=API + "/v3/searchByCategories", method="api_post_paginated_single_depot", rows=n,
                   note="şube × ürün: her şubenin tam kataloğu ve fiyatı")
    it.save_parquet("branches/branch_summary", pd.DataFrame(summ), source_url=API + "/v3/searchByCategories",
                    method="api_post_paginated_single_depot", note="şube başına ürün sayısı (numberOfFound vs toplanan)")
    prods = pd.DataFrame(list(read_chunks(bdir, "product"))).sort_values("first_seen_at").drop_duplicates("id")
    for c in prods.columns:
        if prods[c].map(lambda v: isinstance(v, (list, dict))).any():
            prods[c] = prods[c].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    prods["guncellenme_tarihi"] = prods["first_seen_at"]
    it.save_parquet("branches/products", prods, source_url=API + "/v3/searchByCategories",
                    method="api_post_paginated_single_depot", note="şube taramasında görülen tüm ürünler")
    it.log(f"branches build: {n} şube×ürün satırı, {len(summ)} şube, {len(prods)} ürün")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["depots", "build_depots", "prices", "history", "all", "track",
                                      "branches", "build_branches"])
    ap.add_argument("--rounds", default="1,2", help="branches: 1=ilçe×zincir temsilcisi, 2=kalan tüm şubeler")
    ap.add_argument("--shard", help="branches: i/N (GitHub Actions matrisi)")
    ap.add_argument("--max-minutes", type=float, default=0)
    ap.add_argument("--depots-file")
    ap.add_argument("--skip-file")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threads", type=int, default=5)
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--date")
    ap.add_argument("--depot-levels", default="ilce,il")
    ap.add_argument("--history-ils", default="GEO_IL_34,GEO_IL_06,GEO_IL_35")
    a = ap.parse_args()
    it = Intake("marketfiyati", a.date)
    if a.phase in ("depots", "all"):
        phase_depots(it, a.threads, a.rps, tuple(a.depot_levels.split(",")))
    if a.phase == "build_depots":
        build_depots(it)
    if a.phase in ("prices", "all", "track"):
        phase_prices(it, a.threads, a.rps)
    if a.phase in ("history", "all"):
        phase_history(it, a.threads, a.rps, tuple(a.history_ils.split(",")))
    if a.phase == "branches":
        phase_branches(it, a.rps, tuple(int(x) for x in a.rounds.split(",")), a.shard, a.max_minutes, a.depots_file,
                       a.skip_file, a.limit)
    if a.phase == "build_branches":
        build_branches(it)


if __name__ == "__main__":
    main()
