#!/usr/bin/env python3
"""d1_extension_study.py - coach 2026-09-24 item 3: does D1 extension separate MECHANICAL outcomes, or does it only
condition the trader's selection?

Study 3 found D1 distance from its own EMA20 is the one feature that collapses his take-minus-skip gap. That is a
statement about HIS picks. This runs the same feature through the blind record - every approved TrendCont row of the
graded set (n=2,346), taken blind - to see whether the feature carries an edge on its own.

Buckets: <0.5 / 0.5-1.5 / >1.5 ATR, signed in the trade's direction (negative = D1 extended AGAINST the trade).
Reported as everywhere else: mean R +- SE, +1R bank hit-rate, n, and both halves.
"""
from __future__ import annotations
import csv, glob, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step_study import REC, atr14, table                 # noqa: E402
from pipeline.trendcont_step13_study import load, ema                       # noqa: E402

ORDER = ["against (<0)", "0-0.5 ATR", "0.5-1.5 ATR", ">1.5 ATR"]


def bucket(x: float) -> str:
    return "against (<0)" if x < 0 else ("0-0.5 ATR" if x < 0.5 else ("0.5-1.5 ATR" if x <= 1.5 else ">1.5 ATR"))


def main():
    rows, excl = [], defaultdict(int)
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]
        d1 = load(sym, "d1")
        if not d1: excl[f"{sym}: no D1"] += 1; continue
        closes = [b[4] for b in d1]; e20 = ema(closes, 20)
        days = [b[0][:10] for b in d1]
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            try: rm, mfe = float(r["r_multiple"]), float(r.get("mfe_r") or 0)
            except (TypeError, ValueError): excl[f"{sym}: unusable row"] += 1; continue
            day = r["signal_time"][:10]
            jd = max((k for k, d in enumerate(days) if d < day), default=-1)   # 2026-09-30: previous CLOSED day (was <= : look-ahead)
            if jd < 40: excl[f"{sym}: no D1 history at the signal"] += 1; continue
            a = atr14([[0] + b[1:] for b in d1], jd)
            if a <= 0: excl[f"{sym}: D1 ATR zero"] += 1; continue
            up = r["direction"].upper().startswith("B")
            ext = ((closes[jd] - e20[jd]) / a) * (1 if up else -1)
            rows.append({"r": rm, "bank": mfe >= 1.0, "t": r["signal_time"], "b": bucket(ext), "ext": ext})
    print(f"approved TrendCont rows classified: {len(rows)}")
    for k, v in sorted(excl.items()): print(f"  excluded {v:>5}  ({k})")
    table(rows, lambda r: r["b"], ORDER, "D1 EXTENSION at the signal (distance from the D1 EMA20, signed by the trade)")
    ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
    table(rows, lambda r: r["b"], ORDER, f"D1 EXTENSION - FIRST half (to {mid[:10]})", lambda r: r["t"] <= mid)
    table(rows, lambda r: r["b"], ORDER, "D1 EXTENSION - SECOND half", lambda r: r["t"] > mid)
    hi = [r for r in rows if r["ext"] > 1.5]; lo = [r for r in rows if r["ext"] <= 1.5]
    if hi and lo:
        import statistics as st
        d = st.mean(r["r"] for r in hi) - st.mean(r["r"] for r in lo)
        se = (st.stdev([r["r"] for r in hi]) ** 2 / len(hi) + st.stdev([r["r"] for r in lo]) ** 2 / len(lo)) ** 0.5
        print(f"\n>1.5 ATR minus everything else: {d:+.3f}R +- {se:.3f} (n {len(hi)} vs {len(lo)})")


if __name__ == "__main__": main()
