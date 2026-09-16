"""Uydu değişim tespiti — Sentinel-2 L2A (Copernicus Data Space, S3 pencere okuma), canlı + önbellek.

Parsel çevresi 1 km × 1 km pencerede iki tarih (şimdi ≈ son 75 gün, 1 yıl önce ±60 gün; isteğe bağlı 3 yıl önce) için:
NDVI (B08,B04) ve NDBI (B11,B08) → bitki örtüsü payı (NDVI>0.4), yapılaşmış/çıplak yüzey payı (NDBI>0 ve NDVI<0.2);
SCL ile bulut/gölge/kar maskesi. Fark = yapılaşma değişimi (yüzde puan). 10 m çözünürlük; parsel içi değil, çevre göstergesi.
Kimlik: .env → CDSE_S3_ACCESS_KEY / CDSE_S3_SECRET_KEY (asla koda yazılmaz); yoksa status "credentials_required".
Önbellek: uydu_onbellek.sqlite (hücre = 0,01° ızgara; sahne kimliği + istatistik; 30 gün). Canlı kapatma: GEOPROP_UYDU_CANLI=0.
Uydurma yok: bulutlu pencere (geçerli piksel < %70) atılır, uygun sahne yoksa "sahne_yok".
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

STAC = "https://catalogue.dataspace.copernicus.eu/stac/collections/sentinel-2-l2a/items"
PENCERE_M = 500          # merkezden yarım kenar
ONBELLEK_GUN = 30
GECERLI_PIKSEL_ORANI = 0.7
SCL_GECERLI = {4, 5, 6, 7}   # vegetation, not vegetated, water, unclassified (bulut/gölge/kar hariç)


def _env_yukle(repo: Path):
    p = repo / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class SatelliteChangeEngine:
    def __init__(self, cache_db: str | Path, timeout_s: float = 25.0):
        self.cache_db = Path(cache_db).expanduser().resolve()
        self.timeout_s = timeout_s
        self.enabled = os.environ.get("GEOPROP_UYDU_CANLI", "1") != "0"
        _env_yukle(self.cache_db.parents[2] if len(self.cache_db.parents) > 2 else Path.cwd())

    # ---- yardımcılar ----
    def _cache(self):
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.cache_db)
        c.execute("CREATE TABLE IF NOT EXISTS uydu_hucre (hucre TEXT, donem TEXT, sahne TEXT, tarih TEXT, bulut REAL, ndvi REAL, ndbi REAL, bitki_pay REAL, yapili_pay REAL, gecerli_oran REAL, hesaplanma TEXT, PRIMARY KEY (hucre, donem))")
        return c

    def _stac(self, lat, lon, t0: date, t1: date):
        bbox = f"{lon-0.006},{lat-0.0045},{lon+0.006},{lat+0.0045}"
        url = STAC + "?" + urllib.parse.urlencode({"bbox": bbox, "datetime": f"{t0.isoformat()}T00:00:00Z/{t1.isoformat()}T23:59:59Z", "limit": 20, "sortby": "-properties.datetime"})
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GEOPROP/1.0"}), timeout=self.timeout_s) as r:
            feats = json.loads(r.read().decode("utf-8")).get("features", [])
        feats = [f for f in feats if (f["properties"].get("eo:cloud_cover") or 100) <= 40]
        feats.sort(key=lambda f: (f["properties"].get("eo:cloud_cover") or 100))
        return feats

    def _oku(self, feat, lat, lon):
        import numpy as np
        import rasterio
        from rasterio.warp import transform
        from rasterio.windows import from_bounds
        from rasterio.enums import Resampling
        # rasterio AWS anahtarlarını Env'e doğrudan almaz → AWSSession (boto3 kimliği, özel uç nokta)
        from rasterio.session import AWSSession
        import boto3
        sess = AWSSession(boto3.Session(aws_access_key_id=os.environ.get("CDSE_S3_ACCESS_KEY", ""), aws_secret_access_key=os.environ.get("CDSE_S3_SECRET_KEY", "")),
                          endpoint_url="eodata.dataspace.copernicus.eu")
        env = {"AWS_VIRTUAL_HOSTING": "FALSE", "AWS_HTTPS": "YES", "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}
        assets = feat["assets"]

        def vsis3(a):
            return assets[a]["href"].replace("s3://", "/vsis3/")

        with rasterio.Env(session=sess, **env):
            with rasterio.open(vsis3("B04_10m")) as ds:
                xs, ys = transform("EPSG:4326", ds.crs, [lon], [lat])
                x, y = xs[0], ys[0]
                w = from_bounds(x - PENCERE_M, y - PENCERE_M, x + PENCERE_M, y + PENCERE_M, ds.transform)
                b04 = ds.read(1, window=w).astype("float32"); shp = b04.shape
            with rasterio.open(vsis3("B08_10m")) as ds:
                b08 = ds.read(1, window=from_bounds(x - PENCERE_M, y - PENCERE_M, x + PENCERE_M, y + PENCERE_M, ds.transform), out_shape=shp, resampling=Resampling.bilinear).astype("float32")
            with rasterio.open(vsis3("B11_20m")) as ds:
                b11 = ds.read(1, window=from_bounds(x - PENCERE_M, y - PENCERE_M, x + PENCERE_M, y + PENCERE_M, ds.transform), out_shape=shp, resampling=Resampling.bilinear).astype("float32")
            with rasterio.open(vsis3("SCL_20m")) as ds:
                scl = ds.read(1, window=from_bounds(x - PENCERE_M, y - PENCERE_M, x + PENCERE_M, y + PENCERE_M, ds.transform), out_shape=shp, resampling=Resampling.nearest)
        gecerli = np.isin(scl, list(SCL_GECERLI)) & (b04 > 0) & (b08 > 0)
        oran = float(gecerli.mean()) if gecerli.size else 0.0
        if oran < GECERLI_PIKSEL_ORANI:
            return None, oran
        ndvi = (b08 - b04) / np.maximum(b08 + b04, 1); ndbi = (b11 - b08) / np.maximum(b11 + b08, 1)
        v, b = ndvi[gecerli], ndbi[gecerli]
        return {"ndvi": round(float(v.mean()), 3), "ndbi": round(float(b.mean()), 3),
                "bitki_pay": round(float((v > 0.4).mean() * 100), 1), "yapili_pay": round(float(((b > 0) & (v < 0.2)).mean() * 100), 1),
                "gecerli_oran": round(oran, 2)}, oran

    def _donem(self, c, hucre, donem, lat, lon, t0, t1):
        r = c.execute("SELECT * FROM uydu_hucre WHERE hucre=? AND donem=? AND hesaplanma>=?", (hucre, donem, (date.today() - timedelta(days=ONBELLEK_GUN)).isoformat())).fetchone()
        if r:
            return {"sahne": r[2], "tarih": r[3], "bulut": r[4], "ndvi": r[5], "ndbi": r[6], "bitki_pay": r[7], "yapili_pay": r[8], "gecerli_oran": r[9], "onbellek": True}
        for f in self._stac(lat, lon, t0, t1)[:4]:
            stats, oran = self._oku(f, lat, lon)
            if stats:
                out = {"sahne": f["id"], "tarih": f["properties"]["datetime"][:10], "bulut": f["properties"].get("eo:cloud_cover"), **stats, "onbellek": False}
                c.execute("INSERT OR REPLACE INTO uydu_hucre VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (hucre, donem, out["sahne"], out["tarih"], out["bulut"], out["ndvi"], out["ndbi"], out["bitki_pay"], out["yapili_pay"], out["gecerli_oran"], date.today().isoformat()))
                c.commit()
                return out
        return None

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        if not self.enabled:
            return {"status": "disabled"}
        if not os.environ.get("CDSE_S3_ACCESS_KEY"):
            return {"status": "credentials_required", "not": "Copernicus Data Space S3 anahtarı (.env: CDSE_S3_ACCESS_KEY/SECRET_KEY) tanımlı değil."}
        hucre = f"{round(lat, 2):.2f},{round(lon, 2):.2f}"
        bugun = date.today()
        donemler = {"simdi": (bugun - timedelta(days=75), bugun), "1_yil_once": (bugun - timedelta(days=365 + 60), bugun - timedelta(days=365 - 60)),
                    "3_yil_once": (bugun - timedelta(days=3 * 365 + 75), bugun - timedelta(days=3 * 365 - 75))}
        t = time.time(); c = self._cache(); sonuc = {}
        try:
            for k, (t0, t1) in donemler.items():
                try:
                    sonuc[k] = self._donem(c, hucre, k, lat, lon, t0, t1)
                except Exception as exc:
                    sonuc[k] = None; sonuc[k + "_hata"] = type(exc).__name__
                if time.time() - t > self.timeout_s * 2:
                    break
        finally:
            c.close()
        s, y1, y3 = sonuc.get("simdi"), sonuc.get("1_yil_once"), sonuc.get("3_yil_once")
        if not s:
            return {"status": "sahne_yok", "not": "Son 75 günde bulutsuz Sentinel-2 sahnesi bulunamadı.", "sure_ms": round((time.time() - t) * 1000)}
        def fark(a, b):
            return {"yapili_pay_puan": round(a["yapili_pay"] - b["yapili_pay"], 1), "bitki_pay_puan": round(a["bitki_pay"] - b["bitki_pay"], 1),
                    "ndvi_fark": round(a["ndvi"] - b["ndvi"], 3), "ndbi_fark": round(a["ndbi"] - b["ndbi"], 3)} if a and b else None
        return {"status": "available", "pencere_m": PENCERE_M * 2, "simdi": s, "bir_yil_once": y1, "uc_yil_once": y3,
                "degisim_1y": fark(s, y1), "degisim_3y": fark(s, y3), "sure_ms": round((time.time() - t) * 1000),
                "kaynak": "Copernicus Sentinel-2 L2A (ESA, CDSE S3), NDVI/NDBI; SCL bulut maskesi",
                "not": "1 km pencerede yapılaşmış/çıplak yüzey (NDBI>0 ve NDVI<0.2) ve bitki örtüsü (NDVI>0.4) payları; mevsim etkisini azaltmak için aynı mevsim ±60 gün. 10 m çözünürlük; parsel içi değil çevre göstergesi. Çıplak toprak/hasat edilmiş tarla da 'yapılı' sınıfına düşebilir."}
