#!/usr/bin/env python3
"""trend_of_avg_study.py - trader 2026-10-03, idea 2: IMPROVING vs FADING at day 2. At the close of H4 bar 12 (~48h) compare the
trade's average R over its SECOND day (M1 bid closes from the bar-6 close to the bar-12 close) with its FIRST day (entry to the
bar-6 close); delta = day-2 average - day-1 average.
  SHORTS - CUT (100% at market):  fading (delta < 0 / < -0.25 / < -0.5R);  B (48h average <= +0.5R, coach queue 2);  B AND fading.
  LONGS  - ADD +50% of full size at market (shares the stop, runs to TP2):  improving (delta > 0 / > +0.25 / > +0.5R);
           C (48h average > +1R, coach queue 3);  C AND improving.
Base = live (staged 25%/bar-6, bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at
+1.5R). Each rule acts on its own side only (the other side at base). Engine pipeline/ask_side_study, SCALED. All four detectors,
7 symbols, pinned record; 2012-24 / 2025-26; full-position R; worst DD.
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
from pipeline.combo3_study import run_v                                     # noqa: E402  (gate, cut kc, add ka2 of 50%)

MODE = "SCALED"
SHORT_RULES = [("fading d<0", lambda a2, d: d < 0), ("fading d<-0.25", lambda a2, d: d < -0.25), ("fading d<-0.5", lambda a2, d: d < -0.5),
               ("B (avg<=+0.5)", lambda a2, d: a2 <= 0.5), ("B and fading", lambda a2, d: a2 <= 0.5 and d < 0)]
LONG_RULES = [("improving d>0", lambda a2, d: d > 0), ("improving d>+0.25", lambda a2, d: d > 0.25), ("improving d>+0.5", lambda a2, d: d > 0.5),
              ("C (avg>+1)", lambda a2, d: a2 > 1.0), ("C and improving", lambda a2, d: a2 > 1.0 and d > 0)]
REPORT = os.path.join(A.STUDY, "trend_of_avg_report.txt")


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u or u["k_add"] is None: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5); cum = np.cumsum(F["bc"]); n = F["n"]
    args = (F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], False)
    out = {"base": run_v(*args, None, None)[0]}
    k6 = u["k_add"]; ib = u["i0"] + 12
    k12 = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    ok = k12 is not None and 0 <= k6 < k12 < n
    a1 = cum[k6] / (k6 + 1) if ok else None
    a2 = cum[k12] / (k12 + 1) if ok else None
    d = ((cum[k12] - cum[k6]) / (k12 - k6) - a1) if ok else None
    for name, f in SHORT_RULES:
        hit = ok and not u["up"] and f(a2, d)
        out[name] = run_v(*args, k12, None)[0] if hit else out["base"]; out["h" + name] = hit and out[name] != out["base"]
    for name, f in LONG_RULES:
        hit = ok and u["up"] and f(a2, d)
        out[name] = run_v(*args, None, k12)[0] if hit else out["base"]; out["h" + name] = hit and out[name] != out["base"]
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
        L.append(f"\n{'=' * 90}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R")
        for title, rules in (("SHORTS - cut at day 2", SHORT_RULES), ("LONGS - add +50% at day 2", LONG_RULES)):
            L.append(f"\n  {title}\n  {'rule':20} {'trades':>6} {'vs base':>9} {'t':>6} {'worst DD':>8} {'helped/hurt':>12} {'per trade hit':>13}")
            for name, _ in rules:
                v = [z[4][name] for z in zs]; hz = [z[4][name] - z[4]["base"] for z in zs if z[4]["h" + name]]
                L.append(f"  {name:20} {len(hz):6d} {st.mean(v) - mb:+9.4f} {paired_t(b, v):+6.2f} {maxdd(v):8.1f} "
                         f"{sum(1 for e in hz if e > 0):5d}/{sum(1 for e in hz if e < 0):<6d} {(st.mean(hz) if hz else float('nan')):+13.3f}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
