#!/usr/bin/env bash
#
# mt5_build_ea.sh - sync the EA sources into the MT5 tree and compile BOTH builds
# (HybridForwardTest = tester/popup build, HybridForwardTest-live = Phase-3 LIVE build).
#
#   ./mt5_build_ea.sh            # sync + compile both, fail on any error/warning
#   ./mt5_build_ea.sh --no-sync  # compile what is already in the MT5 tree
#
# REFUSES to run while terminal64 is running: the -live wrapper #includes
# HybridForwardTest.mq5, and touching either file under MQL5\Experts while a
# tester run uses it aborts that run silently (no OnDeinit, .part journal left).
#
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
MT5_DATA_WIN='C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB'
METAEDITOR="/mnt/c/Program Files/OANDA MetaTrader 5/MetaEditor64.exe"
SYNC=true
[[ "${1:-}" == "--no-sync" ]] && SYNC=false
log(){ printf '%s\n' "$*" >&2; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
tasklist.exe /FI "IMAGENAME eq terminal64.exe" 2>/dev/null | grep -qi terminal64.exe && die "terminal64 is running - refusing to touch MQL5\\Experts (would abort a tester run). Wait for it to exit."
[[ -x "$METAEDITOR" ]] || die "MetaEditor not found at $METAEDITOR"

if $SYNC; then
  cp -f "$REPO/mql5/experts/HybridForwardTest.mq5"      "$MT5_DATA/MQL5/Experts/"
  cp -f "$REPO/mql5/experts/HybridForwardTest-live.mq5" "$MT5_DATA/MQL5/Experts/"
  if [[ -d "$REPO/mql5/include/Hybrid" ]]; then mkdir -p "$MT5_DATA/MQL5/Include/Hybrid"; cp -rf "$REPO/mql5/include/Hybrid/." "$MT5_DATA/MQL5/Include/Hybrid/"; fi
  log "synced sources -> $MT5_DATA/MQL5/Experts (+Include/Hybrid)"
fi

rc=0
for n in HybridForwardTest HybridForwardTest-live; do
  ex5="$MT5_DATA/MQL5/Experts/$n.ex5"; logf="$MT5_DATA/MQL5/Experts/$n.log"
  before=$(stat -c %Y "$ex5" 2>/dev/null || echo 0)
  rm -f "$logf"
  "$METAEDITOR" /compile:"$MT5_DATA_WIN\\MQL5\\Experts\\$n.mq5" /log 2>/dev/null || true
  for i in $(seq 1 30); do [[ -s "$logf" ]] && break; sleep 1; done
  res=$(iconv -f UTF-16LE -t UTF-8 "$logf" 2>/dev/null | grep -E 'Result|error|warning' | tail -3 || true)
  after=$(stat -c %Y "$ex5" 2>/dev/null || echo 0)
  size=$(stat -c %s "$ex5" 2>/dev/null || echo 0)
  if echo "$res" | grep -qE 'Result: 0 errors, 0 warnings' && (( after > before )); then
    log "OK   $n.ex5  ${size} B  ($res)"
  else
    log "FAIL $n: $res"; iconv -f UTF-16LE -t UTF-8 "$logf" 2>/dev/null | grep -E 'error|warning' | head -20 >&2; rc=1
  fi
done
exit $rc
