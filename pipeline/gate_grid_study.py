#!/usr/bin/env python3
"""gate_grid_study.py - trader 2026-10-02: the trial-average ADD GATE (coach queue 1) across decision times and bars.
GATE(H, B): at the close of H4 bar H/4 (H = 12 / 24 / 36 / 48 / 72 hours after entry), the trade's average R since entry (mean of
every M1 bid close, first tranche's 1R) is compared with B (-0.25 / 0 / +0.25 / +0.5R). At or above B: the 75% add goes in at
the bar-6 close as live (H <= 24) - or at the decision close (H > 24: the add cannot precede the decision, so EVERY trade's add
waits until then). Below B: the add is held and goes in at market the first time the bid reaches +1R after the decision.
DELAY(H) (H > 24, reference): the add at the H close for every trade, no gate - separates the cost/benefit of the later add
from the gate itself. GATE(24h, 0) is coach-queue study 1. Base = live (the add at the bar-6 close).
Live stack otherwise: bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at +1.5R.
Engine pipeline/ask_side_study, SCALED (ask-corrected). All four detectors, 7 symbols, pinned record; 2012-24 / 2025-26; all /
longs only / shorts only (the rule on one side, the other at base); full-position R; worst DD. Exploratory grid.
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

MODE = "SCALED"; SR = 0.25; HS = (12, 24, 36, 48, 72); BS = (-0.25, 0.0, 0.25, 0.5); REL = 1.0
REPORT = os.path.join(A.STUDY, "gate_grid_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, k_dec=None, gated=False):
    """the add at k_add (None = no add); if gated, held from the decision k_dec until the bid reaches +1R."""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True
    add_pend = k_add is not None and k_add < n
    kd = None
    if gated and k_dec is not None and k_dec < n:
        add_pend = False
        msk = bh[k_dec + 1:] >= REL
        kd = (k_dec + 1 + int(msk.argmax())) if msk.any() else None
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if kd is not None and j <= kd < j2: j2 = kd
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
            legs.append([1 - F0_, max(REL, bo[j]) + eo[j]]); units += 1 - F0_; kd = None
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        j += 1
    return val(xc[n - 1]), units


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u or u["k_add"] is None: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5); cum = np.cumsum(F["bc"]); n = F["n"]
    args = (F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0)
    out = {"base": run_v(*args, u["k_add"], u["up"])[0]}
    for H in HS:
        ib = u["i0"] + H // 4
        k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
        if k is None or not (0 <= k < n):
            for B in BS: out[(H, B)] = out["base"]
            out[("delay", H)] = out["base"]; continue
        avg = cum[k] / (k + 1)
        k_add = u["k_add"] if H <= 24 else k
        if H > 24: out[("delay", H)] = run_v(*args, k_add, u["up"])[0]
        for B in BS:
            out[(H, B)] = run_v(*args, k_add, u["up"], k, avg < B)[0]
            out[("g", H, B)] = avg < B
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
        b = [z[4]["base"] for z in zs]; mb = st.mean(b)
        L.append(f"\n{'=' * 104}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R")
        for side, lab in ((None, "ALL trades"), (True, "LONGS only"), (False, "SHORTS only")):
            pick = lambda z, key: z[4][key] if (side is None or z[3] == side) else z[4]["base"]
            L.append(f"\n  GATE on {lab}: change in R per trade vs base [t] / worst DD   (rows: decision time; columns: average below the bar -> hold until +1R)")
            L.append("  " + f"{'':14}" + "".join(f"{'avg < ' + format(B, '+g') + 'R':>22}" for B in BS) + f"{'add delayed, no gate':>24}")
            for H in HS:
                cells = []
                for B in BS:
                    v = [pick(z, (H, B)) for z in zs]; cells.append(f"{st.mean(v) - mb:+.4f}[{paired_t(b, v):+.1f}]/{maxdd(v):4.1f}")
                if H > 24:
                    v = [pick(z, ("delay", H)) for z in zs]; ref = f"{st.mean(v) - mb:+.4f}[{paired_t(b, v):+.1f}]/{maxdd(v):4.1f}"
                else: ref = "-"
                L.append("  " + f"{str(H) + 'h (bar ' + str(H // 4) + ')':14}" + "".join(f"{c:>22}" for c in cells) + f"{ref:>24}")
        L.append("\n  trades gated (average below the bar at the decision; includes trades already closed by then):")
        for H in HS: L.append(f"    {H}h: " + " / ".join(str(sum(1 for z in zs if z[4].get(('g', H, B)))) for B in BS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
