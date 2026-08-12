<#
  win_scroll_end.ps1 — nudge the visual-tester chart to its latest bar.

  The MT5 Strategy Tester freezes the chart on the tick a new bar forms, a moment
  before it repaints/autoscrolls to include it — so at the approval popup the
  current bar sits one bar off the right edge until you scroll manually. MT5 only
  acts on the "End" key (jump-to-latest) when the chart has real focus (PostMessage
  is ignored), so: briefly foreground the chart window, SendKeys {END}, then restore
  focus to whatever was in front (the popup). Called by os_shot_daemon.py right
  before the capture.
#>
param([string]$match="Strategy Tester Visualization")
Add-Type @"
using System;using System.Runtime.InteropServices;using System.Text;
public class SE {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
Add-Type -AssemblyName System.Windows.Forms
$sb=New-Object System.Text.StringBuilder 512; $t=[IntPtr]::Zero
$cb=[SE+EnumWindowsProc]{ param($h,$l)
  $sb.Clear()|Out-Null; [SE]::GetWindowText($h,$sb,512)|Out-Null
  if($sb.ToString().Contains($match) -and [SE]::IsWindowVisible($h)){ $script:t=$h }; return $true }
[SE]::EnumWindows($cb,[IntPtr]::Zero)|Out-Null
if($t -eq [IntPtr]::Zero){ "NO WINDOW"; exit 2 }
$prev=[SE]::GetForegroundWindow()
[SE]::SetForegroundWindow($t)|Out-Null
Start-Sleep -Milliseconds 180
[System.Windows.Forms.SendKeys]::SendWait("{END}")
Start-Sleep -Milliseconds 120
if($prev -ne [IntPtr]::Zero -and $prev -ne $t){ [SE]::SetForegroundWindow($prev)|Out-Null }
"OK"
