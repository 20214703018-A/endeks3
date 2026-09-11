import csv
import tempfile
import unittest
from pathlib import Path

import duckdb

from tools.gold_gorunumleri import build_gold
from tools.kayipsiz_bronze import build_bronze
from tools.silver_olustur import build_silver
from tools.veri_katalogu import build_catalog


class GoldGorunumleriTest(unittest.TestCase):
    def test_catisma_korunur_kanonik_gorunum_tek_satir_secer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            for name, price in (("fiyat_ozet_a.csv", "100"), ("fiyat_ozet_b.csv", "110")):
                with (root / name).open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream, delimiter=";")
                    writer.writerow(["kategori", "city_id", "donem", "satilik_m2_fiyat"])
                    writer.writerow(["konut", "34", "2026-01", price])
            (root / "empty.db").touch()
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)
            database = Path(tmp) / "gold" / "gold.duckdb"
            gold = build_gold(silver, database)

            self.assertEqual(gold["counts"]["silver_observations"], 2)
            self.assertEqual(gold["counts"]["data_conflict_groups"], 1)
            self.assertEqual(gold["counts"]["resolved_conflict_groups"], 1)
            self.assertEqual(gold["counts"]["canonical_rows"], 1)
            connection = duckdb.connect(str(database), read_only=True)
            try:
                status = connection.execute(
                    "SELECT canonical_status FROM canonical_resolution"
                ).fetchone()[0]
                conflict_status = connection.execute(
                    "SELECT conflict_status FROM canonical_resolution"
                ).fetchone()[0]
                decision = connection.execute(
                    "SELECT decision_status, selection_reason, resolution_confidence "
                    "FROM conflict_resolution_decisions"
                ).fetchone()
                provenance = connection.execute(
                    "SELECT count(*) FROM canonical_with_provenance"
                ).fetchone()[0]
                invalid_sources = connection.execute(
                    "SELECT count(*) FROM source_provenance WHERE status = 'invalid_preserved_in_bronze'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(status, "provisional_unverified_source")
            self.assertEqual(conflict_status, "resolved_deterministically")
            self.assertEqual(decision[0], "resolved_for_internal_canonical")
            self.assertIn(decision[1], {"stable_content_hash_tiebreak", "latest_collection_time"})
            self.assertIn(decision[2], {"low", "medium"})
            self.assertEqual(provenance, 1)
            self.assertEqual(invalid_sources, 1)

    def test_gelecek_piyasa_satiri_kanonige_girmez_ve_denetime_yansir(self):
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
            database = Path(tmp) / "gold" / "gold.duckdb"
            gold = build_gold(silver, database)

            self.assertEqual(gold["counts"]["canonical_rows"], 1)
            self.assertEqual(gold["counts"]["market_projection_observations"], 1)
            self.assertEqual(gold["counts"]["future_dated_observed_anomalies"], 0)
            connection = duckdb.connect(str(database), read_only=True)
            try:
                audit = connection.execute(
                    "SELECT record_class_reason, projection_origin, observation_count "
                    "FROM market_projection_audit"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(
                audit,
                ("future_period_overrides_false_flag", "provider_future_inferred", 1),
            )

    def test_yayin_ve_skorlama_kapisi_onayli_kaynak_ister(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sources"
            root.mkdir()
            source = root / "fiyat_ozet.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(["kategori", "city_id", "donem", "satilik_m2_fiyat"])
                writer.writerow(["konut", "34", "2026-08", "100"])
            catalog = build_catalog([root])
            bronze = build_bronze(catalog, Path(tmp) / "bronze", minimum_free_bytes=0)
            silver = build_silver(bronze, catalog, Path(tmp) / "silver", minimum_free_bytes=0)
            unit = silver["sources"][0]
            registry = {
                "mappings": [
                    {
                        "content_sha256": unit["content_sha256"],
                        "source_table": unit["source_table"],
                        "source_id": "user_model",
                        "registry_status": "approved",
                        "license_status": "user_owned_verified",
                        "public_display_allowed": True,
                        "scoring_allowed": True,
                        "internal_quality_review_allowed": True,
                        "attribution_required": False,
                        "retention_policy": "owner_controlled",
                        "freshness_sla_days": 30,
                        "reviewed_at": "2026-09-10",
                    }
                ]
            }
            database = Path(tmp) / "gold" / "gold.duckdb"
            gold = build_gold(silver, database, registry)

            self.assertEqual(gold["counts"]["registered_source_units"], 1)
            self.assertEqual(gold["counts"]["source_governance_gaps"], 0)
            self.assertEqual(gold["counts"]["publishable_canonical"], 1)
            self.assertEqual(gold["counts"]["scoring_eligible_canonical"], 1)


if __name__ == "__main__":
    unittest.main()
