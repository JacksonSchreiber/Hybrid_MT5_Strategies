#!/usr/bin/env bash
#
# holdout_run.sh - end-to-end driver for the commodity-bloc HOLDOUT symbols AUDNZD, AUDCAD, NZDCAD (pre-registration:
# docs/coach_queue.md "PRE-REGISTRATION - commodity-bloc time stop"). 2026-10-03.
#
# Pipelined, never kills anything:
#   qdmcli lane  download (oos_download.sh method), M1 export, tick export - strictly ONE qdmcli process at a time (QDM keeps its
#                catalogue in one SQLite data.db; no QDM doc confirms that concurrent qdmcli processes are safe on it). AUDCAD and
#                NZDCAD download in ONE qdmcli invocation (symbols=AUDCAD,NZDCAD) so QDM parallelises them inside one process.
#   python lane  H4 builds (oos_h4_from_ticks.py via the stage script) and M1 verification run concurrently with everything else.
#   MT5 lane     import + AA tester run, one terminal at a time, in symbol order, as soon as a symbol's H4 is built. Waits while
#                any terminal64 is running (never launches a second one; the stage script refuses too).
# Stage steps: pipeline/oos_mt5_stage.sh --holdout --steps ... (journals -> data/study/holdout_aa/, never oos_aa/).
# Waits for data/study/holdout_aa/state/STAGE_READY before the first stage step.
# Progress: data/study/holdout_aa/progress.log (one line per milestone; [running: ...] lists the parallel jobs).
#
#   setsid nohup pipeline/holdout_run.sh [PID_OF_RUNNING_AUDNZD_DOWNLOAD] > data/study/holdout_aa/logs/run.out 2>&1 < /dev/null &
#
# ONE-LOOK: nothing here reads a journal outcome column or the tester HTML report.
set -uo pipefail
REPO=/home/jack/hybrid_project
OUT=$REPO/data/study/holdout_aa; STATE=$OUT/state; P=$OUT/progress.log
FLEET=$REPO/data/qdm_csv/fleet; QDM=/home/jack/QDM; STAGE=$REPO/pipeline/oos_mt5_stage.sh
SYMS=(AUDNZD AUDCAD NZDCAD)
FIRST_PID="${1:-}"
mkdir -p "$STATE" "$OUT/logs"
cd "$REPO" || exit 1

running(){ local f l=(); for f in "$STATE"/run.*; do [[ -e "$f" ]] && l+=("${f##*/run.}"); done; echo "${l[*]:-nothing else}"; }
prog(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "$P"; }
progp(){ prog "$* [running: $(running)]"; }
on(){ touch "$STATE/run.$1"; }
off(){ rm -f "$STATE/run.$1"; }
qbusy(){ pgrep -x qdmcli >/dev/null 2>&1 || pgrep -f "/home/jack/QDM/j64" >/dev/null 2>&1; }
wait_q(){ while qbusy; do sleep 15; done; }
mt5_running(){ tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe; }
wait_mt5_free(){ local said=0; while mt5_running; do (( said )) || { prog "MT5 terminal64 is running - waiting for it to close (never killed)"; said=1; }; sleep 60; done; }
gate(){ local said=0; while [[ ! -f "$STATE/STAGE_READY" ]]; do (( said )) || { prog "waiting for the holdout stage script to be ready (STAGE_READY)"; said=1; }; sleep 20; done; }
rm -f "$STATE"/run.*

verify_m1(){  # price-only checks of the M1 CSV (oos_verify.py)
  local s=$1 f="$FLEET/$1-M1-No Session.csv" v="$FLEET/oos_logs/verify_$1.txt"
  on "verify-$s"; python3 "$FLEET/oos_verify.py" "$f" > "$v" 2>&1; off "verify-$s"
  prog "$s: M1 verify: $(grep -E '^header_ok' "$v" | tr -s ' ') | $(grep -E '^monotonic' "$v") | $(grep -E '^gaps>72h' "$v") |" \
       "$(grep -E '^weekday holes >=6h' "$v" | sed 's/ (no weekend, not holiday)//') | $(grep -E '^weekday holes >=24h' "$v")"
}
dl_end(){
  local s=$1
  if [[ -f "$FLEET/oos_logs/$s.done" ]]; then
    prog "$s: download end - store $(grep "$s: store final" "$FLEET/oos_batch.log" | tail -1 | sed 's/.*store final: //');" \
         "M1 export $(grep "$s: EXPORT rc" "$FLEET/oos_batch.log" | tail -1 | sed 's/.*EXPORT //')"
    verify_m1 "$s" &
    return 0
  fi
  prog "$s: download FAILED (no done marker; see data/qdm_csv/fleet/oos_batch.log)"; touch "$STATE/$s.prepfail"; return 1
}
export_sym(){  # qdmcli tick export (foreground, one qdmcli at a time), then the H4 build in the background
  local s=$1
  [[ -f "$STATE/$s.h4" ]] && return 0
  if [[ ! -f "$STATE/$s.exported" || ! -f "$REPO/data/mt5_ready/$s.csv" ]] && [[ ! -f "$STATE/$s.imported" ]]; then
    wait_q; on "tick-export-$s"; progp "$s: tick export start (qdmcli exportToMT5 TICK 2012.01.01..2026.10.03)"
    bash "$STAGE" --holdout --steps export "$s" >> "$OUT/stage.out" 2>&1
    off "tick-export-$s"
    [[ -f "$STATE/$s.exported" ]] || { prog "$s: tick export FAILED (see data/study/holdout_aa/stage.log)"; touch "$STATE/$s.prepfail"; return 1; }
    prog "$s: tick export end - $(grep "$s: .* ticks, first" "$OUT/stage.log" | tail -1 | sed 's/^\[[^]]*\] //')"
  fi
  ( on "h4-$s"; progp "$s: H4 build start (from the tick CSV)"
    bash "$STAGE" --holdout --steps h4 "$s" >> "$OUT/stage.out" 2>&1
    off "h4-$s"
    if [[ -f "$STATE/$s.h4" ]]; then prog "$s: H4 end - $(grep -E '^wrote ' "$OUT/logs/${s}_h4.log" | tail -1)"
    else prog "$s: H4 FAILED (see data/study/holdout_aa/logs/${s}_h4.log)"; touch "$STATE/$s.prepfail"; fi ) &
  H4PIDS+=($!)
}

mt5_lane(){
  gate
  for s in "${SYMS[@]}"; do
    while [[ ! -f "$STATE/$s.h4" && ! -f "$STATE/$s.prepfail" ]]; do sleep 30; done
    if [[ -f "$STATE/$s.aa" ]]; then prog "$s: AA journal already in holdout_aa - MT5 steps skipped"; continue; fi
    [[ -f "$STATE/$s.prepfail" ]] && { prog "$s: MT5 steps skipped (download / export / H4 failed)"; continue; }
    wait_mt5_free
    on "MT5-$s"
    bash "$STAGE" --holdout --steps import,aa "$s" >> "$OUT/stage.out" 2>&1
    off "MT5-$s"
    [[ -f "$STATE/$s.aa" ]] || prog "$s: MT5 steps did not complete (see data/study/holdout_aa/stage.log); re-run: pipeline/oos_mt5_stage.sh --holdout --steps import,aa $s"
  done
}

H4PIDS=()
prog "=== holdout_run start (pid $$). qdmcli lane strictly serial (one process owns QDM's SQLite data.db; concurrent qdmcli safety is not documented), AUDCAD+NZDCAD in ONE qdmcli call; H4/verify concurrent; MT5 one terminal, pipelined"

# ---- 1. AUDNZD download (already running under its own driver)
if [[ -n "$FIRST_PID" ]] && [[ -d /proc/$FIRST_PID ]]; then
  on "download-AUDNZD"; progp "waiting for the running AUDNZD download (driver pid $FIRST_PID)"
  while [[ -d /proc/$FIRST_PID ]]; do sleep 15; done; off "download-AUDNZD"
fi
if [[ ! -f "$FLEET/oos_logs/AUDNZD.done" ]]; then
  wait_q; on "download-AUDNZD"; progp "AUDNZD: download start (retry, oos_download.sh)"
  ( cd "$QDM" && SYMS=AUDNZD bash "$FLEET/oos_download.sh" ); off "download-AUDNZD"
fi
dl_end AUDNZD

# ---- 2. MT5 lane in the background (waits for each symbol's H4)
mt5_lane & MT5PID=$!

# ---- 3. AUDNZD tick export first when the stage is ready, so its import / AA overlap the other two downloads
EXPORTED_FIRST=0
if [[ -f "$STATE/STAGE_READY" && -f "$FLEET/oos_logs/AUDNZD.done" ]]; then export_sym AUDNZD; EXPORTED_FIRST=1; fi

# ---- 4. AUDCAD + NZDCAD: ONE qdmcli update, then the per-symbol driver (coverage check, re-update singly if short, M1 export)
if [[ ! -f "$FLEET/oos_logs/AUDCAD.done" || ! -f "$FLEET/oos_logs/NZDCAD.done" ]]; then
  wait_q; on "download-AUDCAD+NZDCAD"
  progp "AUDCAD, NZDCAD: download start - ONE qdmcli -data action=update symbols=AUDCAD,NZDCAD (log oos_logs/AUDCAD_NZDCAD_update.log)"
  ( cd "$QDM" && ./qdmcli -data action=update symbols=AUDCAD,NZDCAD </dev/null > "$FLEET/oos_logs/AUDCAD_NZDCAD_update.log" 2>&1 ); rc=$?
  progp "AUDCAD, NZDCAD: combined update finished rc=$rc - now coverage check + M1 export per symbol (oos_download.sh)"
  ( cd "$QDM" && SYMS="AUDCAD NZDCAD" bash "$FLEET/oos_download.sh" ); off "download-AUDCAD+NZDCAD"
fi
dl_end AUDCAD; dl_end NZDCAD

# ---- 5. remaining tick exports (serial on qdmcli) + H4 builds (concurrent)
gate
(( EXPORTED_FIRST )) || { [[ -f "$FLEET/oos_logs/AUDNZD.done" ]] && export_sym AUDNZD; }
for s in AUDCAD NZDCAD; do [[ -f "$FLEET/oos_logs/$s.done" ]] && export_sym "$s"; done
for p in "${H4PIDS[@]}"; do wait "$p"; done

# ---- 6. finish: MT5 lane, then build / drag / qa for the holdout set
wait "$MT5PID"
progp "holdout inputs: build / drag / qa start"
bash "$STAGE" --holdout --steps final >> "$OUT/stage.out" 2>&1
prog "holdout inputs: build / drag / qa end (data/study/holdout_aa/qa.txt)"
wait
prog "=== holdout_run finished. AA journals: $(ls "$OUT"/AA_*.csv 2>/dev/null | xargs -r -n1 basename | paste -sd' ')"
