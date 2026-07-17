#!/usr/bin/env bash
#
# rolling_import.sh - drains data/mt5_ready CSVs into MT5 as the fleet
# download pipeline (fleet_download.py) produces them, running IN PARALLEL
# with it. Imports one symbol at a time (never stage more than one big CSV
# at once - see mt5_import.sh/stage_csv_for_import.sh for why: staging COPIES
# the CSV, temporarily doubling its disk footprint).
#
# Per symbol that is complete in pipeline/fleet_state.txt but not yet in
# pipeline/import_state.txt:
#   1. ./mt5_import.sh --clean <BASE>   (stages, imports via AutoImport EA
#      with sessions, cleans the STAGED copy on success)
#   2. Verify success directly from the real import_status.txt (not just
#      mt5_import.sh's exit code): a `<BASE>,OK,...` line with a plausible
#      tick count and last-date.
#   3. Only then delete the SOURCE data/mt5_ready/<BASE>.csv (mt5_import.sh
#      --clean only cleans the staged copy under MQL5\Files\import, not
#      this source file - that's this script's job) and append <BASE> to
#      import_state.txt.
#   4. On any failure: log to import_failures.txt, leave the source CSV in
#      place (so it's retried on our next pass), and continue.
#
# Keeps polling/draining until every symbol currently in fleet_state.txt has
# been imported AND fleet_download.py is no longer running (i.e. downloads
# are done and the backlog is fully drained). Sleeps ~60s when caught up
# with nothing new to do but the fleet is still running.
#
# Real disk guard: pauses (polls, does not abort) if Windows host C: free
# space drops below DISK_GUARD_MIN_GB - checked via `df -BG /mnt/c`, which
# is the true constraint (WSL's own `df /` reports space inside the vhdx's
# current allocation, not real host disk - see pipeline/fleet_download.py's
# module docstring for the full explanation). This driver is a net disk
# *reclaimer* (deletes a multi-GB source CSV per successful import), so
# tripping this guard should be rare - it's a belt-and-suspenders check.
#
# Usage:
#   nohup /home/jack/hybrid_project/pipeline/rolling_import.sh \
#       > /home/jack/hybrid_project/logs/rolling_import.log 2>&1 &
#
set -uo pipefail   # deliberately no -e: failures are handled explicitly per
                   # symbol so one bad import never kills the whole driver.

REPO="/home/jack/hybrid_project"
STATE_FILE="$REPO/pipeline/fleet_state.txt"
IMPORT_STATE="$REPO/pipeline/import_state.txt"
IMPORT_FAILURES="$REPO/pipeline/import_failures.txt"
MT5_READY="$REPO/data/mt5_ready"
MT5_IMPORT_SH="$REPO/pipeline/mt5_import.sh"
LOG_DIR="$REPO/logs/rolling_import"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
STATUS_FILE="$MT5_DATA/MQL5/Files/import/import_status.txt"

DISK_GUARD_MIN_GB=50
DISK_GUARD_POLL_SEC=600   # recheck every 10 min while paused on low disk
POLL_IDLE_SEC=60           # recheck every 60s when caught up but fleet still running
MIN_PLAUSIBLE_TICKS=1000   # floor for "plausible tick count" sanity check

mkdir -p "$LOG_DIR"
touch "$IMPORT_STATE" "$IMPORT_FAILURES"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

free_gb_windows_host() {
  # Real Windows host C: free space, in whole GB. NOT WSL's own `df /` -
  # that reports space inside the vhdx's current allocation, which is
  # fiction relative to the real constraint (see module header comment).
  df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc '0-9'
}

wait_for_disk() {
  local free
  free=$(free_gb_windows_host)
  if (( free >= DISK_GUARD_MIN_GB )); then
    return 0
  fi
  log "C: free ${free}GB < ${DISK_GUARD_MIN_GB}GB guard threshold - pausing (polling every $((DISK_GUARD_POLL_SEC/60)) min)..."
  while (( free < DISK_GUARD_MIN_GB )); do
    sleep "$DISK_GUARD_POLL_SEC"
    free=$(free_gb_windows_host)
    log "  ... rechecked: C: free ${free}GB"
  done
  log "C: free recovered to ${free}GB - resuming."
}

state_names() {
  # First comma-delimited field of every non-empty line in $1.
  [[ -f "$1" ]] && cut -d',' -f1 "$1" | sed '/^$/d'
}

fleet_running() {
  pgrep -f "fleet_download\.py" > /dev/null 2>&1
}

import_one() {
  local base="$1"
  log "=== $base: starting import ==="

  local csv="$MT5_READY/$base.csv"
  if [[ ! -f "$csv" ]]; then
    log "$base: SKIP - source CSV not found at $csv"
    echo "$base,$(date -Iseconds),source CSV missing at import time" >> "$IMPORT_FAILURES"
    return
  fi

  local before_gb after_gb
  before_gb=$(free_gb_windows_host)

  local runlog="$LOG_DIR/${base}.log"
  if ! "$MT5_IMPORT_SH" --clean "$base" > "$runlog" 2>&1; then
    log "$base: mt5_import.sh exited non-zero - see $runlog"
    echo "$base,$(date -Iseconds),mt5_import.sh failed; see $runlog" >> "$IMPORT_FAILURES"
    return
  fi

  # Don't just trust the exit code - parse the real status file for a
  # plausible OK line, same spirit as fleet_download.py's validate_export.
  if [[ ! -f "$STATUS_FILE" ]]; then
    log "$base: FAIL - no import_status.txt found after run"
    echo "$base,$(date -Iseconds),no import_status.txt after run" >> "$IMPORT_FAILURES"
    return
  fi

  local line
  line=$(grep "^${base},OK," "$STATUS_FILE" || true)
  if [[ -z "$line" ]]; then
    log "$base: FAIL - no '${base},OK,' line in import_status.txt"
    echo "$base,$(date -Iseconds),no OK line in import_status.txt" >> "$IMPORT_FAILURES"
    return
  fi

  IFS=',' read -r sym status ticks first last seconds <<< "$line"
  if ! [[ "$ticks" =~ ^[0-9]+$ ]] || (( ticks < MIN_PLAUSIBLE_TICKS )); then
    log "$base: FAIL - implausible tick count ('$ticks' < $MIN_PLAUSIBLE_TICKS)"
    echo "$base,$(date -Iseconds),implausible tick count: $ticks" >> "$IMPORT_FAILURES"
    return
  fi
  if [[ -z "$last" || "${last:0:3}" != "202" ]]; then
    log "$base: FAIL - implausible last-date ('$last')"
    echo "$base,$(date -Iseconds),implausible last date: $last" >> "$IMPORT_FAILURES"
    return
  fi

  log "$base: import verified OK - $ticks ticks, $first .. $last (${seconds}s)"

  rm -f "$csv"
  after_gb=$(free_gb_windows_host)
  echo "$base" >> "$IMPORT_STATE"
  log "$base: SUCCESS - source CSV deleted, C: free ${before_gb}GB -> ${after_gb}GB"
}

log "Rolling import driver starting."
log "Seeded/prior imports: $(state_names "$IMPORT_STATE" | tr '\n' ' ')"

while true; do
  mapfile -t fleet_done < <(state_names "$STATE_FILE")
  mapfile -t imported < <(state_names "$IMPORT_STATE")

  declare -A imported_set=()
  for s in "${imported[@]}"; do imported_set["$s"]=1; done

  backlog=()
  for s in "${fleet_done[@]}"; do
    [[ -n "${imported_set[$s]:-}" ]] || backlog+=("$s")
  done

  if [[ ${#backlog[@]} -eq 0 ]]; then
    if ! fleet_running; then
      log "Backlog empty and fleet_download.py is no longer running - all done. Exiting."
      break
    fi
    log "Backlog empty, fleet still running - sleeping ${POLL_IDLE_SEC}s..."
    sleep "$POLL_IDLE_SEC"
    unset imported_set
    continue
  fi

  log "Backlog: ${#backlog[@]} symbol(s) -> ${backlog[*]}"
  for base in "${backlog[@]}"; do
    wait_for_disk
    import_one "$base"
  done
  unset imported_set
done

log "Rolling import driver finished."
