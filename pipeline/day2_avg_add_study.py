#!/usr/bin/env python3
"""day2_avg_add_study.py - trader 2026-10-02: ADD to strong trades at day 2. At the close of H4 bar 12 (~48h after entry), if the
trade is still open and its average R since entry (mean of every M1 bid close, first tranche's 1R) is ABOVE +1R, add X% of the
ruled full size at market (bid close + the mode's entry spread), X = 25 / 50 / 75 / 100%. Optional constraint on the price at that
moment (bid close, first tranche's 1R): any / between 0 and +1R / between +1 and +2R / below +2R. The add shares the position's stop
(entry after the bank; shorts +0.25R after +1.5R) and runs to TP2. Base = live. Live stack: staged 25%/bar-6, bank per detector
at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop raise. Engine pipeline/ask_side_study, SCALED (ask-corrected).
All four detectors, 7 symbols, pinned record; 2012-24 / 2025-26; all / longs only / shorts only; full-position R; worst DD.
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

MODE = "SCALED"; SR = 0.25; BARS = 12; AVG = 1.0; FRACS = (0.25, 0.5, 0.75, 1.0)
CONS = [("any price", lambda p: True), ("price 0..+1R", lambda p: 0.0 <= p < 1.0), ("price +1..+2R", lambda p: 1.0 <= p < 2.0),
        ("price < +2R", lambda p: p < 2.0)]
REPORT = os.path.join(A.STUDY, "day2_avg_add_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, ka2, frac):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if ka2 is not None and j <= ka2 < j2: j2 = ka2
        j = j2
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if ka2 is not None and j == ka2:
            legs.append([frac, bc[j] + eo[j]]); units += frac; ka2 = None
        j += 1
    return val(xc[n - 1]), units


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5)
    args = (F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"])
    out = {"base": run_v(*args, None, 0.0)[0]}
    ib = u["i0"] + BARS
    k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    ok_k = k is not None and 0 <= k < F["n"]
    avg = float(F["bc"][:k + 1].mean()) if ok_k else None; px = float(F["bc"][k]) if ok_k else None
    strong = ok_k and avg > AVG
    for cname, cond in CONS:
        hit = strong and cond(px)
        for fr in FRACS:
            r = run_v(*args, k if hit else None, fr)[0] if hit else out["base"]
            out[(cname, fr)] = r
        out[("hit", cname)] = hit and run_v(*args, k, FRACS[0])[0] != out["base"]   # still open at the checkpoint
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
        L.append(f"\n{'=' * 100}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R")
        for side, lab in ((None, "ALL trades"), (True, "LONGS only"), (False, "SHORTS only")):
            pick = lambda z, key: z[4][key] if (side is None or z[3] == side) else z[4]["base"]
            L.append(f"\n  {lab}: change in R per trade vs base [t] / worst DD   (rows: price constraint; columns: size added)")
            L.append("  " + f"{'':15}{'adds':>6}" + "".join(f"{'+' + str(int(f * 100)) + '%':>22}" for f in FRACS))
            for cname, _ in CONS:
                nh = sum(1 for z in zs if z[4][("hit", cname)] and (side is None or z[3] == side))
                cells = []
                for fr in FRACS:
                    v = [pick(z, (cname, fr)) for z in zs]; cells.append(f"{st.mean(v) - mb:+.4f}[{paired_t(b, v):+.1f}]/{maxdd(v):4.1f}")
                L.append("  " + f"{cname:15}{nh:6d}" + "".join(f"{c:>22}" for c in cells))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
