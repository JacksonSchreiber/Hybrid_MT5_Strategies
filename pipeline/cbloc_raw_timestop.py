#!/usr/bin/env python3
"""cbloc_raw_timestop.py - trader 2026-10-03: the unlocked commodity-bloc symbols (AUDUSD, NZDUSD, USDCAD), RAW SIGNAL base (100% at
the signal, the signal's stop, out at TP2 - nothing else). One rule: if the bid high has NOT reached +X R (the trade's R) during the first
24 hours (entry -> the close of bar 6 after the signal), close at that close. X = 0.5, 0.75, 1.0, 1.25, 1.5.
Engine = the study engine's raw combo (combo_build kernel, raw settings) + the cut; SCALED (ask-corrected, live spreads).
    python3 pipeline/cbloc_raw_timestop.py      -> data/study/cbloc_raw_timestop_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402

SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
XS = [0.5, 0.75, 1.0, 1.25, 1.5]
REPORT = os.path.join(A.STUDY, "cbloc_raw_timestop_report.txt")


def raw_cut(F, e0, tp2_R, k6, X):
    """raw signal with the 24h cut (X None = no cut) -> (R, why)."""
    xo, xh, xl, xc, bh, n = F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], F["n"]
    peak = -1e9
    for j in range(n):
        if xl[j] <= -1.0: return min(-1.0, xo[j]) - e0, "stop"
        if bh[j] > peak: peak = bh[j]
        if tp2_R is not None and xh[j] >= tp2_R: return tp2_R - e0, "tp2"
        if X is not None and k6 is not None and j == k6 and peak < X: return xc[j] - e0, "cut"
    return xc[n - 1] - e0, "end"


def do_symbol(sym):
    S = CB.sym_obj(sym); combos = CB.grid(); raw_i = CB.find(combos, "off", 0, "off", "off", "off", "off", "off"); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S, x)
        if p is None: continue
        R, _, _ = CB.run_trade(p, [combos[raw_i]])                               # the study engine's raw combo
        k6 = p["kbar"][6] if p["kbar"][6] >= 0 else None
        r = {"raw": raw_cut(p["F"], p["e0"], p["tp2"], k6, None)[0], "engine": float(R[0])}
        for X in XS:
            v, why = raw_cut(p["F"], p["e0"], p["tp2"], k6, X); r[X] = v; r[f"{X}:why"] = why
        out.append((sym, x["t"], x["up"], r))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = [x for x in CB.population() if x["sym"] in SYMS]
    with Pool(3) as pool: res = sorted([z for rs in pool.map(do_symbol, SYMS) for z in rs], key=lambda z: z[1])
    d = max(abs(z[3]["raw"] - z[3]["engine"]) for z in res)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - T0:.0f}s; raw base vs the study engine's raw combo: "
         f"max |diff| {d:.1e} ({'EXACT' if d < 1e-12 else 'MISMATCH'})\n"]
    def block(title, zs):
        if len(zs) < 10: return
        b = [z[3]["raw"] for z in zs]
        L.append(f"{title}  n={len(zs)}  raw {st.mean(b):+.4f}R/trade  total {sum(b):+.1f}  DD {maxdd(b):.1f}  win {100 * sum(v > 0 for v in b) / len(b):.0f}%")
        for X in XS:
            v = [z[3][X] for z in zs]; cut = sum(1 for z in zs if z[3][f"{X}:why"] == "cut")
            L.append(f"   cut if not +{X:<4}R in 24h: {st.mean(v):+.4f}R/trade (vs raw {st.mean(v) - st.mean(b):+.4f}, t {paired_t(b, v):+.2f})  total {sum(v):+7.1f}  "
                     f"DD {maxdd(v):6.1f}  win {100 * sum(q > 0 for q in v) / len(v):.0f}%  | cut {cut} ({100 * cut / len(zs):.0f}%)")
    for per, f in (("ALL YEARS", lambda z: True), ("2012-24", lambda z: z[1][:4] < "2025"), ("2025-26", lambda z: z[1][:4] >= "2025")):
        L.append(f"===== {per} =====")
        for side, sf in (("all  ", lambda z: True), ("long ", lambda z: z[2]), ("short", lambda z: not z[2])):
            block(f"{side}", [z for z in res if f(z) and sf(z)])
        L.append("")
    L.append("===== by symbol, all years =====")
    for s in SYMS: block(s, [z for z in res if z[0] == s])
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
