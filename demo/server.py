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

        # 2. HIZLI TEST ÖRNEKLERİ LİSTESİ API'Sİ
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
