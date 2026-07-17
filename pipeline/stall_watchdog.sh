#!/usr/bin/env bash
# Download stall-killer: watches the active qdmcli download and kills it if it's
# stuck, so the fleet (which fails-and-continues) never wastes hours on a symbol
# Dukascopy is refusing to serve. Non-invasive: only kills a provably-stuck
# `action=update` qdmcli; the orchestrator then logs the failure and moves on.
cd "$(dirname "$0")/.." || exit 1
STALL_MIN=20      # kill if the download's progress line hasn't changed in this long
MAXRUN_MIN=70     # hard cap: no single symbol download should exceed this
LOG=logs/stall_watchdog.log
declare -A lastprog lastchg
echo "$(date '+%F %T') stall-watchdog started (stall>${STALL_MIN}m or run>${MAXRUN_MIN}m)" >> "$LOG"

while pgrep -f fleet_download.py >/dev/null 2>&1; do
  info=$(pgrep -af qdmcli 2>/dev/null | grep -v pgrep | grep 'action=update' | head -1)
  if [ -n "$info" ]; then
    pid=$(awk '{print $1}' <<<"$info")
    sym=$(grep -oE 'symbols=[^ ]+' <<<"$info" | cut -d= -f2)
    et=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' '); : "${et:=0}"
    prog=$(grep -aE 'Writing |Downloading year:' "logs/fleet/${sym}_update.log" 2>/dev/null | tail -1)
    now=$(date +%s)
    if [ "${lastprog[$sym]:-}" != "$prog" ]; then lastprog[$sym]="$prog"; lastchg[$sym]=$now; fi
    stalled=$(( now - ${lastchg[$sym]:-$now} ))
    if (( et > MAXRUN_MIN*60 )) || (( stalled > STALL_MIN*60 )); then
      kill -9 "$pid" 2>/dev/null
      echo "$(date '+%F %T') STALL-KILL $sym pid=$pid elapsed=${et}s no-progress=${stalled}s (last='${prog}') -> fleet fails+continues" >> "$LOG"
      unset "lastprog[$sym]" "lastchg[$sym]"
      sleep 30   # give the orchestrator a moment to advance
    fi
  fi
  sleep 60
done
echo "$(date '+%F %T') stall-watchdog exit (fleet no longer running)" >> "$LOG"
