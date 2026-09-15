#!/usr/bin/env bash
# EMArevQ stretch-threshold scan (coach 2026-09-09). Re-detect EMArev CANDIDATES (isolated
# --strat EMA, no arbitration lock) at 5 stretch thresholds, model-1 (detection is model-
# independent; ~15s per 10y run), V2_EXIT=false so the shadow observer writes the doctrine-v2
# sidecar. Parks per-threshold sidecars to data/study/stretch_scan/<theta>/<sym>.v2.csv.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CJ="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
ROWS=(
 "EURUSD.dk 2016.08.01 2026.06.30"
 "GBPUSD.dk 2013.08.01 2025.12.31"
 "USDJPY.dk 2020.01.01 2025.12.31"
 "XAUUSD.dk 2020.01.01 2025.12.31"
 "US100.dk 2012.01.01 2025.12.31"
 "US500.dk 2012.01.01 2025.12.31"
 "USOIL.dk 2013.01.01 2025.12.31"
)
THETAS=(1.0 1.25 1.5 1.75 2.0)
for th in "${THETAS[@]}"; do
  OUT="$HERE/../data/study/stretch_scan/$th"; mkdir -p "$OUT"
  echo "===== THETA $th ====="
  for r in "${ROWS[@]}"; do
    set -- $r; sym=$1; frm=$2; to=$3
    EMA_STRETCH=$th V2_EXIT=false bash "$HERE/mt5_verify.sh" --mode ALL --strat EMA \
        --symbol "$sym" --from "$frm" --to "$to" --model 1 --timeout 600 2>&1 \
        | grep -E "total journal" | sed "s/^/  $sym /"
    S=$(ls -t "$CJ"/v2study_${sym}_*.csv 2>/dev/null | head -1)
    if [ -n "$S" ]; then cp "$S" "$OUT/${sym}.v2.csv"; echo "    parked $OUT/${sym}.v2.csv ($(($(wc -l <"$S")-1)) rows)"; fi
  done
done
echo "STRETCH SCAN DONE"
