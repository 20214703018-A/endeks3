"""E-ticaret satıcıları — Pazarama pazaryeri mağaza sayfaları (satıcı şirket bilgileri, il/ilçe).

Kaynak: https://www.pazarama.com/sitemaps/magazalar/sitemap_0.xml (robots.txt'de ilan edilmiş sitemap; ~20 bin mağaza)
Her mağaza sayfasının Nuxt durumundaki UI_STORE_INFO bloğu: satıcı kimliği, mağaza adı, ticari unvan, adres,
il, ilçe, KEP adresi, vergi no, MERSİS no, satıcı puanı, VIP, etiketler. Minified değişken referansları
(__NUXT__ fonksiyon argümanları) çözülerek gerçek değerlere dönüştürülür.
Kişisel veri en aza indirilir: kişisel e-posta alınmaz; TC kimlik no (11 hane) ham yazılmaz; VKN (10 hane) ham saklanır; unvan/adres/KEP → STANDARD §3.3 'restricted';
şahıs olabilecek satıcılar sahis_olabilir_sezgisel ile bayraklanır (ürün katmanına girmez).
Engel/kota (403/429) gelince durmaz: bekler ve aynı adresi yeniden dener. Kapalı mağazalar (301→ana sayfa) kayda geçer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso, free_bytes  # noqa: E402

COMPANY = (r"(\ba\.?\s?s\.?\b|anonim|sirket|limited|\bltd|\bsti\b|ortakligi|\bllc\b|\binc\b|holding|ticaret|sanayi|pazarlama|magaza|giyim|"
           r"medya|bank|hizmet|teknoloji|yazilim|market|turizm|insaat|danismanlik|elektronik|mobilya|otomotiv|gida|tekstil|"
           r"\.com|\.net|\.tr|group|grup|store|shop|butik|spor|savunma)")
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


def nuxt_state(html):
    """window.__NUXT__ fonksiyonunu çöz: (env, gövde). Bulunamazsa (None, None)."""
    m = re.search(r"window\.__NUXT__=\(function\(([^)]*)\)\{(.*)\}\((.*)\)\);?</script>", html, re.S)
    if not m:
        return None, None
    params = m.group(1).split(",")
    args = split_args(m.group(3))
    return {p: (args[i] if i < len(args) else None) for i, p in enumerate(params)}, m.group(2)


def tok_val(tok, env):
    if tok is None:
        return None
    tok = tok.strip()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", tok) and tok in env:
        tok = env[tok]
    return js_val(tok) if isinstance(tok, str) else tok


def block(body, name):
    i = body.find(name + ":")
    if i < 0:
        return None
    j = body.find(",UI_", i + len(name))
    return body[i + len(name) + 1: j if j > 0 else None]


def top_objects(arr):
    """'[{...},{...}]' dizgisinden üst düzey nesne metinlerini ayır (dizeler korunur)."""
    out, depth, q, start, i = [], 0, None, None, 0
    while i < len(arr):
        ch = arr[i]
        if q:
            if ch == "\\":
                i += 1
            elif ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch in "{[":
            if ch == "{" and depth == 1:
                start = i
            depth += 1
        elif ch in "}]":
            depth -= 1
            if ch == "}" and depth == 1 and start is not None:
                out.append(arr[start:i + 1])
                start = None
        i += 1
    return out


def flat_keys(obj):
    """Nesnenin yalnız ilk düzey alanlarını bırak (iç içe {…}/[…] atılır)."""
    out, depth, q, i = [], 0, None, 0
    while i < len(obj):
        ch = obj[i]
        if q:
            if depth == 1:
                out.append(ch)
            if ch == "\\":
                if depth == 1 and i + 1 < len(obj):
                    out.append(obj[i + 1])
                i += 1
            elif ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            if depth == 1:
                out.append(ch)
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif depth == 1:
            out.append(ch)
        i += 1
    return "".join(out)


PRODUCT_FIELDS = ["productId", "title", "brand", "price", "salePrice", "salePriceNumber", "productCode", "stockCount",
                  "productRating", "productCommentCount", "isLowestPrice", "minPriceLast30DaysText", "cargoDetail",
                  "seoUrl", "sellerId", "itemGroupCode", "isVariantable"]
STR = r"(\"(?:[^\"\\]|\\.)*\"|[^,]+)"


def parse_katalog(html):
    """Mağaza sayfasındaki ürün listesi (ilk sayfa), toplam ürün sayısı, kategori ve marka listesi."""
    env, body = nuxt_state(html)
    if body is None:
        return None
    out = {}
    pr = block(body, "UI_PRODUCT_PAGE_RESPONSE")
    if pr:
        for k, name in (("urun_toplam", "totalCount"), ("sayfa_boyutu", "pageSize"), ("sayfa_toplam", "totalPages")):
            v = re.search(name + r":([^,}]+)", pr)
            out[k] = tok_val(v.group(1), env) if v else None
    pl = block(body, "UI_PRODUCT_LIST")
    urunler = []
    for o in top_objects(pl or ""):
        flat = flat_keys(o)
        rec = {}
        for f in PRODUCT_FIELDS:
            v = re.search(r"(?:^\{|,)" + f + ":" + STR, flat)
            if v:
                rec[f] = tok_val(v.group(1), env)
        c = re.search(r"categoryInfo:\{id:" + STR + r",name:" + STR, o)
        if c:
            rec["kategori_id"], rec["kategori_ad"] = tok_val(c.group(1), env), tok_val(c.group(2), env)
        d = re.search(r"predictDiscountPrice:\{currency:[^,]+,value:([\d.]+)", o)
        if d:
            rec["sepet_fiyati"] = float(d.group(1))
        im = re.search(r"imageList:\[(\"(?:[^\"\\]|\\.)*\")", o)
        if im:
            rec["ilk_gorsel"] = tok_val(im.group(1), env)
        urunler.append(rec)
    out["urunler"] = urunler
    fl = block(body, "UI_PRODUCT_FILTER_LIST") or ""
    gruplar = {}
    parts = re.split(r"parentName:", fl)
    for g in parts[1:]:
        ad = re.match(STR, g)
        gad = tok_val(ad.group(1), env) if ad else None
        vals = [tok_val(x, env) for x in re.findall(r"\{value:(\"(?:[^\"\\]|\\.)*\"|[A-Za-z_$][\w$]*),valueDetail", g)]
        if gad:
            gruplar.setdefault(str(gad), []).extend(v for v in vals if v is not None)
    out["filtre_gruplari"] = {k: len(v) for k, v in gruplar.items()}
    out["kategoriler"] = gruplar.get("Kategori", [])
    out["markalar"] = gruplar.get("Marka", [])
    return out


def parse_store(html):
    env, body = nuxt_state(html)
    if body is None:
        return None
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
    if tn.isdigit() and len(tn) == 11:  # TCKN ham yazılmaz (ulusal kimlik no; sayfa şu an yayımlamıyor, yayımlarsa da saklanmaz)
        rec.pop("taxNumber", None)
        rec["taxNumber_type"] = "TCKN_ATILDI"
    elif tn.isdigit() and len(tn) == 10:
        rec["taxNumber_type"] = "VKN"  # vergi kimlik no (tüzel/işletme) ham saklanır
    else:
        rec.pop("taxNumber", None)  # sayfada '-' yer tutucusu: site vergi no yayımlamıyor
    ad = rec.get("sellerTradeName") or rec.get("sellerName") or ""
    norm = re.sub(r"[^a-z0-9. ]", " ", str(ad).translate(str.maketrans("İıŞşĞğÜüÖöÇç", "iissgguuoocc")).lower())
    rec["sahis_olabilir_sezgisel"] = bool(ad) and not re.search(COMPANY, norm) and 2 <= len(norm.split()) <= 4
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", help="i/N: sitemap'in her N'inci adresi, i'den başlayarak (GitHub Actions makine payı)")
    ap.add_argument("--rps", type=float, default=1.2, help="istekler arası asgari saniye")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-minutes", type=float, default=0, help="süre dolunca düzgünce çık (0 = sınırsız)")
    ap.add_argument("--stop-after-blocks", type=int, default=0, help="ardışık bu kadar 403/429'da DUR (0 = durmadan bekle)")
    ap.add_argument("--no-katalog", action="store_true", help="ürün/filtre verisini çıkarma (eski davranış)")
    a = ap.parse_args()
    t0 = time.time()
    it = Intake("eticaret_pazarama_satici", rate=a.rps)
    xml = it.get(SM, timeout=120).text
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    si, sn = (1, 1)
    if a.shard:
        si, sn = (int(x) for x in a.shard.split("/"))
        urls = urls[si - 1::sn]
    if si == 1:
        it.save_bytes("sitemap_magazalar.xml", xml.encode(), source_url=SM, method="http_get", rows=len(urls))
    out = it.dir / (f"stores_shard_{si:02d}.jsonl" if a.shard else "stores.jsonl")
    done = set()
    if out.exists():
        done = {json.loads(l)["url"] for l in out.read_text().split("\n") if l.strip()}
    lim = a.limit or int(os.environ.get("LIMIT", 0) or 0)
    if lim:
        urls = urls[:lim]
    it.log(f"{len(urls)} mağaza (pay {si}/{sn}), {len(done)} tamam")
    n = 0
    cool = 0
    blocks = 0
    for u in urls:
        if u in done:
            continue
        if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
            it.log(f"süre doldu ({a.max_minutes} dk) — {n} mağaza işlendi, çıkılıyor (yeniden başlatınca devam eder)")
            break
        while True:  # engel/kota: aynı kimlikle bekle, aynı adresi yeniden dene (kimlik değiştirme yok)
            if free_bytes() < 1.5 * 1024 ** 3:
                it.log("disk boş alanı eşiğin altında — 10 dk bekleniyor")
                time.sleep(600)
                continue
            try:
                r = it.get(u, timeout=90, allow_redirects=False)
            except Exception as e:  # noqa: BLE001
                cool = min(cool + 1, 6)
                it.log(f"hata {u}: {e} — {60 * 2 ** cool // 2} sn bekleniyor")
                time.sleep(60 * 2 ** cool // 2)
                continue
            if r.status_code in (403, 429):
                blocks += 1
                cool = min(cool + 1, 6)
                wait = 60 * 2 ** cool // 2
                it.log(f"engel/kota {r.status_code} ({blocks}. ardışık) — {wait} sn bekleniyor")
                if a.stop_after_blocks and blocks >= a.stop_after_blocks:
                    it.log("ardışık engel eşiği aşıldı — durduruldu (kimlik değiştirilmez)")
                    raise SystemExit(3)
                time.sleep(wait)
                continue
            cool = 0
            blocks = 0
            break
        rec = {"url": u, "status": r.status_code, "at": now_iso(), "guncellenme_tarihi": now_iso()}
        if r.status_code in (301, 302, 307, 308):
            rec["yonlendirme"] = r.headers.get("location")  # kapanmış/pasif mağaza: sitemap'te var, sayfa ana sayfaya yönleniyor
        elif r.status_code == 200:
            try:
                rec["store"] = parse_store(r.text)
                if not a.no_katalog:
                    rec["katalog"] = parse_katalog(r.text)
            except Exception as e:  # noqa: BLE001
                rec["parse_error"] = repr(e)[:200]
        with out.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
        if n % 50 == 0:
            it.log(f"{n} mağaza işlendi")
    it._record(out, source_url=SM, method="http_get_nuxt_state", rows=len(done) + n,
               note="UI_STORE_INFO + ürün listesi/filtreler; kişisel e-posta alınmadı; TCKN ham yazılmaz; restricted: kep, sellerAddress, sellerTradeName(şahıs)")


if __name__ == "__main__":
    main()
