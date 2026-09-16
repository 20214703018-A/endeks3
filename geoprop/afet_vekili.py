"""Afet vekil göstergeleri — resmî taşkın/heyelan haritaları (DSİ, AFAD/TUCBS) API anahtarı gerektirdiğinden ambardaki
mevcut verilerden türetilmiş DÜRÜST vekiller: 30 m DEM (arazi), dere yatağı mesafesi (coğrafi katmanlar), jeolojik birim (MTA).

- Taşkın vekili: en yakın dere yatağı mesafesi + parselin 300 m pencere en düşük kotuna göre yüksekliği (HAND benzeri kaba).
    yüksek: dere ≤ 150 m ve kot farkı ≤ 3 m · orta: dere ≤ 400 m ve kot farkı ≤ 6 m (veya göl/baraj ≤ 300 m) · düşük: diğer · uzak: 2 km'de dere yok
- Heyelan vekili: çevre 300 m ortalama/maks eğim + (varsa) MTA birim yaşı/türü (alüvyon, kil, flîş, volkanik tüf → duyarlı).
    yüksek: ort eğim ≥ %25 veya maks ≥ %40 ve duyarlı birim · orta: ort ≥ %15 · düşük: diğer
Bunlar resmî tehlike sınıfı DEĞİLDİR; kart "vekil gösterge — resmî harita değil" der. Girdi eksikse "hesaplanamadı".
"""
from __future__ import annotations

import re
from typing import Any

DUYARLI_BIRIM = re.compile(r"alüvyon|aluvyon|kil|marn|fliş|flis|tüf|tuf|yamaç molozu|moloz|şeyl|seyl|çamurtaşı|kiltaşı|volkanik", re.I)


def afet_vekilleri(arazi: dict | None, cografya: dict | None, jeoloji: dict | None) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "available", "resmi_degil": True,
                           "not": "Vekil göstergeler: DSİ taşkın tehlike ve AFAD heyelan duyarlılık haritaları TUCBS API anahtarı gerektirdiğinden "
                                  "30 m DEM + dere yatağı mesafesi + jeolojik birimden türetilmiştir. Resmî tehlike sınıfı değildir; "
                                  "imar durumu/E-Plan ve belediye afet raporuyla doğrulanmalıdır."}
    # ---- taşkın ----
    dere = (cografya or {}).get("en_yakin_dere_m"); gol = (cografya or {}).get("en_yakin_gol_baraj_m")
    cevre = (arazi or {}).get("cevre_300m") or {}
    merkez = (arazi or {}).get("merkez_rakim_m")
    kot_min = ((cevre.get("rakim_m") or {}).get("min"))
    kot_farki = round(merkez - kot_min, 1) if merkez is not None and kot_min is not None else None
    if (arazi or {}).get("status") != "available" or cografya is None:
        out["taskin"] = {"sinif": "hesaplanamadı", "dere_m": dere, "kot_farki_m": kot_farki}
    else:
        if dere is None:
            sinif, gerekce = "uzak", "2 km içinde dere/akarsu yatağı yok"
        elif dere <= 150 and kot_farki is not None and kot_farki <= 3:
            sinif, gerekce = "yüksek", f"dere {dere} m, çevre tabanına göre yalnız {kot_farki} m yüksek"
        elif (dere <= 400 and kot_farki is not None and kot_farki <= 6) or (gol is not None and gol <= 300):
            sinif, gerekce = "orta", f"dere {dere} m, kot farkı {kot_farki} m" + (f"; göl/baraj {gol} m" if gol is not None and gol <= 300 else "")
        else:
            sinif, gerekce = "düşük", f"dere {dere} m, kot farkı {kot_farki} m"
        out["taskin"] = {"sinif": sinif, "gerekce": gerekce, "dere_m": dere, "gol_baraj_m": gol, "kot_farki_m": kot_farki}
    # ---- heyelan ----
    egim = (cevre.get("egim_pct") or {})
    ort, mx = egim.get("ort"), egim.get("max")
    j = jeoloji or {}
    birim = " ".join(str(j.get(k) or "") for k in ("aciklama", "yas", "simge")).strip() if j.get("status") == "available" else ""
    # Kuvaterner (alüvyon vb.) birimler zemin/heyelan açısından duyarlı sayılır
    duyarli = (bool(DUYARLI_BIRIM.search(birim)) or bool(j.get("kuvaterner"))) if birim else None
    if ort is None:
        out["heyelan"] = {"sinif": "hesaplanamadı"}
    else:
        if ort >= 25 or (mx is not None and mx >= 40 and duyarli):
            sinif = "yüksek"
        elif ort >= 15 or (mx is not None and mx >= 30):
            sinif = "orta"
        else:
            sinif = "düşük"
        out["heyelan"] = {"sinif": sinif, "cevre_egim_ort_pct": ort, "cevre_egim_max_pct": mx, "duyarli_birim": duyarli, "birim": birim[:120] if birim else None,
                          "gerekce": f"çevre 300 m eğim ort %{ort}, maks %{mx}" + (f"; birim duyarlı ({birim[:40]})" if duyarli else "")}
    return out
