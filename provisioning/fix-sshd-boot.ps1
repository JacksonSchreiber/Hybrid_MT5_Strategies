#Requires -RunAsAdministrator
<#
  fix-sshd-boot.ps1 - run from the provider console when SSH over WireGuard does not come back after a reboot.
  Diagnoses (prints sshd state, listeners, last sshd events, tunnel adapter), then makes sshd self-healing:
  service recovery = restart on any exit, plus a `hybrid-sshd-keeper` task (1 min after boot, then every 5 min)
  that starts sshd if it is not running, plus an interface-scoped firewall rule for TCP 22 on the wg0 adapter.
  RDP stays disabled (spec S2). Idempotent.
#>
$ErrorActionPreference = 'Continue'
$ServerIp = '10.77.0.1'
Write-Host "=== diagnosis" -ForegroundColor Cyan
Get-Service sshd, 'WireGuardTunnel$wg0', TermService | Format-Table -AutoSize Name, Status, StartType | Out-String | Write-Host
"tunnel address present: " + [bool](Get-NetIPAddress -IPAddress $ServerIp -ErrorAction SilentlyContinue)
Get-NetAdapter | Where-Object InterfaceDescription -like '*WireGuard*' | Format-Table -AutoSize Name, Status, InterfaceAlias | Out-String | Write-Host
"listeners on :22: " + ((Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalAddress }) -join ', ')
Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 8 -ErrorAction SilentlyContinue | Format-Table -Wrap TimeCreated, Message | Out-String | Write-Host
Get-NetFirewallRule -DisplayName 'Hybrid *' | Format-Table -AutoSize DisplayName, Enabled, Direction, Action | Out-String | Write-Host

Write-Host "=== fix: sshd recovery + keeper task + interface-scoped rule" -ForegroundColor Cyan
sc.exe failure sshd reset= 0 actions= restart/5000/restart/15000/restart/60000 | Out-Null
sc.exe failureflag sshd 1 | Out-Null
$wgAlias = (Get-NetAdapter | Where-Object InterfaceDescription -like '*WireGuard*' | Select-Object -First 1 -Expand Name)
if ($wgAlias -and -not (Get-NetFirewallRule -DisplayName 'Hybrid SSH on wg adapter' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName 'Hybrid SSH on wg adapter' -Direction Inbound -Protocol TCP -LocalPort 22 -InterfaceAlias $wgAlias -Action Allow | Out-Null
}
$keeper = 'if ((Get-Service sshd).Status -ne ''Running'') { Start-Service sshd }'
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -Command `"$keeper`""
$t1  = New-ScheduledTaskTrigger -AtStartup; $t1.Delay = 'PT1M'
$t2  = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'hybrid-sshd-keeper' -Action $act -Trigger @($t1, $t2) -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Unregister-ScheduledTask -TaskName 'hybrid-boot-rollback' -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "=== start sshd" -ForegroundColor Cyan
& 'C:\Windows\System32\OpenSSH\sshd.exe' -t -f 'C:\ProgramData\ssh\sshd_config'; "config test exit=$LASTEXITCODE"
Start-Service sshd -ErrorAction Continue; Start-Sleep 3
"sshd: " + (Get-Service sshd).Status
"listeners on :22: " + ((Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalAddress }) -join ', ')
Write-Host "=== done - tell the engineer to retry SSH" -ForegroundColor Green
