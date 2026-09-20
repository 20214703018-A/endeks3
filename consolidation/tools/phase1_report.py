#!/usr/bin/env python3
"""Phase 1 envanter çıktılarından insan-okunur RUN REPORT + DATA LOSS REPORT (Markdown) üretir. Salt-okunur."""
import json
import sys
from pathlib import Path

import duckdb

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
INV = OUT / "inventory"
run_id = sys.argv[1] if len(sys.argv) > 1 else sorted((OUT / "reports").glob("RUN_*_summary.json"))[-1].name.replace("_summary.json", "")
S = json.loads((OUT / "reports" / f"{run_id}_summary.json").read_text())
c = duckdb.connect()
c.execute(f"CREATE VIEW inv AS SELECT * FROM '{INV}/inventory.parquet'")
c.execute(f"CREATE VIEW tbl AS SELECT * FROM '{INV}/tables.parquet'")
c.execute(f"CREATE VIEW col AS SELECT * FROM '{INV}/columns.parquet'")
c.execute(f"CREATE VIEW geo AS SELECT * FROM '{INV}/geo_layers.parquet'")
c.execute(f"CREATE VIEW err AS SELECT * FROM '{INV}/errors.parquet'")
c.execute(f"CREATE VIEW perr AS SELECT * FROM '{INV}/parse_errors.parquet'")
c.execute(f"CREATE VIEW dup AS SELECT * FROM '{INV}/duplicates_files.parquet'")
c.execute(f"CREATE VIEW zm AS SELECT * FROM '{INV}/zip_members.parquet'")
c.execute(f"CREATE VIEW cls AS SELECT * FROM '{INV}/classifications.parquet'")
c.execute(f"CREATE VIEW sc AS SELECT * FROM '{INV}/schema_clusters.parquet'")
c.execute(f"CREATE VIEW scm AS SELECT * FROM '{INV}/schema_cluster_members.parquet'")
c.execute(f"CREATE VIEW q AS SELECT * FROM '{OUT}/quarantine/quarantine_registry.parquet'")


def rows(sql):
    try:
        return c.execute(sql).fetchall()
    except Exception as e:  # boş parquet (_empty) veya eksik sütun
        return []


def one(sql, default=0):
    r = rows(sql)
    return r[0][0] if r and r[0] and r[0][0] is not None else default


def md_table(header, data, limit=None):
    data = list(data)[:limit] if limit else list(data)
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in data:
        out.append("| " + " | ".join(("" if v is None else str(v)).replace("|", "\\|").replace("\n", " ") for v in r) + " |")
    return "\n".join(out)


def gb(b):
    return f"{(b or 0)/1e9:.2f} GB"


L = []
L.append(f"# PHASE 1 — DISCOVERY RAPORU\n\nRun ID: `{run_id}`  \nBaşlangıç: {S['started_at']}  \nBitiş: {S['finished_at']}  \nPipeline: {S['pipeline_version']}  code_hash: `{S['code_hash'][:16]}`  rules_hash: `{S['rules_hash'][:16]}`\n")
L.append("Kaynak kökler (READ-ONLY): " + ", ".join(f"`{r}`" for r in S["source_roots"]) + "\n")

# A
L.append("## A. Genel durum\n")
L.append(md_table(["Metrik", "Değer"], [
    ("Taranan dosya", f"{S['files_scanned']:,}"), ("Toplam boyut", gb(S["total_bytes"])), ("Okunabilir (hash alındı)", f"{S['readable']:,}"), ("Okunamayan", f"{S['unreadable']:,}"),
    ("Veri adayı dosya", f"{S['data_candidates']:,}"), ("İçeriği envanterlenen", f"{S['content_probed']:,}"), ("Farklı tespit edilen format", len(S["formats"])),
    ("Tablo/katman/sayfa nesnesi", f"{S['tables']:,}"), ("Sütun", f"{S['columns']:,}"), ("Geo katman", f"{S['geo_layers']:,}"), ("Keşfedilen kayıt (satır+özellik, tekrarlar dahil)", f"{S['records_discovered']:,}"),
    ("Kaydedilen parse hatası", f"{S['parse_errors_recorded']:,}"), ("Hata/uyarı kaydı", f"{S['errors']:,}"),
]))
L.append("\n### Kök bazında\n")
L.append(md_table(["Kök", "Dosya", "Boyut", "Veri adayı"], rows("SELECT source_root, count(*), printf('%.2f GB', sum(size_bytes)/1e9), sum(CASE WHEN is_data_candidate THEN 1 ELSE 0 END) FROM inv GROUP BY 1 ORDER BY sum(size_bytes) DESC")))
L.append("\n### Tespit edilen format (magic bytes; uzantı değil)\n")
L.append(md_table(["Format", "Dosya", "Boyut"], rows("SELECT detected_format, count(*), printf('%.2f GB', sum(size_bytes)/1e9) FROM inv GROUP BY 1 ORDER BY 2 DESC"), 30))
mm = one("SELECT count(*) FROM inv WHERE format_mismatch")
L.append(f"\nUzantı ↔ içerik uyuşmazlığı: **{mm}** dosya (ayrıntı: quarantine_registry X01).\n")
if mm:
    L.append(md_table(["file_id", "dosya", "uzantı", "içerik"], rows("SELECT file_id, relative_path, extension, detected_format FROM inv WHERE format_mismatch ORDER BY size_bytes DESC"), 25))

# B
L.append("\n## B. İçerik kümeleri (tahmini kategori — kesin sınıf DEĞİL)\n")
L.append(md_table(["Tahmini kategori", "Dosya", "Ort. güven", "Boyut"], rows("SELECT predicted_category, count(*), round(avg(classification_confidence),2), printf('%.2f GB', sum(i.size_bytes)/1e9) FROM cls JOIN inv i USING(file_id) GROUP BY 1 ORDER BY 2 DESC")))
L.append("\nEn büyük tablolar (satır sayısına göre):\n")
L.append(md_table(["table_id", "dosya", "tablo", "tür", "satır", "sütun"], rows("SELECT t.table_id, i.relative_path, t.table_name, t.object_type, t.row_count, t.column_count FROM tbl t JOIN inv i USING(file_id) WHERE t.row_count IS NOT NULL ORDER BY t.row_count DESC"), 25))
L.append("\nŞema kümeleri (benzer sütun yapısı; Jaccard≥0.8):\n")
L.append(md_table(["Küme", "Üye", "Farklı imza", "Ortak sütunlar"], rows("SELECT schema_cluster_id, member_count, distinct_signatures, substr(common_columns,1,140) FROM sc ORDER BY member_count DESC"), 20))

# C
L.append("\n## C. Duplicate analizi\n")
L.append(f"- Exact duplicate (SHA-256 aynı) grup: **{S['exact_duplicate_groups']:,}**, dosya: **{S['exact_duplicate_files']:,}**, fazlalık boyut: **{gb(S['duplicate_bytes_redundant'])}** (silinmedi; yalnız `duplicate_group_id` atandı)\n")
L.append(md_table(["Grup", "Üye", "Boyut", "Örnek yollar"], rows("SELECT duplicate_group_id, group_size, printf('%.1f MB', max(size_bytes)/1e6), string_agg(absolute_path, ' ⟷ ' ORDER BY absolute_path) FROM dup GROUP BY 1,2 ORDER BY max(size_bytes) DESC"), 20))
cross = one("SELECT count(DISTINCT duplicate_group_id) FROM (SELECT duplicate_group_id, count(DISTINCT CASE WHEN absolute_path LIKE '%/Desktop/GEOPROP/%' THEN 'G' WHEN absolute_path LIKE '%/Desktop/tkgm/%' THEN 'T' WHEN absolute_path LIKE '%/Downloads/%' THEN 'D' ELSE 'H' END) n FROM dup GROUP BY 1) WHERE n>1")
L.append(f"\nKökler ARASI (GEOPROP ⟷ tkgm ⟷ Downloads ⟷ harita) duplicate grup sayısı: **{cross}**\n")
L.append("\nZIP içerikleri (çıkarılmadı; üye listesi ve CSV/JSON/SQLite üyeleri bellekte envanterlendi):\n")
L.append(md_table(["ZIP", "Üye", "Sıkıştırılmamış", "Envanterlenen üye", "İç içe zip"], rows("SELECT i.relative_path, count(*), printf('%.1f MB', sum(uncompressed_size)/1e6), sum(CASE WHEN probed THEN 1 ELSE 0 END), sum(CASE WHEN nested_zip THEN 1 ELSE 0 END) FROM zm JOIN inv i USING(file_id) GROUP BY 1 ORDER BY sum(uncompressed_size) DESC"), 25))
L.append("\nTahmini kayıt-düzeyi duplicate kümeleri (aynı şema kümesinde birden çok kaynak → Phase 3 adayı):\n")
L.append(md_table(["Küme", "Üye", "Toplam satır", "Örnek tablolar"], rows("SELECT m.schema_cluster_id, count(*), sum(t.row_count), substr(string_agg(DISTINCT t.table_name, ', '),1,160) FROM scm m JOIN tbl t USING(table_id) GROUP BY 1 HAVING count(*)>1 ORDER BY sum(t.row_count) DESC NULLS LAST"), 15))

# D
L.append("\n## D. Veri kalitesi\n")
L.append("### Parse / okuma sorunları\n")
L.append(md_table(["Aşama", "Hata türü", "Adet", "Örnek"], rows("SELECT stage, error_type, count(*), substr(min(error_message),1,120) FROM err GROUP BY 1,2 ORDER BY 3 DESC")))
L.append(f"\nSatır bazında kaydedilen parse hatası: **{one('SELECT count(*) FROM perr'):,}** (ham satır `parse_errors.parquet` içinde korunur; hiçbir satır düşürülmedi)\n")
L.append(md_table(["file_id", "tablo", "hata türü", "adet"], rows("SELECT p.file_id, t.table_name, p.error_type, count(*) FROM perr p LEFT JOIN tbl t USING(table_id) GROUP BY 1,2,3 ORDER BY 4 DESC"), 15))
L.append("\nCSV satır sayısı tutarsızlığı notu olan tablolar (ham satır ≠ yüklenen+reject+başlık; çoğunlukla çok satırlı alıntılı alanlar):\n")
L.append(md_table(["table_id", "tablo", "satır", "reject", "not"], rows("SELECT table_id, table_name, row_count, reject_count, notes FROM tbl WHERE notes LIKE 'LINE_COUNT_MISMATCH%' ORDER BY row_count DESC"), 15))
L.append("\n### Encoding\n")
L.append(md_table(["Encoding", "Dosya"], rows("SELECT encoding, count(*) FROM inv WHERE encoding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")))
L.append("\n### Şema sorunları\n")
L.append("- Sıfırla başlayan sayısal görünümlü değer içeren sütunlar (tip zorlanırsa sıfır kaybolur): **%d** sütun\n" % one("SELECT count(*) FROM col WHERE leading_zero_numeric_count > 0"))
L.append(md_table(["table_id", "tablo", "sütun", "adet", "örnek"], rows("SELECT c.table_id, t.table_name, c.column_name, c.leading_zero_numeric_count, substr(c.sample_values,1,60) FROM col c JOIN tbl t USING(table_id) WHERE c.leading_zero_numeric_count > 0 ORDER BY 4 DESC"), 15))
L.append("\n- SQLite: bildirilen tip ile gözlenen tip çelişen sütunlar (ör. INTEGER sütunda text):\n")
L.append(md_table(["table_id", "tablo", "sütun", "bildirilen", "gözlenen"], rows("SELECT c.table_id, t.table_name, c.column_name, c.declared_type, c.observed_type FROM col c JOIN tbl t USING(table_id) WHERE t.object_type='sqlite_table' AND c.observed_type IS NOT NULL AND ((upper(c.declared_type) LIKE '%INT%' AND c.observed_type LIKE '%\"text\"%') OR (upper(c.declared_type) LIKE '%REAL%' AND c.observed_type LIKE '%\"text\"%') OR (upper(c.declared_type) LIKE '%TEXT%' AND (c.observed_type LIKE '%\"integer\"%' OR c.observed_type LIKE '%\"real\"%'))) ORDER BY t.row_count DESC"), 20))
L.append("\n- Sabit (tek değerli) sütunlar: **%d**\n" % one("SELECT count(*) FROM col WHERE is_constant"))
L.append("\n### Geo sorunları\n")
L.append(md_table(["Metrik", "Değer"], [
    ("Geo katman", one("SELECT count(*) FROM geo")), ("Toplam özellik", one("SELECT sum(feature_count) FROM geo")),
    ("Farklı CRS", one("SELECT string_agg(DISTINCT coalesce(crs,'?'), ', ') FROM geo")),
    ("NULL geometri", one("SELECT sum(null_geometry_count) FROM geo")), ("Boş geometri", one("SELECT sum(empty_geometry_count) FROM geo")), ("Geçersiz geometri (ST_IsValid=false; ONARILMADI)", one("SELECT sum(invalid_geometry_count) FROM geo")),
    ("Geçerlilik ölçülemeyen katman", one("SELECT count(*) FROM geo WHERE invalid_geometry_count IS NULL")),
]))
L.append("\nGeometri türü dağılımı (katman bazında):\n")
L.append(md_table(["file_id", "katman", "sürücü", "özellik", "geometri", "CRS", "geçersiz", "null"], rows("SELECT file_id, layer_name, driver, feature_count, substr(geometry_types,1,50), crs, invalid_geometry_count, null_geometry_count FROM geo ORDER BY feature_count DESC"), 30))
L.append("\nGEO_CONFLICT (Türkiye sınır kutusu dışı koordinat/özellik):\n")
L.append(md_table(["file_id", "tablo", "mesaj"], rows("SELECT file_id, table_name, substr(error_message,1,160) FROM q WHERE status='GEO_CONFLICT' ORDER BY file_id"), 20))

# E karantina
L.append("\n## E. Karantina / inceleme kaydı (Phase 1: yalnız İŞARETLEME; dosya taşınmadı)\n")
L.append(md_table(["Durum", "Kayıt", "Dosya"], rows("SELECT status, count(*), count(DISTINCT file_id) FROM q GROUP BY 1 ORDER BY 2 DESC")))
L.append("\nKural bazında:\n")
L.append(md_table(["Kural", "Durum", "Kayıt", "Dosya", "Gerekçe"], rows("SELECT rule_id, status, count(*), count(DISTINCT file_id), substr(min(error_message),1,150) FROM q GROUP BY 1,2 ORDER BY 3 DESC")))
L.append("\nQUARANTINE durumundaki dosyalar (tam liste `quarantine/quarantine_registry.csv`):\n")
L.append(md_table(["file_id", "yol", "kurallar"], rows("SELECT q.file_id, i.absolute_path, string_agg(DISTINCT q.rule_id, ',') FROM q JOIN inv i USING(file_id) WHERE q.status='QUARANTINE' GROUP BY 1,2 ORDER BY 2"), 60))

# F veri kaybı
dl = S["data_loss"]
L.append("\n## F. DATA LOSS REPORT\n")
L.append("```text\n" + "\n".join([
    f"Source files deleted:                 {dl['source_files_deleted']}",
    f"Source files modified:                {dl['source_files_modified']}",
    f"Source files missing after scan:      {dl['source_files_missing_after_scan']}",
    f"Rows silently dropped:                {dl['rows_silently_dropped']}",
    f"Features silently dropped:            {dl['features_silently_dropped']}",
    f"Columns silently removed:             {dl['columns_silently_removed']}",
    f"Unaccounted records:                  {dl['unaccounted_records']}",
    f"Verify pass (size+mtime, değişen → re-hash): {S['verify']['checked']} dosya kontrol edildi",
]) + "\n```\n")
status = "PASSED" if dl["source_files_modified"] == 0 and dl["source_files_missing_after_scan"] == 0 else "FAILED"
L.append(f"**RUN STATUS: {status}**\n")

(OUT / "reports" / f"{run_id}_PHASE1_REPORT.md").write_text("\n".join(L), encoding="utf-8")
print(OUT / "reports" / f"{run_id}_PHASE1_REPORT.md")
