#!/usr/bin/env python3
"""os_shot_daemon.py — OS-level auto-capture of blind setup charts (tester runs).

WHY THIS EXISTS: ChartScreenShot() yields no file in the MT5 Strategy Tester (it
returns success but writes nothing — confirmed empirically). So instead of asking
MT5 to save the chart, we screenshot the visual-tester *window* at the OS level via
PrintWindow (occlusion-immune — see pipeline/win_capture.ps1), the moment the
approval popup appears.

FLOW (poll loop, ~2s):
  1. List windows (powershell). Detect the approval popup by title:
        "Signal #<N>  -  <strategy>  <dir>"      → gives id + strategy + direction
  2. On a NEW id, PrintWindow the visual-tester chart window:
        "Strategy Tester Visualization : ... on <SYM>,H4 from <YYYY.MM.DD> ..."
     → gives SYMBOL + test-start stamp; the raw PNG has ALL overlays.
  3. Blind-crop the raw window capture (drop the title/menu/toolbar + the chart's
     top-left SYMBOL label at the top; drop the time-axis DATES + toolbox at the
     bottom). Prices on the right axis stay (advisor gets prices by design).
     NOTE: economic-event lines print currency+event name mid-chart and cannot be
     cropped — the launcher sets InpShowEvents=false so they're never drawn.
  4. When the journal row for id N exists (written after the user answers), build
     the blind bundle exactly like inbox_bridge: h4.png (this crop) + d1.png
     (rendered) + setup.md (blind numbers), into the advisor inbox + _archive.

For LIVE trading, ChartScreenShot works and inbox_bridge --watch is the right tool;
this daemon is specifically for tester replays. Reuses inbox_bridge helpers so the
bundle format is identical → pipeline.assistant.frontend --replay-bundles scores it.

stdlib + Pillow; drives powershell.exe (win_capture.ps1) for the capture primitive.
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO.parent))
from pipeline.inbox_bridge import (              # noqa: E402
    find_journal_row, blind_setup_md, render_d1, INBOX, ARCHIVE, JOURNAL_DIR)

# The EA writes a per-signal sidecar here the INSTANT the signal fires (before the
# dialog) — the blind numbers, FILE_COMMON so they're visible live. Reading this
# (rather than the post-decision journal row) is what makes delivery real-time:
# the bundle lands while the approval popup is still up.
PENDING_DIR = JOURNAL_DIR / "pending"


def find_pending(symbol: str, sid: int):
    """Newest pre-dialog sidecar for (symbol, id), across ANY test-start stamp.
    Returns (row, stamp) or (None, None). The stamp comes from the sidecar FILENAME
    — never the visual-window title, whose 'from <date>' is the test RANGE start and
    differs from the EA's g_start_time (first traded bar) when the range opens on a
    non-trading day (L2 range 2022.01.01 but first bar 2022.01.02 → daemon looked for
    _20220101_ while the EA wrote _20220102_)."""
    cands = sorted(PENDING_DIR.glob(f"{symbol}_*_{sid}.csv"),
                   key=lambda p: p.stat().st_mtime)
    for p in reversed(cands):
        try:
            base, sidpart = p.stem.rsplit("_", 1)   # EURUSD.dk_20220102_3 → (…_20220102, 3)
            _sym, stamp = base.rsplit("_", 1)        # EURUSD.dk_20220102 → (EURUSD.dk, 20220102)
        except ValueError:
            continue
        if sidpart != str(sid):
            continue
        try:
            with open(p, newline="", encoding="utf-8", errors="replace") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("signal_id") or "").strip() == str(sid):
                        return r, stamp
        except OSError:
            continue
    return None, None

# --- Windows-side scratch (PowerShell can't -File a \\wsl.localhost path) --------
WIN_TMP = Path("/mnt/c/Users/jacks/AppData/Local/Temp")
PS_SRC = REPO / "win_capture.ps1"
PS_WIN_WSL = WIN_TMP / "win_capture.ps1"          # copy lives here (real C: path)

VISUAL_MATCH = "Strategy Tester Visualization"
POPUP_RE = re.compile(r"^Signal #(\d+)\b")

# Grab the chart this long AFTER the popup first appears. The EA scrolls to
# CHART_END at signal-fire, but the visual chart keeps settling for a moment after
# the popup opens (the latest bar finishes rendering at the right edge) — capturing
# on the first poll grabs it one bar early. Tunable if it's still off.
SETTLE_S = 2.0
# leading space + strict symbol charset so " on " doesn't match inside
# "Visualizati·on·"; symbol is like EURUSD.dk, followed by ",H4 from <date>"
VISUAL_RE = re.compile(r" on ([A-Za-z0-9.]+),\S+ from (\d{4})\.(\d{2})\.(\d{2})")

# blind crop as fractions of window height (calibrated on the 1936x1048 maximized
# visual window: title/menu/toolbar+symbol-label ≈ top 9.5%; time-axis+toolbox
# ≈ below 81%). Proportional so a differently-sized window still blinds correctly.
CROP_TOP_FRAC = 0.095
CROP_BOT_FRAC = 0.81


def _winpath(p: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(p)],
                          capture_output=True, text=True).stdout.strip()


def _ps(*args) -> str:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", _winpath(PS_WIN_WSL), *args],
        capture_output=True, text=True, timeout=60).stdout


def list_windows() -> list[dict]:
    out = _ps("-mode", "list")
    wins = []
    for line in out.splitlines():
        parts = line.split("|~|")
        if len(parts) == 6:
            wins.append({"hwnd": parts[0], "w": int(parts[1]), "h": int(parts[2]),
                         "l": int(parts[3]), "t": int(parts[4]), "title": parts[5]})
    return wins


def capture_visual(dst: Path) -> bool:
    out = _ps("-mode", "cap", "-match", VISUAL_MATCH, "-out", _winpath(dst))
    return out.startswith("OK") and dst.exists()


def blind_crop(raw: Path, dst: Path):
    from PIL import Image
    im = Image.open(raw).convert("RGB")
    w, h = im.size
    top = round(CROP_TOP_FRAC * h)
    bot = round(CROP_BOT_FRAC * h)
    im.crop((0, top, w, max(top + 1, bot))).save(dst, "PNG")


def parse_visual(title: str):
    m = VISUAL_RE.search(title)
    if not m:
        return None, None
    sym = m.group(1).strip()
    stamp = f"{m.group(2)}{m.group(3)}{m.group(4)}"
    return sym, stamp


def build_bundle(sid: int, stamp: str, sym: str, raw: Path, row) -> bool:
    md, sig_dt = blind_setup_md(row)
    arch = ARCHIVE / f"{stamp}_{sid}"
    arch.mkdir(parents=True, exist_ok=True)
    blind_crop(raw, arch / "h4.png")
    d1_ok = render_d1(sym, sig_dt, arch / "d1.png") if sig_dt else False
    if not d1_ok:
        md += "\n_(D1 render unavailable for this symbol — use the H4 + numbers.)_\n"
    (arch / "setup.md").write_text(md, encoding="utf-8")
    INBOX.mkdir(parents=True, exist_ok=True)
    blind_crop(raw, INBOX / "h4.png")
    if d1_ok:
        render_d1(sym, sig_dt, INBOX / "d1.png")
    (INBOX / "setup.md").write_text(md, encoding="utf-8")
    return d1_ok


def watch(interval: float):
    shutil.copyfile(PS_SRC, PS_WIN_WSL)           # ensure the C: copy is current
    print(f"os_shot_daemon watching for approval popups (every {interval}s) — "
          "Ctrl-C to stop")
    captured: dict[int, tuple] = {}               # id -> (raw_path, sym, stamp), awaiting row
    done: set[int] = set()
    while True:
        try:
            wins = list_windows()
            visual = next((w for w in wins if VISUAL_MATCH in w["title"]), None)
            # 1) new popups → capture the chart image now (overlays are live)
            for w in wins:
                m = POPUP_RE.match(w["title"])
                if not m:
                    continue
                sid = int(m.group(1))
                if sid in captured or sid in done:
                    continue
                if not visual:
                    print(f"  Signal #{sid}: popup up but no visual window found — "
                          "skipping capture")
                    continue
                sym, stamp = parse_visual(visual["title"])
                if not sym:
                    print(f"  Signal #{sid}: could not parse visual title — skip")
                    continue
                time.sleep(SETTLE_S)   # let the chart settle on the latest bar first
                raw = WIN_TMP / f"hft_raw_{stamp}_{sid}.png"
                if capture_visual(raw):
                    captured[sid] = (raw, sym, stamp)
                    print(f"  Signal #{sid} {sym}: chart captured")
                else:
                    print(f"  Signal #{sid}: PrintWindow capture FAILED")
            # 2) complete bundles as soon as the numbers exist. The EA's pre-dialog
            #    sidecar is written at signal-fire → delivery is real-time (popup
            #    still up); the post-decision journal row is the fallback. The real
            #    test-start stamp comes from the sidecar, not the title (see above).
            for sid, (raw, sym, title_stamp) in list(captured.items()):
                row, stamp = find_pending(sym, sid)
                if not row:
                    row, stamp = find_journal_row(sym, title_stamp, sid), title_stamp
                if row:
                    d1 = build_bundle(sid, stamp, sym, raw, row)
                    print(f"  Signal #{sid} {sym}: bundle delivered "
                          f"(d1={'yes' if d1 else 'no'}) → {ARCHIVE}/{stamp}_{sid}/")
                    done.add(sid)
                    del captured[sid]
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped."); break
        except subprocess.TimeoutExpired:
            print("  (powershell timeout — retrying)")
            continue


def test_capture():
    """One-shot: capture the visual window now + blind-crop it, print paths.
    For calibration — verify the crop is blind on a real capture."""
    shutil.copyfile(PS_SRC, PS_WIN_WSL)
    wins = list_windows()
    visual = next((w for w in wins if VISUAL_MATCH in w["title"]), None)
    if not visual:
        sys.exit(f"no '{VISUAL_MATCH}' window found — start the visual tester first")
    sym, stamp = parse_visual(visual["title"])
    print(f"visual window: {visual['w']}x{visual['h']}  sym={sym} stamp={stamp}")
    raw = WIN_TMP / "hft_test_raw.png"
    if not capture_visual(raw):
        sys.exit("capture failed")
    dst = WIN_TMP / "hft_test_blind.png"
    blind_crop(raw, dst)
    print(f"raw:   {raw}\nblind: {dst}")


def main():
    ap = argparse.ArgumentParser(description="OS-level blind chart auto-capture (tester).")
    ap.add_argument("--watch", action="store_true", help="poll for popups and deliver bundles")
    ap.add_argument("--interval", type=float, default=2.0, help="poll seconds")
    ap.add_argument("--test-capture", action="store_true",
                    help="one-shot capture+crop of the current visual window (calibration)")
    a = ap.parse_args()
    if a.test_capture:
        test_capture()
    elif a.watch:
        watch(a.interval)
    else:
        ap.error("use --watch (run) or --test-capture (calibrate)")


if __name__ == "__main__":
    main()
