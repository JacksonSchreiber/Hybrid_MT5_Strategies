# VPS provisioning guide — rebuild the live box from nothing

**Purpose:** if the server breaks, a new one is rebuilt by following this file top to bottom. Every step is a script in
`provisioning/` or a command shown verbatim. The engineer updates this file in the same commit as any change to the
box. Spec: `docs/phase3-live-system-requirements.md` (§2 security, §7 ops). Contracts: `docs/live-queue-schema.md`.
Last full pass: 2026-09-16 (steps 1–6 all executed on the current box; the trader waived the rebuild-from-script drill).

## Facts

| item | value |
|---|---|
| provider / OS | Contabo VPS `vmi3580043`, Windows Server 2022 Datacenter, 8 GB RAM. Console = Contabo panel → VPS → VNC (the recovery path when SSH is lost; no RDP anywhere) |
| accounts | `hybridops` = local Administrator, auto-logs on at boot (console locked), SSH key-only; built-in `Administrator` denied over SSH. Password in `C:\ProgramData\hybrid\secrets\hybridops.password` |
| network | WireGuard server `10.77.0.1/24` UDP 51820; peers: engineer WSL `10.77.0.2`, trader phone `10.77.0.3`. Inbound allowed: UDP 51820, TCP 22 + 8080 on `10.77.0.1` only, DHCP, ICMP fragmentation. RDP + WinRM disabled at the service level |
| secrets | `C:\ProgramData\hybrid\secrets\` (Administrators + SYSTEM only, never in the repo): `mt5.json` {login,password,investor_password,server}, `telegram.token`, `telegram.chat_id`, `advisor.token` (Claude Code OAuth from `claude setup-token`, bills the subscription), `hybridops.password`. Master copies: trader's KeePassXC |
| broker / terminal | OANDA MT5 build 6198, demo `OANDA-Demo-1`, symbols carry `.sim`. Data dir `C:\Users\hybridops\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB`, queue root `…\Terminal\Common\Files\live` |
| lineup (coach 2026-09-16) | `US100.sim US500.sim USOIL.sim XAUUSD.sim` ×1.0, `EURUSD.sim GBPUSD.sim` ×0.5 (`config\risk_mult.json` = `provisioning/risk_mult.json`); one EA instance per H4 chart; no US30, no USDJPY |
| runtime | Python 3.12 (+ pillow, certifi, pyyaml, MetaTrader5), Node 22 + `@anthropic-ai/claude-code`, WireGuard 0.5.3, OpenSSH (Windows capability) |
| services (scheduled tasks) | `hybrid-mt5` (at logon), `hybrid-lock-console`, `hybrid-sshd-keeper`, `hybrid-web`, `hybrid-monitor`, `hybrid-advisor` (at startup +45 s, as hybridops), `hybrid-maintenance` (Sat 14:00 UTC, SYSTEM) |
| web | `http://10.77.0.1:8080` from any peer; no login, plain HTTP (trader rulings: the tunnel is the boundary) |
| backups | trader-driven: dashboard **Download backup (30 d)** → `hybrid-backup_<from>_to_<to>.zip`; counter turns red near 30 days (trader ruling 2026-09-16 replaces the nightly pull) |

## Engineer workstation (WSL) prerequisites
`~/.ssh/hybrid_vps_ed25519` (SSH key), `~/.wireguard/hybridvps.conf` (tunnel), `sudo apt install wireguard-tools` with a
NOPASSWD sudoers line for `wg-quick`/`wg`; `sudo wg-quick up ~/.wireguard/hybridvps.conf`;
`ssh -i ~/.ssh/hybrid_vps_ed25519 hybridops@10.77.0.1`. Local MT5 (OANDA, same install hash) builds the EA
(`pipeline/mt5_build_ea.sh`) and the two scripts (`mql5/scripts/*.mq5`, MetaEditor `/compile`).

---

## Step 1 — bootstrap over the console (the only hands-on step)
Fresh box, logged in as Administrator on the Contabo VNC console (or RDP if the image still has it open).
```powershell
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/JacksonSchreiber/Hybrid_MT5_Strategies/main/provisioning/bootstrap.ps1 -OutFile C:\bootstrap.ps1
Set-ExecutionPolicy -Scope Process Bypass -Force
C:\bootstrap.ps1
```
Send the engineer the green block (`public_ip`, `wg_server_pubkey`). The script is idempotent. It creates `hybridops`,
installs OpenSSH (key-only, PowerShell shell, bound to `10.77.0.1`, delayed start + restart-on-failure +
`hybrid-sshd-keeper`, `DenyUsers Administrator`), WireGuard `wg0` with both peers (public keys are in the script),
firewall default-deny + the two allow rules, UTC, Windows Update download-only.
If the WireGuard server key is being restored from KeePassXC (so the phone/workstation configs keep working): put it in
`C:\ProgramData\WireGuard\server.key` **before** running the script. Otherwise re-issue the client configs.
Engineer: update `Endpoint` in `~/.wireguard/hybridvps.conf` (and the phone config) if the IP changed; `wg-quick up`; ssh.

## Step 2 — lock the console
```bash
scp -i ~/.ssh/hybrid_vps_ed25519 provisioning/lockdown.ps1 hybridops@10.77.0.1:C:/ProgramData/hybrid/
ssh -i ~/.ssh/hybrid_vps_ed25519 hybridops@10.77.0.1 'powershell -ExecutionPolicy Bypass -File C:\ProgramData\hybrid\lockdown.ps1'
ssh … 'Restart-Computer -Force'      # wait ~90 s; SSH must return; then from outside the tunnel every TCP port must be closed
```
Incident 2026-09-15 (why sshd has three guards): it started before the tunnel adapter had its address and died;
recovered via the console with `sc.exe failure sshd …` + `Start-Service sshd`. Scheduled tasks run `.ps1` FILES,
never inline `-Command` strings (quoting silently broke a safety task).

## Step 3 — secrets, MT5, EA
1. Secrets: `scp` the five files into `C:\ProgramData\hybrid\secrets\` then `icacls C:\ProgramData\hybrid /inheritance:r /grant "Administrators:(OI)(CI)F" /grant "SYSTEM:(OI)(CI)F"`.
   `hybridops.password`: generate a new random one and `Set-LocalUser -Name hybridops -Password …` (tasks store it).
2. MT5 install (over SSH): `Invoke-WebRequest https://download.mql5.com/cdn/web/oanda.corporation/mt5/oanda5setup.exe -OutFile C:\ProgramData\hybrid\installers\oanda5setup.exe; Start-Process … -ArgumentList /auto -Wait` (exit code 1 = it could not launch the GUI; files are installed).
3. Auto-logon + console lock + MT5 task: `Autologon64.exe /accepteula hybridops <computer> <password>` (Sysinternals, LSA-stored);
   tasks `hybrid-lock-console` (at logon of hybridops: `rundll32 user32.dll,LockWorkStation`) and `hybrid-mt5` (at logon +20 s,
   `terminal64.exe /config:C:\ProgramData\hybrid\mt5\live.ini`, RestartCount 3, no stored password = interactive session).
   MT5 needs the console session: in an SSH session it cannot create chart windows.
4. `C:\ProgramData\hybrid\mt5\live.ini` = `provisioning/live.ini` with `Login/Password/Server` inserted under `[Common]`
   from `secrets\mt5.json` (a `[Common]` section WITHOUT credentials starts the terminal logged out). `[Experts]
   Enabled=1 AllowLiveTrading=1 AllowDllImport=0` arms AutoTrading (§11-8).
5. Deploy files (built locally): `MQL5\Experts\HybridForwardTest-live.ex5`, `MQL5\Scripts\HybridSaveTemplate.ex5`,
   `MQL5\Scripts\HybridLiveLauncher.ex5`, `MQL5\Indicators\HybridTriEMA.ex5`, `MQL5\Presets\hft_live.set`
   (`provisioning/hft_live.set`), `Common\Files\live\config\risk_mult.json` (`provisioning/risk_mult.json`).
   Restoring from a backup zip: `mt5/MQL5/Presets/*`, `mt5/MQL5/Profiles/Templates/hybrid_live.tpl` go back to the same
   places (then step 6 is not needed), `live/**` goes back under the queue root, `mt5/Common/Files/econ_events.csv` too.
6. Template (skip if `hybrid_live.tpl` was restored): temporarily set `[StartUp] Expert=HybridForwardTest-live
   ExpertParameters=hft_live.set Script=HybridSaveTemplate Symbol=EURUSD.sim Period=H4` in a copy of the ini, start the
   terminal once with it (`Register-ScheduledTask … -User hybridops`, `Start-ScheduledTask`), confirm
   `MQL5\Profiles\Templates\hybrid_live.tpl` exists with `<expert>` + inputs, then restore `[StartUp] Script=HybridLiveLauncher`.
7. Start `hybrid-mt5`. Expect within ~3 min: terminal in session 1, log `authorized on OANDA-Demo-1`, expert log
   `Launcher: lineup=6 … template applied=…`, six `HybridForwardTest ACTIVE [LIVE(queue)]` lines, six
   `live\heartbeat_<SYM>.json` rewritten every 60 s with `terminal_trade_allowed:true`, `events_count>0`.
   After every EA rebuild: copy the `.ex5`, `Stop-Process terminal64`, `Start-ScheduledTask hybrid-mt5`. Re-save the
   template only when the EA's INPUTS change (the charts read the template, not `hft_live.set`).

## Step 4 — runtime + services
```powershell
# Python 3.12 + Node 22 + Claude Code + libs
Invoke-WebRequest https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe -OutFile C:\ProgramData\hybrid\installers\python.exe
Start-Process C:\ProgramData\hybrid\installers\python.exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
& "C:\Program Files\Python312\python.exe" -m pip install pillow certifi pyyaml MetaTrader5
Invoke-WebRequest https://nodejs.org/dist/v22.11.0/node-v22.11.0-x64.msi -OutFile C:\ProgramData\hybrid\installers\node.msi
Start-Process msiexec.exe -ArgumentList '/i C:\ProgramData\hybrid\installers\node.msi /qn' -Wait
& "C:\Program Files\nodejs\npm.cmd" install -g @anthropic-ai/claude-code
New-NetFirewallRule -DisplayName 'Hybrid web over WireGuard' -Direction Inbound -Protocol TCP -LocalPort 8080 -LocalAddress 10.77.0.1 -Action Allow
```
Then from the workstation: `provisioning/deploy_live.sh` (full). It copies `live/*.py` + `live/static`, the config
(`provisioning/live_config.vps.json` → `C:\ProgramData\hybrid\live\live_config.json`), the advisor tree
(`C:\ProgramData\hybrid\advisor\live\{quick-reference.html, advisor\{CLAUDE.md = CLAUDE.live.md, library\, .claude\settings.json,
notes.live.md, verdicts.live.log}}` — never the blind role/notes/log), the calendar pipeline (`--calendar` part:
`normalize_econ_tzfix.py, event_classes.py, tier0.py, test_calendar_coverage.py, config\{event_classes.yaml,
political_events.csv, official_schedule.csv}, history\ff_combined.csv`), `maintenance.ps1`, and runs
`live_tasks.ps1` (registers `hybrid-web/monitor/advisor` at startup as hybridops with the stored password,
`hybrid-maintenance` Sat 14:00 UTC as SYSTEM, and pre-trusts the advisor folder in `C:\Users\hybridops\.claude.json`).
Verify: `/health` over the tunnel, dashboard shows six `EA alive`, `hybrid-monitor.task.log` shows a successful
Telegram send (dashboard **Telegram test** button), one manual `python live\calendar_refresh.py --config …` returns
`"ok": true` (coverage test exit 0, coverage ≥ next week).

## Step 5 — operations
- **Maintenance:** `hybrid-maintenance` (Saturday 14:00 UTC) sends "MAINTENANCE: shutting down", stops MT5 gracefully,
  installs Windows updates (PSWindowsUpdate, installed on first run), reboots. The monitor then sends "ALL SYSTEMS UP"
  once all six EAs heartbeat + feed + web are up, or "REBOOT RECOVERY INCOMPLETE" after 10 min. Friday 18:00 UTC:
  Contabo-snapshot reminder. Reboot drill = `powershell -File C:\ProgramData\hybrid\maintenance.ps1 -NoUpdates`.
- **Backups:** trader clicks **Download backup (30 d)**; keep the zips in KeePassXC/OneDrive. Restore = step 3.5.
- **Calendar:** refreshed daily 02:30 UTC by the monitor (ForexFactory this-week XML → forward store → full rebuild
  → coverage test → atomic swap); EAs reload after 03:00 UTC; alerts on failure / >7 d stale / coverage <3 d.
  `official_schedule.csv` (year-ahead FOMC/ECB/BoE/NFP/CPI, FF wins on overlap) and `political_events.csv` (elections,
  coach-reviewed forward batches, pending items in `political_events.draft.csv`) are deployed with `deploy_live.sh --calendar`.
- **Kill switch:** dashboard button writes `config\trading_enabled.json`; missing file = disabled (fail closed).
- **Deploy path for code:** `deploy_live.sh --code` (services restart, ~10 s); EA: step 3.7.

## Recovery quick-reference
| symptom | do |
|---|---|
| SSH times out, tunnel handshakes | console → `Start-Service sshd`; if it keeps dying: `sc.exe qfailure sshd`, `Get-ScheduledTask hybrid-sshd-keeper` |
| SSH times out, no handshake | Contabo console: `Get-Service WireGuardTunnel$wg0`, `C:\ProgramData\WireGuard\wg0.conf` intact? public IP changed? |
| dashboard "EA STALE" for all symbols | `Get-Process terminal64`? none → `Start-ScheduledTask hybrid-mt5` (the monitor does this itself, 3 tries/30 min). Present → expert log (`MQL5\Logs\<date>.log`): `symbol synchronization timeout` = wrong symbol name; `INERT` = AutoTrading/ini issue |
| one symbol missing | `Launcher:` line in the expert log; `hybrid_live.tpl` present? re-run step 3.6 if the template is gone |
| AutoTrading disarmed alert | `live.ini [Experts] Enabled=1 AllowLiveTrading=1`, restart `hybrid-mt5` |
| web down | monitor restarts `hybrid-web` after 3 misses; else `Get-Content C:\ProgramData\hybrid\logs\hybrid-web.task.log -Tail 20` |
| advisor consult FAILED | `hybrid-advisor.task.log`; token expired → `claude setup-token` on the trader's PC, replace `secrets\advisor.token`, restart task; "workspace not trusted" → re-run `live_tasks.ps1` |
| calendar stale / refresh failed | `C:\ProgramData\hybrid\logs\calendar.log`; run `calendar_refresh.py` by hand; FF 429 = wait; coverage test failure = history file corrupt → redeploy `--calendar` |
| Telegram silent | dashboard **Telegram test**; `secrets\telegram.token` / `chat_id`; certifi installed? |
| box lost entirely | steps 1–5 + restore the latest backup zip (step 3.5); minutes-to-trading-ready: not yet measured (drill waived by the trader 2026-09-16) |
