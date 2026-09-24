#!/bin/bash
# Saatlik hafif izleme: şarj istasyonu müsaitliği (yoğunluk/doluluk zaman serisi üretir).
cd /Users/acar/Desktop/endeks3/consolidation/tools || exit 1
python3 -u intake_sarj_epdk.py status >> /Users/acar/Desktop/GEOPROP_CONSOLIDATION/logs/track_hourly_$(date +%F).log 2>&1
