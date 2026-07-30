#!/usr/bin/env bash
#
# recover_missing_indices.sh - final recovery for any Group B index/oil symbol
# still missing after finish_group_b.sh, using a BARE download that is NEVER
# killed.
#
# HARD-WON LESSON: qdmcli's `action=update` commits its downloaded series to the
# store ONLY on its own clean/graceful exit. Any SIGKILL of the qdmcli process
# discards the ENTIRE in-progress series (proved: US30 killed mid-finishing-tail
# -> export reports "Symbol ... doesn't contain any data"). The finishing-tail
# Dukascopy retry-spam is NOT a hang - qdmcli exhausts its retries against the
# newest unpublished days and finalizes on its own (US500's orphaned qdmcli took
# ~1hr post-interruption but committed the full series through 2026.07.16). So:
# NEVER kill an in-flight qdmcli. Launch it, monitor for logging/progress only,
# and WAIT for it to exit by itself - however long that takes.
#
# Per still-missing symbol (US30, USOIL): if store empty -> bare download (no kill,
# wait for natural exit) -> export -> corrected price check -> import -> state.
#
# Waits for finish_group_b.sh (PID arg) to fully exit and qdmcli to be free first.
#
#   Usage: recover_missing_indices.sh [finish_group_b_pid]   (default 947348)
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
LOG_DIR="$REPO/logs/recover_missing_indices"
LOG="$REPO/logs/recover_missing_indices.log"
WAIT_PID="${1:-947348}"

DATEFROM="2003.01.01"; DATETO="2026.07.16"
RECENT_MIN="2026.06.01"
MIN_FILE_BYTES=100000; MIN_ROWS=500; MIN_TICKS=1000
PROGRESS_LOG_EVERY=20   # log a progress heartbeat every N polls (~5 min at 15s)
POLL=15

# only the index/oil symbols (forex all done); duk codes are dot-free
CANDIDATES=(US30 USOIL)
duk_for(){ case "$1" in US30) echo USA30IDXUSD;; USOIL) echo LIGHTCMDUSD;; US100) echo USATECHIDXUSD;; US500) echo USA500IDXUSD;; esac; }
band_for(){ case "$1" in
  US30) echo "30000 75000 300";; USOIL) echo "30 200 1";;
  US100) echo "15000 45000 50";; US500) echo "4000 12000 20";; *) echo "0 9e9 0";; esac; }

mkdir -p "$LOG_DIR"; touch "$IMPORT_STATE" "$RETRY_FAILURES"
log(){ printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }
already_imported(){ grep -qx "$1" "$IMPORT_STATE" 2>/dev/null; }
qdmcli_running(){ pgrep -f "$QDMCLI" >/dev/null 2>&1; }
wait_for_qdmcli_free(){ while qdmcli_running; do sleep 10; done; }

store_records(){
  local duk="$1" tries=0 raw n
  while (( tries < 6 )); do
    wait_for_qdmcli_free
    raw=$( cd "$QDM_DIR" && "$QDMCLI" -symbol action=list 2>/dev/null )
    if [[ -n "$raw" ]]; then
      n=$(printf '%s\n' "$raw" | grep "^${duk}," | awk -F',' '{print $(NF-2)}'); [[ -z "$n" ]] && n=0
      echo "$n"; return
    fi
    tries=$((tries+1)); sleep 5
  done
  echo "-1"
}

# BARE download - launch and WAIT for natural exit. NO kill, ever.
bare_download(){
  local duk="$1" base="$2" logf="$LOG_DIR/${base}_update.log"
  wait_for_qdmcli_free
  : > "$logf"
  ( cd "$QDM_DIR" && "$QDMCLI" -data action=update "symbols=$duk" >> "$logf" 2>&1 ) &
  local sub=$!
  log "$base: BARE download launched (subshell $sub) - will NOT be killed; waiting for qdmcli to finalize on its own (may take a long time on the finishing tail)."
  local i=0
  while kill -0 "$sub" 2>/dev/null; do
    sleep "$POLL"; i=$((i+1))
    if (( i % PROGRESS_LOG_EVERY == 0 )); then
      local prog; prog=$(grep -aoE 'Writing[[:space:]]+[0-9.]+, [0-9]+%' "$logf" 2>/dev/null | tail -1)
      log "$base: still downloading - last progress: ${prog:-<retry/init>} (elapsed ~$(( i*POLL/60 ))m, NOT killing)"
    fi
  done
  wait "$sub" 2>/dev/null; local rc=$?
  log "$base: qdmcli exited on its own (rc=$rc) - data now committed to store."
  return 0
}

export_one(){
  local duk="$1" base="$2" out="$MT5_READY/$base.csv" logf="$LOG_DIR/${base}_export.log"
  wait_for_qdmcli_free
  ( cd "$QDM_DIR" && "$QDMCLI" -data action=exportToMT5 "symbol=$duk" timeframe=TICK \
      "datefrom=$DATEFROM" "dateto=$DATETO" "outputdir=$MT5_READY" "filename=$base" ) > "$logf" 2>&1
  [[ -f "$out" ]] || { log "$base: FAIL - export produced no $out (store still empty? tail: $(grep -aiE 'cannot export|doesn.t contain|error' "$logf" | tail -1))"; return 1; }
  local size rows last; size=$(stat -c%s "$out"); rows=$(wc -l < "$out"); last=$(tail -1 "$out")
  (( size >= MIN_FILE_BYTES )) || { log "$base: FAIL - CSV too small ($size)"; return 1; }
  (( rows >= MIN_ROWS )) || { log "$base: FAIL - CSV too few rows ($rows)"; return 1; }
  [[ "${last:0:10}" > "$RECENT_MIN" || "${last:0:10}" == "$RECENT_MIN" ]] || { log "$base: FAIL - CSV last date ${last:0:10} < $RECENT_MIN (still incomplete)"; return 1; }
  log "$base: export OK - $rows rows, last ${last:0:10}"; return 0
}

price_ok(){
  local base="$1" csv="$MT5_READY/$base.csv" lo hi floor fb lb
  read -r lo hi floor <<< "$(band_for "$base")"
  fb=$(head -1 "$csv" | cut -d',' -f2); lb=$(tail -1 "$csv" | cut -d',' -f2)
  [[ "$fb" =~ ^-?[0-9.]+$ && "$lb" =~ ^-?[0-9.]+$ ]] || { log "$base: FAIL - non-numeric bid ($fb/$lb)"; return 1; }
  awk -v v="$lb" -v lo="$lo" -v hi="$hi" 'BEGIN{exit !(v>=lo && v<=hi)}' || { log "$base: FAIL - last_bid=$lb outside [$lo,$hi]"; return 1; }
  awk -v v="$fb" -v f="$floor" 'BEGIN{exit !(v>f)}' || { log "$base: FAIL - first_bid=$fb <= floor $floor"; return 1; }
  log "$base: price sanity OK - first_bid=$fb (floor $floor), last_bid=$lb in [$lo,$hi]"; return 0
}

do_import(){
  local base="$1" csv="$MT5_READY/$base.csv" runlog="$LOG_DIR/${base}_import.log"
  if ! "$MT5_IMPORT_SH" --clean "$base" > "$runlog" 2>&1; then log "$base: FAIL - mt5_import.sh errored; see $runlog"; return 1; fi
  local line; line=$(grep "^${base},OK," "$STATUS_FILE" 2>/dev/null || true)
  [[ -n "$line" ]] || { log "$base: FAIL - no '${base},OK,' in import_status.txt"; return 1; }
  IFS=',' read -r sym st ticks first last secs <<< "$line"
  { [[ "$ticks" =~ ^[0-9]+$ ]] && (( ticks >= MIN_TICKS )); } || { log "$base: FAIL - implausible ticks ($ticks)"; return 1; }
  [[ "${last:0:3}" == "202" ]] || { log "$base: FAIL - implausible last ($last)"; return 1; }
  echo "$base" >> "$IMPORT_STATE"
  grep -q "^${base}," "$RETRY_FAILURES" 2>/dev/null && { grep -v "^${base}," "$RETRY_FAILURES" > "$RETRY_FAILURES.tmp" && mv "$RETRY_FAILURES.tmp" "$RETRY_FAILURES"; }
  rm -f "$csv"
  log "$base: SUCCESS - $ticks ticks, $first .. $last (${secs}s); imported ${base}.dk, state updated."
  return 0
}

# ============================== main ==============================
log "=== recover_missing_indices starting; waiting for finish_group_b PID $WAIT_PID to exit ==="
while kill -0 "$WAIT_PID" 2>/dev/null; do sleep 20; done
log "finish_group_b PID $WAIT_PID exited. Waiting for any in-flight qdmcli to finalize/free..."
wait_for_qdmcli_free
log "qdmcli free."

for base in "${CANDIDATES[@]}"; do
  duk=$(duk_for "$base")
  if already_imported "$base"; then log "$base: already imported - skip."; continue; fi
  log "=== $base ($duk): recovering ==="
  recs=$(store_records "$duk"); log "$base: store records = $recs"
  if [[ "$recs" == "-1" ]]; then log "$base: FAIL - could not query store; skipping this pass."; continue; fi
  if [[ "$recs" -le 0 ]] 2>/dev/null; then
    bare_download "$duk" "$base"
  else
    log "$base: store already has $recs records - skipping download, exporting."
  fi
  export_one "$duk" "$base" || continue
  price_ok "$base" || continue
  do_import "$base" || continue
done
log "=== recover_missing_indices DONE. import_state now $(grep -c . "$IMPORT_STATE")/49. ==="
