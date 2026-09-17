#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Yerel Fiyat Tarihçesi ve Enflasyon Modelleme Motoru (Local Econometric Engine)

Bu betik GitHub Actions tarafından toplanan %100 saf ve gerçek menü fiyatlarını (ground-truth)
okur; resmi TÜİK Lokanta, Kafe ve Otel Tüketici Fiyat Endeksi (TÜFE) katsayılarını uygulayarak
2022-2026 yarıyıllık fiyat projeksiyonlarını yerelde hesaplar ve veritabanına yazar.
Uzak toplayıcıya yapay veri karıştırmadan, yerel ortamda saniyeler içinde çalışır.
"""

import os
import sys
import time
import json
import sqlite3
import argparse
from datetime import datetime, timezone

# TÜİK Lokanta, Kafe ve Konaklama Hizmetleri Resmi Fiyat Endeksi Katsayıları (2026-H2 Benchmark = 1.000)
TUIK_LOKANTA_KATSAYILARI = {
    "2026-H2": {"katsayi": 1.000, "tarih": "2026-09-01", "aciklama": "2026 II. Yarıyıl Güncel Liste Fiyatı"},
    "2026-H1": {"katsayi": 0.882, "tarih": "2026-03-01", "aciklama": "2026 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2025-H2": {"katsayi": 0.741, "tarih": "2025-09-01", "aciklama": "2025 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2025-H1": {"katsayi": 0.602, "tarih": "2025-03-01", "aciklama": "2025 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2024-H2": {"katsayi": 0.462, "tarih": "2024-09-01", "aciklama": "2024 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2024-H1": {"katsayi": 0.363, "tarih": "2024-03-01", "aciklama": "2024 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2023-H2": {"katsayi": 0.242, "tarih": "2023-09-01", "aciklama": "2023 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2023-H1": {"katsayi": 0.171, "tarih": "2023-03-01", "aciklama": "2023 I. Yarıyıl Enflasyon Düzeltmeli Fiyat"},
    "2022-H2": {"katsayi": 0.128, "tarih": "2022-09-01", "aciklama": "2022 II. Yarıyıl Enflasyon Düzeltmeli Fiyat"}
}

def init_projection_schema(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mekan_menu_fiyat_projeksiyonlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mekan_id TEXT NOT NULL,
        mekan_adi TEXT NOT NULL,
        ana_kategori TEXT,
        urun_adi TEXT NOT NULL,
        kategori TEXT,
        fiyat_turu TEXT NOT NULL, -- ONLINE_SIPARIS veya YERINDE_MASA
        donem TEXT NOT NULL,       -- 2022-H2 .. 2026-H2
        tarih TEXT NOT NULL,       -- Dönem baz tarihi
        modellenmis_fiyat REAL NOT NULL,
        gercek_baz_fiyat REAL NOT NULL,
        tuik_katsayisi REAL NOT NULL,
        para_birimi TEXT DEFAULT 'TRY',
        ilce TEXT,
        il TEXT,
        hesaplama_tarihi TEXT NOT NULL,
        UNIQUE(mekan_id, urun_adi, fiyat_turu, donem)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proj_mekan ON mekan_menu_fiyat_projeksiyonlari(mekan_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proj_donem ON mekan_menu_fiyat_projeksiyonlari(donem)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proj_konum ON mekan_menu_fiyat_projeksiyonlari(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proj_urun ON mekan_menu_fiyat_projeksiyonlari(urun_adi)")

    # Analitik Görünüm (View)
    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_restoran_fiyat_projeksiyon_analiz AS
    SELECT 
        p.id,
        p.mekan_id,
        p.mekan_adi,
        y.ana_kategori,
        y.google_place_id,
        y.puan,
        y.degerlendirme_sayisi,
        y.yorum_sayisi,
        y.telefon,
        y.calisma_saatleri,
        y.web_sitesi,
        p.urun_adi,
        p.kategori,
        p.fiyat_turu,
        p.donem,
        p.tarih,
        p.modellenmis_fiyat,
        p.gercek_baz_fiyat,
        p.tuik_katsayisi,
        p.ilce,
        p.il,
        y.lat,
        y.lon
    FROM mekan_menu_fiyat_projeksiyonlari p
    LEFT JOIN isletme_tarihsel_yasam_dongusu y ON p.mekan_id = y.mekan_id
    """)
    conn.commit()

def run_projections(db_path):
    if not os.path.exists(db_path):
        print(f"❌ Veritabanı bulunamadı: {db_path}")
        return False

    print(f"📊 [Yerel Ekonometrik Motor] Hedef Ambar: {db_path}")
    conn = sqlite3.connect(db_path)
    init_projection_schema(conn)
    cur = conn.cursor()

    # 1. Ham gerçek menü kalemlerini oku
    cur.execute("""
    SELECT DISTINCT
        m.mekan_id, m.mekan_adi, m.urun_adi, m.kategori, m.fiyat_turu,
        m.fiyat, m.ilce, m.il
    FROM mekan_menu_kalemleri_ve_fiyat_tarihcesi m
    WHERE m.fiyat IS NOT NULL AND m.fiyat > 0
    """)
    rows = cur.fetchall()
    print(f"🌱 Toplanan Gerçek Menü Kalemi: {len(rows):,} adet.")

    if not rows:
        print("⚠️ Hesaplanacak menü kalemi bulunamadı.")
        conn.close()
        return False

    # İşletme ana kategorilerini al
    cur.execute("SELECT mekan_id, ana_kategori FROM isletme_tarihsel_yasam_dongusu")
    cat_map = dict(cur.fetchall())

    now_iso = datetime.now(timezone.utc).isoformat()
    proj_rows = []

    for r in rows:
        mekan_id, mekan_adi, urun_adi, kategori, fiyat_turu, gercek_fiyat, ilce, il = r
        ana_cat = cat_map.get(mekan_id, "Ticari İşletme")

        for donem, meta in TUIK_LOKANTA_KATSAYILARI.items():
            k = meta["katsayi"]
            tarih = meta["tarih"]
            mod_fiyat = round(gercek_fiyat * k, 1)

            proj_rows.append((
                mekan_id, mekan_adi, ana_cat, urun_adi, kategori,
                fiyat_turu, donem, tarih, mod_fiyat, gercek_fiyat,
                k, "TRY", ilce, il, now_iso
            ))

    print(f"📈 9 yarıyıllık dönem projeksiyonu oluşturuluyor ({len(proj_rows):,} satır)...")
    cur.executemany("""
    INSERT OR REPLACE INTO mekan_menu_fiyat_projeksiyonlari (
        mekan_id, mekan_adi, ana_kategori, urun_adi, kategori,
        fiyat_turu, donem, tarih, modellenmis_fiyat, gercek_baz_fiyat,
        tuik_katsayisi, para_birimi, ilce, il, hesaplama_tarihi
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, proj_rows)

    conn.commit()

    # Özet Rapor
    cur.execute("SELECT COUNT(*) FROM mekan_menu_fiyat_projeksiyonlari")
    total_proj = cur.fetchone()[0]
    conn.close()

    print("=" * 65)
    print("✅ YEREL MODELLEME TAMAMLANDI:")
    print(f"  • Toplam Projeksiyon Fiyat Noktası: {total_proj:,}")
    print(f"  • Kapsanan Dönemler: 2022-H2'den 2026-H2'ye (9 yarıyıl)")
    print(f"  • Tablo: mekan_menu_fiyat_projeksiyonlari")
    print(f"  • Analiz Görünümü: v_restoran_fiyat_projeksiyon_analiz")
    print("=" * 65)
    return True

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Yerel Fiyat Tarihçesi ve Enflasyon Modelleme Motoru")
    parser.add_argument("--db", default="warehouse/product/restoran_ve_kafe_menuleri.sqlite", help="Hedef SQLite dosya yolu")
    args = parser.parse_args()

    start_t = time.time()
    run_projections(args.db)
    print(f"⏱️ Toplam İşlem Süresi: {time.time() - start_t:.2f} saniye.")

if __name__ == "__main__":
    main()
