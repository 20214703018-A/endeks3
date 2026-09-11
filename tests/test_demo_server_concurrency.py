import socket
import threading
import unittest
import urllib.request

from demo.server import GeopropApiHandler
import http.server


class DemoServerConcurrencyTest(unittest.TestCase):
    def test_yarim_acik_baglanti_ana_sayfayi_bloke_etmez(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GeopropApiHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        idle_client = socket.create_connection(server.server_address, timeout=1)
        worker.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/",
                timeout=2,
            ) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertTrue(response.url.endswith("/alici_arsa_analiz.html"))
                self.assertIn("ALICI KARAR MERKEZİ", body)
        finally:
            idle_client.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
