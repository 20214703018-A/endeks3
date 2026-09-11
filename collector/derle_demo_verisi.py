#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Demo Dashboard Veri Derleyicisi
-------------------------------
SQLite veritabanından ve poligon klasöründen İstanbul (34) il geneli,
39 ilçe ve tüm mahallelerin verilerini tek bir zengin ve optimize JSON
dosyasına (demo/data/istanbul.json) derler.
"""

import json
import sqlite3
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEMO_DIR = BASE_DIR / "demo"
DATA_DIR = DEMO_DIR / "data"
DEFAULT_DB_PATH = BASE_DIR / "collector/data/piyasa_verileri.db"
DEFAULT_POLY_DIR = BASE_DIR / "collector/data/poligonlar"

def parse_ham_json(ham):
    if not ham: return {}
    try:
        d = json.loads(ham)
    except Exception:
        return {}

    return {
        "e_ticaret": {
            "online_pazaryeri_tl": d.get("OnlineRetailOnlyMarketplace") or 0,
            "online_giyim_tl": d.get("MultichannelRetailClothingShoes") or 0,
            "online_elektronik_tl": d.get("MultichannelRetailElectronics") or 0,
            "online_tatil_tl": d.get("OnlineVacationTravel") or 0,
            "online_bahis_tl": d.get("OnlineLegalBetting") or 0,
            "ev_dekorasyon_tl": d.get("MultichannelRetailHomeDecoration") or 0,
            "kullanici_sayisi": d.get("ECommerceCount") or 0
        },
        "harcamalar": {
            "gida": d.get("ExpenseFood") or 0,
            "barinma_kira": d.get("ExpenseShelter") or 0,
            "ulasim": d.get("ExpenseTransportation") or 0,
            "restoran": d.get("ExpenseRestaurant") or 0,
            "giyim": d.get("ExpenseClothing") or 0,
            "saglik": d.get("ExpenseHealth") or 0,
            "egitim": d.get("ExpenseEducation") or 0,
            "eglence": d.get("ExpenseEntertainment") or 0,
            "toplam": d.get("ExpenseTotal") or 0,
            "tasarruf": d.get("SavingTotal") or 0
        },
        "medeni_durum": {
            "evli": d.get("Married") or 0,
            "bekar": d.get("MarriedNever") or 0,
            "bosanmis": d.get("Divorced") or 0,
            "dul": d.get("Widow") or 0
        }
    }

def compile_data(db_path=DEFAULT_DB_PATH, poly_dir=DEFAULT_POLY_DIR, output_dir=DATA_DIR):
    db_path = Path(db_path)
    poly_dir = Path(poly_dir)
    output_dir = Path(output_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f"Veritabanı bulunamadı: {db_path}")
    if not poly_dir.is_dir():
        raise FileNotFoundError(f"Poligon klasörü bulunamadı: {poly_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Veritabanı okunuyor: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    # 1. İlçe Poligon Haritası
    ilce_poligonlari = {}
    city_poly_file = poly_dir / "city_34.json"
    if city_poly_file.exists():
        with open(city_poly_file) as f:
            cp = json.load(f)
            for p in cp.get("polygons", []):
                cid = p.get("properties", {}).get("CountyId") or p.get("id")
                if cid:
                    ilce_poligonlari[int(cid)] = p.get("coordinates")

    # 2. Mahalle Poligon Haritası
    mahalle_poligonlari = {}
    for county_file in poly_dir.glob("county_34_*.json"):
        try:
            with open(county_file) as f:
                d = json.load(f)
                for p in d.get("polygons", []):
                    pid = p.get("id") or p.get("properties", {}).get("DistrictId")
                    if pid and str(pid).isdigit() and int(pid) > 0:
                        mahalle_poligonlari[int(pid)] = {
                            "coordinates": p.get("coordinates"),
                            "name": p.get("properties", {}).get("description")
                        }
        except Exception:
            pass

    print(f"  ✓ {len(ilce_poligonlari)} ilçe, {len(mahalle_poligonlari)} mahalle poligonu yüklendi.")

    # 3. İLÇE VE MAHALLE LİSTELERİ
    cur.execute("SELECT county_id, county_name FROM ilceler WHERE city_id = 34 ORDER BY county_name")
    ilceler_raw = cur.fetchall()

    out = {
        "il": {
            "id": 34,
            "name": "İstanbul",
            "toplam_ilce": len(ilceler_raw)
        },
        "ilceler": [],
        "mahalleler": {}
    }

    for county_id, county_name in ilceler_raw:
        # İlçe Demografi
        cur.execute("""
        SELECT nufus_toplam, nufus_erkek, nufus_kadin, hane_sayisi, ortalama_hane_geliri, toplam_hane_geliri,
               ev_sahibi_orani, kiraci_orani, ses_a_plus, ses_a, ses_b, ses_c, ses_d,
               ses_a_plus_oran, ses_a_oran, ses_b_oran, ses_c_oran, ses_d_oran,
               egitim_universite_oran, egitim_lise_oran, egitim_ortaokul_oran, egitim_ilkokul_oran,
               yas_genc_oran, yas_orta_oran, yas_yasli_oran,
               konut_fiyat_m2, kira_fiyat_m2, arsa_fiyat_m2, ticari_fiyat_m2,
               eczane_sayisi, atm_sayisi, banka_sube_sayisi, ham_json
        FROM demografi WHERE city_id = 34 AND county_id = ? AND seviye = 'ilce' LIMIT 1
        """, (county_id,))
        d_row = cur.fetchone()

        # İlçe Fiyat Özet
        cur.execute("""
        SELECT satilik_m2_fiyat, kiralik_m2_fiyat, ortalama_fiyat, amortisman_yil, brut_kira_getirisi,
               ortalama_bina_yasi, satilik_kalma_suresi_gun, kiralik_kalma_suresi_gun, ilan_sayisi, yillik_fiyat_degisim
        FROM fiyat_ozet WHERE city_id = 34 AND county_id = ? AND seviye = 'ilce' AND kategori = 'konut' LIMIT 1
        """, (county_id,))
        f_row = cur.fetchone()

        # İlçe Fiyat Trendi (Son 36 Ay)
        cur.execute("""
        SELECT ay, satilik_m2_fiyat, kiralik_m2_fiyat, projeksiyon
        FROM fiyat_trend WHERE city_id = 34 AND county_id = ? AND seviye = 'ilce' AND kategori = 'konut'
        ORDER BY ay ASC
        """, (county_id,))
        trend_rows = [{"ay": r[0], "satilik": r[1], "kiralik": r[2], "projeksiyon": r[3]} for r in cur.fetchall()]

        # İlçe Kırılımlar (Bina Yaşı, Oda Sayısı)
        cur.execute("""
        SELECT dagilim_turu, segment, oran, satilik_m2_fiyat, ilan_sayisi
        FROM fiyat_dagilim WHERE city_id = 34 AND county_id = ? AND seviye = 'ilce' AND kategori = 'konut'
        """, (county_id,))
        kirilimlar = {"yas": [], "oda": [], "kat": [], "isitma": []}
        for dt, seg, oran, m2, cnt in cur.fetchall():
            if dt in kirilimlar:
                kirilimlar[dt].append({"segment": seg, "oran": oran or 0, "m2": m2 or 0, "ilan": cnt or 0})

        # İlçe Seçim Sonuçları
        cur.execute("""
        SELECT secim_adi, kazanan_parti, sandik_sayisi, kayitli_secmen, kullanilan_oy, gecerli_oy, parti_sonuclari_json
        FROM secim_sonuclari WHERE city_id = 34 AND county_id = ? AND seviye = 'ilce'
        ORDER BY id DESC LIMIT 1
        """, (county_id,))
        s_row = cur.fetchone()
        secim_data = {}
        if s_row:
            partiler = []
            try:
                raw_p = json.loads(s_row[6]) if s_row[6] else []
                for p in raw_p:
                    partiler.append({"parti": p.get("Secenek"), "oy": p.get("OySayisi"), "oran": p.get("Oran")})
            except Exception:
                pass
            secim_data = {
                "secim_adi": s_row[0], "kazanan": s_row[1], "sandik": s_row[2],
                "secmen": s_row[3], "kullanilan_oy": s_row[4], "gecerli_oy": s_row[5],
                "partiler": sorted(partiler, key=lambda x: x.get("oy") or 0, reverse=True)[:6]
            }

        # İlçe Hemşehri Dağılımı (Top 8)
        cur.execute("""
        SELECT kutuk_ili, kisi_sayisi FROM hemsehri
        WHERE city_id = 34 AND county_id = ? AND seviye = 'ilce'
        ORDER BY kisi_sayisi DESC LIMIT 8
        """, (county_id,))
        hemsehri_list = [{"il": r[0], "kisi": r[1]} for r in cur.fetchall()]

        # İlçe POI Donatı Dağılımı
        cur.execute("""
        SELECT alt_kategori, count(*) FROM poi_noktalari
        WHERE city_id = 34 AND county_id = ?
        GROUP BY alt_kategori ORDER BY count(*) DESC LIMIT 10
        """, (county_id,))
        poi_list = [{"kategori": r[0], "adet": r[1]} for r in cur.fetchall()]

        ham_info = parse_ham_json(d_row[32] if d_row else None)

        # İlçe Objesi
        ilce_obj = {
            "id": county_id,
            "name": county_name,
            "poligon": ilce_poligonlari.get(county_id),
            "nufus": d_row[0] if d_row else None,
            "hane_sayisi": d_row[3] if d_row else None,
            "hane_geliri": d_row[4] if d_row else None,
            "ev_sahibi_orani": d_row[6] if d_row else None,
            "kiraci_orani": d_row[7] if d_row else None,
            "ses": {
                "a_plus": d_row[8] if d_row else 0, "a": d_row[9] if d_row else 0,
                "b": d_row[10] if d_row else 0, "c": d_row[11] if d_row else 0, "d": d_row[12] if d_row else 0,
                "a_plus_oran": d_row[13] if d_row else 0, "a_oran": d_row[14] if d_row else 0,
                "b_oran": d_row[15] if d_row else 0, "c_oran": d_row[16] if d_row else 0, "d_oran": d_row[17] if d_row else 0
            },
            "egitim": {
                "universite": d_row[18] if d_row else 0, "lise": d_row[19] if d_row else 0,
                "ortaokul": d_row[20] if d_row else 0, "ilkokul": d_row[21] if d_row else 0
            },
            "yas_orani": {
                "genc": d_row[22] if d_row else 0, "orta": d_row[23] if d_row else 0, "yasli": d_row[24] if d_row else 0
            },
            "fiyat": {
                "satilik_m2": f_row[0] if f_row and f_row[0] else (d_row[25] if d_row else None),
                "kiralik_m2": f_row[1] if f_row and f_row[1] else (d_row[26] if d_row else None),
                "ortalama_fiyat": f_row[2] if f_row else None,
                "amortisman_yil": f_row[3] if f_row else None,
                "kira_getirisi": f_row[4] if f_row else None,
                "ortalama_bina_yasi": f_row[5] if f_row else None,
                "ilan_sayisi": f_row[8] if f_row else None,
                "yillik_degisim": f_row[9] if f_row else None,
                "arsa_m2": d_row[27] if d_row else None,
                "ticari_m2": d_row[28] if d_row else None
            },
            "e_ticaret": ham_info.get("e_ticaret", {}),
            "harcamalar": ham_info.get("harcamalar", {}),
            "medeni_durum": ham_info.get("medeni_durum", {}),
            "trend": trend_rows,
            "kirilimlar": kirilimlar,
            "secim": secim_data,
            "hemsehri": hemsehri_list,
            "poi": poi_list
        }
        out["ilceler"].append(ilce_obj)

        # 4. İLÇENİN MAHALLELERİ
        cur.execute("SELECT district_id, district_name FROM mahalleler WHERE city_id = 34 AND county_id = ? ORDER BY district_name", (county_id,))
        mahalleler_raw = cur.fetchall()
        out["mahalleler"][county_id] = []

        for dist_id, dist_name in mahalleler_raw:
            # Mahalle Demografi
            cur.execute("""
            SELECT nufus_toplam, nufus_erkek, nufus_kadin, hane_sayisi, ortalama_hane_geliri,
                   ses_a_plus_oran, ses_a_oran, ses_b_oran, ses_c_oran, ses_d_oran,
                   egitim_universite_oran, egitim_lise_oran, egitim_ortaokul_oran, egitim_ilkokul_oran,
                   yas_genc_oran, yas_orta_oran, yas_yasli_oran,
                   konut_fiyat_m2, kira_fiyat_m2, arsa_fiyat_m2, ticari_fiyat_m2,
                   eczane_sayisi, atm_sayisi, banka_sube_sayisi, ham_json
            FROM demografi WHERE city_id = 34 AND county_id = ? AND district_id = ? AND seviye = 'mahalle' LIMIT 1
            """, (county_id, dist_id))
            m_d = cur.fetchone()

            # Mahalle Fiyat Özet
            cur.execute("""
            SELECT satilik_m2_fiyat, kiralik_m2_fiyat, ortalama_fiyat, amortisman_yil, brut_kira_getirisi,
                   ortalama_bina_yasi, ilan_sayisi, yillik_fiyat_degisim
            FROM fiyat_ozet WHERE city_id = 34 AND county_id = ? AND district_id = ? AND seviye = 'mahalle' AND kategori = 'konut' LIMIT 1
            """, (county_id, dist_id))
            m_f = cur.fetchone()

            # Mahalle Trend
            cur.execute("""
            SELECT ay, satilik_m2_fiyat, kiralik_m2_fiyat, projeksiyon
            FROM fiyat_trend WHERE city_id = 34 AND county_id = ? AND district_id = ? AND seviye = 'mahalle' AND kategori = 'konut'
            ORDER BY ay ASC
            """, (county_id, dist_id))
            m_trend = [{"ay": r[0], "satilik": r[1], "kiralik": r[2], "projeksiyon": r[3]} for r in cur.fetchall()]

            # Mahalle Seçim
            cur.execute("""
            SELECT kazanan_parti, parti_sonuclari_json
            FROM secim_sonuclari WHERE city_id = 34 AND county_id = ? AND district_id = ? AND seviye = 'mahalle'
            ORDER BY id DESC LIMIT 1
            """, (county_id, dist_id))
            m_s = cur.fetchone()
            m_secim = {}
            if m_s:
                m_partiler = []
                try:
                    raw_mp = json.loads(m_s[1]) if m_s[1] else []
                    for p in raw_mp:
                        m_partiler.append({"parti": p.get("Secenek"), "oy": p.get("OySayisi"), "oran": p.get("Oran")})
                except Exception:
                    pass
                m_secim = {"kazanan": m_s[0], "partiler": sorted(m_partiler, key=lambda x: x.get("oy") or 0, reverse=True)[:5]}

            m_poly = mahalle_poligonlari.get(dist_id, {}).get("coordinates")
            m_ham = parse_ham_json(m_d[24] if m_d else None)

            m_obj = {
                "id": dist_id,
                "name": dist_name,
                "poligon": m_poly,
                "nufus": m_d[0] if m_d else None,
                "hane_sayisi": m_d[3] if m_d else None,
                "hane_geliri": m_d[4] if m_d else None,
                "ses": {
                    "a_plus_oran": m_d[5] if m_d else 0, "a_oran": m_d[6] if m_d else 0,
                    "b_oran": m_d[7] if m_d else 0, "c_oran": m_d[8] if m_d else 0, "d_oran": m_d[9] if m_d else 0
                },
                "egitim": {
                    "universite": m_d[10] if m_d else 0, "lise": m_d[11] if m_d else 0,
                    "ortaokul": m_d[12] if m_d else 0, "ilkokul": m_d[13] if m_d else 0
                },
                "yas_orani": {
                    "genc": m_d[14] if m_d else 0, "orta": m_d[15] if m_d else 0, "yasli": m_d[16] if m_d else 0
                },
                "fiyat": {
                    "satilik_m2": m_f[0] if m_f and m_f[0] else (m_d[17] if m_d else None),
                    "kiralik_m2": m_f[1] if m_f and m_f[1] else (m_d[18] if m_d else None),
                    "ortalama_fiyat": m_f[2] if m_f else None,
                    "amortisman_yil": m_f[3] if m_f else None,
                    "kira_getirisi": m_f[4] if m_f else None,
                    "ortalama_bina_yasi": m_f[5] if m_f else None,
                    "ilan_sayisi": m_f[6] if m_f else None,
                    "yillik_degisim": m_f[7] if m_f else None
                },
                "e_ticaret": m_ham.get("e_ticaret", {}),
                "harcamalar": m_ham.get("harcamalar", {}),
                "trend": m_trend,
                "secim": m_secim
            }
            out["mahalleler"][county_id].append(m_obj)

    conn.close()

    out_json = output_dir / "istanbul.json"
    out_js = output_dir / "istanbul_data.js"

    print(f"[*] JSON dosyası kaydediliyor: {out_json}")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"[*] Tarayıcı için JS veri paketi kaydediliyor: {out_js}")
    with open(out_js, "w", encoding="utf-8") as f:
        f.write("window.ISTANBUL_DATA = " + json.dumps(out, ensure_ascii=False) + ";\n")

    size_mb = out_json.stat().st_size / (1024 * 1024)
    print(f"[✓] Derleme tamamlandı! Boyut: {size_mb:.2f} MB")
    print(f"    39 İlçe ve {sum(len(v) for v in out['mahalleler'].values())} Mahalle eksiksiz aktarıldı.")

def main():
    parser = argparse.ArgumentParser(
        description="İstanbul demo paketini seçilen SQLite ve poligon kaynağından üretir."
    )
    parser.add_argument("--calistir", action="store_true", help="Çıktı üretimini açıkça başlat")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Kaynak piyasa_verileri.db")
    parser.add_argument("--poligon", type=Path, default=DEFAULT_POLY_DIR, help="Kaynak poligon klasörü")
    parser.add_argument("--cikti", type=Path, default=DATA_DIR, help="Çıktı klasörü")
    args = parser.parse_args()
    if not args.calistir:
        parser.print_help()
        return
    compile_data(args.db, args.poligon, args.cikti)


if __name__ == "__main__":
    main()
