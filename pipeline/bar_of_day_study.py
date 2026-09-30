#!/usr/bin/env python3
"""bar_of_day_study.py - coach 2026-09-30 item 13: expectancy by H4 bar of day on the full blind record.

Population: all detectors - the TrendCont blind record (7 .dk symbols) + SweepMSS / DeepFib / EMArevQ from the long blind
AA runs (exit_s025_study.aa_rows, 8 .dk symbols incl. BTCUSD). BTCUSD TrendCont is NOT included (not in the record).
Outcome: the CURRENT 50% doctrine replay (exit_param_study.replay, bank 50% at min(+1R, TP1), stop to entry), minus the
per-symbol spread drag once per trade = costed R. (Replay, not journal r_multiple: the AA rows' journal R is not all on
the same doctrine, the replay is.)
Bar of day: the SIGNAL bar's open hour, UTC (the .dk feeds are UTC; signal_time is the bar's OPEN time; the trade enters
at that bar's close, 4 h later).
Asset classes: FX = EURUSD GBPUSD USDJPY; indices = US100 US500; commodities = USOIL XAUUSD; crypto = BTCUSD.
Per cell: n, mean costed R, t of the cell's mean against the class's overall mean (one-sample, the cell's own SE),
the two dev halves 2012-2018 / 2019-2024, and 2025+.
Coverage caveat: USDJPY and XAUUSD start 2020 (absent from 2012-18); BTCUSD starts 2018 and is AA-only.

ROLLOVER BAR. The daily rollover (17:00 New York) is 21:00 UTC in US daylight time, 22:00 UTC otherwise. On the UTC H4
grid both fall INSIDE the bar that opens 20:00 UTC; a signal on that bar enters at its close, 00:00 UTC, i.e. AFTER
the rollover. The signal bar opening 16:00 UTC enters at 20:00 UTC, 1-2 h BEFORE the rollover (a trade that is open
through it from the first hour). The live "broker-midnight bar" (00:00 broker = 21:00 UTC in EEST) sits on the broker's
H4 grid, offset 2-3 h from the .dk UTC grid, so it cannot be reproduced exactly here; both UTC candidates are shown.
The .dk replay charges a FLAT median spread: the rollover spread spike that motivated the live penalty is NOT in these
numbers - this table measures price behaviour by bar, not the rollover cost.
"""
from __future__ import annotations
import os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import all_rows, run, root, ASSET, tstat, HOLDOUT   # noqa: E402

HOURS = ("00", "04", "08", "12", "16", "20")

READING = """READING (written after the run)
  * No bar-of-day cell differs from its class mean by |t| >= 2 except crypto 20:00 UTC (t -2.02, n=29, 2012-18 n=4);
    with ~30 cells one |t| near 2 is expected by chance. No cell is consistent in sign across both dev halves AND 2025+
    with a size worth a rule: the 04:00 UTC bar is the most consistent weak spot overall (-0.063R, t -1.43; halves
    -0.053 / -0.067, 2025+ -0.071), and it is not the rollover bar.
  * ROLLOVER: identified as the signal bar OPENING 20:00 UTC (contains 21:00/22:00 UTC; enters 00:00 UTC). On the blind
    record it is +0.060R (n=453, both halves positive: +0.151 / +0.029, 2025+ +0.035) - NOT negative. The pre-rollover
    bar (16:00 UTC open, enters 20:00 UTC) is -0.003R (n=651). The claim '-0.06R on n=204' is not reproduced by any
    population here; the nearest is FX all detectors, 16:00 UTC open: n=202, -0.098R (halves +0.115 / -0.157, i.e. not
    stable), or FX TrendCont 16:00: n=145, -0.064R. If the claim meant the live broker-midnight bar, that bar is on the
    broker's grid and cannot be isolated in .dk data.
  * These are PRICE-behaviour numbers under a flat spread charge. The live penalty for the broker-midnight bar is about
    the rollover SPREAD (0.5-1R on exotics, docs/live-runbook.md) - a cost this table cannot see, and does not refute."""


def cell(xs):
    return (f"{len(xs):5d} {st.mean(xs):+7.3f}" if xs else f"{0:5d} {'-':>7}")


def table(title, rows):
    allr = [x["cr"] for x in rows]
    mu = st.mean(allr)
    print(f"\n{title}   overall n={len(rows)} mean {mu:+.4f}R costed")
    print(f"  {'bar (UTC open)':14} {'n':>5} {'mean':>7} {'t vs all':>8} | {'2012-18 n':>9} {'mean':>7} | "
          f"{'2019-24 n':>9} {'mean':>7} | {'2025+ n':>7} {'mean':>7}")
    for h in HOURS:
        c = [x for x in rows if x["t"][11:13] == h]
        if not c: continue
        v = [x["cr"] for x in c]
        h1 = [x["cr"] for x in c if x["t"][:4] <= "2018"]
        h2 = [x["cr"] for x in c if "2019" <= x["t"][:4] < HOLDOUT]
        ho = [x["cr"] for x in c if x["t"][:4] >= HOLDOUT]
        print(f"  {h + ':00':14} {len(v):5d} {st.mean(v):+7.3f} {tstat(v, mu):+8.2f} | {cell(h1):>17} | {cell(h2):>17} | {cell(ho):>15}")
    odd = [x for x in rows if x["t"][11:13] not in HOURS]
    if odd: print(f"  (off-grid signal hours: {len(odd)} rows)")


def main():
    print(__doc__)
    rows = []
    for x in all_rows():
        r = run(x, 0.5)
        if r is None: continue
        rows.append({"t": x["t"], "sym": root(x["sym"]), "strat": x["strat"], "cr": r - x["cost"]})
    table("ALL CLASSES, ALL DETECTORS", rows)
    for cls in ("FX", "indices", "commodities", "crypto"):
        table(f"{cls.upper()}  ({', '.join(k for k, v in ASSET.items() if v == cls)}), all detectors",
              [x for x in rows if ASSET[x["sym"]] == cls])
    print("\n" + "=" * 110)
    print("ROLLOVER-BAR LOCATOR: where could 'the rollover bar is -0.06R on n=204' come from? n and mean costed R of the")
    print("20:00 and 16:00 UTC signal bars in candidate populations (locator only - no population was chosen on it):")
    pops = {"all detectors, all": rows,
            "all detectors, dev 2012-24": [x for x in rows if x["t"][:4] < HOLDOUT],
            "TrendCont record, all": [x for x in rows if x["strat"] == "TrendCont"],
            "TrendCont record, dev 2012-24": [x for x in rows if x["strat"] == "TrendCont" and x["t"][:4] < HOLDOUT],
            "other 3 detectors, all": [x for x in rows if x["strat"] != "TrendCont"]}
    for cls in ("FX", "indices", "commodities", "crypto"):
        pops[f"{cls}, all detectors"] = [x for x in rows if ASSET[x["sym"]] == cls]
        pops[f"{cls}, TrendCont"] = [x for x in rows if ASSET[x["sym"]] == cls and x["strat"] == "TrendCont"]
    for name, p in pops.items():
        s = []
        for h in ("16", "20"):
            v = [x["cr"] for x in p if x["t"][11:13] == h]
            s.append(f"{h}:00 n={len(v):4d} {st.mean(v) if v else 0:+.3f}")
        print(f"  {name:32} {s[0]}   {s[1]}")
    for det in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ"):
        table(f"per detector: {det}", [x for x in rows if x["strat"] == det])
    print("\n" + READING)


if __name__ == "__main__": main()
