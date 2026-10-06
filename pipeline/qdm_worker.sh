#!/usr/bin/env bash
# qdm_worker.sh <n> - one download worker on the extra QDM install /home/jack/QDM<n> (launcher qdm<n>cli; see docs/qdm_parallel_downloads.md).
# Several workers share one queue (data/qdm_csv/fleet/pool_queue.txt, symbols separated by spaces/lines, # starts a comment). Under flock a worker
# claims the next symbol (oos_logs/<SYM>.claim names the worker; a restarted worker resumes its own claim first), downloads it ALONE (one
# qdmcli call per symbol - a call never lets go of the install until it finishes), checks the store coverage, exports M1 to the fleet dir,
# writes <SYM>.done. Datatype M1 by default (QDM_DATATYPE=TICK for ticks); a symbol registered with the other datatype is deleted and
# re-added. Coverage is accepted when the last bar is recent and either it starts by 2012 or the last update completed with no ERROR
# (many symbols start later on Dukascopy, e.g. DAX 2013-09-30). (A code QDM rejects, or an empty store, gets <SYM>.failed instead - delete it to retry). Exits when nothing is left to claim. qdm_pool.sh starts these as capped user units; by hand:
#   systemd-run --user --unit=qdm-worker-2 --collect -p MemoryMax=1600M -p MemorySwapMax=0 -p CPUQuota=100% -p Nice=19 \
#     -p IOSchedulingClass=idle bash /home/jack/hybrid_project/pipeline/qdm_worker.sh 2
# - DISK GUARD: M1 waits while C: has under 25 GB free; TICK under 53 GB when the MT5 AA stage is running (its pre-export guard needs
#   ~52 GB), else under 45 GB
# - never kills qdmcli (an update records coverage in data.db only on a clean exit)
set -uo pipefail
n=${1:?usage: qdm_worker.sh <n>}; W=qdm$n; QDM=/home/jack/QDM$n; CLI=./qdm${n}cli
FLEET=/home/jack/hybrid_project/data/qdm_csv/fleet; LOGD=$FLEET/oos_logs; P=$FLEET/pool_progress.log
QUEUE=${QUEUE:-$FLEET/pool_queue.txt}; LOCK=$FLEET/pool_queue.lock
FROM=2012.01.01; TO=${TO:-$(date -d yesterday +%Y.%m.%d)}; MIN_LAST=${MIN_LAST:-$(date -d '10 days ago' +%Y.%m.%d)}
DTYPE=${QDM_DATATYPE:-M1}   # M1 comes mostly from StrategyQuant's CDN (DAX 2013-2026 in 5 min, no 429s); TICK goes hour-file by hour-file to
                            # Dukascopy, which throttles Mullvad exits hard (2026-10-05: <1 day/min). Use TICK only when ticks are needed (MT5 import).
mkdir -p "$LOGD"; cd "$QDM" || exit 1; [[ -x $CLI ]] || { echo "no launcher $QDM/$CLI" >&2; exit 1; }
prog(){ printf '[%s] %s: %s\n' "$(date '+%F %T')" "$W" "$*" >> "$P"; }
free_gb(){ df -BG --output=avail /mnt/c | tail -1 | tr -dc '0-9'; }
need_gb(){ [[ $DTYPE == M1 ]] && { echo 25; return; }   # M1 is small (DAX 2013-26: 92 MB store); ticks are GBs per symbol
  pgrep -f 'pipeline/(fleet2_aa_run|oos_mt5_stage)\.sh' >/dev/null && echo 53 || echo 45; }
disk_gate(){ local said=0; while (( $(free_gb) < $(need_gb) )); do
  (( said )) || { prog "waiting: C: free $(free_gb) GB < $(need_gb) GB"; said=1; }; sleep 120; done; }
claim(){ (   # prints the claimed symbol, or nothing when the queue is done
  flock 9; syms=$(sed 's/#.*//' "$QUEUE" | tr -s ' \t' '\n' | grep .)
  for s in $syms; do [[ ! -f $LOGD/$s.done && "$(cat "$LOGD/$s.claim" 2>/dev/null)" == "$W" ]] && { echo "$s"; exit; }; done
  for s in $syms; do [[ -f $LOGD/$s.done || -f $LOGD/$s.claim || -f $LOGD/$s.failed ]] && continue; echo "$W" > "$LOGD/$s.claim"; echo "$s"; exit; done
) 9>"$LOCK"; }
cdn_from(){   # first year StrategyQuant's CDN holds for the symbol (M1). Years before it come day by day from Dukascopy, which throttles
  local y     # Mullvad exits (EUSIDXEUR 2011-14: ~0.5 day/min). Logged only: QDM's update ignores datefrom and has no start-date setting.
  for y in $(seq "${FROM:0:4}" "$(date +%Y)"); do
    [[ $(curl -s -m 15 -o /dev/null -I -w '%{http_code}' "https://cdn.strategyquantcdn.com/data/dukascopy/m1/$1/$y.zip") == 200 ]] && { echo "$y.01.01"; return; }
  done; echo "$FROM"; }
store(){   # "first last records" for the exact symbol in THIS install's registry
  $CLI -symbol action=list </dev/null 2>/dev/null > "$LOGD/_list_$W.txt"
  python3 - "$1" "$LOGD/_list_$W.txt" <<'EOF'
import csv, sys
for r in csv.reader(open(sys.argv[2], errors='replace')):
    if r and r[0] == sys.argv[1] and len(r) >= 8: print(r[4] or '-', r[5] or '-', r[7] or 0); break
else: print('- - 0')
EOF
}
gaps(){ python3 - "$1" <<'EOF'   # weekdays with no M1 bar (excl. 25 Dec / 1 Jan): interior holes left by failed downloads
import sys, datetime as dt
days = set()
for line in open(sys.argv[1], errors='replace'):   # "Generic bar format": 20120119,16:11:00,O,H,L,C,V
    d = line[:8]
    if d.isdigit(): days.add(f'{d[:4]}-{d[4:6]}-{d[6:]}')
if not days: print('no bars'); sys.exit()
a, b = dt.date.fromisoformat(min(days)), dt.date.fromisoformat(max(days)); miss = []
while a <= b:
    if a.weekday() < 5 and (a.month, a.day) not in ((12, 25), (1, 1)) and a.isoformat() not in days: miss.append(a.isoformat())
    a += dt.timedelta(1)
print(f'{len(miss)} empty weekdays' + (f' (e.g. {", ".join(miss[:5])})' if miss else ''))
EOF
}
prog "=== start (pid $$, install $QDM, $(grep -h socksProxyHost "$QDM/qdmcli.config" | tr -d '\r' | sed 's/.*=/socks /' || echo 'no socks'))"
while s=$(claim) && [[ -n $s ]]; do
  disk_gate
  if ! $CLI -symbol action=list </dev/null 2>/dev/null | grep -q "^$s,$s,$DTYPE,"; then   # missing, or registered with the other datatype
    $CLI -symbol action=list </dev/null 2>/dev/null | grep -q "^$s," && $CLI -symbol action=delete "symbols=$s" </dev/null > "$LOGD/${s}_${W}_delete.log" 2>&1
    $CLI -symbol action=add "symbols=$s" datasource=dukascopy datatype=$DTYPE </dev/null > "$LOGD/${s}_${W}_add.log" 2>&1
    $CLI -symbol action=list </dev/null 2>/dev/null | grep -q "^$s,$s,$DTYPE," || { prog "$s: SKIPPED - Dukascopy/QDM did not accept the code"; echo "$W: code not accepted" > "$LOGD/$s.failed"; rm -f "$LOGD/$s.claim"; continue; }
    prog "$s: registered ($DTYPE)"
  fi
  UFROM=$FROM; [[ $DTYPE == M1 ]] && UFROM=$(cdn_from "$s"); [[ $UFROM != "$FROM" ]] && prog "$s: CDN holds it from ${UFROM:0:4} - years before that come from Dukascopy directly (slow: ~0.5 day/min)"
  prev=""; ok=0; clean=0
  for att in 1 2 3 4 5 6; do
    read -r first last recs <<<"$(store "$s")"
    if [[ $recs != 0 && ! $last < $MIN_LAST ]] && { [[ $first < 2012.01.04 ]] || (( clean )); }; then ok=1; break; fi
    if (( att >= 3 )) && [[ $recs != 0 && $prev == "$first $last $recs" && ! $last < $MIN_LAST ]]; then prog "$s: coverage unchanged after a re-run (source starts $first) - accepting"; ok=1; break; fi
    prev="$first $last $recs"; disk_gate; t0=$(date +%s)
    prog "$s: update attempt $att (store $first..$last, $recs records)"
    $CLI -data action=update symbols="$s" </dev/null > "$LOGD/${s}_${W}_update_$att.log" 2>&1; rc=$?
    clean=0; grep -q "^$s, Completed" "$LOGD/${s}_${W}_update_$att.log" && ! grep -q 'ERROR' "$LOGD/${s}_${W}_update_$att.log" && clean=1
    prog "$s: update attempt $att rc=$rc in $(( ($(date +%s) - t0) / 60 )) min, $(grep -c 'Error code: 429' "$LOGD/${s}_${W}_update_$att.log") rate-limit errors$( ((clean)) && echo ', completed clean')"
    if grep -q 'parsing descriptor XML' "$LOGD/${s}_${W}_update_$att.log"; then
      prog "$s: QDM could not read its Dukascopy descriptor through this exit (Cloudflare block?) - claim kept, worker exits; qdm_pool.sh re-provisions the install with another exit"
      exit 3; fi
  done
  read -r first last recs <<<"$(store "$s")"
  (( ok )) || prog "$s: coverage short after 6 updates ($first..$last, $recs records) - exporting what there is"
  [[ $recs == 0 ]] && { prog "$s: FAIL - store empty after 6 updates (oos_logs/$s.failed; delete it to retry)"; echo "$W: store empty" > "$LOGD/$s.failed"; rm -f "$LOGD/$s.claim"; continue; }
  f="$FLEET/$s-M1-No Session.csv"
  for att in 1 2 3; do
    disk_gate; rm -f "$f"
    $CLI -data action=export symbols="$s" timeframe=M1 datefrom=$FROM dateto=$TO outputdir="$FLEET" \
      format="Generic bar format (comma delimited)" </dev/null > "$LOGD/${s}_${W}_export_$att.log" 2>&1
    [[ -s $f ]] && break; sleep 60
  done
  if [[ -s $f ]]; then touch "$LOGD/$s.done"; rm -f "$LOGD/$s.claim"
    prog "$s: DONE - store $first..$last ($recs records), M1 CSV $(du -h "$f" | cut -f1), $(gaps "$f")"
  else prog "$s: export produced no file - left claimed; re-run the worker to retry"; break; fi
done
prog "=== queue done - exit"
