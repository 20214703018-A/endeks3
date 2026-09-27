"""TOBB Sanayi Veri Tabanı (kapasite raporları) toplayıcısı.

Kaynak: https://sanayi.org.tr/#/sanayi-veri-tabani — sitenin herkese açık sorgu servisi
  POST https://sanayi.org.tr/api/svt/{invokeService|invokeEager|invoke}
  gövde: {"params": {"ilId", "ilceId", "kod"}, "methodName": ..., "lazyLoadingEvent": ...}
Kapasite raporu: sanayici firmanın oda tarafından belgelenen üretim kapasitesi, personel ve alan bilgisi.

Toplanan sorgular:
  A anaFaaliyetlereGoreUreticiDagilimi (Türkiye, NACE 2 hane)   → sektör listesi + toplamlar
  B illereGoreKapasiteBilgileri (kod yok ve her NACE-2 için)     → il × birim: üretici, personel kırılımı, üretim miktarı
  C ilIlceGenelDurumu (her il; her il × NACE-2)                  → firma, kapasite raporu, personel, kapalı/açık alan
  D ilPersonelAraliklariDagilimByIlId (her il)                   → il × NACE-2 × personel büyüklük sınıfı
  E ilGenelDurumuIlceDuzeyindeDagilim (her il; poligonlar atılır) → ilçe bazında kapasite raporu sayısı
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

B = "https://sanayi.org.tr/"
ILS = list(range(1, 82))
LZ = {"first": 0, "rows": 100000, "sortField": None, "sortOrder": 1, "filters": {}}


def main():
    it = Intake("tobb_sanayi_kapasite", rate=1.5)
    it.s.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    out = it.dir / "responses.jsonl"
    done = set()
    if out.exists():
        for l in out.read_text().split("\n"):
            try:
                d = json.loads(l)
                if d.get("ok"):
                    done.add(d["key"])
            except Exception:  # noqa: BLE001
                pass

    def call(key, url, method, params, lazy=None):
        if key in done:
            return None
        body = {"params": params, "methodName": method, "lazyLoadingEvent": lazy}
        res, ok, err = None, False, None
        for attempt in range(3):
            try:
                r = it.get(B + url, method="POST", json=body, timeout=900, tries=1)
                if r.status_code == 200:
                    res, ok = r.json(), True
                    break
                err = f"HTTP {r.status_code} {r.text[:200]}"
            except Exception as e:  # noqa: BLE001
                err = repr(e)[:200]
            time.sleep(10 * (attempt + 1))
        if isinstance(res, dict) and "polygon" in res:
            res = {k: v for k, v in res.items() if k != "polygon"}
        with out.open("a") as f:
            f.write(json.dumps({"key": key, "url": B + url, "method": method, "params": params, "ok": ok,
                                "err": err, "at": now_iso(), "res": res}, ensure_ascii=False) + "\n")
        if ok:
            done.add(key)
        else:
            it.log(f"{key}: başarısız {err}")
        return res

    # A — sektör listesi
    a = call("A", "api/svt/invokeService", "anaFaaliyetlereGoreUreticiDagilimi", {"ilId": -1, "ilceId": -1, "kod": None}, LZ)
    sectors = []
    if a is None:  # önceden alınmış
        for l in out.read_text().split("\n"):
            if not l.strip():
                continue
            d = json.loads(l)
            if d["key"] == "A" and d["ok"]:
                a = d["res"]
    rows = a.get("content", a) if isinstance(a, dict) else (a or [])
    sectors = sorted({r.get("SEKTOR_KODU") for r in rows if r.get("SEKTOR_KODU")})
    it.log(f"{len(sectors)} NACE-2 sektör")
    # B — il × birim, toplam ve sektör bazında
    call("B|", "api/svt/invokeService", "illereGoreKapasiteBilgileri", {"ilId": -1, "ilceId": -1, "kod": None})
    for s in sectors:
        call(f"B|{s}", "api/svt/invokeService", "illereGoreKapasiteBilgileri", {"ilId": -1, "ilceId": -1, "kod": s})
    it.log("B bitti")
    # D, E, C (il düzeyi)
    for il in ILS:
        call(f"D|{il}", "api/svt/invokeService", "ilPersonelAraliklariDagilimByIlId", {"ilId": il, "ilceId": -1, "kod": None})
        call(f"E|{il}", "api/svt/invokeService", "ilGenelDurumuIlceDuzeyindeDagilim", {"ilId": il, "ilceId": -1, "kod": None})
        call(f"C|{il}|", "api/svt/invokeEager", "ilIlceGenelDurumu", {"ilId": il, "ilceId": -1, "kod": None})
    it.log("D/E/C il düzeyi bitti")
    # C — il × sektör (en derin kırılım; uzun sürer, kaldığı yerden devam eder)
    for il in ILS:
        for s in sectors:
            call(f"C|{il}|{s}", "api/svt/invokeEager", "ilIlceGenelDurumu", {"ilId": il, "ilceId": -1, "kod": s})
        it.log(f"C il×sektör: il {il} bitti")
    # manifest kaydı
    it._record(out, source_url=B + "api/svt/*", method="api_post_sequential", rows=len(done),
               note="her satır bir sorgu yanıtı (key: A/B/C/D/E|il|nace)")
    it.log("bitti")


if __name__ == "__main__":
    main()
