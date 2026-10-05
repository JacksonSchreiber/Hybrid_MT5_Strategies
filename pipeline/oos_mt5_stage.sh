#!/usr/bin/env bash
#
# oos_mt5_stage.sh - the MT5 side of the out-of-sample (OOS) symbol test (docs/coach_queue.md, "OOS pipeline").
# PREPARED 2026-10-03, NOT RUN. Needs the local MT5 CLOSED (and no qdmcli running) for its whole duration.
#
# Per symbol, resumable (a step is skipped once its marker exists in data/study/oos_aa/state/):
#   1 export  qdmcli exportToMT5 timeframe=TICK from the on-disk QDM store -> data/mt5_ready/<BASE>.csv  (fleet/group-B method;
#             2012.01.01 -> 2026.10.03, US30 from 2013.01.01) + checks: first/last date, last bid vs the M1 CSV's last close
#   2 h4      pipeline/oos_h4_from_ticks.py -> data/study/h4/<BASE>.dk.csv  (bid M1 -> H4 from the same CSV the import reads;
#             bit-identical to dump_h4_dk.py's terminal copy on EURUSD/GBPUSD). Digits as TickImport infers them; a tick with more
#             decimals than that fails the symbol (H4 equivalence unverified).
#   3 import  pipeline/mt5_import.sh --clean <BASE>  (AutoImport EA, TickImport.mqh: DELETES and re-creates <BASE>.dk with inferred
#             specs and 24x5 sessions - the existing 2020-01..2026-07 fleet copy is replaced by 2012..2026-10)
#             then the source CSV is deleted (--keep-csv keeps it)
#   4 aa      pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --model 4 (real ticks) --from <start> --to 2026.06.30
#             with EVERY tester-study env knob unset = EA input defaults (InpV2Exit true, InpTcBankFrac 0.5, no staged / pyramid /
#             shorts-raise / queue rules) - the pinned journals' settings (data/study/ratchet/ratchet_report.md: full lineup,
#             model-4, v2-live; pinned set pre-dates staging). The journal is copied to data/study/oos_aa/ RIGHT AFTER the run (the
#             next run would not overwrite it - different symbol - but any later same-window run would).
# Once, first (skip with --skip-parity): PARITY - the same AA run on EURUSD.dk 2016.08.01..2026.06.29 -> data/study/oos_aa/parity/,
#   compared with the pinned journal on signal fields only (pipeline/oos_parity_check.py). Tells whether today's EA build still
#   produces the pinned record's signals; it does not gate anything.
# At the end: oos_build_inputs.py build (refresh) + drag + qa. The study itself is NOT run (the one look is the trader's command).
#
# HOLDOUT (2026-10-03, commodity-bloc time-stop pre-registration): --holdout runs the SAME steps and settings on the separate holdout
#   set AUDNZD, AUDCAD, NZDCAD (oos_build_inputs.HOLDOUT, never in NEW): state, logs and journals in data/study/holdout_aa/ (never
#   oos_aa/), no parity run, build/drag/qa --holdout, milestones appended to data/study/holdout_aa/progress.log, and after every AA
#   run the agent-log conversion check (pipeline/mt5_agentlog_check.py: "no prices for symbol" counts in that run's new log bytes).
# FLEET2 (2026-10-04, trader "start running mt5"; docs/new_symbols_prereg_2026-10-04.md): --fleet2 runs the same steps and settings
#   on the 26 fleet2 symbols: state, logs, journals and progress.log in data/study/fleet2_aa/ (never oos_aa/holdout_aa), no parity,
#   no 'final' (the study inputs for these symbols are registered later). Symbols whose MT5 custom copy already covers 2012-01..2026-06
#   are pre-marked 'imported' by pipeline/fleet2_aa_run.sh and run --steps aa only (no M1 CSV needed then). USDZAR (dropped from
#   the downloads: Dukascopy HTTP 429) runs on its existing 2020-01.. MT5 copy. The 9 reserved symbols' journals are produced but
#   never opened (the one look waits for the coach + trader).
# --steps LIST (comma-separated subset of export,h4,import,aa,final; default all) lets a driver pipeline the steps
#   (pipeline/holdout_run.sh). Only runs whose steps include import or aa need MT5 closed; import needs the h4 marker, aa needs the
#   imported marker; build/drag/qa run only when 'final' is among the steps.
#
#   pipeline/oos_mt5_stage.sh                      # all registered symbols (US30 only if its M1 CSV exists, i.e. the download finished)
#   pipeline/oos_mt5_stage.sh AUDUSD EURJPY        # some
#   pipeline/oos_mt5_stage.sh --dry-run            # print the plan, touch nothing
#   pipeline/oos_mt5_stage.sh --holdout [--steps export,h4] [AUDNZD ...]      # the holdout set (see HOLDOUT above)
#   flags: --skip-parity  --keep-csv  --aa-timeout S (default 14400)  --import-timeout S (default 5400)  --holdout  --steps LIST
#   suggested: nohup pipeline/oos_mt5_stage.sh > data/study/oos_aa/stage.out 2>&1 &
#
# Time (estimates from this repo's logs): export 3-8 min, H4 1-3 min, staging copy + import 15-45 min, AA run 20-60 min
# (EURUSD 2016-26, 276M ticks: 11-19 min at default settings; new FX 2012-26 ~1.2-2.2x the ticks) => ~45-110 min per symbol,
# ~8-14 h for the nine, + ~20 min parity. Disk: each tick CSV is ~12-25 GB (on /home) and is copied once more to C: while it
# imports; C: needs ~CSV x 1.1 + 30 GB free (checked).
#
# ONE-LOOK: the tester also writes an HTML report (profit, drawdown) and the journals carry r_multiple / pnl / terminal / mfe_r /
# rt_* columns - that is the EA's BASE outcome. Do not open them or sum them before the look.
#
set -uo pipefail

REPO="/home/jack/hybrid_project"
cd "$REPO" || exit 1
QDM_DIR="/home/jack/QDM"; QDMCLI="$QDM_DIR/qdmcli"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
STATUS_FILE="$MT5_DATA/MQL5/Files/import/import_status.txt"
EX5="$MT5_DATA/MQL5/Experts/HybridForwardTest.ex5"
OUT="$REPO/data/study/oos_aa"; STATE="$OUT/state"; LOGS="$OUT/logs"
READY="$REPO/data/mt5_ready"; FLEET="$REPO/data/qdm_csv/fleet"
EXPORT_TO="2026.10.03"; TESTER_TO="2026.06.30"        # TESTER_TO = oos_build_inputs.TESTER_TO (signals before 2026.07.01)
STRATS="SMC,Fib,TrendCont,EMArevQ"
AA_TIMEOUT=14400; IMPORT_TIMEOUT=5400; KEEP_CSV=0; PARITY=1; DRY=0; HOLDOUT=0; FLEET2=0; STEPS="export,h4,import,aa,final"

# BASE (importer / journal name)  QDM code  first date
REG=("AUDUSD AUDUSD 2012.01.01" "USDCAD USDCAD 2012.01.01" "USDCHF USDCHF 2012.01.01" "NZDUSD NZDUSD 2012.01.01"
     "EURJPY EURJPY 2012.01.01" "GBPJPY GBPJPY 2012.01.01" "EURGBP EURGBP 2012.01.01" "USDMXN USDMXN 2012.01.01"
     "XAGUSD XAGUSD 2012.01.01" "US30 USA30IDXUSD 2013.01.01")
# HOLDOUT set (= pipeline/oos_build_inputs.py HOLDOUT; selected only by --holdout)
HREG=("AUDNZD AUDNZD 2012.01.01" "AUDCAD AUDCAD 2012.01.01" "NZDCAD NZDCAD 2012.01.01")
# FLEET2 set (selected only by --fleet2)
FREG=("CADJPY CADJPY 2012.01.01" "CHFJPY CHFJPY 2012.01.01" "EURCHF EURCHF 2012.01.01" "GBPCHF GBPCHF 2012.01.01" "NZDCHF NZDCHF 2012.01.01"
      "EURCAD EURCAD 2012.01.01" "EURNZD EURNZD 2012.01.01" "GBPCAD GBPCAD 2012.01.01" "GBPNZD GBPNZD 2012.01.01" "EURSEK EURSEK 2012.01.01"
      "EURHUF EURHUF 2012.01.01" "EURPLN EURPLN 2012.01.01" "USDZAR USDZAR 2020.01.01"
      "AUDJPY AUDJPY 2012.01.01" "NZDJPY NZDJPY 2012.01.01" "AUDCHF AUDCHF 2012.01.01" "CADCHF CADCHF 2012.01.01" "EURAUD EURAUD 2012.01.01"
      "GBPAUD GBPAUD 2012.01.01" "USDSGD USDSGD 2012.01.01" "EURNOK EURNOK 2012.01.01" "USDSEK USDSEK 2012.01.01" "USDNOK USDNOK 2012.01.01"
      "USDHUF USDHUF 2012.01.01" "USDPLN USDPLN 2012.01.01" "USDCZK USDCZK 2012.01.01")
ORIG="EURUSD GBPUSD US100 US500 USDJPY USOIL XAUUSD BTCUSD"
# every knob mt5_verify.sh turns into an EA input - unset so the EA runs on its input defaults
KNOBS=(STAGED PYR TC_BANK SHORT_RAISE ADD_GATE SHORT_CUT IMPR_ADD EARLY INVERSE V2_EXIT EMA_STRETCH STOP_FLOOR_ATR STOP_FLOOR_SYM
       SHOCK_ATR SHOCK_PULL SHOCK_TPMULT SHOCK_CALGATE WEEKEND_FLAT OFFER_INV TEST_INV TEST_DELAY DELAY_MODE)

SEL=()
while [[ $# -gt 0 ]]; do case "$1" in
  --skip-parity) PARITY=0; shift;;
  --keep-csv) KEEP_CSV=1; shift;;
  --aa-timeout) AA_TIMEOUT="$2"; shift 2;;
  --import-timeout) IMPORT_TIMEOUT="$2"; shift 2;;
  --dry-run) DRY=1; shift;;
  --holdout) HOLDOUT=1; shift;;
  --fleet2) FLEET2=1; shift;;
  --steps) STEPS="$2"; shift 2;;
  -h|--help) sed -n '3,40p' "$0"; exit 0;;
  -*) echo "unknown flag $1" >&2; exit 2;;
  *) SEL+=("${1%.dk}"); shift;;
esac; done

for st_ in ${STEPS//,/ }; do [[ " export h4 import aa final " == *" $st_ "* ]] || { echo "unknown step '$st_' in --steps" >&2; exit 2; }; done
want(){ [[ ",$STEPS," == *",$1,"* ]]; }
if (( HOLDOUT )); then
  REG=("${HREG[@]}"); OUT="$REPO/data/study/holdout_aa"; STATE="$OUT/state"; LOGS="$OUT/logs"; PARITY=0; SETFLAG="--holdout"
elif (( FLEET2 )); then
  REG=("${FREG[@]}"); OUT="$REPO/data/study/fleet2_aa"; STATE="$OUT/state"; LOGS="$OUT/logs"; PARITY=0; SETFLAG="--fleet2"
  STEPS="${STEPS//,final/}"; STEPS="${STEPS//final,/}"; [[ "$STEPS" == final ]] && STEPS=""
else SETFLAG=""; fi
mkdir -p "$STATE" "$LOGS" "$OUT/actions"
(( HOLDOUT || FLEET2 )) || mkdir -p "$OUT/parity"
log(){ printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$OUT/stage.log" >&2; }
prog(){ (( HOLDOUT || FLEET2 )) && printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$OUT/progress.log"; return 0; }
mt5_running(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }
qdm_running(){ pgrep -f "qdmcli " >/dev/null 2>&1; }
done_(){ [[ -f "$STATE/$1.$2" ]]; }
mark(){ date -Iseconds > "$STATE/$1.$2"; }
free_gb(){ df -BG --output=avail "$1" | tail -1 | tr -dc '0-9'; }
FAILED=()

if (want import || want aa || (( PARITY ))) && mt5_running && (( ! DRY )); then
  echo "terminal64.exe is running - refusing to start (close MT5 first; this script never kills it)." >&2; exit 1; fi
log "=== oos_mt5_stage start (pid $$) set=$( (( HOLDOUT )) && echo holdout || { (( FLEET2 )) && echo fleet2 || echo oos; }) steps=$STEPS parity=$PARITY keep_csv=$KEEP_CSV aa_timeout=$AA_TIMEOUT"
[[ -f "$EX5" ]] && log "EA build: HybridForwardTest.ex5 modified $(stat -c '%y' "$EX5")" || log "WARN: $EX5 not found"

AA_J=""
aa_run(){  # SYMBOL FROM TO LOGFILE -> sets AA_J to the journal path on success. Called directly (never in $(...)) so its
  # "exit 1" stops the whole stage when MT5 is still up.
  local sym="$1" frm="$2" to="$3" lf="$4" envu=() k; AA_J=""
  for k in "${KNOBS[@]}"; do envu+=(-u "$k"); done
  mt5_running && { log "terminal64 appeared - stopping (resumable)"; exit 1; }
  local t0; t0=$(date +%s)
  env "${envu[@]}" bash "$REPO/pipeline/mt5_verify.sh" --mode ALL --strat "$STRATS" --symbol "$sym" --from "$frm" --to "$to" \
      --model 4 --timeout "$AA_TIMEOUT" > "$lf" 2>&1
  if grep -q "TIMEOUT" "$lf" || mt5_running; then log "$sym: tester TIMEOUT / terminal still up - stopping (operator: let it finish or close it)"; exit 1; fi
  grep -q "tester finished" "$lf" || { log "$sym: tester did not finish (see $lf)"; return 1; }
  local j; j=$(grep '^journal: ' "$lf" | tail -1 | sed 's/^journal: //')
  [[ -n "$j" && -f "$j" ]] || { log "$sym: no journal found (see $lf)"; return 1; }
  [[ "$(basename "$j")" == AA_${sym}_*.csv ]] || { log "$sym: journal name unexpected: $j"; return 1; }
  (( $(stat -c %Y "$j") >= t0 )) || { log "$sym: journal $j is older than this run"; return 1; }
  AA_J="$j"
}

# ------------------------------------------------------------------ parity (once)
if (( PARITY )) && ! done_ EURUSD parity; then
  if (( DRY )); then log "DRY: parity AA run EURUSD.dk 2016.08.01..2026.06.29 -> $OUT/parity/"
  else
    log "PARITY: AA EURUSD.dk 2016.08.01..2026.06.29 (pinned settings) ..."
    if aa_run EURUSD.dk 2016.08.01 2026.06.29 "$LOGS/parity_EURUSD.log"; then J="$AA_J"
      cp "$J" "$OUT/parity/"; log "parity journal -> $OUT/parity/$(basename "$J")"
      python3 pipeline/oos_parity_check.py "$OUT/parity/$(basename "$J")" | tee -a "$OUT/stage.log"
      mark EURUSD parity
    else log "PARITY run failed - continuing (it does not gate the symbols)"; fi
  fi
fi

# ------------------------------------------------------------------ per symbol
for row in "${REG[@]}"; do
  read -r BASE DUK START <<< "$row"
  if (( ${#SEL[@]} )) && [[ " ${SEL[*]} " != *" $BASE "* ]]; then continue; fi
  [[ " $ORIG " == *" $BASE "* ]] && { log "$BASE is an original symbol - never touched here"; continue; }
  SYM="$BASE.dk"; CSV="$READY/$BASE.csv"; M1="$FLEET/$DUK-M1-No Session.csv"
  [[ -f "$M1" ]] || M1="$FLEET/$BASE-M1-No Session.csv"
  if done_ "$BASE" aa; then log "$BASE: done (AA journal in $OUT) - skipped"; continue; fi
  if ! want export && ! want h4 && ! want import && ! want aa; then continue; fi
  if [[ ! -f "$M1" ]] && { want export || want h4 || ! done_ "$BASE" imported; }; then
    log "$BASE: no M1 CSV ($DUK) - download not finished; skipped"; FAILED+=("$BASE(no M1)"); continue; fi
  log "=== $BASE ($DUK) from $START"
  if (( DRY )); then
    log "DRY: export $DUK TICK $START..$EXPORT_TO -> $CSV ; H4 -> data/study/h4/$SYM.csv ; mt5_import.sh --clean $BASE ;"
    log "DRY: mt5_verify --mode ALL --strat $STRATS --symbol $SYM --from $START --to $TESTER_TO --model 4 -> $OUT/"
    continue
  fi

  # 1 export ----------------------------------------------------------
  if want export && ! done_ "$BASE" imported && { ! done_ "$BASE" exported || [[ ! -f "$CSV" ]]; }; then
    while qdm_running; do log "$BASE: qdmcli busy - waiting"; sleep 60; done
    log "$BASE: exportToMT5 TICK $START..$EXPORT_TO ..."
    ( cd "$QDM_DIR" && "$QDMCLI" -data action=exportToMT5 "symbol=$DUK" timeframe=TICK "datefrom=$START" "dateto=$EXPORT_TO" \
        "outputdir=$READY" "filename=$BASE" ) > "$LOGS/${BASE}_export.log" 2>&1
    rc=$?
    if (( rc != 0 )) || [[ ! -s "$CSV" ]]; then log "$BASE: export failed rc=$rc (see $LOGS/${BASE}_export.log)"; FAILED+=("$BASE(export)"); continue; fi
    first=$(head -1 "$CSV"); last=$(tail -1 "$CSV"); rows=$(wc -l < "$CSV")
    m1last=$(tail -1 "$M1" | cut -d, -f6)
    ok=$(awk -v f="${first:0:4}" -v s="${START:0:4}" -v l="${last:0:7}" -v b="$(echo "$last" | cut -d, -f2)" -v m="$m1last" \
         'BEGIN{ print (f==s && l>="2026.09" && b>0 && m>0 && b/m>0.97 && b/m<1.03) ? 1 : 0 }')
    log "$BASE: $rows ticks, first '${first:0:23}' last '${last:0:23}' bid $(echo "$last" | cut -d, -f2) vs M1 last close $m1last"
    if [[ "$ok" != 1 ]]; then log "$BASE: export check FAILED (first year / last date / price vs M1)"; FAILED+=("$BASE(export-check)"); continue; fi
    mark "$BASE" exported
  fi

  # 2 H4 from the same ticks -------------------------------------------
  if want h4 && ! done_ "$BASE" h4; then
    [[ -f "$CSV" ]] || { log "$BASE: H4 needs the tick CSV $CSV (export first)"; FAILED+=("$BASE(h4: no CSV)"); continue; }
    dec=$(head -1 "$CSV" | awk -F, '{a=split($2,x,"."); b=split($3,y,"."); da=(a>1?length(x[2]):0); db=(b>1?length(y[2]):0); print (da>db?da:db)}')
    dig=$(python3 -c "import sys; sys.path.insert(0,'.'); from pipeline.oos_build_inputs import importer_digits; print(importer_digits('$SYM', $dec))")
    log "$BASE: building H4 (tick decimals $dec -> importer digits $dig) ..."
    python3 pipeline/oos_h4_from_ticks.py --csv "$CSV" --sym "$SYM" --digits "$dig" --force > "$LOGS/${BASE}_h4.log" 2>&1
    cat "$LOGS/${BASE}_h4.log" | tee -a "$OUT/stage.log" >&2
    if ! grep -q "^wrote " "$LOGS/${BASE}_h4.log" || grep -q "UNVERIFIED" "$LOGS/${BASE}_h4.log"; then
      log "$BASE: H4 build failed or unverified - symbol stopped"; FAILED+=("$BASE(h4)"); continue; fi
    mark "$BASE" h4
  fi

  # 3 import -----------------------------------------------------------
  if want import && ! done_ "$BASE" imported; then
    done_ "$BASE" h4 || { log "$BASE: import refused - H4 not built yet (the import deletes the tick CSV H4 is built from)"; FAILED+=("$BASE(import: no H4)"); continue; }
    sz=$(( $(stat -c %s "$CSV") / 1073741824 + 1 )); need=$(( sz + sz / 10 + 30 ))
    if (( $(free_gb /mnt/c) < need )); then log "$BASE: C: has $(free_gb /mnt/c) GB free, need ~$need GB - stopping"; exit 1; fi
    mt5_running && { log "terminal64 appeared - stopping (resumable)"; exit 1; }
    log "$BASE: importing ($sz GB CSV) ..."; prog "$BASE: MT5 import start (mt5_import.sh --clean, $sz GB tick CSV; C: $(free_gb /mnt/c) GB free)"
    if ! bash "$REPO/pipeline/mt5_import.sh" --clean --timeout "$IMPORT_TIMEOUT" "$BASE" > "$LOGS/${BASE}_import.log" 2>&1; then
      mt5_running && { log "$BASE: import timed out, terminal still up - stopping (see $LOGS/${BASE}_import.log)"; exit 1; }
      log "$BASE: import failed (see $LOGS/${BASE}_import.log)"; FAILED+=("$BASE(import)"); continue; fi
    line=$(grep "^${BASE},OK," "$STATUS_FILE" || true)
    IFS=',' read -r _ _ ticks ifirst ilast _ <<< "$line"
    if [[ -z "$line" || "${ifirst:0:4}" != "${START:0:4}" || "${ilast:0:7}" < "2026.09" ]]; then
      log "$BASE: import status implausible: '$line'"; FAILED+=("$BASE(import-check)"); continue; fi
    log "$BASE: imported $ticks ticks $ifirst .. $ilast"; prog "$BASE: MT5 import end - $ticks ticks $ifirst .. $ilast as $SYM (C: $(free_gb /mnt/c) GB free)"
    mark "$BASE" imported
    (( KEEP_CSV )) || { rm -f "$CSV"; log "$BASE: source CSV deleted"; }
  fi

  # 4 AA tester run ----------------------------------------------------
  want aa || continue
  done_ "$BASE" imported || { log "$BASE: AA refused - $SYM not imported by this stage (no imported marker)"; FAILED+=("$BASE(aa: not imported)"); continue; }
  log "$BASE: AA run $SYM $START..$TESTER_TO model 4 ($STRATS) ..."
  prog "$BASE: AA tester run start ($SYM $START..$TESTER_TO, model 4, $STRATS, EA input defaults)"
  python3 pipeline/mt5_agentlog_check.py snap "$STATE/$BASE.agentlog_snap.json"
  if aa_run "$SYM" "$START" "$TESTER_TO" "$LOGS/${BASE}_aa.log"; then J="$AA_J"
    cp "$J" "$OUT/"; a="${J%.csv}.actions.csv"; [[ -f "$a" ]] && cp "$a" "$OUT/actions/"
    cnt=$(grep -E "SweepMSS:|DeepFib:|EMArevQ:|TrendCont:|total journal rows" "$LOGS/${BASE}_aa.log" | tr -s ' ' | paste -sd' ')
    echo "[$BASE counts] $cnt" | tee -a "$OUT/stage.log" >&2
    mark "$BASE" aa; log "$BASE: journal -> $OUT/$(basename "$J")"
    prog "$BASE: AA tester run end - journal $(basename "$J") (signal rows:$cnt)"
  else FAILED+=("$BASE(aa)"); prog "$BASE: AA tester run FAILED (see $LOGS/${BASE}_aa.log)"; fi
  chk=$(python3 pipeline/mt5_agentlog_check.py scan "$STATE/$BASE.agentlog_snap.json" "$SYM" 2>&1); crc=$?
  printf '%s\n' "$chk" | tee -a "$OUT/stage.log" >&2
  prog "$BASE: conversion-price check $( ((crc == 0)) && echo OK || echo 'FAULT (rc '$crc')') - $(printf '%s' "$chk" | head -1 | sed 's/^agent-log check [^:]*: //')"
done

(( DRY )) && { log "DRY run: nothing done"; exit 0; }
# ------------------------------------------------------------------ inputs
if want final; then
  log "refreshing study inputs (build, drag, qa${SETFLAG:+ $SETFLAG}) ..."
  python3 pipeline/oos_build_inputs.py build $SETFLAG >> "$OUT/stage.log" 2>&1
  python3 pipeline/oos_build_inputs.py drag $SETFLAG 2>&1 | tee -a "$OUT/stage.log" >&2
  python3 pipeline/oos_build_inputs.py qa $SETFLAG 2>&1 | tee "$OUT/qa.txt" | tee -a "$OUT/stage.log" >&2
  if (( HOLDOUT )); then
    prog "QA (signal fields only, data/study/holdout_aa/qa.txt):"
    sed 's/^/      /' "$OUT/qa.txt" >> "$OUT/progress.log"
  fi
fi
log "=== stage finished (steps $STEPS). failed/skipped: ${FAILED[*]:-none}"
if (( HOLDOUT )); then want final && log "next (the ONE LOOK, trader's command): python3 pipeline/cbloc_holdout_look.py --look"
elif (( FLEET2 )); then log "fleet2: journals in $OUT (the reserved symbols' journals are never opened; the look waits for the coach + trader)"
else
log "next (the ONE LOOK, trader's command): python3 pipeline/oos_queue_study.py            # readiness check, computes nothing"
log "                                       python3 pipeline/oos_queue_study.py --look [--exclude US30]"
fi
