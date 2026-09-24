"""Cimri.com fiyat karşılaştırma — ürün kataloğu + güncel teklifler (pazaryeri/satıcı, fiyat, stok) + son 3 ay fiyat geçmişi.

Kaynak: https://www.cimri.com/sitemaps/sitemap.xml (robots.txt'de ilan edilmiş). robots.txt'de yasaklı yollar
(/api/, /offer/, /click, /arama, /market/api/graphql) KULLANILMAZ; yalnız ürün sayfasındaki schema.org JSON-LD
'Product' + 'AggregateOffer' okunur: ürün adı, marka, kategori yolu, sku, teklif sayısı, en düşük/en yüksek fiyat ve
her teklif için satıcı ("Pazaryeri/Satıcı" biçiminde, ör. "PttAVM/Ceptocep", "Trendyol/..."), fiyat, stok, teklif URL'si.
Aşamalar: catalog (sitemap'ler → URL + kategori), offers (ürün sayfaları; kategoriler arası dönüşümlü sıra).
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

SM = "https://www.cimri.com/sitemaps/sitemap.xml"


def catalog(it: Intake):
    idx = it.get(SM).text
    subs = re.findall(r"<loc>([^<]+)</loc>", idx)
    out = it.dir / "catalog_urls.jsonl.gz"
    if out.exists():
        it.log("katalog zaten var")
        return
    n = 0
    with gzip.open(out, "wt") as fo:
        for s in subs:
            x = it.get(s, timeout=120).text
            locs = re.findall(r"<loc>([^<]+)</loc>", x)
            kind = s.rsplit("/", 1)[-1].replace(".xml", "")
            if kind == "product":  # alt sitemap dizini
                for ps in locs:
                    y = it.get(ps, timeout=180).text
                    for u in re.findall(r"<loc>([^<]+)</loc>", y):
                        fo.write(json.dumps({"kind": "product", "sitemap": ps, "url": u,
                                             "category": u.split("cimri.com/")[1].split("/")[0]}) + "\n")
                        n += 1
                    it.log(f"katalog: {ps} → toplam {n}")
            else:
                for u in locs:
                    fo.write(json.dumps({"kind": kind, "sitemap": s, "url": u}) + "\n")
    it._record(out, source_url=SM, method="sitemap_walk", rows=sum(1 for _ in gzip.open(out, 'rt')))


def price_history(html):
    """Sayfanın kendi HTML'indeki 'Tablo Görünümü' fiyat geçmişi (son ~3 ay; en düşük fiyatın değiştiği günler)."""
    i = html.find("priceHistoryWrapper")
    if i < 0:
        return []
    m = re.search(r"<table.*?</table>", html[i:i + 120000], re.S)
    if not m:
        return []
    out = []
    for tr in re.findall(r"<tr.*?</tr>", m.group(0), re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td.*?</td>", tr, re.S)]
        if len(cells) >= 2 and re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
            def num(x):
                x = x.replace("TL", "").replace(" ", "").replace(".", "").replace(",", ".")
                try:
                    return float(x)
                except ValueError:
                    return None
            d, mth, y = cells[0][:10].split("/")
            out.append({"date": f"{y}-{mth}-{d}", "min_price": num(cells[1]), "change": num(cells[2]) if len(cells) > 2 else None,
                        "raw": cells[:3]})
    return out


def parse(html):
    prod, crumbs = None, None
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(j, dict) and j.get("@type") == "Product":
            prod = j
        elif isinstance(j, dict) and j.get("@type") == "BreadcrumbList":
            crumbs = [e.get("name") for e in j.get("itemListElement") or []]
    if not prod:
        return None
    off = prod.get("offers") or {}
    return {"name": prod.get("name"), "sku": prod.get("sku"), "brand": (prod.get("brand") or {}).get("name"),
            "category": prod.get("category"), "breadcrumb": crumbs,
            "offerCount": off.get("offerCount"), "lowPrice": off.get("lowPrice"), "highPrice": off.get("highPrice"),
            "currency": off.get("priceCurrency"),
            "offers": [{"seller": (o.get("seller") or {}).get("name"), "price": o.get("price"),
                        "availability": (o.get("availability") or "").rsplit("/", 1)[-1], "url": o.get("url"),
                        "condition": (o.get("itemCondition") or "").rsplit("/", 1)[-1]} for o in off.get("offers") or []],
            "properties": {p.get("name"): p.get("value") for p in prod.get("additionalProperty") or [] if isinstance(p, dict)},
            "price_history": price_history(html)}


def offers(it: Intake, limit: int):
    cat = [json.loads(l) for l in gzip.open(it.dir / "catalog_urls.jsonl.gz", "rt") if '"product"' in l]
    by = defaultdict(deque)
    for c in cat:
        by[c["category"]].append(c["url"])
    out = it.dir / "offers.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["url"] for l in out.open() if l.strip()}
    it.log(f"{len(cat)} ürün, {len(by)} kategori, {len(done)} tamam")
    n = 0
    queues = deque(by.values())
    while queues and n < limit:
        q = queues.popleft()
        u = None
        while q:
            cand = q.popleft()
            if cand not in done:
                u = cand
                break
        if q:
            queues.append(q)
        if not u:
            continue
        try:
            r = it.get(u, timeout=60)
        except Exception as e:  # noqa: BLE001
            it.log(f"hata {u}: {e}")
            continue
        if r.status_code in (403, 429):
            it.log(f"engel {r.status_code} — 10 dk bekleniyor")
            time.sleep(600)
            continue
        rec = {"url": u, "status": r.status_code, "at": now_iso(), "product": parse(r.text) if r.status_code == 200 else None}
        with out.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
        if n % 1000 == 0:
            it.log(f"teklif: {n} ürün sayfası")
    it._record(out, source_url=SM, method="jsonld_product_offers", rows=len(done) + n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["catalog", "offers", "all"])
    ap.add_argument("--limit", type=int, default=10**9)
    a = ap.parse_args()
    it = Intake("cimri_fiyat_karsilastirma", rate=1.0)
    if a.phase in ("catalog", "all"):
        catalog(it)
    if a.phase in ("offers", "all"):
        offers(it, a.limit)


if __name__ == "__main__":
    main()
