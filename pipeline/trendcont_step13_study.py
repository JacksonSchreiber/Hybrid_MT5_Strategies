#!/usr/bin/env python3
"""trendcont_step13_study.py - coach 2026-09-24 study 1: steps 1 and 3 through the same pipeline as item 6.

Same population (approved TrendCont rows of the graded record, n=2,346), same outcome (journal r_multiple; the +1R
bank counts as hit when mfe_r >= 1.0), same reporting (mean R +- SE, bank hit-rate, n per cell, both halves).

  Step 1 - D1 staircase. The reference is the most recent D1 fractal swing of the OPPOSING kind (a swing low for a BUY)
  that had already formed N bars before the signal; "broken" means the last N D1 bars traded through it. N = 5 and 10.

  Step 3 - how far the pullback got from the H4 EMA20 before the trigger: the run of bars immediately before the signal
  bar that CLOSED beyond the EMA20 on the pullback side (bucketed 0-2 / 3-5 / 6+), and the deepest excursion beyond it
  in ATR(14) over that run (bucketed <=0.5 / 0.5-1 / >1).

Bars: a cell <= -0.10R against its best neighbour is a veto candidate; otherwise it is a quality mark.
"""
from __future__ import annotations
import csv, glob, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import overlays as OV                                             # noqa: E402
from pipeline.trendcont_step_study import REC, atr14, cell, table           # noqa: E402

# D1 BAR-INDEX RULE (fixed 2026-09-30, pipeline/d1_lookahead_check.py): a daily feature for an H4 signal uses the last
# D1 bar dated STRICTLY BEFORE the signal's day (d < day) - the previous CLOSED day, what the EA sees. The published
# report used d <= day (the signal day's own, still-forming D1 bar = look-ahead). D1_STRICT = False reproduces it;
# pipeline/d1_rerun_studies.py prints both side by side.
D1_STRICT = True

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BARS = {tf: os.path.join(ROOT, "data", "study", tf) for tf in ("h4", "d1")}


def load(sym: str, tf: str) -> list[list]:
    p = os.path.join(BARS[tf], f"{sym}.csv")
    if not os.path.exists(p): return []
    out = []
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            out.append([r["time"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])])
    return out


def ema(vals: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1); out = []; e = vals[0]
    for v in vals: e = v * k + e * (1 - k); out.append(e)
    return out


def step1(d1: list[list], jd: int, up: bool, n: int) -> str | None:
    """intact / broken: did the last n D1 bars trade through the swing that defined the trend's last step?"""
    if jd < 60 or jd - n < 30: return None
    ref_bars = d1[:jd - n + 1]
    sw = OV.swings(ref_bars, days=90, n=2, bars_per_day=1)
    ref = next((s for s in sw if s["kind"] == ("lo" if up else "hi")), None)
    if ref is None: return None
    seg = d1[jd - n + 1:jd + 1]
    hit = min(b[3] for b in seg) < ref["p"] if up else max(b[2] for b in seg) > ref["p"]
    return "broken" if hit else "intact"


def step3(h4: list[list], i: int, up: bool) -> tuple[str, str] | None:
    """(run of bars closed beyond the EMA20 before the trigger, deepest excursion beyond it in ATR)."""
    if i < 60: return None
    e20 = ema([b[4] for b in h4[:i + 1]], 20)
    a = atr14([[0] + b[1:] for b in h4], i)                    # atr14 wants [t,o,h,l,c]; the time field is unused
    if a <= 0: return None
    run, depth = 0, 0.0
    for j in range(i - 1, max(0, i - 40), -1):                 # bars BEFORE the trigger bar
        beyond = h4[j][4] < e20[j] if up else h4[j][4] > e20[j]
        if not beyond: break
        run += 1
        depth = max(depth, (e20[j] - h4[j][3]) / a if up else (h4[j][2] - e20[j]) / a)
    rb = "0-2" if run <= 2 else ("3-5" if run <= 5 else "6+")
    db = "<=0.5 ATR" if depth <= 0.5 else ("0.5-1 ATR" if depth <= 1.0 else ">1 ATR")
    return rb, db


def main():
    rows, excl = [], defaultdict(int)
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]
        h4, d1 = load(sym, "h4"), load(sym, "d1")
        if not h4 or not d1: excl[f"{sym}: no bars"] += 1; continue
        ih = {b[0][:16]: k for k, b in enumerate(h4)}
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            i = ih.get(r["signal_time"][:16])
            if i is None: excl[f"{sym}: signal bar outside the H4 file"] += 1; continue
            try: rm, mfe = float(r["r_multiple"]), float(r.get("mfe_r") or 0)
            except (TypeError, ValueError): excl[f"{sym}: unusable row"] += 1; continue
            up = r["direction"].upper().startswith("B")
            day = r["signal_time"][:10]
            jd = max((k for k, b in enumerate(d1) if (b[0][:10] < day if D1_STRICT else b[0][:10] <= day)), default=-1)
            s3 = step3(h4, i, up)
            if s3 is None: excl[f"{sym}: no EMA/ATR window"] += 1; continue
            rows.append({"r": rm, "bank": mfe >= 1.0, "t": r["signal_time"], "sym": sym,
                         "s1_5": step1(d1, jd, up, 5), "s1_10": step1(d1, jd, up, 10),
                         "run": s3[0], "depth": s3[1]})
    print(f"approved TrendCont rows classified: {len(rows)}")
    for k, v in sorted(excl.items()): print(f"  excluded {v:>5}  ({k})")

    for n in (5, 10):
        k = f"s1_{n}"
        table([r for r in rows if r[k]], lambda r: r[k], ["intact", "broken"],
              f"STEP 1 - D1 staircase, last {n} D1 bars before the signal")
    table(rows, lambda r: r["run"], ["0-2", "3-5", "6+"], "STEP 3 - bars closed beyond the H4 EMA20 before the trigger")
    table(rows, lambda r: r["depth"], ["<=0.5 ATR", "0.5-1 ATR", ">1 ATR"], "STEP 3 - deepest excursion beyond the EMA20")
    ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
    for name, half in ((f"FIRST half (to {mid[:10]})", lambda r: r["t"] <= mid), ("SECOND half", lambda r: r["t"] > mid)):
        table([r for r in rows if r["s1_10"]], lambda r: r["s1_10"], ["intact", "broken"], f"STEP 1 (N=10) - {name}", half)
        table(rows, lambda r: r["run"], ["0-2", "3-5", "6+"], f"STEP 3 run - {name}", half)


if __name__ == "__main__": main()
