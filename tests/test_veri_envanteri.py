import csv
import contextlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.veri_envanteri import envanter


class VeriEnvanteriTest(unittest.TestCase):
    def test_csv_sqlite_json_ve_zip_salt_okunur_profillenir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "ornek.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(["id", "ad"])
                writer.writerow([1, "Çankaya"])

            db_path = root / "ornek.sqlite"
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE kayit (id INTEGER PRIMARY KEY, ad TEXT)")
                conn.execute("INSERT INTO kayit VALUES (1, 'Çankaya')")
                conn.commit()

            (root / "ornek.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
            with zipfile.ZipFile(root / "paket.zip", "w") as archive:
                archive.write(csv_path, "data/ornek.csv")

            report = envanter([root], zip_csv_satirlari=True, hashes=False)

            self.assertEqual(report["csv"][0]["row_count"], 1)
            self.assertEqual(report["sqlite"][0]["tables"][0]["rows"], 1)
            self.assertEqual(report["json"][0]["keys"], 1)
            self.assertEqual(report["zip"][0]["csv_entries"][0]["row_count"], 1)


if __name__ == "__main__":
    unittest.main()
