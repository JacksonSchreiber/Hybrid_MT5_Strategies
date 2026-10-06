#!/usr/bin/env bash
# qdm_clone.sh <n> [socks_ip] - make (or refresh) /home/jack/QDM<n>, an extra QuantDataManager install for PARALLEL downloads
# (trader 2026-10-05: "do as many installs as you can"; how-to in docs/qdm_parallel_downloads.md).
# QDM runs ONE instance per install: it binds AppWebServerPortQDM (5050 in the main install) and a second qdmcli in the same folder exits
# with "Another instance of QuantDataManager is running". A copy in its own folder with its own port runs alongside.
# - copies the main install WITHOUT its data (user/data/History, internal/tmp 80+ GB, logs) - ~0.8 GB; data.db (the symbol registry)
#   comes along on the first copy only (a refresh keeps the clone's own registry, which records what it downloaded)
# - rewrites the absolute paths in user/settings/settings.xml to the new folder; port 5049+n
# - launcher renamed qdm<n>cli, and no file called qdmcli in the clone: the MT5 stage/driver busy checks match `pgrep -x qdmcli` (the main
#   install) and must never wait on a clone
# - qdmcli.config + qdm<n>cli.config (same content, whichever the launcher reads): heap -Xmx$QDM_HEAP (default 768m) and, when socks_ip is
#   given, -DsocksProxyHost=<ip> -DsocksProxyPort=1080 - a Mullvad relay's SOCKS5 (10.124.x.x, reachable through the WSL Mullvad tunnel),
#   so each clone downloads from its own exit IP (Dukascopy rate-limits per IP: HTTP 429)
# - QDM_CPUS=<k> adds -XX:ActiveProcessorCount=<k>: QDM sizes its download threads to the CPUs it sees (19 here), and that burst of
#   parallel hour-file requests is what draws the 429s
set -euo pipefail
n=${1:?usage: qdm_clone.sh <n>=2.. [socks_ip]}; SOCKS=${2:-}; HEAP=${QDM_HEAP:-768m}; CPUS=${QDM_CPUS:-}
SRC=/home/jack/QDM; DST=/home/jack/QDM$n
[[ $n =~ ^[0-9]+$ ]] && (( n >= 2 )) || { echo "n must be an integer >= 2" >&2; exit 1; }
fresh=1; [[ -f $DST/user/data/data.db ]] && fresh=0
mkdir -p "$DST"
ex=(--exclude=/internal/tmp/ --exclude=/user/data/History/ --exclude=/user/data/us30_copy_backup/ --exclude='/user/log/*'
    --exclude=/qdmcli --exclude=/qdmcli.config --exclude='/qdmcli.config.bak*')
(( fresh )) || ex+=(--exclude=/user/data/data.db)
rsync -a --bwlimit=60000 "${ex[@]}" "$SRC/" "$DST/"
mkdir -p "$DST/internal/tmp" "$DST/user/data/History" "$DST/user/log"
cp -p "$SRC/qdmcli" "$DST/qdm${n}cli"
python3 - "$SRC" "$DST" "$n" "$HEAP" "$SOCKS" "$CPUS" <<'EOF'
import re, sys
src, dst, n, heap, socks, cpus = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
p = dst + '/user/settings/settings.xml'; b = open(p, 'rb').read()
open(p, 'wb').write(re.sub(rb'/home/jack/QDM[0-9]*(?=/)', dst.encode(), b))
p = dst + '/internal/AppSettings.txt'; b = open(p, 'rb').read()
open(p, 'wb').write(re.sub(rb'<AppWebServerPortQDM>\d+<', f'<AppWebServerPortQDM>{5049 + n}<'.encode(), b))
# the main config, minus heap/proxy lines, plus this clone's (CRLF like the original)
keep = [l for l in open(src + '/qdmcli.config', 'rb').read().decode().replace('\r', '').split('\n')
        if l.strip() and not re.match(r'option -(Xms|Xmx|DsocksProxy|XX:ActiveProcessorCount)', l)]
keep += ['option -Xms256m', f'option -Xmx{heap}'] + ([f'option -XX:ActiveProcessorCount={cpus}'] if cpus else []) + ([f'option -DsocksProxyHost={socks}', 'option -DsocksProxyPort=1080'] if socks else [])
cfg = ('\r\n'.join(keep) + '\r\n').encode()
for name in ('qdmcli.config', f'qdm{n}cli.config'): open(f'{dst}/{name}', 'wb').write(cfg)
EOF
echo "QDM$n ready: launcher $DST/qdm${n}cli, port $((5049 + n)), heap $HEAP, cpus ${CPUS:-all}, socks ${SOCKS:-none}, registry $( ((fresh)) && echo copied || echo kept)"
