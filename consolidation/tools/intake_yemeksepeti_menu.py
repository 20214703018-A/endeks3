#!/usr/bin/env python3
"""
YENİ VERİ GİRİŞİ — Yemeksepeti mekân + menü/fiyat toplayıcısı (STANDARD v1.0.0 §10.1 uyumlu)

İzin: robots.txt mekân sayfalarını (/restaurant/<kod>/<slug>) yasaklamıyor; sitemap yayınlıyor (adventure-map-restaurant-*.xml).
Nezaket: tek iş parçacığı, istekler arası 3–6 sn, sabit ve dürüst User-Agent, 403/429/CAPTCHA → geri çekil, 3 kez üst üste → DUR.
Ham: her sayfa byte-birebir gzip (pages/<kod>.html.gz) + .meta (url, HTTP, sha256, zaman). Kayıtlar: records.jsonl (mekân + menü kalemleri, provenance).
Checkpoint: state.json (işlenen kodlar); kaldığı yerden devam. Kaynak ağaca (GEOPROP) yazmaz.

  python3 intake_yemeksepeti_menu.py --dry-run            (sitemap sayımı, plan)
  python3 intake_yemeksepeti_menu.py --run --limit 500     (pilot)
"""
from __future__ import annotations
import argparse, datetime as dt, gzip, hashlib, json, random, re, sys, time
from pathlib import Path
import urllib.request, urllib.error

PARSER_VERSION = "YS_MENU_PARSER_V2"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 GEOPROP-research/1.0 (polite; contact via site form)"
SITEMAPS = [f"https://www.yemeksepeti.com/adventure-map/adventure-map-restaurant-{i}.xml" for i in range(0, 6)]
ROOT = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE" / "yemeksepeti_menu" / "2026-09-18"  # tek sürekli çekim dizini; yeni çekim için tarihi değiştir
DELAY = (3.0, 6.0)


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

PRIORITY_CITIES = {"istanbul", "bursa", "kocaeli", "tekirdag", "balikesir", "izmir", "aydin", "mugla", "manisa", "denizli", "antalya", "mersin", "adana", "hatay", "ankara", "konya", "kayseri", "eskisehir", "samsun", "trabzon", "ordu"}


def norm_city(s):
    if not s: return None
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(str.maketrans("çğıöşüâîû", "cgiosuaiu"))
    return re.sub(r"[^a-z]", "", s)


def raw_extract(html: str) -> dict:
    """Ayrıştırmada kullanılan parçaların BYTE-BİREBİR alt dizgileri: parser değişse de bu parçalar yeniden ayrıştırılabilir."""
    return {"jsonld_blocks": re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.+?)</script>', html, re.DOTALL),
            "menu_category_fragments": [m.group(0) for m in re.finditer(r'"RestaurantMenuCategory:\d+":\{[^\}]+\}', html)],
            "product_fragments": [m.group(0) for m in re.finditer(r'"RestaurantProductData:\d+":\{.*?\}(?=,"Restaurant|\}\}\})', html)],
            "vendor_fragment": (lambda m: m.group(0) if m else None)(re.search(r'"deliveryFee":\{"__typename":"DynamicPricingDeliveryFee".{0,600}', html)),
            "time_fragment": (lambda m: m.group(0) if m else None)(re.search(r'"delivery":\{"__typename":"TimeEstimation".{0,400}', html)),
            "schedule_fragments": re.findall(r'"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)":\{"__typename":"DaySchedule","delivery":\[.*?\]', html),
            "title": (lambda m: m.group(1) if m else None)(re.search(r"<title>(.*?)</title>", html, re.S))}


def http_get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "tr-TR,tr;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}, (e.read() if hasattr(e, "read") else b"")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        # ağ kesintisi: engel DEĞİL; 0 döner, çağıran bekleyip tekrar dener
        return 0, {"network_error": repr(e)[:200]}, b""


def load_sitemap_urls():
    urls = []
    for sm in SITEMAPS:
        st, _, body = http_get(sm)
        if st != 200:
            print(f"  sitemap {sm} HTTP {st}"); continue
        found = re.findall(r"<loc>(.*?)</loc>", body.decode("utf-8", "replace"))
        found = [u for u in found if "/restaurant/" in u]
        print(f"  {sm.split('/')[-1]}: {len(found)} mekân"); urls.extend(found)
        time.sleep(1.5)
    return urls


def parse_page(html: str, url: str):
    """JSON-LD mekân + Apollo state menü kalemleri. Hiçbir değer türetilmez; ham alanlar olduğu gibi."""
    venue = None
    for s in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.+?)</script>', html, re.DOTALL):
        try:
            j = json.loads(s)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") in ("Restaurant", "FoodEstablishment", "LocalBusiness", "Store", "GroceryStore"):
            venue = j; break
    cats = {f"RestaurantMenuCategory:{m.group(1)}": m.group(3) for m in re.finditer(r'"RestaurantMenuCategory:(\d+)":(\{[^\}]+"title":"([^"]+)"[^\}]*\})', html)}
    items = []
    for m in re.finditer(r'"RestaurantProductData:(\d+)":(\{.*?\}(?=,"Restaurant|\}\}\}))', html):
        raw = re.sub(r":undefined", ":null", m.group(2))
        try:
            j = json.loads(raw)
        except Exception:
            items.append({"product_ref": m.group(1), "parse_error": True, "raw_fragment": m.group(2)[:2000]}); continue
        pa = j.get("priceAttributes") or {}
        cat_ref = j.get("menuCategory", {}).get("__ref") if isinstance(j.get("menuCategory"), dict) else None
        items.append({"product_ref": m.group(1), "title": j.get("title"), "description": j.get("description"), "original_price": pa.get("originalPrice"), "discounted_price": pa.get("discountedPrice"),
                      "is_sold_out": j.get("isSoldOut"), "category_ref": cat_ref, "category_title": cats.get(cat_ref), "raw_json": j})
    metrics = {"note": "deliveryFee/time: platformun varsayılan konum için hesapladığı tahmin (konuma bağlı); ham değer olduğu gibi"}
    def grab(pat, key, conv=str):
        mm = re.search(pat, html); metrics[key] = (conv(mm.group(1)) if mm else None)
    grab(r'"duration":\{"__typename":"TimeEstimationDuration","upperLimitInMinutes":(\d+)', "delivery_time_upper_min", int)
    grab(r'"duration":\{"__typename":"TimeEstimationDuration","upperLimitInMinutes":\d+,"lowerLimitInMinutes":(\d+)', "delivery_time_lower_min", int)
    grab(r'"minimumOrderValue":\{"__typename":"DynamicPricingMinimumOrderValue","total":([0-9\.]+)', "minimum_order_value", float)
    grab(r'"deliveryFee":\{"__typename":"DynamicPricingDeliveryFee","total":([0-9\.]+)', "delivery_fee_total", float)
    grab(r'"deliveryFee":\{"__typename":"DynamicPricingDeliveryFee","total":[0-9\.]+,"basketValueFee":[^,]*,"original":([0-9\.]+)', "delivery_fee_original", float)
    grab(r'"rating":\{"__typename":"VendorRating","count":(\d+)', "rating_count", int)
    grab(r'"rating":\{"__typename":"VendorRating","count":\d+,"value":([0-9\.]+)', "rating_value", float)
    grab(r'"deliveryProvider":"([A-Z_]+)"', "delivery_provider")
    grab(r'"minimumDeliveryTime":(\d+)', "minimum_delivery_time", int)
    hours = {}
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        mm = re.search(r'"' + day + r'":\{"__typename":"DaySchedule","delivery":\[(.*?)\]', html)
        if mm: hours[day] = re.findall(r'"from":"([0-9:]+)","to":"([0-9:]+)"', mm.group(1))
    metrics["delivery_hours"] = hours or None
    t = re.search(r"<title>(.*?)</title>", html, re.S); title = t.group(1).strip() if t else None
    if venue is None and title and re.search(r"Online Yemek Sipari", title): metrics["page_kind"] = "city_landing_redirect (mekân listede değil / kaldırılmış)"
    elif venue is None: metrics["page_kind"] = "no_venue_jsonld"
    else: metrics["page_kind"] = "venue_page"
    metrics["page_title"] = title
    return venue, items, metrics


def main():
    ap = argparse.ArgumentParser(); g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true"); g.add_argument("--run", action="store_true"); g.add_argument("--reparse", action="store_true", help="pages/ altındaki ham sayfalardan records_<parser>.jsonl'yi yeniden üret (deterministik)")
    ap.add_argument("--limit", type=int, default=500); ap.add_argument("--max-seconds", type=int, default=3600 * 6)
    ap.add_argument("--store", choices=["html", "extract"], default="extract", help="html: tam sayfa gzip; extract: yalnız ayrıştırılan ham parçalar (JSON-LD + ürün/teslimat JSON)")
    ap.add_argument("--priority-only", action="store_true", help="yalnız PRIORITY_CITIES için extract/kayıt; diğerleri için tek satır iz")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--targets", choices=["sitemap", "staging_priority"], default="sitemap", help="staging_priority: staging'deki 21-il mekân URL'leri (sitemap yerine)")
    ap.add_argument("--delay-min", type=float, default=None); ap.add_argument("--delay-max", type=float, default=None)
    ap.add_argument("--wait-unblock", action="store_true", help="başlamadan önce 15 dk'da bir tek istekle engelin kalkmasını bekle (en çok 6 saat)")
    a = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True); (ROOT / "pages").mkdir(exist_ok=True)
    state_p = ROOT / "state.json"; state = json.loads(state_p.read_text()) if state_p.exists() else {"done": [], "blocked_streak": 0}
    log = open(ROOT / "fetch_log.jsonl", "a", encoding="utf-8")
    def L(rec): log.write(json.dumps(rec, ensure_ascii=False) + "\n"); log.flush()
    if a.reparse:
        out = open(ROOT / f"records_{PARSER_VERSION}.jsonl", "w", encoding="utf-8"); n = 0
        for gz in sorted((ROOT / "pages").glob("*.html.gz")):
            meta = json.loads(gz.with_name(gz.name.replace(".html.gz", ".meta.json")).read_text())
            text = gzip.decompress(gz.read_bytes()).decode("utf-8", "replace"); code = gz.name.replace(".html.gz", "")
            venue, items, metrics = parse_page(text, meta["url"])
            out.write(json.dumps({"record_kind": "venue_page", "venue_code": code, "url": meta["url"], "fetched_at": meta["fetched_at"], "http_status": meta["http_status"], "page_sha256": meta["sha256"], "parser_version": PARSER_VERSION,
                                  "venue_jsonld": venue, "metrics": metrics, "menu_item_count": len(items), "acquisition_class": "web_research", "sensitivity": "none"}, ensure_ascii=False) + "\n")
            for it in items:
                out.write(json.dumps({"record_kind": "menu_item", "venue_code": code, "url": meta["url"], "fetched_at": meta["fetched_at"], "page_sha256": meta["sha256"], "parser_version": PARSER_VERSION, **it}, ensure_ascii=False) + "\n")
            n += 1
        out.close(); print(f"yeniden ayrıştırıldı: {n} sayfa → records_{PARSER_VERSION}.jsonl"); return
    global DELAY
    if a.delay_min and a.delay_max: DELAY = (a.delay_min, a.delay_max)
    if a.targets == "staging_priority":
        import duckdb
        con = duckdb.connect(str(Path.home() / "Desktop/GEOPROP_CONSOLIDATION/staging/geoprop_staging.duckdb"), read_only=True)
        rows = con.execute("SELECT raw_url, raw_sehir, raw_restoran_kodu FROM stg.poi_business__stg_yemek_ve_market_teslimat_ekosistemi__uye_restoranlar_ve_hacim WHERE raw_url IS NOT NULL").fetchall()
        urls = [u for u, city, code in rows if norm_city(city) in PRIORITY_CITIES]
        print(f"hedef: staging'deki 21 öncelikli il mekânları: {len(urls):,} / {len(rows):,}")
    else:
        print("sitemap okunuyor…"); urls = load_sitemap_urls()
    if a.wait_unblock and not a.dry_run:
        probe = urls[0]; waited = 0
        while waited <= 6 * 3600:
            st, _, body = http_get(probe)
            if st == 200 and b"px-captcha" not in body[:3000]:
                print(f"engel kalkmış görünüyor (HTTP {st}); başlıyor."); L({"event": "unblock_probe_ok", "at": now(), "waited_s": waited}); break
            print(f"hâlâ engel (HTTP {st}); 15 dk bekleniyor… (toplam {waited//60} dk)", flush=True); L({"event": "unblock_probe_blocked", "at": now(), "http": st, "waited_s": waited})
            time.sleep(900); waited += 900
        else:
            print("6 saat içinde engel kalkmadı; çıkılıyor."); return
    uniq = list(dict.fromkeys(urls)); done = set(state["done"])
    todo = [u for u in uniq if u.rstrip("/").split("/")[-2] not in done]
    print(f"toplam mekân URL: {len(uniq):,}  işlenmiş: {len(done):,}  kalan: {len(todo):,}  bu çalıştırmada: {min(a.limit, len(todo)):,}")
    est = min(a.limit, len(todo)) * (sum(DELAY) / 2 + 1.5)
    print(f"tahmini süre: {est/60:.0f} dk; tahmini disk: ~{min(a.limit, len(todo)) * 0.08:.0f} MB (gzip sayfa) — hedef: {ROOT}")
    if a.dry_run:
        L({"event": "dry_run", "at": now(), "total": len(uniq), "done": len(done), "todo": len(todo)}); return
    L({"event": "start", "at": now(), "limit": a.limit, "delay": DELAY, "ua": UA, "parser": PARSER_VERSION})
    rec_out = open(ROOT / f"records_{PARSER_VERSION}.jsonl", "a", encoding="utf-8")
    t0 = time.time(); n_ok = n_items = n_skip = 0; streak = 0
    (ROOT / "extract").mkdir(exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor
    import threading
    lock = threading.Lock(); stop = {"flag": False}
    def work(idx_url):
        i, url = idx_url
        if stop["flag"] or time.time() - t0 > a.max_seconds: return None
        code = url.rstrip("/").split("/")[-2]
        st, hdrs, body = http_get(url); fetched = now(); sha = hashlib.sha256(body).hexdigest()
        text = body.decode("utf-8", "replace")
        time.sleep(random.uniform(*DELAY))
        return (i, url, code, st, hdrs, body, text, fetched, sha)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(work, list(enumerate(todo[: a.limit], 1))):
            if res is None: continue
            i, url, code, st, hdrs, body, text, fetched, sha = res
            if st == 0:  # ağ yok → 2 dk bekle, bu URL'yi işlenmiş sayma (bir sonraki çalıştırmada tekrar)
                L({"url": url, "event": "network_error", "at": fetched, "err": hdrs.get("network_error")}); print(f"  [{i}] {code}: ağ hatası, 120 sn bekleniyor…", flush=True); time.sleep(120); continue
            blocked = st in (403, 429, 503)
            if blocked:
                streak += 1; L({"url": url, "http": st, "event": "blocked", "at": fetched, "streak": streak})
                print(f"  [{i}] {code}: HTTP {st} — engel/limit sinyali ({streak}/3). Geri çekiliyor…", flush=True)
                state["blocked_streak"] = streak; state_p.write_text(json.dumps(state))
                if streak >= 2: print("2 ardışık engel → DURDU (kimlik değiştirme yok). Sonra --wait-unblock ile devam."); stop["flag"] = True; break
                print("  30 dk soğuma…", flush=True); time.sleep(1800); continue
            streak = 0
            venue, items, metrics = parse_page(text, url) if st == 200 else (None, [], {})
            city = norm_city(((venue or {}).get("address") or {}).get("addressLocality")) if venue else None
            is_priority = (city in PRIORITY_CITIES) if city else False
            keep = (not a.priority_only) or is_priority or venue is None  # mekân bulunamayan sayfalar (kaldırılmış) da iz olarak kalır
            meta = {"url": url, "http_status": st, "headers": hdrs, "fetched_at": fetched, "bytes": len(body), "sha256": sha, "city": city, "priority": is_priority, "store": a.store if keep else "stub"}
            if keep and a.store == "html":
                (ROOT / "pages" / f"{code}.html.gz").write_bytes(gzip.compress(body))
            elif keep:
                (ROOT / "extract" / f"{code}.json.gz").write_bytes(gzip.compress(json.dumps(raw_extract(text), ensure_ascii=False).encode()))
            (ROOT / ("pages" if a.store == "html" else "extract") / f"{code}.meta.json").write_text(json.dumps(meta, ensure_ascii=False))
            rec = {"record_kind": "venue_page", "venue_code": code, "url": url, "fetched_at": fetched, "http_status": st, "page_sha256": sha, "parser_version": PARSER_VERSION, "city_norm": city, "priority": is_priority,
                   "venue_jsonld": venue if keep else None, "metrics": metrics if keep else {"page_kind": metrics.get("page_kind")}, "menu_item_count": len(items) if keep else None, "stored": a.store if keep else "stub", "acquisition_class": "web_research", "sensitivity": "none"}
            rec_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if keep:
                for it in items:
                    rec_out.write(json.dumps({"record_kind": "menu_item", "venue_code": code, "url": url, "fetched_at": fetched, "page_sha256": sha, "parser_version": PARSER_VERSION, **it}, ensure_ascii=False) + "\n")
                n_ok += 1; n_items += len(items)
            else: n_skip += 1
            rec_out.flush(); state["done"].append(code); state["blocked_streak"] = 0
            if i % 10 == 0: state_p.write_text(json.dumps(state))
            name = (venue or {}).get("name") if venue else None
            print(f"  [{i}/{min(a.limit, len(todo))}] {code} HTTP {st} il={city} {'✓' if keep else 'iz'} mekân={name!r} menü={len(items)} | saklanan={n_ok} iz={n_skip} menü={n_items}", flush=True)
    state_p.write_text(json.dumps(state))
    L({"event": "finish", "at": now(), "pages_stored": n_ok, "pages_stub": n_skip, "menu_items": n_items, "seconds": round(time.time() - t0)})
    print(f"BİTTİ saklanan={n_ok} iz={n_skip} menü kalemi={n_items} süre={time.time()-t0:.0f}s → {ROOT}")


if __name__ == "__main__":
    main()
