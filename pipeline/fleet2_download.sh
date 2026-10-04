#!/usr/bin/env bash
# fleet2_download.sh - trader 2026-10-04: download every live-universe symbol we have no data for yet (QDM Dukascopy TICK, 2012+) and
# export its M1 CSV - DOWNLOAD ONLY (no MT5 import / AA run). Batches of 4-5 symbols in ONE qdmcli update each (QDM parallelises
# inside one process; one qdmcli at a time - its SQLite store), then data/qdm_csv/fleet/oos_download.sh per batch for the coverage
# check (re-updates a short symbol singly), the M1 export and the done marker. Order = whole groups first (symbol_groups.py).
# Never kills qdmcli (an update commits to the store only on a clean exit). Safe to relaunch: done markers are skipped.
#   setsid nohup pipeline/fleet2_download.sh > data/qdm_csv/fleet/fleet2_driver.out 2>&1 < /dev/null &
set -uo pipefail
QDM=/home/jack/QDM; FLEET=/home/jack/hybrid_project/data/qdm_csv/fleet; LOGD=$FLEET/oos_logs; P=$FLEET/fleet2_progress.log
mkdir -p "$LOGD"
BATCHES=("AUDJPY CADJPY NZDJPY CHFJPY"            # Yen (have USDJPY, EURJPY, GBPJPY)
         "EURCHF GBPCHF AUDCHF NZDCHF CADCHF"     # Swiss franc (have USDCHF)
         "EURAUD EURCAD EURNZD GBPAUD"            # Euro / Sterling crosses (have EURGBP)
         "GBPCAD GBPNZD USDZAR USDSGD"            # rest of the crosses + EM carry (have USDMXN)
         "EURSEK EURNOK USDSEK USDNOK"            # Nordic
         "USDHUF USDPLN EURHUF EURPLN USDCZK")    # Central Europe
prog(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "$P"; }
busy(){ pgrep -x qdmcli >/dev/null 2>&1 || pgrep -f "$QDM/j64" >/dev/null 2>&1; }
free_wait(){ while busy; do sleep 15; done; }
cd "$QDM" || exit 1
prog "=== fleet2_download start (pid $$): ${#BATCHES[@]} batches, download + M1 export only"
for b in "${BATCHES[@]}"; do
  todo=(); for s in $b; do [[ -f "$LOGD/$s.done" ]] || todo+=("$s"); done
  [[ ${#todo[@]} -eq 0 ]] && { prog "batch [$b]: all done - skipped"; continue; }
  csv=$(IFS=,; echo "${todo[*]}")
  free_wait; prog "batch [${todo[*]}]: ONE qdmcli update start (log oos_logs/fleet2_${todo[0]}_batch.log)"
  ./qdmcli -data action=update symbols="$csv" </dev/null > "$LOGD/fleet2_${todo[0]}_batch.log" 2>&1; rc=$?
  prog "batch [${todo[*]}]: update finished rc=$rc - coverage check + M1 export per symbol (oos_download.sh)"
  SYMS="${todo[*]}" bash "$FLEET/oos_download.sh"
  for s in "${todo[@]}"; do
    if [[ -f "$LOGD/$s.done" ]]; then prog "$s: DONE - $(ls "$FLEET/$s-M1-No Session.csv" 2>/dev/null >/dev/null && echo "M1 CSV $(du -h "$FLEET/$s-M1-No Session.csv" | cut -f1)" || echo "no M1 CSV?")"
    else prog "$s: NOT done (see oos_batch.log)"; fi
  done
done
prog "=== fleet2_download end"
