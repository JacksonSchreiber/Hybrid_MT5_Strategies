#!/usr/bin/env bash
# deploy_live.sh - copy the live services + advisor material to the VPS over the tunnel and (re)start the tasks.
#   provisioning/deploy_live.sh            # full deploy (code, config, advisor role/library, tasks) + restart
#   provisioning/deploy_live.sh --code     # code + config only, restart
#   provisioning/deploy_live.sh --prune    # full deploy + delete advisor library files that no longer exist locally
#   provisioning/deploy_live.sh --config   # queue config only: lineup.txt, risk_mult.json, live.json, lineup_history.json (no restart;
#                                          #   the EAs re-read risk_mult/live.json on their own; a NEW lineup needs --ea or an MT5 restart)
#   provisioning/deploy_live.sh --ea       # EA + launcher binaries (built locally, gate chain PASSED) -> box MT5 tree, then restart MT5
# Requires the WireGuard tunnel up (sudo wg-quick up ~/.wireguard/hybridvps.conf) and the hybridops SSH key.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TRAIN="/mnt/c/Users/jacks/OneDrive/Trading/hybrid_project/training"
KEY="$HOME/.ssh/hybrid_vps_ed25519"; HOST="hybridops@10.77.0.1"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15 $HOST"; SCP="scp -q -i $KEY -o BatchMode=yes"
APP='C:/ProgramData/hybrid/live'; ADV='C:/ProgramData/hybrid/advisor/live'
MODE="${1:-full}"; PRUNE=false
[[ "${2:-}" == "--prune" || "$MODE" == "--prune" ]] && { PRUNE=true; [[ "$MODE" == "--prune" ]] && MODE=full; }
log(){ printf '%s\n' "$*" >&2; }
QCFG='C:/Users/hybridops/AppData/Roaming/MetaQuotes/Terminal/Common/Files/live/config'
if [[ "$MODE" == "--config" || "$MODE" == "--ea" ]]; then
  # queue config (single source: provisioning/). risk_mult + live.json are re-read by every EA at its poll/heartbeat cadence.
  $SCP "$REPO"/provisioning/lineup.txt "$REPO"/provisioning/risk_mult.json "$REPO"/provisioning/live.json "$REPO"/provisioning/lineup_history.json "$HOST:$QCFG/"
  log "queue config copied (lineup.txt, risk_mult.json, live.json, lineup_history.json)"
fi
if [[ "$MODE" == "--ea" ]]; then
  MT5LOCAL="/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB/MQL5"
  TERM=$($SSH "(Get-ChildItem 'C:/Users/hybridops/AppData/Roaming/MetaQuotes/Terminal' -Directory | Where-Object { Test-Path (Join-Path \$_.FullName 'MQL5/Experts') } | Select-Object -First 1).FullName" | tr -d '\r')
  [[ -n "$TERM" ]] || { log "ERROR: box terminal data dir not found"; exit 1; }
  TERM="${TERM//\\//}"
  $SCP "$MT5LOCAL/Experts/HybridForwardTest-live.ex5" "$HOST:$TERM/MQL5/Experts/"
  $SCP "$MT5LOCAL/Scripts/HybridLiveLauncher.ex5" "$HOST:$TERM/MQL5/Scripts/"
  log "binaries copied to $TERM (EA -live + launcher); restarting MT5 (the documented box procedure: stop, then the hybrid-mt5 task)"
  $SSH "Stop-Process -Name terminal64 -Force -ErrorAction SilentlyContinue; Start-Sleep 5; Start-ScheduledTask hybrid-mt5; Start-Sleep 3; (Get-ScheduledTask hybrid-mt5).State" | tr -d '\r'
  exit 0
fi
[[ "$MODE" == "--config" ]] && exit 0
$SSH "New-Item -ItemType Directory -Force -Path $APP/live/static, $ADV/advisor/.claude, $ADV/advisor/library, C:/ProgramData/hybrid/logs | Out-Null; 'dirs ok'" | tr -d '\r'
$SCP "$REPO"/live/__init__.py "$REPO"/live/common.py "$REPO"/live/charts.py "$REPO"/live/webapp.py "$REPO"/live/monitor.py "$REPO"/live/advisor_runner.py "$REPO"/live/mt5feed.py "$REPO"/live/overlays.py "$REPO"/live/calendar_refresh.py "$REPO"/live/backup.py "$REPO"/live/feedd.py "$REPO"/live/brief_runner.py "$REPO"/live/exposure.py "$REPO"/live/eligibility.py "$REPO"/live/month_export.py "$REPO"/live/sysres.py "$HOST:$APP/live/"
$SCP "$REPO"/live/static/lw.js "$REPO"/live/static/hybrid_chart.js "$HOST:$APP/live/static/"
$SCP "$REPO"/provisioning/live_config.vps.json "$HOST:$APP/live_config.json"
$SCP "$REPO"/config/symbol_rules.json "$HOST:$APP/symbol_rules.json"      # per-symbol doctrine flags (class + suspended session rules) for the advisor card
$SCP "$REPO"/provisioning/live_tasks.ps1 "$HOST:C:/ProgramData/hybrid/live_tasks.ps1"
$SCP "$REPO"/provisioning/maintenance.ps1 "$HOST:C:/ProgramData/hybrid/maintenance.ps1"
$SCP "$REPO"/provisioning/load_check.ps1 "$HOST:C:/ProgramData/hybrid/load_check.ps1"   # capacity check: terminal64 CPU/RAM + heartbeat ages
log "code + config copied"
if [[ "$MODE" == "full" || "$MODE" == "--calendar" ]]; then
  # calendar pipeline: normalizer + classifier + coverage test + rules + history (3 MB) - the box rebuilds econ_events.csv daily
  CAL='C:/ProgramData/hybrid/calendar'
  $SSH "New-Item -ItemType Directory -Force -Path $CAL/pipeline, $CAL/config, $CAL/history, $CAL/snapshots, $CAL/build | Out-Null; 'calendar dirs ok'" | tr -d '\r'
  $SCP "$REPO"/pipeline/normalize_econ_tzfix.py "$REPO"/pipeline/event_classes.py "$REPO"/pipeline/tier0.py "$REPO"/pipeline/test_calendar_coverage.py "$HOST:$CAL/pipeline/"
  $SCP "$REPO"/config/event_classes.yaml "$REPO"/config/political_events.csv "$REPO"/config/official_schedule.csv "$HOST:$CAL/config/"
  $SCP "$REPO"/data/econ/ff_combined.csv "$HOST:$CAL/history/ff_combined.csv"
  log "calendar pipeline copied"
fi
if [[ "$MODE" == "full" ]]; then
  # advisor material: LIVE role only (never the blind CLAUDE.md / notes.md / verdicts.log), quick reference, library
  $SCP "$TRAIN/quick-reference.html" "$HOST:$ADV/quick-reference.html"
  $SCP "$TRAIN/advisor/CLAUDE.live.md" "$HOST:$ADV/advisor/CLAUDE.md"
  $SCP -r "$TRAIN/advisor/library/." "$HOST:$ADV/advisor/library/"
  $SCP "$REPO/provisioning/advisor_settings.json" "$HOST:$ADV/advisor/.claude/settings.json"
  $SSH "foreach (\$f in '$ADV/advisor/verdicts.live.log') { if (-not (Test-Path \$f)) { New-Item -ItemType File -Path \$f | Out-Null } }; 'advisor material ok'" | tr -d '\r'
  if [[ "${PRUNE:-false}" == "true" ]]; then
    # coach 2026-09-17: the copy is additive, so a RETIRED library note would linger on the box and keep being read.
    # --prune deletes any file under advisor/library that no longer exists locally. Compared by RELATIVE path through a
    # manifest (a path built by string-replacement on the box was the 2026-09-17 bug: every file looked stale).
    (cd "$TRAIN/advisor" && find library -type f | sed 's|\\|/|g' | sort) > /tmp/hybrid_library_manifest.txt
    $SCP /tmp/hybrid_library_manifest.txt "$HOST:C:/ProgramData/hybrid/library_manifest.txt"
    $SSH "\$root='C:\\ProgramData\\hybrid\\advisor\\live\\advisor'; \$keep=Get-Content 'C:\\ProgramData\\hybrid\\library_manifest.txt';
          Get-ChildItem \"\$root\\library\" -Recurse -File | ForEach-Object {
            \$rel = \$_.FullName.Substring(\$root.Length + 1) -replace '\\\\','/';
            if (\$keep -notcontains \$rel) { Remove-Item \$_.FullName -Force; 'pruned ' + \$rel } }" | tr -d '\r'
    rm -f /tmp/hybrid_library_manifest.txt
  fi
  $SSH "powershell -NoProfile -ExecutionPolicy Bypass -File C:/ProgramData/hybrid/live_tasks.ps1" | tr -d '\r'
fi
# restart the three services (Stop kills the .cmd tree; Start relaunches in the task's own session)
$SSH 'foreach ($t in "hybrid-feed","hybrid-web","hybrid-monitor","hybrid-advisor") { Stop-ScheduledTask $t -ErrorAction SilentlyContinue }; Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*Python312*" } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 2; foreach ($t in "hybrid-feed","hybrid-web","hybrid-monitor","hybrid-advisor") { Start-ScheduledTask $t }; Start-Sleep 6; (Get-ScheduledTask hybrid-feed,hybrid-web,hybrid-monitor,hybrid-advisor | % { $_.TaskName + "=" + $_.State }) -join ", "' | tr -d '\r'
sleep 4
curl -s -m 8 http://10.77.0.1:8080/health && echo "  <- web health from the tunnel" || log "WARN: web health check failed"
