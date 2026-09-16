"""TÜİK bölge motoru: ilçe eşleşmesi, mahalle adayları (kadastro ≠ idari), türetilen yüzdeler; tablo yoksa None."""
import sqlite3, tempfile, unittest
from pathlib import Path
from geoprop.tuik_bolge import TuikRegionEngine


class TuikBolgeTest(unittest.TestCase):
    def test_ozet(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tuik_bolge.sqlite"
            with sqlite3.connect(p) as c:
                c.executescript("""
                    CREATE TABLE nufus_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, yil INTEGER, nufus INTEGER);
                    CREATE TABLE nufus_mahalle (tuik_kodu INTEGER, il TEXT, ilce TEXT, belediye TEXT, mahalle TEXT, il_norm TEXT, ilce_norm TEXT, mahalle_norm TEXT, yil INTEGER, nufus INTEGER);
                    CREATE TABLE nufus_koy (tuik_kodu INTEGER, il TEXT, ilce TEXT, koy TEXT, il_norm TEXT, ilce_norm TEXT, koy_norm TEXT, yil INTEGER, nufus INTEGER);
                    CREATE TABLE yapi_izin_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, belge TEXT, yil INTEGER, daire INTEGER);
                    CREATE TABLE yapi_izin_il (il TEXT, il_norm TEXT, belge TEXT, yil INTEGER, ceyrek TEXT, daire INTEGER);
                    CREATE TABLE konut_satis_ilce (tuik_kodu INTEGER, il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, yil INTEGER, ay INTEGER, satis INTEGER);
                    CREATE TABLE goc_il (donem TEXT, il TEXT, il_norm TEXT, nufus INTEGER, aldigi INTEGER, verdigi INTEGER, net INTEGER, net_hiz_binde REAL);
                    CREATE TABLE ses_ilce (il TEXT, ilce TEXT, il_norm TEXT, ilce_norm TEXT, yil INTEGER, ses_skor REAL, ust REAL, ust_alti REAL, orta REAL, alt REAL, en_alt REAL);
                    CREATE TABLE hanehalki_il (il TEXT, il_norm TEXT, hanehalki INTEGER, ortalama_buyukluk REAL);
                    CREATE TABLE nufus_projeksiyon_il (il TEXT, il_norm TEXT, nufus_2023 INTEGER, nufus_2030 INTEGER, yillik_artis_binde REAL);
                    CREATE TABLE kapsama (tablo TEXT PRIMARY KEY, satir INTEGER, guncellenme TEXT, kaynak TEXT);
                    INSERT INTO nufus_ilce VALUES (1,'Yalova','Çiftlikköy','yalova','ciftlikkoy',2020,40000),(1,'Yalova','Çiftlikköy','yalova','ciftlikkoy',2025,50000);
                    INSERT INTO nufus_mahalle VALUES (9,'Yalova','Çiftlikköy','Çiftlikköy Bel.','Sultaniye','yalova','ciftlikkoy','sultaniye',2025,4901);
                    INSERT INTO yapi_izin_ilce VALUES (1,'Yalova','Çiftlikköy','yalova','ciftlikkoy','ruhsat',2024,1000),(1,'Yalova','Çiftlikköy','yalova','ciftlikkoy','ruhsat',2025,1500),
                                                       (1,'Yalova','Çiftlikköy','yalova','ciftlikkoy','kullanma',2025,700);
                    INSERT INTO konut_satis_ilce VALUES (1,'Yalova','Çiftlikköy','yalova','ciftlikkoy',2025,1,100),(1,'Yalova','Çiftlikköy','yalova','ciftlikkoy',2025,2,150);
                    INSERT INTO goc_il VALUES ('2023-2024','Yalova','yalova',300000,10000,8000,2000,6.7);
                    INSERT INTO ses_ilce VALUES ('Yalova','','yalova','',2023,140,10,20,40,20,10),('Yalova','Çiftlikköy','yalova','ciftlikkoy',2023,150.04,12,22,40,16,10),('Yalova','Altınova','yalova','altinova',2023,120,5,15,40,25,15);
                """)
            e = TuikRegionEngine(p)
            r = e.analyze({"il": "Yalova", "ilce": "Çiftlikköy", "mahalle": "Kadastro Mah", "mahalle_adaylari": ["Kadastro Mah", "Sultaniye Mahallesi"]})
            self.assertTrue(r["ilce_eslesti"]); self.assertEqual(r["nufus"]["ilce_nufus"], 50000); self.assertEqual(r["nufus"]["degisim_5y_pct"], 25.0)
            self.assertEqual(r["nufus"]["mahalle"]["nufus"], 4901)                       # ikinci aday (idari mahalle) eşleşti
            self.assertEqual(r["konut_arzi"]["ruhsat_daire"], 1500); self.assertEqual(r["konut_arzi"]["ruhsat_5y_ortalamaya_gore_pct"], 50.0)
            self.assertEqual(r["konut_arzi"]["ruhsat_daire_1000_kisi"], 30.0)
            self.assertEqual(r["konut_satis"]["son_12ay"], 250); self.assertIsNone(r["konut_satis"]["degisim_12ay_pct"])
            self.assertEqual(r["ses"]["il_sira"], 1); self.assertEqual(r["ses"]["il_ilce_sayisi"], 2); self.assertEqual(r["il_ses_skor"], 140.0)
            self.assertEqual(r["nufus"]["il_goc"][0]["net"], 2000)
            r2 = e.analyze({"il": "Yalova", "ilce": "Yokilçe"})
            self.assertFalse(r2["ilce_eslesti"]); self.assertEqual(r2["konut_arzi"].get("ruhsat_daire"), None)
            self.assertIsNone(TuikRegionEngine(Path(d) / "yok.sqlite").analyze({"il": "Yalova"}))
            self.assertEqual(e.analyze({})["status"], "location_required")


if __name__ == "__main__":
    unittest.main()
