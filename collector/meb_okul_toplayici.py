"""MEB resmî okul listesi + koordinat + LGS taban puanı → warehouse/product/resmi_egitim.sqlite

Kaynaklar (resmî, ücretsiz, anahtarsız):
  1. meb.gov.tr/baglantilar/okullar/okullar_ajax.php  → il başına tüm kurumlar (ad, host, YOL=il/ilçe/kurum kodu)
  2. https://<host>.meb.k12.tr/tema/harita.php           → her okulun koordinatı (≈700 B; nazik hız: eşzamanlı 6)
  3. e-okul SNV08008.ASPX (LGS tercih listesi)           → sınavla öğrenci alan liselerin taban puanı (ilk yerleştirme + 2. nakil)
  4. https://<host>.meb.k12.tr/ (ana sayfa, ~22 KB)      → derslik / öğretmen / öğrenci sayısı (telefon/adres toplanmaz — karar 2026-09-13)

Tablolar:
  okul(kurum_kodu PK, il_kodu, ilce_kodu, il, ilce, ad, ad_norm, tur, host, lat, lon, koordinat_kaynagi,
       derslik, ogretmen, ogrenci, istatistik_guncellenme, guncellenme)
  lgs_taban(tercih_kodu PK, il_kodu, ilce, okul_adi, okul_adi_norm, okul_turu, alan, ogretim_sekli, pansiyon, dil,
            kontenjan, taban_ilk, taban_nakil, yil, kurum_kodu (eşleşme), ulusal_sira, ulusal_yuzdelik, il_sira, tur_sira)
  kapsama(tablo, satir, guncellenme)

Puanlama (yalnız veri olan okullara; uydurma yok): sınavla alan liselerde okul başına EN YÜKSEK taban puanı
(ilk yerleştirme; yoksa 2. nakil) → ulusal sıra/yüzdelik, il içi sıra, tür içi sıra. Adrese dayalı okulların
(ilkokul/ortaokul, sınavsız liseler) resmî puanı YOKTUR; bunlar "puan verisi yok" olarak kalır.

40-makine kalıbı (GitHub Actions): `--koordinat --shard i/N --csv out.csv` → shard yalnız kendi payındaki
(kurum_kodu % N == i-1) okulların koordinatını CSV'ye yazar; `--csv-yukle a.csv b.csv …` birleştirir.

Çalıştırma (yeniden çalıştırılabilir; koordinatı alınmış okullar atlanır):
  python3.13 collector/meb_okul_toplayici.py --liste [--il 77]
  python3.13 collector/meb_okul_toplayici.py --koordinat [--il 77] [--workers 6]
  python3.13 collector/meb_okul_toplayici.py --lgs [--il 77]
  python3.13 collector/meb_okul_toplayici.py --puanla
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name  # noqa: E402

OUT_DB = os.path.join(REPO, "warehouse", "product", "resmi_egitim.sqlite")
MEB_AJAX = "https://www.meb.gov.tr/baglantilar/okullar/okullar_ajax.php"
EOKUL_URL = "https://e-okul.meb.gov.tr/SinavIslemleri/BasvuruIslemleri/OKSTERCIH/SNV08008.ASPX"
UA = "Mozilla/5.0 (GEOPROP resmi okul toplayici; iletisim: heytteknoloji@gmail.com)"
IL_KODLARI = list(range(1, 82))

# Ad → tür (MEB adlandırması). Sıra önemli: özel türler önce.
TUR_KURALLARI = (
    ("bilsem", ("bilim ve sanat merkezi",)),
    ("ozel_egitim", ("ozel egitim",)),
    ("halk_egitim", ("halk egitim", "mesleki egitim merkezi", "olgunlasma", "ogretmenevi", "rehberlik", "akşam sanat", "aksam sanat")),
    ("fen_lisesi", ("fen lisesi",)),
    ("sosyal_bilimler_lisesi", ("sosyal bilimler lisesi",)),
    ("imam_hatip_lisesi", ("imam hatip lisesi",)),
    ("meslek_lisesi", ("mesleki ve teknik", "meslek lisesi", "cok programli", "guzel sanatlar lisesi", "spor lisesi", "teknik lise")),
    ("anadolu_lisesi", ("anadolu lisesi",)),
    ("lise", ("lisesi", " lise")),
    ("imam_hatip_ortaokulu", ("imam hatip ortaokulu",)),
    ("ortaokul", ("ortaokulu", "ortaokul")),
    ("ilkokul", ("ilkokulu", "ilkokul")),
    ("anaokulu", ("anaokulu", "anasinifi", "kres")),
)
LISE_TURLERI = {"fen_lisesi", "sosyal_bilimler_lisesi", "imam_hatip_lisesi", "meslek_lisesi", "anadolu_lisesi", "lise"}


def okul_turu(ad: str) -> str:
    n = normalize_name(ad)
    for tur, keys in TUR_KURALLARI:
        if any(k in n for k in keys):
            return tur
    return "diger"


def _num(v: str | None) -> float | None:
    v = (v or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def open_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    c = sqlite3.connect(OUT_DB, timeout=60)
    c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS okul (kurum_kodu INTEGER PRIMARY KEY, il_kodu INTEGER, ilce_kodu INTEGER, il TEXT, ilce TEXT,
            ad TEXT, ad_norm TEXT, tur TEXT, host TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT);
        CREATE INDEX IF NOT EXISTS idx_okul_il ON okul (il_kodu, ilce_kodu);
    """)
    for col, typ in (("derslik", "INTEGER"), ("ogretmen", "INTEGER"), ("ogrenci", "INTEGER"), ("istatistik_guncellenme", "TEXT")):
        if col not in {r[1] for r in c.execute("PRAGMA table_info(okul)")}:
            c.execute(f"ALTER TABLE okul ADD COLUMN {col} {typ}")
    c.executescript("""
        CREATE INDEX IF NOT EXISTS idx_okul_tur_lat ON okul (tur, lat, lon);
        CREATE INDEX IF NOT EXISTS idx_okul_adnorm ON okul (il_kodu, ad_norm);
        CREATE TABLE IF NOT EXISTS lgs_taban (tercih_kodu INTEGER PRIMARY KEY, il_kodu INTEGER, ilce TEXT, okul_adi TEXT, okul_adi_norm TEXT,
            okul_turu TEXT, alan TEXT, ogretim_sekli TEXT, pansiyon TEXT, dil TEXT, kontenjan INTEGER, taban_ilk REAL, taban_nakil REAL,
            yil INTEGER, kurum_kodu INTEGER, ulusal_sira INTEGER, ulusal_yuzdelik REAL, il_sira INTEGER, tur_sira INTEGER, guncellenme TEXT);
        CREATE INDEX IF NOT EXISTS idx_lgs_kurum ON lgs_taban (kurum_kodu);
        CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT);
    """)
    return c


def _post(url: str, data: dict, headers: dict | None = None, opener=None, timeout=60) -> str:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={"User-Agent": UA, **(headers or {})})
    op = opener or urllib.request.build_opener()
    with op.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ---------- 1) liste ----------
def liste(c: sqlite3.Connection, iller: list[int]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for il in iller:
        j = json.loads(_post(MEB_AJAX, {"draw": 1, "start": 0, "length": 10000, "il": il, "ilce": 0, "search[value]": ""},
                             headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.meb.gov.tr/baglantilar/okullar/index.php"}))
        rows = []
        for r in j.get("data", []):
            parts = (r.get("YOL") or "").split("/")
            if len(parts) != 3:
                continue
            il_kodu, ilce_kodu, kurum = int(parts[0]), int(parts[1]), int(parts[2])
            ad_full = r.get("OKUL_ADI") or ""
            seg = [s.strip() for s in ad_full.split(" - ")]
            il_ad, ilce_ad, ad = (seg[0], seg[1], " - ".join(seg[2:])) if len(seg) >= 3 else (None, None, ad_full)
            rows.append((kurum, il_kodu, ilce_kodu, il_ad, ilce_ad, ad, normalize_name(ad), okul_turu(ad), r.get("HOST"), now))
        c.executemany("""INSERT INTO okul (kurum_kodu, il_kodu, ilce_kodu, il, ilce, ad, ad_norm, tur, host, guncellenme)
                         VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kurum_kodu) DO UPDATE SET il=excluded.il, ilce=excluded.ilce,
                         ad=excluded.ad, ad_norm=excluded.ad_norm, tur=excluded.tur, host=excluded.host, guncellenme=excluded.guncellenme""", rows)
        c.commit()
        print(f"  il {il:2}: {len(rows):5,} kurum (toplam {j.get('recordsTotal')})", flush=True)
        time.sleep(0.3)


# ---------- 2) koordinat ----------
_COORD_RX = re.compile(r"q=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)")


YOK = ("yok",)   # sayfa geldi ama koordinat girilmemiş (q=0,0) — tekrar denenmez


def _harita(host: str):
    """(lat, lon) | YOK (sayfa var, koordinat yok) | None (ağ/HTTP hatası)."""
    url = f"https://{host}.meb.k12.tr/tema/harita.php"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15) as r:
                body = r.read().decode("utf-8", "ignore")
            m = _COORD_RX.search(body)
            if not m:
                return YOK if "maps" in body else None
            lat, lon = float(m.group(1)), float(m.group(2))
            return (lat, lon) if 35.0 <= lat <= 43.5 and 25.0 <= lon <= 45.5 else YOK
        except Exception:
            time.sleep(1.0 + attempt)
    return None


def koordinat(c: sqlite3.Connection, iller: list[int], workers: int = 6, shard: tuple[int, int] | None = None,
              csv_out: str | None = None) -> None:
    ph = ",".join("?" for _ in iller)
    todo = c.execute(f"SELECT kurum_kodu, host FROM okul WHERE lat IS NULL AND koordinat_kaynagi IS NULL AND host IS NOT NULL AND host<>'' AND il_kodu IN ({ph})", iller).fetchall()
    if shard:
        i, n = shard
        todo = [t for t in todo if t[0] % n == (i - 1)]
    print(f"koordinat alınacak: {len(todo):,} okul, {workers} eşzamanlı{f', shard {shard[0]}/{shard[1]}' if shard else ''}", flush=True)
    now = datetime.now(timezone.utc).isoformat()
    done = bos = 0; t0 = time.time(); batch = []
    csv_f = open(csv_out, "a", encoding="utf-8") if csv_out else None

    def yaz(batch):
        if csv_f:
            for lat, lon, src, ts, kurum in batch:
                csv_f.write(f"{kurum},{'' if lat is None else lat},{'' if lon is None else lon},{src},{ts}\n")
            csv_f.flush()
        else:
            c.executemany("UPDATE okul SET lat=?, lon=?, koordinat_kaynagi=?, guncellenme=? WHERE kurum_kodu=?", batch); c.commit()

    # Blok tespiti: ilk 20 okulun hiçbirinden koordinat gelmezse (403/timeout) açık hata ver.
    if todo:
        import random
        ornek = random.sample(todo, min(20, len(todo)))   # rastgele: koordinatsız okullar listenin başında kümelenir
        if all(_harita(host) is None for _, host in ornek):
            print("  20 rastgele istek yanıtsız; 30 s bekleyip yeniden deneniyor…", flush=True); time.sleep(30)
            if all(_harita(host) is None for _, host in random.sample(todo, min(20, len(todo)))):
                raise SystemExit("harita.php 40 istekte yanıt vermedi — IP engeli olabilir; Türkiye IP'sinden çalıştırın")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_harita, host): kurum for kurum, host in todo}
        for f in as_completed(futs):
            kurum = futs[f]; res = f.result()
            if res is YOK:
                batch.append((None, None, "harita.php: koordinat girilmemiş", now, kurum)); bos += 1
            elif res:
                batch.append((res[0], res[1], "meb.k12.tr/tema/harita.php", now, kurum)); done += 1
            else:
                bos += 1
            if len(batch) >= 200:
                yaz(batch); batch = []
                print(f"  {done + bos:6,}/{len(todo):,}  koordinatlı {done:,}  boş {bos:,}  {time.time() - t0:5.0f}s", flush=True)
    if batch:
        yaz(batch)
    if csv_f:
        csv_f.close()
    print(f"bitti: koordinatlı {done:,}, boş {bos:,}")


# ---------- 2b) istatistik (ana sayfa) ----------
# Tema başına markup değişir (tablo / <p class=okulumuz-sayi> / "Derslik:<strong>27</strong>");
# etiketler atılıp düz metinde "Etiket [Sayısı] : 27" arandığında hepsi yakalanır.
_STAT_RX = {
    "derslik": re.compile(r"Derslik(?:\s*Say[ıi]s[ıi])?\s*:?\s*(\d{1,4})\b", re.I),
    "ogretmen": re.compile(r"Ö[ğg]retmen(?:\s*Say[ıi]s[ıi])?\s*:?\s*(\d{1,4})\b", re.I),
    "ogrenci": re.compile(r"Ö[ğg]renci(?:\s*Say[ıi]s[ıi])?\s*:?\s*(\d{1,5})\b", re.I),
}


def _anasayfa(host: str) -> dict | None:
    url = f"https://{host}.meb.k12.tr/"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
                h = r.read(300_000).decode("utf-8", "ignore")
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
            out = {k: int(m.group(1)) for k, rx in _STAT_RX.items() if (m := rx.search(t))}
            return out or None
        except Exception:
            time.sleep(1.0 + attempt)
    return None


def istatistik(c: sqlite3.Connection, iller: list[int], workers: int = 6, shard: tuple[int, int] | None = None,
               csv_out: str | None = None) -> None:
    ph = ",".join("?" for _ in iller)
    todo = c.execute(f"SELECT kurum_kodu, host FROM okul WHERE istatistik_guncellenme IS NULL AND host IS NOT NULL AND host<>'' AND il_kodu IN ({ph})", iller).fetchall()
    if shard:
        i, n = shard; todo = [t for t in todo if t[0] % n == (i - 1)]
    print(f"istatistik alınacak: {len(todo):,} okul, {workers} eşzamanlı{f', shard {shard[0]}/{shard[1]}' if shard else ''}", flush=True)
    now = datetime.now(timezone.utc).isoformat()
    done = bos = 0; t0 = time.time(); batch = []
    csv_f = open(csv_out, "a", encoding="utf-8") if csv_out else None

    def yaz(batch):
        if csv_f:
            for row in batch:
                csv_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            csv_f.flush()
        else:
            c.executemany("UPDATE okul SET derslik=?, ogretmen=?, ogrenci=?, istatistik_guncellenme=? WHERE kurum_kodu=?",
                          [(r["derslik"], r["ogretmen"], r["ogrenci"], r["ts"], r["kurum"]) for r in batch]); c.commit()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_anasayfa, host): kurum for kurum, host in todo}
        for f in as_completed(futs):
            kurum = futs[f]; res = f.result()
            if res:
                batch.append({"kurum": kurum, "derslik": res.get("derslik"), "ogretmen": res.get("ogretmen"), "ogrenci": res.get("ogrenci"), "ts": now}); done += 1
            else:
                bos += 1
            if len(batch) >= 100:
                yaz(batch); batch = []
                print(f"  {done + bos:6,}/{len(todo):,}  istatistikli {done:,}  boş {bos:,}  {time.time() - t0:5.0f}s", flush=True)
    if batch:
        yaz(batch)
    if csv_f:
        csv_f.close()
    print(f"bitti: istatistikli {done:,}, boş {bos:,}")


def istatistik_yukle(c: sqlite3.Connection, paths: list[str]) -> None:
    n = 0
    for pth in paths:
        rows = []
        with open(pth, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line); rows.append((r["derslik"], r["ogretmen"], r["ogrenci"], r["ts"], r["kurum"]))
        c.executemany("UPDATE okul SET derslik=?, ogretmen=?, ogrenci=?, istatistik_guncellenme=? WHERE kurum_kodu=?", rows); n += len(rows)
    c.commit(); print(f"JSONL'den {n:,} istatistik yüklendi")


def csv_yukle(c: sqlite3.Connection, paths: list[str]) -> None:
    n = 0
    for pth in paths:
        rows = []
        with open(pth, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) == 5:
                    rows.append((float(parts[1]) if parts[1] else None, float(parts[2]) if parts[2] else None, parts[3], parts[4], int(parts[0])))
        c.executemany("UPDATE okul SET lat=?, lon=?, koordinat_kaynagi=?, guncellenme=? WHERE kurum_kodu=?", rows); n += len(rows)
    c.commit(); print(f"CSV'den {n:,} koordinat yüklendi")


# ---------- 3) LGS ----------
def lgs(c: sqlite3.Connection, iller: list[int]) -> None:
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    hdr = {"Referer": EOKUL_URL}
    now = datetime.now(timezone.utc).isoformat()
    for il in iller:
        try:
            h = op.open(urllib.request.Request(EOKUL_URL, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8", "ignore")
            form = {n: v for n, v in re.findall(r'<input type="hidden" name="([^"]+)"[^>]*value="([^"]*)"', h)}
            form.update({"ddlIl": str(il), "ddlIlce": "-1", "chkFenSosyal": "on", "chkAnadolu": "on", "chkMesleki": "on", "chkAIHL": "on", "btnGiris": "Listele"})
            r = _post(EOKUL_URL, form, headers=hdr, opener=op, timeout=120)
        except urllib.error.HTTPError as exc:
            # e-okul Türkiye dışı IP'leri 403 ile reddeder (GitHub Actions). Bu adım yerelden çalıştırılır.
            print(f"  il {il:2}: LGS alınamadı (HTTP {exc.code}) — e-okul yurt dışı IP'ye kapalı olabilir; yerelden çalıştırın", flush=True)
            if exc.code == 403 and il == iller[0]:
                print("  ilk ilde 403 → LGS adımı atlanıyor"); return
            continue
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", r, re.S)
        header, rows = None, []
        for tr in trs:
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", td)).replace("&nbsp;", "").strip() for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            if not cells:
                continue
            if cells[0] == "Tercih Kodu":
                header = cells; continue
            if header is None or len(cells) < 11 or not cells[0].isdigit():
                continue
            yil_m = re.search(r"(20\d\d)", " ".join(header)); yil = int(yil_m.group(1)) if yil_m else None
            ilce, okul_adi = (cells[1].split(" / ", 1) + [""])[:2] if " / " in cells[1] else ("", cells[1])
            # başlık sırası: kod, ad, tür, alan, süre, şekil, pansiyon, dil, kontenjan, taban(nakil), taban(ilk)
            taban_nakil, taban_ilk = _num(cells[9]), _num(cells[10])
            rows.append((int(cells[0]), il, ilce.strip(), okul_adi.strip(), normalize_name(okul_adi), cells[2], cells[3] or None, cells[5], cells[6], cells[7],
                         int(cells[8]) if cells[8].isdigit() else None, taban_ilk, taban_nakil, yil, now))
        c.executemany("""INSERT OR REPLACE INTO lgs_taban (tercih_kodu, il_kodu, ilce, okul_adi, okul_adi_norm, okul_turu, alan, ogretim_sekli,
                         pansiyon, dil, kontenjan, taban_ilk, taban_nakil, yil, guncellenme) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        c.commit()
        print(f"  il {il:2}: {len(rows):4} LGS satırı", flush=True)
        time.sleep(0.5)


# ---------- 4) eşleştirme + puanlama ----------
def puanla(c: sqlite3.Connection) -> None:
    # a) lgs_taban → okul eşleşmesi: aynı il, ad_norm eşit; yoksa ad_norm içerme (tek aday)
    okullar = {}
    for kurum, il, ad_norm, ilce in c.execute("SELECT kurum_kodu, il_kodu, ad_norm, ilce FROM okul WHERE tur IN ('fen_lisesi','sosyal_bilimler_lisesi','imam_hatip_lisesi','meslek_lisesi','anadolu_lisesi','lise')"):
        okullar.setdefault(il, []).append((kurum, ad_norm, normalize_name(ilce or "")))
    eslesen = 0; upd = []
    for tk, il, ilce, ad_norm in c.execute("SELECT tercih_kodu, il_kodu, ilce, okul_adi_norm FROM lgs_taban"):
        adaylar = okullar.get(il, [])
        ilce_n = normalize_name(ilce or "")
        tam = [k for k, a, i in adaylar if a == ad_norm and (not ilce_n or i == ilce_n)] or [k for k, a, i in adaylar if a == ad_norm]
        if not tam:
            tam = [k for k, a, i in adaylar if (ad_norm in a or a in ad_norm) and i == ilce_n]
        if len(tam) == 1:
            upd.append((tam[0], tk)); eslesen += 1
    c.executemany("UPDATE lgs_taban SET kurum_kodu=? WHERE tercih_kodu=?", upd)
    # b) okul başına en yüksek taban (ilk yerleştirme; yoksa nakil) → sıralama
    rows = c.execute("""SELECT tercih_kodu, il_kodu, okul_turu, COALESCE(taban_ilk, taban_nakil) AS p FROM lgs_taban WHERE COALESCE(taban_ilk, taban_nakil) IS NOT NULL""").fetchall()
    rows.sort(key=lambda r: -r[3])
    n = len(rows)
    il_sayac: dict[int, int] = {}; tur_sayac: dict[str, int] = {}
    upd = []
    for i, (tk, il, tur, p) in enumerate(rows, 1):
        il_sayac[il] = il_sayac.get(il, 0) + 1; tur_sayac[tur] = tur_sayac.get(tur, 0) + 1
        upd.append((i, round(100.0 * i / n, 2), il_sayac[il], tur_sayac[tur], tk))
    c.executemany("UPDATE lgs_taban SET ulusal_sira=?, ulusal_yuzdelik=?, il_sira=?, tur_sira=? WHERE tercih_kodu=?", upd)
    now = datetime.now(timezone.utc).isoformat()
    for t in ("okul", "lgs_taban"):
        c.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?)", (t, c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], now))
    c.commit(); c.execute("ANALYZE"); c.commit()
    print(f"LGS satırı {n:,} sıralandı; okul eşleşmesi {eslesen:,}/{c.execute('SELECT COUNT(*) FROM lgs_taban').fetchone()[0]:,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--liste", action="store_true"); ap.add_argument("--koordinat", action="store_true")
    ap.add_argument("--lgs", action="store_true"); ap.add_argument("--puanla", action="store_true")
    ap.add_argument("--il", type=int, nargs="*"); ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--shard", help="i/N: koordinat adımını N parçaya böl, i. parçayı işle (1 tabanlı)")
    ap.add_argument("--csv", help="koordinatları DB yerine bu CSV'ye yaz (shard çıktısı)")
    ap.add_argument("--csv-yukle", nargs="*", help="shard CSV'lerini DB'ye yükle")
    ap.add_argument("--istatistik", action="store_true", help="ana sayfadan derslik/öğretmen/öğrenci")
    ap.add_argument("--istatistik-yukle", nargs="*", help="shard JSONL'lerini DB'ye yükle")
    a = ap.parse_args()
    iller = a.il or IL_KODLARI
    shard = None
    if a.shard:
        i, n = a.shard.split("/"); shard = (int(i), int(n))
    c = open_db()
    if a.liste: print("MEB okul listesi…"); liste(c, iller)
    if a.koordinat: print("koordinatlar…"); koordinat(c, iller, a.workers, shard, a.csv)
    if a.csv_yukle: csv_yukle(c, a.csv_yukle)
    if a.istatistik: print("okul istatistikleri…"); istatistik(c, iller, a.workers, shard, a.csv)
    if a.istatistik_yukle: istatistik_yukle(c, a.istatistik_yukle)
    if a.lgs: print("LGS taban puanları…"); lgs(c, iller)
    if a.puanla: print("eşleştirme + puanlama…"); puanla(c)
    c.close(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
