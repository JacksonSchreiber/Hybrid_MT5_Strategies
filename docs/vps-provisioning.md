# VPS provisioning guide — rebuild the live box from nothing

**Purpose:** if the server breaks, a new one is rebuilt by following this file top to bottom. Every step is
either a script in `provisioning/` or a command shown verbatim. The engineer updates this file in the same
commit as any change to the box. Spec: `docs/phase3-live-system-requirements.md` §2 (security), §7 (ops).

| item | value |
|---|---|
| OS | Windows Server 2022 Datacenter (Contabo image), 8 GB RAM, ~87 GB free on C: |
| broker/terminal | OANDA MT5 (demo on `OANDA-Demo-1`); the FTMO challenge account, when it arrives, is a second terminal install |
| lineup (coach ruling 2026-09-16) | `US100.sim US500.sim USOIL.sim XAUUSD.sim` ×1.0, `EURUSD.sim GBPUSD.sim` ×0.5 (`config\risk_mult.json` = `provisioning/risk_mult.json`); one EA instance per chart, per-symbol heartbeats. No US30 (untrained), no USDJPY |
| ops account | `hybridops` (local Administrators; SSH key-only; built-in `Administrator` denied over SSH) |
| WireGuard | server `10.77.0.1/24`, UDP `51820`; peers: engineer workstation `10.77.0.2/32`, trader phone `10.77.0.3/32` |
| inbound allowed | UDP 51820 (WireGuard) and TCP 22 on `10.77.0.1` only. Everything else blocked, RDP disabled (S2) |
| clock | UTC |
| secrets | `C:\ProgramData\hybrid\secrets\` (ACL: Administrators + SYSTEM only; never in the repo): `mt5.json` {login, password, investor_password, server}, `telegram.token`, `telegram.chat_id`, `advisor.token` (Claude Code OAuth from `claude setup-token`, bills the subscription). Master copies live in the trader's KeePassXC; re-copy with `scp` over the tunnel |
| status | steps 1–4 done 2026-09-15; EA live-heartbeating on EURUSD.sim; web/monitor/advisor services up; external probe: no TCP port open |
| provider | Contabo. Recovery path if SSH is ever lost: Contabo panel → VPS → VNC (browser). Log in as Administrator, PowerShell as admin |

## Step 1 — bootstrap over RDP (the only step ever done by hand on the console)

Fresh box, RDP open, logged in as Administrator.

1. Open **PowerShell as Administrator**.
2. Get `provisioning/bootstrap.ps1` onto the box. Either paste it into the console (RDP clipboard) after
   `notepad C:\bootstrap.ps1`, or if the repo is reachable:
   ```powershell
   Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/JacksonSchreiber/Hybrid_MT5_Strategies/main/provisioning/bootstrap.ps1 -OutFile C:\bootstrap.ps1
   ```
3. Run it:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   C:\bootstrap.ps1
   ```
4. Send the engineer the green block it prints (`public_ip`, `wg_server_pubkey`). Nothing in it is secret.
   Transcript: `C:\ProgramData\hybrid\bootstrap.log`.

What it did: `hybridops` account; OpenSSH Server key-only, PowerShell as shell, listening on `10.77.0.1`
only, `DenyUsers Administrator`, sshd **delayed start + restart-on-failure + `hybrid-sshd-keeper` task**
(the tunnel service reports running before the adapter has its address; without these sshd crashes at boot); WireGuard MSI (silent) + `wg0` tunnel
service with the engineer's public key as the only peer; firewall default-deny inbound + the two allow
rules; UTC; Windows Update set to download-and-notify (no auto-install/reboot).

**Engineer side (WSL):** private keys live in `~/.ssh/hybrid_vps_ed25519` and `~/.wireguard/client.key`; the phone's
full config is `~/.wireguard/phone.conf` (also kept in the trader's KeePassXC). Private keys are never in the repo.
Tunnel config `~/.wireguard/hybridvps.conf`:
```
[Interface]
PrivateKey = <client.key>
Address = 10.77.0.2/32

[Peer]
PublicKey = <wg_server_pubkey from step 1>
Endpoint = <public_ip>:51820
AllowedIPs = 10.77.0.0/24
PersistentKeepalive = 25
```
`sudo wg-quick up ~/.wireguard/hybridvps.conf` then `ssh -i ~/.ssh/hybrid_vps_ed25519 hybridops@10.77.0.1`.
The script is idempotent: re-run it after any failure (accounts, keys and rules are reused/replaced, never duplicated).
Re-issuing keys: generate new ones, put the public halves in `bootstrap.ps1`, re-run step 1 (idempotent).

## Step 2 — lock the console (done 2026-09-15)

Over SSH, run `provisioning/lockdown.ps1` (copy it with `scp -i ~/.ssh/hybrid_vps_ed25519 provisioning/lockdown.ps1
hybridops@10.77.0.1:C:/ProgramData/hybrid/` then `ssh … 'powershell -ExecutionPolicy Bypass -File C:\ProgramData\hybrid\lockdown.ps1'`).
Then `ssh … 'Restart-Computer -Force'`, wait ~90 s, confirm SSH returns, and probe the public IP from outside the
tunnel: every TCP port must be closed (S1). Final state: inbound allow = `Hybrid WireGuard in`, `Hybrid SSH over
WireGuard`, DHCP-In, ICMPv4 fragmentation-needed; `TermService` and `WinRM` disabled; `fDenyTSConnections=1`.

**Incident record (2026-09-15):** first reboot after lockdown locked the box out — sshd started in the same second
the tunnel service reported running, before `10.77.0.1` existed, and died; a timed "re-enable RDP" safety task
also failed (PowerShell quoting inside a scheduled-task argument). Recovered via the provider console with four
lines (`sc.exe failure sshd …`, `sc.exe failureflag sshd 1`, `Start-Service sshd`). Permanent fix is in
`bootstrap.ps1` (delayed start, failure actions, file-based keeper task). Lesson: scheduled tasks run a `.ps1`
file, never an inline `-Command` string.

## Step 3 — MT5 terminal, EA deploy, live start-up (done 2026-09-15, data-feed check pending)

**Session model (why):** MT5 cannot create chart windows in an SSH (session 0) context, and an EA needs a chart. So the
terminal runs in the console session: `hybridops` auto-logs on at boot (password stored as an LSA secret by Sysinternals
Autologon, not plaintext registry), the console locks itself 0 s after logon (`hybrid-lock-console` task) and the
terminal starts 20 s after logon (`hybrid-mt5` task, restarts up to 3× a minute apart). The console is reachable only
through the Contabo VNC panel. The `hybridops` password lives in `C:\ProgramData\hybrid\secrets\hybridops.password`
(needed only to re-register tasks after a rebuild).

Over SSH:
1. `Invoke-WebRequest https://download.mql5.com/cdn/web/oanda.corporation/mt5/oanda5setup.exe -OutFile C:\ProgramData\hybrid\oanda5setup.exe`
   then `Start-Process … -ArgumentList /auto -Wait` (exit code 1 = it could not launch the GUI; the files are installed).
2. Autologon: `Autologon64.exe /accepteula hybridops <computer> <password>` (download from live.sysinternals.com).
3. Tasks: `hybrid-lock-console` (at logon of hybridops: `rundll32 user32.dll,LockWorkStation`), `hybrid-mt5` (at logon
   + 20 s: `terminal64.exe /config:C:\ProgramData\hybrid\mt5\live.ini`, no stored password = interactive session).
4. `C:\ProgramData\hybrid\mt5\live.ini` = `provisioning/live.ini` with `Login/Password/Server` inserted under
   `[Common]` from `secrets\mt5.json` (the template in the repo has no credentials; **a `[Common]` section without
   them makes the terminal start logged-out**). `[Experts] Enabled=1 AllowLiveTrading=1 AllowDllImport=0` arms
   AutoTrading (§11-8). `[StartUp]` attaches `HybridForwardTest-live` to EURUSD H4 with `MQL5\Presets\hft_live.set`.
5. Deploy (built locally by `pipeline/mt5_build_ea.sh`, copied with `scp`): `MQL5\Experts\HybridForwardTest-live.ex5`,
   `MQL5\Indicators\HybridTriEMA.ex5`, `MQL5\Presets\hft_live.set` (= `provisioning/hft_live.set`),
   `Common\Files\econ_events.csv`. Data dir: `C:\Users\hybridops\AppData\Roaming\MetaQuotes\Terminal\EE0304F13905552AE0B5EAEFB04866EB`;
   queue root: `C:\Users\hybridops\AppData\Roaming\MetaQuotes\Terminal\Common\Files\live`.
6. Reboot; expect: `terminal64` in session 1, terminal log `authorized on <server>` + `trading has been enabled`,
   expert log `HybridForwardTest ACTIVE [LIVE(queue)]`, `live\heartbeat_EURUSD.json` rewritten every 60 s with
   `terminal_trade_allowed:true, mql_trade_allowed:true`.

**Six charts, one EA each (2026-09-16):** MT5's `[StartUp]` attaches one expert to one chart and the profile only
persists charts on a graceful exit, so the EA is attached by scripts instead: `HybridSaveTemplate` (run ONCE on a chart
with the live EA attached: `[StartUp] Expert=HybridForwardTest-live … Script=HybridSaveTemplate`) saves
`MQL5\Profiles\Templates\hybrid_live.tpl` = the EA + all its inputs; the permanent `live.ini` runs
`[StartUp] Script=HybridLiveLauncher` (host chart EURUSD.sim H4), which opens one H4 chart per lineup symbol (input
`InpSymbols`), applies the template to any chart without the EA, and closes non-lineup charts. Idempotent on every
terminal start. Both scripts live in `mql5/scripts/`, compiled `.ex5` deployed to `MQL5\Scripts\`. After an EA
rebuild: copy the new `.ex5`, Stop-Process the terminal, Start-ScheduledTask hybrid-mt5 (the template references the
file by path, so every chart loads the new build). The template must be re-saved only if the EA's INPUTS change
(`hft_live.set` is no longer what the charts read - the template is).

**Symbol naming:** OANDA's MT5 servers suffix symbols with `.sim` (`EURUSD.sim`); a `[StartUp] Symbol=EURUSD` chart never
synchronises and the EA is removed after 5 min ("symbol synchronization timeout"). The queue files are therefore
`heartbeat_EURUSD.sim.json`, `signals/EURUSD.sim-*.json`, journals `EURUSD.sim_YYYYMM.csv`; `risk_mult.json` keys use the
root (`EURUSD`). An FTMO server uses plain `EURUSD` — ini change only. Verified 2026-09-15 21:59 UTC: heartbeat every
60 s on the wall clock, `account_login` set, equity 25 000, `terminal_trade_allowed`/`mql_trade_allowed` true,
`trading_enabled` false (no `config\trading_enabled.json` yet — the kill switch stays off until the shadow period).

## Step 4 — web app, monitor (notifier + watchdog), advisor runner (done 2026-09-15)

Runtime (over SSH, once): Python 3.12 (`python-3.12.6-amd64.exe /quiet InstallAllUsers=1 PrependPath=1`) + `pip install
pillow certifi matplotlib`; Node 22 (`node-v22.11.0-x64.msi /qn`) + `npm i -g @anthropic-ai/claude-code`.
Layout: `C:\ProgramData\hybrid\live\{live\*.py, live_config.json, hybrid-*.cmd}`; advisor tree
`C:\ProgramData\hybrid\advisor\live\{quick-reference.html, advisor\{CLAUDE.md (= CLAUDE.live.md), library\,
.claude\settings.json, notes.live.md, verdicts.live.log, bundles\}}`; logs `C:\ProgramData\hybrid\logs\`.

Deploy from the workstation: `provisioning/deploy_live.sh` (copies code + `provisioning/live_config.vps.json` + the
live advisor material, runs `provisioning/live_tasks.ps1`, restarts the tasks, checks `/health` over the tunnel).
`--code` skips the advisor material. Tasks `hybrid-web`, `hybrid-monitor`, `hybrid-advisor`: at startup + 45 s, as
`hybridops` with the stored password, restart every minute on failure, each runs its `.cmd` (never inline commands).
Firewall: `Hybrid web over WireGuard` = TCP 8080 on `10.77.0.1` only. The advisor folder is pre-trusted in
`C:\Users\hybridops\.claude.json` (otherwise its `settings.json` permissions are ignored).

Web app: `http://10.77.0.1:8080` from any WireGuard peer (phone = 10.77.0.3). No login (trader ruling 2026-09-15:
the tunnel is the boundary); plain HTTP inside the tunnel. Verify after any deploy or reboot: `/health` returns
`{"ok":true}`, dashboard shows `EA alive` with a fresh beat, `hybrid-monitor.task.log` shows a successful Telegram
send (the daily summary fires once after start when past 21:05 UTC).

**Calendar (coach ruling 2026-09-16, same source made live):** `live/calendar_refresh.py`, run by the monitor daily at
02:30 UTC (and on start if the deployed file is >1.5 d old). Pulls ForexFactory's official
`ff_calendar_thisweek.xml` (the ONLY rolling file - `nextweek` does not exist; GMT times; stable series id in `<url>`),
snapshots it under `C:\ProgramData\hybrid\calendar\snapshots\`, accumulates rows into `forward.json` keyed by
(series id, UTC date) so re-pulls replace revised times, then FULLY REBUILDS: history (`history\ff_combined.csv`,
rows before the store's first day) + forward store → `pipeline\normalize_econ_tzfix.py` (classes from
`config\event_classes.yaml`, `config\political_events.csv` merged) → `pipeline\test_calendar_coverage.py` must exit 0
→ sanity (row count, coverage end ≥ today, V-class present in the forward window) → atomic replace of
`Common\Files\econ_events.csv`. Every EA instance reloads the file once a day after 03:00 UTC (`events_reload`
audit line; heartbeat carries `events_count`/`events_last_utc`). Monitor alerts: refresh failure, file >7 d old,
coverage <3 d. Needs PyYAML on the box (`pip install pyyaml`). Deploy the pipeline with `deploy_live.sh --calendar`.
Known limit: FF gives ≤7 days of forward visibility; elections come from `political_events.csv` (manual, must be
kept ≥12 months ahead); scheduled V-class beyond one week needs a second forward layer (ruling pending).

Gotchas found: (1) a child `claude` inherits `CLAUDE_*` variables from a parent Claude Code session and hangs — the
runner scrubs them; (2) Python's default cert store on a fresh Windows box fails Telegram's chain — `certifi` is used
when present; (3) `.sim` symbol suffix (step 3).

## Step 5 — backup / restore and the Windows Update maintenance window
_Pending._

## Recovery quick-reference
_Pending (filled in with the first real recovery drill, spec §7)._
