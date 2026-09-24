#!/bin/bash
# GEOPROP günlük fiyat/durum izleme — her gün bir kez çalışır (launchd: com.geoprop.track.daily.plist).
# Her kaynak kendi tarihli klasörüne (GEOPROP_RAW_INTAKE/<kaynak>/<YYYY-MM-DD>/) yazar; geçmiş birikir.
#  1) Hal fiyatları: HKS ulusal + İzmir sebze-meyve/balık (son 10 gün; tatil boşlukları kapanır)
#  2) Zincir market fiyatları: 81 il × 7 zincir kalem kalem (Market Fiyatı; 1 istek/sn, engelde durur)
#  3) Şarj istasyonları: EPDK Şarj@TR anlık müsaitlik listesi
#  4) EPDK akaryakıt/LPG günlük bülteni (kota izin verdikçe)
set -u
T=/Users/acar/Desktop/endeks3/consolidation/tools
L=/Users/acar/Desktop/GEOPROP_CONSOLIDATION/logs/track_$(date +%F).log
cd "$T" || exit 1
{
  echo "=== $(date -Iseconds) başlangıç"
  python3 -u intake_hal.py track
  python3 -u intake_sarj_epdk.py status
  python3 -u intake_marketfiyati.py track --threads 1 --rps 1
  python3 -u intake_epdk_api.py
  echo "=== $(date -Iseconds) bitti"
} >> "$L" 2>&1
