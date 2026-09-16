<#
  maintenance.ps1 - weekly maintenance window (trader ruling 2026-09-16: Saturday afternoon). Task `hybrid-maintenance`
  runs it Saturdays 14:00 UTC as SYSTEM: Telegram "shutting down", graceful MT5 stop, install Windows updates
  (PSWindowsUpdate), reboot. The monitor sends "ALL SYSTEMS UP" once every EA heartbeats after the reboot.
  `-NoUpdates` = reboot only (used for reboot drills).
#>
param([switch]$NoUpdates)
$ErrorActionPreference = 'Continue'
$log = 'C:\ProgramData\hybrid\logs\maintenance.log'
function L($m) { $line = (Get-Date).ToUniversalTime().ToString('s') + 'Z ' + $m; Add-Content -Path $log -Value $line; Write-Host $line }
function TG($text) {
  try {
    $tok = (Get-Content 'C:\ProgramData\hybrid\secrets\telegram.token' -Raw).Trim(); $cid = (Get-Content 'C:\ProgramData\hybrid\secrets\telegram.chat_id' -Raw).Trim()
    Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$tok/sendMessage" -Body @{ chat_id = $cid; text = $text } -TimeoutSec 20 | Out-Null
  } catch { L "telegram failed: $_" }
}
L "maintenance start (NoUpdates=$NoUpdates)"
TG ("MAINTENANCE: box shutting down now for " + $(if ($NoUpdates) { "a reboot drill" } else { "Windows updates + reboot" }) + " (Saturday window). Expect 'ALL SYSTEMS UP' within ~5 min of it coming back.")
# graceful MT5 stop so the profile/journals flush (Stop-Process would also be fine: everything is atomic + restart-safe)
Get-Process terminal64 -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
Start-Sleep 15
Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force
if (-not $NoUpdates) {
  try {
    if (-not (Get-Module -ListAvailable PSWindowsUpdate)) {
      Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
      Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
      Install-Module PSWindowsUpdate -Force -Scope AllUsers
    }
    Import-Module PSWindowsUpdate
    $r = Get-WindowsUpdate -AcceptAll -Install -IgnoreReboot -MicrosoftUpdate -ErrorAction Continue
    L ("updates: " + (($r | ForEach-Object { $_.Title + ' [' + $_.Result + ']' }) -join '; '))
  } catch { L "update step failed: $_" ; TG "MAINTENANCE: Windows update step failed ($_). Rebooting anyway." }
}
L "rebooting"
Restart-Computer -Force
