#!/usr/bin/env python3
"""cbloc_v51_direction.py - trader 2026-10-04: draft v5.1 for the commodity bloc (AUDUSD, NZDUSD, USDCAD) - how directional was the market
at the signal? Measures on the H4 bars up to and including the signal bar:
  er20 / er50  Kaufman efficiency ratio = |net change| / sum of |bar-to-bar changes| over 20 / 50 bars (1 = straight line, 0 = chop)
  ser20        the same, signed by the trade: + = the market had been moving IN the trade's direction, - = against it
  adx14        Wilder's ADX(14) (trend strength, direction-blind)
v5.1 = 100% at the signal; 1h check (sell if avg R < -0.1); 3.5h check (sell if avg R < -0.15); stop to entry at +0.5R; 16h cut (sell if
avg R < +1.5); stop +1R at +1.5R; +100% at +2R inside 24h; TP2.  -> data/study/cbloc_v51_direction_report.txt"""
import os, sys, statistics as st, collections
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd                # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]; REPORT = os.path.join(A.STUDY, "cbloc_v51_direction_report.txt")


def v51(F, e0, tp2, k6, k16, kE, k2, tmin):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[1.0, e0]]; stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0; peak = -1e9
    val = lambda r: sum(s * (r - e) for s, e in legs)
    lv = min(0.5, tp2) if tp2 is not None else 0.5
    out = lambda r, kind, j: (val(r), kind, dd, (tmin[j] - tmin[0]) / 60.0, peak)
    for j in range(n):
        cum += bc[j]
        if xl[j] <= stop: return out(min(stop, xo[j]), "stop -1R" if stop == -1.0 else ("stop at entry" if stop == e0 else "stop at +1R"), j)
        if bh[j] > peak: peak = bh[j]
        if not hit and xh[j] >= lv: hit = True; stop = max(stop, e0)
        if not moved and bh[j] >= 1.5: moved = True; stop = max(stop, e0 + 1.0)
        if not dd and k6 >= 0 and j < k6 and bh[j] >= 2.0: legs.append([1.0, max(2.0, bo[j]) + eo[j]]); dd = True
        if tp2 is not None and xh[j] >= tp2: return out(tp2, "TP2", j)
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1: return out(xc[j], "1h check sell", j)
        if k2 >= 0 and j == k2 and cum / (j + 1) < -0.15: return out(xc[j], "3.5h check sell", j)
        if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5: return out(xc[j], "16h cut", j)
    return out(xc[n - 1], "window end", n - 1)


def direction(x):
    h4, i = x["bars"], x["i0"] - 1
    if i < 160: return None
    c = np.array([b[4] for b in h4[i - 159:i + 1]]); h = np.array([b[2] for b in h4[i - 159:i + 1]]); l = np.array([b[3] for b in h4[i - 159:i + 1]])
    er = lambda w: abs(c[-1] - c[-1 - w]) / max(np.abs(np.diff(c[-1 - w:])).sum(), 1e-12)
    sg = 1.0 if x["up"] else -1.0
    ser20 = sg * (c[-1] - c[-21]) / max(np.abs(np.diff(c[-21:])).sum(), 1e-12)
    # Wilder ADX(14)
    up_m = h[1:] - h[:-1]; dn_m = l[:-1] - l[1:]
    pdm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0); ndm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    def rma(a, n=14):
        out = np.empty_like(a); out[n - 1] = a[:n].sum()
        for k in range(n, len(a)): out[k] = out[k - 1] - out[k - 1] / n + a[k]
        return out[n - 1:]
    atr, p, m = rma(tr), rma(pdm), rma(ndm)
    pdi, ndi = 100 * p / atr, 100 * m / atr; dx = 100 * np.abs(pdi - ndi) / np.maximum(pdi + ndi, 1e-12)
    adx = dx[:14].mean()
    for v in dx[14:]: adx = (adx * 13 + v) / 14
    return {"er20": er(20), "er50": er(50), "ser20": ser20, "adx14": adx}


def do(sym):
    S = CB.sym_obj(sym); o = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x); d = direction(x)
        if p is None or d is None: continue
        k0 = p["k0"]; k16 = (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + 4][0]))) - 1 - k0) if p["i0"] + 4 < len(x["bars"]) else -1
        kh = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
        R, kind, dd, hrs, peak = v51(p["F"], p["e0"], p["tp2"], p["kbar"][6], k16, kh(1), kh(3.5), S.t[k0:k0 + p["n"]])
        o.append(dict(d, R=R, kind=kind + (" + doubled" if dd else ""), hrs=hrs, peak=peak, up=x["up"], t=x["t"], strat=x["strat"]))
    return o


POP = []
def main():
    global POP
    POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["t"])
    R = [z["R"] for z in res]
    L = [__doc__, f"v5.1: {len(res)} trades (signals with 160+ H4 bars of history), {st.mean(R):+.4f}R/trade, total {sum(R):+.1f}R, DD {maxdd(R):.1f}"]
    L.append(f"medians, all trades: ER20 {st.median(z['er20'] for z in res):.3f}  ER50 {st.median(z['er50'] for z in res):.3f}  "
             f"signed ER20 {st.median(z['ser20'] for z in res):+.3f}  ADX14 {st.median(z['adx14'] for z in res):.1f}\n")
    L.append("| How the trade ended | Trades | Avg R | Total R | Median hours | Median ER20 | Median ER50 | Median signed ER20 | Median ADX14 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for z in res: g[z["kind"]].append(z)
    for k, zs in sorted(g.items(), key=lambda kv: sum(z["R"] for z in kv[1])):
        m = lambda f: st.median(z[f] for z in zs)
        L.append(f"| {k} | {len(zs)} ({100 * len(zs) / len(res):.1f}%) | {st.mean(z['R'] for z in zs):+.2f} | {sum(z['R'] for z in zs):+.1f} | {m('hrs'):.1f} | "
                 f"{m('er20'):.3f} | {m('er50'):.3f} | {m('ser20'):+.3f} | {m('adx14'):.1f} |")
    for side, sf in (("ALL", lambda z: True), ("LONG", lambda z: z["up"]), ("SHORT", lambda z: not z["up"])):
        zs = [z for z in res if sf(z)]
        L.append(f"\n===== {side} (n={len(zs)}): v5.1 R per trade by fifths of each measure (low -> high) =====")
        for k, nm in (("er20", "ER20"), ("er50", "ER50"), ("ser20", "signed ER20 (- against / + with the trade)"), ("adx14", "ADX14")):
            qs = np.quantile([z[k] for z in zs], [0.2, 0.4, 0.6, 0.8]); cells = []
            for b in range(5):
                lo = -1e9 if b == 0 else qs[b - 1]; hi = 1e9 if b == 4 else qs[b]
                q = [z["R"] for z in zs if lo <= z[k] < hi]
                cells.append(f"Q{b + 1} {st.mean(q):+.3f} (n {len(q)})")
            L.append(f"  {nm:44} cuts {', '.join(format(v, '.3f') for v in qs)} | " + " | ".join(cells))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
