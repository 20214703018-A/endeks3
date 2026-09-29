"""Sayı denetimi: dil modelinin cevabındaki her sayı, araç sonuçlarında (veya kullanıcının sorusunda) bulunmalı.

Deterministik kural:
  • Cevaptan sayılar Türkçe yazım kuralıyla çıkarılır (1.234,5 → 1234.5; %7,03 → 7.03).
  • Kaynak metinlerden (araç çıktıları: Türkçe markdown + JSON) tüm sayılar çıkarılır.
  • Cevaptaki d ondalıklı bir sayı, kaynaktaki bir sayının d ondalığa yuvarlanmış haliyle eşitse GEÇER.
  • 0–20 arası tam sayılar (sıra, adet ifadeleri: "ilk 5") ve kullanıcının kendi yazdığı sayılar serbesttir.
Geçmeyen sayılar 'uydurma olabilir' diye raporlanır.
"""
from __future__ import annotations

import re

# 1.234.567,89 | 1234,5 | 1234 | 12.5 (JSON/İngilizce) — harf/rakam içindeki (hash, geo_id) sayılar alınmaz
_TR = re.compile(r"(?<![\w.,/-])-?\d{1,3}(?:\.\d{3})+(?:,\d+)?(?![\w])|(?<![\w.,/-])-?\d+(?:,\d+)?(?![\w.,]\d)(?![A-Za-z_])")
_EN = re.compile(r"(?<![\w.,/-])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w])")


def _tr_deger(tok: str) -> tuple[float, int]:
    if "," in tok:
        tam, ond = tok.split(",", 1)
        return float(tam.replace(".", "") + "." + ond), len(ond)
    return float(tok.replace(".", "")), 0


def cevap_sayilari(metin: str) -> list[tuple[str, float, int]]:
    out = []
    for m in _TR.finditer(metin):
        tok = m.group(0)
        try:
            v, d = _tr_deger(tok)
        except ValueError:
            continue
        out.append((tok, v, d))
    return out


def kaynak_sayilari(*metinler: str) -> list[float]:
    s: set[float] = set()
    for metin in metinler:
        for tok, v, _ in cevap_sayilari(metin):
            s.add(v)
        for m in _EN.finditer(metin):
            try:
                s.add(float(m.group(0)))
            except ValueError:
                pass
    return sorted(s)


def denetle(cevap: str, kaynaklar: list[str], soru: str = "") -> list[str]:
    """Kaynaklarda karşılığı olmayan sayıların listesi (boşsa cevap temiz)."""
    havuz = kaynak_sayilari(*kaynaklar, soru)
    kotu = []
    for tok, v, d in cevap_sayilari(cevap):
        if d == 0 and 0 <= v <= 20:
            continue
        tol = 0.5 * 10 ** (-d) + 1e-9
        if not any(abs(v - t) <= tol or abs(v - round(t, d)) <= 1e-9 for t in havuz):
            kotu.append(tok)
    return sorted(set(kotu), key=kotu.index)
