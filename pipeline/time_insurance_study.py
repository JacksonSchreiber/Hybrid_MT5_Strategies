#!/usr/bin/env python3
"""time_insurance_study.py - trader 2026-10-01, insurance question, study 4: does TIME after +1.5R separate the trades that go
on to TP2 from the ones that come back to entry, where depth (study 1, pullback_map_study.py) did not?

PART A - diagnostic (price path, no rule): trades that reach +1.5R after the bank (M5, blind TrendCont record). For N = 3,
6, 12, 24, 48 H4 bars after the first +1.5R touch: among trades STILL OPEN then (neither TP2 nor back to entry), the share that
go on to TP2, and per unit: the R of HOLDING to the end vs CLOSING at that bar's close - for one unit of the original
position (entry 0R) and one unit of the pyramid (entry +1.5R).
PART B - rules on the live stack (staged entry 25%/bar-6, bank 25% at +1R, stop to entry, pyramid 50% at +1.5R, stop at entry,
TP2), each vs today: if TP2 has not been reached N H4 bars after the pyramid fired, at that bar's close
   P  - close the pyramid leg only;     ALL - close the whole position.
M5, all 7 symbols, 2012-24 and 2025-26, long/short; full-position R; spread drag per unit entered.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                         # noqa: E402

NS = (3, 6, 12, 24, 48); BPB = 48                                    # M5 bars per H4 bar
BANK, F0, NADD, PYR_R, PYR_F = 0.25, 0.25, 6, 1.5, 0.5


def diag(M, k0, up, e, s, tp1, tp2):
    """-> (outcome, tgt, {N: close R at N bars after the first +1.5R, or None if finished before})"""
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    if tgt is None: return None
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5)
    stop = -1.0; banked = False; k15 = None; at = {}
    for k in range(k0, end):
        hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k]))
        if lo <= stop: return ("LOSER", tgt, at) if k15 is not None else None
        if not banked and hi >= bank_R: banked = True; stop = 0.0
        if banked and k15 is None and hi >= 1.5: k15 = k
        if hi >= tgt: return ("WINNER", tgt, at) if k15 is not None else None
        if k15 is not None:
            for n in NS:
                if k - k15 + 1 == n * BPB: at[n] = toR(C[k])
    return None


def run(M, k0, k_add, up, e, s, tp1, tp2, cost_unit, N=None, mode=None):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    legs = [[F0, 0.0, True]]; locked = 0.0; stop = -1.0; banked = False; added = False; kp = None; cost = cost_unit * F0
    val = lambda r: locked + sum(g[0] * (r - g[1]) for g in legs)
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k])); last = c
        if lo <= stop: return val(min(stop, o)) - cost
        if not banked and hi >= bank_R:
            banked = True; locked += BANK * sum(g[0] * (bank_R - g[1]) for g in legs)
            for g in legs: g[0] *= (1 - BANK)
            stop = 0.0
        if banked and kp is None and hi >= PYR_R:
            kp = k; legs.append([PYR_F, max(PYR_R, o), False]); cost += cost_unit * PYR_F
        if tgt is not None and hi >= tgt: return val(tgt) - cost
        if not added and k_add is not None and k == k_add:
            legs.append([1 - F0, c, True]); added = True; cost += cost_unit * (1 - F0)
        if N and kp is not None and k - kp + 1 == N * BPB:
            if mode == "ALL": return val(c) - cost
            for g in legs:
                if not g[2] and g[0] > 0: locked += g[0] * (c - g[1]); g[0] = 0.0
    return val(last) - cost


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"]); cache = {}; prep = []
    for x in rows:
        h4 = x["bars"]; i0 = x["i0"]
        if i0 + NADD >= len(h4): continue
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        ka = bisect.bisect_left(M["t"], h4[i0 + NADD][0][:16]) - 1; off = h4[i0 - 1][4] - M["c"][k0 - 1]
        prep.append((x, M, k0, ka, (x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None,
                                     (x["tp2"] - off) if x["tp2"] else None, x["cost"])))
    print(__doc__)
    # PART A
    print("PART A - still open N H4 bars after the first +1.5R touch (2012-24 | 2025-26)")
    print(f"  {'N':>3} {'open':>5} {'go on to TP2':>13} {'orig unit: hold':>16} {'close now':>10} {'pyr unit: hold':>15} {'close now':>10}")
    for per, f in (("2012-24", lambda x: x["t"][:4] < "2025"), ("2025-26", lambda x: x["t"][:4] >= "2025")):
        ds = [diag(M, k0, *a[:5]) for x, M, k0, ka, a in prep if f(x)]
        ds = [d for d in ds if d]
        print(f"  {per}: {len(ds)} trades reached +1.5R ({sum(1 for d in ds if d[0] == 'WINNER')} to TP2)")
        for n in NS:
            op = [d for d in ds if n in d[2]]
            if not op: continue
            hold_o = st.mean((d[1] if d[0] == "WINNER" else 0.0) for d in op); close_o = st.mean(d[2][n] for d in op)
            print(f"  {n:3d} {len(op):5d} {sum(1 for d in op if d[0] == 'WINNER') / len(op) * 100:12.0f}% {hold_o:+16.3f} {close_o:+10.3f} "
                  f"{hold_o - 1.5:+15.3f} {close_o - 1.5:+10.3f}")
    # PART B
    dev = [i for i, p in enumerate(prep) if p[0]["t"][:4] < "2025"]; hold = [i for i, p in enumerate(prep) if p[0]["t"][:4] >= "2025"]
    longs = [i for i in dev if prep[i][0]["up"]]; shorts = [i for i in dev if not prep[i][0]["up"]]
    hx = [i for i in hold if not prep[i][0]["sym"].startswith("XAU")]
    base = [run(M, k0, ka, *a) for x, M, k0, ka, a in prep]
    print(f"\nPART B - rules on the live stack (base 2012-24 {st.mean(base[i] for i in dev):+.4f}R, DD {maxdd([base[i] for i in dev]):.1f}R, "
          f"longs {st.mean(base[i] for i in longs):+.4f}, shorts {st.mean(base[i] for i in shorts):+.4f})")
    hdr = f"  {'rule':22} {'2012-24 R':>9} {'vs base':>8} {'t':>6} {'DD':>6} {'longs':>8} {'shorts':>8} | {'2025-26 vs':>10} {'ex-gold':>8}"
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for mode in ("P", "ALL"):
        for n in NS:
            v = [run(M, k0, ka, *a, n, mode) for x, M, k0, ka, a in prep]
            d = [v[i] for i in dev]; b = [base[i] for i in dev]
            print(f"  {('close pyramid' if mode == 'P' else 'close all') + f' after {n:2d} bars':22} {st.mean(d):+9.4f} {st.mean(d) - st.mean(b):+8.4f} "
                  f"{paired_t(b, d):+6.2f} {maxdd(d):6.1f} {st.mean(v[i] for i in longs):+8.4f} {st.mean(v[i] for i in shorts):+8.4f} | "
                  f"{st.mean(v[i] - base[i] for i in hold):+10.4f} {st.mean(v[i] - base[i] for i in hx):+8.4f}", flush=True)


if __name__ == "__main__": main()
