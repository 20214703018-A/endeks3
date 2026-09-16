"""Okul motoru: kademe yakınlığı, LGS eşleşmesi, adrese dayalı lisede puan yok, tutarsız oran gizlenir."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from geoprop.okullar import SchoolEngine


def _db(path: Path):
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE okul (kurum_kodu INTEGER PRIMARY KEY, il_kodu INTEGER, ilce_kodu INTEGER, il TEXT, ilce TEXT, ad TEXT, ad_norm TEXT,
                tur TEXT, host TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, derslik INTEGER, ogretmen INTEGER, ogrenci INTEGER,
                istatistik_guncellenme TEXT, guncellenme TEXT);
            CREATE TABLE lgs_taban (tercih_kodu INTEGER PRIMARY KEY, il_kodu INTEGER, ilce TEXT, okul_adi TEXT, okul_adi_norm TEXT, okul_turu TEXT,
                alan TEXT, ogretim_sekli TEXT, pansiyon TEXT, dil TEXT, kontenjan INTEGER, taban_ilk REAL, taban_nakil REAL, yil INTEGER,
                kurum_kodu INTEGER, ulusal_sira INTEGER, ulusal_yuzdelik REAL, il_sira INTEGER, tur_sira INTEGER, guncellenme TEXT);
            CREATE TABLE kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT);
            INSERT INTO okul VALUES (1,77,1,'YALOVA','MERKEZ','Test Fen Lisesi','test fen lisesi','fen_lisesi','h1',40.005,30.0,'meb',24,40,600,NULL,NULL);
            INSERT INTO okul VALUES (2,77,1,'YALOVA','MERKEZ','Mahalle Anadolu Lisesi','mahalle anadolu lisesi','anadolu_lisesi','h2',40.002,30.0,'adres: mahalle merkezi (yaklaşık)',1,30,713,NULL,NULL);
            INSERT INTO okul VALUES (3,77,1,'YALOVA','MERKEZ','Cumhuriyet İlkokulu','cumhuriyet ilkokulu','ilkokul','h3',40.001,30.0,'meb',20,25,500,NULL,NULL);
            INSERT INTO lgs_taban VALUES (100,77,'MERKEZ','Test Fen Lisesi','test fen lisesi','Fen Lisesi',NULL,NULL,NULL,NULL,90,480.5,470.0,2026,1,12,0.4,1,3,NULL);
            INSERT INTO kapsama VALUES ('okul', 3, '2026-09-13T00:00:00+00:00');
        """)


class OkullarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); p = Path(self.tmp.name) / "resmi_egitim.sqlite"; _db(p); self.e = SchoolEngine(p)

    def tearDown(self):
        self.tmp.cleanup()

    def test_kademe_lgs_ve_oran(self):
        r = self.e.analyze(40.0, 30.0)
        self.assertEqual(r["status"], "available"); self.assertEqual(r["lgs_yili"], 2026)
        ilk = r["kademeler"]["ilkokul"]["en_yakin"][0]
        self.assertEqual(ilk["ad"], "Cumhuriyet İlkokulu"); self.assertEqual(ilk["ogrenci_derslik"], 25.0); self.assertFalse(ilk["konum_yaklasik"])
        liseler = {x["ad"]: x for x in r["kademeler"]["lise"]["en_yakin"]}
        self.assertEqual(liseler["Test Fen Lisesi"]["lgs"]["taban_puan"], 480.5); self.assertEqual(liseler["Test Fen Lisesi"]["lgs"]["ulusal_sira"], 12)
        self.assertEqual(liseler["Mahalle Anadolu Lisesi"]["lgs"]["puan_verisi"], "yok")
        self.assertIsNone(liseler["Mahalle Anadolu Lisesi"]["ogrenci_derslik"])     # 713/1 tutarsız → gizli
        self.assertTrue(liseler["Mahalle Anadolu Lisesi"]["konum_yaklasik"])
        best = r["en_iyi_liseler_5km"]; self.assertEqual(best["puanli_lise"], 1); self.assertEqual(best["liste"][0]["ad"], "Test Fen Lisesi")

    def test_db_yoksa_none(self):
        self.assertIsNone(SchoolEngine(Path(self.tmp.name) / "yok.sqlite").analyze(40.0, 30.0))
        self.assertEqual(self.e.analyze(None, None)["status"], "coordinate_required")


if __name__ == "__main__":
    unittest.main()
