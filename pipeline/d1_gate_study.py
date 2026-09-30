#!/usr/bin/env python3
"""d1_gate_study.py - coach 2026-09-24 stage 2, PRE-REGISTERED: should TrendCont gate on D1 extension >= +0.5 ATR?

Three confirmations, reported together, in the coach's order:

  (a) PRE-2020 slice. Above-minus-below at the 0.5 cut must be >= +0.15R with the same sign in both halves.
      On disk: EURUSD 2017-19 is the only one of the three named symbols whose RECORD covers those years (GBPUSD's
      record jumps 2016 -> 2020, USDJPY's starts 2020). GBPUSD 2014-16 is reported beside it as the other pre-2020
      sample that exists, clearly labelled, rather than leaving the test on one symbol without saying so.

  (b) STUDY-TEMPLATE BAR on the gated population, full record, COSTS ON: mean R >= +0.15R, n >= 150, both halves
      positive. Costs are the per-symbol scaled spread discount from data/study/spread_drag.json - the .dk imports
      carry no spread, so every figure in this program is a mid-price figure until that is charged.

  (c) HIS TAKES by extension bucket (W11-W14), absolute mean R and count - what the gate would cost him, in his own
      realised R, before it is switched on.
"""
from __future__ import annotations
import csv, glob, json, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step_study import REC, atr14                        # noqa: E402
from pipeline.trendcont_step13_study import load, ema                       # noqa: E402
from pipeline.trader_selection_study import CJ, WINDOWS                     # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DRAG = {k: v["drag_scaled"] for k, v in json.load(open(os.path.join(ROOT, "data", "study", "spread_drag.json"))).items()}
FX_FALLBACK = 0.007          # USDJPY is not in the drag table; the FX entries run 0.006-0.008R


def drag_for(sym: str) -> float:
    return DRAG.get(sym.split(".")[0], FX_FALLBACK)


def record_rows():
    """every approved TrendCont row with its D1 extension, raw R and costed R."""
    out = []
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]
        d1 = load(sym, "d1")
        if not d1: continue
        closes = [b[4] for b in d1]; e20 = ema(closes, 20); days = [b[0][:10] for b in d1]
        cost = drag_for(sym)
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            try: rm, mfe = float(r["r_multiple"]), float(r.get("mfe_r") or 0)
            except (TypeError, ValueError): continue
            jd = max((k for k, d in enumerate(days) if d < r["signal_time"][:10]), default=-1)   # 2026-09-30: previous CLOSED day (was <=)
            if jd < 40: continue
            a = atr14([[0] + b[1:] for b in d1], jd)
            if a <= 0: continue
            up = r["direction"].upper().startswith("B")
            out.append({"sym": sym, "t": r["signal_time"], "r": rm, "cr": rm - cost, "bank": mfe >= 1.0,
                        "ext": ((closes[jd] - e20[jd]) / a) * (1 if up else -1)})
    return out


def split(rows, fld="r"):
    hi = [r for r in rows if r["ext"] >= 0.5]; lo = [r for r in rows if r["ext"] < 0.5]
    if not hi or not lo: return None
    d = st.mean(r[fld] for r in hi) - st.mean(r[fld] for r in lo)
    se = ((st.stdev([r[fld] for r in hi]) ** 2 / len(hi) + st.stdev([r[fld] for r in lo]) ** 2 / len(lo)) ** 0.5) if len(hi) > 1 and len(lo) > 1 else 0
    return d, se, len(hi), len(lo), st.mean(r[fld] for r in hi), st.mean(r[fld] for r in lo)


def halves(rows):
    ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
    return [r for r in rows if r["t"] <= mid], [r for r in rows if r["t"] > mid], mid


def main():
    rows = record_rows()
    print(f"record rows with a D1 extension: {len(rows)}\n")

    print("(a) PRE-2020 SLICE — above-minus-below at the 0.5 ATR cut (bar: >= +0.15R, same sign in both halves)")
    for label, syms, lo_y, hi_y in (("EURUSD 2017-19 (the specified test)", {"EURUSD.dk"}, "2017", "2019"),
                                    ("GBPUSD 2014-16 (the other pre-2020 sample on disk)", {"GBPUSD.dk"}, "2014", "2016")):
        sub = [r for r in rows if r["sym"] in syms and lo_y <= r["t"][:4] <= hi_y]
        s = split(sub)
        if not s: print(f"  {label:48} no rows either side of the cut"); continue
        d, se, nh, nl, mh, ml = s
        h1, h2, mid = halves(sub)
        s1, s2 = split(h1), split(h2)
        same = (s1 and s2 and (s1[0] > 0) == (s2[0] > 0))
        print(f"  {label:48} above {mh:+.3f}R (n={nh})  below {ml:+.3f}R (n={nl})  diff {d:+.3f} +-{se:.3f}")
        print(f"  {'':48} halves: {s1[0]:+.3f} / {s2[0]:+.3f}  -> {'PASS' if d >= 0.15 and same else 'FAIL'}"
              f" (>= +0.15R: {'yes' if d >= 0.15 else 'no'}, same sign: {'yes' if same else 'no'})"
              if s1 and s2 else f"  {'':48} halves: too few rows")

    print("\n(b) STUDY-TEMPLATE BAR on the gated population (ext >= +0.5 ATR), COSTS ON")
    gated = [r for r in rows if r["ext"] >= 0.5]
    h1, h2, mid = halves(gated)
    for nm, sub in (("full record", gated), (f"first half (to {mid[:10]})", h1), ("second half", h2)):
        if not sub: continue
        raw = st.mean(r["r"] for r in sub); cost = st.mean(r["cr"] for r in sub)
        se = st.stdev([r["cr"] for r in sub]) / len(sub) ** 0.5
        print(f"  {nm:28} n={len(sub):>5}  raw {raw:+.3f}R  costed {cost:+.3f}R +-{se:.3f}  bank {sum(r['bank'] for r in sub)/len(sub)*100:.0f}%")
    c = st.mean(r["cr"] for r in gated)
    ok = c >= 0.15 and len(gated) >= 150 and st.mean(r["cr"] for r in h1) > 0 and st.mean(r["cr"] for r in h2) > 0
    print(f"  -> {'PASS' if ok else 'FAIL'} (>= +0.15R costed: {'yes' if c >= 0.15 else 'no'}, n >= 150: "
          f"{'yes' if len(gated) >= 150 else 'no'}, both halves positive: "
          f"{'yes' if st.mean(r['cr'] for r in h1) > 0 and st.mean(r['cr'] for r in h2) > 0 else 'no'})")
    print("  per-symbol costed mean inside the gate:")
    by = defaultdict(list)
    for r in gated: by[r["sym"]].append(r["cr"])
    for k in sorted(by): print(f"    {k:12} {st.mean(by[k]):+.3f}R  (n={len(by[k])}, discount {drag_for(k):.3f}R)")

    print("\n(c) HIS TAKES by extension bucket, W11-W14 - absolute mean R and count (what the gate would cost him)")
    takes = []
    for label, dec, base in WINDOWS:
        dp = os.path.join(CJ, dec)
        if not os.path.exists(dp): continue
        sym = dec.split("_")[0]
        d1 = load(sym, "d1")
        if not d1: continue
        closes = [b[4] for b in d1]; e20 = ema(closes, 20); days = [b[0][:10] for b in d1]
        for r in csv.DictReader(open(dp, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or not r.get("decision", "").startswith("approved"): continue
            try: own = float(r["r_multiple"])
            except (TypeError, ValueError): continue
            jd = max((k for k, d in enumerate(days) if d < r["signal_time"][:10]), default=-1)   # 2026-09-30: previous CLOSED day (was <=)
            if jd < 40: continue
            a = atr14([[0] + b[1:] for b in d1], jd)
            if a <= 0: continue
            up = r["direction"].upper().startswith("B")
            takes.append({"ext": ((closes[jd] - e20[jd]) / a) * (1 if up else -1), "r": own, "win": label})
    for nm, f in (("below the gate (<0.5 ATR)", lambda x: x["ext"] < 0.5), ("inside the gate (>=0.5 ATR)", lambda x: x["ext"] >= 0.5)):
        sub = [t for t in takes if f(t)]
        if not sub: print(f"  {nm:28} none"); continue
        print(f"  {nm:28} n={len(sub):>3}  mean {st.mean(t['r'] for t in sub):+.3f}R  total {sum(t['r'] for t in sub):+.2f}R")
    print(f"  his TrendCont R in those windows: {sum(t['r'] for t in takes):+.2f} over {len(takes)} takes; "
          f"the gate would have blocked {sum(1 for t in takes if t['ext'] < 0.5)} of them "
          f"({sum(t['r'] for t in takes if t['ext'] < 0.5):+.2f}R)")


if __name__ == "__main__": main()
