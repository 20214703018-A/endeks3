#!/usr/bin/env bash
# Kullanım: ops/ambar_indir.sh <dosya_adi.sqlite> <hedef_klasor>
# warehouse-latest release'inden <dosya>.gz (tek parça) ya da <dosya>.gz.part-* (parçalı) indirir,
# birleştirip açar. Dosya yoksa 1 döner (çağıran "ilk koşu" diye devam edebilir).
set -euo pipefail
NAME="$1"; DIR="$2"; REL="${AMBAR_RELEASE:-warehouse-latest}"
mkdir -p "$DIR"
TMP="$(mktemp -d)"
if gh release download "$REL" --pattern "${NAME}.gz*" --dir "$TMP" --repo "$GITHUB_REPOSITORY" 2>/dev/null && ls "$TMP"/* >/dev/null 2>&1; then
  if ls "$TMP"/*.part-* >/dev/null 2>&1; then
    cat "$TMP"/${NAME}.gz.part-* | gunzip -c > "$DIR/$NAME"
  else
    gunzip -c "$TMP/${NAME}.gz" > "$DIR/$NAME"
  fi
  rm -rf "$TMP"
  ls -la "$DIR/$NAME"
else
  rm -rf "$TMP"
  echo "ℹ️ ${NAME} release'te yok."
  exit 1
fi
