#!/usr/bin/env python3
"""day2_avg_cut_study.py - trader 2026-10-02: cut trades whose 48-hour AVERAGE is weak. At the close of H4 bar 12 (2 trading days,
~48h after entry) the trade's average R over that time (mean of every M1 bid close from entry to that close, first tranche's 1R)
is computed. If it is NOT above THR (argv[1]; default +0.5R), SELL X% of every open ticket at market (a short at the ask); the rest runs on under the
live stack (the +1.5R pyramid still adds 50% of the ORIGINAL full size). X = 100 / 75 / 50 / 25%. Base = live (no cut).
Live stack: staged 25%/bar-6, bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at
+1.5R. Engine pipeline/ask_side_study (M1; BID = bid-only + DRAG, SCALED = ask-corrected). All four detectors, 7 symbols, pinned
record; 2012-24 and 2025-26; long/short; full-position R; worst DD.
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

MODES = ("BID", "SCALED"); SR = 0.25; BARS = 12
THR = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5          # the 48h-average bar (trader: +0.5, then +0.25 and 0)
VARS = [("base", None)] + [(f"sell {int(f * 100)}%", f) for f in (1.0, 0.75, 0.5, 0.25)]
REPORT = os.path.join(A.STUDY, "day2_avg_cut_report.txt" if THR == 0.5 else f"day2_avg_cut_{THR:g}_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, kc, frac):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n; pyr_pend = True
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if kc is not None and frac is not None and j <= kc < j2: j2 = kc
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
        if frac is not None and kc is not None and j == kc:
            kc = None
            if frac >= 1.0: return val(xc[j]), units
            locked += frac * sum(s * (xc[j] - e) for s, e in legs); legs = [[s * (1 - frac), e] for s, e in legs]
        j += 1
    return val(xc[n - 1]), units


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    ib = u["i0"] + BARS
    kc = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    if kc is not None and not (0 <= kc < F["n"]): kc = None
    weak = kc is not None and float(F["bc"][:kc + 1].mean()) <= THR
    out = {"weak": False}
    for name, frac in VARS:
        r, units = run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], kc if weak else None, frac)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0)
    # "weak" counts only trades still OPEN at the checkpoint (a trade already closed is unaffected by any variant)
    out["weak"] = weak and out["sell 100%"] != out["base"]
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
    lines = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for md in MODES:
        for per in ("dev", "hold"):
            zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
            b = [z[4][md]["base"] for z in zs]
            wz = [z for z in zs if z[4][md]["weak"]]
            lines.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}. Base {st.mean(b):+.4f}R, worst DD {maxdd(b):.1f}R. "
                         f"Still open at day 2 with a 48h average <= {THR:+g}R: {len(wz)} ({sum(1 for z in wz if z[3])} long / {sum(1 for z in wz if not z[3])} short)")
            lines.append(f"  {'rule':10} {'R/trade':>8} {'vs base':>8} {'t':>6} {'DD':>6} | {'on cut trades':>13} {'longs':>8} {'shorts':>8} {'helped/hurt':>11}")
            for name, _ in VARS[1:]:
                v = [z[4][md][name] for z in zs]; d = [p - q for p, q in zip(v, b)]
                mm = lambda a: st.mean(a) if a else float("nan")
                dw = [z[4][md][name] - z[4][md]["base"] for z in wz]
                dl = [z[4][md][name] - z[4][md]["base"] for z in wz if z[3]]; ds = [z[4][md][name] - z[4][md]["base"] for z in wz if not z[3]]
                lines.append(f"  {name:10} {st.mean(v):+8.4f} {st.mean(d):+8.4f} {paired_t(b, v):+6.2f} {maxdd(v):6.1f} | "
                             f"{mm(dw):+13.4f} {mm(dl):+8.4f} {mm(ds):+8.4f} {sum(1 for e in dw if e > 1e-9):5d}/{sum(1 for e in dw if e < -1e-9):<5d}")
            for side, lab in ((False, "SHORTS ONLY"), (True, "LONGS ONLY")):        # the rule applied to one side, the other at base
                lines.append(f"  -- rule on {lab} (account R and DD; the other side at base)")
                for name, _ in VARS[1:]:
                    v = [z[4][md][name] if z[3] == side else z[4][md]["base"] for z in zs]
                    cz = [z for z in wz if z[3] == side]; dc = [z[4][md][name] - z[4][md]["base"] for z in cz]
                    lines.append(f"  {name:10} {st.mean(v):+8.4f} {st.mean(v) - st.mean(b):+8.4f} {paired_t(b, v):+6.2f} {maxdd(v):6.1f} | cut {len(cz):4d}  "
                                 f"helped/hurt {sum(1 for e in dc if e > 1e-9)}/{sum(1 for e in dc if e < -1e-9)}")
    open(REPORT, "w").write("\n".join(lines) + "\n"); print("\n".join(lines))


if __name__ == "__main__": main()
