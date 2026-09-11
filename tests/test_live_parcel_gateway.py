import unittest

from geoprop.live_parcel import LiveParcelGateway, ParcelQueryError


class LiveParcelGatewayTest(unittest.TestCase):
    def test_sorgu_salt_okunur_ve_kaynak_linki_donmez(self):
        calls = []

        def process(**kwargs):
            calls.append(kwargs)
            return {
                "parsel": {
                    "il": "Yalova", "ilce": "Çiftlikköy", "mahalle": "Sultaniye",
                    "mahalle_id": 1, "ada_no": "20", "parsel_no": "20",
                    "alan_m2": 284, "nitelik": "Arsa", "zemin_durumu": "Ana taşınmaz",
                    "pafta": "", "mevkii": "", "enlem": 40.64, "boylam": 29.32,
                    "geometry": {"type": "Polygon", "coordinates": []},
                },
                "imar": {"success": False, "veri_durumu": "kaynak_erisilemedi"},
                "veri_guveni": {"kadastro": "kaynakta_dogrulandi"},
            }

        result = LiveParcelGateway(process).query({"lat": 40.64, "lon": 29.32})

        self.assertFalse(calls[0]["persist"])
        self.assertEqual(result["cadastre"]["status"], "verified_at_source")
        self.assertEqual(result["zoning"]["status"], "source_unavailable")
        self.assertIsNone(result["cadastre"]["source_url"])
        self.assertIsNone(result["zoning"]["fields"]["kaks_emsal"])

    def test_koordinatsiz_ve_parselsiz_sorgu_reddedilir(self):
        with self.assertRaises(ParcelQueryError):
            LiveParcelGateway(lambda **kwargs: None).query({"il": "Yalova"})

    def test_kismi_eplan_alani_ayri_durumla_sunulur(self):
        def process(**kwargs):
            return {
                "parsel": {
                    "il": "Yalova", "ilce": "Çiftlikköy", "mahalle": "Sultaniye",
                    "mahalle_id": 1, "ada_no": "220", "parsel_no": "4",
                    "alan_m2": 2159.34, "nitelik": "Arsa", "zemin_durumu": "Ana Taşınmaz",
                    "enlem": 40.64, "boylam": 29.32,
                    "geometry": {"type": "Polygon", "coordinates": [[[29.32, 40.64]]]},
                },
                "imar": {
                    "success": True,
                    "veri_durumu": "imar_alani_kismen_dogrulandi",
                    "kaynak": "ÇŞİDB E-Plan",
                    "plan_fonksiyon": "GELİŞME KONUT ALANI",
                    "kat_adedi": 4,
                    "kaks_emsal": None,
                    "taks": None,
                    "planlar": [{"plan_adi": "Plan", "pin_tucbs_no": "UİP-1"}],
                },
            }

        result = LiveParcelGateway(process).query({"lat": 40.64, "lon": 29.32})

        self.assertEqual(result["zoning"]["status"], "partial_zoning_verified")
        self.assertEqual(result["zoning"]["fields"]["kat_adedi"], 4)
        self.assertIsNone(result["zoning"]["fields"]["kaks_emsal"])
        self.assertEqual(result["zoning"]["plans"][0]["pin_tucbs_no"], "UİP-1")


if __name__ == "__main__":
    unittest.main()
