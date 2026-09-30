import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SERVER_PATH = Path(__file__).parents[1] / "collector" / "extension_bot" / "server.py"
SPEC = importlib.util.spec_from_file_location("extension_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class ExtensionServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = server.DB_MENU
        server.DB_MENU = Path(self.temp.name) / "menu.sqlite"
        self.conn = server.connect_menu_db()
        server.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        server.DB_MENU = self.old_db
        self.temp.cleanup()

    def venue(self):
        return {
            "id": "place-1", "adi": "Test Restoran", "kategori": "Restoran",
            "tum_kategoriler": '["Restoran"]', "degerlendirme_sayisi": 25,
            "tam_adres": "Test", "mahalle": "Merkez", "ilce": "Konak", "il": "İzmir",
            "ilce_nufusu": 100000, "mahalle_nufusu": 5000,
            "lat": 38.4, "lon": 27.1, "maps_url": "https://www.google.com/maps/test",
            "google_kaynagi": "Google Maps", "query": "Test Restoran İzmir menü",
        }

    def test_target_city_normalization_accepts_ascii_google_shards(self):
        self.assertEqual(server.canonical_target_city("Istanbul"), "İstanbul")
        self.assertEqual(server.canonical_target_city("Izmir"), "İzmir")
        self.assertEqual(server.canonical_target_city("Canakkale"), "Çanakkale")
        self.assertEqual(server.canonical_target_city("Diyarbakir"), "Diyarbakır")
        self.assertEqual(server.canonical_target_city("Adana"), "Adana")  # 81 il kapsamı
        self.assertEqual(server.canonical_target_city("Sanliurfa"), "Şanlıurfa")
        self.assertIsNone(server.canonical_target_city("Otomotiv"))  # kaymış sütunlu shard satırı
        self.assertEqual(len(server.ALL_CITIES), 81)

    def test_food_category_rejects_internet_and_gaming_cafes(self):
        self.assertFalse(server.is_food_category("Kafe", None, "Moss Internet Cafe"))
        self.assertFalse(server.is_food_category("Kafe", None, "Apex E-Spor Game Center"))
        self.assertFalse(server.is_food_category("Kafe", None, "PlayStation Cafe"))
        self.assertFalse(server.is_food_category("Kafe", None, "Avrupa Internet ve Oyun Cafe"))
        self.assertFalse(server.is_food_category("Kafe", None, "Thor Gaming Cafe"))
        self.assertFalse(server.is_food_category("Kafe", None, "Cadde Bilardo Cafe"))
        self.assertFalse(server.is_food_category("Kafe", None, "Cafe de Coiffeur Berber Kuaför"))
        self.assertFalse(server.is_food_category("Kafe", None, "Yağmur Kırtasiye ve Kafeterya"))
        self.assertTrue(server.is_food_category("Kafe", None, "Moda Kahve Evi"))

    def test_queue_resumes_same_lease(self):
        server.sync_queue(self.conn, [self.venue()])
        first = server.lease_next(self.conn)
        second = server.lease_next(self.conn)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["lease_token"], second["lease_token"])
        self.assertTrue(second["resumed"])

    def test_queue_leases_west_metros_and_high_yield_categories_first(self):
        base = self.venue()
        venues = [
            {**base, "id": "ankara-pizza", "il": "Ankara", "kategori": "Pizza"},
            {**base, "id": "izmir-lokanta", "il": "İzmir", "kategori": "Restoran & Lokanta"},
            {**base, "id": "izmir-pizza", "il": "İzmir", "kategori": "Pizza"},
            {**base, "id": "adana-pizza", "il": "Adana", "kategori": "Pizza"},
        ]
        # geçmiş: Pizza 3/4 menülü, Restoran & Lokanta 0/4
        history = [("h-p%d" % i, "Pizza", "completed" if i < 3 else "completed_no_menu") for i in range(4)]
        history += [("h-l%d" % i, "Restoran & Lokanta", "completed_no_menu") for i in range(4)]
        for venue_id, category, status in history:
            self.conn.execute(
                "INSERT INTO menu_tarama_kuyrugu (mekan_id, payload_json, durum, created_at, updated_at) VALUES (?,?,?,?,?)",
                (venue_id, server.json.dumps({"kategori": category, "il": "İzmir"}), status, "t", "t"),
            )
        server.sync_queue(self.conn, venues)
        order = []
        for _ in venues:
            leased = server.lease_next(self.conn)
            order.append(leased["id"])
            self.conn.execute("UPDATE menu_tarama_kuyrugu SET durum='completed' WHERE mekan_id=?", (leased["id"],))
        self.assertEqual(order, ["izmir-pizza", "izmir-lokanta", "ankara-pizza", "adana-pizza"])

    def test_false_failures_requeued_once(self):
        server.sync_queue(self.conn, [self.venue()])
        self.conn.execute(
            "UPDATE menu_tarama_kuyrugu SET durum='failed', deneme_sayisi=2, son_hata=? WHERE mekan_id='place-1'",
            ("Zaman aşımı: içerik betiği 0.5 dk içinde yanıt vermedi | url: x",),
        )
        self.assertEqual(server.requeue_known_false_failures(self.conn), 1)
        row = self.conn.execute("SELECT durum, deneme_sayisi FROM menu_tarama_kuyrugu").fetchone()
        self.assertEqual((row["durum"], row["deneme_sayisi"]), ("queued", 0))
        self.conn.execute("UPDATE menu_tarama_kuyrugu SET durum='failed', son_hata='Zaman aşımı: y'")
        self.assertEqual(server.requeue_known_false_failures(self.conn), 0)  # tek seferlik

    def test_queue_counts_by_city_uses_canonical_payload_city(self):
        server.sync_queue(self.conn, [self.venue()])
        self.assertEqual(server.queue_counts_by_city(self.conn), {"İzmir": 1})

    def test_save_persists_provider_price_and_image(self):
        server.sync_queue(self.conn, [self.venue()])
        leased = server.lease_next(self.conn)
        result = server.save_result(self.conn, {
            "id": leased["id"], "lease_token": leased["lease_token"],
            "source_url": "https://www.google.com/search?q=test",
            "degerlendirme_sayisi": 25,
            "fiyat_saglayici": "Yemeksepeti", "saglayici_kaniti": "Sağlayan: Yemeksepeti",
            "fiyatlar": [{
                "urun": "Kebap", "fiyat": 350, "ham_fiyat_metni": "350 TL",
                "saglayici": "Yemeksepeti", "saglayici_kaniti": "Sağlayan: Yemeksepeti",
                "yakalama_yontemi": "test", "guven_puani": 0.9,
            }],
            "gorseller": [{"url": "https://example.com/menu.jpg", "guven_puani": 0.9}],
        })
        self.assertEqual(result["prices"], 1)
        self.assertEqual(result["images"], 1)
        provider = self.conn.execute("SELECT fiyat_saglayici FROM menu_fiyat_gozlemleri").fetchone()[0]
        self.assertEqual(provider, "Yemeksepeti")
        status = self.conn.execute("SELECT durum FROM menu_tarama_kuyrugu").fetchone()[0]
        self.assertEqual(status, "completed")

    def test_failed_save_does_not_advance_queue(self):
        server.sync_queue(self.conn, [self.venue()])
        leased = server.lease_next(self.conn)
        with self.assertRaises(ValueError):
            server.save_result(self.conn, {
                "id": leased["id"], "lease_token": "wrong",
                "source_url": "https://www.google.com/"
            })
        status = self.conn.execute("SELECT durum FROM menu_tarama_kuyrugu").fetchone()[0]
        self.assertEqual(status, "in_progress")

    def test_dashboard_queries_report_saved_prices_and_images(self):
        server.sync_queue(self.conn, [self.venue()])
        lease = server.lease_next(self.conn)
        server.save_result(self.conn, {
            "id": lease["id"], "lease_token": lease["lease_token"],
            "source_url": "https://www.google.com/search?q=test", "fiyat_saglayici": "Yemeksepeti",
            "fiyatlar": [{"urun": "Kebap", "fiyat": 350, "guven_puani": 0.9}],
            "gorseller": [{"url": "https://lh3.googleusercontent.com/a", "guven_puani": 0.85}],
        })
        totals = server.dashboard_totals(self.conn)
        self.assertEqual((totals["prices"], totals["images"]), (1, 1))
        self.assertEqual(totals["providers"], {"Yemeksepeti": 1})
        recent = server.recent_venues(self.conn)
        self.assertEqual(len(recent), 1)
        self.assertEqual((recent[0]["durum"], recent[0]["fiyat"], recent[0]["gorsel"]), ("completed", 1, 1))
        detail = server.venue_detail(self.conn, "place-1")
        self.assertEqual(detail["fiyatlar"][0]["urun_adi"], "Kebap")
        self.assertEqual(detail["gorseller"][0]["gorsel_url"], "https://lh3.googleusercontent.com/a")
        self.assertIsNone(server.venue_detail(self.conn, "yok"))

    def test_images_are_downloaded_and_shrunk_to_disk(self):
        import io
        from PIL import Image
        server.sync_queue(self.conn, [self.venue()])
        lease = server.lease_next(self.conn)
        server.save_result(self.conn, {
            "id": lease["id"], "lease_token": lease["lease_token"],
            "source_url": "https://www.google.com/search?q=test", "fiyat_saglayici": "Google",
            "fiyatlar": [],
            "gorseller": [
                {"url": "https://lh3.googleusercontent.com/a=w1080-h1080", "guven_puani": 0.85, "bolum": "Menüyü göster"},
                {"url": "https://lh3.googleusercontent.com/b=w1080-h1080", "guven_puani": 0.85, "bolum": "Menüde öne çıkanlar"},
            ],
        })
        big = Image.new("RGB", (3000, 2000), (200, 30, 30))
        buffer = io.BytesIO(); big.save(buffer, "PNG")
        requested = []
        old_fetch, old_dir = server.fetch_image_bytes, server.IMAGE_DIR
        server.fetch_image_bytes = lambda url: (requested.append(url), buffer.getvalue())[1]
        server.IMAGE_DIR = Path(self.temp.name) / "img"
        try:
            self.assertEqual(server.download_pending_images(), {"done": 2, "failed": 0})
        finally:
            server.fetch_image_bytes, server.IMAGE_DIR = old_fetch, old_dir
        # Google adresinden doğrudan küçük boyut istenir; menü kartı 1024, yemek fotoğrafı 480
        self.assertIn("https://lh3.googleusercontent.com/a=w1024-h1024", requested)
        self.assertIn("https://lh3.googleusercontent.com/b=w480-h480", requested)
        rows = self.conn.execute(
            "SELECT kategori, yerel_dosya, dosya_boyutu, genislik, yukseklik FROM mekan_menu_gorselleri ORDER BY id"
        ).fetchall()
        self.assertEqual([r["kategori"] for r in rows], ["Menüyü göster", "Menüde öne çıkanlar"])
        self.assertEqual((rows[0]["genislik"], rows[0]["yukseklik"]), (1024, 683))
        self.assertEqual((rows[1]["genislik"], rows[1]["yukseklik"]), (480, 320))
        for row in rows:
            path = Path(self.temp.name) / "img" / row["yerel_dosya"]
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_size, row["dosya_boyutu"])
            self.assertLess(row["dosya_boyutu"], len(buffer.getvalue()))
        detail = server.venue_detail(self.conn, "place-1")
        self.assertEqual(detail["gorseller"][0]["yerel_dosya"], rows[0]["yerel_dosya"])

    def test_restaurant_scope_excludes_cafes(self):
        for category in ("Restoran & Lokanta", "Kebapçı & Ocakbaşı", "Pizza", "Hamburger", "Fast Food", "Bar ve ızgara lokantası"):
            self.assertTrue(server.is_restaurant_category(category), category)
        for category in ("Kafe", "3. Nesil Kahveci", "Pastane & Fırın", "Tatlı", "Dondurma", "Büfe", "Kahve dükkanı"):
            self.assertFalse(server.is_restaurant_category(category), category)

    def test_popular_times_are_saved_per_day_and_hour(self):
        server.sync_queue(self.conn, [self.venue()])
        lease = server.lease_next(self.conn)
        result = server.save_result(self.conn, {
            "id": lease["id"], "lease_token": lease["lease_token"],
            "source_url": "https://www.google.com/search?q=test", "fiyat_saglayici": "Bulunamadı",
            "fiyatlar": [], "gorseller": [],
            "populer_saatler": {
                "canli": "Biraz yoğun", "bekleme": "Genellikle beklemek gerekmiyor", "kalis_suresi": "1,5-4 saat",
                "gunler": {
                    "Pazartesi": [{"saat": "09:00", "yuzde": 15}, {"saat": "12:00", "yuzde": 97}, {"saat": "xx", "yuzde": 5}],
                    "Cuma": [{"saat": "20:00", "yuzde": 100}],
                    "Bilinmeyen": [{"saat": "09:00", "yuzde": 50}],
                },
            },
        })
        self.assertEqual(result["busy_hours"], 3)
        rows = self.conn.execute(
            "SELECT gun, gun_no, saat, yogunluk_yuzde FROM mekan_populer_saatler ORDER BY gun_no, saat"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Pazartesi", 1, 9, 15), ("Pazartesi", 1, 12, 97), ("Cuma", 5, 20, 100)])
        detail = server.venue_detail(self.conn, "place-1")
        self.assertEqual(detail["populer_saatler"]["canli_durum"], "Biraz yoğun")
        self.assertEqual(detail["populer_saatler"]["gunler"]["Cuma"], [{"saat": 20, "yuzde": 100}])
        self.assertEqual(server.dashboard_totals(self.conn)["busy_venues"], 1)


if __name__ == "__main__":
    unittest.main()
