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
import math
import urllib.parse
import urllib.request
import urllib.error
import http.server

# Collector dizinini ekle
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
COLLECTOR_DIR = os.path.join(PARENT_DIR, "collector")

if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from geoprop.land_analysis import LandAnalysisEngine, LandAnalysisError
from geoprop.live_parcel import LiveParcelGateway, ParcelQueryError
from collector.parsel_imar_ve_degisiklik_toplayici import ParselImarToplayici

PORT = 8088
# Sunucuyu yalnızca görüntülemek veritabanına dokunmamalı. Sorgu motoru da
# salt-okunur API kullanımı için depolama başlatmadan hazırlanır.
toplayici = ParselImarToplayici(init_storage=False)
parsel_gateway = LiveParcelGateway(toplayici.process_parsel)
PRODUCT_LAND_DATABASE = os.path.join(PARENT_DIR, "warehouse", "product", "arsa_emsalleri.sqlite")
LEGACY_LISTING_DATABASE = os.path.join(COLLECTOR_DIR, "data", "ilanlar.db")
LAND_DATABASE = PRODUCT_LAND_DATABASE if os.path.exists(PRODUCT_LAND_DATABASE) else LEGACY_LISTING_DATABASE
arsa_motoru = LandAnalysisEngine(LAND_DATABASE)
poi_cache = {}


def haversine_m(lat1, lon1, lat2, lon2):
    radius = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def classify_poi(tags):
    amenity = tags.get("amenity")
    if amenity in {"hospital", "clinic", "doctors"}:
        return "health"
    if amenity in {"school", "college", "university", "kindergarten"}:
        return "education"
    if tags.get("shop") == "mall":
        return "mall"
    if tags.get("leisure") == "park":
        return "park"
    if (
        tags.get("highway") == "bus_stop"
        or tags.get("public_transport") in {"station", "platform", "stop_position"}
        or tags.get("railway") in {"station", "halt", "tram_stop", "subway_entrance"}
    ):
        return "transit"
    return None


def fetch_nearby_pois(lat, lon, radius):
    cache_key = (round(lat, 4), round(lon, 4), radius)
    cached = poi_cache.get(cache_key)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]

    overpass_query = f'''[out:json][timeout:18];(
      nwr(around:{radius},{lat},{lon})["amenity"~"hospital|clinic|doctors|school|college|university|kindergarten"];
      nwr(around:{radius},{lat},{lon})["shop"="mall"];
      nwr(around:{radius},{lat},{lon})["leisure"="park"];
      nwr(around:{radius},{lat},{lon})["public_transport"~"station|platform|stop_position"];
      nwr(around:{radius},{lat},{lon})["highway"="bus_stop"];
      nwr(around:{radius},{lat},{lon})["railway"~"station|halt|tram_stop|subway_entrance"];
    );out center tags;'''
    endpoint = "https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": overpass_query})
    request = urllib.request.Request(
        endpoint,
        headers={"User-Agent": "GEOPROP-AI-Parsel-Demo/0.1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=28) as response:
        payload = json.load(response)

    candidates = []
    seen = set()
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        category = classify_poi(tags)
        center = element.get("center") or {}
        poi_lat = element.get("lat", center.get("lat"))
        poi_lon = element.get("lon", center.get("lon"))
        if not category or poi_lat is None or poi_lon is None:
            continue
        poi_name = tags.get("name") or tags.get("name:tr")
        if category == "health" and poi_name and any(word in poi_name.casefold() for word in ("veteriner", "hayvan", "pet")):
            continue
        dedupe_key = (category, round(float(poi_lat), 5), round(float(poi_lon), 5))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append({
            "category": category,
            "lat": float(poi_lat),
            "lon": float(poi_lon),
            "name": poi_name,
            "distance_m": haversine_m(lat, lon, float(poi_lat), float(poi_lon)),
            "source": "OpenStreetMap",
        })

    limits = {"health": 2, "education": 3, "mall": 2, "transit": 1, "park": 2}
    selected = []
    for category, limit in limits.items():
        group = sorted((item for item in candidates if item["category"] == category), key=lambda item: item["distance_m"])
        selected.extend(group[:limit])
    selected.sort(key=lambda item: item["distance_m"])
    poi_cache[cache_key] = (time.time(), selected)
    return selected

class GeopropApiHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=CURRENT_DIR, **kwargs)

    def send_json(self, payload, status_code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self, max_bytes=64 * 1024):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Geçersiz Content-Length") from exc
        if length <= 0 or length > max_bytes:
            raise ValueError("İstek gövdesi boş veya izin verilen boyutu aşıyor.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Geçerli bir JSON gövdesi gereklidir.") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON kökü nesne olmalıdır.")
        return payload

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/v1/arsa/analiz":
            self.send_json({"status": "error", "message": "Uç nokta bulunamadı."}, 404)
            return

        started = time.time()
        try:
            request_data = self.read_json_body()
            live_result = None
            live_warning = None
            if request_data.get("canli_parsel_sorgula", True):
                try:
                    live_result = parsel_gateway.query(request_data)
                except ParcelQueryError as exc:
                    live_warning = str(exc)
                except Exception:
                    live_warning = "Canlı parsel kaynağına erişilemedi; analiz girilen bilgilerle sürdürüldü."

            analysis = arsa_motoru.analyze(request_data, live_result)
            if live_warning:
                analysis["warnings"].append(live_warning)
            analysis["elapsed_ms"] = round((time.time() - started) * 1000)
            self.send_json(analysis)
        except (ValueError, LandAnalysisError, ParcelQueryError) as exc:
            self.send_json({"status": "validation_error", "message": str(exc)}, 400)
        except Exception as exc:
            self.send_json(
                {
                    "status": "error",
                    "message": "Arsa analizi tamamlanamadı.",
                    "detail": str(exc),
                },
                500,
            )

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/alici_arsa_analiz.html")
            self.end_headers()
            return

        if path == "/api/v1/veri-durumu":
            self.send_json(
                {
                    "status": "success",
                    "modules": {
                        "conflict_resolution": "ready",
                        "land_comparables": "ready",
                        "tkgm_live": "on_demand",
                        "zoning_live": "on_demand_best_effort",
                    },
                    "land_comparable_database": (
                        "warehouse_product_index"
                        if LAND_DATABASE == PRODUCT_LAND_DATABASE
                        else "legacy_listing_database"
                    ),
                    "source_links_public": False,
                    "internal_provenance_retained": True,
                }
            )
            return

        # 1. CANLI PARSEL & İMAR SORGULAMA API UÇ NOKTASI
        if path in {"/api/parsel-sorgu", "/api/v1/parsel/canli"}:
            start_time = time.time()
            try:
                params = {key: values[0] for key, values in query.items() if values}
                result = parsel_gateway.query(params)

                elapsed_ms = round((time.time() - start_time) * 1000)
                if path == "/api/v1/parsel/canli":
                    result["elapsed_ms"] = elapsed_ms
                    self.send_json(result, 200 if result["status"] == "success" else 404)
                    return
                if result["status"] == "success":
                    response_data = {
                        "status": "success",
                        "sure_ms": elapsed_ms,
                        "kaynak": {
                            "kadastro": "TKGM MEGSİS",
                            "imar": result.get("zoning", {}).get("source_name"),
                        },
                        "data": {
                            "parsel": {
                                "il": result["parcel"].get("province"),
                                "ilce": result["parcel"].get("district"),
                                "mahalle": result["parcel"].get("neighbourhood"),
                                "mahalle_id": result["parcel"].get("neighbourhood_id"),
                                "ada_no": result["parcel"].get("block"),
                                "parsel_no": result["parcel"].get("parcel"),
                                "alan_m2": result["parcel"].get("area_m2"),
                                "alan_raw": result["parcel"].get("area_raw"),
                                "nitelik": result["parcel"].get("quality"),
                                "zemin_durumu": result["parcel"].get("ground_title_status"),
                                "pafta": result["parcel"].get("map_sheet"),
                                "mevkii": result["parcel"].get("locality"),
                                "enlem": result["parcel"].get("lat"),
                                "boylam": result["parcel"].get("lon"),
                                "geometry": result["parcel"].get("geometry"),
                            },
                            "imar": {
                                "success": result["zoning"]["status"] == "verified_at_source",
                                "veri_durumu": result["zoning"]["status"],
                                "kaynak": result["zoning"].get("source_name"),
                                **result["zoning"].get("fields", {}),
                                "aski_degisiklikleri": result["zoning"].get("plan_changes", []),
                            },
                            "bagimsiz_bolumler": [],
                            "veri_guveni": result.get("confidence", {}),
                        },
                    }
                    status_code = 200
                else:
                    response_data = {
                        "status": "not_found",
                        "sure_ms": elapsed_ms,
                        "mesaj": "Belirtilen koordinat veya ada/parselde kadastral parsel kaydı bulunamadı (yol, meydan veya tescil harici alan olabilir)."
                    }
                    status_code = 404

            except ParcelQueryError as exc:
                response_data = {"status": "validation_error", "mesaj": str(exc)}
                status_code = 400
            except Exception as e:
                elapsed_ms = round((time.time() - start_time) * 1000)
                response_data = {
                    "status": "error",
                    "sure_ms": elapsed_ms,
                    "hata": str(e)
                }
                status_code = 500

            self.send_json(response_data, status_code)
            return

        # 2. PARSEL ÇEVRESİNDE DEĞER OLUŞTURAN YAKIN NOKTALAR
        elif path == "/api/yakin-poiler":
            try:
                lat = float(query["lat"][0])
                lon = float(query["lon"][0])
                radius = max(500, min(3000, int(query.get("radius", [1800])[0])))
                points = fetch_nearby_pois(lat, lon, radius)
                response_data = {
                    "status": "success",
                    "radius_m": radius,
                    "source": "OpenStreetMap / Overpass",
                    "points": points,
                }
                status_code = 200
            except (KeyError, ValueError):
                response_data = {"status": "error", "message": "Geçerli enlem ve boylam gereklidir."}
                status_code = 400
            except Exception as exc:
                response_data = {"status": "error", "message": "Yakın çevre noktaları alınamadı.", "detail": str(exc)}
                status_code = 502

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode("utf-8"))
            return

        # 3. MODEL/DEMO TİCARİ İSTİHBARAT & E-TİCARET KARNESİ API'Sİ
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
                "donem": "MODEL-2026-2027",
                "veri_sinifi": "model_demo",
                "uyari": "Bu bölümdeki skorlar doğrulanmış resmî ölçüm değildir.",
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
            # Tarayıcılar performans için bağlantıyı önceden açıp istek
            # göndermeden bekletebilir. Tek iş parçacıklı TCPServer böyle bir
            # bağlantıda kilitlenip statik arayüz dahil tüm istekleri durdurur.
            # ThreadingHTTPServer her bağlantıyı ayrı işleyerek arayüzü canlı
            # TKGM/imar ve yarım açık tarayıcı bağlantılarından yalıtır.
            with http.server.ThreadingHTTPServer(("127.0.0.1", port), GeopropApiHandler) as httpd:
                url = f"http://localhost:{port}/alici_arsa_analiz.html"
                print("=" * 70)
                print(f"  ⚡ GEOPROP AI CANLI İMAR & PARSEL BORU HATTI SUNUCUSU")
                print(f"  📍 Alıcı Arsa Analizi:      {url}")
                print(f"  📍 Canlı İmar Test Sayfası: http://localhost:{port}/imar_canli_test.html")
                print(f"  📍 Veri Analiz Dashboard:   http://localhost:{port}/index.html")
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
