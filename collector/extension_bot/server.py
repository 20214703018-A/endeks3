import json
import sqlite3
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse

DB_MENU = "warehouse/product/restoran_ve_kafe_menuleri.sqlite"
DB_PLACES = "warehouse/product/google_places_ve_yogunluk.sqlite"
TARGET_CITIES = ['Antalya', 'İstanbul', 'Ankara', 'Bursa', 'Konya', 'Eskişehir', 'Muğla', 'İzmir', 'Mersin', 'Aydın']

venues = []
current_index = 0

def load_venues():
    global venues
    print("Mekanlar yükleniyor...")
    if not os.path.exists(DB_PLACES):
        # Fallback test verisi
        venues = [
            {"id": "T1", "adi": "Lara Aspava", "ilce": "Muratpaşa", "il": "Antalya", "mahalle": "Şirinyalı"},
            {"id": "T2", "adi": "Marje Mantı", "ilce": "Muratpaşa", "il": "Antalya", "mahalle": "Fener"}
        ]
        return
        
    conn = sqlite3.connect(DB_PLACES)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT google_place_id, isim, ilce, il, mahalle
        FROM google_places_ticari_yogunluk
        WHERE il IN ({','.join(['?']*len(TARGET_CITIES))})
          AND yorum_sayisi >= 50
          AND (ana_kategori LIKE '%Restoran%' OR ana_kategori LIKE '%Kafe%')
        ORDER BY yorum_sayisi DESC
    """, (*TARGET_CITIES,))
    for row in cur.fetchall():
        venues.append({
            "id": row[0], "adi": row[1], "ilce": row[2], "il": row[3], "mahalle": row[4]
        })
    conn.close()
    print(f"{len(venues)} mekan başarıyla yüklendi.")

def save_data(data):
    os.makedirs(os.path.dirname(DB_MENU), exist_ok=True)
    conn = sqlite3.connect(DB_MENU)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mekan_menu_gorselleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT NOT NULL,
            mekan_adi TEXT NOT NULL,
            gorsel_url TEXT NOT NULL,
            kaynak TEXT NOT NULL,
            kategori TEXT,
            fotograf_tarihi TEXT,
            il TEXT,
            ilce TEXT,
            mahalle TEXT,
            tarama_tarihi TEXT NOT NULL,
            UNIQUE(mekan_id, gorsel_url)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mekan_menu_kalemleri_ve_fiyat_tarihcesi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT,
            mekan_adi TEXT,
            donem TEXT,
            tarih TEXT,
            fiyat_turu TEXT,
            platform TEXT,
            kategori TEXT,
            urun_adi TEXT,
            fiyat REAL,
            ilce TEXT,
            il TEXT,
            mahalle TEXT,
            guncellenme_tarihi TEXT
        )
    """)
    
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    donem = time.strftime('%Y-%m')
    
    g_say = 0
    for gorsel in data.get("gorseller", []):
        try:
            cur.execute("""
                INSERT OR IGNORE INTO mekan_menu_gorselleri
                (mekan_id, mekan_adi, gorsel_url, kaynak, kategori, tarama_tarihi, il, ilce, mahalle)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (data['id'], data['adi'], gorsel, "GoogleKnowledgePanel", "Menü/Öne Çıkanlar", now, data['il'], data['ilce'], data['mahalle']))
            if cur.rowcount > 0: g_say += 1
        except: pass
        
    f_say = 0
    for fiyat in data.get("fiyatlar", []):
        try:
            cur.execute("""
                INSERT INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi 
                (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori, urun_adi, fiyat, ilce, il, mahalle, guncellenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (data['id'], data['adi'], donem, now, "BilgiPanosu", fiyat['platform'], "Menü", fiyat['urun'], fiyat['fiyat'], data['ilce'], data['il'], data['mahalle'], now))
            f_say += 1
        except: pass
        
    conn.commit()
    conn.close()
    print(f"✅ {data['adi']} -> {g_say} Görsel, {f_say} Fiyat Kaydedildi.")

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global current_index
        if self.path == '/next':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            if current_index >= len(venues):
                self.wfile.write(json.dumps({"status": "done"}).encode('utf-8'))
            else:
                v = venues[current_index]
                current_index += 1
                query = f"{v['adi']} {v['ilce']} {v['il']} menü"
                v['query'] = query
                self.wfile.write(json.dumps(v).encode('utf-8'))
                print(f"👉 Sıradaki: {query}")
                
    def do_POST(self):
        if self.path == '/save':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            save_data(data)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

def run():
    load_venues()
    server_address = ('127.0.0.1', 5050)
    httpd = HTTPServer(server_address, RequestHandler)
    print("🚀 GEOPROP Extension Server çalışıyor: http://127.0.0.1:5050")
    print("Lütfen Chrome uzantısına tıklayarak taramayı başlatın.")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
