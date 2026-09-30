#!/usr/bin/env python3
"""load_filter_retro.py - coach 2026-09-24 item 4: retro-test the load filter with D1 extension as the primary sort.

Filter as ruled: hard drops (spread > 0.05R, release < 12h, correlated cluster >= 1.5%), then rank by D1 extension
descending, (symbol x bar-of-day) as tiebreak. Bars: >= 60% of cards removed AND >= 70% of the trader's taken R kept.

What this retro-test can and cannot do on W11-W14:
  * the RANKER is fully reconstructible - D1 extension is computed from bars the same way the live card now prints it;
  * the HARD DROPS are not. Those windows are .dk imports with no spread, the correlated-cluster test depends on what
    was open in his session at that moment, and only the event feed is reconstructible. So this reports the ranker
    alone, which is the conservative reading: the hard drops can only remove more cards, and the cards they remove are
    ones he was told not to take anyway.
Reported as a curve over keep-fraction, because the ruling fixes the sort but not the cut.
"""
from __future__ import annotations
import csv, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step_study import atr14                             # noqa: E402
from pipeline.trendcont_step13_study import load, ema                       # noqa: E402
from pipeline.trader_selection_study import CJ, WINDOWS                     # noqa: E402


def rows():
    out = []
    for label, dec, base in WINDOWS:
        dp, bp = os.path.join(CJ, dec), os.path.join(CJ, base)
        if not (os.path.exists(dp) and os.path.exists(bp)): continue
        bl = {r["signal_time"]: r for r in csv.DictReader(open(bp, newline="", encoding="ascii", errors="replace"))}
        sym = dec.split("_")[0]
        d1 = load(sym, "d1")
        if not d1: continue
        closes = [b[4] for b in d1]; e20 = ema(closes, 20); days = [b[0][:10] for b in d1]
        for r in csv.DictReader(open(dp, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont": continue
            d = r.get("decision", "")
            took = d.startswith("approved")
            if not took and (d != "skipped" or r.get("skip_reason") in ("8", "9", "0")): continue
            b = bl.get(r["signal_time"])
            if not b: continue
            jd = max((k for k, x in enumerate(days) if x < r["signal_time"][:10]), default=-1)   # 2026-09-30: previous CLOSED day (was <=)
            if jd < 40: continue
            a = atr14([[0] + x[1:] for x in d1], jd)
            if a <= 0: continue
            up = r["direction"].upper().startswith("B")
            try: own = float(r["r_multiple"]) if took and r.get("r_multiple") else 0.0
            except ValueError: own = 0.0
            out.append({"ext": ((closes[jd] - e20[jd]) / a) * (1 if up else -1), "took": took, "own_r": own,
                        "sym": sym, "hour": r["signal_time"][11:13], "win": label,
                        "base_r": float(b["r_multiple"]) if b.get("r_multiple") else 0.0})
    return out


def main():
    rs = rows()
    taken = [r for r in rs if r["took"]]
    tot_r = sum(r["own_r"] for r in taken)
    print(f"{len(rs)} cards over W11-W14 ({len(taken)} taken, his realised R = {tot_r:+.2f})")
    print("ranker = D1 extension descending; tiebreak (symbol x bar-of-day); hard drops not reconstructible - see the docstring\n")
    order = sorted(rs, key=lambda r: (-r["ext"], r["sym"], r["hour"]))
    print(f"  {'keep':>6} {'cards kept':>11} {'removed':>8} | {'his R kept':>11} {'retained':>9} | {'taken kept':>10} | bars met")
    for frac in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50):
        k = max(1, round(len(order) * frac)); kept = order[:k]
        kr = sum(r["own_r"] for r in kept if r["took"]); nt = sum(1 for r in kept if r["took"])
        rem = (1 - k / len(order)) * 100; ret = (kr / tot_r * 100) if tot_r else 0.0
        ok = "YES" if rem >= 60 and ret >= 70 else ""
        print(f"  {frac*100:>5.0f}% {k:>11} {rem:>7.0f}% | {kr:>+10.2f} {ret:>8.0f}% | {nt:>4}/{len(taken):<5} | {ok}")
    print("\nwhat the filter would have removed, by whether he took it:")
    for frac in (0.25, 0.40):
        k = round(len(order) * frac); dropped = order[k:]
        dt = [r for r in dropped if r["took"]]
        print(f"  keep {frac*100:.0f}%: drops {len(dropped)} cards - {len(dt)} of them his takes, worth {sum(r['own_r'] for r in dt):+.2f}R "
              f"(mean {sum(r['own_r'] for r in dt)/max(1,len(dt)):+.3f}R); the {len(dropped)-len(dt)} he skipped were worth "
              f"{sum(r['base_r'] for r in dropped if not r['took']):+.2f}R blind")


if __name__ == "__main__": main()
