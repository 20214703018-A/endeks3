#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP Araç GPS ve Trafik Yoğunluğu Toplayıcı
==============================================
İBB Açık Veri Portalı resmî saatlik araç yoğunluğu ve hız (Floating Car Data) akışını okur:
- URL: Ocak 2025 Saatlik Trafik Yoğunluğu (141 MB CSV, ~2.5M satır)
- Parametreler: DATE_TIME, LATITUDE, LONGITUDE, GEOHASH, MINIMUM_SPEED, MAXIMUM_SPEED, AVERAGE_SPEED, NUMBER_OF_VEHICLES

Hesaplanan Metrikler (Geohash ve Yol Segmenti Bazında):
- Günlük ortalama geçen araç sayısı
- Zirve saat (peak hour) araç debisi
- Ortalama koridor akış hızı (km/s)
- Trafik sıkışıklık oranı (% kaç saat hız 30 km/s altında)
- Trafik ve ticari görünürlük skoru

Hedef Ambar: warehouse/product/ulasim_ve_hareketlilik_gps.sqlite
Kural Uyumu: %100 ampirik gerçek GPS sensör verisi, sentetik yok, ISO 8601.
"""

import sys
import os
import time
import csv
import sqlite3
import datetime
import urllib.request
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "warehouse", "product", "ulasim_ve_hareketlilik_gps.sqlite")
IBB_TRAFFIC_URL_2025_01 = "https://data.ibb.gov.tr/dataset/3ee6d744-5da2-40c8-9cd6-0e3e41f1928f/resource/57cb067b-1a0b-460b-8342-7884bd4537e8/download/traffic_density_202501.csv"

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 1. Koridor / Geohash Özet Profili (Hızlı mekânsal eşleşme ve ticari lokasyon skoru için)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS arac_gps_koridor_profili (
        geohash TEXT PRIMARY KEY,
        il TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        toplam_gozlem_saati INTEGER NOT NULL,
        toplam_arac_hacmi INTEGER NOT NULL,
        gunluk_ortalama_arac REAL NOT NULL,
        zirve_saat_arac INTEGER NOT NULL,
        ortalama_akinti_hizi REAL NOT NULL,
        min_kaydedilen_hiz REAL NOT NULL,
        sıkısik_saat_sayisi INTEGER NOT NULL,
        sıkısiklik_orani_yuzde REAL NOT NULL,
        trafik_kategorisi TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_arac_gps_lat_lon ON arac_gps_koridor_profili(lat, lon);")
    
    # 2. Saatlik Gerçek Zamanlı Trafik Akışı Örneklemi (Zirve saatler ve kritik akslar)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS arac_gps_saatlik_akis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        tarih_saat TEXT NOT NULL,
        geohash TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        arac_sayisi INTEGER NOT NULL,
        ortalama_hiz REAL NOT NULL,
        min_hiz REAL NOT NULL,
        max_hiz REAL NOT NULL,
        yogunluk_seviyesi TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_saatlik_geo_tarih ON arac_gps_saatlik_akis(geohash, tarih_saat);")
    
    conn.commit()
    conn.close()

def stream_and_ingest_traffic(url=IBB_TRAFFIC_URL_2025_01, max_records=None):
    init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now_iso = datetime.datetime.now().isoformat()
    
    print(f"🚗 [ARAÇ GPS / TRAFİK] İBB Floating Car Data akışı başlatılıyor...")
    print(f"   URL: {url}")
    
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    
    # Geohash aggregations
    # geohash -> {lat, lon, count, sum_vehicles, max_vehicles, sum_speed, min_speed, congested_hours, unique_days}
    geo_stats = defaultdict(lambda: {
        "lat": 0.0, "lon": 0.0, "obs": 0, "sum_v": 0, "max_v": 0,
        "sum_spd": 0.0, "min_spd": 999.0, "congested": 0, "days": set()
    })
    
    hourly_sample = []
    total_processed = 0
    start_time = time.time()
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        # Read stream line by line
        header_line = resp.readline().decode("utf-8", errors="ignore").strip()
        header = [h.strip('"\ufeff') for h in header_line.split(",")]
        
        # Expected: DATE_TIME, LATITUDE, LONGITUDE, GEOHASH, MINIMUM_SPEED, MAXIMUM_SPEED, AVERAGE_SPEED, NUMBER_OF_VEHICLES
        dt_idx = 0
        lat_idx = 1
        lon_idx = 2
        geo_idx = 3
        min_spd_idx = 4
        max_spd_idx = 5
        avg_spd_idx = 6
        num_v_idx = 7
        
        for line in resp:
            line_str = line.decode("utf-8", errors="ignore").strip()
            if not line_str:
                continue
            parts = [p.strip('"') for p in line_str.split(",")]
            if len(parts) < 8:
                continue
                
            try:
                dt = parts[dt_idx]
                lat = float(parts[lat_idx])
                lon = float(parts[lon_idx])
                geohash = parts[geo_idx].strip()
                min_spd = float(parts[min_spd_idx])
                max_spd = float(parts[max_spd_idx])
                avg_spd = float(parts[avg_spd_idx])
                num_v = int(parts[num_v_idx])
                
                day = dt[:10]
                
                # Aggregate
                st = geo_stats[geohash]
                st["lat"] = lat
                st["lon"] = lon
                st["obs"] += 1
                st["sum_v"] += num_v
                if num_v > st["max_v"]:
                    st["max_v"] = num_v
                st["sum_spd"] += avg_spd
                if min_spd < st["min_spd"]:
                    st["min_spd"] = min_spd
                if avg_spd < 30.0:
                    st["congested"] += 1
                st["days"].add(day)
                
                # Sample representative hours (every 100th record or high density)
                if total_processed % 80 == 0:
                    yogunluk = "Sıkışık" if avg_spd < 25 else ("Yoğun" if avg_spd < 50 else "Akıcı")
                    hourly_sample.append(("İstanbul", dt, geohash, lat, lon, num_v, avg_spd, min_spd, max_spd, yogunluk, now_iso))
                
                total_processed += 1
                
                if total_processed % 100000 == 0:
                    elapsed = time.time() - start_time
                    print(f"   ... {total_processed:,} araç GPS gözlemi işlendi ({len(geo_stats)} aktif arter) [{elapsed:.1f}s]")
                    
                if max_records and total_processed >= max_records:
                    print(f"   Belirtilen sınır ({max_records:,}) değerine ulaşıldı.")
                    break
            except (ValueError, IndexError):
                continue
                
    elapsed = time.time() - start_time
    print(f"✓ {total_processed:,} gerçek araç GPS kaydı okundu. Toplam {len(geo_stats)} benzersiz arter/geohash profillendirildi. ({elapsed:.1f}s)")
    
    # Save hourly samples in batches
    print("   💾 Saatlik akış tablosuna örneklem aktarılıyor...")
    cur.executemany("""
    INSERT INTO arac_gps_saatlik_akis (
        il, tarih_saat, geohash, lat, lon, arac_sayisi, ortalama_hiz, min_hiz, max_hiz, yogunluk_seviyesi, guncellenme_tarihi
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, hourly_sample)
    
    # Save corridor profile
    print("   💾 Koridor özet profilleri SQLite ambarına yazılıyor...")
    profile_rows = []
    for gh, st in geo_stats.items():
        obs = st["obs"]
        if obs == 0:
            continue
        days_cnt = max(1, len(st["days"]))
        daily_v = round(st["sum_v"] / days_cnt, 1)
        avg_spd = round(st["sum_spd"] / obs, 1)
        min_spd = round(st["min_spd"], 1) if st["min_spd"] < 999 else 0.0
        cong_pct = round((st["congested"] / obs) * 100.0, 1)
        
        if daily_v > 15000 or (st["max_v"] > 1500 and cong_pct > 25):
            kat = "Ana Arter / Çok Yoğun Ticari Görünürlük"
        elif daily_v > 5000 or st["max_v"] > 500:
            kat = "İkincil Arter / Orta Yoğunluk"
        else:
            kat = "Bağlantı Yolu / Serbest Akış"
            
        profile_rows.append((
            gh, "İstanbul", st["lat"], st["lon"], obs, st["sum_v"],
            daily_v, st["max_v"], avg_spd, min_spd, st["congested"],
            cong_pct, kat, now_iso
        ))
        
    cur.executemany("""
    INSERT INTO arac_gps_koridor_profili (
        geohash, il, lat, lon, toplam_gozlem_saati, toplam_arac_hacmi,
        gunluk_ortalama_arac, zirve_saat_arac, ortalama_akinti_hizi,
        min_kaydedilen_hiz, sıkısik_saat_sayisi, sıkısiklik_orani_yuzde,
        trafik_kategorisi, guncellenme_tarihi
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(geohash) DO UPDATE SET
        toplam_gozlem_saati=excluded.toplam_gozlem_saati,
        toplam_arac_hacmi=excluded.toplam_arac_hacmi,
        gunluk_ortalama_arac=excluded.gunluk_ortalama_arac,
        zirve_saat_arac=excluded.zirve_saat_arac,
        ortalama_akinti_hizi=excluded.ortalama_akinti_hizi,
        min_kaydedilen_hiz=excluded.min_kaydedilen_hiz,
        sıkısik_saat_sayisi=excluded.sıkısik_saat_sayisi,
        sıkısiklik_orani_yuzde=excluded.sıkısiklik_orani_yuzde,
        trafik_kategorisi=excluded.trafik_kategorisi,
        guncellenme_tarihi=excluded.guncellenme_tarihi;
    """, profile_rows)
    
    conn.commit()
    
    # Summary
    p_cnt = cur.execute("SELECT COUNT(*) FROM arac_gps_koridor_profili").fetchone()[0]
    h_cnt = cur.execute("SELECT COUNT(*) FROM arac_gps_saatlik_akis").fetchone()[0]
    print(f"✅ [ARAÇ GPS / TRAFİK] Ambarlama tamamlandı:")
    print(f"   📊 arac_gps_koridor_profili: {p_cnt:,} arter/geohash profili")
    print(f"   📊 arac_gps_saatlik_akis: {h_cnt:,} saatlik akış kaydı")
    
    conn.close()

if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    stream_and_ingest_traffic(max_records=limit)
