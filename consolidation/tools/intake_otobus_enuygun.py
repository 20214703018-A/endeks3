"""Şehirlerarası otobüs rotaları/seferleri — enuygun.com rota sayfaları (schema.org yapılandırılmış veri).

Kaynak: https://www.enuygun.com/otobus-bileti/sitemap/sitemap.city-to-city.xml (35 bin+ rota; robots.txt izinli)
Her rota sayfasındaki JSON-LD 'BusTrip' kayıtları: kalkış/varış durağı, kalkış/varış saati, firma, fiyat (TRY)
+ SSS metni (mesafe km, süre, firma sayısı vb.). Yalnız yapılandırılmış bloklar saklanır (HTML değil).
Öncelik: 81 il merkezi arası rotalar (Batı büyükşehirleri önce), sonra kalan rotalar.
Kullanım: python3 intake_otobus_enuygun.py [--all]
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

SM = "https://www.enuygun.com/otobus-bileti/sitemap/sitemap.{}.xml"
ILLER = ["istanbul", "izmir", "bursa", "antalya", "kocaeli", "mugla", "tekirdag", "balikesir", "aydin", "ankara",
         "adana", "adiyaman", "afyonkarahisar", "agri", "aksaray", "amasya", "ardahan", "artvin", "bartin", "batman",
         "bayburt", "bilecik", "bingol", "bitlis", "bolu", "burdur", "canakkale", "cankiri", "corum", "denizli",
         "diyarbakir", "duzce", "edirne", "elazig", "erzincan", "erzurum", "eskisehir", "gaziantep", "giresun",
         "gumushane", "hakkari", "hatay", "igdir", "isparta", "kahramanmaras", "karabuk", "karaman", "kars",
         "kastamonu", "kayseri", "kirikkale", "kirklareli", "kirsehir", "kilis", "konya", "kutahya", "malatya",
         "manisa", "mardin", "mersin", "mus", "nevsehir", "nigde", "ordu", "osmaniye", "rize", "sakarya", "samsun",
         "siirt", "sinop", "sivas", "sanliurfa", "sirnak", "tokat", "trabzon", "tunceli", "usak", "van", "yalova",
         "yozgat", "zonguldak"]
PRI = {s: i for i, s in enumerate(ILLER)}


def main():
    all_routes = "--all" in sys.argv
    it = Intake("otobus_seferleri_enuygun", rate=1.5)
    locs = {}
    for kind in ("city-to-city", "terminal", "all-companies", "city-based-company", "city-to-terminal", "terminal-to-city"):
        x = it.get(SM.format(kind)).text
        locs[kind] = re.findall(r"<loc>([^<]+)</loc>", x)
        it.save_bytes(f"sitemap/{kind}.xml", x.encode(), source_url=SM.format(kind), method="http_get",
                      rows=len(locs[kind]))
    it.log({k: len(v) for k, v in locs.items()})
    routes = []
    for u in locs["city-to-city"]:
        slug = u.rstrip("/").rsplit("/", 1)[-1]
        a, _, b = slug.partition("-")
        # il-il çiftleri (tek kelimelik il adları; iki kelimeli olmayan iller için güvenilir)
        pa, pb = PRI.get(a), PRI.get(b)
        il_pair = pa is not None and pb is not None
        if il_pair or all_routes:
            routes.append((min(pa or 99, pb or 99) if il_pair else 999, u, il_pair))
    routes.sort()
    out = it.dir / "routes.jsonl"
    done = set()
    if out.exists():
        with out.open("rb") as fh:  # 390 MB+ dosya: belleğe tek parça okumadan satır satır
            done = {json.loads(l)["url"] for l in fh if l.strip()}
    it.log(f"{len(routes)} rota (il-il: {sum(1 for r in routes if r[2])}); {len(done)} tamam")
    n = 0
    for _, u, il_pair in routes:
        if u in done:
            continue
        try:
            r = it.get(u, timeout=60)
        except Exception as e:  # noqa: BLE001
            it.log(f"hata {u}: {e}")
            continue
        blocks = []
        for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
            try:
                blocks.append(json.loads(m.group(1)))
            except ValueError:
                pass
        trips = [t for b in blocks for t in (b if isinstance(b, list) else [b]) if isinstance(t, dict) and t.get("@type") == "BusTrip"]
        faq = [b for b in blocks if isinstance(b, dict) and b.get("@type") == "FAQPage"]
        rec = {"url": u, "status": r.status_code, "il_il": il_pair, "at": now_iso(), "trips": trips,
               "faq": faq[0].get("mainEntity") if faq else None}
        with out.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
        if n % 200 == 0:
            it.log(f"{n} rota işlendi (son: {u}, {len(trips)} sefer)")
    it._record(out, source_url=SM.format("city-to-city"), method="http_get_jsonld", rows=len(done) + n,
               note="her satır bir rota sayfasının JSON-LD BusTrip + SSS blokları")
    it.log("bitti")


if __name__ == "__main__":
    main()
