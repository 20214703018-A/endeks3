#!/usr/bin/env python3
"""Local, resumable queue and persistence service for GEOPROP menu collection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
import calendar
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow yoksa görsel indirilir ama yeniden boyutlandırılmaz
    Image = ImageOps = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Kod git deposunda (endeks3), veri ambarı ise ~/Desktop/GEOPROP/warehouse altında durur.
# Sunucu GEOPROP içinden çalıştırılırsa oradaki warehouse kullanılır; aksi halde GEOPROP_DATA_ROOT.
# Yalnız klasörün varlığına bakılmaz: endeks3/warehouse/product menü görselleri için de oluşur ve
# eskiden sunucu bu yüzden boş bir veritabanı açıyordu. Kök, menü veritabanı gerçekten oradaysa seçilir.
_LOCAL_MENU_DB = PROJECT_ROOT / "warehouse/product/restoran_ve_kafe_menuleri.sqlite"
DATA_ROOT = Path(os.environ.get("GEOPROP_DATA_ROOT") or (
    PROJECT_ROOT if _LOCAL_MENU_DB.is_file() and _LOCAL_MENU_DB.stat().st_size > 0 else Path.home() / "Desktop" / "GEOPROP"
))
DB_MENU = Path(os.environ.get("GEOPROP_MENU_DB", DATA_ROOT / "warehouse/product/restoran_ve_kafe_menuleri.sqlite"))
DB_PLACES = Path(os.environ.get("GEOPROP_PLACES_DB", DATA_ROOT / "warehouse/product/google_places_ve_yogunluk.sqlite"))
DB_STATS = Path(os.environ.get("GEOPROP_STATS_DB", DATA_ROOT / "warehouse/product/bolge_istatistik.sqlite"))
# Güncel Google ambarından (warehouse-latest) yalnız kuyruk için gereken yiyecek-içecek satırlarının
# süzülmüş kopyası; varsa DB_PLACES'e ek kaynak olarak okunur (bkz. README "Mekan kaynağı").
DB_PLACES_MENU_KAYNAK = Path(os.environ.get(
    "GEOPROP_MENU_KAYNAK_DB", DATA_ROOT / "warehouse/product/menu_kaynak_google_mekanlari.sqlite"
))
RAW_INTAKE_ROOT = Path(os.environ.get("GEOPROP_RAW_INTAKE_ROOT", DATA_ROOT.parent / "GEOPROP_RAW_INTAKE"))

ALL_CITIES = (
    "Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Aksaray", "Amasya", "Ankara", "Antalya", "Ardahan",
    "Artvin", "Aydın", "Balıkesir", "Bartın", "Batman", "Bayburt", "Bilecik", "Bingöl", "Bitlis", "Bolu",
    "Burdur", "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır", "Düzce", "Edirne",
    "Elazığ", "Erzincan", "Erzurum", "Eskişehir", "Gaziantep", "Giresun", "Gümüşhane", "Hakkari", "Hatay",
    "Iğdır", "Isparta", "İstanbul", "İzmir", "Kahramanmaraş", "Karabük", "Karaman", "Kars", "Kastamonu",
    "Kayseri", "Kilis", "Kırıkkale", "Kırklareli", "Kırşehir", "Kocaeli", "Konya", "Kütahya", "Malatya",
    "Manisa", "Mardin", "Mersin", "Muğla", "Muş", "Nevşehir", "Niğde", "Ordu", "Osmaniye", "Rize",
    "Sakarya", "Samsun", "Şanlıurfa", "Siirt", "Sinop", "Şırnak", "Sivas", "Tekirdağ", "Tokat", "Trabzon",
    "Tunceli", "Uşak", "Van", "Yalova", "Yozgat", "Zonguldak",
)
# Öncelik katmanları (AGENTS.md kural 10): önce Batı büyükşehirleri, sonra ilk turda taranan
# diğer iller, en son kalan iller. Kuyruk bu sırayla, her katman içinde menü bulma oranı
# yüksek kategoriler önce olacak şekilde işlenir.
WEST_METRO_CITIES = ("İstanbul", "İzmir", "Bursa", "Antalya", "Kocaeli", "Muğla", "Tekirdağ", "Balıkesir", "Aydın")
FIRST_ROUND_CITIES = ("Ankara", "Konya", "Çanakkale", "Diyarbakır", "Trabzon")
# GEOPROP_MENU_ILLER="İzmir,Muğla" gibi virgüllü liste kapsamı daraltır; boş/"hepsi" = 81 il.
_cities_env = os.environ.get("GEOPROP_MENU_ILLER", "hepsi").strip()
TARGET_CITIES = ALL_CITIES if _cities_env.casefold() in ("", "hepsi") else tuple(
    city.strip() for city in _cities_env.split(",") if city.strip()
)
MIN_REVIEWS = 10
MIN_COUNTY_POPULATION = 25_000
MIN_NEIGHBORHOOD_POPULATION = 3_000
MAX_ATTEMPTS = 2  # yalnız zaman aşımı/CAPTCHA için tek tekrar; menü bulunamayan mekan tekrar denenmez
MAX_BODY_BYTES = 2_000_000
LEASE_TIMEOUT_SECONDS = 15 * 60
# ThreadingHTTPServer: paralel çalışanların /next istekleri aynı anda gelebilir; aynı mekanın iki
# çalışana verilmemesi için seçme+kiralama tek kilit altında yapılır.
LEASE_LOCK = threading.Lock()

# Görseller yerel diske küçültülerek indirilir. Menü kartı fotoğrafları OCR ile okunacağı için
# uzun kenar 1024 px'te tutulur (~140 KB); yemek fotoğrafları yalnız önizleme amaçlı (~30 KB).
IMAGE_DIR = Path(os.environ.get("GEOPROP_MENU_IMAGE_DIR", PROJECT_ROOT / "warehouse/product/menu_gorselleri"))
MENU_IMAGE_EDGE, MENU_IMAGE_QUALITY = 1024, 65
DISH_IMAGE_EDGE, DISH_IMAGE_QUALITY = 480, 60
DISH_SECTIONS = ("menüde öne çıkanlar",)
IMAGE_DOWNLOAD_TIMEOUT = 20
IMAGE_MAX_DOWNLOAD_ATTEMPTS = 3
IMAGE_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

FOOD_CATEGORY_KEYWORDS = (
    "restoran", "restaurant", "lokanta", "kafe", "cafe", "kahve", "coffee",
    "pastane", "firin", "bakery", "pizza", "burger", "doner", "kebap", "kebab",
    "tatli", "yemek", "bufe", "fast food", "steak", "balik", "meyhane",
    "cay", "dondurma",
)
NON_FOOD_VENUE_MARKERS = (
    "internet", "cyber cafe", "cyber kafe",
    "playstation", "e-spor", "e spor", "espor",
    "game center", "game arena", "gaming", "gamer cafe", "gamer kafe",
    "oyun salonu", "oyun merkezi", "oyun cafe", "oyun kafe",
    "simulator cafe", "simülasyon merkezi", "bilardo",
    "bilgisayar tamir", "bilgisayar hizmetleri",
    "kuaför", "berber", "fitness salonu", "spor salonu",
    "araç kiralama", "oto yıkama", "oto servis",
    "kırtasiye", "çiçekçilik", "emlak", "pet shop", "telefon tamir",
)
VILLAGE_MARKERS = ("koy", "koyu", "koy mahallesi", "belde")

# Kapsam: varsayılan tüm yiyecek-içecek (kafe, kahveci, pastane, tatlıcı, büfe dahil). Bunların
# menü bulma oranı restoranlardan düşük olduğundan kuyrukta sona doğru kalırlar (bkz. queue_priority).
# GEOPROP_MENU_SCOPE=restoran ile yalnız restoran tipi mekanlara daraltılır.
SCOPE = os.environ.get("GEOPROP_MENU_SCOPE", "hepsi")
RESTAURANT_CATEGORY_KEYWORDS = (
    "restoran", "restaurant", "lokanta", "kebap", "kebab", "ocakbasi", "pizza",
    "hamburger", "burger", "fast food", "doner", "balik", "steak", "meyhane",
    "izgara", "kofte", "pide", "lahmacun", "mantı", "manti", "et lokantasi",
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_tr(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold()).replace("ı", "i")
    return "".join(char for char in text if not unicodedata.combining(char) and char.isalnum())


# Google shard'ları aynı ili hem "İstanbul" hem "Istanbul" gibi farklı
# yazımlarla saklayabiliyor. Kapsamı SQL'de tam metin eşleşmesiyle daraltmak
# gerçek kayıtların atlanmasına neden olur; şehir karşılaştırmasını tek yerde
# Türkçe karakterlerden bağımsız yapıyoruz.
TARGET_CITY_BY_KEY = {normalize_tr(city): city for city in TARGET_CITIES}
CITY_TIER = {
    **{normalize_tr(city): 2 for city in WEST_METRO_CITIES},
    **{normalize_tr(city): 1 for city in FIRST_ROUND_CITIES},
}


def canonical_target_city(value: Any) -> str | None:
    return TARGET_CITY_BY_KEY.get(normalize_tr(value))


def is_food_category(
    primary: str | None,
    all_categories: str | None,
    venue_name: str | None = None,
) -> bool:
    identity = normalize_tr(f"{venue_name or ''} {primary or ''} {all_categories or ''}")
    if any(normalize_tr(marker) in identity for marker in NON_FOOD_VENUE_MARKERS):
        return False
    normalized = normalize_tr(f"{primary or ''} {all_categories or ''}")
    return any(normalize_tr(keyword) in normalized for keyword in FOOD_CATEGORY_KEYWORDS)


def is_restaurant_category(primary: str | None) -> bool:
    """Ana kategori restoran tipi mi? (Kafe/kahveci/pastane gibi kayıtlar ilk aşamada dışarıda.)"""
    normalized = normalize_tr(primary)
    return any(normalize_tr(keyword) in normalized for keyword in RESTAURANT_CATEGORY_KEYWORDS)


def is_village_name(name: str | None) -> bool:
    normalized = normalize_tr(name)
    return bool(normalized) and any(normalize_tr(marker) in normalized for marker in VILLAGE_MARKERS)


def sqlite_has_table(path: Path, table: str) -> bool:
    """Dosya var, boş değil ve beklenen tabloyu içeriyor mu? (0 baytlık yer tutucular geçersiz sayılır.)"""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def connect_menu_db() -> sqlite3.Connection:
    DB_MENU.parent.mkdir(parents=True, exist_ok=True)
    # Ana dosya yok ama -shm/-wal kalıntısı varsa veritabanı silinmiş/taşınmış demektir: sessizce boş
    # bir veritabanı açıp sıfırdan başlamak yerine dur (GEOPROP_MENU_YENI=1 ile bilerek yeni başlatılabilir).
    if (not DB_MENU.exists() or DB_MENU.stat().st_size == 0) and os.environ.get("GEOPROP_MENU_YENI") != "1":
        orphans = [p.name for p in (Path(f"{DB_MENU}-shm"), Path(f"{DB_MENU}-wal")) if p.exists()]
        if orphans:
            raise SystemExit(
                f"Menü veritabanı bulunamadı: {DB_MENU} (yalnız {', '.join(orphans)} kalıntısı var).\n"
                "Yedekten geri yükleyin: python3 ops/veri_indir.py indir geoprop_warehouse_ek_20261002 "
                "\"warehouse/product/restoran_ve_kafe_menuleri.sqlite\" --hedef <klasör>\n"
                "Bilerek boş veritabanıyla başlamak için: GEOPROP_MENU_YENI=1"
            )
    conn = sqlite3.connect(DB_MENU, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mekan_menu_gorselleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT NOT NULL,
            mekan_adi TEXT NOT NULL,
            gorsel_url TEXT NOT NULL,
            kaynak TEXT NOT NULL,
            kategori TEXT,
            fotograf_tarihi TEXT,
            il TEXT,
            ilce TEXT,
            mahalle TEXT,
            tarama_tarihi TEXT NOT NULL,
            UNIQUE(mekan_id, gorsel_url)
        );

        CREATE TABLE IF NOT EXISTS mekan_menu_kalemleri_ve_fiyat_tarihcesi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT NOT NULL,
            mekan_adi TEXT NOT NULL,
            donem TEXT NOT NULL,
            tarih TEXT NOT NULL,
            fiyat_turu TEXT NOT NULL,
            platform TEXT NOT NULL,
            kategori TEXT NOT NULL,
            urun_adi TEXT NOT NULL,
            fiyat REAL NOT NULL,
            tam_adres TEXT NOT NULL DEFAULT '',
            mahalle TEXT,
            ilce TEXT NOT NULL,
            il TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            guncellenme_tarihi TEXT NOT NULL,
            UNIQUE(mekan_id, urun_adi, donem, fiyat_turu)
        );

        CREATE TABLE IF NOT EXISTS menu_tarama_kuyrugu (
            mekan_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            durum TEXT NOT NULL DEFAULT 'queued',
            deneme_sayisi INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT,
            leased_at TEXT,
            completed_at TEXT,
            son_hata TEXT,
            son_sonuc_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_menu_queue_status ON menu_tarama_kuyrugu(durum, updated_at);

        CREATE TABLE IF NOT EXISTS menu_tarama_gozlemleri (
            observation_id TEXT PRIMARY KEY,
            mekan_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            kaynak_url TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(mekan_id, payload_sha256)
        );

        CREATE TABLE IF NOT EXISTS menu_fiyat_gozlemleri (
            observation_id TEXT PRIMARY KEY,
            mekan_id TEXT NOT NULL,
            mekan_adi TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            urun_adi TEXT NOT NULL,
            fiyat REAL NOT NULL,
            para_birimi TEXT NOT NULL DEFAULT 'TRY',
            fiyat_saglayici TEXT NOT NULL,
            saglayici_kaniti TEXT,
            ham_fiyat_metni TEXT,
            kategori TEXT,
            kaynak_url TEXT NOT NULL,
            yakalama_yontemi TEXT NOT NULL,
            guven_puani REAL NOT NULL,
            il TEXT NOT NULL,
            ilce TEXT NOT NULL,
            mahalle TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_menu_observation_venue_time
            ON menu_fiyat_gozlemleri(mekan_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_menu_observation_provider
            ON menu_fiyat_gozlemleri(fiyat_saglayici, observed_at);

        -- Google "Popüler saatler": gün × saat yoğunluk yüzdesi (100 = haftanın en yoğun saati).
        -- Her tarama ayrı bir gözlemdir; aynı mekan için sonraki taramalar üzerine yazmaz, eklenir.
        CREATE TABLE IF NOT EXISTS mekan_populer_saatler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mekan_id TEXT NOT NULL,
            mekan_adi TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            gun TEXT NOT NULL,
            gun_no INTEGER NOT NULL,
            saat INTEGER NOT NULL,
            yogunluk_yuzde INTEGER NOT NULL,
            il TEXT, ilce TEXT, mahalle TEXT,
            UNIQUE(mekan_id, observed_at, gun_no, saat)
        );
        CREATE INDEX IF NOT EXISTS idx_populer_saatler_mekan ON mekan_populer_saatler(mekan_id, observed_at);
        CREATE TABLE IF NOT EXISTS mekan_populer_saat_ozeti (
            mekan_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            canli_durum TEXT,
            bekleme_suresi TEXT,
            kalis_suresi TEXT,
            gun_sayisi INTEGER NOT NULL,
            PRIMARY KEY(mekan_id, observed_at)
        );
        """
    )

    for column, definition in (
        ("kategori", "TEXT"),
        ("kaynak_url", "TEXT"),
        ("yakalama_yontemi", "TEXT"),
        ("guven_puani", "REAL"),
        ("yerel_dosya", "TEXT"),          # IMAGE_DIR'e göre göreli yol; NULL = henüz indirilmedi
        ("dosya_boyutu", "INTEGER"),
        ("genislik", "INTEGER"),
        ("yukseklik", "INTEGER"),
        ("indirme_denemesi", "INTEGER NOT NULL DEFAULT 0"),
        ("indirme_hatasi", "TEXT"),
    ):
        add_column_if_missing(conn, "mekan_menu_gorselleri", column, definition)

    for column, definition in (
        ("aciklama", "TEXT"),
        ("orijinal_fiyat", "REAL"),
        ("para_birimi", "TEXT DEFAULT 'TRY'"),
        ("fiyat_saglayici", "TEXT"),
        ("saglayici_kaniti", "TEXT"),
        ("ham_fiyat_metni", "TEXT"),
        ("kaynak_url", "TEXT"),
        ("yakalama_yontemi", "TEXT"),
        ("guven_puani", "REAL"),
        ("degerlendirme_sayisi", "INTEGER"),
    ):
        add_column_if_missing(conn, "mekan_menu_kalemleri_ve_fiyat_tarihcesi", column, definition)

    # Kuyruk önceliği (büyük önce): il katmanı + kategorinin geçmiş menü bulma oranı. Bkz. queue_priority.
    add_column_if_missing(conn, "menu_tarama_kuyrugu", "oncelik", "REAL NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_menu_queue_priority ON menu_tarama_kuyrugu(durum, oncelik)")
    # Paralel tarama: her kiralama hangi eklenti çalışanına (pencere/sekme yuvası) verildiğini tutar.
    add_column_if_missing(conn, "menu_tarama_kuyrugu", "isci", "TEXT")
    # Tek seferlik bakım işlerinin (ör. zaman aşımı yeniden kuyruğu) yapıldığını hatırlar.
    conn.execute("CREATE TABLE IF NOT EXISTS menu_bot_meta (anahtar TEXT PRIMARY KEY, deger TEXT, updated_at TEXT)")
    conn.commit()


def load_population_rules() -> tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]]:
    if not sqlite_has_table(DB_STATS, "demografi"):
        raise FileNotFoundError(f"Bölge istatistik veritabanı bulunamadı ya da boş: {DB_STATS}")
    conn = sqlite3.connect(f"file:{DB_STATS}?mode=ro", uri=True)
    try:
        counties = {
            (normalize_tr(city), normalize_tr(county)): int(population)
            for city, county, population in conn.execute(
                """
                SELECT i.ad, c.ad, d.nufus_toplam
                FROM demografi d
                JOIN ref_il i ON i.city_id=d.city_id
                JOIN ref_ilce c ON c.city_id=d.city_id AND c.county_id=d.county_id
                WHERE d.seviye='ilce' AND d.nufus_toplam >= ?
                """,
                (MIN_COUNTY_POPULATION,),
            )
        }
        neighborhoods = {
            (normalize_tr(city), normalize_tr(county), normalize_tr(neighborhood)): int(population)
            for city, county, neighborhood, population in conn.execute(
                """
                SELECT i.ad, c.ad, m.ad, d.nufus_toplam
                FROM demografi d
                JOIN ref_il i ON i.city_id=d.city_id
                JOIN ref_ilce c ON c.city_id=d.city_id AND c.county_id=d.county_id
                JOIN ref_mahalle m
                  ON m.city_id=d.city_id AND m.county_id=d.county_id AND m.district_id=d.district_id
                WHERE d.seviye='mahalle' AND d.nufus_toplam >= ?
                """,
                (MIN_NEIGHBORHOOD_POPULATION,),
            )
        }
    finally:
        conn.close()
    return counties, neighborhoods


def discover_places_databases() -> list[Path]:
    databases = [DB_PLACES, DB_PLACES_MENU_KAYNAK]
    shard_pattern = "github_actions/endeks3/run_*/artifacts/shard-db-*/google_places_ve_yogunluk.sqlite"
    if RAW_INTAKE_ROOT.exists():
        databases.extend(sorted(RAW_INTAKE_ROOT.glob(shard_pattern)))
    unique: list[Path] = []
    seen: set[Path] = set()
    for database in databases:
        resolved = database.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if sqlite_has_table(resolved, "google_places_ticari_yogunluk"):
            unique.append(resolved)
        elif resolved.exists():
            print(f"UYARI: boş ya da tablosuz Google veritabanı atlandı: {resolved}")
    return unique


def load_eligible_venues() -> tuple[list[dict[str, Any]], Counter[str]]:
    databases = discover_places_databases()
    if not databases:
        raise FileNotFoundError(f"Google Places veritabanı bulunamadı: {DB_PLACES}")
    valid_counties, valid_neighborhoods = load_population_rules()
    eligible_by_id: dict[str, dict[str, Any]] = {}
    reasons: Counter[str] = Counter()
    for database in databases:
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT google_place_id, cid, isim, ana_kategori, tum_kategoriler,
                       COALESCE(NULLIF(degerlendirme_sayisi, 0), NULLIF(yorum_sayisi, 0), 0) AS review_count,
                       tam_adres, mahalle, ilce, il, lat, lon, maps_url, kaynak, guncellenme_tarihi
                FROM google_places_ticari_yogunluk
                WHERE kaynak LIKE 'Google Maps%'
                  AND COALESCE(NULLIF(degerlendirme_sayisi, 0), NULLIF(yorum_sayisi, 0), 0) >= ?
                """,
                (MIN_REVIEWS,),
            )
            for row in rows:
                canonical_city = canonical_target_city(row["il"])
                if canonical_city is None:
                    continue
                if not row["google_place_id"] or not is_food_category(
                    row["ana_kategori"], row["tum_kategoriler"], row["isim"]
                ):
                    reasons["food_category"] += 1
                    continue
                if SCOPE == "restoran" and not is_restaurant_category(row["ana_kategori"]):
                    reasons["restoran_disi_kategori"] += 1
                    continue
                city_key = normalize_tr(canonical_city)
                county_key = normalize_tr(row["ilce"])
                neighborhood_key = normalize_tr(row["mahalle"])
                county_population = valid_counties.get((city_key, county_key))
                if county_population is None:
                    reasons["county_population_or_match"] += 1
                    continue
                if not neighborhood_key or is_village_name(row["mahalle"]):
                    reasons["missing_or_village_neighborhood"] += 1
                    continue
                neighborhood_population = valid_neighborhoods.get((city_key, county_key, neighborhood_key))
                if neighborhood_population is None:
                    reasons["neighborhood_population_or_match"] += 1
                    continue
                venue = {
                    "id": row["google_place_id"], "cid": row["cid"], "adi": row["isim"],
                    "kategori": row["ana_kategori"], "tum_kategoriler": row["tum_kategoriler"],
                    "degerlendirme_sayisi": int(row["review_count"]), "tam_adres": row["tam_adres"] or "",
                    "mahalle": row["mahalle"], "ilce": row["ilce"], "il": canonical_city,
                    "ilce_nufusu": county_population, "mahalle_nufusu": neighborhood_population,
                    "lat": float(row["lat"]), "lon": float(row["lon"]), "maps_url": row["maps_url"],
                    "google_kaynagi": row["kaynak"], "google_guncellenme_tarihi": row["guncellenme_tarihi"],
                    "kaynak_veritabani": str(database),
                }
                # Sorguya "menü" eklenmez: mekan adı + adres tek başına bilgi panelini getirir,
                # "Menü" düğmesi panelde zaten var; fazladan kelime Google'ı web sonuçlarına kaydırabiliyor.
                venue["query"] = f'{venue["adi"]} {venue["tam_adres"]}'.strip()
                current = eligible_by_id.get(venue["id"])
                if current is None or venue["degerlendirme_sayisi"] > current["degerlendirme_sayisi"]:
                    eligible_by_id[venue["id"]] = venue
        finally:
            conn.close()
    eligible = sorted(
        eligible_by_id.values(),
        key=lambda venue: (-venue["degerlendirme_sayisi"], venue["il"], venue["adi"]),
    )
    return eligible, reasons


def category_menu_rates(conn: sqlite3.Connection) -> tuple[dict[str, float], float]:
    """Taranmış mekanlardan kategori başına menü bulma oranı (fiyat ya da menü fotoğrafı çıkan / taranan).

    Az örnekli kategoriler genel orana doğru çekilir (m-tahmini, m=20), böylece 2-3 şanslı mekan
    bir kategoriyi kuyruğun başına taşımaz. Hiç veri yoksa tüm kategoriler eşit kalır.
    """
    rows = conn.execute(
        """
        SELECT json_extract(payload_json, '$.kategori') AS kategori,
               SUM(durum='completed') AS menulu, COUNT(*) AS taranan
        FROM menu_tarama_kuyrugu
        WHERE durum IN ('completed', 'completed_no_menu', 'failed')
        GROUP BY 1
        """
    ).fetchall()
    total = sum(row["taranan"] for row in rows)
    overall = (sum(row["menulu"] for row in rows) / total) if total else 0.5
    prior = 20
    rates = {
        normalize_tr(row["kategori"]): (row["menulu"] + prior * overall) / (row["taranan"] + prior)
        for row in rows
    }
    return rates, overall


def queue_priority(venue: dict[str, Any], rates: dict[str, float], overall: float) -> float:
    """Büyük değer önce taranır: il katmanı (Batı büyükşehirleri 2, ilk tur illeri 1, diğer 0) × 10
    + kategorinin menü bulma oranı (0–1) + çok küçük bir yorum sayısı katkısı (eşitlik bozucu)."""
    tier = CITY_TIER.get(normalize_tr(venue.get("il")), 0)
    rate = rates.get(normalize_tr(venue.get("kategori")), overall)
    reviews = min(int(venue.get("degerlendirme_sayisi") or 0), 10_000)
    return round(tier * 10 + rate + reviews / 1_000_000, 6)


def sync_queue(conn: sqlite3.Connection, venues: list[dict[str, Any]], loaded_sources: list[Path]) -> None:
    now = now_iso()
    # Aynı mekan farklı Google ambarlarında farklı kimlikle gelebilir (eski kayıtlar 24 karakterlik kod,
    # güncel ambar Google'ın kendi place kodu). Google cid her ikisinde ortak: kuyrukta aynı cid'li kayıt
    # varsa onun kimliği kullanılır, böylece taranmış mekan yeniden kuyruğa girmez.
    id_by_cid: dict[str, str] = {}
    for row in conn.execute(
        "SELECT mekan_id, json_extract(payload_json, '$.cid') AS cid FROM menu_tarama_kuyrugu ORDER BY created_at"
    ):
        if row["cid"] and str(row["cid"]) not in id_by_cid:
            id_by_cid[str(row["cid"])] = row["mekan_id"]
    for venue in venues:
        existing_id = id_by_cid.get(str(venue.get("cid") or ""))
        if existing_id and existing_id != venue["id"]:
            venue["google_place_id_kaynak"] = venue["id"]
            venue["id"] = existing_id
    eligible_ids = {venue["id"] for venue in venues}
    loaded = {str(path) for path in loaded_sources}
    rates, overall = category_menu_rates(conn)
    with conn:
        for venue in venues:
            payload = json.dumps(venue, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT INTO menu_tarama_kuyrugu (mekan_id, payload_json, durum, oncelik, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?)
                ON CONFLICT(mekan_id) DO UPDATE SET payload_json=excluded.payload_json,
                    oncelik=excluded.oncelik, updated_at=excluded.updated_at,
                    durum=CASE WHEN menu_tarama_kuyrugu.durum='excluded' THEN 'queued' ELSE menu_tarama_kuyrugu.durum END
                """,
                (venue["id"], payload, queue_priority(venue, rates, overall), now, now),
            )
        kept_missing_source = 0
        for row in conn.execute(
            """
            SELECT mekan_id, json_extract(payload_json, '$.il') AS il,
                   json_extract(payload_json, '$.kategori') AS kategori,
                   json_extract(payload_json, '$.kaynak_veritabani') AS kaynak
            FROM menu_tarama_kuyrugu WHERE durum IN ('queued','in_progress','blocked')
            """
        ).fetchall():
            if row["mekan_id"] not in eligible_ids:
                # Kaynak veritabanı bu açılışta okunamadıysa (silinmiş/taşınmış) mekanın uygunluğu
                # bilinemez: il/kapsam kuralı açıkça dışarıda bırakmıyorsa kuyrukta kalır.
                in_scope = canonical_target_city(row["il"]) is not None and (
                    SCOPE != "restoran" or is_restaurant_category(row["kategori"])
                )
                if row["kaynak"] and row["kaynak"] not in loaded and in_scope:
                    kept_missing_source += 1
                    continue
                conn.execute(
                    "UPDATE menu_tarama_kuyrugu SET durum='excluded', son_hata=?, updated_at=? WHERE mekan_id=?",
                    ("Güncel kapsam veya uygunluk kurallarının dışında", now, row["mekan_id"]),
                )
    if kept_missing_source:
        print(f"UYARI: kaynak veritabanı okunamayan {kept_missing_source} mekan kuyrukta bırakıldı (dışarı atılmadı).")


TWIN_MAX_DEGREES = 0.002  # ~200 m


def mark_duplicate_twins(conn: sqlite3.Connection) -> int:
    """Google ambarında aynı mekan farklı kimlikle birden çok kez bulunabiliyor (aynı ad, il, ilçe ve
    ~200 m içinde). Bunlar aynı Google aramasını tekrarlatır; eşi taranmış ya da kuyrukta önde olan
    kayıt bırakılır, diğeri silinmeden 'excluded' + nedeniyle işaretlenir (taranan eşin sonucu geçerlidir)."""
    rows = conn.execute(
        "SELECT mekan_id, durum, payload_json FROM menu_tarama_kuyrugu "
        "WHERE durum IN ('queued','blocked','in_progress','completed','completed_no_menu')"
    ).fetchall()
    groups: dict[tuple[str, str, str], list[tuple[int, int, str, str, float, float]]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("lat") is None or payload.get("lon") is None:
            continue
        key = (normalize_tr(payload.get("adi")), normalize_tr(payload.get("il")), normalize_tr(payload.get("ilce")))
        rank = 0 if row["durum"].startswith("completed") else 1 if row["durum"] == "in_progress" else 2
        groups.setdefault(key, []).append((rank, -int(payload.get("degerlendirme_sayisi") or 0), row["mekan_id"],
                                           row["durum"], float(payload["lat"]), float(payload["lon"])))
    now = now_iso()
    marked = 0
    with conn:
        for members in groups.values():
            if len(members) < 2:
                continue
            kept: list[tuple[str, float, float]] = []
            for _, _, venue_id, status, lat, lon in sorted(members):
                twin = next((k for k in kept if abs(k[1] - lat) < TWIN_MAX_DEGREES and abs(k[2] - lon) < TWIN_MAX_DEGREES), None)
                if twin and status in ("queued", "blocked"):
                    conn.execute(
                        "UPDATE menu_tarama_kuyrugu SET durum='excluded', son_hata=?, updated_at=? WHERE mekan_id=?",
                        (f"Tekrar kayıt: {twin[0]} ile aynı ad/ilçe ve ~200 m içinde", now, venue_id),
                    )
                    marked += 1
                elif not twin:
                    kept.append((venue_id, lat, lon))
    return marked


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {row["durum"]: row["adet"] for row in conn.execute(
        "SELECT durum, COUNT(*) AS adet FROM menu_tarama_kuyrugu GROUP BY durum"
    )}
    result["total"] = sum(result.values())
    return result


def queue_counts_by_city(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["il"]: row["adet"]
        for row in conn.execute(
            """
            SELECT json_extract(payload_json, '$.il') AS il, COUNT(*) AS adet
            FROM menu_tarama_kuyrugu
            WHERE durum != 'excluded'
            GROUP BY json_extract(payload_json, '$.il')
            ORDER BY il
            """
        )
        if row["il"]
    }


def lease_next(conn: sqlite3.Connection, worker: str = "1") -> dict[str, Any] | None:
    """Çalışana (worker) bir mekan kiralar. Aynı çalışanın süresi dolmamış kiralaması varsa onu
    "resumed" olarak geri verir (sekme yenilendi/servis çalışanı uyandı); başka çalışanlarınkine dokunmaz."""
    with LEASE_LOCK:
        return _lease_next_locked(conn, worker)


def _lease_next_locked(conn: sqlite3.Connection, worker: str) -> dict[str, Any] | None:
    now = now_iso()
    expiry_epoch = time.time() - LEASE_TIMEOUT_SECONDS
    with conn:
        for active in conn.execute(
            "SELECT * FROM menu_tarama_kuyrugu WHERE durum='in_progress' ORDER BY leased_at"
        ).fetchall():
            try:
                leased_epoch = calendar.timegm(time.strptime(active["leased_at"], "%Y-%m-%dT%H:%M:%SZ"))
            except (TypeError, ValueError):
                leased_epoch = 0
            if leased_epoch < expiry_epoch:
                conn.execute(
                    "UPDATE menu_tarama_kuyrugu SET durum='queued', lease_token=NULL, leased_at=NULL, isci=NULL, updated_at=? WHERE mekan_id=?",
                    (now, active["mekan_id"]),
                )
            elif (active["isci"] or "1") == worker:
                payload = json.loads(active["payload_json"])
                payload.update({"lease_token": active["lease_token"], "resumed": True, "isci": worker})
                return payload
        row = conn.execute(
            """
            SELECT * FROM menu_tarama_kuyrugu
            WHERE durum IN ('queued','blocked') AND deneme_sayisi < ?
            ORDER BY CASE durum WHEN 'blocked' THEN 0 ELSE 1 END, oncelik DESC, updated_at, mekan_id LIMIT 1
            """,
            (MAX_ATTEMPTS,),
        ).fetchone()
        if not row:
            return None
        token = uuid.uuid4().hex
        conn.execute(
            """
            UPDATE menu_tarama_kuyrugu SET durum='in_progress', lease_token=?, leased_at=?, isci=?,
                deneme_sayisi=deneme_sayisi+1, updated_at=? WHERE mekan_id=?
            """,
            (token, now, worker, now, row["mekan_id"]),
        )
        payload = json.loads(row["payload_json"])
        payload.update({"lease_token": token, "resumed": False, "isci": worker})
        return payload


def require_queue_lease(conn: sqlite3.Connection, data: dict[str, Any]) -> sqlite3.Row:
    venue_id = str(data.get("id") or "")
    token = str(data.get("lease_token") or "")
    row = conn.execute("SELECT * FROM menu_tarama_kuyrugu WHERE mekan_id=?", (venue_id,)).fetchone()
    if not row or row["durum"] != "in_progress" or not token or token != row["lease_token"]:
        raise ValueError("Geçersiz veya süresi dolmuş kuyruk kiralaması")
    return row


def clean_text(value: Any, max_length: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def safe_menu_image(value: Any) -> str | None:
    url = clean_text(value, 2_000)
    parsed = urlparse(url)
    return url if parsed.scheme == "https" and parsed.netloc else None


DAY_ORDER = {"Pazartesi": 1, "Salı": 2, "Çarşamba": 3, "Perşembe": 4, "Cuma": 5, "Cumartesi": 6, "Pazar": 7}


def save_popular_times(conn: sqlite3.Connection, venue: dict[str, Any], observed_at: str, payload: Any) -> int:
    """Eklentinin gönderdiği popüler saatleri satır satır yazar; yazılan (gün, saat) sayısını döndürür."""
    if not isinstance(payload, dict) or not isinstance(payload.get("gunler"), dict):
        return 0
    rows = 0
    for day, hours in payload["gunler"].items():
        day_no = DAY_ORDER.get(str(day))
        if not day_no or not isinstance(hours, list):
            continue
        for entry in hours:
            if not isinstance(entry, dict):
                continue
            match = re.match(r"^(\d{1,2}):00$", str(entry.get("saat") or ""))
            try:
                percent = int(entry.get("yuzde"))
            except (TypeError, ValueError):
                continue
            if not match or not 0 <= percent <= 100:
                continue
            rows += conn.execute(
                """INSERT OR IGNORE INTO mekan_populer_saatler
                   (mekan_id, mekan_adi, observed_at, gun, gun_no, saat, yogunluk_yuzde, il, ilce, mahalle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (venue["id"], venue["adi"], observed_at, day, day_no, int(match.group(1)), percent,
                 venue["il"], venue["ilce"], venue["mahalle"]),
            ).rowcount
    if rows:
        conn.execute(
            """INSERT OR REPLACE INTO mekan_populer_saat_ozeti
               (mekan_id, observed_at, canli_durum, bekleme_suresi, kalis_suresi, gun_sayisi)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (venue["id"], observed_at, clean_text(payload.get("canli"), 120) or None,
             clean_text(payload.get("bekleme"), 120) or None, clean_text(payload.get("kalis_suresi"), 120) or None,
             len([d for d in payload["gunler"] if d in DAY_ORDER])),
        )
    return rows


def save_result(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, int | str]:
    queue_row = require_queue_lease(conn, data)
    venue = json.loads(queue_row["payload_json"])
    observed_at = now_iso()
    source_url = clean_text(data.get("source_url"), 2_000)
    if not source_url.startswith("https://www.google.com/"):
        raise ValueError("Kaynak URL doğrulanamadı")
    live_reviews = int(data.get("degerlendirme_sayisi") or 0)
    if live_reviews and live_reviews < MIN_REVIEWS:
        # Kural gereği kapsam dışı ama bu bir tarama sonucu, istek hatası değil: 400 döndürmek eklentiye
        # "sunucu bozuk" dedirtip bütün taramayı durduruyordu. Mekan nedeniyle 'failed' işaretlenir.
        reason = f"Canlı değerlendirme sayısı {MIN_REVIEWS} altında: {live_reviews}"
        with conn:
            conn.execute(
                "UPDATE menu_tarama_kuyrugu SET durum='failed', lease_token=NULL, leased_at=NULL, son_hata=?, updated_at=? WHERE mekan_id=?",
                (reason, observed_at, queue_row["mekan_id"]),
            )
        return {"status": "failed", "prices": 0, "images": 0, "busy_hours": 0, "reason": reason}
    raw_payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    payload_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    observation_id = hashlib.sha256(f'{venue["id"]}|{payload_hash}'.encode()).hexdigest()
    prices = data.get("fiyatlar") if isinstance(data.get("fiyatlar"), list) else []
    images = data.get("gorseller") if isinstance(data.get("gorseller"), list) else []
    price_count = 0
    image_count = 0
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO menu_tarama_gozlemleri
               (observation_id, mekan_id, observed_at, kaynak_url, payload_sha256, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (observation_id, venue["id"], observed_at, source_url, payload_hash, raw_payload),
        )
        for item in prices[:500]:
            if not isinstance(item, dict):
                continue
            product = clean_text(item.get("urun"), 180)
            provider = clean_text(item.get("saglayici") or data.get("fiyat_saglayici") or "Google", 100)
            provider_evidence = clean_text(item.get("saglayici_kaniti") or data.get("saglayici_kaniti"), 300)
            raw_price = clean_text(item.get("ham_fiyat_metni"), 100)
            method = clean_text(item.get("yakalama_yontemi") or "google_menu_panel", 80)
            confidence = max(0.0, min(float(item.get("guven_puani") or 0.0), 1.0))
            try:
                price = float(item.get("fiyat"))
            except (TypeError, ValueError):
                continue
            if len(product) < 2 or price <= 0 or price > 100_000 or confidence < 0.65:
                continue
            item_id = hashlib.sha256(json.dumps(
                [venue["id"], observed_at, product, price, provider, source_url], ensure_ascii=False
            ).encode()).hexdigest()
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO menu_fiyat_gozlemleri
                    (observation_id, mekan_id, mekan_adi, observed_at, urun_adi, fiyat,
                     para_birimi, fiyat_saglayici, saglayici_kaniti, ham_fiyat_metni,
                     kategori, kaynak_url, yakalama_yontemi, guven_puani,
                     il, ilce, mahalle, lat, lon)
                VALUES (?, ?, ?, ?, ?, ?, 'TRY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, venue["id"], venue["adi"], observed_at, product, price, provider,
                 provider_evidence, raw_price, clean_text(item.get("kategori") or "Menü", 100),
                 source_url, method, confidence, venue["il"], venue["ilce"], venue["mahalle"],
                 venue["lat"], venue["lon"]),
            ).rowcount
            if not inserted:
                continue
            price_count += 1
            price_type = f"BilgiPanosu:{provider}"[:100]
            conn.execute(
                """
                INSERT INTO mekan_menu_kalemleri_ve_fiyat_tarihcesi
                    (mekan_id, mekan_adi, donem, tarih, fiyat_turu, platform, kategori,
                     urun_adi, fiyat, para_birimi, fiyat_saglayici, saglayici_kaniti,
                     ham_fiyat_metni, kaynak_url, yakalama_yontemi, guven_puani,
                     degerlendirme_sayisi, tam_adres, mahalle, ilce, il, lat, lon, guncellenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'TRY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mekan_id, urun_adi, donem, fiyat_turu) DO UPDATE SET
                    fiyat=excluded.fiyat, platform=excluded.platform,
                    fiyat_saglayici=excluded.fiyat_saglayici, saglayici_kaniti=excluded.saglayici_kaniti,
                    ham_fiyat_metni=excluded.ham_fiyat_metni, kaynak_url=excluded.kaynak_url,
                    yakalama_yontemi=excluded.yakalama_yontemi, guven_puani=excluded.guven_puani,
                    degerlendirme_sayisi=excluded.degerlendirme_sayisi,
                    guncellenme_tarihi=excluded.guncellenme_tarihi
                """,
                (venue["id"], venue["adi"], observed_at[:7], observed_at, price_type, provider,
                 clean_text(item.get("kategori") or "Menü", 100), product, price, provider,
                 provider_evidence, raw_price, source_url, method, confidence,
                 live_reviews or venue["degerlendirme_sayisi"], venue["tam_adres"], venue["mahalle"],
                 venue["ilce"], venue["il"], venue["lat"], venue["lon"], observed_at),
            )
        for image in images[:30]:
            image_url = safe_menu_image(image.get("url") if isinstance(image, dict) else image)
            confidence = float(image.get("guven_puani") or 0.0) if isinstance(image, dict) else 0.0
            if not image_url or confidence < 0.75:
                continue
            # Panel bölümü ("Menüyü göster" = menü kartı fotoğrafı, "Menüde öne çıkanlar" = yemek fotoğrafı)
            section = clean_text(image.get("bolum") if isinstance(image, dict) else "", 60) or "Menü"
            image_count += conn.execute(
                """
                INSERT OR IGNORE INTO mekan_menu_gorselleri
                    (mekan_id, mekan_adi, gorsel_url, kaynak, kategori, tarama_tarihi,
                     il, ilce, mahalle, kaynak_url, yakalama_yontemi, guven_puani)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'google_menu_panel', ?)
                """,
                (venue["id"], venue["adi"], image_url,
                 clean_text(data.get("fiyat_saglayici") or "Google", 100), section, observed_at,
                 venue["il"], venue["ilce"], venue["mahalle"], source_url, confidence),
            ).rowcount
        busy_rows = save_popular_times(conn, venue, observed_at, data.get("populer_saatler"))
        final_status = "completed" if price_count or image_count else "completed_no_menu"
        summary = json.dumps({"prices": price_count, "images": image_count, "busy_hours": busy_rows,
                              "provider": data.get("fiyat_saglayici")}, ensure_ascii=False)
        conn.execute(
            """
            UPDATE menu_tarama_kuyrugu SET durum=?, completed_at=?, lease_token=NULL,
                leased_at=NULL, son_hata=NULL, son_sonuc_json=?, updated_at=? WHERE mekan_id=?
            """,
            (final_status, observed_at, summary, observed_at, venue["id"]),
        )
    return {"status": final_status, "prices": price_count, "images": image_count, "busy_hours": busy_rows}


def fail_job(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, str]:
    row = require_queue_lease(conn, data)
    reason = clean_text(data.get("error") or "Bilinmeyen tarama hatası", 500)
    blocked = bool(data.get("blocked"))
    next_status = "blocked" if blocked else ("failed" if row["deneme_sayisi"] >= MAX_ATTEMPTS else "queued")
    with conn:
        conn.execute(
            "UPDATE menu_tarama_kuyrugu SET durum=?, lease_token=NULL, leased_at=NULL, son_hata=?, updated_at=? WHERE mekan_id=?",
            (next_status, reason, now_iso(), row["mekan_id"]),
        )
    return {"status": next_status}




# --- Görsel indirme --------------------------------------------------------------
_download_lock = threading.Lock()


def image_profile(section: str | None) -> tuple[int, int]:
    """Bölüme göre (uzun kenar px, JPEG kalitesi). Bilinmeyen bölüm menü kartı sayılır (OCR güvenli)."""
    if normalize_tr(section) in {normalize_tr(name) for name in DISH_SECTIONS}:
        return DISH_IMAGE_EDGE, DISH_IMAGE_QUALITY
    return MENU_IMAGE_EDGE, MENU_IMAGE_QUALITY


def sized_google_image_url(url: str, edge: int) -> str:
    """lh3.googleusercontent.com adresleri '=w1080-h1080' son ekiyle sunucu tarafında küçültülür."""
    if "googleusercontent.com" in url and "=" in url:
        return f"{url.split('=', 1)[0]}=w{edge}-h{edge}"
    return url


def fetch_image_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": IMAGE_USER_AGENT, "Accept": "image/*"})
    with urlopen(request, timeout=IMAGE_DOWNLOAD_TIMEOUT) as response:
        return response.read()


def shrink_image(data: bytes, edge: int, quality: int) -> tuple[bytes, int, int]:
    if Image is None:
        return data, 0, 0
    import io
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((edge, edge))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue(), image.width, image.height


def local_image_path(row: sqlite3.Row) -> Path:
    digest = hashlib.sha1(row["gorsel_url"].encode("utf-8")).hexdigest()[:16]
    return Path(normalize_tr(row["il"]) or "bilinmeyen") / row["mekan_id"] / f"{digest}.jpg"


def download_image_row(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    edge, quality = image_profile(row["kategori"])
    relative = local_image_path(row)
    target = IMAGE_DIR / relative
    try:
        raw = fetch_image_bytes(sized_google_image_url(row["gorsel_url"], edge))
        data, width, height = shrink_image(raw, edge, quality)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        with conn:
            conn.execute(
                """UPDATE mekan_menu_gorselleri SET yerel_dosya=?, dosya_boyutu=?, genislik=?, yukseklik=?,
                   indirme_denemesi=indirme_denemesi+1, indirme_hatasi=NULL WHERE id=?""",
                (relative.as_posix(), len(data), width, height, row["id"]),
            )
        return True
    except Exception as exc:  # ağ/karar hataları kaydı bozmasın, sonraki turda tekrar denensin
        with conn:
            conn.execute(
                "UPDATE mekan_menu_gorselleri SET indirme_denemesi=indirme_denemesi+1, indirme_hatasi=? WHERE id=?",
                (clean_text(f"{type(exc).__name__}: {exc}", 300), row["id"]),
            )
        return False


def download_pending_images(limit: int = 500) -> dict[str, int]:
    """Henüz indirilmemiş görselleri sırayla indirir. Aynı anda tek iş parçacığı çalışır."""
    if not _download_lock.acquire(blocking=False):
        return {"skipped": 1}
    done = failed = 0
    try:
        with connect_menu_db() as conn:
            rows = conn.execute(
                """SELECT id, mekan_id, gorsel_url, kategori, il FROM mekan_menu_gorselleri
                   WHERE yerel_dosya IS NULL AND indirme_denemesi < ? ORDER BY id LIMIT ?""",
                (IMAGE_MAX_DOWNLOAD_ATTEMPTS, limit),
            ).fetchall()
            for row in rows:
                if download_image_row(conn, row):
                    done += 1
                else:
                    failed += 1
        if done or failed:
            print(f"[{now_iso()}] görsel indirme: {done} tamam, {failed} hata")
    finally:
        _download_lock.release()
    return {"done": done, "failed": failed}


def start_image_download_thread() -> None:
    threading.Thread(target=download_pending_images, daemon=True, name="geoprop-image-download").start()


# --- Canlı pano sorguları -------------------------------------------------------
# Pano (GET /) yalnız okur; kuyruk ve gözlem tablolarından son durumu derler.

def dashboard_totals(conn: sqlite3.Connection) -> dict[str, Any]:
    prices, priced_venues = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT mekan_id) FROM menu_fiyat_gozlemleri"
    ).fetchone()
    images, image_venues = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT mekan_id) FROM mekan_menu_gorselleri"
    ).fetchone()
    downloaded, disk_bytes, download_failed = conn.execute(
        """SELECT COUNT(yerel_dosya), COALESCE(SUM(dosya_boyutu), 0),
                  SUM(CASE WHEN yerel_dosya IS NULL AND indirme_denemesi >= ? THEN 1 ELSE 0 END)
           FROM mekan_menu_gorselleri""",
        (IMAGE_MAX_DOWNLOAD_ATTEMPTS,),
    ).fetchone()
    providers = {
        row["fiyat_saglayici"]: row["adet"] for row in conn.execute(
            "SELECT fiyat_saglayici, COUNT(*) AS adet FROM menu_fiyat_gozlemleri GROUP BY 1 ORDER BY 2 DESC"
        )
    }
    busy_venues = conn.execute("SELECT COUNT(DISTINCT mekan_id) FROM mekan_populer_saatler").fetchone()[0]
    return {
        "busy_venues": busy_venues,
        "prices": prices, "priced_venues": priced_venues,
        "images": images, "image_venues": image_venues, "providers": providers,
        "downloaded": downloaded, "disk_bytes": disk_bytes, "download_failed": download_failed or 0,
        "image_dir": str(IMAGE_DIR),
    }


def recent_venues(conn: sqlite3.Connection, limit: int = 60) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT mekan_id, payload_json, durum, deneme_sayisi, completed_at, updated_at, son_hata, son_sonuc_json
        FROM menu_tarama_kuyrugu
        WHERE durum IN ('completed', 'completed_no_menu', 'failed', 'blocked', 'in_progress')
        ORDER BY updated_at DESC LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        summary = json.loads(row["son_sonuc_json"]) if row["son_sonuc_json"] else {}
        result.append({
            "id": row["mekan_id"], "adi": payload.get("adi"), "il": payload.get("il"),
            "ilce": payload.get("ilce"), "mahalle": payload.get("mahalle"),
            "kategori": payload.get("kategori"), "degerlendirme_sayisi": payload.get("degerlendirme_sayisi"),
            "durum": row["durum"], "deneme": row["deneme_sayisi"],
            "fiyat": int(summary.get("prices") or 0), "gorsel": int(summary.get("images") or 0),
            "populer_saat": int(summary.get("busy_hours") or 0),
            "saglayici": summary.get("provider"), "tamamlanma": row["completed_at"],
            "guncelleme": row["updated_at"], "hata": row["son_hata"],
        })
    return result


def venue_detail(conn: sqlite3.Connection, venue_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json, durum, son_hata, completed_at FROM menu_tarama_kuyrugu WHERE mekan_id=?",
        (venue_id,),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload_json"])
    prices = [
        dict(price) for price in conn.execute(
            """
            SELECT urun_adi, fiyat, fiyat_saglayici, kategori, ham_fiyat_metni, guven_puani, observed_at, kaynak_url
            FROM menu_fiyat_gozlemleri WHERE mekan_id=? ORDER BY observed_at DESC, urun_adi
            """,
            (venue_id,),
        )
    ]
    images = [
        dict(image) for image in conn.execute(
            "SELECT gorsel_url, kaynak, kategori, tarama_tarihi, yerel_dosya, dosya_boyutu, genislik, yukseklik, indirme_hatasi FROM mekan_menu_gorselleri WHERE mekan_id=? ORDER BY id",
            (venue_id,),
        )
    ]
    latest = conn.execute(
        "SELECT * FROM mekan_populer_saat_ozeti WHERE mekan_id=? ORDER BY observed_at DESC LIMIT 1", (venue_id,)
    ).fetchone()
    busy = None
    if latest:
        days: dict[str, list[dict[str, int]]] = {}
        for hour_row in conn.execute(
            "SELECT gun, saat, yogunluk_yuzde FROM mekan_populer_saatler WHERE mekan_id=? AND observed_at=? ORDER BY gun_no, saat",
            (venue_id, latest["observed_at"]),
        ):
            days.setdefault(hour_row["gun"], []).append({"saat": hour_row["saat"], "yuzde": hour_row["yogunluk_yuzde"]})
        busy = {**dict(latest), "gunler": days}
    return {
        "id": venue_id, "mekan": payload, "durum": row["durum"], "hata": row["son_hata"],
        "tamamlanma": row["completed_at"], "fiyatlar": prices, "gorseller": images, "populer_saatler": busy,
    }



# --- Canlı pano (GET /) ---------------------------------------------------------
# Tek dosyada, dış bağımlılık yok. 4 saniyede bir /recent'i çeker; satıra tıklanınca /venue ile
# o mekanın fiyat listesi ve görselleri açılır. Aynı origin olduğu için CORS gerekmez.
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GEOPROP Menü Panosu</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1c1f24; --muted:#6b7280; --line:#e5e7eb; --ok:#15803d; --warn:#b45309; --bad:#b91c1c; --run:#1d4ed8; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background:var(--bg); color:var(--ink); }
  header { display:flex; align-items:baseline; gap:16px; padding:14px 20px; background:var(--card); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:2; }
  header h1 { font-size:18px; margin:0; }
  header .pulse { color:var(--muted); font-size:12px; }
  main { display:grid; grid-template-columns: minmax(0, 1.4fr) minmax(360px, 1fr); gap:16px; padding:16px 20px; }
  @media (max-width: 960px) { main { grid-template-columns: 1fr; } }
  .cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:10px; grid-column: 1 / -1; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .card .k { color:var(--muted); font-size:12px; }
  .card .v { font-size:22px; font-weight:700; margin-top:2px; }
  .card .s { color:var(--muted); font-size:12px; margin-top:2px; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  .panel h2 { font-size:14px; margin:0; padding:10px 14px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
  .panel h2 span { color:var(--muted); font-weight:400; font-size:12px; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { font-size:12px; color:var(--muted); font-weight:600; background:#fafafa; position:sticky; top:0; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:#f3f4f6; }
  tbody tr.sel { background:#e8efff; }
  td.num { text-align:right; font-variant-numeric: tabular-nums; }
  .tag { display:inline-block; padding:1px 7px; border-radius:99px; font-size:11px; font-weight:600; }
  .tag.completed { background:#dcfce7; color:var(--ok); }
  .tag.completed_no_menu { background:#fef3c7; color:var(--warn); }
  .tag.failed, .tag.blocked { background:#fee2e2; color:var(--bad); }
  .tag.in_progress { background:#dbeafe; color:var(--run); }
  .muted { color:var(--muted); font-size:12px; }
  .scroll { max-height: calc(100vh - 260px); overflow:auto; }
  #detail .body { padding:12px 14px; }
  #detail .imgs { display:grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap:6px; margin-top:10px; }
  #detail .imgs a { display:block; aspect-ratio:1; overflow:hidden; border-radius:6px; border:1px solid var(--line); background:#eee; }
  #detail .imgs img { width:100%; height:100%; object-fit:cover; }
  .err { color:var(--bad); }
  .cities { display:flex; flex-wrap:wrap; gap:6px; }
  .cities span { background:#f3f4f6; border-radius:6px; padding:2px 8px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>GEOPROP Menü Toplayıcı — canlı pano</h1>
  <div class="pulse" id="pulse">bağlanıyor…</div>
</header>
<main>
  <section class="cards" id="cards"></section>
  <section class="panel">
    <h2>Son işlenen mekanlar <span id="listNote"></span></h2>
    <div class="scroll">
      <table>
        <thead><tr><th>Mekan</th><th>Konum</th><th>Durum</th><th>Sağlayıcı</th><th class="num">Fiyat</th><th class="num">Görsel</th><th class="num">Yoğunluk</th><th>Zaman</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </section>
  <section class="panel" id="detail">
    <h2>Mekan detayı <span id="detailNote">bir satıra tıklayın</span></h2>
    <div class="body" id="detailBody"><div class="muted">Fiyatlar ve görseller burada görünecek.</div></div>
  </section>
</main>
<script>
  const el = (id) => document.getElementById(id);
  const fmtTL = (v) => new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY", maximumFractionDigits: 2 }).format(v);
  const fmtBytes = (b) => b < 1048576 ? (b / 1024).toFixed(0) + " KB" : b < 1073741824 ? (b / 1048576).toFixed(1) + " MB" : (b / 1073741824).toFixed(2) + " GB";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ago = (iso) => {
    if (!iso) return "";
    const sec = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
    if (sec < 60) return Math.round(sec) + " sn önce";
    if (sec < 3600) return Math.round(sec / 60) + " dk önce";
    if (sec < 86400) return (sec / 3600).toFixed(1) + " sa önce";
    return new Date(iso).toLocaleString("tr-TR");
  };
  const STATUS = { completed: "Menü alındı", completed_no_menu: "Menü yok", failed: "Başarısız", blocked: "CAPTCHA", in_progress: "Taranıyor", queued: "Sırada" };
  let selected = null, lastVenueSig = "";

  function renderCards(data) {
    const q = data.queue || {}, t = data.totals || {};
    const done = (q.completed || 0) + (q.completed_no_menu || 0);
    const remaining = (q.queued || 0) + (q.in_progress || 0) + (q.blocked || 0);
    const providers = Object.entries(t.providers || {}).map(([k, v]) => esc(k) + " " + v).join(" · ") || "—";
    const cities = Object.entries(data.cities || {}).sort((a, b) => a[0].localeCompare(b[0], "tr")).map(([c, n]) => "<span>" + esc(c) + " " + n + "</span>").join("");
    el("cards").innerHTML = [
      ["Tamamlanan mekan", done, (q.completed || 0) + " menülü · " + (q.completed_no_menu || 0) + " menüsüz"],
      ["Kalan", remaining, (q.in_progress || 0) + " taranıyor · " + (q.blocked || 0) + " CAPTCHA"],
      ["Başarısız", q.failed || 0, "deneme hakkı biten"],
      ["Toplanan fiyat", t.prices || 0, (t.priced_venues || 0) + " mekandan"],
      ["Popüler saatler", t.busy_venues || 0, "mekan için 7 günlük yoğunluk"],
      ["Toplanan görsel", t.images || 0, (t.image_venues || 0) + " mekandan"],
      ["Diske indirilen", t.downloaded || 0, fmtBytes(t.disk_bytes || 0) + (t.download_failed ? " · " + t.download_failed + " indirilemedi" : "")],
      ["Sağlayıcılar", "", providers],
    ].map(([k, v, s]) => '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + '</div><div class="s">' + s + "</div></div>").join("")
      + '<div class="card" style="grid-column:1/-1"><div class="k">Kuyruktaki iller</div><div class="cities">' + cities + "</div></div>";
  }

  function renderRows(venues) {
    el("rows").innerHTML = venues.map((v) =>
      '<tr data-id="' + esc(v.id) + '"' + (v.id === selected ? ' class="sel"' : "") + ">"
      + "<td><strong>" + esc(v.adi) + "</strong><div class=\\"muted\\">" + esc(v.kategori || "") + (v.degerlendirme_sayisi ? " · " + v.degerlendirme_sayisi + " yorum" : "") + "</div></td>"
      + "<td>" + esc(v.il) + " / " + esc(v.ilce) + "<div class=\\"muted\\">" + esc(v.mahalle || "") + "</div></td>"
      + '<td><span class="tag ' + esc(v.durum) + '">' + (STATUS[v.durum] || v.durum) + "</span>" + (v.hata ? '<div class="muted err" title="' + esc(v.hata) + '">' + esc(v.hata.split("|")[0].slice(0, 60)) + "</div>" : "") + "</td>"
      + "<td>" + esc(v.saglayici || "—") + "</td>"
      + '<td class="num">' + v.fiyat + '</td><td class="num">' + v.gorsel + '</td><td class="num">' + (v.populer_saat ? "✓" : "—") + "</td>"
      + '<td class="muted" title="' + esc(v.guncelleme) + '">' + ago(v.guncelleme) + "</td></tr>"
    ).join("") || '<tr><td colspan="8" class="muted">Henüz işlenmiş mekan yok.</td></tr>';
  }

  // 7 gün × saat ısı haritası; renk yoğunluk yüzdesine göre.
  function renderBusy(b) {
    if (!b || !b.gunler || !Object.keys(b.gunler).length) return "";
    const order = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
    const hours = [...new Set(Object.values(b.gunler).flat().map((h) => h.saat))].sort((a, c) => a - c);
    const cell = (p) => '<td title="%' + p + '" style="padding:2px;text-align:center;font-size:10px;background:rgba(29,78,216,' + (p / 100 * 0.85).toFixed(2) + ');color:' + (p > 55 ? "#fff" : "#333") + '">' + (p || "") + "</td>";
    return '<h3 style="font-size:13px;margin:14px 0 4px">Popüler saatler <span class="muted">(' + Object.keys(b.gunler).length + " gün · " + esc(b.observed_at || "") + ")</span></h3>"
      + '<div class="muted">' + [b.canli_durum, b.bekleme_suresi, b.kalis_suresi ? "kalış: " + b.kalis_suresi : null].filter(Boolean).map(esc).join(" · ") + "</div>"
      + '<div style="overflow:auto"><table style="font-size:11px"><thead><tr><th></th>' + hours.map((h) => "<th style=\"padding:2px;text-align:center\">" + h + "</th>").join("") + "</tr></thead><tbody>"
      + order.filter((d) => b.gunler[d]).map((d) => "<tr><td style=\"padding:2px 6px;white-space:nowrap\">" + d.slice(0, 3) + "</td>" + hours.map((h) => cell((b.gunler[d].find((x) => x.saat === h) || {}).yuzde || 0)).join("") + "</tr>").join("")
      + "</tbody></table></div>";
  }

  async function loadDetail(id) {
    selected = id;
    el("detailNote").textContent = "yükleniyor…";
    try {
      const d = await (await fetch("/venue?id=" + encodeURIComponent(id))).json();
      const m = d.mekan || {};
      const prices = d.fiyatlar || [], images = d.gorseller || [];
      const byProvider = {};
      prices.forEach((p) => { (byProvider[p.fiyat_saglayici] ||= []).push(p); });
      el("detailNote").textContent = prices.length + " fiyat · " + images.length + " görsel";
      el("detailBody").innerHTML =
        "<div><strong>" + esc(m.adi) + "</strong> <span class=\\"tag " + esc(d.durum) + "\\">" + (STATUS[d.durum] || d.durum) + "</span></div>"
        + '<div class="muted">' + esc(m.tam_adres || [m.mahalle, m.ilce, m.il].filter(Boolean).join(", ")) + (m.maps_url ? ' · <a href="' + esc(m.maps_url) + '" target="_blank" rel="noopener">Google Maps</a>' : "") + "</div>"
        + (d.hata ? '<div class="err" style="margin-top:6px">' + esc(d.hata) + "</div>" : "")
        + Object.entries(byProvider).map(([prov, items]) =>
            '<h3 style="font-size:13px;margin:12px 0 4px">' + esc(prov) + ' <span class="muted">(' + items.length + ")</span></h3>"
            + '<table><tbody>' + items.map((p) => "<tr><td>" + esc(p.urun_adi) + (p.kategori && p.kategori !== "Menü" ? ' <span class="muted">' + esc(p.kategori) + "</span>" : "") + '</td><td class="num">' + fmtTL(p.fiyat) + "</td></tr>").join("") + "</tbody></table>"
          ).join("")
        + (prices.length ? "" : '<div class="muted" style="margin-top:8px">Bu mekan için fiyat kaydı yok.</div>')
        + renderBusy(d.populer_saatler)
        + (images.length ? '<div class="imgs">' + images.map((i) => { const local = i.yerel_dosya ? "/img/" + i.yerel_dosya.split("/").map(encodeURIComponent).join("/") : null; const title = esc(i.kategori || "Menü") + (local ? " · " + fmtBytes(i.dosya_boyutu || 0) + " · " + i.genislik + "×" + i.yukseklik : i.indirme_hatasi ? " · indirilemedi: " + esc(i.indirme_hatasi) : " · indiriliyor…"); return '<a href="' + esc(local || i.gorsel_url) + '" target="_blank" rel="noopener" title="' + title + '"' + (local ? "" : ' style="opacity:.6"') + '><img loading="lazy" src="' + esc(local || i.gorsel_url) + '" alt=""></a>'; }).join("") + "</div>" : "");
      document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
    } catch (e) {
      el("detailNote").textContent = "hata: " + e.message;
    }
  }

  el("rows").addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-id]");
    if (tr) loadDetail(tr.dataset.id);
  });

  async function refresh() {
    try {
      const data = await (await fetch("/recent?limit=80")).json();
      renderCards(data);
      renderRows(data.venues || []);
      const sig = JSON.stringify((data.venues || []).slice(0, 3).map((v) => [v.id, v.durum, v.guncelleme]));
      if (sig !== lastVenueSig) {
        lastVenueSig = sig;
        // Seçili mekan yoksa en son biten mekanı otomatik göster
        if (!selected) { const first = (data.venues || []).find((v) => v.durum === "completed") || (data.venues || [])[0]; if (first) loadDetail(first.id); }
        else if ((data.venues || []).some((v) => v.id === selected && v.durum !== "in_progress")) loadDetail(selected);
      }
      const cur = (data.venues || []).find((v) => v.durum === "in_progress");
      el("pulse").textContent = (cur ? "şu an: " + cur.adi + " · " : "") + "son yenileme " + new Date().toLocaleTimeString("tr-TR");
      el("listNote").textContent = (data.venues || []).length + " kayıt";
    } catch (e) {
      el("pulse").textContent = "sunucuya ulaşılamadı: " + e.message;
    }
  }
  refresh();
  setInterval(refresh, 4000);
</script>
</body>
</html>
"""

class RequestHandler(BaseHTTPRequestHandler):
    server_version = "GEOPROPMenu/2.0"

    def _allowed_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        return origin if origin.startswith("chrome-extension://") else ""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Geçersiz istek boyutu")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON nesnesi bekleniyordu")
        return data

    def _origin_ok(self) -> bool:
        return self._allowed_origin() != ""

    def _send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_local_image(self, relative: str) -> None:
        try:
            target = (IMAGE_DIR / relative).resolve()
            target.relative_to(IMAGE_DIR.resolve())  # dizin dışına çıkışı engelle
            data = target.read_bytes()
        except (ValueError, OSError):
            self._send_json(404, {"error": "not_found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/panel"):
            self._send_html(DASHBOARD_HTML)
            return
        if path.startswith("/img/"):
            self._send_local_image(path[len("/img/"):])
            return
        if not self._origin_ok():
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        path = urlparse(self.path).path
        try:
            with connect_menu_db() as conn:
                init_schema(conn)
                if path == "/next":
                    worker = re.sub(r"[^0-9A-Za-z_-]", "", parse_qs(urlparse(self.path).query).get("worker", ["1"])[0])[:16] or "1"
                    venue = lease_next(conn, worker)
                    if venue is None:
                        self._send_json(200, {"status": "done", "queue": queue_counts(conn)})
                    else:
                        self._send_json(200, {"status": "ok", **venue, "queue": queue_counts(conn)})
                    return
                if path == "/recent":
                    query = parse_qs(urlparse(self.path).query)
                    limit = int(query.get("limit", ["60"])[0])
                    self._send_json(200, {
                        "status": "ok", "queue": queue_counts(conn), "cities": queue_counts_by_city(conn),
                        "totals": dashboard_totals(conn), "venues": recent_venues(conn, limit),
                        "server_time": now_iso(),
                    })
                    return
                if path == "/venue":
                    query = parse_qs(urlparse(self.path).query)
                    detail = venue_detail(conn, query.get("id", [""])[0])
                    if detail is None:
                        self._send_json(404, {"error": "not_found"})
                    else:
                        self._send_json(200, {"status": "ok", **detail})
                    return
                if path == "/status":
                    current = conn.execute(
                        "SELECT mekan_id, payload_json, deneme_sayisi, leased_at FROM menu_tarama_kuyrugu WHERE durum='in_progress' LIMIT 1"
                    ).fetchone()
                    current_payload = json.loads(current["payload_json"]) if current else None
                    active_rows = conn.execute(
                        "SELECT isci, payload_json FROM menu_tarama_kuyrugu WHERE durum='in_progress' ORDER BY isci"
                    ).fetchall()
                    self._send_json(200, {
                        "active": [{"isci": r["isci"] or "1", "adi": json.loads(r["payload_json"]).get("adi")} for r in active_rows],
                        "status": "ok", "queue": queue_counts(conn),
                        "cities": queue_counts_by_city(conn),
                        "current": {"id": current["mekan_id"], "adi": current_payload["adi"],
                                    "attempt": current["deneme_sayisi"], "leased_at": current["leased_at"]} if current else None,
                        "rules": {"cities": TARGET_CITIES, "scope": SCOPE,
                                  "priority_tiers": [WEST_METRO_CITIES, FIRST_ROUND_CITIES, "diğer iller"],
                                  "min_reviews": MIN_REVIEWS,
                                  "min_county_population": MIN_COUNTY_POPULATION,
                                  "min_neighborhood_population": MIN_NEIGHBORHOOD_POPULATION,
                                  "villages_excluded": True},
                    })
                    return
            self._send_json(404, {"error": "not_found"})
        except Exception as exc:
            self._send_json(500, {"error": type(exc).__name__, "message": str(exc)[:500]})

    def do_POST(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            with connect_menu_db() as conn:
                init_schema(conn)
                if path == "/save":
                    result = save_result(conn, data)
                    self._send_json(200, result)
                    if result["images"]:
                        start_image_download_thread()
                    return
                if path == "/fail":
                    self._send_json(200, fail_job(conn, data))
                    return
            self._send_json(404, {"error": "not_found"})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": "validation_error", "message": str(exc)[:500]})
        except Exception as exc:
            self._send_json(500, {"error": type(exc).__name__, "message": str(exc)[:500]})

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def log_message(self, message_format: str, *args: Any) -> None:
        print(f"[{now_iso()}] {self.client_address[0]} {message_format % args}")


# v2.3 öncesi iki hata yüzünden "başarısız" sayılan mekanlar bir kez yeniden kuyruğa alınır:
#  - Zaman aşımı: bekçi 30 sn'de doluyordu; popüler saatlerin 7 gün sekmesi bu süreyi aşıyordu.
#  - "Canlı değerlendirme sayısı 10 altında": yorum sayısı, panel yerine yorum yazan kişinin
#    profilindeki "3 yorum" gibi sayılardan okunabiliyordu.
RETRY_MIGRATION_KEY = "v2_3_zaman_asimi_ve_yorum_sayisi_yeniden_kuyruk"


def requeue_known_false_failures(conn: sqlite3.Connection) -> int:
    if conn.execute("SELECT 1 FROM menu_bot_meta WHERE anahtar=?", (RETRY_MIGRATION_KEY,)).fetchone():
        return 0
    now = now_iso()
    with conn:
        count = conn.execute(
            """
            UPDATE menu_tarama_kuyrugu
            SET durum='queued', deneme_sayisi=0, lease_token=NULL, leased_at=NULL, updated_at=?,
                son_hata='Yeniden kuyrukta (v2.3 düzeltmesi); önceki hata: ' || son_hata
            WHERE durum='failed'
              AND (son_hata LIKE 'Zaman aşımı%' OR son_hata LIKE 'Canlı değerlendirme sayısı 10 altında%')
            """,
            (now,),
        ).rowcount
        conn.execute(
            "INSERT INTO menu_bot_meta (anahtar, deger, updated_at) VALUES (?, ?, ?)",
            (RETRY_MIGRATION_KEY, str(count), now),
        )
    return count


def prepare_queue() -> tuple[int, Counter[str]]:
    with connect_menu_db() as conn:
        init_schema(conn)
        requeued = requeue_known_false_failures(conn)
        if requeued:
            print(f"Önceki hatalı 'başarısız' sayılan {requeued} mekan yeniden kuyruğa alındı.")
        existing = conn.execute("SELECT COUNT(*) FROM menu_tarama_kuyrugu").fetchone()[0]
    try:
        venues, reasons = load_eligible_venues()
    except (FileNotFoundError, sqlite3.Error) as error:
        # Kaynaklar (Google ambarı / nüfus tablosu) yoksa ama kuyruk doluysa mevcut kuyrukla devam edilir.
        if not existing:
            raise SystemExit(f"Kuyruk boş ve kaynak okunamadı: {error}")
        print(f"UYARI: kuyruk güncellenemedi ({error}); mevcut {existing} kayıtlık kuyrukla devam ediliyor.")
        with connect_menu_db() as conn:
            report_twins(conn)
        return 0, Counter({"kaynak_okunamadi": 1})
    with connect_menu_db() as conn:
        sync_queue(conn, venues, discover_places_databases())
        report_twins(conn)
    return len(venues), reasons


def report_twins(conn: sqlite3.Connection) -> None:
    twins = mark_duplicate_twins(conn)
    if twins:
        print(f"Tekrar kayıt olarak işaretlenen (aranmayacak) mekan: {twins}")


def run() -> None:
    venue_count, excluded_reasons = prepare_queue()
    print(f"Veri ambarı: {DB_MENU}")
    print(f"Kapsam: {'yalnız restoranlar' if SCOPE == 'restoran' else 'tüm yiyecek-içecek'} (GEOPROP_MENU_SCOPE={SCOPE})")
    print(f"Uygun gerçek Google mekanı: {venue_count}")
    print(f"Kapsam dışı nedenleri: {dict(excluded_reasons)}")
    cities_label = "81 il" if TARGET_CITIES == ALL_CITIES else ", ".join(TARGET_CITIES)
    print(f"Kurallar: {cities_label} | değerlendirme >= {MIN_REVIEWS} | ilçe >= {MIN_COUNTY_POPULATION} | mahalle >= {MIN_NEIGHBORHOOD_POPULATION} | köyler hariç")
    print(f"Sıra: önce {', '.join(WEST_METRO_CITIES)}; sonra {', '.join(FIRST_ROUND_CITIES)}; sonra diğer iller "
          "(her grupta menü bulma oranı yüksek kategoriler önce)")
    with connect_menu_db() as conn:
        print(f"Kuyruk: {queue_counts(conn)}")
    port = int(os.environ.get("GEOPROP_MENU_PORT", "5050"))
    server = ThreadingHTTPServer(("127.0.0.1", port), RequestHandler)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Görsel dizini: {IMAGE_DIR} (menü kartı {MENU_IMAGE_EDGE}px/q{MENU_IMAGE_QUALITY}, yemek {DISH_IMAGE_EDGE}px/q{DISH_IMAGE_QUALITY})")
    start_image_download_thread()  # önceki oturumlardan kalan indirilmemiş görseller
    print(f"GEOPROP Menü Toplayıcı sunucusu: http://127.0.0.1:{port}  (canlı pano: http://127.0.0.1:{port}/)")
    server.serve_forever()


if __name__ == "__main__":
    run()
