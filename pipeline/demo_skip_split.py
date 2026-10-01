#!/usr/bin/env python3
"""demo_skip_split.py - coach 2026-10-01 A6: the 14-day demo skips' blind outcomes split by skip code and by the advisor's
last verdict on the signal. Outcomes as demo_skip_shadow.py (50% doctrine, raw R, open trades marked at the last bar).
Advisor verdicts: data/study/verdicts.live.log (box advisor log), last verdict per (symbol, signal id) dated before the
demo was archived (2026-09-29T21:00Z); the log line format changed over the period, so the symbol is the field before '#id'
and the verdict is the first word of the field after the strategy/direction field."""
import csv, glob, os, re, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import replay_ex                     # noqa: E402
from pipeline.trendcont_step13_study import load                      # noqa: E402
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REASON = {"1": "1 counter-trend", "2": "2 event", "3": "3 structure", "4": "4 R:R", "5": "5 correlation", "6": "6 other", "8": "8 no response"}

verd = {}
for ln in open(os.path.join(ROOT, "data", "study", "verdicts.live.log"), encoding="utf-8-sig", errors="replace"):
    f = [x.strip() for x in ln.split("|")]
    if len(f) < 5 or f[0] >= "2026-09-29T21": continue
    k = next((i for i, x in enumerate(f) if re.fullmatch(r"#\d+", x)), None)
    if k is None or k + 2 >= len(f): continue
    w = f[k + 2].split()[0] if f[k + 2] else ""
    if w in ("TAKE", "SKIP", "ADJUST", "WAIT"): verd[(f[k - 1], f[k][1:])] = w + (" (default)" if "default" in f[k + 2] else "")

rows = []
for p in sorted(glob.glob(os.path.join(ROOT, "data", "study", "demo_journal", "*.csv"))):
    if ".actions" in p or ".delays" in p: continue
    rows += [r for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")) if r.get("decision") == "skipped"]
by_code, by_verd, both = defaultdict(list), defaultdict(list), defaultdict(list)
for r in rows:
    bars = load(r["symbol"], "h4"); idx = {b[0][:16]: k for k, b in enumerate(bars)} if bars else {}
    i = idx.get(r["signal_time"][:16])
    if i is None: continue
    e, s = float(r["entry"]), float(r["sl"]); tp1 = float(r["tp1"]) if r.get("tp1") else None; tp2 = float(r["tp2"]) if r.get("tp2") else None
    R = replay_ex(bars, i + 1, r["direction"].upper().startswith("B"), e, s, tp1, tp2, "doc", 0.5, 0.0)[0]
    code = ("auto " if r.get("auto") == "1" else "") + REASON.get(r.get("skip_reason", ""), r.get("skip_reason", "?"))
    v = verd.get((r["symbol"], r["signal_id"]), "no verdict")
    by_code[code].append(R); by_verd[v].append(R); both[(v, code)].append(R)
fmt = lambda xs: f"n={len(xs):2d}  total {sum(xs):+6.2f}R  mean {st.mean(xs):+.3f}  winners(>0) {sum(1 for x in xs if x > 0)}"
print("DEMO SKIPS BY SKIP CODE (positive = the skip cost money)")
for k in sorted(by_code): print(f"  {k:22} {fmt(by_code[k])}")
print("\nDEMO SKIPS BY THE ADVISOR'S LAST VERDICT")
for k in sorted(by_verd): print(f"  {k:22} {fmt(by_verd[k])}")
print("\nCROSS (advisor verdict x skip code)")
for k in sorted(both): print(f"  {k[0]:16} {k[1]:20} {fmt(both[k])}")
