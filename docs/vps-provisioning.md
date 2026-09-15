# VPS provisioning guide — rebuild the live box from nothing

**Purpose:** if the server breaks, a new one is rebuilt by following this file top to bottom. Every step is
either a script in `provisioning/` or a command shown verbatim. The engineer updates this file in the same
commit as any change to the box. Spec: `docs/phase3-live-system-requirements.md` §2 (security), §7 (ops).

| item | value |
|---|---|
| OS | Windows Server 2022 Datacenter (Contabo image), 8 GB RAM, ~87 GB free on C: |
| broker/terminal | OANDA MT5 (demo on `OANDA-Demo-1`); the FTMO challenge account, when it arrives, is a second terminal install |
| ops account | `hybridops` (local Administrators; SSH key-only; built-in `Administrator` denied over SSH) |
| WireGuard | server `10.77.0.1/24`, UDP `51820`; peers: engineer workstation `10.77.0.2/32`, trader phone `10.77.0.3/32` |
| inbound allowed | UDP 51820 (WireGuard) and TCP 22 on `10.77.0.1` only. Everything else blocked, RDP disabled (S2) |
| clock | UTC |
| secrets | `C:\ProgramData\hybrid\secrets\` (ACL: Administrators + SYSTEM only; never in the repo): `mt5.json` {login, password, investor_password, server}, `telegram.token`, `telegram.chat_id`, `advisor.token` (Claude Code OAuth from `claude setup-token`, bills the subscription). Master copies live in the trader's KeePassXC; re-copy with `scp` over the tunnel |
| status | steps 1–3 done 2026-09-15; reboot tests passed; EA live-heartbeating on EURUSD.sim; external probe: no TCP port open |
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

**Symbol naming:** OANDA's MT5 servers suffix symbols with `.sim` (`EURUSD.sim`); a `[StartUp] Symbol=EURUSD` chart never
synchronises and the EA is removed after 5 min ("symbol synchronization timeout"). The queue files are therefore
`heartbeat_EURUSD.sim.json`, `signals/EURUSD.sim-*.json`, journals `EURUSD.sim_YYYYMM.csv`; `risk_mult.json` keys use the
root (`EURUSD`). An FTMO server uses plain `EURUSD` — ini change only. Verified 2026-09-15 21:59 UTC: heartbeat every
60 s on the wall clock, `account_login` set, equity 25 000, `terminal_trade_allowed`/`mql_trade_allowed` true,
`trading_enabled` false (no `config\trading_enabled.json` yet — the kill switch stays off until the shadow period).

## Step 4 — web app, notifier, advisor runner, watchdog as services
_Pending._

## Step 5 — backup / restore and the Windows Update maintenance window
_Pending._

## Recovery quick-reference
_Pending (filled in with the first real recovery drill, spec §7)._
