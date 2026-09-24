#!/usr/bin/env python3
"""trendcont_step4_study.py - coach 2026-09-24 study: step 4 (trigger candle), the last untested structural step.

Same population, bars and reporting as items 6 and study 1: approved TrendCont rows of the graded record (n=2,346),
outcome = journal r_multiple, +1R bank = mfe_r >= 1.0, mean R +- SE per cell, both halves.

Measured on the SIGNAL bar itself (the trigger candle):
  body / range          - how decisive the close was inside the bar
  close position        - which third of the bar's range the close landed in, in the trade direction
  body / ATR(14)        - the candle's size against the instrument's own volatility
  direction vs trend    - did the trigger close in the trade's direction at all (a bull candle on a BUY)
"""
from __future__ import annotations
import csv, glob, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step_study import REC, atr14, table                 # noqa: E402
from pipeline.trendcont_step13_study import load                            # noqa: E402


def trigger(bar: list, a: float, up: bool) -> dict:
    o, h, l, c = bar[1], bar[2], bar[3], bar[4]
    rng = h - l
    body = abs(c - o)
    br = body / rng if rng > 0 else 0.0
    pos = (c - l) / rng if rng > 0 else 0.5                    # 0 = closed on the low, 1 = on the high
    if not up: pos = 1.0 - pos                                 # express it in the trade's direction
    return {
        "body_range": "<0.3" if br < 0.3 else ("0.3-0.6" if br < 0.6 else ">=0.6"),
        "close_pos": "bottom third" if pos < 1 / 3 else ("middle third" if pos < 2 / 3 else "top third"),
        "body_atr": "<0.5 ATR" if body < 0.5 * a else ("0.5-1 ATR" if body < a else ">=1 ATR"),
        "with_dir": "trigger closed with the trade" if ((c > o) == up and c != o) else "against / doji",
    }


def main():
    rows, excl = [], defaultdict(int)
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]
        h4 = load(sym, "h4")
        if not h4: excl[f"{sym}: no bars"] += 1; continue
        idx = {b[0][:16]: k for k, b in enumerate(h4)}
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            i = idx.get(r["signal_time"][:16])
            if i is None or i < 60: excl[f"{sym}: signal bar outside the H4 file"] += 1; continue
            try: rm, mfe = float(r["r_multiple"]), float(r.get("mfe_r") or 0)
            except (TypeError, ValueError): excl[f"{sym}: unusable row"] += 1; continue
            a = atr14([[0] + b[1:] for b in h4], i)
            if a <= 0: excl[f"{sym}: no ATR"] += 1; continue
            rows.append({"r": rm, "bank": mfe >= 1.0, "t": r["signal_time"],
                         **trigger(h4[i], a, r["direction"].upper().startswith("B"))})
    print(f"approved TrendCont rows classified: {len(rows)}")
    for k, v in sorted(excl.items()): print(f"  excluded {v:>5}  ({k})")
    specs = [("body_range", ["<0.3", "0.3-0.6", ">=0.6"], "STEP 4 - trigger body / range"),
             ("close_pos", ["bottom third", "middle third", "top third"], "STEP 4 - close position in the bar, in the trade's direction"),
             ("body_atr", ["<0.5 ATR", "0.5-1 ATR", ">=1 ATR"], "STEP 4 - trigger body / ATR(14)"),
             ("with_dir", ["trigger closed with the trade", "against / doji"], "STEP 4 - trigger direction vs the trade")]
    for k, order, title in specs: table(rows, lambda r, k=k: r[k], order, title)
    ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
    for name, half in ((f"FIRST half (to {mid[:10]})", lambda r: r["t"] <= mid), ("SECOND half", lambda r: r["t"] > mid)):
        for k, order, title in specs[:3]: table(rows, lambda r, k=k: r[k], order, f"{title} - {name}", half)


if __name__ == "__main__": main()
