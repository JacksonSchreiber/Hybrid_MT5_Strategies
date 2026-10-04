#!/usr/bin/env python3
"""cbloc_knob_search.py - trader 2026-10-03: every management knob for the unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD), guided by
the trader's principles (slow trades are dead trades - cut them in the first day; winners win fast; tight stops). Raw-signal family,
SCALED engine (ask-corrected, live spreads), results in full-position R, risk-matched for the opening stop (result / stop fraction).
KNOBS (52,920 combos):
  sf    opening stop: 1.0 (the signal's) or 0.75 of its distance
  F0    trial: 1.0 / 0.5 / 0.25 first; the rest at the bar-6 close unless promoted or cut
  promo early promotion inside the first 24h when the bid reaches +1R / +1.5R (with a trial only)
  cut   first-day cut: at the close of bar 3 (12h) or bar 6 (24h), close everything if the bid high has not reached +1 / +1.5 / +2R
  loss  12h loss cut: at the bar-3 close, close if price is below -0.25R / 0R
  bank  25% / 50% at the first +0.5R / +1R, the stop to entry at that moment
  be    stop to entry (no bank) at +0.5R / +1R
  lock  every stop to +1R at +1.5R / to +1R at +2R / to +1.5R at +2R
  tp    TP2, or a cap at 2R / 3R (whichever is nearer)
Per minute: stop, bank, be, promotion, lock, target, the 12h loss cut, the first-day cut, the bar-6 add.
OVERFIT GUARD: the search ranks on 2012-2021 (TRAIN) only; 2022-2026 (TEST) is reported for the top combos and never used to choose.
The commodity-bloc holdouts (AUDNZD, AUDCAD, NZDCAD) are not touched.
    python3 pipeline/cbloc_knob_search.py      -> data/study/cbloc_knob_search_report.txt (+ cbloc_knob_search.npz)
"""
from __future__ import annotations
import itertools, os, sys, time
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.progress import Bar                        # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]; SPLIT = "2022"
REPORT = os.path.join(A.STUDY, "cbloc_knob_search_report.txt"); NPZ = os.path.join(A.STUDY, "cbloc_knob_search.npz")
SF = [1.0, 0.75]; F0P = [(1.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.5, 1.5), (0.25, 0.0), (0.25, 1.0), (0.25, 1.5)]
CUT = [(0, 0.0)] + [(b, l) for b in (3, 6) for l in (1.0, 1.5, 2.0)]; LOSS = [-9.0, -0.25, 0.0]
BANK = [(0.0, 0.0)] + [(f, l) for f in (0.25, 0.5) for l in (0.5, 1.0)]; BE = [0.0, 0.5, 1.0]
LOCK = [(0.0, 0.0), (1.5, 1.0), (2.0, 1.0), (2.0, 1.5)]; TP = [0.0, 2.0, 3.0]
COLS = ["sf", "F0", "promo", "cut_bar", "cut_lvl", "loss", "bank_f", "bank_lvl", "be", "lock_trig", "lock_lvl", "tp_cap"]


def grid():
    rows = []
    for sf, (F0, pr), (cb, cl), lo, (bf, bl), be, (lt, ll), tp in itertools.product(SF, F0P, CUT, LOSS, BANK, BE, LOCK, TP):
        rows.append((sf, F0, pr, cb, cl, lo, bf, bl, be, lt, ll, tp))
    return np.array(rows, dtype=np.float64)


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, has_tp2, tp2, k3, k6, P, out):
    for c in range(P.shape[0]):
        sf = P[c, 0]; F0 = P[c, 1]; promo = P[c, 2]; cb = P[c, 3]; cl = P[c, 4]; loss = P[c, 5]
        bf = P[c, 6]; blv = P[c, 7]; be = P[c, 8]; lt = P[c, 9]; ll = P[c, 10]; cap = P[c, 11]
        s0 = F0; e_0 = e0; s1 = 0.0; e_1 = 0.0; locked = 0.0; stop = -sf
        banked = False; added = F0 >= 1.0; locked_done = False; peak = -1e9
        tgt = tp2 if has_tp2 else 1e9
        if cap > 0.0 and cap < tgt: tgt = cap
        bl = blv if blv < tgt else tgt
        kc = -1
        if cb == 3.0: kc = k3
        elif cb == 6.0: kc = k6
        r = xc[n - 1]; done = False
        for j in range(n):
            if xl[j] <= stop:
                r = stop if stop < xo[j] else xo[j]; done = True; break
            if bh[j] > peak: peak = bh[j]
            if bf > 0.0 and (not banked) and xh[j] >= bl:
                banked = True; locked += bf * (s0 * (bl - e_0) + s1 * (bl - e_1)); s0 *= 1 - bf; s1 *= 1 - bf
                if stop < e0: stop = e0
            if be > 0.0 and bh[j] >= be and stop < e0: stop = e0
            if promo > 0.0 and (not added) and k6 >= 0 and j < k6 and bh[j] >= promo:
                s1 = 1 - F0; e_1 = (promo if promo > bo[j] else bo[j]) + eo[j]; added = True
            if lt > 0.0 and (not locked_done) and bh[j] >= lt:
                locked_done = True
                if stop < e0 + ll: stop = e0 + ll
            if xh[j] >= tgt:
                r = tgt; done = True; break
            if loss > -9.0 and k3 >= 0 and j == k3 and xc[j] < loss:
                r = xc[j]; done = True; break
            if kc >= 0 and j == kc and peak < cl:
                r = xc[j]; done = True; break
            if (not added) and k6 >= 0 and j == k6:
                s1 = 1 - F0; e_1 = bc[j] + eo[j]; added = True
        out[c] = (locked + s0 * (r - e_0) + s1 * (r - e_1)) / sf


POP = []; P = None
def do_chunk(idx):
    out = np.zeros((len(idx), P.shape[0]), dtype=np.float32); meta = []
    for q, i in enumerate(idx):
        x = POP[i]; S = CB.sym_obj(x["sym"]); p = CB.prep(S, x)
        if p is None: meta.append(None); continue
        F = p["F"]; tmp = np.zeros(P.shape[0])
        kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], p["tp2"] is not None,
             p["tp2"] if p["tp2"] is not None else 0.0, p["kbar"][3], p["kbar"][6], P, tmp)
        out[q] = tmp; meta.append((x["sym"], x["t"], bool(x["up"])))
    return idx, out, meta


def maxdd_rows(M):
    """worst drawdown of every column of M (trades in time order x combos)."""
    c = np.cumsum(M, axis=0, dtype=np.float64); peak = np.maximum.accumulate(np.vstack([np.zeros((1, M.shape[1])), c]), axis=0)[1:]
    return (peak - c).max(axis=0)


def label(r):
    sf, F0, pr, cb, cl, lo, bf, bl, be, lt, ll, tp = r
    parts = [f"stop {'full' if sf == 1 else '3/4'}", "no trial" if F0 >= 1 else f"trial {int(F0 * 100)}%" + (f" promo@+{pr}R" if pr else "")]
    parts.append("no cut" if not cb else f"cut {'12h' if cb == 3 else '24h'} <+{cl}R")
    if lo > -9: parts.append(f"12h loss-cut <{lo}R")
    if bf: parts.append(f"bank {int(bf * 100)}%@+{bl}R->entry")
    if be: parts.append(f"BE@+{be}R")
    if lt: parts.append(f"lock +{ll}R@+{lt}R")
    parts.append("TP2" if not tp else f"cap {tp}R")
    return ", ".join(parts)


def main():
    global POP, P
    T0 = time.time(); P = grid()
    POP = sorted([x for x in CB.population() if x["sym"] in SYMS], key=lambda x: x["t"])
    S0 = CB.sym_obj(POP[0]["sym"]); p0 = CB.prep(S0, POP[0]); tmp = np.zeros(2)     # compile once in the parent
    kern(p0["F"]["xo"], p0["F"]["xh"], p0["F"]["xl"], p0["F"]["xc"], p0["F"]["bo"], p0["F"]["bh"], p0["F"]["bc"], p0["F"]["eo"], p0["n"], p0["e0"],
         p0["tp2"] is not None, p0["tp2"] or 0.0, p0["kbar"][3], p0["kbar"][6], P[:2], tmp); CB._SYMS.clear()
    chunks = [list(range(i, min(i + 12, len(POP)))) for i in range(0, len(POP), 12)]
    M = np.zeros((len(POP), P.shape[0]), dtype=np.float32); meta = [None] * len(POP); bar = Bar(len(POP), "search", "trades")
    import multiprocessing as mp
    with mp.get_context("fork").Pool(18) as pool:
        for idx, out, mt in pool.imap_unordered(do_chunk, chunks):
            M[idx] = out
            for i, m in zip(idx, mt): meta[i] = m
            bar.update(len(idx))
    bar.done()
    ok = np.array([m is not None for m in meta]); M = M[ok]; meta = [m for m in meta if m]
    np.savez_compressed(NPZ, M=M, P=P, meta=np.array([f"{s}|{t}|{int(u)}" for s, t, u in meta]))
    # engine checks against the earlier studies
    def find(**kw):
        d = dict(zip(COLS, [1.0, 1.0, 0, 0, 0, -9.0, 0, 0, 0, 0, 0, 0])); d.update(kw)
        return int(np.where(np.all(np.isclose(P, [d[k] for k in COLS]), axis=1))[0][0])
    raw = find(); prev = find(F0=0.25, cut_bar=6, cut_lvl=1.5, bank_f=0.25, bank_lvl=0.5, lock_trig=1.5, lock_lvl=1.0)
    tr = np.array([m[1][:4] < SPLIT for m in meta]); te = ~tr; up = np.array([m[2] for m in meta]); sy = np.array([m[0] for m in meta])
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {M.shape[0]} trades x {M.shape[1]:,} combos in {(time.time() - T0) / 60:.1f} min; "
         f"TRAIN 2012-2021 n={tr.sum()}, TEST 2022-2026 n={te.sum()}",
         f"engine checks: raw signal {M[:, raw].mean():+.4f} (cbloc_raw_timestop: -0.1065); trial+bank->entry+cut+lock combo {M[:, prev].mean():+.4f} (cbloc_combo_trial: +0.0117)"]
    mtr, mte = M[tr].mean(axis=0), M[te].mean(axis=0); ddtr, ddte = maxdd_rows(M[tr]), maxdd_rows(M[te]); ddall = maxdd_rows(M)
    tottr = M[tr].sum(axis=0)
    rho = np.corrcoef(np.argsort(np.argsort(mtr)), np.argsort(np.argsort(mte)))[0, 1]
    L.append(f"how well TRAIN predicts TEST across all combos (rank correlation of R/trade): {rho:+.2f}\n")
    L.append(f"reference: raw signal TRAIN {mtr[raw]:+.4f} (DD {ddtr[raw]:.1f}) TEST {mte[raw]:+.4f} (DD {ddte[raw]:.1f}) | previous best (trial+bank+cut+lock) "
             f"TRAIN {mtr[prev]:+.4f} (DD {ddtr[prev]:.1f}) TEST {mte[prev]:+.4f} (DD {ddte[prev]:.1f})\n")
    def board(title, order):
        L.append(f"===== {title} (chosen on TRAIN only) =====")
        for c in order[:15]:
            sub = lambda m: M[m, c].mean()
            L.append(f"  TRAIN {mtr[c]:+.4f} (DD {ddtr[c]:5.1f}, tot/DD {tottr[c] / max(ddtr[c], 1e-9):+.2f}) | TEST {mte[c]:+.4f} (DD {ddte[c]:5.1f}) | all DD {ddall[c]:5.1f} | "
                     f"long {sub(up):+.3f} short {sub(~up):+.3f} | " + " ".join(f"{s[:6]} {sub(sy == s):+.3f}" for s in SYMS) + f"\n      {label(P[c])}")
        L.append("")
    board("best R per trade on TRAIN", np.argsort(-mtr))
    board("best total R / drawdown on TRAIN", np.argsort(-(tottr / np.maximum(ddtr, 1e-9))))
    # knob by knob: average TEST R/trade of the TRAIN top-1% that use each setting (which settings the good combos share)
    top = np.argsort(-(tottr / np.maximum(ddtr, 1e-9)))[:max(50, P.shape[0] // 100)]
    L.append("===== knob settings among the TRAIN top 1% (by total/DD): share of combos using each setting =====")
    for k, name in enumerate(COLS):
        vals, cnt = np.unique(P[top, k], return_counts=True)
        L.append(f"  {name:10} " + "  ".join(f"{v:g}: {100 * c / len(top):.0f}%" for v, c in zip(vals, cnt)))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
