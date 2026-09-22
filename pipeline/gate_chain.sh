#!/usr/bin/env bash
# gate_chain.sh - full EA gate chain: build both EAs, tester parity (normal + -live) vs data/baselines/m1_pre_refactor, live self-test + validator.
set -u
cd "$(dirname "$0")/.."
BASE=data/baselines/m1_pre_refactor.csv; CJ=/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal
R=/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/live_selftest
pipeline/mt5_build_ea.sh 2>&1 | tail -2 || { echo "BUILD FAIL"; exit 1; }
for ex in HybridForwardTest HybridForwardTest-live; do
  pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ --symbol EURUSD.dk --from 2024.01.01 --to 2024.06.30 --model 4 --timeout 900 --expert $ex >/dev/null 2>&1
  J=$(ls -t $CJ/AA_EURUSD.dk_*.csv | grep -vE 'actions|inv|part|delays' | head -1)
  if diff -q "$J" $BASE >/dev/null && diff -q "${J%.csv}.actions.csv" ${BASE%.csv}.actions.csv >/dev/null; then echo "PARITY PASS ($ex)"; else echo "PARITY FAIL ($ex)"; fi
done
ST=$(pipeline/live_selftest.sh --selftest --poll 20 --symbol EURUSD.dk --from 2024.01.01 --to 2024.06.30 --timeout 2400 2>&1)
echo "$ST" | grep -E 'finished|signals:|tasks pending|journal rows|WARN'
# a timed-out self-test leaves a partial root: validating it would PASS on part of the window (2026-09-21) - fail instead
if echo "$ST" | grep -q "TIMEOUT"; then echo "SELFTEST TIMEOUT - the terminal was still running; gate FAILED (re-run when it has exited)"; echo "validator exit=1"; exit 1; fi
python3 pipeline/live_queue_check.py --root $R --symbol EURUSD.dk --expect-selftest --expect-live-cols | tail -4
echo "validator exit=${PIPESTATUS[0]}"
