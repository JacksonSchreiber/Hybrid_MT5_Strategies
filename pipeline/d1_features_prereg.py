#!/usr/bin/env python3
"""d1_features_prereg.py - coach 2026-09-30 item 15: correctly-aligned D1 features, ONE pass, PRE-REGISTERED.

=== PRE-REGISTRATION (fixed in this header before any outcome was computed; the code implements exactly this) ===
Population  all detectors: the TrendCont blind record (7 .dk symbols) + SweepMSS / DeepFib / EMArevQ from the long
            blind AA runs (aa_rows, 8 .dk symbols incl. BTCUSD), pooled.
Outcome     50% doctrine replay (exit_param_study.replay: bank 50% at min(+1R, TP1), stop to entry) minus the per-symbol
            spread drag once = costed R.
Index rule  EVERY feature is computed on the previous closed D1 bar: jd = the last D1 bar dated STRICTLY BEFORE the
            signal's UTC day (d < day). Rows with no such bar, or too little D1 history for a feature, are excluded
            from that feature only (counts reported).
Features    F1 D1 extension   (close[jd] - EMA20[jd]) / ATR14[jd], signed by direction (+ = extended WITH the trade).
                              EMA as trendcont_step13_study.ema over the whole D1 series; ATR14 = MT5 iATR (simple
                              14-bar mean of true range). Needs jd >= 40.
            F2 D1 staircase   trendcont_step13_study.step1 with N = 10 (the step-1 study's primary N): 'intact' vs
                              'broken' over the last 10 closed D1 bars. Binary - its "terciles" are its two classes.
            F3 regime age     D1 bars the frozen regime tag (trader_selection_study.regime_series: TREND_UP / DOWN /
                              CHOP) has held at jd, whatever the tag. Rows with a blank tag (< 210 D1 bars) excluded.
            F4 D1 ATR pct     ATR14[jd] ranked against ATR14 over the 252 D1 bars before jd (share strictly lower).
                              Needs jd >= 266.
Split       terciles; the two cutpoints are the 1/3 and 2/3 quantiles of the feature over the POOLED DEV rows
            (2012-2024), fixed once and reused unchanged in the halves and the holdout.
Pass bar    on dev 2012-2024: (best tercile mean - worst tercile mean) >= +0.15R costed, AND the SAME two terciles
            (best minus worst, as identified on the whole dev set) differ with a positive sign in BOTH dev halves,
            2012-2018 and 2019-2024 (split by calendar year).
Holdout     2025+ is printed ONLY for features that pass. Nothing is selected or re-cut after this pass.
Information only (not tested): the same tercile table for the TrendCont record alone.
=== END PRE-REGISTRATION ===

The >=10 live-card reconciliation of D1 numbers is PENDING (done separately).
"""
from __future__ import annotations
import os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import all_rows, run, prev_day, atr_sma, root, HOLDOUT, tstat   # noqa: E402
from pipeline.trendcont_step13_study import load, ema, step1                                     # noqa: E402
from pipeline.trader_selection_study import regime_series                                        # noqa: E402

FEATS = ("F1 D1 extension (signed, ATR)", "F2 D1 staircase N=10", "F3 regime age (D1 bars)", "F4 D1 ATR14 percentile (252d)")


def d1_cache():
    out = {}
    for s in ("EURUSD", "GBPUSD", "USDJPY", "US100", "US500", "USOIL", "XAUUSD", "BTCUSD"):
        d1 = load(f"{s}.dk", "d1")
        c = [b[4] for b in d1]
        reg = regime_series(d1)
        age = [0] * len(reg)
        for k in range(1, len(reg)): age[k] = age[k - 1] + 1 if reg[k] and reg[k] == reg[k - 1] else 0
        atr = [atr_sma(d1, j) for j in range(len(d1))]
        out[s] = {"d1": d1, "days": [b[0][:10] for b in d1], "c": c, "e20": ema(c, 20), "reg": reg, "age": age, "atr": atr}
    return out


def features(x, D):
    jd = prev_day(D["days"], x["t"])
    f = {}
    if jd < 0: return f, jd
    up = x["up"]
    if jd >= 40 and D["atr"][jd] > 0:
        f[FEATS[0]] = (D["c"][jd] - D["e20"][jd]) / D["atr"][jd] * (1 if up else -1)
    s1 = step1(D["d1"], jd, up, 10)
    if s1: f[FEATS[1]] = s1
    if D["reg"][jd]: f[FEATS[2]] = D["age"][jd]
    if jd >= 266 and D["atr"][jd] > 0:
        prior = D["atr"][jd - 252:jd]
        f[FEATS[3]] = sum(1 for a in prior if a < D["atr"][jd]) / len(prior)
    return f, jd


def q(vals, p):
    v = sorted(vals); k = p * (len(v) - 1); lo = int(k)
    return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (k - lo)


def main():
    print(__doc__)
    D = d1_cache()
    rows, lost = [], defaultdict(int)
    for x in all_rows():
        r = run(x, 0.5)
        if r is None: continue
        f, jd = features(x, D[root(x["sym"])])
        if jd < 0: lost["no prior D1 bar"] += 1
        for k in FEATS:
            if k not in f: lost[k] += 1
        rows.append({"t": x["t"], "strat": x["strat"], "cr": r - x["cost"], **f})
    dev = [x for x in rows if x["t"][:4] < HOLDOUT]
    print(f"rows {len(rows)} (dev 2012-2024 {len(dev)}, holdout {HOLDOUT}+ {len(rows) - len(dev)})")
    for k, v in lost.items(): print(f"  excluded from {k}: {v}")

    verdicts = {}
    for feat in FEATS:
        fd = [x for x in dev if feat in x]
        if feat == FEATS[1]:
            groups = ["intact", "broken"]; key = lambda v: v
            cuts = "binary"
        else:
            c1, c2 = q([x[feat] for x in fd], 1 / 3), q([x[feat] for x in fd], 2 / 3)
            groups = ["T1 low", "T2 mid", "T3 high"]
            key = lambda v, c1=c1, c2=c2: "T1 low" if v <= c1 else ("T2 mid" if v <= c2 else "T3 high")
            cuts = f"cutpoints {c1:.3f} / {c2:.3f}"
        print("\n" + "=" * 112); print(f"{feat}   ({cuts}, fixed on pooled dev)")
        print(f"  {'group':10} {'dev n':>6} {'mean':>7} {'t':>6} | {'2012-18 n':>9} {'mean':>7} | {'2019-24 n':>9} {'mean':>7} || "
              f"{'TrendCont dev n':>15} {'mean':>7}  (info)")
        means, h1m, h2m = {}, {}, {}
        for g in groups:
            c = [x["cr"] for x in fd if key(x[feat]) == g]
            a = [x["cr"] for x in fd if key(x[feat]) == g and x["t"][:4] <= "2018"]
            b = [x["cr"] for x in fd if key(x[feat]) == g and x["t"][:4] >= "2019"]
            tc = [x["cr"] for x in fd if key(x[feat]) == g and x["strat"] == "TrendCont"]
            means[g] = st.mean(c); h1m[g] = st.mean(a) if a else float("nan"); h2m[g] = st.mean(b) if b else float("nan")
            print(f"  {g:10} {len(c):6d} {st.mean(c):+7.3f} {tstat(c):+6.2f} | {len(a):9d} {h1m[g]:+7.3f} | {len(b):9d} {h2m[g]:+7.3f} || "
                  f"{len(tc):15d} {st.mean(tc) if tc else 0:+7.3f}")
        best = max(groups, key=lambda g: means[g]); worst = min(groups, key=lambda g: means[g])
        spread = means[best] - means[worst]
        d1_, d2_ = h1m[best] - h1m[worst], h2m[best] - h2m[worst]
        ok = spread >= 0.15 and d1_ > 0 and d2_ > 0
        verdicts[feat] = ok
        print(f"  best {best} - worst {worst}: dev {spread:+.3f}R (bar +0.15) | 2012-18 {d1_:+.3f} | 2019-24 {d2_:+.3f} "
              f"-> {'PASS' if ok else 'FAIL'}")
        if ok:
            ho = [x for x in rows if x["t"][:4] >= HOLDOUT and feat in x]
            print(f"  HOLDOUT {HOLDOUT}+ (same cutpoints):")
            for g in groups:
                c = [x["cr"] for x in ho if key(x[feat]) == g]
                print(f"    {g:10} n={len(c):4d} mean {st.mean(c) if c else 0:+.3f}")
            hb = [x["cr"] for x in ho if key(x[feat]) == best]; hw = [x["cr"] for x in ho if key(x[feat]) == worst]
            if hb and hw: print(f"    best - worst on holdout: {st.mean(hb) - st.mean(hw):+.3f}R")
    print("\n" + "=" * 112)
    print("VERDICTS: " + "; ".join(f"{k}: {'PASS' if v else 'FAIL'}" for k, v in verdicts.items()))


if __name__ == "__main__": main()
