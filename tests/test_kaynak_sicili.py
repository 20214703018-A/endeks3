import json
import tempfile
import unittest
from pathlib import Path

from tools.kaynak_sicili import build_registry


class KaynakSiciliTest(unittest.TestCase):
    def setUp(self):
        definitions_path = Path(__file__).parents[1] / "config" / "kaynak_sicili_tanimlari.json"
        self.definitions = json.loads(definitions_path.read_text(encoding="utf-8"))

    def test_her_silver_birimi_tek_kaynaga_baglanir(self):
        silver = {
            "generated_at": "2026-09-10T10:00:00+00:00",
            "summary": {"source_logical_rows": 12},
            "sources": [
                {
                    "content_sha256": "a" * 64,
                    "source_table": "fiyat_trend",
                    "source_locator": "/paket/piyasa.db",
                    "all_locators": ["/paket/piyasa.db"],
                    "source_occurrence_count": 1,
                    "rows": 10,
                    "status": "written",
                    "classification": {
                        "domain": "property_market",
                        "entity": "price_trend",
                        "data_class": "mixed_observed_projection",
                    },
                },
                {
                    "content_sha256": "b" * 64,
                    "source_table": "",
                    "source_locator": "/paket/bilinmeyen.bin",
                    "all_locators": ["/paket/bilinmeyen.bin"],
                    "source_occurrence_count": 1,
                    "rows": 2,
                    "status": "invalid_preserved_in_bronze",
                },
            ],
        }
        catalog = {
            "generated_at": "2026-09-10T09:00:00+00:00",
            "occurrences": [
                {
                    "content_sha256": "a" * 64,
                    "modified": "2026-09-08T09:00:00+00:00",
                }
            ],
        }
        registry = build_registry(silver, catalog, self.definitions)

        self.assertTrue(registry["summary"]["mapping_balance_ok"])
        self.assertTrue(registry["summary"]["row_balance_ok"])
        self.assertEqual(registry["summary"]["fallback_source_units"], 1)
        self.assertEqual(
            registry["mappings"][0]["source_id"],
            "emlakjet_endeksa_market_collected",
        )
        self.assertEqual(registry["mappings"][1]["source_id"], "unregistered_source")
        self.assertTrue(registry["mappings"][0]["public_display_allowed"])
        self.assertEqual(
            registry["mappings"][0]["license_status"],
            "user_publication_rights_declared",
        )

    def test_hak_beyani_upstream_haklarini_haric_tutar(self):
        rights = self.definitions["rights_declaration"]
        self.assertTrue(rights["asserted_by_user"])
        self.assertIn("ozgun_model_ciktilari", rights["ownership_scope"])
        self.assertIn(
            "ucuncu_taraf_sitelerden_gelen_ham_icerik",
            rights["excluded_from_ownership_claim"],
        )
        self.assertTrue(rights["publication_rights_asserted_by_user"])
        self.assertFalse(rights["public_source_links_enabled"])
        self.assertTrue(rights["internal_provenance_retained"])
        self.assertFalse(rights["effective_after_identity_confirmation"])


if __name__ == "__main__":
    unittest.main()
