#!/usr/bin/env bash
#
# start_level.sh - one-paste setup for a training-program level.
#
#   bash /home/jack/hybrid_project/pipeline/start_level.sh 2          # level 2
#   bash /home/jack/hybrid_project/pipeline/start_level.sh 3 --alt    # level 3 alternate window
#   bash /home/jack/hybrid_project/pipeline/start_level.sh 7a         # final exam, part a
#
# What it does, in order:
#   1. Ensures the blind-approve baseline (AA_ALL) exists for the level's
#      symbol+window - runs mt5_verify.sh headless if not, then parks the
#      journal in journal/baselines/ so it can't collide with your session.
#   2. Launches MT5 with the visual tester pre-configured (symbol, dates,
#      H4, real ticks, $25k, HybridForwardTest in interactive mode) - the
#      test auto-starts; you just answer the dialogs.
#   3. Opens the quick-reference guide in your browser.
#
# Flags: --alt (use the level's alternate window)  --skip-baseline  --dry-run
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
MT5_DATA_WIN='C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB'
TERMINAL="/mnt/c/Program Files/OANDA MetaTrader 5/terminal64.exe"
COMMON_JOURNAL="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
BASELINE_DIR="$COMMON_JOURNAL/baselines"
SETDIR="$MT5_DATA/MQL5/Profiles/Tester"
INI_WSL="$MT5_DATA/hft_level.ini"
INI_WIN="$MT5_DATA_WIN\\hft_level.ini"
GUIDE_WIN='C:\Users\jacks\OneDrive\Trading\hybrid_project\training\quick-reference.html'

log(){ printf '%s\n' "$*" >&2; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

LEVEL=""; ALT=false; SKIP_BASELINE=false; DRYRUN=false
while [[ $# -gt 0 ]]; do case "$1" in
  --alt) ALT=true; shift;;
  --skip-baseline) SKIP_BASELINE=true; shift;;
  --dry-run) DRYRUN=true; shift;;
  -h|--help) sed -n '3,20p' "$0"; exit 0;;
  *) LEVEL="$1"; shift;;
esac; done
[[ -n "$LEVEL" ]] || die "usage: start_level.sh <0-6|7a|7b|7c> [--alt] [--skip-baseline]"

# --- level table: symbol from to [alt-symbol alt-from alt-to] ---------------
# Keep in sync with training/training-program.html level cards.
case "$LEVEL" in
  0)  P=(EURUSD.dk 2021.07.01 2021.12.31); A=(EURUSD.dk 2020.07.01 2020.12.31);;
  1)  P=(EURUSD.dk 2021.01.01 2021.12.31); A=(EURUSD.dk 2020.01.01 2020.12.31);;
  2)  P=(EURUSD.dk 2022.01.01 2022.12.31); A=(EURUSD.dk 2023.01.01 2023.12.31);;
  3)  P=(GBPUSD.dk 2023.01.01 2023.12.31); A=(GBPUSD.dk 2021.01.01 2021.12.31);;
  4)  P=(USDJPY.dk 2024.01.01 2024.12.31); A=(USDJPY.dk 2022.01.01 2022.12.31);;
  5)  P=(XAUUSD.dk 2023.01.01 2023.12.31); A=(XAUUSD.dk 2022.01.01 2022.12.31);;
  6)  P=(US100.dk  2022.01.01 2022.12.31); A=(US500.dk  2022.01.01 2022.12.31);;
  7a) P=(EURUSD.dk 2025.01.01 2025.12.31); A=();;
  7b) P=(GBPUSD.dk 2025.01.01 2025.12.31); A=();;
  7c) P=(XAUUSD.dk 2025.01.01 2025.12.31); A=();;
  *) die "unknown level '$LEVEL' (0-6, 7a, 7b, 7c)";;
esac
if $ALT; then
  [[ ${#A[@]} -gt 0 ]] || die "level $LEVEL has no alternate window (final-exam levels are single-shot)"
  SYMBOL="${A[0]}"; FROM="${A[1]}"; TO="${A[2]}"
else
  SYMBOL="${P[0]}"; FROM="${P[1]}"; TO="${P[2]}"
fi
FROMC="${FROM//./}"; TOC="${TO//./}"
BASELINE="$BASELINE_DIR/${SYMBOL}_${FROMC}_${TOC}_AA_ALL.csv"

log "=== Level $LEVEL$($ALT && echo ' (alternate window)') ==="
log "    $SYMBOL  $FROM -> $TO  (H4, real ticks, \$25k, interactive)"
if $DRYRUN; then
  log "dry-run: baseline file would be $BASELINE ($([[ -f $BASELINE ]] && echo exists || echo missing))"
  log "dry-run: would launch: \"$TERMINAL\" /config:$INI_WIN"
  exit 0
fi

[[ -x "$TERMINAL" ]] || die "terminal64 not found at $TERMINAL"
proc_running(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }
proc_running && die "MT5 is already running - close it first (baseline and tester need the terminal to themselves)."

# --- 1. baseline ------------------------------------------------------------
mkdir -p "$BASELINE_DIR"
if [[ -f "$BASELINE" ]]; then
  log "baseline already exists: $BASELINE"
elif $SKIP_BASELINE; then
  log "baseline SKIPPED by flag - remember to generate it before the coach critique."
else
  log "generating blind-approve baseline first (headless, real ticks - a year can take a while; don't touch MT5)..."
  BEFORE=$(ls -1 "$COMMON_JOURNAL" 2>/dev/null | sort || true)
  "$HERE/mt5_verify.sh" --mode ALL --strat SMC,Fib,EMA --symbol "$SYMBOL" \
      --from "$FROM" --to "$TO" --model 4 --timeout 5400
  AFTER=$(ls -1 "$COMMON_JOURNAL" 2>/dev/null | sort || true)
  NEWJ=$(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER") | grep -i "^${SYMBOL}_" | tail -1 || true)
  [[ -n "$NEWJ" ]] || die "baseline run produced no journal - see mt5_verify output above."
  mv "$COMMON_JOURNAL/$NEWJ" "$BASELINE"
  log "baseline parked: $BASELINE"
fi

# --- 2. interactive visual tester ------------------------------------------
mkdir -p "$SETDIR"
{
  echo "InpRiskPct=0.01"
  echo "InpMagic=990217"
  echo "InpUseColoredDialog=true"
  echo "InpAutoApprove=0"
  echo "InpUseSMC=true"
  echo "InpUseFib=true"
  echo "InpUseEMA=true"
} > "$SETDIR/hft_level.set"
{
  echo "; auto-generated by pipeline/start_level.sh (level $LEVEL)"
  echo "[Tester]"
  echo "Expert=HybridForwardTest"
  echo "ExpertParameters=hft_level.set"
  echo "Symbol=$SYMBOL"
  echo "Period=H4"
  echo "Model=4"
  echo "ExecutionMode=0"
  echo "Optimization=0"
  echo "FromDate=$FROM"
  echo "ToDate=$TO"
  echo "ForwardMode=0"
  echo "Deposit=25000"
  echo "Currency=USD"
  echo "Leverage=100"
  echo "Visual=1"
  echo "ReplaceReport=1"
  echo "ShutdownTerminal=0"
} > "$INI_WSL"

log "launching MT5 visual tester..."
nohup "$TERMINAL" "/config:$INI_WIN" >/dev/null 2>&1 &
disown

# --- 3. open the guide ------------------------------------------------------
cmd.exe /c start "" "$GUIDE_WIN" >/dev/null 2>&1 || true

log ""
log "=== Ready. The visual test auto-starts in MT5. ==="
log "  - If no dialog appears on the first alert: check BOTH 'Allow DLL imports'"
log "    boxes (Tools > Options > Expert Advisors, AND the tester Settings tab),"
log "    then press Start again. TradeDialog.dll must be deployed (mql5/dll/build.sh)."
log "  - Press Pause (VK_PAUSE) before deciding to freeze the chart / take blind"
log "    screenshots for the advisor (crop title bar + time axis)."
log "  - Every skip needs a reason. Good session."
