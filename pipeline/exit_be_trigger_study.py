#!/usr/bin/env python3
"""exit_be_trigger_study.py - trader 2026-09-30: WHEN the stop moves to entry.

The bank stays at +1R (or TP1 if nearer, as today); only the stop-to-entry trigger moves:
    +0.25 / +0.50 / +0.75 / +1.00 (today) / +1.25 / +1.50R.
Below +1R the stop goes to entry before anything is banked; above +1R the runner keeps the original stop between the
bank and the trigger. Run under today's bank (50%) and under the proposed smaller banks (33 / 25 / 15%) and no bank.
H4 on all 7 symbols and M5 on the 5 with minute data; same bar rules as exit_param_study.py / m5_study.py (stop checked
first at the previous bars' level, a bar opening through it fills at the open). Both periods shown; nothing selected.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.exit_param_study import longest_dry                    # noqa: E402
from pipeline import m5_study as M5                                   # noqa: E402

BANKS = (0.50, 0.33, 0.25, 0.15, 0.0)
TRIGS = (0.25, 0.50, 0.75, 1.00, 1.25, 1.50)


def replay(O, H, L, C, k0, kmax, up, entry, sl, tp1, tp2, frac, be_at):
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    doc = min(1.0, toR(tp1)) if tp1 else 1.0
    tgt = toR(tp2) if tp2 else None
    bank_R = doc if (frac and (tgt is None or doc < tgt - 1e-9)) else None
    be = doc if abs(be_at - 1.0) < 1e-9 else be_at                   # +1R keeps today's "or TP1 if nearer"
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    end = min(len(O), k0 + kmax)
    for j in range(k0, end):
        hi_R, lo_R = (toR(H[j]), toR(L[j])) if up else (toR(L[j]), toR(H[j]))
        if lo_R <= stop_R: return locked + rem * min(stop_R, toR(O[j]))
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, frac * bank_R, 1.0 - frac
        if tgt is not None and hi_R >= tgt: return locked + rem * tgt
        hwm = max(hwm, hi_R)
        if hwm >= be: stop_R = max(stop_R, 0.0)
    return locked + rem * toR(C[end - 1])


def table(title, rows, get):
    n = len(rows); half = n // 2
    dev = [k for k in range(n) if rows[k]["t"][:4] < "2025"]; hold = [k for k in range(n) if rows[k]["t"][:4] >= "2025"]
    res = {(f, t): [(get(x, f, t) or 0.0) - x["cost"] for x in rows] for f in BANKS for t in TRIGS}
    cur = res[(0.50, 1.00)]
    print(f"{title} (n={n})")
    print(f"  {'bank':5} {'stop->entry at':>14} {'R/trade':>8} {'vs today':>9} {'t':>6} {'vs same bank @+1R':>18} {'t':>6} "
          f"{'t 1st½':>7} {'t 2nd½':>7} {'2012-24':>8} {'2025+':>8} {'worst DD':>9} {'>+0.3R %':>9} {'dry run':>8}")
    for f in BANKS:
        ref = res[(f, 1.00)]
        for t in TRIGS:
            v = res[(f, t)]
            tc = paired_t(cur, v) if v is not cur else 0.0
            tr = paired_t(ref, v) if v is not ref else 0.0
            t1 = paired_t(ref[:half], v[:half]) if tr else 0.0; t2 = paired_t(ref[half:], v[half:]) if tr else 0.0
            raw = [a + x["cost"] for a, x in zip(v, rows)]
            mark = "  <- today" if (f, t) == (0.50, 1.00) else ("  (ref)" if t == 1.00 else "")
            print(f"  {int(f * 100):4d}% {'+' + format(t, '.2f') + 'R':>14} {st.mean(v):+8.4f} {st.mean(v) - st.mean(cur):+9.4f} {tc:+6.2f} "
                  f"{st.mean(v) - st.mean(ref):+18.4f} {tr:+6.2f} {t1:+7.2f} {t2:+7.2f} {st.mean(v[k] for k in dev):+8.4f} "
                  f"{st.mean(v[k] for k in hold):+8.4f} {maxdd(v):9.1f} {100 * sum(1 for a in raw if a > 0.3) / n:9.1f} "
                  f"{longest_dry(raw):8d}{mark}")
        print()


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    arr = {}
    for x in rows:
        if x["sym"] not in arr:
            b = x["bars"]; arr[x["sym"]] = ([r[1] for r in b], [r[2] for r in b], [r[3] for r in b], [r[4] for r in b])
    table("H4, all 7 symbols, costed", rows,
          lambda x, f, t: replay(*arr[x["sym"]], x["i0"], 600, x["up"], x["e"], x["s"], x["tp1"], x["tp2"], f, t))
    cache, use = {}, []
    for x in rows:
        if x["sym"] not in cache: cache[x["sym"]] = M5.load_m5(x["sym"])
        M = cache[x["sym"]]
        if not M: continue
        t0 = x["bars"][x["i0"]][0][:16]; k0 = bisect.bisect_left(M["t"], t0)
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != t0[:10]: continue
        use.append({**x, "M": M, "k0": k0, "off": x["bars"][x["i0"] - 1][4] - M["c"][k0 - 1]})
    table("M5, 5 symbols (no XAUUSD / USDJPY), costed", use,
          lambda x, f, t: replay(x["M"]["o"], x["M"]["h"], x["M"]["l"], x["M"]["c"], x["k0"], M5.MAX_M5, x["up"],
                                 x["e"] - x["off"], x["s"] - x["off"], (x["tp1"] - x["off"]) if x["tp1"] else None,
                                 (x["tp2"] - x["off"]) if x["tp2"] else None, f, t))
    print("'>+0.3R %' = trades finishing above +0.3R (a real winner, not just the bank). dry run = longest streak without one.")


if __name__ == "__main__": main()
