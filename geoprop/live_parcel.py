"""Canlı TKGM/imar sonucunu ürünün kararlı veri sözleşmesine çevirir."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class ParcelQueryError(ValueError):
    """Geçersiz parsel sorgusu."""


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ParcelQueryError("Enlem, boylam ve sayısal alanlar geçerli olmalıdır.") from exc


class LiveParcelGateway:
    """Mevcut ParselImarToplayici'yı salt-okunur bir ürün portu olarak kullanır."""

    def __init__(self, process_parcel: Callable[..., dict | None]):
        self._process_parcel = process_parcel

    def query(self, params: dict[str, Any]) -> dict[str, Any]:
        lat = _optional_float(params.get("lat"))
        lon = _optional_float(params.get("lon"))
        mahalle_id = params.get("mahalle_id") or params.get("mahalleId")
        ada = str(params.get("ada") or "").strip() or None
        parsel = str(params.get("parsel") or "").strip() or None

        if lat is not None and not 35.0 <= lat <= 43.0:
            raise ParcelQueryError("Enlem Türkiye sınırları içinde olmalıdır.")
        if lon is not None and not 25.0 <= lon <= 46.0:
            raise ParcelQueryError("Boylam Türkiye sınırları içinde olmalıdır.")
        if not ((mahalle_id and ada and parsel) or (lat is not None and lon is not None)):
            raise ParcelQueryError(
                "Koordinat çifti veya mahalle ID + ada + parsel birlikte gereklidir."
            )
        if mahalle_id not in (None, ""):
            try:
                mahalle_id = int(mahalle_id)
            except (TypeError, ValueError) as exc:
                raise ParcelQueryError("TKGM mahalle ID tam sayı olmalıdır.") from exc

        queried_at = datetime.now(timezone.utc).isoformat()
        result = self._process_parcel(
            mahalle_id=mahalle_id,
            ada=ada,
            parsel=parsel,
            il=str(params.get("il") or "").strip(),
            ilce=str(params.get("ilce") or "").strip(),
            mahalle=str(params.get("mahalle") or "").strip(),
            lat=lat,
            lon=lon,
            persist=False,
        )

        if not result:
            return {
                "status": "not_found",
                "queried_at": queried_at,
                "cadastre": {
                    "status": "not_found",
                    "source_name": "TKGM MEGSİS",
                    "source_url": None,
                },
                "zoning": {
                    "status": "not_queried",
                    "source_name": None,
                    "source_url": None,
                },
                "parcel": None,
                "warnings": ["Koordinat veya ada/parsel için tescilli parsel bulunamadı."],
            }

        parcel = result.get("parsel") or {}
        zoning = result.get("imar") or {}
        zoning_data_status = zoning.get("veri_durumu")
        fields = {
            key: zoning.get(key)
            for key in (
                "imar_durumu",
                "plan_fonksiyon",
                "kaks_emsal",
                "taks",
                "gabari",
                "kat_adedi",
                "yapi_nizami",
                "on_bahce",
                "yan_bahce",
                "plan_adi",
                "plan_turu",
                "pin_tucbs_no",
                "onay_tarihi",
                "yururluk_tarihi",
                "plan_sureci",
                "plan_kayit_tarihi",
                "plan_olcegi",
                "dogal_sit",
                "havaalani_mania",
                "hazine_durumu",
            )
        }
        has_kaks_taks = bool(fields.get("kaks_emsal") and fields.get("taks"))
        has_function_data = any(fields.get(key) not in (None, "") for key in (
            "plan_fonksiyon", "gabari", "kat_adedi", "on_bahce", "yan_bahce"
        ))
        if has_kaks_taks:
            public_zoning_status = "verified_at_source"
        elif has_function_data:
            public_zoning_status = "partial_zoning_verified"
        elif zoning_data_status == "plan_kapsami_dogrulandi":
            public_zoning_status = "plan_coverage_verified"
        elif zoning_data_status == "plan_bulunamadi":
            public_zoning_status = "not_found"
        else:
            public_zoning_status = "source_unavailable"
        warnings: list[str] = []
        if public_zoning_status == "partial_zoning_verified":
            warnings.append(
                "E-Plan fonksiyon ve mevcut yapılaşma alanlarını doğruladı; kaynakta sıfır/boş olan KAKS ve TAKS tahminle doldurulmadı."
            )
        elif public_zoning_status == "plan_coverage_verified":
            warnings.append(
                "E-Plan plan kesişimini doğruladı; parsel bazlı yapılaşma koşulları bulunamadığı için KAKS ve TAKS boş bırakıldı."
            )
        elif public_zoning_status == "not_found":
            warnings.append(
                "E-Plan'da bu noktayı kesen aktif plan bulunamadı; KAKS ve TAKS tahminle doldurulmadı."
            )
        elif public_zoning_status == "source_unavailable":
            warnings.append(
                "İmar kaynağına erişilemedi; KAKS, TAKS ve plan alanları tahminle doldurulmadı."
            )

        return {
            "status": "success",
            "queried_at": queried_at,
            "cadastre": {
                "status": "verified_at_source",
                "source_name": "TKGM MEGSİS",
                "source_url": None,
            },
            "zoning": {
                "status": public_zoning_status,
                "source_name": zoning.get("kaynak") if zoning.get("success") else None,
                "source_url": None,
                "fields": fields,
                "plans": zoning.get("planlar") or [],
                "function_layers": zoning.get("fonksiyon_katmanlari") or [],
                "plan_changes": zoning.get("aski_degisiklikleri") or [],
            },
            "parcel": {
                "province": parcel.get("il"),
                "district": parcel.get("ilce"),
                "neighbourhood": parcel.get("mahalle"),
                "neighbourhood_id": parcel.get("mahalle_id"),
                "block": parcel.get("ada_no"),
                "parcel": parcel.get("parsel_no"),
                "area_m2": parcel.get("alan_m2"),
                "area_raw": parcel.get("alan_raw"),
                "quality": parcel.get("nitelik"),
                "ground_title_status": parcel.get("zemin_durumu"),
                "map_sheet": parcel.get("pafta"),
                "locality": parcel.get("mevkii"),
                "lat": parcel.get("enlem"),
                "lon": parcel.get("boylam"),
                "geometry": parcel.get("geometry"),
            },
            "confidence": result.get("veri_guveni") or {},
            "warnings": warnings,
        }
