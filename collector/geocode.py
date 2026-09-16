"""Paylaşılan adres → koordinat çözücü (ücretsiz OSM tabanlı; anahtarsız).

Sıra: Photon (komoot) → Nominatim (yedek; 429 görürse 10 dk devre dışı). Tek süreçte çalıştırın:
Nominatim politikası ≤1 istek/sn, Photon için de aynı nezaket uygulanır (iki toplayıcı aynı anda
koşunca 429 yedik — 2026-09-13).

Adres ayrıştırma (Türk adres kalıpları): 'X Mahallesi', 'Y Sokak/Sk./Caddesi/Cad./Bulvarı', 'Z Köyü'.
Deneme sırası ve etiket (hassasiyet düşerek):
  1. sokak + ilçe + il           → "sokak düzeyi (yaklaşık)"
  2. mahalle + ilçe + il (place) → "mahalle merkezi (yaklaşık)"
  3. köy + ilçe + il (place)     → "köy merkezi (yaklaşık)"
  4. kurum adı + ilçe + il       → "ad eşleşmesi (yaklaşık)"
Doğrulama: sonucun idari zinciri il adını içermeli (Photon: state; Nominatim: display_name), ilçe varsa
ilçe adı da (Photon: county/city/district alanlarından biri; il-merkez ilçeleri muaf). Uydurma yok:
doğrulanamayan sonuç atılır → None.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402

UA = "GEOPROP/1.0 (adres kodlama; iletisim: heytteknoloji@gmail.com)"
PHOTON = "https://photon.komoot.io/api/"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

_MAH_RX = re.compile(r"([A-ZÇĞİÖŞÜa-zçğıöşü0-9\.\- ]{2,50}?)\s*(?:Mahallesi|Mah\.|Mah\b|MAHALLESİ|MAH\.|MAHALLESI|Mh\.|MH\.)", re.I)
_SOK_RX = re.compile(r"([A-ZÇĞİÖŞÜa-zçğıöşü0-9\.\- ]{2,50}?)\s*(?:Sokak|Sokağı|Sk\.|Sok\.|SOKAK|SK\.|SOK\.|Caddesi|Cad\.|Cd\.|CADDESİ|CAD\.|Bulvarı|Bulv\.|Blv\.|BULVARI)", re.I)
_KOY_RX = re.compile(r"([A-ZÇĞİÖŞÜa-zçğıöşü\- ]{2,40}?)\s*(?:Köyü|KÖYÜ|Koyu|Beldesi|BELDESİ)")

_last_call = 0.0
_nominatim_blocked_until = 0.0


def tr_title(text: str | None) -> str:
    words = []
    for w in (text or "").split():
        low = w.replace("I", "ı").replace("İ", "i").lower()
        words.append(low[:1].replace("i", "İ").replace("ı", "I").upper() + low[1:])
    return " ".join(words)


def _throttle() -> None:
    global _last_call
    wait = 1.05 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _photon(q: str, place_only: bool) -> list[dict[str, Any]]:
    _throttle()
    params = {"q": q, "limit": 5, "lang": "en"}
    if place_only:
        params["osm_tag"] = "place"
    try:
        with urllib.request.urlopen(urllib.request.Request(PHOTON + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA}), timeout=20) as r:
            feats = json.loads(r.read().decode("utf-8")).get("features", [])
    except Exception:
        return []
    out = []
    for f in feats:
        p = f.get("properties", {}); lon, lat = f["geometry"]["coordinates"]
        admin = " ".join(str(p.get(k) or "") for k in ("state", "county", "city", "district", "locality", "name"))
        out.append({"lat": lat, "lon": lon, "admin": admin, "country": p.get("countrycode")})
    return out


def _nominatim(q: str) -> list[dict[str, Any]]:
    global _nominatim_blocked_until
    if time.time() < _nominatim_blocked_until:
        return []
    _throttle()
    try:
        url = NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 3, "countrycodes": "tr"})
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _nominatim_blocked_until = time.time() + 600
        return []
    except Exception:
        return []
    return [{"lat": float(x["lat"]), "lon": float(x["lon"]), "admin": x.get("display_name", ""), "country": "TR"} for x in res]


def _dogrula(cands: list[dict[str, Any]], il: str, ilce: str | None) -> tuple[float, float] | None:
    il_n, ilce_n = normalize_name(il), normalize_name(ilce or "")
    for c in cands:
        if c.get("country") and c["country"].upper() != "TR":
            continue
        a = normalize_name(c["admin"])
        if il_n in a and (not ilce_n or ilce_n == "merkez" or ilce_n in a):
            return (c["lat"], c["lon"])
    return None


def adres_kodla(adres: str | None, il: str, ilce: str | None, kurum_adi: str | None = None) -> tuple[float, float, str] | None:
    """(lat, lon, hassasiyet_etiketi) | None."""
    adres = adres or ""
    ilce_t, il_t = tr_title(ilce), tr_title(il)
    denemeler: list[tuple[str, bool, str]] = []
    if (m := _SOK_RX.search(adres)):
        sok = re.sub(r"\s+", " ", m.group(0)).strip()
        denemeler.append((f"{sok} {ilce_t} {il_t}".strip(), False, "adres: sokak düzeyi (yaklaşık)"))
    if (m := _MAH_RX.search(adres)):
        mah = tr_title(m.group(1))
        denemeler.append((f"{mah} {ilce_t} {il_t}".strip(), True, "adres: mahalle merkezi (yaklaşık)"))
        denemeler.append((f"{mah} Mahallesi {ilce_t} {il_t}".strip(), False, "adres: mahalle merkezi (yaklaşık)"))
    if (m := _KOY_RX.search(adres)) or (kurum_adi and (m := _KOY_RX.search(kurum_adi))):
        denemeler.append((f"{tr_title(m.group(1))} {ilce_t} {il_t}".strip(), True, "adres: köy merkezi (yaklaşık)"))
    if kurum_adi:
        denemeler.append((f"{kurum_adi} {ilce_t} {il_t}".strip(), False, "ad eşleşmesi (yaklaşık)"))
    for q, place_only, etiket in denemeler:
        hit = _dogrula(_photon(q, place_only), il, ilce)
        if hit:
            return (hit[0], hit[1], etiket)
    for q, _, etiket in denemeler[:2]:
        hit = _dogrula(_nominatim(q), il, ilce)
        if hit:
            return (hit[0], hit[1], etiket)
    return None
