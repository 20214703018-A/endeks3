#!/bin/zsh
# Tüm all_*.json spec'lerini sırayla uygular; her aile için log. Kaynak köklere yazmaz.
cd ~/Desktop/GEOPROP_CONSOLIDATION
for f in reference_geography price_series listings cadastre_zoning poi_business demographics_context education economy mobility_logistics vehicles unclassified; do
  spec="tools/specs/all_$f.json"; [ -f "$spec" ] || continue
  echo "=== $f $(date +%H:%M:%S) ===" 
  python3 tools/phase2_stage_generic.py --spec "$spec" --apply 2>&1 | grep -E 'YAZILDI|HATA|Traceback|"family"' | sed "s/^/[$f] /"
  df -h /System/Volumes/Data | tail -1 | awk '{print "  boş disk:", $4}'
done
echo "ALL_DONE $(date +%H:%M:%S)"
