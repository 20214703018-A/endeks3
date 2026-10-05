#!/usr/bin/env python3
"""
YENİ VERİ TOPLU İNCELEME RAPORU (STANDARD §10.1, kaynak AİLESİ düzeyinde) — salt okunur.

kapsama_kontrol.py çıktısındaki B (yalnız staging), C4 (envanterde, işlenmemiş) ve D (envanter dışı) dosyalarını kaynak
ailelerine gruplar; her ailede en büyük dosyalardan örnek alıp yeni_veri_inceleme.profil() ile ölçer. Ek olarak katalogda
olmayan güncel kaynaklar (release DB'leri vb.) --ek-kaynaklar ile verilir. Sentetik veri işaretleri karantina kurallarından
(tahmin_ufku, yildiz_dagilimi/y2022…, demo/mock yolları) ve Google toplayıcısının bilinen kimlik kusurundan (sha24 sahte
place_id) aranır. Çıktı: new_data_review_aile_<tarih>.md + .json
"""
import argparse, json, os, re, sys, time, duckdb
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yeni_veri_inceleme import profil, tablolar

YEREL = [  # arşiv yolu öneki → Codespace'teki geri getirilmiş yer
    ("GEOPROP_RAW_INTAKE/", "/tmp/geo/GEOPROP_RAW_INTAKE/"), ("warehouse/", "/tmp/geo/Desktop/GEOPROP/warehouse/"),
    ("VERİLER/", "/tmp/geo/Desktop/GEOPROP/VERİLER/"), ("collector/", "/tmp/geo/Desktop/GEOPROP/collector/"),
    ("GEOPROP/", "/tmp/geo/Desktop/GEOPROP/"), ("Downloads/", "/tmp/geo/home/Downloads/"), ("tkgm/", "/tmp/geo/Desktop/tkgm/"),
    ("harita/", "/tmp/geo/Desktop/harita/"), ("GEOPROP_AMBAR/", "/tmp/geo/yeni/AMBAR_ESKI/")]
OKUNUR = (".sqlite", ".sqlite3", ".db", ".duckdb", ".parquet", ".csv", ".tsv", ".jsonl", ".ndjson", ".geojson", ".json",
          ".jsonl.gz", ".json.gz", ".csv.gz", ".xlsx", ".xls", ".zip", ".geojson.gz")
SENTETIK_KOL = {"tahmin_ufku": "T06 tahmin_ufku (sentetik ETBİS/SEGE demo)", "yildiz_dagilimi": "T07 formülle yıldız dağılımı",
                "y2022": "T07 yıl bazlı formül bölmesi", "yildiz_5": "T07 formülle yıldız dağılımı", "record_class": "T08 önceki ekip notu"}
SENTETIK_YOL = re.compile(r"/(demo|mock|mocks|fixtures?|samples?|ornek|örnek|test|tests)/", re.I)
GEOPROP_DISI = re.compile(r"OperaSetup|Chinook_Sqlite|NCWS|KullaniciListesiXml|newUser\w*List|black-icon|"
                         r"workspace-[0-9a-f-]+ \(1\)/(public|src|videos|download)|extension/veri-ajani|harita/data/satellite", re.I)
HASSAS_AILE = re.compile(r"kisisel_veriler|gib_mukellef|restricted/", re.I)
HEDEF = [  # anahtar kelime → önerilen kanonik model
    (r"Google mekân ambarı|google_places|osm_poi|kamu_bina|otopark|kargo|lojistik|pazarlar|tesis", "poi + poi_snapshot (işletme/nokta)"),
    (r"Google detay", "poi_detail + review_observation (yazar adı hassas)"),
    (r"gib_mukellef|gleif|kobi|ito_|ostim|zincir_markalar|firma", "business_registry (firma sicili; kişisel alanlar hassas)"),
    (r"BOLGE_CSV|ULUSAL_CSV|nihai_paket|TEK_PAKET|bolge_\d\d|/m\d\d_", "price_series paketi (staging'deki ulusal/bölge paketiyle sha karşılaştırması)"),
    (r"aracrisk", "vehicle_listing_observation (araç ilan emsali)"),
    (r"cevre|yapilasma", "indicator_observation (çevre/yapı stoğu)"),
    (r"ulasim_ve_hareketlilik|mobility|cameras_route", "mobility_observation"),
    (r"resmi_gazete", "legal_notice (Resmî Gazete ilanları)"),
    (r"sarj|epdk", "charging_socket / energy (EPDK)"),
    (r"collector/data/cografya|mahalle_koordinat|csv_ciktilari", "geo_entity referansı (mevcut omurgayla karşılaştır)"),
    (r"github_actions", "GitHub artifact (Google shard'ları → güncel ambarla kapsanır)"),
    (r"place|poi|mekan|isletme", "poi + poi_snapshot (işletme/nokta)"),
    (r"yorum|review", "review_observation (yorum; yazar adı hassas)"),
    (r"yogunluk|popular|anlik", "busyness_observation (yoğunluk serisi)"),
    (r"ilan|listing|emlak-harita|area_analytics|devren|airbnb|sahibinden", "listing_observation (ilan emsali)"),
    (r"marketfiyati|fiyat|price|hal_|akaryakit|cimri|opet", "product_price_observation (ürün fiyatı)"),
    (r"arsa|endeks|trend|emlakjet", "price_observation (bölge fiyat endeksi; projeksiyon ayrı)"),
    (r"menu|yemeksepeti|teslimat|restoran", "menu_item_observation / venue_delivery_observation"),
    (r"gtfs|kentkart|ego_|toplu_tasima|otobus|sefer|transit|havalimani|eurocontrol|denizcilik", "transit_* / mobility_observation"),
    (r"tuik|sdmx|istatistik|bkm|sgk|tobb|btk|ticaret|eurostat|ons_|makro", "indicator_observation (resmî gösterge)"),
    (r"egitim|okul|meb|lgs|yurt|universite", "education_facility + observation"),
    (r"saglik|hastane|eczane|khgm", "health_facility + observation"),
    (r"turizm|ktb|otel|konaklama", "tourism_facility (KTB) + observation"),
    (r"imar|parsel|tkgm|kadastro|ada", "parcel + zoning_observation"),
    (r"google_ads|reklam", "ad_observation (reklam şeffaflığı)"),
    (r"kgm|karayol|trafik", "road / traffic_observation"),
    (r"poligon|sinir|boundary|geojson|katman|cografi|vektor|fay|dere", "geo_layer (coğrafi katman)"),
    (r"ckan|acikveri|ibb", "açık veri portalı → kaynağa göre (gösterge/nokta/katman)")]


def aile_adi(yol):
    p = yol.split("/")
    if yol.startswith("GEOPROP_RAW_INTAKE/"): return "/".join(p[:2])
    if yol.startswith("Downloads/emlak-harita"): return "Downloads/emlak-harita-* (" + p[-1].split(".")[0] + ")"
    if yol.startswith("warehouse/product/"): return "/".join(p[:3])
    return "/".join(p[:3]) if len(p) > 2 else "/".join(p[:2])


def yerel(yol):
    for on, hedef in YEREL:
        if yol.startswith(on): return hedef + yol[len(on):]
    return None


def hedef_model(ad):
    if GEOPROP_DISI.search(ad): return "GEOPROP DIŞI (program/örnek/kişisel liste) — eklenmez, kullanıcı kararı"
    for rx, m in HEDEF:
        if re.search(rx, ad, re.I): return m
    return "incelenecek"


def yaz(sonuc, cikti, tarih):
    for r in sonuc:
        r["hedef"] = hedef_model(r["aile"])
        if HASSAS_AILE.search(r["aile"]) and "kişisel veri içeren kaynak (restricted)" not in r["uyarilar"]:
            r["uyarilar"].append("kişisel veri içeren kaynak (restricted)")
    json.dump(sonuc, open(f"{cikti}/new_data_review_aile_{tarih}.json", "w"), ensure_ascii=False, indent=1, default=str)
    gruplar = [("Eklenecek kaynaklar", lambda r: not r["hedef"].startswith("GEOPROP DIŞI")),
               ("GEOPROP dışı görünen dosyalar (eklenmez; onayınızı bekler)", lambda r: r["hedef"].startswith("GEOPROP DIŞI"))]
    L = [f"# Yeni veri toplu inceleme raporu (aile düzeyi) — {tarih}", "",
         f"{len(sonuc)} kaynak ailesi · {sum(r['dosya'] for r in sonuc):,} dosya · {sum(r['boyut_mb'] for r in sonuc)/1e3:,.1f} GB. "
         "Sınıf: B yalnız staging · C4 envanterde işlenmemiş · D envanter dışı · E katalog dışı güncel kaynak. "
         "Satırlar ailedeki en büyük dosyalardan ÖRNEKLEM ölçümüdür.", ""]
    for baslik, kosul in gruplar:
        g = [r for r in sonuc if kosul(r)]
        L += [f"## {baslik} — {len(g)} aile, {sum(r['boyut_mb'] for r in g)/1e3:,.1f} GB", "",
              "| # | Aile | Dosya | MB | Sınıf | Ölçülen satır | Mahalleye düşen | Kişisel veri şüphesi | Uyarı | Önerilen hedef |",
              "|---:|---|---:|---:|---|---:|---:|---|---|---|"]
        for i, r in enumerate(g, 1):
            L.append(f"| {i} | `{r['aile']}` | {r['dosya']:,} | {r['boyut_mb']:,.0f} | {','.join(s.split('_')[0] for s in r['siniflar'])} | "
                     f"{r['olculen_satir']:,} | {'%' + format(r['mahalle_eslesme']*100, '.0f') if r['mahalle_eslesme'] is not None else '—'} | "
                     f"{', '.join(r['hassas'])[:40] or '—'} | {'; '.join(r['uyarilar'])[:110] or '—'} | {r['hedef']} |")
        L.append("")
    Path(f"{cikti}/new_data_review_aile_{tarih}.md").write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yalniz-yaz", default=None, help="mevcut JSON'u yeniden sınıflandırıp yaz (ölçüm yapmaz)")
    ap.add_argument("--kapsama", required=True); ap.add_argument("--canonical", required=True); ap.add_argument("--cikti", required=True)
    ap.add_argument("--ek-kaynaklar", default=None, help="JSON: [{ad, yol, aciklama}] katalog dışı güncel kaynaklar")
    ap.add_argument("--ornek-dosya", type=int, default=8)
    a = ap.parse_args(); os.makedirs(a.cikti, exist_ok=True)
    if a.yalniz_yaz:
        yaz(json.load(open(a.yalniz_yaz)), a.cikti, time.strftime("%Y-%m-%d")); print("yeniden yazıldı"); return
    c = duckdb.connect()
    rows = c.execute(f"""SELECT yedek, arsiv_yolu, bayt, sinif FROM read_parquet('{a.kapsama}')
                         WHERE sinif IN ('B_staging','C4_islenmemis','D_envanter_disi')""").fetchall()
    aileler = {}
    for yedek, yol, bayt, sinif in rows:
        k = aile_adi(yol); v = aileler.setdefault(k, {"aile": k, "dosya": 0, "bayt": 0, "siniflar": set(), "ornekler": []})
        v["dosya"] += 1; v["bayt"] += bayt; v["siniflar"].add(sinif); v["ornekler"].append((bayt, yol))
    if a.ek_kaynaklar:
        for e in json.load(open(a.ek_kaynaklar)):
            aileler[e["ad"]] = {"aile": e["ad"], "dosya": 1, "bayt": os.path.getsize(e["yol"]), "siniflar": {"E_guncel_kaynak"},
                                "ornekler": [(os.path.getsize(e["yol"]), e["yol"])], "aciklama": e.get("aciklama", ""), "mutlak": True}
    # mahalle poligonları bir kez
    print(f"{len(aileler)} aile", flush=True)
    sonuc = []; t0 = time.time()
    for i, (k, v) in enumerate(sorted(aileler.items(), key=lambda x: -x[1]["bayt"]), 1):
        ornek = [y for b, y in sorted(v["ornekler"], reverse=True) if y.lower().endswith(OKUNUR)][: a.ornek_dosya]
        prof = {}; uyari = set()
        for y in ornek:
            yol = y if v.get("mutlak") else yerel(y)
            if SENTETIK_YOL.search("/" + y + "/"): uyari.add("Q06 demo/test/örnek yolu")
            if not yol or not os.path.exists(yol):
                prof[y] = {"hata": "yerelde yok"}; continue
            try:
                cc = duckdb.connect(); cc.execute("INSTALL spatial; LOAD spatial; INSTALL sqlite; LOAD sqlite;")
                cc.execute(f"ATTACH '{a.canonical}' AS canon (READ_ONLY)")
                cc.execute("CREATE TEMP TABLE mah AS SELECT geo_id, geometry, bbox_xmin, bbox_xmax, bbox_ymin, bbox_ymax "
                           "FROM canon.geo_entity WHERE level='mahalle' AND geometry IS NOT NULL")
                tl = {}
                for ad, ref in tablolar(cc, yol)[:12]:
                    try:
                        p = profil(cc, ref, True); tl[ad] = p
                        for kol, _ in p["sutunlar"]:
                            if kol.lower() in SENTETIK_KOL: uyari.add(SENTETIK_KOL[kol.lower()])
                        idk = p.get("kimlik", {}).get("google_place_id")
                    except Exception as e:
                        tl[ad] = {"hata": str(e)[:120]}
                prof[y] = tl; cc.close()
            except Exception as e:
                prof[y] = {"hata": str(e)[:150]}
        if k.startswith("GEOPROP_AMBAR/"): uyari.add("eski kopya (Eylül) — güncel sürümü [güncel] grubunda ölçüldü")
        if re.search(r"google_places", k): uyari.add("Google toplayıcı kimlik kusuru (2026-09-20): eski kayıtlarda sha24 sahte place_id / bozuk koordinat → işaretlenecek")
        if "Öğelerle Yeni Klasör" in k or re.search(r"\(\d\)|kopya|copy", k, re.I): uyari.add("olası kopya (sha ile doğrulanacak)")
        # özet sayılar
        satir = sum(t.get("satir", 0) for f in prof.values() if isinstance(f, dict) for t in f.values() if isinstance(t, dict))
        koord = [t["koordinat"] for f in prof.values() if isinstance(f, dict) for t in f.values() if isinstance(t, dict) and t.get("koordinat")]
        mah = [x.get("mahalle_eslesme_orani_ornek") for x in koord if x.get("mahalle_eslesme_orani_ornek") is not None]
        hassas = sorted({h for f in prof.values() if isinstance(f, dict) for t in f.values() if isinstance(t, dict) for h in t.get("hassas_supheli", [])})
        r = {"aile": k, "dosya": v["dosya"], "boyut_mb": round(v["bayt"] / 1e6, 1), "siniflar": sorted(v["siniflar"]),
             "olculen_dosya": len(ornek), "olculen_satir": satir, "koordinatli_tablo": len(koord),
             "mahalle_eslesme": round(sum(mah) / len(mah), 3) if mah else None, "hassas": hassas, "uyarilar": sorted(uyari),
             "hedef": hedef_model(k), "aciklama": v.get("aciklama", ""), "profil": prof}
        sonuc.append(r)
        if i % 10 == 0: print(f"  {i}/{len(aileler)} · {time.time()-t0:.0f} sn", flush=True)
    yaz(sonuc, a.cikti, time.strftime("%Y-%m-%d"))
    print(f"bitti · {len(sonuc)} aile · {time.time()-t0:.0f} sn")


if __name__ == "__main__":
    main()
