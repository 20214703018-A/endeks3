#!/bin/bash
# Pazarama GitHub Actions çıktısını indirir ve yerelde çözer (özel anahtar: ~/.config/geoprop/pazarama_age.key).
# Kullanım: pazarama_gha_indir.sh <run_id> [hedef_klasor]
set -euo pipefail
RUN="${1:?run_id gerekli}"
KEY="$HOME/.config/geoprop/pazarama_age.key"
DEST="${2:-$HOME/Desktop/GEOPROP_RAW_INTAKE/eticaret_pazarama_satici/gha_$RUN}"
TMP="$(mktemp -d)"
gh run download "$RUN" --repo 20214703018-A/endeks3 --dir "$TMP"
mkdir -p "$DEST"
for f in "$TMP"/*/*.age; do
  n="$(basename "$f" .tar.gz.age)"
  mkdir -p "$DEST/$n"
  age -d -i "$KEY" "$f" | tar xz -C "$DEST/$n"
  echo "çözüldü: $n"
done
rm -rf "$TMP"
echo "bitti → $DEST"
