"""Çevre göstergeleri — cevre.sqlite (hava kalitesi, ÇŞB SİM) + resmi_gazete.sqlite (bölgeyi etkileyen kararlar).

Hava: parsele en yakın 3 istasyon (≤ 40 km); en yakın istasyonun son 365 gün PM10 / PM2.5 / NO2 / SO2 ortalaması, geçerli gün
sayısı, AB günlük sınırı (PM10 > 50 µg/m³) aşım günü, DSÖ 2021 yıllık kılavuzu (PM10 15, PM2.5 5, NO2 10) ile oran. İstasyon
uzaksa (> 15 km) "temsil gücü düşük" etiketi. Ölçüm yoksa NULL; uydurma yok.
Resmî Gazete: son N ay içinde il+ilçe (varsa mahalle) eşleşen kararlar (kategori, işlem, tarih, başlık); ada/parsel düzeyi yok.
Deprem senaryosu (yalnız İstanbul, İBB 7,5 Mw gece): mahalle bazlı hasar/can kaybı + 2017 bina stoku → ağır hasar oranı.
Gürültü (yalnız İstanbul, İBB 2024): 55/65/75 dB eş-gürültü çizgilerine mesafe (gündüz/gece) — banda kesin atama yapılmaz.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .veri_yardimcilari import haversine_km, normalize_name, normalize_neighbourhood

DSO_YILLIK = {"pm10": 15.0, "pm25": 5.0, "no2": 10.0}
AB_GUNLUK_PM10 = 50.0
ISTASYON_ARAMA_KM = 40.0
TEMSIL_KM = 15.0
RG_AY = 18


class EnvironmentEngine:
    def __init__(self, cevre_db: str | Path, rg_db: str | Path):
        self.cevre_db = Path(cevre_db).expanduser().resolve()
        self.rg_db = Path(rg_db).expanduser().resolve()

    @staticmethod
    def _open(path: Path, table: str):
        if not path.exists():
            return None
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            c.close(); return None
        return c

    def hava(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._open(self.cevre_db, "hava_istasyon")
        if c is None:
            return None
        try:
            d = ISTASYON_ARAMA_KM / 111.0
            cands = []
            for r in c.execute("SELECT * FROM hava_istasyon WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (lat - d, lat + d, lon - d * 2, lon + d * 2)):
                km = haversine_km(lat, lon, r["lat"], r["lon"])
                if km <= ISTASYON_ARAMA_KM:
                    cands.append((km, dict(r)))
            cands.sort(key=lambda x: x[0])
            if not cands:
                return {"status": "no_station", "arama_km": ISTASYON_ARAMA_KM, "kaynak": "ÇŞB SİM Ulusal Hava Kalitesi İzleme Ağı"}
            bas = (date.today() - timedelta(days=365)).isoformat()
            istasyonlar = []
            for km, s in cands[:3]:
                r = c.execute("""SELECT COUNT(pm10) n_pm10, AVG(pm10) pm10, SUM(pm10>?) asim_pm10, COUNT(pm25) n_pm25, AVG(pm25) pm25,
                                        COUNT(no2) n_no2, AVG(no2) no2, COUNT(so2) n_so2, AVG(so2) so2, MIN(gun) ilk, MAX(gun) son
                                 FROM hava_gunluk WHERE istasyon_id=? AND gun>=?""", (AB_GUNLUK_PM10, s["id"], bas)).fetchone()
                it = {"ad": s["ad"], "tip": s["tip"], "il": s["il"], "ilce": s["ilce"], "lat": s["lat"], "lon": s["lon"], "mesafe_km": round(km, 1),
                      "temsil_gucu": "yüksek" if km <= 5 else ("orta" if km <= TEMSIL_KM else "düşük"),
                      "donem": {"ilk": r["ilk"], "son": r["son"]},
                      "pm10": {"ortalama": round(r["pm10"], 1) if r["pm10"] is not None else None, "gecerli_gun": r["n_pm10"], "ab_gunluk_asim_gun": r["asim_pm10"],
                               "dso_yillik_kat": round(r["pm10"] / DSO_YILLIK["pm10"], 1) if r["pm10"] else None},
                      "pm25": {"ortalama": round(r["pm25"], 1) if r["pm25"] is not None else None, "gecerli_gun": r["n_pm25"],
                               "dso_yillik_kat": round(r["pm25"] / DSO_YILLIK["pm25"], 1) if r["pm25"] else None},
                      "no2": {"ortalama": round(r["no2"], 1) if r["no2"] is not None else None, "gecerli_gun": r["n_no2"],
                              "dso_yillik_kat": round(r["no2"] / DSO_YILLIK["no2"], 1) if r["no2"] else None},
                      "so2": {"ortalama": round(r["so2"], 1) if r["so2"] is not None else None, "gecerli_gun": r["n_so2"]}}
                istasyonlar.append(it)
            en = istasyonlar[0]
            aylik = [dict(x) for x in c.execute("""SELECT substr(gun,1,7) ay, ROUND(AVG(pm10),1) pm10, ROUND(AVG(pm25),1) pm25, COUNT(pm10) n
                                                    FROM hava_gunluk WHERE istasyon_id=? AND gun>=? GROUP BY 1 ORDER BY 1""", (cands[0][1]["id"], bas))]
            meta = c.execute("SELECT guncellenme FROM kapsama WHERE tablo='hava_gunluk'").fetchone()
        finally:
            c.close()
        pm = en["pm10"]["ortalama"]
        sinif = None if pm is None else ("iyi" if pm <= 20 else "orta" if pm <= 40 else "kötü" if pm <= 75 else "çok kötü")
        return {"status": "available", "en_yakin": en, "istasyonlar": istasyonlar, "aylik_pm": aylik, "pm10_sinif": sinif,
                "esikler": {"dso_yillik": DSO_YILLIK, "ab_gunluk_pm10": AB_GUNLUK_PM10},
                "guncellenme": meta["guncellenme"] if meta else None,
                "kaynak": "ÇŞB SİM Ulusal Hava Kalitesi İzleme Ağı (günlük ortalama, son 365 gün)",
                "not": "Değerler istasyon noktasına aittir; parsel istasyondan uzaksa temsil gücü düşer. PM10 sınıfı yıllık ortalamaya göre (≤20 iyi, ≤40 orta, ≤75 kötü)."}

    def deprem_senaryo(self, il: str | None, ilce: str | None, mahalleler: list[str] | None) -> dict[str, Any] | None:
        if not il or normalize_name(il) != "istanbul":
            return {"status": "not_covered", "not": "Mahalle bazlı deprem senaryosu yalnız İstanbul (İBB) için yayımlanmış."}
        c = self._open(self.cevre_db, "deprem_senaryo_mahalle")
        if c is None:
            return None
        try:
            r = None
            for m in (mahalleler or []):
                if not m:
                    continue
                r = c.execute("SELECT * FROM deprem_senaryo_mahalle WHERE ilce_norm=? AND mahalle_norm=?", (normalize_name(ilce or ""), normalize_neighbourhood(m))).fetchone()
                if r:
                    break
            if not r:
                return {"status": "no_match", "not": "Mahalle adı İBB senaryo listesiyle eşleşmedi."}
            ilce_n = normalize_name(ilce or "")
            sira = c.execute("SELECT COUNT(*)+1 FROM deprem_senaryo_mahalle WHERE agir_hasar_orani>?", (r["agir_hasar_orani"] or 0,)).fetchone()[0]
            n = c.execute("SELECT COUNT(*) FROM deprem_senaryo_mahalle WHERE agir_hasar_orani IS NOT NULL").fetchone()[0]
            ilce_ort = c.execute("SELECT ROUND(AVG(agir_hasar_orani),1) FROM deprem_senaryo_mahalle WHERE ilce_norm=?", (ilce_n,)).fetchone()[0]
            ist_ort = c.execute("SELECT ROUND(SUM(cok_agir+agir)*100.0/SUM(bina_toplam),1) FROM deprem_senaryo_mahalle WHERE bina_toplam>0").fetchone()[0]
        finally:
            c.close()
        d = dict(r)
        return {"status": "available", "mahalle": d["mahalle"], "ilce": d["ilce"], "senaryo": "7,5 Mw gece (İBB / Kandilli)",
                "bina_toplam_2017": d["bina_toplam"], "cok_agir": d["cok_agir"], "agir": d["agir"], "orta": d["orta"], "hafif": d["hafif"],
                "agir_hasar_orani": d["agir_hasar_orani"], "ilce_ortalama": ilce_ort, "istanbul_ortalama": ist_ort, "istanbul_sira": sira, "mahalle_sayisi": n,
                "can_kaybi": d["can_kaybi"], "agir_yarali": d["agir_yarali"], "gecici_barinma": d["gecici_barinma"],
                "bina_yasi": {"1980_oncesi": d["bina_1980_oncesi"], "1980_2000": d["bina_1980_2000"], "2000_sonrasi": d["bina_2000_sonrasi"]},
                "kaynak": "İBB açık veri: Deprem Senaryosu Analiz Sonuçları + 2017 mahalle bina sayıları",
                "not": "Senaryo sonuçları mahalle toplamıdır; tekil parsel/bina için geçerli değildir. Ağır hasar oranı = (çok ağır + ağır) / 2017 bina stoku."}

    def gurultu(self, lat: float | None, lon: float | None, arama_m: int = 400) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._open(self.cevre_db, "gurultu_hat")
        if c is None:
            return None
        try:
            from shapely import wkb
            from shapely.geometry import Point
            d = arama_m / 111000.0
            rows = c.execute("SELECT h.donem, h.seviye_db, h.geom FROM gurultu_rtree r JOIN gurultu_hat h ON h.id=r.id "
                             "WHERE r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=?", (lat - d, lat + d, lon - d * 1.4, lon + d * 1.4)).fetchall()
            if not rows and not c.execute("SELECT 1 FROM gurultu_hat LIMIT 1").fetchone():
                return None
        finally:
            c.close()
        if not rows:
            # kapsama dışı mı (İstanbul dışı) yoksa sessiz bölge mi? İstanbul kutusu kabaca 27.9–29.95 / 40.8–41.6
            if 27.9 <= lon <= 29.95 and 40.8 <= lat <= 41.6:
                return {"status": "available", "kapsam": "İstanbul", "arama_m": arama_m, "mesafe_m": {}, "gunduz_sinif": "haritada kaynak yok", "gece_sinif": "haritada kaynak yok",
                        "not": f"{arama_m} m içinde stratejik haritada 55 dB üstü eş-gürültü çizgisi yok (ana karayolu/raylı/sanayi kaynaklarından uzak; yerel sokak gürültüsü haritada yoktur)."}
            return {"status": "not_covered", "not": "Stratejik gürültü haritası yalnız İstanbul (İBB 2024) için işlendi."}
        pt = Point(lon, lat); k = math.cos(math.radians(lat))
        en: dict[str, dict[int, float]] = {}
        for donem, sev, g in rows:
            line = wkb.loads(g)
            near = line.interpolate(line.project(pt))
            m = math.hypot((near.x - lon) * 111000 * k, (near.y - lat) * 111000)
            if m <= arama_m and m < en.setdefault(donem, {}).get(sev, 1e9):
                en[donem][sev] = round(m)
        def sinif(dm):
            if not dm:
                return "haritada kaynak yok (55 dB çizgisi ≥ %d m uzakta)" % arama_m
            if dm.get(75, 1e9) <= 30:
                return "çok gürültülü (75 dB hattı bitişik)"
            if dm.get(65, 1e9) <= 30:
                return "gürültülü (65 dB hattı bitişik)"
            if dm.get(55, 1e9) <= 30:
                return "orta (55 dB hattı bitişik)"
            return "en yakın %d dB hattı %d m" % (min(dm, key=dm.get), min(dm.values()))
        return {"status": "available", "kapsam": "İstanbul", "arama_m": arama_m,
                "mesafe_m": {d: {str(s): v for s, v in sorted(dm.items())} for d, dm in en.items()},
                "gunduz_sinif": sinif(en.get("gunduz", {})), "gece_sinif": sinif(en.get("gece", {})),
                "kaynak": "İBB Birleştirilmiş Stratejik Gürültü Haritası 2024 (TÜBİTAK MAM), Lgündüz/Lgece eş-gürültü çizgileri",
                "not": "Harita eş-gürültü çizgisi olarak yayımlandığından parselin dB bandı kesin atanmaz; çizgiye mesafe verilir (çizgi karayolu/raylı/sanayi kaynaklı birleşik seviyedir)."}

    def trafik(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        """İBB saatlik trafik yoğunluğu (geohash-6 hücre): parselin hücresi (yoksa ≤1,5 km en yakın hücre) için hafta içi/sonu 24 saat profili."""
        if lat is None or lon is None:
            return {"status": "coordinate_required"}
        c = self._open(self.cevre_db, "trafik_saatlik")
        if c is None:
            return None
        try:
            d = 1.5 / 111.0
            hucreler = {}
            for r in c.execute("SELECT DISTINCT geohash, lat, lon FROM trafik_saatlik WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (lat - d, lat + d, lon - d * 1.4, lon + d * 1.4)):
                hucreler[r["geohash"]] = (haversine_km(lat, lon, r["lat"], r["lon"]), r["lat"], r["lon"])
            if not hucreler:
                if 27.9 <= lon <= 29.95 and 40.8 <= lat <= 41.6:
                    return {"status": "no_cell", "not": "1,5 km içinde trafik ölçüm hücresi yok (ana yol ağı dışı)."}
                return {"status": "not_covered", "not": "Saatlik trafik yoğunluğu yalnız İstanbul (İBB açık veri) için işlendi."}
            gh, (km, clat, clon) = min(hucreler.items(), key=lambda kv: kv[1][0])
            prof = {"haftaici": [None] * 24, "haftasonu": [None] * 24}; hiz = {"haftaici": [None] * 24, "haftasonu": [None] * 24}
            for r in c.execute("SELECT gun_tipi, saat, ort_arac, ort_hiz, gozlem FROM trafik_saatlik WHERE geohash=?", (gh,)):
                prof[r["gun_tipi"]][r["saat"]] = r["ort_arac"]; hiz[r["gun_tipi"]][r["saat"]] = r["ort_hiz"]
            # İstanbul geneli hafta içi hücre ortalaması (karşılaştırma)
            ist = c.execute("SELECT AVG(ort_arac) FROM trafik_saatlik WHERE gun_tipi='haftaici'").fetchone()[0]
            meta = c.execute("SELECT kaynak, guncellenme FROM kapsama WHERE tablo='trafik_saatlik'").fetchone()
        finally:
            c.close()
        hi = [v for v in prof["haftaici"] if v is not None]
        ort_hi = sum(hi) / len(hi) if hi else None
        zirve = max(range(24), key=lambda h: prof["haftaici"][h] or 0) if hi else None
        en_yavas = min((h for h in range(24) if hiz["haftaici"][h] is not None), key=lambda h: hiz["haftaici"][h], default=None)
        return {"status": "available", "hucre": gh, "hucre_mesafe_km": round(km, 2), "hucre_lat": clat, "hucre_lon": clon, "hucre_boyut": "≈1,2 km × 0,6 km",
                "profil_arac": prof, "profil_hiz_kmh": hiz, "haftaici_ortalama_arac": round(ort_hi, 1) if ort_hi else None,
                "istanbul_ortalama_arac": round(ist, 1) if ist else None, "goreli_yogunluk": round(ort_hi / ist, 2) if ort_hi and ist else None,
                "zirve_saat": zirve, "en_yavas_saat": en_yavas, "en_yavas_hiz_kmh": hiz["haftaici"][en_yavas] if en_yavas is not None else None,
                "kaynak": meta["kaynak"] if meta else "İBB Saatlik Trafik Yoğunluk Verisi",
                "not": "Araç GPS'lerinden türetilmiş hücre×saat araç sayısı ve ortalama hız (son 12 yayımlanmış ay ortalaması). Kişi/yaya yoğunluğu değil; ticari canlılık için vekil."}

    def resmi_gazete(self, il: str | None, ilce: str | None, mahalleler: list[str] | None = None) -> dict[str, Any] | None:
        if not il:
            return {"status": "location_required"}
        c = self._open(self.rg_db, "karar")
        if c is None:
            return None
        try:
            il_n, ilce_n = normalize_name(il), normalize_name(ilce or "")
            bas = (date.today() - timedelta(days=RG_AY * 30)).isoformat()
            mah_n = {normalize_neighbourhood(m) for m in (mahalleler or []) if m}
            rows = c.execute("""SELECT k.id, k.tarih, k.rg_sayi, k.karar_no, k.baslik, k.kategori, k.islem, k.dosya, y.ilce, y.mahalle, y.mahalle_norm, y.ilce_norm, y.kaynak
                                FROM karar k JOIN karar_yer y ON y.karar_id=k.id
                                WHERE y.il_norm=? AND k.tarih>=? ORDER BY k.tarih DESC""", (il_n, bas)).fetchall()
            # karar başına: ilçe satırı varsa il-geneli satırı yok sayılır (başka ilçeye özgü karar bu ilçeye "il geneli" diye düşmesin)
            ilceli = {r["id"] for r in rows if r["ilce_norm"]}
            kararlar, gorulen = [], set()
            for r in rows:
                seviye = None
                if ilce_n and r["ilce_norm"] == ilce_n:
                    seviye = "mahalle" if r["mahalle"] and normalize_neighbourhood(r["mahalle"]) in mah_n else "ilçe"
                elif not r["ilce_norm"] and r["kaynak"] == "metin" and r["id"] not in ilceli:
                    seviye = "il"   # harita etiketinden gelen il adı çok gürültülü (kurum adları) → yalnız metin kalıbı
                if not seviye or r["id"] in gorulen:
                    continue
                gorulen.add(r["id"])
                kararlar.append({"id": r["id"], "tarih": r["tarih"], "rg_sayi": r["rg_sayi"], "karar_no": r["karar_no"], "baslik": r["baslik"],
                                 "kategori": r["kategori"], "islem": r["islem"], "eslesme": seviye, "ilce": r["ilce"], "mahalle": r["mahalle"],
                                 "yer_kaynagi": r["kaynak"], "guven": "yüksek" if r["kaynak"] == "metin" else "düşük (güzergâh haritası etiketi)"})
            sira = {"mahalle": 0, "ilçe": 1, "il": 2}
            kararlar.sort(key=lambda k: (sira[k["eslesme"]], -int(k["tarih"].replace("-", ""))))
            meta = c.execute("SELECT MIN(tarih), MAX(tarih), COUNT(*) FROM taranan_gun").fetchone()
        finally:
            c.close()
        return {"status": "available", "donem_ay": RG_AY, "kararlar": kararlar[:25], "toplam": len(kararlar),
                "sayim": {s: sum(1 for k in kararlar if k["eslesme"] == s) for s in ("mahalle", "ilçe", "il")},
                "taranan": {"ilk": meta[0], "son": meta[1], "gun": meta[2]} if meta else None,
                "kaynak": "T.C. Resmî Gazete (fihrist + Cumhurbaşkanı kararı PDF OCR)",
                "not": "Karar yerleri metinden il/ilçe/mahalle düzeyinde çıkarılır; ada/parsel listesi OCR ile güvenilir okunamadığından kararın parseli kapsayıp kapsamadığı RG ekinden doğrulanmalıdır."}
