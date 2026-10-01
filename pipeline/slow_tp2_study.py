#!/usr/bin/env python3
"""slow_tp2_study.py - trader 2026-10-01, the opposite of early_mfe_tp2_study: SLOW trades. Of the trades still open T trading
days after entry (T x 6 H4 bars) whose best R so far is still BELOW M, what share reach TP2, and how long do they take?
T in 1 / 2 / 3 / 5 / 10 trading days; M in 0.5 / 1 / 1.5 / 2 R. "Best R so far" = the highest bid-side favourable excursion from
the first entry up to the checkpoint (M1). Outcome under the live stack (bank at +1R/TP1 -> stop to entry; shorts' stop to
+0.25R at +1.5R; stop checked first each bar). Time to TP2: calendar days from entry (and from the checkpoint). Mean R = the
trade's full-position R under the live stack (staged 25/75, pyramid). Comparison row: all trades / all open at T.
Engine pipeline/ask_side_study, SCALED (ask-corrected). All four detectors, 7 symbols; 2012-24 and 2025-26; long/short.
"""
from __future__ import annotations
import os, statistics as st, sys
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows                             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402
from pipeline import trial_levels_study as TL                               # noqa: E402
from pipeline.early_mfe_tp2_study import outcome                            # noqa: E402

MODE = "SCALED"; TS = (1, 2, 3, 5, 10); MS = (0.5, 1.0, 1.5, 2.0)
REPORT = os.path.join(A.STUDY, "slow_tp2_report.txt")

ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        u = A.setup(S, x, MODE)
        if not u or u["k_add"] is None or u["tp2_R"] is None: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
        e0 = float(F["eo"][0])
        oc, jx = outcome(F, e0, u["bank_R"], u["tp2_R"], u["up"])
        r, _, _ = TL.run_v(F, e0, e0, u["bank_R"], u["tp2_R"], BANKF.get(x["strat"], 0.5), A.F0, u["k_add"], u["up"], True, None, None)
        t0 = S.t[u["k0"]]; tx = S.t[u["k0"] + jx] if jx is not None else None
        cps = {}
        for T in TS:
            ib = u["i0"] + 6 * T
            if ib >= len(S.h4): continue
            kT = int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"]                 # the first M1 bar of H4 bar 6T+1 = the checkpoint
            if kT <= 0 or kT >= F["n"]: continue
            alive = jx is None or jx >= kT
            cps[T] = (alive, float(F["bh"][:kT].max()), (S.t[u["k0"] + kT] - t0) / 1440.0)
        res.append((x["t"], x["up"], oc, (tx - t0) / 1440.0 if tx is not None else None, r, cps))
    return res


def main():
    global ALL
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = [z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs]
    L = [__doc__.rstrip()]
    hdr = (f"  {'group':34} {'n':>5} {'-> TP2':>7} {'longs':>6} {'shorts':>6} {'mean R':>7} | {'days to TP2 mean':>16} {'median':>6} "
           f"{'from checkpoint mean':>20} {'median':>6}")
    def row(lab, s, dT=None):
        if not s: return
        tp = lambda q: (sum(1 for z in q if z[2] == "tp2") / len(q) * 100) if q else float("nan")
        w = [z[3] for z in s if z[2] == "tp2"]
        lo = [z for z in s if z[1]]; sh = [z for z in s if not z[1]]
        tail = (f"{st.mean(w):16.1f} {st.median(w):6.1f} " + (f"{st.mean(v - dT[i] for i, v in w2):20.1f} {st.median(v - dT[i] for i, v in w2):6.1f}" if dT else "")
                if w else "")
        L.append(f"  {lab:34} {len(s):5d} {tp(s):6.0f}% {tp(lo):5.0f}% {tp(sh):5.0f}% {st.mean(z[4] for z in s):+7.3f} | " + tail)
    for per in ("dev", "hold"):
        zs = [z for z in res if (z[0][:4] < "2025") == (per == "dev")]
        L.append(f"\n{'2012-24' if per == 'dev' else '2025-26'}  (n={len(zs)})"); L.append(hdr)
        row("ALL TRADES", zs)
        for T in TS:
            op = [z for z in zs if T in z[5] and z[5][T][0]]
            L.append(f"  -- still open after {T} trading day{'s' if T > 1 else ''} (checkpoint ~{st.median(z[5][T][2] for z in op):.1f} calendar days) --")
            for lab, s in [("all still open", op)] + [(f"best R so far < +{m:g}R", [z for z in op if z[5][T][1] < m]) for m in MS] \
                           + [(f"best R so far >= +{MS[1]:g}R", [z for z in op if z[5][T][1] >= MS[1]])]:
                if not s: continue
                w2 = [(i, z[3]) for i, z in enumerate(s) if z[2] == "tp2"]; dT = [z[5][T][2] for z in s]
                tp = lambda q: (sum(1 for z in q if z[2] == "tp2") / len(q) * 100) if q else float("nan")
                lo = [z for z in s if z[1]]; sh = [z for z in s if not z[1]]
                w = [v for _, v in w2]
                tail = (f"{st.mean(w):16.1f} {st.median(w):6.1f} {st.mean(v - dT[i] for i, v in w2):20.1f} {st.median(v - dT[i] for i, v in w2):6.1f}" if w else "")
                L.append(f"    {lab:32} {len(s):5d} {tp(s):6.0f}% {tp(lo):5.0f}% {tp(sh):5.0f}% {st.mean(z[4] for z in s):+7.3f} | {tail}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
