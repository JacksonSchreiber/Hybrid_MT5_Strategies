#!/usr/bin/env bash
# Item 2 fleet: AA model-4 full-range per core program symbol -> study journals with
# mfe_r/recovery columns. Sequential (mt5_verify waits for its terminal). MT5 must be closed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CJ="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
OUT="$HERE/../data/study/mfe"; mkdir -p "$OUT"
# symbol from to
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
  echo "FLEET $sym $frm..$to"
  bash "$HERE/mt5_verify.sh" --mode ALL --strat SMC,Fib,EMA --symbol "$sym" --from "$frm" --to "$to" --model 4 --timeout 5400 2>&1 | grep -E "journal:|total journal"
  J=$(ls -t "$CJ"/AA_${sym}_*.csv 2>/dev/null | head -1)
  [ -n "$J" ] && cp "$J" "$OUT/${sym}.study.csv" && echo "  parked $OUT/${sym}.study.csv"
done
echo "FLEET DONE"
