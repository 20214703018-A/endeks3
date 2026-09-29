"""GEOPROP ontoloji tabanlı analiz agent'ı.

Katmanlar (yukarıdan aşağı):
  doğal dil (Claude: MCP veya API)  →  analiz planı (JSON, pydantic)  →  motor (DuckDB, kurallı)
Sayıları yalnız motor üretir; yapay zekâ sayı hesaplamaz.
"""
__version__ = "0.1.0"
