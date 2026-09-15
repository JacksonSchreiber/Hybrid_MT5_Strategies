#Requires -RunAsAdministrator
<#
  lockdown.ps1 - STEP 2 of docs/vps-provisioning.md. Run by the engineer over SSH once SSH-over-WireGuard
  is confirmed. Spec S1/S2/S10: RDP and WinRM disabled at the service level, every stock inbound allow rule
  off except DHCP and ICMP fragmentation, leaving exactly the two Hybrid rules. Idempotent. Recovery path if
  this ever locks you out: the provider's VNC console (no RDP needed).
#>
$ErrorActionPreference = 'Continue'
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' fDenyTSConnections 1
Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue
sc.exe config TermService start= disabled | Out-Null       # no Stop-Service: it hangs while an RDP session is open; dies at reboot
sc.exe config WinRM start= disabled | Out-Null
Stop-Service WinRM -Force -ErrorAction SilentlyContinue
Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow |
  Where-Object { $_.DisplayName -notlike 'Hybrid *' -and $_.DisplayName -notmatch 'DHCP-In|Fragmentation Needed' } |
  Disable-NetFirewallRule
"inbound allow: " + ((Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow | ForEach-Object DisplayName) -join ' | ')
"TermService=" + (Get-Service TermService).StartType + " WinRM=" + (Get-Service WinRM).StartType + " fDeny=" + (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server').fDenyTSConnections
