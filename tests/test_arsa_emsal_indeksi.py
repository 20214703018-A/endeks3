import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tools.arsa_emsal_indeksi import build_index


class ArsaEmsalIndeksiTest(unittest.TestCase):
    def test_yayin_hakki_olan_arsa_link_olmadan_indekslenir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "silver"
            target = root / "cadastre_planning" / "parcel_zoning"
            target.mkdir(parents=True)
            content_hash = "a" * 64
            payload = {
                "ilan_id": "10", "kategori": "arsa", "tip": "Satılık Arsa",
                "il": "Yalova", "ilce": "Merkez", "mahalle": "Safran",
                "fiyat_tl": 2_000_000, "m2": 400, "m2_birim_fiyat": 5_000,
                "ilan_pin_lat": 40.6, "ilan_pin_lon": 29.2,
                "url": "https://example.test/ilan/10",
            }
            table = pa.Table.from_pylist([
                {
                    "observation_id": bytes.fromhex("01" * 32),
                    "source_content_sha256": bytes.fromhex(content_hash),
                    "source_table": "ilanlar",
                    "natural_key_sha256": bytes.fromhex("02" * 32),
                    "record_class": "observed",
                    "validation_status": "valid",
                    "product_policy": "eligible_after_quality_and_license_validation",
                    "quality_score": 80,
                    "collection_time": "2026-09-10T00:00:00",
                    "period": "2026-09",
                    "normalized_record_json": json.dumps(payload),
                }
            ])
            pq.write_table(table, target / "source-ilanlar.parquet")
            registry = {
                "mappings": [{
                    "content_sha256": content_hash,
                    "source_table": "ilanlar",
                    "domain": "cadastre_planning",
                    "entity": "parcel_zoning",
                    "registry_status": "owner_rights_declared",
                    "license_status": "user_publication_rights_declared",
                    "public_display_allowed": True,
                }]
            }
            output = Path(tmp) / "product" / "arsa.sqlite"
            report = build_index(root, registry, output)

            self.assertEqual(report["row_count"], 1)
            with sqlite3.connect(output) as connection:
                row = connection.execute(
                    "SELECT ilan_id, source_url, source_content_sha256 FROM ilanlar"
                ).fetchone()
            self.assertEqual(row, (10, None, content_hash))


if __name__ == "__main__":
    unittest.main()
