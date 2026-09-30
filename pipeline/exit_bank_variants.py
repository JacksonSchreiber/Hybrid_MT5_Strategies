#!/usr/bin/env python3
"""exit_bank_variants.py - trader 2026-09-30: bank 25% + stop to +0.25R, and bank 33% + stop to entry, beside the
base / bank 25% / no-bank references; H4 on all 7 symbols and M5 on the 5 with minute data. Same replay rules as
exit_param_study.py / m5_study.py. Nothing is selected here: both periods shown."""
import os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t      # noqa: E402
from pipeline.exit_param_study import replay, longest_dry            # noqa: E402
from pipeline import m5_study as M5                                   # noqa: E402
import bisect

V = [("current: bank 50%, stop to entry", "doc", 0.50, 0.0), ("bank 25%, stop to entry", "doc", 0.25, 0.0),
     ("bank 20%, stop to entry", "doc", 0.20, 0.0), ("bank 15%, stop to entry", "doc", 0.15, 0.0),
     ("bank 25%, stop to +0.25R", "doc", 0.25, 0.25), ("bank 33%, stop to entry", "doc", 0.33, 0.0),
     ("no bank, stop to +0.25R", None, 0.0, 0.25), ("no bank, stop to entry", None, 0.0, 0.0)]


def report(title, rows, series):
    n = len(rows); half = n // 2
    dev = [k for k in range(n) if rows[k]["t"][:4] < "2025"]; hold = [k for k in range(n) if rows[k]["t"][:4] >= "2025"]
    print(f"{title} (n={n}; halves split at {rows[half]['t'][:10]})")
    print(f"  {'rule':36} {'R/trade':>8} {'t':>6} {'t 1st½':>7} {'t 2nd½':>7} {'2012-24':>8} {'2025+':>8} {'worst DD':>9} {'win%':>6} {'dry run':>8}")
    b = series[V[0][0]]
    for name, *_ in V:
        v = series[name]; c = [x - r["cost"] for x, r in zip(v, rows)]; bc = [x - r["cost"] for x, r in zip(b, rows)]
        t = paired_t(bc, c) if v is not b else 0.0
        t1 = paired_t(bc[:half], c[:half]) if t else 0.0; t2 = paired_t(bc[half:], c[half:]) if t else 0.0
        print(f"  {name:36} {st.mean(c):+8.4f} {t:+6.2f} {t1:+7.2f} {t2:+7.2f} {st.mean(c[k] for k in dev):+8.4f} "
              f"{st.mean(c[k] for k in hold):+8.4f} {maxdd(c):9.1f} {100 * sum(1 for x in v if x > 0.01) / n:6.1f} {longest_dry(v):8d}")
    print()


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    h4 = {name: [replay(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], at, f, s) or 0.0 for x in rows]
          for name, at, f, s in V}
    report("H4, all 7 symbols, costed", rows, h4)
    cache, use = {}, []
    for x in rows:
        if x["sym"] not in cache: cache[x["sym"]] = M5.load_m5(x["sym"])
        M = cache[x["sym"]]
        if not M: continue
        t0 = x["bars"][x["i0"]][0][:16]; k0 = bisect.bisect_left(M["t"], t0)
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != t0[:10]: continue
        use.append({**x, "M": M, "k0": k0, "off": x["bars"][x["i0"] - 1][4] - M["c"][k0 - 1]})
    m5 = {name: [M5.m5_replay(x["M"], x["k0"], x["up"], x["e"] - x["off"], x["s"] - x["off"],
                              (x["tp1"] - x["off"]) if x["tp1"] else None, (x["tp2"] - x["off"]) if x["tp2"] else None,
                              f, s) or 0.0 for x in use] for name, at, f, s in V}
    report(f"M5, {len({x['sym'] for x in use})} symbols, costed", use, m5)


if __name__ == "__main__": main()
