#!/usr/bin/env bash
#
# mt5_parallel.sh - run many tester passes AT ONCE (trader 2026-10-03): MT5 optimisation mode ("slow complete") hands one pass
# to each local agent (one per core; RAM is the practical cap - about 1 GB per real-tick pass). Each pass is one combination of
# the --grid inputs; the EA (MQL_OPTIMIZATION) journals it under Common\Files\journal\par\<symbol>_<tag>\ with a params.txt.
#
#   pipeline/mt5_parallel.sh --symbol EURUSD.dk --from 2016.08.01 --to 2026.06.30 --model 4 \
#       --grid InpAddGate=bool --grid InpShortCutAvg=0:0.5:0.5 --grid InpImprovingAdd=0:0.5:0.5 --out data/study/par/queue_eurusd
#   pipeline/mt5_parallel.sh ... --grid InpParIdx=0:1:7          # 8 identical passes (timing / agent check)
#
#   --grid NAME=bool             false and true
#   --grid NAME=start:step:stop  numeric range (inclusive)
#   Fixed inputs: the same base as mt5_verify.sh (AA=ALL, the --strat detectors, InpRiskPct 0.01) plus its env knobs
#   (STAGED PYR TC_BANK SHORT_RAISE ADD_GATE SHORT_CUT IMPR_ADD INVERSE EARLY V2_EXIT ...) - a --grid input overrides its knob.
#   Output: <out>/<symbol>_<tag>/ (journal, actions, params.txt) + <out>/index.csv (tag, symbol, params, journal rows).
#
# SAFETY: refuses if terminal64.exe is running (never launches over the user's terminal); never kills it. Clears ONLY this
# expert's optimisation cache (Tester\cache\<expert>.*), else MT5 skips passes it has seen and writes no journal.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
MT5_DATA_WIN='C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB'
TERMINAL="/mnt/c/Program Files/OANDA MetaTrader 5/terminal64.exe"
PAR="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal/par"
SETDIR="$MT5_DATA/MQL5/Profiles/Tester"; INI_WSL="$MT5_DATA/hft_parallel.ini"; INI_WIN="$MT5_DATA_WIN\\hft_parallel.ini"
SYMBOL="EURUSD.dk"; FROM="2023.01.01"; TO="2024.12.31"; MODEL=4; TIMEOUT=14400; STRAT="SMC,Fib,TrendCont,EMArevQ"
EXPERT="HybridForwardTest"; OUT=""; GRID=()
log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2; }
die(){ log "ERROR: $*"; exit 1; }
while [[ $# -gt 0 ]]; do case "$1" in
  --symbol) SYMBOL="$2"; shift 2;; --from) FROM="$2"; shift 2;; --to) TO="$2"; shift 2;; --model) MODEL="$2"; shift 2;;
  --strat) STRAT="$2"; shift 2;; --timeout) TIMEOUT="$2"; shift 2;; --expert) EXPERT="$2"; shift 2;;
  --grid) GRID+=("$2"); shift 2;; --out) OUT="$2"; shift 2;;
  -h|--help) sed -n '3,22p' "$0"; exit 0;; *) die "unknown flag: $1";; esac; done
[[ ${#GRID[@]} -gt 0 ]] || die "need at least one --grid"
[[ -n "$OUT" ]] || OUT="$REPO/data/study/par/$(date +%Y%m%d_%H%M%S)_${SYMBOL}"
proc_running(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }
proc_running && die "terminal64 already running - refusing to launch (would disrupt the user)."
has(){ [[ ",$STRAT," == *",$1,"* ]] && echo true || echo false; }

declare -A G
for g in "${GRID[@]}"; do
  n="${g%%=*}"; v="${g#*=}"
  if [[ "$v" == "bool" ]]; then G[$n]="false||false||0||true||Y"
  else IFS=: read -r a st b <<< "$v"; [[ -n "$a" && -n "$st" && -n "$b" ]] || die "bad --grid $g"; G[$n]="$a||$a||$st||$b||Y"; fi
done
# passes run at once = min(grid combinations, cores); RAM (~1 GB per real-tick pass, 16 GB box) caps it -> MAX_PAR (default 7)
NPASS=1
for n in "${!G[@]}"; do
  IFS='|' read -r -a f <<< "${G[$n]//||/|}"
  if [[ "${f[0]}" == "false" ]]; then c=2; else c=$(python3 -c "import sys;a,st,b=map(float,sys.argv[1:]);print(int(round((b-a)/st))+1)" "${f[1]}" "${f[2]}" "${f[3]}"); fi
  NPASS=$(( NPASS * c ))
done
MAX_PAR="${MAX_PAR:-7}"
(( NPASS <= MAX_PAR )) || die "$NPASS passes > MAX_PAR=$MAX_PAR (16 GB RAM: ~1 GB per real-tick pass) - shrink the grid or set MAX_PAR"
log "$NPASS passes (all at once)"
fixed(){ local n="$1" v="$2"; [[ -n "${G[$n]:-}" ]] || echo "$n=$v"; }   # a grid input wins over a fixed one
mkdir -p "$SETDIR"
{
  echo "InpRiskPct=0.01"; echo "InpMagic=990217"; echo "InpUseColoredDialog=false"; echo "InpAutoApprove=1"
  echo "InpUseSMC=$(has SMC)"; echo "InpUseFib=$(has Fib)"; echo "InpUseEMA=$(has EMA)"; echo "InpUseEMArevQ=$(has EMArevQ)"
  echo "InpUseShock=$(has Shock)"; echo "InpUseEmaRevInv=$(has EmaRevInv)"; echo "InpUseTrendCont=$(has TrendCont)"
  [[ -n "${TC_BANK:-}" ]]     && fixed InpTcBankFrac "$TC_BANK"
  [[ -n "${PYR:-}" ]]         && fixed InpPyramid true
  [[ -n "${STAGED:-}" ]]      && fixed InpStaged true
  [[ -n "${EARLY:-}" ]]       && fixed InpEarlyPromoteR "$EARLY"
  [[ -n "${INVERSE:-}" ]]     && fixed InpInverse true
  [[ -n "${SHORT_RAISE:-}" ]] && fixed InpShortRaiseR "$SHORT_RAISE"
  [[ -n "${ADD_GATE:-}" ]]    && fixed InpAddGate true
  [[ -n "${SHORT_CUT:-}" ]]   && fixed InpShortCutAvg "$SHORT_CUT"
  [[ -n "${IMPR_ADD:-}" ]]    && fixed InpImprovingAdd "$IMPR_ADD"
  [[ -n "${V2_EXIT:-}" ]]     && fixed InpV2Exit "$V2_EXIT"
  [[ -n "${WEEKEND_FLAT:-}" ]] && fixed InpWeekendFlat "$WEEKEND_FLAT"
  for n in "${!G[@]}"; do echo "$n=${G[$n]}"; done
} > "$SETDIR/hft_parallel.set"
{
  echo "; auto-generated by pipeline/mt5_parallel.sh"; echo "[Tester]"; echo "Expert=$EXPERT"; echo "ExpertParameters=hft_parallel.set"
  echo "Symbol=$SYMBOL"; echo "Period=H4"; echo "Model=$MODEL"; echo "ExecutionMode=0"
  echo "Optimization=1"; echo "OptimizationCriterion=0"        # 1 = slow complete: every grid combination, one pass per agent
  echo "FromDate=$FROM"; echo "ToDate=$TO"; echo "ForwardMode=0"; echo "Deposit=25000"; echo "Currency=USD"; echo "Leverage=100"
  echo "Visual=0"; echo "ReplaceReport=1"; echo "ShutdownTerminal=1"
} > "$INI_WSL"
rm -f "$MT5_DATA/Tester/cache/$EXPERT".* 2>/dev/null || true
log "grid: $(grep '||Y' "$SETDIR/hft_parallel.set" | tr '\n' ' ')"
log "[Tester] $SYMBOL H4 model=$MODEL $FROM..$TO optimisation (complete) -> $OUT"
mkdir -p "$PAR" "$OUT"
START=$(date +%s); touch -d "@$START" "$OUT/.start"
nohup "$TERMINAL" "/config:$INI_WIN" >/dev/null 2>&1 &
sleep 10; proc_running && log "terminal64 running." || log "WARN: terminal64 not detected yet."
while proc_running; do
  (( $(date +%s) - START >= TIMEOUT )) && { log "TIMEOUT ${TIMEOUT}s - terminal still running; leaving it for the operator."; break; }
  sleep 10
done
log "optimisation finished in $(( $(date +%s) - START ))s"
echo "tag,symbol,journal,rows,params" > "$OUT/index.csv"
n=0
while IFS= read -r d; do
  b=$(basename "$d"); [[ "$b" == "${SYMBOL}_"* ]] || continue
  [[ -n "$(find "$d" -maxdepth 1 -newer "$OUT/.start" -print -quit)" ]] || continue
  rm -rf "$OUT/$b"; cp -r "$d" "$OUT/$b"
  j=$(ls "$OUT/$b"/AA_*.csv 2>/dev/null | grep -vE '\.actions\.|\.inv\.|\.part\.|\.delays\.' | head -1 || true)
  rows=$([[ -n "$j" ]] && echo $(( $(wc -l < "$j") - 1 )) || echo 0)
  echo "${b#${SYMBOL}_},$SYMBOL,$(basename "${j:-none}"),$rows,\"$(tr -d '\r\n' < "$OUT/$b/params.txt" 2>/dev/null)\"" >> "$OUT/index.csv"
  n=$((n+1))
done < <(find "$PAR" -mindepth 1 -maxdepth 1 -type d)
log "$n pass journal(s) collected -> $OUT/index.csv"
(( n > 0 )) || { log "NO pass journals - check the tester's optimisation ran (agents enabled? cache?)"; exit 1; }
