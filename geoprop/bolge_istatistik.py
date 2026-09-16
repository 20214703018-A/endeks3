"""Bölge istatistik motoru — ulusal CSV paketlerinden kurulan ambarı (bolge_istatistik.sqlite)
tek noktadan, id-anahtarlı ve indeks-destekli okur.

Mimari:
  - İdari referans hub'ı (ref_il / ref_ilce / ref_mahalle, ad_norm indeksli): isimden id'ye
    çözümleme WHERE'de fonksiyon çağırmadan (indeks kullanılır) yapılır; sonuç süreç içi
    önbelleklenir.
  - Her loader mahalle → ilçe → il düşüşüyle (scope) veri arar; hangi düzeyde bulunduğunu söyler.
  - Salt-okunur bağlantı (mode=ro), tablo yoksa None; testler geçici DB ile kırılmaz.
  - `profil()` tüm bölümleri tek sözlükte, loader başına süreyle döner.

Kısıtlı setler (seçim, hemşehri) BU motorda YOKTUR; `RestrictedDataEngine` ayrı dosyada,
erişim `erisim_politikasi.kisitli_erisim` ile denetlenir.
"""

from __future__ import annotations

import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from .veri_yardimcilari import normalize_name, normalize_neighbourhood, optional_float


class BolgeIstatistikEngine:
    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    # ---------- altyapı ----------
    def _conn(self) -> sqlite3.Connection | None:
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _has(c: sqlite3.Connection, table: str) -> bool:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    @lru_cache(maxsize=4096)
    def resolve_ids(self, il: str | None, ilce: str | None, mahalle: str | None) -> dict[str, Any]:
        """(il, ilçe, mahalle) adlarını referans hub'ında id'lere çevirir (indeksli, önbellekli)."""
        out: dict[str, Any] = {"city_id": None, "county_id": None, "district_id": None,
                               "mahalle_norm": None, "matched": {}}
        c = self._conn()
        if c is None or not self._has(c, "ref_il"):
            return out
        try:
            il_n = normalize_name(il)
            if il_n:
                r = c.execute("SELECT city_id, ad FROM ref_il WHERE ad_norm=? LIMIT 1", (il_n,)).fetchone()
                if r:
                    out["city_id"] = r["city_id"]; out["matched"]["il"] = r["ad"]
            ilce_n = normalize_name(ilce)
            if out["city_id"] is not None and ilce_n:
                r = c.execute("SELECT county_id, ad FROM ref_ilce WHERE city_id=? AND ad_norm=? LIMIT 1",
                              (out["city_id"], ilce_n)).fetchone()
                if r:
                    out["county_id"] = r["county_id"]; out["matched"]["ilce"] = r["ad"]
            mah_n = normalize_neighbourhood(mahalle)
            if out["county_id"] is not None and mah_n:
                # İstatistik tabloları mahalleyi (county_id, mahalle_norm) ile taşır; ref_mahalle'nin
                # küresel district_id'si yalnız eşleşme kanıtıdır, kapsam anahtarı değildir.
                out["mahalle_norm"] = mah_n
                r = c.execute("SELECT district_id, ad FROM ref_mahalle WHERE county_id=? AND ad_norm=? LIMIT 1",
                              (out["county_id"], mah_n)).fetchone()
                if r:
                    out["district_id"] = r["district_id"]; out["matched"]["mahalle"] = r["ad"]
        finally:
            c.close()
        return out

    def _fetch_scoped(self, c, table: str, ids: dict, extra: str = "", params: tuple = (),
                      many: bool = False, order: str = ""):
        """mahalle → ilçe → il sırasıyla ilk bulunan düzeyi döner: (rows|row, scope)."""
        if not self._has(c, table):
            return (None, None)
        attempts = []
        if ids.get("county_id") and ids.get("mahalle_norm"):
            attempts.append(("mahalle", "seviye='mahalle' AND county_id=? AND mahalle_norm=?",
                             (ids["county_id"], ids["mahalle_norm"])))
        if ids.get("county_id"):
            attempts.append(("ilce", "seviye='ilce' AND county_id=? AND district_id=0", (ids["county_id"],)))
        if ids.get("city_id"):
            attempts.append(("il", "seviye='il' AND city_id=? AND county_id=0", (ids["city_id"],)))
        for scope, where, p in attempts:
            sql = f'SELECT * FROM "{table}" WHERE {where}{(" AND " + extra) if extra else ""}{(" ORDER BY " + order) if order else ""}'
            rows = c.execute(sql, p + params).fetchall()
            if rows:
                return ([dict(r) for r in rows] if many else dict(rows[0]), scope)
        return (None, None)

    # ---------- loader'lar ----------
    def demografi(self, c, ids):
        row, scope = self._fetch_scoped(c, "demografi", ids)
        if not row:
            return None
        return {
            "status": "available", "scope": scope, "bolge": row.get("bolge_adi"),
            "nufus": row.get("nufus_toplam"), "nufus_erkek": row.get("nufus_erkek"), "nufus_kadin": row.get("nufus_kadin"),
            "hane_sayisi": row.get("hane_sayisi"), "ortalama_hane_geliri": row.get("ortalama_hane_geliri"),
            "ev_sahibi_orani": row.get("ev_sahibi_orani"), "kiraci_orani": row.get("kiraci_orani"),
            "ses": {k: row.get(f"ses_{k}_oran") for k in ("a_plus", "a", "b", "c", "d")},
            "egitim": {k: row.get(f"egitim_{k}_oran") for k in ("universite", "lise", "ortaokul", "ilkokul")},
            "yas": {k: row.get(f"yas_{k}_oran") for k in ("genc", "orta", "yasli")},
        }

    def yillik_satis(self, c, ids):
        rows, scope = self._fetch_scoped(c, "yillik_satis", ids, many=True, order="yil")
        if not rows:
            return None
        seri = [{"yil": int(r["yil"]) if r.get("yil") else None,
                 "konut": r.get("toplam_konut_satisi"), "ipotekli_konut": r.get("ipotekli_konut_satisi"),
                 "arsa": r.get("arsa_arazi_satisi"), "ipotekli_arsa": r.get("ipotekli_arsa_satisi")} for r in rows]
        return {"status": "available", "scope": scope, "seri": seri,
                "yil_araligi": [seri[0]["yil"], seri[-1]["yil"]] if seri else None}

    def fiyat_ozet(self, c, ids, kategori: str):
        row, scope = self._fetch_scoped(c, "fiyat_ozet", ids, extra="kategori=?", params=(kategori,), order="donem DESC")
        if not row:
            return None
        return {
            "status": "available", "scope": scope, "kategori": kategori, "donem": row.get("donem"),
            "satilik_m2": row.get("satilik_m2_fiyat"), "kiralik_m2": row.get("kiralik_m2_fiyat"),
            "ortalama_fiyat": row.get("ortalama_fiyat"), "amortisman_yil": row.get("amortisman_yil"),
            "brut_kira_getirisi": row.get("brut_kira_getirisi"), "ortalama_bina_yasi": row.get("ortalama_bina_yasi"),
            "satilik_kalma_gun": row.get("satilik_kalma_suresi_gun"), "kiralik_kalma_gun": row.get("kiralik_kalma_suresi_gun"),
            "ilan_sayisi": row.get("ilan_sayisi"), "yillik_degisim": row.get("yillik_fiyat_degisim"),
        }

    def dagilim_kirilim(self, c, ids, kategori: str = "konut"):
        rows, scope = self._fetch_scoped(c, "dagilim_kirilim", ids, extra="kategori=?", params=(kategori,), many=True)
        if not rows:
            return None
        gruplar: dict[str, list] = {}
        for r in rows:
            gruplar.setdefault(r.get("dagilim_turu") or "diger", []).append({
                "segment": r.get("segment"), "oran": r.get("oran"), "satilik_m2": r.get("satilik_m2_fiyat"),
                "kiralik_m2": r.get("kiralik_m2_fiyat"), "ilan": r.get("ilan_sayisi"), "amortisman": r.get("amortisman_yil")})
        for k in gruplar:
            gruplar[k].sort(key=lambda x: -(x["oran"] or 0))
        return {"status": "available", "scope": scope, "kategori": kategori, "gruplar": gruplar}

    # Arsa/konut/dükkan analizinde öne çıkarılan POI grupları (alt_kategori → grup).
    POI_GRUPLARI = {
        "saglik": ("Hastane", "Eczane", "Veteriner", "Sağlık Ocağı", "Poliklinik", "Aile Sağlığı Merkezi"),
        "egitim": ("İlkokul", "Ortaokul", "Lise", "Okul Öncesi", "Kolej", "Üniversite", "Yüksekokul/Akademi", "Kütüphane"),
        "ulasim": ("Durak (Otobüs)", "Durak (Minibüs)", "Durak (Taksi)", "Metro", "Tramvay", "Metrobüs", "Tren İstasyonu", "İskele", "Havalimanı", "Otogar"),
        "alisveris": ("Zincir Marketler", "Gross Market", "AVM", "Pazar Alanı", "Çarşı ve Pasajlar"),
        "yeme_icme": ("Kafe", "Lokanta/Restoran", "Fast Food", "Fırın/Pastane", "Pizza", "Bar"),
        "spor_kultur": ("Spor Merkezi", "Spor Salonu", "Spor Tesisi", "Spor Kulübü", "Basketbol/Voleybol Sahası", "Yüzme Havuzu", "Müze", "Kültür/Kongre Merkezi", "Sosyal Tesis"),
        "konut_stoku": ("Kooperatif/Site", "Villa Sitesi"),
        "ibadet": ("Cami", "Kilise", "Cemevi", "Sinagog"),
    }

    def poi_ilce(self, c, ids):
        """İlçe POI sayımı: önceden hesaplanmış `poi_ilce_ozet` (ilçe × alt_kategori) okunur;
        yoksa büyük tabloya düşer. Koordinat yoktur → yakınlık değil, ilçe donanımı."""
        if not ids.get("county_id"):
            return None
        if self._has(c, "poi_ilce_ozet"):
            rows = c.execute("SELECT alt_kategori, sayi FROM poi_ilce_ozet WHERE county_id=? ORDER BY sayi DESC",
                             (ids["county_id"],)).fetchall()
        elif self._has(c, "poi_ilce"):
            rows = c.execute("SELECT alt_kategori, COUNT(*) AS sayi FROM poi_ilce WHERE county_id=? "
                             "GROUP BY alt_kategori ORDER BY sayi DESC", (ids["county_id"],)).fetchall()
        else:
            return None
        if not rows:
            return None
        sayim = {r["alt_kategori"] or "diger": int(r["sayi"]) for r in rows}
        gruplar = {}
        for grup, kats in self.POI_GRUPLARI.items():
            alt = {k: sayim[k] for k in kats if k in sayim}
            if alt:
                gruplar[grup] = {"toplam": sum(alt.values()), "alt": alt}
        toplam = sum(sayim.values())
        # Kaynak ilçe başına 2000 kayıtla kesiyor (90 ilçe tam bu sınırda): bu ilçelerde sayılar
        # alt sınırdır; üst değer uydurulmaz, yalnız işaretlenir.
        return {"status": "available", "scope": "ilce", "toplam": toplam,
                "kaynak_siniri": toplam >= 2000, "gruplar": gruplar, "kategoriler": sayim}

    def yas_piramidi(self, c, ids):
        row, scope = self._fetch_scoped(c, "yas_piramidi", ids)
        if not row:
            return None
        gruplar = []
        for k, v in row.items():
            if k.startswith("Age_") and k.endswith("_Total") and v is not None:
                gruplar.append({"grup": k[4:-6].replace("_", "-"), "toplam": v,
                                "erkek": row.get(k[:-6] + "_Male"), "kadin": row.get(k[:-6] + "_Female")})
        return {"status": "available", "scope": scope, "gruplar": gruplar} if gruplar else None

    def medeni_stok(self, c, ids):
        row, scope = self._fetch_scoped(c, "medeni_stok", ids)
        if not row:
            return None
        return {"status": "available", "scope": scope,
                "toplam_konut": row.get("toplam_konut_sayisi"), "ticari_mulk": row.get("toplam_ticari_mulk"),
                "yazlik_konut": row.get("yazlik_konut_sayisi"), "sahibinden_ilan": row.get("sahibinden_ilan_sayisi"),
                "emlakci_ilan": row.get("emlakci_ilan_sayisi"),
                "medeni": {k: row.get(v) for k, v in (("evli", "evli_sayisi"), ("bekar", "bekar_hic_evlenmemis"),
                                                     ("bosanmis", "bosanmis_sayisi"), ("dul", "dul_sayisi"))}}

    def eticaret_harcama(self, c, ids):
        row, scope = self._fetch_scoped(c, "eticaret_harcama", ids)
        if not row:
            return None
        keys = ("hanehalki_geliri", "aylik_toplam_harcama", "aylik_barinma_kira_harcamasi", "aylik_gida_harcamasi",
                "e_ticaret_yogunluk", "online_pazaryeri_tl", "guncel_2026_kira_barinma_tl", "guncel_2026_hanehalki_geliri_tl")
        return {"status": "available", "scope": scope, **{k: row.get(k) for k in keys}}

    # ---------- birleşik profil ----------
    def profil(self, request: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        c = self._conn()
        if c is None:
            return {"status": "unavailable"}
        try:
            ids = self.resolve_ids(request.get("il"), request.get("ilce"), request.get("mahalle"))
            if ids["city_id"] is None:
                return {"status": "location_unresolved", "ids": ids}
            sureler: dict[str, float] = {}
            def timed(ad, fn, *a):
                s = time.perf_counter(); v = fn(c, ids, *a); sureler[ad] = round((time.perf_counter() - s) * 1000, 1); return v
            out = {
                "status": "available",
                "ids": ids,
                "demografi": timed("demografi", self.demografi),
                "yillik_satis": timed("yillik_satis", self.yillik_satis),
                "konut_fiyat_ozet": timed("konut_fiyat_ozet", self.fiyat_ozet, "konut"),
                "arsa_fiyat_ozet": timed("arsa_fiyat_ozet", self.fiyat_ozet, "arsa"),
                "konut_kirilim": timed("konut_kirilim", self.dagilim_kirilim, "konut"),
                "poi_ilce": timed("poi_ilce", self.poi_ilce),
                "yas_piramidi": timed("yas_piramidi", self.yas_piramidi),
                "medeni_stok": timed("medeni_stok", self.medeni_stok),
                "eticaret_harcama": timed("eticaret_harcama", self.eticaret_harcama),
            }
            sureler["toplam"] = round((time.perf_counter() - t0) * 1000, 1)
            out["sureler_ms"] = sureler
            return out
        finally:
            c.close()
