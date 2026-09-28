#!/bin/bash
# Market Fiyatı şube katalogları — GitHub Actions zinciri (yerelde, arka planda).
# Son koşu bitince: artifact'ları indir → GEOPROP_RAW_INTAKE/.../branches/ içine ekle → tamam listesini güncelle →
# ayar dosyasındaki RUN sayacını artırıp marketfiyati-subeler dalına gönder (yeni koşu tetiklenir).
# Kalan şube kalmayınca build_branches çalıştırır ve çıkar. Mac uyursa bekler, uyanınca kaldığı yerden sürer.
set -u
export PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin  # /usr/local/bin/python3: pandas kurulu olan
R=20214703018-A/endeks3
WF=marketfiyati_subeler_40_makine.yml
W=$HOME/Desktop/GEOPROP_CONSOLIDATION/run/mf_wt
DL=$HOME/Desktop/GEOPROP_CONSOLIDATION/run/mf_dl
B=$HOME/Desktop/GEOPROP_RAW_INTAKE/marketfiyati/2026-09-24/branches
TOOLS=$HOME/Desktop/endeks3/consolidation/tools
mkdir -p "$DL"
log() { echo "[$(date +%Y-%m-%dT%H:%M:%S%z)] mf_zincir: $*"; }

while true; do
  id=$(gh run list -R $R --branch marketfiyati-subeler --workflow $WF --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null)
  if [ -z "$id" ]; then sleep 300; continue; fi
  if [ -f "$DL/$id.pushed" ]; then sleep 300; continue; fi   # bu koşunun devamı zaten tetiklendi, yenisi bekleniyor
  st=$(gh run view $id -R $R --json status -q .status 2>/dev/null)
  if [ "$st" != "completed" ]; then sleep 600; continue; fi
  if [ ! -f "$DL/$id.merged" ]; then
    rm -rf "$DL/$id"
    if ! gh run download $id -R $R -D "$DL/$id" >/dev/null 2>&1; then log "koşu $id indirilemedi, yeniden denenecek"; sleep 300; continue; fi
    n=$(ls "$DL/$id"/*/branches/*.jsonl.gz 2>/dev/null | wc -l)
    [ "$n" -gt 0 ] && cp "$DL/$id"/*/branches/*.jsonl.gz "$B"/
    grep -h "süre sınırı\|DURDU\|hata" "$DL/$id"/*/run.log 2>/dev/null | cut -c1-200 > "$DL/$id.notes"
    rm -rf "$DL/$id"; touch "$DL/$id.merged"
    log "koşu $id birleştirildi: $n parça dosya; $(grep -c DURDU "$DL/$id.notes") makine engel nedeniyle durdu"
  fi
  left=$(python3 - "$B" "$W" <<'EOF'
import gzip, json, glob, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2] + "/consolidation/tools")
import pandas as pd
import intake_marketfiyati as m
B, W = sys.argv[1], Path(sys.argv[2])
done = set()
for f in glob.glob(B + "/depot_*.jsonl.gz"):
    try:
        with gzip.open(f, "rt") as fh:
            done |= {json.loads(l)["depot_id"] for l in fh}
    except Exception:
        pass
(W / "consolidation/girdi/marketfiyati_branches_done.txt").write_text("\n".join(sorted(done)) + "\n")
order = m.branch_order(pd.read_parquet(W / "consolidation/girdi/marketfiyati_depots.parquet"))
print(sum(1 for r in order if r["depot_id"] not in done))
EOF
)
  if [ -z "$left" ]; then log "kalan hesaplanamadı"; sleep 600; continue; fi
  log "kalan şube: $left"
  if [ "$left" -eq 0 ]; then
    touch "$DL/$id.pushed"
    (cd "$TOOLS" && INTAKE_RUN_DATE=2026-09-24 python3 -u intake_marketfiyati.py build_branches)
    log "bitti"; exit 0
  fi
  cd "$W" || exit 1
  git pull -q --rebase 2>/dev/null
  run=$(grep '^RUN=' .github/marketfiyati_run.env | cut -d= -f2); run=$(( ${run:-0} + 1 ))
  if grep -q '^RUN=' .github/marketfiyati_run.env; then sed -i '' "s/^RUN=.*/RUN=$run/" .github/marketfiyati_run.env; else echo "RUN=$run" >> .github/marketfiyati_run.env; fi
  git add .github/marketfiyati_run.env consolidation/girdi/marketfiyati_branches_done.txt
  git commit -q -m "marketfiyati şubeler: koşu $run (kalan $left şube)" && git push -q && touch "$DL/$id.pushed" && log "koşu $run tetiklendi"
  sleep 300
done
