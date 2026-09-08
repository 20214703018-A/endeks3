#!/usr/bin/env python3
"""
GEOPROP AI - Local HTTP Server Runner
Launches the demo dashboard and optionally opens default browser.
"""
import http.server
import socketserver
import webbrowser
import os
import sys

PORT = 8080
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # Clean terminal output
        pass

def main():
    port = PORT
    while port < 8100:
        try:
            with socketserver.TCPServer(("", port), Handler) as httpd:
                url = f"http://localhost:{port}"
                print("=" * 65)
                print(f"  🚀 GEOPROP AI Demo Sunucusu Başlatıldı")
                print(f"  📍 Yerel Adres: {url}")
                print(f"  📂 Dizin: {DIRECTORY}")
                print("=" * 65)
                print("  Durdurmak için Ctrl+C tuşlarına basın.")
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
                httpd.serve_forever()
                break
        except OSError:
            port += 1

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nSunucu kapatıldı.")
        sys.exit(0)
