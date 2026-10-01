#!/usr/bin/env python3
"""time_raise_sl_legs_study.py - trader 2026-10-01, follow-up to time_raise_sl_study.py (which raised the WHOLE position):
raise only ONE side's stop N H4 bars after the pyramid fired (if TP2 not yet reached), at that bar's close:
   ORIG - the original + staged-add legs' stop to +L R; the pyramid keeps its stop at entry (0R).
   PYR  - the pyramid leg's stop to +L R; the original + staged add keep theirs at 0R.
If that bar closes at/below +L, the raised legs are closed at the close (a stop cannot sit above the market).
Live stack otherwise unchanged (staged 25%/bar-6, bank 25% at +1R, stops to entry, pyramid 50% at +1.5R, TP2). Per-leg stops,
stop first each bar, gap fills at the open. M5, all 7 symbols, 2012-24 and 2025-26, long/short; full-position R; spread drag.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                         # noqa: E402

NS = (3, 6, 12, 24, 48); LS = (0.25, 0.5, 0.75, 1.0, 1.25); BPB = 48
BANK, F0, NADD, PYR_R, PYR_F = 0.25, 0.25, 6, 1.5, 0.5
PREP = []


def run(M, k0, k_add, up, e, s, tp1, tp2, cost_unit, N=None, Lv=None, side=None):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    legs = [[F0, 0.0, True, -1.0]]; locked = 0.0; banked = False; added = False; kp = None; cost = cost_unit * F0
    val = lambda r: locked + sum(g[0] * (r - g[1]) for g in legs)
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k])); last = c
        for g in legs:
            if g[0] > 0 and lo <= g[3]: locked += g[0] * (min(g[3], o) - g[1]); g[0] = 0.0
        if all(g[0] <= 0 for g in legs): return locked - cost
        if not banked and hi >= bank_R:
            banked = True; locked += BANK * sum(g[0] * (bank_R - g[1]) for g in legs)
            for g in legs: g[0] *= (1 - BANK); g[3] = max(g[3], 0.0)
        if banked and kp is None and hi >= PYR_R:
            kp = k; legs.append([PYR_F, max(PYR_R, o), False, 0.0]); cost += cost_unit * PYR_F
        if tgt is not None and hi >= tgt: return val(tgt) - cost
        if not added and k_add is not None and k == k_add:
            legs.append([1 - F0, c, True, legs[0][3]]); added = True; cost += cost_unit * (1 - F0)
        if N and kp is not None and k - kp + 1 == N * BPB:
            for g in legs:
                if g[0] > 0 and g[2] == (side == "ORIG") and Lv > g[3]:
                    if c <= Lv: locked += g[0] * (c - g[1]); g[0] = 0.0
                    else: g[3] = Lv
            if all(g[0] <= 0 for g in legs): return locked - cost
    return val(last) - cost


def work(arg):
    return arg, [run(M, k0, ka, *a, *arg) for x, M, k0, ka, a in PREP]


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"]); cache = {}
    for x in rows:
        h4 = x["bars"]; i0 = x["i0"]
        if i0 + NADD >= len(h4): continue
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        ka = bisect.bisect_left(M["t"], h4[i0 + NADD][0][:16]) - 1; off = h4[i0 - 1][4] - M["c"][k0 - 1]
        PREP.append((x, M, k0, ka, (x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None,
                                     (x["tp2"] - off) if x["tp2"] else None, x["cost"])))
    print(__doc__)
    dev = [i for i, p in enumerate(PREP) if p[0]["t"][:4] < "2025"]; hold = [i for i, p in enumerate(PREP) if p[0]["t"][:4] >= "2025"]
    longs = [i for i in dev if PREP[i][0]["up"]]; shorts = [i for i in dev if not PREP[i][0]["up"]]
    hx = [i for i in hold if not PREP[i][0]["sym"].startswith("XAU")]
    jobs = [(None, None, None)] + [(n, l, sd) for sd in ("ORIG", "PYR") for n in NS for l in LS]
    with Pool(min(8, os.cpu_count() or 4)) as pool: res = dict(pool.map(work, jobs))
    base = res[(None, None, None)]; m = lambda v, ix: st.mean(v[i] for i in ix)
    print(f"base (today) 2012-24 {m(base, dev):+.4f}R  DD {maxdd([base[i] for i in dev]):.1f}R  longs {m(base, longs):+.4f}  "
          f"shorts {m(base, shorts):+.4f} | 2025-26 {m(base, hold):+.4f}  ex-gold {m(base, hx):+.4f}")
    for sd in ("ORIG", "PYR"):
        print(f"\n{sd}: {'original + staged add raised, pyramid stays at 0R' if sd == 'ORIG' else 'pyramid raised, original + staged add stay at 0R'}")
        hdr = f"  {'after N bars':>12} {'SL to':>6} {'2012-24 R':>9} {'vs base':>8} {'t':>6} {'DD':>6} {'longs':>8} {'shorts':>8} | {'2025-26 vs':>10} {'ex-gold':>8}"
        print(hdr); print("  " + "-" * (len(hdr) - 2))
        for n in NS:
            for l in LS:
                v = res[(n, l, sd)]; d = [v[i] for i in dev]; b = [base[i] for i in dev]
                print(f"  {n:12d} {'+' + format(l, '.2f'):>6} {st.mean(d):+9.4f} {st.mean(d) - st.mean(b):+8.4f} {paired_t(b, d):+6.2f} {maxdd(d):6.1f} "
                      f"{m(v, longs):+8.4f} {m(v, shorts):+8.4f} | {st.mean(v[i] - base[i] for i in hold):+10.4f} {st.mean(v[i] - base[i] for i in hx):+8.4f}")
            print()


if __name__ == "__main__": main()
