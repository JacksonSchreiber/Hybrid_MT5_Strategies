#!/usr/bin/env python3
"""short_bank_avg_study.py - trader 2026-10-03, idea 3: a BIGGER BANK for WEAK SHORTS. When a SHORT reaches the bank (+1R or TP1 if
nearer), if its average R since entry (mean of every M1 bid close up to that bar) is below X (0 / +0.25 / +0.5R; "all" = every
short), it banks F (50% / 75%) instead of its detector's live share (TrendCont / DeepFib 25%, SweepMSS / EMArevQ 50%; a bigger
share only - never smaller). Longs untouched. Base = live (staged 25%/bar-6, stop to entry at the bank, pyramid 50% at +1.5R,
shorts stop to +0.25R at +1.5R). Engine pipeline/ask_side_study (SCALED; BID alongside). All four detectors, 7 symbols, pinned
record; 2012-24 / 2025-26; full-position R; worst DD.
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
VARS = [("base", None, None)] + [(f"avg<{x if x != 9 else 'any'} bank {int(f * 100)}%", x, f) for x in (0.0, 0.25, 0.5, 9) for f in (0.5, 0.75)]
REPORT = os.path.join(A.STUDY, "short_bank_avg_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, cum, X, Fb):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True; hit = False
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, hit
        if not banked and xh[j] >= bank_R:
            bf = bankf
            if X is not None and not up and cum[j] / (j + 1) < X and Fb > bankf: bf = Fb; hit = True
            banked = True; locked += bf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, hit
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        j += 1
    return val(xc[n - 1]), units, hit


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5); cum = np.cumsum(F["bc"])
    out = {}
    for name, X, Fb in VARS:
        r, units, h = run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], cum, X, Fb)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0); out["h" + name] = h
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
            L.append(f"  {'shorts: bigger bank if':24} {'shorts':>6} {'vs base':>9} {'t':>6} {'worst DD':>8} {'per short hit':>13}")
            for name, *_ in VARS[1:]:
                v = [z[4][md][name] for z in zs]; hz = [z[4][md][name] - z[4][md]["base"] for z in zs if z[4][md]["h" + name]]
                L.append(f"  {name:24} {len(hz):6d} {st.mean(v) - mb:+9.4f} {paired_t(b, v):+6.2f} {maxdd(v):8.1f} {(st.mean(hz) if hz else float('nan')):+13.3f}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
