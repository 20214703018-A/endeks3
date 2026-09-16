"""Semt pazarı motoru: sayım/gün dağılımı/en yakın; tablo yoksa None."""
import sqlite3, tempfile, unittest
from pathlib import Path
from geoprop.pazarlar import MarketEngine


class PazarlarTest(unittest.TestCase):
    def test_sayim_gun_en_yakin(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pazarlar.sqlite"
            with sqlite3.connect(p) as c:
                c.executescript("""
                    CREATE TABLE pazar (id INTEGER PRIMARY KEY, kaynak TEXT, il TEXT, ilce TEXT, mahalle TEXT, ad TEXT, gunler TEXT, kapali INTEGER, tip TEXT, lat REAL, lon REAL, guncellenme TEXT);
                    CREATE TABLE kapsama (kaynak TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT);
                    INSERT INTO pazar VALUES (1,'ibb_acik_veri','İstanbul','Kadıköy','Fikirtepe','Salı Pazarı','sali',1,'Semt Pazarı',40.005,30.0,NULL);
                    INSERT INTO pazar VALUES (2,'osm','İstanbul','Kadıköy',NULL,'Açık Pazar','sali,cumartesi',0,NULL,40.02,30.0,NULL);
                    INSERT INTO pazar VALUES (3,'hks','İstanbul','Kadıköy',NULL,'Uzak Pazar','',NULL,NULL,41.0,30.0,NULL);
                    INSERT INTO kapsama VALUES ('ibb_acik_veri',1,NULL); INSERT INTO kapsama VALUES ('osm',1,NULL); INSERT INTO kapsama VALUES ('hks',1,NULL);
                """)
            r = MarketEngine(p).analyze(40.0, 30.0)
            self.assertEqual(r["sayim"]["1000m"], 1); self.assertEqual(r["sayim"]["3000m"], 2); self.assertEqual(r["sayim"]["kapali_3000m"], 1)
            self.assertEqual(r["gun_dagilimi"], {"Salı": 2, "Cumartesi": 1})
            self.assertEqual(r["en_yakin"][0]["mahalle"], "Fikirtepe"); self.assertEqual(r["en_yakin"][0]["gunler"], ["Salı"])
            self.assertIsNone(MarketEngine(Path(d) / "yok.sqlite").analyze(40.0, 30.0))
            self.assertEqual(MarketEngine(p).analyze(None, None)["status"], "coordinate_required")


if __name__ == "__main__":
    unittest.main()
