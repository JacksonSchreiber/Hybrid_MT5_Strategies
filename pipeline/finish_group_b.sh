#!/usr/bin/env bash
#
# finish_group_b.sh - robust, correct finisher for the 4 index/oil symbols,
# SUPERSEDING the buggy group_b_import.sh (which 99%-tail-false-killed US500,
# orphaned its qdmcli via an incomplete kill, and then false-skipped US30 when
# the orphan made a -symbol list query fail).
#
# CORE INVARIANT: drive each symbol's action off its ACTUAL store coverage, not a
# blunt record count, and treat a finishing-tail stall (>=97%, i.e. qdmcli chasing
# the newest not-yet-published day past our DATETO) as SUCCESS, never FAIL.
#
# Per symbol (US100 US500 US30 USOIL):
#   - already in import_state.txt            -> skip
#   - a valid preserved CSV already on disk  -> reuse it (US100's 24GB export)
#   - else store has records                 -> export from store
#   - else store empty (US30, false-skipped) -> download (finishing-tail-aware
#                                               guard + PROPER pkill-by-symbol) -> export
#   then: exported CSV's LAST date must be recent (>= RECENT_MIN) or we refuse it
#   as incomplete; CORRECTED price sanity (recent price in current-level band;
#   oldest price only floor-checked - the old check wrongly tested the 2011-era
#   index level against today's band); import via mt5_import.sh --clean (memory-
#   safe dd staging); verify; append import_state; clear retry_failures; rm CSV.
#
# Waits for the buggy run (PID arg, default 885303) to fully exit first, and for
# any lingering/orphaned qdmcli to clear, so it never collides. Idempotent.
#
#   Usage: finish_group_b.sh [buggy_run_pid]      (default 885303)
#
set -uo pipefail

REPO="/home/jack/hybrid_project"
QDM_DIR="/home/jack/QDM"
QDMCLI="$QDM_DIR/qdmcli"
MT5_READY="$REPO/data/mt5_ready"
MT5_IMPORT_SH="$REPO/pipeline/mt5_import.sh"
IMPORT_STATE="$REPO/pipeline/import_state.txt"
RETRY_FAILURES="$REPO/pipeline/retry_failures.txt"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
STATUS_FILE="$MT5_DATA/MQL5/Files/import/import_status.txt"
LOG_DIR="$REPO/logs/finish_group_b"
LOG="$REPO/logs/finish_group_b.log"
WAIT_PID="${1:-885303}"

DATEFROM="2003.01.01"
DATETO="2026.07.16"
RECENT_MIN="2026.06.01"   # exported CSV's last-line date must be >= this (YYYY.MM.DD string compare).
                          # Lenient on purpose: a finishing-tail stall can leave a symbol complete only
                          # through early July (e.g. USOIL ~07.05) - immaterial for a 2003-2026 backtest.
                          # A GENUINELY partial store (download died years early, e.g. 2015) is still caught.
STALL_SEC=480
POLL_SEC=15
MIN_FILE_BYTES=100000
MIN_ROWS=500
MIN_TICKS=1000
FINISH_TAIL_PCT=97

ORDER=(US100 US500 US30 USOIL)
duk_for() { case "$1" in
  US100) echo USATECHIDXUSD ;; US500) echo USA500IDXUSD ;;
  US30)  echo USA30IDXUSD  ;; USOIL) echo LIGHTCMDUSD  ;; esac; }
# base: last_lo last_hi first_floor
band_for() { case "$1" in
  US100) echo "15000 45000 50" ;; US500) echo "4000 12000 20" ;;
  US30)  echo "30000 75000 300" ;; USOIL) echo "30 200 1" ;; *) echo "0 9e9 0" ;; esac; }

mkdir -p "$LOG_DIR"; touch "$IMPORT_STATE" "$RETRY_FAILURES"
log(){ printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }
already_imported(){ grep -qx "$1" "$IMPORT_STATE" 2>/dev/null; }
qdmcli_running(){ pgrep -f "$QDMCLI" >/dev/null 2>&1; }
wait_for_qdmcli_free(){ while qdmcli_running; do sleep 10; done; }
term_running(){ ps -eo args 2>/dev/null | grep -i terminal64.exe | grep -qv grep; }

fail(){ log "$1: FAIL - $2"; }

# store record count via -symbol action=list (retry on empty - an empty list is a
# FAILED query, not proof of absence; that empty-==-absent bug false-skipped US30).
store_records(){
  local duk="$1" tries=0 raw n
  while (( tries < 5 )); do
    wait_for_qdmcli_free
    raw=$( cd "$QDM_DIR" && "$QDMCLI" -symbol action=list 2>/dev/null )
    if [[ -n "$raw" ]]; then
      n=$(printf '%s\n' "$raw" | grep "^${duk}," | awk -F',' '{print $(NF-2)}')
      [[ -z "$n" ]] && n=0
      echo "$n"; return 0
    fi
    tries=$((tries+1)); sleep 5
  done
  echo "-1"   # could not query
}

# download with a finishing-tail-aware guard and a PROPER kill (pkill by symbol,
# not just the subshell $! - that incomplete kill is what orphaned US500's qdmcli).
kill_qdm_for(){ local duk="$1"; pkill -9 -f "$QDMCLI .*symbols=$duk" 2>/dev/null; }

download_symbol(){
  local duk="$1" base="$2" logf="$LOG_DIR/${base}_update.log"
  wait_for_qdmcli_free
  : > "$logf"
  ( cd "$QDM_DIR" && "$QDMCLI" -data action=update "symbols=$duk" >> "$logf" 2>&1 ) &
  local sub=$!
  log "$base: download launched (subshell $sub), finishing-tail-aware guard ${STALL_SEC}s"
  local last_size=-1 last_change; last_change=$(date +%s)
  while kill -0 "$sub" 2>/dev/null; do
    sleep "$POLL_SEC"
    local sz now pct; sz=$(stat -c%s "$logf" 2>/dev/null || echo 0); now=$(date +%s)
    [[ "$sz" != "$last_size" ]] && { last_size=$sz; last_change=$now; }
    if (( now - last_change >= STALL_SEC )); then
      pct=$(grep -aoE '[0-9]+%' "$logf" 2>/dev/null | tail -1 | tr -d '%'); : "${pct:=0}"
      if (( pct >= FINISH_TAIL_PCT )); then
        log "$base: finishing-tail stall at ${pct}% (qdmcli chasing newest unpublished day past $DATETO) - treating as COMPLETE, stopping cleanly."
        kill_qdm_for "$duk"; kill -9 "$sub" 2>/dev/null; wait "$sub" 2>/dev/null
        return 0
      fi
      log "$base: GENUINE stall at ${pct}% (<${FINISH_TAIL_PCT}%) - killing."
      kill_qdm_for "$duk"; kill -9 "$sub" 2>/dev/null; wait "$sub" 2>/dev/null
      return 1
    fi
  done
  wait "$sub" 2>/dev/null || true
  return 0
}

export_from_store(){
  local duk="$1" base="$2" out="$MT5_READY/$base.csv" logf="$LOG_DIR/${base}_export.log"
  wait_for_qdmcli_free
  ( cd "$QDM_DIR" && "$QDMCLI" -data action=exportToMT5 "symbol=$duk" timeframe=TICK \
      "datefrom=$DATEFROM" "dateto=$DATETO" "outputdir=$MT5_READY" "filename=$base" ) > "$logf" 2>&1
  [[ -f "$out" ]] || { fail "$base" "export produced no $out (see $logf)"; return 1; }
  return 0
}

csv_valid_and_recent(){   # $1=base ; echoes nothing, returns 0 if CSV exists, big enough, recent last date
  local base="$1" csv="$MT5_READY/$base.csv" size rows last
  [[ -f "$csv" ]] || return 1
  size=$(stat -c%s "$csv"); rows=$(wc -l < "$csv"); last=$(tail -1 "$csv")
  (( size >= MIN_FILE_BYTES )) || { log "$base: CSV too small ($size)"; return 1; }
  (( rows >= MIN_ROWS )) || { log "$base: CSV too few rows ($rows)"; return 1; }
  [[ "${last:0:10}" > "$RECENT_MIN" || "${last:0:10}" == "$RECENT_MIN" ]] || { log "$base: CSV last date ${last:0:10} older than $RECENT_MIN (incomplete store data)"; return 1; }
  return 0
}

price_ok(){   # corrected: recent price in band; oldest price only floor-checked
  local base="$1" csv="$MT5_READY/$base.csv" lo hi floor fb lb
  read -r lo hi floor <<< "$(band_for "$base")"
  fb=$(head -1 "$csv" | cut -d',' -f2); lb=$(tail -1 "$csv" | cut -d',' -f2)
  [[ "$fb" =~ ^-?[0-9.]+$ && "$lb" =~ ^-?[0-9.]+$ ]] || { fail "$base" "non-numeric bid (first=$fb last=$lb)"; return 1; }
  awk -v v="$lb" -v lo="$lo" -v hi="$hi" 'BEGIN{exit !(v>=lo && v<=hi)}' || { fail "$base" "last_bid=$lb outside current band [$lo,$hi] - real scale/data problem"; return 1; }
  awk -v v="$fb" -v f="$floor" 'BEGIN{exit !(v>f)}' || { fail "$base" "first_bid=$fb <= floor $floor (garbage)"; return 1; }
  log "$base: price sanity OK - first_bid=$fb (floor $floor), last_bid=$lb in [$lo,$hi]"; return 0
}

do_import(){
  local base="$1" csv="$MT5_READY/$base.csv" runlog="$LOG_DIR/${base}_import.log"
  if ! "$MT5_IMPORT_SH" --clean "$base" > "$runlog" 2>&1; then fail "$base" "mt5_import.sh errored; see $runlog"; return 1; fi
  [[ -f "$STATUS_FILE" ]] || { fail "$base" "no import_status.txt"; return 1; }
  local line; line=$(grep "^${base},OK," "$STATUS_FILE" || true)
  [[ -n "$line" ]] || { fail "$base" "no '${base},OK,' line in import_status.txt"; return 1; }
  IFS=',' read -r sym st ticks first last secs <<< "$line"
  { [[ "$ticks" =~ ^[0-9]+$ ]] && (( ticks >= MIN_TICKS )); } || { fail "$base" "implausible ticks ($ticks)"; return 1; }
  [[ "${last:0:3}" == "202" ]] || { fail "$base" "implausible last date ($last)"; return 1; }
  echo "$base" >> "$IMPORT_STATE"
  grep -q "^${base}," "$RETRY_FAILURES" 2>/dev/null && { grep -v "^${base}," "$RETRY_FAILURES" > "$RETRY_FAILURES.tmp" && mv "$RETRY_FAILURES.tmp" "$RETRY_FAILURES"; }
  rm -f "$csv"
  log "$base: SUCCESS - $ticks ticks, $first .. $last (${secs}s); imported ${base}.dk, state updated, CSV removed."
  return 0
}

# ============================== main ==============================
log "=== finish_group_b starting; waiting for buggy run PID $WAIT_PID to exit ==="
while kill -0 "$WAIT_PID" 2>/dev/null; do sleep 20; done
log "buggy run PID $WAIT_PID exited. Waiting for any lingering/orphaned qdmcli to clear..."
wait_for_qdmcli_free
log "qdmcli clear."

if term_running; then
  if ps -eo args | grep -i terminal64.exe | grep -qv grep && ! ps -eo args | grep -i terminal64.exe | grep -q autoimport_startup.ini; then
    log "a NON-autoimport terminal64 (likely the user's MT5) is open - cannot import safely. Aborting; re-run when it's closed."; exit 1
  fi
fi

for base in "${ORDER[@]}"; do
  duk=$(duk_for "$base")
  log "=== $base ($duk) ==="
  if already_imported "$base"; then log "$base: already imported - skip."; continue; fi

  # 1. get a valid, recent exported CSV
  if csv_valid_and_recent "$base"; then
    log "$base: reusing preserved valid CSV (no re-export)."
  else
    recs=$(store_records "$duk")
    log "$base: store records = $recs"
    if [[ "$recs" == "-1" ]]; then fail "$base" "could not query -symbol list (port contention?) - skipping this pass"; continue; fi
    if [[ "$recs" -le 0 ]] 2>/dev/null; then
      log "$base: store empty - downloading full history."
      if ! download_symbol "$duk" "$base"; then continue; fi
    else
      log "$base: store has $recs records - exporting from store."
    fi
    if ! export_from_store "$duk" "$base"; then continue; fi
    if ! csv_valid_and_recent "$base"; then
      # store data was partial/not current; one completion attempt then re-export
      log "$base: exported CSV not recent - store data incomplete; refusing to import stale data."
      fail "$base" "exported CSV last date not recent (< $RECENT_MIN) - store data incomplete, needs a fresh full download"
      continue
    fi
  fi

  # 2. corrected price sanity
  if ! price_ok "$base"; then continue; fi
  # 3. import
  do_import "$base" || continue
done

log "=== finish_group_b DONE. import_state now $(grep -c . "$IMPORT_STATE")/49. ==="
