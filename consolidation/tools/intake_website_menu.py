#!/usr/bin/env python3
"""
YENİ VERİ GİRİŞİ — Restoran web sitelerinden yapısal menü (schema.org Menu / MenuItem / Offer) toplayıcısı.

Hedef listesi: OSM gıda POI'leri (website/contact:website/url/menu etiketi) — official_public kaynaktan gelen adresler.
Kurallar: her site için robots.txt kontrolü (urllib.robotparser, bizim UA), site başına en fazla 3 sayfa (ana sayfa + 'menu' bağlantıları),
istekler arası 3–5 sn, tek iş parçacığı, 403/429 → o siteyi bırak. Ham: her sayfanın JSON-LD blokları byte-birebir + sayfa sha256 (extract/).
Çıktı: records.jsonl (site_result + menu_item), fetch_log.jsonl, state.json (checkpoint).
  python3 intake_website_menu.py --dry-run | --run --limit 300
"""
import argparse, datetime as dt, gzip, hashlib, json, random, re, time, urllib.request, urllib.error, urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 GEOPROP-research/1.0 (polite)"
SRC = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE" / "osm_extract" / "turkey-latest_2026-09-13" / "food_pois.geojsonseq"
ROOT = Path.home() / "Desktop" / "GEOPROP_RAW_INTAKE" / "website_menu" / "2026-09-19"
PARSER = "WEB_MENU_PARSER_V1"; DELAY = (3.0, 5.0)


def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "tr-TR,tr;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.geturl(), r.read()
    except urllib.error.HTTPError as e: return e.code, url, b""
    except Exception as e: return 0, url, repr(e)[:200].encode()


def robots_ok(base):
    rp = urllib.robotparser.RobotFileParser()
    try:
        st, _, body = get(urljoin(base, "/robots.txt"), 15)
        if st == 200: rp.parse(body.decode("utf-8", "replace").splitlines()); return rp.can_fetch(UA, base), "robots_parsed"
        return True, f"robots_http_{st}"
    except Exception as e: return True, f"robots_err"


def jsonld_blocks(html):
    return re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.+?)</script>', html, re.DOTALL | re.IGNORECASE)


def walk(obj, out):
    if isinstance(obj, dict):
        t = obj.get("@type"); t = t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else None)
        if t in ("MenuItem", "Product") and (obj.get("offers") or obj.get("price")):
            off = obj.get("offers") or {}
            if isinstance(off, list): off = off[0] if off else {}
            out.append({"name": obj.get("name"), "description": obj.get("description"), "price": off.get("price") or obj.get("price"), "currency": off.get("priceCurrency"), "type": t})
        for v in obj.values(): walk(v, out)
    elif isinstance(obj, list):
        for v in obj: walk(v, out)


def parse(html):
    items = []; menus = 0; types = set(); menu_urls = []
    for b in jsonld_blocks(html):
        try: j = json.loads(b)
        except Exception:
            try: j = json.loads(re.sub(r",\s*}", "}", b))
            except Exception: continue
        objs = j if isinstance(j, list) else [j]
        for o in objs:
            if isinstance(o, dict):
                t = o.get("@type"); types.add(str(t))
                if o.get("hasMenu"):
                    hm = o["hasMenu"]; menus += 1
                    if isinstance(hm, str): menu_urls.append(hm)
                    elif isinstance(hm, dict) and hm.get("url"): menu_urls.append(hm["url"])
        walk(j, items)
    return items, menus, types, menu_urls


def main():
    ap = argparse.ArgumentParser(); g = ap.add_mutually_exclusive_group(required=True); g.add_argument("--dry-run", action="store_true"); g.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=300); a = ap.parse_args()
    targets = []
    for line in open(SRC, encoding="utf-8"):
        line = line.strip().lstrip("\x1e")
        if not line: continue
        f = json.loads(line); p = f["properties"]
        w = p.get("website") or p.get("contact:website") or p.get("url") or p.get("menu") or p.get("website:menu")
        if not w: continue
        if not w.startswith("http"): w = "https://" + w
        g_ = f.get("geometry") or {}; c = g_.get("coordinates") if g_.get("type") == "Point" else None
        targets.append({"osm_id": f.get("id") or p.get("@id"), "name": p.get("name"), "amenity": p.get("amenity") or p.get("shop"), "website": w.strip(), "menu_tag": p.get("menu") or p.get("website:menu"), "lon": c[0] if c else None, "lat": c[1] if c else None, "addr_city": p.get("addr:city"), "addr_district": p.get("addr:district")})
    seen = set(); uniq = []
    for t in targets:
        host = urlparse(t["website"]).netloc.lower()
        if host and host not in seen: seen.add(host); uniq.append(t)
    ROOT.mkdir(parents=True, exist_ok=True); (ROOT / "extract").mkdir(exist_ok=True)
    state_p = ROOT / "state.json"; state = json.loads(state_p.read_text()) if state_p.exists() else {"done": []}
    todo = [t for t in uniq if urlparse(t["website"]).netloc.lower() not in set(state["done"])]
    print(f"OSM web siteli gıda POI: {len(targets):,} → benzersiz site: {len(uniq):,} | işlenmiş {len(state['done'])} | bu çalıştırma: {min(a.limit, len(todo))}")
    if a.dry_run: return
    log = open(ROOT / "fetch_log.jsonl", "a", encoding="utf-8"); rec = open(ROOT / f"records_{PARSER}.jsonl", "a", encoding="utf-8")
    n_menu_sites = n_items = 0; t0 = time.time()
    for i, t in enumerate(todo[: a.limit], 1):
        host = urlparse(t["website"]).netloc.lower(); base = f"{urlparse(t['website']).scheme}://{host}/"
        ok, why = robots_ok(base)
        res = {"record_kind": "site_result", "osm_id": t["osm_id"], "name": t["name"], "website": t["website"], "host": host, "robots": why, "allowed": ok, "fetched_at": now(), "pages": [], "menu_items": 0, "jsonld_types": [], "parser_version": PARSER, "acquisition_class": "web_research", "source_of_url": "OSM (official_public)"}
        if not ok:
            log.write(json.dumps({"host": host, "event": "robots_disallow", "at": now()}) + "\n"); rec.write(json.dumps(res, ensure_ascii=False) + "\n"); state["done"].append(host); continue
        pages = [t["website"]] + ([t["menu_tag"]] if t["menu_tag"] and t["menu_tag"].startswith("http") else [])
        visited = set(); items_all = []; types_all = set(); depth = 0
        while pages and depth < 3:
            url = pages.pop(0)
            if url in visited: continue
            visited.add(url); depth += 1
            st, final, body = get(url); time.sleep(random.uniform(*DELAY))
            html = body.decode("utf-8", "replace") if st == 200 else ""
            sha = hashlib.sha256(body).hexdigest() if st == 200 else None
            items, menus, types, menu_urls = parse(html) if html else ([], 0, set(), [])
            if st == 200:
                blocks = jsonld_blocks(html)
                (ROOT / "extract" / f"{host}_{depth}.json.gz").write_bytes(gzip.compress(json.dumps({"url": url, "final_url": final, "fetched_at": now(), "sha256": sha, "jsonld_blocks": blocks, "title": (re.search(r"<title>(.*?)</title>", html, re.S) or [None, None])[1]}, ensure_ascii=False).encode()))
            res["pages"].append({"url": url, "http": st, "sha256": sha, "jsonld_blocks": len(jsonld_blocks(html)) if html else 0, "items": len(items)})
            items_all.extend(items); types_all |= types
            if st in (403, 429): log.write(json.dumps({"host": host, "event": f"http_{st}", "at": now()}) + "\n"); break
            for mu in menu_urls:
                if mu not in visited: pages.append(urljoin(url, mu))
            if depth == 1 and html and not menu_urls:
                for m in re.findall(r'href="([^"#]{1,120})"', html):
                    if re.search(r"(?i)men[uü]", m) and urlparse(urljoin(url, m)).netloc.lower() == host: pages.append(urljoin(url, m)); break
        res["menu_items"] = len(items_all); res["jsonld_types"] = sorted(types_all)[:10]
        rec.write(json.dumps(res, ensure_ascii=False) + "\n")
        for it in items_all:
            rec.write(json.dumps({"record_kind": "menu_item", "osm_id": t["osm_id"], "host": host, "fetched_at": now(), "parser_version": PARSER, **it}, ensure_ascii=False) + "\n")
        rec.flush(); state["done"].append(host); state_p.write_text(json.dumps(state))
        if items_all: n_menu_sites += 1; n_items += len(items_all)
        print(f"  [{i}/{min(a.limit, len(todo))}] {host[:40]:40s} robots={ok} sayfa={len(res['pages'])} jsonld={sum(p['jsonld_blocks'] for p in res['pages'])} menü kalemi={len(items_all)} | menülü site={n_menu_sites} kalem={n_items}", flush=True)
    log.write(json.dumps({"event": "finish", "at": now(), "sites": min(a.limit, len(todo)), "menu_sites": n_menu_sites, "items": n_items, "seconds": round(time.time() - t0)}) + "\n")
    print(f"BİTTİ site={min(a.limit, len(todo))} menülü={n_menu_sites} kalem={n_items}")


if __name__ == "__main__":
    main()
