<#
  win_capture.ps1 — OS-level window capture for the blind-advisor pipeline.

  ChartScreenShot() does NOT produce files in the MT5 Strategy Tester (it returns
  true but writes nothing — confirmed). So we grab the visual-tester chart at the
  OS level instead. PrintWindow renders a window's OWN content into a bitmap even
  when the window is occluded/behind others — unlike CopyFromScreen, which grabs
  whatever pixels are physically on top. That occlusion-immunity is the whole
  reason this works while the user has other windows focused.

  Modes:
    -mode list
        Emit one line per visible titled window: hwnd|~|W|~|H|~|L|~|T|~|title
    -mode cap -match <substr> -out <winpath.png>
        PrintWindow the first visible window whose title contains <substr> to
        <winpath.png>. Prints "OK|~|W|~|H|~|title" or "NONE".

  Called by pipeline/os_shot_daemon.py (WSL) via powershell.exe.
#>
param([string]$mode="list",[string]$match="",[string]$out="")

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Drawing;
public class Win {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left,Top,Right,Bottom; }
}
"@ -ReferencedAssemblies System.Drawing

[Win]::SetProcessDPIAware() | Out-Null

$found = New-Object System.Collections.ArrayList
$sb = New-Object System.Text.StringBuilder 1024
$cb = [Win+EnumWindowsProc]{ param($h,$l)
  $sb.Clear() | Out-Null
  [Win]::GetWindowText($h,$sb,1024) | Out-Null
  $t = $sb.ToString()
  if ($t.Length -gt 0 -and [Win]::IsWindowVisible($h) -and -not [Win]::IsIconic($h)) {
    $r = New-Object Win+RECT
    [Win]::GetWindowRect($h,[ref]$r) | Out-Null
    $w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
    if ($w -gt 0 -and $hh -gt 0) {
      $found.Add([pscustomobject]@{H=$h; T=$t; L=$r.Left; Top=$r.Top; W=$w; Hh=$hh}) | Out-Null
    }
  }
  return $true
}
[Win]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null

if ($mode -eq "list") {
  foreach ($x in $found) {
    "{0}|~|{1}|~|{2}|~|{3}|~|{4}|~|{5}" -f $x.H.ToInt64(),$x.W,$x.Hh,$x.L,$x.Top,$x.T
  }
  exit 0
}

# cap mode
$tgt = $found | Where-Object { $_.T -like "*$match*" } | Select-Object -First 1
if (-not $tgt) { "NONE"; exit 2 }
$bmp = New-Object System.Drawing.Bitmap $tgt.W, $tgt.Hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
# flag 2 = PW_RENDERFULLCONTENT (captures GPU/DirectX-composited client area too)
$ok = [Win]::PrintWindow($tgt.H, $hdc, 2)
$g.ReleaseHdc($hdc); $g.Dispose()
if ($ok) { $bmp.Save($out); "OK|~|$($tgt.W)|~|$($tgt.Hh)|~|$($tgt.T)" }
else { "FAIL" }
$bmp.Dispose()
