#!/usr/bin/env python3
"""shorts_raise_sl_study.py - trader 2026-10-01: SHORTS only, by asset group - the timed stop raise (3 H4 bars after the pyramid,
whole position to +L, L = 0.25/0.5/0.75/1.0; time_raise_sl_study.run) vs today: R/trade, change, paired t, helped/hurt, and the
worst drawdown of that group's own trade sequence (time order, full-position R). Post-hoc slice (direction x group) - exploratory.
M5, 2012-24 and 2025-26.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import time_raise_sl_study as W                         # noqa: E402
from pipeline.exit_mgmt_study import paired_t, maxdd                  # noqa: E402

LS = (0.25, 0.5, 0.75, 1.0)
grp = lambda s: "index" if s.split(".")[0] in ("US100", "US500") else ("gold" if s.startswith("XAU") else ("oil" if s.startswith("USOIL") else "fx"))


def main():
    rows = sorted(W.load_rows(), key=lambda x: x["t"]); cache = {}; out = []
    for x in rows:
        if x["up"]: continue
        h4 = x["bars"]; i0 = x["i0"]
        if i0 + W.NADD >= len(h4): continue
        if x["sym"] not in cache: cache[x["sym"]] = W.load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        ka = bisect.bisect_left(M["t"], h4[i0 + W.NADD][0][:16]) - 1; off = h4[i0 - 1][4] - M["c"][k0 - 1]
        a = (x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None, (x["tp2"] - off) if x["tp2"] else None, x["cost"])
        out.append((x, W.run(M, k0, ka, *a), {L: W.run(M, k0, ka, *a, 3, L) for L in LS}))
    print(__doc__)
    for per, f in (("2012-24", lambda x: x["t"][:4] < "2025"), ("2025-26", lambda x: x["t"][:4] >= "2025")):
        print(f"\n{per}")
        print(f"  {'group':6} {'n':>4} {'stop to':>8} {'R/trade':>8} {'change':>8} {'t':>6} {'helped':>6} {'hurt':>4} {'worst DD':>9} {'DD change':>9}")
        for g in ("all", "index", "fx", "gold", "oil"):
            z = [o for o in out if f(o[0]) and (g == "all" or grp(o[0]["sym"]) == g)]
            if len(z) < 5: continue
            b = [o[1] for o in z]; bdd = maxdd(b)
            print(f"  {g:6} {len(z):4d} {'today':>8} {st.mean(b):+8.3f} {'':>8} {'':>6} {'':>6} {'':>4} {bdd:9.1f}")
            for L in LS:
                v = [o[2][L] for o in z]; d = [p - q for p, q in zip(v, b)]
                print(f"  {'':6} {'':4} {'+' + format(L, '.2f') + 'R':>8} {st.mean(v):+8.3f} {st.mean(d):+8.3f} {paired_t(b, v):+6.2f} "
                      f"{sum(1 for e in d if e > 1e-9):6d} {sum(1 for e in d if e < -1e-9):4d} {maxdd(v):9.1f} {maxdd(v) - bdd:+9.1f}")


if __name__ == "__main__": main()
