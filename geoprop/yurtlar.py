"""Öğrenci yurtları (KYK + GSB lisanslı özel) — yakınlık ve kapasite; yurtlar.sqlite (kardeş dosya).

Konut/dükkan talebi için: 1 km / 3 km içindeki yurt sayısı ve toplam kapasite (kız/erkek), en yakın N yurt.
Doluluk yayımlanmaz → yalnız kapasite; KYK kapasitesi kısmi (ihale listesi) → bilinmeyen "kapasite: bilinmiyor".
Koordinatlar adresten mahalle/köy merkezi (yaklaşık) olabilir → `konum_yaklasik`.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

from .veri_yardimcilari import haversine_km

YARICAPLAR_KM = (1.0, 3.0)
EN_YAKIN_N = 5
ARAMA_KM = 5.0


class DormEngine:
    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def _conn(self):
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='yurt'").fetchone():
            c.close(); return None
        return c

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._conn()
        if c is None:
            return None
        try:
            dlat = ARAMA_KM / 111.0; dlon = ARAMA_KM / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
            cols = {r[1] for r in c.execute("PRAGMA table_info(yurt)")}
            extra = ", kiz_kapasite, erkek_kapasite" if "kiz_kapasite" in cols else ", NULL AS kiz_kapasite, NULL AS erkek_kapasite"
            rows = c.execute(f"SELECT kaynak, il, ilce, ad, tip, kapasite, kapasite_kaynagi, lat, lon, koordinat_kaynagi{extra} FROM yurt "
                             "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
            items = []
            for r in rows:
                d = haversine_km(lat, lon, r["lat"], r["lon"])
                if d <= ARAMA_KM:
                    items.append({"kaynak": r["kaynak"], "kaynak_etiket": "KYK (GSB)" if r["kaynak"] == "kyk" else "Özel (GSB lisanslı)",
                                  "ad": r["ad"], "tip": r["tip"], "kapasite": r["kapasite"], "kapasite_kaynagi": r["kapasite_kaynagi"],
                                  "kiz_kapasite": r["kiz_kapasite"], "erkek_kapasite": r["erkek_kapasite"],
                                  "ilce": r["ilce"], "lat": r["lat"], "lon": r["lon"], "mesafe_m": round(d * 1000),
                                  "konum_yaklasik": "yaklaşık" in (r["koordinat_kaynagi"] or "")})
            items.sort(key=lambda x: x["mesafe_m"])
            ozet = {}
            for r_km in YARICAPLAR_KM:
                sub = [x for x in items if x["mesafe_m"] <= r_km * 1000]
                ozet[f"{int(r_km * 1000)}m"] = {
                    "yurt": len(sub), "kyk": sum(1 for x in sub if x["kaynak"] == "kyk"), "ozel": sum(1 for x in sub if x["kaynak"] == "ozel"),
                    "kapasite_bilinen": sum(x["kapasite"] or 0 for x in sub), "kapasitesi_bilinmeyen": sum(1 for x in sub if x["kapasite"] is None),
                    # KYGM listesinde kız/erkek ayrı; özel yurtlarda tip tek cinsiyet → kapasite o cinse yazılır.
                    "kiz_kapasite": sum((x["kiz_kapasite"] if x["kiz_kapasite"] is not None else (x["kapasite"] or 0 if (x["tip"] or "").lower().startswith("k") else 0)) for x in sub),
                    "erkek_kapasite": sum((x["erkek_kapasite"] if x["erkek_kapasite"] is not None else (x["kapasite"] or 0 if (x["tip"] or "").lower().startswith("e") else 0)) for x in sub),
                }
            meta = {r["tablo"]: r["guncellenme"] for r in c.execute("SELECT tablo, guncellenme FROM kapsama")} if c.execute("SELECT 1 FROM sqlite_master WHERE name='kapsama'").fetchone() else {}
            toplam_koordinatli = c.execute("SELECT COUNT(*) FROM yurt WHERE lat IS NOT NULL").fetchone()[0]
            toplam = c.execute("SELECT COUNT(*) FROM yurt").fetchone()[0]
        finally:
            c.close()
        return {
            "status": "available",
            "kaynak": "GSB/KYGM yurt listesi (kygm.gsb.gov.tr) + GSB Özel Barınma Hizmetleri (ozelbarinmahizmetleri.gsb.gov.tr); KYK kapasitesi KYGM işletme listesi",
            "guncellenme": meta.get("yurt_ozel") or meta.get("yurt_kyk"),
            "kapsam": {"toplam_yurt": toplam, "koordinatli": toplam_koordinatli},
            "arama_km": ARAMA_KM, "ozet": ozet, "en_yakin": items[:EN_YAKIN_N],
            "not": "Doluluk (barınan öğrenci sayısı) yurt bazında yayımlanmaz; yalnız lisanslı kapasite. KYK yurtlarının bir kısmında kapasite bilinmiyor.",
        }
