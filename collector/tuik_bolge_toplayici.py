"""TÜİK bölge göstergeleri → warehouse/product/tuik_bolge.sqlite

Kaynak dosyalar (warehouse/raw/tuik/, elle indirildi — TÜİK veri portalı ve MEDAS curl'e "Erişim engellendi" verir,
tarayıcı oturumundan alınır; MEDAS raporları 50.000 hücre sınırıyla parça parça CSV):
  yapi_izin_il_daire.xls            İllere göre yapı belgesi verilen daire sayısı (yıl + çeyrek; ruhsat / kullanma izni)
  medas_yapi_izin_ilce_daire.csv    MEDAS kn=135: ilçe × yıl daire sayısı (ruhsat, kullanma izni; "1. Binalar")
  medas_yapi_ruhsat_amac_ilce_*.csv MEDAS kn=135: ilçe × yıl × kullanım amacı yüzölçümü m² (11 ikamet; 121 otel, 122 ofis, 123 ticaret,
                                    124 trafik/iletişim, 125 sanayi/depo, 126 kamu-eğitim-sağlık, 127 diğer; 12 ikamet dışı toplam)
  medas_adnks_ilce_nufus.csv        MEDAS kn=95: ilçe × yıl nüfus (2007–)
  medas_adnks_mahalle_nufus_2025.csv / medas_adnks_koy_nufus_2025.csv   mahalle ve köy nüfusu (son yıl)
  medas_konut_satis_ilce_*.csv      MEDAS kn=73: ilçe × ay konut satış sayısı (2013–)
  goc_il.xls                        İllerin aldığı/verdiği/net göç ve hızı (dönem bazlı)
  sosyoekonomik_il_ilce_2023.xls    İl/ilçe SES skoru ve seviye dağılımı (2023)
  hanehalki_il.xls, nufus_artis_il.xls   il hanehalkı, nüfus projeksiyonu (2030)
Uydurma yok: boş hücre NULL. Adlar normalize_name ile *_norm sütunlarında. Veri sınıfı: açık (toplu istatistik).

  python3.13 collector/tuik_bolge_toplayici.py [--reset]
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys
from datetime import date

import xlrd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
from geoprop.veri_yardimcilari import normalize_name, tr_title  # noqa: E402

RAW = os.path.join(REPO, "warehouse", "raw", "tuik")
OUT = os.path.join(REPO, "warehouse", "product", "tuik_bolge.sqlite")
_DUZEY = re.compile(r"^(.*?)\((.*)\)-(\d+)$")   # "Adana(Aladağ)-1757", "Adana(Aladağ/Aladağ Bel./Akören Mah.)-176887"
IL_DUZELT = {"Afyon": "Afyonkarahisar"}


def _num(s):
    s = (s or "").strip().replace(",", ".")
    if not s or s in ("-", "…", "..", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _duzey(text):
    """→ (il, ilçe/alt ad, tuik_kodu)."""
    m = _DUZEY.match(text.strip())
    if not m:
        return None
    il, ic, kod = m.group(1).strip(), m.group(2).strip(), int(m.group(3))
    return IL_DUZELT.get(il, il), ic, kod


def schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS yapi_izin_il (il TEXT, il_norm TEXT, belge TEXT, yil INTEGER, ceyrek TEXT, daire INTEGER,
        PRIMARY KEY (il, belge, yil, ceyrek)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS yapi_izin_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, belge TEXT, yil INTEGER, daire INTEGER,
        PRIMARY KEY (tuik_kodu, belge, yil)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_yapi_izin_ilce_ad ON yapi_izin_ilce (il_norm, ilce_norm);
    CREATE TABLE IF NOT EXISTS yapi_ruhsat_amac_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, amac_kodu TEXT, amac TEXT, yil INTEGER, yuzolcumu_m2 REAL,
        PRIMARY KEY (tuik_kodu, amac_kodu, yil)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_yapi_ruhsat_amac_ad ON yapi_ruhsat_amac_ilce (il_norm, ilce_norm);
    CREATE TABLE IF NOT EXISTS nufus_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, yil INTEGER, nufus INTEGER,
        PRIMARY KEY (tuik_kodu, yil)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_nufus_ilce_ad ON nufus_ilce (il_norm, ilce_norm);
    CREATE TABLE IF NOT EXISTS nufus_mahalle (tuik_kodu INTEGER, il TEXT, ilce TEXT, belediye TEXT, mahalle TEXT, il_norm TEXT, ilce_norm TEXT,
        mahalle_norm TEXT, yil INTEGER, nufus INTEGER, PRIMARY KEY (tuik_kodu, yil)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_nufus_mahalle_ad ON nufus_mahalle (il_norm, ilce_norm, mahalle_norm);
    CREATE TABLE IF NOT EXISTS nufus_koy (tuik_kodu INTEGER, il TEXT, ilce TEXT, koy TEXT, il_norm TEXT, ilce_norm TEXT, koy_norm TEXT,
        yil INTEGER, nufus INTEGER, PRIMARY KEY (tuik_kodu, yil)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_nufus_koy_ad ON nufus_koy (il_norm, ilce_norm, koy_norm);
    CREATE TABLE IF NOT EXISTS konut_satis_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, yil INTEGER, ay INTEGER, satis INTEGER,
        PRIMARY KEY (tuik_kodu, yil, ay)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_konut_satis_ilce_ad ON konut_satis_ilce (il_norm, ilce_norm);
    CREATE TABLE IF NOT EXISTS goc_il (donem TEXT, il TEXT, il_norm TEXT, nufus INTEGER, aldigi INTEGER, verdigi INTEGER, net INTEGER, net_hiz_binde REAL,
        PRIMARY KEY (donem, il)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS ses_ilce (il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, yil INTEGER, ses_skor REAL, ust REAL, ust_alti REAL, orta REAL, alt REAL, en_alt REAL,
        PRIMARY KEY (il, ilce, yil)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS hanehalki_il (il TEXT, il_norm TEXT, hanehalki INTEGER, ortalama_buyukluk REAL, PRIMARY KEY (il)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS nufus_projeksiyon_il (il TEXT, il_norm TEXT, nufus_2023 INTEGER, nufus_2030 INTEGER, yillik_artis_binde REAL, PRIMARY KEY (il)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
    """)


def _kapsama(c, tablo, kaynak):
    n = c.execute(f"SELECT COUNT(*) FROM {tablo}").fetchone()[0]
    c.execute("INSERT OR REPLACE INTO kapsama VALUES (?,?,?,?)", (tablo, n, date.today().isoformat(), kaynak))
    print(f"  {tablo}: {n:,}")


def yapi_izin_il(c):
    sh = xlrd.open_workbook(os.path.join(RAW, "yapi_izin_il_daire.xls")).sheet_by_index(0)
    iller = [str(sh.cell_value(2, j)).strip() for j in range(4, sh.ncols)]
    belge = yil = None
    for r in range(3, sh.nrows):
        b = str(sh.cell_value(r, 0)).strip()
        if b.startswith("Yapı ruhsatı"):
            belge = "ruhsat"
        elif b.startswith("Yapı kullanma"):
            belge = "kullanma"
        elif b and not str(sh.cell_value(r, 1)).strip():
            continue   # dipnot
        y = _num(str(sh.cell_value(r, 1)))
        if y:
            yil = int(y)
        ceyrek = str(sh.cell_value(r, 2)).strip() or "yil"
        if belge is None or yil is None or _num(str(sh.cell_value(r, 3))) is None:
            continue
        for j, il in enumerate(iller):
            if not il:
                continue
            v = _num(str(sh.cell_value(r, 4 + j)))
            c.execute("INSERT OR REPLACE INTO yapi_izin_il VALUES (?,?,?,?,?,?)", (il, normalize_name(il), belge, yil, ceyrek, int(v) if v is not None else None))
    _kapsama(c, "yapi_izin_il", "TÜİK Yapı İzin İstatistikleri (il, yıl/çeyrek)")


def yapi_izin_ilce(c):
    L = open(os.path.join(RAW, "medas_yapi_izin_ilce_daire.csv"), encoding="utf-8").read().split("\n")
    kolonlar = [_duzey(x) for x in L[1].split("|")[3:]]
    belge = None
    for line in L[4:]:
        p = line.split("|")
        if len(p) < 4:
            continue
        if p[0].startswith("Yapı Ruhsat"):
            belge = "ruhsat"
        elif p[0].startswith("Yapı Kullanma"):
            belge = "kullanma"
        y = _num(p[2])
        if belge is None or y is None:
            continue
        for k, v in zip(kolonlar, p[3:]):
            if not k:
                continue
            il, ilce, kod = k
            n = _num(v)
            c.execute("INSERT OR REPLACE INTO yapi_izin_ilce VALUES (?,?,?,?,?,?,?,?)",
                      (kod, il, tr_title(ilce), normalize_name(il), normalize_name(ilce), belge, int(y), int(n) if n is not None else None))
    _kapsama(c, "yapi_izin_ilce", "TÜİK MEDAS Yapı İzin İstatistikleri (ilçe, yıl; 1. Binalar daire sayısı)")


def yapi_ruhsat_amac_ilce(c):
    for f in sorted(glob.glob(os.path.join(RAW, "medas_yapi_ruhsat_amac_ilce_*.csv"))):
        L = open(f, encoding="utf-8").read().split("\n")
        kolonlar = [_duzey(x) for x in L[1].split("|")[3:]]
        amac_kodu = amac = None
        for line in L[4:]:
            p = line.split("|")
            if len(p) < 4:
                continue
            m = re.match(r"(\d+)\.\s*\((.*)\)", p[1].strip())
            if m:
                amac_kodu, amac = m.group(1), m.group(2)
            y = _num(p[2])
            if amac_kodu is None or y is None:
                continue
            for k, v in zip(kolonlar, p[3:]):
                if not k:
                    continue
                il, ilce, kod = k
                n = _num(v)
                c.execute("INSERT OR REPLACE INTO yapi_ruhsat_amac_ilce VALUES (?,?,?,?,?,?,?,?,?)",
                          (kod, il, tr_title(ilce), normalize_name(il), normalize_name(ilce), amac_kodu, amac, int(y), n))
    _kapsama(c, "yapi_ruhsat_amac_ilce", "TÜİK MEDAS Yapı ruhsatı yüzölçümü, kullanım amacına göre (ilçe, yıl)")


def nufus_ilce(c):
    yil = None
    for line in open(os.path.join(RAW, "medas_adnks_ilce_nufus.csv"), encoding="utf-8").read().split("\n"):
        p = line.split("|")
        if len(p) < 3:
            continue
        if _num(p[0]):
            yil = int(_num(p[0]))
        k = _duzey(p[1]) if p[1] else None
        if not k or yil is None or _num(p[2]) is None:
            continue
        il, ilce, kod = k
        c.execute("INSERT OR REPLACE INTO nufus_ilce VALUES (?,?,?,?,?,?,?)", (kod, il, tr_title(ilce), normalize_name(il), normalize_name(ilce), yil, int(_num(p[2]))))
    _kapsama(c, "nufus_ilce", "TÜİK ADNKS ilçe nüfusu (MEDAS)")


def nufus_mahalle_koy(c):
    for dosya, tablo in (("medas_adnks_mahalle_nufus_*.csv", "nufus_mahalle"), ("medas_adnks_koy_nufus_*.csv", "nufus_koy")):
        yil = None
        for f in sorted(glob.glob(os.path.join(RAW, dosya))):
            for line in open(f, encoding="utf-8").read().split("\n"):
                p = line.split("|")
                if len(p) < 3:
                    continue
                if _num(p[0]):
                    yil = int(_num(p[0]))
                k = _duzey(p[1]) if p[1] else None
                if not k or yil is None or _num(p[2]) is None:
                    continue
                il, ic, kod = k
                parca = [x.strip() for x in ic.split("/")]
                nufus = int(_num(p[2]))
                if tablo == "nufus_mahalle":
                    # "Aladağ/Aladağ Bel./Akören Mah." → ilçe, belediye, mahalle
                    ilce = parca[0]; bel = parca[1] if len(parca) > 2 else None; mah = re.sub(r"\s*Mah\.?$", "", parca[-1])
                    c.execute("INSERT OR REPLACE INTO nufus_mahalle VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (kod, il, tr_title(ilce), bel, tr_title(mah), normalize_name(il), normalize_name(ilce), normalize_name(mah), yil, nufus))
                else:
                    ilce = parca[0]; koy = re.sub(r"\s*Köy\.?$", "", parca[-1])
                    c.execute("INSERT OR REPLACE INTO nufus_koy VALUES (?,?,?,?,?,?,?,?,?)",
                              (kod, il, tr_title(ilce), tr_title(koy), normalize_name(il), normalize_name(ilce), normalize_name(koy), yil, nufus))
        _kapsama(c, tablo, "TÜİK ADNKS belediye/köy/mahalle nüfusu (MEDAS)")


def konut_satis_ilce(c):
    for f in sorted(glob.glob(os.path.join(RAW, "medas_konut_satis_ilce_*.csv"))):
        L = open(f, encoding="utf-8").read().split("\n")
        ust = L[1].split("|")[3:]; aylar = L[2].split("|")[3:]
        kolon = []; cur = None
        for u, a in zip(ust, aylar):
            if u.strip():
                cur = _duzey(u)
            m = re.match(r"(\d{2})-", a.strip())
            kolon.append((cur, int(m.group(1))) if cur and m else None)
        for line in L[4:]:
            p = line.split("|")
            if len(p) < 4 or _num(p[2]) is None:
                continue
            yil = int(_num(p[2]))
            for k, v in zip(kolon, p[3:]):
                if not k:
                    continue
                (il, ilce, kod), ay = k
                n = _num(v)
                c.execute("INSERT OR REPLACE INTO konut_satis_ilce VALUES (?,?,?,?,?,?,?,?)",
                          (kod, il, tr_title(ilce), normalize_name(il), normalize_name(ilce), yil, ay, int(n) if n is not None else None))
    _kapsama(c, "konut_satis_ilce", "TÜİK MEDAS Konut Satış İstatistikleri (ilçe, ay)")


def goc_il(c):
    sh = xlrd.open_workbook(os.path.join(RAW, "goc_il.xls")).sheet_by_index(0)
    donem = None
    for r in range(3, sh.nrows):
        d = str(sh.cell_value(r, 0)).strip()
        if re.match(r"\d{4}-\d{4}", d):
            donem = d.split("(")[0]
        il = str(sh.cell_value(r, 1)).strip()
        if not donem or not il or il.startswith("Toplam") or _num(str(sh.cell_value(r, 2))) is None:
            continue
        vals = [_num(str(sh.cell_value(r, j))) for j in range(2, 7)]
        c.execute("INSERT OR REPLACE INTO goc_il VALUES (?,?,?,?,?,?,?,?)",
                  (donem, il, normalize_name(il), *[int(v) if v is not None else None for v in vals[:4]], vals[4]))
    _kapsama(c, "goc_il", "TÜİK ADNKS iller arası göç")


def ses_ilce(c):
    sh = xlrd.open_workbook(os.path.join(RAW, "sosyoekonomik_il_ilce_2023.xls")).sheet_by_index(0)
    for r in range(5, sh.nrows):
        il, ilce = str(sh.cell_value(r, 0)).strip(), str(sh.cell_value(r, 1)).strip()
        skor = _num(str(sh.cell_value(r, 2)))
        if not il or il.startswith("Toplam") or il.startswith("TÜİK") or skor is None:
            continue
        vals = [_num(str(sh.cell_value(r, j))) for j in range(4, 9)]
        c.execute("INSERT OR REPLACE INTO ses_ilce VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (il, ilce or "(il toplamı)", normalize_name(il), normalize_name(ilce) if ilce else "", 2023, skor, *vals))
    _kapsama(c, "ses_ilce", "TÜİK Sosyoekonomik Seviye 2023 (il/ilçe)")


def il_tablolari(c):
    sh = xlrd.open_workbook(os.path.join(RAW, "hanehalki_il.xls")).sheet_by_index(0)
    for r in range(3, sh.nrows):
        il = str(sh.cell_value(r, 0)).strip(); n = _num(str(sh.cell_value(r, 1)))
        if il and not il.startswith("Toplam") and n is not None and not il.startswith("Kaynak") and not il.startswith("TÜİK"):
            c.execute("INSERT OR REPLACE INTO hanehalki_il VALUES (?,?,?,?)", (il, normalize_name(il), int(n), _num(str(sh.cell_value(r, 2)))))
    _kapsama(c, "hanehalki_il", "TÜİK Nüfus ve Konut Sayımı 2021 hanehalkı")
    sh = xlrd.open_workbook(os.path.join(RAW, "nufus_artis_il.xls")).sheet_by_index(0)
    for r in range(4, sh.nrows):
        il = str(sh.cell_value(r, 0)).strip(); n = _num(str(sh.cell_value(r, 1)))
        if il and not il.startswith("Toplam") and n is not None and not il.startswith("Kaynak"):
            c.execute("INSERT OR REPLACE INTO nufus_projeksiyon_il VALUES (?,?,?,?,?)", (il, normalize_name(il), int(n), int(_num(str(sh.cell_value(r, 2))) or 0) or None, _num(str(sh.cell_value(r, 3)))))
    _kapsama(c, "nufus_projeksiyon_il", "TÜİK nüfus projeksiyonu 2023→2030")


def main():
    if "--reset" in sys.argv and os.path.exists(OUT):
        os.remove(OUT)
    c = sqlite3.connect(OUT); schema(c)
    for fn in (yapi_izin_il, yapi_izin_ilce, yapi_ruhsat_amac_ilce, nufus_ilce, nufus_mahalle_koy, konut_satis_ilce, goc_il, ses_ilce, il_tablolari):
        fn(c); c.commit()
    c.execute("ANALYZE"); c.commit(); c.close()
    print("tamam:", OUT)


if __name__ == "__main__":
    main()
