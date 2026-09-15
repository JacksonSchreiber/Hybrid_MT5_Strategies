#!/usr/bin/env bash
# Serialized QDM M1 fleet export for the breakout-candidate gate (offline, no MT5).
# Fast in-store exports first, then the slow add+download for USDJPY/XAUUSD.
# One JVM at a time. Frozen ranges = wide; qdmcli emits only what exists.
set -uo pipefail
QDM=/home/jack/QDM
OUT=/home/jack/hybrid_project/data/qdm_csv/fleet
LOG=$OUT/qdm_fleet.log
mkdir -p "$OUT"; : > "$LOG"
FROM=2012.01.01; TO=2026.09.09
cd "$QDM" || exit 1

export1(){ # $1 = QDM symbol name
  echo "[$(date +%H:%M:%S)] EXPORT $1 M1 $FROM..$TO" | tee -a "$LOG"
  timeout 900 ./qdmcli -data action=export symbols="$1" timeframe=M1 \
    datefrom=$FROM dateto=$TO outputdir="$OUT" \
    format="Generic bar format (comma delimited)" >>"$LOG" 2>&1
  echo "[$(date +%H:%M:%S)] EXPORT $1 rc=$? -> $(ls -la "$OUT/$1-M1-No Session.csv" 2>/dev/null | awk '{print $5}')" | tee -a "$LOG"
}
download1(){ # $1 = QDM symbol name (not in store): add + update (full history)
  echo "[$(date +%H:%M:%S)] ADD $1" | tee -a "$LOG"
  timeout 600 ./qdmcli -symbol action=add symbols="$1" datasource=dukascopy datatype=TICK >>"$LOG" 2>&1
  echo "[$(date +%H:%M:%S)] ADD $1 rc=$?" | tee -a "$LOG"
  echo "[$(date +%H:%M:%S)] UPDATE(download) $1 ..." | tee -a "$LOG"
  timeout 9000 ./qdmcli -data action=update symbols="$1" >>"$LOG" 2>&1
  echo "[$(date +%H:%M:%S)] UPDATE $1 rc=$?" | tee -a "$LOG"
}

# --- in-store symbols: EURUSD GBPUSD USOIL(LIGHT) US500 US100 -> export directly ---
for s in EURUSD GBPUSD LIGHTCMDUSD USA500IDXUSD USATECHIDXUSD; do export1 "$s"; done
echo "[$(date +%H:%M:%S)] === in-store exports DONE ===" | tee -a "$LOG"

# --- missing symbols: USDJPY XAUUSD -> add + download + export (slow) ---
for s in USDJPY XAUUSD; do download1 "$s"; export1 "$s"; done
echo "[$(date +%H:%M:%S)] === ALL DONE ===" | tee -a "$LOG"
