"""Ortak veri toplama (intake) yardımcıları — 2026-09-24 gece toplama turu.

Her toplayıcı ham yanıtı olduğu gibi (sıkıştırılmış) saklar ve her dosya için
manifest.jsonl'ye bir satır yazar: kaynak URL, yöntem, çekim zamanı (ISO 8601),
satır sayısı, bayt, sha256. Ham kök kuralı: GEOPROP_RAW_INTAKE altına yalnız
yeni tarihli klasör açılır; mevcut dosyalar değiştirilmez.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import os
import random
import time
from pathlib import Path

import requests

RAW_ROOT = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
MIN_FREE_BYTES = 1536 * 1024 * 1024  # disk bu eşiğin altına inerse yazma durur (sistem sağlığı)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def free_bytes(path: Path = RAW_ROOT) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


class DiskFull(RuntimeError):
    pass


class Intake:
    def __init__(self, domain: str, run_date: str | None = None, rate: float = 1.0):
        self.domain = domain
        # INTAKE_RUN_DATE: gece yarısını aşan/yeniden başlatılan turlar aynı tarihli klasöre devam etsin
        self.date = run_date or os.environ.get("INTAKE_RUN_DATE") or dt.date.today().isoformat()
        self.dir = RAW_ROOT / domain / self.date
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.dir / "manifest.jsonl"
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
        self.rate = rate  # istekler arası asgari saniye
        self._last = 0.0

    # ---- HTTP ----
    def get(self, url, *, method="GET", tries=4, timeout=60, **kw) -> requests.Response:
        err = None
        for i in range(tries):
            wait = self.rate - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait + random.random() * 0.2)
            self._last = time.time()
            try:
                r = self.s.request(method, url, timeout=timeout, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    err = f"HTTP {r.status_code}"
                    time.sleep(min(60, 3 * 2 ** i))
                    continue
                return r
            except requests.RequestException as e:  # ağ hatası → yeniden dene
                err = repr(e)
                time.sleep(min(60, 3 * 2 ** i))
        raise RuntimeError(f"{url}: {err}")

    # ---- yazma ----
    def _check_disk(self):
        if free_bytes() < MIN_FREE_BYTES:
            raise DiskFull(f"boş alan {free_bytes() / 1e6:.0f} MB < eşik")

    def _record(self, path: Path, *, source_url, method, rows=None, note=None, extra=None):
        data = path.read_bytes()
        rec = {
            "file": str(path.relative_to(RAW_ROOT)),
            "source_url": source_url,
            "method": method,
            "fetched_at": now_iso(),
            "rows": rows,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if note:
            rec["note"] = note
        if extra:
            rec.update(extra)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def save_bytes(self, name: str, content: bytes, *, source_url, method="http_get",
                   rows=None, note=None, compress=True, extra=None):
        self._check_disk()
        path = self.dir / (name + (".gz" if compress and not name.endswith(".gz") else ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        if compress and not name.endswith(".gz"):
            with gzip.open(path, "wb", compresslevel=9) as f:
                f.write(content)
        else:
            path.write_bytes(content)
        return self._record(path, source_url=source_url, method=method, rows=rows,
                            note=note, extra=extra)

    def save_json(self, name: str, obj, **kw):
        rows = kw.pop("rows", len(obj) if isinstance(obj, list) else None)
        return self.save_bytes(name if name.endswith(".json") else name + ".json",
                               json.dumps(obj, ensure_ascii=False).encode(), rows=rows, **kw)

    def save_jsonl(self, name: str, records, **kw):
        buf = io.BytesIO()
        n = 0
        for r in records:
            buf.write(json.dumps(r, ensure_ascii=False).encode() + b"\n")
            n += 1
        return self.save_bytes(name if name.endswith(".jsonl") else name + ".jsonl",
                               buf.getvalue(), rows=n, **kw)

    def save_parquet(self, name: str, df, *, source_url, method="derived", note=None, extra=None):
        self._check_disk()
        path = self.dir / (name if name.endswith(".parquet") else name + ".parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, compression="zstd", index=False)
        return self._record(path, source_url=source_url, method=method, rows=len(df),
                            note=note, extra=extra)

    def log(self, msg: str):
        line = f"[{now_iso()}] {self.domain}: {msg}"
        print(line, flush=True)
        with (self.dir / "run.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def repair_gz_jsonl(path: Path) -> int:
    """Süreç öldürülünce yarım kalan .jsonl.gz'yi onarır: okunabilen tam JSON satırlarını temiz tek
    gzip üyesi olarak yeniden yazar (yarım üyeye ekleme yapılırsa sonrası okunamaz)."""
    import zlib
    path = Path(path)
    if not path.exists():
        return 0
    raw, out, pos = path.read_bytes(), b"", 0
    while pos < len(raw):
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            out += d.decompress(raw[pos:])
        except zlib.error:
            break
        if not d.eof or not d.unused_data:
            break
        pos = len(raw) - len(d.unused_data)
    good = []
    for line in out.split(b"\n"):
        if line.strip():
            try:
                json.loads(line)
                good.append(line)
            except ValueError:
                pass
    tmp = path.with_suffix(path.suffix + ".repair")
    with gzip.open(tmp, "wb") as f:
        f.write(b"\n".join(good) + (b"\n" if good else b""))
    os.replace(tmp, path)
    return len(good)


def fill_admin_nearest(df, lat="lat", lon="lon"):
    """Poligon içinde kalmayan (kıyı/sınır/hatalı poligon) noktaları en yakın mahalle merkezine bağlar.
    admin_match sütunu: 'contains' | 'nearest_centroid' (+ admin_nearest_km). Kural 5: boş bırakma, işaretle."""
    import numpy as np
    import pandas as pd
    from scipy.spatial import cKDTree
    geo = pd.read_parquet(Path.home() / "Desktop/GEOPROP_CONSOLIDATION/tmp/geo_centroids.parquet")
    m = geo[geo.level == "mahalle"].dropna(subset=["centroid_lat", "centroid_lon"]).reset_index(drop=True)
    df = df.copy()
    df["admin_match"] = np.where(df["il_geo_id"].notna(), "contains", None)
    miss = df["il_geo_id"].isna() & df[lat].notna() & df[lon].notna()
    if miss.any():
        k = np.cos(np.radians(39.0))
        tree = cKDTree(np.c_[m.centroid_lat.values, m.centroid_lon.values * k])
        d, idx = tree.query(np.c_[df.loc[miss, lat].values, df.loc[miss, lon].values * k])
        hit = m.iloc[idx]
        df.loc[miss, "mahalle_geo_id"] = hit.geo_id.values
        df.loc[miss, "mahalle_adi"] = hit.name.values
        df.loc[miss, "ilce_geo_id"] = hit.parent_geo_id.values
        df.loc[miss, "il_geo_id"] = hit.il_geo_id.values
        if "ilce_adi" in df:
            df.loc[miss, "ilce_adi"] = hit.ilce_adi.values
        if "il_adi" in df:
            df.loc[miss, "il_adi"] = hit.il_adi.values
        df.loc[miss, "admin_match"] = "nearest_centroid"
        df.loc[miss, "admin_nearest_km"] = d * 111.0
    return df
