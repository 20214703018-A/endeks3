"""MTA jeoloji nokta sorgusu: geometri atılır, hata önbelleklenmez, kapatılabilir."""
import unittest
from unittest import mock

from geoprop.jeoloji import GeologyContextEngine


class JeolojiTest(unittest.TestCase):
    def test_ozellik_doner_geometri_yok(self):
        e = GeologyContextEngine(enabled=True)
        with mock.patch.object(e, "_get_feature_info", return_value=[{"kod": "1000", "simge": "Q", "aciklama": "Q(a): Ayrilmamis Kuvaterner", "yas": "Kuvaterner"}]) as m:
            r = e.formation_at(40.66, 29.32)
            r2 = e.formation_at(40.66004, 29.32004)   # aynı 4-ondalık hücre → önbellek
        self.assertEqual(r["status"], "available"); self.assertEqual(r["simge"], "Q"); self.assertTrue(r["kuvaterner"])
        self.assertNotIn("_geometry", r); self.assertIn("MTA", r["kaynak"])
        self.assertEqual(m.call_count, 1); self.assertEqual(r2["simge"], "Q")

    def test_hata_onbellege_alinmaz(self):
        e = GeologyContextEngine(enabled=True)
        with mock.patch.object(e, "_get_feature_info", side_effect=[OSError("down"), [{"simge": "k2pn", "yas": "Eosen"}]]) as m:
            self.assertEqual(e.formation_at(41.0, 40.5)["status"], "kaynak_erisilemedi")
            self.assertEqual(e.formation_at(41.0, 40.5)["status"], "available")
        self.assertEqual(m.call_count, 2)

    def test_kapali_ve_koordinatsiz(self):
        self.assertEqual(GeologyContextEngine(enabled=False).formation_at(41.0, 40.5)["status"], "disabled")
        self.assertEqual(GeologyContextEngine(enabled=True).formation_at(None, None)["status"], "coordinate_required")


if __name__ == "__main__":
    unittest.main()
