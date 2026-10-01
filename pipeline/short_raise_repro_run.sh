#!/usr/bin/env bash
# short_raise_repro_run.sh - coach item 25 guard: EA real-tick runs, live stack, shorts stop raise off / on, two symbols.
# Needs the local MT5 closed (mt5_verify refuses otherwise). ~4 tester runs; results -> data/study/short_raise_repro/.
set -u
cd "$(dirname "$0")/.."
OUT=data/study/short_raise_repro; mkdir -p $OUT
CJ=/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal
for sym in ${@:-EURUSD.dk US100.dk}; do
  for mode in off on; do
    if [[ $mode == on ]]; then export SHORT_RAISE=0.25; else unset SHORT_RAISE; fi
    STAGED=1 PYR=1 TC_BANK=0.25 pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --symbol $sym \
      --from 2016.08.01 --to 2026.06.30 --model 4 --timeout 7200 > $OUT/${sym}_${mode}.log 2>&1
    J=$(ls -t $CJ/AA_${sym}_*.csv | grep -vE 'actions|inv|part|delays' | head -1)
    cp "$J" $OUT/${sym}_${mode}.csv; echo "$sym $mode -> $(wc -l < $OUT/${sym}_${mode}.csv) lines"
  done
done
python3 pipeline/short_raise_repro_check.py ${@:-EURUSD.dk US100.dk}
