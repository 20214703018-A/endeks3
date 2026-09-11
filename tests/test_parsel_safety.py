import tempfile
import unittest
import contextlib
from pathlib import Path

from collector.parsel_imar_ve_degisiklik_toplayici import (
    ParselImarToplayici,
    init_database,
    parse_localized_number,
)


class _FailedResponse:
    status_code = 503


class _FailedSession:
    def get(self, *args, **kwargs):
        return _FailedResponse()

    def post(self, *args, **kwargs):
        return _FailedResponse()


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _OfficialEPlanSession:
    def get(self, url, **kwargs):
        if url.endswith("/fSession/getSessionInfo"):
            return _Response(204)
        if url.endswith("/fSession/loginAsGuest"):
            return _Response(200, {})
        if url.endswith("/planGML/getPlanLayerData"):
            return _Response(200, [{
                "tableName": "uip_konut",
                "columns": ["Adı", "Emsal Kaks", "TAKS", "Kat Adedi", "Ön Bahçe Mesafesi", "Yan Bahçe Mesafesi"],
                "data": [["GELİŞME KONUT ALANI", "0", "0", "4", "5", "3"]],
            }])
        raise AssertionError(url)

    def post(self, url, **kwargs):
        return _Response(200, [{
            "recID": "plan-1",
            "3f52b328-525b-49cb-a126-b0b2569a1553": "Örnek Uygulama İmar Planı",
            "319c08f6-0971-45c9-aef6-4ed53c108d45": "UİP-1",
            "4e0d4291-130b-410a-8fee-f78309bac985": "1",
            "24da024e-bcba-425e-991a-5480164990ba": "2025-02-25T12:09:41",
            "5301b42b-4818-45a7-acd9-d25621db5197_3b452987-6f95-4b19-906e-7394e8f78723": "Uygulama İmar Planı",
            "a895b410-68e4-4960-888b-c73321ac865f_2c9e87e1-d52c-4a71-acf7-b48e39f6044a": "1000",
            "e5478ae0-d4f2-4555-ae12-401d68612cf0_fce47bc6-1bbe-45bb-b7c7-03ee71a2ffbb": "Kesinleşti",
        }])


class ParselSafetyTest(unittest.TestCase):
    def test_tkgm_alan_bicimleri_dogru_ayristirilir(self):
        self.assertEqual(parse_localized_number("2,159.34"), 2159.34)
        self.assertEqual(parse_localized_number("2.159,34"), 2159.34)
        self.assertEqual(parse_localized_number("2159,34"), 2159.34)
        self.assertEqual(parse_localized_number(2159.34), 2159.34)

    def test_eplan_hatasi_varsayilan_imar_uretmez(self):
        collector = ParselImarToplayici.__new__(ParselImarToplayici)
        collector.session = _FailedSession()
        collector.headers_eplan = {}

        result = collector.fetch_eplan_and_zoning(
            "Ankara", "Çankaya", "Aziziye", "1", "2", lat=39.0, lon=32.0
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["veri_durumu"], "kaynak_erisilemedi")
        self.assertIsNone(result["kaks_emsal"])
        self.assertIsNone(result["taks"])
        self.assertIsNone(result["pin_tucbs_no"])

    def test_resmi_eplan_fonksiyonunu_cekmek_sifir_kaks_taks_uretmez(self):
        collector = ParselImarToplayici.__new__(ParselImarToplayici)
        collector.session = _OfficialEPlanSession()
        collector.headers_eplan = {}

        result = collector.fetch_eplan_and_zoning(
            "Yalova", "Çiftlikköy", "Sultaniye", "220", "4", lat=40.64, lon=29.32
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["veri_durumu"], "imar_alani_kismen_dogrulandi")
        self.assertEqual(result["plan_fonksiyon"], "GELİŞME KONUT ALANI")
        self.assertEqual(result["kat_adedi"], 4.0)
        self.assertEqual(result["on_bahce"], 5.0)
        self.assertEqual(result["yan_bahce"], 3.0)
        self.assertIsNone(result["kaks_emsal"])
        self.assertIsNone(result["taks"])
        self.assertEqual(result["pin_tucbs_no"], "UİP-1")

    def test_bagimsiz_bolum_kaydi_modellenmez(self):
        collector = ParselImarToplayici.__new__(ParselImarToplayici)
        self.assertEqual(
            collector.fetch_bagimsiz_bolumler(1, "2", "3", "Kat Mülkiyet", 1400),
            [],
        )

    def test_salt_okunur_sorgu_kaydetmez(self):
        collector = ParselImarToplayici.__new__(ParselImarToplayici)
        collector.fetch_tkgm_megsis = lambda **kwargs: {
            "success": True,
            "il": "Ankara",
            "ilce": "Çankaya",
            "mahalle": "Aziziye",
            "mahalle_id": 1,
            "ada_no": "2",
            "parsel_no": "3",
            "alan_m2": 100.0,
            "nitelik": "Arsa",
            "zemin_durumu": "Ana Taşınmaz",
            "pafta": "",
            "mevkii": "",
            "enlem": 39.0,
            "boylam": 32.0,
            "geometry": {},
        }
        collector.fetch_eplan_and_zoning = lambda *args, **kwargs: {
            "success": False,
            "veri_durumu": "kaynak_erisilemedi",
            "aski_degisiklikleri": [],
        }
        collector.fetch_bagimsiz_bolumler = lambda *args, **kwargs: []
        collector.save_to_db = lambda *args, **kwargs: self.fail("salt-okunur sorgu yazmamalı")

        result = collector.process_parsel(lat=39.0, lon=32.0, persist=False)

        self.assertEqual(result["veri_guveni"]["kadastro"], "kaynakta_dogrulandi")
        self.assertEqual(result["veri_guveni"]["imar"], "kaynak_erisilemedi")

    def test_cikis_ek_veritabani_hedefi_kullanilir(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "grup.sqlite"
            init_database(str(db_path))
            collector = ParselImarToplayici.__new__(ParselImarToplayici)
            collector.db_path = str(db_path)
            parsel = {
                "il": "Ankara", "ilce": "Çankaya", "mahalle": "Aziziye",
                "mahalle_id": 1, "ada_no": "2", "parsel_no": "3", "alan_m2": 100.0,
                "nitelik": "Arsa", "zemin_durumu": "Ana Taşınmaz", "pafta": "",
                "mevkii": "", "enlem": 39.0, "boylam": 32.0, "geometry": {},
            }
            imar = {"success": False, "aski_degisiklikleri": []}

            collector.save_to_db(parsel, imar, [])

            import sqlite3
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parsel_imar_kayitlari").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT kaynak FROM parsel_imar_kayitlari").fetchone()[0], "TKGM MEGSİS")


if __name__ == "__main__":
    unittest.main()
