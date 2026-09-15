# VPS provisioning guide — rebuild the live box from nothing

**Purpose:** if the server breaks, a new one is rebuilt by following this file top to bottom. Every step is
either a script in `provisioning/` or a command shown verbatim. The engineer updates this file in the same
commit as any change to the box. Spec: `docs/phase3-live-system-requirements.md` §2 (security), §7 (ops).

| item | value |
|---|---|
| OS | Windows Server 2022 (provider image) |
| ops account | `hybridops` (local Administrators; SSH key-only; built-in `Administrator` denied over SSH) |
| WireGuard | server `10.77.0.1/24`, UDP `51820`; engineer workstation peer `10.77.0.2/32` |
| inbound allowed | UDP 51820 (WireGuard) and TCP 22 on `10.77.0.1` only. Everything else blocked, RDP disabled (S2) |
| clock | UTC |
| secrets | `C:\ProgramData\hybrid\secrets\` (never in the repo), readable by `hybridops` + the service accounts only |
| status | step 1 written 2026-09-15, awaiting first run |

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
only, `DenyUsers Administrator`, service dependency on the tunnel; WireGuard MSI (silent) + `wg0` tunnel
service with the engineer's public key as the only peer; firewall default-deny inbound + the two allow
rules; UTC; Windows Update set to download-and-notify (no auto-install/reboot).

**Engineer side (WSL):** private keys live in `~/.ssh/hybrid_vps_ed25519` and `~/.wireguard/client.key`.
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
Re-issuing keys: generate new ones, put the public halves in `bootstrap.ps1`, re-run step 1 (idempotent).

## Step 2 — lock the console (after SSH over WireGuard is confirmed)
_To be filled in when executed: disable RDP at the service level (`TermService` disabled + the Remote
Desktop firewall group off), external port scan from outside the tunnel must show only UDP 51820 (S1)._

## Step 3 — MT5 terminal, EA deploy, live `.ini` (AutoTrading armed)
_Pending._

## Step 4 — web app, notifier, advisor runner, watchdog as services
_Pending._

## Step 5 — backup / restore and the Windows Update maintenance window
_Pending._

## Recovery quick-reference
_Pending (filled in with the first real recovery drill, spec §7)._
