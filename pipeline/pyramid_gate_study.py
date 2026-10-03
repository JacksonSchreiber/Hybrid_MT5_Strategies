#!/usr/bin/env python3
"""pyramid_gate_study.py - trader 2026-10-03: GATE THE +1.5R PYRAMID on the trade's average. At the moment the pyramid would fire
(after the bank, the first bid touch of +1.5R), the add of 50% of the full size goes in only if the trade's average R since entry
(mean of every M1 bid close up to that bar) is above X (0 / +0.25 / +0.5 / +0.75 / +1R); otherwise this trade gets no pyramid.
"no pyramid" (reference) = never add. The shorts stop raise to +0.25R at the +1.5R touch applies either way (as live).
Base = live (pyramid on every trade that reaches +1.5R after the bank). Live stack otherwise: staged 25%/bar-6, bank per detector at
+1R/TP1 -> stop to entry. Engine pipeline/ask_side_study (M1; SCALED = ask-corrected, BID = bid-only + DRAG). All four detectors,
7 symbols, pinned record; 2012-24 / 2025-26; all / longs / shorts; full-position R; worst DD.
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

MODES = ("SCALED", "BID"); SR = 0.25
VARS = [("base", None), ("avg > 0R", 0.0), ("avg > +0.25R", 0.25), ("avg > +0.5R", 0.5), ("avg > +0.75R", 0.75), ("avg > +1R", 1.0), ("no pyramid", 99.0)]
REPORT = os.path.join(A.STUDY, "pyramid_gate_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, cum, X):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True; gated = False
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, gated
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            pyr_pend = False
            if X is None or cum[j] / (j + 1) > X:
                legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F
            else: gated = True
            if not up: stop = max(stop, be + SR)                            # the live shorts stop raise, independent of the add
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, gated
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        j += 1
    return val(xc[n - 1]), units, gated


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5); cum = np.cumsum(F["bc"])
    out = {}
    for name, X in VARS:
        r, units, g = run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], cum, X)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0); out["g" + name] = g
    return out


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        r = {md: trade(S, x, md) for md in MODES}
        if any(v is None for v in r.values()): continue
        res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def main():
    global ALL
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = sorted([z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs], key=lambda z: z[1])
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for md in MODES:
        for per in ("dev", "hold"):
            zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
            b = [z[4][md]["base"] for z in zs]; mb = st.mean(b)
            L.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R")
            L.append(f"  {'pyramid only if':15} {'skipped':>7} | {'ALL: vs base [t] / DD':>26} | {'LONGS only':>26} | {'SHORTS only':>26} | {'on skipped L / S':>18}")
            for name, X in VARS[1:]:
                cells = []
                for side in (None, True, False):
                    vv = [z[4][md][name] if (side is None or z[3] == side) else z[4][md]["base"] for z in zs]
                    cells.append(f"{st.mean(vv) - mb:+.4f}[{paired_t(b, vv):+.1f}]/{maxdd(vv):5.1f}")
                gz = [z for z in zs if z[4][md]["g" + name]]
                dl = [z[4][md][name] - z[4][md]["base"] for z in gz if z[3]]; ds = [z[4][md][name] - z[4][md]["base"] for z in gz if not z[3]]
                mm = lambda a: st.mean(a) if a else float("nan")
                L.append(f"  {name:15} {len(gz):7d} | {cells[0]:>26} | {cells[1]:>26} | {cells[2]:>26} | {mm(dl):+.3f} / {mm(ds):+.3f}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
