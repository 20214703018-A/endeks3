"""Açıklanabilir arsa emsal seçimi ve ön değerleme motoru."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import statistics
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .geographic_context import GeographicContextEngine
from .parcel_shape import analyze_shape


MODEL_VERSION = "land-market-evidence-v2.1.0"

# SEGE ilçe gelişmişlik verisi (istihbarat ambarı) — repo köküne göre.
_REPO_ROOT = Path(__file__).resolve().parents[1]
INTELLIGENCE_DB = _REPO_ROOT / "collector" / "data" / "turkiye_makro_ve_mikro_istihbarat.sqlite"


# Paylaşılan yardımcılar ayrı modülde (döngüsel import olmadan diğer motorlar da kullanır);
# geriye dönük uyumluluk için buradan yeniden dışa aktarılır.
from .veri_yardimcilari import (
    NORM_COLUMNS,  # noqa: E402,F401
    LandAnalysisError, normalize_name, normalize_neighbourhood, optional_float, haversine_km,
)
from .bolge_istatistik import BolgeIstatistikEngine  # noqa: E402
from .jeoloji import GeologyContextEngine  # noqa: E402
from .arazi import TerrainEngine  # noqa: E402
from .yakin_noktalar import NearbyPoiEngine  # noqa: E402
from .okullar import SchoolEngine  # noqa: E402
from .yurtlar import DormEngine  # noqa: E402
from .universiteler import UniversityEngine  # noqa: E402
from .pazarlar import MarketEngine  # noqa: E402
from .tuik_bolge import TuikRegionEngine  # noqa: E402
from .cevre import EnvironmentEngine  # noqa: E402
from .afet_vekili import afet_vekilleri  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise LandAnalysisError("Yüzdelik hesaplamak için veri bulunamadı.")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def weighted_median(items: list[tuple[float, float]]) -> float:
    ordered = sorted(items, key=lambda item: item[0])
    threshold = sum(weight for _, weight in ordered) / 2
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _name_where(connection: sqlite3.Connection, table: str, *columns: str) -> str:
    """Ad eşleşmesi için WHERE parçası. Tabloda önceden normalize edilmiş `*_norm` sütunu varsa
    indeksli eşitlik (`il_norm=?`), yoksa fonksiyonlu biçim (`geoprop_normalize(il)=?`) kullanılır.
    Fonksiyonlu biçim indeks kullanamaz ve tabloyu satır satır tarar; ürün ambarları
    `ensure_normalized_name_columns` ile sütunları taşır, geçici test DB'leri taşımayabilir."""
    existing = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    parts = []
    for column in columns:
        norm_col = NORM_COLUMNS[column][0]
        if norm_col in existing:
            parts.append(f"{norm_col}=?")
        elif column == "mahalle":
            parts.append(f"geoprop_neighbourhood({column})=?")
        else:
            parts.append(f"geoprop_normalize({column})=?")
    return " AND ".join(parts)


class LandAnalysisEngine:
    """Yerel ilan gözlemlerinden bağlantısız, açıklanabilir arsa ön değerlemesi üretir."""

    FACTORS = {
        "shape": {
            "regular": "Düzenli parsel şekli",
            "near_regular": "Düzenliye yakın parsel şekli",
            "irregular": "Düzensiz parsel şekli",
            "very_irregular": "Çok düzensiz parsel şekli",
            "unknown": "Parsel şekli bilinmiyor",
        },
        "road": {
            "cadastral_frontage": "Kadastral yola cephe",
            "road_access": "Yol erişimi mevcut",
            "limited": "Sınırlı yol erişimi",
            "none": "Doğrudan yol erişimi yok",
            "unknown": "Yol durumu bilinmiyor",
        },
        "water": {
            "connected": "Su bağlantısı mevcut",
            "nearby": "Su altyapısı yakın",
            "none": "Su bağlantısı doğrulanmadı",
            "unknown": "Su durumu bilinmiyor",
        },
        "ground": {
            "suitable": "Zemin uygun olarak bildirildi",
            "conditional": "Zemin koşullu olarak bildirildi",
            "risky": "Zemin riski bildirildi",
            "unknown": "Zemin verisi bilinmiyor",
        },
        "growth": {
            "toward_growth": "Bildirilen gelişme yönünde",
            "neutral": "Gelişme yönü nötr",
            "outside_growth": "Bildirilen gelişme yönü dışında",
            "unknown": "Gelişme yönü bilinmiyor",
        },
        "zoning": {
            "zoned": "İmarlı olarak bildirildi",
            "development": "Gelişme alanı olarak bildirildi",
            "agricultural": "Tarım niteliği bildirildi",
            "protected": "Koruma kısıtı bildirildi",
            "unknown": "İmar durumu bilinmiyor",
        },
    }

    def __init__(self, listing_database: str | Path, density_database: str | Path | None = None):
        self.listing_database = Path(listing_database).expanduser().resolve()
        # TKGM alım-satım yoğunluğu ayrı ambardadır (varsayılan: kardeş dosya).
        if density_database is not None:
            self.density_database = Path(density_database).expanduser().resolve()
        else:
            self.density_database = self.listing_database.with_name("tkgm_alim_satim.sqlite")
        # Coğrafi katmanlar (fay, elektrik, su, dere, orman/sit/sahil, demiryolu…).
        self.geographic_database = self.listing_database.with_name("cografi_katmanlar.sqlite")
        # Mahalle konut/yapılaşma profili (ortalama konut m², kat).
        self.building_database = self.listing_database.with_name("mahalle_yapilasma.sqlite")
        # Emlakjet bölge endeks (konut amortisman/getiri/kira/bina yaşı).
        self.investment_database = self.listing_database.with_name("emlakjet_bolge_endeks.sqlite")
        # Ulusal bölge istatistikleri (demografi, yıllık satış, fiyat özeti, kırılımlar, POI…).
        self.bolge_database = self.listing_database.with_name("bolge_istatistik.sqlite")
        self._bolge_engine = BolgeIstatistikEngine(self.bolge_database)
        # Jeolojik birim: MTA WMS canlı nokta sorgusu (poligon saklanmaz; bkz. jeoloji.py).
        self._geology_engine = GeologyContextEngine()
        # Arazi (rakım/eğim/bakı): DEM pencere okuma; 30 m sınıfı → "bölgesel", 10 m gelince parsel düzeyi.
        self._terrain_engine = TerrainEngine()
        # OSM Türkiye POI ambarı (kardeş dosya): koordinat bazlı yakınlık, hatlar, zincirler.
        self.poi_database = self.listing_database.with_name("osm_poi.sqlite")
        self._nearby_engine = NearbyPoiEngine(self.poi_database)
        # MEB resmî okullar (koordinat + kapasite + LGS puanı).
        self._school_engine = SchoolEngine(self.listing_database.with_name("resmi_egitim.sqlite"))
        self._dorm_engine = DormEngine(self.listing_database.with_name("yurtlar.sqlite"))
        self._university_engine = UniversityEngine(self.listing_database.with_name("universite.sqlite"))
        self._market_engine = MarketEngine(self.listing_database.with_name("pazarlar.sqlite"))
        self._tuik_engine = TuikRegionEngine(self.listing_database.with_name("tuik_bolge.sqlite"), self._admin_lookup_lazy())
        self._env_engine = EnvironmentEngine(self.listing_database.with_name("cevre.sqlite"), self.listing_database.with_name("resmi_gazete.sqlite"))
        self._geographic_engine: GeographicContextEngine | None = None

    def _admin_lookup_lazy(self):
        """idari_sinirlar.sqlite varsa AdminLookup (poligonlar tembel yüklenir); yoksa None."""
        p = self.listing_database.with_name("idari_sinirlar.sqlite")
        if not p.exists():
            return None
        try:
            from .idari import AdminLookup
            return AdminLookup(p)
        except Exception:
            return None

    def _load_land_listings(
        self,
        province: Any = None,
        center: tuple[float, float] | None = None,
        radius_km: float | None = None,
    ) -> list[dict[str, Any]]:
        """Arsa ilanlarını döndürür. `center`+`radius_km` verilirse enlem/boylam kutusuyla
        (idx_ilanlar_coordinates) ön eleme yapılır; haversine kesin eleme çağıranındır."""
        if not self.listing_database.exists():
            raise LandAnalysisError("Arsa ilan gözlem veritabanı bulunamadı.")
        uri = f"file:{self.listing_database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.create_function("geoprop_normalize", 1, normalize_name, deterministic=True)
            sql = """
                SELECT ilan_id, baslik, il, ilce, mahalle, fiyat_tl, m2,
                       birim_m2_fiyat, ilan_tarihi, crawled_at, enlem, boylam,
                       ada_no, parsel_no, imar_durumu, kaks_emsal
                FROM ilanlar
                WHERE lower(kategori) = 'arsa'
                  AND fiyat_tl > 0 AND m2 > 0 AND birim_m2_fiyat > 0
            """
            params: list[Any] = []
            if center is not None and radius_km:
                lat, lon = center
                dlat = radius_km / 111.0
                dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
                sql += " AND enlem BETWEEN ? AND ? AND boylam BETWEEN ? AND ?"
                params += [lat - dlat, lat + dlat, lon - dlon, lon + dlon]
            normalized_province = normalize_name(province)
            if normalized_province:
                sql += " AND " + _name_where(connection, "ilanlar", "il")
                params.append(normalized_province)
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def _load_market_index(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """En dar mevcut coğrafi düzeyde en güncel gözlenen mahalle endeksini döndürür."""
        uri = f"file:{self.listing_database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='arsa_mahalle_ozet'"
            ).fetchone()
            if not exists:
                return None
            connection.row_factory = sqlite3.Row
            connection.create_function("geoprop_normalize", 1, normalize_name, deterministic=True)
            connection.create_function(
                "geoprop_neighbourhood", 1, normalize_neighbourhood, deterministic=True
            )
            category = normalize_name(request.get("kategori") or "arsa")
            province = normalize_name(request.get("il"))
            district = normalize_name(request.get("ilce"))
            neighbourhood = normalize_neighbourhood(request.get("mahalle"))
            # İl düzeyinde indeksli ön eleme; ilçe/mahalle eşleşmesi il altkümesinde Python'da.
            rows = connection.execute(
                f"""
                SELECT kategori, city_id, county_id, district_id, il, ilce, mahalle, donem,
                       satilik_m2_fiyat, min_m2_fiyat, max_m2_fiyat, ortalama_fiyat,
                       ortalama_m2, fiyat_endeksi, aylik_fiyat_degisim,
                       yillik_fiyat_degisim, ilan_sayisi, ilanda_kalma_suresi_gun,
                       stok_degisim_orani, yillik_stok_degisim, guncellenme_tarihi
                FROM arsa_mahalle_ozet
                WHERE {_name_where(connection, "arsa_mahalle_ozet", "il")} AND geoprop_normalize(kategori)=?
                ORDER BY donem DESC
                """,
                (province, category),
            ).fetchall()
        candidates = [dict(row) for row in rows]
        if neighbourhood:
            exact = [
                row for row in candidates
                if normalize_name(row.get("ilce")) == district
                and normalize_neighbourhood(row.get("mahalle")) == neighbourhood
            ]
            if exact:
                exact[0]["match_scope"] = "mahalle"
                return exact[0]
        if district:
            district_rows = [row for row in candidates if normalize_name(row.get("ilce")) == district]
            if district_rows:
                priced = [row for row in district_rows if optional_float(row.get("satilik_m2_fiyat"))]
                if priced:
                    priced.sort(key=lambda row: int(row.get("ilan_sayisi") or 0), reverse=True)
                    priced[0]["match_scope"] = "ilce_temsilci_mahalle"
                    return priced[0]
        return None

    def _load_market_index_history(
        self, category: Any, row: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Eşleşen mahalle/bölge için aylık endeks geçmişini (gözlenen + projeksiyon) döndürür."""
        if not row or None in (
            row.get("city_id"), row.get("county_id"), row.get("district_id")
        ):
            return []
        uri = f"file:{self.listing_database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='arsa_mahalle_trend'"
            ).fetchone()
            if not exists:
                return []
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "geoprop_normalize", 1, normalize_name, deterministic=True
            )
            series = connection.execute(
                """
                SELECT ay, satilik_m2_fiyat, fiyat_endeksi, min_m2_fiyat, max_m2_fiyat,
                       ortalama_m2, ilan_sayisi, aylik_fiyat_degisim, yillik_fiyat_degisim,
                       record_class
                FROM arsa_mahalle_trend
                WHERE geoprop_normalize(kategori)=? AND city_id=? AND county_id=? AND district_id=?
                ORDER BY ay ASC
                """,
                (
                    normalize_name(category or "arsa"),
                    row.get("city_id"),
                    row.get("county_id"),
                    row.get("district_id"),
                ),
            ).fetchall()
        return [
            {
                "period": item["ay"],
                "unit_price": item["satilik_m2_fiyat"],
                "price_index": item["fiyat_endeksi"],
                "min_unit_price": item["min_m2_fiyat"],
                "max_unit_price": item["max_m2_fiyat"],
                "avg_area_m2": item["ortalama_m2"],
                "listing_count": item["ilan_sayisi"],
                "monthly_change": item["aylik_fiyat_degisim"],
                "yearly_change": item["yillik_fiyat_degisim"],
                "record_class": item["record_class"],
                "is_projection": item["record_class"] == "projection",
            }
            for item in series
        ]

    @staticmethod
    def _parse_segment_bounds(name: Any) -> tuple[int, int | None] | None:
        """'1-1.000' / '20.001-100.000' / '100.001 ve üzeri' → (alt, üst) m² sınırları."""
        text = str(name or "").strip()
        if not text or text == "0":
            return None
        cleaned = text.replace(".", "").replace("\xa0", " ")
        numbers = [int(part) for part in re.findall(r"\d+", cleaned)]
        if not numbers:
            return None
        if "üzeri" in cleaned.lower() or "uzeri" in cleaned.lower() or len(numbers) == 1:
            return (numbers[0], None)
        return (numbers[0], numbers[1])

    def _load_area_segments(
        self, category: Any, row: dict[str, Any] | None, target_area: float | None = None
    ) -> list[dict[str, Any]]:
        """İlçe seviyesinde alan bandına göre m² fiyat kırılımını döndürür."""
        if not row or None in (row.get("city_id"), row.get("county_id")):
            return []
        uri = f"file:{self.listing_database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='arsa_alan_segmentleri'"
            ).fetchone()
            if not exists:
                return []
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "geoprop_normalize", 1, normalize_name, deterministic=True
            )
            rows = connection.execute(
                """
                SELECT bolge_adi, segment_adi, satilik_m2_fiyat, min_m2_fiyat, max_m2_fiyat,
                       ortalama_fiyat, ortalama_m2, ilan_sayisi, ilan_orani, ilanda_kalma_suresi
                FROM arsa_alan_segmentleri
                WHERE geoprop_normalize(kategori)=? AND city_id=? AND county_id=?
                """,
                (normalize_name(category or "arsa"), row.get("city_id"), row.get("county_id")),
            ).fetchall()
        segments: list[dict[str, Any]] = []
        for item in rows:
            bounds = self._parse_segment_bounds(item["segment_adi"])
            unit_price = optional_float(item["satilik_m2_fiyat"])
            # Veri taşımayan '0' bandını (fiyatsız) atla.
            if bounds is None and unit_price is None:
                continue
            low, high = bounds if bounds else (None, None)
            matches = bool(
                target_area is not None
                and low is not None
                and target_area >= low
                and (high is None or target_area <= high)
            )
            segments.append({
                "band": item["segment_adi"],
                "region": item["bolge_adi"],
                "min_area_m2": low,
                "max_area_m2": high,
                "unit_price": unit_price,
                "min_unit_price": item["min_m2_fiyat"],
                "max_unit_price": item["max_m2_fiyat"],
                "avg_total_price": item["ortalama_fiyat"],
                "avg_area_m2": item["ortalama_m2"],
                "listing_count": item["ilan_sayisi"],
                "listing_share": item["ilan_orani"],
                "days_on_market": item["ilanda_kalma_suresi"],
                "matches_input": matches,
            })
        segments.sort(key=lambda seg: (seg["min_area_m2"] if seg["min_area_m2"] is not None else 10**18))
        return segments

    def _declared_zoning(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """İlanlarda beyan edilen imar durumu dağılımını (gerçek veri) döndürür.

        KAKS/TAKS gibi sayısal değerler kaynakta güvenilir olmadığından üretilmez;
        yalnızca beyan edilen imar durumu (ör. 'Konut İmarlı') ve dağılımı sunulur.
        """
        province = normalize_name(request.get("il"))
        if not province:
            return None
        district = normalize_name(request.get("ilce"))
        neighbourhood = normalize_neighbourhood(request.get("mahalle"))

        uri = f"file:{self.listing_database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.create_function("geoprop_normalize", 1, normalize_name, deterministic=True)
            connection.create_function("geoprop_neighbourhood", 1, normalize_neighbourhood, deterministic=True)
            scopes: list[tuple[str, str, tuple]] = []
            if neighbourhood and district:
                scopes.append(("mahalle", _name_where(connection, "ilanlar", "il", "ilce", "mahalle"),
                               (province, district, neighbourhood)))
            if district:
                scopes.append(("ilce", _name_where(connection, "ilanlar", "il", "ilce"), (province, district)))
            scopes.append(("il", _name_where(connection, "ilanlar", "il"), (province,)))
            for scope_name, where, params in scopes:
                rows = connection.execute(
                    f"""
                    SELECT imar_durumu AS label, COUNT(*) AS n
                    FROM ilanlar
                    WHERE {where}
                      AND imar_durumu IS NOT NULL AND TRIM(imar_durumu) <> ''
                    GROUP BY imar_durumu
                    ORDER BY n DESC
                    """,
                    params,
                ).fetchall()
                if rows:
                    total = sum(int(row["n"]) for row in rows)
                    return {
                        "status": "declared_from_listings",
                        "scope": scope_name,
                        "dominant": rows[0]["label"],
                        "sample_size": total,
                        "distribution": [
                            {
                                "label": row["label"],
                                "count": int(row["n"]),
                                "share": round(int(row["n"]) / total, 3),
                            }
                            for row in rows[:6]
                        ],
                        "note": "İlan beyanıdır; resmi plan fonksiyonu ve KAKS/TAKS TKGM/E-Plan'dan doğrulanmalıdır.",
                    }
        return None

    def _load_neighborhood_building(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Mahalle konut/yapılaşma profili: ortalama konut m², kat, oda dağılımı."""
        province = normalize_name(request.get("il"))
        district = normalize_name(request.get("ilce"))
        neighbourhood = normalize_neighbourhood(request.get("mahalle"))
        if not province or not district or not neighbourhood or not self.building_database.exists():
            return None
        uri = f"file:{self.building_database}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                connection.create_function("geoprop_normalize", 1, normalize_name, deterministic=True)
                connection.create_function("geoprop_neighbourhood", 1, normalize_neighbourhood, deterministic=True)
                row = connection.execute(
                    f"""
                    SELECT il, ilce, mahalle, konut_ilan, ort_m2_brut, medyan_m2_net,
                           ort_kat, max_kat, oda_dagilimi
                    FROM mahalle_yapilasma_ozet
                    WHERE {_name_where(connection, "mahalle_yapilasma_ozet", "il", "ilce", "mahalle")}
                    LIMIT 1
                    """,
                    (province, district, neighbourhood),
                ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            oda = json.loads(row["oda_dagilimi"]) if row["oda_dagilimi"] else []
        except (ValueError, TypeError):
            oda = []
        return {
            "status": "available",
            "source": "Konut ilan ambarı (mahalle agregatı)",
            "listing_count": row["konut_ilan"],
            "avg_gross_m2": row["ort_m2_brut"],
            "median_net_m2": row["medyan_m2_net"],
            "avg_floor": row["ort_kat"],
            "max_floor": row["max_kat"],
            "room_mix": oda,
            "note": "Kat, ilanın bulunduğu kattır (binanın toplam kat sayısı değil); yapılaşma yüksekliği göstergesidir. Bina yaşı kaynakta yok.",
        }

    def _load_investment_profile(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Emlakjet konut endeksinden amortisman/getiri/kira/bina yaşı (yatırım bağlamı).

        Arsa kiralanmadığından amortisman arsada yoktur; bölgenin KONUT piyasası
        olgunluğu bağlamı olarak mahalle→ilçe sırasıyla döner.
        """
        province = normalize_name(request.get("il"))
        district = normalize_name(request.get("ilce"))
        neighbourhood = normalize_neighbourhood(request.get("mahalle"))
        if not province or not self.investment_database.exists():
            return None
        uri = f"file:{self.investment_database}?mode=ro"
        columns = ("mahalle", "amortisman", "getiri", "ort_bina_yasi",
                   "kira_m2_fiyat", "kira_fiyat", "m2_fiyat", "donem")
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                connection.create_function("geoprop_normalize", 1, normalize_name, deterministic=True)
                connection.create_function("geoprop_neighbourhood", 1, normalize_neighbourhood, deterministic=True)
                row = None
                scope = None
                if district and neighbourhood:
                    row = connection.execute(
                        f"""SELECT {', '.join(columns)} FROM bolge_endeks
                            WHERE tip='konut' AND seviye='mahalle'
                              AND {_name_where(connection, "bolge_endeks", "il", "ilce", "mahalle")}
                              AND amortisman IS NOT NULL
                            ORDER BY donem DESC LIMIT 1""",
                        (province, district, neighbourhood),
                    ).fetchone()
                    if row:
                        scope = "mahalle"
                if not row and district:
                    row = connection.execute(
                        f"""SELECT {', '.join(columns)} FROM bolge_endeks
                            WHERE tip='konut' AND seviye='ilce'
                              AND {_name_where(connection, "bolge_endeks", "il", "ilce")}
                              AND amortisman IS NOT NULL
                            ORDER BY donem DESC LIMIT 1""",
                        (province, district),
                    ).fetchone()
                    if row:
                        scope = "ilce"
        except sqlite3.Error:
            return None
        if not row:
            return None
        return {
            "status": "available",
            "scope": scope,
            "source": "Emlakjet bölge endeks (konut)",
            "matched_mahalle": row["mahalle"],
            "amortization_years": row["amortisman"],
            "rental_yield_pct": row["getiri"],
            "avg_building_age": row["ort_bina_yasi"],
            "rent_m2_price": row["kira_m2_fiyat"],
            "rent_price": row["kira_fiyat"],
            "sale_m2_price": row["m2_fiyat"],
            "period": row["donem"],
            "note": "Amortisman/getiri/kira KONUT piyasasına aittir; arsa kiralanmaz. Bölge yatırım olgunluğu bağlamıdır.",
        }

    @staticmethod
    def _load_district_development(request: dict[str, Any]) -> dict[str, Any] | None:
        """SEGE ilçe gelişmişlik kademesi/sıralaması (arsa yatırım bağlamı)."""
        province = normalize_name(request.get("il"))
        district = normalize_name(request.get("ilce"))
        if not province or not district or not INTELLIGENCE_DB.exists():
            return None
        uri = f"file:{INTELLIGENCE_DB}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                connection.create_function(
                    "geoprop_normalize", 1, normalize_name, deterministic=True
                )
                row = connection.execute(
                    """
                    SELECT il_adi, ilce_adi, sege_siralamasi, sege_skoru,
                           gelismislik_kademesi, kademe_tanimi, sosyo_ekonomik_sinif,
                           veri_donemi
                    FROM sege_973_ilce_gelismislik
                    WHERE geoprop_normalize(il_adi)=? AND geoprop_normalize(ilce_adi)=?
                    LIMIT 1
                    """,
                    (province, district),
                ).fetchone()
                total = connection.execute(
                    "SELECT COUNT(*) FROM sege_973_ilce_gelismislik"
                ).fetchone()[0]
        except sqlite3.Error:
            return None
        if not row:
            return None
        return {
            "status": "available",
            "source": "SEGE (Sanayi ve Teknoloji Bakanlığı)",
            "rank": row["sege_siralamasi"],
            "total_districts": total,
            "score": row["sege_skoru"],
            "tier": row["gelismislik_kademesi"],
            "tier_label": row["kademe_tanimi"],
            "socioeconomic_class": row["sosyo_ekonomik_sinif"],
            "period": row["veri_donemi"],
        }

    @staticmethod
    def _city_expansion(zoning: dict[str, Any] | None) -> dict[str, Any]:
        """Canlı E-Plan plan listesinden şehir genişleme/üst ölçek plan bağlamını çıkarır.

        Üst ölçek planlar (Çevre Düzeni, Nazım İmar Planı) makro arazi kullanımını ve
        gelişme alanlarını belirler. Yapısal 'gelişme alanı' tanımı E-Plan GML'inde
        çoğu planda dijital değildir; bu yüzden plan adı/fonksiyonundaki 'gelişme,
        genişleme, rezerv, yeni yerleşim' gibi işaretler de taranır.
        """
        plans = (zoning or {}).get("plans") or []
        fields = (zoning or {}).get("fields") or {}
        if not plans:
            return {
                "status": "requires_live_query",
                "message": "Şehir genişleme/üst ölçek plan bilgisi TKGM+E-Plan canlı sorgusuyla gelir.",
            }
        keywords = ("gelişme", "gelisme", "genişleme", "genisleme", "rezerv",
                    "yeni yerleşim", "yeni yerlesim", "kentsel dönüşüm", "kentsel donusum")
        upper_scale = []
        for plan in plans:
            tur = normalize_name(plan.get("plan_turu"))
            if "cevre duzeni" in tur or "nazim" in tur:
                upper_scale.append({
                    "plan_adi": plan.get("plan_adi"),
                    "plan_turu": plan.get("plan_turu"),
                    "olcek": plan.get("olcek"),
                    "onay_durumu": plan.get("onay_durumu"),
                })
        signals = []
        for plan in plans:
            name = str(plan.get("plan_adi") or "")
            if any(kw in name.lower() for kw in keywords):
                signals.append(name)
        function_name = str(fields.get("plan_fonksiyon") or "")
        if any(kw in function_name.lower() for kw in keywords):
            signals.append(function_name)
        return {
            "status": "available",
            "is_expansion_area": bool(signals),
            "upper_scale_plans": upper_scale,
            "expansion_signals": signals,
            "plan_count": len(plans),
        }

    def _load_transaction_density(
        self,
        lat: float | None,
        lon: float | None,
        radius_km: float = 3.0,
        years: int = 5,
        analiz_tip: int | None = None,
        max_points: int = 4000,
    ) -> dict[str, Any] | None:
        """TKGM ambarından hedef koordinatın çevresindeki alım-satım yoğunluğunu döndürür.

        Parsel bazlı gerçek tapu işlem sayıları (canlı değil, önceden ambara alınmış).
        """
        if not self.density_database.exists():
            return None
        if lat is None or lon is None:
            return {"status": "coordinate_required", "radius_km": radius_km,
                    "message": "Satış yoğunluğu için parsel koordinatı gerekli."}
        # Enlem/boylam kutusuyla ön eleme (indeks kullanır), sonra haversine.
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
        uri = f"file:{self.density_database}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tkgm_alim_satim_yogunlugu'"
            ).fetchone()
            if not exists:
                return None
            connection.row_factory = sqlite3.Row
            # Tip/yıl meta bilgisini KÜÇÜK kapsama tablosundan al (büyük tabloyu
            # taramamak için). İstenen tip yoksa en çok kayıtlı tipi seç.
            where_tip = "WHERE analiz_tip=?" if analiz_tip is not None else ""
            params_tip = (analiz_tip,) if analiz_tip is not None else ()
            meta = connection.execute(
                f"""
                SELECT analiz_tip, analiz_tip_ad, MAX(yil) AS my
                FROM tkgm_analiz_kapsama {where_tip}
                GROUP BY analiz_tip
                ORDER BY SUM(nokta_sayisi) DESC LIMIT 1
                """,
                params_tip,
            ).fetchone()
            if not meta or meta["my"] is None:
                return {"status": "unavailable", "radius_km": radius_km}
            analiz_tip = meta["analiz_tip"]
            tip_label = meta["analiz_tip_ad"]
            max_year = meta["my"]
            min_year = max_year - years + 1
            # SQLite bırakılırsa PRIMARY KEY (analiz_tip) ile neredeyse tüm tabloyu
            # tarar (~3 sn). Enlem/boylam kutusu çok seçicidir; mekânsal indeksi
            # zorlayınca sorgu ~150 ms'ye iner. İndeks yoksa ipucunu atla.
            has_bbox_index = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_astim_bbox'"
            ).fetchone()
            index_hint = "INDEXED BY idx_astim_bbox" if has_bbox_index else ""
            rows = connection.execute(
                f"""
                SELECT parsel_id, enlem, boylam, sayi, yil
                FROM tkgm_alim_satim_yogunlugu {index_hint}
                WHERE enlem BETWEEN ? AND ? AND boylam BETWEEN ? AND ?
                  AND analiz_tip=? AND yil BETWEEN ? AND ?
                """,
                (lat - dlat, lat + dlat, lon - dlon, lon + dlon,
                 analiz_tip, min_year, max_year),
            ).fetchall()
        by_year: dict[int, int] = {}
        parcels: dict[int, dict[str, Any]] = {}
        total_tx = 0
        for row in rows:
            distance = haversine_km(lat, lon, row["enlem"], row["boylam"])
            if distance > radius_km:
                continue
            sayi = int(row["sayi"] or 0)
            total_tx += sayi
            by_year[row["yil"]] = by_year.get(row["yil"], 0) + sayi
            pid = row["parsel_id"]
            bucket = parcels.get(pid)
            if bucket is None:
                parcels[pid] = {
                    "parcel_id": pid, "lat": row["enlem"], "lon": row["boylam"],
                    "count": sayi, "distance_km": round(distance, 3),
                }
            else:
                bucket["count"] += sayi
        # Harita yükü için nokta listesini sınırla (işlem sayısına göre en yoğunlar).
        points = sorted(parcels.values(), key=lambda p: -p["count"])
        return {
            "status": "available" if parcels else "no_records_in_radius",
            "source": "TKGM MEGSİS (ambar)",
            "radius_km": radius_km,
            "analiz_tip": analiz_tip,
            "analiz_tip_ad": tip_label,
            "year_start": min_year,
            "year_end": max_year,
            "total_transactions": total_tx,
            "unique_parcels": len(parcels),
            "by_year": [{"year": y, "transactions": by_year[y]} for y in sorted(by_year)],
            "points_truncated": len(points) > max_points,
            "points": points[:max_points],
        }

    @staticmethod
    def _recency_score(value: Any) -> float:
        if not value:
            return 0.35
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            return 0.35
        days = max((date.today() - parsed).days, 0)
        return max(0.2, 1 - days / 730)

    def _select_comparables(
        self,
        request: dict[str, Any],
        submitted_request: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str, int]:
        province = normalize_name(request.get("il"))
        district = normalize_name(request.get("ilce"))
        neighbourhood = normalize_name(request.get("mahalle"))
        target_area = optional_float(request.get("alan_m2"))
        lat = optional_float(request.get("lat"))
        lon = optional_float(request.get("lon"))
        target_block = str(request.get("ada") or "").strip()
        target_parcel = str(request.get("parsel") or "").strip()
        submitted_block = str((submitted_request or {}).get("ada") or "").strip()
        submitted_parcel = str((submitted_request or {}).get("parsel") or "").strip()

        # 3 km emsal araması hedef parselin koordinatına dayanır. Koordinat yoksa
        # yaklaşık/geniş alan taraması yapmak yerine açık bir durum döneriz.
        if lat is None or lon is None:
            return [], "koordinat_gerekli", 0

        rows = self._load_land_listings(request.get("il"), center=(lat, lon), radius_km=1.0)
        enriched: list[dict[str, Any]] = []
        for row in rows:
            if province and normalize_name(row.get("il")) != province:
                continue
            identity_match = bool(
                (
                    target_block
                    and target_parcel
                    and str(row.get("ada_no") or "").strip() == target_block
                    and str(row.get("parsel_no") or "").strip() == target_parcel
                )
                or (
                    submitted_block
                    and submitted_parcel
                    and str(row.get("ada_no") or "").strip() == submitted_block
                    and str(row.get("parsel_no") or "").strip() == submitted_parcel
                )
            )
            same_identity = identity_match and (
                not district or normalize_name(row.get("ilce")) == district
            )
            same_coordinate_and_area = bool(
                target_area
                and None not in (lat, lon, row.get("enlem"), row.get("boylam"))
                and abs(float(row["enlem"]) - lat) < 0.00001
                and abs(float(row["boylam"]) - lon) < 0.00001
                and abs(float(row["m2"]) - target_area) / target_area < 0.001
            )
            if same_identity or same_coordinate_and_area:
                continue
            row_lat = optional_float(row.get("enlem"))
            row_lon = optional_float(row.get("boylam"))
            # Koordinatı olmayan ilanlar 1 km sonucuna alınmaz.
            if row_lat is None or row_lon is None:
                continue
            distance = haversine_km(lat, lon, row_lat, row_lon)
            # Yalnızca hedef koordinatın 1 km yarıçapı içindeki emsaller.
            if distance > 1.0:
                continue
            item = dict(row)
            item["distance_km"] = distance
            # Aykırı fiyat işaretlemesi yapılmaz; 1 km içindeki tüm ilanlar döner.
            item["is_price_outlier"] = False
            item["same_district"] = bool(
                district and normalize_name(row.get("ilce")) == district
            )
            item["same_neighbourhood"] = bool(
                neighbourhood and normalize_name(row.get("mahalle")) == neighbourhood
            )
            enriched.append(item)

        for item in enriched:
            area = float(item["m2"])
            area_similarity = min(area, target_area) / max(area, target_area) if target_area else 0.5
            distance_score = max(0.0, 1 - item["distance_km"] / 1)
            location_score = 1.0 if item["same_neighbourhood"] else (0.7 if item["same_district"] else 0.35)
            recency_score = self._recency_score(item.get("ilan_tarihi"))
            item["similarity_score"] = round(
                100 * (0.35 * location_score + 0.30 * area_similarity + 0.25 * distance_score + 0.10 * recency_score),
                1,
            )
        enriched.sort(key=lambda item: (-item["similarity_score"], str(item["ilan_id"])))
        return enriched, "1_km_yaricap", 0

    def _factor_evidence(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        chosen = {
            "shape": request.get("sekil") or "unknown",
            "road": request.get("yol") or "unknown",
            "water": request.get("su") or "unknown",
            "ground": request.get("zemin") or "unknown",
            "growth": request.get("gelisim") or "unknown",
            "zoning": request.get("imar") or "unknown",
        }
        factors = []
        for group, value in chosen.items():
            safe_value = value if value in self.FACTORS[group] else "unknown"
            label = self.FACTORS[group][safe_value]
            factors.append(
                {
                    "factor": group,
                    "value": safe_value,
                    "label": label,
                    "evidence": "user_input" if safe_value != "unknown" else "missing",
                }
            )
        return factors

    @staticmethod
    def _example_project(area_m2: float, zoning: dict[str, Any]) -> dict[str, Any] | None:
        if zoning.get("status") != "verified_at_source":
            return None
        fields = zoning.get("fields") or {}
        kaks = optional_float(fields.get("kaks_emsal"))
        taks = optional_float(fields.get("taks"))
        if not kaks or not taks or kaks <= 0 or taks <= 0:
            return None
        footprint = area_m2 * taks
        gross = area_m2 * kaks
        calculated_floors = max(1, math.ceil(gross / footprint))
        stated_floors = optional_float(fields.get("kat_adedi"))
        return {
            "status": "indicative_from_verified_parameters",
            "parcel_area_m2": round(area_m2, 2),
            "max_footprint_m2": round(footprint, 2),
            "total_gross_construction_m2": round(gross, 2),
            "open_area_m2": round(max(area_m2 - footprint, 0), 2),
            "indicative_floor_count": int(stated_floors or calculated_floors),
            "kaks": kaks,
            "taks": taks,
            "warning": "Örnek proje ruhsat veya kazanılmış imar hakkı değildir; plan notları ve çekme mesafeleri ayrıca doğrulanmalıdır.",
        }

    def analyze(self, request: dict[str, Any], live_parcel: dict[str, Any] | None = None) -> dict[str, Any]:
        submitted_request = dict(request)
        request = dict(request)
        parcel = (live_parcel or {}).get("parcel") or {}
        evidence_warnings: list[str] = list((live_parcel or {}).get("warnings") or [])
        submitted_area = optional_float(request.get("alan_m2"))
        live_area = optional_float(parcel.get("area_m2"))
        if submitted_area and live_area and abs(submitted_area - live_area) / live_area > 0.05:
            evidence_warnings.append(
                f"Girilen {submitted_area:g} m² ile canlı kadastro alanı {live_area:g} m² uyuşmadı; analizde canlı kadastro alanı kullanıldı."
            )
        for request_key, parcel_key in (
            ("il", "province"),
            ("ilce", "district"),
            ("mahalle", "neighbourhood"),
            ("ada", "block"),
            ("parsel", "parcel"),
            ("alan_m2", "area_m2"),
            ("lat", "lat"),
            ("lon", "lon"),
        ):
            if parcel.get(parcel_key) not in (None, ""):
                request[request_key] = parcel[parcel_key]
        live_zoning = (live_parcel or {}).get("zoning", {})
        live_zoning_status = live_zoning.get("status")
        live_plan_function = (live_zoning.get("fields") or {}).get("plan_fonksiyon")
        if live_zoning_status in {"verified_at_source", "partial_zoning_verified"} and live_plan_function:
            request["imar"] = "zoned"

        area_m2 = optional_float(request.get("alan_m2"))
        if area_m2 is None or not 20 <= area_m2 <= 100_000_000:
            raise LandAnalysisError("Arsa alanı 20–100.000.000 m² arasında olmalıdır.")
        if not str(request.get("il") or "").strip():
            raise LandAnalysisError("İl bilgisi gereklidir.")

        timings: dict[str, float] = {}
        t_start = time.perf_counter()

        def timed(stage: str, fn, *args):
            s0 = time.perf_counter()
            try:
                return fn(*args)
            finally:
                timings[stage] = round((time.perf_counter() - s0) * 1000, 1)

        market_index = timed("market_index", self._load_market_index, request)
        if market_index:
            history = timed(
                "market_index_history", self._load_market_index_history,
                request.get("kategori") or "arsa", market_index,
            )
            market_index["history"] = history
            observed = [p for p in history if not p["is_projection"]]
            projected = [p for p in history if p["is_projection"]]
            market_index["history_summary"] = {
                "total_points": len(history),
                "observed_count": len(observed),
                "projection_count": len(projected),
                "first_period": history[0]["period"] if history else None,
                "last_observed_period": observed[-1]["period"] if observed else None,
                "last_period": history[-1]["period"] if history else None,
            }
        area_segments = timed(
            "area_segments", self._load_area_segments,
            request.get("kategori") or "arsa", market_index, optional_float(request.get("alan_m2")),
        )
        declared_zoning = timed("declared_zoning", self._declared_zoning, request)
        transaction_density = timed(
            "transaction_density", self._load_transaction_density,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        if self._geographic_engine is None:
            self._geographic_engine = GeographicContextEngine(self.geographic_database)
        geographic_context = timed(
            "geographic_context", self._geographic_engine.analyze,
            parcel.get("geometry"),
            optional_float(request.get("lat")),
            optional_float(request.get("lon")),
        )
        # Parsel geometrisi varsa şekil (düzenlilik) hesaplanır ve şekil faktörünü
        # besler; kullanıcı girişine gerek kalmaz.
        parcel_shape = analyze_shape(parcel.get("geometry"))
        if parcel_shape:
            request["sekil"] = parcel_shape["classification"]
        comparables, scope, flagged_outlier_count = timed(
            "comparables", self._select_comparables, request, submitted_request
        )
        factors = self._factor_evidence(request)
        district_development = timed("district_development", self._load_district_development, request)
        neighborhood_building = timed("neighborhood_building", self._load_neighborhood_building, request)
        investment_profile = timed("investment_profile", self._load_investment_profile, request)
        # Ulusal bölge profili: demografi, yıllık satış, fiyat özeti, kırılımlar, POI (açık sınıf).
        bolge_profili = timed("bolge_profili", self._bolge_engine.profil, request)
        yakin_noktalar = timed(
            "yakin_noktalar", self._nearby_engine.analyze,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        okullar = timed(
            "okullar", self._school_engine.analyze,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        yurtlar = timed(
            "yurtlar", self._dorm_engine.analyze,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        universiteler = timed(
            "universiteler", self._university_engine.analyze,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        pazarlar = timed(
            "pazarlar", self._market_engine.analyze,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        # TKGM kadastro mahallesi ADNKS idari mahallesiyle farklı olabilir → kullanıcının yazdığı mahalle de denenir
        tuik_bolge = timed(
            "tuik_bolge", self._tuik_engine.analyze,
            {**request, "mahalle_adaylari": [request.get("mahalle"), submitted_request.get("mahalle")]},
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        hava_kalitesi = timed(
            "hava_kalitesi", self._env_engine.hava,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        resmi_gazete = timed(
            "resmi_gazete", self._env_engine.resmi_gazete,
            (tuik_bolge or {}).get("il") or request.get("il"), (tuik_bolge or {}).get("ilce") or request.get("ilce"),
            [request.get("mahalle"), submitted_request.get("mahalle")],
        )
        deprem_senaryo = timed(
            "deprem_senaryo", self._env_engine.deprem_senaryo,
            (tuik_bolge or {}).get("il") or request.get("il"), (tuik_bolge or {}).get("ilce") or request.get("ilce"),
            [request.get("mahalle"), submitted_request.get("mahalle")],
        )
        gurultu = timed(
            "gurultu", self._env_engine.gurultu,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        trafik = timed(
            "trafik", self._env_engine.trafik,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        arazi = timed(
            "arazi", self._terrain_engine.analyze,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        zemin_jeoloji = timed(
            "zemin_jeoloji", self._geology_engine.formation_at,
            optional_float(request.get("lat")), optional_float(request.get("lon")),
        )
        afet_vekili = timed("afet_vekili", afet_vekilleri, arazi, geographic_context, zemin_jeoloji)
        timings["toplam"] = round((time.perf_counter() - t_start) * 1000, 1)
        if live_zoning_status in {"verified_at_source", "partial_zoning_verified"} and live_plan_function:
            for factor in factors:
                if factor["factor"] == "zoning":
                    factor["evidence"] = "verified_live_source"
                    factor["label"] = "İmar durumu canlı kaynakta doğrulandı"
        # İstatistikler aykırı işaretli emsaller hariç tutularak hesaplanır;
        # aykırılar listede kalır ama medyan/aralık gibi robust özetleri bozmaz.
        robust_prices = [
            float(item["birim_m2_fiyat"]) for item in comparables
            if not item.get("is_price_outlier")
        ] or [float(item["birim_m2_fiyat"]) for item in comparables]
        analysis_key = "|".join(
            str(request.get(key) or "")
            for key in ("il", "ilce", "mahalle", "ada", "parsel", "alan_m2", "lat", "lon")
        )
        analysis_id = hashlib.sha256(analysis_key.encode("utf-8")).hexdigest()[:16]

        if scope == "koordinat_gerekli":
            comparable_status = "coordinate_required"
        elif comparables:
            comparable_status = "available"
        else:
            comparable_status = "insufficient_comparables"
        comparable_statistics = {
            "status": comparable_status,
            "selected_count": len(comparables),
            "scope": scope,
            "median_unit_price": round(statistics.median(robust_prices)) if comparables else None,
            "weighted_median_unit_price": round(weighted_median([
                (float(item["birim_m2_fiyat"]), max(item["similarity_score"], 1))
                for item in comparables
                if not item.get("is_price_outlier")
            ] or [
                (float(item["birim_m2_fiyat"]), max(item["similarity_score"], 1))
                for item in comparables
            ])) if comparables else None,
            "low_unit_price": round(percentile(robust_prices, 0.25)) if comparables else None,
            "high_unit_price": round(percentile(robust_prices, 0.75)) if comparables else None,
            "flagged_outlier_count": flagged_outlier_count,
        }
        # Endeks ile ilan emsalleri iki bağımsız kanıt ailesidir. Bedel algoritması
        # ayrıca tanımlanana kadar burada ağırlık, harmanlama veya fiyat üretmeyiz.
        valuation: dict[str, Any] = {
            "status": "algorithm_definition_pending",
            "estimated_unit_price": None,
            "estimated_total_price": None,
            "low_total_price": None,
            "high_total_price": None,
            "confidence_score": None,
            "confidence_label": "hesaplanmadi",
            "inputs_combined": False,
            "factor_multiplier": None,
        }

        public_comparables = [
            {
                "listing_id": item["ilan_id"],
                "title": item["baslik"],
                "province": item["il"],
                "district": item["ilce"],
                "neighbourhood": item["mahalle"],
                "price_tl": item["fiyat_tl"],
                "area_m2": item["m2"],
                "unit_price_tl": item["birim_m2_fiyat"],
                "distance_km": round(item["distance_km"], 2) if item["distance_km"] is not None else None,
                "lat": item["enlem"],
                "lon": item["boylam"],
                "similarity_score": item["similarity_score"],
                "listing_date": item["ilan_tarihi"],
                "zoning_declaration": item["imar_durumu"],
                "is_price_outlier": bool(item.get("is_price_outlier")),
            }
            for item in comparables
        ]
        zoning = (live_parcel or {}).get("zoning") or {"status": "not_queried", "fields": {}}
        return {
            "status": "success",
            "analysis_id": analysis_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "submitted_input": submitted_request,
            "input": request,
            "parcel_evidence": live_parcel,
            "comparable_selection": {
                "scope": scope,
                "radius_km": 1.0,
                "selected_count": len(public_comparables),
                "flagged_outlier_count": flagged_outlier_count,
                "method": "within_1km_all_listings",
                "message": (
                    "1 km emsal araması için parsel koordinatı gerekli."
                    if scope == "koordinat_gerekli" else None
                ),
                "comparables": public_comparables,
            },
            "market_index": market_index,
            "area_price_segments": {
                "status": "available" if area_segments else "unavailable",
                "region": area_segments[0]["region"] if area_segments else None,
                "target_area_m2": area_m2,
                "segments": area_segments,
            },
            "declared_zoning": declared_zoning,
            "transaction_density": transaction_density,
            "geographic_context": geographic_context,
            "parcel_shape": parcel_shape,
            "city_expansion": self._city_expansion(zoning),
            "district_development": district_development,
            "neighborhood_building": neighborhood_building,
            "investment_profile": investment_profile,
            "bolge_profili": bolge_profili,
            "zemin_jeoloji": zemin_jeoloji,
            "arazi": arazi,
            "yakin_noktalar": yakin_noktalar,
            "okullar": okullar,
            "yurtlar": yurtlar,
            "universiteler": universiteler,
            "pazarlar": pazarlar,
            "tuik_bolge": tuik_bolge,
            "hava_kalitesi": hava_kalitesi,
            "resmi_gazete": resmi_gazete,
            "deprem_senaryo": deprem_senaryo,
            "gurultu": gurultu,
            "trafik_saatlik": trafik,
            "afet_vekili": afet_vekili,
            "comparable_statistics": comparable_statistics,
            "factors": factors,
            "valuation": valuation,
            "example_project": self._example_project(area_m2, zoning),
            "data_policy": {
                "public_source_links": False,
                "internal_provenance_retained": True,
                "restricted_context_used": False,
            },
            "timings_ms": timings,
            "warnings": [
                *evidence_warnings,
                "Sonuç istatistiksel ön değerlemedir; ekspertiz veya kesin satış fiyatı değildir.",
                "İlan fiyatı gerçekleşmiş satış fiyatı değildir.",
                "Endeks ve ilan emsalleri birbirinden bağımsız sunulur; otomatik bedel üretiminde birleştirilmez.",
                "Bedel algoritması tanımlanana kadar parsel faktörleri fiyat üzerinde uygulanmaz.",
            ],
        }
