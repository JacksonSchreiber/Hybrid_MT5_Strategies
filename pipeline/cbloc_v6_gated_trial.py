#!/usr/bin/env python3
"""cbloc_v6_gated_trial.py - trader 2026-10-04: commodity bloc (AUDUSD, NZDUSD, USDCAD) draft v6 = v5.1 without the 1h and 3.5h checks,
with the trial back and a promotion gate:
  trial     F0 (50%) at the signal; the rest is added at the early promotion (bid high +E R, before the bar-3 close = 12h) or at the
            bar-3 close, whichever comes first
  gate      when a promotion is due and the average R since entry is < LO, it is held; it is placed (at that minute's close) once
            the average reaches HI. Applies to the early and the 12h promotion. (v6 as asked: LO 0.25, HI 0.5, E 0.75)
  kept      stop to entry at +0.5R; 16h cut (bar-4 close: avg < +1.5 -> sell); stop +1R at +1.5R; +100% at +2R before the bar-6
            close; TP2.
'no 12h add' = promotion only by the early trigger (any time) or the gate. Kernel = numba; with the gate off it reproduces the Python
trial draft from the chat and v5.1 exactly (checked in the run). SCALED (ask-corrected, live spreads). Holdout symbols untouched.
    python3 pipeline/cbloc_v6_gated_trial.py     -> data/study/cbloc_v6_gated_trial_report.txt
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

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_v6_gated_trial_report.txt")
OFF = -1e9
LOS, HIS, ES, F0S = [0.0, 0.15, 0.25, 0.35], [0.4, 0.5, 0.75], [0.5, 0.75, 1.0], [0.5, 0.25]


def variants():
    # (name, F0, E, LO, HI, sched, chk1, chk35)
    v = [("v5.1", 1.0, 0.75, OFF, OFF, 1, 1, 1), ("chat trial v6 (50%, 1h + 3.5h checks)", 0.5, 0.75, OFF, OFF, 1, 1, 1),
         ("v6, no gate", 0.5, 0.75, OFF, OFF, 1, 0, 0), ("v6 AS ASKED (gate 0.25 / 0.5)", 0.5, 0.75, 0.25, 0.5, 1, 0, 0),
         ("v6 as asked, trial 25%", 0.25, 0.75, 0.25, 0.5, 1, 0, 0), ("v6 as asked, no 12h add", 0.5, 0.75, 0.25, 0.5, 0, 0, 0),
         ("v6 no gate, trial 25%", 0.25, 0.75, OFF, OFF, 1, 0, 0)]
    for f0 in F0S:
        for lo in LOS:
            for hi in HIS: v.append((f"grid F0 {f0} LO {lo} HI {hi}", f0, 0.75, lo, hi, 1, 0, 0))
        for e in ES: v.append((f"grid F0 {f0} E {e}", f0, e, 0.25, 0.5, 1, 0, 0))
    return v


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, has_tp2, tp2, k6, k16, kE, k35, k12, kp_end, F0, E, lo, hi, sched):
    """-> (R, promotion kind 0 none / 1 early / 2 at 12h / 3 gate release)"""
    sp = 0.0; ep = 0.0; s1 = 0.0; e1 = 0.0
    stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0; added = F0 >= 1.0; pend = False; kind = 0
    lv = 0.5
    if has_tp2 and tp2 < lv: lv = tp2
    for j in range(n):
        cum += bc[j]; avg = cum / (j + 1)
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            return F0 * (r - e0) + sp * (r - ep) + s1 * (r - e1), kind
        if (not hit) and xh[j] >= lv:
            hit = True
            if e0 > stop: stop = e0
        if (not added) and (not pend) and kp_end >= 0 and j < kp_end and bh[j] >= E:
            if avg >= lo:
                b = bo[j] if bo[j] > E else E
                sp = 1.0 - F0; ep = b + eo[j]; added = True; kind = 1
            else: pend = True
        elif pend and avg >= hi:
            sp = 1.0 - F0; ep = bc[j] + eo[j]; added = True; pend = False; kind = 3
        if (not moved) and bh[j] >= 1.5:
            moved = True
            if e0 + 1.0 > stop: stop = e0 + 1.0
        if (not dd) and k6 >= 0 and j < k6 and bh[j] >= 2.0:
            b = bo[j] if bo[j] > 2.0 else 2.0
            s1 = 1.0; e1 = b + eo[j]; dd = True
        if has_tp2 and xh[j] >= tp2:
            return F0 * (tp2 - e0) + sp * (tp2 - ep) + s1 * (tp2 - e1), kind
        if kE >= 0 and j == kE and avg < -0.1:
            return F0 * (xc[j] - e0) + sp * (xc[j] - ep) + s1 * (xc[j] - e1), kind
        if k35 >= 0 and j == k35 and avg < -0.15:
            return F0 * (xc[j] - e0) + sp * (xc[j] - ep) + s1 * (xc[j] - e1), kind
        if sched == 1 and (not added) and (not pend) and k12 >= 0 and j == k12:
            if avg >= lo:
                sp = 1.0 - F0; ep = bc[j] + eo[j]; added = True; kind = 2
            else: pend = True
        if k16 >= 0 and j == k16 and avg < 1.5:
            return F0 * (xc[j] - e0) + sp * (xc[j] - ep) + s1 * (xc[j] - e1), kind
    r = xc[n - 1]
    return F0 * (r - e0) + sp * (r - ep) + s1 * (r - e1), kind


def trial_py(F, e0, tp2, k6, k16, kE, k2, k12, F0, chk35):
    """the Python trial draft from the chat (F0 = 1 -> v5.1) - the parity reference"""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0, e0]]; stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0; added = F0 >= 1.0
    val = lambda r: sum(s * (r - e) for s, e in legs)
    lv = min(0.5, tp2) if tp2 is not None else 0.5
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop: return val(min(stop, xo[j]))
        if not hit and xh[j] >= lv: hit = True; stop = max(stop, e0)
        if not added and k12 >= 0 and j < k12 and bh[j] >= 0.75: legs.append([1 - F0, max(0.75, bo[j]) + eo[j]]); added = True
        if not moved and bh[j] >= 1.5: moved = True; stop = max(stop, e0 + 1.0)
        if not dd and k6 >= 0 and j < k6 and bh[j] >= 2.0: legs.append([1.0, max(2.0, bo[j]) + eo[j]]); dd = True
        if tp2 is not None and xh[j] >= tp2: return val(tp2)
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1: return val(xc[j])
        if chk35 and k2 >= 0 and j == k2 and cum / (j + 1) < -0.15: return val(xc[j])
        if not added and k12 >= 0 and j == k12: legs.append([1 - F0, bc[j] + eo[j]]); added = True
        if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5: return val(xc[j])
    return val(xc[n - 1])


def do(sym):
    S = CB.sym_obj(sym); V = variants(); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k0 = p["k0"]; n = p["n"]
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + N][0]))) - 1 - k0) if p["i0"] + N < len(x["bars"]) else -1
        kh = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
        k16, k12, kE, k35, k6 = kb(4), kb(3), kh(1), kh(3.5), p["kbar"][6]
        tp2 = p["tp2"]; ht = tp2 is not None; tv = tp2 if ht else 0.0
        R = np.empty(len(V)); K = np.empty(len(V), dtype=np.int8)
        for q, (_, f0, e, lo, hi, sc, c1, c35) in enumerate(V):
            R[q], K[q] = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], n, p["e0"], ht, tv, k6, k16,
                              kE if c1 else -1, k35 if c35 else -1, k12, k12 if sc else n, f0, e, lo, hi, sc)
        par = (trial_py(F, p["e0"], tp2, k6, k16, kE, k35, k12, 1.0, True), trial_py(F, p["e0"], tp2, k6, k16, kE, k35, k12, 0.5, True))
        out.append(dict(t=x["t"], up=x["up"], sym=sym, strat=x["strat"], R=R, K=K, par=par))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]; V = variants()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    R = np.array([z["R"] for z in res]); K = np.array([z["K"] for z in res])
    d = max(max(abs(z["R"][0] - z["par"][0]), abs(z["R"][1] - z["par"][1])) for z in res)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades x {len(V)} variants, {time.time() - T0:.0f}s; gate-off cells vs the "
         f"Python drafts (v5.1, chat trial): max |diff| {d:.1e} ({'EXACT' if d < 1e-9 else 'MISMATCH'})"]
    col = lambda q: list(R[:, q]); ref = col(0)
    def line(q, nm=None):
        v = col(q); sub = lambda f: st.mean([z["R"][q] for z in res if f(z)])
        pk = K[:, q]; pr = f"promoted {100 * (pk > 0).mean():.0f}% (early {100 * (pk == 1).mean():.0f} / 12h {100 * (pk == 2).mean():.0f} / gate {100 * (pk == 3).mean():.0f})"
        return (f"  {nm or V[q][0]:40} {st.mean(v):+.4f}R" + (f" (vs v5.1 {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})" if q else " " * 28)
                + f"  total {sum(v):+6.1f}  DD {maxdd(v):5.1f}  tot/DD {sum(v) / maxdd(v):5.2f} | {pr} | long {sub(lambda z: z['up']):+.4f} short {sub(lambda z: not z['up']):+.4f} | "
                f"2012-21 {sub(lambda z: z['t'][:4] < '2022'):+.4f} 2022-26 {sub(lambda z: z['t'][:4] >= '2022'):+.4f} | "
                + " ".join(f"{s[:6]} {sub(lambda z, s=s: z['sym'] == s):+.3f}" for s in SYMS) + " | "
                + " ".join(f"{d_} {sub(lambda z, d_=d_: z['strat'] == d_):+.3f}" for d_ in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")))
    L.append("\n===== LINEUP =====")
    for q in range(7): L.append(line(q))
    idx = {v[0]: q for q, v in enumerate(V)}
    cell = lambda q: f"{st.mean(col(q)):+.3f}/{maxdd(col(q)):4.1f}/{sum(col(q)) / maxdd(col(q)):5.2f}"
    for f0 in F0S:
        L.append(f"\n===== GATE GRID, trial {int(f0 * 100)}%, early promote +0.75R (cells: R per trade / worst DD / total÷DD) =====")
        L.append("  hold if avg <    " + "".join(f"{'place at avg +' + str(hi):>22}" for hi in HIS))
        for lo in LOS: L.append(f"  +{lo:<5}          " + "".join(f"{cell(idx[f'grid F0 {f0} LO {lo} HI {hi}']):>22}" for hi in HIS))
        L.append(f"  early promote level (gate 0.25 / 0.5): " + "  ".join(f"+{e}R {cell(idx[f'grid F0 {f0} E {e}'])}" for e in ES))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
