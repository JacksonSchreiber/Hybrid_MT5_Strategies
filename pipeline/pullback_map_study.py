#!/usr/bin/env python3
"""pullback_map_study.py - trader 2026-10-01, insurance question, study 1 (no rule tested): after a TrendCont trade first
reaches +1.5R (post-bank, where the pyramid fires), how deep does it pull back before it finishes?

Population: the blind TrendCont record (2,346 rows), replayed on M5 (all 7 symbols), from the bar after the signal bar.
The PRICE PATH only (independent of sizing): bank level = +1R or TP1 if nearer; a trade "reaches +1.5R" if its high-water
mark gets there before it trades back to entry after the bank (or to the stop before the bank). From the first +1.5R touch
the deepest pullback (lowest R, measured on the trade's adverse side, stop-first per bar) is tracked until:
  WINNER  - TP2 is reached;   LOSER - price trades back to the original entry (0R, the live stop after the bank);
  OPEN    - neither within 600 H4 bars (excluded from the cost table).
Then, for ONE UNIT of the original position (entry 0R), a hypothetical insurance stop at +L instead of 0 SAVES L on every loser (it exits at L, not 0) and COSTS (TP2 - L) on every winner that
dipped to L before TP2. Break-even needs: share of winners cut x (TP2 - L) = share of losers x L.
M5 replay; 2012-24 and 2025-26; long/short.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, MAX_BARS            # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                        # noqa: E402

LEVELS = (0.25, 0.5, 0.75, 1.0, 1.25)


def path(M, k0, up, e, s, tp1, tp2):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    if tgt is None: return None
    O, H, L = M["o"], M["h"], M["l"]; end = min(len(O), k0 + MAX_M5)
    stop = -1.0; banked = False; reached = False; low_after = None
    for k in range(k0, end):
        hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k]))
        if reached: low_after = min(low_after, lo)
        if lo <= stop:
            return ("LOSER", low_after, tgt) if reached else None
        if not banked and hi >= bank_R: banked = True; stop = 0.0
        if banked and not reached and hi >= 1.5:
            reached = True; low_after = 1.5
        if hi >= tgt:
            return ("WINNER", low_after, tgt) if reached else None
    return ("OPEN", low_after, tgt) if reached else None


def table(name, res):
    w = [r for r in res if r[0] == "WINNER"]; lo = [r for r in res if r[0] == "LOSER"]
    if not w or not lo: print(f"  {name}: too few (winners {len(w)}, losers {len(lo)})"); return
    print(f"\n  {name}: reached +1.5R n={len(res)} -> WINNER (TP2) {len(w)}  LOSER (back to entry) {len(lo)}  "
          f"open {len(res) - len(w) - len(lo)}  · winners' TP2 median {st.median(r[2] for r in w):.2f}R")
    qs = sorted(r[1] for r in w)
    pct = lambda q: qs[min(len(qs) - 1, int(q * len(qs)))]
    print(f"    winners' deepest pullback after +1.5R (R): 10% {pct(0.10):+.2f} · 25% {pct(0.25):+.2f} · median {pct(0.5):+.2f} · "
          f"75% {pct(0.75):+.2f} · 90% {pct(0.9):+.2f}")
    print(f"    {'insurance stop':>15} {'winners cut':>12} {'cost / unit':>12} {'saving / unit':>14} {'net / unit':>11}   (per trade that reached +1.5R)")
    n = len(w) + len(lo)
    for L in LEVELS:
        cut = [r for r in w if r[1] <= L]
        cost = sum(r[2] - L for r in cut) / n
        save = len(lo) * L / n
        print(f"    {'+' + format(L, '.2f') + 'R':>15} {len(cut) / len(w) * 100:10.0f}% {-cost:+12.3f} {save:+14.3f} {save - cost:+11.3f}")


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"]); cache = {}; res = []
    for x in rows:
        h4 = x["bars"]; i0 = x["i0"]
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        off = h4[i0 - 1][4] - M["c"][k0 - 1]
        r = path(M, k0, x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None, (x["tp2"] - off) if x["tp2"] else None)
        if r: res.append((r, x))
    print(__doc__)
    sel = lambda f: [r for r, x in res if f(x)]
    table("2012-24, all", sel(lambda x: x["t"][:4] < "2025"))
    table("2012-24, longs", sel(lambda x: x["t"][:4] < "2025" and x["up"]))
    table("2012-24, shorts", sel(lambda x: x["t"][:4] < "2025" and not x["up"]))
    table("2025-26, all", sel(lambda x: x["t"][:4] >= "2025"))
    table("2025-26, ex-gold", sel(lambda x: x["t"][:4] >= "2025" and not x["sym"].startswith("XAU")))


if __name__ == "__main__": main()
