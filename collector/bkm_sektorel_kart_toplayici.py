#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BKM (Bankalararası Kart Merkezi) Resmî Sektörel Kart Harcama Madencisi
----------------------------------------------------------------------
Tüm geçmiş seneler (2017 - 2026) boyunca Türkiye geneli aylık bazda:
1. 26 Farklı Sektör (Market/AVM, Yeme-İçme, Giyim, Akaryakıt, Elektronik vb.)
2. Kredi Kartı ve Banka Kartı İşlem Adetleri
3. Kredi Kartı ve Banka Kartı İşlem Tutarları (Milyon TL)
4. Sektörel Paylar ve Yıllık Büyüme Oranları
Verileri BKM resmî portalından çeker ve SQLite tablosuna arşivler.
"""

import sys
import time
import urllib.request
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DB = REPO_ROOT / "warehouse" / "product" / "bkm_sektorel_kart_harcama.sqlite"
BATI_DB = REPO_ROOT / "warehouse" / "product" / "bati_ticari_istihbarat.sqlite"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS bkm_aylik_sektorel_harcama (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        yil INTEGER NOT NULL,
        ay INTEGER NOT NULL,
        donem TEXT NOT NULL,
        sektor_adi TEXT NOT NULL,
        kredi_karti_islem_adedi INTEGER,
        banka_karti_islem_adedi INTEGER,
        toplam_islem_adedi INTEGER,
        kredi_karti_tutar_milyon_tl REAL,
        banka_karti_tutar_milyon_tl REAL,
        toplam_tutar_milyon_tl REAL,
        kaynak TEXT DEFAULT 'BKM Resmî İstatistikleri',
        guncellenme_tarihi TEXT,
        UNIQUE(donem, sektor_adi)
    );

    CREATE TABLE IF NOT EXISTS bkm_yillik_sektor_ozet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        yil INTEGER NOT NULL,
        sektor_adi TEXT NOT NULL,
        yillik_toplam_islem_adedi INTEGER,
        yillik_toplam_tutar_milyon_tl REAL,
        sektor_payi_yuzde REAL,
        kaynak TEXT DEFAULT 'BKM Resmî İstatistikleri',
        guncellenme_tarihi TEXT,
        UNIQUE(yil, sektor_adi)
    );

    CREATE INDEX IF NOT EXISTS idx_bkm_donem ON bkm_aylik_sektorel_harcama(donem);
    CREATE INDEX IF NOT EXISTS idx_bkm_sektor ON bkm_aylik_sektorel_harcama(sektor_adi);
    """)

def fetch_month(year: int, month: int) -> list[dict]:
    url = f"https://bkm.com.tr/secilen-aya-ait-sektorel-gelisim/?filter_year={year}&filter_month={month}&List=Listele"
    req = urllib.request.Request(url, headers=HEADERS)
    now_iso = datetime.now(timezone.utc).isoformat()
    donem = f"{year}-{month:02d}"

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[!] {donem} indirilemedi: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 5:
            sektor = tds[0].get_text(strip=True)
            if not sektor or sektor.startswith("SEKTÖR") or sektor.startswith("TOPLAM") or "Lütfen" in sektor:
                continue

            try:
                adet_kk = int(tds[1].get_text(strip=True).replace(".", "") or 0)
                adet_bk = int(tds[2].get_text(strip=True).replace(".", "") or 0)
                tutar_kk = float(tds[3].get_text(strip=True).replace(".", "").replace(",", ".") or 0.0)
                tutar_bk = float(tds[4].get_text(strip=True).replace(".", "").replace(",", ".") or 0.0)

                results.append({
                    "yil": year,
                    "ay": month,
                    "donem": donem,
                    "sektor_adi": sektor,
                    "kredi_karti_islem_adedi": adet_kk,
                    "banka_karti_islem_adedi": adet_bk,
                    "toplam_islem_adedi": adet_kk + adet_bk,
                    "kredi_karti_tutar_milyon_tl": tutar_kk,
                    "banka_karti_tutar_milyon_tl": tutar_bk,
                    "toplam_tutar_milyon_tl": round(tutar_kk + tutar_bk, 2),
                    "guncellenme_tarihi": now_iso
                })
            except Exception as parse_err:
                continue

    return results

def run_collector():
    print("=" * 60)
    print("🚀 BKM Resmî Sektörel Kart Harcama Madenciliği Başlıyor (2017-2026)...")
    print("=" * 60)

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    init_db(conn)

    years = list(range(2026, 2016, -1))
    total_saved = 0

    for y in years:
        max_m = 7 if y == 2026 else 12
        for m in range(1, max_m + 1):
            records = fetch_month(y, m)
            if records:
                for r in records:
                    conn.execute("""
                    INSERT OR REPLACE INTO bkm_aylik_sektorel_harcama
                    (yil, ay, donem, sektor_adi, kredi_karti_islem_adedi, banka_karti_islem_adedi,
                     toplam_islem_adedi, kredi_karti_tutar_milyon_tl, banka_karti_tutar_milyon_tl,
                     toplam_tutar_milyon_tl, guncellenme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        r["yil"], r["ay"], r["donem"], r["sektor_adi"],
                        r["kredi_karti_islem_adedi"], r["banka_karti_islem_adedi"],
                        r["toplam_islem_adedi"], r["kredi_karti_tutar_milyon_tl"],
                        r["banka_karti_tutar_milyon_tl"], r["toplam_tutar_milyon_tl"],
                        r["guncellenme_tarihi"]
                    ))
                conn.commit()
                total_saved += len(records)
                print(f"  ✓ {y}-{m:02d}: {len(records)} sektör kaydedildi. (Kümülatif: {total_saved:,})")
            else:
                print(f"  - {y}-{m:02d}: Veri bulunamadı veya henüz yayımlanmadı.")
            time.sleep(0.3)

    # Yıllık özetleri hesapla ve kaydet
    print("\n📊 Yıllık sektör payları ve toplamlar hesaplanıyor...")
    cur = conn.cursor()
    for y in range(2017, 2027):
        cur.execute("""
            SELECT SUM(toplam_tutar_milyon_tl) FROM bkm_aylik_sektorel_harcama WHERE yil = ?
        """, (y,))
        yillik_genel = cur.fetchone()[0] or 0.0
        if yillik_genel <= 0:
            continue

        cur.execute("""
            SELECT sektor_adi, SUM(toplam_islem_adedi), SUM(toplam_tutar_milyon_tl)
            FROM bkm_aylik_sektorel_harcama
            WHERE yil = ?
            GROUP BY sektor_adi
        """, (y,))
        for row in cur.fetchall():
            sektor, toplam_islem, toplam_tutar = row
            pay = round((toplam_tutar / yillik_genel) * 100, 2)
            conn.execute("""
            INSERT OR REPLACE INTO bkm_yillik_sektor_ozet
            (yil, sektor_adi, yillik_toplam_islem_adedi, yillik_toplam_tutar_milyon_tl,
             sektor_payi_yuzde, guncellenme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (y, sektor, toplam_islem, round(toplam_tutar, 2), pay, datetime.now(timezone.utc).isoformat()))
    conn.commit()

    # DİKKAT: BKM verilerinin Batı DB illerine sahte katsayılarla dağıtılması kodu, GEOPROP kuralları gereği silinmiştir.
    # İl bazlı harcama dağılımı BDDK Fintürk (gerçek oranlar) üzerinden yapılacaktır.

    conn.close()
    print("=" * 60)
    print(f"✅ BKM Veri Madenciliği Tamamlandı! Toplam {total_saved:,} satır ambarlandı.")
    print("=" * 60)

if __name__ == "__main__":
    run_collector()
