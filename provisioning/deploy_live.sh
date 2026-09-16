#!/usr/bin/env bash
# deploy_live.sh - copy the live services + advisor material to the VPS over the tunnel and (re)start the tasks.
#   provisioning/deploy_live.sh            # full deploy (code, config, advisor role/library, tasks) + restart
#   provisioning/deploy_live.sh --code     # code + config only, restart
# Requires the WireGuard tunnel up (sudo wg-quick up ~/.wireguard/hybridvps.conf) and the hybridops SSH key.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TRAIN="/mnt/c/Users/jacks/OneDrive/Trading/hybrid_project/training"
KEY="$HOME/.ssh/hybrid_vps_ed25519"; HOST="hybridops@10.77.0.1"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15 $HOST"; SCP="scp -q -i $KEY -o BatchMode=yes"
APP='C:/ProgramData/hybrid/live'; ADV='C:/ProgramData/hybrid/advisor/live'
MODE="${1:-full}"
log(){ printf '%s\n' "$*" >&2; }
$SSH "New-Item -ItemType Directory -Force -Path $APP/live/static, $ADV/advisor/.claude, $ADV/advisor/library, C:/ProgramData/hybrid/logs | Out-Null; 'dirs ok'" | tr -d '\r'
$SCP "$REPO"/live/__init__.py "$REPO"/live/common.py "$REPO"/live/charts.py "$REPO"/live/webapp.py "$REPO"/live/monitor.py "$REPO"/live/advisor_runner.py "$REPO"/live/mt5feed.py "$REPO"/live/overlays.py "$HOST:$APP/live/"
$SCP "$REPO"/live/static/lw.js "$REPO"/live/static/hybrid_chart.js "$HOST:$APP/live/static/"
$SCP "$REPO"/provisioning/live_config.vps.json "$HOST:$APP/live_config.json"
$SCP "$REPO"/provisioning/live_tasks.ps1 "$HOST:C:/ProgramData/hybrid/live_tasks.ps1"
log "code + config copied"
if [[ "$MODE" == "full" ]]; then
  # advisor material: LIVE role only (never the blind CLAUDE.md / notes.md / verdicts.log), quick reference, library
  $SCP "$TRAIN/quick-reference.html" "$HOST:$ADV/quick-reference.html"
  $SCP "$TRAIN/advisor/CLAUDE.live.md" "$HOST:$ADV/advisor/CLAUDE.md"
  $SCP -r "$TRAIN/advisor/library/." "$HOST:$ADV/advisor/library/"
  $SCP "$REPO/provisioning/advisor_settings.json" "$HOST:$ADV/advisor/.claude/settings.json"
  $SSH "foreach (\$f in '$ADV/advisor/notes.live.md','$ADV/advisor/verdicts.live.log') { if (-not (Test-Path \$f)) { New-Item -ItemType File -Path \$f | Out-Null } }; 'advisor material ok'" | tr -d '\r'
  $SSH "powershell -NoProfile -ExecutionPolicy Bypass -File C:/ProgramData/hybrid/live_tasks.ps1" | tr -d '\r'
fi
# restart the three services (Stop kills the .cmd tree; Start relaunches in the task's own session)
$SSH 'foreach ($t in "hybrid-web","hybrid-monitor","hybrid-advisor") { Stop-ScheduledTask $t -ErrorAction SilentlyContinue }; Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*Python312*" } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 2; foreach ($t in "hybrid-web","hybrid-monitor","hybrid-advisor") { Start-ScheduledTask $t }; Start-Sleep 6; (Get-ScheduledTask hybrid-web,hybrid-monitor,hybrid-advisor | % { $_.TaskName + "=" + $_.State }) -join ", "' | tr -d '\r'
sleep 4
curl -s -m 8 http://10.77.0.1:8080/health && echo "  <- web health from the tunnel" || log "WARN: web health check failed"
