#!/usr/bin/env python3
"""cbloc_v51_limit_entry.py - trader 2026-10-04: draft v5.1 (commodity bloc: AUDUSD, NZDUSD, USDCAD) with a LIMIT ENTRY instead of
the market entry at the signal. The limit sits D R better than the signal price (entry side: ask for longs, bid for shorts) and is
cancelled after H hours unfilled (missed signal = 0R). Fill: the first M1 whose entry-side low (exit-side low + spread) reaches the
limit, at the limit or the M1 open if it gapped through. Stop-first inside the fill minute (conservative).
The filled trade is managed as its own v5.1 trade from the actual fill: same stop price and TP2 price as the signal, R = fill->stop
distance (smaller -> risk-equalised: the same money at risk per trade as the market entry, so more lots), stop to entry at +0.5R,
+1R lock at +1.5R, the 1h / 3.5h checks timed from the fill, avg R since the fill; the 16h cut and the +2R double window stay on
the signal's bar clock. Also: HALF = 50% at market (v5.1) + 50% on the limit, each managed on its own.
'same lots' = the R in the signal's R units (no resize). Kernel = cbloc_v51_cut_grid.kern; the identity transform reproduces v5.1
exactly (checked in the run). SCALED (ask-corrected, live spreads). Holdout symbols untouched.
    python3 pipeline/cbloc_v51_limit_entry.py     -> data/study/cbloc_v51_limit_entry_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
from pipeline.cbloc_v51_cut_grid import kern              # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_v51_limit_entry_report.txt")
DS = [0.1, 0.2, 0.3, 0.4, 0.5]
HS = [1, 2, 4, 8]


def managed(S, F, k0, jf, c, tp2, k6, k16):
    """v5.1 on the frame from minute jf with the price reference moved to c (bid-level of the fill) and R rescaled to 1 + c.
    -> (R in the new R units, scale s)"""
    s = 1.0 + c; sl = slice(jf, None); tr = lambda a: (a[sl] - c) / s
    xo, xh, xl, xc, bo, bh, bc = (tr(F[k]) for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc")); eo = F["eo"][sl] / s
    n = F["n"] - jf; kf = k0 + jf
    kh = lambda H: int(np.searchsorted(S.t, S.t[kf] + int(H * 60))) - 1 - kf
    ht = tp2 is not None; tv = (tp2 - c) / s if ht else 0.0
    r, _ = kern(xo, xh, xl, xc, bo, bh, bc, eo, n, float(eo[0]), ht, tv, k6 - jf if k6 >= 0 else -1, kh(1), kh(3.5),
                k16 - jf if k16 >= jf else -1, 1.5, -1, 0, 0.0)
    return r, s


def do(sym):
    S = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k0 = p["k0"]; tp2 = p["tp2"]; k6 = p["kbar"][6]
        k16 = (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + 4][0]))) - 1 - k0) if p["i0"] + 4 < len(x["bars"]) else -1
        base, _ = managed(S, F, k0, 0, 0.0, tp2, k6, k16)                    # identity transform = v5.1
        ent_lo = F["xl"] + F["spR"]; ent_op = F["bo"] + F["eo"]
        r = {"base": base}
        for H in HS:
            kc = int(np.searchsorted(S.t, S.t[k0] + H * 60)) - 1 - k0
            for D in DS:
                hit = np.nonzero(ent_lo[:kc + 1] <= -D)[0]
                if len(hit) == 0: r[(D, H)] = (None, 0.0, 1.0); continue
                jf = int(hit[0]); f = min(-D, float(ent_op[jf])); c = f - float(F["eo"][jf])
                rr, s = managed(S, F, k0, jf, c, tp2, k6, k16)
                r[(D, H)] = (jf, rr, s)
        out.append(dict(t=x["t"], up=x["up"], sym=sym, strat=x["strat"], r=r))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    from pipeline.cbloc_v51_cut_grid import variants as _cv   # noqa: F401  (kern parity is checked against the Python draft there)
    from pipeline.cbloc_v51_trend_check import v51_py        # noqa: F401
    ref = [z["r"]["base"] for z in res]
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - T0:.0f}s; identity transform: "
         f"{st.mean(ref):+.4f}R/trade, total {sum(ref):+.1f}, DD {maxdd(ref):.1f} (v5.1 = +0.0624 / +108.3 / 12.5)"]
    lim = lambda z, k: z["r"][k][1]                                          # risk-equalised, 0 when missed
    lots = lambda z, k: z["r"][k][1] * z["r"][k][2]                          # same lots (signal R units)
    half = lambda z, k: 0.5 * z["r"]["base"] + 0.5 * z["r"][k][1]
    fill = lambda k: 100 * sum(1 for z in res if z["r"][k][0] is not None) / len(res)
    cell = lambda v: f"{st.mean(v):+.3f}/{maxdd(v):4.1f}/{sum(v) / maxdd(v):5.2f}"
    for title, fn in (("LIMIT ONLY, risk-equalised (cells: R per signal / worst DD / total÷DD; missed = 0)", lim),
                      ("LIMIT ONLY, same lots (signal R units)", lots),
                      ("HALF AT MARKET + HALF ON THE LIMIT (risk-equalised)", half)):
        L.append(f"\n===== {title} =====")
        L.append("  cancel after  " + "".join(f"{'limit -' + str(D) + 'R':>18}" for D in DS))
        for H in HS: L.append(f"  {H:>3}h         " + "".join(f"{cell([fn(z, (D, H)) for z in res]):>18}" for D in DS))
    L.append("\n===== fill rate (share of signals the limit fills) =====")
    for H in HS: L.append(f"  {H:>3}h         " + "".join(f"{fill((D, H)):17.0f}%" for D in DS))
    L.append("\n===== what the MISSED signals would have made under v5.1 (total R; the fast winners show up here) =====")
    for H in HS:
        L.append(f"  {H:>3}h         " + "".join(f"{sum(z['r']['base'] for z in res if z['r'][(D, H)][0] is None):+18.1f}" for D in DS))
    def show(title, fn, keys):
        L.append(f"\n===== {title} (t = paired vs v5.1) =====")
        for k in keys:
            v = [fn(z, k) for z in res]; sub = lambda f: st.mean([fn(z, k) for z in res if f(z)])
            filled = [z["r"][k][1] for z in res if z["r"][k][0] is not None]
            L.append(f"  limit -{k[0]}R, cancel {k[1]}h: {st.mean(v):+.4f}R/signal (vs v5.1 {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})  DD {maxdd(v):5.1f}  "
                     f"tot/DD {sum(v) / maxdd(v):5.2f}  fill {fill(k):.0f}% ({st.mean(filled):+.3f}R per fill) | long {sub(lambda z: z['up']):+.4f} short {sub(lambda z: not z['up']):+.4f} | "
                     f"2012-21 {sub(lambda z: z['t'][:4] < '2022'):+.4f} 2022-26 {sub(lambda z: z['t'][:4] >= '2022'):+.4f} | "
                     + " ".join(f"{s[:6]} {sub(lambda z, s=s: z['sym'] == s):+.3f}" for s in SYMS) + " | "
                     + " ".join(f"{d} {sub(lambda z, d=d: z['strat'] == d):+.3f}" for d in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")))
    keys = [(D, H) for D in DS for H in HS]
    td = lambda fn: (lambda k: -sum(fn(z, k) for z in res) / maxdd([fn(z, k) for z in res]))
    show("LIMIT ONLY (risk-equalised): top 5 by total/DD", lim, sorted(keys, key=td(lim))[:5])
    show("LIMIT ONLY (risk-equalised): top 5 by R per signal", lim, sorted(keys, key=lambda k: -st.mean([lim(z, k) for z in res]))[:5])
    show("HALF + HALF: top 5 by total/DD", half, sorted(keys, key=td(half))[:5])
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
