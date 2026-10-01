#!/usr/bin/env python3
"""pyramid_sell_study.py - trader 2026-10-01: when the +1.5R pyramid fires, also SELL part of the original position?

Base = the live stack on the blind TrendCont record: staged entry (25% at the signal, +75% at the close of H4 bar 6 if still
open), bank 25% at +1R (or TP1 if nearer) on every leg open then, stop for the whole position to the original entry, runner
to TP2, and the +1.5R pyramid: the first bar reaching +1.5R after the bank adds P (of the ORIGINAL full size) at +1.5R (the
open if gapped), stop at the original entry, target TP2.
Variants: at that same moment, close X of every ORIGINAL leg that is on (the first tranche, and the staged add if it is in)
at +1.5R. X in 0 / 25 / 33 / 50 / 75 / 100%; P in 25 / 33 / 50 / 75 / 100%. (X=0, P=50% is today's live stack.)
M5 replay (the pyramid acts on an intrabar touch - coach rule: M5 before any number), all 7 symbols, development 2012-24 and
holdout 2025-26, long/short split; units = R of the full ruled position; spread drag per unit traded (a sale is free here:
the drag is charged on entries only, as in the other studies).
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                         # noqa: E402

BANK, F0, N, PYR_R = 0.25, 0.25, 6, 1.5


def run(M, k0, k_add, up, e, s, tp1, tp2, cost_unit, X, P):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    legs = [[F0, 0.0, True]]                      # [size, entry R, original?]
    locked = 0.0; stop = -1.0; banked = False; added = False; pyr = False; cost = cost_unit * F0
    val = lambda r: locked + sum(sz * (r - en) for sz, en, _ in legs)
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k])); last = c
        if lo <= stop: return val(min(stop, o)) - cost
        if not banked and hi >= bank_R:
            banked = True; locked += BANK * sum(sz * (bank_R - en) for sz, en, _ in legs)
            for g in legs: g[0] *= (1 - BANK)
            stop = 0.0
        if banked and not pyr and hi >= PYR_R:
            px = max(PYR_R, o); pyr = True
            if X > 0:                                  # sell X of every ORIGINAL leg that is on, at the pyramid's price
                for g in legs:
                    if g[2]: locked += X * g[0] * (px - g[1]); g[0] *= (1 - X)
            if P > 0: legs.append([P, px, False]); cost += cost_unit * P
        if tgt is not None and hi >= tgt: return val(tgt) - cost
        if not added and k_add is not None and k == k_add:
            legs.append([1 - F0, c, True]); added = True; cost += cost_unit * (1 - F0)
    return val(last) - cost


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"]); cache = {}; prep = []
    for x in rows:
        h4 = x["bars"]; i0 = x["i0"]
        if i0 + N >= len(h4): continue
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        ka = bisect.bisect_left(M["t"], h4[i0 + N][0][:16]) - 1; off = h4[i0 - 1][4] - M["c"][k0 - 1]
        prep.append((x, M, k0, ka, (x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None,
                                     (x["tp2"] - off) if x["tp2"] else None, x["cost"])))
    dev = [i for i, p in enumerate(prep) if p[0]["t"][:4] < "2025"]; hold = [i for i, p in enumerate(prep) if p[0]["t"][:4] >= "2025"]
    longs = [i for i in dev if prep[i][0]["up"]]; shorts = [i for i in dev if not prep[i][0]["up"]]
    hx = [i for i in hold if not prep[i][0]["sym"].startswith("XAU")]
    base = [run(M, k0, ka, *a, 0.0, 0.5) for x, M, k0, ka, a in prep]
    print(__doc__)
    print(f"n={len(prep)} (2012-24 {len(dev)}, 2025-26 {len(hold)}); base = today's live stack (sell 0%, pyramid 50%)\n")
    hdr = f"{'sell X / pyramid P':22} {'2012-24 R':>9} {'vs base':>8} {'t':>6} {'DD':>6} {'longs':>8} {'shorts':>8} | {'2025-26':>8} {'vs base':>8} {'ex-gold':>8}"
    print(hdr); print("-" * len(hdr))
    for X in (0.0, 0.25, 0.33, 0.50, 0.75, 1.0):
        for P in (0.25, 0.33, 0.50, 0.75, 1.0):
            v = base if (X == 0.0 and P == 0.5) else [run(M, k0, ka, *a, X, P) for x, M, k0, ka, a in prep]
            d = [v[i] for i in dev]; b = [base[i] for i in dev]
            h = [v[i] for i in hold]; hb = [base[i] for i in hold]
            print(f"sell {X:4.0%} / pyr {P:4.0%}   {st.mean(d):+9.4f} {st.mean(d) - st.mean(b):+8.4f} {paired_t(b, d):+6.2f} {maxdd(d):6.1f} "
                  f"{st.mean(v[i] for i in longs):+8.4f} {st.mean(v[i] for i in shorts):+8.4f} | {st.mean(h):+8.4f} {st.mean(h) - st.mean(hb):+8.4f} "
                  f"{st.mean(v[i] - base[i] for i in hx):+8.4f}", flush=True)
        print()
    print("vs base = paired difference from today's live stack; ex-gold = 2025-26 difference without XAUUSD; DD = worst 2012-24 drawdown in R.")


if __name__ == "__main__": main()
