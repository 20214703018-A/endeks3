import csv
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.veri_katalogu import build_catalog, classify, main


class VeriKataloguTest(unittest.TestCase):
    def test_fiziksel_ve_arsiv_kopyalari_silinmeden_hash_ile_gruplanir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "03_fiyat_ozet_konut_arsa.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(["id", "kategori", "satilik_m2_fiyat"])
                writer.writerow(["1", "arsa", "1000"])
            copied = root / "03_fiyat_ozet_konut_arsa (1).csv"
            copied.write_bytes(csv_path.read_bytes())

            nested_payload = io.BytesIO()
            with zipfile.ZipFile(nested_payload, "w") as nested:
                nested.writestr("data/03_fiyat_ozet_konut_arsa.csv", csv_path.read_bytes())
            with zipfile.ZipFile(root / "paket.zip", "w") as archive:
                archive.writestr("data/03_fiyat_ozet_konut_arsa.csv", csv_path.read_bytes())
                archive.writestr("nested.zip", nested_payload.getvalue())

            catalog = build_catalog([root], max_archive_depth=3)
            target_groups = [
                group for group in catalog["exact_duplicate_groups"] if group["occurrence_count"] == 4
            ]

            self.assertEqual(len(target_groups), 1)
            self.assertEqual(catalog["summary"]["physical_file_occurrences"], 3)
            self.assertEqual(catalog["summary"]["archive_member_occurrences"], 3)
            self.assertEqual(catalog["summary"]["csv_rows_with_occurrences"], 4)
            self.assertEqual(catalog["summary"]["errors"], 0)

    def test_siniflandirma_urun_kisitlarini_ve_projeksiyonu_ayirir(self):
        election = classify("07_secim_sonuclari_ve_oylar.csv", ["kazanan_parti"])
        trend = classify("04_aylik_fiyat_trendi.csv", ["ay", "projeksiyon"])
        advisor = classify("10_gayrimenkul_danismanlari.csv", ["danisman_adi", "telefon"])

        self.assertEqual(election["domain"], "restricted_context")
        self.assertEqual(election["product_policy"], "exclude_from_property_scoring_and_targeting")
        self.assertEqual(trend["domain"], "property_market")
        self.assertEqual(trend["data_class"], "mixed_observed_projection")
        self.assertEqual(advisor["privacy"], "personal_data_review")

    def test_bilinen_sentetik_parsel_tablolari_karantinaya_alinir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "parsel_imar_degisiklikleri.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE parsel_imar_kayitlari "
                    "(id INTEGER PRIMARY KEY, ada_no TEXT, parsel_no TEXT, kaks_emsal REAL)"
                )
                connection.execute(
                    "INSERT INTO parsel_imar_kayitlari VALUES (1, '10', '20', 1.5)"
                )
                connection.commit()
            finally:
                connection.close()

            catalog = build_catalog([root])
            table = catalog["sqlite_tables"][0]

            self.assertEqual(table["classification"]["data_class"], "quarantine")
            self.assertEqual(table["classification"]["product_policy"], "exclude_from_canonical_outputs")

    def test_cli_calistir_bayragi_olmadan_taramayi_reddeder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main([tmp])
            self.assertEqual(context.exception.code, 2)

    def test_bos_veritabani_olusumu_kaybolmadan_invalid_isaretlenir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "emlakjet_veri.db"
            database.touch()

            catalog = build_catalog([root])
            occurrence = catalog["occurrences"][0]

            self.assertEqual(occurrence["validation_status"], "invalid")
            self.assertEqual(occurrence["classification"]["data_class"], "invalid")
            self.assertEqual(catalog["summary"]["errors"], 1)


if __name__ == "__main__":
    unittest.main()
