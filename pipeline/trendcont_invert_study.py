#!/usr/bin/env python3
"""trendcont_invert_study.py - trader 2026-10-08 (quick study): every TrendCont signal on the commodity bloc traded INVERTED - a BUY
becomes a SELL and vice versa at the same entry, the stop and TP2 mirrored around the entry at the SAME distances (the original's 1R
stop below a BUY -> 1R above the SELL; TP2 the same R distance on the other side) - with raw management on the M1 engine (100% at
the signal, out at TP2 or the stop; still open at the 40-day replay end -> last close), model spread and +1 pip round trip. Compared
with the original raw signal on the same trades (raw is the only comparison). Population: the combo builder's (AA journals; the six
bloc symbols incl. the AUDNZD / AUDCAD / NZDCAD holdout trio, whose look was spent 2026-10-04), 2012-2024 and 2025-26 shown apart.
    python3 pipeline/trendcont_invert_study.py      -> data/study/trendcont_invert/report.md + trades.csv
"""
from __future__ import annotations
import csv, os, statistics as st, sys, time
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_rules as BR                    # noqa: E402
from live import backtest_engine as E                        # noqa: E402
from live import symbol_groups as SG                         # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "trendcont_invert")
BLOC = [s + ".dk" for s in next(g for g in SG.GROUPS if g[0] == "com")[3]]
RAW = {"schema": E.SCHEMA, "entry": {"pct": 100}, "tp2": True, "levers": []}
KIND = {0: "stopped", 1: "TP2", 3: "replay end"}


def replay(S, x):
    p = CB.prep(S, x)
    if p is None: return None
    F = dict(p["F"]); ctx, _, _ = BR.full_ctx(S, x, p)
    r = E.run(BR.NB, F, ctx, E.compile_cfg(E.validate(RAW)[0], ctx, True))
    pips = abs(x["e"] - x["s"]) / (10 * A.TICK[x["sym"]])
    return float(r[0]), float(r[0]) - 1.0 / pips, KIND.get(int(r[1]), str(r[1]))


def stats(v):
    n = len(v)
    if not n: return "n 0"
    m = sum(v) / n; sd = st.stdev(v) if n > 1 else 0
    return f"n {n:4}  mean {m:+.3f}R  total {sum(v):+7.1f}R  t {m / (sd / n ** 0.5) if sd else 0:+.2f}  win {sum(1 for x in v if x > 0) / n * 100:3.0f}%"


def main():
    os.makedirs(OUT, exist_ok=True); t0 = time.time()
    pop = [x for x in CB.population() if x["sym"] in BLOC and x["strat"] == "TrendCont"]
    rows = []
    for sym in BLOC:
        S = CB.sym_obj(sym)
        for x in [x for x in pop if x["sym"] == sym]:
            o = replay(S, x)
            if o is None: continue
            e = x["e"]; inv = dict(x, up=not x["up"], s=2 * e - x["s"], tp1=(2 * e - x["tp1"]) if x["tp1"] else None, tp2=(2 * e - x["tp2"]) if x["tp2"] else None)
            iv = replay(S, inv)
            if iv is None: continue
            rows.append({"sym": sym.split(".")[0], "t": x["t"], "year": int(x["t"][:4]), "dir": "BUY" if x["up"] else "SELL", "raw": o[0], "raw1": o[1],
                         "raw_exit": o[2], "inv": iv[0], "inv1": iv[1], "inv_exit": iv[2]})
    with open(os.path.join(OUT, "trades.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    L = [f"# TrendCont inverted on the commodity bloc (quick study, trader 2026-10-08) - {len(rows)} signals, {time.time() - t0:.0f}s", ""]
    def block(title, xs):
        L.extend([f"## {title}", "", "```",
                  f"original raw  (model): {stats([z['raw'] for z in xs])}", f"original raw  (+1pip): {stats([z['raw1'] for z in xs])}",
                  f"INVERTED      (model): {stats([z['inv'] for z in xs])}", f"INVERTED      (+1pip): {stats([z['inv1'] for z in xs])}",
                  f"exits: original {dict((k, sum(1 for z in xs if z['raw_exit'] == k)) for k in ('stopped', 'TP2', 'replay end'))} | "
                  f"inverted {dict((k, sum(1 for z in xs if z['inv_exit'] == k)) for k in ('stopped', 'TP2', 'replay end'))}", "```", ""])
    block("All, 2012-2026", rows); block("2012-2024", [z for z in rows if z["year"] <= 2024]); block("2025-2026", [z for z in rows if z["year"] >= 2025])
    for d in ("BUY", "SELL"): block(f"Original {d} (inverted = {'SELL' if d == 'BUY' else 'BUY'})", [z for z in rows if z["dir"] == d])
    for s in sorted({z["sym"] for z in rows}): block(s, [z for z in rows if z["sym"] == s])
    block("2012-2018", [z for z in rows if z["year"] <= 2018]); block("2019-2024", [z for z in rows if 2019 <= z["year"] <= 2024])
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L)); print("\n".join(L))


if __name__ == "__main__": main()
