#!/usr/bin/env python3
"""avg_cut_trail_study.py - trader 2026-10-15: "what if we check average every 4 hours for the first 24 hours of the trade? hour 4 close if
avg below -.2, hour 8 close if avg is below -.1, hour 12 close if avg is below 0, hour 16 close if avg below .1, hour 20 close if avg below
.2, hour 24 close if avg below .2. hour 12+ check the average on the most recent 12 hours. then also add a trailing stop applied every 15m
of 1R below the highest point reached. the stop only goes up not down".

The plan, as builder levers on the rule engine (live/backtest_engine.py - the Practice tab's and the live plan runner's kernel):
  entry 100% at the signal, TP2 on;
  cut_avg {at_h 4, below -0.2} {8, -0.1} {12, 0, window 12} {16, +0.1, window 12} {20, +0.2, window 12} {24, +0.2, window 12}
    (hours from the entry-bar open, so 4 h = the entry bar's close; the average = the mean of the M1 bid closes since the entry, or over
    the last 12 trading hours from 12 h on);
  trail {every_h 0.25, dist 1.0, raise only}: every 15 trading minutes the stop goes to 1R below the highest exit-side price so far.
Pre-registered lines (nothing else is tried): raw | the cuts only | the trail only | THE PLAN (cuts + trail) | the plan without TP2.
Same population as avg_wait_entry_study (open symbols only; locked never loaded; 2025-26 bloc only), model spread, +1 pip shown too.
    python3 pipeline/avg_cut_trail_study.py         -> data/study/avg_cut_trail/report.md + trades.csv
"""
from __future__ import annotations
import csv, os, statistics as st, sys, time
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_rules as BR                    # noqa: E402  (full_ctx + the numba kernel; the module only, never the script)
from pipeline import oos_build_inputs as OI                  # noqa: E402
from pipeline import avg_wait_entry_study as W               # noqa: E402  (population, maxdd, tstat)
from live import backtest_engine as E                        # noqa: E402
from live import symbol_groups as SG                         # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "avg_cut_trail")
CUTS = [{"type": "cut_avg", "at_h": 4, "below": -0.2}, {"type": "cut_avg", "at_h": 8, "below": -0.1},
        {"type": "cut_avg", "at_h": 12, "below": 0.0, "window_h": 12}, {"type": "cut_avg", "at_h": 16, "below": 0.1, "window_h": 12},
        {"type": "cut_avg", "at_h": 20, "below": 0.2, "window_h": 12}, {"type": "cut_avg", "at_h": 24, "below": 0.2, "window_h": 12}]
TRAIL = [{"type": "trail", "every_h": 0.25, "dist": 1.0, "lower": False}]
plan = lambda levers, tp2=True: {"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": tp2, "levers": levers}
LINES = [("raw", "raw signal", plan([])), ("cuts", "the six average cuts only", plan(CUTS)), ("trail", "the 15-min 1R trail only", plan(TRAIL)),
         ("plan", "THE PLAN: cuts + trail", plan(CUTS + TRAIL)), ("plan_notp2", "the plan without TP2", plan(CUTS + TRAIL, tp2=False))]
CFG = {k: E.validate(c)[0] for k, _, c in LINES}
CUT_H = {k: [c["at_h"] for c in CFG[k]["levers"] if c["type"].startswith("cut")] for k in CFG}


def sim(S, x):
    p = CB.prep(S, x)
    if p is None: return None
    F = dict(p["F"]); ctx, _, _ = BR.full_ctx(S, x, p)
    pips = abs(x["e"] - x["s"]) / (10 * A.TICK[x["sym"]])
    out = {}
    for k, _, _ in LINES:
        r = E.run(BR.NB, F, ctx, E.compile_cfg(CFG[k], ctx, True))
        R, kind, cut = float(r[0]), int(r[1]), int(r[6])
        if kind == 0: why = "stop -1R" if R <= -0.999 else "trailed stop"
        elif kind == 2: why = f"cut {CUT_H[k][cut]:g}h"
        else: why = {1: "TP2", 3: "replay end", 4: "banked out"}.get(kind, str(kind))
        out[k] = {"R": R, "R1": R - float(r[3]) / pips, "why": why, "exit_j": int(r[2])}
    return out


def table(rows, L, key="R"):
    L.append("| line | R / trade | total R | vs raw / trade | t vs raw | win % | max DD R |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    raw = [z["res"]["raw"][key] for z in rows]
    for k, lbl, _ in LINES:
        v = [z["res"][k][key] for z in rows]; d = [a - b for a, b in zip(v, raw)]
        L.append(f"| {lbl} | {st.mean(v):+.3f} | {sum(v):+.1f} | {st.mean(d):+.3f} | {W.tstat(d) if k != 'raw' else 0:+.2f} | "
                 f"{sum(1 for a in v if a > 0) / len(v) * 100:.0f}% | {W.maxdd(v):.1f} |")


def main():
    os.makedirs(OUT, exist_ok=True); t0 = time.time()
    pop, bad = W.population()
    print(f"population {len(pop)} open signals; not ready: {bad}", flush=True)
    rows = []
    for sym in sorted({x["sym"] for x in pop}):
        S = CB.sym_obj(sym); grp = (SG.group_of(OI.root(sym)) or {"name": "other"})["name"]
        for x in [x for x in pop if x["sym"] == sym]:
            o = sim(S, x)
            if o is None: continue
            rows.append({"res": o, "sym": OI.root(sym), "grp": "Oil (USOIL)" if grp == "Standalone" else grp, "t": x["t"], "year": int(x["t"][:4]),
                         "strat": x["strat"], "dir": "BUY" if x["up"] else "SELL"})
        print(f"{sym}: {sum(1 for z in rows if z['sym'] == OI.root(sym))} trades, {time.time() - t0:.0f}s", flush=True)
    rows.sort(key=lambda z: z["t"])

    with open(os.path.join(OUT, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sym", "grp", "t", "strat", "dir"] + [f"{k}_{c}" for k, _, _ in LINES for c in ("R", "R1", "exit")])
        for z in rows:
            w.writerow([z["sym"], z["grp"], z["t"], z["strat"], z["dir"]] +
                       [v for k, _, _ in LINES for v in (f"{z['res'][k]['R']:.4f}", f"{z['res'][k]['R1']:.4f}", z["res"][k]["why"])])

    L = [f"# Average cuts every 4 h for 24 h + a 15-min 1R trailing stop - trader 2026-10-15", "",
         f"{len(rows)} trades (open symbols; locked never loaded; 2025-26 bloc only), {time.time() - t0:.0f}s. Rule engine (the Practice "
         f"tab's / live plan runner's kernel), entry 100% at the signal, card R, model spread. Paired with raw on the same trades.", ""]
    def block(title, rs):
        if not rs: return
        L.extend([f"## {title} ({len(rs)} trades)", ""]); table(rs, L); L.append("")
    block("All open signals", rows)
    L.extend(["### Same, +1 pip round trip", ""]); table(rows, L, "R1"); L.append("")

    # how the plan's trades ended, and what raw did on those same trades
    L.extend(["## How THE PLAN's trades ended (and raw on the same trades)", "", "| exit | trades | plan R / trade | raw R / trade | plan - raw, total R |",
              "|---|---:|---:|---:|---:|"])
    order = ["TP2", "stop -1R", "trailed stop"] + [f"cut {h:g}h" for h in CUT_H["plan"]] + ["replay end", "banked out"]
    for wy in order:
        rs = [z for z in rows if z["res"]["plan"]["why"] == wy]
        if not rs: continue
        a = [z["res"]["plan"]["R"] for z in rs]; b = [z["res"]["raw"]["R"] for z in rs]
        L.append(f"| {wy} | {len(rs)} | {st.mean(a):+.3f} | {st.mean(b):+.3f} | {sum(a) - sum(b):+.1f} |")
    tp2raw = [z for z in rows if z["res"]["raw"]["why"] == "TP2"]
    L.extend(["", f"Raw reached TP2 on {len(tp2raw)} trades; the plan kept {sum(1 for z in tp2raw if z['res']['plan']['why'] == 'TP2')} of them "
              f"(the rest: " + ", ".join(f"{wy} {n}" for wy, n in sorted({(w_, sum(1 for z in tp2raw if z['res']['plan']['why'] == w_))
              for w_ in {z['res']['plan']['why'] for z in tp2raw} if w_ != 'TP2'}, key=lambda t: -t[1])) + ").", ""])

    for g in sorted({z["grp"] for z in rows}): block(g, [z for z in rows if z["grp"] == g])
    for s_ in sorted({z["strat"] for z in rows}): block(f"Detector {s_}", [z for z in rows if z["strat"] == s_])
    for d in ("BUY", "SELL"): block(d, [z for z in rows if z["dir"] == d])
    block("2012-2018", [z for z in rows if z["year"] <= 2018]); block("2019-2024", [z for z in rows if 2019 <= z["year"] <= 2024])
    block("2025-2026 (commodity bloc only - the rest is research-locked)", [z for z in rows if z["year"] >= 2025])
    for s_ in sorted({z["sym"] for z in rows}): block(s_, [z for z in rows if z["sym"] == s_])
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L)); print("\n".join(L))


if __name__ == "__main__": main()
