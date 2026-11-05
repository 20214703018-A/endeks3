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
# mevcut asset'ler (sayfalı: gh release view 1000'e yakın listede eksik dönebiliyor)
RID=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$REL" --jq .id)
ALL=$(gh api --paginate "repos/$GITHUB_REPOSITORY/releases/$RID/assets?per_page=100" --jq '.[].name')
OLD=$(grep -F "${NAME}.gz" <<<"$ALL" || true)
# 6 Kas 2026: release 1000 dosya sınırındaydı; ilk parçalar --clobber ile ezildi, son parça 422 aldı, paket sonu kopuk kaldı.
# Eski parçaya dokunmadan ÖNCE yeni dosyaların sığdığı denetlenir.
YENI=0; for f in "$OUT"/*; do grep -qxF "$(basename "$f")" <<<"$ALL" || YENI=$((YENI + 1)); done
TOPLAM=$(grep -c . <<<"$ALL" || true)
if [ $((TOPLAM + YENI)) -gt 1000 ]; then
  echo "::error::$REL dosya sınırı: $TOPLAM mevcut + $YENI yeni > 1000; hiçbir parça değiştirilmedi"; rm -rf "$OUT"; exit 1
fi
# yeni parçaları yükle (aynı adlı varsa --clobber üstüne yazar)
# GitHub ara sıra 500 verir (7 Eki 2026: detay yüklemesi kayboldu) → dosya başına 5 deneme, artan bekleme
for f in "$OUT"/*; do
  for i in 1 2 3 4 5; do
    gh release upload "$REL" "$f" --clobber --repo "$GITHUB_REPOSITORY" && break
    [ "$i" = 5 ] && { echo "yükleme 5 denemede başarısız: $f"; exit 1; }
    echo "yükleme hatası ($i/5), $((i * 30)) sn sonra tekrar: $f"; sleep $((i * 30))
  done
done
NEW=$(ls "$OUT")
# artık kullanılmayan eski asset'leri sil (yeni sette olmayanlar)
for a in $OLD; do
  if ! grep -qx "$a" <<<"$NEW"; then
    gh release delete-asset "$REL" "$a" --yes --repo "$GITHUB_REPOSITORY" || true
    echo "eski asset silindi: $a"
  fi
done
rm -rf "$OUT"
