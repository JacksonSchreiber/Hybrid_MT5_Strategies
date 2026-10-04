#!/usr/bin/env python3
"""cbloc_v3_atr.py - trader 2026-10-04: draft v3 for the commodity bloc (AUDUSD, NZDUSD, USDCAD) - ATR of winners vs losers.
v3 = raw signal; trial 25%; stop to entry at the first +0.5R (no bank); the 75% promoted at +0.75R inside 24h; 24h cut at +1.5R (the add
otherwise at the bar-6 close); every stop to +1R at +1.5R; +100% of full size at +2R inside 24h; TP2. SCALED, full-position R.
Volatility at the signal (H4 bars up to and including the signal bar):
  atr_pct  ATR14 / price x100      stop_atr  the signal's stop distance / ATR14      atr_regime  ATR14 / ATR100 (>1 = expanding)
Winner R > +0.05, loser R < -0.05, scratch in between.  -> data/study/cbloc_v3_atr_report.txt"""
import os, sys, statistics as st, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd                # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_v3_atr_report.txt")


def v3(F, e0, tp2, k6):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    F0 = 0.25; legs = [[F0, e0]]; stop = -1.0; hit = False; moved = False; peak = -1e9; added = False; dd = False
    lv = min(0.5, tp2) if tp2 is not None else 0.5
    val = lambda r: sum(s * (r - e) for s, e in legs)
    for j in range(n):
        if xl[j] <= stop: return val(min(stop, xo[j]))
        if bh[j] > peak: peak = bh[j]
        if not hit and xh[j] >= lv: hit = True; stop = max(stop, e0)
        if not added and k6 >= 0 and j < k6 and bh[j] >= 0.75: legs.append([1 - F0, max(0.75, bo[j]) + eo[j]]); added = True
        if not moved and bh[j] >= 1.5: moved = True; stop = max(stop, e0 + 1.0)
        if not dd and k6 >= 0 and j < k6 and bh[j] >= 2.0: legs.append([1.0, max(2.0, bo[j]) + eo[j]]); dd = True
        if tp2 is not None and xh[j] >= tp2: return val(tp2)
        if k6 >= 0 and j == k6:
            if peak < 1.5: return val(xc[j])
            if not added: legs.append([1 - F0, bc[j] + eo[j]]); added = True
    return val(xc[n - 1])


def vol(x):
    h4, i = x["bars"], x["i0"] - 1                              # the signal bar
    if i < 101: return None
    tr = lambda k: max(h4[k][2] - h4[k][3], abs(h4[k][2] - h4[k - 1][4]), abs(h4[k][3] - h4[k - 1][4]))
    a14 = st.mean(tr(k) for k in range(i - 13, i + 1)); a100 = st.mean(tr(k) for k in range(i - 99, i + 1))
    return {"atr_pct": 100 * a14 / h4[i][4], "stop_atr": abs(x["e"] - x["s"]) / a14, "atr_regime": a14 / a100}


def do(sym):
    S = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x); v = vol(x)
        if p is None or v is None: continue
        out.append(dict(v, R=v3(p["F"], p["e0"], p["tp2"], p["kbar"][6]), up=x["up"], sym=sym, t=x["t"]))
    return out


POP = []
def main():
    global POP
    POP = [x for x in CB.population() if x["sym"] in SYMS]; t0 = time.time()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s; v3 {st.mean(z['R'] for z in res):+.4f}R/trade"]
    grp = lambda z: "winner" if z["R"] > 0.05 else ("loser" if z["R"] < -0.05 else "scratch")
    for side, sf in (("ALL", lambda z: True), ("LONG", lambda z: z["up"]), ("SHORT", lambda z: not z["up"])):
        zs = [z for z in res if sf(z)]
        L.append(f"\n===== {side} (n={len(zs)}) - median (and mean) volatility at the signal =====")
        L.append(f"  {'':8} {'n':>5} {'avg R':>7} | {'ATR % of price':>18} | {'stop in ATRs':>16} | {'ATR14/ATR100':>16}")
        for g in ("winner", "scratch", "loser"):
            q = [z for z in zs if grp(z) == g]
            if not q: continue
            m = lambda k: f"{st.median(z[k] for z in q):.3f} ({st.mean(z[k] for z in q):.3f})"
            L.append(f"  {g:8} {len(q):5d} {st.mean(z['R'] for z in q):+7.2f} | {m('atr_pct'):>18} | {m('stop_atr'):>16} | {m('atr_regime'):>16}")
        for k, nm in (("atr_pct", "ATR % of price"), ("stop_atr", "stop distance in ATRs"), ("atr_regime", "ATR14 / ATR100 (volatility expanding >1)")):
            qs = np.quantile([z[k] for z in zs], [0.2, 0.4, 0.6, 0.8])
            L.append(f"  v3 by {nm} quintile:")
            for b in range(5):
                lo = -1e9 if b == 0 else qs[b - 1]; hi = 1e9 if b == 4 else qs[b]
                q = [z for z in zs if lo <= z[k] < hi]
                L.append(f"     Q{b + 1} ({'<' + format(qs[0], '.3f') if b == 0 else ('>=' + format(qs[3], '.3f') if b == 4 else format(lo, '.3f') + '-' + format(hi, '.3f')):>13}): n={len(q):4d}  "
                         f"v3 {st.mean(z['R'] for z in q):+.4f}R  total {sum(z['R'] for z in q):+6.1f}  winners {100 * sum(1 for z in q if grp(z) == 'winner') / len(q):3.0f}%  losers {100 * sum(1 for z in q if grp(z) == 'loser') / len(q):3.0f}%")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
