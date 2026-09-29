"""E-ticaret satıcıları — Pazarama pazaryeri mağaza sayfaları (satıcı şirket bilgileri, il/ilçe).

Kaynak: https://www.pazarama.com/sitemaps/magazalar/sitemap_0.xml (robots.txt'de ilan edilmiş sitemap; ~20 bin mağaza)
Her mağaza sayfasının Nuxt durumundaki UI_STORE_INFO bloğu: satıcı kimliği, mağaza adı, ticari unvan, adres,
il, ilçe, KEP adresi, vergi no, MERSİS no, satıcı puanı, VIP, etiketler. Minified değişken referansları
(__NUXT__ fonksiyon argümanları) çözülerek gerçek değerlere dönüştürülür.
Kişisel veri en aza indirilir (kullanıcı onayı 2026-09-24): kişisel e-posta alınmaz; 11 haneli vergi no (şahıslarda
TC kimlik no) maskelenir (yalnız ilk 2 + son 2 hane); unvan/adres/KEP → STANDARD §3.3 'restricted' sütun.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

SM = "https://www.pazarama.com/sitemaps/magazalar/sitemap_0.xml"
FIELDS = ["sellerId", "sellerName", "sellerTradeName", "sellerAddress", "sellerCityName", "sellerDistrictName",
          "kep", "taxNumber", "sellerMersisNumber", "sellerScore", "sellerRating", "isVip", "deliveryType",
          "sellerTags", "shareUrl", "sellerRatingDescriptionTitle"]  # kişisel e-posta (customerVisibleEmail) alınmaz


def split_args(s):
    """JS argüman listesini üst düzey virgüllerden böl (dizeler/parantezler korunur)."""
    out, depth, cur, i, q = [], 0, [], 0, None
    while i < len(s):
        ch = s[i]
        if q:
            cur.append(ch)
            if ch == "\\":
                cur.append(s[i + 1]); i += 1
            elif ch == q:
                q = None
        elif ch in "\"'":
            q = ch; cur.append(ch)
        elif ch in "([{":
            depth += 1; cur.append(ch)
        elif ch in ")]}":
            depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        out.append("".join(cur).strip())
    return out


def js_val(tok):
    if tok is None:
        return None
    if tok in ("void 0", "null", "undefined"):
        return None
    if tok == "true" or tok == "!0":
        return True
    if tok == "false" or tok == "!1":
        return False
    if tok[:1] in "\"'":
        try:
            return json.loads('"' + tok[1:-1].replace('\\"', '"').replace('"', '\\"') + '"') if tok[0] == "'" else json.loads(tok)
        except ValueError:
            return tok[1:-1]
    try:
        return float(tok) if "." in tok else int(tok)
    except ValueError:
        return tok


def parse_store(html):
    m = re.search(r"window\.__NUXT__=\(function\(([^)]*)\)\{(.*)\}\((.*)\)\);?</script>", html, re.S)
    if not m:
        return None
    params = m.group(1).split(",")
    args = split_args(m.group(3))
    env = {p: (args[i] if i < len(args) else None) for i, p in enumerate(params)}
    body = m.group(2)
    b = re.search(r"UI_STORE_INFO:\{(.*?)\},UI_", body, re.S)
    if not b:
        return None
    rec = {}
    for f in FIELDS:
        v = re.search(r"(?:^|,)" + f + r":(\"(?:[^\"\\]|\\.)*\"|\[[^\]]*\]|[^,]+)", b.group(1))
        if not v:
            continue
        tok = v.group(1)
        if re.fullmatch(r"[A-Za-z_$][\w$]*", tok) and tok in env:
            tok = env[tok]
        rec[f] = js_val(tok) if isinstance(tok, str) else tok
    tn = str(rec.get("taxNumber") or "")
    if tn.isdigit() and len(tn) == 11:  # TC kimlik no → maskele
        rec["taxNumber"] = tn[:2] + "*" * 7 + tn[-2:]
        rec["taxNumber_type"] = "TCKN_maskeli"
    elif tn.isdigit() and len(tn) == 10:
        rec["taxNumber_type"] = "VKN"
    return rec


def main():
    it = Intake("eticaret_pazarama_satici", rate=1.2)
    xml = it.get(SM, timeout=120).text
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    it.save_bytes("sitemap_magazalar.xml", xml.encode(), source_url=SM, method="http_get", rows=len(urls))
    out = it.dir / "stores.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["url"] for l in out.read_text().split("\n") if l.strip()}
    it.log(f"{len(urls)} mağaza, {len(done)} tamam")
    n = 0
    for u in urls:
        if u in done:
            continue
        try:
            r = it.get(u, timeout=90)
        except Exception as e:  # noqa: BLE001
            it.log(f"hata {u}: {e}")
            continue
        rec = {"url": u, "status": r.status_code, "at": now_iso()}
        if r.status_code == 200:
            try:
                rec["store"] = parse_store(r.text)
            except Exception as e:  # noqa: BLE001
                rec["parse_error"] = repr(e)[:200]
        with out.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
        if n % 500 == 0:
            it.log(f"{n} mağaza işlendi")
        if r.status_code in (403, 429):
            it.log(f"engel/kota yanıtı {r.status_code} — durduruldu")
            break
    it._record(out, source_url=SM, method="http_get_nuxt_state", rows=len(done) + n,
               note="UI_STORE_INFO; kişisel e-posta alınmadı; TCKN maskeli; restricted: kep, sellerAddress, sellerTradeName(şahıs)")


if __name__ == "__main__":
    main()
