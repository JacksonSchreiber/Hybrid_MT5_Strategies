#!/usr/bin/env python3
"""early_mfe_tp2_study.py - trader 2026-10-01: of the trades that reach a high R inside their first 6 H4 bars (the staged
trial, up to the bar-6 close), what share go on to hit TP2 - vs the average trade?
Best R in bars 1-6 = the highest bid-side favourable excursion from the first tranche's entry, M1, through the close of H4 bar 6.
Outcome under the live stack (bank at +1R/TP1 -> stop to entry; shorts' stop to +0.25R at +1.5R; stop checked first each bar):
TP2 = the runner reached TP2; also the mean full-position R (live stack, staged 25/75 + pyramid) per bucket.
Engine pipeline/ask_side_study, SCALED (ask-corrected) mode. All four detectors, 7 symbols; 2012-24 and 2025-26; long/short.
"""
from __future__ import annotations
import os, statistics as st, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows                             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402
from pipeline import trial_levels_study as TL                               # noqa: E402

MODE = "SCALED"; BUCKETS = [(-9, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 99)]
REPORT = os.path.join(A.STUDY, "early_mfe_tp2_report.txt")


def outcome(F, be, bank_R, tp2_R, up):
    xh, xl, bh, n = F["xh"], F["xl"], F["bh"], F["n"]
    stop, banked, raised = -1.0, False, False
    for j in range(n):
        if xl[j] <= stop: return "stop"
        if not banked and xh[j] >= bank_R: banked = True; stop = be
        if banked and not raised and bh[j] >= A.PYR_R:
            raised = True
            if not up: stop = max(stop, be + 0.25)
        if tp2_R is not None and xh[j] >= tp2_R: return "tp2"
    return "open"


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        u = A.setup(S, x, MODE)
        if not u or u["k_add"] is None or u["tp2_R"] is None: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
        e0 = float(F["eo"][0]); be = e0
        k = min(u["k_add"], F["n"] - 1)
        mfe6 = float(F["bh"][:k + 1].max())
        oc = outcome(F, be, u["bank_R"], u["tp2_R"], u["up"])
        r, units, _ = TL.run_v(F, e0, be, u["bank_R"], u["tp2_R"], BANKF.get(x["strat"], 0.5), A.F0, u["k_add"], u["up"], True, None, None)
        res.append((x["t"], x["up"], x["strat"], mfe6, oc, r, u["tp2_R"]))
    return res


def main():
    global ALL
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = [z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs]
    L = [__doc__.rstrip()]
    for per in ("dev", "hold"):
        zs = [z for z in res if (z[0][:4] < "2025") == (per == "dev")]
        L.append(f"\n{'2012-24' if per == 'dev' else '2025-26'}  (n={len(zs)}; TP2 median {st.median(z[6] for z in zs):.2f}R)")
        L.append(f"  {'best R in bars 1-6':>20} {'n':>5} {'share':>6} {'-> TP2':>7} {'longs':>7} {'shorts':>7} {'stopped':>8} {'mean R':>7}")
        def row(lab, s):
            if not s: return
            tp = lambda q: (sum(1 for z in q if z[4] == "tp2") / len(q) * 100) if q else float("nan")
            lo = [z for z in s if z[1]]; sh = [z for z in s if not z[1]]
            L.append(f"  {lab:>20} {len(s):5d} {len(s) / len(zs) * 100:5.0f}% {tp(s):6.0f}% {tp(lo):6.0f}% {tp(sh):6.0f}% "
                     f"{sum(1 for z in s if z[4] == 'stop') / len(s) * 100:7.0f}% {st.mean(z[5] for z in s):+7.3f}")
        row("ALL TRADES", zs)
        for a, b in BUCKETS:
            row(f"{'<' + str(b) if a < 0 else (str(a) + '+' if b > 50 else f'{a}-{b}')}R", [z for z in zs if a <= z[3] < b])
        for a in (1.5, 2.0, 2.5):
            row(f">= {a}R (cumulative)", [z for z in zs if z[3] >= a])
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
