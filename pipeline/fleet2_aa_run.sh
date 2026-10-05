#!/usr/bin/env bash
# fleet2_aa_run.sh - trader 2026-10-04 "start running mt5 whenever ready and have it work at the same time": drives
# pipeline/oos_mt5_stage.sh --fleet2 over the 26 fleet2 symbols (docs/new_symbols_prereg_2026-10-04.md), one MT5 job at a time.
#   Phase A (starts now): the 13 symbols whose MT5 custom copy already covers the window (pre-marked 'imported' in
#            data/study/fleet2_aa/state/) - --steps aa only: no QDM, no tick CSV, no disk growth, runs alongside the downloads.
#   Phase B: the short-history symbols - export,h4,import,aa (extends them to 2012) - only after the fleet2-download service has
#            finished, so QDM is idle (one qdmcli at a time); one symbol at a time; the stage deletes each tick CSV after import and
#            stops itself if C: is short of space. USD-base pairs FIRST: they are the profit-currency conversion symbols of the EUR
#            exotics (USDSEK for EURSEK, USDNOK for EURNOK, USDHUF for EURHUF, USDPLN for EURPLN).
#   Phase C: EURSEK, EURHUF, EURPLN - aa only on their long MT5 copies, AFTER their conversion pairs are extended.
# CONVERSION (measured on phase A, 2026-10-04): a missing PROFIT-currency conversion symbol stops the tester producing signals - the
#   JPY crosses yield 2020+ only, because USDJPY.dk (an ORIGINAL symbol, never touched here) has MT5 history from 2020-01; so AUDJPY and
#   NZDJPY are not re-imported (phase A2 runs their 2020+ copies). A missing MARGIN-currency symbol does NOT: EURUSD.dk (2016-07+) /
#   GBPUSD.dk (2013-07+) only log "no prices" warnings - EURCHF/EURNZD/GBPCAD/GBPNZD have full 2012-26 signals with ~95% filled in both
#   periods. Hence the order: USD-base exotics (USDSEK/USDNOK/USDHUF/USDPLN = the profit conversion of EURSEK/EURNOK/EURHUF/EURPLN) first.
# QDM sharing (coordinator 2026-10-04): a third downloader (fleet3-download) keeps QDM busy after fleet2. Before each export the
#   driver touches data/qdm_csv/fleet/QDM_YIELD (fleet3 starts no new qdmcli call while it exists), waits until qdmcli is idle
#   (anchored checks; never kills a call in progress), runs export+h4 only, then removes the file - QDM is held for the export, not
#   for the import or the tester run.
# terminal64 open (the trader) -> WAIT and resume (never kill). Any other stage stop (timeout, disk) -> the driver stops and logs it.
# The 9 reserved symbols' journals are produced but never opened; no study result is computed here.
#   setsid nohup pipeline/fleet2_aa_run.sh > data/study/fleet2_aa/driver.out 2>&1 < /dev/null &
set -uo pipefail
REPO=/home/jack/hybrid_project; cd "$REPO" || exit 1
OUT=data/study/fleet2_aa; P="$OUT/progress.log"; mkdir -p "$OUT"
YIELD=data/qdm_csv/fleet/QDM_YIELD
qdm_busy(){ pgrep -x qdmcli >/dev/null 2>&1 || pgrep -f "^/home/jack/QDM/j64" >/dev/null 2>&1; }
trap 'rm -f "$REPO/$YIELD"' EXIT
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
A2="AUDJPY NZDJPY"
B="USDSGD USDSEK USDNOK USDHUF USDPLN USDCZK AUDCHF CADCHF EURAUD GBPAUD EURNOK"
C="EURSEK EURHUF EURPLN"
t0=$(date +%s)
# phase A (CADJPY CHFJPY EURCHF GBPCHF NZDCHF EURCAD EURNZD GBPCAD GBPNZD USDZAR; EURSEK/EURHUF/EURPLN unmarked -> skipped) may still be
# running from the first driver: wait for it (one MT5 job at a time)
while pgrep -f "^bash pipeline/oos_mt5_stage.sh --fleet2" >/dev/null; do sleep 120; done
prog "=== driver v2 (pid $$) - phase A2 (--steps aa, 2020+ copies): $A2"
run_stage --steps aa $A2 || { prog "phase A2 stopped"; exit 1; }
prog "phase A finished in $(( ($(date +%s) - t0) / 60 )) min; phase B waits for the fleet2-download service to finish (QDM idle)"
while systemctl --user is-active --quiet fleet2-download; do sleep 600; done
prog "downloads finished - phase B (export,h4,import,aa), one symbol at a time: $B"
for s in $B; do
  if [[ ! -f "$OUT/state/$s.h4" ]]; then
    touch "$YIELD"; n=0; while qdm_busy; do (( n++ % 10 == 0 )) && prog "$s: QDM_YIELD set - waiting for the current qdmcli call to finish"; sleep 30; done
    run_stage --steps export,h4 "$s"; rc=$?; rm -f "$YIELD"
    (( rc == 0 )) || { prog "phase B stopped at $s (export/h4)"; exit 1; }
  fi
  run_stage --steps import,aa "$s" || { prog "phase B stopped at $s (import/aa)"; exit 1; }
done
for s in $C; do
  c="USD${s#EUR}"
  if [[ -f "$OUT/state/$c.imported" && -f "$OUT/state/$c.exported" ]]; then
    echo "$(date -Iseconds) pre-existing MT5 custom copy $s.dk (long history); its conversion pair $c.dk was extended by this stage" > "$OUT/state/$s.imported"
    prog "phase C: $s aa only (conversion pair $c extended)"; run_stage --steps aa "$s" || { prog "phase C stopped at $s"; exit 1; }
  else prog "phase C: $s NOT run - its conversion pair $c was not extended (see the stage log)"; fi
done
prog "=== done in $(( ($(date +%s) - t0) / 60 )) min - failed/skipped per the stage log: $(grep 'stage finished' "$OUT/stage.log" | tail -1 | sed 's/.*failed\/skipped: //')"
