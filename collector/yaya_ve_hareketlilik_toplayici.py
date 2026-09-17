#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP Yaya GPS, Yayalaştırılmış Akslar ve İstasyon Tahliye Hacmi Toplayıcı
============================================================================
Şehirlerin ticari can damarlarını oluşturan gerçek yaya hareketliliği verilerini toplar:
1. İBB Yayalaştırılmış Ticari Sokaklar (2.066 adet resmî sokak geometrisi ve yayalaştırma türü)
2. İzmir Büyükşehir Belediyesi Yaya ve Bisiklet Sayım Sensör İstasyonları (Mavişehir, Alsancak, Pasaport, Konak vb.)
3. Batı Büyükşehirleri (Antalya, İzmir, Bursa, Muğla) Ticari Yaya Koridorları ve Kordon Hatları (OpenStreetMap Overpass)
4. İBB Metro ve Raylı Sistem İstasyon Bazlı Günlük Yaya Tahliye Akışı (Çevre ticari sokaklara boşalan yaya hacmi)

Hedef Ambar: warehouse/product/ulasim_ve_hareketlilik_gps.sqlite
Kural Uyumu: %100 ampirik/resmî açık veri, sentetik yok, lat/lon + ISO 8601.
"""

import sys
import os
import time
import json
import sqlite3
import datetime
import urllib.request
import urllib.parse
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "warehouse", "product", "ulasim_ve_hareketlilik_gps.sqlite")

URL_IBB_YAYALASTIRMA = "https://data.ibb.gov.tr/tr/dataset/abaf2b08-5176-4217-af0c-81923b86f937/resource/19a399e4-8d96-4b23-8a32-db202cbcd3d1/download/yayalastirma_verisi.geojson"
URL_IZMIR_STATIONS = "https://acikveri.bizizmir.com/dataset/f96adb92-1b98-40c6-92aa-5207e7b519bb/resource/283c0f13-7fad-4637-9628-9c1b2b786584/download/bisikletli-yaya-saym-istasyonlar.csv"
URL_IZMIR_COUNTS = "https://acikveri.bizizmir.com/dataset/f96adb92-1b98-40c6-92aa-5207e7b519bb/resource/60459c57-42d5-4c48-9c4d-80e0b1ee9796/download/2020-sayim-verileri.csv"
URL_IBB_RAIL_2024 = "https://data.ibb.gov.tr/dataset/ae3b2e4b-073a-48d0-8ef3-f28f19bcb19c/resource/6028373f-6bcf-45a9-95a3-f3f741b4b55e/download/2024-yl-rayl-sistemler-istasyon-bazl-yolcu-ve-yolculuk-saylar.csv"

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 1. Yayalaştırılmış Ticari Yollar ve Geometriler
    cur.execute("""
    CREATE TABLE IF NOT EXISTS yayalastirilmis_ticari_yollar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        ilce TEXT,
        yol_adi TEXT NOT NULL,
        durum TEXT NOT NULL,
        yol_turu TEXT,
        uzunluk_metre REAL,
        geometri_geojson TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yaya_yol_lat_lon ON yayalastirilmis_ticari_yollar(lat, lon);")
    
    # 2. Yaya Sayım Sensör İstasyonları ve Günlük Yaya Debileri
    cur.execute("""
    CREATE TABLE IF NOT EXISTS yaya_sayim_sensor_istasyonlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        istasyon_adi TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        tarih TEXT NOT NULL,
        gunluk_yaya_giris INTEGER,
        gunluk_yaya_cikis INTEGER,
        gunluk_toplam_yaya INTEGER NOT NULL,
        gunluk_bisiklet_toplam INTEGER,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(istasyon_adi, tarih)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sensor_tarih ON yaya_sayim_sensor_istasyonlari(istasyon_adi, tarih);")
    
    # 3. İstasyon Bazlı Günlük Yaya Tahliye Hacmi (Metro, Tramvay, Vapur)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS istasyon_yaya_tahliye_hacmi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        ilce TEXT NOT NULL,
        istasyon_adi TEXT NOT NULL,
        hat_adi TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        tarih TEXT NOT NULL,
        gunluk_yolcu_giris_cikis INTEGER NOT NULL,
        yolcu_sayisi INTEGER NOT NULL,
        kaynak TEXT NOT NULL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(istasyon_adi, hat_adi, tarih)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_istasyon_tahliye_lat_lon ON istasyon_yaya_tahliye_hacmi(lat, lon);")
    
    conn.commit()
    conn.close()

def ingest_ibb_pedestrian_geojson():
    print("🚶 [İBB YAYALAŞTIRMA] 2.066 yayalaştırılmış cadde/sokak indiriliyor...")
    req = urllib.request.Request(URL_IBB_YAYALASTIRMA, headers={"User-Agent": "Mozilla/5.0"})
    now_iso = datetime.datetime.now().isoformat()
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            features = data.get("features", [])
            
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        rows = []
        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            yol_adi = str(props.get("YOL_ISMI") or "").strip()
            durum = str(props.get("MEVCUT_DURUM") or "").strip()
            yol_turu = str(props.get("YOL_TURU") or "").strip()
            
            coords = geom.get("coordinates", [])
            if not coords:
                continue
                
            # Compute center lat/lon
            if geom.get("type") == "LineString":
                all_lons = [c[0] for c in coords if len(c) >= 2]
                all_lats = [c[1] for c in coords if len(c) >= 2]
            elif geom.get("type") == "MultiLineString":
                all_lons = [c[0] for part in coords for c in part if len(c) >= 2]
                all_lats = [c[1] for part in coords for c in part if len(c) >= 2]
            else:
                continue
                
            if not all_lats or not all_lons:
                continue
                
            center_lat = sum(all_lats) / len(all_lats)
            center_lon = sum(all_lons) / len(all_lons)
            geom_json = json.dumps(geom, ensure_ascii=False)
            
            rows.append((
                "İstanbul", None, yol_adi, durum, yol_turu, None,
                geom_json, center_lat, center_lon, "İBB Açık Veri Portalı", now_iso
            ))
            
        cur.executemany("""
        INSERT INTO yayalastirilmis_ticari_yollar (
            il, ilce, yol_adi, durum, yol_turu, uzunluk_metre, geometri_geojson,
            lat, lon, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        
        conn.commit()
        cnt = cur.execute("SELECT COUNT(*) FROM yayalastirilmis_ticari_yollar WHERE il='İstanbul'").fetchone()[0]
        print(f"   ✓ İBB yayalaştırma verisi ambarlandı: {cnt:,} cadde/sokak geometrisi.")
        conn.close()
    except Exception as e:
        print(f"   ✗ İBB yayalaştırma hatası: {e}")

def ingest_izmir_pedestrian_sensors():
    print("🚶 [İZMİR YAYA SENSÖRLERİ] Bizİzmir sensör istasyonları ve debileri indiriliyor...")
    now_iso = datetime.datetime.now().isoformat()
    
    # 1. Stations coordinates
    st_req = urllib.request.Request(URL_IZMIR_STATIONS, headers={"User-Agent": "Mozilla/5.0"})
    st_coords = {}
    try:
        with urllib.request.urlopen(st_req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            lines = content.strip().splitlines()
            for line in lines[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    name = parts[0]
                    lat = float(parts[1])
                    lon = float(parts[2])
                    st_coords[name.lower()] = (name, lat, lon)
    except Exception as e:
        print(f"   ✗ İzmir istasyon koordinat hatası: {e}")
        return

    # 2. Daily Counts
    cnt_req = urllib.request.Request(URL_IZMIR_COUNTS, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(cnt_req, timeout=20) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            lines = content.strip().splitlines()
            if not lines:
                return
            header = [h.strip() for h in lines[0].split(",")]
            
            # Map station name to indices in header
            # E.g. 'Alsancak_ Yaya_IN', 'Alsancak_Yaya_OUT', 'Alsancak_Yaya_TOTAL'
            station_indices = {}
            for k_name, (orig_name, lat, lon) in st_coords.items():
                in_idx = -1
                out_idx = -1
                tot_idx = -1
                bike_idx = -1
                for idx, col in enumerate(header):
                    col_clean = col.lower().replace(" ", "").replace("_", "")
                    target = orig_name.lower().replace(" ", "")
                    if target in col_clean:
                        if "yaya" in col_clean and "total" in col_clean:
                            tot_idx = idx
                        elif "yaya" in col_clean and "in" in col_clean:
                            in_idx = idx
                        elif "yaya" in col_clean and "out" in col_clean:
                            out_idx = idx
                        elif "bisiklet" in col_clean and "total" in col_clean:
                            bike_idx = idx
                if tot_idx != -1:
                    station_indices[orig_name] = (lat, lon, in_idx, out_idx, tot_idx, bike_idx)
                    
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            
            rows = []
            for line in lines[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < len(header):
                    continue
                tarih_raw = parts[0]
                # Format: 01-01-2020 00:00:00 -> 2020-01-01
                try:
                    dt_part = tarih_raw.split()[0]
                    d_p = dt_part.split("-")
                    tarih_iso = f"{d_p[2]}-{d_p[1]}-{d_p[0]}"
                except Exception:
                    tarih_iso = tarih_raw
                    
                for st_name, (lat, lon, in_i, out_i, tot_i, bike_i) in station_indices.items():
                    try:
                        tot_yaya = int(parts[tot_i])
                        in_yaya = int(parts[in_i]) if in_i != -1 else None
                        out_yaya = int(parts[out_i]) if out_i != -1 else None
                        bike_tot = int(parts[bike_i]) if bike_i != -1 else None
                        
                        rows.append((
                            "İzmir", st_name, lat, lon, tarih_iso,
                            in_yaya, out_yaya, tot_yaya, bike_tot,
                            "İzmir Açık Veri Portalı", now_iso
                        ))
                    except (ValueError, IndexError):
                        continue
                        
            cur.executemany("""
            INSERT INTO yaya_sayim_sensor_istasyonlari (
                il, istasyon_adi, lat, lon, tarih, gunluk_yaya_giris,
                gunluk_yaya_cikis, gunluk_toplam_yaya, gunluk_bisiklet_toplam,
                kaynak, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(istasyon_adi, tarih) DO UPDATE SET
                gunluk_toplam_yaya=excluded.gunluk_toplam_yaya,
                gunluk_yaya_giris=excluded.gunluk_yaya_giris,
                gunluk_yaya_cikis=excluded.gunluk_yaya_cikis,
                gunluk_bisiklet_toplam=excluded.gunluk_bisiklet_toplam,
                guncellenme_tarihi=excluded.guncellenme_tarihi;
            """, rows)
            
            conn.commit()
            cnt = cur.execute("SELECT COUNT(*) FROM yaya_sayim_sensor_istasyonlari WHERE il='İzmir'").fetchone()[0]
            print(f"   ✓ İzmir yaya sayım sensör kayıtları ambarlandı: {cnt:,} günlük ölçüm.")
            conn.close()
    except Exception as e:
        print(f"   ✗ İzmir sayım verisi hatası: {e}")

OVERPASS_MIRRORS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter"
]

BUYUKSEHIRLER_30 = [
    "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya",
    "Adana", "Konya", "Gaziantep", "Şanlıurfa", "Kocaeli",
    "Mersin", "Diyarbakır", "Hatay", "Manisa", "Kayseri",
    "Samsun", "Balıkesir", "Kahramanmaraş", "Van", "Aydın",
    "Denizli", "Sakarya", "Tekirdağ", "Muğla", "Eskişehir",
    "Mardin", "Malatya", "Trabzon", "Erzurum", "Ordu"
]

def query_osm_pedestrian_corridors(city_name, force=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cnt_existing = cur.execute(f"SELECT COUNT(*) FROM yayalastirilmis_ticari_yollar WHERE il='{city_name}'").fetchone()[0]
    conn.close()
    if cnt_existing > 100 and not force:
        print(f"🚶 [OSM YAYA KORİDORLARI] {city_name} zaten ambarda mevcut ({cnt_existing:,} kayıt), atlanıyor.", flush=True)
        return cnt_existing

    print(f"🚶 [OSM YAYA KORİDORLARI] {city_name} ticari yaya aksları ve kordonları çekiliyor...", flush=True)
    query = f"""
    [out:json][timeout:25];
    area["name"="{city_name}"]->.searchArea;
    (
      way["highway"="pedestrian"](area.searchArea);
      way["highway"="living_street"](area.searchArea);
      way["highway"="footway"]["name"](area.searchArea);
    );
    out center tags;
    """
    data = f"data={urllib.parse.quote(query)}".encode("utf-8")
    now_iso = datetime.datetime.now().isoformat()
    
    for mirror_url in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(mirror_url, data=data, headers={"User-Agent": "GEOPROP-PedestrianEngine/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                elements = res_data.get("elements", [])
                
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            
            rows = []
            for el in elements:
                tags = el.get("tags", {})
                name = tags.get("name")
                if not name:
                    continue
                hw = tags.get("highway", "pedestrian")
                center = el.get("center", {})
                lat = center.get("lat")
                lon = center.get("lon")
                if not lat or not lon:
                    continue
                    
                durum = "YAYALAŞTIRILMIŞ" if hw in ("pedestrian", "footway") else "YAYA ÖNCELİKLİ (LIVING STREET)"
                yol_turu = "Cadde" if "caddesi" in name.lower() or "cd" in name.lower() else ("Sokak" if "sokak" in name.lower() or "sk" in name.lower() else "Gezinti Yolu / Kordon")
                geom_json = json.dumps({"type": "Point", "coordinates": [lon, lat]}, ensure_ascii=False)
                
                rows.append((
                    city_name, None, name, durum, yol_turu, None,
                    geom_json, lat, lon, "OpenStreetMap Overpass", now_iso
                ))
                
            cur.executemany("""
            INSERT INTO yayalastirilmis_ticari_yollar (
                il, ilce, yol_adi, durum, yol_turu, uzunluk_metre, geometri_geojson,
                lat, lon, kaynak, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            
            conn.commit()
            cnt = cur.execute(f"SELECT COUNT(*) FROM yayalastirilmis_ticari_yollar WHERE il='{city_name}'").fetchone()[0]
            print(f"   ✓ {city_name} OSM ticari yaya yolları ambarlandı: {cnt:,} cadde/aks.", flush=True)
            conn.close()
            return cnt
        except Exception as e:
            time.sleep(1.5)
            continue
    print(f"   ✗ {city_name} Overpass tüm aynalarda zaman aşımına uğradı.", flush=True)
    return 0

def ingest_ibb_rail_passengers_2024(sample_limit=20000):
    print("🚶 [METRO & İSTASYON TAHLİYE] İBB 2024 istasyon yaya tahliye hacimleri indiriliyor...")
    req = urllib.request.Request(URL_IBB_RAIL_2024, headers={"User-Agent": "Mozilla/5.0"})
    now_iso = datetime.datetime.now().isoformat()
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # Stream first N lines (each line is a terminal/day record)
        with urllib.request.urlopen(req, timeout=45) as resp:
            header_line = resp.readline().decode("utf-8", errors="ignore").strip()
            # "transaction_year","transaction_month","transaction_day","line","station_name","station_number","terminal_number","town","longitude","latitude","passage_cnt","passanger_cnt"
            
            station_day_agg = defaultdict(lambda: {
                "line": "", "town": "", "lat": 0.0, "lon": 0.0, "passages": 0, "passengers": 0
            })
            
            count = 0
            for line in resp:
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    continue
                parts = [p.strip('"') for p in line_str.split(",")]
                if len(parts) < 12:
                    continue
                    
                year = parts[0]
                month = parts[1].zfill(2)
                day = parts[2].zfill(2)
                line_name = parts[3]
                st_name = parts[4]
                town = parts[7]
                try:
                    lon = float(parts[8])
                    lat = float(parts[9])
                    passage_cnt = int(parts[10])
                    passenger_cnt = int(parts[11])
                except (ValueError, IndexError):
                    continue
                    
                tarih = f"{year}-{month}-{day}"
                key = (st_name, line_name, tarih)
                agg = station_day_agg[key]
                agg["line"] = line_name
                agg["town"] = town
                agg["lat"] = lat
                agg["lon"] = lon
                agg["passages"] += passage_cnt
                agg["passengers"] += passenger_cnt
                
                count += 1
                if sample_limit and count >= sample_limit:
                    break
                    
        rows = []
        for (st_name, line_name, tarih), agg in station_day_agg.items():
            rows.append((
                "İstanbul", agg["town"], st_name, agg["line"], agg["lat"], agg["lon"],
                tarih, agg["passages"], agg["passengers"], "İBB Raylı Sistemler 2024", now_iso
            ))
            
        cur.executemany("""
        INSERT INTO istasyon_yaya_tahliye_hacmi (
            il, ilce, istasyon_adi, hat_adi, lat, lon, tarih,
            gunluk_yolcu_giris_cikis, yolcu_sayisi, kaynak, guncellenme_tarihi
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(istasyon_adi, hat_adi, tarih) DO UPDATE SET
            gunluk_yolcu_giris_cikis=excluded.gunluk_yolcu_giris_cikis,
            yolcu_sayisi=excluded.yolcu_sayisi,
            guncellenme_tarihi=excluded.guncellenme_tarihi;
        """, rows)
        
        conn.commit()
        cnt = cur.execute("SELECT COUNT(*) FROM istasyon_yaya_tahliye_hacmi").fetchone()[0]
        print(f"   ✓ İBB istasyon yaya tahliye kayıtları ambarlandı: {cnt:,} günlük istasyon akışı.")
        conn.close()
    except Exception as e:
        print(f"   ✗ İBB raylı sistem tahliye hatası: {e}")

def run_all(cities=BUYUKSEHIRLER_30):
    init_db(DB_PATH)
    ingest_ibb_pedestrian_geojson()
    ingest_izmir_pedestrian_sensors()
    print(f"\n🚶 30 Büyükşehir için yaya aksları ve kordon madenciliği başlatılıyor...")
    for city in cities:
        query_osm_pedestrian_corridors(city)
        time.sleep(1.2)
    ingest_ibb_rail_passengers_2024(sample_limit=25000)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    print("\n📊 === ULASIM VE HAREKETLİLİK GPS AMBAR ÖZETİ ===")
    for table_name in ["yayalastirilmis_ticari_yollar", "yaya_sayim_sensor_istasyonlari", "istasyon_yaya_tahliye_hacmi"]:
        cnt = cur.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"   - {table_name}: {cnt:,} satır ambarlandı.")
    conn.close()

if __name__ == "__main__":
    run_all()
