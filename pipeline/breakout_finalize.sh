#!/usr/bin/env bash
# Wait for the JPY/XAU M1 exports (or the T+3h deadline), then run the ONE official
# breakout gate evaluation over all available symbols. Reads CSVs only (no QDM JVM).
set -uo pipefail
FLEET=/home/jack/hybrid_project/data/qdm_csv/fleet
DEADLINE=$(( $(date +%s) + 3*3600 ))   # T+3h from finalizer start (data launched ~same time)
jpy="$FLEET/USDJPY-M1-No Session.csv"
xau="$FLEET/XAUUSD-M1-No Session.csv"
echo "[finalize] waiting for JPY/XAU M1 (deadline $(date -d @$DEADLINE +%H:%M))..."
while :; do
  [[ -s "$jpy" && -s "$xau" ]] && { echo "[finalize] both present -> evaluating all 7"; break; }
  (( $(date +%s) >= DEADLINE )) && { echo "[finalize] DEADLINE hit -> evaluating available only"; break; }
  sleep 60
done
# small settle so a just-written CSV is fully flushed
sleep 5
cd /home/jack/hybrid_project
echo "[finalize] symbols present:"; ls "$FLEET"/*.csv 2>/dev/null | xargs -n1 basename
python3 pipeline/breakout_backtest.py run 2>&1 | tail -25
echo "[finalize] DONE -> data/study/breakouts_report.md"
