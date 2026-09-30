# sshd-keeper.ps1 - keep SSH actually usable, not merely "Running".
#
# A wedged sshd looks perfectly healthy to Get-Service: the service says Running, the port accepts TCP, and then the
# daemon never sends its banner, so every new session hangs at the handshake. That is what happened on 2026-09-30 -
# nine and a half hours locked out of a box whose web app was answering the whole time, while the old keeper
# (`if ((Get-Service sshd).Status -ne "Running") { Start-Service sshd }`) saw "Running" and did nothing.
#
# So probe the PROTOCOL: connect, read the first bytes, and require an "SSH-" banner. Two consecutive silent probes
# (>= 5 min apart) before restarting, because a restart kills live sessions and one timeout can just be a busy box.
$ErrorActionPreference = 'SilentlyContinue'
$addr  = '10.77.0.1'
$port  = 22
$state = 'C:\ProgramData\hybrid\sshd-keeper.state'
$log   = 'C:\ProgramData\hybrid\logs\sshd-keeper.log'

function Test-SshBanner {
    try {
        $c = New-Object Net.Sockets.TcpClient
        if (-not $c.ConnectAsync($addr, $port).Wait(5000)) { $c.Close(); return $false }
        $s = $c.GetStream(); $s.ReadTimeout = 5000
        $buf = New-Object byte[] 16
        $n = $s.Read($buf, 0, 16)
        $c.Close()
        return ($n -gt 0 -and [Text.Encoding]::ASCII.GetString($buf, 0, $n).StartsWith('SSH-'))
    } catch { return $false }
}

if ((Get-Service sshd).Status -ne 'Running') {
    Start-Service sshd
    Set-Content -Path $state -Value '0' -Encoding ascii
    Add-Content -Path $log -Value ("{0}Z sshd was stopped - started" -f (Get-Date).ToUniversalTime().ToString('s'))
    exit
}

if (Test-SshBanner) { Set-Content -Path $state -Value '0' -Encoding ascii; exit }

$n = 0
if (Test-Path $state) { $n = [int]((Get-Content $state -Raw).Trim()) }
$n++
Set-Content -Path $state -Value $n -Encoding ascii
Add-Content -Path $log -Value ("{0}Z sshd running but sent no banner (strike {1} of 2)" -f (Get-Date).ToUniversalTime().ToString('s'), $n)

if ($n -ge 2) {
    Restart-Service sshd -Force
    Set-Content -Path $state -Value '0' -Encoding ascii
    Add-Content -Path $log -Value ("{0}Z sshd silent on two consecutive probes - RESTARTED" -f (Get-Date).ToUniversalTime().ToString('s'))
}
