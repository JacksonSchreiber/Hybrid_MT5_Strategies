$R='C:\Users\hybridops\AppData\Roaming\MetaQuotes\Terminal\Common\Files\live'
$p=Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object -First 1
if(-not $p){ 'terminal64: NOT RUNNING'; exit }
$c0=$p.TotalProcessorTime.TotalSeconds; Start-Sleep 60; $p.Refresh(); $c1=$p.TotalProcessorTime.TotalSeconds
$cores=(Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
$os=Get-CimInstance Win32_OperatingSystem
$tot=Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 2 -MaxSamples 3 | % { $_.CounterSamples.CookedValue } | Measure-Object -Average
'terminal64: working set {0:N0} MB, private {1:N0} MB, CPU {2:N1}% of the box over 60 s ({3:N1}% of one core), threads {4}, handles {5}' -f ($p.WorkingSet64/1MB),($p.PrivateMemorySize64/1MB),(($c1-$c0)/60/$cores*100),(($c1-$c0)/60*100),$p.Threads.Count,$p.HandleCount
'box: RAM {0:N1} of {1:N1} GB free, total CPU {2:N1}% (3 x 2 s), {3} cores' -f ($os.FreePhysicalMemory/1MB),($os.TotalVisibleMemorySize/1MB),$tot.Average,$cores
$now=[DateTime]::UtcNow; $ages=@()
foreach($f in Get-ChildItem "$R\heartbeat_*.json"){ try{ $j=Get-Content $f.FullName -Raw | ConvertFrom-Json; $a=($now-[DateTime]::Parse($j.ts).ToUniversalTime()).TotalSeconds; $ages+=[pscustomobject]@{s=$j.symbol;age=[math]::Round($a);st=$j.status;wf=$j.weekend_flat} }catch{} }
$ok=($ages | ? { $_.st -eq 'running' -and $_.age -lt 180 }).Count
'heartbeats: {0} files, {1} running+fresh (<180 s), age max {2} s, median {3} s' -f $ages.Count,$ok,($ages | Measure-Object age -Maximum).Maximum,(($ages | Sort age)[[int]($ages.Count/2)]).age
$ages | ? { $_.st -ne 'running' -or $_.age -ge 180 } | % { '  STALE/STOPPED: {0} age {1}s status {2}' -f $_.s,$_.age,$_.st }
$ages | ? { $_.wf } | % { '  weekend_flat ON: {0}' -f $_.s }
