#!/usr/bin/env python3
"""demo_skip_shadow.py - coach item 11 (29 Sep list): blind outcomes of the trader's 14-day DEMO skips.

Population: every row with decision == "skipped" in data/study/demo_journal (box live/archive/demo-20260929/journal),
auto-skips (election gate, auto=1) shown separately. Each is replayed from its own levels under the 50% doctrine that
was live on the demo (and the 25% bank for reference), from the bar after the signal bar, on the broker's H4 bars
(data/study/h4/<SYM>.sim.csv, fetched from the box feed; journal signal_time and bar epochs are both broker clock).
Raw R (no spread-drag entry exists for the .sim universe). Trades still open are marked at the last bar.
"""
from __future__ import annotations
import csv, glob, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import replay_ex                     # noqa: E402
from pipeline.trendcont_step13_study import load                      # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REASON = {"1": "counter-trend", "2": "event", "3": "structure", "4": "R:R", "5": "correlation", "6": "other", "8": "no response"}


def main():
    print(__doc__)
    rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "study", "demo_journal", "*.csv"))):
        if ".actions" in p or ".delays" in p: continue
        rows += [r for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")) if r.get("decision") == "skipped"]
    print(f"demo skips: {len(rows)}\n")
    print(f"{'symbol':11} {'id':>3} {'signal (broker)':16} {'strat':9} {'dir':4} {'reason':14} {'auto':4} {'R 50%':>7} {'R 25%':>7} status")
    agg = defaultdict(lambda: [0, 0.0, 0.0]); nob = []
    for r in rows:
        bars = load(r["symbol"], "h4"); idx = {b[0][:16]: k for k, b in enumerate(bars)} if bars else {}
        i = idx.get(r["signal_time"][:16])
        if i is None: nob.append(r); continue
        e, s = float(r["entry"]), float(r["sl"])
        tp1 = float(r["tp1"]) if r.get("tp1") else None; tp2 = float(r["tp2"]) if r.get("tp2") else None
        up = r["direction"].upper().startswith("B")
        a = replay_ex(bars, i + 1, up, e, s, tp1, tp2, "doc", 0.5, 0.0); b = replay_ex(bars, i + 1, up, e, s, tp1, tp2, "doc", 0.25, 0.0)
        rc = REASON.get(r.get("skip_reason", ""), r.get("skip_reason", "?"))
        st_ = f"closed {bars[a[1]][0][:16]}" if a[2] else "OPEN, marked"
        print(f"{r['symbol']:11} {r['signal_id']:>3} {r['signal_time'][:16]:16} {r['strategy']:9} {r['direction']:4} {rc:14} "
              f"{r.get('auto') or '0':4} {a[0]:+7.3f} {b[0]:+7.3f} {st_}")
        k = ("auto" if r.get("auto") == "1" else "trader") + " / " + r["strategy"]
        agg[k][0] += 1; agg[k][1] += a[0]; agg[k][2] += b[0]
        agg["ALL trader skips" if r.get("auto") != "1" else "ALL auto skips"][0] += 1
        agg["ALL trader skips" if r.get("auto") != "1" else "ALL auto skips"][1] += a[0]
        agg["ALL trader skips" if r.get("auto") != "1" else "ALL auto skips"][2] += b[0]
    print("\nsummary (raw R; positive = the skip cost money, negative = the skip saved money)")
    for k in sorted(agg):
        n, s50, s25 = agg[k]
        print(f"  {k:28} n={n:3d}  total {s50:+7.2f}R (mean {s50 / n:+.3f})   under 25% bank {s25:+7.2f}R")
    if nob: print(f"\nno bars for {len(nob)}: " + ", ".join(f"{r['symbol']}#{r['signal_id']}" for r in nob))


if __name__ == "__main__": main()
