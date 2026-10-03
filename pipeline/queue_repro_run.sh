#!/usr/bin/env bash
# queue_repro_run.sh - coach 2026-10-03 item 5: EA real-tick runs of the coach-queue rules, live stack (staged, pyramid, TrendCont bank
# 25%, shorts raise 0.25), each rule alone ON vs OFF, two symbols; plus a no-shorts-raise run (item 25's corrected increment on the
# fixed staged-add build). Needs the local MT5 closed (mt5_verify refuses otherwise). ~54 min per run.
#   pipeline/queue_repro_run.sh [EURUSD.dk US100.dk]   -> data/study/queue_repro/<SYM>_{off,A,B,I,nosr}.csv
set -u
cd "$(dirname "$0")/.."
OUT=data/study/queue_repro; mkdir -p $OUT
CJ=/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal
for sym in ${@:-EURUSD.dk US100.dk}; do
  for mode in off A B I nosr; do
    unset ADD_GATE SHORT_CUT IMPR_ADD; SR=0.25
    case $mode in A) export ADD_GATE=1;; B) export SHORT_CUT=0.5;; I) export IMPR_ADD=0.5;; nosr) SR=;; esac
    [[ -f $OUT/${sym}_${mode}.csv ]] && { echo "$sym $mode: exists, skipped"; continue; }
    if [[ -n "$SR" ]]; then export SHORT_RAISE=$SR; else unset SHORT_RAISE; fi
    STAGED=1 PYR=1 TC_BANK=0.25 pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --symbol $sym \
      --from 2016.08.01 --to 2026.06.30 --model 4 --timeout 7200 > $OUT/${sym}_${mode}.log 2>&1
    J=$(ls -t $CJ/AA_${sym}_*.csv | grep -vE 'actions|inv|part|delays' | head -1)
    if grep -q "tester finished" $OUT/${sym}_${mode}.log; then
      cp "$J" $OUT/${sym}_${mode}.csv; [[ -f "${J%.csv}.actions.csv" ]] && cp "${J%.csv}.actions.csv" $OUT/${sym}_${mode}.actions.csv
      echo "$sym $mode -> $(wc -l < $OUT/${sym}_${mode}.csv) lines"
    else echo "$sym $mode: TESTER DID NOT FINISH (see $OUT/${sym}_${mode}.log)"; fi
  done
done
python3 pipeline/queue_repro_check.py ${@:-EURUSD.dk US100.dk}
