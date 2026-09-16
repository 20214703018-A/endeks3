"""OSM POI yakınlık motoru: en yakın/sayım/hat/zincir; OSRM kapalıyken yol alanı yok; DB yoksa None."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from geoprop.yakin_noktalar import NearbyPoiEngine


def _db(path: Path):
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE poi (id INTEGER PRIMARY KEY, osm_tip TEXT, osm_id INTEGER, kategori TEXT, alt_kategori TEXT, marka TEXT,
                              ad TEXT, ad_norm TEXT, lat REAL, lon REAL, etiketler TEXT);
            CREATE VIRTUAL TABLE poi_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);
            CREATE TABLE hat (id INTEGER PRIMARY KEY, osm_id INTEGER, tur TEXT, ref TEXT, ad TEXT, operator TEXT, from_ad TEXT, to_ad TEXT, renk TEXT);
            CREATE TABLE hat_durak (hat_id INTEGER, sira INTEGER, durak_osm_tip TEXT, durak_osm_id INTEGER, PRIMARY KEY (hat_id, sira));
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO poi VALUES (1,'node',11,'saglik','hastane',NULL,'Test Hastanesi','test hastanesi',40.0045,30.0,'{}');
            INSERT INTO poi VALUES (2,'node',12,'ulasim','otobus_duragi',NULL,'Durak A','durak a',40.001,30.0,'{}');
            INSERT INTO poi VALUES (3,'node',13,'alisveris','market','A101','A101 Merkez','a101 merkez',40.002,30.0,'{}');
            INSERT INTO poi VALUES (4,'node',14,'alisveris','market','BİM','BİM','bim',40.003,30.0,'{}');
            INSERT INTO poi VALUES (5,'node',15,'alisveris','avm','Uzak AVM',NULL,'uzak avm',41.0,30.0,'{}');
            INSERT INTO poi_rtree SELECT id, lat, lat, lon, lon FROM poi;
            INSERT INTO hat VALUES (1, 900, 'bus', '14B', 'Kadıköy - Ataşehir', 'İETT', 'Kadıköy', 'Ataşehir', NULL);
            INSERT INTO hat_durak VALUES (1, 1, 'node', 12);
            INSERT INTO meta VALUES ('pbf_mtime', '2026-09-12T23:59:35+00:00');
        """)


class YakinNoktalarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / "osm_poi.sqlite"; _db(self.path)
        self.e = NearbyPoiEngine(self.path); self.e.osrm_enabled = False

    def tearDown(self):
        self.tmp.cleanup()

    def test_en_yakin_sayim_hat_zincir(self):
        r = self.e.analyze(40.0, 30.0)
        self.assertEqual(r["status"], "available")
        h = r["hedefler"]["hastane"]["en_yakin"][0]
        self.assertEqual(h["ad"], "Test Hastanesi"); self.assertAlmostEqual(h["mesafe_m"], 500, delta=5); self.assertNotIn("yol_mesafe_m", h)
        self.assertEqual(r["hedefler"]["avm"]["bulunan"], 0)             # 111 km uzak, 20 km yarıçap dışında
        self.assertEqual(r["sayim"]["market"]["1000m"], 2)
        self.assertEqual(r["toplu_tasima"]["durak_500m"], 1)
        self.assertEqual(r["toplu_tasima"]["hatlar"][0]["hat"], "14B")
        self.assertEqual(r["zincirler_1km"]["market"], {"A101": 1, "BİM": 1})
        self.assertEqual(r["veri_tarihi"], "2026-09-12T23:59:35+00:00")

    def test_resmi_liste_birlestirme(self):
        # kardeş onemli_tesisler.sqlite: hastane_ozel (OSM ile 400 m içinde çakışır → işaret), hal (OSM'de yok → ayrı madde, yaklaşık)
        with sqlite3.connect(self.path.with_name("onemli_tesisler.sqlite")) as c:
            c.executescript("""
                CREATE TABLE hastane_ozel (id INTEGER PRIMARY KEY, il TEXT, ilce TEXT, ad TEXT, ad_norm TEXT, tip TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT);
                CREATE TABLE hal (id INTEGER PRIMARY KEY, il TEXT, ilce TEXT, ad TEXT, tur TEXT, faaliyet_tarihi TEXT, adres TEXT, lat REAL, lon REAL, koordinat_kaynagi TEXT, guncellenme TEXT);
                INSERT INTO hastane_ozel VALUES (1,'X','Y','ÖZEL TEST HASTANESİ','ozel test hastanesi','Özel Genel Hastane',40.0046,30.0,'osm: ad eşleşmesi',NULL);
                INSERT INTO hal VALUES (1,'X','Y','X BELEDİYESİ TOPTANCI HALİ','Belediye Hali','1.1.2000','adres',40.02,30.0,'adres: mahalle merkezi (yaklaşık)',NULL);
            """)
        r = self.e.analyze(40.0, 30.0)
        h = r["hedefler"]["hastane"]
        self.assertEqual(h["bulunan"], 1); self.assertEqual(h["en_yakin"][0]["resmi"]["tip"], "Özel Genel Hastane"); self.assertFalse(h["en_yakin"][0]["konum_yaklasik"])
        hal = r["hedefler"]["hal"]
        self.assertEqual(hal["bulunan"], 1); self.assertTrue(hal["en_yakin"][0]["konum_yaklasik"]); self.assertIsNone(hal["en_yakin"][0]["osm_id"])
        self.assertEqual(hal["en_yakin"][0]["resmi"]["tur"], "Belediye Hali"); self.assertIn("HKS", hal["resmi_kaynak"])

    def test_db_yoksa_none_koordinatsiz(self):
        self.assertIsNone(NearbyPoiEngine(Path(self.tmp.name) / "yok.sqlite").analyze(40.0, 30.0))
        self.assertEqual(self.e.analyze(None, None)["status"], "coordinate_required")


if __name__ == "__main__":
    unittest.main()
