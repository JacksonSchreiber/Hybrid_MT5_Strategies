#!/usr/bin/env python3
"""raise_sl_atr_study.py - trader 2026-10-01: does ATR predict what the timed stop raise (3 H4 bars after the pyramid, stop to
+0.25R) costs or saves? Exploratory - no rule is selected here.
Features at the SIGNAL (H4 bar-index rule: ATR14 = simple (non-Wilder) mean of true range over the 14 CLOSED H4 bars ending with the
signal bar i0-1; no later bar is used):
   stop/ATR  - R (|entry - stop|) / ATR14: a small stop relative to normal volatility puts +0.25R inside ordinary noise;
   ATR regime - ATR14 / mean ATR14 of the previous 100 bars (hot vs quiet market).
Per trade: rule R - today's R (whole position, from time_raise_sl_study; original-only, from time_raise_sl_legs_study).
Quintile edges are fixed on 2012-24 and applied unchanged to 2025-26. M5, all 7 symbols.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows                       # noqa: E402
from pipeline.m5_study import load_m5                                 # noqa: E402
from pipeline import time_raise_sl_study as W                         # noqa: E402
from pipeline import time_raise_sl_legs_study as S                    # noqa: E402


def atr_series(h4, j, n=14):
    tr = lambda k: max(h4[k][2] - h4[k][3], abs(h4[k][2] - h4[k - 1][4]), abs(h4[k][3] - h4[k - 1][4]))
    return st.mean(tr(k) for k in range(j - n + 1, j + 1))


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"]); cache = {}; out = []
    for x in rows:
        h4 = x["bars"]; i0 = x["i0"]; j = i0 - 1
        if i0 + W.NADD >= len(h4) or j < 120: continue
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        ka = bisect.bisect_left(M["t"], h4[i0 + W.NADD][0][:16]) - 1; off = h4[i0 - 1][4] - M["c"][k0 - 1]
        a = (x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None, (x["tp2"] - off) if x["tp2"] else None, x["cost"])
        atr = atr_series(h4, j); reg = atr / st.mean(atr_series(h4, k) for k in range(j - 100, j))
        b = W.run(M, k0, ka, *a)
        out.append({"x": x, "rr": abs(x["e"] - x["s"]) / atr, "reg": reg, "base": b,
                    "whole": W.run(M, k0, ka, *a, 3, 0.25) - b, "orig": S.run(M, k0, ka, *a, 3, 0.25, "ORIG") - b})
    print(__doc__)
    dev = [z for z in out if z["x"]["t"][:4] < "2025"]; hold = [z for z in out if z["x"]["t"][:4] >= "2025"]
    for feat, name in (("rr", "stop / ATR14 at the signal"), ("reg", "ATR regime (ATR14 / its 100-bar mean)")):
        qs = sorted(z[feat] for z in dev); edges = [qs[int(len(qs) * q / 5)] for q in range(1, 5)]
        bucket = lambda v: sum(v >= e for e in edges)
        print(f"\n{name}   (quintile edges from 2012-24: {', '.join(f'{e:.2f}' for e in edges)})")
        print(f"  {'bucket':>16} | {'2012-24 n':>9} {'today R':>8} {'whole':>8} {'orig':>8} {'hit':>4} {'cut':>4} {'L whole':>8} {'S whole':>8} | "
              f"{'2025-26 n':>9} {'whole':>8} {'orig':>8} {'hit':>4} {'cut':>4}")
        for q in range(5):
            lo = f"{edges[q - 1]:.2f}" if q else "min"; hi = f"{edges[q]:.2f}" if q < 4 else "max"
            d = [z for z in dev if bucket(z[feat]) == q]; h = [z for z in hold if bucket(z[feat]) == q]
            hit = lambda zs: sum(1 for z in zs if z["whole"] > 1e-9); cut = lambda zs: sum(1 for z in zs if z["whole"] < -1e-9)
            mm = lambda zs, k: st.mean(z[k] for z in zs) if zs else float("nan")
            dl = [z for z in d if z["x"]["up"]]; ds = [z for z in d if not z["x"]["up"]]
            print(f"  {lo + '-' + hi:>16} | {len(d):9d} {mm(d, 'base'):+8.3f} {mm(d, 'whole'):+8.3f} {mm(d, 'orig'):+8.3f} {hit(d):4d} {cut(d):4d} "
                  f"{mm(dl, 'whole'):+8.3f} {mm(ds, 'whole'):+8.3f} | {len(h):9d} {mm(h, 'whole'):+8.3f} {mm(h, 'orig'):+8.3f} {hit(h):4d} {cut(h):4d}")
    print("\n  whole / orig = mean R change per trade from the raise (whole position / original only); hit = trades it helped,"
          " cut = trades it hurt (mostly ~-5R TP2 misses). L/S = longs/shorts, 2012-24.")


if __name__ == "__main__": main()
