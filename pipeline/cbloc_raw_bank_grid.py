#!/usr/bin/env python3
"""cbloc_raw_bank_grid.py - trader 2026-10-03: unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD), RAW SIGNAL base, plus
  24h cut  close at the bar-6 close if the bid high has not reached +X R by then; X in {none, 0.5, 0.75, 1.0, 1.25, 1.5}
  bank     bank share b in {0 (none), 25, 50, 75, 100%} when the price first reaches +L R, L in {0.5 .. 2.0}; the stop then moves to entry
           (as live); L at or beyond TP2 = TP2. Raw = no TP1, no staging, no pyramid.
Per minute (the engine's order): stop, bank, TP2, the 24h cut. SCALED (ask-corrected, live spreads). Kernel compiled with numba; the
no-bank cells reproduce cbloc_raw_timestop.py exactly (checked in the run).
    python3 pipeline/cbloc_raw_bank_grid.py     -> data/study/cbloc_raw_bank_grid_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline import cbloc_raw_timestop as RT            # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402

SYMS = RT.SYMS
CUTS = [None, 0.5, 0.75, 1.0, 1.25, 1.5]
SHARES = [0.0, 0.25, 0.5, 0.75, 1.0]
LEVELS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
REPORT = os.path.join(A.STUDY, "cbloc_raw_bank_grid_report.txt")


@njit(cache=True)
def kern(xo, xh, xl, xc, bh, n, e0, has_tp2, tp2_R, k6, cutX, bf, bL):
    """raw + optional bank + optional 24h cut -> R. cutX < 0 = no cut; bf = 0 = no bank."""
    size = 1.0; locked = 0.0; stop = -1.0; banked = False; peak = -1e9
    bank_lvl = bL
    if has_tp2 and bank_lvl > tp2_R: bank_lvl = tp2_R
    for j in range(n):
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            return locked + size * (r - e0)
        if bh[j] > peak: peak = bh[j]
        if bf > 0.0 and (not banked) and xh[j] >= bank_lvl:
            banked = True; locked += bf * (bank_lvl - e0); size = size * (1 - bf); stop = e0
            if size <= 1e-12: return locked
        if has_tp2 and xh[j] >= tp2_R:
            return locked + size * (tp2_R - e0)
        if cutX >= 0.0 and k6 >= 0 and j == k6 and peak < cutX:
            return locked + size * (xc[j] - e0)
    return locked + size * (xc[n - 1] - e0)


def cells():
    out = [(c, 0.0, None) for c in CUTS]
    out += [(c, b, L) for c in CUTS for b in SHARES[1:] for L in LEVELS]
    return out


def do_symbol(sym):
    S = CB.sym_obj(sym); out = []; C = cells()
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k6 = p["kbar"][6]; tp2 = p["tp2"]
        r = {}
        for c, b, L in C:
            r[(c, b, L)] = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], p["n"], p["e0"], tp2 is not None, tp2 if tp2 is not None else 0.0,
                                k6 if k6 is not None else -1, -1.0 if c is None else c, b, L if L is not None else 0.0)
        r["chk"] = [RT.raw_cut(F, p["e0"], tp2, k6 if k6 >= 0 else None, c)[0] for c in CUTS]
        out.append((sym, x["t"], x["up"], r))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: res = sorted([z for rs in pool.map(do_symbol, SYMS) for z in rs], key=lambda z: z[1])
    d = max(abs(z[3][(c, 0.0, None)] - z[3]["chk"][q]) for z in res for q, c in enumerate(CUTS))
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades x {len(cells())} variants, {time.time() - T0:.0f}s; "
         f"no-bank cells vs cbloc_raw_timestop: max |diff| {d:.1e} ({'EXACT' if d < 1e-12 else 'MISMATCH'})"]
    base = [z[3][(None, 0.0, None)] for z in res]
    def grid(zs, title):
        b0 = [z[3][(None, 0.0, None)] for z in zs]
        L.append(f"\n===== {title}: n={len(zs)}, raw signal {st.mean(b0):+.4f}R/trade, DD {maxdd(b0):.1f} =====")
        for c in CUTS:
            nb = [z[3][(c, 0.0, None)] for z in zs]
            L.append(f"\n  24h cut {'none' if c is None else '+' + str(c) + 'R'}   (no bank: {st.mean(nb):+.4f}R/trade, DD {maxdd(nb):.1f})")
            L.append("    R/trade        " + "".join(f"{'@+' + str(l) + 'R':>10}" for l in LEVELS))
            for b in SHARES[1:]:
                L.append(f"    bank {int(b * 100):3d}%      " + "".join(f"{st.mean([z[3][(c, b, l)] for z in zs]):+10.4f}" for l in LEVELS))
            L.append("    worst DD       " + "".join(f"{'@+' + str(l) + 'R':>10}" for l in LEVELS))
            for b in SHARES[1:]:
                L.append(f"    bank {int(b * 100):3d}%      " + "".join(f"{maxdd([z[3][(c, b, l)] for z in zs]):10.1f}" for l in LEVELS))
    grid(res, "ALL YEARS, both directions")
    # the best cells overall, with the splits
    allc = [k for k in res[0][3] if k != "chk"]
    ranked = sorted(allc, key=lambda k: -st.mean([z[3][k] for z in res]))[:15]
    L.append("\n===== TOP 15 cells (all years) with long / short and 2012-24 / 2025-26 =====")
    nm = lambda k: f"cut {'none' if k[0] is None else '+' + str(k[0])}, " + ("no bank" if k[1] == 0 else f"bank {int(k[1] * 100)}% @+{k[2]}R")
    for k in ranked:
        v = [z[3][k] for z in res]
        sub = lambda f: st.mean([z[3][k] for z in res if f(z)])
        L.append(f"  {nm(k):34} {st.mean(v):+.4f}R/trade (vs raw {st.mean(v) - st.mean(base):+.4f}, t {paired_t(base, v):+.2f})  DD {maxdd(v):6.1f}  total {sum(v):+7.1f} | "
                 f"long {sub(lambda z: z[2]):+.4f} short {sub(lambda z: not z[2]):+.4f} | 2012-24 {sub(lambda z: z[1][:4] < '2025'):+.4f} 2025-26 {sub(lambda z: z[1][:4] >= '2025'):+.4f}")
    L.append("\n===== per symbol, the top 5 cells =====")
    for s in SYMS:
        zs = [z for z in res if z[0] == s]; b0 = [z[3][(None, 0.0, None)] for z in zs]
        top = sorted(allc, key=lambda k: -st.mean([z[3][k] for z in zs]))[:5]
        L.append(f"  {s} (raw {st.mean(b0):+.4f}): " + " | ".join(f"{nm(k)} {st.mean([z[3][k] for z in zs]):+.4f}" for k in top))
    for side, f in (("LONGS", lambda z: z[2]), ("SHORTS", lambda z: not z[2])):
        grid([z for z in res if f(z)], side)
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
