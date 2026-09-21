#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP - Google Places DETAY Toplayıcı (Faz 2b)

Liste taraması (google_places_ve_yogunluk_toplayici.py) işletme başına isim, kategori, puan,
yorum sayısı, koordinat, adres ve web sitesi verir. Bu modül her işletme için Maps'in yer-detayı
isteğini (maps/preview/place) tekrarlayıp liste yanıtında OLMAYAN alanları toplar:

  - telefon (yerel + uluslararası biçim)
  - çalışma saatleri (gün gün, JSON)
  - Google'ın kısa/uzun açıklaması
  - ChIJ place_id (resmî Places API kimliği)
  - oturumsuz erişilebilen ~5 yorum: yorum id, mikrosaniye zaman damgası, puan, metin,
    yazar id, alt puanlar (Yemek/Hizmet/Atmosfer varsa)

Not: Google, yorumların tamamını ("Diğer yorumlar") ve popüler saatleri oturum açmış kullanıcıya
saklıyor; oturumsuz yükte işletme başına ~5 yorum gelir. Bu modül hesap çerezi KULLANMAZ.

Girdi: ana ambar (google_places_ve_yogunluk.sqlite, feature_id sütunu dolu satırlar).
Çıktı: warehouse/product/google_places_detay.sqlite  (tablolar: google_places_detay, google_places_yorumlar)
Öncelik: büyükşehir + yüksek yorum sayısı önce; --gecmis-db verilirse son N günde taranan işletme atlanır.
"""

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_OUT = os.path.join(BASE_DIR, "warehouse/product/google_places_detay.sqlite")
DEFAULT_MASTER = os.path.join(BASE_DIR, "warehouse/master/google_places_ve_yogunluk.sqlite")

BUYUKSEHIR = {
    "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya", "Adana", "Konya", "Gaziantep", "Mersin", "Kocaeli",
    "Diyarbakır", "Hatay", "Manisa", "Kayseri", "Samsun", "Balıkesir", "Kahramanmaraş", "Van", "Aydın",
    "Denizli", "Sakarya", "Tekirdağ", "Muğla", "Eskişehir", "Mardin", "Malatya", "Trabzon", "Erzurum",
    "Ordu", "Şanlıurfa",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Maps arayüzünün yer-detayı isteğinden yakalanan pb şablonu (ftid/lat/lon değişken).
PB_TEMPLATE = (
    "!1m14!1s{ftid}!3m12!1m3!1d2000!2d{lon}!3d{lat}!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1"
    "!12m4!2m3!1i360!2i120!4i8!13m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
    "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2"
    "!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20!14m3!1sX!7e81!15i10112"
    "!15m106!1m26!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1!18m15!3b1!4b1!5b1!6b1!13b1!14b1!17b1!21b1!22b1!30b1"
    "!32b1!33m1!1b1!34b1!36e2!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
    "!39m3!2m2!2i1!3i1!43b1!52b1!55b1!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298!72m22!1m8!2b1!5b1!7b1"
    "!12m4!1b1!2b1!4m1!1e1!4b1!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4"
    "!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts!6m1!1e1!9b1!89b1!90m2!1m1!1e2"
    "!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b1!21m0!22m1!1e81"
    "!30m8!3b1!6m2!1b1!2b1!7m2!1e3!2b1!9b1!34m5!7b1!10b1!14b1!15m1!1b0!37i795"
)


def init_db(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_detay (
        google_place_id TEXT PRIMARY KEY,      -- liste ambarındaki kimlik (CID)
        feature_id TEXT,
        place_id_chij TEXT,
        isim TEXT,
        telefon TEXT,
        telefon_uluslararasi TEXT,
        web_sitesi TEXT,
        tam_adres TEXT,
        kisa_aciklama TEXT,
        uzun_aciklama TEXT,
        calisma_saatleri_json TEXT,
        puan REAL,
        yorum_sayisi INTEGER,
        yorum_sayisi_metin TEXT,
        cekilen_yorum INTEGER,
        yanit_boyutu INTEGER,
        durum TEXT,                            -- TAM | KISMI (yorum yok) | HATA
        tarama_tarihi TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS google_places_yorumlar (
        yorum_id TEXT PRIMARY KEY,
        google_place_id TEXT NOT NULL,
        yazar_id TEXT,
        yazar_adi TEXT,
        puan INTEGER,
        metin TEXT,
        yorum_zamani TEXT,                     -- ISO 8601 UTC (mikrosaniye epoch'tan)
        guncelleme_zamani TEXT,
        goreli_zaman TEXT,
        alt_puanlar_json TEXT,
        kayit_tarihi TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gpy_place ON google_places_yorumlar(google_place_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gpy_zaman ON google_places_yorumlar(yorum_zamani)")
    conn.commit()
    return conn


def _find_entity(obj, ftid):
    """Yanıtta feature_id string'ini içeren listeyi (yer varlığı) bulur."""
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, str) and x == ftid:
                return obj
            r = _find_entity(x, ftid)
            if r is not None:
                return r
    return None


def _g(o, *path):
    """Güvenli iç içe erişim."""
    for p in path:
        try:
            o = o[p]
        except (IndexError, KeyError, TypeError):
            return None
        if o is None:
            return None
    return o


def _iso(us):
    try:
        return datetime.fromtimestamp(int(us) / 1e6, tz=timezone.utc).isoformat()
    except Exception:
        return None


def fetch_detail(ftid, lat, lon, timeout=25):
    pb = PB_TEMPLATE.format(ftid=ftid, lat=lat, lon=lon)
    url = "https://www.google.com/maps/preview/place?authuser=0&hl=tr&gl=tr&pb=" + urllib.parse.quote(pb, safe="!:")
    req = urllib.request.Request(url, headers={
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8",
        "Referer": "https://www.google.com/maps",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    return raw


def parse_detail(raw, ftid):
    data = json.loads(raw[4:] if raw.startswith(")]}'") else raw)
    ent = _find_entity(data, ftid)
    if ent is None:
        return None, []
    tel = _g(ent, 178, 0)
    saat = _g(ent, 203, 0)   # [[gün_adı, gün_no, [y,m,d], [[ "11:30–22:00", ...], ...], ...], ...]
    detay = {
        "feature_id": ftid,
        "place_id_chij": _g(ent, 78),
        "isim": _g(ent, 11),
        "telefon": _g(tel, 0) if tel else None,
        "telefon_uluslararasi": _g(tel, 1, 1, 0) if tel else None,
        "web_sitesi": _g(ent, 7, 0),
        "tam_adres": _g(ent, 18),
        "kisa_aciklama": _g(ent, 32, 0, 1),
        "uzun_aciklama": _g(ent, 32, 1, 1),
        "calisma_saatleri_json": json.dumps(
            [{"gun": g[0], "gun_no": g[1], "saatler": [(x[0] if isinstance(x, list) else x) for x in (g[3] or []) if x]}
             for g in saat if isinstance(g, list) and len(g) > 3 and isinstance(g[0], str)],
            ensure_ascii=False) if isinstance(saat, list) else None,
        "puan": _g(ent, 4, 7),
        "yorum_sayisi": _g(ent, 4, 8),
        "yorum_sayisi_metin": _g(ent, 4, 3, 1),
    }
    yorumlar = []
    revs = _g(ent, 175, 9, 0, 0) or []
    for r in revs:
        rid = _g(r, 0, 0)
        if not rid:
            continue
        alt = []
        for sub in (_g(r, 0, 2, 6) or []):
            v = _g(sub, 11, 0)
            if isinstance(v, int):
                alt.append(v)
        yorumlar.append({
            "yorum_id": rid,
            "yazar_id": _g(r, 0, 1, 4, 5, 3),
            "yazar_adi": _g(r, 0, 1, 4, 5, 0),
            "puan": _g(r, 0, 2, 0, 0),
            "metin": _g(r, 0, 2, 15, 0, 0),
            "yorum_zamani": _iso(_g(r, 0, 1, 2)),
            "guncelleme_zamani": _iso(_g(r, 0, 1, 3)),
            "goreli_zaman": _g(r, 0, 1, 6),
            "alt_puanlar_json": json.dumps(alt) if alt else None,
        })
    return detay, yorumlar


def secilecek_isletmeler(master_path, shard, num_shards, hist_conn, yenileme_gunu, limit=None, min_yorum=0):
    """Ana ambardan bu shard'a düşen, feature_id'li işletmeler; öncelik büyükşehir + yorum sayısı."""
    m = sqlite3.connect(f"file:{os.path.abspath(master_path)}?mode=ro", uri=True)
    rows = m.execute(
        "SELECT google_place_id, feature_id, isim, lat, lon, il, COALESCE(yorum_sayisi,0) "
        "FROM google_places_ticari_yogunluk WHERE feature_id IS NOT NULL AND lat IS NOT NULL").fetchall()
    m.close()
    taranmis = set()
    if hist_conn is not None:
        try:
            taranmis = {r[0] for r in hist_conn.execute(
                "SELECT google_place_id FROM google_places_detay WHERE durum='TAM' "
                "AND julianday('now')-julianday(tarama_tarihi) < ?", (yenileme_gunu,))}
        except Exception:
            taranmis = set()
    secim = []
    for pid, ftid, isim, lat, lon, il, yorum in rows:
        if yorum < min_yorum:
            continue
        digest = int(hashlib.sha256(str(pid).encode("utf-8")).hexdigest()[:8], 16)
        if digest % num_shards != shard - 1:
            continue
        if pid in taranmis:
            continue
        oncelik = (1 if il in BUYUKSEHIR else 0, yorum)
        secim.append((oncelik, pid, ftid, isim, lat, lon))
    secim.sort(key=lambda x: x[0], reverse=True)
    if limit:
        secim = secim[:limit]
    return [s[1:] for s in secim], len(rows), len(taranmis)


def main():
    ap = argparse.ArgumentParser(description="GEOPROP Google Places detay toplayıcı (telefon, saatler, ~5 zaman damgalı yorum)")
    ap.add_argument("--shard", type=int, default=1)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--master", default=DEFAULT_MASTER, help="Liste ambarı (feature_id kaynağı)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--gecmis-db", default=None, help="Önceki detay ambarı (salt okunur); yakın tarihte taranan atlanır")
    ap.add_argument("--yenileme-gunu", type=float, default=30.0)
    ap.add_argument("--min-yorum", type=int, default=0, help="Bu sayının altında yorumu olan işletmeleri atla")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-seconds", type=int, default=16200)
    ap.add_argument("--bekleme", type=float, default=0.8, help="İstekler arası ortalama bekleme (sn)")
    args = ap.parse_args()

    if not os.path.exists(args.master):
        print(f"[!] Liste ambarı yok: {args.master} — detay için önce liste koşusu gerekir.")
        return
    hist = None
    if args.gecmis_db and os.path.exists(args.gecmis_db) and os.path.abspath(args.gecmis_db) != os.path.abspath(args.out):
        hist = sqlite3.connect(f"file:{os.path.abspath(args.gecmis_db)}?mode=ro", uri=True)
    hedef, toplam, atlanan = secilecek_isletmeler(args.master, args.shard, args.num_shards, hist, args.yenileme_gunu, args.limit, args.min_yorum)
    print(f"Shard {args.shard}/{args.num_shards}: ambarda {toplam:,} feature_id'li işletme; {atlanan:,} yakın tarihte taranmış; bu shard için {len(hedef):,} hedef.")

    conn = init_db(args.out)
    cur = conn.cursor()
    t0 = time.time()
    n_ok = n_kismi = n_hata = n_yorum = 0
    ardisik_kismi = 0
    for i, (pid, ftid, isim, lat, lon) in enumerate(hedef, 1):
        if args.max_seconds and time.time() - t0 >= args.max_seconds:
            print(f"⏰ Süre sınırı ({args.max_seconds} sn): {i-1}/{len(hedef)} işlendi.")
            break
        now = datetime.now(timezone.utc).isoformat()
        try:
            raw = fetch_detail(ftid, lat, lon)
            detay, yorumlar = parse_detail(raw, ftid)
        except Exception as e:
            n_hata += 1
            cur.execute("INSERT OR REPLACE INTO google_places_detay (google_place_id, feature_id, isim, durum, tarama_tarihi) VALUES (?,?,?,?,?)",
                        (pid, ftid, isim, f"HATA: {str(e)[:80]}", now))
            conn.commit()
            time.sleep(3.0)
            continue
        if detay is None:
            n_hata += 1
            cur.execute("INSERT OR REPLACE INTO google_places_detay (google_place_id, feature_id, isim, durum, yanit_boyutu, tarama_tarihi) VALUES (?,?,?,?,?,?)",
                        (pid, ftid, isim, "HATA: varlık bulunamadı", len(raw), now))
            conn.commit()
            time.sleep(2.0)
            continue
        durum = "TAM" if yorumlar else "KISMI"
        if yorumlar:
            n_ok += 1
            ardisik_kismi = 0
        else:
            n_kismi += 1
            ardisik_kismi += 1
        cur.execute("""INSERT OR REPLACE INTO google_places_detay
            (google_place_id, feature_id, place_id_chij, isim, telefon, telefon_uluslararasi, web_sitesi, tam_adres,
             kisa_aciklama, uzun_aciklama, calisma_saatleri_json, puan, yorum_sayisi, yorum_sayisi_metin,
             cekilen_yorum, yanit_boyutu, durum, tarama_tarihi)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, ftid, detay["place_id_chij"], detay["isim"] or isim, detay["telefon"], detay["telefon_uluslararasi"],
             detay["web_sitesi"], detay["tam_adres"], detay["kisa_aciklama"], detay["uzun_aciklama"],
             detay["calisma_saatleri_json"], detay["puan"], detay["yorum_sayisi"], detay["yorum_sayisi_metin"],
             len(yorumlar), len(raw), durum, now))
        for y in yorumlar:
            cur.execute("""INSERT OR REPLACE INTO google_places_yorumlar
                (yorum_id, google_place_id, yazar_id, yazar_adi, puan, metin, yorum_zamani, guncelleme_zamani, goreli_zaman, alt_puanlar_json, kayit_tarihi)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (y["yorum_id"], pid, y["yazar_id"], y["yazar_adi"], y["puan"], y["metin"], y["yorum_zamani"],
                 y["guncelleme_zamani"], y["goreli_zaman"], y["alt_puanlar_json"], now))
            n_yorum += 1
        conn.commit()
        if i % 25 == 0 or i == len(hedef):
            hiz = i / max(1.0, time.time() - t0)
            print(f"[{i}/{len(hedef)}] tam={n_ok} kısmi={n_kismi} hata={n_hata} yorum={n_yorum} | {hiz:.2f} işletme/sn | son: {isim[:30]} tel={detay['telefon']} yorum={len(yorumlar)}", flush=True)
        # Google yorum bloğunu vermeyi keserse (IP kısıtı) hızı düşür
        if ardisik_kismi >= 20:
            print("  ⚠️ 20 ardışık yanıtta yorum yok — 60 sn bekleniyor (olası IP kısıtı).", flush=True)
            time.sleep(60)
            ardisik_kismi = 0
        time.sleep(random.uniform(args.bekleme * 0.6, args.bekleme * 1.4))

    conn.close()
    if hist is not None:
        hist.close()
    print(f"\n✅ Detay bitti: tam={n_ok} kısmi={n_kismi} hata={n_hata} yorum={n_yorum} ({time.time()-t0:.0f} sn). Çıktı: {args.out}")


if __name__ == "__main__":
    main()
