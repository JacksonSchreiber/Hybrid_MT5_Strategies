#!/usr/bin/env python3
"""pyramid_raise_sl_study.py - trader 2026-10-01: when the +1.5R pyramid fires, RAISE the stop of the ORIGINAL position only.

Base = the live stack (pipeline/pyramid_sell_study.py docstring): staged entry 25%/bar-6, bank 25% at +1R, stop to entry,
pyramid 50% of the full size at +1.5R with its stop at the original entry, target TP2.
Variant: at the pyramid's trigger, every ORIGINAL leg that is on (the first tranche, and the staged add if in) moves its stop
to +L R (L = 0 today; tested 0.25 / 0.50 / 0.75 / 1.00 / 1.25); the pyramid leg keeps its stop at entry (0R). A staged add
that arrives later joins with the original legs' stop (as the EA does: the add takes the position's current stop) - skipped if
its bar-6 price is already through that stop. Each leg is stopped on its own level; TP2 closes everything.
M5, all 7 symbols, 2012-24 and 2025-26, long/short; full-position R; spread drag per unit entered.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                         # noqa: E402

BANK, F0, N, PYR_R, PYR_F = 0.25, 0.25, 6, 1.5, 0.5


def run(M, k0, k_add, up, e, s, tp1, tp2, cost_unit, L):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    legs = [[F0, 0.0, True, -1.0]]                 # [size, entry R, original?, stop R]
    locked = 0.0; banked = False; added = False; pyr = False; cost = cost_unit * F0; orig_stop = -1.0
    O, H, Lo, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(Lo[k])) if up else (toR(Lo[k]), toR(H[k])); last = c
        for g in legs:                                             # each leg on its own stop (previous bars' level)
            if g[0] > 0 and lo <= g[3]: locked += g[0] * (min(g[3], o) - g[1]); g[0] = 0.0
        if not any(g[0] > 0 for g in legs) and (added or k_add is None or k > k_add or True) and legs and pyr is not None:
            if all(g[0] == 0 for g in legs) and (added or True): return locked - cost
        if not banked and hi >= bank_R:
            banked = True; locked += BANK * sum(g[0] * (bank_R - g[1]) for g in legs)
            for g in legs: g[0] *= (1 - BANK); g[3] = max(g[3], 0.0)
            orig_stop = max(orig_stop, 0.0)
        if banked and not pyr and hi >= PYR_R:
            px = max(PYR_R, o); pyr = True
            for g in legs:
                if g[2]: g[3] = max(g[3], L)
            orig_stop = max(orig_stop, L)
            legs.append([PYR_F, px, False, 0.0]); cost += cost_unit * PYR_F
        if tgt is not None and hi >= tgt:
            return locked + sum(g[0] * (tgt - g[1]) for g in legs) - cost
        if not added and k_add is not None and k == k_add:
            added = True
            if c > orig_stop: legs.append([1 - F0, c, True, orig_stop]); cost += cost_unit * (1 - F0)
    return locked + sum(g[0] * (last - g[1]) for g in legs) - cost


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
    base = [run(M, k0, ka, *a, 0.0) for x, M, k0, ka, a in prep]
    print(__doc__)
    print(f"n={len(prep)} (2012-24 {len(dev)}, 2025-26 {len(hold)}); base = today's live stack (original stop stays at entry)\n")
    hdr = f"{'original stop at +1.5R':24} {'2012-24 R':>9} {'vs base':>8} {'t':>6} {'DD':>6} {'longs':>8} {'shorts':>8} | {'2025-26':>8} {'vs base':>8} {'ex-gold':>8}"
    print(hdr); print("-" * len(hdr))
    for L in (0.0, 0.25, 0.50, 0.75, 1.00, 1.25):
        v = base if L == 0 else [run(M, k0, ka, *a, L) for x, M, k0, ka, a in prep]
        d = [v[i] for i in dev]; b = [base[i] for i in dev]; h = [v[i] for i in hold]; hb = [base[i] for i in hold]
        print(f"{'entry (today)' if L == 0 else f'+{L:.2f}R':24} {st.mean(d):+9.4f} {st.mean(d) - st.mean(b):+8.4f} {paired_t(b, d):+6.2f} {maxdd(d):6.1f} "
              f"{st.mean(v[i] for i in longs):+8.4f} {st.mean(v[i] for i in shorts):+8.4f} | {st.mean(h):+8.4f} {st.mean(h) - st.mean(hb):+8.4f} "
              f"{st.mean(v[i] - base[i] for i in hx):+8.4f}", flush=True)


if __name__ == "__main__": main()
