"""Resmî Gazete izleyici → warehouse/product/resmi_gazete.sqlite (karar, karar_yer)

Kaynak: resmigazete.gov.tr/fihrist?tarih=YYYY-MM-DD (günlük fihrist; curl — urllib bu makinede sertifika hatası veriyor).
Gayrimenkulü etkileyen karar başlıkları regex ile sınıflanır (KATEGORILER): acele kamulaştırma, kamulaştırma, kentsel dönüşüm /
riskli alan / rezerv alan, sit / korunan alan, turizm koruma-gelişim bölgesi, OSB / serbest bölge / teknoloji geliştirme bölgesi /
endüstri bölgesi, raylı sistem / metro / demiryolu, yol / otoyol, enerji iletim-doğal gaz hattı, üniversite, liman / havalimanı,
maden / jeotermal / petrol ruhsat sahası, baraj / sulama / içme suyu.
Karar PDF'leri gömülü fontla (metin çıkarılamıyor) → pymupdf 200 dpi + tesseract (tur) OCR; ilk 2 sayfa. Metinden
"X İli, Y İlçesi, Z Mahallesi/Köyü" kalıpları (birden çok) → karar_yer. Ek listelerdeki ada/parsel OCR'da güvenilir okunmuyor
→ SAKLANMAZ (uydurma yok); karar metni tam metin olarak saklanır, arayüz "mahalle/ilçe düzeyi" der.

  python3.13 collector/resmi_gazete_toplayici.py [--gun 365] [--reset]
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, tr_title  # noqa: E402

OUT = os.path.join(REPO, "warehouse", "product", "resmi_gazete.sqlite")
RAW = os.path.join(REPO, "warehouse", "raw", "resmi_gazete")
UA = "Mozilla/5.0 (GEOPROP veri toplayici)"
# konu (ilk eşleşen) + işlem (kamulaştırma türü) ayrı tutulur: "enerji hattı için acele kamulaştırma" → konu=enerji_hatti, islem=acele
ISLEMLER: tuple[tuple[str, str], ...] = (("acele_kamulastirma", r"acele kamulaştır"), ("kamulastirma", r"kamulaştır"))
KATEGORILER: tuple[tuple[str, str], ...] = (
    ("kentsel_donusum", r"kentsel dönüşüm|riskli alan|rezerv yapı|rezerv alan|yenileme alanı"),
    ("sit_koruma", r"sit alan|kesin korunacak|korunacak hassas|milli park|tabiat park|sulak alan"),
    ("turizm_bolgesi", r"turizm koruma ve gelişim|turizm merkezi|turizm bölgesi"),
    ("sanayi_bolgesi", r"organize sanayi|serbest bölge|teknoloji geliştirme bölgesi|endüstri bölgesi|sanayi sitesi"),
    ("rayli_sistem", r"raylı|metro|demiryolu|hızlı tren|iltisak hattı|tramvay"),
    ("yol", r"devlet yolu|otoyol|bağlantı yolu|çevre yolu|köprü|tünel"),
    ("enerji_hatti", r"enerji iletim|enerji nakil|trafo|transformatör|doğal gaz|doğalgaz|boru hattı|\b(?-i:RES|GES|HES|JES|TM)\b|santral|elektrik"),
    ("universite", r"üniversite"),
    ("liman_havalimani", r"liman|havalimanı|havaalanı|iskele"),
    ("maden_ruhsat", r"maden|jeotermal|petrol arama|ruhsat sahası"),
    ("su_yapisi", r"baraj|sulama|içme ?suyu|atıksu|gölet|taşkın"),
    ("kamulastirma_diger", r"kamulaştır|taşınmaz"),
)
_YER = re.compile(r"([A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+)?)\s+İli,?\s+(?:([A-ZÇĞİÖŞÜ][A-Za-zçğıöşüÇĞİÖŞÜ\.]+(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)?)(?:\s+ve\s+([A-ZÇĞİÖŞÜ][a-zçğıöşü]+))?\s+İlçe(?:si|leri),?\s*)?(?:([A-ZÇĞİÖŞÜ][A-Za-zçğıöşüÇĞİÖŞÜ\.\-]+(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)?)\s+(?:Mahallesi|Köyü|Beldesi))?")


def _curl(url, out=None):
    cmd = ["curl", "-s", "-m", "60", "-A", UA, url] + (["-o", out] if out else [])
    r = subprocess.run(cmd, capture_output=True, timeout=90)
    return r.stdout.decode("utf-8", "ignore") if not out else os.path.exists(out)


def fihrist(d: date):
    h = _curl(f"https://www.resmigazete.gov.tr/fihrist?tarih={d.isoformat()}")
    sayi = re.search(r"Sayı\s*:\s*(\d{5})", h)
    items = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', h, re.S):
        u, t = m.group(1), html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2)))).strip(" –-")
        if "eskiler/" in u and t and not t.startswith("PDF"):
            items.append((u, t))
    return (sayi.group(1) if sayi else None), items


def kategori(baslik: str):
    """→ (konu, islem) | None. Yalnız yönetmelik/eğitim başlıkları (üniversite yönetmeliği) elenir."""
    if re.search(r"yönetmeli|tebliğ|genelge|mahkeme|kurul üyeliğ|atama|görevlendir|sınav|eğitim-öğretim", baslik, re.I):
        return None
    islem = next((k for k, rx in ISLEMLER if re.search(rx, baslik, re.I)), None)
    konu = next((k for k, rx in KATEGORILER if re.search(rx, baslik, re.I)), None)
    if konu == "kamulastirma_diger" and not islem:
        return None
    return (konu, islem) if konu else None


def ocr(pdf_path: str, max_pages=2) -> str:
    import pymupdf
    parcalar = []
    doc = pymupdf.open(pdf_path)
    for i, p in enumerate(doc):
        if i >= max_pages:
            break
        png = os.path.join(tempfile.gettempdir(), "geoprop_rg_page.png")
        p.get_pixmap(dpi=200).save(png)
        r = subprocess.run(["tesseract", png, "-", "-l", "tur", "--psm", "6"], capture_output=True, text=True, timeout=120)
        parcalar.append(r.stdout)
    return "\n".join(parcalar)


_IDARI = None


def _idari():
    """{il_norm: (il_ad, {ilce_norm: ilce_ad})} — idari_sinirlar.sqlite'tan (harita etiketi eşleşmesi için)."""
    global _IDARI
    if _IDARI is None:
        _IDARI = {}
        p = os.path.join(REPO, "warehouse", "product", "idari_sinirlar.sqlite")
        if os.path.exists(p):
            cc = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            for ad, il_adi, seviye in cc.execute("SELECT ad, il_adi, seviye FROM sinir"):
                if seviye == "il":
                    _IDARI.setdefault(normalize_name(ad), (ad, {}))
            for ad, il_adi, seviye in cc.execute("SELECT ad, il_adi, seviye FROM sinir"):
                if seviye == "ilce" and il_adi and normalize_name(il_adi) in _IDARI:
                    _IDARI[normalize_name(il_adi)][1][normalize_name(ad)] = ad
            cc.close()
    return _IDARI


def harita_yerleri(metin: str, baslik: str):
    """Kalıp bulunamayan (güzergâh haritalı) kararlar: metindeki il adları + o ilin ilçe adları (büyük harf etiketler).
    Düşük güven: kaynak='harita'. Kısa/ortak ilçe adları (Merkez, Kale, Bor…) il olmadan alınmaz."""
    idari = _idari()
    if not idari:
        return []
    kelimeler = {normalize_name(w) for w in re.findall(r"[A-ZÇĞİÖŞÜa-zçğıöşü]{3,}", baslik + " " + metin)}
    out = []
    for il_n, (il_ad, ilceler) in idari.items():
        if il_n in kelimeler:
            hit = [ilce for ilce_n, ilce in ilceler.items() if ilce_n in kelimeler and len(ilce_n) >= 5 and ilce_n != "merkez"]
            out.append((il_ad, None, None))
            out += [(il_ad, ilce, None) for ilce in hit]
    return out


_YER2 = re.compile(r"([A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+)?)\s+İli(?:,|\s+(?:ve|ile))?\s+([^\.;]{0,160}?)\s+İlçe(?:si|leri|sinde|lerinde|sinin|lerinin)")
_MAH2 = re.compile(r"([A-ZÇĞİÖŞÜ][A-Za-zçğıöşüÇĞİÖŞÜ\.\-]+(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+){0,2})(?:\s*\([^)]*\))?\s+(?:Mahallesi|Mahalleleri|Köyü|Beldesi)")
_IL_TEK = re.compile(r"([A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü\.]+)?)\s+İli(?:,|\s)\s*(?:Sınırları|sınırları|Genelinde|genelinde)")


def yerler(metin: str, baslik: str):
    """'X İli, A, B ve C İlçeleri, Z Mahallesi' kalıpları → {(il, ilçe|None, mahalle|None)}. İlçe listesi virgül/ve ile ayrılır."""
    out = set()
    idari = _idari()
    for kaynak in (baslik, metin):
        txt = kaynak.replace("\n", " ")
        for m in _YER2.finditer(txt):
            il = tr_title(m.group(1).strip("."))
            il_n = normalize_name(il)
            if idari and il_n not in idari:
                continue
            parca = re.split(r",|\bve\b|\bile\b", m.group(2))
            ilceler = []
            for pcs in parca:
                ad = pcs.strip(" .")
                if not ad:
                    continue
                ad_n = normalize_name(ad)
                if idari and il_n in idari and ad_n not in idari[il_n][1] and ad_n != "merkez":
                    # "Merkez İlçesi, Karaağaç Mahallesi" gibi: son parça mahalle olabilir → ilçe değilse atla
                    continue
                ilceler.append(tr_title(ad))
            # kalıptan sonraki 120 karakterde mahalle
            kuyruk = txt[m.end():m.end() + 120]
            mah = _MAH2.match(kuyruk.lstrip(", "))
            mah_ad = tr_title(mah.group(1)) if mah else None
            for ilce in ilceler or [None]:
                out.add((il, ilce, mah_ad if len(ilceler) <= 1 else None))
        for m in _IL_TEK.finditer(txt):
            il = tr_title(m.group(1).strip("."))
            if not idari or normalize_name(il) in idari:
                out.add((il, None, None))
    return sorted(out, key=lambda x: (x[0], x[1] or "", x[2] or ""))


def yer_yaz(c, kid, metin, baslik):
    bulunan, kaynak_y = yerler(metin, baslik), "metin"
    if not bulunan:
        bulunan, kaynak_y = harita_yerleri(metin, baslik), "harita"
    c.execute("DELETE FROM karar_yer WHERE karar_id=?", (kid,))
    for il, ilce, mah in bulunan:
        c.execute("INSERT OR IGNORE INTO karar_yer VALUES (?,?,?,?,?,?,?,?)",
                  (kid, il, ilce, mah, normalize_name(il), normalize_name(ilce or ""), normalize_name(mah or ""), kaynak_y))


def main():
    if "--yer-yenile" in sys.argv:   # yalnız yer çıkarımını (regex/idari liste değişince) yeniden çalıştır; indirme/OCR yok
        c = sqlite3.connect(OUT)
        c.executescript("DROP TABLE IF EXISTS karar_yer; CREATE TABLE karar_yer (karar_id TEXT, il TEXT, ilce TEXT, mahalle TEXT, il_norm TEXT, ilce_norm TEXT, mahalle_norm TEXT, kaynak TEXT, PRIMARY KEY (karar_id, il_norm, ilce_norm, mahalle_norm)) WITHOUT ROWID; CREATE INDEX IF NOT EXISTS idx_karar_yer_ad ON karar_yer (il_norm, ilce_norm);")
        for kid, t, m in c.execute("SELECT id, baslik, metin FROM karar").fetchall():
            yer_yaz(c, kid, m or "", t)
        c.commit(); print("karar_yer:", c.execute("SELECT COUNT(*), COUNT(DISTINCT karar_id) FROM karar_yer").fetchone()); c.close(); return
    gun = int(sys.argv[sys.argv.index("--gun") + 1]) if "--gun" in sys.argv else 365
    if "--reset" in sys.argv and os.path.exists(OUT):
        os.remove(OUT)
    os.makedirs(RAW, exist_ok=True)
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS karar (id TEXT PRIMARY KEY, tarih TEXT, rg_sayi TEXT, karar_no TEXT, baslik TEXT, kategori TEXT, islem TEXT, dosya TEXT,
        metin TEXT, metin_sha256 TEXT, ocr INTEGER, guncellenme TEXT);
    CREATE INDEX IF NOT EXISTS idx_karar_tarih ON karar (tarih);
    CREATE TABLE IF NOT EXISTS karar_yer (karar_id TEXT, il TEXT, ilce TEXT, mahalle TEXT, il_norm TEXT, ilce_norm TEXT, mahalle_norm TEXT, kaynak TEXT,
        PRIMARY KEY (karar_id, il_norm, ilce_norm, mahalle_norm)) WITHOUT ROWID;   -- PK'da NULL olamaz: *_norm boş dize
    CREATE INDEX IF NOT EXISTS idx_karar_yer_ad ON karar_yer (il_norm, ilce_norm);
    CREATE TABLE IF NOT EXISTS taranan_gun (tarih TEXT PRIMARY KEY, madde INTEGER, eslesen INTEGER, guncellenme TEXT);
    CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
    """)
    now = datetime.now(timezone.utc).isoformat()
    n_new = 0
    for i in range(gun + 1):
        d = date.today() - timedelta(days=i)
        if c.execute("SELECT 1 FROM taranan_gun WHERE tarih=?", (d.isoformat(),)).fetchone() and i > 1:
            continue
        sayi, items = fihrist(d)
        esl = 0
        for u, t in items:
            k = kategori(t)
            if not k:
                continue
            esl += 1
            kid = u.split("/")[-1].rsplit(".", 1)[0]
            if c.execute("SELECT 1 FROM karar WHERE id=?", (kid,)).fetchone():
                continue
            metin, ocr_flag = "", 0
            if u.endswith(".pdf"):
                pdf = os.path.join(RAW, kid + ".pdf")
                if _curl(u, pdf):
                    try:
                        metin, ocr_flag = ocr(pdf), 1
                    except Exception as exc:
                        metin = ""; print("  OCR hata", kid, exc)
            elif u.endswith(".htm"):
                hh = _curl(u)
                metin = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", hh)))[:20000]
            kno = re.search(r"Karar Sayısı\s*:\s*(\d+)", t + " " + metin)
            c.execute("INSERT OR REPLACE INTO karar VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (kid, d.isoformat(), sayi, kno.group(1) if kno else None, t, k[0], k[1], u.split("/")[-1], metin,
                       hashlib.sha256(metin.encode()).hexdigest(), ocr_flag, now))
            yer_yaz(c, kid, metin, t)
            n_new += 1
            time.sleep(0.3)
        c.execute("INSERT OR REPLACE INTO taranan_gun VALUES (?,?,?,?)", (d.isoformat(), len(items), esl, now))
        c.commit()
        if i % 30 == 0:
            print(f"  {d} … yeni karar {n_new}", flush=True)
        time.sleep(0.3)
    for t, k in (("karar", "Resmî Gazete fihrist + karar PDF OCR"), ("karar_yer", "karar metninden il/ilçe/mahalle")):
        c.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?,?)", (t, c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], now, k))
    c.commit(); c.close()
    print("tamam; yeni karar:", n_new)


if __name__ == "__main__":
    main()
