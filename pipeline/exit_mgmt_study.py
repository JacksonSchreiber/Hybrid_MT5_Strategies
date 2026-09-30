#!/usr/bin/env python3
"""exit_mgmt_study.py - coach 2026-09-30: exit-management studies A / B / C on the TrendCont blind record.

Replays every approved TrendCont row (n=2,346) bar by bar over H4, from the bar AFTER the signal bar (the EA decides
at the signal bar's close), under the current doctrine and under each variant:

  base  doctrine v2: bank 50% at +1R (or TP1 if nearer), stop to entry, runner to TP2
  A1    stop to -0.50R once +0.25R is reached       A2  stop to -0.50R once +0.50R is reached
  A3    stop to -0.25R once +0.50R is reached       (A: from +1R on, the base doctrine applies unchanged)
  B     step trail lagging 1R in 0.25R steps up to +1R (+0.25 -> -0.75 ... +1R -> entry), then base doctrine
  C     the same trail carried past +1R (+1.25 -> +0.25, +1.5 -> +0.5 ...); 50% still banked at +1R, the runner
        leaves at TP2 or the trail, whichever first

Bar rules, conservative and identical for every variant, so the comparison is fair:
  * inside a bar the STOP is checked first, at the level set by PREVIOUS bars only - a stop is never raised and hit
    on the strength of the same bar's high (that would be intra-bar look-ahead)
  * then the bank / targets; then the high-water mark updates and the next bar's stop is computed
  * a trade still open when the data ends is marked at the last close ("end")
Costs: the per-symbol scaled spread discount from data/study/spread_drag.json, once per trade.

The replay's own base is the comparator for every variant (same bar rules on both sides); it is calibrated against
the journal's +0.060R before any variant is read.
"""
from __future__ import annotations
import csv, glob, json, math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step13_study import load                       # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REC = os.path.join(ROOT, "data", "study", "trendcont")
DRAG = {k: v["drag_scaled"] for k, v in json.load(open(os.path.join(ROOT, "data", "study", "spread_drag.json"))).items()}
MAX_BARS = 600                                                           # ~100 trading days of H4; longer = "end"


def trail_stop(hwm: float, uncapped: bool) -> float | None:
    """Step trail lagging 1R in 0.25R steps: the stop in R for a high-water mark in R (None = not armed yet)."""
    if hwm < 0.25: return None
    s = math.floor(hwm / 0.25 + 1e-9) * 0.25 - 1.0
    return s if uncapped else min(s, 0.0)


def replay(bars, i0, up, entry, sl, tp1, tp2, rule):
    """-> (R, terminal, mae_after_025, mae_after_050, runner_trail_exit_R or None). All in R; rule in base/A1/A2/A3/B/C."""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0                        # the nearer of +1R and TP1
    tp2_R = toR(tp2) if tp2 else None
    stop_R, banked, hwm, locked = -1.0, False, 0.0, 0.0
    worst_after = {0.25: None, 0.5: None}
    for j in range(i0, min(len(bars), i0 + MAX_BARS)):
        b = bars[j]
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        for k in worst_after:                                           # MAE after first touching +k (logged, coach item)
            if worst_after[k] is not None: worst_after[k] = min(worst_after[k], lo_R)
        # 1) stop first, at the level set by previous bars
        if lo_R <= stop_R:
            rest = 0.5 if banked else 1.0
            res = locked + rest * stop_R
            term = ("BE" if abs(stop_R) < 1e-9 else ("TRAIL" if stop_R > 0 else "SL")) if banked else ("SL" if stop_R <= -0.999 else "CAP")
            return res, term, worst_after[0.25], worst_after[0.5], (stop_R if banked and stop_R > 0 else None)
        # 2) bank, then the runner's target
        if not banked and hi_R >= bank_R:
            banked, locked = True, 0.5 * bank_R
            if stop_R < 0.0: stop_R = 0.0                                # stop to entry (base doctrine) - takes effect NEXT bar
        if banked and tp2_R is not None and hi_R >= tp2_R:
            return locked + 0.5 * tp2_R, "TP", worst_after[0.25], worst_after[0.5], None
        if not banked and tp2_R is not None and hi_R >= tp2_R:          # single-target rows: TP straight through
            return tp2_R, "TP", worst_after[0.25], worst_after[0.5], None
        # 3) high-water mark, then the stop for the NEXT bar
        hwm = max(hwm, hi_R)
        for k in worst_after:
            if worst_after[k] is None and hwm >= k: worst_after[k] = lo_R if hi_R >= k else lo_R
        new = stop_R
        if rule == "A1" and hwm >= 0.25: new = max(new, -0.5)
        if rule == "A2" and hwm >= 0.50: new = max(new, -0.5)
        if rule == "A3" and hwm >= 0.50: new = max(new, -0.25)
        if rule in ("B", "C"):
            t = trail_stop(hwm, uncapped=(rule == "C"))
            if t is not None: new = max(new, t)
        stop_R = new                                                    # ratchets: never loosens
    last = bars[min(len(bars), i0 + MAX_BARS) - 1]
    mark = toR(last[4])
    return (locked + 0.5 * mark if banked else mark), "end", worst_after[0.25], worst_after[0.5], None


def load_rows():
    out = []
    for p in sorted(glob.glob(os.path.join(REC, "*.csv"))):
        sym = os.path.basename(p)[:-4]
        h4 = load(sym, "h4")
        if not h4: continue
        idx = {b[0][:16]: k for k, b in enumerate(h4)}
        for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont" or r.get("decision") != "approved": continue
            i = idx.get(r["signal_time"][:16])
            try: e, s = float(r["entry"]), float(r["sl"]); jr = float(r["r_multiple"])
            except (TypeError, ValueError): continue
            if i is None: continue
            tp1 = float(r["tp1"]) if r.get("tp1") else None
            tp2 = float(r["tp2"]) if r.get("tp2") else (float(r["tp"]) if r.get("tp") else None)
            out.append({"sym": sym, "t": r["signal_time"], "up": r["direction"].upper().startswith("B"),
                        "e": e, "s": s, "tp1": tp1, "tp2": tp2, "i0": i + 1, "journal_r": jr, "journal_term": r.get("terminal"),
                        "bars": h4, "cost": DRAG.get(sym.split(".")[0], 0.007)})
    return out


def run(rows, rule):
    res = []
    for x in rows:
        o = replay(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], rule)
        if o: res.append({**{k: x[k] for k in ("sym", "t", "cost", "journal_r", "journal_term")},
                          "r": o[0], "term": o[1], "mae025": o[2], "mae050": o[3], "trail_exit": o[4]})
    return res


def maxdd(rs):
    cur = peak = dd = 0.0
    for r in rs:
        cur += r; peak = max(peak, cur); dd = max(dd, peak - cur)
    return dd


def main():
    rows = load_rows()
    print(f"replaying {len(rows)} approved TrendCont rows over H4\n")
    base = run(rows, "base")
    bm = st.mean(x["r"] for x in base); bj = st.mean(x["journal_r"] for x in base)
    from collections import Counter
    print(f"CALIBRATION  replay base {bm:+.4f}R vs journal {bj:+.4f}R (n={len(base)})")
    print(f"             exits replay {dict(Counter(x['term'] for x in base))}  journal {dict(Counter(x['journal_term'] for x in base))}\n")
    order = sorted(range(len(base)), key=lambda k: base[k]["t"])
    syms = sorted({x["sym"] for x in base})
    hdr = f"{'rule':6} {'mean R':>8} {'costed':>8} {'vs base':>8} {'losers saved':>13} {'winners cut':>12} {'worst DD':>9}  per-symbol mean R (costed)"
    print(hdr); print("-" * len(hdr))
    out_rows = {}
    for rule in ("base", "A1", "A2", "A3", "B", "C"):
        v = base if rule == "base" else run(rows, rule)
        out_rows[rule] = v
        m = st.mean(x["r"] for x in v); mc = st.mean(x["r"] - x["cost"] for x in v)
        saved = sum(1 for a, b in zip(base, v) if a["r"] <= -0.99 and b["r"] > a["r"] + 1e-9)
        cut = sum(1 for a, b in zip(base, v) if a["r"] > 0 and b["r"] < a["r"] - 1e-9)
        dd = maxdd([v[k]["r"] - v[k]["cost"] for k in order])
        ps = defaultdict(list)
        for x in v: ps[x["sym"]].append(x["r"] - x["cost"])
        per = " ".join(f"{s.split('.')[0]}:{st.mean(ps[s]):+.3f}" for s in syms)
        print(f"{rule:6} {m:+8.4f} {mc:+8.4f} {m - bm:+8.4f} {saved:13d} {cut:12d} {dd:9.1f}  {per}")
    # per-symbol majority test (the coach's bar)
    print("\nBAR: mean R above the base AND better in a majority of the 7 symbols individually")
    bs = defaultdict(list)
    for x in base: bs[x["sym"]].append(x["r"] - x["cost"])
    for rule in ("A1", "A2", "A3", "B", "C"):
        v = out_rows[rule]; ps = defaultdict(list)
        for x in v: ps[x["sym"]].append(x["r"] - x["cost"])
        wins = sum(1 for s in syms if st.mean(ps[s]) > st.mean(bs[s]) + 1e-9)
        m = st.mean(x["r"] - x["cost"] for x in v); mb = st.mean(x["r"] - x["cost"] for x in base)
        print(f"  {rule}: costed {m:+.4f} vs base {mb:+.4f} ({'above' if m > mb else 'not above'}), better in {wins}/{len(syms)} symbols"
              f" -> {'PASS' if (m > mb and wins > len(syms) / 2) else 'FAIL'}")
    c = out_rows["C"]; te = [x for x in c if x["trail_exit"] is not None]
    print(f"\nC runner: trail exits before TP2 on {len(te)} of {len(c)} trades ({len(te)/len(c)*100:.1f}%), "
          f"runner stop at a mean of +{st.mean(x['trail_exit'] for x in te):.2f}R" if te else "\nC runner: no trail exits")
    # the logging item: MAE after +0.25R / +0.50R on the base replay, written out as study rows
    p = os.path.join(ROOT, "data", "study", "exit_mgmt_rows.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["symbol", "signal_time", "journal_r", "base_r", "base_exit", "mae_after_025", "mae_after_050"]
                                      + [f"{k}_r" for k in ("A1", "A2", "A3", "B", "C")])
        for k in range(len(base)):
            w.writerow([base[k]["sym"], base[k]["t"], base[k]["journal_r"], round(base[k]["r"], 4), base[k]["term"],
                        "" if base[k]["mae025"] is None else round(base[k]["mae025"], 4),
                        "" if base[k]["mae050"] is None else round(base[k]["mae050"], 4)]
                       + [round(out_rows[r][k]["r"], 4) for r in ("A1", "A2", "A3", "B", "C")])
    print(f"\nper-trade rows (incl. mae_after_025 / mae_after_050) -> {p}")


if __name__ == "__main__": main()
