#!/usr/bin/env bash
# Bir iş akışı çıktısını TARİHLİ olarak 'veri-arsivi' release'ine kalıcı yazar (artifact'lar 1-90 günde silinir).
# Kullanım: ops/arsive_yaz.sh <dosya> [ek_etiket]   →  <dosya_adı>_<YYYY-MM-DD>[_ek].gz
# Her koşu ayrı dosya olur; bir koşu öncekinin üzerine yazmaz (aynı gün tekrar koşarsa o günün dosyası yenilenir).
set -euo pipefail
F="$1"; EK="${2:-}"; REL="${ARSIV_RELEASE:-veri-arsivi}"
[ -s "$F" ] || { echo "ℹ️ $F yok/boş — arşive yazılmadı"; exit 0; }
GUN=$(date -u +%Y-%m-%d); B=$(basename "$F"); AD="${B%.*}_${GUN}${EK:+_$EK}.${B##*.}.gz"
gh release view "$REL" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1 || \
  gh release create "$REL" --repo "$GITHUB_REPOSITORY" --latest=false -t "Toplayıcı çıktı arşivi (tarihli)" \
    -n "Zamanlanmış toplayıcıların her koşusunun sonucu, tarihli dosya olarak. Kanonik DB bu arşivden beslenir."
gzip -1 -c "$F" > "/tmp/$AD"
SZ=$(stat -c %s "/tmp/$AD")
if [ "$SZ" -gt $((1900*1024*1024)) ]; then
  (cd /tmp && split -b 1900m -d --suffix-length=2 "$AD" "$AD.part-" && rm -f "$AD")
  gh release upload "$REL" /tmp/"$AD".part-* --clobber --repo "$GITHUB_REPOSITORY"
else
  gh release upload "$REL" "/tmp/$AD" --clobber --repo "$GITHUB_REPOSITORY"
fi
echo "✅ arşivlendi: $REL/$AD ($((SZ/1048576)) MB)"
