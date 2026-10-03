#!/usr/bin/env python3
"""combo3_class_split.py - coach 2026-10-03: the queue rules (A, B, I, A+B, A+B+I) split by symbol class x direction x period on the
original seven symbols (same engine and population as combo3_study). The coach's I bar: positive on FX longs alone.
Per-trade results are cached in data/study/combo3_trades.pkl for further splits.
    python3 pipeline/combo3_class_split.py
"""
import os, pickle, statistics as st, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from multiprocessing import Pool
from pipeline import combo3_study as K                          # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t  # noqa: E402
from pipeline.exit_s025_study import aa_rows                     # noqa: E402

PKL = os.path.join(K.A.STUDY, "combo3_trades.pkl"); REPORT = os.path.join(K.A.STUDY, "combo3_class_report.txt")
SHOW = ["A", "B", "I", "A+B", "A+B+I"]


def cls(sym):
    r = sym.split(".")[0]
    if r.startswith(("US", "DE", "UK", "JP")) and any(ch.isdigit() for ch in r): return "index"
    if r.startswith(("XAU", "XAG")): return "metal"
    if r.startswith("BTC"): return "crypto"
    if r.startswith(("USOIL", "UKOIL", "XBR", "XTI")): return "oil"
    return "FX"


def main():
    T0 = time.time()
    if os.path.exists(PKL): res = pickle.load(open(PKL, "rb"))
    else:
        K.ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                       key=lambda x: x["t"])
        with Pool(2) as pool: res = sorted([z for rs in pool.map(K.do_symbol, sorted({x["sym"] for x in K.ALL})) for z in rs], key=lambda z: z[1])
        pickle.dump(res, open(PKL, "wb"))
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min; classes: " +
         ", ".join(f"{s}={cls(s)}" for s in sorted({z[0] for z in res}))]
    md = "SCALED"
    for per in ("2012-24", "2025-26"):
        for c in ("FX", "index", "metal", "oil", "all"):
            for side in ("long", "short"):
                zs = [z for z in res if (z[1][:4] < "2025") == (per == "2012-24") and (c == "all" or cls(z[0]) == c) and z[3] == (side == "long")]
                if len(zs) < 5: continue
                b = [z[4][md]["base"] for z in zs]
                cells = []
                for name in SHOW:
                    v = [z[4][md][name] for z in zs]
                    cells.append(f"{name} {st.mean(v) - st.mean(b):+.4f} (t {paired_t(b, v):+.1f}, DD {maxdd(v):.1f})")
                L.append(f"{per} {c:6} {side:5} n={len(zs):4d} base {st.mean(b):+.4f} DD {maxdd(b):5.1f} | " + " | ".join(cells))
        L.append("")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
