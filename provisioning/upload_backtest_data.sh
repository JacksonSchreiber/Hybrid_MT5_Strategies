#!/usr/bin/env bash
# upload_backtest_data.sh - the backtest tab's data (built locally by pipeline/backtest_*.py) -> <box root>\backtest\ (live/backtest.py
# data_dir default). Staged into backtest.new, then swapped in, so the server never reads a half-written set. Sessions/reports live
# under <root>\web\backtest\ and are not touched. ~260 MB at ~9 MB/s. Needs the WireGuard tunnel and the hybridops key.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; D="$REPO/data/backtest"
KEY="$HOME/.ssh/hybrid_vps_ed25519"; HOST="hybridops@10.77.0.1"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15 $HOST"; SCP="scp -q -C -i $KEY -o BatchMode=yes"
ROOT='C:/Users/hybridops/AppData/Roaming/MetaQuotes/Terminal/Common/Files/live'
FILES=(split_v1.json cards_v3.json.gz results_v1.json.gz menu_v1.json info_v1.json)
for f in "${FILES[@]}" frames_v1/index.json; do [[ -f "$D/$f" ]] || { echo "missing $D/$f" >&2; exit 1; }; done
$SSH "Remove-Item -Recurse -Force '$ROOT/backtest.new' -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force -Path '$ROOT/backtest.new/frames_v1' | Out-Null; 'staging ok'" | tr -d '\r'
for f in "${FILES[@]}"; do $SCP "$D/$f" "$HOST:$ROOT/backtest.new/"; echo "  $f"; done
$SCP "$D"/frames_v1/* "$HOST:$ROOT/backtest.new/frames_v1/"; echo "  frames_v1 ($(ls "$D"/frames_v1 | wc -l) files)"
# verify sizes on the box against the local files before swapping
loc=$(cd "$D" && { for f in "${FILES[@]}"; do stat -c %s "$f"; done; for f in frames_v1/*; do stat -c %s "$f"; done; } | paste -sd+ | bc)
rem=$($SSH "(Get-ChildItem -Recurse -File '$ROOT/backtest.new' | Measure-Object -Property Length -Sum).Sum" | tr -d '\r')
[[ "$loc" == "$rem" ]] || { echo "SIZE MISMATCH local $loc vs box $rem - not swapped" >&2; exit 1; }
$SSH "Remove-Item -Recurse -Force '$ROOT/backtest.old' -ErrorAction SilentlyContinue; if (Test-Path '$ROOT/backtest') { Rename-Item '$ROOT/backtest' 'backtest.old' }; Rename-Item '$ROOT/backtest.new' 'backtest'; Remove-Item -Recurse -Force '$ROOT/backtest.old' -ErrorAction SilentlyContinue; 'swapped'" | tr -d '\r'
echo "backtest data live on the box ($loc bytes)"
