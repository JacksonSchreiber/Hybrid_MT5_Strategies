#!/usr/bin/env bash
# fleet2_aa_run.sh - trader 2026-10-04 "start running mt5 whenever ready and have it work at the same time": drives
# pipeline/oos_mt5_stage.sh --fleet2 over the 26 fleet2 symbols (docs/new_symbols_prereg_2026-10-04.md), one MT5 job at a time.
#   Phase A (starts now): the 13 symbols whose MT5 custom copy already covers the window (pre-marked 'imported' in
#            data/study/fleet2_aa/state/) - --steps aa only: no QDM, no tick CSV, no disk growth, runs alongside the downloads.
#   Phase B: the 13 short-history symbols - export,h4,import,aa (extends them to 2012) - only after the fleet2-download service has
#            finished, so QDM is idle (one qdmcli at a time); one symbol at a time; the stage deletes each tick CSV after import and
#            stops itself if C: is short of space.
# terminal64 open (the trader) -> WAIT and resume (never kill). Any other stage stop (timeout, disk) -> the driver stops and logs it.
# The 9 reserved symbols' journals are produced but never opened; no study result is computed here.
#   setsid nohup pipeline/fleet2_aa_run.sh > data/study/fleet2_aa/driver.out 2>&1 < /dev/null &
set -uo pipefail
REPO=/home/jack/hybrid_project; cd "$REPO" || exit 1
OUT=data/study/fleet2_aa; P="$OUT/progress.log"; mkdir -p "$OUT"
prog(){ printf '[%s] driver: %s\n' "$(date '+%F %T')" "$*" >> "$P"; }
mt5_running(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }
wait_mt5(){ local n=0; while mt5_running; do (( n++ % 6 == 0 )) && prog "terminal64 is open (the trader?) - waiting, never killing"; sleep 300; done; }
run_stage(){   # stage args -> 0 when the stage completed; retries after a terminal64 pause
  local tries=0 rc
  while :; do
    wait_mt5
    bash pipeline/oos_mt5_stage.sh --fleet2 "$@" >> "$OUT/stage.out" 2>&1; rc=$?
    (( rc == 0 )) && return 0
    if tail -3 "$OUT/stage.log" | grep -q "terminal64 appeared\|terminal64.exe is running"; then
      prog "stage paused (terminal64 opened) - resuming when it closes"; (( ++tries > 48 )) && return 1; continue; fi
    prog "stage stopped rc=$rc - see $OUT/stage.log; the driver stops (resumable: re-run this script)"; return 1
  done
}
A="CADJPY CHFJPY EURCHF GBPCHF NZDCHF EURCAD EURNZD GBPCAD GBPNZD EURSEK EURHUF EURPLN USDZAR"
B="AUDJPY NZDJPY AUDCHF CADCHF EURAUD GBPAUD USDSGD EURNOK USDSEK USDNOK USDHUF USDPLN USDCZK"
prog "=== start (pid $$) - phase A (--steps aa): $A"
t0=$(date +%s)
run_stage --steps aa $A || { prog "phase A stopped"; exit 1; }
prog "phase A finished in $(( ($(date +%s) - t0) / 60 )) min; phase B waits for the fleet2-download service to finish (QDM idle)"
while systemctl --user is-active --quiet fleet2-download; do sleep 600; done
prog "downloads finished - phase B (export,h4,import,aa), one symbol at a time: $B"
for s in $B; do run_stage --steps export,h4,import,aa "$s" || { prog "phase B stopped at $s"; exit 1; }; done
prog "=== done in $(( ($(date +%s) - t0) / 60 )) min - failed/skipped per the stage log: $(grep 'stage finished' "$OUT/stage.log" | tail -1 | sed 's/.*failed\/skipped: //')"
