#!/usr/bin/env bash
# One-shot progress snapshot for the FTMO data pipeline (download + rolling import).
cd "$(dirname "$0")/.." || exit 1
TOTAL=49

bar(){ local p=$1 w=${2:-26} f i; f=$(( p*w/100 ))
       printf '['; for ((i=0;i<f;i++)); do printf '#'; done
       for ((i=f;i<w;i++)); do printf '.'; done; printf '] %3d%%' "$p"; }

IM=$(grep -c . pipeline/import_state.txt 2>/dev/null || echo 0)
DL=$(grep -c . pipeline/fleet_state.txt 2>/dev/null || echo 0)

# ---- what qdmcli is doing THIS instant ----
qcmd=$(pgrep -af qdmcli 2>/dev/null | grep -v pgrep | head -1)
dact="idle"; dsym="-"
if   [[ $qcmd =~ action=update[[:space:]]symbols=([^[:space:]]+) ]]; then dact="downloading"; dsym=${BASH_REMATCH[1]}
elif [[ $qcmd =~ action=exportToMT5[[:space:]]symbol=([^[:space:]]+) ]]; then dact="exporting"; dsym=${BASH_REMATCH[1]}
fi

dlline="  now downloading: (between symbols)"
al="logs/fleet/${dsym}_update.log"
if [ "$dact" = downloading ] && [ -f "$al" ]; then
  pl=$(grep -aE '(Downloading year:|Writing )' "$al" 2>/dev/null | tail -1)
  pct=$(printf '%s' "$pl" | grep -oE '[0-9]+%' | tail -1 | tr -d '%'); : "${pct:=0}"
  if printf '%s' "$pl" | grep -q 'Downloading year:'; then
     phase="fetching"; when="year $(printf '%s' "$pl" | grep -oE 'year:[0-9]{4}' | cut -d: -f2)"
  else
     phase="writing "; when=$(printf '%s' "$pl" | grep -oE '[0-9]{4}\.[0-9]{2}\.[0-9]{2}' | tail -1)
  fi
  # time-on-symbol = the honest stall signal (network retries keep the log mtime
  # fresh even when real progress is frozen, so mtime alone is misleading)
  qpid=$(pgrep -f "symbols=${dsym}" 2>/dev/null | head -1)
  qet=$(ps -o etimes= -p "$qpid" 2>/dev/null | tr -d ' '); : "${qet:=0}"; qm=$(( qet/60 ))
  net=""; tail -4 "$al" 2>/dev/null | grep -qE 'Retrying request|NoHttpResponse|I/O exception' && net="  (retrying Dukascopy)"
  warn=""; (( qm >= 45 )) && warn=" <-- SLOW"; (( qm >= 68 )) && warn=" <-- watchdog kills @70m"
  dlline=$(printf '  now downloading: %-8s %s  %s %s   on-symbol %dm%s%s' \
           "$dsym" "$(bar "$pct" 16)" "$phase" "$when" "$qm" "$warn" "$net")
elif [ "$dact" = exporting ]; then
  dlline="  now exporting:   $dsym -> MT5 CSV"
fi

# ---- what the importer is doing THIS instant ----
imp=$(tail -1 logs/rolling_import.log 2>/dev/null | sed 's/\[[^]]*\] //' | cut -c1-64)
pgrep -f terminal64 >/dev/null 2>&1 && mt5="MT5 OPEN (importing)" || mt5="MT5 closed"

dlrun=$(pgrep -f fleet_download.py >/dev/null && echo running || echo DONE)
imrun=$(pgrep -f rolling_import.sh  >/dev/null && echo running || echo DONE)
disk=$(df -BG --output=avail /mnt/c | tail -1 | tr -d ' ')

printf 'FTMO pipeline  %(%H:%M:%S)T   disk %s free   downloads:%s  imports:%s\n' -1 "$disk" "$dlrun" "$imrun"
printf '  IN MT5 (goal)  %s   %2d/%d\n' "$(bar $(( IM*100/TOTAL )))" "$IM" "$TOTAL"
printf '  DOWNLOADED     %s   %2d/%d\n' "$(bar $(( DL*100/TOTAL )))" "$DL" "$TOTAL"
printf '%s\n' "$dlline"
printf '  now importing  : %s  [%s]\n' "${imp:-idle}" "$mt5"
