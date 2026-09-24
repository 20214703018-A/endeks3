"""E-ticaret şirketlerinin adresleri — ETBİS kayıtlı sitelerin kendi sayfalarından.

6563 sayılı Kanun ve Ticari İletişim Yönetmeliği gereği e-ticaret siteleri unvan, adres, MERSİS/vergi no ve iletişim
bilgilerini (künye) yayımlamak zorundadır; mesafeli satış sözleşmesinde de satıcı adresi bulunur.
Girdi: GEOPROP_RAW_INTAKE/etbis_eticaret_siteleri/<tarih>/list_rows.jsonl (site URL, unvan, il)
Her site için: robots.txt'ye uyarak ana sayfa + en çok 3 aday sayfa (iletişim, hakkımızda, künye, mesafeli satış,
KVKK/aydınlatma) indirilir; yalnız çıkarılan alanlar saklanır (HTML saklanmaz):
  - schema.org JSON-LD Organization/LocalBusiness PostalAddress
  - Türkçe adres kalıpları (Mah./Mahallesi, Cad., Sok., Bulvar, No:, posta kodu, il/ilçe adı) — aday metin parçaları
  - MERSİS (16 hane), KEP adresi, telefon (sabit hat) — şirket künyesi
Adres → il/ilçe eşlemesi konsolidasyonda (kanonik geo_entity adlarıyla) yapılır.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.robotparser as rp
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import RAW_ROOT, UA, Intake, now_iso  # noqa: E402

urllib3.disable_warnings()
SRC = RAW_ROOT / "etbis_eticaret_siteleri" / "2026-09-24" / "list_rows.jsonl"
CAND = re.compile(r"iletisim|iletişim|contact|hakkimizda|hakkımızda|about|kunye|künye|mesafeli|satis-sozlesmesi|"
                  r"satış-sözleşmesi|sozlesme|kvkk|aydinlatma|kurumsal|bize-ulasin", re.I)
ADDR = re.compile(r"([A-ZÇĞİÖŞÜa-zçğıöşü0-9\.\-/ ]{2,60}\s(?:Mah\.|Mah\s|Mahallesi\b|Mh\.|MAH\.|MAHALLESİ)[^<>\n]{5,220})")
ADDR_OK = re.compile(r"(No\s*[:.]?\s*\d|Sok|Sk\.|Cad|Cd\.|Bulv|Blv|/\s*[A-ZÇĞİÖŞÜ])")
ADDR2 = re.compile(r"(?:Adres|Address|Merkez|Adresi)\s*[:：]\s*([^<>\n]{15,250})", re.I)
MERSIS = re.compile(r"MERS[İI]S[^0-9]{0,25}(\d[\d ]{14,20}\d)", re.I)
KEP = re.compile(r"[\w.\-]+@[\w\-]+\.kep\.tr", re.I)
PHONE = re.compile(r"(?:\+90|0)\s?\(?(?:2\d{2}|3\d{2}|4\d{2})\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}")
_local = threading.local()


def sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
        _local.s.verify = False
    return _local.s


def text_of(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<br\s*/?>|</(p|div|li|td|span|h\d)>", "\n", html)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    return re.sub(r"[ \t]+", " ", t)


def jsonld_addresses(html):
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            j = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = [j]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                a = x.get("address")
                if isinstance(a, dict):
                    out.append({k: a.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")}
                               | {"org": x.get("name"), "type": x.get("@type")})
                elif isinstance(a, str):
                    out.append({"streetAddress": a, "org": x.get("name"), "type": x.get("@type")})
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
    return out


def extract(html):
    t = text_of(html)
    return {"jsonld": jsonld_addresses(html),
            "addr_mah": list(dict.fromkeys(a.strip() for a in ADDR.findall(t) if ADDR_OK.search(a)))[:6],
            "addr_label": list(dict.fromkeys(a.strip() for a in ADDR2.findall(t)))[:6],
            "mersis": list(dict.fromkeys(re.sub(r"\s", "", m) for m in MERSIS.findall(t)))[:3],
            "kep": list(dict.fromkeys(k.lower() for k in KEP.findall(t)))[:3],
            "telefon": list(dict.fromkeys(PHONE.findall(t)))[:3]}


def fetch(url, timeout=20):
    r = sess().get(url, timeout=timeout, allow_redirects=True)
    ct = r.headers.get("content-type", "")
    if r.status_code != 200 or ("html" not in ct and ct):
        return r.status_code, r.url, ""
    return r.status_code, r.url, r.text[:1_500_000]


def do_site(site):
    base = site if site.startswith("http") else "http://" + site
    host = urlparse(base).netloc
    rec = {"site": site, "host": host, "at": now_iso(), "pages": []}
    robots = rp.RobotFileParser()
    try:
        rr = sess().get(f"{urlparse(base).scheme}://{host}/robots.txt", timeout=10)
        robots.parse(rr.text.splitlines() if rr.status_code == 200 else [])
    except Exception:  # noqa: BLE001
        robots.parse([])
    try:
        if not robots.can_fetch(UA, base):
            rec["robots_disallow"] = True
            return rec
        st, final, html = fetch(base)
        rec["status"], rec["final_url"] = st, final
        if not html:
            return rec
        ex = extract(html)
        rec["pages"].append({"url": final, **ex})
        links = []
        for h in re.findall(r'href=["\']([^"\'#]+)["\']', html, re.I):
            u = urljoin(final, h)
            if urlparse(u).netloc.endswith(urlparse(final).netloc.replace("www.", "")) and CAND.search(u) and u not in links:
                links.append(u)
        # öncelik: iletişim > künye > mesafeli > hakkımızda
        links.sort(key=lambda u: (0 if re.search("iletisim|iletişim|contact|kunye|künye", u, re.I) else
                                  1 if re.search("mesafeli|sozlesme|sözleşme", u, re.I) else 2))
        for u in links[:3]:
            if not robots.can_fetch(UA, u):
                continue
            time.sleep(1.0)
            try:
                st2, f2, h2 = fetch(u)
            except Exception:  # noqa: BLE001
                continue
            if h2:
                rec["pages"].append({"url": f2, **extract(h2)})
    except Exception as e:  # noqa: BLE001
        rec["error"] = repr(e)[:200]
    return rec


def main():
    it = Intake("eticaret_sirket_adresleri")
    sites, meta = [], {}
    lines = SRC.read_text().splitlines()
    for extra in sorted(SRC.parent.glob("all_rows*.jsonl")):  # il bilgisi olmayan siteler (tam liste turları)
        lines += extra.read_text().splitlines()
    for l in lines:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        for r in d["rows"]:
            s = (r.get("site") or "").strip()
            if s and s not in meta:
                meta[s] = {"unvan": r.get("unvan"), "il": d.get("city"), "siteId": r.get("siteId")}
                sites.append(s)
    out = it.dir / "sites.jsonl"
    done = set()
    if out.exists():
        for l in out.read_text().splitlines():
            try:
                done.add(json.loads(l)["site"])
            except ValueError:  # yarım kalmış satır
                pass
    todo = [s for s in sites if s not in done]
    it.log(f"{len(sites)} site (ETBİS listesinden), {len(todo)} kaldı")
    lock = threading.Lock()
    n = 0
    with ThreadPoolExecutor(8) as ex:
        for f in as_completed([ex.submit(do_site, s) for s in todo]):
            rec = f.result()
            rec.update({"etbis_" + k: v for k, v in meta.get(rec["site"], {}).items()})
            with lock, out.open("a") as fo:
                fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 500 == 0:
                it.log(f"{n}/{len(todo)} site işlendi")
    it._record(out, source_url="(ETBİS kayıtlı siteler, her sitenin kendi sayfaları)", method="site_contact_extraction",
               rows=len(done) + n, note="yalnız çıkarılmış alanlar; HTML saklanmadı; robots.txt uygulandı")


if __name__ == "__main__":
    main()
