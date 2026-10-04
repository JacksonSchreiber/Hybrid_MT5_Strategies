#!/usr/bin/env python3
"""cbloc_v51_trend_check.py - trader 2026-10-04: draft v5.1 (commodity bloc: AUDUSD, NZDUSD, USDCAD; no trial) plus a TREND CHECK -
is the trade getting better? Average R (mean of M1 bid closes, in the trade's R) per clock window after entry: hour 1-2 vs hour 2-3,
2-3 vs 3-4, ... (window w hours, rolling hourly; the first check is when both windows start at hour 1 or later). At each hour boundary
from the first check to hour L: if (new window avg - previous window avg) < m the trade is not improving; after `consec` such checks
in a row -> sell everything at that minute's bid close. A comparison is skipped when either window has < 30 M1 bars (weekend,
rollover). Guard 'lock' = no trend check once the +1.5R lock is on (covers every doubled trade).
v5.1 = 100% at the signal; 1h check (avg since entry < -0.1 -> sell); 3.5h check (avg since entry < -0.15 -> sell); stop to entry at
+0.5R; 16h cut (bar-4 close: avg since entry < +1.5 -> sell); stop +1R at +1.5R; +100% at +2R before the bar-6 close; TP2.
Bases: v5.1, and v5 (= v5.1 without the 3.5h check, so the trend check replaces it). Kernel = numba; with the trend check off it
reproduces the Python v5 / v5.1 drafts exactly (checked in the run). SCALED (ask-corrected, live spreads). Holdout symbols untouched.
    python3 pipeline/cbloc_v51_trend_check.py     -> data/study/cbloc_v51_trend_check_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time, collections
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_v51_trend_check_report.txt")
MINB = 30
WS, LS, MS, GS, CS = [1, 2], [4, 6, 8, 12, 15], [-0.1, -0.05, 0.0, 0.05, 0.1], [0, 1], [1, 2]
KIND = ["stop -1R", "stop at entry", "stop at +1R", "TP2", "1h check sell", "3.5h check sell", "TREND CHECK sell", "16h cut", "window end"]


def variants():
    v = [(1, 0, 0, 0.0, 0, 1), (0, 0, 0, 0.0, 0, 1)]          # v5.1, v5 (trend check off)
    for b in (1, 0):
        for w in WS:
            for L in LS:
                if L < 1 + 2 * w: continue
                for m in MS:
                    for g in GS:
                        for c in CS: v.append((b, w, L, m, g, c))
    return v


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, csum, kh, n, e0, has_tp2, tp2, k6, k16, kE, k35, w, last_h, m, guard, consec):
    s1 = 0.0; e1 = 0.0; stop = -1.0; scode = 0; hit = False; moved = False; dd = False; cum = 0.0
    lv = 0.5
    if has_tp2 and tp2 < lv: lv = tp2
    hp = 1 + 2 * w; bad = 0
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            return (r - e0) + s1 * (r - e1), scode, dd
        if (not hit) and xh[j] >= lv:
            hit = True
            if e0 > stop: stop = e0; scode = 1
        if (not moved) and bh[j] >= 1.5:
            moved = True
            if e0 + 1.0 > stop: stop = e0 + 1.0; scode = 2
        if (not dd) and k6 >= 0 and j < k6 and bh[j] >= 2.0:
            b = bo[j] if bo[j] > 2.0 else 2.0
            s1 = 1.0; e1 = b + eo[j]; dd = True
        if has_tp2 and xh[j] >= tp2:
            return (tp2 - e0) + s1 * (tp2 - e1), 3, dd
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1:
            return (xc[j] - e0) + s1 * (xc[j] - e1), 4, dd
        if k35 >= 0 and j == k35 and cum / (j + 1) < -0.15:
            return (xc[j] - e0) + s1 * (xc[j] - e1), 5, dd
        if w > 0:
            while hp <= last_h and kh[hp] < j: hp += 1
            while hp <= last_h and kh[hp] == j:
                if not (guard == 1 and moved):
                    a2 = kh[hp - 2 * w]; a1 = kh[hp - w]; a0 = kh[hp]
                    if a1 - a2 >= MINB and a0 - a1 >= MINB:
                        prev = (csum[a1 + 1] - csum[a2 + 1]) / (a1 - a2)
                        new = (csum[a0 + 1] - csum[a1 + 1]) / (a0 - a1)
                        if new - prev < m: bad += 1
                        else: bad = 0
                        if bad >= consec:
                            return (xc[j] - e0) + s1 * (xc[j] - e1), 6, dd
                hp += 1
        if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5:
            return (xc[j] - e0) + s1 * (xc[j] - e1), 7, dd
    return (xc[n - 1] - e0) + s1 * (xc[n - 1] - e1), 8, dd


def v51_py(F, e0, tp2, k6, k16, kE, k2):
    """the Python draft used in chat (v5.1; k2 = -1 -> v5) - the parity reference"""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[1.0, e0]]; stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0
    val = lambda r: sum(s * (r - e) for s, e in legs)
    lv = min(0.5, tp2) if tp2 is not None else 0.5
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop: return val(min(stop, xo[j]))
        if not hit and xh[j] >= lv: hit = True; stop = max(stop, e0)
        if not moved and bh[j] >= 1.5: moved = True; stop = max(stop, e0 + 1.0)
        if not dd and k6 >= 0 and j < k6 and bh[j] >= 2.0: legs.append([1.0, max(2.0, bo[j]) + eo[j]]); dd = True
        if tp2 is not None and xh[j] >= tp2: return val(tp2)
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1: return val(xc[j])
        if k2 >= 0 and j == k2 and cum / (j + 1) < -0.15: return val(xc[j])
        if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5: return val(xc[j])
    return val(xc[n - 1])


def do(sym):
    S = CB.sym_obj(sym); V = variants(); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k0 = p["k0"]; n = p["n"]
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + N][0]))) - 1 - k0) if p["i0"] + N < len(x["bars"]) else -1
        kh_ = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
        k16, kE, k35, k6 = kb(4), kh_(1), kh_(3.5), p["kbar"][6]
        kh = np.array([-1] + [kh_(h) for h in range(1, 17)], dtype=np.int64)
        bc = np.asarray(F["bc"], dtype=np.float64); csum = np.concatenate(([0.0], np.cumsum(bc)))
        tp2 = p["tp2"]; ht = tp2 is not None; tv = tp2 if ht else 0.0
        R = np.empty(len(V)); C = np.empty(len(V), dtype=np.int8); D = np.empty(len(V), dtype=np.bool_)
        for q, (b, w, L, mm, g, c) in enumerate(V):
            R[q], C[q], D[q] = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], bc, F["eo"], csum, kh, n, p["e0"], ht, tv,
                                    k6, k16, kE, k35 if b else -1, w, L, mm, g, c)
        par = (v51_py(F, p["e0"], tp2, k6, k16, kE, k35), v51_py(F, p["e0"], tp2, k6, k16, kE, -1))
        out.append(dict(t=x["t"], up=x["up"], sym=sym, strat=x["strat"], R=R, C=C, D=D, par=par))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]; V = variants()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    R = np.array([z["R"] for z in res]); C = np.array([z["C"] for z in res]); D = np.array([z["D"] for z in res])
    d51 = max(abs(z["R"][0] - z["par"][0]) for z in res); d5 = max(abs(z["R"][1] - z["par"][1]) for z in res)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades x {len(V)} variants, {time.time() - T0:.0f}s; parity vs the "
         f"Python drafts: v5.1 max |diff| {d51:.1e}, v5 {d5:.1e} ({'EXACT' if max(d51, d5) < 1e-9 else 'MISMATCH'})"]
    col = lambda q: list(R[:, q])
    ref = col(0)
    stat = lambda v: (st.mean(v), maxdd(v), sum(v) / maxdd(v))
    for q, nm in ((0, "v5.1"), (1, "v5  ")):
        a, b, c = stat(col(q)); L.append(f"{nm}: {a:+.4f}R/trade  total {sum(col(q)):+.1f}  DD {b:.1f}  total/DD {c:.2f}")
    idx = {v: q for q, v in enumerate(V)}
    for b, bn in ((1, "BASE v5.1 (1h + 3.5h checks) + trend check"), (0, "BASE v5 (1h check; the trend check replaces the 3.5h check)")):
        for w in WS:
            for g in GS:
                for c in CS:
                    L.append(f"\n===== {bn} | window {w}h | guard {'lock (skip once +1.5R reached)' if g else 'none'} | cut after {c} bad check{'s in a row' if c > 1 else ''} =====")
                    L.append("  cells: R per trade / worst DD / total÷DD / % sold by the trend check")
                    L.append("  check to " + "".join(f"{'m ' + format(m, '+.2f'):>27}" for m in MS))
                    for Lh in LS:
                        if Lh < 1 + 2 * w: continue
                        cells = []
                        for m in MS:
                            q = idx[(b, w, Lh, m, g, c)]; a_, b_, c_ = stat(col(q))
                            cells.append(f"{a_:+.3f}/{b_:4.1f}/{c_:5.2f}/{100 * (C[:, q] == 6).mean():3.0f}%")
                        L.append(f"  {Lh:>4}h   " + "".join(f"{s:>27}" for s in cells))
    nmv = lambda v: (f"{'v5.1' if v[0] else 'v5'} + trend: {v[1]}h windows to {v[2]}h, m {v[3]:+.2f}, "
                     f"{'lock guard' if v[4] else 'no guard'}, {v[5]} in a row")
    grid_q = list(range(2, len(V)))
    top = sorted(grid_q, key=lambda q: -stat(col(q))[2])[:12]
    L.append("\n===== TOP 12 by total/DD (both bases), with splits; t = paired vs v5.1 =====")
    for q in top:
        v = col(q); sub = lambda f: st.mean([z["R"][q] for z in res if f(z)])
        L.append(f"  {nmv(V[q]):66} {st.mean(v):+.4f}R (vs v5.1 {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})  DD {maxdd(v):5.1f}  "
                 f"tot/DD {sum(v) / maxdd(v):5.2f}  sold {100 * (C[:, q] == 6).mean():.0f}% | long {sub(lambda z: z['up']):+.4f} short {sub(lambda z: not z['up']):+.4f} | "
                 f"2012-21 {sub(lambda z: z['t'][:4] < '2022'):+.4f} 2022-26 {sub(lambda z: z['t'][:4] >= '2022'):+.4f} | "
                 + " ".join(f"{s[:6]} {sub(lambda z, s=s: z['sym'] == s):+.3f}" for s in SYMS) + " | "
                 + " ".join(f"{d} {sub(lambda z, d=d: z['strat'] == d):+.3f}" for d in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")))
    L.append("\n===== what the trend check sells, by the trade's v5.1 outcome (top 4 cells) =====")
    for q in top[:4]:
        L.append(f"  {nmv(V[q])}")
        g = collections.defaultdict(list)
        for i in range(len(res)):
            if C[i, q] == 6: g[KIND[C[i, 0]] + (" + doubled" if D[i, 0] else "")].append(i)
        for k, ii in sorted(g.items(), key=lambda kv: sum(R[i, 0] for i in kv[1])):
            L.append(f"     {k:28} {len(ii):4d} trades  v5.1 {sum(R[i, 0] for i in ii):+7.1f}R  ->  sold {sum(R[i, q] for i in ii):+7.1f}R  "
                     f"(net {sum(R[i, q] - R[i, 0] for i in ii):+6.1f}R)")
        other = sum(R[i, q] - R[i, 0] for i in range(len(res)) if C[i, q] != 6)
        L.append(f"     (trades not sold by the trend check: net {other:+.1f}R)")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
