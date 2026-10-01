#!/usr/bin/env python3
"""shorts_targets_study.py - trader 2026-10-01: SHORTS only - do lower targets help? Stop management exactly as live (initial stop
-1R; stop to entry at the live bank trigger = +1R or TP1 if nearer; pyramid 50% at +1.5R once that trigger has happened; staged
25%/bar-6). Varied:  TP2' = min(the trade's TP2, X) for X in (today, 3.5, 3.0, 2.5, 2.0);  bank level Y (the 25% partial) in
(today, 0.75, 0.5) - when Y is below the live trigger the bank fills at Y but the stop still moves only at the live trigger.
Also: plain size scaling of shorts (1.0 / 0.75 / 0.5x) on the account (longs unchanged).
Longs unchanged everywhere. M5, all 7 symbols, 2012-24 and 2025-26; full-position R; spread drag per unit entered.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                         # noqa: E402

BANK, F0, NADD, PYR_R, PYR_F = 0.25, 0.25, 6, 1.5, 0.5
XS = (None, 3.5, 3.0, 2.5, 2.0); YS = (None, 0.75, 0.5)
grp = lambda s: "index" if s.split(".")[0] in ("US100", "US500") else ("gold" if s.startswith("XAU") else ("oil" if s.startswith("USOIL") else "fx"))
PREP = []


def run(M, k0, k_add, up, e, s, tp1, tp2, cost_unit, X=None, Y=None):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    trig = min(1.0, toR(tp1)) if tp1 else 1.0; bank_R = trig if Y is None else min(Y, trig)
    tgt = toR(tp2) if tp2 else None
    if tgt is not None and X is not None: tgt = min(tgt, X)
    legs = [[F0, 0.0]]; locked = 0.0; stop = -1.0; banked = False; be = False; added = False; pyr = False; cost = cost_unit * F0
    val = lambda r: locked + sum(g[0] * (r - g[1]) for g in legs)
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k])); last = c
        if lo <= stop: return val(min(stop, o)) - cost
        if not banked and hi >= bank_R:
            banked = True; locked += BANK * sum(g[0] * (bank_R - g[1]) for g in legs)
            for g in legs: g[0] *= (1 - BANK)
        if not be and hi >= trig: be = True; stop = 0.0
        if be and not pyr and hi >= PYR_R and (tgt is None or tgt > PYR_R):
            pyr = True; legs.append([PYR_F, max(PYR_R, o)]); cost += cost_unit * PYR_F
        if tgt is not None and hi >= tgt: return val(tgt) - cost
        if not added and k_add is not None and k == k_add:
            legs.append([1 - F0, c]); added = True; cost += cost_unit * (1 - F0)
    return val(last) - cost


def work(arg):
    return arg, [run(M, k0, ka, *a, *arg) if not x["up"] else None for x, M, k0, ka, a in PREP]


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
    jobs = [(X, Y) for X in XS for Y in YS] + [("L", None)]
    with Pool(8) as pool: res = dict(pool.map(work, [j for j in jobs if j[0] != "L"]))
    longs_base = [run(M, k0, ka, *a) if x["up"] else None for x, M, k0, ka, a in PREP]
    base = res[(None, None)]
    lab = lambda v, d: "today" if v is None else f"{v:+.2f}R"
    for per, f in (("2012-24", lambda x: x["t"][:4] < "2025"), ("2025-26", lambda x: x["t"][:4] >= "2025")):
        S = [i for i, p in enumerate(PREP) if f(p[0]) and not p[0]["up"]]; A = [i for i, p in enumerate(PREP) if f(p[0])]
        print(f"\n{per} - SHORTS (n={len(S)})   change per trade by group: index / fx / gold / oil")
        print(f"  {'TP2':>7} {'TP1 bank':>8} {'R/trade':>8} {'change':>8} {'t':>6} {'short DD':>8} | {'index':>7} {'fx':>7} {'gold':>7} {'oil':>7} | {'account R':>9} {'acct DD':>7}")
        for X in XS:
            for Y in YS:
                v = res[(X, Y)]; s = [v[i] for i in S]; b = [base[i] for i in S]
                g = lambda G: (lambda ix: f"{st.mean(v[i] - base[i] for i in ix):+7.3f}" if len(ix) >= 5 else f"{'-':>7}")([i for i in S if grp(PREP[i][0]['sym']) == G])
                acc = [v[i] if v[i] is not None else longs_base[i] for i in A]
                print(f"  {lab(X, 0):>7} {lab(Y, 0):>8} {st.mean(s):+8.3f} {st.mean(s) - st.mean(b):+8.3f} {paired_t(b, s) if X or Y else 0:+6.2f} {maxdd(s):8.1f} | "
                      f"{g('index')} {g('fx')} {g('gold')} {g('oil')} | {st.mean(acc):+9.4f} {maxdd(acc):7.1f}")
            print()
        print(f"  plain size on shorts (targets as today): account R/trade and DD")
        for m in (1.0, 0.75, 0.5):
            acc = [base[i] * m if base[i] is not None else longs_base[i] for i in A]
            print(f"    shorts x{m:.2f}: account {st.mean(acc):+.4f}R/trade  DD {maxdd(acc):.1f}R  (short DD {maxdd([base[i] * m for i in S]):.1f}R)")


if __name__ == "__main__": main()
