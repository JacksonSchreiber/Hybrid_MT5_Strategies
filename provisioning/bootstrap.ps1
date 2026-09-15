#Requires -RunAsAdministrator
<#
  bootstrap.ps1 - STEP 1 of docs/vps-provisioning.md. Run ONCE, as Administrator, in PowerShell on a fresh
  Windows Server 2022 box (over RDP). After it prints the server's WireGuard public key and public IP, the
  engineer connects over WireGuard + SSH and RDP is disabled at the service level (spec S2).

  What it does (spec §2.1/2.2): dedicated admin account `hybridops` (SSH key-only; built-in Administrator
  denied over SSH), OpenSSH Server bound ONLY to the WireGuard address, WireGuard tunnel service
  (10.77.0.1/24, UDP 51820), firewall default-deny inbound with exactly two allow rules (WireGuard UDP,
  SSH on 10.77.0.1). RDP is left as-is until SSH-over-WireGuard is confirmed working.

  Only PUBLIC keys are embedded here; private keys never leave their owner.
#>
$ErrorActionPreference = 'Stop'
$OpsUser   = 'hybridops'
$ClientPub = 'F+UOLc2dyuJ+koxrAA9H+T6rVcxnHECN72hgC6VZGmo='                              # engineer's WireGuard public key
$SshPub    = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII+uWJpLb6qHbClSbkyw1S64wTjJpwijNnk7LnhCpMfz hybrid-ops@wsl'
$WgNet     = '10.77.0'; $ServerIp = "$WgNet.1"; $ClientIp = "$WgNet.2"; $WgPort = 51820
$WgMsi     = 'https://download.wireguard.com/windows-client/wireguard-amd64-0.5.3.msi'
$Log       = 'C:\ProgramData\hybrid\bootstrap.log'
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
Start-Transcript -Path $Log -Append | Out-Null
function Step($m){ Write-Host "`n=== $m" -ForegroundColor Cyan }

Step "1/6 dedicated admin account $OpsUser (random password, never used: SSH is key-only)"
if (-not (Get-LocalUser -Name $OpsUser -ErrorAction SilentlyContinue)) {
  $pw = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
  New-LocalUser -Name $OpsUser -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires -Description 'Hybrid ops (SSH key-only)' | Out-Null
  Add-LocalGroupMember -Group 'Administrators' -Member $OpsUser
}

Step "2/6 OpenSSH Server (key-only, PowerShell shell, Administrator denied)"
if ((Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State -ne 'Installed') { Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null }
Set-Service sshd -StartupType Automatic
Start-Service sshd; Start-Sleep 3; Stop-Service sshd            # first start materialises C:\ProgramData\ssh\*
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force | Out-Null
$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path $ak -Value $SshPub -Encoding ascii
icacls $ak /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
$cfg = 'C:\ProgramData\ssh\sshd_config'
$body = Get-Content $cfg | Where-Object { $_ -notmatch '^\s*(PasswordAuthentication|PubkeyAuthentication|ListenAddress|DenyUsers|KbdInteractiveAuthentication)\b' }
$body += @('', '# --- hybrid provisioning (spec S3) ---', "ListenAddress $ServerIp", 'PubkeyAuthentication yes',
           'PasswordAuthentication no', 'KbdInteractiveAuthentication no', 'DenyUsers Administrator')
Set-Content -Path $cfg -Value $body -Encoding ascii
Remove-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue   # the capability's any-address rule

Step "3/6 WireGuard tunnel service wg0 ($ServerIp/24, UDP $WgPort)"
$wgDir = 'C:\Program Files\WireGuard'
if (-not (Test-Path "$wgDir\wireguard.exe")) {
  $msi = "$env:TEMP\wireguard.msi"; Invoke-WebRequest -Uri $WgMsi -OutFile $msi -UseBasicParsing
  Start-Process msiexec.exe -ArgumentList "/i `"$msi`" DO_NOT_LAUNCH=1 /qn" -Wait
}
$confDir = 'C:\ProgramData\WireGuard'; New-Item -ItemType Directory -Force -Path $confDir | Out-Null
icacls $confDir /inheritance:r /grant 'Administrators:(OI)(CI)F' /grant 'SYSTEM:(OI)(CI)F' | Out-Null
$privPath = "$confDir\server.key"; $pubPath = "$confDir\server.pub"
if (-not (Test-Path $privPath)) {
  $priv = (& "$wgDir\wg.exe" genkey).Trim(); Set-Content -Path $privPath -Value $priv -Encoding ascii -NoNewline
  $pub  = ($priv | & "$wgDir\wg.exe" pubkey).Trim(); Set-Content -Path $pubPath -Value $pub -Encoding ascii -NoNewline
}
$priv = (Get-Content $privPath -Raw).Trim(); $pub = (Get-Content $pubPath -Raw).Trim()
@"
[Interface]
PrivateKey = $priv
Address = $ServerIp/24
ListenPort = $WgPort

# engineer workstation (WSL)
[Peer]
PublicKey = $ClientPub
AllowedIPs = $ClientIp/32
"@ | Set-Content -Path "$confDir\wg0.conf" -Encoding ascii
if (Get-Service 'WireGuardTunnel$wg0' -ErrorAction SilentlyContinue) { & "$wgDir\wireguard.exe" /uninstalltunnelservice wg0; Start-Sleep 2 }
& "$wgDir\wireguard.exe" /installtunnelservice "$confDir\wg0.conf"
Start-Sleep 3
sc.exe config sshd depend= 'WireGuardTunnel$wg0' | Out-Null   # sshd binds 10.77.0.1, so it must start after the tunnel

Step "4/6 firewall: default-deny inbound; allow only WireGuard UDP and SSH on $ServerIp"
Get-NetFirewallRule -DisplayName 'Hybrid *' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Hybrid WireGuard in' -Direction Inbound -Protocol UDP -LocalPort $WgPort -Action Allow | Out-Null
New-NetFirewallRule -DisplayName 'Hybrid SSH over WireGuard' -Direction Inbound -Protocol TCP -LocalPort 22 -LocalAddress $ServerIp -Action Allow | Out-Null
Set-NetFirewallProfile -All -Enabled True -DefaultInboundAction Block -DefaultOutboundAction Allow

Step "5/6 start sshd on the tunnel address"
Start-Service sshd
Start-Sleep 2

Step "6/6 basics: UTC clock, no auto-reboot from Windows Update (maintenance window is set later)"
Set-TimeZone -Id 'UTC'
w32tm /resync | Out-Null
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoRebootWithLoggedOnUsers -Value 1 -Type DWord
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name AUOptions -Value 3 -Type DWord   # download, notify - never auto-install

$pubIp = try { (Invoke-RestMethod -Uri 'https://api.ipify.org' -UseBasicParsing).Trim() } catch { '<lookup failed - use the provider console IP>' }
Write-Host "`n================ HAND THIS TO THE ENGINEER ================" -ForegroundColor Green
Write-Host "public_ip        = $pubIp"
Write-Host "wg_server_pubkey = $pub"
Write-Host "wg_port          = $WgPort"
Write-Host "sshd             = $((Get-Service sshd).Status)   tunnel = $((Get-Service 'WireGuardTunnel$wg0').Status)"
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "RDP is still enabled. It is disabled (step 2 of the guide) only after SSH over WireGuard is confirmed."
Stop-Transcript | Out-Null
