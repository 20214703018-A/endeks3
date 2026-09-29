"""MCP sunucusu (stdio, JSON-RPC 2.0) — ek paket gerektirmez.

Claude masaüstü / Claude Code bu süreci başlatır; araçlar araclar.py'dekilerdir. stdout yalnız protokol mesajı taşır,
günlükler stderr'e gider. Analizler tek bir işçi iş parçacığında sırayla çalışır (DuckDB bağlantısı ve bellek-içi
alan önbelleği o iş parçacığına aittir); açılışta ısınma da aynı iş parçacığında yapılır.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import traceback

from . import __version__
from .araclar import arac_tanimlari, calistir_arac
from .yonerge import YONERGE

DESTEKLENEN = ("2025-06-18", "2025-03-26", "2024-11-05")
_yaz_kilit = threading.Lock()


def _log(*a):
    print("[geoprop-mcp]", *a, file=sys.stderr, flush=True)


def _gonder(mesaj: dict):
    with _yaz_kilit:
        sys.stdout.write(json.dumps(mesaj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _yanit(mid, sonuc=None, hata=None):
    m = {"jsonrpc": "2.0", "id": mid}
    if hata:
        m["error"] = hata
    else:
        m["result"] = sonuc
    _gonder(m)


def _isci(is_kuyrugu: queue.Queue):
    try:
        from .motor import isit
        _log("ısınma başladı (yer dizini + poligon alanları)")
        isit()
        _log("ısınma bitti")
    except Exception as e:  # ısınma hatası sunucuyu durdurmaz; ilk çağrıda yeniden denenir
        _log("ısınma hatası:", e)
    while True:
        mid, ad, girdi = is_kuyrugu.get()
        try:
            metin, hata = calistir_arac(ad, girdi or {})
            _yanit(mid, {"content": [{"type": "text", "text": metin}], "isError": hata})
        except Exception:
            _yanit(mid, {"content": [{"type": "text", "text": traceback.format_exc()[-2000:]}], "isError": True})


def main():
    araclar = [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]} for t in arac_tanimlari()]
    is_kuyrugu: queue.Queue = queue.Queue()
    threading.Thread(target=_isci, args=(is_kuyrugu,), daemon=True).start()
    _log(f"başladı; {len(araclar)} araç")
    for satir in sys.stdin:
        satir = satir.strip()
        if not satir:
            continue
        try:
            m = json.loads(satir)
        except json.JSONDecodeError:
            _gonder({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON ayrıştırılamadı"}})
            continue
        yontem, mid, prm = m.get("method"), m.get("id"), m.get("params") or {}
        if mid is None:          # bildirim (notifications/initialized, cancelled...) — yanıt yok
            continue
        if yontem == "initialize":
            istenen = prm.get("protocolVersion")
            _yanit(mid, {"protocolVersion": istenen if istenen in DESTEKLENEN else DESTEKLENEN[0],
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "geoprop", "version": __version__},
                         "instructions": YONERGE})
        elif yontem == "ping":
            _yanit(mid, {})
        elif yontem == "tools/list":
            _yanit(mid, {"tools": araclar})
        elif yontem == "tools/call":
            is_kuyrugu.put((mid, prm.get("name"), prm.get("arguments")))
        elif yontem in ("resources/list", "prompts/list"):
            _yanit(mid, {yontem.split("/")[0]: []})
        else:
            _yanit(mid, hata={"code": -32601, "message": f"Desteklenmeyen yöntem: {yontem}"})


if __name__ == "__main__":
    main()
