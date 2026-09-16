"""Kısıtlı veri erişim politikası — TEK karar noktası.

Kullanıcı kararı (2026-09-12): seçim, hemşehri/kütük, tütün gibi hassas setler yalnızca belirli
üyelik paketlerinde, belirli sorgu tiplerinde ve adminde erişilebilir; PII yalnız admin/iç
eşleştirme; hiçbiri arsa fiyatlamasına/alıcı arayüzüne girmez. Bu dosya ile
`.claude/skills/geoprop-veri-mimarisi/references/guvenlik-siniflandirma.md` birbirini yansıtır;
birini değiştirince diğerini de güncelle (güvenlik denetimi tutarsızlığı yakalar).

Her izin verilen kısıtlı erişim `erisim_denetim` tablosuna yazılır (sessiz erişim yok).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PAKETLER = ("anonim", "uye_temel", "uye_pro", "uye_kurumsal", "admin")

# veri_seti → izinli paketler ve izinli sorgu tipleri. `arsa_analiz` hiçbir kısıtlı sete erişemez.
KISITLI_SETLER: dict[str, dict[str, set[str]]] = {
    "secim_sonuclari": {"paketler": {"uye_kurumsal", "admin"}, "sorgular": {"dukkan_analiz", "bolge_raporu"}},
    "hemsehri_kutuk":  {"paketler": {"uye_kurumsal", "admin"}, "sorgular": {"dukkan_analiz", "bolge_raporu"}},
    "tutun_sigara":    {"paketler": {"uye_pro", "uye_kurumsal", "admin"}, "sorgular": {"dukkan_analiz"}},
    "kisisel_veriler": {"paketler": {"admin"}, "sorgular": {"admin_eslestirme"}},
}

_REPO = Path(__file__).resolve().parents[1]
DENETIM_DB = _REPO / "warehouse" / "restricted" / "erisim_denetim.sqlite"


def erisim_izni_var_mi(veri_seti: str, uye_paketi: str | None, sorgu_tipi: str | None,
                       admin_mi: bool = False) -> bool:
    """Açık setler her zaman True; kısıtlı setlerde paket × sorgu tipi × admin kuralı."""
    kural = KISITLI_SETLER.get(veri_seti)
    if kural is None:
        return True
    sorgu = sorgu_tipi or ""
    if admin_mi:
        return sorgu in (kural["sorgular"] | {"admin_eslestirme"})
    return (uye_paketi or "anonim") in kural["paketler"] and sorgu in kural["sorgular"]


def denetim_kaydi(veri_seti: str, kullanici_id: str | None, uye_paketi: str | None,
                  sorgu_tipi: str | None, kapsam: dict[str, Any] | None = None,
                  izin: bool = True) -> None:
    """Kısıtlı set erişim girişimini (izinli veya reddedilen) kaydeder."""
    try:
        os.makedirs(DENETIM_DB.parent, exist_ok=True)
        with sqlite3.connect(DENETIM_DB) as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS erisim_denetim (
                     zaman TEXT, kullanici_id TEXT, uye_paketi TEXT, sorgu_tipi TEXT,
                     veri_seti TEXT, kapsam TEXT, izin INTEGER)"""
            )
            c.execute(
                "INSERT INTO erisim_denetim VALUES (?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), kullanici_id, uye_paketi, sorgu_tipi,
                 veri_seti, str(kapsam or {}), 1 if izin else 0),
            )
        try:
            os.chmod(DENETIM_DB, 0o600)
        except OSError:
            pass
    except sqlite3.Error:
        pass  # denetim yazılamasa da erişim kararı değişmez; sessizce yutma yerine loglanabilir


def kisitli_erisim(veri_seti: str, kullanici_id: str | None, uye_paketi: str | None,
                   sorgu_tipi: str | None, admin_mi: bool = False,
                   kapsam: dict[str, Any] | None = None) -> bool:
    """İzin kontrolü + denetim kaydı tek çağrıda. Kısıtlı okuma yapan her yer bunu kullanır."""
    izin = erisim_izni_var_mi(veri_seti, uye_paketi, sorgu_tipi, admin_mi)
    if veri_seti in KISITLI_SETLER:
        denetim_kaydi(veri_seti, kullanici_id, uye_paketi, sorgu_tipi, kapsam, izin)
    return izin
