#!/usr/bin/env bash
# hybrid_tunnel.sh - one command to reach the live box from this laptop.
#
#   hybrid up       WireGuard + the web tunnel          hybrid status   what is actually up
#   hybrid down     tunnel + WireGuard, in that order   hybrid web      open the dashboard
#   hybrid ssh      a shell on the box                  hybrid restart  down then up
#
# WireGuard gives this machine 10.77.0.2 and routes 10.77.0.0/24 to the VPS. The web app binds 10.77.0.1:8080, which
# WSL can reach directly but a Windows browser cannot, so the SSH tunnel re-publishes it on this machine's localhost -
# WSL forwards localhost to Windows, so http://localhost:8080 works in any browser here.
set -uo pipefail

CONF="${HYBRID_WG_CONF:-$HOME/.wireguard/hybridvps.conf}"
IFACE="$(basename "$CONF" .conf)"
KEY="${HYBRID_SSH_KEY:-$HOME/.ssh/hybrid_vps_ed25519}"
HOST="${HYBRID_SSH_HOST:-hybridops@10.77.0.1}"
PORT="${HYBRID_PORT:-8080}"
REMOTE="${HYBRID_REMOTE:-10.77.0.1:8080}"
PIDF="/tmp/hybrid-tunnel-$UID.pid"

say() { printf '%s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
no()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

wg_is_up()  { ip link show "$IFACE" >/dev/null 2>&1; }
tun_pid()   { [[ -f $PIDF ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null && cat "$PIDF"; }   # the SUPERVISOR pid (= its process group)

wg_up() {
  wg_is_up && { ok "WireGuard $IFACE already up"; return 0; }
  [[ -r $CONF ]] || { no "no WireGuard config at $CONF"; return 1; }
  say "bringing up WireGuard (needs sudo)…"
  sudo wg-quick up "$CONF" >/dev/null 2>&1 && ok "WireGuard $IFACE up (10.77.0.2 → 10.77.0.0/24)" \
    || { no "wg-quick up failed"; sudo wg-quick up "$CONF"; return 1; }
}

wg_down() {
  wg_is_up || { ok "WireGuard already down"; return 0; }
  sudo wg-quick down "$CONF" >/dev/null 2>&1 && ok "WireGuard $IFACE down" || no "wg-quick down failed"
}

tun_up() {
  local p; p=$(tun_pid) && { ok "tunnel already up (pid $p) → http://localhost:$PORT"; return 0; }
  rm -f "$PIDF"
  if ss -ltn 2>/dev/null | grep -q ":$PORT "; then no "port $PORT is already in use on this machine"; return 1; fi
  # Supervised: an SSH forward dies whenever the box restarts (MT5 deploys, reboots), and a dead tunnel quietly
  # locks the browser out of the dashboard. So the forward runs inside a reconnect loop in its own process group,
  # and `hybrid down` kills the whole group. ExitOnForwardFailure makes a half-open forward exit and retry.
  setsid bash -c "while :; do
      ssh -i '$KEY' -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
          -o ConnectTimeout=15 -N -L '$PORT:$REMOTE' '$HOST'
      sleep 5
    done" >/dev/null 2>&1 < /dev/null &
  local pid=$!
  echo "$pid" > "$PIDF"
  local i; for i in 1 2 3 4 5 6 7 8; do
    sleep 1; ss -ltn 2>/dev/null | grep -q ":$PORT " && { ok "tunnel up (supervised, pid $pid) → http://localhost:$PORT"; return 0; }
  done
  ok "tunnel supervisor running (pid $pid) - it will keep retrying until the box answers"
}

tun_down() {
  local p; p=$(tun_pid) || { ok "tunnel already down"; rm -f "$PIDF"; return 0; }
  kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null      # the whole group: the loop AND its ssh child
  rm -f "$PIDF"; ok "tunnel down"
}

status() {
  say "hybrid box:"
  if wg_is_up; then
    local hs; hs=$(sudo -n wg show "$IFACE" latest-handshakes 2>/dev/null | awk '{print $2}')
    if [[ -n ${hs:-} && $hs -gt 0 ]]; then ok "WireGuard up · last handshake $(( $(date +%s) - hs ))s ago"
    else ok "WireGuard up"; fi
  else no "WireGuard down"; fi
  local p; p=$(tun_pid) && ok "tunnel up (pid $p) → http://localhost:$PORT" || no "tunnel down"
  local h; h=$(curl -fsS --max-time 4 "http://localhost:$PORT/health" 2>/dev/null) \
    && ok "web answering: $h" || no "web not answering on localhost:$PORT"
}

case "${1:-status}" in
  up)      wg_up && tun_up && say "" && status ;;
  down)    tun_down; wg_down ;;
  restart) tun_down; wg_down; sleep 1; wg_up && tun_up ;;
  status)  status ;;
  ssh)     shift; exec ssh -i "$KEY" "$HOST" "$@" ;;
  web)     command -v explorer.exe >/dev/null && explorer.exe "http://localhost:$PORT" \
             || xdg-open "http://localhost:$PORT" 2>/dev/null || say "http://localhost:$PORT" ;;
  *)       say "usage: hybrid {up|down|restart|status|ssh|web}"; exit 2 ;;
esac
