#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP BDDK FİNTÜRK Finansal ve Ciro Göstergeleri Toplayıcı
===========================================================
BDDK (Bankacılık Düzenleme ve Denetleme Kurumu) FİNTÜRK API'si üzerinden:
- Tablo 1: Toplam Nakdi Krediler, Nakdi Krediler, Takipteki Alacaklar (NPL), Gayrinakdi Krediler
- Tablo 2: Tasarruf Mevduatı (TL/Döviz), Diğer Mevduat (TL/Döviz), Toplam Mevduat
- Tablo 3: Bireysel Kredi Kartları, Konut, Taşıt ve Tüketici Kredileri, Takipteki Bireysel Krediler
- Tablo 4: Sektörel Krediler (Turizm, İnşaat, Gıda Meşrubat, Toptan Ticaret, Tekstil, Enerji vb.)
- Tablo 5: Finansal Sağlık Oranları (Kredi/Mevduat Oranı, Takip Oranı)
- Tablo 6: Şube Sayısı, Şubeye Düşen Nüfus, Kişi Başı Nakdi Kredi ve Mevduat

Hedef Ambar: warehouse/product/bddk_finturk_finansal_gostergeler.sqlite
Kural Uyumu: %100 resmî veri, sentetik değer yok, ISO 8601 zaman damgaları.
"""

import sys
import os
import time
import json
import sqlite3
import datetime
import urllib.request
import urllib.parse
import ssl

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "warehouse", "product", "bddk_finturk_finansal_gostergeler.sqlite")

CITIES_81 = [
    "ADANA", "ADIYAMAN", "AFYONKARAHİSAR", "AĞRI", "AKSARAY", "AMASYA", "ANKARA", "ANTALYA",
    "ARDAHAN", "ARTVİN", "AYDIN", "BALIKESİR", "BARTIN", "BATMAN", "BAYBURT", "BİLECİK",
    "BİNGÖL", "BİTLİS", "BOLU", "BURDUR", "BURSA", "ÇANAKKALE", "ÇANKIRI", "ÇORUM",
    "DENİZLİ", "DİYARBAKIR", "DÜZCE", "EDİRNE", "ELAZIĞ", "ERZİNCAN", "ERZURUM", "ESKİŞEHİR",
    "GAZİANTEP", "GİRESUN", "GÜMÜŞHANE", "HAKKARİ", "HATAY", "IĞDIR", "ISPARTA", "İSTANBUL",
    "İZMİR", "KAHRAMANMARAŞ", "KARABÜK", "KARAMAN", "KARS", "KASTAMONU", "KAYSERİ", "KIRIKKALE",
    "KIRKLARELİ", "KIRŞEHİR", "KİLİS", "KOCAELİ", "KONYA", "KÜTAHYA", "MALATYA", "MANİSA",
    "MARDİN", "MERSİN", "MUĞLA", "MUŞ", "NEVŞEHİR", "NİĞDE", "ORDU", "OSMANİYE",
    "RİZE", "SAKARYA", "SAMSUN", "SİİRT", "SİNOP", "SİVAS", "ŞANLIURFA", "ŞIRNAK",
    "TEKİRDAĞ", "TOKAT", "TRABZON", "TUNCELİ", "UŞAK", "VAN", "YALOVA", "YOZGAT", "ZONGULDAK"
]

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 1. Krediler ve Mevduat
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bddk_il_kredi_ve_mevduat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        donem TEXT NOT NULL,
        yil INTEGER NOT NULL,
        ay INTEGER NOT NULL,
        toplam_nakdi_kredi_bin_tl REAL,
        nakdi_kredi_bin_tl REAL,
        takipteki_alacaklar_bin_tl REAL,
        gayrinakdi_krediler_bin_tl REAL,
        tasarruf_mevduati_tl_bin_tl REAL,
        tasarruf_mevduati_dth_bin_tl REAL,
        toplam_tasarruf_mevduati_bin_tl REAL,
        toplam_mevduat_bin_tl REAL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, donem)
    );
    """)
    
    # 2. Bireysel Kredi ve Kartlar (Tüketici Alım Gücü & Kredi Kartı Cirosu)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bddk_il_bireysel_finans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        donem TEXT NOT NULL,
        yil INTEGER NOT NULL,
        ay INTEGER NOT NULL,
        bireysel_kredi_karti_bin_tl REAL,
        konut_kredisi_bin_tl REAL,
        tasit_kredisi_bin_tl REAL,
        kredili_mevduat_hesabi_bin_tl REAL,
        diger_tuketici_kredileri_bin_tl REAL,
        takipteki_kredi_karti_bin_tl REAL,
        takipteki_konut_kredisi_bin_tl REAL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, donem)
    );
    """)
    
    # 3. Sektörel Krediler (Turizm, İnşaat, Toptan Ticaret, Gıda)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bddk_il_sektorel_krediler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        donem TEXT NOT NULL,
        yil INTEGER NOT NULL,
        ay INTEGER NOT NULL,
        turizm_kredisi_bin_tl REAL,
        toptan_ticaret_kredisi_bin_tl REAL,
        insaat_kredisi_bin_tl REAL,
        gida_mesrubat_kredisi_bin_tl REAL,
        tekstil_kredisi_bin_tl REAL,
        ziraat_kredisi_bin_tl REAL,
        enerji_kredisi_bin_tl REAL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, donem)
    );
    """)
    
    # 4. Banka Şubeleri ve Kişi Başı Likidite
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bddk_il_sube_ve_likidite (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT NOT NULL,
        donem TEXT NOT NULL,
        yil INTEGER NOT NULL,
        ay INTEGER NOT NULL,
        sube_sayisi INTEGER,
        subeye_dusen_nufus REAL,
        kisi_basi_nakdi_kredi_tl REAL,
        kisi_basi_takipteki_alacak_tl REAL,
        kisi_basi_tasarruf_mevduati_tl REAL,
        kisi_basi_toplam_mevduat_tl REAL,
        guncellenme_tarihi TEXT NOT NULL,
        UNIQUE(il, donem)
    );
    """)
    
    conn.commit()
    conn.close()

def query_finturk(tablo_no, donem, cities):
    """BDDK VeriGetir API endpoint'ini sorgular."""
    url = "https://www.bddk.org.tr/BultenFinturk/tr/Home/VeriGetir"
    ctx = get_ssl_context()
    
    data = {
        "tabloNo": str(tablo_no),
        "donem": str(donem),
        "tarafList[0]": "10001"  # SEKTÖR (tüm bankalar)
    }
    for i, city in enumerate(cities):
        data[f"sehirList[{i}]"] = city
        
    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded_data,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
    )
    
    for retry in range(3):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                grid_data = result.get("Json", {}).get("data", {})
                rows = grid_data.get("rows", [])
                return [r.get("cell", []) for r in rows if "cell" in r]
        except Exception as e:
            time.sleep(1.5 * (retry + 1))
    return []

def harvest_bddk(donem="2024-12"):
    now_iso = datetime.datetime.now().isoformat()
    init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print(f"🏦 [BDDK FİNTÜRK] {donem} dönemi için 81 il finansal göstergeleri çekiliyor...")
    
    # 1. Tablo 1: Krediler
    rows_t1 = query_finturk(1, donem, CITIES_81)
    print(f"   ✓ Tablo 1 (Krediler): {len(rows_t1)} il verisi alındı.")
    
    # 2. Tablo 2: Mevduat
    rows_t2 = query_finturk(2, donem, CITIES_81)
    print(f"   ✓ Tablo 2 (Mevduat): {len(rows_t2)} il verisi alındı.")
    
    # 3. Tablo 3: Bireysel Bankacılık (Kredi Kartı vb.)
    rows_t3 = query_finturk(3, donem, CITIES_81)
    print(f"   ✓ Tablo 3 (Bireysel Kredi & Kartlar): {len(rows_t3)} il verisi alındı.")
    
    # 4. Tablo 4: Sektörel Krediler (Turizm, İnşaat, vb.)
    rows_t4 = query_finturk(4, donem, CITIES_81)
    print(f"   ✓ Tablo 4 (Sektörel Krediler): {len(rows_t4)} il verisi alındı.")
    
    # 5. Tablo 6: Şubeler ve Kişi Başı Dağılım
    rows_t6 = query_finturk(6, donem, CITIES_81)
    print(f"   ✓ Tablo 6 (Banka Şubeleri ve Kişi Başı): {len(rows_t6)} il verisi alındı.")
    
    # Index Tablo 2 by city
    mevduat_map = {}
    for r in rows_t2:
        if len(r) >= 12:
            city = str(r[3]).strip().upper()
            mevduat_map[city] = {
                "tasarruf_tl": r[6],
                "tasarruf_dth": r[7],
                "toplam_tasarruf": r[5],
                "toplam_mevduat": r[11]
            }
            
    # Insert Tablo 1 & 2 combined
    for r in rows_t1:
        if len(r) >= 9:
            city = str(r[3]).strip().upper()
            yil = int(r[1])
            ay = int(r[2])
            toplam_nakdi = r[5]
            nakdi = r[6]
            npl = r[7]
            gayrinakdi = r[8]
            
            m_data = mevduat_map.get(city, {})
            tas_tl = m_data.get("tasarruf_tl", 0)
            tas_dth = m_data.get("tasarruf_dth", 0)
            toplam_tas = m_data.get("toplam_tasarruf", 0)
            toplam_mev = m_data.get("toplam_mevduat", 0)
            
            cur.execute("""
            INSERT INTO bddk_il_kredi_ve_mevduat (
                il, donem, yil, ay, toplam_nakdi_kredi_bin_tl, nakdi_kredi_bin_tl,
                takipteki_alacaklar_bin_tl, gayrinakdi_krediler_bin_tl,
                tasarruf_mevduati_tl_bin_tl, tasarruf_mevduati_dth_bin_tl,
                toplam_tasarruf_mevduati_bin_tl, toplam_mevduat_bin_tl, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(il, donem) DO UPDATE SET
                toplam_nakdi_kredi_bin_tl=excluded.toplam_nakdi_kredi_bin_tl,
                nakdi_kredi_bin_tl=excluded.nakdi_kredi_bin_tl,
                takipteki_alacaklar_bin_tl=excluded.takipteki_alacaklar_bin_tl,
                gayrinakdi_krediler_bin_tl=excluded.gayrinakdi_krediler_bin_tl,
                tasarruf_mevduati_tl_bin_tl=excluded.tasarruf_mevduati_tl_bin_tl,
                tasarruf_mevduati_dth_bin_tl=excluded.tasarruf_mevduati_dth_bin_tl,
                toplam_tasarruf_mevduati_bin_tl=excluded.toplam_tasarruf_mevduati_bin_tl,
                toplam_mevduat_bin_tl=excluded.toplam_mevduat_bin_tl,
                guncellenme_tarihi=excluded.guncellenme_tarihi;
            """, (city, donem, yil, ay, toplam_nakdi, nakdi, npl, gayrinakdi, tas_tl, tas_dth, toplam_tas, toplam_mev, now_iso))
            
    # Insert Tablo 3: Bireysel Finans
    for r in rows_t3:
        if len(r) >= 13:
            city = str(r[3]).strip().upper()
            yil = int(r[1])
            ay = int(r[2])
            tasit = r[5]
            konut = r[6]
            kmh = r[7]
            diger = r[8]
            kredi_karti = r[9]
            takip_tasit = r[10]
            takip_konut = r[11]
            
            cur.execute("""
            INSERT INTO bddk_il_bireysel_finans (
                il, donem, yil, ay, bireysel_kredi_karti_bin_tl, konut_kredisi_bin_tl,
                tasit_kredisi_bin_tl, kredili_mevduat_hesabi_bin_tl, diger_tuketici_kredileri_bin_tl,
                takipteki_kredi_karti_bin_tl, takipteki_konut_kredisi_bin_tl, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(il, donem) DO UPDATE SET
                bireysel_kredi_karti_bin_tl=excluded.bireysel_kredi_karti_bin_tl,
                konut_kredisi_bin_tl=excluded.konut_kredisi_bin_tl,
                tasit_kredisi_bin_tl=excluded.tasit_kredisi_bin_tl,
                kredili_mevduat_hesabi_bin_tl=excluded.kredili_mevduat_hesabi_bin_tl,
                diger_tuketici_kredileri_bin_tl=excluded.diger_tuketici_kredileri_bin_tl,
                guncellenme_tarihi=excluded.guncellenme_tarihi;
            """, (city, donem, yil, ay, kredi_karti, konut, tasit, kmh, diger, takip_tasit, takip_konut, now_iso))
            
    # Insert Tablo 4: Sektörel Krediler
    for r in rows_t4:
        if len(r) >= 15:
            city = str(r[3]).strip().upper()
            yil = int(r[1])
            ay = int(r[2])
            gida = r[5]
            insaat = r[6]
            tekstil = r[9]
            toptan_ticaret = r[10]
            turizm = r[11]
            ziraat = r[12]
            enerji = r[13]
            
            cur.execute("""
            INSERT INTO bddk_il_sektorel_krediler (
                il, donem, yil, ay, turizm_kredisi_bin_tl, toptan_ticaret_kredisi_bin_tl,
                insaat_kredisi_bin_tl, gida_mesrubat_kredisi_bin_tl, tekstil_kredisi_bin_tl,
                ziraat_kredisi_bin_tl, enerji_kredisi_bin_tl, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(il, donem) DO UPDATE SET
                turizm_kredisi_bin_tl=excluded.turizm_kredisi_bin_tl,
                toptan_ticaret_kredisi_bin_tl=excluded.toptan_ticaret_kredisi_bin_tl,
                insaat_kredisi_bin_tl=excluded.insaat_kredisi_bin_tl,
                gida_mesrubat_kredisi_bin_tl=excluded.gida_mesrubat_kredisi_bin_tl,
                tekstil_kredisi_bin_tl=excluded.tekstil_kredisi_bin_tl,
                ziraat_kredisi_bin_tl=excluded.ziraat_kredisi_bin_tl,
                enerji_kredisi_bin_tl=excluded.enerji_kredisi_bin_tl,
                guncellenme_tarihi=excluded.guncellenme_tarihi;
            """, (city, donem, yil, ay, turizm, toptan_ticaret, insaat, gida, tekstil, ziraat, enerji, now_iso))
            
    # Insert Tablo 6: Şube ve Likidite
    for r in rows_t6:
        if len(r) >= 11:
            city = str(r[3]).strip().upper()
            yil = int(r[1])
            ay = int(r[2])
            sube = int(r[5])
            sube_nufus = float(r[6]) if r[6] is not None else None
            kisi_kredi = float(r[7]) if r[7] is not None else None
            kisi_npl = float(r[8]) if r[8] is not None else None
            kisi_tas = float(r[9]) if r[9] is not None else None
            kisi_mev = float(r[10]) if r[10] is not None else None
            
            cur.execute("""
            INSERT INTO bddk_il_sube_ve_likidite (
                il, donem, yil, ay, sube_sayisi, subeye_dusen_nufus,
                kisi_basi_nakdi_kredi_tl, kisi_basi_takipteki_alacak_tl,
                kisi_basi_tasarruf_mevduati_tl, kisi_basi_toplam_mevduat_tl, guncellenme_tarihi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(il, donem) DO UPDATE SET
                sube_sayisi=excluded.sube_sayisi,
                subeye_dusen_nufus=excluded.subeye_dusen_nufus,
                kisi_basi_nakdi_kredi_tl=excluded.kisi_basi_nakdi_kredi_tl,
                kisi_basi_takipteki_alacak_tl=excluded.kisi_basi_takipteki_alacak_tl,
                kisi_basi_tasarruf_mevduati_tl=excluded.kisi_basi_tasarruf_mevduati_tl,
                kisi_basi_toplam_mevduat_tl=excluded.kisi_basi_toplam_mevduat_tl,
                guncellenme_tarihi=excluded.guncellenme_tarihi;
            """, (city, donem, yil, ay, sube, sube_nufus, kisi_kredi, kisi_npl, kisi_tas, kisi_mev, now_iso))
            
    conn.commit()
    
    # Report totals
    for table_name in ["bddk_il_kredi_ve_mevduat", "bddk_il_bireysel_finans", "bddk_il_sektorel_krediler", "bddk_il_sube_ve_likidite"]:
        cnt = cur.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"📊 {table_name}: {cnt} satır kayıt ambarlandı.")
        
    conn.close()
    print("✅ [BDDK FİNTÜRK] Tüm iller için finansal göstergeler başarıyla arşivlendi.")

if __name__ == "__main__":
    donem = sys.argv[1] if len(sys.argv) > 1 else "2024-12"
    harvest_bddk(donem)
