"""TÜİK bölge göstergeleri — tuik_bolge.sqlite (kardeş dosya): nüfus dinamiği, konut arzı, satış hacmi, sosyoekonomik seviye.

Girdi il/ilçe/mahalle adı (istekten; yoksa koordinattan idari sınırla). Çıktı yalnız kaynakta olan değerler; türetilenler
(değişim yüzdesi, kişi başına arz) formülüyle etiketlenir. Tablo/dosya yoksa None.
- nufus: ilçe son yıl + 1/5/10 yıl değişim; mahalle nüfusu (son yıl, ad eşleşirse); il göç net hızı (son 3 dönem); 2030 projeksiyonu (il).
- konut_arzi: ilçe yapı ruhsatı ve kullanma izni daire sayıları (son 5 yıl), 1000 kişiye düşen ruhsat (son yıl), il çeyreklik son değer.
- konut_satis: ilçe aylık seri (son 24 ay), son 12 ay toplamı ve önceki 12 aya göre değişim; 1000 kişiye satış.
- ses: ilçe SES skoru ve seviye dağılımı (2023), il içi sıra ve Türkiye sırası.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .veri_yardimcilari import normalize_name, normalize_neighbourhood


def _pct(new, old):
    return round((new - old) / old * 100, 1) if new is not None and old else None


class TuikRegionEngine:
    def __init__(self, database: str | Path, admin_lookup=None):
        self.database = Path(database).expanduser().resolve()
        self.admin_lookup = admin_lookup   # AdminLookup (lat, lon) → (il, ilçe); tembel

    def _conn(self):
        if not self.database.exists():
            return None
        c = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='nufus_ilce'").fetchone():
            c.close(); return None
        return c

    @staticmethod
    def _ilce_norm_adaylari(ilce: str | None, il_n: str) -> list[str]:
        """'merkez' ilçesi TÜİK'te 'Merkez' olarak geçer; büyükşehirlerde merkez ilçe yok."""
        n = normalize_name(ilce or "")
        return [n] if n else ["merkez"]

    def analyze(self, request: dict[str, Any], lat: float | None = None, lon: float | None = None) -> dict[str, Any] | None:
        il, ilce, mahalle = request.get("il"), request.get("ilce"), request.get("mahalle")
        if (not il or not ilce) and lat is not None and lon is not None and self.admin_lookup is not None:
            try:
                il2, ilce2 = self.admin_lookup.lookup(lat, lon)
                il, ilce = il or il2, ilce or ilce2
            except Exception:
                pass
        if not il:
            return {"status": "location_required"}
        c = self._conn()
        if c is None:
            return None
        il_n = normalize_name(il)
        try:
            # ---- ilçe kimliği (TÜİK kodu) ----
            kod = None
            for cand in self._ilce_norm_adaylari(ilce, il_n):
                r = c.execute("SELECT tuik_kodu, ilce FROM nufus_ilce WHERE il_norm=? AND ilce_norm=? LIMIT 1", (il_n, cand)).fetchone()
                if r:
                    kod, ilce_ad = r["tuik_kodu"], r["ilce"]; break
            out: dict[str, Any] = {"status": "available", "il": il, "ilce": ilce_ad if kod else ilce, "ilce_eslesti": kod is not None,
                                   "kaynak": "TÜİK (ADNKS, Yapı İzin, Konut Satış, SES 2023, göç, projeksiyon)"}
            # ---- nüfus ----
            nufus: dict[str, Any] = {}
            if kod:
                seri = {r["yil"]: r["nufus"] for r in c.execute("SELECT yil, nufus FROM nufus_ilce WHERE tuik_kodu=? ORDER BY yil", (kod,))}
                if seri:
                    son = max(seri); n0 = seri[son]
                    nufus = {"ilce_nufus": n0, "yil": son, "degisim_1y_pct": _pct(n0, seri.get(son - 1)),
                             "degisim_5y_pct": _pct(n0, seri.get(son - 5)), "degisim_10y_pct": _pct(n0, seri.get(son - 10)),
                             "seri": [{"yil": y, "nufus": v} for y, v in sorted(seri.items())][-12:]}
                adaylar = [m for m in (request.get("mahalle_adaylari") or [mahalle]) if m]
                if adaylar:
                    r = None
                    for m in adaylar:   # önce kadastro mahallesi, sonra kullanıcının yazdığı idari mahalle
                        m_n = normalize_neighbourhood(m)
                        r = c.execute("SELECT mahalle, belediye, yil, nufus FROM nufus_mahalle WHERE il_norm=? AND ilce_norm=? AND mahalle_norm=? ORDER BY yil DESC LIMIT 1",
                                      (il_n, normalize_name(ilce_ad), m_n)).fetchone()
                        if not r:   # köy olabilir
                            r = c.execute("SELECT koy AS mahalle, NULL AS belediye, yil, nufus FROM nufus_koy WHERE il_norm=? AND ilce_norm=? AND koy_norm=? ORDER BY yil DESC LIMIT 1",
                                          (il_n, normalize_name(ilce_ad), m_n)).fetchone()
                        if r:
                            break
                    nufus["mahalle"] = {"ad": r["mahalle"], "belediye": r["belediye"], "yil": r["yil"], "nufus": r["nufus"]} if r else {"ad": adaylar[0], "nufus": None, "not": "TÜİK mahalle listesinde ad eşleşmedi (kadastro mahallesi idari mahalleden farklı olabilir)"}
            goc = [dict(r) for r in c.execute("SELECT donem, aldigi, verdigi, net, net_hiz_binde FROM goc_il WHERE il_norm=? ORDER BY donem DESC LIMIT 3", (il_n,))]
            proj = c.execute("SELECT nufus_2023, nufus_2030, yillik_artis_binde FROM nufus_projeksiyon_il WHERE il_norm=?", (il_n,)).fetchone()
            hh = c.execute("SELECT hanehalki, ortalama_buyukluk FROM hanehalki_il WHERE il_norm=?", (il_n,)).fetchone()
            nufus["il_goc"] = goc
            nufus["il_projeksiyon_2030"] = dict(proj) if proj else None
            nufus["il_hanehalki"] = dict(hh) if hh else None
            out["nufus"] = nufus
            # ---- konut arzı ----
            arz: dict[str, Any] = {}
            if kod:
                rows = c.execute("SELECT belge, yil, daire FROM yapi_izin_ilce WHERE tuik_kodu=? ORDER BY yil", (kod,)).fetchall()
                by = {"ruhsat": {}, "kullanma": {}}
                for r in rows:
                    by[r["belge"]][r["yil"]] = r["daire"]
                yillar = sorted({r["yil"] for r in rows})
                arz["seri"] = [{"yil": y, "ruhsat_daire": by["ruhsat"].get(y), "kullanma_daire": by["kullanma"].get(y)} for y in yillar[-6:]]
                if yillar:
                    son = yillar[-1]
                    ruh = by["ruhsat"].get(son); ort5 = [v for y, v in by["ruhsat"].items() if son - 5 <= y < son and v is not None]
                    arz["son_yil"] = son; arz["ruhsat_daire"] = ruh; arz["kullanma_daire"] = by["kullanma"].get(son)
                    arz["ruhsat_5y_ortalamaya_gore_pct"] = _pct(ruh, sum(ort5) / len(ort5)) if ruh is not None and ort5 else None
                    n0 = nufus.get("ilce_nufus")
                    arz["ruhsat_daire_1000_kisi"] = round(ruh / n0 * 1000, 2) if ruh is not None and n0 else None
                    arz["not"] = "Yapı ruhsatı = başlayacak inşaat (gelecek arz); yapı kullanma izni = tamamlanan konut. 'Binalar' toplamı, daire sayısı."
            # kullanım amacı kırılımı (yüzölçümü m²): son 3 yıl toplamı → ikamet / ofis / ticaret / sanayi / otel / kamu payları
            if kod and c.execute("SELECT 1 FROM sqlite_master WHERE name='yapi_ruhsat_amac_ilce'").fetchone():
                son_yil = c.execute("SELECT MAX(yil) FROM yapi_ruhsat_amac_ilce WHERE tuik_kodu=? AND yuzolcumu_m2 IS NOT NULL", (kod,)).fetchone()[0]
                if son_yil:
                    rows = c.execute("SELECT amac_kodu, amac, SUM(yuzolcumu_m2) m2 FROM yapi_ruhsat_amac_ilce WHERE tuik_kodu=? AND yil>? AND yil<=? GROUP BY 1,2",
                                     (kod, son_yil - 3, son_yil)).fetchall()
                    m2 = {r["amac_kodu"]: (r["amac"], r["m2"] or 0) for r in rows}
                    toplam = (m2.get("11", ("", 0))[1] or 0) + (m2.get("12", ("", 0))[1] or 0)
                    etiket = {"11": "Konut", "121": "Otel", "122": "Ofis", "123": "Ticaret", "124": "Ulaşım/iletişim", "125": "Sanayi/depo", "126": "Kamu/eğitim/sağlık", "127": "Diğer"}
                    arz["kullanim_amaci"] = {"donem": f"{son_yil - 2}–{son_yil}", "toplam_m2": round(toplam),
                                             "paylar": [{"kod": k, "etiket": etiket[k], "m2": round(v[1]), "pay_pct": round(v[1] / toplam * 100, 1) if toplam else None}
                                                        for k, v in sorted(m2.items()) if k in etiket and v[1]],
                                             "konut_disi_pay_pct": round((m2.get("12", ("", 0))[1] or 0) / toplam * 100, 1) if toplam else None,
                                             "seri": [dict(r) for r in c.execute("SELECT yil, SUM(CASE WHEN amac_kodu='11' THEN yuzolcumu_m2 END) konut_m2, SUM(CASE WHEN amac_kodu='12' THEN yuzolcumu_m2 END) konut_disi_m2 "
                                                                                 "FROM yapi_ruhsat_amac_ilce WHERE tuik_kodu=? GROUP BY yil ORDER BY yil", (kod,))][-8:]}
            ilr = c.execute("SELECT yil, ceyrek, daire FROM yapi_izin_il WHERE il_norm=? AND belge='ruhsat' AND ceyrek<>'yil' ORDER BY yil DESC, ceyrek DESC LIMIT 4", (il_n,)).fetchall()
            arz["il_son_ceyrekler_ruhsat"] = [dict(r) for r in ilr]
            out["konut_arzi"] = arz
            # ---- konut satış ----
            satis: dict[str, Any] = {}
            if kod:
                rows = c.execute("SELECT yil, ay, satis FROM konut_satis_ilce WHERE tuik_kodu=? AND satis IS NOT NULL ORDER BY yil, ay", (kod,)).fetchall()
                if rows:
                    seri = [(r["yil"], r["ay"], r["satis"]) for r in rows]
                    son12 = sum(v for _, _, v in seri[-12:]); onceki12 = sum(v for _, _, v in seri[-24:-12]) if len(seri) >= 24 else None
                    satis = {"son_ay": f"{seri[-1][0]}-{seri[-1][1]:02d}", "son_12ay": son12, "onceki_12ay": onceki12, "degisim_12ay_pct": _pct(son12, onceki12),
                             "seri_24ay": [{"donem": f"{y}-{m:02d}", "satis": v} for y, m, v in seri[-24:]],
                             "yillik": {}}
                    for y, m, v in seri:
                        satis["yillik"][y] = satis["yillik"].get(y, 0) + v
                    n0 = nufus.get("ilce_nufus")
                    satis["satis_1000_kisi_12ay"] = round(son12 / n0 * 1000, 2) if n0 else None
            out["konut_satis"] = satis
            # ---- SES ----
            ses = None
            if kod:
                r = c.execute("SELECT * FROM ses_ilce WHERE il_norm=? AND ilce_norm=?", (il_n, normalize_name(ilce_ad))).fetchone()
                if r:
                    il_sira = c.execute("SELECT COUNT(*)+1 FROM ses_ilce WHERE il_norm=? AND ilce_norm<>'' AND ses_skor>?", (il_n, r["ses_skor"])).fetchone()[0]
                    il_n_ilce = c.execute("SELECT COUNT(*) FROM ses_ilce WHERE il_norm=? AND ilce_norm<>''", (il_n,)).fetchone()[0]
                    tr_sira = c.execute("SELECT COUNT(*)+1 FROM ses_ilce WHERE ilce_norm<>'' AND ses_skor>?", (r["ses_skor"],)).fetchone()[0]
                    tr_n = c.execute("SELECT COUNT(*) FROM ses_ilce WHERE ilce_norm<>''").fetchone()[0]
                    ses = {"yil": r["yil"], "skor": round(r["ses_skor"], 1), "ust_pct": round(r["ust"], 1), "ust_alti_pct": round(r["ust_alti"], 1), "orta_pct": round(r["orta"], 1),
                           "alt_pct": round(r["alt"], 1), "en_alt_pct": round(r["en_alt"], 1), "il_sira": il_sira, "il_ilce_sayisi": il_n_ilce, "turkiye_sira": tr_sira, "turkiye_ilce_sayisi": tr_n}
            il_ses = c.execute("SELECT ses_skor FROM ses_ilce WHERE il_norm=? AND ilce_norm=''", (il_n,)).fetchone()
            out["ses"] = ses; out["il_ses_skor"] = round(il_ses["ses_skor"], 1) if il_ses else None
            out["ses_not"] = "SES skoru: TÜİK hanehalkı sosyoekonomik seviye endeksi (Türkiye ort. 133); A/A+ üst, B üst-altı, C orta, D alt, E en alt hanehalkı payları."
            out["guncellenme"] = {r["tablo"]: r["guncellenme"] for r in c.execute("SELECT tablo, guncellenme FROM kapsama")}
        finally:
            c.close()
        return out
