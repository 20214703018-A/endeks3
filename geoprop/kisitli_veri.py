"""Kısıtlı veri motoru — seçim, hemşehri/kütük ve tütün setlerinin TEK okuma noktası.

Kurallar (bkz. erisim_politikasi.KISITLI_SETLER ve skill referansı guvenlik-siniflandirma.md):
  - Her okuma `kisitli_erisim()` ile paket × sorgu tipi × admin kararından geçer ve denetim
    tablosuna yazılır. Reddedilen istek veri sızdırmaz: yalnız {"status": "restricted"} döner.
  - Bu motor `LandAnalysisEngine.analyze()` tarafından ÇAĞRILMAZ; arsa fiyatlamasına ve alıcı
    arayüzüne hiçbir kısıtlı alan girmez. Sunucu, ayrı uç noktadan ve kimlik bağlamıyla çağırır.
  - Bağlantı salt-okunur; dosya yoksa/tablo yoksa None (test ortamı kırılmaz).
  - Kişi düzeyi kayıt yoktur: seçim = ilçe/mahalle toplamı ve kazanan; hemşehri = kütük ili
    dağılımı (ilk N). Ham satır listesi dışarı verilmez.

Konum çözümü ürün DB'sindeki referans hub'ından (BolgeIstatistikEngine.resolve_ids) alınır;
kısıtlı DB aynı id evrenini ve `mahalle_norm` sütununu taşır.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .bolge_istatistik import BolgeIstatistikEngine
from .erisim_politikasi import kisitli_erisim

_REPO = Path(__file__).resolve().parents[1]
DEFAULT_RESTRICTED_DB = _REPO / "warehouse" / "restricted" / "kisitli_istatistik.sqlite"
DEFAULT_INTELLIGENCE_DB = _REPO / "collector" / "data" / "turkiye_makro_ve_mikro_istihbarat.sqlite"


class RestrictedDataEngine:
    def __init__(self, bolge_engine: BolgeIstatistikEngine,
                 restricted_database: str | Path = DEFAULT_RESTRICTED_DB,
                 intelligence_database: str | Path = DEFAULT_INTELLIGENCE_DB):
        self.bolge = bolge_engine
        self.restricted_database = Path(restricted_database).expanduser().resolve()
        self.intelligence_database = Path(intelligence_database).expanduser().resolve()

    # ---------- altyapı ----------
    @staticmethod
    def _open(path: Path) -> sqlite3.Connection | None:
        if not path.exists():
            return None
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _has(c: sqlite3.Connection, table: str) -> bool:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    @staticmethod
    def _scope_clauses(ids: dict[str, Any]) -> list[tuple[str, str, tuple]]:
        out = []
        if ids.get("county_id") and ids.get("mahalle_norm"):
            out.append(("mahalle", "seviye='mahalle' AND county_id=? AND mahalle_norm=?", (ids["county_id"], ids["mahalle_norm"])))
        if ids.get("county_id"):
            out.append(("ilce", "seviye='ilce' AND county_id=? AND district_id=0", (ids["county_id"],)))
        if ids.get("city_id"):
            out.append(("il", "seviye='il' AND city_id=? AND county_id=0", (ids["city_id"],)))
        return out

    def _gate(self, veri_seti: str, kimlik: dict[str, Any], kapsam: dict[str, Any]) -> bool:
        return kisitli_erisim(
            veri_seti,
            kullanici_id=kimlik.get("kullanici_id"),
            uye_paketi=kimlik.get("uye_paketi"),
            sorgu_tipi=kimlik.get("sorgu_tipi"),
            admin_mi=bool(kimlik.get("admin_mi")),
            kapsam=kapsam,
        )

    # ---------- setler ----------
    def secim_profili(self, ids: dict[str, Any], kimlik: dict[str, Any]) -> dict[str, Any]:
        """Seçim bazında kazanan + katılım; en dar bulunan düzey (mahalle→ilçe→il)."""
        if not self._gate("secim_sonuclari", kimlik, {k: ids.get(k) for k in ("city_id", "county_id", "mahalle_norm")}):
            return {"status": "restricted"}
        c = self._open(self.restricted_database)
        if c is None or not self._has(c, "secim_sonuclari"):
            return {"status": "unavailable"}
        try:
            for scope, where, p in self._scope_clauses(ids):
                rows = c.execute(
                    f"SELECT secim_kodu, secim_adi, sandik_sayisi, kayitli_secmen, kullanilan_oy, gecerli_oy, kazanan_parti "
                    f"FROM secim_sonuclari WHERE {where} ORDER BY secim_kodu", p).fetchall()
                if rows:
                    secimler = []
                    for r in rows:
                        kayitli, kullanilan = r["kayitli_secmen"], r["kullanilan_oy"]
                        secimler.append({
                            "kod": r["secim_kodu"], "ad": r["secim_adi"], "kazanan": r["kazanan_parti"],
                            "kayitli_secmen": kayitli, "kullanilan_oy": kullanilan, "gecerli_oy": r["gecerli_oy"],
                            "katilim_orani": round(100 * kullanilan / kayitli, 1) if kayitli and kullanilan else None,
                        })
                    return {"status": "available", "scope": scope, "secimler": secimler, "veri_sinifi": "kisitli"}
            return {"status": "unavailable"}
        finally:
            c.close()

    def hemsehri_profili(self, ids: dict[str, Any], kimlik: dict[str, Any], ilk_n: int = 10) -> dict[str, Any]:
        """Nüfus kütüğü iline göre dağılım: ilk N kütük ili ve payları."""
        if not self._gate("hemsehri_kutuk", kimlik, {k: ids.get(k) for k in ("city_id", "county_id", "mahalle_norm")}):
            return {"status": "restricted"}
        c = self._open(self.restricted_database)
        if c is None or not self._has(c, "hemsehri_kutuk"):
            return {"status": "unavailable"}
        try:
            for scope, where, p in self._scope_clauses(ids):
                rows = c.execute(
                    f"SELECT kutuk_ili, kisi_sayisi FROM hemsehri_kutuk WHERE {where} AND kisi_sayisi IS NOT NULL "
                    f"ORDER BY kisi_sayisi DESC", p).fetchall()
                if rows:
                    toplam = sum(float(r["kisi_sayisi"] or 0) for r in rows)
                    return {
                        "status": "available", "scope": scope, "toplam_kisi": toplam, "kutuk_ili_sayisi": len(rows),
                        "ilk": [{"kutuk_ili": r["kutuk_ili"], "kisi": r["kisi_sayisi"],
                                 "pay": round(100 * float(r["kisi_sayisi"]) / toplam, 1) if toplam else None}
                                for r in rows[:ilk_n]],
                        "veri_sinifi": "kisitli",
                    }
            return {"status": "unavailable"}
        finally:
            c.close()

    def tutun_profili(self, il: str | None, kimlik: dict[str, Any]) -> dict[str, Any]:
        """TÜİK tütün/sigara bölge istatistiği (8 bölge satırı + Türkiye geneli)."""
        if not self._gate("tutun_sigara", kimlik, {"il": il}):
            return {"status": "restricted"}
        c = self._open(self.intelligence_database)
        if c is None or not self._has(c, "tuik_tutun_ve_sigara_istatistikleri"):
            return {"status": "unavailable"}
        try:
            keys = ("bolge_adi", "erkek_gunluk_sigara_orani", "kadin_gunluk_sigara_orani", "toplam_sigara_orani",
                    "tuketim_seviyesi", "veri_yili", "veri_donemi")
            rows = [dict(r) for r in c.execute("SELECT * FROM tuik_tutun_ve_sigara_istatistikleri")]
            bolge = next((r for r in rows if il and il.casefold() in (r.get("bolge_adi") or "").casefold()), None)
            ulusal = next((r for r in rows if "Türkiye Geneli" in (r.get("bolge_adi") or "")), None)
            if not bolge and not ulusal:
                return {"status": "unavailable"}
            return {"status": "available", "veri_sinifi": "kisitli",
                    "bolge": {k: bolge.get(k) for k in keys} if bolge else None,
                    "ulusal": {k: ulusal.get(k) for k in keys} if ulusal else None}
        finally:
            c.close()

    # ---------- birleşik ----------
    def profil(self, request: dict[str, Any], kimlik: dict[str, Any]) -> dict[str, Any]:
        """Kimlik bağlamına göre erişilebilen kısıtlı setleri döner; her set ayrı ayrı gate'lenir."""
        ids = self.bolge.resolve_ids(request.get("il"), request.get("ilce"), request.get("mahalle"))
        if ids["city_id"] is None:
            return {"status": "location_unresolved", "ids": ids}
        return {
            "status": "available",
            "ids": ids,
            "sorgu_tipi": kimlik.get("sorgu_tipi"),
            "secim": self.secim_profili(ids, kimlik),
            "hemsehri": self.hemsehri_profili(ids, kimlik),
            "tutun": self.tutun_profili(ids["matched"].get("il") or request.get("il"), kimlik),
        }
