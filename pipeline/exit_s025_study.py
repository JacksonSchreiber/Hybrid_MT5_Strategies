#!/usr/bin/env python3
"""exit_s025_study.py - trader 2026-09-30: is "no bank, stop to +0.25R once the trade reaches +1R" legit?

Candidate S: nothing banked; when the high-water mark reaches the trigger (+1R) the stop moves to +0.25R; the full
position runs to TP2 or that stop. Base: today's doctrine (bank 50% at +1R or TP1 if nearer, stop to entry).
Bar rules as exit_param_study.py (stop first at the previous bars' level, a bar opening through it fills at the open).

Checks, in-sample (the blind TrendCont record, 2,346 rows - the set the candidate was picked on):
  1. per year and per symbol: does the gain come from everywhere or from a few pockets?
  2. month-block bootstrap of the mean difference (trades in the same month are not independent)
  3. where the difference comes from (which base outcomes it turns into what)
  4. extra slippage on every stop exit above entry (a +0.25R stop is a tight stop)
  5. neighbourhood: stop level x trigger grid - a real effect is a plateau, a fluke is a spike;
     the trader's trigger rows (+0.5 / +0.75 / +1.25 / +1.5 / +2R) are in it
Out-of-sample (never used to pick anything):
  6. BTCUSD TrendCont (a symbol outside the record) and the other three detectors (SweepMSS, DeepFib, EMArevQ) on the
     8 symbols, from the long blind AA runs.
"""
from __future__ import annotations
import csv, math, os, random, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS, DRAG   # noqa: E402
from pipeline.trendcont_step13_study import load                                  # noqa: E402

AAJ = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
AA_FILES = ["AA_BTCUSD.dk_20180101_20260716.csv", "AA_EURUSD.dk_20160801_20260629.csv", "AA_GBPUSD.dk_20130801_20251230.csv",
            "AA_US100.dk_20120223_20251230.csv", "AA_US500.dk_20120215_20251230.csv", "AA_USDJPY.dk_20200126_20251230.csv",
            "AA_USOIL.dk_20130124_20251230.csv", "AA_XAUUSD.dk_20200126_20251230.csv"]


def replay(bars, i0, up, entry, sl, tp1, tp2, mode, trig=1.0, stop_to=0.25, slip=0.0):
    """mode 'base' (doctrine v2) | 'S' (no bank, stop to stop_to at trig). slip: R lost on any stop exit above entry.
    -> (R, exit kind)"""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    tp2_R = toR(tp2) if tp2 else None
    if mode == "base":
        bank_R = min(1.0, toR(tp1)) if tp1 else 1.0
        if tp2_R is not None and bank_R >= tp2_R - 1e-9: bank_R = None
        frac, trig, stop_to = 0.5, (bank_R if bank_R is not None else 1.0), 0.0
    else:
        bank_R, frac = None, 0.0
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    for j in range(i0, min(len(bars), i0 + MAX_BARS)):
        b = bars[j]
        o_R = toR(b[1])
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo_R <= stop_R:
            px = min(stop_R, o_R)
            if stop_R > -0.999: px -= slip
            kind = "SL" if stop_R <= -0.999 else ("BE" if abs(stop_R) < 1e-9 else ("LOCK" if stop_R > 0 else "CUT"))
            return locked + rem * px, kind
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, frac * bank_R, 1.0 - frac
        if tp2_R is not None and hi_R >= tp2_R:
            return locked + rem * tp2_R, "TP"
        hwm = max(hwm, hi_R)
        if hwm >= trig: stop_R = max(stop_R, stop_to)
    last = bars[min(len(bars), i0 + MAX_BARS) - 1]
    return locked + rem * toR(last[4]), "end"


def aa_rows(strats: set[str], syms: set[str] | None = None):
    out = []
    for fn in AA_FILES:
        sym = fn.split("_")[1]
        if syms and sym not in syms: continue
        h4 = load(sym, "h4")
        if not h4: continue
        idx = {b[0][:16]: k for k, b in enumerate(h4)}
        for r in csv.DictReader(open(os.path.join(AAJ, fn), newline="", encoding="ascii", errors="replace")):
            if r.get("decision") != "approved" or r.get("strategy") not in strats: continue
            i = idx.get(r["signal_time"][:16])
            try: e, s = float(r["entry"]), float(r["sl"])
            except (TypeError, ValueError): continue
            if i is None: continue
            tp1 = float(r["tp1"]) if r.get("tp1") else None
            tp2 = float(r["tp2"]) if r.get("tp2") else (float(r["tp"]) if r.get("tp") else None)
            out.append({"sym": sym, "t": r["signal_time"], "up": r["direction"].upper().startswith("B"), "e": e, "s": s,
                        "tp1": tp1, "tp2": tp2, "i0": i + 1, "bars": h4, "strat": r["strategy"],
                        "cost": DRAG.get(sym.split(".")[0], 0.007)})
    return sorted(out, key=lambda x: x["t"])


def go(rows, mode, **kw):
    return [replay(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], mode, **kw) for x in rows]


def summary(rows, b, v):
    bc = [x[0] - r["cost"] for x, r in zip(b, rows)]; vc = [x[0] - r["cost"] for x, r in zip(v, rows)]
    return st.mean(bc), st.mean(vc), paired_t([x[0] for x in b], [x[0] for x in v]), maxdd(bc), maxdd(vc)


def boot(rows, b, v, n=4000, seed=7):
    """Month-block bootstrap of the mean per-trade difference."""
    by = defaultdict(list)
    for r, x, y in zip(rows, b, v): by[r["t"][:7]].append(y[0] - x[0])
    months = list(by.values()); rnd = random.Random(seed); outs = []
    for _ in range(n):
        s = c = 0
        for _ in range(len(months)):
            m = months[rnd.randrange(len(months))]; s += sum(m); c += len(m)
        outs.append(s / c)
    outs.sort()
    return outs[int(0.025 * n)], outs[int(0.975 * n)], sum(1 for o in outs if o <= 0) / n


def main():
    rows = sorted([x for x in load_rows()], key=lambda x: x["t"])
    b = go(rows, "base"); v = go(rows, "S")
    keep = [k for k in range(len(rows)) if b[k] and v[k]]
    rows = [rows[k] for k in keep]; b = [b[k] for k in keep]; v = [v[k] for k in keep]
    mb, mv, t, db, dv = summary(rows, b, v)
    print(f"CANDIDATE: no bank, stop to +0.25R once +1R is reached  (blind TrendCont record, n={len(rows)})")
    print(f"  costed R/trade  base {mb:+.4f}  candidate {mv:+.4f}  diff {mv - mb:+.4f}  paired t {t:+.2f}   worst DD {db:.1f}R -> {dv:.1f}R\n")

    print("1a. PER YEAR              n    base   cand   diff      t")
    by = defaultdict(list)
    for r, x, y in zip(rows, b, v): by[r["t"][:4]].append((x[0] - r["cost"], y[0] - r["cost"]))
    wins = 0
    for yr in sorted(by):
        p = by[yr]; d = [y - x for x, y in p]
        tt = paired_t([x for x, _ in p], [y for _, y in p])
        wins += st.mean(d) > 0
        print(f"   {yr}               {len(p):5d} {st.mean(x for x, _ in p):+.3f} {st.mean(y for _, y in p):+.3f} {st.mean(d):+.3f}  {tt:+5.2f}")
    print(f"   candidate ahead in {wins} of {len(by)} years\n")
    print("1b. PER SYMBOL            n    base   cand   diff      t   DD base -> cand")
    bs = defaultdict(list)
    for r, x, y in zip(rows, b, v): bs[r["sym"]].append((x[0] - r["cost"], y[0] - r["cost"]))
    for s_ in sorted(bs):
        p = bs[s_]; d = [y - x for x, y in p]
        print(f"   {s_:20} {len(p):5d} {st.mean(x for x, _ in p):+.3f} {st.mean(y for _, y in p):+.3f} {st.mean(d):+.3f}  "
              f"{paired_t([x for x, _ in p], [y for _, y in p]):+5.2f}   {maxdd([x for x, _ in p]):5.1f} -> {maxdd([y for _, y in p]):5.1f}")

    lo, hi, p0 = boot(rows, b, v)
    print(f"\n2. MONTH-BLOCK BOOTSTRAP (4,000 resamples): 95% interval of the diff {lo:+.4f} .. {hi:+.4f} R/trade; "
          f"share of resamples where the candidate is not better: {p0 * 100:.1f}%\n")

    print("3. WHERE THE DIFFERENCE COMES FROM (base exit -> candidate exit: trades, total R change)")
    tr = defaultdict(lambda: [0, 0.0])
    for x, y in zip(b, v):
        k = f"{x[1]:>4} -> {y[1]:<4}"; tr[k][0] += 1; tr[k][1] += y[0] - x[0]
    for k, (n, s) in sorted(tr.items(), key=lambda kv: kv[1][1]):
        if abs(s) > 0.5 or n > 20: print(f"   {k}  {n:5d} trades  {s:+8.1f} R")

    print("\n4. EXTRA SLIPPAGE on every stop exit above entry (applied to both sides; base's BE exits count too)")
    for slip in (0.02, 0.05, 0.10):
        bb = go(rows, "base", slip=slip); vv = go(rows, "S", slip=slip)
        m1, m2, tt, _, _ = summary(rows, bb, vv)
        print(f"   slip {slip:.2f}R: base {m1:+.4f}  candidate {m2:+.4f}  diff {m2 - m1:+.4f}  t {tt:+.2f}")

    print("\n5. NEIGHBOURHOOD: costed R/trade (paired t vs base, worst DD) - rows = stop level, columns = trigger")
    trigs = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0); levels = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
    print("   stop \\ trigger " + "".join(f"{('+' + str(tg) + 'R'):>24}" for tg in trigs))
    for L in levels:
        cells = []
        for tg in trigs:
            if L >= tg: cells.append(f"{'-':>24}"); continue
            vv = go(rows, "S", trig=tg, stop_to=L); _, m2, tt, _, d2 = summary(rows, b, vv)
            cells.append(f"{m2:+.3f} (t{tt:+.1f}, {d2:4.0f}R)".rjust(24))
        print(f"   +{L:.2f}R         " + "".join(cells))
    print(f"   base: {mb:+.3f}, worst DD {db:.0f}R")

    print("\n6. OUT OF SAMPLE (never used to choose the candidate)")
    for label, rr in (("BTCUSD TrendCont (symbol not in the record)", aa_rows({"TrendCont"}, {"BTCUSD.dk"})),
                      ("SweepMSS, 8 symbols", aa_rows({"SweepMSS"})),
                      ("DeepFib, 8 symbols", aa_rows({"DeepFib"})),
                      ("EMArevQ, 8 symbols", aa_rows({"EMArevQ"})),
                      ("the three other detectors pooled", aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}))):
        bb = go(rr, "base"); vv = go(rr, "S")
        k = [i for i in range(len(rr)) if bb[i] and vv[i]]
        rr = [rr[i] for i in k]; bb = [bb[i] for i in k]; vv = [vv[i] for i in k]
        if not rr: print(f"   {label}: no rows"); continue
        m1, m2, tt, d1, d2 = summary(rr, bb, vv)
        print(f"   {label:44} n={len(rr):4d}  base {m1:+.4f}  candidate {m2:+.4f}  diff {m2 - m1:+.4f}  t {tt:+.2f}  DD {d1:.1f} -> {d2:.1f}R")


if __name__ == "__main__": main()
