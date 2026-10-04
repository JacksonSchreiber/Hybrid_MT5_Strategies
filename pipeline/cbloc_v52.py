#!/usr/bin/env python3
"""cbloc_v52.py - the FROZEN commodity-bloc draft v5.2 engine (trader 2026-10-04), used by the holdout look
(pipeline/cbloc_holdout_look.py) and its in-sample check. Identical per-trade logic to cbloc_v52_filters.kern; also returns the units
traded (1, or 2 when doubled) for the spread-sensitivity lines.
v5.2, per M1 minute in this order:
  stop (signal stop -1R; checked first) -> stop to entry at +0.5R (longs) / +0.3R (shorts) -> stop +1R at +1.5R (bid high) ->
  +100% at +2R (bid high) before the bar-6 close -> TP2 -> 1h after entry: sell if the average R since entry < -0.1 ->
  3.5h after entry: sell if the average < -0.15 -> bar-4 close (16h): sell if the average < +1.5.
100% at the signal; average R = mean of the M1 bid closes since entry in the trade's R. SCALED frame (ask_side_study.frame).
"""
from __future__ import annotations
import numpy as np
from numba import njit
from pipeline import ask_side_study as A

BE_LONG, BE_SHORT = 0.5, 0.3


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, has_tp2, tp2, k6, k16, kE, k35, be):
    """-> (R, exit minute, units traded)"""
    s1 = 0.0; e1 = 0.0; stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0
    lv = be
    if has_tp2 and tp2 < lv: lv = tp2
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            return (r - e0) + s1 * (r - e1), j, 1.0 + s1
        if (not hit) and xh[j] >= lv:
            hit = True
            if e0 > stop: stop = e0
        if (not moved) and bh[j] >= 1.5:
            moved = True
            if e0 + 1.0 > stop: stop = e0 + 1.0
        if (not dd) and k6 >= 0 and j < k6 and bh[j] >= 2.0:
            b = bo[j] if bo[j] > 2.0 else 2.0
            s1 = 1.0; e1 = b + eo[j]; dd = True
        if has_tp2 and xh[j] >= tp2: return (tp2 - e0) + s1 * (tp2 - e1), j, 1.0 + s1
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1: return (xc[j] - e0) + s1 * (xc[j] - e1), j, 1.0 + s1
        if k35 >= 0 and j == k35 and cum / (j + 1) < -0.15: return (xc[j] - e0) + s1 * (xc[j] - e1), j, 1.0 + s1
        if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5: return (xc[j] - e0) + s1 * (xc[j] - e1), j, 1.0 + s1
    return (xc[n - 1] - e0) + s1 * (xc[n - 1] - e1), n - 1, 1.0 + s1


def trade(S, x, u, F):
    """v5.2 on one trade from ask_side_study.setup (u) and frame (F). -> (R, units)"""
    h4, i0, k0 = x["bars"], u["i0"], u["k0"]
    k16 = (int(np.searchsorted(S.t, A.tmin(h4[i0 + 4][0]))) - 1 - k0) if i0 + 4 < len(h4) else -1
    kh = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
    k6 = u["k_add"] if u["k_add"] is not None else -1
    tp2 = u["tp2_R"]; ht = tp2 is not None
    r, _, units = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], F["n"], float(F["eo"][0]), ht,
                       tp2 if ht else 0.0, k6, k16, kh(1), kh(3.5), BE_LONG if u["up"] else BE_SHORT)
    return float(r), float(units)
