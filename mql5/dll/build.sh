#!/usr/bin/env bash
#
# build.sh - cross-compile TradeDialog.dll from WSL and deploy it to MT5.
#
# Builds the colour-coded approve/deny dialog DLL used by
# mql5/experts/HybridForwardTest.mq5, using zig as the C cross-compiler
# (no Visual Studio / mingw needed), then copies it into the terminal's
# MQL5\Libraries\ folder (where MT5 loads #import DLLs from - NOT Files\).
#
# Usage:
#   ./build.sh            # build + verify exports + deploy
#   ./build.sh --no-deploy # build + verify only
#
set -euo pipefail

ZIG="/home/jack/tools/zig-x86_64-linux-0.16.0/zig"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/TradeDialog.c"
OUT="$HERE/TradeDialog.dll"
MT5_LIB="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB/MQL5/Libraries"

log() { printf '%s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -x "$ZIG" ]] || die "zig not found/executable at $ZIG"
[[ -f "$SRC" ]] || die "source not found: $SRC"

log "compiling TradeDialog.dll ..."
"$ZIG" cc -target x86_64-windows-gnu -shared -O2 \
    -o "$OUT" "$SRC" \
    -luser32 -lgdi32 -lkernel32
[[ -f "$OUT" ]] || die "build produced no output"
log "built: $OUT ($(stat -c%s "$OUT") bytes)"

# --- verify the exported symbol name is the plain, undecorated name --------
log "exported symbols:"
python3 - "$OUT" <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
# locate PE header
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe:pe+4] == b"PE\x00\x00", "not a PE file"
coff = pe + 4
nsec, = struct.unpack_from("<H", data, coff+2)
opt = coff + 20
magic, = struct.unpack_from("<H", data, opt)          # 0x20b = PE32+
# export data dir is the 1st data directory; its offset depends on PE32 vs PE32+
ddir = opt + (112 if magic == 0x20b else 96)
exp_rva, exp_size = struct.unpack_from("<II", data, ddir)
if exp_rva == 0:
    print("  (no export directory!)"); sys.exit(1)
# build section table to map RVA -> file offset
sec = []
so = opt + struct.unpack_from("<H", data, coff+16)[0]  # opt header size
for i in range(nsec):
    off = so + i*40
    vsz, va, rsz, rptr = struct.unpack_from("<IIII", data, off+8)
    sec.append((va, vsz, rptr, rsz))
def rva2off(rva):
    for va, vsz, rptr, rsz in sec:
        if va <= rva < va + max(vsz, rsz):
            return rptr + (rva - va)
    raise ValueError("bad rva")
eo = rva2off(exp_rva)
nnames, = struct.unpack_from("<I", data, eo+24)
names_rva, = struct.unpack_from("<I", data, eo+32)
no = rva2off(names_rva)
for i in range(nnames):
    nrva, = struct.unpack_from("<I", data, no+i*4)
    o = rva2off(nrva)
    end = data.index(b"\x00", o)
    print("   ", data[o:end].decode("ascii", "replace"))
PY

# --- deploy ----------------------------------------------------------------
if [[ "${1:-}" == "--no-deploy" ]]; then
    log "skipping deploy (--no-deploy)"
    exit 0
fi
[[ -d "$MT5_LIB" ]] || mkdir -p "$MT5_LIB"
cp -f "$OUT" "$MT5_LIB/"
log "deployed -> MQL5\\Libraries\\TradeDialog.dll"
