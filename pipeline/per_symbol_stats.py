#!/usr/bin/env python3
"""per_symbol_stats.py - trader 2026-10-03: every downloaded symbol individually, the CURRENT DEPLOYED stack ("base": staged 25%/bar-6,
bank per detector at +1R/TP1 -> stop to entry, pyramid +50% at +1.5R, shorts stop to +0.25R at +1.5R) vs base + coach-queue A+B+I
(and A+B, since I failed its bar). Engine: combo3_study.trade (M1, SCALED = ask-corrected with live spreads + drag), unchanged.
Population per symbol = all four detectors from TODAY's EA build:
  original seven: the pinned AA journals for all four detectors (TrendCont from AA, not the pre-re-arm REC record - like for like);
  new nine: data/study/oos_aa AA journals (EURJPY/GBPJPY only 2020+, EURGBP only 2013-16: the tester lacked conversion history).
Per-symbol only - nothing pooled (the excluded crosses are addendum-only for the coach's look).
    python3 pipeline/per_symbol_stats.py      -> data/study/per_symbol_report.txt (+ per_symbol_trades.pkl)
"""
import os, pickle, statistics as st, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import oos_queue_study as Q                 # registers the new symbols' engine inputs  # noqa: E402
from pipeline import oos_build_inputs as OI               # noqa: E402
from pipeline.combo3_class_split import cls               # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402

ORIG = ["EURUSD.dk", "GBPUSD.dk", "US100.dk", "US500.dk", "USDJPY.dk", "USOIL.dk", "XAUUSD.dk"]
REPORT = os.path.join(Q.A.STUDY, "per_symbol_report.txt"); PKL = os.path.join(Q.A.STUDY, "per_symbol_trades.pkl")
NOTE = {"EURJPY.dk": "2020+ only*", "GBPJPY.dk": "2020+ only*", "EURGBP.dk": "2013-16 only*"}


def main():
    T0 = time.time()
    if os.path.exists(PKL): res = pickle.load(open(PKL, "rb"))
    else:
        new = [s for s in OI.NEW if s != "US30.dk"]
        bad, drag = Q.check_new(new)
        new = [s for s in new if s not in bad]
        ALL = Q.pop_original(set(ORIG), aa_trendcont=True) + Q.pop_new(new, drag)
        ALL = sorted(ALL, key=lambda x: x["t"]); syms = sorted({x["sym"] for x in ALL})
        with Pool(6) as pool:
            out = pool.map(Q.do_symbol, [(s, [x for x in ALL if x["sym"] == s]) for s in syms])
        res = sorted([z for rs in out for z in rs], key=lambda z: z[1])
        pickle.dump(res, open(PKL, "wb"))
    md = "SCALED"
    syms = ORIG + [s for s in OI.NEW if any(z[0] == s for z in res)]
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {(time.time() - T0) / 60:.1f} min. R per trade = full position, costs in.",
         "DD = worst peak-to-trough of the cumulative R (trade order).  * = partial years only (tester data fault, see docstring).\n",
         f"{'symbol':10} {'class':6} {'years':13} {'n':>5} {'long%':>5} | {'LIVE STACK':^31} | {'LIVE + A+B+I':^38} | {'+A+B':>7} | {'2025-26 live':>12} {'+ABI':>7}",
         f"{'':10} {'':6} {'':13} {'':>5} {'':>5} | {'R/trade':>8} {'win%':>5} {'totalR':>7} {'DD':>6} | {'R/trade':>8} {'totalR':>7} {'DD':>6} {'vs live':>8} {'t':>5} | {'R/trade':>7} | {'R/trade':>12} {'R/tr':>7}"]
    tot = {"base": [], "A+B+I": []}
    for s in syms:
        zs = [z for z in res if z[0] == s]
        if not zs: continue
        b = [z[4][md]["base"] for z in zs]; v = [z[4][md]["A+B+I"] for z in zs]; ab = [z[4][md]["A+B"] for z in zs]
        y0, y1 = zs[0][1][:4], zs[-1][1][:4]
        rec = [z for z in zs if z[1][:4] >= "2025"]
        rb = st.mean([z[4][md]["base"] for z in rec]) if rec else float("nan"); rv = st.mean([z[4][md]["A+B+I"] for z in rec]) if rec else float("nan")
        L.append(f"{s:10} {cls(s):6} {(y0 + '-' + y1 + ('*' if s in NOTE else '')):13} {len(zs):5d} {100 * sum(z[3] for z in zs) / len(zs):5.0f} | "
                 f"{st.mean(b):+8.4f} {100 * sum(x > 0 for x in b) / len(b):5.1f} {sum(b):+7.1f} {maxdd(b):6.1f} | "
                 f"{st.mean(v):+8.4f} {sum(v):+7.1f} {maxdd(v):6.1f} {st.mean(v) - st.mean(b):+8.4f} {paired_t(b, v):+5.1f} | {st.mean(ab):+7.4f} | "
                 f"{rb:+8.4f} n={len(rec):3d} {rv:+7.4f}")
    L.append("\nOriginal seven = the symbols the system was built on; the other nine were never used in development.")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
