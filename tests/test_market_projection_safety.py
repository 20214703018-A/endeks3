import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from collector.collector import (
    PROJECTION_FORMULA_VERSION,
    init_db,
    projection_metadata,
    save_demografi,
    save_fiyat_ve_kirilimlar,
)


class MarketProjectionSafetyTest(unittest.TestCase):
    def test_provider_bayragi_ve_gelecek_donem_algilanir(self):
        reference = datetime(2026, 9, 8)
        self.assertEqual(
            projection_metadata(
                {"PropertyYear": 2026, "PropertyMonth": 10, "AnalysisType": "Static"},
                reference,
            ),
            (1, "provider_future_inferred"),
        )
        self.assertEqual(
            projection_metadata(
                {"PropertyYear": 2026, "PropertyMonth": 8, "AnalysisType": "Projection"},
                reference,
            ),
            (1, "provider_declared"),
        )

    def test_toplayici_projeksiyon_izini_veritabanina_yazar(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "piyasa.db"
            init_db(database)
            connection = sqlite3.connect(database)
            try:
                save_demografi(
                    connection,
                    "il",
                    77,
                    0,
                    0,
                    "Yalova",
                    {
                        "Demography": {
                            "Total_BB_Sale_2024": 100,
                            "Total_BBMortgaged_Sale_2024": 10,
                            "Total_AT_Sale_2024": 50,
                            "Total_Listing_2024": 200,
                        }
                    },
                )
                rows = connection.execute(
                    "SELECT yil, projeksiyon, projeksiyon_kaynagi, hesaplama_surumu "
                    "FROM yillik_satislar WHERE yil BETWEEN 2025 AND 2027 ORDER BY yil"
                ).fetchall()
                self.assertEqual(
                    rows,
                    [
                        (2025, 1, "collector_fixed_growth", PROJECTION_FORMULA_VERSION),
                        (2026, 1, "collector_fixed_growth", PROJECTION_FORMULA_VERSION),
                        (2027, 1, "collector_fixed_growth", PROJECTION_FORMULA_VERSION),
                    ],
                )

                future_year = datetime.now().year + 1
                save_fiyat_ve_kirilimlar(
                    connection,
                    "konut",
                    "il",
                    34,
                    0,
                    0,
                    "İstanbul",
                    {
                        "Trend": [
                            {
                                "PropertyYear": future_year,
                                "PropertyMonth": 1,
                                "AnalysisType": "Static",
                                "UnitPriceForSale": 100,
                            }
                        ]
                    },
                )
                trend = connection.execute(
                    "SELECT projeksiyon, projeksiyon_kaynagi, guncellenme_tarihi "
                    "FROM fiyat_trend"
                ).fetchone()
                summary = connection.execute(
                    "SELECT projeksiyon, projeksiyon_kaynagi FROM fiyat_ozet"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(trend[:2], (1, "provider_future_inferred"))
            self.assertIsNotNone(trend[2])
            self.assertEqual(summary, (1, "provider_future_inferred"))


if __name__ == "__main__":
    unittest.main()
