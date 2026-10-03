#!/usr/bin/env python3
"""day4_raise_study.py - trader 2026-10-02: raise the stop to +0.25R at day 4. At the close of H4 bar 24 (~4 trading days), for a
trade still open, every ticket's stop moves to +0.25R (first fill + 0.25 x 1R), tighten-only:
  ALL        every open trade; if that close is already at/below +0.25R the position is closed at market there.
  PRICE>1R   only trades whose price (bid close) is above +1R at that moment.
  AVG>1R     only trades whose average R since entry (mean of every M1 bid close) is above +1R.
Base = live (staged 25%/bar-6, bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at
+1.5R - so many shorts are already at +0.25R). Engine pipeline/ask_side_study, SCALED (ask-corrected). All four detectors, 7
symbols, pinned record; 2012-24 / 2025-26; all / longs only / shorts only; full-position R; worst DD.
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402
from pipeline.day3_weak_study import run_v                                  # noqa: E402  (stop action: tighten to L, out at market if through)

MODE = "SCALED"; CHK = 24; LVL = 0.25
VARS = ["all", "price>1R", "avg>1R"]
REPORT = os.path.join(A.STUDY, "day4_raise_report.txt")


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5)
    args = (F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"])
    out = {"base": run_v(*args, None, None, None)[0]}
    ib = u["i0"] + CHK
    k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    ok = k is not None and 0 <= k < F["n"]
    px = float(F["bc"][k]) if ok else None; avg = float(F["bc"][:k + 1].mean()) if ok else None
    cond = {"all": ok, "price>1R": ok and px > 1.0, "avg>1R": ok and avg > 1.0}
    for v in VARS:
        out[v] = run_v(*args, k, "stop", LVL)[0] if cond[v] else out["base"]
        out["hit" + v] = cond[v] and out[v] != out["base"]
    return out


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        r = trade(S, x)
        if r: res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def main():
    global ALL
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = sorted([z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs], key=lambda z: z[1])
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for per in ("dev", "hold"):
        zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
        b = [z[4]["base"] for z in zs]; mb = st.mean(b)
        L.append(f"\n{'=' * 100}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R")
        L.append(f"  {'raise to +0.25R':16} {'changed':>8} | {'ALL: vs base [t] / DD':>26} | {'LONGS only':>26} | {'SHORTS only':>26} | {'helped/hurt':>11}")
        for v in VARS:
            cells = []
            for side in (None, True, False):
                vv = [z[4][v] if (side is None or z[3] == side) else z[4]["base"] for z in zs]
                cells.append(f"{st.mean(vv) - mb:+.4f}[{paired_t(b, vv):+.1f}]/{maxdd(vv):5.1f}")
            hz = [z[4][v] - z[4]["base"] for z in zs if z[4]["hit" + v]]
            L.append(f"  {v:16} {len(hz):8d} | {cells[0]:>26} | {cells[1]:>26} | {cells[2]:>26} | {sum(1 for e in hz if e > 0)}/{sum(1 for e in hz if e < 0)}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
