#!/usr/bin/env bash
#
# group_b_import.sh - completes the fleet's final 4 exception symbols
# (US100, US500, US30, USOIL) to reach 49/49.
#
# BACKSTORY: these 4 are indices/oil whose Dukascopy instrument is shown by
# QDM's own `-instrument action=list` with a dotted display name
# (USATECH.IDX, USA500.IDX, USA30.IDX, LIGHT.CMD) - and `-symbol action=add`
# silently refuses to register ANY symbol name containing a literal "." on
# this qdmcli build (confirmed in pipeline/retry_batch.sh's investigation).
# QDM's DISPLAY name is not Dukascopy's raw feed code, though: the actual
# dot-free, currency-suffixed codes work fine in both qdmcli AND Dukascopy's
# raw datafeed (independently verified by fetching real ticks directly from
# datafeed.dukascopy.com, bypassing QDM entirely, before this script was
# written - see the feasibility-probe report in this session's history):
#
#   FTMO base -> QDM/Dukascopy dot-free code
#   US100     -> USATECHIDXUSD   (confirmed registers in qdmcli; PM verified live)
#   US500     -> USA500IDXUSD    (pattern-confirmed; direct-feed fetch flaky
#                                  server but same naming convention as the
#                                  other 3, all of which fetched real ticks)
#   US30      -> USA30IDXUSD     (confirmed: direct fetch got 12,954 real
#                                  ticks, bid ~52808 - sane Dow level for 2026)
#   USOIL     -> LIGHTCMDUSD     (confirmed: direct fetch got 8,991 real
#                                  ticks, bid ~79.56 - sane WTI price)
#
# config/symbols.yaml has been updated to these codes with the same
# explanation, so a future session doesn't repeat this investigation.
#
# PIPELINE per symbol (mirrors retry_batch.sh's proven functions): add (if
# not already registered) -> update (full history, 8-min stall guard) ->
# exportToMT5 (filename=<FTMO_BASE> so the CSV lands as
# data/mt5_ready/<FTMO_BASE>.csv, NOT the dukascopy code - the importer
# keys off the FTMO base name) -> PRICE SANITY CHECK (these are indices/
# commodities, not forex - a scale bug would be off by a power of 10, so
# the exported CSV's first/last bid/ask are checked against a wide
# per-symbol plausible band before ever importing) -> mt5_import.sh --clean
# -> verify via the real import_status.txt -> append to import_state.txt,
# remove the symbol's now-stale NEEDS_MANUAL line from retry_failures.txt,
# delete the source CSV.
#
# Digits/contract-size for these 4 index/commodity custom symbols are
# ALREADY handled correctly by the existing shared import engine
# (mql5/include/Hybrid/TickImport.mqh's TI_SetInferredSpecs): it special-
# cases base=="US100"/"US500"/"US30" (digits capped at 2, contract=1.0) and
# base=="USOIL" (contract=1000.0) - no MQL5 changes needed here, verified
# by reading that code before writing this script.
#
# Serialization: one qdmcli and one terminal64 at a time, identical
# discipline to retry_batch.sh (wait_for_qdmcli_free before every qdmcli
# call; ensure_terminal_free refuses to touch an unrecognized terminal64
# session and just flags it rather than importing).
#
# Usage:
#   nohup /home/jack/hybrid_project/pipeline/group_b_import.sh \
#       > /home/jack/hybrid_project/logs/group_b_import.log 2>&1 &
#
set -uo pipefail

REPO="/home/jack/hybrid_project"
QDM_DIR="/home/jack/QDM"
QDMCLI="$QDM_DIR/qdmcli"
MT5_READY="$REPO/data/mt5_ready"
MT5_IMPORT_SH="$REPO/pipeline/mt5_import.sh"
IMPORT_STATE="$REPO/pipeline/import_state.txt"
RETRY_FAILURES="$REPO/pipeline/retry_failures.txt"
LOG_DIR="$REPO/logs/group_b_import"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"

DATEFROM="2003.01.01"
DATETO="2026.07.16"
MIN_LAST_DATE_PREFIX="2026.07"
MIN_FILE_BYTES=100000
MIN_ROWS=500
MIN_PLAUSIBLE_TICKS=1000

STALL_SEC=480
POLL_SEC=15
QDMCLI_WAIT_POLL_SEC=10
TERM_CLOSE_WAIT_SEC=180

# FTMO base -> dukascopy dot-free code
GROUP_B_FTMO=(US100 US500 US30 USOIL)
GROUP_B_DUK=(USATECHIDXUSD USA500IDXUSD USA30IDXUSD LIGHTCMDUSD)

# Price sanity bands (low, high) - wide enough to comfortably span 2020-2026
# index/commodity growth, tight enough to catch a x10/x0.1 scale bug.
band_for() {
  case "$1" in
    US100) echo "10000 50000" ;;
    US500) echo "3000 15000" ;;
    US30)  echo "25000 70000" ;;
    USOIL) echo "30 200" ;;
    *)     echo "0 999999999" ;;
  esac
}

mkdir -p "$LOG_DIR"
touch "$IMPORT_STATE" "$RETRY_FAILURES"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

fail() {
  local base="$1" reason="$2"
  log "$base: FAIL - $reason"
  echo "$base,$(date -Iseconds),$reason" >> "$RETRY_FAILURES"
}

already_imported() { grep -qx "$1" "$IMPORT_STATE" 2>/dev/null; }

remove_needs_manual() {
  local base="$1"
  local tmp; tmp=$(mktemp)
  grep -v "^${base}," "$RETRY_FAILURES" > "$tmp" 2>/dev/null || true
  mv "$tmp" "$RETRY_FAILURES"
  log "$base: removed stale NEEDS_MANUAL line from retry_failures.txt"
}

# --- qdmcli serialization (identical to retry_batch.sh) --------------------
qdmcli_running() { pgrep -f "qdmcli " > /dev/null 2>&1; }

wait_for_qdmcli_free() {
  if qdmcli_running; then
    log "qdmcli already running - waiting for it to finish..."
  fi
  while qdmcli_running; do
    sleep "$QDMCLI_WAIT_POLL_SEC"
  done
}

run_qdm() {
  local logfile="$1"; shift
  wait_for_qdmcli_free
  ( cd "$QDM_DIR" && "$QDMCLI" "$@" ) > "$logfile" 2>&1
  echo $?
}

# --- terminal64 coordination (identical to retry_batch.sh) -----------------
terminal_procs() { ps -eo pid,args | grep -i terminal64.exe | grep -v grep || true; }

ensure_terminal_free() {
  local procs pid
  procs=$(terminal_procs)
  [[ -z "$procs" ]] && return 0

  log "terminal64 already running at startup - investigating: $procs"
  if echo "$procs" | grep -q "autoimport_startup.ini"; then
    log "looks like the lingering AutoImport terminal - waiting up to ${TERM_CLOSE_WAIT_SEC}s for it to self-close..."
    local waited=0
    while (( waited < TERM_CLOSE_WAIT_SEC )); do
      procs=$(terminal_procs)
      [[ -z "$procs" ]] && { log "terminal64 self-closed after ${waited}s."; return 0; }
      sleep 10
      waited=$(( waited + 10 ))
    done
    pid=$(echo "$procs" | head -1 | awk '{print $1}')
    log "autoimport terminal64 (pid $pid) still up after ${TERM_CLOSE_WAIT_SEC}s - closing that PID specifically."
    taskkill.exe /PID "$pid" /F > /dev/null 2>&1
    sleep 5
    procs=$(terminal_procs)
    if [[ -z "$procs" ]]; then
      log "closed."
      return 0
    else
      log "still running after taskkill attempt - treating as unknown, will not import this run."
      return 1
    fi
  else
    log "terminal64 running with NO autoimport config - looks like a USER session. NOT touching it. Imports will be PAUSED/flagged; downloads+exports still proceed."
    return 1
  fi
}

# --- symbol-list helpers (CSV-safe, same right-anchored field extraction
# as retry_batch.sh) ---------------------------------------------------
symbol_list_raw() {
  local logfile="$LOG_DIR/_symbol_list.log"
  local rc; rc=$(run_qdm "$logfile" -symbol action=list)
  [[ "$rc" == "0" ]] && cat "$logfile" || true
}

symbol_registered() { symbol_list_raw | grep -q "^${1},"; }

symbol_record_count() {
  local line
  line=$(symbol_list_raw | grep "^${1},")
  [[ -z "$line" ]] && { echo 0; return; }
  echo "$line" | awk -F',' '{n=$(NF-2); print (n=="" ? 0 : n)}'
}

# --- STEP 0: register + verify all 4 codes before committing to full
# downloads. Does NOT trust the "Add symbols."/"Exit app - ok" message -
# always re-verifies via a fresh -symbol action=list, exactly the check
# that exposed the dotted-name bug in the first place. -----------------
step0_verify_codes() {
  log "=== STEP 0: verifying all 4 dot-free codes register correctly ==="
  local all_ok=1
  for i in "${!GROUP_B_FTMO[@]}"; do
    local ftmo="${GROUP_B_FTMO[$i]}" duk="${GROUP_B_DUK[$i]}"
    if symbol_registered "$duk"; then
      log "$ftmo ($duk): already registered - OK"
      continue
    fi
    local logfile="$LOG_DIR/${ftmo}_add.log"
    local rc; rc=$(run_qdm "$logfile" -symbol action=add "symbols=$duk" datasource=dukascopy datatype=TICK)
    if [[ "$rc" != "0" ]]; then
      fail "$ftmo" "STEP0 symbol add failed rc=$rc for code $duk; see $logfile"
      all_ok=0
      continue
    fi
    if ! symbol_registered "$duk"; then
      fail "$ftmo" "STEP0: code '$duk' add reported OK but does NOT appear in -symbol action=list - code likely wrong, needs correcting"
      all_ok=0
      continue
    fi
    log "$ftmo ($duk): registered and VERIFIED via -symbol action=list"
  done
  if (( all_ok == 0 )); then
    log "STEP 0: one or more codes failed to verify - see retry_failures.txt. Continuing only with the codes that DID verify."
  else
    log "STEP 0: all 4 codes verified registered. Proceeding to full pipeline."
  fi
}

# --- update (stall-guarded, identical logic to retry_batch.sh) -------------
do_update_stall_guarded() {
  local duk="$1" ftmo="$2"
  local existing_records; existing_records=$(symbol_record_count "$duk")
  if [[ "$existing_records" -gt 0 ]] 2>/dev/null; then
    log "$ftmo ($duk): already has $existing_records record(s) - skipping update."
    return 0
  fi

  wait_for_qdmcli_free
  local logfile="$LOG_DIR/${ftmo}_update.log"
  : > "$logfile"
  ( cd "$QDM_DIR" && "$QDMCLI" -data action=update "symbols=$duk" >> "$logfile" 2>&1 ) &
  local qpid=$!
  log "$ftmo ($duk): update launched (pid $qpid), stall guard ${STALL_SEC}s"

  local last_size=-1 last_change
  last_change=$(date +%s)
  while kill -0 "$qpid" 2>/dev/null; do
    sleep "$POLL_SEC"
    local cur_size; cur_size=$(stat -c%s "$logfile" 2>/dev/null || echo 0)
    local now; now=$(date +%s)
    if [[ "$cur_size" != "$last_size" ]]; then
      last_size=$cur_size
      last_change=$now
    fi
    local stall=$(( now - last_change ))
    if (( stall >= STALL_SEC )); then
      local lastline; lastline=$(grep -v "DEBUG\|TRACE" "$logfile" 2>/dev/null | tail -1)
      log "$ftmo: STALLED - no log growth for ${stall}s (last: ${lastline:-<none>}) - killing pid $qpid"
      kill -9 "$qpid" 2>/dev/null
      wait "$qpid" 2>/dev/null
      fail "$ftmo" "update stalled >${STALL_SEC}s, killed; last progress: ${lastline:-<none>}"
      return 1
    fi
  done
  wait "$qpid"
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    fail "$ftmo" "update failed rc=$rc; see $logfile"
    return 1
  fi

  local records; records=$(symbol_record_count "$duk")
  if [[ "$records" -le 0 ]] 2>/dev/null; then
    fail "$ftmo" "update exited 0 but 0 records present - treating as failed"
    return 1
  fi
  log "$ftmo: download OK - $records records"
  return 0
}

do_export() {
  local duk="$1" ftmo="$2"
  # filename=$ftmo (NOT $duk) - the importer keys off the FTMO base name.
  local out_csv="$MT5_READY/$ftmo.csv"
  local logfile="$LOG_DIR/${ftmo}_export.log"
  local rc; rc=$(run_qdm "$logfile" -data action=exportToMT5 "symbol=$duk" timeframe=TICK \
      "datefrom=$DATEFROM" "dateto=$DATETO" "outputdir=$MT5_READY" "filename=$ftmo")
  if [[ "$rc" != "0" ]]; then
    fail "$ftmo" "exportToMT5 failed rc=$rc; see $logfile"
    return 1
  fi

  if [[ ! -f "$out_csv" ]]; then
    fail "$ftmo" "export reported OK but $out_csv missing"
    return 1
  fi
  local size; size=$(stat -c%s "$out_csv")
  if (( size < MIN_FILE_BYTES )); then
    fail "$ftmo" "export CSV too small ($size bytes)"
    return 1
  fi
  local last_line; last_line=$(tail -1 "$out_csv")
  if [[ "${last_line:0:7}" != "$MIN_LAST_DATE_PREFIX" ]]; then
    fail "$ftmo" "export CSV last line date implausible: ${last_line:0:20}"
    return 1
  fi
  local rows; rows=$(wc -l < "$out_csv")
  if (( rows < MIN_ROWS )); then
    fail "$ftmo" "export CSV row count too low ($rows)"
    return 1
  fi
  log "$ftmo: export OK - $rows rows, $size bytes"
  return 0
}

# --- CRITICAL: price sanity check (index/commodity, not forex - a scale
# bug is a power-of-10 error) - checked directly against the exported CSV,
# the ground truth for what will actually get imported. --------------------
do_price_sanity() {
  local ftmo="$1"
  local csv="$MT5_READY/$ftmo.csv"
  read -r lo hi <<< "$(band_for "$ftmo")"

  local first_line last_line
  first_line=$(head -1 "$csv")
  last_line=$(tail -1 "$csv")
  # format: YYYY.MM.DD HH:MM:SS.mmm,bid,ask  (no header)
  local first_bid last_bid
  first_bid=$(echo "$first_line" | cut -d',' -f2)
  last_bid=$(echo "$last_line" | cut -d',' -f2)

  local ok=1
  for v in "$first_bid" "$last_bid"; do
    if ! awk -v v="$v" -v lo="$lo" -v hi="$hi" 'BEGIN{exit !(v>=lo && v<=hi)}'; then
      ok=0
    fi
  done

  if (( ok == 0 )); then
    fail "$ftmo" "PRICE SANITY FAILED - first_bid=$first_bid last_bid=$last_bid outside plausible band [$lo, $hi] - possible power-of-10 scale error, NOT importing"
    return 1
  fi
  log "$ftmo: price sanity OK - first_bid=$first_bid last_bid=$last_bid within [$lo, $hi]"
  return 0
}

# --- import (same verification discipline as retry_batch.sh) ---------------
do_import() {
  local ftmo="$1"
  local csv="$MT5_READY/$ftmo.csv"
  local status_file="$MT5_DATA/MQL5/Files/import/import_status.txt"
  local runlog="$LOG_DIR/${ftmo}_import.log"

  if [[ ! -f "$csv" ]]; then
    fail "$ftmo" "no CSV to import at $csv"
    return 1
  fi

  if ! "$MT5_IMPORT_SH" --clean "$ftmo" > "$runlog" 2>&1; then
    fail "$ftmo" "mt5_import.sh failed; see $runlog"
    return 1
  fi

  if [[ ! -f "$status_file" ]]; then
    fail "$ftmo" "no import_status.txt after run"
    return 1
  fi
  local line; line=$(grep "^${ftmo},OK," "$status_file" || true)
  if [[ -z "$line" ]]; then
    fail "$ftmo" "no '${ftmo},OK,' line in import_status.txt"
    return 1
  fi
  IFS=',' read -r sym status ticks first last seconds <<< "$line"
  if ! [[ "$ticks" =~ ^[0-9]+$ ]] || (( ticks < MIN_PLAUSIBLE_TICKS )); then
    fail "$ftmo" "implausible tick count ($ticks)"
    return 1
  fi
  if [[ -z "$last" || "${last:0:3}" != "202" ]]; then
    fail "$ftmo" "implausible last date ($last)"
    return 1
  fi

  log "$ftmo: import verified OK - $ticks ticks, $first .. $last (${seconds}s)"
  rm -f "$csv"
  echo "$ftmo" >> "$IMPORT_STATE"
  remove_needs_manual "$ftmo"
  log "$ftmo: SUCCESS - imported as ${ftmo}.dk, source CSV deleted, state updated"
  return 0
}

# --- main --------------------------------------------------------------
log "group_b_import.sh starting."
log "Symbols: US100->USATECHIDXUSD US500->USA500IDXUSD US30->USA30IDXUSD USOIL->LIGHTCMDUSD"

step0_verify_codes

TERMINAL_OK=1
if ! ensure_terminal_free; then
  TERMINAL_OK=0
fi

n_ok=0
n_fail=0
for i in "${!GROUP_B_FTMO[@]}"; do
  ftmo="${GROUP_B_FTMO[$i]}"; duk="${GROUP_B_DUK[$i]}"
  log "=== $ftmo ($duk): starting ==="
  if already_imported "$ftmo"; then
    log "$ftmo: already in import_state.txt, skipping entirely."
    continue
  fi
  if ! symbol_registered "$duk"; then
    log "$ftmo: code '$duk' never verified in STEP 0 - skipping (see retry_failures.txt)."
    n_fail=$((n_fail+1))
    continue
  fi

  if ! do_update_stall_guarded "$duk" "$ftmo"; then n_fail=$((n_fail+1)); continue; fi
  if ! do_export "$duk" "$ftmo"; then n_fail=$((n_fail+1)); continue; fi
  if ! do_price_sanity "$ftmo"; then n_fail=$((n_fail+1)); continue; fi

  if (( TERMINAL_OK == 0 )); then
    log "$ftmo: export+price-check OK but imports are PAUSED this run (terminal64 conflict) - CSV left in data/mt5_ready for a later import pass."
    echo "$ftmo,$(date -Iseconds),export OK but import paused - terminal64 conflict, retry import later" >> "$RETRY_FAILURES"
    n_fail=$((n_fail+1))
    continue
  fi

  if ! do_import "$ftmo"; then n_fail=$((n_fail+1)); continue; fi
  n_ok=$((n_ok+1))
done

TOTAL_DONE=$(wc -l < "$IMPORT_STATE")
log "=== SUMMARY ==="
log "Group B this run: $n_ok/${#GROUP_B_FTMO[@]} completed, $n_fail failed/incomplete."
log "See $RETRY_FAILURES for per-symbol reasons on any failure."
log "TOTAL: ${TOTAL_DONE}/49 symbols in import_state.txt."
log "group_b_import.sh finished."
