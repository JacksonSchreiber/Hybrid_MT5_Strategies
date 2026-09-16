#Requires -RunAsAdministrator
<#
  live_tasks.ps1 - STEP 4 of docs/vps-provisioning.md: register the three live services as scheduled tasks that
  start at boot as hybridops (stored password from secrets, so they run whether the console is logged on or not),
  restart on failure, and write their stdout to C:\ProgramData\hybrid\logs\<name>.task.log via a .cmd wrapper.
  Also pre-trusts the advisor folder for Claude Code. Idempotent; run after deploy_live.sh copied the files.
#>
$ErrorActionPreference = 'Stop'
$py  = 'C:\Program Files\Python312\python.exe'
$app = 'C:\ProgramData\hybrid\live'
$cfg = "$app\live_config.json"
$logs = 'C:\ProgramData\hybrid\logs'; New-Item -ItemType Directory -Force -Path $logs | Out-Null
$pw = (Get-Content 'C:\ProgramData\hybrid\secrets\hybridops.password' -Raw).Trim()
$svc = @{ 'hybrid-web' = 'webapp.py'; 'hybrid-monitor' = 'monitor.py'; 'hybrid-advisor' = 'advisor_runner.py'; 'hybrid-feed' = 'feedd.py' }
foreach ($name in $svc.Keys) {
  $cmd = "$app\$name.cmd"
  @("@echo off", "cd /d `"$app`"", "`"$py`" -X utf8 -u `"$app\live\$($svc[$name])`" --config `"$cfg`" >> `"$logs\$name.task.log`" 2>&1") | Set-Content -Path $cmd -Encoding ascii
  $act = New-ScheduledTaskAction -Execute $cmd
  $trg = New-ScheduledTaskTrigger -AtStartup; $trg.Delay = 'PT45S'
  $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable
  Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -User 'hybridops' -Password $pw -RunLevel Highest -Force | Out-Null
}
# services keeper (SYSTEM, at startup +90 s then every 5 min): starts any hybrid-web/monitor/advisor task that is not
# running. Boot triggers of user tasks proved unreliable on 2026-09-16 (web+monitor did not launch after a reboot).
$keeper = 'C:\ProgramData\hybrid\services-keeper.ps1'
Set-Content -Path $keeper -Encoding ascii -Value @'
foreach ($t in "hybrid-web","hybrid-monitor","hybrid-advisor","hybrid-feed") { $s = (Get-ScheduledTask $t -ErrorAction SilentlyContinue).State; if ($s -and $s -ne "Running") { Start-ScheduledTask $t } }
'@
$kAct = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $keeper"
$kT1 = New-ScheduledTaskTrigger -AtStartup; $kT1.Delay = 'PT90S'
$kT2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'hybrid-services-keeper' -Action $kAct -Trigger @($kT1, $kT2) -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true 2>$null
# weekly maintenance window: Saturday 14:00 UTC (trader ruling 2026-09-16), runs provisioning/maintenance.ps1 as SYSTEM
$mAct = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\hybrid\maintenance.ps1'
$mTrg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 14:00
$mSet = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
Register-ScheduledTask -TaskName 'hybrid-maintenance' -Action $mAct -Trigger $mTrg -Settings $mSet -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
# Claude Code trust for the advisor folder (settings.json permissions are ignored in an untrusted workspace)
$cj = 'C:\Users\hybridops\.claude.json'
$j = if (Test-Path $cj) { Get-Content $cj -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
if (-not $j.PSObject.Properties['projects']) { $j | Add-Member -NotePropertyName projects -NotePropertyValue ([pscustomobject]@{}) }
$live = 'C:\ProgramData\hybrid\advisor\live\advisor'
foreach ($dirn in @($live, 'C:\ProgramData\hybrid\advisor\live\brief')) {
  if (-not $j.projects.PSObject.Properties[$dirn]) { $j.projects | Add-Member -NotePropertyName $dirn -NotePropertyValue ([pscustomobject]@{ hasTrustDialogAccepted = $true }) }
  else { $j.projects.$dirn | Add-Member -NotePropertyName hasTrustDialogAccepted -NotePropertyValue $true -Force }
}
$j | ConvertTo-Json -Depth 8 | Set-Content -Path $cj -Encoding ascii
"tasks: " + ((Get-ScheduledTask hybrid-web, hybrid-monitor, hybrid-advisor | ForEach-Object { $_.TaskName + '=' + $_.State }) -join ', ')
