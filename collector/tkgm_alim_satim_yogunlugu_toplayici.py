"""TKGM MEGSİS 'Analiz' (Alım Satım Yoğunluğu / Ana Taşınmaz Satış) toplayıcısı.

parselsorgu.tkgm.gov.tr uygulamasının 'analiz' uç noktasından, il + yıl bazında
parsel düzeyinde işlem yoğunluğunu (enlem/boylam/sayı) çeker ve ürün ambarına
(sqlite) yazar. Canlı istek yerine önceden ambara alınır; arayüz DB'den okur.

Uç noktalar (Cloudflare yok, doğrudan GET):
  - İl listesi:      {BASE}/idariYapi/ilListe                (GeoJSON: properties {text,id})
  - Analiz tipleri:  {BASE}/analiz/analizTip                 (id, ad, baslangicYil, bitisYil)
  - Analiz verisi:   {BASE}/analiz?AnalizTip=&Yil=&IlId=     (liste: {parselId,enlem,boylam,sayi})

Kapsama tablosu sayesinde tekrar çalıştırıldığında çekilmiş (tip,il,yıl)
kombinasyonlarını atlar; kesintiden sonra kaldığı yerden devam eder.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

try:
    from curl_cffi import requests as http
    _IMPERSONATE = {"impersonate": "chrome124"}
except ImportError:  # curl_cffi yoksa stdlib'e düş (bu uç noktada Cloudflare gözlenmedi)
    import urllib.request
    import json as _json

    class _UrllibShim:
        class Session:
            def get(self, url, headers=None, timeout=30, **_):
                req = urllib.request.Request(url, headers=headers or {})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read()
                return type("R", (), {
                    "status_code": resp.status,
                    "text": body.decode("utf-8", "replace"),
                    "json": lambda self=None, _b=body: _json.loads(_b),
                })()
    http = _UrllibShim()
    _IMPERSONATE = {}

BASE = "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api"
REFERER = "https://parselsorgu.tkgm.gov.tr/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": REFERER,
}

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Yoğunluk verisi ayrı/standalone ambarda tutulur; 837MB'lik ürün DB'sini CI ve
# git'te taşımamak için buraya yazılır, arayüz bu DB'yi ayrıca okur.
DEFAULT_DB = os.path.join(REPO_DIR, "warehouse", "product", "tkgm_alim_satim.sqlite")


def init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tkgm_alim_satim_yogunlugu (
            analiz_tip INTEGER NOT NULL,
            yil INTEGER NOT NULL,
            parsel_id INTEGER NOT NULL,
            il_id INTEGER,
            enlem REAL,
            boylam REAL,
            sayi INTEGER,
            PRIMARY KEY (analiz_tip, yil, parsel_id)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_astim_bbox
            ON tkgm_alim_satim_yogunlugu (enlem, boylam);
        CREATE INDEX IF NOT EXISTS idx_astim_tip_yil
            ON tkgm_alim_satim_yogunlugu (analiz_tip, yil);

        CREATE TABLE IF NOT EXISTS tkgm_analiz_kapsama (
            analiz_tip INTEGER NOT NULL,
            analiz_tip_ad TEXT,
            il_id INTEGER NOT NULL,
            il_ad TEXT,
            yil INTEGER NOT NULL,
            nokta_sayisi INTEGER,
            toplam_islem INTEGER,
            cekilme_tarihi TEXT,
            PRIMARY KEY (analiz_tip, il_id, yil)
        );
        """
    )
    connection.commit()


def _get_json(session, url: str, retries: int = 4):
    last_error = None
    for attempt in range(retries):
        try:
            response = session.get(url, headers=HEADERS, timeout=120, **_IMPERSONATE)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # ağ/parse hatası
            last_error = str(exc)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"İstek başarısız ({url}): {last_error}")


def load_provinces(session) -> list[dict]:
    data = _get_json(session, f"{BASE}/idariYapi/ilListe")
    features = data.get("features", data) if isinstance(data, dict) else data
    provinces = []
    for feature in features:
        props = feature.get("properties", feature) if isinstance(feature, dict) else {}
        if props.get("id") is not None:
            provinces.append({"id": int(props["id"]), "ad": props.get("text")})
    return sorted(provinces, key=lambda p: p["id"])


def load_analiz_types(session) -> dict[int, str]:
    data = _get_json(session, f"{BASE}/analiz/analizTip")
    return {int(item["id"]): item.get("ad") for item in data}


def already_done(connection, tip: int, il_id: int, yil: int) -> bool:
    row = connection.execute(
        "SELECT nokta_sayisi FROM tkgm_analiz_kapsama "
        "WHERE analiz_tip=? AND il_id=? AND yil=?",
        (tip, il_id, yil),
    ).fetchone()
    return row is not None and row[0] is not None


def ingest_province_year(connection, session, tip: int, tip_ad: str,
                         il: dict, yil: int) -> tuple[int, int]:
    url = f"{BASE}/analiz?AnalizTip={tip}&Yil={yil}&IlId={il['id']}"
    points = _get_json(session, url)
    if not isinstance(points, list):
        points = []
    # API aynı parseli işlem başına birden çok döndürebilir; parsel bazında
    # topla (sayi'ları toplayıp konumu koru) ki PRIMARY KEY işlemleri ezmesin.
    aggregated: dict[int, dict] = {}
    for p in points:
        pid = p.get("parselId")
        if pid is None:
            continue
        pid = int(pid)
        bucket = aggregated.get(pid)
        if bucket is None:
            aggregated[pid] = {
                "enlem": p.get("enlem"),
                "boylam": p.get("boylam"),
                "sayi": int(p.get("sayi") or 0),
            }
        else:
            bucket["sayi"] += int(p.get("sayi") or 0)
    rows = [
        (tip, yil, pid, il["id"], v["enlem"], v["boylam"], v["sayi"])
        for pid, v in aggregated.items()
    ]
    total_tx = sum(r[6] for r in rows)
    connection.executemany(
        "INSERT OR REPLACE INTO tkgm_alim_satim_yogunlugu "
        "(analiz_tip, yil, parsel_id, il_id, enlem, boylam, sayi) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    connection.execute(
        "INSERT OR REPLACE INTO tkgm_analiz_kapsama "
        "(analiz_tip, analiz_tip_ad, il_id, il_ad, yil, nokta_sayisi, toplam_islem, cekilme_tarihi) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (tip, tip_ad, il["id"], il["ad"], yil, len(rows), total_tx,
         datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    return len(rows), total_tx


def main() -> int:
    parser = argparse.ArgumentParser(description="TKGM alım-satım yoğunluğu ambar toplayıcısı")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--tipler", default="1", help="Virgüllü AnalizTip id (varsayılan 1: Alım Satım Yoğunluğu)")
    parser.add_argument("--yil-bas", type=int, default=2021)
    parser.add_argument("--yil-bit", type=int, default=2025)
    parser.add_argument("--iller", default="", help="Virgüllü il id filtresi (boş=81 il)")
    parser.add_argument("--shard", default="", help="'i/N' — 81 ili N makineye böler, bu makine i. dilimi çeker (0 veya 1 tabanlı)")
    parser.add_argument("--limit-il", type=int, default=0, help="İlk N il (test için)")
    parser.add_argument("--sleep", type=float, default=0.4)
    parser.add_argument("--force", action="store_true", help="Çekilmiş kombinasyonları da yeniden çek")
    args = parser.parse_args()

    tipler = [int(t) for t in args.tipler.split(",") if t.strip()]
    yillar = list(range(args.yil_bas, args.yil_bit + 1))
    il_filter = {int(x) for x in args.iller.split(",") if x.strip()}

    connection = sqlite3.connect(args.db)
    init_schema(connection)
    session = http.Session()

    provinces = load_provinces(session)
    if il_filter:
        provinces = [p for p in provinces if p["id"] in il_filter]
    if args.shard:
        # 'i/N' → il listesini id sırasına göre N dilime böl (round-robin, dengeli).
        # i 1 tabanlıdır (1..N); workflow matrix'i 1..40 verir.
        index_raw, _, count_raw = args.shard.partition("/")
        shard_count = int(count_raw)
        shard_index = (int(index_raw) - 1) % shard_count
        provinces = [p for i, p in enumerate(provinces) if i % shard_count == shard_index]
        print(f"Shard {int(index_raw)}/{shard_count} (dizin {shard_index}): "
              f"{len(provinces)} il → {[p['ad'] for p in provinces]}")
    if args.limit_il:
        provinces = provinces[:args.limit_il]
    type_names = load_analiz_types(session)

    combos = len(tipler) * len(provinces) * len(yillar)
    print(f"Hedef: {len(tipler)} tip × {len(provinces)} il × {len(yillar)} yıl = {combos} sorgu")
    print(f"DB: {args.db}")

    done = grand_points = grand_tx = 0
    t0 = time.time()
    for tip in tipler:
        tip_ad = type_names.get(tip, str(tip))
        for il in provinces:
            for yil in yillar:
                done += 1
                if not args.force and already_done(connection, tip, il["id"], yil):
                    print(f"[{done}/{combos}] atla (çekilmiş) tip={tip} {il['ad']} {yil}")
                    continue
                try:
                    n, tx = ingest_province_year(connection, session, tip, tip_ad, il, yil)
                    grand_points += n
                    grand_tx += tx
                    hiz = done / max(time.time() - t0, 1e-6)
                    print(f"[{done}/{combos}] tip={tip} {il['ad']} {yil}: "
                          f"{n:,} nokta, {tx:,} işlem  ({hiz:.1f} sorgu/sn)")
                except Exception as exc:
                    print(f"[{done}/{combos}] HATA tip={tip} {il['ad']} {yil}: {exc}", file=sys.stderr)
                time.sleep(args.sleep)

    print(f"\nBitti. Toplam {grand_points:,} nokta, {grand_tx:,} işlem, "
          f"{time.time() - t0:.0f} sn.")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
