import sqlite3
import tempfile
import unittest
from pathlib import Path

from geoprop.land_analysis import LandAnalysisEngine, LandAnalysisError


SCHEMA = """
CREATE TABLE ilanlar (
    ilan_id INTEGER, kategori TEXT, baslik TEXT, il TEXT, ilce TEXT, mahalle TEXT,
    fiyat_tl REAL, m2 REAL, birim_m2_fiyat REAL, ilan_tarihi TEXT, crawled_at TEXT,
    enlem REAL, boylam REAL, ada_no TEXT, parsel_no TEXT, imar_durumu TEXT,
    kaks_emsal TEXT
)
"""


class LandAnalysisTest(unittest.TestCase):
    def make_database(self, root: Path) -> Path:
        path = root / "ilanlar.db"
        with sqlite3.connect(path) as connection:
            connection.execute(SCHEMA)
            for index, unit_price in enumerate((9000, 9500, 10000, 10500, 11000, 100000), 1):
                area = 250 + index * 10
                connection.execute(
                    "INSERT INTO ilanlar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        index, "arsa", f"Emsal {index}", "Yalova", "Çiftlikköy",
                        "Sultaniye Mahallesi", unit_price * area, area, unit_price,
                        "2026-08-01", "2026-09-10T00:00:00", 40.64 + index / 10000,
                        29.32 + index / 10000, "20", str(index), "Konut İmarlı", None,
                    ),
                )
        return path

    def test_emsal_secimi_aykiri_fiyati_dislar_ve_link_dondurmez(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LandAnalysisEngine(self.make_database(Path(tmp)))
            result = engine.analyze(
                {
                    "il": "Yalova", "ilce": "Çiftlikköy",
                    "mahalle": "Sultaniye Mahallesi", "alan_m2": 284,
                    "lat": 40.6438, "lon": 29.3246,
                    "sekil": "regular", "yol": "cadastral_frontage",
                }
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["comparable_selection"]["removed_outlier_count"], 1)
            self.assertEqual(result["comparable_selection"]["selected_count"], 5)
            self.assertGreater(result["valuation"]["estimated_total_price"], 0)
            self.assertFalse(result["data_policy"]["public_source_links"])
            self.assertTrue(result["data_policy"]["internal_provenance_retained"])
            self.assertNotIn("url", result["comparable_selection"]["comparables"][0])

    def test_dogrulanmis_imar_parametrelerinden_ornek_proje_hesaplanir(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LandAnalysisEngine(self.make_database(Path(tmp)))
            live = {
                "parcel": {"area_m2": 500, "province": "Yalova"},
                "zoning": {
                    "status": "verified_at_source",
                    "fields": {"kaks_emsal": 1.5, "taks": 0.3, "kat_adedi": 5},
                },
            }
            result = engine.analyze({"il": "Yalova", "alan_m2": 500}, live)

            self.assertEqual(result["example_project"]["total_gross_construction_m2"], 750)
            self.assertEqual(result["example_project"]["max_footprint_m2"], 150)
            self.assertEqual(result["example_project"]["indicative_floor_count"], 5)

    def test_gecersiz_alan_reddedilir(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LandAnalysisEngine(self.make_database(Path(tmp)))
            with self.assertRaises(LandAnalysisError):
                engine.analyze({"il": "Yalova", "alan_m2": 0})

    def test_canli_kadastro_alani_girdiden_onceliklidir(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LandAnalysisEngine(self.make_database(Path(tmp)))
            live = {
                "parcel": {
                    "area_m2": 2159.34, "province": "Yalova", "district": "Çiftlikköy",
                    "neighbourhood": "Sultaniye", "block": "220", "parcel": "4",
                    "lat": 40.64, "lon": 29.32,
                },
                "zoning": {"status": "source_unavailable", "fields": {}},
            }
            result = engine.analyze(
                {"il": "Yalova", "ilce": "Çiftlikköy", "alan_m2": 284, "ada": "20", "parsel": "1"},
                live,
            )

            self.assertEqual(result["submitted_input"]["alan_m2"], 284)
            self.assertEqual(result["input"]["alan_m2"], 2159.34)
            self.assertIn("uyuşmadı", result["warnings"][0])
            self.assertNotIn(
                1,
                [item["listing_id"] for item in result["comparable_selection"]["comparables"]],
            )


if __name__ == "__main__":
    unittest.main()
