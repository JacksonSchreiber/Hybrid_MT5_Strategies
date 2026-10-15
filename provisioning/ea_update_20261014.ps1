# EA update 2026-10-14 (custom levels / pend_bars): the safe box restart (memory: box-mt5-restart-is-hard-kill)
$ErrorActionPreference = 'Stop'
$T   = 'C:/Users/hybridops/AppData/Roaming/MetaQuotes/Terminal/EE0304F13905552AE0B5EAEFB04866EB/MQL5/Experts'
$NEW = 'C:/ProgramData/hybrid/ea_staging/HybridForwardTest-live.ex5'
$BK  = 'C:/ProgramData/hybrid/ea_backup'
New-Item -ItemType Directory -Force -Path $BK | Out-Null
Copy-Item "$T/HybridForwardTest-live.ex5" "$BK/HybridForwardTest-live.ex5.20261012" -Force
"backup " + (Get-FileHash -Algorithm MD5 "$BK/HybridForwardTest-live.ex5.20261012").Hash.ToLower()
try {
  Stop-ScheduledTask hybrid-monitor
  Start-Sleep 2
  Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'monitor\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "killed monitor " + $_.ProcessId }
  Disable-ScheduledTask hybrid-mt5 | Out-Null
  $p = Get-Process terminal64 -ErrorAction SilentlyContinue
  if ($p) { "stopping terminal64 " + $p.Id; Stop-Process -Id $p.Id -Force }
  $n = 0; while ((Get-Process terminal64 -ErrorAction SilentlyContinue) -and $n -lt 60) { Start-Sleep 1; $n++ }
  if (Get-Process terminal64 -ErrorAction SilentlyContinue) { throw "terminal64 still running after 60 s - file NOT swapped" }
  "terminal64 gone after $n s"
  Copy-Item $NEW "$T/HybridForwardTest-live.ex5" -Force
  "installed " + (Get-FileHash -Algorithm MD5 "$T/HybridForwardTest-live.ex5").Hash.ToLower()
} finally {
  Enable-ScheduledTask hybrid-mt5 | Out-Null
  Start-ScheduledTask hybrid-mt5
  Start-Sleep 10
  Start-ScheduledTask hybrid-monitor
  Start-Sleep 3
  "terminal64: " + ((Get-Process terminal64 -ErrorAction SilentlyContinue | ForEach-Object { $_.Id.ToString() + ' started ' + $_.StartTime }) -join ', ')
  Get-ScheduledTask hybrid-mt5, hybrid-monitor | ForEach-Object { $_.TaskName + ' ' + $_.State }
}
