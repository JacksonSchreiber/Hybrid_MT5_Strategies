#!/usr/bin/env python3
"""time_cut_study.py - trader 2026-10-01: close UNDERWATER trades at a time checkpoint. At the close of H4 bar 6T (T trading
days after entry), if the price (bid close of the last M1 bar) is below THR R, the whole position is closed at market (a short at
the ask); otherwise the live stack runs on. Main cells (trader): T = 2 days, THR = 0 and -0.5R. Context: T 1/3/5, THR -0.25.
Live stack (staged 25%/bar-6, bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at
+1.5R). Engine pipeline/ask_side_study (M1; BID = bid-only + DRAG, SCALED = ask-corrected). All four detectors, 7 symbols;
2012-24 and 2025-26; long/short; full-position R; worst DD in signal-time order. Exploratory (12 cells).
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

MODES = ("BID", "SCALED"); SR = 0.25; TS = (1, 2, 3, 5); THRS = (0.0, -0.25, -0.5)
VARS = [("base", None, None)] + [(f"{t}d<{th:+g}", t, th) for t in TS for th in THRS]
REPORT = os.path.join(A.STUDY, "time_cut_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, kc, thr):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n; pyr_pend = True; cut = False
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if kc is not None and j <= kc < j2: j2 = kc
        j = j2
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, cut
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, cut
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if kc is not None and j == kc:
            if bc[j] < thr: return val(xc[j]), units, True
            kc = None
        j += 1
    return val(xc[n - 1]), units, cut


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    kcs = {}
    for t in TS:
        ib = u["i0"] + 6 * t
        kcs[t] = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
        if kcs[t] is not None and not (0 <= kcs[t] < F["n"]): kcs[t] = None
    out = {}
    for name, t, th in VARS:
        r, units, cut = run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], kcs.get(t), th)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0); out["cut" + name] = cut
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
            lines.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}. Base {st.mean(b):+.4f}R, worst DD {maxdd(b):.1f}R  "
                         f"(longs {st.mean(z[4][md]['base'] for z in zs if z[3]):+.4f}, shorts {st.mean(z[4][md]['base'] for z in zs if not z[3]):+.4f})")
            lines.append(f"  {'rule':10} {'cut':>4} {'R/trade':>8} {'vs base':>8} {'t':>6} {'DD':>6} | {'on cut trades':>13} {'longs':>8} {'shorts':>8} {'helped/hurt':>11}")
            for name, *_ in VARS[1:]:
                v = [z[4][md][name] for z in zs]; d = [p - q for p, q in zip(v, b)]
                cz = [z for z in zs if z[4][md]["cut" + name]]
                mm = lambda a: st.mean(a) if a else float("nan")
                dc = [z[4][md][name] - z[4][md]["base"] for z in cz]
                dl = [z[4][md][name] - z[4][md]["base"] for z in cz if z[3]]; ds = [z[4][md][name] - z[4][md]["base"] for z in cz if not z[3]]
                lines.append(f"  {name:10} {len(cz):4d} {st.mean(v):+8.4f} {st.mean(d):+8.4f} {paired_t(b, v):+6.2f} {maxdd(v):6.1f} | "
                             f"{mm(dc):+13.4f} {mm(dl):+8.4f} {mm(ds):+8.4f} {sum(1 for e in dc if e > 1e-9):5d}/{sum(1 for e in dc if e < -1e-9):<5d}")
    open(REPORT, "w").write("\n".join(lines) + "\n"); print("\n".join(lines))


if __name__ == "__main__": main()
