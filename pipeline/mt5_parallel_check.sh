#!/usr/bin/env bash
# mt5_parallel_check.sh - first run of the parallel tester (2026-10-03): build, a 4-pass smoke test with a RAM log, then the queue
# reproduction (EURUSD, off/A/B/I and their combinations = 8 passes) as ONE parallel job, compared with today's serial journals.
#   pipeline/mt5_parallel_check.sh [--wait-stage]      (--wait-stage: wait for the OOS stage to finish and MT5 to close first)
set -u
cd "$(dirname "$0")/.."
OUT=data/study/par; mkdir -p $OUT
log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
mt5_up(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }
if [[ "${1:-}" == "--wait-stage" ]]; then
  log "waiting for the OOS stage to finish and terminal64 to close ..."
  while pgrep -f "bash pipeline/oos_mt5_stage.sh" >/dev/null || mt5_up; do sleep 30; done
  log "MT5 free"
fi
mt5_up && { log "terminal64 running - refusing"; exit 1; }
ramlog(){   # every 20 s: free RAM (MB) and the summed working set of the tester agents
  while :; do
    powershell.exe -NoProfile -Command '$f=[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024); $a=Get-Process metatester64 -ErrorAction SilentlyContinue; "{0},{1},{2}" -f $f, ($a|Measure-Object).Count, [math]::Round((($a|Measure-Object WorkingSet64 -Sum).Sum)/1MB)' 2>/dev/null | tr -d '\r' | sed "s/^/$(date +%T),/"
    sleep 20
  done; }

log "=== build"; B=$(pipeline/mt5_build_ea.sh 2>&1); echo "$B" | tail -2
[[ $(echo "$B" | grep -c "0 errors") -ge 2 ]] || { log "BUILD FAILED"; exit 1; }

log "=== smoke: 4 identical passes, EURUSD 2024H1, real ticks"
ramlog > $OUT/smoke_ram.csv & RP=$!
S0=$(date +%s)
STAGED=1 PYR=1 TC_BANK=0.25 SHORT_RAISE=0.25 pipeline/mt5_parallel.sh --symbol EURUSD.dk --from 2024.01.01 --to 2024.06.30 --model 4 \
  --grid InpParIdx=0:1:3 --out $OUT/smoke 2>&1 | tail -4
kill $RP 2>/dev/null
log "smoke wall time $(( $(date +%s) - S0 ))s; RAM log (time,free MB,agents,agents MB):"; sort -t, -k2 -n $OUT/smoke_ram.csv | head -1 | sed 's/^/  lowest free: /'
awk -F, 'NR>1{print $4}' $OUT/smoke/index.csv | sort | uniq -c | sed 's/^/  journal rows per pass: /'

log "=== serial reference: one pass alone, same window (for the speed-up)"
S1=$(date +%s)
STAGED=1 PYR=1 TC_BANK=0.25 SHORT_RAISE=0.25 pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --symbol EURUSD.dk \
  --from 2024.01.01 --to 2024.06.30 --model 4 --timeout 3600 > $OUT/smoke_serial.log 2>&1
log "single run wall time $(( $(date +%s) - S1 ))s"

log "=== queue reproduction as one parallel job: InpAddGate x InpShortCutAvg x InpImprovingAdd = 8 passes"
ramlog > $OUT/queue_ram.csv & RP=$!
S2=$(date +%s)
MAX_PAR=8 STAGED=1 PYR=1 TC_BANK=0.25 SHORT_RAISE=0.25 pipeline/mt5_parallel.sh --symbol EURUSD.dk --from 2016.08.01 --to 2026.06.30 --model 4 \
  --grid InpAddGate=bool --grid InpShortCutAvg=0:0.5:0.5 --grid InpImprovingAdd=0:0.5:0.5 --out $OUT/queue_eurusd 2>&1 | tail -4
kill $RP 2>/dev/null
log "queue job wall time $(( $(date +%s) - S2 ))s (serial: 4 runs took ~$(( 4 * 15 )) min); lowest free RAM:"; sort -t, -k2 -n $OUT/queue_ram.csv | head -1
python3 pipeline/mt5_parallel_compare.py $OUT/queue_eurusd data/study/queue_repro EURUSD.dk
log "=== DONE"
