#!/usr/bin/env bash
#
# us30_supervisor.sh - make the US30 direct-feed backfill self-healing.
#
# The backfill (pipeline/backfill_us30.py) fetches ~47k hourly tick files from
# Dukascopy's flaky feed. It stalls in MULTIPLE modes: black-hole connections
# (sockets open, no bytes - the engineer added per-request timeouts for this) AND
# a 0-socket event-loop freeze (no requests in flight at all - a per-request
# timeout can't help that). Rather than chase every internal stall mode, this
# supervisor watches externally and RESTARTS the backfill whenever it stops making
# progress. Restart is SAFE + cheap: the backfill commits each fetched file to
# data/us30_backfill_cache/ and tracks completion in its sqlite manifest, so a
# relaunch resumes from where it left off with zero re-fetch and zero data loss.
#
# Progress signal = newest file mtime under the cache dir. If it hasn't advanced
# in STALL_SEC, kill + relaunch. Stops when US30 lands in import_state.txt.
#
# During the DECODE/IMPORT phase (after all slots are fetched) the cache
# legitimately stops growing - the supervisor detects that via the log and stops
# restarting so it doesn't interrupt CSV-build/import.
#
#   Usage: nohup bash pipeline/us30_supervisor.sh >> logs/us30_supervisor.log 2>&1 &
#
set -uo pipefail

REPO=/home/jack/hybrid_project
PY=/home/jack/trading-backtest/.venv/bin/python3
SCRIPT="$REPO/pipeline/backfill_us30.py"
LOG="$REPO/logs/backfill_us30.log"
SUPLOG="$REPO/logs/us30_supervisor.log"
CACHE="$REPO/data/us30_backfill_cache"
CSV="$REPO/data/mt5_ready/US30.csv"
STATE="$REPO/pipeline/import_state.txt"

STALL_SEC=240      # no cache growth for 4 min => restart (fetch phase only)
POLL=30
MAX_RESTARTS=400   # backstop; ~ enough to grind through the flaky feed

log(){ printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$SUPLOG"; }
newest_cache_epoch(){ find "$CACHE" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1; }
backfill_pid(){ pgrep -f 'backfill_us30\.py' 2>/dev/null | head -1; }
launch(){ ( cd "$REPO" && nohup "$PY" "$SCRIPT" >> "$LOG" 2>&1 & ) ; log "launched backfill (pid $(sleep 2; backfill_pid))"; }
in_decode_or_import(){ [[ -f "$CSV" ]] && return 0; grep -aiE 'decode phase|building CSV|import phase|import OK|mt5_import' "$LOG" 2>/dev/null | grep -q . ; }

restarts=0
log "=== us30_supervisor starting (STALL_SEC=$STALL_SEC) ==="
while true; do
  if grep -qx US30 "$STATE" 2>/dev/null; then log "US30 present in import_state.txt - COMPLETE. Supervisor exiting."; break; fi

  pid=$(backfill_pid)
  if [[ -z "$pid" ]]; then
    if in_decode_or_import; then
      log "backfill not running but decode/import markers present - assuming it finished the run; waiting for import to land in state."
    else
      log "backfill not running - launching."
      launch
    fi
    sleep "$POLL"; continue
  fi

  if in_decode_or_import; then
    # cache legitimately idle during decode/import - do not restart
    sleep "$POLL"; continue
  fi

  now=$(date +%s); nc=$(newest_cache_epoch); : "${nc:=$now}"
  age=$(( now - nc ))
  if (( age > STALL_SEC )); then
    if (( restarts >= MAX_RESTARTS )); then
      log "STALL (age ${age}s) but hit MAX_RESTARTS=$MAX_RESTARTS - stopping supervisor, needs a human look."
      break
    fi
    restarts=$(( restarts + 1 ))
    log "STALL: no cache growth for ${age}s (backfill pid $pid) - restart #$restarts (resumable)."
    kill "$pid" 2>/dev/null; sleep 3
    kill -0 "$pid" 2>/dev/null && { kill -9 "$pid" 2>/dev/null; sleep 2; }
    launch
    sleep 60   # give the fresh process time to ramp before re-evaluating
  fi
  sleep "$POLL"
done
log "=== us30_supervisor DONE (restarts=$restarts) ==="
