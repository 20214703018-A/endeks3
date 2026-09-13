"""Paylaşılan veri yardımcıları — isim normalizasyonu, sayı çevirme, mesafe.

`land_analysis.py` bunları yeniden dışa aktarır; collector'lar ve diğer motorlar doğrudan
buradan da içe aktarabilir (döngüsel import olmaz).
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any


class LandAnalysisError(ValueError):
    """Analiz girdisi veya veri kaynağı hatası."""


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("ı", "i")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def normalize_neighbourhood(value: Any) -> str:
    normalized = normalize_name(value)
    for suffix in (" mahallesi", " mahalle", " mh"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].strip()
    return normalized


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


# Ad sütunu → türetilmiş normalize sütun (il→il_norm, ilce→ilce_norm, mahalle→mahalle_norm).
NORM_COLUMNS: dict[str, tuple[str, Any]] = {
    "il": ("il_norm", normalize_name),
    "ilce": ("ilce_norm", normalize_name),
    "mahalle": ("mahalle_norm", normalize_neighbourhood),
}


def ensure_normalized_name_columns(connection, table: str, columns=("il", "ilce", "mahalle")) -> list[str]:
    """Ad sütunları için önceden normalize edilmiş, indeksli `*_norm` sütunları ekler.

    WHERE içinde `geoprop_normalize(il)=?` gibi fonksiyon çağrısı indeks kullanamaz ve tabloyu
    satır satır Python'a taşır (63K ilanda ~100 ms/sorgu). Türetilmiş sütun + bileşik indeks aynı
    sorguyu <1 ms'ye indirir. Tek geçişli UPDATE; tekrar çağrılabilir (yalnız NULL'ları doldurur).
    Döner: tabloda var olan/eklenen norm sütun adları.
    """
    existing = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    added: list[str] = []
    for source in columns:
        if source not in existing:
            continue
        norm_col, fn = NORM_COLUMNS[source]
        if norm_col not in existing:
            connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{norm_col}" TEXT')
        connection.create_function(f"geoprop_norm_{source}", 1, lambda v, f=fn: f(v) if v is not None else None,
                                   deterministic=True)
        connection.execute(f'UPDATE "{table}" SET "{norm_col}"=geoprop_norm_{source}("{source}") '
                           f'WHERE "{norm_col}" IS NULL AND "{source}" IS NOT NULL')
        added.append(norm_col)
    if added:
        connection.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_norm" ON "{table}" ({", ".join(added)})')
        connection.commit()
    return added
