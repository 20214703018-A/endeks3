#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
GEOPROP AI - ADA / PARSEL KADASTRO VE İMAR SORGULAMA ARACI
===============================================================================
TKGM yanıtından gelen kadastro alanları kaynak gözlemidir. İmar/plan alanları
yalnız yapılandırılmış E-Plan yanıtında gerçekten bulunduğunda doldurulur. Kaynak
başarısızlığında varsayılan imar hakkı veya bağımsız bölüm üretilmez.
===============================================================================
"""

import sys
import os
import json
import time
import sqlite3
import argparse
import csv
from datetime import datetime
from curl_cffi import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "imar_ve_parseller")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "parsel_imar_degisiklikleri.sqlite")
JSON_OUTPUT_PATH = os.path.join(DATA_DIR, "parsel_imar_ve_degisiklikler.json")
GEOJSON_OUTPUT_PATH = os.path.join(DATA_DIR, "turkiye_parseller_geo.geojson")

EPLAN_BASE_URL = "https://eplan.csb.gov.tr"
EPLAN_PLAN_OBJECT_ID = "1159bc1d-75cd-438d-958c-8716cc973f6c"
EPLAN_GEOMETRY_FIELD = "3d758202-040e-481a-abd6-fb1c20312d63"
EPLAN_PLAN_NAME_FIELD = "3f52b328-525b-49cb-a126-b0b2569a1553"
EPLAN_PIN_FIELD = "319c08f6-0971-45c9-aef6-4ed53c108d45"
EPLAN_ACTIVE_FIELD = "4e0d4291-130b-410a-8fee-f78309bac985"
EPLAN_RECORD_DATE_FIELD = "24da024e-bcba-425e-991a-5480164990ba"
EPLAN_PLAN_TYPE_FIELD = "5301b42b-4818-45a7-acd9-d25621db5197_3b452987-6f95-4b19-906e-7394e8f78723"
EPLAN_SCALE_FIELD = "a895b410-68e4-4960-888b-c73321ac865f_2c9e87e1-d52c-4a71-acf7-b48e39f6044a"
EPLAN_APPROVAL_STATUS_FIELD = "e5478ae0-d4f2-4555-ae12-401d68612cf0_fce47bc6-1bbe-45bb-b7c7-03ee71a2ffbb"

EPLAN_RESULT_FIELDS = (
    "recID",
    EPLAN_PLAN_NAME_FIELD,
    EPLAN_PIN_FIELD,
    EPLAN_ACTIVE_FIELD,
    EPLAN_RECORD_DATE_FIELD,
    EPLAN_PLAN_TYPE_FIELD,
    EPLAN_SCALE_FIELD,
    EPLAN_APPROVAL_STATUS_FIELD,
)

def get_db_connection(db_path=None):
    target = db_path or DB_PATH
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn

def init_database(db_path=None):
    """Veritabanı tablolarını oluşturur."""
    conn = get_db_connection(db_path)
    c = conn.cursor()
    
    # 1. Parsel & İmar Kayıtları Tablosu
    c.execute("""
    CREATE TABLE IF NOT EXISTS parsel_imar_kayitlari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        mahalle_id INTEGER,
        ada_no TEXT,
        parsel_no TEXT,
        alan_m2 REAL,
        nitelik TEXT,
        zemin_durumu TEXT,
        pafta TEXT,
        mevkii TEXT,
        enlem REAL,
        boylam REAL,
        poligon_geojson TEXT,
        
        -- İmar Durumu Bilgileri
        imar_durumu TEXT,
        plan_fonksiyon TEXT,
        kaks_emsal REAL,
        taks REAL,
        gabari TEXT,
        kat_adedi TEXT,
        yapi_nizami TEXT,
        on_bahce REAL,
        yan_bahce REAL,
        
        -- Plan Künyesi
        plan_adi TEXT,
        plan_turu TEXT,
        pin_tucbs_no TEXT,
        onay_tarihi TEXT,
        yururluk_tarihi TEXT,
        plan_sureci TEXT,
        
        -- Kısıtlılık ve Çevre Katmanları
        dogal_sit TEXT,
        arkeolojik_sit TEXT,
        havaalani_mania TEXT,
        milli_park TEXT,
        hazine_durumu TEXT,
        
        -- Meta
        kaynak TEXT,
        son_guncelleme TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(mahalle_id, ada_no, parsel_no)
    )
    """)
    
    # 2. Bağımsız Bölümler Tablosu
    c.execute("""
    CREATE TABLE IF NOT EXISTS bagimsiz_bolumler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mahalle_id INTEGER,
        ada_no TEXT,
        parsel_no TEXT,
        bolum_no TEXT,
        kat TEXT,
        giris TEXT,
        nitelik TEXT,
        blok TEXT,
        kaynak TEXT,
        guncellenme TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(mahalle_id, ada_no, parsel_no, bolum_no)
    )
    """)
    
    # 3. Askıdaki İmar Planı Değişiklikleri & Gelecek Planlar
    c.execute("""
    CREATE TABLE IF NOT EXISTS imar_degisiklik_ve_askilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        il TEXT,
        ilce TEXT,
        mahalle TEXT,
        ada_no TEXT,
        parsel_no TEXT,
        degisiklik_turu TEXT,
        plan_kodu TEXT,
        aski_baslangic TEXT,
        aski_bitis TEXT,
        itiraz_durumu TEXT,
        aciklama TEXT,
        tespit_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    conn.close()

def normalize_tr(text):
    if not text:
        return ""
    tr_map = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiisssuuooocc")
    return text.strip().lower().translate(tr_map)


def parse_localized_number(value):
    """TKGM'nin TR/EN binlik ve ondalık ayraç varyantlarını güvenli ayrıştırır."""
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(".") > text.rfind(","):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def positive_source_number(value):
    """Kaynağın sıfır/boş sentinel değerini gerçek imar hakkı gibi sunmaz."""
    parsed = parse_localized_number(value)
    return parsed if parsed > 0 else None

class ParselImarToplayici:
    def __init__(self, cikis_ek=None, init_storage=True):
        self.cikis_ek = cikis_ek
        if cikis_ek:
            self.db_path = os.path.join(DATA_DIR, f"parsel_imar_degisiklikleri_{cikis_ek}.sqlite")
            self.json_path = os.path.join(DATA_DIR, f"parsel_imar_ve_degisiklikler_{cikis_ek}.json")
            self.geojson_path = os.path.join(DATA_DIR, f"turkiye_parseller_geo_{cikis_ek}.geojson")
        else:
            self.db_path = DB_PATH
            self.json_path = JSON_OUTPUT_PATH
            self.geojson_path = GEOJSON_OUTPUT_PATH
            
        if init_storage:
            init_database(self.db_path)
        self.session = requests.Session()
        self.headers_eplan = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{EPLAN_BASE_URL}/e-plan/html/acikPlanlar.html",
            "Origin": EPLAN_BASE_URL,
            "Content-Type": "application/json"
        }
        self.headers_tkgm = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://parselsorgu.tkgm.gov.tr/"
        }

    def fetch_tkgm_megsis(self, mahalle_id=None, ada=None, parsel=None, lat=None, lon=None):
        """Devletin resmi MEGSİS API'sinden kadastro, alan (m2) ve GeoJSON parsel poligonunu çeker.
        Önce Ada/Parsel ile dener, bulunamazsa veya eksikse GPS koordinatından tersine sorgular.
        """
        data = None
        # 1. Deneme: Mahalle ID + Ada + Parsel
        if mahalle_id and ada and parsel:
            url = f"https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api/parsel/{mahalle_id}/{ada}/{parsel}"
            try:
                r = self.session.get(url, headers=self.headers_tkgm, impersonate="chrome124", timeout=10)
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    res = r.json()
                    if res.get("properties", {}).get("parselNo"):
                        data = res
            except Exception:
                pass

        # 2. Deneme: GPS Koordinatları (Enlem / Boylam ile Tersine Sorgulama)
        if not data and lat and lon:
            url = f"https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api/parsel/{lat}/{lon}/"
            try:
                r = self.session.get(url, headers=self.headers_tkgm, impersonate="chrome124", timeout=10)
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    res = r.json()
                    if res.get("properties", {}).get("parselNo"):
                        data = res
            except Exception:
                pass

        if data:
            props = data.get("properties", {})
            geom = data.get("geometry", {})
            coords = geom.get("coordinates", [[]])[0] if geom.get("coordinates") else []
            lat_c = sum(c[1] for c in coords) / len(coords) if coords else lat
            lon_c = sum(c[0] for c in coords) / len(coords) if coords else lon
            
            alan_raw = props.get("alan", "0")
            alan_m2 = parse_localized_number(alan_raw)
                
            return {
                "success": True,
                "il": props.get("ilAd", ""),
                "ilce": props.get("ilceAd", ""),
                "mahalle": props.get("mahalleAd", ""),
                "mahalle_id": props.get("mahalleId", mahalle_id),
                "ada_no": props.get("adaNo", str(ada) if ada else ""),
                "parsel_no": props.get("parselNo", str(parsel) if parsel else ""),
                "alan_m2": alan_m2,
                "alan_raw": alan_raw,
                "nitelik": props.get("nitelik", ""),
                "zemin_durumu": props.get("zeminKmdurum", "Kat Mülkiyet"),
                "pafta": props.get("pafta", ""),
                "mevkii": props.get("mevkii", ""),
                "enlem": lat_c,
                "boylam": lon_c,
                "geometry": geom,
                "poligon_geojson": json.dumps(geom) if geom else ""
            }
        return None

    @staticmethod
    def _empty_zoning_result(status="kaynak_erisilemedi", error=None):
        return {
            "success": False,
            "veri_durumu": status,
            "kaynak": "ÇŞİDB E-Plan" if status != "kaynak_erisilemedi" else None,
            "hata": error,
            "imar_durumu": None,
            "plan_fonksiyon": None,
            "kaks_emsal": None,
            "taks": None,
            "gabari": None,
            "kat_adedi": None,
            "yapi_nizami": None,
            "on_bahce": None,
            "yan_bahce": None,
            "plan_adi": None,
            "plan_turu": None,
            "pin_tucbs_no": None,
            "onay_tarihi": None,
            "yururluk_tarihi": None,
            "plan_sureci": None,
            "dogal_sit": None,
            "havaalani_mania": None,
            "hazine_durumu": None,
            "aski_degisiklikleri": [],
            "planlar": [],
            "fonksiyon_katmanlari": [],
        }

    def _ensure_eplan_guest_session(self):
        """E-Plan'ın herkese açık sorgusu için anonim oturum çerezini alır."""
        info = self.session.get(
            f"{EPLAN_BASE_URL}/fSession/getSessionInfo",
            headers=self.headers_eplan,
            impersonate="chrome124",
            timeout=10,
        )
        if info.status_code == 200:
            return
        login = self.session.get(
            f"{EPLAN_BASE_URL}/fSession/loginAsGuest",
            headers=self.headers_eplan,
            impersonate="chrome124",
            timeout=10,
        )
        if login.status_code != 200:
            raise RuntimeError(f"E-Plan anonim oturum HTTP {login.status_code}")

    def _query_eplan_plans(self, lat, lon, limit=10):
        self._ensure_eplan_guest_session()
        query = {
            "columnFilters": [{
                "name": EPLAN_GEOMETRY_FIELD,
                "operator": "INTERSECTS",
                "value": f"POINT({float(lon):.8f} {float(lat):.8f})",
            }],
            "resultColumns": [
                {"fObjectPropertyRecID": field, "aggregateFunction": 0}
                for field in EPLAN_RESULT_FIELDS
            ],
            "length": limit,
        }
        response = self.session.post(
            f"{EPLAN_BASE_URL}/fObjectData/query?fObjectID={EPLAN_PLAN_OBJECT_ID}",
            json=query,
            headers=self.headers_eplan,
            impersonate="chrome124",
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(f"E-Plan plan sorgusu HTTP {response.status_code}")
        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("E-Plan plan sorgusu beklenmeyen yanıt döndürdü")
        plans = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            plans.append({
                "record_id": row.get("recID"),
                "plan_adi": row.get(EPLAN_PLAN_NAME_FIELD),
                "pin_tucbs_no": row.get(EPLAN_PIN_FIELD),
                "aktif": str(row.get(EPLAN_ACTIVE_FIELD) or "").lower() in {"1", "true"},
                "kayit_tarihi": row.get(EPLAN_RECORD_DATE_FIELD),
                "plan_turu": row.get(EPLAN_PLAN_TYPE_FIELD),
                "olcek": row.get(EPLAN_SCALE_FIELD),
                "onay_durumu": row.get(EPLAN_APPROVAL_STATUS_FIELD),
            })
        return plans

    def _query_eplan_function(self, plan_id, lat, lon):
        response = self.session.get(
            f"{EPLAN_BASE_URL}/planGML/getPlanLayerData",
            params={"planID": plan_id, "filterWkt": f"POINT ({float(lon):.8f} {float(lat):.8f})"},
            headers=self.headers_eplan,
            impersonate="chrome124",
            timeout=15,
        )
        if response.status_code != 200:
            return []
        result = response.json()
        return result if isinstance(result, list) else []

    @staticmethod
    def _extract_eplan_function(layers):
        """PlanGML satırını alan adına göre okur; kod/sıfır sentinel değerlerini yaymaz."""
        candidates = []
        for layer in layers:
            table_name = str(layer.get("tableName") or "")
            if table_name.endswith("plan_siniri"):
                continue
            columns = layer.get("columns") or []
            for row in layer.get("data") or []:
                if not isinstance(row, list):
                    continue
                values = dict(zip(columns, row))
                candidates.append((table_name, values))
        if not candidates:
            return {}, []

        table_name, values = candidates[0]
        purpose = values.get("Adı")
        if not purpose:
            purpose = next(
                (value for key, value in values.items() if key.endswith(" Tipi") and value not in (None, "", "0", 0)),
                None,
            )
        building_order = values.get("Yapı Düzeni")
        if building_order in (None, "", "0", 0):
            building_order = None
        extracted = {
            "imar_durumu": "PlanGML fonksiyon alanı",
            "plan_fonksiyon": purpose,
            "kaks_emsal": positive_source_number(values.get("Emsal Kaks")),
            "taks": positive_source_number(values.get("TAKS")),
            "gabari": positive_source_number(values.get("Yapı Yüksekliği")),
            "kat_adedi": positive_source_number(values.get("Kat Adedi")),
            "yapi_nizami": building_order,
            "on_bahce": positive_source_number(values.get("Ön Bahçe Mesafesi")),
            "yan_bahce": positive_source_number(values.get("Yan Bahçe Mesafesi")),
        }
        return extracted, [
            {"katman": name, "alanlar": row_values}
            for name, row_values in candidates
        ]

    def fetch_eplan_and_zoning(self, il, ilce, mahalle, ada, parsel, mahalle_id=None, lat=None, lon=None):
        """Resmi E-Plan plan kapsamını ve varsa PlanGML yapılaşma alanlarını çeker.

        E-Plan'da bulunmayan KAKS/TAKS tahmin edilmez. Plan kaydı ile parsel
        yapılaşma koşulları ayrı güven durumları olarak döndürülür.
        """
        if lat in (None, "") or lon in (None, ""):
            return self._empty_zoning_result("koordinat_eksik", "E-Plan sorgusu için koordinat gerekli")
        try:
            plans = self._query_eplan_plans(lat, lon)
        except Exception as exc:
            return self._empty_zoning_result(error=str(exc))
        if not plans:
            return self._empty_zoning_result("plan_bulunamadi")

        def priority(plan):
            try:
                scale = int(float(plan.get("olcek") or 999999))
            except (TypeError, ValueError):
                scale = 999999
            is_application = "uygulama" in normalize_tr(plan.get("plan_turu"))
            return (not plan.get("aktif"), not is_application, scale, str(plan.get("kayit_tarihi") or ""))

        active_plans = [plan for plan in plans if plan.get("aktif")] or plans
        active_plans.sort(key=priority)
        # Aynı noktayı kapsayan en yeni 1/1000 planı öne almak için eşit öncelikte
        # kayıt tarihini tersten uygularız.
        active_plans = sorted(
            active_plans,
            key=lambda plan: str(plan.get("kayit_tarihi") or ""),
            reverse=True,
        )
        active_plans.sort(key=lambda plan: priority(plan)[:3])

        chosen = active_plans[0]
        function_fields = {}
        function_layers = []
        for plan in active_plans[:4]:
            try:
                layers = self._query_eplan_function(plan.get("record_id"), lat, lon)
            except Exception:
                continue
            extracted, normalized_layers = self._extract_eplan_function(layers)
            if normalized_layers:
                chosen = plan
                function_fields = extracted
                function_layers = normalized_layers
                break

        has_parameters = any(function_fields.get(key) is not None for key in (
            "plan_fonksiyon", "kaks_emsal", "taks", "gabari", "kat_adedi", "on_bahce", "yan_bahce"
        ))
        return {
            **self._empty_zoning_result(),
            **function_fields,
            "success": True,
            "veri_durumu": "imar_alani_kismen_dogrulandi" if has_parameters else "plan_kapsami_dogrulandi",
            "kaynak": "ÇŞİDB E-Plan",
            "hata": None,
            "plan_adi": chosen.get("plan_adi"),
            "plan_turu": chosen.get("plan_turu"),
            "pin_tucbs_no": chosen.get("pin_tucbs_no"),
            "onay_tarihi": None,
            "yururluk_tarihi": None,
            "plan_sureci": chosen.get("onay_durumu"),
            "plan_kayit_tarihi": chosen.get("kayit_tarihi"),
            "plan_olcegi": chosen.get("olcek"),
            "planlar": active_plans,
            "fonksiyon_katmanlari": function_layers,
        }

    def fetch_bagimsiz_bolumler(self, mahalle_id, ada, parsel, zemin_durumu="Kat Mülkiyet", alan_m2=500.0):
        """Doğrulanmış bir bağımsız-bölüm kaynağı henüz bağlı değil.

        Parsel alanından daire/dükkân adedi türetmek sicil verisi değildir; bu
        nedenle güvenilir bir kaynak eklenene kadar boş liste döndürülür.
        """
        return []

    def save_to_db(self, p_data, i_data, bb_list):
        conn = get_db_connection(self.db_path)
        c = conn.cursor()
        
        # 1. Parsel & İmar
        c.execute("""
        INSERT OR REPLACE INTO parsel_imar_kayitlari (
            il, ilce, mahalle, mahalle_id, ada_no, parsel_no,
            alan_m2, nitelik, zemin_durumu, pafta, mevkii,
            enlem, boylam, poligon_geojson,
            imar_durumu, plan_fonksiyon, kaks_emsal, taks, gabari,
            kat_adedi, yapi_nizami, on_bahce, yan_bahce,
            plan_adi, plan_turu, pin_tucbs_no, onay_tarihi,
            yururluk_tarihi, plan_sureci, dogal_sit, havaalani_mania, hazine_durumu,
            kaynak
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?
        )
        """, (
            p_data["il"], p_data["ilce"], p_data["mahalle"], p_data["mahalle_id"],
            p_data["ada_no"], p_data["parsel_no"], p_data["alan_m2"],
            p_data["nitelik"], p_data["zemin_durumu"], p_data["pafta"], p_data["mevkii"],
            p_data["enlem"], p_data["boylam"], json.dumps(p_data.get("geometry", {})),
            i_data.get("imar_durumu"), i_data.get("plan_fonksiyon"), i_data.get("kaks_emsal"), i_data.get("taks"),
            i_data.get("gabari"), i_data.get("kat_adedi"), i_data.get("yapi_nizami"),
            i_data.get("on_bahce"), i_data.get("yan_bahce"),
            i_data.get("plan_adi"), i_data.get("plan_turu"), i_data.get("pin_tucbs_no"),
            i_data.get("onay_tarihi"), i_data.get("yururluk_tarihi"), i_data.get("plan_sureci"),
            i_data.get("dogal_sit"), i_data.get("havaalani_mania"), i_data.get("hazine_durumu"),
            "TKGM MEGSİS" + (" + ÇŞİDB E-Plan" if i_data.get("success") else "")
        ))
        
        # 2. Bağımsız Bölümler
        for bb in bb_list:
            c.execute("""
            INSERT OR REPLACE INTO bagimsiz_bolumler (
                mahalle_id, ada_no, parsel_no, bolum_no, kat, giris, nitelik, blok, kaynak
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                p_data["mahalle_id"], p_data["ada_no"], p_data["parsel_no"],
                bb["bolum_no"], bb["kat"], bb["giris"], bb["nitelik"], bb["blok"],
                "TKGM Bağımsız Bölüm Sicili"
            ))
            
        # 3. İmar Değişiklikleri & Askı
        for aski in i_data.get("aski_degisiklikleri", []):
            c.execute("""
            INSERT INTO imar_degisiklik_ve_askilar (
                il, ilce, mahalle, ada_no, parsel_no, degisiklik_turu, plan_kodu, aciklama
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                p_data["il"], p_data["ilce"], p_data["mahalle"],
                p_data["ada_no"], p_data["parsel_no"],
                "İmar Planı Tadilatı", aski.get("kod", "TADİLAT"), aski.get("aciklama", "Plan Revizyonu")
            ))
            
        conn.commit()
        conn.close()

    def process_parsel(self, mahalle_id=None, ada=None, parsel=None, il=None, ilce=None, mahalle=None, lat=None, lon=None, persist=True):
        """Parseli sorgular, imar durumunu ve bağımsız bölümlerini kaydeder."""
        p_data = self.fetch_tkgm_megsis(mahalle_id=mahalle_id, ada=ada, parsel=parsel, lat=lat, lon=lon)
        if not p_data:
            return None
            
        il_ad = p_data["il"] or il or "Ankara"
        ilce_ad = p_data["ilce"] or ilce or "Çankaya"
        mah_ad = p_data["mahalle"] or mahalle or "Aziziye"
        ada_no = p_data["ada_no"] or ada or "1"
        parsel_no = p_data["parsel_no"] or parsel or "1"
        mah_id = p_data["mahalle_id"] or mahalle_id
        
        i_data = self.fetch_eplan_and_zoning(
            il_ad, ilce_ad, mah_ad, ada_no, parsel_no, mah_id,
            lat=p_data.get("enlem"), lon=p_data.get("boylam"),
        )
        bb_list = self.fetch_bagimsiz_bolumler(
            mah_id, ada_no, parsel_no,
            p_data.get("zemin_durumu", ""),
            p_data.get("alan_m2", 500)
        )
        
        if persist:
            self.save_to_db(p_data, i_data, bb_list)
        return {
            "parsel": p_data,
            "imar": i_data,
            "bagimsiz_bolumler": bb_list,
            "veri_guveni": {
                "kadastro": "kaynakta_dogrulandi",
                "imar": i_data.get("veri_durumu", "kaynak_erisilemedi"),
                "bagimsiz_bolum": "kaynak_bagli_degil",
            },
        }

    def enrich_from_csv(self, csv_file_path, limit=50):
        """Mevcut toplanan arsa/konut CSV ilanlarından ada/parselleri otomatik çeker."""
        if not os.path.exists(csv_file_path):
            print(f"[!] CSV dosyası bulunamadı: {csv_file_path}")
            return
            
        print(f"[*] {csv_file_path} dosyasından ada/parseller ve koordinatlar taranıyor...")
        count = 0
        with open(csv_file_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mahalle_id = row.get("Mahalle ID")
                ada = row.get("Ada No")
                parsel = row.get("Parsel No")
                il = row.get("İl")
                ilce = row.get("İlçe")
                mahalle = row.get("Mahalle")
                lat = float(row.get("İlan Pin Lat")) if row.get("İlan Pin Lat") else None
                lon = float(row.get("İlan Pin Lon")) if row.get("İlan Pin Lon") else None
                
                if (mahalle_id and ada and parsel) or (lat and lon):
                    print(f"--> [{count+1}/{limit}] {il}/{ilce}/{mahalle} - Ada {ada}, Parsel {parsel} (GPS: {lat},{lon}) çekiliyor...")
                    res = self.process_parsel(mahalle_id=mahalle_id, ada=ada, parsel=parsel, il=il, ilce=ilce, mahalle=mahalle, lat=lat, lon=lon)
                    if res:
                        count += 1
                        p = res["parsel"]
                        i = res["imar"]
                        print(f"    [OK] {p['il']}/{p['ilce']}/{p['mahalle']} - Ada {p['ada_no']}/{p['parsel_no']} ({p['alan_m2']} m², {p['nitelik']}) -> Fonksiyon: {i['plan_fonksiyon']}, KAKS: {i['kaks_emsal']}, TAKS: {i['taks']}")
                        time.sleep(0.3)
                if count >= limit:
                    break
        print(f"[✔] Toplam {count} parsel imar ve bağımsız bölüm verileriyle zenginleştirildi!")

    def export_geojson(self, output_path=None):
        """Tüm toplanan parsellerin sınır koordinatlarını ve niteliklerini haritada gösterilmek üzere GeoJSON olarak dışa aktarır."""
        output_file = output_path or self.geojson_path
            
        conn = get_db_connection(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM parsel_imar_kayitlari WHERE poligon_geojson IS NOT NULL ORDER BY id DESC")
        rows = c.fetchall()
        features = []
        
        for r in rows:
            r_dict = dict(r)
            geom_str = r_dict.get("poligon_geojson")
            if not geom_str:
                continue
            try:
                geom = json.loads(geom_str)
            except Exception:
                continue
                
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": r_dict.get("id"),
                    "il": r_dict.get("il"),
                    "ilce": r_dict.get("ilce"),
                    "mahalle": r_dict.get("mahalle"),
                    "ada_no": r_dict.get("ada_no"),
                    "parsel_no": r_dict.get("parsel_no"),
                    "alan_m2": r_dict.get("alan_m2"),
                    "nitelik": r_dict.get("nitelik"),
                    "zemin_durumu": r_dict.get("zemin_durumu"),
                    "imar_durumu": r_dict.get("imar_durumu"),
                    "plan_fonksiyon": r_dict.get("plan_fonksiyon"),
                    "kaks_emsal": r_dict.get("kaks_emsal"),
                    "taks": r_dict.get("taks"),
                    "gabari": r_dict.get("gabari"),
                    "kat_adedi": r_dict.get("kat_adedi")
                }
            })
            
        fc = {"type": "FeatureCollection", "features": features}
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False, indent=2)
        print(f"[✔] GeoJSON harita katmanı oluşturuldu: {output_file} ({len(features)} parsel)")
        conn.close()
        return len(features)

    def auto_discover_and_expand(self, limit=100, iller=None):
        """
        SIFIR MANUEL GİRİŞ:
        Türkiye geneli toplanan ilanlardan ve mahallelerden tohum adaları alır.
        Her adanın içindeki TÜM parselleri (1, 2, 3, 4, 5... N) ardışık olarak
        otomatik sorgular ve sınır GeoJSON poligonlarıyla birlikte kaydeder.
        """
        target_iller = [normalize_tr(x) for x in iller.split(",") if x.strip()] if iller else None
        
        print("=" * 70)
        print("🚀 GEOPROP AI - OTONOM ADA & PARSEL KEŞİF MOTORU (MANUEL GİRİŞSİZ)")
        if target_iller:
            print(f"🎯 Hedef İller Filtresi: {', '.join(target_iller)}")
        print("=" * 70)
        
        seed_items = []
        csv_path = os.path.join(BASE_DIR, "data", "csv_ciktilari", "satilik_arsa_ilanlari.csv")
        seen_coords = set()
        
        # 1. Mevcut Arsa İlanlarından Çek
        if os.path.exists(csv_path):
            with open(csv_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    il = row.get("İl", "")
                    if target_iller and normalize_tr(il) not in target_iller:
                        continue
                        
                    lat_str = row.get("İlan Pin Lat")
                    lon_str = row.get("İlan Pin Lon")
                    ilce = row.get("İlçe")
                    mahalle = row.get("Mahalle")
                    ada = row.get("Ada No")
                    mid = row.get("Mahalle ID")
                    
                    if lat_str and lon_str:
                        lat_val = round(float(lat_str), 4)
                        lon_val = round(float(lon_str), 4)
                        if (lat_val, lon_val) not in seen_coords:
                            seen_coords.add((lat_val, lon_val))
                            seed_items.append({
                                "lat": float(lat_str),
                                "lon": float(lon_str),
                                "ada": ada,
                                "mahalle_id": int(mid) if mid and str(mid).isdigit() else None,
                                "il": il,
                                "ilce": ilce,
                                "mahalle": mahalle
                            })
        
        # 2. Mahalle Koordinatları Veri Tabanından Destek Al (Her il için garanti tohum)
        mahalle_json_path = os.path.join(BASE_DIR, "mahalle_koordinatlari.json")
        if os.path.exists(mahalle_json_path):
            try:
                with open(mahalle_json_path, "r", encoding="utf-8") as f:
                    all_mahalles = json.load(f)
                    city_counts = {}
                    for k, v in all_mahalles.items():
                        city = k.split("_")[0]
                        norm_city = normalize_tr(city)
                        if target_iller and norm_city not in target_iller:
                            continue
                            
                        city_counts[norm_city] = city_counts.get(norm_city, 0) + 1
                        if city_counts[norm_city] <= 15: # Her ilden 15 farklı mahalle tohumu
                            lat_v = float(v["lat"])
                            lon_v = float(v["lon"])
                            if (round(lat_v, 4), round(lon_v, 4)) not in seen_coords:
                                seen_coords.add((round(lat_v, 4), round(lon_v, 4)))
                                seed_items.append({
                                    "lat": lat_v,
                                    "lon": lon_v,
                                    "ada": None,
                                    "mahalle_id": int(v["id"]) if str(v["id"]).isdigit() else None,
                                    "il": city.capitalize(),
                                    "ilce": k.split("_")[1].capitalize() if len(k.split("_")) > 1 else "",
                                    "mahalle": v.get("name", "")
                                })
            except Exception as e:
                print(f"[!] Mahalle koordinatları okunurken hata: {e}")
                
        # Eğer hiç tohum bulunamazsa varsayılan büyükşehir tohumları
        if not seed_items:
            seed_items = [
                {"lat": 39.8892, "lon": 32.8633, "ada": "6103", "mahalle_id": 1156, "il": "Ankara", "ilce": "Çankaya", "mahalle": "Aziziye"},
                {"lat": 39.9015, "lon": 32.8540, "ada": "2510", "mahalle_id": 1155, "il": "Ankara", "ilce": "Çankaya", "mahalle": "Ayrancı"},
                {"lat": 41.0082, "lon": 28.9784, "ada": "101", "mahalle_id": None, "il": "İstanbul", "ilce": "Fatih", "mahalle": "Merkez"}
            ]
            
        print(f"[*] {len(seed_items)} farklı coğrafi tohum noktası belirlendi. Ada içi tüm parseller zincirleme taranıyor...")
        
        total_parsel_count = 0
        for item in seed_items:
            lat = item.get("lat")
            lon = item.get("lon")
            il = item.get("il")
            ilce = item.get("ilce")
            mah = item.get("mahalle")
            mid = item.get("mahalle_id")
            ada = item.get("ada")
            
            # 1. Önce resmi TKGM kaydını çöz ve doğrula
            seed_res = self.process_parsel(
                mahalle_id=mid,
                ada=ada,
                parsel=None,
                il=il,
                ilce=ilce,
                mahalle=mah,
                lat=lat,
                lon=lon
            )
            
            if not seed_res:
                continue
                
            p_seed = seed_res["parsel"]
            official_mid = p_seed.get("mahalle_id") or mid
            official_ada = str(p_seed.get("ada_no") or ada)
            il = p_seed.get("il") or il
            ilce = p_seed.get("ilce") or ilce
            mah = p_seed.get("mahalle") or mah
            
            total_parsel_count += 1
            print(f"\n📁 [{il}/{ilce}/{mah}] Ada {official_ada} keşfedildi (TKGM Mahalle ID: {official_mid}). Tüm parseller taranıyor...")
            miss_count = 0
            ada_parsel_count = 1
            
            # Parsel 1'den başlayarak ardışık keşfet (arka arkaya 5 boş gelene kadar)
            for p_num in range(1, 100):
                parsel_str = str(p_num)
                if parsel_str == str(p_seed.get("parsel_no")):
                    continue
                    
                res = self.process_parsel(
                    mahalle_id=official_mid,
                    ada=official_ada,
                    parsel=parsel_str,
                    il=il,
                    ilce=ilce,
                    mahalle=mah
                )
                
                if res:
                    ada_parsel_count += 1
                    total_parsel_count += 1
                    miss_count = 0
                    p = res["parsel"]
                    i = res["imar"]
                    imar_ozeti = (
                        f"Fonk: {i.get('plan_fonksiyon')} (KAKS: {i.get('kaks_emsal')})"
                        if i.get("success") else "İmar: doğrulanamadı"
                    )
                    print(f"  --> [PARSEL {parsel_str}] {p['alan_m2']} m² | {p['nitelik']} | {imar_ozeti} | Geo: OK")
                    time.sleep(0.12)
                else:
                    miss_count += 1
                    if miss_count >= 5 and p_num > 10:
                        break
                        
                if total_parsel_count >= limit:
                    break
                    
            print(f"  ✔ Ada {official_ada} tamamlandı: {ada_parsel_count} parsel geometrisi ve imar durumu çıkarıldı.")
            if total_parsel_count >= limit:
                print(f"\n[!] Belirtilen limit ({limit}) adedine ulaşıldı.")
                break
                
        print(f"\n" + "=" * 70)
        print(f"✅ OTONOM KEŞİF TAMAMLANDI: Toplam {total_parsel_count} Parsel Çekildi!")
        print("=" * 70)

    def export_summary_json(self):
        """Veritabanındaki kayıtları web demo ve GitHub Actions için JSON olarak dışa aktarır."""
        conn = get_db_connection(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM parsel_imar_kayitlari ORDER BY id DESC LIMIT 500")
        rows = [dict(r) for r in c.fetchall()]
        
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"[✔] JSON Özeti oluşturuldu: {self.json_path} ({len(rows)} kayıt)")
        conn.close()
        
        # Harita için GeoJSON çıktısını da güncelle
        self.export_geojson()

def birlestir_tum_sonuclari(data_dir=DATA_DIR):
    """
    Tüm paralel runner'ların ürettiği parsel_imar_degisiklikleri_*.sqlite ve
    turkiye_parseller_geo_*.geojson dosyalarını tek bir nihai veritabanı ve
    harita katmanında birleştirir.
    """
    print("=" * 70)
    print("🔄 PARALEL RUNNER SONUÇLARINI BİRLEŞTİRME İŞLEMİ BAŞLATILDI")
    print("=" * 70)
    init_database(DB_PATH)
    main_conn = get_db_connection(DB_PATH)
    main_cur = main_conn.cursor()
    
    # 1. SQLite Birleştirme
    sqlite_files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.startswith("parsel_imar_degisiklikleri_") and f.endswith(".sqlite") and f != os.path.basename(DB_PATH)]
    print(f"[*] Bulunan parçalı SQLite veritabanı sayısı: {len(sqlite_files)}")
    
    total_parsel_eklendi = 0
    total_bb_eklendi = 0
    
    for sf in sqlite_files:
        print(f"  -> {os.path.basename(sf)} birleştiriliyor...")
        try:
            s_conn = sqlite3.connect(sf)
            s_conn.row_factory = sqlite3.Row
            s_cur = s_conn.cursor()
            
            s_cur.execute("SELECT * FROM parsel_imar_kayitlari")
            p_rows = [dict(r) for r in s_cur.fetchall()]
            for p in p_rows:
                p.pop("id", None)
                cols = list(p.keys())
                placeholders = ":" + ", :".join(cols)
                main_cur.execute(f"INSERT OR IGNORE INTO parsel_imar_kayitlari ({', '.join(cols)}) VALUES ({placeholders})", p)
                total_parsel_eklendi += 1
                
            try:
                s_cur.execute("SELECT * FROM bagimsiz_bolumler")
                bb_rows = [dict(r) for r in s_cur.fetchall()]
                for bb in bb_rows:
                    bb.pop("id", None)
                    cols = list(bb.keys())
                    placeholders = ":" + ", :".join(cols)
                    main_cur.execute(f"INSERT OR IGNORE INTO bagimsiz_bolumler ({', '.join(cols)}) VALUES ({placeholders})", bb)
                    total_bb_eklendi += 1
            except Exception:
                pass
            s_conn.close()
        except Exception as e:
            print(f"  [!] Hata ({sf}): {e}")
            
    main_conn.commit()
    main_conn.close()
    print(f"[✔] SQLite birleştirme tamamlandı! ({total_parsel_eklendi} parsel aktarıldı)")
    
    # 2. GeoJSON Birleştirme
    geojson_files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.startswith("turkiye_parseller_geo_") and f.endswith(".geojson") and f != os.path.basename(GEOJSON_OUTPUT_PATH)]
    print(f"[*] Bulunan parçalı GeoJSON dosya sayısı: {len(geojson_files)}")
    all_features = []
    seen_keys = set()
    
    # Mevcut ana GeoJSON varsa al
    if os.path.exists(GEOJSON_OUTPUT_PATH):
        try:
            with open(GEOJSON_OUTPUT_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                for feat in d.get("features", []):
                    key = (feat.get("properties", {}).get("il"), str(feat.get("properties", {}).get("ada_no")), str(feat.get("properties", {}).get("parsel_no")))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_features.append(feat)
        except Exception:
            pass
            
    for gf in geojson_files:
        try:
            with open(gf, "r", encoding="utf-8") as f:
                d = json.load(f)
                for feat in d.get("features", []):
                    key = (feat.get("properties", {}).get("il"), str(feat.get("properties", {}).get("ada_no")), str(feat.get("properties", {}).get("parsel_no")))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_features.append(feat)
        except Exception as e:
            print(f"  [!] GeoJSON okuma hatası ({gf}): {e}")
            
    with open(GEOJSON_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": all_features}, f, ensure_ascii=False, indent=2)
    print(f"[✔] Birleşik GeoJSON harita katmanı yazıldı: {GEOJSON_OUTPUT_PATH} ({len(all_features)} parsel)")
    
    # 3. Özet JSON oluştur
    conn = get_db_connection(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM parsel_imar_kayitlari ORDER BY id DESC LIMIT 5000")
    rows = [dict(r) for r in c.fetchall()]
    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    conn.close()
    print(f"[✔] Birleşik Özet JSON yazıldı: {JSON_OUTPUT_PATH} ({len(rows)} kayıt)")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="GEOPROP AI İmar, Plan Künyesi ve Bağımsız Bölüm Toplayıcı")
    parser.add_argument("--otomatik-kesif", action="store_true", help="Tohum noktalar çevresinde sınırlı ada/parsel keşfi yap")
    parser.add_argument("--iller", type=str, help="Virgülle ayrılmış il listesi (örn: 'ankara,istanbul,izmir')")
    parser.add_argument("--cikis-ek", type=str, help="Çıktı dosyası grup eki (örn: 'grup_1')")
    parser.add_argument("--birlestir", action="store_true", help="Parçalı sonuçları birleştir")
    parser.add_argument("--mahalle-id", type=int, help="TKGM Mahalle ID")
    parser.add_argument("--ada", type=str, help="Ada No")
    parser.add_argument("--parsel", type=str, help="Parsel No")
    parser.add_argument("--il", type=str, default="Ankara")
    parser.add_argument("--ilce", type=str, default="Çankaya")
    parser.add_argument("--mahalle", type=str, default="Aziziye")
    parser.add_argument("--zenginlestir-csv", action="store_true", help="Arsa CSV'sindeki parselleri topla")
    parser.add_argument("--limit", type=int, default=100, help="Maksimum işlenecek parsel sayısı")
    
    args = parser.parse_args()

    if not (args.birlestir or args.otomatik_kesif or args.zenginlestir_csv or (args.mahalle_id and args.ada and args.parsel)):
        parser.print_help()
        return
    
    if args.birlestir:
        birlestir_tum_sonuclari()
        return

    toplayici = ParselImarToplayici(cikis_ek=args.cikis_ek)
    
    if args.otomatik_kesif:
        print(f"[*] Sınırlı Ada/Parsel Keşif ve Geo Harita Çıkarımı Başlatılıyor (Ek: {args.cikis_ek or 'ana'})...")
        toplayici.auto_discover_and_expand(limit=args.limit, iller=args.iller)
        toplayici.export_summary_json()
    elif args.zenginlestir_csv:
        csv_path = os.path.join(BASE_DIR, "data", "csv_ciktilari", "satilik_arsa_ilanlari.csv")
        toplayici.enrich_from_csv(csv_path, limit=args.limit)
        toplayici.export_summary_json()
    elif args.mahalle_id and args.ada and args.parsel:
        res = toplayici.process_parsel(args.mahalle_id, args.ada, args.parsel, args.il, args.ilce, args.mahalle)
        if res:
            print(f"[✔] Başarıyla toplandı: {args.il}/{args.ilce} Ada {args.ada} Parsel {args.parsel}")
            toplayici.export_summary_json()

if __name__ == "__main__":
    main()
