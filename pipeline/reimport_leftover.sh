#!/usr/bin/env bash
#
# reimport_leftover.sh - recover symbols that DOWNLOADED + EXPORTED OK but whose
# MT5 import failed, leaving their CSV behind in data/mt5_ready/ (retry_batch.sh
# deletes the source CSV only on a verified-successful import, so any leftover
# there == a failed import). The known case: USDCNH (12.7 GB) died on the pre-fix
# ENOMEM bulk-copy during staging; stage_csv_for_import.sh is now memory-safe
# (dd oflag=sync), so a re-import succeeds.
#
# It WAITS for retry_batch.sh (or any given PID) to exit and for terminal64 to be
# free before touching MT5, so it never collides with the live pipeline. Then it
# re-imports each leftover CSV, verifies via the real import_status.txt, appends
# to import_state.txt, clears the symbol's retry_failures.txt line, and deletes
# the source CSV to reclaim disk.
#
#   Usage: reimport_leftover.sh [retry_batch_pid]   (default PID 774283)
#
set -uo pipefail

REPO="/home/jack/hybrid_project"
MT5_READY="$REPO/data/mt5_ready"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
STATUS_FILE="$MT5_DATA/MQL5/Files/import/import_status.txt"
IMPORT_STATE="$REPO/pipeline/import_state.txt"
RETRY_FAILURES="$REPO/pipeline/retry_failures.txt"
IMPORT_SH="$REPO/pipeline/mt5_import.sh"
LOG="$REPO/logs/reimport_leftover.log"
WAIT_PID="${1:-774283}"
MIN_TICKS=1000000

log(){ printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }
term_running(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }

log "=== reimport_leftover starting; waiting for pipeline PID $WAIT_PID to exit ==="
while kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
log "pipeline PID $WAIT_PID exited."

tries=0
while term_running; do
  log "terminal64 still running; waiting for it to free..."
  sleep 20; tries=$((tries+1))
  if (( tries > 90 )); then log "ERROR: terminal64 never freed after ~30min; aborting."; exit 1; fi
done

shopt -s nullglob
csvs=("$MT5_READY"/*.csv)
if (( ${#csvs[@]} == 0 )); then
  log "no leftover CSVs in mt5_ready - nothing to re-import. DONE ($(grep -c . "$IMPORT_STATE")/49)."
  exit 0
fi
log "leftover CSV(s) to re-import: ${csvs[*]}"

for csv in "${csvs[@]}"; do
  base=$(basename "$csv" .csv)
  if grep -qx "$base" "$IMPORT_STATE"; then
    log "$base: already in import_state.txt; removing stale source CSV only."
    rm -f "$csv"; continue
  fi
  log "$base: re-importing via memory-safe stage ($(numfmt --to=iec "$(stat -c%s "$csv")" 2>/dev/null || stat -c%s "$csv"))..."
  if ! "$IMPORT_SH" --clean "$base" >> "$LOG" 2>&1; then
    log "$base: FAIL - mt5_import.sh errored (see above); leaving CSV in place."; continue
  fi
  if [[ ! -f "$STATUS_FILE" ]]; then log "$base: FAIL - no import_status.txt after run."; continue; fi
  line=$(grep "^${base},OK," "$STATUS_FILE" || true)
  if [[ -z "$line" ]]; then log "$base: FAIL - no '${base},OK,' line in import_status.txt."; continue; fi
  IFS=',' read -r sym st ticks first last secs <<< "$line"
  if ! [[ "$ticks" =~ ^[0-9]+$ ]] || (( ticks < MIN_TICKS )); then log "$base: FAIL - implausible tick count ($ticks)."; continue; fi
  if [[ "${last:0:3}" != "202" ]]; then log "$base: FAIL - implausible last date ($last)."; continue; fi

  echo "$base" >> "$IMPORT_STATE"
  if [[ -f "$RETRY_FAILURES" ]] && grep -q "^${base}," "$RETRY_FAILURES"; then
    grep -v "^${base}," "$RETRY_FAILURES" > "$RETRY_FAILURES.tmp" && mv "$RETRY_FAILURES.tmp" "$RETRY_FAILURES"
    log "$base: cleared recovered symbol from retry_failures.txt."
  fi
  rm -f "$csv"
  log "$base: SUCCESS - $ticks ticks, $first .. $last (${secs}s); appended to import_state, source CSV deleted."
done
log "=== reimport_leftover DONE. import_state now $(grep -c . "$IMPORT_STATE")/49. ==="
