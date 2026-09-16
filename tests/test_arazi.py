"""DEM arazi motoru: sentetik yamaçta eğim/bakı doğru, kaynak hatası dürüst, kapatılabilir."""
import unittest
from unittest import mock

import numpy as np

from geoprop.arazi import TerrainEngine


class AraziTest(unittest.TestCase):
    def test_sentetik_yamac_egim_ve_baki(self):
        e = TerrainEngine(enabled=True)
        cell = 7.0
        n = 45
        # Güneye (satır arttıkça = güney) inen %20 eğimli yamaç: her hücre 1.4 m düşer
        rows = np.arange(n)[:, None].repeat(n, axis=1)
        elev = 100.0 - rows * cell * 0.20
        with mock.patch.object(e, "_window", return_value=(elev, cell)):
            r = e.analyze(40.0, 30.0)
        self.assertEqual(r["status"], "available")
        self.assertAlmostEqual(r["parsel_90m"]["egim_pct"]["ort"], 20.0, delta=0.5)
        self.assertEqual(r["parsel_90m"]["sinif"], "orta")
        self.assertEqual(r["parsel_90m"]["baki_yon"], "G")
        self.assertEqual(r["kapsam"], "bolgesel"); self.assertIn("30 m", r["not"])

    def test_kaynak_hatasi(self):
        e = TerrainEngine(enabled=True)
        with mock.patch.object(e, "_window", side_effect=OSError("down")):
            self.assertEqual(e.analyze(40.0, 30.0)["status"], "kaynak_erisilemedi")

    def test_kapali_ve_koordinatsiz(self):
        self.assertEqual(TerrainEngine(enabled=False).analyze(40.0, 30.0)["status"], "disabled")
        self.assertEqual(TerrainEngine(enabled=True).analyze(None, None)["status"], "coordinate_required")


if __name__ == "__main__":
    unittest.main()
