#!/usr/bin/env python3
"""bank_ladder_detectors.py - coach 2026-09-30 item 10: the bank ladder split per detector.

Ladder: bank 50 / 33 / 25 / 15 / 0% at the doctrine point min(+1R, TP1), stop to entry at that same point (the 0% rung
is study F: nothing banked, stop to entry, full position to TP2). exit_param_study.replay, bar rules unchanged; costed
= minus the per-symbol spread drag once per trade (spread_drag.json; 0.007R default where a root is missing).

Populations: SweepMSS, DeepFib, EMArevQ from the long blind AA runs (exit_s025_study.aa_rows, 8 .dk symbols incl.
BTCUSD); TrendCont (the blind record, 7 symbols) recomputed here with the same replay as the reference.
Periods: dev 2012-2024 and the sealed holdout 2025+ shown SEPARATELY - nothing is selected, both are printed.
Per rung: mean costed R, difference vs 50%, paired t vs 50% (per trade), worst drawdown of the costed series in signal
order, n. EMArevQ is also split by the row's TP1 distance: where tp1R < 1 the bank (and the stop move) fire at TP1,
BELOW +1R, so its ladder is a different trade-off.

Conclusion per detector: does sign(25% - 50%) on dev match TrendCont's (positive on the record)?

NB the paired t is IDENTICAL on every rung of a population: bank point and stop move are the same for every rung, so
per trade (rung - 50%) = (0.5 - f) x (runner R - bank R), exactly linear in f. The ladder has one degree of freedom per
population - the sign of the mean (runner - bank) - and the rungs only scale it (and the drawdown).
"""
from __future__ import annotations
import os, statistics as st, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import all_rows, run, HOLDOUT            # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t                       # noqa: E402

LADDER = (0.50, 0.33, 0.25, 0.15, 0.0)


def tp1R(x):
    if not x["tp1"]: return None
    return (x["tp1"] - x["e"]) * (1 if x["up"] else -1) / abs(x["e"] - x["s"])


def table(label, rows):
    if not rows: print(f"  {label}: no rows"); return None
    res = {f: [run(x, f) for x in rows] for f in LADDER}
    keep = [k for k in range(len(rows)) if all(res[f][k] is not None for f in LADDER)]
    cost = [rows[k]["cost"] for k in keep]
    base = [res[0.5][k] for k in keep]
    print(f"  {label}  (n={len(keep)})")
    out = {}
    for f in LADDER:
        v = [res[f][k] for k in keep]; vc = [a - c for a, c in zip(v, cost)]
        t = paired_t(base, v) if f != 0.5 else 0.0
        out[f] = st.mean(vc)
        print(f"    bank {int(round(f * 100)):>2}%   costed {st.mean(vc):+.4f}   vs 50% {st.mean(v) - st.mean(base):+.4f}   "
              f"t {t:+5.2f}   worst DD {maxdd(vc):6.1f}R")
    return out


def main():
    print(__doc__)
    rows = all_rows()
    signs = {}
    for det in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ"):
        rr = [x for x in rows if x["strat"] == det]
        print("=" * 100); print(det)
        for per, f in (("dev 2012-2024", lambda x: x["t"][:4] < HOLDOUT), (f"holdout {HOLDOUT}+", lambda x: x["t"][:4] >= HOLDOUT)):
            o = table(per, [x for x in rr if f(x)])
            if o and per.startswith("dev"): signs[det] = o[0.25] - o[0.5]
        if det == "EMArevQ":
            lo = [x for x in rr if tp1R(x) is not None and tp1R(x) < 1 - 1e-9]
            print(f"\n  EMArevQ rows with tp1R < 1 (bank fires at TP1 below +1R): {len(lo)} of {len(rr)}"
                  f" (median tp1R {st.median(tp1R(x) for x in lo):.2f})" if lo else "")
            for per, f in (("dev 2012-2024", lambda x: x["t"][:4] < HOLDOUT), (f"holdout {HOLDOUT}+", lambda x: x["t"][:4] >= HOLDOUT)):
                table(f"tp1R < 1, {per}", [x for x in lo if f(x)])
                table(f"tp1R >= 1, {per}", [x for x in rr if f(x) and not (tp1R(x) is not None and tp1R(x) < 1 - 1e-9)])
        print()
    print("=" * 100)
    print("SIGN OF (25% - 50%) ON DEV 2012-2024, costed R/trade:")
    ref = signs.get("TrendCont", 0.0)
    for det, d in signs.items():
        print(f"  {det:10} {d:+.4f}  " + ("(reference)" if det == "TrendCont" else
                                          ("matches TrendCont" if (d > 0) == (ref > 0) else "does NOT match TrendCont")))
    print("\n(holdout per-detector n is small - printed for completeness, not for a verdict)")


if __name__ == "__main__": main()
