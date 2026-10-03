#!/bin/bash
# Pazarama satıcı toplayıcısı — DURMAZ: çökerse/uyuyup uyanırsa aynı klasörden kaldığı yerden sürer.
# Engel/kota (403/429) betiğin içinde bekleyerek aşılır (kimlik değiştirme yok). Bitince çıkış kodu 0 → döngü biter.
export INTAKE_RUN_DATE=2026-10-03
cd "$(dirname "$0")/.." || exit 1
until python3 intake_pazarama_satici.py; do
  echo "[$(date -Iseconds)] pazarama betiği çıkış kodu $? — 60 sn sonra yeniden başlatılıyor"
  sleep 60
done
echo "[$(date -Iseconds)] pazarama tamamlandı"
