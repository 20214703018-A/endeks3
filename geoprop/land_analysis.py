"""Açıklanabilir arsa emsal seçimi ve ön değerleme motoru."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import statistics
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


MODEL_VERSION = "land-comparable-v1.0.0"


class LandAnalysisError(ValueError):
    """Analiz girdisi veya veri kaynağı hatası."""


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("ı", "i")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise LandAnalysisError("Sayısal alanlardan biri geçersiz.") from exc
    if not math.isfinite(number):
        raise LandAnalysisError("Sayısal alanlar sonlu olmalıdır.")
    return number


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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


class LandAnalysisEngine:
    """Yerel ilan gözlemlerinden bağlantısız, açıklanabilir arsa ön değerlemesi üretir."""

    FACTORS = {
        "shape": {
            "regular": (1.03, "Düzenli parsel şekli"),
            "near_regular": (1.01, "Düzenliye yakın parsel şekli"),
            "irregular": (0.94, "Düzensiz parsel şekli"),
            "very_irregular": (0.88, "Çok düzensiz parsel şekli"),
            "unknown": (1.0, "Parsel şekli bilinmiyor"),
        },
        "road": {
            "cadastral_frontage": (1.07, "Kadastral yola cephe"),
            "road_access": (1.03, "Yol erişimi mevcut"),
            "limited": (0.94, "Sınırlı yol erişimi"),
            "none": (0.82, "Doğrudan yol erişimi yok"),
            "unknown": (1.0, "Yol durumu bilinmiyor"),
        },
        "water": {
            "connected": (1.02, "Su bağlantısı mevcut"),
            "nearby": (1.0, "Su altyapısı yakın"),
            "none": (0.96, "Su bağlantısı doğrulanmadı"),
            "unknown": (1.0, "Su durumu bilinmiyor"),
        },
        "ground": {
            "suitable": (1.03, "Zemin uygun olarak bildirildi"),
            "conditional": (0.95, "Zemin koşullu olarak bildirildi"),
            "risky": (0.82, "Zemin riski bildirildi"),
            "unknown": (1.0, "Zemin verisi bilinmiyor"),
        },
        "growth": {
            "toward_growth": (1.06, "Bildirilen gelişme yönünde"),
            "neutral": (1.0, "Gelişme yönü nötr"),
            "outside_growth": (0.96, "Bildirilen gelişme yönü dışında"),
            "unknown": (1.0, "Gelişme yönü bilinmiyor"),
        },
        "zoning": {
            "zoned": (1.08, "İmarlı olarak bildirildi"),
            "development": (1.04, "Gelişme alanı olarak bildirildi"),
            "agricultural": (0.93, "Tarım niteliği bildirildi"),
            "protected": (0.75, "Koruma kısıtı bildirildi"),
            "unknown": (1.0, "İmar durumu bilinmiyor"),
        },
    }

    def __init__(self, listing_database: str | Path):
        self.listing_database = Path(listing_database).expanduser().resolve()

    def _load_land_listings(self, province: Any = None) -> list[dict[str, Any]]:
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
            params: tuple[Any, ...] = ()
            normalized_province = normalize_name(province)
            if normalized_province:
                sql += " AND geoprop_normalize(il) = ?"
                params = (normalized_province,)
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

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

    @staticmethod
    def _remove_price_outliers(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        if len(rows) < 5:
            return rows, 0
        prices = [float(row["birim_m2_fiyat"]) for row in rows]
        median = statistics.median(prices)
        deviations = [abs(value - median) for value in prices]
        mad = statistics.median(deviations)
        tolerance = max(3 * 1.4826 * mad, median * 0.35)
        filtered = [
            row for row in rows
            if abs(float(row["birim_m2_fiyat"]) - median) <= tolerance
        ]
        return (filtered or rows), len(rows) - len(filtered)

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

        rows = self._load_land_listings(request.get("il"))
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
            item = dict(row)
            row_lat = optional_float(row.get("enlem"))
            row_lon = optional_float(row.get("boylam"))
            item["distance_km"] = (
                haversine_km(lat, lon, row_lat, row_lon)
                if None not in (lat, lon, row_lat, row_lon)
                else None
            )
            item["same_district"] = bool(
                district and normalize_name(row.get("ilce")) == district
            )
            item["same_neighbourhood"] = bool(
                neighbourhood and normalize_name(row.get("mahalle")) == neighbourhood
            )
            enriched.append(item)

        scopes = [
            ("mahalle_ve_5_km", lambda item: item["same_neighbourhood"] and (item["distance_km"] is None or item["distance_km"] <= 5)),
            ("ilce_ve_15_km", lambda item: item["same_district"] and (item["distance_km"] is None or item["distance_km"] <= 15)),
            ("ilce_geneli", lambda item: item["same_district"]),
            ("il_geneli", lambda item: True),
        ]
        selected: list[dict[str, Any]] = []
        selected_scope = "veri_yok"
        for scope, predicate in scopes:
            selected = [item for item in enriched if predicate(item)]
            selected_scope = scope
            if len(selected) >= 5:
                break

        selected, removed = self._remove_price_outliers(selected)
        for item in selected:
            area = float(item["m2"])
            area_similarity = min(area, target_area) / max(area, target_area) if target_area else 0.5
            distance_score = 0.45 if item["distance_km"] is None else max(0.0, 1 - item["distance_km"] / 25)
            location_score = 1.0 if item["same_neighbourhood"] else (0.7 if item["same_district"] else 0.35)
            recency_score = self._recency_score(item.get("ilan_tarihi"))
            item["similarity_score"] = round(
                100 * (0.35 * location_score + 0.30 * area_similarity + 0.25 * distance_score + 0.10 * recency_score),
                1,
            )
        selected.sort(key=lambda item: (-item["similarity_score"], item["ilan_id"]))
        return selected[:12], selected_scope, removed

    def _factor_adjustment(self, request: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
        chosen = {
            "shape": request.get("sekil") or "unknown",
            "road": request.get("yol") or "unknown",
            "water": request.get("su") or "unknown",
            "ground": request.get("zemin") or "unknown",
            "growth": request.get("gelisim") or "unknown",
            "zoning": request.get("imar") or "unknown",
        }
        multiplier = 1.0
        factors = []
        for group, value in chosen.items():
            safe_value = value if value in self.FACTORS[group] else "unknown"
            coefficient, label = self.FACTORS[group][safe_value]
            multiplier *= coefficient
            factors.append(
                {
                    "factor": group,
                    "value": safe_value,
                    "coefficient": coefficient,
                    "label": label,
                    "evidence": "user_input" if safe_value != "unknown" else "missing",
                }
            )
        # Birbirine bağımlı kullanıcı girdilerinin bileşik etkisinin aşırı
        # büyümesini önlemek için toplam ayarı ilk sürümde +/- %20 ile sınırla.
        return min(max(multiplier, 0.80), 1.20), factors

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

        comparables, scope, outlier_count = self._select_comparables(request, submitted_request)
        multiplier, factors = self._factor_adjustment(request)
        if live_zoning_status in {"verified_at_source", "partial_zoning_verified"} and live_plan_function:
            for factor in factors:
                if factor["factor"] == "zoning":
                    factor["evidence"] = "verified_live_source"
                    factor["label"] = "İmar durumu canlı kaynakta doğrulandı"
        unit_prices = [float(item["birim_m2_fiyat"]) for item in comparables]
        analysis_key = "|".join(
            str(request.get(key) or "")
            for key in ("il", "ilce", "mahalle", "ada", "parsel", "alan_m2", "lat", "lon")
        )
        analysis_id = hashlib.sha256(analysis_key.encode("utf-8")).hexdigest()[:16]

        valuation: dict[str, Any]
        if not comparables:
            valuation = {
                "status": "insufficient_comparables",
                "estimated_unit_price": None,
                "estimated_total_price": None,
                "low_total_price": None,
                "high_total_price": None,
                "confidence_score": 0,
                "confidence_label": "yetersiz_veri",
            }
        else:
            weighted = weighted_median(
                [
                    (float(item["birim_m2_fiyat"]), max(item["similarity_score"], 1))
                    for item in comparables
                ]
            )
            adjusted_unit = weighted * multiplier
            dispersion = (percentile(unit_prices, 0.75) - percentile(unit_prices, 0.25)) / max(statistics.median(unit_prices), 1)
            location_quality = sum(item["similarity_score"] for item in comparables) / len(comparables) / 100
            sample_quality = min(len(comparables) / 10, 1)
            scope_multiplier = {
                "mahalle_ve_5_km": 1.0,
                "ilce_ve_15_km": 0.90,
                "ilce_geneli": 0.80,
                "il_geneli": 0.65,
            }.get(scope, 0.50)
            confidence_score = round(
                100
                * (0.45 * sample_quality + 0.35 * location_quality + 0.20 * max(0, 1 - dispersion))
                * scope_multiplier
            )
            valuation = {
                "status": "statistical_pre_valuation",
                "estimated_unit_price": round(adjusted_unit),
                "estimated_total_price": round(adjusted_unit * area_m2),
                "low_total_price": round(percentile(unit_prices, 0.25) * multiplier * area_m2),
                "high_total_price": round(percentile(unit_prices, 0.75) * multiplier * area_m2),
                "confidence_score": confidence_score,
                "confidence_label": "yuksek" if confidence_score >= 75 else ("orta" if confidence_score >= 50 else "dusuk"),
                "raw_weighted_median_unit_price": round(weighted),
                "factor_multiplier": round(multiplier, 4),
                "dispersion_iqr_ratio": round(dispersion, 4),
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
                "selected_count": len(public_comparables),
                "removed_outlier_count": outlier_count,
                "method": "robust_mad_filter_then_weighted_median",
                "comparables": public_comparables,
            },
            "factors": factors,
            "valuation": valuation,
            "example_project": self._example_project(area_m2, zoning),
            "data_policy": {
                "public_source_links": False,
                "internal_provenance_retained": True,
                "restricted_context_used": False,
            },
            "warnings": [
                *evidence_warnings,
                "Sonuç istatistiksel ön değerlemedir; ekspertiz veya kesin satış fiyatı değildir.",
                "İlan fiyatı gerçekleşmiş satış fiyatı değildir.",
                "Bilinmeyen faktörler nötr katsayıyla ele alınmıştır.",
            ],
        }
