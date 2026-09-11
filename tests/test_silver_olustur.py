import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from tools.kayipsiz_bronze import build_bronze
from tools.silver_olustur import build_silver, row_class
from tools.veri_katalogu import build_catalog


class SilverOlusturTest(unittest.TestCase):
    def test_toplayici_yillik_satis_projeksiyon_kaynagi_taninir(self):
        for year in (2025, 2026, 2027):
            with self.subTest(year=year):
                result = row_class(
                    "observed",
                    "annual_sales",
                    {"yil": year},
                    period=str(year),
                    reference_time="2026-09-08T10:00:00",
                    locator="/paket/data/piyasa_verileri.db",
                )
                self.assertEqual(
                    result,
                    (
                        "projection",
                        "collector_fixed_growth_period",
                        "collector_fixed_growth",
                    ),
                )

    def test_satir_dengesi_projeksiyon_ayrimi_ve_dogal_anahtar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            source = root / "04_aylik_fiyat_trendi_2021_2026.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(
                    ["id", "kategori", "seviye", "city_id", "county_id", "district_id", "bolge_adi", "ay", "satilik_m2_fiyat", "projeksiyon"]
                )
                writer.writerow(["1", "konut", "il", "34", "0", "0", "İstanbul", "2025-01", "100", "0"])
                writer.writerow(["2", "konut", "il", "34", "0", "0", "İstanbul", "2027-01", "120", "1"])
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0, batch_size=1)

            self.assertTrue(silver["summary"]["row_balance_ok"])
            self.assertEqual(silver["summary"]["record_classes"], {"observed": 1, "projection": 1})
            self.assertEqual(silver["summary"]["natural_key_kinds"], {"natural": 2})
            output = silver["sources"][0]["output"]
            table = pq.read_table(output)
            self.assertEqual(table.num_rows, 2)
            self.assertEqual(table["period"].to_pylist(), ["2025-01", "2027-01"])
            self.assertEqual(len(set(table["natural_key_sha256"].to_pylist())), 2)

    def test_ayni_dogal_anahtar_farkli_degerler_silinmez(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            for name, price in (("fiyat_ozet_a.csv", "100"), ("fiyat_ozet_b.csv", "110")):
                with (root / name).open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream, delimiter=";")
                    writer.writerow(["kategori", "city_id", "county_id", "donem", "satilik_m2_fiyat"])
                    writer.writerow(["konut", "34", "0", "2026-01", price])
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)

            self.assertEqual(silver["summary"]["written_or_reused_rows"], 2)
            tables = [pq.read_table(source["output"]) for source in silver["sources"]]
            keys = [table["natural_key_sha256"][0].as_py() for table in tables]
            payloads = [table["normalized_payload_sha256"][0].as_py() for table in tables]
            self.assertEqual(keys[0], keys[1])
            self.assertNotEqual(payloads[0], payloads[1])

    def test_sqlite_tablo_sinifi_ve_invalid_olusum_korunur(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            database = root / "ilanlar.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE ilanlar (ilan_id TEXT, fiyat_tl INTEGER)")
                connection.execute("INSERT INTO ilanlar VALUES ('A1', 3000000)")
                connection.commit()
            finally:
                connection.close()
            (root / "empty.db").touch()
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)

            self.assertTrue(silver["summary"]["row_balance_ok"])
            self.assertEqual(silver["summary"]["statuses"]["invalid_preserved_in_bronze"], 1)
            ready = [source for source in silver["sources"] if source.get("output")]
            self.assertEqual(ready[0]["classification"]["domain"], "listing_observation")

    def test_projeksiyon_donemi_satiri_projeksiyon_olarak_ayrilir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            source = root / "ciro_projeksiyon.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(["id", "il", "projeksiyon_donemi", "ciro_potansiyeli_skoru"])
                writer.writerow(["1", "Ankara", "2026-2030", "75"])
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)

            self.assertEqual(silver["summary"]["record_classes"], {"projection": 1})

    def test_gelecek_donem_false_bayragini_gecersiz_kilar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            source = root / "04_aylik_fiyat_trendi_2021_2026.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(
                    [
                        "kategori",
                        "city_id",
                        "ay",
                        "satilik_m2_fiyat",
                        "projeksiyon",
                        "guncellenme_tarihi",
                    ]
                )
                writer.writerow(["konut", "34", "2026-08", "100", "0", "2026-09-08T10:00:00"])
                writer.writerow(["konut", "34", "2026-10", "110", "0", "2026-09-08T10:00:00"])
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)

            self.assertEqual(silver["summary"]["record_classes"], {"observed": 1, "projection": 1})
            table = pq.read_table(silver["sources"][0]["output"])
            self.assertEqual(
                table["record_class_reason"].to_pylist(),
                ["explicit_observed_flag", "future_period_overrides_false_flag"],
            )
            self.assertEqual(table["projection_origin"].to_pylist(), [None, "provider_future_inferred"])
            self.assertEqual(
                table["classification_reference_time"].to_pylist(),
                ["2026-09-08T10:00:00", "2026-09-08T10:00:00"],
            )

    def test_bayraksiz_gelecek_fiyat_ozeti_projeksiyondur(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            source = root / "fiyat_ozet.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(
                    ["kategori", "city_id", "donem", "satilik_m2_fiyat", "guncellenme_tarihi"]
                )
                writer.writerow(["arsa", "6", "2027-08", "500", "2026-09-08T10:00:00"])
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)

            self.assertEqual(silver["summary"]["record_classes"], {"projection": 1})
            table = pq.read_table(silver["sources"][0]["output"])
            self.assertEqual(table["record_class_reason"][0].as_py(), "future_period_after_reference")


if __name__ == "__main__":
    unittest.main()
