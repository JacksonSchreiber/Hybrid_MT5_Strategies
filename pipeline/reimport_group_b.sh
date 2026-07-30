#!/usr/bin/env bash
#
# reimport_group_b.sh - recover Group B index/oil symbols (US100/US500/US30/USOIL)
# that group_b_import.sh DOWNLOADED + EXPORTED correctly but then FALSELY rejected
# on an over-narrow price-sanity band.
#
# THE BUG (in group_b_import.sh's do_price_sanity): it checked BOTH the oldest and
# newest tick against one current-level band. But these indices were legitimately
# far lower 15 years ago -- e.g. US100's first tick is 2011-09-19 at 2277.6, a REAL
# Nasdaq-100 level, not a scale error (last tick 28908.9 is the correct 2026 level).
# A power-of-10 scale bug is UNIFORM, so it would shift the RECENT price by 10x too;
# checking the recent price alone catches it. The oldest tick only needs a loose
# floor (catch zero/negative/garbage), not the current-level band.
#
# This re-imports each PRESERVED CSV (group_b_import.sh's fail path does not delete
# it) with the corrected check:
#   - last_bid  (most recent) must sit in the current-level band  [catches x10 bug]
#   - first_bid (oldest)       only needs to exceed a loose floor  [allows low early]
#
# Waits for group_b_import.sh (PID arg, default 885303) to exit, then for terminal64
# to be free, so it never collides with the live run. Idempotent: skips anything
# already in import_state.txt (e.g. USOIL if the main run imported it fine).
#
#   Usage: reimport_group_b.sh [group_b_import_pid]   (default 885303)
#
set -uo pipefail

REPO="/home/jack/hybrid_project"
MT5_READY="$REPO/data/mt5_ready"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
STATUS_FILE="$MT5_DATA/MQL5/Files/import/import_status.txt"
IMPORT_STATE="$REPO/pipeline/import_state.txt"
RETRY_FAILURES="$REPO/pipeline/retry_failures.txt"
IMPORT_SH="$REPO/pipeline/mt5_import.sh"
LOG="$REPO/logs/reimport_group_b.log"
WAIT_PID="${1:-885303}"

MIN_FILE_BYTES=100000
MIN_ROWS=500
MIN_TICKS=1000
LAST_DATE_PREFIX="2026.07"

# base : last_lo last_hi first_floor  (last_bid must be in [lo,hi]; first_bid > floor)
ORDER=(US100 US500 US30 USOIL)
band_for() {
  case "$1" in
    US100) echo "15000 45000 50" ;;
    US500) echo "4000 12000 20" ;;
    US30)  echo "30000 75000 300" ;;
    USOIL) echo "30 200 1" ;;
    *)     echo "0 999999999 0" ;;
  esac
}

log(){ printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }
term_running(){ ps -eo args 2>/dev/null | grep -i terminal64.exe | grep -qv grep; }
already_imported(){ grep -qx "$1" "$IMPORT_STATE" 2>/dev/null; }

log "=== reimport_group_b starting; waiting for group_b_import PID $WAIT_PID to exit ==="
while kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
log "group_b_import PID $WAIT_PID exited."

tries=0
while term_running; do
  # only wait out OUR autoimport terminal; a user session we must not disturb
  if ps -eo args | grep -i terminal64.exe | grep -qv grep && ! ps -eo args | grep -i terminal64.exe | grep -q autoimport_startup.ini; then
    log "a NON-autoimport terminal64 (likely the user's MT5) is open - cannot import safely. Aborting; re-run this script when it's closed."
    exit 1
  fi
  log "autoimport terminal64 still running; waiting to free..."; sleep 15; tries=$((tries+1))
  (( tries > 40 )) && { log "ERROR: terminal64 never freed after ~10min; aborting."; exit 1; }
done

reimport_one() {
  local base="$1" csv="$MT5_READY/$base.csv"
  if already_imported "$base"; then log "$base: already in import_state.txt - skipping."; return 0; fi
  if [[ ! -f "$csv" ]]; then log "$base: no preserved CSV at $csv - skipping (needs re-export via fixed group_b_import.sh)."; return 1; fi

  # --- validate the export (same discipline as group_b_import.sh do_export) ---
  local size rows last_line
  size=$(stat -c%s "$csv"); rows=$(wc -l < "$csv"); last_line=$(tail -1 "$csv")
  if (( size < MIN_FILE_BYTES )); then log "$base: FAIL - CSV too small ($size bytes)."; return 1; fi
  if (( rows < MIN_ROWS )); then log "$base: FAIL - CSV too few rows ($rows)."; return 1; fi
  if [[ "${last_line:0:7}" != "$LAST_DATE_PREFIX" ]]; then log "$base: FAIL - CSV last date implausible: ${last_line:0:20}"; return 1; fi

  # --- CORRECTED price sanity ---
  read -r lo hi floor <<< "$(band_for "$base")"
  local first_bid last_bid
  first_bid=$(head -1 "$csv" | cut -d',' -f2)
  last_bid=$(printf '%s' "$last_line" | cut -d',' -f2)
  if ! [[ "$first_bid" =~ ^-?[0-9.]+$ && "$last_bid" =~ ^-?[0-9.]+$ ]]; then
    log "$base: FAIL - non-numeric bid (first='$first_bid' last='$last_bid')."; return 1
  fi
  if ! awk -v v="$last_bid" -v lo="$lo" -v hi="$hi" 'BEGIN{exit !(v>=lo && v<=hi)}'; then
    log "$base: FAIL - last_bid=$last_bid outside current-level band [$lo,$hi] - genuine scale/data problem, NOT importing."; return 1
  fi
  if ! awk -v v="$first_bid" -v f="$floor" 'BEGIN{exit !(v>f)}'; then
    log "$base: FAIL - first_bid=$first_bid <= floor $floor (garbage/zero/negative), NOT importing."; return 1
  fi
  log "$base: price sanity OK (corrected) - first_bid=$first_bid (floor $floor), last_bid=$last_bid in [$lo,$hi]."

  # --- import via the existing engine ---
  log "$base: importing (memory-safe stage already in stage_csv_for_import.sh)..."
  if ! "$IMPORT_SH" --clean "$base" >> "$LOG" 2>&1; then log "$base: FAIL - mt5_import.sh errored (see above)."; return 1; fi
  [[ -f "$STATUS_FILE" ]] || { log "$base: FAIL - no import_status.txt."; return 1; }
  local line; line=$(grep "^${base},OK," "$STATUS_FILE" || true)
  [[ -n "$line" ]] || { log "$base: FAIL - no '${base},OK,' line in import_status.txt."; return 1; }
  IFS=',' read -r sym st ticks first last secs <<< "$line"
  if ! [[ "$ticks" =~ ^[0-9]+$ ]] || (( ticks < MIN_TICKS )); then log "$base: FAIL - implausible ticks ($ticks)."; return 1; fi
  if [[ "${last:0:3}" != "202" ]]; then log "$base: FAIL - implausible last date ($last)."; return 1; fi

  echo "$base" >> "$IMPORT_STATE"
  if grep -q "^${base}," "$RETRY_FAILURES" 2>/dev/null; then
    grep -v "^${base}," "$RETRY_FAILURES" > "$RETRY_FAILURES.tmp" && mv "$RETRY_FAILURES.tmp" "$RETRY_FAILURES"
    log "$base: cleared stale line(s) from retry_failures.txt."
  fi
  rm -f "$csv"
  log "$base: SUCCESS - $ticks ticks, $first .. $last (${secs}s); imported as ${base}.dk, appended to import_state, CSV deleted."
  return 0
}

for base in "${ORDER[@]}"; do reimport_one "$base" || true; done
log "=== reimport_group_b DONE. import_state now $(grep -c . "$IMPORT_STATE")/49. ==="
