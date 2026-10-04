#!/usr/bin/env python3
"""cbloc_timeraise_study.py - trader 2026-10-04: a simple stop raise over time on the USD commodity-bloc symbols (AUDUSD, NZDUSD,
USDCAD), run on the RAW signal and on the current live setup for the bloc (live stack + T+L1.0).
  TIME RAISE  at the bar-3 close (0.5 day) the stop goes to entry, at the bar-6 close (1 day) to +0.25R, at the bar-18 close (3 days)
              to +0.5R - tighten only (a stop already higher, e.g. after the bank or the bloc lock, stays).
              "close" = when the price is already at/through the new stop at that bar close, the trade is closed there (market)
              "skip"  = then that raise is skipped and the stop stays where it was (the next step tries again)
  RAW         100% at the signal, the signal's stop, out at TP2 / the stop / the window end
  LIVE (bloc) 25% at the signal + 75% at the bar-6 close, bank per detector at +1R/TP1 -> stop to entry, +50% pyramid at +1.5R,
              T+L1.0: at the bar-6 close everything is closed unless the bid high reached +1.5R; at +1.5R every stop to +1R
Per minute: stop -> +1.5R flag -> bank -> pyramid + lock -> TP2 -> bloc 24h cut -> time raise -> staged add.
Kernel = numba; with the time raise off it reproduces cbloc_timestop_study.run_h (live, T+L1.0) and the raw replay exactly (checked).
SCALED (model cost = the M1 bar minimum spread, a LOWER bound - real FX costs are higher).
    python3 pipeline/cbloc_timeraise_study.py     -> data/study/cbloc_timeraise_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline import cbloc_timestop_study as CT          # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_timeraise_report.txt")
BARS = (3, 6, 18); LVLS = np.array([0.0, 0.25, 0.5])
# (name, setup 0 raw / 1 live bloc / 2 live standard (check only), time raise 0 off / 1 close / 2 skip)
V = [("raw", 0, 0), ("raw + time raise (close)", 0, 1), ("raw + time raise (skip)", 0, 2),
     ("live (bloc T+L1.0)", 1, 0), ("live + time raise (close)", 1, 1), ("live + time raise (skip)", 1, 2), ("_live standard", 2, 0)]
KIND = {0: "stop", 1: "TP2", 2: "24h cut", 3: "window end", 10: "closed at 12h (below entry)", 11: "closed at 1 day (below +0.25R)",
        12: "closed at 3 days (below +0.5R)"}


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, has_tp2, tp2_R, bank_on, bank_R, bankf, F0, k_add, up, pyr_on, pyr_R, pyr_f,
         tstop, rmode, rto, sr, trig, rk, rl, tr):
    ls = np.zeros(4); le = np.zeros(4); nl = 1; ls[0] = F0; le[0] = e0
    locked = 0.0; stop = -1.0; banked = False; trigp = True; reached = False; q = 0; rstop = -1
    add_pend = F0 < 1.0 and k_add >= 0 and k_add < n
    for j in range(n):
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            v = locked
            for a in range(nl): v += ls[a] * (r - le[a])
            return v, 0, rstop
        if bh[j] >= trig: reached = True
        if bank_on and (not banked) and xh[j] >= bank_R:
            banked = True; tot = 0.0
            for a in range(nl): tot += ls[a] * (bank_R - le[a])
            locked += bankf * tot
            for a in range(nl): ls[a] = ls[a] * (1 - bankf)
            if be > stop: stop = be; rstop = -1
        if pyr_on and banked and trigp and bh[j] >= pyr_R:
            ls[nl] = pyr_f; le[nl] = (pyr_R if pyr_R > bo[j] else bo[j]) + eo[j]; nl += 1
            if rmode == 1:
                if not up and be + sr > stop: stop = be + sr; rstop = -1
            elif rmode == 2:
                if be + rto > stop: stop = be + rto; rstop = -1
            trigp = False
        if has_tp2 and xh[j] >= tp2_R:
            v = locked
            for a in range(nl): v += ls[a] * (tp2_R - le[a])
            return v, 1, rstop
        if tstop and k_add >= 0 and j == k_add and not reached:
            v = locked
            for a in range(nl): v += ls[a] * (xc[j] - le[a])
            return v, 2, rstop
        if tr > 0 and q < 3 and j == rk[q]:
            lvl = be + rl[q]
            if lvl > stop:
                if xc[j] <= lvl:
                    if tr == 1:
                        v = locked
                        for a in range(nl): v += ls[a] * (xc[j] - le[a])
                        return v, 10 + q, rstop
                else:
                    stop = lvl; rstop = q
            q += 1
        if add_pend and j == k_add:
            ls[nl] = 1 - F0; le[nl] = bc[j] + eo[j]; nl += 1; add_pend = False
    v = locked
    for a in range(nl): v += ls[a] * (xc[n - 1] - le[a])
    return v, 3, rstop


def do(sym):
    S = CB.sym_obj(sym); out = []; dmax = 0.0
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; h4, i0, k0 = x["bars"], p["i0"], p["k0"]
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(h4[i0 + N][0]))) - 1 - k0) if i0 + N < len(h4) else -1
        rk = np.array([kb(N) for N in BARS], dtype=np.int64)
        assert rk[0] == p["kbar"][3] and rk[1] == p["kbar"][6]
        ka = p["kbar"][6]; tp2 = p["tp2"]; R = {}
        for name, su, tr in V:
            live = su > 0
            R[name] = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], p["be"], tp2 is not None,
                           tp2 if tp2 is not None else 0.0, live, p["bank_R"](1.0), p["bankf_det"], A.F0 if live else 1.0, ka if live else -1,
                           p["up"], live, A.PYR_R, A.PYR_F, su == 1, 2 if su == 1 else 1, CB.BLOC_LOCK, CT.K.SR, CT.TRIG, rk, LVLS, tr)
        # exactness: time raise off == run_h (live bloc / live standard) and the raw replay
        k_add = ka if ka >= 0 else None
        for name, ts, rt in (("live (bloc T+L1.0)", 1, CB.BLOC_LOCK), ("_live standard", None, None)):
            v = CT.run_h(F, p["e0"], p["be"], p["bank_R"](1.0), tp2, p["bankf_det"], A.F0, k_add, p["up"], ts, rt)[0]
            dmax = max(dmax, abs(R[name][0] - v))
        xl, xh, xo, xc = F["xl"], F["xh"], F["xo"], F["xc"]
        hit = np.nonzero((xl <= -1.0) | ((xh >= tp2) if tp2 is not None else np.zeros(p["n"], bool)))[0]
        raw = ((min(-1.0, xo[hit[0]]) if xl[hit[0]] <= -1.0 else tp2) if hit.size else xc[p["n"] - 1]) - p["e0"]
        dmax = max(dmax, abs(R["raw"][0] - raw))
        out.append((x["t"], x["up"], sym, x["strat"], R))
    return out, dmax


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: rr = pool.map(do, SYMS)
    res = sorted([z for r, _ in rr for z in r], key=lambda z: z[0]); dmax = max(d for _, d in rr)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades ({', '.join(s.split('.')[0] for s in SYMS)}), "
         f"{time.time() - T0:.0f}s; time raise off vs run_h (live bloc, live standard) and the raw replay: max |diff| {dmax:.1e} "
         f"({'EXACT' if dmax < 1e-12 else 'MISMATCH'})\n"]
    col = lambda nm, f=lambda z: True: [z[4][nm][0] for z in res if f(z)]
    for base, names in (("raw", V[1:3]), ("live (bloc T+L1.0)", V[4:6])):
        b = col(base)
        L.append(f"  {base:28} {st.mean(b):+.4f}R  total {sum(b):+7.1f}  DD {maxdd(b):6.1f}  tot/DD {sum(b) / maxdd(b):+6.2f}" + " " * 25 +
                 f"| long {st.mean(col(base, lambda z: z[1])):+.4f} short {st.mean(col(base, lambda z: not z[1])):+.4f} | "
                 f"2012-24 {st.mean(col(base, lambda z: z[0][:4] < '2025')):+.4f} 2025-26 {st.mean(col(base, lambda z: z[0][:4] >= '2025')):+.4f} | "
                 + " ".join(f"{s[:6]} {st.mean(col(base, lambda z, s=s: z[2] == s)):+.3f}" for s in SYMS))
        for nm, *_ in names:
            v = col(nm)
            L.append(f"  {nm:28} {st.mean(v):+.4f}R  total {sum(v):+7.1f}  DD {maxdd(v):6.1f}  tot/DD {sum(v) / maxdd(v):+6.2f}"
                     f" | vs base {st.mean(v) - st.mean(b):+.4f} (t {paired_t(b, v):+.2f})"
                     f" | long {st.mean(col(nm, lambda z: z[1])):+.4f} short {st.mean(col(nm, lambda z: not z[1])):+.4f} | "
                     f"2012-24 {st.mean(col(nm, lambda z: z[0][:4] < '2025')):+.4f} 2025-26 {st.mean(col(nm, lambda z: z[0][:4] >= '2025')):+.4f} | "
                     + " ".join(f"{s[:6]} {st.mean(col(nm, lambda z, s=s: z[2] == s)):+.3f}" for s in SYMS))
        L.append("")
    L.append("How the trades ended (time raise 'close'), with the base result of the same trades:")
    for base, nm in (("raw", "raw + time raise (close)"), ("live (bloc T+L1.0)", "live + time raise (close)")):
        L.append(f"  {nm}:")
        for k in sorted({z[4][nm][1] for z in res}):
            zs = [z for z in res if z[4][nm][1] == k]
            for side, sf in (("long ", lambda z: z[1]), ("short", lambda z: not z[1])):
                ss = [z for z in zs if sf(z)]
                if not ss: continue
                L.append(f"    {KIND[k]:30} {side} n {len(ss):4}  avg {st.mean(z[4][nm][0] for z in ss):+.3f}R  (base {st.mean(z[4][base][0] for z in ss):+.3f}R)")
        stp = [z for z in res if z[4][nm][1] == 0 and z[4][nm][2] >= 0]
        for q in range(3):
            ss = [z for z in stp if z[4][nm][2] == q]
            if ss: L.append(f"    stopped on the raised stop ({['entry', '+0.25R', '+0.5R'][q]:6}) n {len(ss):4}  avg {st.mean(z[4][nm][0] for z in ss):+.3f}R  (base {st.mean(z[4][base][0] for z in ss):+.3f}R)")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
