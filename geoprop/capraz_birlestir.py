"""
GEOPROP çaprazlama: eski ayrıştırıcıyla toplanmış shard verisini güncel ana ambarla birleştirir.

Neden var: 2026-09-20 koşusunun 40 shard'ı (~3,3 milyon işletme) eski ayrıştırıcıyla toplandı;
koordinat, kategori, puan ve yorum sayısı doğru ama il/ilçe sorgu metninden türetildiği için
%57'si yanlış, adres alanı sorgu metni, kimlik isim+koordinat hash'i. Bu veri atılmasın diye:

  1. Ana ambardaki (yeni, CID kimlikli) işletmelerle eşleştirilir:
       a) isim+lat+lon hash'i birebir aynıysa  -> zaten var, atla
       b) normalize isim aynı ve mesafe <= 60 m -> zaten var, atla
  2. Eşleşmeyenler ana ambara eklenir; il/ilçe/mahalle koordinattan (en yakın mahalle merkezi,
     collector/mahalle_koordinatlari.json) atanır; tam_adres NULL bırakılır (uydurma adres yok);
     kaynak = "Google Maps PB (eski ayrıştırıcı, koordinattan idari atama)".
  3. Eski gözlem ve örnek yorum satırları da eklenen işletmeler için taşınır.

Kullanım: python geoprop/capraz_birlestir.py <eski_shard_klasoru> <ana_ambar.sqlite>
"""
import glob
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAHALLE_JSON = os.path.join(BASE_DIR, "collector", "mahalle_koordinatlari.json")
REHBER_JSON = os.path.join(BASE_DIR, "collector", "turkiye_il_ilce_rehberi.json")
ESLESME_MESAFE_M = 60.0
KAYNAK_ETIKETI = "Google Maps PB (eski ayrıştırıcı, koordinattan idari atama)"


def normalize(name):
    s = unicodedata.normalize("NFKD", (name or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.replace("ı", "i")).strip()


class MahalleIndeksi:
    """Koordinat -> (il, ilçe, mahalle) en yakın mahalle merkezi üzerinden."""

    def __init__(self):
        with open(MAHALLE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        il_map, ilce_map = {}, {}
        with open(REHBER_JSON, "r", encoding="utf-8") as f:
            for v in json.load(f).values():
                il_map[v["city_slug"]] = v["city_name"]
                for c in v.get("ilceler", []):
                    ilce_map[(v["city_slug"], c["county_slug"])] = c["county_name"]
        self.cell = 0.02  # ~2.2 km
        self.grid = {}
        for k, v in data.items():
            parts = k.split("_")
            if len(parts) < 3 or v.get("lat") is None:
                continue
            il = il_map.get(parts[0], parts[0].capitalize())
            ilce = ilce_map.get((parts[0], parts[1]), parts[1].capitalize())
            mah = v.get("name", "").replace("Mahallesi", "").strip()
            la, lo = float(v["lat"]), float(v["lon"])
            self.grid.setdefault((int(la / self.cell), int(lo / self.cell)), []).append((la, lo, il, ilce, mah))

    def bul(self, lat, lon):
        gi, gj = int(lat / self.cell), int(lon / self.cell)
        best, best_d = None, float("inf")
        cos_la = math.cos(math.radians(lat))
        for r in (1, 2, 4):
            for i in range(gi - r, gi + r + 1):
                for j in range(gj - r, gj + r + 1):
                    for la, lo, il, ilce, mah in self.grid.get((i, j), ()):
                        d = ((la - lat) * 111.0) ** 2 + ((lo - lon) * 111.0 * cos_la) ** 2
                        if d < best_d:
                            best, best_d = (il, ilce, mah), d
            if best is not None:
                return best + (round(math.sqrt(best_d), 2),)
        return (None, None, None, None)


def eski_hash(isim, lat, lon):
    return hashlib.sha256(f"{isim}_{lat}_{lon}".encode("utf-8")).hexdigest()[:24]


def main(eski_dir, master_path):
    shard_dbs = sorted(glob.glob(os.path.join(eski_dir, "**", "google_places_ve_yogunluk.sqlite"), recursive=True))
    if not shard_dbs:
        print("Eski shard bulunamadı:", eski_dir)
        return
    print(f"{len(shard_dbs)} eski shard, ana ambar: {master_path}")
    m = sqlite3.connect(master_path)
    m.execute("PRAGMA journal_mode=OFF")
    m.execute("PRAGMA synchronous=OFF")
    cols_master = [r[1] for r in m.execute("PRAGMA table_info(google_places_ticari_yogunluk)")]
    for col in ("feature_id", "gcid_kategoriler", "posta_kodu", "web_sitesi", "idari_atama_mesafe_km"):
        if col not in cols_master:
            m.execute(f"ALTER TABLE google_places_ticari_yogunluk ADD COLUMN {col} TEXT")
            cols_master.append(col)

    # Ana ambarın eşleştirme indeksi: eski hash + (normalize isim, 0.001° hücre)
    print("Ana ambar indeksleniyor...")
    yeni_hash = set()
    yeni_isim_grid = {}
    n_master = 0
    for isim, lat, lon in m.execute("SELECT isim, lat, lon FROM google_places_ticari_yogunluk"):
        n_master += 1
        yeni_hash.add(eski_hash(isim, lat, lon))
        key = (normalize(isim), int(lat / 0.001), int(lon / 0.001))
        yeni_isim_grid.setdefault(key[0], []).append((lat, lon))
    print(f"  ana ambar: {n_master:,} işletme")

    def zaten_var(isim, lat, lon):
        if eski_hash(isim, lat, lon) in yeni_hash:
            return True
        cos_la = math.cos(math.radians(lat))
        for la, lo in yeni_isim_grid.get(normalize(isim), ()):
            d_m = math.sqrt(((la - lat) * 111000.0) ** 2 + ((lo - lon) * 111000.0 * cos_la) ** 2)
            if d_m <= ESLESME_MESAFE_M:
                return True
        return False

    idx = MahalleIndeksi()
    eklenen = atlanan = 0
    for sp in shard_dbs:
        s = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
        s.row_factory = sqlite3.Row
        cols_shard = [r[1] for r in s.execute("PRAGMA table_info(google_places_ticari_yogunluk)")]
        ortak = [c for c in cols_shard if c in cols_master and c not in ("id",)]
        shard_eklenen = 0
        for row in s.execute("SELECT * FROM google_places_ticari_yogunluk WHERE lat IS NOT NULL AND lon IS NOT NULL"):
            if zaten_var(row["isim"], row["lat"], row["lon"]):
                atlanan += 1
                continue
            il, ilce, mah, mesafe = idx.bul(row["lat"], row["lon"])
            d = {c: row[c] for c in ortak}
            d["il"], d["ilce"], d["mahalle"] = il, ilce, mah
            d["tam_adres"] = None            # uydurma adres taşınmaz
            d["telefon"] = None
            d["calisma_saatleri"] = None
            d["cid"] = None                  # eski cid anlamsızdı (süreç-bağımlı hash)
            d["kaynak"] = KAYNAK_ETIKETI
            d["idari_atama_mesafe_km"] = mesafe
            d["maps_url"] = f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}"
            keys = list(d.keys())
            m.execute(
                f'INSERT OR IGNORE INTO google_places_ticari_yogunluk ({", ".join(keys)}) VALUES ({", ".join("?" * len(keys))})',
                [d[k] for k in keys],
            )
            yeni_hash.add(eski_hash(row["isim"], row["lat"], row["lon"]))
            yeni_isim_grid.setdefault(normalize(row["isim"]), []).append((row["lat"], row["lon"]))
            eklenen += 1
            shard_eklenen += 1
        s.close()
        m.commit()
        print(f"  {os.path.basename(os.path.dirname(sp))}: +{shard_eklenen:,} işletme (toplam eklenen {eklenen:,}, atlanan {atlanan:,})")

    # Gözlem ve yorum taşımayı toplu yap (ATTACH ile, yalnızca eklenen işletmeler için)
    print("Gözlem ve örnek yorumlar taşınıyor...")
    for sp in shard_dbs:
        m.execute("ATTACH DATABASE ? AS shard_db", (sp,))
        for tbl, cols in (("google_places_gozlem", None), ("google_places_yorumlar_ve_niyet", "google_place_id, mekan_adi, yorum_metni, puan, kayit_tarihi")):
            try:
                if cols:
                    m.execute(f"""INSERT OR IGNORE INTO {tbl} ({cols}) SELECT {cols} FROM shard_db.{tbl}
                                  WHERE google_place_id IN (SELECT google_place_id FROM google_places_ticari_yogunluk WHERE kaynak=?)""",
                              (KAYNAK_ETIKETI,))
                else:
                    m.execute(f"""INSERT OR IGNORE INTO {tbl} SELECT * FROM shard_db.{tbl}
                                  WHERE google_place_id IN (SELECT google_place_id FROM google_places_ticari_yogunluk WHERE kaynak=?)""",
                              (KAYNAK_ETIKETI,))
            except sqlite3.OperationalError as e:
                print(f"    [!] {tbl} taşınamadı: {e}")
        m.commit()
        m.execute("DETACH DATABASE shard_db")

    toplam = m.execute("SELECT COUNT(*) FROM google_places_ticari_yogunluk").fetchone()[0]
    print(f"\n✅ Çaprazlama bitti: {eklenen:,} işletme eklendi, {atlanan:,} zaten vardı. Ana ambar: {toplam:,} işletme.")
    m.execute("VACUUM")
    m.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Kullanım: python geoprop/capraz_birlestir.py <eski_shard_klasoru> <ana_ambar.sqlite>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
