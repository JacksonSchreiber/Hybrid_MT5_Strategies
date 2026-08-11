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
#      H4, real ticks, $25k, HybridForwardTest in interactive mode, blind
#      screenshots on) - the test auto-starts; you just answer the dialogs.
#   2b. Boots the OS-capture daemon: when each approval popup appears it grabs the
#      visual chart via PrintWindow (ChartScreenShot yields no file in the tester),
#      blind-crops it, and delivers a bundle (H4 overlays, D1 rendered, blind
#      numbers) to the advisor's inbox. Stops itself when you close the tester.
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
  1)  P=(EURUSD.dk 2023.07.01 2023.12.31); A=();;                                  # L1 RETRY window (2026-08-08): failed on 2021-H1 + 2023-H1 (both burned); no alt left — this is the last EURUSD shot before symbol change
  2)  P=(EURUSD.dk 2022.01.01 2022.12.31); A=(GBPUSD.dk 2022.01.01 2022.12.31);;   # alt reassigned to GBPUSD 2022 (2023-H2 went to the L1 retry); same freight-train regime
  3)  P=(GBPUSD.dk 2023.01.01 2023.12.31); A=(GBPUSD.dk 2024.01.01 2024.12.31);;   # alt was GBPUSD 2021: contained L0's studied 2021-H2 on a ~0.9-correlated pair (amended 2026-08-07)
  4)  P=(USDJPY.dk 2024.01.01 2024.12.31); A=(USDJPY.dk 2022.01.01 2022.12.31);;
  5)  P=(XAUUSD.dk 2023.01.01 2023.12.31); A=(XAUUSD.dk 2022.01.01 2022.12.31);;
  6)  P=(US100.dk  2022.01.01 2022.12.31); A=(US500.dk  2022.01.01 2022.12.31);;
  7a) P=(EURUSD.dk 2025.07.01 2026.06.30); A=();;   # was 2025 full year: 2025-H1 sits inside the interactively-traded 2024-01→2025-06 window (amended 2026-08-07; engineer must verify .dk tick coverage into 2026 before this runs)
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
  echo "InpShotOnDecision=true"   # capture the H4 chart (with overlays) on each signal
  echo "InpBlindLabels=true"      # on-chart label carries no date -> blind screenshots
  echo "InpShowEvents=false"      # BLINDNESS: event lines print currency+event name (e.g.
                                  # "USD FOMC Meeting Minutes") on the chart -> would leak to
                                  # the blind advisor. Off; advisor gets calendar via Tier-0.
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

# --- 2b. auto-boot the OS-capture daemon ------------------------------------
# ChartScreenShot() yields no file in the Strategy Tester, so we grab the visual
# chart at the OS level (PrintWindow) the moment each approval popup appears, then
# deliver a blind bundle (H4 overlays cropped blind, D1 rendered, blind numbers)
# to the advisor inbox. Self-terminates when the tester closes.
BRIDGE_LOG="$MT5_DATA/os_shot_daemon.log"
if command -v python3 >/dev/null 2>&1 && command -v powershell.exe >/dev/null 2>&1; then
  log "os-capture daemon: blind setup shots -> advisor inbox (log: $BRIDGE_LOG)"
  (
    : > "$BRIDGE_LOG"
    python3 -u "$HERE/os_shot_daemon.py" --watch >>"$BRIDGE_LOG" 2>&1 &
    bpid=$!
    for _ in $(seq 1 40); do if proc_running; then break; fi; sleep 2; done  # wait ≤80s for MT5
    while proc_running; do sleep 10; done                                    # run until it closes
    kill "$bpid" 2>/dev/null || true
  ) >/dev/null 2>&1 &
  disown
else
  log "WARNING: python3/powershell.exe not found - OS-capture daemon NOT started (setups won't auto-deliver)."
fi

# --- 3. open the guide ------------------------------------------------------
cmd.exe /c start "" "$GUIDE_WIN" >/dev/null 2>&1 || true

log ""
log "=== Ready. The visual test auto-starts in MT5. ==="
log "  - If no dialog appears on the first alert: check BOTH 'Allow DLL imports'"
log "    boxes (Tools > Options > Expert Advisors, AND the tester Settings tab),"
log "    then press Start again. TradeDialog.dll must be deployed (mql5/dll/build.sh)."
log "  - Blind setup shots auto-deliver to the advisor inbox as signals fire"
log "    (training/advisor/inbox/); just prompt the advisor - no manual capture."
log "  - Press Pause (VK_PAUSE) any time to freeze the chart and study it."
log "  - Every skip needs a reason. Good session."
