"""Yakın önemli noktalar — osm_poi.sqlite üzerinden koordinat bazlı (R-tree) sorgu.

- Kategori başına en yakın N (kuş uçuşu) + 500 m / 1 km / 3 km sayımları.
- 500 m içindeki toplu taşıma duraklarından geçen hatlar (hat_durak → hat).
- Zincir/marka varlığı (1 km): market/kahve/restoran/tekel markaları ve sayıları.
- Ana hedefler (hastane, AVM, üniversite, OSB, liman, hal, tren, otogar, havalimanı) için OSRM public
  sunucudan yol mesafesi/süresi (tek "table" isteği; hata/zaman aşımı → yalnız kuş uçuşu, uydurma yok).
- Resmî listeler (kardeş dosya onemli_tesisler.sqlite): HKS toptancı halleri, OSBÜK OSB'leri, SB faal özel hastaneler.
  OSM adayıyla 400 m içinde çakışan resmî kayıt OSM satırını "resmi" olarak işaretler; çakışmayan resmî kayıt ayrı
  madde olarak eklenir (koordinat kaynağı yaklaşık olabilir → "konum_yaklasik"). Dosya/tablo yoksa yalnız OSM.
- Salt-okunur bağlantı, tablo yoksa None; bbox ön eleme R-tree, kesin mesafe haversine.
Kaynak: © OpenStreetMap katkıcıları (ODbL); resmî listeler HKS / OSBÜK / SB. Veri sınıfı: açık.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

from .veri_yardimcilari import haversine_km, normalize_name

OSRM_URL = os.environ.get("GEOPROP_OSRM_URL", "https://router.project-osrm.org")
USER_AGENT = "GEOPROP/1.0 (nearby POI)"

# Kartta öne çıkarılan hedefler: (alt_kategori, etiket, arama yarıçapı km, en yakın N)
HEDEFLER: tuple[tuple[str, str, float, int], ...] = (
    ("hastane", "Hastane", 25, 3), ("saglik_ocagi", "Sağlık ocağı / ASM", 5, 3), ("eczane", "Eczane", 3, 3),
    ("ilkokul", "İlkokul", 5, 3), ("ortaokul", "Ortaokul", 5, 3), ("lise", "Lise", 8, 3), ("okul", "Okul (tür belirsiz)", 5, 2),
    ("anaokulu", "Anaokulu / kreş", 3, 2), ("universite", "Üniversite", 30, 3),
    ("otobus_duragi", "Otobüs durağı", 2, 5), ("tramvay_duragi", "Tramvay durağı", 5, 2), ("metro_istasyonu", "Metro", 10, 2),
    ("tren_istasyonu", "Tren istasyonu", 30, 2), ("otogar", "Otogar", 30, 1), ("havalimani", "Havalimanı", 100, 1),
    ("iskele", "İskele", 20, 1), ("liman", "Liman", 60, 1),
    ("avm", "AVM", 20, 3), ("market", "Market", 2, 5), ("tekel", "Tekel / büfe", 2, 3), ("pazar", "Pazar yeri", 5, 2), ("hal", "Toptancı hali", 40, 1),
    ("kafe", "Kafe", 2, 5), ("restoran", "Restoran", 2, 5), ("fast_food", "Fast food", 2, 3),
    ("osb", "OSB", 40, 2), ("sanayi_sitesi", "Sanayi sitesi", 15, 2), ("sanayi_alani", "Sanayi alanı", 10, 2),
    ("stadyum", "Stadyum", 20, 1), ("spor_salonu", "Spor salonu", 5, 3), ("park", "Park", 3, 3),
    ("karakol", "Karakol", 10, 1), ("itfaiye", "İtfaiye", 15, 1), ("belediye", "Belediye", 15, 1),
    ("benzin_istasyonu", "Akaryakıt", 5, 2), ("otopark", "Otopark", 1, 3), ("cami", "Cami", 2, 3), ("ibadethane", "Diğer ibadethane", 3, 1),
)
OSRM_HEDEFLER = ("hastane", "avm", "universite", "osb", "liman", "hal", "tren_istasyonu", "otogar", "havalimani", "metro_istasyonu")
SAYIM_YARICAP_KM = (0.5, 1.0, 3.0)
ZINCIR_KATEGORILER = ("market", "kafe", "restoran", "fast_food", "tekel", "benzin_istasyonu", "spor_salonu")
# alt_kategori → (onemli_tesisler tablosu, ek sütunlar, kaynak etiketi)
RESMI_LISTELER: dict[str, tuple[str, tuple[str, ...], str]] = {
    "hal": ("hal", ("tur", "faaliyet_tarihi"), "HKS toptancı hal kayıt listesi (hal.gov.tr)"),
    "osb": ("osb", ("tur", "durum", "alan_ha", "parsel_sayisi"), "OSBÜK OSB listesi"),
    "hastane": ("hastane_ozel", ("tip",), "SB faal özel hastane listesi"),
    "liman": ("liman", ("faaliyet", "isletici"), "UAB Denizcilik GM ISPS liman tesisleri listesi (2022)"),
}
RESMI_CAKISMA_M = 400
# Liman listesi toplayıcıda zaten 3 km içindeki OSM nesnesine oturtulur (aynı koordinat) → yoğun sanayi kıyısında yanlış
# komşuya yapışmasın diye eşik dar; oturtulamayanlar ≈1 km hassasiyetli resmî koordinatla ayrı madde olur.
RESMI_CAKISMA_OZEL_M = {"liman": 60}
# OSM kapsamı zaten iyi olan kategorilerde yalnız adla kodlanmış (en zayıf) resmî konum ayrı madde OLMAZ; sadece
# çakışan OSM satırını zenginleştirir (ilçe merkezine düşmüş "en yakın hastane" göstermemek için).
RESMI_AYRI_MADDE_YOK = {"hastane": ("ad eşleşmesi",)}


class NearbyPoiEngine:
    def __init__(self, database: str | Path, osrm_url: str = OSRM_URL, osrm_timeout_s: float = 5.0):
        self.database = Path(database).expanduser().resolve()
        self.osrm_url = osrm_url.rstrip("/")
        self.osrm_timeout_s = osrm_timeout_s
        self.osrm_enabled = os.environ.get("GEOPROP_OSRM_CANLI", "1") != "0"
        self.resmi_database = self.database.with_name("onemli_tesisler.sqlite")
        self.degisim_database = self.database.with_name("osm_degisim.sqlite")
        self.idari_database = self.database.with_name("idari_sinirlar.sqlite")
        self._admin = None

    def _conn(self) -> sqlite3.Connection | None:
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='poi'").fetchone():
            c.close(); return None
        return c

    @staticmethod
    def _bbox(lat: float, lon: float, r_km: float) -> tuple[float, float, float, float]:
        dlat = r_km / 111.0
        dlon = r_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
        return lat - dlat, lat + dlat, lon - dlon, lon + dlon

    def _candidates(self, c, lat, lon, r_km, alt: str | None = None, kategori: str | None = None) -> list[dict[str, Any]]:
        a0, a1, o0, o1 = self._bbox(lat, lon, r_km)
        cols = "p.id, p.osm_tip, p.osm_id, p.kategori, p.alt_kategori, p.marka, p.ad, p.lat, p.lon"
        if alt:
            # Kategori-özel: (alt_kategori, lat, lon) indeksi → enlem bandı; geniş yarıçapta R-tree'den çok daha seçici.
            rows = c.execute(
                f"SELECT {cols} FROM poi p WHERE p.alt_kategori=? AND p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?",
                (alt, a0, a1, o0, o1)).fetchall()
        else:
            where, params = ["r.min_lat>=? AND r.max_lat<=? AND r.min_lon>=? AND r.max_lon<=?"], [a0, a1, o0, o1]
            if kategori:
                where.append("p.kategori=?"); params.append(kategori)
            rows = c.execute(f"SELECT {cols} FROM poi_rtree r JOIN poi p ON p.id=r.id WHERE {' AND '.join(where)}", params).fetchall()
        out = []
        for row in rows:
            d = haversine_km(lat, lon, row["lat"], row["lon"])
            if d <= r_km:
                x = dict(row); x["mesafe_m"] = round(d * 1000); out.append(x)
        out.sort(key=lambda x: x["mesafe_m"])
        return out

    def _resmi_conn(self) -> sqlite3.Connection | None:
        if not self.resmi_database.exists():
            return None
        c = sqlite3.connect(f"file:{self.resmi_database}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        return c

    def _resmi_birlestir(self, rc, alt: str, lat, lon, r_km, cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resmî listeyi OSM adaylarıyla birleştirir (çakışan → işaret, çakışmayan → yeni madde)."""
        if rc is None or alt not in RESMI_LISTELER:
            return cands
        tablo, ek, _ = RESMI_LISTELER[alt]
        if not rc.execute("SELECT 1 FROM sqlite_master WHERE name=?", (tablo,)).fetchone():
            return cands
        a0, a1, o0, o1 = self._bbox(lat, lon, r_km)
        rows = rc.execute(f"SELECT ad, il, ilce, lat, lon, koordinat_kaynagi, {', '.join(ek)} FROM {tablo} "
                          "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (a0, a1, o0, o1)).fetchall()
        for r in rows:
            d = haversine_km(lat, lon, r["lat"], r["lon"])
            if d > r_km:
                continue
            resmi = {k: r[k] for k in ek}
            resmi["ad"] = r["ad"]; resmi["ilce"] = r["ilce"]
            yakin = min(cands, key=lambda x: haversine_km(r["lat"], r["lon"], x["lat"], x["lon"]), default=None)
            if yakin and haversine_km(r["lat"], r["lon"], yakin["lat"], yakin["lon"]) * 1000 <= RESMI_CAKISMA_OZEL_M.get(alt, RESMI_CAKISMA_M):
                yakin["resmi"] = resmi
                continue
            # koordinatı yaklaşık (geocode) resmî kayıt: aynı adlı OSM nesnesi yarıçapta varsa ona bağla (konumu OSM'den)
            adli = [x for x in cands if x["ad"] and "resmi" not in x and
                    difflib.SequenceMatcher(None, normalize_name(x["ad"]), normalize_name(r["ad"])).ratio() >= 0.72]
            if adli:
                adli[0]["resmi"] = resmi
                continue
            if any(k in (r["koordinat_kaynagi"] or "") for k in RESMI_AYRI_MADDE_YOK.get(alt, ())):
                continue
            cands.append({"id": None, "osm_tip": None, "osm_id": None, "kategori": None, "alt_kategori": alt, "marka": None,
                          "ad": r["ad"], "lat": r["lat"], "lon": r["lon"], "mesafe_m": round(d * 1000), "resmi": resmi,
                          "konum_yaklasik": "yaklaşık" in (r["koordinat_kaynagi"] or "")})
        cands.sort(key=lambda x: x["mesafe_m"])
        return cands

    # Ticari yoğunluk grupları: OSM alt kategorileri (ofis/mağaza kategorileri toplayıcıda açılınca eklenir)
    YOGUNLUK_GRUPLARI = {
        "yeme_icme": ("restoran", "kafe", "fast_food", "bar"),
        "perakende": ("market", "firin", "tekel", "avm", "magaza", "toptanci"),
        "is_ofis": ("ofis", "banka", "atm", "kuafor_guzellik"),
        "sanayi": ("fabrika", "sanayi_sitesi", "sanayi_alani", "osb"),
    }

    def _yogunluk(self, c, lat, lon, r_km=1.0) -> dict[str, Any] | None:
        """Parsel çevresi (r_km) işletme yoğunluğu / ilçe ortalaması (km² başına). Kaynak: OSM POI + idari ilçe alanı + ilce_sayim."""
        if not self.idari_database.exists() or not self.degisim_database.exists():
            return None
        try:
            if self._admin is None:
                from .idari import AdminLookup
                self._admin = AdminLookup(self.idari_database)
            il, ilce = self._admin.lookup(lat, lon)
        except Exception:
            return None
        if not il or not ilce:
            return None
        ic = sqlite3.connect(f"file:{self.idari_database}?mode=ro", uri=True)
        try:
            r = ic.execute("SELECT alan_km2 FROM sinir WHERE seviye='ilce' AND ad=? AND il_adi=?", (ilce, il)).fetchone()
        finally:
            ic.close()
        if not r or not r[0]:
            return None
        alan = r[0]
        dc = sqlite3.connect(f"file:{self.degisim_database}?mode=ro", uri=True)
        try:
            tarih = dc.execute("SELECT MAX(tarih) FROM ilce_sayim").fetchone()[0]
            ilce_sayim = {row[0]: row[1] for row in dc.execute("SELECT alt_kategori, sayi FROM ilce_sayim WHERE tarih=? AND il=? AND ilce=?", (tarih, il, ilce))}
        finally:
            dc.close()
        yerel = {x["alt_kategori"]: 0 for x in []}
        for x in self._candidates(c, lat, lon, r_km):
            yerel[x["alt_kategori"]] = yerel.get(x["alt_kategori"], 0) + 1
        alan_yerel = math.pi * r_km * r_km
        out = {}
        for grup, altlar in self.YOGUNLUK_GRUPLARI.items():
            n_yerel = sum(yerel.get(a, 0) for a in altlar); n_ilce = sum(ilce_sayim.get(a, 0) for a in altlar)
            yerel_km2 = n_yerel / alan_yerel; ilce_km2 = n_ilce / alan
            out[grup] = {"yerel_sayi": n_yerel, "yerel_km2": round(yerel_km2, 1), "ilce_km2": round(ilce_km2, 2), "ilce_sayi": n_ilce,
                         "oran": round(yerel_km2 / ilce_km2, 1) if ilce_km2 > 0 else None}
        return {"yaricap_km": r_km, "il": il, "ilce": ilce, "ilce_alan_km2": alan, "gruplar": out,
                "not": "Oran = 1 km çevresindeki km² başına işletme / ilçe geneli km² başına işletme (OSM). 1 = ilçe ortalaması; ofis/mağaza kategorileri OSM yeniden işlenince eklenir."}

    def _gecmis(self, lat, lon, r_km=1.0) -> dict[str, Any] | None:
        """osm_degisim.sqlite::poi_yasam (yıllık public kesitler 2021→): r_km içinde OSM'de ilk görülme / kaldırılma / ad-marka değişimi."""
        if not self.degisim_database.exists():
            return None
        c = sqlite3.connect(f"file:{self.degisim_database}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='poi_yasam'").fetchone():
                return None
            kesitler = [r[0] for r in c.execute("SELECT tarih FROM kesit ORDER BY tarih")]
            a0, a1, o0, o1 = self._bbox(lat, lon, r_km)
            # gecmis JSON'u ağır (satır başına rastgele erişim) → önce hafif sütunlar, sonra yalnız ad değişimli satırlar için gecmis
            rows = [dict(r) for r in c.execute("SELECT osm_tip, osm_id, alt_kategori, marka, ad, lat, lon, ilk_gorulme, son_gorulme, durum, ad_degisim, marka_degisim, tasinma "
                                                "FROM poi_yasam WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (a0, a1, o0, o1))
                    if haversine_km(lat, lon, r["lat"], r["lon"]) <= r_km]
            for r in rows:
                r["gecmis"] = c.execute("SELECT gecmis FROM poi_yasam WHERE osm_tip=? AND osm_id=?", (r["osm_tip"], r["osm_id"])).fetchone()[0] if r["ad_degisim"] else "[]"
        finally:
            c.close()
        if not rows:
            return {"status": "available", "kesitler": kesitler, "toplam": 0}
        # Ad değişimi: yazım düzeltmesi / ek-kısaltma ("Şua"→"Şua Eczane", "ing bank"→"ING") sayılmaz; benzerlik < 0,6 ve alt dize değilse gerçek değişim
        def gercek_ad_degisimi(r):
            if not r["ad_degisim"]:
                return False
            adlar = [normalize_name(x["ad"]) for x in json.loads(r["gecmis"] or "[]") if x.get("ad")]
            for a, b in zip(adlar, adlar[1:]):
                if a != b and a not in b and b not in a and difflib.SequenceMatcher(None, a, b).ratio() < 0.6:
                    return True
            return False
        for r in rows:
            r["ad_degisim"] = 1 if gercek_ad_degisimi(r) else 0
        ilk = {k: 0 for k in kesitler}; kald = {k: 0 for k in kesitler}
        for r in rows:
            ilk[r["ilk_gorulme"]] = ilk.get(r["ilk_gorulme"], 0) + 1
            if r["durum"] == "kaldirildi":
                kald[r["son_gorulme"]] = kald.get(r["son_gorulme"], 0) + 1
        taban = kesitler[0]
        def olay_listesi(filtre, n=8):
            out = []
            for r in sorted((x for x in rows if filtre(x)), key=lambda x: x["son_gorulme"] if x["durum"] == "kaldirildi" else x["ilk_gorulme"], reverse=True)[:n]:
                g = json.loads(r["gecmis"] or "[]")
                out.append({"ad": r["ad"], "alt_kategori": r["alt_kategori"], "marka": r["marka"], "ilk": r["ilk_gorulme"], "son": r["son_gorulme"], "durum": r["durum"],
                            "eski_ad": next((x["ad"] for x in g if x.get("ad") and x["ad"] != r["ad"]), None) if r["ad_degisim"] else None, "lat": r["lat"], "lon": r["lon"]})
            return out
        return {"status": "available", "yaricap_km": r_km, "kesitler": kesitler, "toplam": len(rows),
                "taban_2021": ilk.get(taban, 0),
                "yeni_eklenen": {k: v for k, v in ilk.items() if k != taban}, "kaldirilan": {k: v for k, v in kald.items() if v},
                "aktif": sum(1 for r in rows if r["durum"] == "aktif"), "kaldirildi_toplam": sum(1 for r in rows if r["durum"] == "kaldirildi"),
                "ad_degistiren": sum(1 for r in rows if r["ad_degisim"] > 0), "marka_degistiren": sum(1 for r in rows if r["marka_degisim"] > 0),
                "son_eklenenler": olay_listesi(lambda x: x["durum"] == "aktif" and x["ilk_gorulme"] != taban),
                "son_kaldirilanlar": olay_listesi(lambda x: x["durum"] == "kaldirildi"),
                "ad_degisenler": olay_listesi(lambda x: x["ad_degisim"] > 0),
                "not": "OSM yıllık kesitleri (1 Ocak 2021→2025 + güncel). 'İlk görülme' OSM'ye işlenme tarihidir, açılış tarihi değil; 'kaldırıldı' haritacı düzeltmesi de olabilir. Kaynak: © OpenStreetMap katkıcıları (ODbL)."}

    def _degisim(self, lat, lon, r_km=1.0, gun=90) -> dict[str, Any] | None:
        """osm_degisim.sqlite (haftalık kesit farkı): son `gun` günde r_km içinde OSM'ye eklenen / OSM'den kaldırılan POI'ler."""
        if not self.degisim_database.exists():
            return None
        c = sqlite3.connect(f"file:{self.degisim_database}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='poi_olay'").fetchone():
                return None
            anlik = [dict(r) for r in c.execute("SELECT tarih, poi, eklenen, silinen FROM anlik ORDER BY tarih")]
            if len(anlik) < 2:
                return {"status": "baseline", "anlik_sayisi": len(anlik), "ilk_anlik": anlik[0]["tarih"] if anlik else None,
                        "not": "Değişim izleme için en az iki haftalık kesit gerekir; ilk kesit alındı."}
            from datetime import date, timedelta
            bas = (date.today() - timedelta(days=gun)).isoformat()
            a0, a1, o0, o1 = self._bbox(lat, lon, r_km)
            olaylar = []
            for r in c.execute("SELECT tarih, olay, alt_kategori, marka, ad, lat, lon FROM poi_olay WHERE tarih>=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (bas, a0, a1, o0, o1)):
                if haversine_km(lat, lon, r["lat"], r["lon"]) <= r_km:
                    olaylar.append(dict(r))
            ozet: dict[str, dict[str, int]] = {}
            for o in olaylar:
                ozet.setdefault(o["olay"], {})[o["alt_kategori"]] = ozet.get(o["olay"], {}).get(o["alt_kategori"], 0) + 1
            return {"status": "available", "gun": gun, "yaricap_km": r_km, "eklenen": sum(1 for o in olaylar if o["olay"] == "eklendi"),
                    "kaldirilan": sum(1 for o in olaylar if o["olay"] == "silindi"), "ozet": ozet, "olaylar": olaylar[:30],
                    "anlik": anlik[-6:], "not": "OSM'den kaldırılma haritacı düzeltmesi de olabilir; kapanış anlamına gelmez."}
        finally:
            c.close()

    def _routes_for_stops(self, c, stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not stops or not c.execute("SELECT 1 FROM sqlite_master WHERE name='hat_durak'").fetchone():
            return []
        seen: dict[int, dict[str, Any]] = {}
        for s in stops:
            for h in c.execute(
                "SELECT h.id, h.tur, h.ref, h.ad, h.operator, h.from_ad, h.to_ad FROM hat_durak d JOIN hat h ON h.id=d.hat_id "
                "WHERE d.durak_osm_tip=? AND d.durak_osm_id=?", (s["osm_tip"], s["osm_id"])):
                if h["id"] not in seen:
                    seen[h["id"]] = {"tur": h["tur"], "hat": h["ref"] or h["ad"], "ad": h["ad"], "operator": h["operator"],
                                     "guzergah": f"{h['from_ad']} → {h['to_ad']}" if h["from_ad"] and h["to_ad"] else None,
                                     "en_yakin_durak": s["ad"], "durak_mesafe_m": s["mesafe_m"]}
        return sorted(seen.values(), key=lambda x: (x["tur"], str(x["hat"])))

    def _osrm_table(self, lat, lon, targets: list[dict[str, Any]]) -> None:
        """Hedeflere yol mesafesi/süresi; başarısızlıkta alanlar eklenmez (kuş uçuşu kalır)."""
        if not self.osrm_enabled or not targets:
            return
        coords = ";".join([f"{lon:.6f},{lat:.6f}"] + [f"{t['lon']:.6f},{t['lat']:.6f}" for t in targets])
        url = f"{self.osrm_url}/table/v1/driving/{coords}?sources=0&annotations=distance,duration"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=self.osrm_timeout_s) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            dist, dur = j["distances"][0][1:], j["durations"][0][1:]
        except Exception:
            return
        for t, d, s in zip(targets, dist, dur):
            if d is not None and s is not None:
                t["yol_mesafe_m"] = round(d); t["yol_sure_dk"] = round(s / 60, 1)

    def analyze(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._conn()
        if c is None:
            return None
        rc = self._resmi_conn()
        try:
            hedefler: dict[str, Any] = {}
            osrm_targets: list[dict[str, Any]] = []
            for alt, label, r_km, n in HEDEFLER:
                cands = self._resmi_birlestir(rc, alt, lat, lon, r_km, self._candidates(c, lat, lon, r_km, alt=alt))
                items = [{"ad": x["ad"], "marka": x["marka"], "lat": x["lat"], "lon": x["lon"], "mesafe_m": x["mesafe_m"],
                          "osm_tip": x["osm_tip"], "osm_id": x["osm_id"], "resmi": x.get("resmi"), "konum_yaklasik": x.get("konum_yaklasik", False)}
                         for x in cands[:n]]
                hedefler[alt] = {"etiket": label, "arama_km": r_km, "bulunan": len(cands), "en_yakin": items,
                                 "resmi_kaynak": RESMI_LISTELER[alt][2] if alt in RESMI_LISTELER and rc is not None else None}
                if alt in OSRM_HEDEFLER and items:
                    osrm_targets.append(items[0])
            self._osrm_table(lat, lon, osrm_targets[:12])

            sayim: dict[str, dict[str, int]] = {}
            for r_km in SAYIM_YARICAP_KM:
                key = f"{int(r_km * 1000)}m"
                for x in self._candidates(c, lat, lon, r_km):
                    sayim.setdefault(x["alt_kategori"], {})[key] = sayim.get(x["alt_kategori"], {}).get(key, 0) + 1

            duraklar = [x for x in self._candidates(c, lat, lon, 0.5) if x["alt_kategori"] in ("otobus_duragi", "tramvay_duragi", "metro_istasyonu", "tren_istasyonu", "iskele")]
            hatlar = self._routes_for_stops(c, duraklar[:25])

            zincir: dict[str, dict[str, int]] = {}
            for x in self._candidates(c, lat, lon, 1.0):
                if x["alt_kategori"] in ZINCIR_KATEGORILER and x["marka"]:
                    zincir.setdefault(x["alt_kategori"], {})[x["marka"]] = zincir.get(x["alt_kategori"], {}).get(x["marka"], 0) + 1
            meta = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")} if c.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() else {}
            yogunluk = self._yogunluk(c, lat, lon)
        finally:
            c.close()
            if rc is not None:
                rc.close()
        return {
            "status": "available",
            "kaynak": "© OpenStreetMap katkıcıları (ODbL) — Geofabrik Türkiye kesiti; resmî listeler: HKS (hal), OSBÜK (OSB), SB (özel hastane)",
            "veri_tarihi": meta.get("pbf_mtime"),
            "yol_mesafesi_kaynak": "OSRM (project-osrm.org) — araçla" if self.osrm_enabled else None,
            "hedefler": hedefler,
            "sayim": sayim,
            "toplu_tasima": {"durak_500m": len(duraklar), "hatlar": hatlar,
                             "en_yakin_duraklar": [{"ad": d["ad"], "tur": d["alt_kategori"], "mesafe_m": d["mesafe_m"]} for d in duraklar[:5]]},
            "zincirler_1km": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in zincir.items()},
            "degisim_1km": self._degisim(lat, lon),
            "yogunluk_1km": yogunluk,
            "gecmis_1km": self._gecmis(lat, lon),
        }
