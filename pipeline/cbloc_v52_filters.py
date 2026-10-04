#!/usr/bin/env python3
"""cbloc_v52_filters.py - trader 2026-10-04: commodity bloc (AUDUSD, NZDUSD, USDCAD) draft v5.2 + four simple signal filters.
v5.2 = v5.1 with the stop to entry at +0.5R for longs and +0.3R for shorts: 100% at the signal; 1h check (avg < -0.1 -> sell); 3.5h
check (avg < -0.15 -> sell); 16h cut (bar-4 close: avg < +1.5 -> sell); stop +1R at +1.5R; +100% at +2R before the bar-6 close; TP2.
  1 SPREAD/STOP   spread at entry as a share of the stop distance (spR at the first M1); skip when above a cut
  2 TIME OF DAY   the entry H4 bar's open hour, UTC (the study data's clock: week opens Sun 20:00): 20 / 00 / 04 / 08 / 12 / 16
  3 OVERLAP       AUD/NZD: a new AUDUSD (NZDUSD) signal while a same-direction NZDUSD (AUDUSD) trade is open -> skip / half size.
                  USD (all three): a new signal while any open trade on another symbol has the same USD direction -> skip / half
  4 MOMENTUM      signed ER20 (efficiency ratio over the 20 H4 bars to the signal bar, + = moving in the trade's direction)
For each filter: the trades it skips (n, mean R, t vs 0) and the taken set (R/trade, total, DD, total/DD) with long/short, periods.
Kernel = numba; longs+0.5/shorts+0.5 reproduces v5.1 exactly (checked). SCALED (ask-corrected, live spreads). Holdout untouched.
    python3 pipeline/cbloc_v52_filters.py     -> data/study/cbloc_v52_filters_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time, math
import numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd                # noqa: E402
from pipeline.cbloc_v51_trend_check import v51_py         # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_v52_filters_report.txt")
BE_LONG, BE_SHORT = 0.5, 0.3


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, has_tp2, tp2, k6, k16, kE, k35, be):
    s1 = 0.0; e1 = 0.0; stop = -1.0; hit = False; moved = False; dd = False; cum = 0.0
    lv = be
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
        if has_tp2 and xh[j] >= tp2: return (tp2 - e0) + s1 * (tp2 - e1), j
        if kE >= 0 and j == kE and cum / (j + 1) < -0.1: return (xc[j] - e0) + s1 * (xc[j] - e1), j
        if k35 >= 0 and j == k35 and cum / (j + 1) < -0.15: return (xc[j] - e0) + s1 * (xc[j] - e1), j
        if k16 >= 0 and j == k16 and cum / (j + 1) < 1.5: return (xc[j] - e0) + s1 * (xc[j] - e1), j
    return (xc[n - 1] - e0) + s1 * (xc[n - 1] - e1), n - 1


def ser20(x):
    c = [b[4] for b in x["bars"][max(0, x["i0"] - 21):x["i0"]]]
    if len(c) < 21: return None
    path = sum(abs(c[k] - c[k - 1]) for k in range(1, 21))
    return (1.0 if x["up"] else -1.0) * (c[-1] - c[0]) / path if path > 0 else 0.0


def do(sym):
    S = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        F = p["F"]; k0 = p["k0"]
        kb = lambda N: (int(np.searchsorted(S.t, A.tmin(x["bars"][p["i0"] + N][0]))) - 1 - k0) if p["i0"] + N < len(x["bars"]) else -1
        kh = lambda H: int(np.searchsorted(S.t, S.t[k0] + int(H * 60))) - 1 - k0
        k16, kE, k35, k6 = kb(4), kh(1), kh(3.5), p["kbar"][6]
        tp2 = p["tp2"]; ht = tp2 is not None; tv = tp2 if ht else 0.0
        a = (F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], ht, tv, k6, k16, kE, k35)
        r52, j = kern(*a, BE_LONG if x["up"] else BE_SHORT)
        r51, _ = kern(*a, 0.5)
        tin = int(S.t[k0]); tout = int(S.t[k0 + j])
        out.append(dict(t=x["t"], up=x["up"], sym=sym, strat=x["strat"], R=r52, r51=r51, par=v51_py(F, p["e0"], tp2, k6, k16, kE, k35),
                        tin=tin, tout=tout, sp=float(F["spR"][0]), hr=int((A.tmin(x["bars"][p["i0"]][0]) // 60) % 24), ser=ser20(x)))
    return out


def tstat(v):
    if len(v) < 3: return float("nan")
    s = st.stdev(v); return st.mean(v) / (s / math.sqrt(len(v))) if s > 0 else float("nan")


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z["tin"])
    d = max(abs(z["r51"] - z["par"]) for z in res)
    base = [z["R"] for z in res]
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - T0:.0f}s; longs+0.5/shorts+0.5 vs the Python v5.1 draft: "
         f"max |diff| {d:.1e} ({'EXACT' if d < 1e-9 else 'MISMATCH'})",
         f"v5.2: {st.mean(base):+.4f}R/trade  total {sum(base):+.1f}  DD {maxdd(base):.1f}  total/DD {sum(base) / maxdd(base):.2f}  "
         f"| long {st.mean([z['R'] for z in res if z['up']]):+.4f} short {st.mean([z['R'] for z in res if not z['up']]):+.4f}"]

    def taken(nm, w):
        """w = weight per trade (1 take, 0.5 half, 0 skip), aligned with res"""
        v = [z["R"] * wi for z, wi in zip(res, w)]; kept = [z for z, wi in zip(res, w) if wi > 0]
        sk = [z["R"] for z, wi in zip(res, w) if wi == 0]; hf = [z["R"] for z, wi in zip(res, w) if 0 < wi < 1]
        sub = lambda f: (sum(z["R"] * wi for z, wi in zip(res, w) if f(z)) / max(1, sum(1 for z, wi in zip(res, w) if f(z) and wi > 0)))
        skipped = f"skips {len(sk):4d} (mean {st.mean(sk):+.3f}, t {tstat(sk):+.2f})" if sk else "skips    0" + " " * 22
        half = f" half {len(hf)} (mean {st.mean(hf):+.3f})" if hf else ""
        L.append(f"  {nm:34} {skipped}{half} | taken {len(kept):4d}: {sum(v) / len(kept):+.4f}R/trade  total {sum(v):+6.1f}  DD {maxdd(v):5.1f}  "
                 f"tot/DD {sum(v) / maxdd(v):5.2f} | long {sub(lambda z: z['up']):+.4f} short {sub(lambda z: not z['up']):+.4f} | "
                 f"2012-21 {sub(lambda z: z['t'][:4] < '2022'):+.4f} 2022-26 {sub(lambda z: z['t'][:4] >= '2022'):+.4f}")

    def fifths(key, title):
        L.append(f"  by fifths of {title} (low -> high), mean R:")
        for side, f in (("all", lambda z: True), ("long", lambda z: z["up"]), ("short", lambda z: not z["up"])):
            zs = sorted([z for z in res if f(z) and z[key] is not None], key=lambda z: z[key]); g = np.array_split(np.arange(len(zs)), 5)
            cells = [f"{st.mean([zs[i]['R'] for i in ix]):+.3f}" for ix in g]
            cuts = [zs[ix[-1]][key] for ix in g[:4]]                                          # rank fifths (ties split by rank)
            L.append(f"     {side:5} cuts {', '.join(format(v, '.3f') for v in cuts):30} | " + "  ".join(cells))

    L.append("\n===== 1) SPREAD / STOP at entry =====")
    sps = [z["sp"] for z in res]
    L.append(f"  spread as a share of the stop: median {100 * np.median(sps):.1f}%, 80th pct {100 * np.percentile(sps, 80):.1f}%, 95th {100 * np.percentile(sps, 95):.1f}%")
    fifths("sp", "spread/stop")
    for pct in (95, 90, 80, 70):
        cut = np.percentile(sps, pct); taken(f"skip spread/stop > {100 * cut:.1f}% (top {100 - pct}%)", [0 if z["sp"] > cut else 1 for z in res])

    L.append("\n===== 2) TIME OF DAY (entry H4 bar open, UTC) =====")
    names = {20: "20-24 NY close/Sydney", 0: "00-04 Tokyo", 4: "04-08 Tokyo/pre-London", 8: "08-12 London", 12: "12-16 London/NY", 16: "16-20 NY afternoon"}
    for h in (20, 0, 4, 8, 12, 16):
        zs = [z for z in res if z["hr"] == h]
        if not zs: continue
        lg = [z["R"] for z in zs if z["up"]]; sh = [z["R"] for z in zs if not z["up"]]; v = [z["R"] for z in zs]
        L.append(f"  {names[h]:24} n {len(zs):4d}  {st.mean(v):+.4f}R (t {tstat(v):+.2f})  total {sum(v):+6.1f} | long {st.mean(lg):+.4f} (n {len(lg)})  short {st.mean(sh):+.4f} (n {len(sh)}) | "
                 f"2012-21 {st.mean([z['R'] for z in zs if z['t'][:4] < '2022']):+.4f} 2022-26 {st.mean([z['R'] for z in zs if z['t'][:4] >= '2022']):+.4f}")
    for nm, hs in (("skip 20-04 UTC (NY close + early Asia)", (20, 0)), ("skip 16-24 UTC (NY afternoon + close)", (16, 20)),
                   ("only 00-12 UTC (Asia + London)", (12, 16, 20)), ("only 08-20 UTC (London + NY)", (20, 0, 4))):
        taken(nm, [0 if z["hr"] in hs else 1 for z in res])

    L.append("\n===== 3) OVERLAP =====")
    usd = lambda z: (-1 if z["up"] else 1) if z["sym"] != "USDCAD.dk" else (1 if z["up"] else -1)      # +1 = long USD
    def overlap_w(scope, mode):
        w = []; open_ = []                                                                              # (tout, sym, usd dir, up)
        for z in res:
            open_ = [o for o in open_ if o[0] > z["tin"]]
            if scope == "audnzd":
                hit = z["sym"] in ("AUDUSD.dk", "NZDUSD.dk") and any(o[1] in ("AUDUSD.dk", "NZDUSD.dk") and o[1] != z["sym"] and o[3] == z["up"] for o in open_)
            else:
                hit = any(o[1] != z["sym"] and o[2] == usd(z) for o in open_)
            wi = (0.0 if mode == "skip" else 0.5) if hit else 1.0
            w.append(wi)
            if wi > 0: open_.append((z["tout"], z["sym"], usd(z), z["up"]))
        return w
    taken("AUD/NZD same direction open: skip", overlap_w("audnzd", "skip"))
    taken("AUD/NZD same direction open: half", overlap_w("audnzd", "half"))
    taken("same USD direction open (all 3): skip", overlap_w("usd", "skip"))
    taken("same USD direction open (all 3): half", overlap_w("usd", "half"))

    L.append("\n===== 4) MOMENTUM: signed ER20 (+ = market already moving the trade's way) =====")
    fifths("ser", "signed ER20")
    for thr in (-0.1, 0.0, 0.1):
        taken(f"skip signed ER20 < {thr:+.1f}", [0 if (z["ser"] is not None and z["ser"] < thr) else 1 for z in res])
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
