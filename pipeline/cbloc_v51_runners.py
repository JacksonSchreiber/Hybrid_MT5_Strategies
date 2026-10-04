#!/usr/bin/env python3
"""cbloc_v51_runners.py - trader 2026-10-04: draft v5.1 (commodity bloc: AUDUSD, NZDUSD, USDCAD) - let the big winners run further.
  RUNNER (every trade that touches TP2; v5.1 = sell all at TP2):
     tp3 m,g   at the TP2 touch the stop goes to TP2 - g R; out at TP3 = m x TP2 (R from the signal price), the stop, or the window end
     trail D   at the TP2 touch the stop goes to TP2 - D and then trails the bid high - D
     half      50% off at TP2, the rest: stop TP2 - 1R, trailing the bid high - 1R
     once TP2 is touched no time check applies (1h / 3.5h / 16h).
  DOUBLE     the +2R add (before the bar-6 close) at 100% (v5.1) / 150% / 200% of the first entry
  ADD2       at +3R (bid high, any time, only if doubled): none / stop to +2R only / +50% and stop +2R / +100% and stop +2R
Everything else = v5.1: 100% at the signal; 1h check (avg < -0.1 -> sell); 3.5h check (avg < -0.15 -> sell); stop to entry at
+0.5R; 16h cut (bar-4 close: avg < +1.5 -> sell); stop +1R at +1.5R. Kernel = numba; the v5.1 cell reproduces the Python draft
exactly (checked in the run). SCALED (ask-corrected, live spreads); swap / financing is not modelled (matters for multi-day runners).
Holdout symbols untouched.
    python3 pipeline/cbloc_v51_runners.py     -> data/study/cbloc_v51_runners_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
from pipeline.cbloc_v51_trend_check import v51_py         # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_v51_runners_report.txt")
RUN = [("tp2", 0, 0.0, 0.0)] + [("tp3", 1, m, g) for m in (1.5, 2.0, 3.0) for g in (0.5, 1.0)] + [("trail", 2, 0.0, d) for d in (0.5, 1.0, 1.5)] + [("half", 3, 0.0, 1.0)]
DMS = [1.0, 1.5, 2.0]
ADD = [("none", 0.0, 0), ("stop +2R at +3R", 0.0, 1), ("+50% at +3R", 0.5, 1), ("+100% at +3R", 1.0, 1)]


def variants():
    return [(r, dm, a) for r in range(len(RUN)) for dm in DMS for a in range(len(ADD))]


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, has_tp2, tp2, k6, kE, k35, k16, rmode, m3, gd, dm, a2, l2):
    """-> (R, exit minute, ran past TP2)"""
    s1 = 0.0; e1 = 0.0; s2 = 0.0; e2 = 0.0; mult = 1.0; locked = 0.0
    stop = -1.0; hit = False; moved = False; dd = False; done2 = False; ran = False; cum = 0.0; peak = -1e9
    lv = 0.5
    if has_tp2 and tp2 < lv: lv = tp2
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            return locked + mult * ((r - e0) + s1 * (r - e1) + s2 * (r - e2)), j, ran
        if bh[j] > peak: peak = bh[j]
        if (not hit) and xh[j] >= lv:
            hit = True
            if e0 > stop: stop = e0
        if (not moved) and bh[j] >= 1.5:
            moved = True
            if e0 + 1.0 > stop: stop = e0 + 1.0
        if (not dd) and k6 >= 0 and j < k6 and bh[j] >= 2.0:
            b = bo[j] if bo[j] > 2.0 else 2.0
            s1 = dm; e1 = b + eo[j]; dd = True
        if dd and (not done2) and l2 == 1 and bh[j] >= 3.0:
            done2 = True
            if a2 > 0.0:
                b = bo[j] if bo[j] > 3.0 else 3.0
                s2 = a2; e2 = b + eo[j]
            if e0 + 2.0 > stop: stop = e0 + 2.0
        if has_tp2:
            if rmode == 0:
                if xh[j] >= tp2:
                    return locked + mult * ((tp2 - e0) + s1 * (tp2 - e1) + s2 * (tp2 - e2)), j, ran
            else:
                if (not ran) and xh[j] >= tp2:
                    ran = True
                    if rmode == 3:
                        locked += 0.5 * mult * ((tp2 - e0) + s1 * (tp2 - e1) + s2 * (tp2 - e2)); mult = 0.5 * mult
                    if tp2 - gd > stop: stop = tp2 - gd
                if ran:
                    if rmode >= 2 and peak - gd > stop: stop = peak - gd
                    if rmode == 1 and xh[j] >= m3 * tp2:
                        r = m3 * tp2
                        return locked + mult * ((r - e0) + s1 * (r - e1) + s2 * (r - e2)), j, ran
        if not ran:
            if kE >= 0 and j == kE and cum / (j + 1) < -0.1:
                return locked + mult * ((xc[j] - e0) + s1 * (xc[j] - e1) + s2 * (xc[j] - e2)), j, ran
            if k35 >= 0 and j == k35 and cum / (j + 1) < -0.15:
                return locked + mult * ((xc[j] - e0) + s1 * (xc[j] - e1) + s2 * (xc[j] - e2)), j, ran
            if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5:
                return locked + mult * ((xc[j] - e0) + s1 * (xc[j] - e1) + s2 * (xc[j] - e2)), j, ran
    r = xc[n - 1]
    return locked + mult * ((r - e0) + s1 * (r - e1) + s2 * (r - e2)), n - 1, ran


def do(sym):
    S = CB.sym_obj(sym); V = variants(); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k0 = p["k0"]
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + N][0]))) - 1 - k0) if p["i0"] + N < len(x["bars"]) else -1
        kh = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
        k16, kE, k35, k6 = kb(4), kh(1), kh(3.5), p["kbar"][6]
        tp2 = p["tp2"]; ht = tp2 is not None; tv = tp2 if ht else 0.0
        R = np.empty(len(V)); H = np.empty(len(V)); G = np.empty(len(V), dtype=np.bool_)
        for q, (ri, dm, ai) in enumerate(V):
            _, rm, m3, gd = RUN[ri]; _, a2, l2 = ADD[ai]
            r, j, ran = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], ht, tv,
                             k6, kE, k35, k16, rm, m3, gd, dm, a2, l2)
            R[q] = r; H[q] = (S.t[k0 + j] - S.t[k0]) / 60.0; G[q] = ran
        out.append(dict(t=x["t"], up=x["up"], sym=sym, strat=x["strat"], R=R, H=H, G=G, tp2=tv if ht else None,
                        par=v51_py(F, p["e0"], tp2, k6, k16, kE, k35)))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]; V = variants()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    R = np.array([z["R"] for z in res]); H = np.array([z["H"] for z in res]); G = np.array([z["G"] for z in res])
    idx = {v: q for q, v in enumerate(V)}; q0 = idx[(0, 1.0, 0)]
    d = max(abs(z["R"][q0] - z["par"]) for z in res)
    tps = [z["tp2"] for z in res if z["tp2"] is not None]
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades x {len(V)} variants, {time.time() - T0:.0f}s; v5.1 cell vs the "
         f"Python draft: max |diff| {d:.1e} ({'EXACT' if d < 1e-9 else 'MISMATCH'})",
         f"TP2 distance (R from the signal price): median {np.median(tps):.2f}, 25-75% {np.percentile(tps, 25):.2f}-{np.percentile(tps, 75):.2f}; "
         f"no TP2 on {sum(1 for z in res if z['tp2'] is None)} trades"]
    col = lambda q: list(R[:, q]); ref = col(q0)
    def line(nm, q):
        v = col(q); g = G[:, q]
        runh = f"  ran past TP2 {int(g.sum()):3d}, their median hold {np.median(H[g, q]) / 24:4.1f} days" if g.any() else ""
        sub = lambda f: st.mean([z["R"][q] for z in res if f(z)])
        return (f"  {nm:44} {st.mean(v):+.4f}R (vs v5.1 {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})  total {sum(v):+6.1f}  DD {maxdd(v):5.1f}  "
                f"tot/DD {sum(v) / maxdd(v):5.2f} | long {sub(lambda z: z['up']):+.4f} short {sub(lambda z: not z['up']):+.4f} | "
                f"2012-21 {sub(lambda z: z['t'][:4] < '2022'):+.4f} 2022-26 {sub(lambda z: z['t'][:4] >= '2022'):+.4f}{runh}")
    rname = lambda ri: {"tp2": "sell all at TP2 (v5.1)", "tp3": "TP3 = {m}x TP2, stop TP2-{g}R", "trail": "trail after TP2, bid high - {g}R",
                        "half": "50% off at TP2, trail the rest 1R"}[RUN[ri][0]].format(m=RUN[ri][2], g=RUN[ri][3])
    L.append("\n===== 1) RUNNER after TP2 (double 100%, no second add) =====")
    for ri in range(len(RUN)): L.append(line(rname(ri), idx[(ri, 1.0, 0)]))
    L.append("\n===== 2) DOUBLE SIZE (sell at TP2, no second add) =====")
    for dm in DMS: L.append(line(f"double {int(dm * 100)}%", idx[(0, dm, 0)]))
    L.append("\n===== 3) SECOND ADD at +3R (sell at TP2, double 100%) =====")
    for ai in range(len(ADD)): L.append(line(ADD[ai][0], idx[(0, 1.0, ai)]))
    nm = lambda v: f"{rname(v[0])} | double {int(v[1] * 100)}% | {ADD[v[2]][0]}"
    L.append("\n===== 4) ALL COMBINATIONS - top 10 by total/DD =====")
    for q in sorted(range(len(V)), key=lambda q: -sum(col(q)) / maxdd(col(q)))[:10]: L.append(line(nm(V[q]), q))
    L.append("\n===== top 10 by R per trade =====")
    for q in sorted(range(len(V)), key=lambda q: -st.mean(col(q)))[:10]: L.append(line(nm(V[q]), q))
    best = max(range(len(V)), key=lambda q: sum(col(q)) / maxdd(col(q)))
    sub = lambda q, f: st.mean([z["R"][q] for z in res if f(z)])
    L.append("\nby symbol / detector, v5.1 vs the best by total/DD:")
    for q in (q0, best):
        L.append(f"  {nm(V[q]):80} " + " ".join(f"{s[:6]} {sub(q, lambda z, s=s: z['sym'] == s):+.3f}" for s in SYMS) + " | "
                 + " ".join(f"{d_} {sub(q, lambda z, d_=d_: z['strat'] == d_):+.3f}" for d_ in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
