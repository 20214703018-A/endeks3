#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Canlı İmar & Parsel API Sunucusu
-------------------------------------------------
Statik web arayüzünü (HTML/CSS/JS) sunarken aynı zamanda
tarayıcının CORS kısıtlamalarına takılmadan TKGM MEGSİS ve
E-Plan imar durumunu canlı sorgulayabilmesi için yüksek hızlı
bir API proxy köprüsü sağlar.
"""

import os
import sys
import json
import time
import urllib.parse
import http.server
import socketserver

# Collector dizinini ekle
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
COLLECTOR_DIR = os.path.join(PARENT_DIR, "collector")

if COLLECTOR_DIR not in sys.path:
    sys.path.insert(0, COLLECTOR_DIR)

from parsel_imar_ve_degisiklik_toplayici import ParselImarToplayici

PORT = 8088
toplayici = ParselImarToplayici()

class GeopropApiHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=CURRENT_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. CANLI PARSEL & İMAR SORGULAMA API UÇ NOKTASI
        if path == "/api/parsel-sorgu":
            start_time = time.time()
            lat = float(query["lat"][0]) if "lat" in query and query["lat"][0] else None
            lon = float(query["lon"][0]) if "lon" in query and query["lon"][0] else None
            mahalle_id = int(query["mahalleId"][0]) if "mahalleId" in query and query["mahalleId"][0] else None
            ada = str(query["ada"][0]) if "ada" in query and query["ada"][0] else None
            parsel = str(query["parsel"][0]) if "parsel" in query and query["parsel"][0] else None
            il = str(query.get("il", [""])[0])
            ilce = str(query.get("ilce", [""])[0])
            mahalle = str(query.get("mahalle", [""])[0])

            try:
                # Toplayıcı motorumuz üzerinden canlı sorgu
                result = toplayici.process_parsel(
                    mahalle_id=mahalle_id,
                    ada=ada,
                    parsel=parsel,
                    il=il,
                    ilce=ilce,
                    mahalle=mahalle,
                    lat=lat,
                    lon=lon
                )

                elapsed_ms = round((time.time() - start_time) * 1000)

                if result:
                    response_data = {
                        "status": "success",
                        "sure_ms": elapsed_ms,
                        "kaynak": "TKGM MEGSİS & Canlı İmar Motoru",
                        "data": result
                    }
                    status_code = 200
                else:
                    response_data = {
                        "status": "not_found",
                        "sure_ms": elapsed_ms,
                        "mesaj": "Belirtilen koordinat veya ada/parselde kadastral parsel kaydı bulunamadı (yol, meydan veya tescil harici alan olabilir)."
                    }
                    status_code = 404

            except Exception as e:
                elapsed_ms = round((time.time() - start_time) * 1000)
                response_data = {
                    "status": "error",
                    "sure_ms": elapsed_ms,
                    "hata": str(e)
                }
                status_code = 500

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode("utf-8"))
            return

        # 2. 2026 VE SONRASI TİCARİ İSTİHBARAT & E-TİCARET KARNESİ API'Sİ
        elif path == "/api/bolge-istihbarat":
            il = str(query.get("il", [""])[0]).strip()
            ilce = str(query.get("ilce", [""])[0]).strip()
            mahalle = str(query.get("mahalle", [""])[0]).strip()
            lat = float(query["lat"][0]) if "lat" in query and query["lat"][0] else None
            lon = float(query["lon"][0]) if "lon" in query and query["lon"][0] else None

            istihbarat_db = os.path.join(COLLECTOR_DIR, "data", "turkiye_makro_ve_mikro_istihbarat.sqlite")
            lojistik_db = os.path.join(COLLECTOR_DIR, "data", "eticaret_ve_lojistik.sqlite")

            res = {
                "status": "success",
                "yil": 2026,
                "donem": "2026-Q3 ve Sonrası (2026-2027 Projeksiyonu)",
                "il": il,
                "ilce": ilce,
                "mahalle": mahalle,
                "etbis": None,
                "sege": None,
                "ciro_potansiyeli": None,
                "lojistik": {"toplam_kargomat": 0, "toplam_sube": 0, "en_yakin_kargomat": None, "en_yakin_sube": None},
                "tutun_saglik": None,
                "mahalle_harcama": None
            }

            import math
            def haversine_m(lat1, lon1, lat2, lon2):
                if not (lat1 and lon1 and lat2 and lon2): return None
                R = 6371000
                phi1, phi2 = math.radians(lat1), math.radians(lat2)
                dphi = math.radians(lat2 - lat1)
                dlam = math.radians(lon2 - lon1)
                a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
                return round(2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

            # A) Makro ve Mikro İstihbarat DB
            if os.path.exists(istihbarat_db):
                import sqlite3
                conn_m = sqlite3.connect(istihbarat_db)
                conn_m.row_factory = sqlite3.Row
                cm = conn_m.cursor()

                # 1. ETBİS 2026 E-Ticaret
                if il:
                    cm.execute("SELECT * FROM etbis_81_il_e_ticaret WHERE il_adi LIKE ? LIMIT 1", (f"%{il}%",))
                    row_et = cm.fetchone()
                    if row_et: res["etbis"] = dict(row_et)

                # 2. SEGE 2026 İlçe
                if il and ilce:
                    cm.execute("SELECT * FROM sege_973_ilce_gelismislik WHERE il_adi LIKE ? AND ilce_adi LIKE ? LIMIT 1", (f"%{il}%", f"%{ilce}%"))
                    row_sege = cm.fetchone()
                    if row_sege: res["sege"] = dict(row_sege)

                # 3. Ciro & Lokasyon Potansiyeli Skoru (2026-2027)
                if il and ilce:
                    cm.execute("SELECT * FROM ciro_ve_ticari_potansiyel_endeksi WHERE il LIKE ? AND ilce LIKE ? LIMIT 1", (f"%{il}%", f"%{ilce}%"))
                    row_ciro = cm.fetchone()
                    if row_ciro: res["ciro_potansiyeli"] = dict(row_ciro)

                # 4. TÜİK Tütün & Sigara 2026
                cm.execute("SELECT * FROM tuik_tutun_ve_sigara_istatistikleri WHERE bolge_adi LIKE ? OR bolge_adi LIKE ? LIMIT 1", (f"%{il}%", "%Türkiye Geneli%"))
                row_tutun = cm.fetchone()
                if row_tutun: res["tutun_saglik"] = dict(row_tutun)

                conn_m.close()

            # B) E-Ticaret ve Lojistik DB
            if os.path.exists(lojistik_db):
                import sqlite3
                conn_l = sqlite3.connect(lojistik_db)
                conn_l.row_factory = sqlite3.Row
                cl = conn_l.cursor()

                # Lojistik & Kargomatlar
                if il:
                    cl.execute("SELECT * FROM kargo_ve_teslimat_noktalari WHERE il_ad LIKE ?", (f"%{il}%",))
                    pts = cl.fetchall()
                    kargomatlar = [dict(p) for p in pts if p["tip"] == "PTT_KARGOMAT"]
                    subeler = [dict(p) for p in pts if p["tip"] == "PTT_SUBE"]
                    res["lojistik"]["toplam_kargomat"] = len(kargomatlar)
                    res["lojistik"]["toplam_sube"] = len(subeler)

                    if lat and lon:
                        for k in kargomatlar:
                            if k.get("lat") and k.get("lon"):
                                k["mesafe_metre"] = haversine_m(lat, lon, k["lat"], k["lon"])
                        kargomatlar_dist = [k for k in kargomatlar if k.get("mesafe_metre") is not None]
                        if kargomatlar_dist:
                            kargomatlar_dist.sort(key=lambda x: x["mesafe_metre"])
                            res["lojistik"]["en_yakin_kargomat"] = kargomatlar_dist[0]

                        for s in subeler:
                            if s.get("lat") and s.get("lon"):
                                s["mesafe_metre"] = haversine_m(lat, lon, s["lat"], s["lon"])
                        subeler_dist = [s for s in subeler if s.get("mesafe_metre") is not None]
                        if subeler_dist:
                            subeler_dist.sort(key=lambda x: x["mesafe_metre"])
                            res["lojistik"]["en_yakin_sube"] = subeler_dist[0]

                # Mahalle Harcama Kalemleri
                if mahalle:
                    cl.execute("SELECT * FROM eticaret_ve_harcama_kalemleri WHERE seviye='mahalle' AND mahalle LIKE ? LIMIT 1", (f"%{mahalle}%",))
                    row_m = cl.fetchone()
                    if row_m: res["mahalle_harcama"] = dict(row_m)

                conn_l.close()

            # C) Arabam.com Vasıta ve Refah Endeksi DB
            vasita_db = os.path.join(COLLECTOR_DIR, "data", "arabam_vasita_piyasasi.sqlite")
            if os.path.exists(vasita_db):
                import sqlite3
                conn_v = sqlite3.connect(vasita_db)
                conn_v.row_factory = sqlite3.Row
                cv = conn_v.cursor()
                if il and ilce:
                    cv.execute("SELECT * FROM ilce_arac_refah_endeksi WHERE il LIKE ? AND ilce LIKE ? LIMIT 1", (f"%{il}%", f"%{ilce}%"))
                    row_v = cv.fetchone()
                    if row_v: res["arac_refah"] = dict(row_v)
                if not res.get("arac_refah") and il:
                    cv.execute("SELECT * FROM ilce_arac_refah_endeksi WHERE il LIKE ? LIMIT 1", (f"%{il}%",))
                    row_v = cv.fetchone()
                    if row_v: res["arac_refah"] = dict(row_v)
                conn_v.close()

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        # 3. HIZLI TEST ÖRNEKLERİ LİSTESİ API'Sİ
        elif path == "/api/hizli-ornekler":
            ornekler = [
                {
                    "ad": "🏛️ Ankara Çankaya Köşkü",
                    "aciklama": "Cumhurbaşkanlığı Çankaya Köşkü Yerleşkesi (180.657 m²)",
                    "lat": 39.8892,
                    "lon": 32.8633,
                    "ada": "5964",
                    "parsel": "6",
                    "il": "Ankara",
                    "ilce": "Çankaya",
                    "mahalle": "Çankaya"
                },
                {
                    "ad": "🏢 Ankara Aziziye Apartman",
                    "aciklama": "İmar fonksiyonu, KAKS 1.5, TAKS 0.4 apartman parseli",
                    "lat": 39.8891,
                    "lon": 32.8631,
                    "ada": "6103",
                    "parsel": "22",
                    "il": "Ankara",
                    "ilce": "Çankaya",
                    "mahalle": "Aziziye"
                },
                {
                    "ad": "🏖️ Yalova Altınova Konut Arsası",
                    "aciklama": "Kentsel konut gelişim alanı ve kadastro sınırı",
                    "lat": 40.6975,
                    "lon": 29.5108,
                    "ada": "151",
                    "parsel": "1",
                    "il": "Yalova",
                    "ilce": "Altınova",
                    "mahalle": "Cumhuriyet"
                },
                {
                    "ad": "🌲 Yalova Armutlu Kapaklı Köyü",
                    "aciklama": "Köy yerleşik alanı ve zeytinlik kırsal parsel",
                    "lat": 40.4638,
                    "lon": 28.9683,
                    "ada": "101",
                    "parsel": "1",
                    "il": "Yalova",
                    "ilce": "Armutlu",
                    "mahalle": "Kapaklı"
                }
            ]
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(ornekler, ensure_ascii=False).encode("utf-8"))
            return

        # 3. STATİK DOSYALARI SUN (HTML, CSS, JS, GEOJSON)
        super().do_GET()

def main():
    port = PORT
    while port < 8110:
        try:
            with socketserver.TCPServer(("", port), GeopropApiHandler) as httpd:
                url = f"http://localhost:{port}/imar_canli_test.html"
                print("=" * 70)
                print(f"  ⚡ GEOPROP AI CANLI İMAR & PARSEL BORU HATTI SUNUCUSU")
                print(f"  📍 Canlı İmar Test Sayfası: {url}")
                print(f"  📍 Ana Analiz Dashboard:    http://localhost:{port}/index.html")
                print(f"  📂 Kök Dizin:               {CURRENT_DIR}")
                print("=" * 70)
                print("  Çıkış için Ctrl+C tuşlarına basın.\n")
                httpd.serve_forever()
                break
        except OSError:
            port += 1

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSunucu kapatıldı.")
        sys.exit(0)
