import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from tools.kayipsiz_bronze import CLASSIFICATION_VERSION, build_bronze
from tools.veri_katalogu import build_catalog


class KayipsizBronzeTest(unittest.TestCase):
    def test_benzersiz_icerik_bir_kez_yazilir_olusumlar_manifestte_kalir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            csv_path = root / "03_fiyat_ozet_konut_arsa.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(["id", "kategori", "satilik_m2_fiyat"])
                writer.writerow(["1", "arsa", "1000"])
                writer.writerow(["2", "konut", "2000"])
            (root / "03_fiyat_ozet_konut_arsa (1).csv").write_bytes(csv_path.read_bytes())
            (root / "city_1.json").write_text(
                json.dumps({"id": 1, "name": "Adana"}, ensure_ascii=False), encoding="utf-8"
            )

            database = root / "ilanlar.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE ilanlar (ilan_id TEXT, fiyat_tl INTEGER)")
                connection.execute("INSERT INTO ilanlar VALUES ('A1', 3000000)")
                connection.commit()
            finally:
                connection.close()

            catalog = build_catalog([root])
            output = Path(tmp) / "warehouse" / "bronze"
            report = build_bronze(catalog, output, minimum_free_bytes=0, batch_size=1)

            self.assertTrue(report["summary"]["row_balance_ok"])
            self.assertEqual(report["summary"]["source_logical_rows"], 4)
            csv_assets = [asset for asset in report["assets"] if asset["suffix"] == ".csv"]
            self.assertEqual(len(csv_assets), 1)
            self.assertEqual(csv_assets[0]["occurrence_count"], 2)
            metadata = pq.read_metadata(csv_assets[0]["output"])
            self.assertEqual(metadata.num_rows, 2)
            self.assertEqual(
                metadata.metadata[b"classification_version"], CLASSIFICATION_VERSION.encode("ascii")
            )
            self.assertEqual(sum(report["summary"]["logical_rows_by_domain"].values()), 4)
            self.assertEqual(sum(report["summary"]["logical_rows_by_data_class"].values()), 4)

            second = build_bronze(catalog, output, minimum_free_bytes=0, batch_size=1)
            self.assertTrue(second["summary"]["row_balance_ok"])
            self.assertGreaterEqual(second["summary"]["statuses"].get("reused", 0), 2)

    def test_sqlite_domain_filtresi_tablo_sinifina_gore_uygulanir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            database = root / "karma.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE ilanlar (ilan_id TEXT, fiyat_tl INTEGER)")
                connection.execute("INSERT INTO ilanlar VALUES ('A1', 3000000)")
                connection.execute("CREATE TABLE secim_sonuclari (il TEXT, oy INTEGER)")
                connection.execute("INSERT INTO secim_sonuclari VALUES ('Adana', 10)")
                connection.commit()
            finally:
                connection.close()

            catalog = build_catalog([root])
            report = build_bronze(
                catalog,
                Path(tmp) / "warehouse" / "bronze",
                domains={"listing_observation"},
                minimum_free_bytes=0,
            )

            self.assertTrue(report["summary"]["row_balance_ok"])
            self.assertEqual(report["summary"]["source_logical_rows"], 1)
            self.assertEqual(report["summary"]["logical_rows_by_domain"], {"listing_observation": 1})
            self.assertEqual([table["table"] for table in report["assets"][0]["tables"]], ["ilanlar"])

    def test_invalid_kaynak_bronze_satirina_donusturulmez_ama_manifestte_kalir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            (root / "empty.db").touch()
            (root / "empty-copy.db").touch()
            catalog = build_catalog([root])
            output = Path(tmp) / "warehouse" / "bronze"

            report = build_bronze(catalog, output, minimum_free_bytes=0)

            self.assertEqual(report["summary"]["statuses"]["invalid_preserved_in_catalog"], 1)
            self.assertTrue(report["summary"]["row_balance_ok"])
            self.assertEqual(report["assets"][0]["status"], "invalid_preserved_in_catalog")
            self.assertEqual(report["assets"][0]["occurrence_count"], 2)
            self.assertEqual(len(report["assets"][0]["all_locators"]), 2)

    def test_katalogdan_sonra_degisiklik_yapilan_kaynak_reddedilir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            source = root / "03_fiyat_ozet_konut_arsa.csv"
            source.write_text("id;fiyat\n1;100\n", encoding="utf-8")
            catalog = build_catalog([root])
            source.write_text("id;fiyat\n1;999\n", encoding="utf-8")

            report = build_bronze(
                catalog, Path(tmp) / "warehouse" / "bronze", minimum_free_bytes=0
            )

            self.assertFalse(report["summary"]["row_balance_ok"])
            self.assertEqual(report["summary"]["technical_errors"], 1)
            self.assertIn("Kaynak hash değişti", report["assets"][0]["error"])


if __name__ == "__main__":
    unittest.main()
