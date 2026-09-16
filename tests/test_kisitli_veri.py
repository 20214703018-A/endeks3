"""Kısıtlı veri katmanı: erişim politikası kararı, denetim kaydı, motorun sızdırmazlığı ve
bölge istatistik motorunun mahalle→ilçe→il kapsam düşüşü (geçici DB'lerle)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geoprop import erisim_politikasi
from geoprop.bolge_istatistik import BolgeIstatistikEngine
from geoprop.kisitli_veri import RestrictedDataEngine


def _product_db(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE ref_il (city_id INTEGER PRIMARY KEY, ad TEXT, ad_norm TEXT);
            CREATE TABLE ref_ilce (county_id INTEGER PRIMARY KEY, city_id INTEGER, ad TEXT, ad_norm TEXT);
            CREATE TABLE ref_mahalle (district_id INTEGER PRIMARY KEY, county_id INTEGER, city_id INTEGER, ad TEXT, ad_norm TEXT);
            INSERT INTO ref_il VALUES (1,'Adana','adana');
            INSERT INTO ref_ilce VALUES (1104,1,'Seyhan','seyhan');
            INSERT INTO ref_mahalle VALUES (18005,1104,1,'Kurtuluş','kurtulus');
            CREATE TABLE demografi (seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER,
                bolge_adi TEXT, mahalle_norm TEXT, nufus_toplam REAL, hane_sayisi REAL, ev_sahibi_orani REAL);
            INSERT INTO demografi VALUES ('il',1,0,0,'Adana',NULL,2300000,700000,69);
            INSERT INTO demografi VALUES ('ilce',1,1104,0,'Adana - Seyhan',NULL,786931,232849,68);
            INSERT INTO demografi VALUES ('mahalle',1,1104,37,'Adana - Seyhan - Kurtuluş','kurtulus',12000,4000,55);
            CREATE TABLE poi_ilce_ozet (city_id INTEGER, county_id INTEGER, alt_kategori TEXT, sayi INTEGER);
            INSERT INTO poi_ilce_ozet VALUES (1,1104,'Hastane',12),(1,1104,'Metro',3),(1,1104,'Cami',90);
        """)


def _restricted_db(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE secim_sonuclari (seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER,
                bolge_adi TEXT, mahalle_norm TEXT, secim_kodu TEXT, secim_adi TEXT, sandik_sayisi REAL,
                kayitli_secmen REAL, kullanilan_oy REAL, gecerli_oy REAL, kazanan_parti TEXT);
            INSERT INTO secim_sonuclari VALUES ('ilce',1,1104,0,'Adana - Seyhan',NULL,'2023genel','2023 Genel',
                1500,460000,390000,380000,'X');
            CREATE TABLE hemsehri_kutuk (seviye TEXT, city_id INTEGER, county_id INTEGER, district_id INTEGER,
                bolge_adi TEXT, mahalle_norm TEXT, kutuk_ili TEXT, kisi_sayisi REAL);
            INSERT INTO hemsehri_kutuk VALUES ('ilce',1,1104,0,'Adana - Seyhan',NULL,'ADANA',300000);
            INSERT INTO hemsehri_kutuk VALUES ('ilce',1,1104,0,'Adana - Seyhan',NULL,'MARDİN',100000);
        """)


class ErisimPolitikasiTest(unittest.TestCase):
    def test_acik_set_her_zaman_izinli(self):
        self.assertTrue(erisim_politikasi.erisim_izni_var_mi("demografi", None, None))

    def test_kisitli_set_paket_ve_sorgu_tipi_ister(self):
        f = erisim_politikasi.erisim_izni_var_mi
        self.assertFalse(f("secim_sonuclari", "anonim", "bolge_raporu"))
        self.assertFalse(f("secim_sonuclari", "uye_kurumsal", "arsa_analiz"))   # arsa analizi asla
        self.assertTrue(f("secim_sonuclari", "uye_kurumsal", "bolge_raporu"))
        self.assertTrue(f("tutun_sigara", "uye_pro", "dukkan_analiz"))
        self.assertFalse(f("tutun_sigara", "uye_pro", "bolge_raporu"))

    def test_admin_de_sorgu_tipine_bagli(self):
        f = erisim_politikasi.erisim_izni_var_mi
        self.assertTrue(f("secim_sonuclari", None, "bolge_raporu", admin_mi=True))
        self.assertFalse(f("secim_sonuclari", None, "arsa_analiz", admin_mi=True))
        self.assertTrue(f("kisisel_veriler", None, "admin_eslestirme", admin_mi=True))
        self.assertFalse(f("kisisel_veriler", "uye_kurumsal", "admin_eslestirme"))


class KisitliVeriMotoruTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.product = root / "bolge_istatistik.sqlite"; _product_db(self.product)
        self.restricted = root / "kisitli_istatistik.sqlite"; _restricted_db(self.restricted)
        self.denetim = root / "erisim_denetim.sqlite"
        self._patch = mock.patch.object(erisim_politikasi, "DENETIM_DB", self.denetim); self._patch.start()
        self.bolge = BolgeIstatistikEngine(self.product)
        self.engine = RestrictedDataEngine(self.bolge, self.restricted, root / "yok.sqlite")

    def tearDown(self):
        self._patch.stop(); self.tmp.cleanup()

    def _denetim(self):
        with sqlite3.connect(self.denetim) as c:
            return c.execute("SELECT veri_seti, uye_paketi, sorgu_tipi, izin FROM erisim_denetim ORDER BY rowid").fetchall()

    def test_anonim_hicbir_kisitli_veri_gormez_ve_denetime_yazilir(self):
        r = self.engine.profil({"il": "Adana", "ilce": "Seyhan"},
                               {"kullanici_id": "u1", "uye_paketi": "anonim", "sorgu_tipi": "bolge_raporu"})
        self.assertEqual(r["secim"], {"status": "restricted"})
        self.assertEqual(r["hemsehri"], {"status": "restricted"})
        self.assertEqual(r["tutun"], {"status": "restricted"})
        self.assertNotIn("secimler", str(r))
        izinler = self._denetim()
        self.assertEqual({row[3] for row in izinler}, {0})
        self.assertEqual({row[0] for row in izinler}, {"secim_sonuclari", "hemsehri_kutuk", "tutun_sigara"})

    def test_kurumsal_bolge_raporu_secim_ve_hemsehri_alir_tutun_almaz(self):
        r = self.engine.profil({"il": "Adana", "ilce": "Seyhan"},
                               {"kullanici_id": "u2", "uye_paketi": "uye_kurumsal", "sorgu_tipi": "bolge_raporu"})
        self.assertEqual(r["secim"]["scope"], "ilce")
        self.assertEqual(r["secim"]["secimler"][0]["kazanan"], "X")
        self.assertAlmostEqual(r["secim"]["secimler"][0]["katilim_orani"], 84.8, places=1)
        self.assertEqual(r["hemsehri"]["ilk"][0]["kutuk_ili"], "ADANA")
        self.assertEqual(r["hemsehri"]["ilk"][0]["pay"], 75.0)
        self.assertEqual(r["tutun"], {"status": "restricted"})     # tütün yalnız dükkan analizinde

    def test_arsa_analiz_sorgusu_kisitli_set_alamaz(self):
        r = self.engine.profil({"il": "Adana", "ilce": "Seyhan"},
                               {"uye_paketi": "uye_kurumsal", "sorgu_tipi": "arsa_analiz", "admin_mi": True})
        self.assertEqual(r["secim"], {"status": "restricted"})
        self.assertEqual(r["hemsehri"], {"status": "restricted"})


class BolgeIstatistikKapsamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.product = Path(self.tmp.name) / "bolge_istatistik.sqlite"; _product_db(self.product)
        self.engine = BolgeIstatistikEngine(self.product)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mahalle_kapsami_county_id_ve_mahalle_norm_ile_bulunur(self):
        p = self.engine.profil({"il": "Adana", "ilce": "Seyhan", "mahalle": "Kurtuluş Mahallesi"})
        self.assertEqual(p["ids"]["mahalle_norm"], "kurtulus")
        self.assertEqual(p["demografi"]["scope"], "mahalle")
        self.assertEqual(p["demografi"]["nufus"], 12000)

    def test_bilinmeyen_mahalle_ilceye_duser(self):
        p = self.engine.profil({"il": "Adana", "ilce": "Seyhan", "mahalle": "Yok Böyle Bir Yer"})
        self.assertEqual(p["demografi"]["scope"], "ilce")
        self.assertEqual(p["demografi"]["nufus"], 786931)

    def test_poi_ozet_gruplanir(self):
        p = self.engine.profil({"il": "Adana", "ilce": "Seyhan"})
        poi = p["poi_ilce"]
        self.assertEqual(poi["toplam"], 105)
        self.assertEqual(poi["gruplar"]["saglik"]["toplam"], 12)
        self.assertEqual(poi["gruplar"]["ulasim"]["alt"]["Metro"], 3)

    def test_db_yoksa_unavailable(self):
        self.assertEqual(BolgeIstatistikEngine(Path(self.tmp.name) / "yok.sqlite").profil({"il": "Adana"}),
                         {"status": "unavailable"})


if __name__ == "__main__":
    unittest.main()
