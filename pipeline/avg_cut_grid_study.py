#!/usr/bin/env python3
"""avg_cut_grid_study.py - trader 2026-10-02: the AVERAGE-R cut across checkpoints and bars, alone and combined with the add gate.
CUT(T, B): at the close of H4 bar 6T (T trading days after entry, T = 1..5), if the trade's average R since entry (mean of every
M1 bid close, first tranche's 1R) is NOT above B (0 / +0.25 / +0.5 / +0.75 / +1 / +1.25 / +1.5R), close 100% at market (a short at
the ask). Reported on all trades, shorts only and longs only (the rule on one side, the other at base).
GATE (study 1 of the coach queue): at the bar-6 close, a trial average below 0 holds the staged add until the bid reaches +1R
(added at market then); otherwise the add goes in as live.
GATE + CUT(T, B) on shorts: the gate on every trade, the cut on shorts only.
Live stack otherwise: staged 25%/bar-6, bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R
at +1.5R. Engine pipeline/ask_side_study, SCALED (ask-corrected). All four detectors, 7 symbols, pinned record; 2012-24 / 2025-26;
full-position R; worst DD in signal-time order. Exploratory grid (35 cells x 3 sides) - expect some cells to look good by chance.
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402

MODE = "SCALED"; SR = 0.25; TS = (1, 2, 3, 4, 5); BS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5); GATE_LVL = 1.0
REPORT = os.path.join(A.STUDY, "avg_cut_grid_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, kc, gate):
    """kc: M1 index of the cut (None = no cut); gate: hold the add until +1R (trial average < 0)."""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    kd = None
    if gate and add_pend:
        add_pend = False
        msk = bh[k_add + 1:] >= GATE_LVL
        kd = (k_add + 1 + int(msk.argmax())) if msk.any() else None
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        for ev in (kd, kc):
            if ev is not None and j <= ev < j2: j2 = ev
        j = j2
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if kd is not None and j == kd:
            legs.append([1 - F0_, max(GATE_LVL, bo[j]) + eo[j]]); units += 1 - F0_; kd = None
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if kc is not None and j == kc: return val(xc[j]), units
        j += 1
    return val(xc[n - 1]), units


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); be = e0; bankf = BANKF.get(x["strat"], 0.5); ka = u["k_add"]
    cum = np.cumsum(F["bc"])
    gate = ka is not None and 0 <= ka < F["n"] and cum[ka] / (ka + 1) < 0
    kcs = {}
    for T in TS:
        ib = u["i0"] + 6 * T
        k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
        kcs[T] = (k, cum[k] / (k + 1)) if (k is not None and 0 <= k < F["n"]) else (None, None)
    args = (F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, ka, u["up"])
    out = {"base": run_v(*args, None, False)[0], "gate": run_v(*args, None, gate)[0], "gated": gate}
    for T in TS:
        k, avg = kcs[T]
        for B in BS:
            cut = k if (k is not None and avg <= B) else None
            out[("cut", T, B)] = run_v(*args, cut, False)[0] if cut is not None else out["base"]
            out[("both", T, B)] = run_v(*args, cut, gate)[0] if (cut is not None or gate) else out["base"]
            out[("hit", T, B)] = cut is not None
    return out


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        r = trade(S, x)
        if r: res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def main():
    global ALL
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = sorted([z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs], key=lambda z: z[1])
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for per in ("dev", "hold"):
        zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
        b = [z[4]["base"] for z in zs]; mb, db = st.mean(b), maxdd(b)
        L.append(f"\n{'=' * 110}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {db:.1f}R")
        def grid(title, pick):
            L.append(f"\n  {title}\n  change in R per trade vs base [t] / worst DD   (rows: checkpoint in trading days; columns: the bar)")
            L.append("  " + f"{'':6}" + "".join(f"{'<= ' + format(B, '+g') + 'R':>22}" for B in BS))
            for T in TS:
                cells = []
                for B in BS:
                    v = [pick(z, T, B) for z in zs]
                    cells.append(f"{st.mean(v) - mb:+.4f}[{paired_t(b, v):+.1f}]/{maxdd(v):4.1f}")
                L.append("  " + f"{'day ' + str(T):6}" + "".join(f"{c:>22}" for c in cells))
        grid("CUT on ALL trades", lambda z, T, B: z[4][("cut", T, B)])
        grid("CUT on SHORTS only", lambda z, T, B: z[4][("cut", T, B)] if not z[3] else z[4]["base"])
        grid("CUT on LONGS only", lambda z, T, B: z[4][("cut", T, B)] if z[3] else z[4]["base"])
        g = [z[4]["gate"] for z in zs]
        L.append(f"\n  GATE alone (hold the add until +1R when the trial average < 0): {st.mean(g) - mb:+.4f} [t {paired_t(b, g):+.2f}] / DD {maxdd(g):.1f}"
                 f"   ({sum(1 for z in zs if z[4]['gated'])} trades gated)")
        grid("GATE on all + CUT on SHORTS only", lambda z, T, B: z[4][("both", T, B)] if not z[3] else z[4]["gate"])
        L.append("\n  complement check (SHORTS-only cut): GATE+CUT vs the sum of the two separate gains; positive = they add up / help each other")
        L.append("  " + f"{'':6}" + "".join(f"{'<= ' + format(B, '+g') + 'R':>12}" for B in BS))
        for T in TS:
            cells = []
            for B in BS:
                both = st.mean(z[4][("both", T, B)] if not z[3] else z[4]["gate"] for z in zs) - mb
                cut = st.mean(z[4][("cut", T, B)] if not z[3] else z[4]["base"] for z in zs) - mb
                cells.append(f"{both - (cut + st.mean(g) - mb):+.4f}")
            L.append("  " + f"{'day ' + str(T):6}" + "".join(f"{c:>12}" for c in cells))
        L.append("\n  shorts actually cut per cell (still open at the checkpoint and at/below the bar), by bar left to right:")
        for T in TS:
            L.append(f"    day {T}: " + " / ".join(str(sum(1 for z in zs if not z[3] and z[4][("cut", T, B)] != z[4]["base"])) for B in BS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
