#!/usr/bin/env python3
"""
YENİ VERİ GİRİŞİ — TKGM idari yapı (il → ilçe → mahalle) resmî GeoJSON'ları.

Kaynak: https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api/idariYapi/{ilListe | ilceListe/{ilId} | mahalleListe/{ilceId}}
Hedef : ~/Desktop/GEOPROP_RAW_INTAKE/tkgm_idari_yapi/<tarih>/   (değişmez ham kök; sonra §10.1 kapısından geçer)

Kurallar: her yanıt byte-birebir kaydedilir (yeniden serileştirme yok), yanında .meta.json (url, HTTP durum, başlıklar,
zaman damgası, sha256, süre). Var olan dosya üzerine YAZILMAZ (resume). İstekler arası bekleme. Hata → fetch_log'a yazılır, devam.
"""
import hashlib
import json
import sys
import time
import datetime as dt
from pathlib import Path
import urllib.request
import urllib.error

BASE = "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api/idariYapi"
DAY = dt.datetime.now().strftime("%Y-%m-%d")
ROOT = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE" / "tkgm_idari_yapi" / DAY
DELAY = 0.5
HEADERS = {"User-Agent": "Mozilla/5.0 (GEOPROP consolidation; polite fetch)", "Referer": "https://parselsorgu.tkgm.gov.tr/", "Accept": "application/json"}


def fetch(url: str, dest: Path) -> dict | None:
    """Ham byte'ları dest'e yaz; meta döndür. Dosya varsa atla (resume)."""
    meta_path = dest.with_suffix(dest.suffix + ".meta.json")
    if dest.exists() and meta_path.exists():
        return json.loads(meta_path.read_text())
    t0 = time.time()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            status = r.status
            hdrs = dict(r.headers.items())
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        status = e.code
        hdrs = dict(e.headers.items()) if e.headers else {}
    except Exception as e:
        log({"url": url, "error": repr(e), "at": now()})
        return None
    meta = {"url": url, "http_status": status, "headers": hdrs, "fetched_at": now(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "elapsed_s": round(time.time() - t0, 3)}
    if status == 200 and body:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(body)
        tmp.rename(dest)  # atomik; var olanın üzerine yazılmaz (yukarıda kontrol edildi)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    else:
        log({"url": url, "http_status": status, "bytes": len(body), "at": now(), "body_head": body[:200].decode("utf-8", "replace")})
    return meta


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(rec: dict):
    with open(ROOT / "fetch_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    log({"event": "start", "at": now(), "base": BASE, "delay_s": DELAY})
    m = fetch(f"{BASE}/ilListe", ROOT / "il" / "ilListe.geojson")
    if not m or m.get("http_status") != 200:
        print("il listesi alınamadı", m); sys.exit(1)
    iller = json.loads((ROOT / "il" / "ilListe.geojson").read_bytes())["features"]
    print(f"il: {len(iller)}", flush=True)
    n_ilce_total = 0; n_mah_total = 0; fails = 0
    for i, il in enumerate(sorted(iller, key=lambda x: x["properties"]["id"]), 1):
        il_id = il["properties"]["id"]; il_ad = il["properties"]["text"]
        time.sleep(DELAY)
        m = fetch(f"{BASE}/ilceListe/{il_id}", ROOT / "ilce" / f"ilceListe_{il_id}.geojson")
        if not m or m.get("http_status") != 200:
            fails += 1; print(f"  [{i}/81] {il_ad}: ilçe listesi HATA {m and m.get('http_status')}", flush=True); continue
        ilceler = json.loads((ROOT / "ilce" / f"ilceListe_{il_id}.geojson").read_bytes())["features"]
        n_ilce_total += len(ilceler)
        n_mah = 0
        for ilce in sorted(ilceler, key=lambda x: x["properties"]["id"]):
            ilce_id = ilce["properties"]["id"]
            time.sleep(DELAY)
            mm = fetch(f"{BASE}/mahalleListe/{ilce_id}", ROOT / "mahalle" / f"mahalleListe_{il_id}_{ilce_id}.geojson")
            if not mm or mm.get("http_status") != 200:
                fails += 1; continue
            try:
                n_mah += len(json.loads((ROOT / "mahalle" / f"mahalleListe_{il_id}_{ilce_id}.geojson").read_bytes())["features"])
            except Exception as e:
                log({"url": mm.get("url"), "error": f"json parse: {e}", "at": now()})
        n_mah_total += n_mah
        print(f"  [{i}/81] {il_ad} (id={il_id}): ilçe={len(ilceler)} mahalle={n_mah}  toplam mahalle={n_mah_total} hata={fails}", flush=True)
    log({"event": "finish", "at": now(), "il": len(iller), "ilce": n_ilce_total, "mahalle": n_mah_total, "failures": fails})
    print(f"BİTTİ il={len(iller)} ilçe={n_ilce_total} mahalle={n_mah_total} hata={fails}")


if __name__ == "__main__":
    main()
