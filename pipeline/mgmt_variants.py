#!/usr/bin/env python3
"""mgmt_variants.py - trader 2026-10-03: every combination of the post-signal management layers, per trade, for the webapp's
"Management" selector. Same engine and rule definitions as combo3_study (run_v generalised: each layer is a switch).
Layers (bits):
  S  staged entry (25% at the signal, 75% at the bar-6 close)      A  hold a weak trial's add until +1R (only with S)
  K  bank at +1R / TP1 (detector share) -> stop to entry           P  +50% pyramid at +1.5R
  R  shorts' stop to +0.25R at +1.5R                                B  shorts closed at the day-2 close if their average <= +0.5R
  I  longs +50% at the day-2 close when day 2 averages better than day 1
Raw signal (nothing on): 100% at the signal, the signal's stop, out at TP2 or the stop (or the end of the replay window).
With K off, the +1.5R moment (P / R) is armed from the start; with K on it waits for the bank, exactly as live.
96 variants per trade: index = sa*32 + K + 2P + 4R + 8B + 16I, sa = 0 (S off) / 1 (S on, A off) / 2 (S and A on).
Index 39 = the live stack (= combo3 'base'); index 95 = everything (= combo3 'A+B+I').
"""
from __future__ import annotations
import numpy as np
from pipeline import ask_side_study as A
from pipeline import combo3_study as K
from pipeline.inverse_study import BANKF

LIVE, ALL = 39, 95


def idx(sa, k, p, r, b, i): return sa * 32 + k + 2 * p + 4 * r + 8 * b + 16 * i


def run_g(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, gate, kc, ka2, bank_on, pyr_on, sr_on):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; trig = pyr_on or sr_on
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n; kd = None
    if gate and add_pend:
        add_pend = False
        msk = bh[k_add + 1:] >= K.GATE_LVL
        kd = (k_add + 1 + int(msk.argmax())) if msk.any() else None
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        armed = banked or not bank_on
        j2 = A.nxt(F, j, stop, bank_R if (bank_on and not banked) else None, tp2_R, armed and trig, None, k_add if add_pend else None)
        for ev in (kd, kc, ka2):
            if ev is not None and j <= ev < j2: j2 = ev
        j = j2
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units
        if bank_on and not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if (banked or not bank_on) and trig and bh[j] >= A.PYR_R:
            if pyr_on: legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F
            if sr_on and not up: stop = max(stop, be + K.SR)
            trig = False
        if kd is not None and j == kd:
            legs.append([1 - F0_, max(K.GATE_LVL, bo[j]) + eo[j]]); units += 1 - F0_; kd = None
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if kc is not None and j == kc: return val(xc[j]), units
        if ka2 is not None and j == ka2:
            legs.append([K.ADD_F, bc[j] + eo[j]]); units += K.ADD_F; ka2 = None
        j += 1
    return val(xc[n - 1]), units


def variants(S, x, mode="SCALED"):
    """-> list of 96 full-position R (costs in), or None (no M1 coverage)."""
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    cum = np.cumsum(F["bc"]); n = F["n"]; ka = u["k_add"]
    gate = ka is not None and 0 <= ka < n and cum[ka] / (ka + 1) < 0
    ib = u["i0"] + K.CHK
    k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    avg2 = cum[k] / (k + 1) if (k is not None and 0 <= k < n) else None
    cut = k if (avg2 is not None and not u["up"] and avg2 <= K.CUT_BAR) else None
    add_i = None
    if avg2 is not None and u["up"] and ka is not None and 0 <= ka < k:
        if (cum[k] - cum[ka]) / (k - ka) > cum[ka] / (ka + 1): add_i = k
    out = [0.0] * 96
    for sa in (0, 1, 2):
        F0_ = 1.0 if sa == 0 else A.F0
        for kk in (0, 1):
            for p in (0, 1):
                for r in (0, 1):
                    for b in (0, 1):
                        for i in (0, 1):
                            v, units = run_g(F, e0, be, u["bank_R"], u["tp2_R"], bankf, F0_, ka, u["up"], gate and sa == 2,
                                             cut if b else None, add_i if i else None, bool(kk), bool(p), bool(r))
                            out[idx(sa, kk, p, r, b, i)] = v - (x["cost"] * units if m["drag"] else 0.0)
    return out
