#!/usr/bin/env bash
# TrendCont candidate fleet backtest (coach 2026-09-09, part B). STANDALONE, doctrine-v2
# exits (InpV2Exit default true), model 4, full ranges. Parks AA journal per symbol.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CJ="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
OUT="$HERE/../data/study/trendcont"; mkdir -p "$OUT"
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
  echo "TCFLEET $sym $frm..$to"
  bash "$HERE/mt5_verify.sh" --mode ALL --strat TrendCont --symbol "$sym" \
      --from "$frm" --to "$to" --model 4 --timeout 5400 2>&1 | grep -E "total journal"
  J=$(ls -t "$CJ"/AA_${sym}_*.csv 2>/dev/null | grep -v actions | grep -v '\.inv\.' | grep -v part | head -1)
  [ -n "$J" ] && cp "$J" "$OUT/${sym}.csv" && echo "  parked $OUT/${sym}.csv ($(($(wc -l <"$J")-1)) rows)"
done
echo "TCFLEET DONE"
