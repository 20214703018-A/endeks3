"""Veri erişim katmanı — kanonik DuckDB'yi SALT OKUNUR açar; diske hiçbir şey yazmaz.

Her ontoloji ölçüsü, standart sütunlu bir SELECT'e çevrilir:
    geo_id, seviye, donem, boyut, deger, tur, kopya, celiski, deger_min, deger_max,
    kaynaklar, secilen_kaynak, satir_hash, edinim, turetim
Birim düzeltmesi (olcek_kurallari) ve tekilleştirme (kurallar.tekillestirme) sorgu anında yapılır.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import duckdb

from .ontology import Olcu, Ontoloji, yukle
from .paths import CANONICAL

KESIT_ISLETME = "2026-09"          # Google/OSM/Yemeksepeti işletme gözlem kesiti (poi.last_observed_at 2026-09-18/19)
KESIT_HARITA = "2026-09-13"        # OSM son kesit (poi_lifecycle_osm)
GECERLI_KOORD = "p.coord_validity = 'valid'"

_yerel = threading.local()


def baglanti() -> duckdb.DuckDBPyConnection:
    """İş parçacığı başına bir bellek-içi bağlantı; kanonik READ_ONLY bağlanır."""
    c = getattr(_yerel, "c", None)
    if c is None:
        if not CANONICAL.exists():
            raise FileNotFoundError(f"Kanonik veritabanı bulunamadı: {CANONICAL}")
        c = duckdb.connect(":memory:")
        c.execute("LOAD spatial")
        c.execute(f"SET memory_limit='{os.environ.get('GEOPROP_MEM', '2GB')}'")
        c.execute(f"SET threads={int(os.environ.get('GEOPROP_THREADS', '4'))}")
        # bellek taşması için sistem geçici dizini (veri dizinine hiçbir şey yazılmaz)
        c.execute(f"SET temp_directory='{Path(tempfile.gettempdir()) / 'geoprop_agent_spill'}'")
        c.execute(f"ATTACH '{CANONICAL}' AS k (READ_ONLY)")
        _yerel.c = c
    return c


def meta() -> dict:
    return dict(baglanti().execute("SELECT key, value FROM k.main._meta").fetchall())


def q(sql: str, params: list | None = None) -> list[dict]:
    cur = baglanti().execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def lit(s) -> str:
    """SQL string literali (parametre kullanılamayan CTE gövdeleri için)."""
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


# ---------------------------------------------------------------------------------------------- kapsam
def kapsam_sql(seviye: str, kapsam_geo_id: str | None, kapsam_seviye: str | None) -> str:
    """seviye düzeyindeki, kapsamın içinde kalan geo_id'ler."""
    w = [f"g.level = {lit(seviye)}"]
    if kapsam_geo_id:
        if kapsam_seviye == seviye:
            w.append(f"g.geo_id = {lit(kapsam_geo_id)}")
        elif kapsam_seviye == "il":
            w.append(f"g.il_geo_id = {lit(kapsam_geo_id)}")
        elif kapsam_seviye == "ilce" and seviye == "mahalle":
            w.append(f"g.parent_geo_id = {lit(kapsam_geo_id)}")
        elif kapsam_seviye == "ulke":
            pass
        else:
            raise ValueError(f"'{kapsam_seviye}' kapsamı içinde '{seviye}' seviyesi aranamaz")
    return f"SELECT g.geo_id FROM k.main.geo_entity g WHERE {' AND '.join(w)}"


# ---------------------------------------------------------------------------------------------- ham satırlar


def _carpan_sql(ont: Ontoloji, olcek: str | None) -> str:
    if not olcek:
        return "1"
    kural = ont.olcek_kurallari[olcek]
    parca = " ".join(f"WHEN {lit(t)} THEN {c}" for t, c in sorted(kural.get("tablo_carpani", {}).items()))
    return f"(CASE source_table {parca} ELSE {kural['varsayilan_carpan']} END)" if parca else str(kural["varsayilan_carpan"])


def _fiyat_ham(ont: Ontoloji, o: Olcu, seviye: str) -> str:
    k = o.kaynak
    return f"""SELECT geo_id, level AS seviye, period AS donem, NULL::VARCHAR AS boyut,
        parsed_value * {_carpan_sql(ont, o.olcek)} AS v,
        CASE observation_kind WHEN 'projected' THEN 'projeksiyon' ELSE 'olcum' END AS tur,
        source_table AS kaynak,
        coalesce(source_updated_at, '') || '|' || source_table || '|' || source_row_hash AS sirakey,
        source_row_hash AS satir_hash, acquisition_class AS edinim, NULL::VARCHAR AS turetim
      FROM k.main.price_observation
      WHERE category = {lit(k['category'])} AND metric = {lit(k['metric'])} AND level = {lit(seviye)}
        AND parsed_value IS NOT NULL"""


def _nufus_ham(seviye: str) -> str:
    base = """SELECT p.geo_id, p.level AS seviye, p.period AS donem, NULL::VARCHAR AS boyut, p.parsed_value AS v,
        'olcum' AS tur, p.acquisition_class || ':' || p.staging_view AS kaynak,
        lpad(p.source_priority::VARCHAR, 4, '0') || '|' || p.source_row_hash AS sirakey,
        p.source_row_hash AS satir_hash, p.acquisition_class AS edinim, NULL::VARCHAR AS turetim
      FROM k.main.population_observation p WHERE p.metric = 'population' AND p.parsed_value IS NOT NULL"""
    if seviye in ("mahalle", "ilce"):
        return base + f" AND p.level = {lit(seviye)}"
    grup = "g.il_geo_id" if seviye == "il" else "'GEO_TR'"
    return f"""SELECT {grup} AS geo_id, {lit(seviye)} AS seviye, p.period AS donem, NULL::VARCHAR AS boyut,
        sum(p.parsed_value) AS v, 'olcum' AS tur, 'official_public:TÜİK ilçe toplamı' AS kaynak,
        '' AS sirakey, NULL AS satir_hash, 'official_public' AS edinim,
        'TÜİK ilçe nüfuslarının toplamı (' || count(*) || ' ilçe)' AS turetim
      FROM k.main.population_observation p JOIN k.main.geo_entity g USING (geo_id)
      WHERE p.level = 'ilce' AND p.metric = 'population' AND p.parsed_value IS NOT NULL
      GROUP BY 1, 3"""


def _donem_norm(col: str) -> str:
    # '2024-9' → '2024-09' (BDDK dönemleri iki biçimde yazılmış)
    return f"CASE WHEN regexp_matches({col}, '^[0-9]{{4}}-[0-9]$') THEN substr({col}, 1, 5) || '0' || substr({col}, 6) ELSE {col} END"


def _gosterge_ham(o: Olcu, seviye: str) -> str:
    k = o.kaynak
    w = [f"domain = {lit(k['domain'])}", f"metric = {lit(k['metric'])}", "parsed_value IS NOT NULL"]
    if "series_version" in k:
        w.append(f"series_version = {lit(k['series_version'])}")
    if "dim1" in k:
        w.append(f"dim1 = {lit(k['dim1'])}")
    boyut = {"bkm_sektor": "dim1", "bina_turu": "dim2"}.get(o.boyut or "", "NULL")
    sel = f"""SELECT geo_id, level AS seviye, {_donem_norm('period')} AS donem, {boyut}::VARCHAR AS boyut, parsed_value AS v,
        'olcum' AS tur, series_version AS kaynak, coalesce(recorded_at, '') || '|' || source_row_hash AS sirakey,
        source_row_hash AS satir_hash, acquisition_class AS edinim, NULL::VARCHAR AS turetim
      FROM k.main.indicator_observation WHERE {' AND '.join(w)}"""
    mevcut = {"konut_satis_adedi"}  # ilçe toplamından türetilen üst seviyeler
    if o.id in mevcut and seviye in ("il", "ulke"):
        grup = "g.il_geo_id" if seviye == "il" else "'GEO_TR'"
        return f"""SELECT {grup} AS geo_id, {lit(seviye)} AS seviye, x.donem, x.boyut, sum(x.v) AS v, 'olcum' AS tur,
            'ilçe toplamı:' || any_value(x.kaynak) AS kaynak, '' AS sirakey, NULL AS satir_hash, any_value(x.edinim) AS edinim,
            'ilçe değerlerinin toplamı (' || count(*) || ' ilçe)' AS turetim
          FROM ({sel} AND level = 'ilce') x JOIN k.main.geo_entity g USING (geo_id) GROUP BY 1, 3, 4"""
    return sel + f" AND level = {lit(seviye)}"


def _seviye_grup(seviye: str, mahalle_col: str, ilce_col: str) -> tuple[str, str]:
    """(grup ifadesi, ek JOIN) — işletme/harita nesnesini seviyeye toplar."""
    if seviye == "mahalle":
        return mahalle_col, ""
    if seviye == "ilce":
        return ilce_col, ""
    if seviye == "il":
        return "gi.il_geo_id", f" JOIN k.main.geo_entity gi ON gi.geo_id = {ilce_col}"
    return "'GEO_TR'", ""


def _boyut_filtre(boyut: str | None) -> tuple[str, str]:
    if not boyut:
        return "TRUE", "NULL"
    tur, _, deger = boyut.partition(":")
    if tur == "sektor":
        return f"p.predicted_sector = {lit(deger)}", lit(boyut)
    if tur == "kategori":
        return f"p.predicted_category = {lit(deger)}", lit(boyut)
    raise ValueError(f"İşletme boyutu 'sektor:<ad>' veya 'kategori:<ad>' olmalı, gelen: {boyut}")


def _sifir_doldur(ic: str, seviye: str, donem: str, boyut_sql: str, kaynak: str, edinim: str, ne: str, sifir: bool) -> str:
    """Sayımlarda kayıt olmayan birim = 0 (yok ≠ bilinmiyor). Ortalama gibi ölçülerde doldurulmaz (sifir=False)."""
    kat = "LEFT JOIN" if sifir else "JOIN"
    return f"""SELECT g.geo_id, {lit(seviye)} AS seviye, {lit(donem)} AS donem, {boyut_sql}::VARCHAR AS boyut,
        {'coalesce(c.v, 0.0)' if sifir else 'c.v'} AS v, 'turetilmis' AS tur, {lit(kaynak)} AS kaynak, '' AS sirakey, NULL AS satir_hash,
        {lit(edinim)} AS edinim, {lit(ne)} || ' (n=' || coalesce(c.n, 0) || ')' AS turetim
      FROM k.main.geo_entity g {kat} ({ic}) c ON c.geo_id = g.geo_id WHERE g.level = {lit(seviye)}"""


def _isletme_ham(oid: str, seviye: str, boyut: str | None) -> str:
    # mahalle düzeyinde yalnız poligon-içi atamalar (adres metninden atamalar güveni 0,33 → dışarıda)
    grup, join = _seviye_grup(seviye, "p.assigned_geo_id", "p.assigned_ilce_geo_id")
    bf, bsql = _boyut_filtre(boyut)
    w = [GECERLI_KOORD, bf, f"{grup} IS NOT NULL"]
    if seviye == "mahalle":
        w.append("p.assignment_method LIKE 'ST_Contains%'")
    if oid == "turu_belirsiz_isletme":
        w.append("p.predicted_category IS NULL")
    deger = "avg(p.rating)" if oid == "ortalama_puan" else "count(*)::DOUBLE"
    if oid == "ortalama_puan":
        w.append("p.rating IS NOT NULL")
    ne = {"isletme_sayisi": "geçerli koordinatlı işletme sayımı", "turu_belirsiz_isletme": "kategorisi boş işletme sayımı",
          "ortalama_puan": "Google puanı ortalaması"}[oid]
    ic = f"""SELECT {grup} AS geo_id, {deger} AS v, count(*) AS n
      FROM k.main.poi p{join} WHERE {' AND '.join(w)} GROUP BY 1"""
    return _sifir_doldur(ic, seviye, KESIT_ISLETME, bsql, "k.poi", "web_research+osm", ne, oid != "ortalama_puan")


def _harita_sayi_ham(seviye: str, boyut: str | None) -> str:
    grup, join = _seviye_grup(seviye, "p.assigned_geo_id", "p.assigned_ilce_geo_id")
    w = ["p.map_status = 'aktif'", f"{grup} IS NOT NULL"]
    if boyut:
        w.append(f"p.osm_category = {lit(boyut)}")
    ic = f"""SELECT {grup} AS geo_id, count(*)::DOUBLE AS v, count(*) AS n
      FROM k.main.poi_lifecycle_osm p{join} WHERE {' AND '.join(w)} GROUP BY 1"""
    return _sifir_doldur(ic, seviye, KESIT_HARITA, lit(boyut), "k.poi_lifecycle_osm", "osm_odbl", "OSM aktif nesne sayımı", True)


def _hareket_ham(oid: str, seviye: str, boyut: str | None) -> str:
    grup = {"mahalle": "t.geo_id", "ilce": "g.parent_geo_id", "il": "g.il_geo_id", "ulke": "'GEO_TR'"}[seviye]
    ifade = {
        "harita_eklenen": "sum(t.added_to_map)", "harita_cikan": "sum(t.removed_from_map)",
        "harita_mevcut": "sum(t.present_on_map)",
        # mahalle tablosundaki formülle aynı: (eklenen+çıkan)/(önceki mevcut+eklenen); önceki = mevcut − net değişim
        "harita_devir_hizi": "100.0 * (sum(t.added_to_map) + sum(t.removed_from_map)) / nullif(sum(t.present_on_map) - sum(t.net_change) + sum(t.added_to_map), 0)",
    }[oid]
    return f"""SELECT {grup} AS geo_id, {lit(seviye)} AS seviye, t.snapshot_date AS donem, t.category_group AS boyut,
        {ifade}::DOUBLE AS v, 'turetilmis' AS tur, 'k.analytics.mahalle_turnover_osm' AS kaynak, '' AS sirakey,
        NULL AS satir_hash, 'osm_odbl' AS edinim,
        'OSM harita varlığı (açılış/kapanış değildir); önceki kesit ' || any_value(t.prev_date) AS turetim
      FROM k.analytics.mahalle_turnover_osm t JOIN k.main.geo_entity g ON g.geo_id = t.geo_id
      WHERE t.category_group = {lit(boyut or 'all')} AND t.prev_date IS NOT NULL
      GROUP BY 1, 3, 4"""


_ALAN_HAZIR: set[tuple[int, str]] = set()


def _alan_tablosu(seviye: str) -> str:
    """Poligon alanlarını bu oturumun BELLEK-İÇİ geçici tablosunda bir kez hesaplar (kanonik dosyaya yazılmaz)."""
    c = baglanti()
    ad = f"_alan_{seviye}"
    if (id(c), seviye) not in _ALAN_HAZIR:
        # eksen sırası doğrulandı: ST_FlipCoordinates ile İstanbul 5.447 km², Kadıköy 25,3 km²
        c.execute(f"""CREATE OR REPLACE TEMP TABLE {ad} AS SELECT geo_id, level AS seviye,
            ST_Area_Spheroid(ST_FlipCoordinates(geometry)) / 1e6 AS v, coalesce(geometry_source, '?') AS kaynak
            FROM k.main.geo_entity WHERE level = {lit(seviye)} AND geometry IS NOT NULL""")
        _ALAN_HAZIR.add((id(c), seviye))
    return ad


def _alan_ham(seviye: str) -> str:
    t = _alan_tablosu(seviye)
    return f"""SELECT geo_id, seviye, 'poligon' AS donem, NULL::VARCHAR AS boyut, v, 'turetilmis' AS tur,
        'k.geo_entity.geometry:' || kaynak AS kaynak, '' AS sirakey, NULL AS satir_hash,
        'official_public|web_research' AS edinim, 'poligon alanı (elipsoid üzerinde)' AS turetim
      FROM temp.main.{t}"""


def _nufus_kesit_sql(seviye: str) -> str:
    """Oran hesapları için tek nüfus: mahalle TÜİK 2025, yoksa web 2024 (bayraklı); ilçe/il TÜİK 2025."""
    if seviye == "mahalle":
        return """SELECT geo_id, arg_max(parsed_value, period) AS nufus, arg_max(period || ':' || acquisition_class, period) AS nufus_kaynagi
          FROM k.main.population_observation WHERE level = 'mahalle' AND metric = 'population' AND period IN ('2024', '2025')
          GROUP BY 1"""
    if seviye == "ilce":
        return """SELECT geo_id, parsed_value AS nufus, '2025:official_public' AS nufus_kaynagi FROM k.main.population_observation
          WHERE level = 'ilce' AND metric = 'population' AND period = '2025'"""
    grup = "g.il_geo_id" if seviye == "il" else "'GEO_TR'"
    return f"""SELECT {grup} AS geo_id, sum(p.parsed_value) AS nufus, '2025:official_public (ilçe toplamı)' AS nufus_kaynagi
      FROM k.main.population_observation p JOIN k.main.geo_entity g USING (geo_id)
      WHERE p.level = 'ilce' AND p.metric = 'population' AND p.period = '2025' GROUP BY 1"""


def _oran_ham(oid: str, seviye: str, boyut: str | None) -> str:
    if oid == "nufus_yogunlugu":
        return f"""SELECT a.geo_id, a.seviye, 'kesit' AS donem, NULL::VARCHAR AS boyut, n.nufus / nullif(a.v, 0) AS v,
            'turetilmis' AS tur, 'nüfus/alan' AS kaynak, '' AS sirakey, NULL AS satir_hash, 'turetilmis' AS edinim,
            'nüfus (' || n.nufus_kaynagi || ') / poligon alanı' AS turetim
          FROM ({_alan_ham(seviye)}) a JOIN ({_nufus_kesit_sql(seviye)}) n USING (geo_id)"""
    if oid == "bin_kisi_basina_isletme":
        return f"""SELECT i.geo_id, i.seviye, i.donem, i.boyut, 1000.0 * i.v / nullif(n.nufus, 0) AS v,
            'turetilmis' AS tur, 'işletme/nüfus' AS kaynak, '' AS sirakey, NULL AS satir_hash, 'turetilmis' AS edinim,
            'işletme sayısı / nüfus (' || n.nufus_kaynagi || ') × 1000' AS turetim
          FROM ({_isletme_ham('isletme_sayisi', seviye, boyut)}) i JOIN ({_nufus_kesit_sql(seviye)}) n USING (geo_id)"""
    if oid == "km2_basina_isletme":
        return f"""SELECT i.geo_id, i.seviye, i.donem, i.boyut, i.v / nullif(a.v, 0) AS v,
            'turetilmis' AS tur, 'işletme/alan' AS kaynak, '' AS sirakey, NULL AS satir_hash, 'turetilmis' AS edinim,
            'işletme sayısı / poligon alanı' AS turetim
          FROM ({_isletme_ham('isletme_sayisi', seviye, boyut)}) i JOIN ({_alan_ham(seviye)}) a USING (geo_id)"""
    if oid == "nufus_degisim_5y":
        grup = {"ilce": "p.geo_id", "il": "g.il_geo_id"}[seviye]
        return f"""WITH s AS (SELECT max(period) AS son FROM k.main.population_observation WHERE level = 'ilce' AND metric = 'population'),
            t AS (SELECT {grup} AS geo_id,
                    sum(p.parsed_value) FILTER (WHERE p.period = s.son) AS y1,
                    sum(p.parsed_value) FILTER (WHERE p.period = (s.son::INT - 5)::VARCHAR) AS y0,
                    any_value(s.son) AS son
                  FROM k.main.population_observation p JOIN k.main.geo_entity g USING (geo_id), s
                  WHERE p.level = 'ilce' AND p.metric = 'population' GROUP BY 1)
          SELECT geo_id, {lit(seviye)} AS seviye, (son::INT - 5)::VARCHAR || '→' || son AS donem, NULL::VARCHAR AS boyut,
            100.0 * (y1 / y0 - 1) AS v, 'turetilmis' AS tur, 'TÜİK ADNKS ilçe' AS kaynak, '' AS sirakey, NULL AS satir_hash,
            'official_public' AS edinim, 'TÜİK ilçe nüfusu, 5 yıllık değişim' AS turetim
          FROM t WHERE y0 > 0 AND y1 IS NOT NULL"""
    if oid == "konut_satis_12ay":
        aylik = yukle().olcu("konut_satis_adedi")
        return f"""WITH a AS ({olcum_sql(aylik, seviye)}),
            s AS (SELECT max(donem) AS son FROM a),
            b AS (SELECT a.geo_id, a.deger, a.donem, s.son FROM a, s
                  WHERE a.donem > strftime(strptime(s.son || '-01', '%Y-%m-%d') - INTERVAL 12 MONTH, '%Y-%m'))
          SELECT geo_id, {lit(seviye)} AS seviye, min(donem) || '→' || max(donem) AS donem, NULL::VARCHAR AS boyut,
            sum(deger) AS v, 'turetilmis' AS tur, 'tuik_medas_2025_revision' AS kaynak, '' AS sirakey, NULL AS satir_hash,
            'official_public' AS edinim, 'son 12 ay toplamı (' || count(*) || ' ay)' AS turetim
          FROM b GROUP BY geo_id HAVING count(*) = 12"""
    raise KeyError(oid)


def ham_sql(o: Olcu, seviye: str, boyut: str | None = None) -> str:
    ont = yukle()
    if seviye not in o.seviyeler:
        raise ValueError(f"'{o.ad}' ölçüsü '{seviye}' seviyesinde yok (var olan: {', '.join(o.seviyeler)})")
    if o.kaynak.get("tablo") == "price_observation":
        return _fiyat_ham(ont, o, seviye)
    if o.id == "nufus":
        return _nufus_ham(seviye)
    if "domain" in o.kaynak:
        return _gosterge_ham(o, seviye)
    if o.id in ("isletme_sayisi", "turu_belirsiz_isletme", "ortalama_puan"):
        return _isletme_ham(o.id, seviye, boyut)
    if o.id == "harita_nesnesi_sayisi":
        return _harita_sayi_ham(seviye, boyut)
    if o.id.startswith("harita_"):
        return _hareket_ham(o.id, seviye, boyut)
    if o.id == "alan_km2":
        return _alan_ham(seviye)
    return _oran_ham(o.id, seviye, boyut)


def fiyat_toplu(geo_ids: list[str]) -> list[dict]:
    """Profil için: verilen yerlerde katalogdaki TÜM fiyat ölçülerinin en güncel ölçülmüş değeri (tek tarama)."""
    ont = yukle()
    esl = [(o.kaynak["category"], o.kaynak["metric"], o.id, o.olcek or "")
           for o in ont.olculer.values() if o.kaynak.get("tablo") == "price_observation"]
    values = ", ".join(f"({lit(a)}, {lit(b)}, {lit(c)}, {lit(d)})" for a, b, c, d in esl)
    carpan = ["CASE m.olcek"]
    for ad, kural in sorted(ont.olcek_kurallari.items()):
        ic = " ".join(f"WHEN {lit(t)} THEN {c}" for t, c in sorted(kural.get("tablo_carpani", {}).items()))
        carpan.append(f"WHEN {lit(ad)} THEN " + (f"(CASE p.source_table {ic} ELSE {kural['varsayilan_carpan']} END)" if ic else str(kural["varsayilan_carpan"])))
    carpan.append("ELSE 1 END")
    ids = ", ".join(lit(g) for g in geo_ids)
    return q(f"""WITH m(category, metric, olcu_id, olcek) AS (VALUES {values}),
      h AS (SELECT p.geo_id, p.level AS seviye, p.period AS donem, m.olcu_id, p.parsed_value * ({' '.join(carpan)}) AS v,
              p.source_table AS kaynak, coalesce(p.source_updated_at, '') || '|' || p.source_table || '|' || p.source_row_hash AS sirakey,
              p.source_row_hash AS satir_hash, p.acquisition_class AS edinim
            FROM k.main.price_observation p JOIN m USING (category, metric)
            WHERE p.geo_id IN ({ids}) AND p.observation_kind = 'measured' AND p.parsed_value IS NOT NULL),
      d AS (SELECT geo_id, any_value(seviye) AS seviye, olcu_id, donem, arg_max(v, sirakey) AS deger, count(*) AS kopya,
              (max(v) - min(v)) > 1e-9 + 1e-6 * max(abs(v)) AS celiski, min(v) AS deger_min, max(v) AS deger_max,
              array_to_string(list_sort(list(DISTINCT kaynak)), ',') AS kaynaklar, arg_max(kaynak, sirakey) AS secilen_kaynak,
              arg_max(satir_hash, sirakey) AS satir_hash, min(edinim) AS edinim
            FROM h GROUP BY geo_id, olcu_id, donem)
      SELECT * FROM d QUALIFY row_number() OVER (PARTITION BY geo_id, olcu_id ORDER BY donem DESC) = 1
      ORDER BY geo_id, olcu_id""")


def olcum_sql(o: Olcu, seviye: str, boyut: str | None = None, geo_filtre_sql: str | None = None,
              projeksiyon: bool = False) -> str:
    """Tekilleştirilmiş standart gözlem SELECT'i."""
    w = []
    if geo_filtre_sql:
        w.append(f"geo_id IN ({geo_filtre_sql})")
    if not projeksiyon:
        w.append("tur <> 'projeksiyon'")
    # işletme/harita boyutları ham sorguda süzülür; göstergelerde (bkm sektör, bina türü) burada
    if boyut and not o.turetilmis:
        w.append(f"boyut = {lit(boyut)}")
    where = ("WHERE " + " AND ".join(w)) if w else ""
    return f"""SELECT geo_id, any_value(seviye) AS seviye, donem, boyut, tur,
        arg_max(v, sirakey) AS deger, count(*) AS kopya,
        (max(v) - min(v)) > 1e-9 + 1e-6 * max(abs(v)) AS celiski, min(v) AS deger_min, max(v) AS deger_max,
        array_to_string(list_sort(list(DISTINCT kaynak)), ',') AS kaynaklar, arg_max(kaynak, sirakey) AS secilen_kaynak,
        arg_max(satir_hash, sirakey) AS satir_hash, min(edinim) AS edinim, any_value(turetim) AS turetim
      FROM ({ham_sql(o, seviye, boyut)}) h {where}
      GROUP BY geo_id, donem, boyut, tur"""
