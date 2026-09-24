#!/usr/bin/env python3
"""trendcont_step_study.py - coach 2026-09-24 item 6, PRE-REGISTERED.

Does the record support the amended TrendCont thresholds? Two questions, both measured on approved TrendCont rows of
the graded record (data/study/trendcont/<SYM>.csv), with the outcome taken from the journal (r_multiple; the +1R bank
counts as hit when mfe_r >= 1.0):

  Step 5 - obstacles between entry and +1R, from the EA's OWN swing set (overlays.swings = DrawSwingMarkers, 5-bar
  fractal 2L/2R over a 14-day window, computed on the bars up to and including the signal bar):
      none      - no swing of the blocking kind in (entry, +1R]
      single    - one level (cluster of swings within 0.1R of each other)
      repeated  - a level the set touches >= 2 times within 0.1R  <- the coach's "wall"
  bucketed by the nearest level's distance: <= 0.5R vs 0.5-1R.
  Pre-registered bars: "repeated" <= -0.10R against "none" confirms the veto; "single" within +-0.05R of "none"
  confirms the quality mark; anything else and the thresholds move to what the cells say.

  Step 2 - the pullback's largest bar as a multiple of H4 ATR(14) (Wilder, at the signal bar), bucketed
  < 1.5 / 1.5-2 / >= 2. Pullback = from the most recent swing the price retraced FROM (a swing high for a BUY) to the
  signal bar - the same window the live setup card now prints.

Both tables are also split into halves by signal date, because a threshold that only works in one half is noise.

Bars come from data/study/h4/<SYM>.csv (pipeline/dump_h4_dk.py, a read-only attach to the running terminal), falling
back to the old partial exports in data/backtests/emarev_inv. Rows whose signal bar is outside the available history
are reported as excluded, never silently dropped.
"""
from __future__ import annotations
import argparse, csv, glob, os, statistics as st, sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import overlays as OV                                             # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REC = os.path.join(ROOT, "data", "study", "trendcont")
H4 = os.path.join(ROOT, "data", "study", "h4")                  # pipeline/dump_h4_dk.py parks the full .dk history here
H4_FALLBACK = os.path.join(ROOT, "data", "backtests", "emarev_inv")
CLUSTER_R = 0.10          # two swings within 0.1R are the same level touched twice (the coach's definition)


def load_bars(sym: str) -> list[list]:
    """[[key, o, h, l, c]] oldest first, key = 'YYYY.MM.DD HH:MM' (the journal's own clock)."""
    p = os.path.join(H4, f"{sym}.csv")
    if not os.path.exists(p): p = os.path.join(H4_FALLBACK, f"{sym}.h4.csv")
    if not os.path.exists(p): return []
    out = []
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            try: out.append([r["time"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])])
            except (KeyError, ValueError): pass
    return out


def atr14(bars: list[list], i: int, n: int = 14) -> float:
    """Wilder ATR at bar i, same definition as the EA's iATR."""
    if i < n: return 0.0
    tr = []
    for j in range(max(1, i - 200), i + 1):
        h, l, pc = bars[j][2], bars[j][3], bars[j - 1][4]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(tr) < n: return 0.0
    a = sum(tr[:n]) / n
    for x in tr[n:]: a = (a * (n - 1) + x) / n
    return a


def classify(row: dict, bars: list[list], i: int) -> dict | None:
    try:
        e, s = float(row["orig_entry"]), float(row["orig_sl"])
        r_mult = float(row["r_multiple"]); mfe = float(row.get("mfe_r") or 0)
    except (TypeError, ValueError): return None
    R = abs(e - s)
    if R <= 0: return None
    up = row["direction"].upper().startswith("B")
    tgt = e + R if up else e - R
    win = bars[:i + 1]
    sw = OV.swings(win, 14, 2)
    kind = "hi" if up else "lo"
    obs = [x for x in sw if x["kind"] == kind and (e < x["p"] <= tgt if up else tgt <= x["p"] < e)]
    # cluster obstacles that sit within 0.1R of each other: a level touched twice is one wall, not two
    clusters: list[list[float]] = []
    for p in sorted(x["p"] for x in obs):
        if clusters and abs(p - clusters[-1][-1]) <= CLUSTER_R * R: clusters[-1].append(p)
        else: clusters.append([p])
    if not clusters: klass, near = "none", None
    else:
        klass = "repeated" if any(len(c) >= 2 for c in clusters) else "single"
        near = min(abs(sum(c) / len(c) - e) / R for c in clusters)
    # the trend's own last extreme (the swing the pullback retraced from) - the amended rule says it is NOT a wall
    origin = next((x for x in sw if x["kind"] == kind), None)
    obs_ex = [x for x in obs if not (origin and abs(x["p"] - origin["p"]) < 1e-12)]
    klass_ex = "none" if not obs_ex else ("repeated" if any(
        sum(1 for y in obs_ex if abs(y["p"] - x["p"]) <= CLUSTER_R * R) >= 2 for x in obs_ex) else "single")
    # step 2: the pullback leg, largest bar range / ATR
    a = atr14(bars, i)
    j0 = None
    for x in sw:
        if x["kind"] == kind and x["bars_ago"] > 0: j0 = i - x["bars_ago"]; break
    if j0 is None or j0 < 0: j0 = max(0, i - 7)
    mx = max(((bars[j][2] - bars[j][3]) / a) for j in range(j0, i + 1)) if a > 0 else 0.0
    return {"klass": klass, "klass_ex": klass_ex, "near": near, "slam": mx, "r": r_mult, "bank": mfe >= 1.0,
            "t": row["signal_time"], "sym": row["symbol"]}


def cell(rows: list[dict]) -> str:
    """mean R +- standard error, bank hit-rate, n. The SE matters: these cells differ by less than 0.2R and the
    per-trade spread is over 1R, so a difference under ~2 SE is not a difference."""
    if not rows: return f"{'-':>8} {'':>9} {'-':>6} {0:>5}"
    rs = [r["r"] for r in rows]
    se = (st.stdev(rs) / len(rs) ** 0.5) if len(rs) > 1 else 0.0
    return f"{st.mean(rs):>8.3f} +-{se:<7.3f} {sum(r['bank'] for r in rows)/len(rows)*100:>5.0f}% {len(rows):>5}"


def table(rows: list[dict], keyf, order, title, half=None):
    if half: rows = [r for r in rows if half(r)]
    print(f"\n{title}   (mean R +- SE | +1R bank | n)")
    for k in order:
        sub = [r for r in rows if keyf(r) == k]
        print(f"  {k:<26} {cell(sub)}")


def main():
    ap = argparse.ArgumentParser(); ap.parse_args()
    rows, excluded = [], defaultdict(int)
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]
        bars = load_bars(sym)
        idx = {b[0][:16]: k for k, b in enumerate(bars)}
        rr = [r for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace"))
              if r.get("strategy") == "TrendCont" and r.get("decision") == "approved"]
        if not bars:
            excluded[f"{sym}: no H4 file"] += len(rr); continue
        for r in rr:
            i = idx.get(r["signal_time"][:16])
            if i is None: excluded[f"{sym}: signal bar outside the H4 file"] += 1; continue
            if i < 60: excluded[f"{sym}: less than 60 bars of history"] += 1; continue
            c = classify(r, bars, i)
            if c is None: excluded[f"{sym}: unusable row"] += 1
            else: rows.append(c)
    print(f"approved TrendCont rows classified: {len(rows)}")
    for k, v in sorted(excluded.items()): print(f"  excluded {v:>5}  ({k})")
    by_sym = defaultdict(int)
    for r in rows: by_sym[r["sym"]] += 1
    print("  per symbol: " + ", ".join(f"{k} {v}" for k, v in sorted(by_sym.items())))

    ORD5 = ["none", "single", "repeated"]
    table(rows, lambda r: r["klass"], ORD5, "STEP 5 - obstacles between entry and +1R (EA swing set)")
    table(rows, lambda r: r["klass_ex"], ORD5, "STEP 5 - same, EXCLUDING the swing the pullback retraced from (the amended rule)")
    near = [r for r in rows if r["near"] is not None]
    table(near, lambda r: f"{r['klass']} {'<=0.5R' if r['near'] <= 0.5 else '0.5-1R'}",
          [f"{k} {b}" for k in ("single", "repeated") for b in ("<=0.5R", "0.5-1R")], "STEP 5 - by nearest level's distance")
    ORD2 = ["<1.5", "1.5-2", ">=2"]
    slam = lambda r: "<1.5" if r["slam"] < 1.5 else ("1.5-2" if r["slam"] < 2 else ">=2")
    table(rows, slam, ORD2, "STEP 2 - largest pullback bar / H4 ATR(14)")
    ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
    for name, half in (("FIRST half (to " + mid[:10] + ")", lambda r: r["t"] <= mid), ("SECOND half", lambda r: r["t"] > mid)):
        table(rows, lambda r: r["klass"], ORD5, f"STEP 5 - {name}", half)
        table(rows, slam, ORD2, f"STEP 2 - {name}", half)


if __name__ == "__main__": main()
