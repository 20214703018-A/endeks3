"""KYK (GSB) ve özel öğrenci yurtları → warehouse/product/yurtlar.sqlite

Kaynaklar (resmî, ücretsiz, anahtarsız):
  1. KYK yurtları:   https://kygm.gsb.gov.tr/Ajax/gettesis.aspx?il=<il>          → ad, tip (kız/erkek), adres
     KYK kapasite:   kygm.gsb.gov.tr "seçime çıkılacak işletme yerlerinin listesi.pdf" → yurt adı + toplam kapasite
                     (ihale listesi; tüm yurtları kapsamaz → kapasitesi bilinmeyen yurt NULL kalır, uydurulmaz)
  2. Özel yurtlar:   https://ozelbarinmahizmetleri.gsb.gov.tr/ajax/getozeltesis.aspx?il=<il> → ad, tip, KAPASİTE, adres
  3. Koordinat:      adresten mahalle/köy + ilçe + il → Nominatim (≤1 istek/sn), idari ad doğrulamalı, "yaklaşık" etiketli

Doluluk (içerideki öğrenci sayısı) hiçbir kaynakta yurt bazında yayımlanmaz; yalnız kapasite verilir.
Telefon saklanmaz (kullanıcı kararı: ad, konum, sayısal bilgi yeter).

Tablo: yurt(id PK, kaynak 'kyk'|'ozel', il, ilce, ad, ad_norm, tip, kapasite, kapasite_kaynagi, adres, lat, lon,
            koordinat_kaynagi, guncellenme)
Kullanım:
  python3.13 collector/yurt_toplayici.py --kyk --ozel [--il yalova] ; --koordinat [--limit N]
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402
from collector.meb_okul_toplayici import tr_title  # noqa: E402

OUT_DB = os.path.join(REPO, "warehouse", "product", "yurtlar.sqlite")
UA = "Mozilla/5.0 (GEOPROP yurt toplayici; iletisim: heytteknoloji@gmail.com)"
KYK_URL = "https://kygm.gsb.gov.tr/Ajax/gettesis.aspx?il={il}"
OZEL_URL = "https://ozelbarinmahizmetleri.gsb.gov.tr/ajax/getozeltesis.aspx?il={il}"
KYK_KAPASITE_PDF = "https://kygm.gsb.gov.tr/Public/Edit/images/KYK/se%C3%A7ime%20%C3%A7%C4%B1k%C4%B1lacak%20i%C5%9Fletme%20yerlerinin%20listesi.pdf"

ILLER = ["Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Amasya", "Ankara", "Antalya", "Artvin", "Aydın", "Balıkesir", "Bilecik", "Bingöl",
         "Bitlis", "Bolu", "Burdur", "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır", "Edirne", "Elazığ", "Erzincan",
         "Erzurum", "Eskişehir", "Gaziantep", "Giresun", "Gümüşhane", "Hakkari", "Hatay", "Isparta", "Mersin", "İstanbul", "İzmir", "Kars",
         "Kastamonu", "Kayseri", "Kırklareli", "Kırşehir", "Kocaeli", "Konya", "Kütahya", "Malatya", "Manisa", "Kahramanmaraş", "Mardin",
         "Muğla", "Muş", "Nevşehir", "Niğde", "Ordu", "Rize", "Sakarya", "Samsun", "Siirt", "Sinop", "Sivas", "Tekirdağ", "Tokat", "Trabzon",
         "Tunceli", "Şanlıurfa", "Uşak", "Van", "Yozgat", "Zonguldak", "Aksaray", "Bayburt", "Karaman", "Kırıkkale", "Batman", "Şırnak",
         "Bartın", "Ardahan", "Iğdır", "Yalova", "Karabük", "Kilis", "Osmaniye", "Düzce"]
# GSB ajax il anahtarı: ascii küçük harf (JS 'cevir' ile aynı: ağrı→agri, afyonkarahisar…)
IL_KEY = {il: normalize_name(il).replace(" ", "") for il in ILLER}


def open_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    c = sqlite3.connect(OUT_DB, timeout=60)
    c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS yurt (id INTEGER PRIMARY KEY AUTOINCREMENT, kaynak TEXT, il TEXT, ilce TEXT, ad TEXT, ad_norm TEXT,
            tip TEXT, kapasite INTEGER, kapasite_kaynagi TEXT, adres TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT,
            UNIQUE (kaynak, il, ad_norm));
        CREATE INDEX IF NOT EXISTS idx_yurt_lat ON yurt (kaynak, lat, lon);
        CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT);
    """)
    return c


def _get(url: str, timeout=60) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}), timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _kartlar(html: str) -> list[dict]:
    """GSB liste HTML'i: 'AD | Tipi : | Kız | Kapasite : | 144 | Telefon : | … | Adres : | …' düz metne indirgenip bölünür."""
    body = html[html.find("</div>", html.find("aspNetHidden")):] if "aspNetHidden" in html else html
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", body)).replace("&nbsp;", " ")
    t = re.sub(r"(\| ?)+", "| ", t)
    parts = [p.strip() for p in t.split("|")]
    out, i = [], 0
    while i < len(parts):
        if parts[i] == "Tipi :" and i > 0:
            ad = parts[i - 1]; rec = {"ad": ad, "tip": parts[i + 1] if i + 1 < len(parts) else None}
            j = i + 2
            while j < len(parts) and parts[j] != "Tipi :":
                if parts[j] == "Kapasite :" and j + 1 < len(parts) and parts[j + 1].isdigit():
                    rec["kapasite"] = int(parts[j + 1])
                if parts[j] == "Adres :" and j + 1 < len(parts):
                    rec["adres"] = parts[j + 1]
                    break
                j += 1
            if ad and not re.fullmatch(r"[-\d ]*", ad):
                out.append(rec)
            i = j
        i += 1
    return out


def _ilce_from_adres(adres: str | None, il: str) -> str | None:
    """'… Merkez/YALOVA', 'Çınarcık / YALOVA', '(MERKEZ) … /MERKEZ/YALOVA' → ilçe."""
    if not adres:
        return None
    m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü\.]+)\s*/\s*" + re.escape(il.upper()[:4]) + r"[A-ZÇĞİÖŞÜa-zçğıöşü]*\s*$", adres.strip(), re.I)
    return tr_title(m.group(1)) if m else None


def kyk_kapasite_pdf() -> dict[tuple[str, str], int]:
    """(il, ad_norm) → toplam kapasite; pypdf ile ihale listesi."""
    from pypdf import PdfReader
    data = urllib.request.urlopen(urllib.request.Request(KYK_KAPASITE_PDF, headers={"User-Agent": UA}), timeout=90).read()
    out = {}
    rx = re.compile(r"^([A-ZÇĞİÖŞÜ]+) (\S+) (.+?) (MERKEZİ|ŞUBE|[A-ZÇĞİÖŞÜ]\s*BLOK\S*|BLOK\S*) (?:[A-ZÇĞİÖŞÜ\-]+ )+(\d+) (\d+)$")
    for page in PdfReader(io.BytesIO(data)).pages:
        for line in page.extract_text().splitlines():
            m = rx.match(line.strip())
            if m:
                il, ad, kap = m.group(1), m.group(3), int(m.group(5))
                key = (normalize_name(il), normalize_name(re.sub(r"\(.*?\)", "", ad)))
                out[key] = max(out.get(key, 0), kap)
    return out


def topla(c: sqlite3.Connection, kaynak: str, iller: list[str]) -> int:
    now = datetime.now(timezone.utc).isoformat(); n = 0
    kap = kyk_kapasite_pdf() if kaynak == "kyk" else {}
    if kaynak == "kyk":
        print(f"  KYK kapasite PDF: {len(kap)} yurt", flush=True)
    for il in iller:
        url = (KYK_URL if kaynak == "kyk" else OZEL_URL).format(il=IL_KEY[il])
        try:
            kartlar = _kartlar(_get(url))
        except Exception as exc:
            print(f"  {il}: HATA {type(exc).__name__}", flush=True); continue
        rows = []
        for k in kartlar:
            ad_norm = normalize_name(k["ad"])
            kapasite, kk = k.get("kapasite"), ("gsb özel barınma listesi" if k.get("kapasite") else None)
            if kaynak == "kyk":
                hit = kap.get((normalize_name(il), normalize_name(re.sub(r"\(.*?\)|MÜDÜRLÜĞÜ", "", k["ad"]))))
                kapasite, kk = (hit, "kygm işletme listesi (pdf)") if hit else (None, None)
            rows.append((kaynak, il, _ilce_from_adres(k.get("adres"), il), k["ad"], ad_norm, k.get("tip"), kapasite, kk, k.get("adres"), now))
        c.executemany("""INSERT INTO yurt (kaynak, il, ilce, ad, ad_norm, tip, kapasite, kapasite_kaynagi, adres, guncellenme)
                         VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kaynak, il, ad_norm) DO UPDATE SET tip=excluded.tip,
                         kapasite=COALESCE(excluded.kapasite, yurt.kapasite), kapasite_kaynagi=COALESCE(excluded.kapasite_kaynagi, yurt.kapasite_kaynagi),
                         adres=excluded.adres, ilce=COALESCE(excluded.ilce, yurt.ilce), guncellenme=excluded.guncellenme""", rows)
        c.commit(); n += len(rows)
        print(f"  {kaynak:4} {il:15} {len(rows):4}", flush=True); time.sleep(0.4)
    c.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?)", (f"yurt_{kaynak}", c.execute("SELECT COUNT(*) FROM yurt WHERE kaynak=?", (kaynak,)).fetchone()[0], now))
    c.commit(); return n


def koordinat(c: sqlite3.Connection, limit: int | None = None) -> int:
    from collector.geocode import adres_kodla
    rows = c.execute("SELECT id, il, ilce, ad, adres FROM yurt WHERE lat IS NULL AND (koordinat_kaynagi IS NULL OR koordinat_kaynagi='bulunamadı')").fetchall()
    if limit:
        rows = rows[:limit]
    print(f"koordinat: {len(rows):,} yurt (≈{len(rows) * 1.5 / 60:.0f} dk)", flush=True)
    now = datetime.now(timezone.utc).isoformat(); ok = 0
    for i, (yid, il, ilce, ad, adres) in enumerate(rows, 1):
        hit = adres_kodla(adres, il, ilce, ad)
        if hit:
            c.execute("UPDATE yurt SET lat=?, lon=?, koordinat_kaynagi=?, guncellenme=? WHERE id=?", (hit[0], hit[1], hit[2], now, yid)); ok += 1
        else:
            c.execute("UPDATE yurt SET koordinat_kaynagi=?, guncellenme=? WHERE id=?", ("bulunamadı", now, yid))
        if i % 25 == 0:
            c.commit(); print(f"  {i:,}/{len(rows):,}  bulundu {ok:,}", flush=True)
    c.commit(); print(f"koordinat tamamlanan: {ok:,}"); return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kyk", action="store_true"); ap.add_argument("--ozel", action="store_true"); ap.add_argument("--koordinat", action="store_true")
    ap.add_argument("--il", nargs="*"); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    iller = [i for i in ILLER if not a.il or IL_KEY[i] in {normalize_name(x).replace(" ", "") for x in a.il}]
    c = open_db()
    if a.kyk: print("KYK yurtları…"); topla(c, "kyk", iller)
    if a.ozel: print("Özel yurtlar…"); topla(c, "ozel", iller)
    if a.koordinat: koordinat(c, a.limit)
    c.close(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
