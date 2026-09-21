#!/usr/bin/env bash
# Kullanım: ops/ambar_yukle.sh <yol/dosya.sqlite>
# gzip'ler; 1500 MB'ı aşarsa parçalara böler (GitHub asset sınırı 2 GB). Önce yeni parçaları
# yükler, sonra eski/artık kullanılmayan parça ve tek-parça asset'lerini siler (veri kaybı riski yok).
set -euo pipefail
F="$1"; NAME="$(basename "$F")"; REL="${AMBAR_RELEASE:-warehouse-latest}"
OUT="$(mktemp -d)"
gzip -1 -c "$F" > "$OUT/${NAME}.gz"
SZ=$(stat -c %s "$OUT/${NAME}.gz")
LIMIT=$((1500*1024*1024))
if [ "$SZ" -gt "$LIMIT" ]; then
  ( cd "$OUT" && split -b 1500m -d --suffix-length=2 "${NAME}.gz" "${NAME}.gz.part-" && rm -f "${NAME}.gz" )
fi
ls -la "$OUT"
# mevcut ilgili asset'ler
OLD=$(gh release view "$REL" --repo "$GITHUB_REPOSITORY" --json assets --jq '.assets[].name' | grep -F "${NAME}.gz" || true)
# yeni parçaları yükle (aynı adlı varsa --clobber üstüne yazar)
gh release upload "$REL" "$OUT"/* --clobber --repo "$GITHUB_REPOSITORY"
NEW=$(ls "$OUT")
# artık kullanılmayan eski asset'leri sil (yeni sette olmayanlar)
for a in $OLD; do
  if ! grep -qx "$a" <<<"$NEW"; then
    gh release delete-asset "$REL" "$a" --yes --repo "$GITHUB_REPOSITORY" || true
    echo "eski asset silindi: $a"
  fi
done
rm -rf "$OUT"
