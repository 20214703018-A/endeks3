"""Dosya yolları. Veri git dışında: ~/Desktop/GEOPROP_CONSOLIDATION (araçlardaki OUT sabiti)."""
import os
from pathlib import Path

OUT = Path(os.environ.get("GEOPROP_OUT", Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"))
CANONICAL = OUT / "canonical" / "v1.1" / "geoprop_canonical_v1_1.duckdb"
SERVING_DIR = OUT / "serving" / "v1"
SERVING = Path(os.environ.get("GEOPROP_SERVING", SERVING_DIR / "geoprop_serving_v1.duckdb"))
TMP = OUT / "tmp" / "serving_build"
PKG = Path(__file__).resolve().parent
ONTOLOGY = PKG / "ontology.yaml"
AGENT_ROOT = PKG.parent
