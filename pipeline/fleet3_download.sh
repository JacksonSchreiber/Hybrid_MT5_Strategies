#!/usr/bin/env bash
# fleet3_download.sh - trader 2026-10-05: "in the background continue downloading symbols we don't have yet". After fleet2 (every FX
# pair on the account except USDZAR), this adds instruments OUTSIDE the account list for group-behaviour research: European/Asian
# indices, commodities, metals, more FX - QDM Dukascopy TICK from 2012, M1 CSV export only (no MT5 import / AA run).
# - waits for fleet2 to finish (its unit inactive), one qdmcli at a time, never kills qdmcli (an update commits only on a clean exit)
# - YIELDS to the MT5 AA stage: while data/qdm_csv/fleet/QDM_YIELD exists, it doesn't start the next qdmcli call
# - DISK GUARD: pauses while C: has under 45 GB free (the MT5 re-imports need tick CSV x 1.1 + 30 GB)
# - each code is registered with `-symbol action=add` and confirmed in `-symbol action=list`; codes Dukascopy lacks are skipped
# - done markers are shared with fleet2 (oos_logs/<SYM>.done), so a relaunch skips finished symbols
#   run as a capped user service (see the memory note on heavy jobs)
set -uo pipefail
QDM=/home/jack/QDM; FLEET=/home/jack/hybrid_project/data/qdm_csv/fleet; LOGD=$FLEET/oos_logs; P=$FLEET/fleet3_progress.log
YIELD=$FLEET/QDM_YIELD; MIN_FREE_GB=45
mkdir -p "$LOGD"
BATCHES=("DEUIDXEUR GBRIDXGBP JPNIDXJPY FRAIDXEUR"                 # DAX, FTSE 100, Nikkei 225, CAC 40
         "EUSIDXEUR AUSIDXAUD HKGIDXHKD CHEIDXCHF"                 # Euro Stoxx 50, ASX 200, Hang Seng, SMI
         "BRENTCMDUSD GASCMDUSD COPPERCMDUSD XPTCMDUSD XPDCMDUSD"   # Brent, natural gas, copper, platinum, palladium
         "USDTRY EURTRY USDHKD USDCNH"
         "USDDKK EURDKK USDILS ESPIDXEUR"                          # + IBEX 35
         "USDZAR")                                                 # retry (fleet2 dropped it on Dukascopy HTTP 429)
prog(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "$P"; }
busy(){ pgrep -x qdmcli >/dev/null 2>&1 || pgrep -f "^$QDM/j64" >/dev/null 2>&1; }
free_gb(){ df -BG --output=avail /mnt/c | tail -1 | tr -dc '0-9'; }
gate(){   # wait until QDM is idle, the MT5 stage doesn't want it, and C: has room
  local said=0
  while busy || [[ -f "$YIELD" ]] || (( $(free_gb) < MIN_FREE_GB )); do
    if (( ! said )); then prog "waiting: qdm busy=$(busy && echo yes || echo no) yield=$([[ -f $YIELD ]] && echo yes || echo no) C: free $(free_gb) GB"; said=1; fi
    sleep 60
  done
}
registered(){ ./qdmcli -symbol action=list </dev/null 2>/dev/null | grep -q "^$1,"; }
cd "$QDM" || exit 1
prog "=== fleet3_download start (pid $$): waiting for fleet2 to finish"
while systemctl --user is-active --quiet fleet2-download; do sleep 120; done
prog "fleet2 finished; ${#BATCHES[@]} batches"
for b in "${BATCHES[@]}"; do
  todo=()
  for s in $b; do
    [[ -f "$LOGD/$s.done" ]] && continue
    gate
    if ! registered "$s"; then
      ./qdmcli -symbol action=add "symbols=$s" datasource=dukascopy datatype=TICK </dev/null > "$LOGD/${s}_add.log" 2>&1
      gate
      if registered "$s"; then prog "$s: registered in QDM"; else prog "$s: SKIPPED - Dukascopy/QDM did not accept the code (see oos_logs/${s}_add.log)"; continue; fi
    fi
    todo+=("$s")
  done
  [[ ${#todo[@]} -eq 0 ]] && { prog "batch [$b]: nothing to do"; continue; }
  csv=$(IFS=,; echo "${todo[*]}")
  gate; prog "batch [${todo[*]}]: ONE qdmcli update start (log oos_logs/fleet3_${todo[0]}_batch.log)"
  ./qdmcli -data action=update symbols="$csv" </dev/null > "$LOGD/fleet3_${todo[0]}_batch.log" 2>&1; rc=$?
  n429=$(grep -c 'Error code: 429' "$LOGD/fleet3_${todo[0]}_batch.log")
  prog "batch [${todo[*]}]: update finished rc=$rc ($n429 rate-limit errors) - coverage check + M1 export per symbol"
  gate; SYMS="${todo[*]}" bash "$FLEET/oos_download.sh"
  for s in "${todo[@]}"; do
    if [[ -f "$LOGD/$s.done" ]]; then prog "$s: DONE - $(ls "$FLEET/$s-M1-No Session.csv" >/dev/null 2>&1 && echo "M1 CSV $(du -h "$FLEET/$s-M1-No Session.csv" | cut -f1)" || echo "no M1 CSV?")"
    else prog "$s: NOT done (see oos_batch.log)"; fi
  done
done
prog "=== fleet3_download end"
