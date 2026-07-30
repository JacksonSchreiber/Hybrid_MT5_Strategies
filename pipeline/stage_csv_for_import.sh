#!/usr/bin/env bash
#
# stage_csv_for_import.sh - MT5 custom-symbol import helper (run from WSL)
#
# Bridges the WSL-native repo and the Windows MT5 sandbox for the Dukascopy
# tick import (see docs/mt5-import.md).
#
#   Stage a symbol's CSV into the MT5 sandbox (MQL5\Files\import\):
#       ./stage_csv_for_import.sh EURUSD
#
#   Remove a staged CSV after a successful import (reclaim ~7 GB on C:):
#       ./stage_csv_for_import.sh --clean EURUSD
#
#   Sync the MQL5 sources from the repo into MQL5\Scripts\ (needed for the
#   terminal to see/compile them):
#       ./stage_csv_for_import.sh --sync-scripts
#
#   Sync + compile both scripts with the MetaEditor CLI and show the log:
#       ./stage_csv_for_import.sh --compile
#
set -euo pipefail

# --- fixed paths for this machine (mirror config/settings.yaml mt5.*) --------
REPO="/home/jack/hybrid_project"
MT5_DATA="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB"
MT5_DATA_WIN='C:\Users\jacks\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB'
METAEDITOR="/mnt/c/Program Files/OANDA MetaTrader 5/MetaEditor64.exe"

SRC_DIR="$REPO/mql5/scripts"
EXP_SRC_DIR="$REPO/mql5/experts"
INC_SRC_DIR="$REPO/mql5/include"
IMPORT_DIR="$MT5_DATA/MQL5/Files/import"
SCRIPTS_DIR="$MT5_DATA/MQL5/Scripts"
EXPERTS_DIR="$MT5_DATA/MQL5/Experts"
INCLUDE_DIR="$MT5_DATA/MQL5/Include"
MT5_READY="$REPO/data/mt5_ready"

log()  { printf '%s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '3,26p' "$0"
  exit "${1:-0}"
}

# --- sync .mq5/.mqh sources into the MT5 tree (Scripts, Experts, Include) -----
sync_scripts() {
  [[ -d "$SRC_DIR" ]] || die "source dir not found: $SRC_DIR"
  mkdir -p "$SCRIPTS_DIR"
  local f
  for f in "$SRC_DIR"/*.mq5; do
    [[ -e "$f" ]] || die "no .mq5 sources in $SRC_DIR"
    cp -f "$f" "$SCRIPTS_DIR/"
    log "synced $(basename "$f") -> MQL5\\Scripts\\"
  done
  # experts (optional)
  if [[ -d "$EXP_SRC_DIR" ]] && compgen -G "$EXP_SRC_DIR/*.mq5" >/dev/null; then
    mkdir -p "$EXPERTS_DIR"
    for f in "$EXP_SRC_DIR"/*.mq5; do
      cp -f "$f" "$EXPERTS_DIR/"
      log "synced $(basename "$f") -> MQL5\\Experts\\"
    done
  fi
  # include tree (optional, preserve subdirs e.g. Hybrid\)
  if [[ -d "$INC_SRC_DIR" ]]; then
    mkdir -p "$INCLUDE_DIR"
    ( cd "$INC_SRC_DIR" && find . -name '*.mqh' -print0 | while IFS= read -r -d '' rel; do
        mkdir -p "$INCLUDE_DIR/$(dirname "$rel")"
        cp -f "$rel" "$INCLUDE_DIR/$rel"
        log "synced include $rel -> MQL5\\Include\\"
      done )
  fi
}

# --- compile one .mq5 via MetaEditor CLI, print the UTF-16 log ---------------
# args: <MQL5-subdir> <name.mq5>   e.g. compile_one Experts HybridForwardTest.mq5
compile_one() {
  local subdir="$1" name="$2"
  local host_dir="$MT5_DATA/MQL5/$subdir"
  local win_path="$MT5_DATA_WIN\\MQL5\\$subdir\\$name"
  log "compiling $subdir\\$name ..."
  # MetaEditor /compile returns the error count as its exit code; tolerate it.
  "$METAEDITOR" /compile:"$win_path" /log || true
  local logf="$host_dir/${name%.mq5}.log"
  if [[ -f "$logf" ]]; then
    log "----- $name compile log -----"
    iconv -f UTF-16LE -t UTF-8 "$logf" 2>/dev/null | sed '/^$/d' >&2 || cat "$logf" >&2
    log "-----------------------------"
    if iconv -f UTF-16LE -t UTF-8 "$logf" 2>/dev/null | grep -qiE '0 error'; then
      log "==> $name: 0 errors"
    else
      log "==> $name: NON-ZERO errors (see log above)"
    fi
  else
    log "WARNING: no compile log produced at $logf"
  fi
}

compile_all() {
  [[ -x "$METAEDITOR" ]] || die "MetaEditor not found/executable: $METAEDITOR"
  sync_scripts
  compile_one Scripts "ImportTicks.mq5"
  compile_one Scripts "VerifyImport.mq5"
  [[ -f "$EXPERTS_DIR/HybridForwardTest.mq5" ]] && compile_one Experts "HybridForwardTest.mq5"
  [[ -f "$EXPERTS_DIR/AutoImport.mq5" ]] && compile_one Experts "AutoImport.mq5"
}

# --- disk-space guard on C: --------------------------------------------------
check_space() {
  local need_bytes="$1"
  local avail
  avail=$(df -B1 --output=avail "$MT5_DATA" | tail -1 | tr -d ' ')
  local need_pad=$(( need_bytes + need_bytes/10 ))   # +10% headroom
  log "C: free=$(numfmt --to=iec "$avail" 2>/dev/null || echo "$avail") ; need~$(numfmt --to=iec "$need_pad" 2>/dev/null || echo "$need_pad")"
  (( avail >= need_pad )) || die "not enough free space on C: (need ~$need_pad bytes, have $avail)"
}

# --- stage <BASE>.csv into the sandbox ---------------------------------------
stage() {
  local base="$1"
  local src="$MT5_READY/$base.csv"
  [[ -f "$src" ]] || die "source CSV not found: $src (is the fleet job done for $base?)"
  local size
  size=$(stat -c%s "$src")
  check_space "$size"
  mkdir -p "$IMPORT_DIR"
  local dst="$IMPORT_DIR/$base.csv"
  log "staging $base.csv ($(numfmt --to=iec "$size" 2>/dev/null || echo "$size")) -> MQL5\\Files\\import\\ ..."
  # Low-RAM WSL (~7 GB) copying a large CSV onto the 9p /mnt/c mount: a plain bulk
  # `cp` accumulates dirty write-back pages faster than 9p can flush them and dies
  # with ENOMEM ("Cannot allocate memory") on files >~10 GB (USDCNH, 12.7 GB, hit
  # this; USDCZK, 8.5 GB, barely didn't). dd with oflag=sync commits each block
  # before issuing the next, bounding dirty memory to one block. Slower, but safe.
  rm -f "$dst"
  if ! dd if="$src" of="$dst" bs=32M oflag=sync status=none; then
    die "dd copy failed for $base (memory-safe staging to sandbox)"
  fi
  local dsize
  dsize=$(stat -c%s "$dst")
  (( dsize == size )) || die "copy size mismatch ($dsize != $size) - copy may be incomplete"
  log "staged OK: $dst"
  log "next: in MT5, drag ImportTicks onto any chart and set SymbolBase=$base"
}

# --- remove a staged CSV -----------------------------------------------------
clean() {
  local base="$1"
  local dst="$IMPORT_DIR/$base.csv"
  if [[ -f "$dst" ]]; then
    rm -f "$dst"
    log "removed staged $dst"
  else
    log "nothing to clean: $dst not present"
  fi
}

# --- arg parsing -------------------------------------------------------------
[[ $# -ge 1 ]] || usage 1
case "$1" in
  -h|--help)      usage 0 ;;
  --sync-scripts) sync_scripts ;;
  --compile)      compile_all ;;
  --clean)        [[ $# -eq 2 ]] || die "usage: --clean <BASE>"; clean "$2" ;;
  -*)             die "unknown flag: $1" ;;
  *)              [[ $# -eq 1 ]] || die "usage: $0 <BASE>"; stage "$1" ;;
esac
