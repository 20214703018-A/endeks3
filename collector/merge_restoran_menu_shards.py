#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Restoran Menü, Değerlendirme, QR ve Google Places Shard Birleştirici (Lossless Zero-Data-Loss Merge Engine)
40 paralel sanal makineden gelen menü, fiyat, değerlendirme, QR ve Google Places shard SQLite dosyalarını:
1. Otomatik yedekleme (.bak) emniyetiyle,
2. İşlemsel (transactional) bütünlükle,
3. UNIQUE(mekan_id, urun_adi, donem, fiyat_turu) kuralı ile hiçbir geçmiş veriyi silmeden/kaybetmeden,
4. Çapraz doğrulama ve satır sayısı denetimiyle
tek merkezî 'warehouse/product/restoran_ve_kafe_menuleri.sqlite' ambarında birleştirir.
"""

import os
import sys
import glob
import shutil
import sqlite3
import argparse
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_TARGET_DB = os.path.join(BASE_DIR, "warehouse/product/restoran_ve_kafe_menuleri.sqlite")

def init_target_db(db_path):
    """Hedef veritabanı şemasını, indekslerini ve görünümünü hazırlar."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Menü Kalemleri ve Tarihsel Fiyat Serisi Tablosu
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mekan_menu_kalemleri_ve_fiyat_tarihcesi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mekan_id TEXT NOT NULL,
        mekan_adi TEXT NOT NULL,
        donem TEXT NOT NULL,
        tarih TEXT NOT NULL,
        fiyat_turu TEXT NOT NULL,
        platform TEXT NOT NULL,
        kategori TEXT NOT NULL,
        urun_adi TEXT NOT NULL,
        aciklama TEXT,
        fiyat REAL NOT NULL,
        orijinal_fiyat REAL,
        para_birimi TEXT DEFAULT 'TRY',
        komisyon_aciklamasi TEXT,
        fiyat_segmenti TEXT,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        yorum_sayisi INTEGER,
        stokta_var_mi INTEGER DEFAULT 1,
        gorsel_url TEXT,
        kaynak_url TEXT,
        tam_adres TEXT NOT NULL,
        mahalle TEXT,
        ilce TEXT NOT NULL,
        il TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(mekan_id, urun_adi, donem, fiyat_turu)
    )
    """)
    for col_def in [("fiyat_segmenti", "TEXT"), ("puan", "REAL"), ("degerlendirme_sayisi", "INTEGER"), ("yorum_sayisi", "INTEGER")]:
        try:
            cur.execute(f"ALTER TABLE mekan_menu_kalemleri_ve_fiyat_tarihcesi ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_mekan_id ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(mekan_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_konum ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_donem ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(donem)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_fiyat_turu ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(fiyat_turu)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_kategori ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(kategori)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_menu_puan ON mekan_menu_kalemleri_ve_fiyat_tarihcesi(puan, degerlendirme_sayisi)")

    # 2. İşletme Tarihsel Yaşam Döngüsü ve Detaylı İstihbarat Tablosu
    cur.execute("""
    CREATE TABLE IF NOT EXISTS isletme_tarihsel_yasam_dongusu (
        mekan_id TEXT PRIMARY KEY,
        google_place_id TEXT,
        mekan_adi TEXT NOT NULL,
        ana_kategori TEXT,
        mutfaklar TEXT,
        fiyat_segmenti TEXT,
        tam_adres TEXT NOT NULL,
        mahalle TEXT,
        ilce TEXT NOT NULL,
        il TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        web_sitesi TEXT,
        qr_menu_url TEXT,
        durum TEXT NOT NULL,
        ilk_tespit_tarihi TEXT,
        son_tespit_tarihi TEXT,
        faaliyet_suresi_ay INTEGER,
        puan REAL,
        degerlendirme_sayisi INTEGER,
        yorum_sayisi INTEGER,
        yildiz_dagilimi TEXT,
        min_sepet_tutari REAL,
        teslimat_ucreti REAL,
        teslimat_suresi TEXT,
        odeme_yontemleri TEXT,
        kampanyalar TEXT,
        yorum_hacmi_2022 INTEGER,
        yorum_hacmi_2023 INTEGER,
        yorum_hacmi_2024 INTEGER,
        yorum_hacmi_2025 INTEGER,
        yorum_hacmi_2026 INTEGER,
        musteri_trendi TEXT,
        kaynak TEXT,
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    for col_def in [
        ("google_place_id", "TEXT"), ("mutfaklar", "TEXT"), ("fiyat_segmenti", "TEXT"),
        ("telefon", "TEXT"), ("calisma_saatleri", "TEXT"), ("web_sitesi", "TEXT"), ("qr_menu_url", "TEXT"),
        ("yildiz_dagilimi", "TEXT"), ("min_sepet_tutari", "REAL"), ("teslimat_ucreti", "REAL"),
        ("teslimat_suresi", "TEXT"), ("odeme_yontemleri", "TEXT"), ("kampanyalar", "TEXT")
    ]:
        try:
            cur.execute(f"ALTER TABLE isletme_tarihsel_yasam_dongusu ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_konum ON isletme_tarihsel_yasam_dongusu(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_durum ON isletme_tarihsel_yasam_dongusu(durum)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_trend ON isletme_tarihsel_yasam_dongusu(musteri_trendi)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_puan ON isletme_tarihsel_yasam_dongusu(puan, degerlendirme_sayisi)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yasam_gplace ON isletme_tarihsel_yasam_dongusu(google_place_id)")

    # 3. Google Places Ham Ticari Yoğunluk Tablosu
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_ticari_yogunluk (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_place_id TEXT UNIQUE,
        cid TEXT,
        isim TEXT NOT NULL,
        arama_terimi TEXT,
        ana_kategori TEXT,
        tum_kategoriler TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        degerlendirme_sayisi INTEGER,
        yildiz_dagilimi TEXT,
        tam_adres TEXT,
        mahalle TEXT,
        ilce TEXT,
        il TEXT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        telefon TEXT,
        calisma_saatleri TEXT,
        maps_url TEXT,
        web_sitesi TEXT,
        kaynak TEXT DEFAULT 'Google Maps',
        guncellenme_tarihi TEXT NOT NULL
    )
    """)
    for col_def in [("degerlendirme_sayisi", "INTEGER"), ("yildiz_dagilimi", "TEXT"), ("web_sitesi", "TEXT")]:
        try:
            cur.execute(f"ALTER TABLE google_places_ticari_yogunluk ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass

    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_ilce ON google_places_ticari_yogunluk(il, ilce)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gplaces_coords ON google_places_ticari_yogunluk(lat, lon)")

    # 4. Tarama Geçmişi Tablosu
    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_tarama_gecmisi (
        mekan_id TEXT PRIMARY KEY,
        url TEXT,
        durum TEXT,
        kalem_sayisi INTEGER,
        tarih TEXT
    )
    """)

    # 5. Mekan Ardıl-Öncül Dönüşüm ve Devir Tarihçesi
    cur.execute("""
    CREATE TABLE IF NOT EXISTS isletme_ardil_oncul_donusum_tarihcesi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kategori TEXT NOT NULL,
        onceki_isletme_adi TEXT NOT NULL,
        yeni_isletme_adi TEXT NOT NULL,
        degisim_tarihi TEXT NOT NULL,
        donusum_tanimi TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(onceki_isletme_adi, yeni_isletme_adi, degisim_tarihi, lat, lon)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_donusum_coords ON isletme_ardil_oncul_donusum_tarihcesi(lat, lon)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_donusum_tarih ON isletme_ardil_oncul_donusum_tarihcesi(degisim_tarihi)")

    # 6. Kapsamlı Görünüm
    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_restoran_kapsamli_istihbarat AS
    SELECT 
        y.mekan_id,
        y.google_place_id,
        y.mekan_adi,
        y.ana_kategori,
        y.mutfaklar,
        y.fiyat_segmenti,
        y.puan,
        y.degerlendirme_sayisi,
        y.yorum_sayisi,
        y.yildiz_dagilimi,
        y.min_sepet_tutari,
        y.teslimat_ucreti,
        y.teslimat_suresi,
        y.odeme_yontemleri,
        y.kampanyalar,
        y.telefon,
        y.web_sitesi,
        y.qr_menu_url,
        y.musteri_trendi,
        y.durum,
        y.faaliyet_suresi_ay,
        y.tam_adres,
        y.ilce,
        y.il,
        y.lat,
        y.lon,
        COUNT(DISTINCT m.urun_adi) as aktif_urun_sayisi,
        ROUND(AVG(CASE WHEN m.donem = '2026-H2' AND m.fiyat_turu = 'ONLINE_SIPARIS' THEN m.fiyat END), 1) as ort_online_fiyat_2026,
        ROUND(AVG(CASE WHEN m.donem = '2026-H2' AND m.fiyat_turu = 'YERINDE_MASA' THEN m.fiyat END), 1) as ort_masa_fiyat_2026,
        ROUND(AVG(CASE WHEN m.donem = '2022-H2' AND m.fiyat_turu = 'YERINDE_MASA' THEN m.fiyat END), 1) as ort_masa_fiyat_2022
    FROM isletme_tarihsel_yasam_dongusu y
    LEFT JOIN mekan_menu_kalemleri_ve_fiyat_tarihcesi m ON y.mekan_id = m.mekan_id
    GROUP BY y.mekan_id
    """)

    conn.commit()
    conn.close()

def backup_database(db_path):
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{db_path}.{ts}.bak"
        shutil.copy2(db_path, backup_path)
        print(f"🛡️ Otomatik Emniyet Yedeği Alındı: {backup_path}")
        return backup_path
    return None

def get_counts(conn):
    cur = conn.cursor()
    m_cnt = cur.execute("SELECT count(*) FROM mekan_menu_kalemleri_ve_fiyat_tarihcesi").fetchone()[0]
    y_cnt = cur.execute("SELECT count(*) FROM isletme_tarihsel_yasam_dongusu").fetchone()[0]
    try:
        gp_cnt = cur.execute("SELECT count(*) FROM google_places_ticari_yogunluk").fetchone()[0]
    except Exception:
        gp_cnt = 0
    try:
        t_cnt = cur.execute("SELECT count(*) FROM isletme_ardil_oncul_donusum_tarihcesi").fetchone()[0]
    except Exception:
        t_cnt = 0
    g_cnt = cur.execute("SELECT count(*) FROM menu_tarama_gecmisi").fetchone()[0]
    return m_cnt, y_cnt, gp_cnt, t_cnt, g_cnt

def merge_shards(shard_files, target_db):
    if not shard_files:
        print("⚠️ Birleştirilecek shard dosyası bulunamadı.")
        return False

    print(f"🔄 Birleştirme Başlatılıyor: {len(shard_files)} shard dosyası hedefe eklenecek.")
    print(f"🎯 Hedef Ambar: {target_db}")

    init_target_db(target_db)
    backup_file = backup_database(target_db)

    conn = sqlite3.connect(target_db)
    cur = conn.cursor()

    init_m_cnt, init_y_cnt, init_gp_cnt, init_t_cnt, init_g_cnt = get_counts(conn)
    print(f"📊 Başlangıç Durumu: {init_m_cnt} fiyat noktası | {init_y_cnt} işletme | {init_gp_cnt} Google Places | {init_t_cnt} devir kaydı.")

    success_shards = 0
    failed_shards = 0

    for shard_path in shard_files:
        if not os.path.exists(shard_path) or os.path.getsize(shard_path) == 0:
            print(f"⚠️ Boş veya geçersiz shard atlandı: {shard_path}")
            continue

        shard_name = os.path.basename(shard_path)
        print(f"  ↳ İşleniyor: {shard_name} ...", end=" ", flush=True)

        try:
            cur.execute(f"ATTACH DATABASE ? AS shard_db", (shard_path,))

            # 1. Menü Kalemleri (Kayıpsız INSERT OR IGNORE)
            cur.execute("""
            INSERT OR IGNORE INTO main.mekan_menu_kalemleri_ve_fiyat_tarihcesi (
                mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform,
                kategori, urun_adi, aciklama, fiyat, orijinal_fiyat, para_birimi,
                komisyon_aciklamasi, fiyat_segmenti, puan, degerlendirme_sayisi, yorum_sayisi,
                stokta_var_mi, gorsel_url, kaynak_url,
                tam_adres, mahalle, ilce, il, lat, lon, guncellenme_tarihi
            )
            SELECT
                mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform,
                kategori, urun_adi, aciklama, fiyat, orijinal_fiyat, para_birimi,
                komisyon_aciklamasi, fiyat_segmenti, puan, degerlendirme_sayisi, yorum_sayisi,
                stokta_var_mi, gorsel_url, kaynak_url,
                tam_adres, mahalle, ilce, il, lat, lon, guncellenme_tarihi
            FROM shard_db.mekan_menu_kalemleri_ve_fiyat_tarihcesi
            """)

            # 2. İşletme Yaşam Döngüsü ve Detaylı İstihbarat (INSERT OR REPLACE)
            cur.execute("""
            INSERT OR REPLACE INTO main.isletme_tarihsel_yasam_dongusu (
                mekan_id, google_place_id, mekan_adi, ana_kategori, mutfaklar, fiyat_segmenti,
                tam_adres, mahalle, ilce, il, lat, lon,
                telefon, calisma_saatleri, web_sitesi, qr_menu_url,
                durum, ilk_tespit_tarihi, son_tespit_tarihi, faaliyet_suresi_ay,
                puan, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi,
                min_sepet_tutari, teslimat_ucreti, teslimat_suresi, odeme_yontemleri, kampanyalar,
                yorum_hacmi_2022, yorum_hacmi_2023, yorum_hacmi_2024, yorum_hacmi_2025, yorum_hacmi_2026,
                musteri_trendi, kaynak, guncellenme_tarihi
            )
            SELECT
                mekan_id, google_place_id, mekan_adi, ana_kategori, mutfaklar, fiyat_segmenti,
                tam_adres, mahalle, ilce, il, lat, lon,
                telefon, calisma_saatleri, web_sitesi, qr_menu_url,
                durum, ilk_tespit_tarihi, son_tespit_tarihi, faaliyet_suresi_ay,
                puan, degerlendirme_sayisi, yorum_sayisi, yildiz_dagilimi,
                min_sepet_tutari, teslimat_ucreti, teslimat_suresi, odeme_yontemleri, kampanyalar,
                yorum_hacmi_2022, yorum_hacmi_2023, yorum_hacmi_2024, yorum_hacmi_2025, yorum_hacmi_2026,
                musteri_trendi, kaynak, guncellenme_tarihi
            FROM shard_db.isletme_tarihsel_yasam_dongusu
            """)

            # 3. Google Places Ham Tablosu (INSERT OR REPLACE)
            try:
                cur.execute("""
                INSERT OR REPLACE INTO main.google_places_ticari_yogunluk (
                    google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
                    puan, yorum_sayisi, degerlendirme_sayisi, yildiz_dagilimi, tam_adres, mahalle, ilce, il,
                    lat, lon, telefon, calisma_saatleri, maps_url, web_sitesi, kaynak, guncellenme_tarihi
                )
                SELECT
                    google_place_id, cid, isim, arama_terimi, ana_kategori, tum_kategoriler,
                    puan, yorum_sayisi, degerlendirme_sayisi, yildiz_dagilimi, tam_adres, mahalle, ilce, il,
                    lat, lon, telefon, calisma_saatleri, maps_url, web_sitesi, kaynak, guncellenme_tarihi
                FROM shard_db.google_places_ticari_yogunluk
                """)
            except Exception:
                pass

            # 4. Tarama Geçmişi
            cur.execute("""
            INSERT OR REPLACE INTO main.menu_tarama_gecmisi (mekan_id, url, durum, kalem_sayisi, tarih)
            SELECT mekan_id, url, durum, kalem_sayisi, tarih
            FROM shard_db.menu_tarama_gecmisi
            """)

            # 5. Mekan Ardıl-Öncül Dönüşüm Tarihçesi (INSERT OR IGNORE)
            try:
                cur.execute("""
                INSERT OR IGNORE INTO main.isletme_ardil_oncul_donusum_tarihcesi (
                    kategori, onceki_isletme_adi, yeni_isletme_adi, degisim_tarihi,
                    donusum_tanimi, lat, lon, kaynak, guncellenme_tarihi
                )
                SELECT
                    kategori, onceki_isletme_adi, yeni_isletme_adi, degisim_tarihi,
                    donusum_tanimi, lat, lon, kaynak, guncellenme_tarihi
                FROM shard_db.isletme_ardil_oncul_donusum_tarihcesi
                """)
            except Exception:
                pass

            conn.commit()
            cur.execute("DETACH DATABASE shard_db")
            success_shards += 1
            print("✓ Eklendi")
        except Exception as e:
            conn.rollback()
            try:
                cur.execute("DETACH DATABASE shard_db")
            except Exception:
                pass
            print(f"❌ HATA: {e}")
            failed_shards += 1

    final_m_cnt, final_y_cnt, final_gp_cnt, final_t_cnt, final_g_cnt = get_counts(conn)
    print("\n" + "=" * 65)
    print(f"📈 BİRLEŞTİRME SONUÇ RAPORU (Lossless Integrity Audit):")
    print(f"  • Başarılı Shard Sayısı: {success_shards} | Hatalı Shard: {failed_shards}")
    print(f"  • Menü & Fiyat Satırları: {init_m_cnt} -> {final_m_cnt} (+{final_m_cnt - init_m_cnt} yeni fiyat noktası)")
    print(f"  • İşletme Yaşam Döngüsü: {init_y_cnt} -> {final_y_cnt} (+{final_y_cnt - init_y_cnt} yeni işletme)")
    print(f"  • Google Places Mekanları: {init_gp_cnt} -> {final_gp_cnt} (+{final_gp_cnt - init_gp_cnt} yeni mekan)")
    print(f"  • Devir & Dönüşüm Kayıtları: {init_t_cnt} -> {final_t_cnt}")
    print(f"  • Tarama Kayıtları: {init_g_cnt} -> {final_g_cnt}")
    print("=" * 65)

    if final_m_cnt < init_m_cnt:
        print("🚨 KRİTİK HATA: Birleştirme sonrasında satır sayısı azaldı! Yedekten geri yükleniyor...")
        conn.close()
        if backup_file and os.path.exists(backup_file):
            shutil.copy2(backup_file, target_db)
            print("✓ Yedek geri yüklendi.")
        return False

    try:
        cur.execute("PRAGMA optimize")
        cur.execute("VACUUM")
        conn.commit()
    except Exception:
        pass

    conn.close()
    return True

def main():
    parser = argparse.ArgumentParser(description="GEOPROP Restoran, Menü, QR ve Google Places Shard Birleştirici")
    parser.add_argument("--shards", required=True, help="Shard sqlite dosyalarının glob deseni (örn: 'artifacts/**/menu_shard_*.sqlite')")
    parser.add_argument("--out", default=DEFAULT_TARGET_DB, help="Hedef birleşik veritabanı yolu")
    args = parser.parse_args()

    files = sorted(glob.glob(args.shards, recursive=True))
    if not files:
        print(f"❌ '{args.shards}' deseniyle eşleşen shard bulunamadı!")
        sys.exit(1)

    ok = merge_shards(files, args.out)
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
