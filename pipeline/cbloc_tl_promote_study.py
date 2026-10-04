#!/usr/bin/env python3
"""cbloc_tl_promote_study.py - trader 2026-10-04: the simple commodity-bloc rules (T / T+L1.0, cbloc_timestop_study) plus an EARLY
PROMOTE and a NEGATIVE HOLD, on AUDUSD/NZDUSD/USDCAD (IN-SAMPLE only - the holdout AUDCAD/AUDNZD/NZDCAD was spent 2026-10-04).
Base = the live setup (25% at the signal + 75% at the bar-6 close, bank per detector at +1R/TP1 -> stop to entry, +50% pyramid at
+1.5R, shorts' stop to +0.25R at +1.5R).  T = at the bar-6 close, close everything unless the bid high reached +1.5R.  L1.0 = at the
first +1.5R (with the pyramid) every stop to +1R.
  EP X      early promote: before the bar-6 close, the first bid high >= +X R adds the 75% (at X or the M1 open if gapped)
  NEG HOLD  at the bar-6 close a trade whose bid close is below its entry is NOT cut and gets NO add: it stays at 25% and the 75% is
            added when the bid high first reaches +X R (any time later)
  variants  1) T+L1.0 + EP X   2) T+L1.0 + EP X + NEG HOLD (positive-but-slow trades still cut)   3) no cut, L1.0, EP X, NEG HOLD
            (positive trades get the add at bar 6 as live)   ref) live + rule A (24h average < 0 holds the add until +1R)
Per minute: stop -> +1.5R reached flag -> early promote / held release -> bank -> pyramid + stop raise -> TP2 -> bar-6 cut -> bar-6 add.
Kernel = numba; live / T / T+L1.0 reproduce cbloc_timestop_study.run_h exactly (checked). SCALED. Model cost (a LOWER bound).
    python3 pipeline/cbloc_tl_promote_study.py     -> data/study/cbloc_tl_promote_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline import cbloc_timestop_study as C           # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
from pipeline.inverse_study import BANKF                  # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_tl_promote_report.txt")
XS = [0.5, 0.75, 1.0]
# (name, cut 0 none / 1 T / 2 T except negative, raise_to (-1 = live shorts +0.25), ep, neg_hold, gate_avg)
V = [("live", 0, -1.0, 0.0, 0, 0), ("T", 1, -1.0, 0.0, 0, 0), ("T+L1.0", 1, 1.0, 0.0, 0, 0)]
V += [(f"1) T+L1.0 + early promote +{x}", 1, 1.0, x, 0, 0) for x in XS]
V += [(f"2) T+L1.0 + EP +{x} + negative hold", 2, 1.0, x, 1, 0) for x in XS]
V += [(f"3) no cut, L1.0, EP +{x} + negative hold", 0, 1.0, x, 1, 0) for x in XS]
V += [("ref) live + rule A", 0, -1.0, 0.0, 0, 1)]


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, bank_R, has_tp2, tp2, bankf, F0, k_add, up, cut, raise_to, ep, neg_hold, gate_avg,
         TRIG, PYR_R, PYR_F, SR):
    ls = np.zeros(4); le = np.zeros(4); nl = 1; ls[0] = F0; le[0] = e0
    locked = 0.0; stop = -1.0; banked = False; trig = True; reached = False; cum = 0.0
    add_pend = F0 < 1.0 and k_add >= 0 and k_add < n; held = False; rel = ep if ep > 0.0 else 1.0
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            v = locked
            for q in range(nl): v += ls[q] * (r - le[q])
            return v
        if bh[j] >= TRIG: reached = True
        if add_pend and ep > 0.0 and j < k_add and bh[j] >= ep:                       # early promote
            b = bo[j] if bo[j] > ep else ep
            ls[nl] = 1.0 - F0; le[nl] = b + eo[j]; nl += 1; add_pend = False
        if held and bh[j] >= rel:                                                       # held add released
            b = bo[j] if bo[j] > rel else rel
            ls[nl] = 1.0 - F0; le[nl] = b + eo[j]; nl += 1; held = False
        if (not banked) and xh[j] >= bank_R:
            banked = True
            for q in range(nl):
                locked += bankf * ls[q] * (bank_R - le[q]); ls[q] *= (1.0 - bankf)
            stop = e0
        if banked and trig and bh[j] >= PYR_R:
            b = bo[j] if bo[j] > PYR_R else PYR_R
            ls[nl] = PYR_F; le[nl] = b + eo[j]; nl += 1
            if raise_to < 0.0:
                if (not up) and e0 + SR > stop: stop = e0 + SR
            elif e0 + raise_to > stop: stop = e0 + raise_to
            trig = False
        if has_tp2 and xh[j] >= tp2:
            v = locked
            for q in range(nl): v += ls[q] * (tp2 - le[q])
            return v
        if j == k_add:
            neg = xc[j] < e0
            if cut == 1 and not reached:
                v = locked
                for q in range(nl): v += ls[q] * (xc[j] - le[q])
                return v
            if cut == 2 and (not reached) and not neg:
                v = locked
                for q in range(nl): v += ls[q] * (xc[j] - le[q])
                return v
            if add_pend:
                if (neg_hold == 1 and neg) or (gate_avg == 1 and cum / (j + 1) < 0.0):
                    held = True
                else:
                    ls[nl] = 1.0 - F0; le[nl] = bc[j] + eo[j]; nl += 1
                add_pend = False
    v = locked
    for q in range(nl): v += ls[q] * (xc[n - 1] - le[q])
    return v


def do(sym):
    S = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        u = A.setup(S, x, "SCALED")
        if not u: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], "SCALED", u["scale"])
        e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5); tp2 = u["tp2_R"]; ka = u["k_add"] if u["k_add"] is not None else -1
        R = [kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], F["n"], e0, u["bank_R"], tp2 is not None,
                  tp2 if tp2 is not None else 0.0, bankf, A.F0, ka, u["up"], c, r, ep, nh, ga, C.TRIG, A.PYR_R, A.PYR_F, C.K.SR)
             for _, c, r, ep, nh, ga in V]
        par = [C.run_h(F, e0, e0, u["bank_R"], tp2, bankf, A.F0, u["k_add"], u["up"], ts, rt)[0] for _, ts, rt in C.VARIANTS[:2] + [C.VARIANTS[6]]]
        out.append((x["t"], x["up"], sym, x["strat"], R, par))
    return out


POP = []
def main():
    global POP, SYMS, REPORT
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    if "--all6" in sys.argv:                 # trader 2026-10-04: all six bloc symbols. AUDCAD/AUDNZD/NZDCAD's one look was spent the
        from pipeline import cbloc_holdout_look as HL     # same day (T / T+L1.0 / v5.2) - this is a check, not a clean test.
        HL.register_holdout(); hp, _ = HL.population(HL.HOLD)
        POP = POP + hp; SYMS = SYMS + HL.HOLD; REPORT = REPORT.replace(".txt", "_all6.txt")
    assert [v[0] for v in C.VARIANTS[:2] + [C.VARIANTS[6]]] == ["live", "T", "T+L1.0"]
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z[0])
    d = max(abs(z[4][q] - z[5][q]) for z in res for q in range(3))
    col = lambda q: [z[4][q] for z in res]; ref = col(0); tl = col(2)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades ({', '.join(s.split('.')[0] for s in SYMS)}), {time.time() - T0:.0f}s; "
         f"live / T / T+L1.0 vs run_h: max |diff| {d:.1e} ({'EXACT' if d < 1e-9 else 'MISMATCH'})\n"]
    for q, (nm, *_) in enumerate(V):
        v = col(q); sub = lambda f: st.mean([z[4][q] for z in res if f(z)])
        L.append(f"  {nm:40} {st.mean(v):+.4f}R  total {sum(v):+7.1f}  DD {maxdd(v):6.1f}  tot/DD {sum(v) / maxdd(v):+6.2f}"
                 + (f" | vs T+L1.0 {st.mean(v) - st.mean(tl):+.4f} (t {paired_t(tl, v):+.2f})" if q > 2 else " " * 31)
                 + f" | long {sub(lambda z: z[1]):+.4f} short {sub(lambda z: not z[1]):+.4f} | 2012-24 {sub(lambda z: z[0][:4] < '2025'):+.4f} "
                 f"2025-26 {sub(lambda z: z[0][:4] >= '2025'):+.4f} | " + " ".join(f"{s[:6]} {sub(lambda z, s=s: z[2] == s):+.3f}" for s in SYMS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
