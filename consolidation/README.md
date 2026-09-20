# consolidation/ — GEOPROP veri konsolidasyonu (kod + belgeler)
Bu klasör yalnız **kod ve belgeleri** içerir. Veri (ham envanter, staging parquet, kanonik DuckDB) git'e girmez; çalışma dizini `~/Desktop/GEOPROP_CONSOLIDATION` (araçlardaki `OUT` sabiti).
- `STANDARD.md` — veri standardı (kurallar) · `DURUM_VE_PLAN.md` — sade durum/plan
- `tools/` — Phase 1 (envanter), Phase 2 (staging), Phase 3 (eşleştirme/kategori), Phase 4 (kanonik) betikleri; `specs/` staging tarifleri; `quarantine_rules.json`
- `validation/` — doğruluk raporları, karantina kanıt kartları, golden set sayfaları · `reports/` — koşu raporları · `mappings/*.json` — eşleştirici yapılandırmaları
- Ham veri kökleri (dokunulmaz): ~/Desktop/GEOPROP (bu deponun veri dolu klonu), ~/Desktop/tkgm, ~/Desktop/harita, ~/Downloads, ~/Desktop/GEOPROP_RAW_INTAKE
`~/Desktop/GEOPROP_CONSOLIDATION/{tools,validation,reports,STANDARD.md,DURUM_VE_PLAN.md}` bu klasöre sembolik bağdır; tek kopya burada yaşar.
