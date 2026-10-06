#!/usr/bin/env bash
# qdm_pool.sh [queue_file] - download a symbol queue on as many parallel QDM installs as memory allows (trader 2026-10-05: "keep increasing
# it and if it gets close to the limit stop making new installs"). How-to: docs/qdm_parallel_downloads.md.
# Every STEP_S (default 180 s) it adds ONE worker when all of these hold:
#   - a symbol in the queue is still unclaimed (no .done/.claim/.failed in oos_logs)
#   - every worker unit (running a qdmcli call OR waiting on the disk guard, which costs no RAM yet) is counted at the biggest worker
#     RSS seen (900 MB until one is measured): (workers + 1) x that <= MemAvailable + RSS of the clones' JVMs now - RESERVE_MB - MARGIN_MB.
#     (Counting only MemAvailable let workers pile up behind the disk guard and then all start their JVMs at once.)
#   - the install number stays <= MAX_N
# A worker = install /home/jack/QDM<n> (made or refreshed by qdm_clone.sh with a fresh Mullvad SOCKS5 exit - tested by fetching QDM's
# Dukascopy descriptor XML through it, since Cloudflare serves some Mullvad IPs an HTML challenge - that no other install
# uses) + pipeline/qdm_worker.sh <n> as the capped user unit qdm-worker-<n>. The pool never stops a worker, it only stops adding; it exits
# when the queue is done and no worker unit is left. Installs whose unit is gone but which still hold a claim are restarted first, within
# the same memory budget (their partial download lives in that install).
#   RESERVE_MB (default 4000) keeps room for the MT5 stage while it runs: its main-install export (-Xmx2g, ~2.4 GB) and its H4 build from
#   the tick CSV (oos_h4_from_ticks.py: peaked ~3.8 GB for USDNOK, 464M ticks - this overran a 2400 MB reserve on 2026-10-05); IDLE_RESERVE_MB
#   (default 600) otherwise. WORKER_MEMMAX caps each worker unit (default 1600M).
# Run it as a user unit itself:
#   systemd-run --user --unit=qdm-pool --collect -p Nice=10 bash /home/jack/hybrid_project/pipeline/qdm_pool.sh
set -uo pipefail
REPO=/home/jack/hybrid_project; FLEET=$REPO/data/qdm_csv/fleet; LOGD=$FLEET/oos_logs; P=$FLEET/pool_progress.log
QUEUE=${1:-$FLEET/pool_queue.txt}; STEP_S=${STEP_S:-180}; RESERVE_MB=${RESERVE_MB:-4000}; MARGIN_MB=${MARGIN_MB:-600}
MAX_N=${MAX_N:-40}; WMEM=${WORKER_MEMMAX:-1600M}
mkdir -p "$LOGD"
prog(){ printf '[%s] pool: %s\n' "$(date '+%F %T')" "$*" >> "$P"; }
reserve_mb(){ pgrep -f 'pipeline/(fleet2_aa_run|oos_mt5_stage)\.sh' >/dev/null && echo "$RESERVE_MB" || echo "${IDLE_RESERVE_MB:-600}"; }
avail_mb(){ awk '/^MemAvailable/{print int($2/1024)}' /proc/meminfo; }
unit_active(){ systemctl --user is-active --quiet "qdm-worker-$1.service"; }
active_ns(){ systemctl --user list-units --type=service --state=active --no-legend 'qdm-worker-*' 2>/dev/null | grep -oE 'qdm-worker-[0-9]+' | cut -d- -f3; }
rss_sum(){ ps -eo comm=,rss= 2>/dev/null | awk '$1 ~ /^qdm[0-9]+cli$/ {t += $2} END {print int(t / 1024)}'; }
rss_max(){ local m=0 r n; for n in $(active_ns); do r=$(ps -C "qdm${n}cli" -o rss= 2>/dev/null | sort -n | tail -1); r=$(( ${r:-0} / 1024 )); (( r > m )) && m=$r; done; echo $m; }
queue_syms(){ sed 's/#.*//' "$QUEUE" | tr -s ' \t' '\n' | grep .; }
unclaimed(){ local c=0 s; for s in $(queue_syms); do [[ -f $LOGD/$s.done || -f $LOGD/$s.claim || -f $LOGD/$s.failed ]] || c=$((c + 1)); done; echo $c; }
socks_of(){ grep -h socksProxyHost "/home/jack/QDM$1/qdmcli.config" 2>/dev/null | tr -d '\r' | sed 's/.*=//'; }
DESC=http://autoupdate.strategyquant.com/dukascopy/dukascopy_ver3.xml   # QDM fetches this first; some Mullvad IPs get a Cloudflare HTML page
socks_ok(){ [[ -n $1 ]] && curl -s -m 20 --socks5 "$1:1080" -o /dev/null -w '%{content_type}' "$DESC" 2>/dev/null | grep -q xml; }
pick_socks(){   # a working Mullvad SOCKS5 exit no install uses; European relays first (Dukascopy is in Geneva), one per city in turn
  local used h ip; used=" $(grep -h socksProxyHost /home/jack/QDM[0-9]*/qdmcli.config 2>/dev/null | tr -d '\r' | sed 's/.*=//' | tr '\n' ' ') "
  for h in $(mullvad relay list 2>/dev/null | python3 -c '
import re, sys, itertools
pref = "ch de nl gb fr se no dk at be es it pl cz fi ie pt ro bg hu rs hr si sk ee lv lt gr cy lu al md ua".split()
city = {}
for h in re.findall(r"\b([a-z]{2}-[a-z]{3}-wg-\d{3})\b", sys.stdin.read()): city.setdefault(h[:6], []).append(h)
keys = sorted(city, key=lambda k: (pref.index(k[:2]) if k[:2] in pref else 99, k))
for row in itertools.zip_longest(*[city[k] for k in keys]):
    for h in row:
        if h: print(h)'); do
    ip=$(getent hosts "${h/-wg-/-wg-socks5-}.relays.mullvad.net" | awk '{print $1; exit}')
    [[ -z $ip || $used == *" $ip "* ]] && continue
    socks_ok "$ip" && { echo "$ip $h"; return 0; }
  done; return 1; }
start_worker(){   # n -> install refreshed/made with a working socks exit, worker unit started
  local n=$1 s h
  s=$(socks_of "$n")
  if [[ ! -d /home/jack/QDM$n ]] || ! socks_ok "$s"; then
    read -r s h <<<"$(pick_socks)" || true
    [[ -z ${s:-} ]] && { prog "no free working Mullvad SOCKS exit - not adding QDM$n"; return 1; }
    bash "$REPO/pipeline/qdm_clone.sh" "$n" "$s" >> "$P" 2>&1 || { prog "qdm_clone.sh $n failed"; return 1; }
    prog "QDM$n: socks exit $s ($h)"
  fi
  systemd-run --user --unit="qdm-worker-$n" --collect -p MemoryMax="$WMEM" -p MemorySwapMax=0 -p CPUQuota=100% -p Nice=19 \
    -p IOSchedulingClass=idle bash "$REPO/pipeline/qdm_worker.sh" "$n" >/dev/null 2>&1 \
    && prog "worker qdm-worker-$n started (MemAvailable $(avail_mb) MB, clones' JVMs $(rss_sum) MB, biggest $(rss_max) MB, $(unclaimed) unclaimed)" \
    || { prog "systemd-run for qdm-worker-$n failed"; return 1; }
}
prog "=== start (pid $$): queue $QUEUE ($(queue_syms | wc -l) symbols, $(unclaimed) unclaimed), reserve ${RESERVE_MB} MB + margin ${MARGIN_MB} MB, max QDM$MAX_N"
claimed_idle(){ local c w n; for c in "$LOGD"/*.claim; do   # installs holding a claim with no running worker (their partial data is there)
  [[ -f $c ]] || continue; w=$(cat "$c"); n=${w#qdm}; [[ $n =~ ^[0-9]+$ && -d /home/jack/QDM$n ]] && ! unit_active "$n" && echo "$n"; done | sort -un; }
while :; do
  na=$(active_ns | wc -l); left=$(unclaimed)
  (( na == 0 && left == 0 )) && [[ -z $(claimed_idle) ]] && { prog "=== queue done, no worker left - exit"; break; }
  idle=$(claimed_idle | head -1)
  if (( left > 0 )) || [[ -n $idle ]]; then
    est=$(rss_max); (( est < 900 )) && est=900; av=$(avail_mb); rs=$(reserve_mb); budget=$(( av + $(rss_sum) - rs - MARGIN_MB ))
    if (( (na + 1) * est <= budget )); then
      n=${idle:-2}; [[ -n $idle ]] || while (( n <= MAX_N )) && unit_active "$n"; do n=$((n + 1)); done   # a claimed idle install first
      if (( n <= MAX_N )); then start_worker "$n"
      else prog "at MAX_N=$MAX_N - not adding"; fi
    elif (( $(date +%s) - ${noted:-0} > 1800 )); then noted=$(date +%s)   # at most every 30 min
      prog "memory: budget $budget MB (available $av + clones $(rss_sum) - reserve $rs - margin $MARGIN_MB) holds $na workers at $est MB - not adding"; fi
  fi
  sleep "$STEP_S"
done
