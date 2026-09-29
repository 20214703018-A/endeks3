"""Deterministik zaman serisi projeksiyonu — rastgelelik yok, parametre araması sabit ızgara üzerinde.

Yöntem (sade anlatım):
  1. Birkaç basit, açıklanabilir model: son dönem eğilimi (12/36 adım), sönümlü trend (Holt), düz (değişim yok),
     aylık sayım serilerinde mevsimsel modeller.
  2. Geriye dönük sınama (rolling-origin backtest): serinin geçmişinde her başlangıç noktasından ileriye tahmin yapılıp
     gerçekleşenle karşılaştırılır. Model hatası = ortalama mutlak log hata (≈ ortalama % hata).
  3. Topluluk (ensemble): modeller, sınama hatalarının tersiyle ağırlıklandırılır (hatası az olan daha çok söz sahibi).
  4. Belirsizlik aralığı: topluluğun GEÇMİŞTEKİ gerçek hatalarından (ufuk başına %10–%90 yüzdelikleri); yeterli örnek yoksa
     1 adımlık hata × √ufuk ile genişletilir. Yani aralık varsayım değil, ölçülmüş geçmiş hatadır.
Seri pozitif olmalı (fiyat, nüfus, adet); log uzayında çalışılır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Z80 = 1.2815515655446004   # standart normal %90 yüzdeliği (80% aralık)


# ------------------------------------------------------------------------------------------ dönem yardımcıları
def donem_indeks(d: str) -> tuple[int, str]:
    """'2026-08' → (ay sayacı, 'ay'); '2025' → (yıl, 'yil'); '2026-Q2' → (çeyrek sayacı, 'ceyrek')."""
    if len(d) == 4 and d.isdigit():
        return int(d), "yil"
    if "-Q" in d:
        y, q = d.split("-Q")
        return int(y) * 4 + int(q) - 1, "ceyrek"
    y, m = d[:4], d[5:7]
    return int(y) * 12 + int(m) - 1, "ay"


def indeks_donem(i: int, tur: str) -> str:
    if tur == "yil":
        return str(i)
    if tur == "ceyrek":
        return f"{i // 4}-Q{i % 4 + 1}"
    return f"{i // 12}-{i % 12 + 1:02d}"


def duzenli_seri(noktalar: list[tuple[str, float]]) -> tuple[list[str], list[float], int, str]:
    """Dönemleri sıralar, adım (ay/çeyrek/yıl, BDDK gibi 3 aylık seriler dahil) bulur; sondaki kesintisiz parçayı döndürür."""
    nk = sorted((donem_indeks(d)[0], d, v) for d, v in noktalar if v is not None and v > 0)
    if len(nk) < 2:
        return [d for _, d, _ in nk], [v for _, _, v in nk], 1, donem_indeks(nk[0][1])[1] if nk else "ay"
    tur = donem_indeks(nk[-1][1])[1]
    farklar = [b[0] - a[0] for a, b in zip(nk, nk[1:])]
    adim = max(set(farklar), key=lambda f: (farklar.count(f), -f))
    bas = len(nk) - 1
    while bas > 0 and nk[bas][0] - nk[bas - 1][0] == adim:
        bas -= 1
    parca = nk[bas:]
    return [d for _, d, _ in parca], [v for _, _, v in parca], adim, tur


# ------------------------------------------------------------------------------------------ modeller (log uzayı)
def _drift(z: list[float], h: int, pencere: int) -> list[float]:
    w = min(pencere, len(z) - 1)
    d = (z[-1] - z[-1 - w]) / w if w > 0 else 0.0
    return [z[-1] + d * k for k in range(1, h + 1)]


def _duz(z: list[float], h: int) -> list[float]:
    return [z[-1]] * h


_HOLT_IZGARA = [(a, b, p) for a in (0.2, 0.4, 0.6, 0.8) for b in (0.05, 0.1, 0.2) for p in (0.8, 0.9, 0.98)]


def _holt_sonumlu(z: list[float], h: int) -> list[float]:
    """Sönümlü Holt; (α, β, φ) sabit ızgarada tek adım karesel hatayı en aza indiren (eşitlikte ızgaradaki ilk)."""
    if len(z) < 4:
        return _drift(z, h, 12)
    en = None
    for a, b, p in _HOLT_IZGARA:
        l, t, sse = z[0], z[1] - z[0], 0.0
        for y in z[1:]:
            f = l + p * t
            sse += (y - f) ** 2
            l_ = a * y + (1 - a) * (l + p * t)
            t = b * (l_ - l) + (1 - b) * p * t
            l = l_
        if en is None or sse < en[0] - 1e-15:
            en = (sse, l, t, p)
    _, l, t, p = en
    out, carp = [], 0.0
    for k in range(1, h + 1):
        carp += p ** k
        out.append(l + carp * t)
    return out


def _mevsimsel_naif(z: list[float], h: int, m: int) -> list[float]:
    """Geçen yılın aynı dönemi × (son m dönem / önceki m dönem) büyümesi (log)."""
    if len(z) < 2 * m:
        return _drift(z, h, m)
    g = (sum(z[-m:]) - sum(z[-2 * m:-m])) / m / m   # dönem başına ortalama log büyüme
    return [z[-m + ((k - 1) % m)] + g * (m * ((k - 1) // m + 1)) for k in range(1, h + 1)]


def _mevsimsel_drift(z: list[float], h: int, m: int) -> list[float]:
    """Son 3 yılın ortalama mevsim etkisi çıkarılır, mevsimsiz seride 24 dönem eğilimi, sonra mevsim etkisi geri eklenir."""
    n = len(z)
    if n < 3 * m:
        return _mevsimsel_naif(z, h, m)
    son = z[-3 * m:]
    ort = sum(son) / len(son)
    etki = [sum(son[i::m]) / len(son[i::m]) - ort for i in range(m)]
    faz0 = (n - 3 * m) % m
    mevsimsiz = [z[i] - etki[(i - (n - 3 * m) + faz0) % m] if i >= n - 3 * m else z[i] for i in range(n)]
    trend = _drift(mevsimsiz[-3 * m:], h, 2 * m)
    return [trend[k - 1] + etki[(3 * m + k - 1 + faz0) % m] for k in range(1, h + 1)]


def modeller(tur: str, adim: int, mevsimsel: bool) -> dict:
    per_yil = {"ay": 12, "ceyrek": 4, "yil": 1}[tur] // (adim if tur == "ay" else 1) or 1
    k1, k3 = max(1, per_yil), max(2, 3 * per_yil)
    m = {
        f"egilim_son_{k1}": lambda z, h: _drift(z, h, k1),
        f"egilim_son_{k3}": lambda z, h: _drift(z, h, k3),
        "sonumlu_trend": _holt_sonumlu,
        "duz": _duz,
    }
    if tur == "yil":
        m = {"egilim_son_5": lambda z, h: _drift(z, h, 5), "egilim_son_10": lambda z, h: _drift(z, h, 10),
             "sonumlu_trend": _holt_sonumlu, "duz": _duz}
    if mevsimsel and per_yil >= 4:
        m["mevsimsel_naif"] = lambda z, h: _mevsimsel_naif(z, h, per_yil)
        m["mevsimsel_egilim"] = lambda z, h: _mevsimsel_drift(z, h, per_yil)
    return m


# ------------------------------------------------------------------------------------------ sınama + topluluk
@dataclass
class Tahmin:
    donemler: list[str]
    orta: list[float]
    alt80: list[float]
    ust80: list[float]
    agirliklar: dict[str, float]
    model_hatalari: dict[str, float]            # ortalama mutlak log hata (≈ %)
    topluluk_hatasi: dict[int, float]           # ufuk → ortalama mutlak % hata (geçmiş sınama)
    sinama_sayisi: int
    uyarilar: list[str] = field(default_factory=list)
    son_donem: str = ""
    son_deger: float = 0.0


def tahmin_et(noktalar: list[tuple[str, float]], ufuk: int, mevsimsel: bool = False, min_egitim: int | None = None) -> Tahmin:
    donemler, y, adim, tur = duzenli_seri(noktalar)
    uy = []
    if len(y) < len(noktalar):
        uy.append(f"Seride boşluk/sıfır değer var; yalnız son kesintisiz {len(y)} dönem kullanıldı.")
    if len(y) < 6:
        raise ValueError(f"Projeksiyon için en az 6 kesintisiz dönem gerekir (mevcut {len(y)}).")
    z = [math.log(v) for v in y]
    ms = modeller(tur, adim, mevsimsel)
    n = len(z)
    min_egitim = min_egitim or max(6, min(n // 2, 36 // adim if tur == "ay" else n // 2))
    H = min(ufuk, n - min_egitim) if n - min_egitim >= 1 else 1
    hatalar: dict[str, list[tuple[int, float]]] = {k: [] for k in ms}
    for o in range(min_egitim, n - 1):
        hh = min(H, n - o)
        for ad, f in ms.items():
            p = f(z[:o], hh)
            for k in range(hh):
                hatalar[ad].append((k + 1, p[k] - z[o + k]))
    mae = {ad: (sum(abs(e) for _, e in es) / len(es) if es else float("inf")) for ad, es in hatalar.items()}
    ters = {ad: 1.0 / max(v, 1e-6) for ad, v in mae.items() if math.isfinite(v)}
    top = sum(ters.values())
    w = {ad: ters[ad] / top for ad in sorted(ters)}
    # topluluğun geçmiş hataları (aynı sınama noktalarında)
    top_hata: dict[int, list[float]] = {}
    for o in range(min_egitim, n - 1):
        hh = min(H, n - o)
        tahminler = {ad: ms[ad](z[:o], hh) for ad in w}
        for k in range(hh):
            e = sum(w[ad] * tahminler[ad][k] for ad in w) - z[o + k]
            top_hata.setdefault(k + 1, []).append(e)
    tam = {ad: ms[ad](z, ufuk) for ad in w}
    orta_z = [sum(w[ad] * tam[ad][k] for ad in w) for k in range(ufuk)]
    s1 = _sd(top_hata.get(1, [0.0]))
    alt, ust = [], []
    for k in range(1, ufuk + 1):
        es = top_hata.get(k, [])
        if len(es) >= 8:
            q10, q90 = _yuzdelik(es, 0.10), _yuzdelik(es, 0.90)
            # e = tahmin − gerçek → gerçek = tahmin − e
            alt.append(math.exp(orta_z[k - 1] - q90)); ust.append(math.exp(orta_z[k - 1] - q10))
        else:
            s = s1 * math.sqrt(k) if s1 > 0 else 0.02 * math.sqrt(k)
            alt.append(math.exp(orta_z[k - 1] - Z80 * s)); ust.append(math.exp(orta_z[k - 1] + Z80 * s))
    # aralık ufukla daralamaz: log uzaklıkları kümülatif en büyükle tek yönlü genişletilir
    ga, gu = 0.0, 0.0
    for k in range(ufuk):
        ga = max(ga, orta_z[k] - math.log(alt[k]))
        gu = max(gu, math.log(ust[k]) - orta_z[k])
        alt[k], ust[k] = math.exp(orta_z[k] - ga), math.exp(orta_z[k] + gu)
    if ufuk > H:
        uy.append(f"Ufuk ({ufuk}) geçmiş sınamanın ölçebildiği en uzun ufku ({H}) aşıyor; ötesindeki aralıklar √ufuk ile genişletildi.")
    son_i, _ = donem_indeks(donemler[-1])
    gelecek = [indeks_donem(son_i + adim * k, tur) for k in range(1, ufuk + 1)]
    return Tahmin(donemler=gelecek, orta=[math.exp(v) for v in orta_z], alt80=alt, ust80=ust, agirliklar=w,
                  model_hatalari={k: 100 * v for k, v in mae.items()},
                  topluluk_hatasi={k: 100 * sum(abs(e) for e in es) / len(es) for k, es in sorted(top_hata.items())},
                  sinama_sayisi=len(top_hata.get(1, [])), uyarilar=uy, son_donem=donemler[-1], son_deger=y[-1])


def _sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _yuzdelik(xs: list[float], q: float) -> float:
    s = sorted(xs)
    i = q * (len(s) - 1)
    a, b = math.floor(i), math.ceil(i)
    return s[a] + (s[b] - s[a]) * (i - a)
