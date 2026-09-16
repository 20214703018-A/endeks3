import sqlite3, tempfile, unittest
from pathlib import Path
from geoprop.yurtlar import DormEngine


class YurtlarTest(unittest.TestCase):
    def test_ozet_ve_en_yakin(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "yurtlar.sqlite"
            with sqlite3.connect(p) as c:
                c.executescript("""
                    CREATE TABLE yurt (id INTEGER PRIMARY KEY, kaynak TEXT, il TEXT, ilce TEXT, ad TEXT, ad_norm TEXT, tip TEXT, kapasite INTEGER,
                        kapasite_kaynagi TEXT, adres TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT);
                    CREATE TABLE kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT);
                    INSERT INTO yurt VALUES (1,'kyk','Yalova','Merkez','YALOVA YURDU','yalova yurdu','Kız',NULL,NULL,'x',40.004,30.0,'adres: köy merkezi (yaklaşık)',NULL);
                    INSERT INTO yurt VALUES (2,'ozel','Yalova','Merkez','ÖZEL ELİF ERKEK','ozel elif erkek','Erkek',156,'gsb','x',40.002,30.0,'adres: sokak düzeyi (yaklaşık)',NULL);
                    INSERT INTO yurt VALUES (3,'ozel','Yalova','Merkez','ÖZEL UZAK KIZ','ozel uzak kiz','Kız',300,'gsb','x',40.02,30.0,'adres: sokak düzeyi (yaklaşık)',NULL);
                """)
            r = DormEngine(p).analyze(40.0, 30.0)
            self.assertEqual(r["ozet"]["1000m"]["yurt"], 2); self.assertEqual(r["ozet"]["1000m"]["erkek_kapasite"], 156)
            self.assertEqual(r["ozet"]["1000m"]["kapasitesi_bilinmeyen"], 1)
            self.assertEqual(r["ozet"]["3000m"]["kiz_kapasite"], 300)
            self.assertEqual(r["en_yakin"][0]["ad"], "ÖZEL ELİF ERKEK"); self.assertTrue(r["en_yakin"][0]["konum_yaklasik"])
            self.assertIsNone(DormEngine(Path(d) / "yok.sqlite").analyze(40.0, 30.0))


if __name__ == "__main__":
    unittest.main()
