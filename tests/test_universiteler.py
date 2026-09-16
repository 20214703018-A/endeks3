import json, sqlite3, tempfile, unittest
from pathlib import Path
from geoprop.universiteler import UniversityEngine


class UniversitelerTest(unittest.TestCase):
    def test_poligon_icinde_ve_ogrenci(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "universite.sqlite"
            poly = json.dumps({"type": "Polygon", "coordinates": [[[29.99, 39.99], [30.01, 39.99], [30.01, 40.01], [29.99, 40.01], [29.99, 39.99]]]})
            with sqlite3.connect(p) as c:
                c.executescript("""
                    CREATE TABLE universite (ad TEXT PRIMARY KEY, ad_norm TEXT, tur TEXT, il TEXT, toplam_t INTEGER, aof_toplam INTEGER, uzaktan_toplam INTEGER, kampus_toplam INTEGER);
                    CREATE TABLE kampus (poi_id INTEGER PRIMARY KEY, ad TEXT, universite TEXT, il TEXT, ilce TEXT, lat REAL, lon REAL, alan_m2 REAL, geometri TEXT,
                        eslesme TEXT, ilce_ogrenci INTEGER, ilce_aof INTEGER, ilce_kampus_sayisi INTEGER, guncellenme TEXT, bina_parcasi INTEGER, kapsayan_poi_id INTEGER);
                    CREATE TABLE kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
                    INSERT INTO universite VALUES ('TEST ÜNİVERSİTESİ','test universitesi','DEVLET','TESTİL',50000,40000,0,10000);
                """)
                c.execute("INSERT INTO kampus VALUES (1,'Test Kampüsü','TEST ÜNİVERSİTESİ','TESTİL','MERKEZ',40.0,30.0,3000000,?, 'ad', 8000, 40000, 1, NULL, 0, NULL)", (poly,))
                c.execute("INSERT INTO kampus VALUES (2,'Fizik Bölümü','TEST ÜNİVERSİTESİ','TESTİL','MERKEZ',40.001,30.001,NULL,NULL,'ad',NULL,NULL,NULL,NULL,1,1)")
                c.execute("INSERT INTO kampus VALUES (3,'Adsız nokta',NULL,'TESTİL','MERKEZ',40.05,30.0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,NULL)")
            r = UniversityEngine(p).analyze(40.0, 30.0)
            self.assertEqual(r["bulunan"], 1)                       # bina parçası ve adsız nokta hariç
            k = r["kampusler"][0]
            self.assertTrue(k["parsel_icinde"]); self.assertEqual(k["mesafe_m"], 0)
            self.assertEqual(k["ilce_ogrenci_aof_haric"], 8000); self.assertEqual(k["universite_toplam_aof_haric"], 10000)
            self.assertEqual(k["alan_ha"], 300.0)
            self.assertIsNone(UniversityEngine(Path(d) / "yok.sqlite").analyze(40.0, 30.0))


if __name__ == "__main__":
    unittest.main()
