#!/usr/bin/env python3
"""streak_avg_study.py - trader 2026-10-03: STREAKS of improving / worsening DAILY averages. Day d's average = mean of the M1 bid closes
in trading day d (H4 bars 6(d-1)..6d after entry), in the first tranche's 1R.
  IMPROVING for N days: day1 < day2 < ... < dayN (N = 2..5) -> at the close of H4 bar 6N add +50% of the full size at market (shares
                         the stop, runs to TP2).
  WORSENING for N days: day1 > day2 > ... > dayN -> at the close of H4 bar 6N sell X% (100 / 75 / 50 / 25) of every open ticket at
                         market; the rest runs on.
Longs and shorts reported separately (the rule on one side, the other at base). Base = live (staged 25%/bar-6, bank per detector at
+1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at +1.5R). Engine pipeline/ask_side_study, SCALED. All four
detectors, 7 symbols, pinned record; 2012-24 / 2025-26; full-position R; worst DD.
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402
from pipeline.combo3_study import run_v as run_add                          # noqa: E402  (args ..., gate, kc, ka2 -> +50%)
from pipeline.day3_weak_study import run_v as run_sell                      # noqa: E402  (args ..., kc, "sell", frac)

MODE = "SCALED"; NS = (2, 3, 4, 5); SELLS = (1.0, 0.75, 0.5, 0.25)
REPORT = os.path.join(A.STUDY, "streak_avg_report.txt")


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5); cum = np.cumsum(F["bc"]); n = F["n"]
    base_args = (F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"])
    out = {"base": run_sell(*base_args, None, None, None)[0]}
    ks = []
    for d in range(0, 6):
        ib = u["i0"] + 6 * d
        ks.append((int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if (d == 0 or ib < len(S.h4)) else None)
    ks[0] = -1
    day = {}
    for d in range(1, 6):
        a, b = ks[d - 1], ks[d]
        if b is None or not (0 <= b < n) or b <= a: break
        day[d] = (cum[b] - (cum[a] if a >= 0 else 0.0)) / (b - a)
    for N in NS:
        k = ks[N] if N in day else None
        up = N in day and all(day[i] < day[i + 1] for i in range(1, N))
        dn = N in day and all(day[i] > day[i + 1] for i in range(1, N))
        out[("add", N)] = run_add(*base_args, False, None, k)[0] if up else out["base"]
        out[("hadd", N)] = up and out[("add", N)] != out["base"]
        for f in SELLS:
            out[("sell", N, f)] = run_sell(*base_args, k, "sell", f)[0] if dn else out["base"]
        out[("hsell", N)] = dn and out[("sell", N, 1.0)] != out["base"]
    return out


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        r = trade(S, x)
        if r: res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def main():
    global ALL
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = sorted([z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs], key=lambda z: z[1])
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for per in ("dev", "hold"):
        zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
        b = [z[4]["base"] for z in zs]; mb = st.mean(b)
        L.append(f"\n{'=' * 104}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R")
        for side, lab in ((True, "LONGS"), (False, "SHORTS")):
            L.append(f"\n  {lab} - change in R per trade vs base [t] / worst DD   (trades the rule changed in brackets)")
            L.append(f"  {'streak':8} | {'IMPROVING -> add +50%':>30} | " + " | ".join(f"{'WORSENING -> sell ' + str(int(f * 100)) + '%':>30}" for f in SELLS))
            for N in NS:
                def cell(key, hkey):
                    v = [z[4][key] if z[3] == side else z[4]["base"] for z in zs]
                    nh = sum(1 for z in zs if z[3] == side and z[4][hkey])
                    return f"{st.mean(v) - mb:+.4f}[{paired_t(b, v):+.1f}]/{maxdd(v):4.1f} ({nh})"
                L.append(f"  {str(N) + ' days':8} | {cell(('add', N), ('hadd', N)):>30} | " + " | ".join(f"{cell(('sell', N, f), ('hsell', N)):>30}" for f in SELLS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
