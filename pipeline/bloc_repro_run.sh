#!/usr/bin/env bash
# bloc_repro_run.sh - trader 2026-10-04: the EA's real-tick reproduction of the commodity-bloc T+L1.0 rules (InpBlocTL) vs the study
# (pipeline/cbloc_timestop_study.run_h "live" vs "T+L1.0"). Live stack (STAGED=1 PYR=1 TC_BANK=0.25 SHORT_RAISE=0.25), all four
# detectors, bloc OFF vs ON. Needs the local MT5 closed (mt5_verify refuses otherwise).
#   pipeline/bloc_repro_run.sh [AUDUSD.dk] [2018.01.01] [2026.06.30]   -> data/study/bloc_repro/<SYM>_{off,bloc}.csv
set -u
cd "$(dirname "$0")/.."
SYM=${1:-AUDUSD.dk}; FROM=${2:-2018.01.01}; TO=${3:-2026.06.30}
OUT=data/study/bloc_repro; mkdir -p $OUT
CJ=/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal
for mode in off bloc; do
  [[ -f $OUT/${SYM}_${mode}.csv ]] && { echo "$SYM $mode: exists, skipped"; continue; }
  if [[ $mode == bloc ]]; then export BLOC_TL=1; else unset BLOC_TL; fi
  STAGED=1 PYR=1 TC_BANK=0.25 SHORT_RAISE=0.25 pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --symbol $SYM \
    --from $FROM --to $TO --model 4 --timeout 9000 > $OUT/${SYM}_${mode}.log 2>&1
  J=$(ls -t $CJ/AA_${SYM}_*.csv | grep -vE 'actions|inv|part|delays' | head -1)
  if grep -q "tester finished" $OUT/${SYM}_${mode}.log; then
    cp "$J" $OUT/${SYM}_${mode}.csv; [[ -f "${J%.csv}.actions.csv" ]] && cp "${J%.csv}.actions.csv" $OUT/${SYM}_${mode}.actions.csv
    echo "$SYM $mode -> $(wc -l < $OUT/${SYM}_${mode}.csv) lines"
  else echo "$SYM $mode: TESTER DID NOT FINISH (see $OUT/${SYM}_${mode}.log)"; fi
done
python3 pipeline/bloc_repro_check.py $SYM
