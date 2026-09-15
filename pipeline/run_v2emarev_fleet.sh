#!/usr/bin/env bash
# EMArev-by-trigger study (coach 2026-09-09): re-run the fleet with the PRE-v2 exit
# (V2_EXIT=false) so the frozen signal set is byte-identical, while the EA's shadow
# observer reconstructs the doctrine-v2 R + trigger-impulse feature into a per-symbol
# sidecar (journal\v2study_<sym>_*.csv). Same ranges as run_mfe_fleet.sh. MT5 closed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CJ="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
OUT="$HERE/../data/study/v2emarev"; mkdir -p "$OUT"
ROWS=(
 "EURUSD.dk 2016.08.01 2026.06.30"
 "GBPUSD.dk 2013.08.01 2025.12.31"
 "USDJPY.dk 2020.01.01 2025.12.31"
 "XAUUSD.dk 2020.01.01 2025.12.31"
 "US100.dk 2012.01.01 2025.12.31"
 "US500.dk 2012.01.01 2025.12.31"
 "USOIL.dk 2013.01.01 2025.12.31"
)
for r in "${ROWS[@]}"; do
  set -- $r; sym=$1; frm=$2; to=$3
  echo "V2FLEET $sym $frm..$to"
  V2_EXIT=false bash "$HERE/mt5_verify.sh" --mode ALL --strat SMC,Fib,EMA --symbol "$sym" \
      --from "$frm" --to "$to" --model 4 --timeout 5400 2>&1 | grep -E "total journal"
  S=$(ls -t "$CJ"/v2study_${sym}_*.csv 2>/dev/null | head -1)
  [ -n "$S" ] && cp "$S" "$OUT/${sym}.v2.csv" && echo "  parked $OUT/${sym}.v2.csv ($(($(wc -l <"$S")-1)) rows)"
done
echo "V2FLEET DONE"
