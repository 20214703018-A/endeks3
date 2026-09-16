"""Çevre motoru: hava kalitesi en yakın istasyon/özet; Resmî Gazete il/ilçe/mahalle eşleşme düzeyi; DB yoksa None."""
import sqlite3, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path
from geoprop.cevre import EnvironmentEngine


class CevreTest(unittest.TestCase):
    def test_hava_ve_rg(self):
        with tempfile.TemporaryDirectory() as d:
            cv, rg = Path(d) / "cevre.sqlite", Path(d) / "resmi_gazete.sqlite"
            with sqlite3.connect(cv) as c:
                c.executescript("""
                    CREATE TABLE hava_istasyon (id TEXT PRIMARY KEY, kod TEXT, ad TEXT, il TEXT, ilce TEXT, il_norm TEXT, tip TEXT, lat REAL, lon REAL, son_veri TEXT, guncellenme TEXT);
                    CREATE TABLE hava_gunluk (istasyon_id TEXT, gun TEXT, pm10 REAL, pm25 REAL, so2 REAL, no2 REAL);
                    CREATE TABLE kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
                    INSERT INTO hava_istasyon VALUES ('a','1','Yakın','X','Y','x','Kentsel',40.01,30.0,NULL,NULL),('b','2','Uzak','X','Y','x','Kırsal',40.2,30.0,NULL,NULL);
                """)
                g = date.today()
                for i in range(10):
                    c.execute("INSERT INTO hava_gunluk VALUES ('a',?,?,?,NULL,20)", ((g - timedelta(days=i)).isoformat(), 60 if i < 5 else 20, 10))
            with sqlite3.connect(rg) as c:
                c.executescript("""
                    CREATE TABLE karar (id TEXT PRIMARY KEY, tarih TEXT, rg_sayi TEXT, karar_no TEXT, baslik TEXT, kategori TEXT, islem TEXT, dosya TEXT, metin TEXT, metin_sha256 TEXT, ocr INTEGER, guncellenme TEXT);
                    CREATE TABLE karar_yer (karar_id TEXT, il TEXT, ilce TEXT, mahalle TEXT, il_norm TEXT, ilce_norm TEXT, mahalle_norm TEXT, kaynak TEXT);
                    CREATE TABLE taranan_gun (tarih TEXT PRIMARY KEY, madde INTEGER, eslesen INTEGER, guncellenme TEXT);
                """)
                t = date.today().isoformat()
                c.execute("INSERT INTO karar VALUES ('k1',?, '1','11','Mahalle kararı','kentsel_donusum','acele_kamulastirma','k1.pdf','',NULL,1,NULL)", (t,))
                c.execute("INSERT INTO karar VALUES ('k2',?, '1','12','İlçe kararı','enerji_hatti',NULL,'k2.pdf','',NULL,1,NULL)", (t,))
                c.execute("INSERT INTO karar VALUES ('k3',?, '1','13','Başka ilçe','yol',NULL,'k3.pdf','',NULL,1,NULL)", (t,))
                c.execute("INSERT INTO karar VALUES ('k4','2020-01-01','1','14','Eski karar','yol',NULL,'k4.pdf','',NULL,1,NULL)")
                c.executemany("INSERT INTO karar_yer VALUES (?,?,?,?,?,?,?,?)", [
                    ('k1','X','Y','Z','x','y','z','metin'), ('k2','X','Y',None,'x','y','','harita'), ('k3','X','Q',None,'x','q','','metin'), ('k4','X','Y',None,'x','y','','metin')])
                c.execute("INSERT INTO taranan_gun VALUES (?,5,2,NULL)", (t,))
            e = EnvironmentEngine(cv, rg)
            h = e.hava(40.0, 30.0)
            self.assertEqual(h["en_yakin"]["ad"], "Yakın"); self.assertEqual(h["en_yakin"]["pm10"]["ortalama"], 40.0)
            self.assertEqual(h["en_yakin"]["pm10"]["ab_gunluk_asim_gun"], 5); self.assertEqual(h["en_yakin"]["temsil_gucu"], "yüksek")
            self.assertEqual(h["pm10_sinif"], "orta"); self.assertEqual(len(h["istasyonlar"]), 2); self.assertEqual(h["en_yakin"]["no2"]["dso_yillik_kat"], 2.0)
            r = e.resmi_gazete("X", "Y", ["Z Mahallesi"])
            self.assertEqual([k["id"] for k in r["kararlar"]], ["k1", "k2"])
            self.assertEqual(r["kararlar"][0]["eslesme"], "mahalle"); self.assertEqual(r["kararlar"][1]["eslesme"], "ilçe"); self.assertIn("düşük", r["kararlar"][1]["guven"])
            self.assertEqual(r["sayim"], {"mahalle": 1, "ilçe": 1, "il": 0})
            self.assertIsNone(EnvironmentEngine(Path(d) / "yok.sqlite", rg).hava(40.0, 30.0))
            self.assertEqual(e.hava(None, None)["status"], "coordinate_required")
            self.assertEqual(e.hava(0.0, 0.0)["status"], "no_station")


if __name__ == "__main__":
    unittest.main()
