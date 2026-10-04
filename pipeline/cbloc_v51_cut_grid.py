#!/usr/bin/env python3
"""cbloc_v51_cut_grid.py - trader 2026-10-04: draft v5.1 (commodity bloc: AUDUSD, NZDUSD, USDCAD; no trial) - move the 16h cut, or
replace it with a raised stop.
  CUT    at the close of H4 bar N after the signal (N = 2..12 -> 8h..48h), sell everything if the average R since entry < Y
         (v5.1 = bar 4, Y = +1.5).
  RAISE  no cut; at the bar-N close instead move the stop:
           fixed     to entry + X R (X = -0.5 .. +1.0); if the bid is already at/below that level, sell at the bid close
           relative  to the bid close - D R (D = 0.25 .. 1.0); never forces a sale
         (never lowers a stop).
  NONE   no cut and no raise (only v5.1's stops: entry at +0.5R, +1R at +1.5R).
Everything else = v5.1: 100% at the signal; 1h check (avg < -0.1 -> sell); 3.5h check (avg < -0.15 -> sell); stop to entry at +0.5R;
stop +1R at +1.5R; +100% at +2R before the bar-6 close; TP2. Kernel = numba; the bar-4 / +1.5 cell reproduces the Python v5.1 draft
exactly (checked in the run). SCALED (ask-corrected, live spreads). Holdout symbols untouched.
    python3 pipeline/cbloc_v51_cut_grid.py     -> data/study/cbloc_v51_cut_grid_report.txt
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
REPORT = os.path.join(A.STUDY, "cbloc_v51_cut_grid_report.txt")
NS = [2, 3, 4, 5, 6, 8, 12]
YS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
XS = [-0.5, -0.25, 0.0, 0.25, 0.5, 1.0]
DS = [0.25, 0.5, 0.75, 1.0]


def variants():
    v = [("cut", 4, 1.5), ("none", 0, 0.0)]
    v += [("cut", N, y) for N in NS for y in YS if (N, y) != (4, 1.5)]
    v += [("fix", N, x) for N in NS for x in XS] + [("rel", N, d) for N in NS for d in DS]
    return v


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, has_tp2, tp2, k6, kE, k35, kcut, ycut, kr, rmode, rval):
    s1 = 0.0; e1 = 0.0; stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0
    lv = 0.5
    if has_tp2 and tp2 < lv: lv = tp2
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            return (r - e0) + s1 * (r - e1), j
        if (not hit) and xh[j] >= lv:
            hit = True
            if e0 > stop: stop = e0
        if (not moved) and bh[j] >= 1.5:
            moved = True
            if e0 + 1.0 > stop: stop = e0 + 1.0
        if (not dd) and k6 >= 0 and j < k6 and bh[j] >= 2.0:
            b = bo[j] if bo[j] > 2.0 else 2.0
            s1 = 1.0; e1 = b + eo[j]; dd = True
        if has_tp2 and xh[j] >= tp2:
            return (tp2 - e0) + s1 * (tp2 - e1), j
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1:
            return (xc[j] - e0) + s1 * (xc[j] - e1), j
        if k35 >= 0 and j == k35 and cum / (j + 1) < -0.15:
            return (xc[j] - e0) + s1 * (xc[j] - e1), j
        if kcut >= 0 and j == kcut and cum / (j + 1) < ycut:
            return (xc[j] - e0) + s1 * (xc[j] - e1), j
        if rmode > 0 and kr >= 0 and j == kr:
            lvl = e0 + rval if rmode == 1 else xc[j] - rval
            if xc[j] <= lvl:
                return (xc[j] - e0) + s1 * (xc[j] - e1), j
            if lvl > stop: stop = lvl
    return (xc[n - 1] - e0) + s1 * (xc[n - 1] - e1), n - 1


def do(sym):
    S = CB.sym_obj(sym); V = variants(); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k0 = p["k0"]
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + N][0]))) - 1 - k0) if p["i0"] + N < len(x["bars"]) else -1
        kh = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
        KB = {N: kb(N) for N in NS}; kE, k35, k6 = kh(1), kh(3.5), p["kbar"][6]
        tp2 = p["tp2"]; ht = tp2 is not None; tv = tp2 if ht else 0.0
        R = np.empty(len(V)); H = np.empty(len(V))
        for q, (kind, N, val) in enumerate(V):
            kc, y, kr, rm = -1, 0.0, -1, 0
            if kind == "cut": kc, y = KB[N], val
            elif kind == "fix": kr, rm = KB[N], 1
            elif kind == "rel": kr, rm = KB[N], 2
            r, j = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], ht, tv, k6, kE, k35,
                        kc, y, kr, rm, val)
            R[q] = r; H[q] = (S.t[k0 + j] - S.t[k0]) / 60.0
        par = v51_py(F, p["e0"], tp2, k6, KB[4], kE, k35)
        out.append(dict(t=x["t"], up=x["up"], sym=sym, strat=x["strat"], R=R, H=H, par=par))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]; V = variants()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    R = np.array([z["R"] for z in res]); H = np.array([z["H"] for z in res])
    d = max(abs(z["R"][0] - z["par"]) for z in res)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades x {len(V)} variants, {time.time() - T0:.0f}s; "
         f"bar-4/+1.5 cell vs the Python v5.1 draft: max |diff| {d:.1e} ({'EXACT' if d < 1e-9 else 'MISMATCH'})"]
    col = lambda q: list(R[:, q]); ref = col(0); idx = {v: q for q, v in enumerate(V)}
    cell = lambda q: f"{st.mean(col(q)):+.3f}/{maxdd(col(q)):4.1f}/{sum(col(q)) / maxdd(col(q)):5.2f}"
    for q, nm in ((0, "v5.1 (cut at bar 4 if avg < +1.5)"), (1, "no cut, no raise")):
        v = col(q); L.append(f"{nm:36} {st.mean(v):+.4f}R  total {sum(v):+.1f}  DD {maxdd(v):.1f}  total/DD {sum(v) / maxdd(v):.2f}  "
                             f"median hours {np.median(H[:, q]):.1f}")
    L.append("\n===== CUT: sell at the bar-N close if the average R since entry < Y  (cells: R per trade / worst DD / total÷DD) =====")
    L.append("  bar (hours)   " + "".join(f"{'Y < +' + format(y, '.2f'):>18}" for y in YS))
    for N in NS:
        L.append(f"  bar {N:2d} ({4 * N:2d}h)  " + "".join(f"{cell(0 if (N, y) == (4, 1.5) else idx[('cut', N, y)]):>18}" for y in YS))
    L.append("\n===== RAISE instead of the cut: at the bar-N close move the stop (fixed: to entry+X, sell if already below; relative: bid - D) =====")
    hdr = [f"to {x:+.2f}R" for x in XS] + [f"bid - {d_:.2f}R" for d_ in DS]
    L.append("  bar (hours)   " + "".join(f"{h:>18}" for h in hdr))
    for N in NS:
        L.append(f"  bar {N:2d} ({4 * N:2d}h)  " + "".join(f"{cell(idx[('fix', N, x)]):>18}" for x in XS)
                 + "".join(f"{cell(idx[('rel', N, d_)]):>18}" for d_ in DS))
    nm = lambda v: ({"cut": f"cut at bar {v[1]} ({4 * v[1]}h) if avg < +{v[2]}", "none": "no cut, no raise",
                     "fix": f"raise at bar {v[1]} ({4 * v[1]}h) to {v[2]:+.2f}R", "rel": f"raise at bar {v[1]} ({4 * v[1]}h) to bid - {v[2]}R"}[v[0]])
    def show(title, qs):
        L.append(f"\n===== {title} (t = paired vs v5.1) =====")
        for q in qs:
            v = col(q); sub = lambda f: st.mean([z["R"][q] for z in res if f(z)])
            L.append(f"  {nm(V[q]):36} {st.mean(v):+.4f}R (vs v5.1 {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})  DD {maxdd(v):5.1f}  "
                     f"tot/DD {sum(v) / maxdd(v):5.2f}  med h {np.median(H[:, q]):5.1f} | long {sub(lambda z: z['up']):+.4f} short {sub(lambda z: not z['up']):+.4f} | "
                     f"2012-21 {sub(lambda z: z['t'][:4] < '2022'):+.4f} 2022-26 {sub(lambda z: z['t'][:4] >= '2022'):+.4f} | "
                     + " ".join(f"{s[:6]} {sub(lambda z, s=s: z['sym'] == s):+.3f}" for s in SYMS) + " | "
                     + " ".join(f"{d_} {sub(lambda z, d_=d_: z['strat'] == d_):+.3f}" for d_ in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")))
    tot_dd = lambda q: sum(col(q)) / maxdd(col(q))
    show("TOP 6 CUT cells by total/DD", sorted([q for q, v in enumerate(V) if v[0] == "cut"], key=lambda q: -tot_dd(q))[:6])
    show("TOP 6 CUT cells by R per trade", sorted([q for q, v in enumerate(V) if v[0] == "cut"], key=lambda q: -st.mean(col(q)))[:6])
    show("TOP 6 RAISE cells by total/DD", sorted([q for q, v in enumerate(V) if v[0] in ("fix", "rel")], key=lambda q: -tot_dd(q))[:6])
    show("TOP 6 RAISE cells by R per trade", sorted([q for q, v in enumerate(V) if v[0] in ("fix", "rel")], key=lambda q: -st.mean(col(q)))[:6])
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
