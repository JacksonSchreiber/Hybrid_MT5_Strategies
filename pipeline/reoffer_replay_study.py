#!/usr/bin/env python3
"""reoffer_replay_study.py - coach item 26 D15: replay the spread re-offer on the ask-side engine.

Population: the pinned record (TrendCont blind record + SweepMSS / DeepFib / EMArevQ AA rows, 7 .dk symbols), M1
(pipeline/ask_side_study). A signal QUALIFIES when the spread at its entry minute (the first M1 bar of the entry H4 bar) costs
more than 0.10R of its stop under the spread mode (SCALED: Sept-2026 hourly median re-priced to the trade's price level; ABS:
the same in price units). Those are the signals the live gate refuses.
RE-OFFER (item 26 A2/A3/A6): hold up to 3 H4 bars from the entry bar; at every M1 close re-check: cancel if price has traded
through the stop (a short on the ask) or reached +1R / TP1 if nearer (measured on the ORIGINAL entry); else publish the first
time spread <= 0.10R of the CURRENT distance (market entry: ask for a buy, bid for a sell; original stop and targets). The
re-offered trade's 1R is the new entry-to-original-stop distance (lots re-sized), then the live stack: staged 25% with the add
at the close of H4 bar 6 counted from the SIGNAL bar (the EA's staged add counts from the row's signal time), bank per detector
at +1R/TP1 -> stop to the new entry, pyramid 50% at +1.5R, shorts stop to +0.25R at +1.5R, runner to TP2.
Compared per qualifying signal: RE-OFFER (cancelled / never normalised = 0R) vs ON-TIME (entered at the signal through the wide
spread - the gate ignored) vs REJECT (0R, today). 2012-24 and 2025-26, long/short, by symbol.
GRID: the .dk H4 bars close at 00/04/../20 UTC, the live broker's at 01/../21 UTC (the 21:00 rollover close - 10 of the 16 live
spread rejects). Unshifted, no replayed entry ever meets the rollover spread. REOFFER_SHIFT_H=1 re-runs with the profile shifted
one hour (each .dk close sees the spread of the matching broker close), which is the live case.
CAVEAT: the hourly profile is a LOWER bound on the spread (MT5 bar minimum spread; ask_side_report.txt), so the qualifying set is
small and biased to the rollover hour; within-hour timing is not modelled (the profile is flat across each hour).
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from collections import defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, paired_t                    # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402
from pipeline import trial_levels_study as TL                               # noqa: E402

MODES = ("SCALED", "ABS"); MAXR = 0.10; HOLD_H4 = 3
SHIFT = int(os.environ.get("REOFFER_SHIFT_H", "0"))       # +1: align the .dk H4 grid (closes 00/04/../20 UTC) to the broker's (01/../21)
REPORT = os.path.join(A.STUDY, "reoffer_replay_report.txt" if not int(os.environ.get("REOFFER_SHIFT_H", "0")) else "reoffer_replay_shift1h_report.txt")


def one(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]; up = u["up"]; sg = 1.0 if up else -1.0
    sp_px = S.sp * (u["scale"] if mode == "SCALED" else 1.0)                  # price-unit spread per M1 bar
    k0 = u["k0"]
    if sp_px[k0] / u["R"] <= MAXR: return None                                  # the gate would have let it through
    F = A.frame(S, k0, u["end"], up, u["e"], u["R"], mode, u["scale"])
    bankf = BANKF.get(x["strat"], 0.5); e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0
    ontime, _, _ = TL.run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], up, m["side"], None, None)
    i0 = u["i0"]; h4 = S.h4
    kend = (int(np.searchsorted(S.t, S.h4t[i0 + HOLD_H4])) - k0) if i0 + HOLD_H4 < len(h4) else F["n"]
    s = u["s"]; res = {"ontime": ontime, "re": 0.0, "status": "never", "held": None, "drift": None}
    for j in range(min(kend, F["n"])):
        if F["xl"][j] <= -1.0: res["status"] = "cancel_stop"; return res
        if F["xh"][j] >= u["bank_R"]: res["status"] = "cancel_1R"; return res
        if j == 0: continue                                                     # the refused minute itself
        bid = S.c[k0 + j]; sp = sp_px[k0 + j]
        fill = bid + sp if up else bid
        dist = (fill - s) if up else (s - bid - sp * 0)                         # a short's stop is hit on the ask: distance from the bid fill
        if dist <= 0: res["status"] = "cancel_stop"; return res
        if sp / dist > MAXR: continue
        kn = k0 + j + 1                                                         # enter at this M1 close = the next bar's start
        if kn >= len(S.t): return res
        Rn = dist; An = bid                                                     # frame reference = the bid at entry; a long pays sp via eo
        G = A.frame(S, kn, min(len(S.t), kn + A.MAXM1), up, An, Rn, mode, u["scale"])
        toR = lambda p: (p - An) * sg / Rn
        tp1 = (x["tp1"] - (x["e"] - u["e"])) if x["tp1"] else None; tp2 = (x["tp2"] - (x["e"] - u["e"])) if x["tp2"] else None
        bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tp2_R = toR(tp2) if tp2 else None
        if tp2_R is not None and bank_R >= tp2_R - 1e-9: bank_R = tp2_R
        if tp2_R is not None and tp2_R <= 0: res["status"] = "cancel_1R"; return res
        k_add = (int(np.searchsorted(S.t, S.h4t[i0 + 6])) - 1 - kn) if i0 + 6 < len(h4) else None
        if k_add is not None and k_add < 0: k_add = 0
        g0 = float(G["eo"][0]); gbe = g0 if m["side"] else 0.0
        r, _, _ = TL.run_v(G, g0, gbe, bank_R, tp2_R, bankf, A.F0, k_add, up, m["side"], None, None)
        res.update(re=r, status="reoffered", held=j, drift=((bid - u["e"]) * sg) / u["R"])
        return res
    return res


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); out = []
    if SHIFT:                                                   # each minute sees the spread of the same minute one hour LATER in the day
        prof = A.spread_profile()[sym.split(".")[0] + ".sim"]
        S.sp = prof[((S.t // 60) + SHIFT) % 24]
    for x in [x for x in ALL if x["sym"] == sym]:
        r = {md: one(S, x, md) for md in MODES}
        if all(v is None for v in r.values()): continue
        out.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return out


def main():
    global ALL
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = [z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs]
    L = [__doc__.rstrip(), f"\nRUN: {len(ALL)} signals scanned, {(time.time() - T0) / 60:.1f} min"]
    for md in MODES:
        for per in ("dev", "hold"):
            zs = [z for z in res if z[4][md] is not None and ((z[1][:4] < "2025") == (per == "dev"))]
            L.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: {len(zs)} signals refused by the 0.10R gate at the signal")
            if not zs: continue
            stc = defaultdict(int)
            for z in zs: stc[z[4][md]["status"]] += 1
            L.append("  outcome of the hold: " + ", ".join(f"{k} {v}" for k, v in sorted(stc.items())))
            ro = [z for z in zs if z[4][md]["status"] == "reoffered"]
            if ro:
                L.append(f"  re-offered: held median {st.median(z[4][md]['held'] for z in ro):.0f} min, drift vs the signal entry median "
                         f"{st.median(z[4][md]['drift'] for z in ro):+.2f}R")
            L.append(f"  {'subset':14} {'n':>4} | {'RE-OFFER':>9} {'ON-TIME':>9} {'REJECT':>7} | {'re-offer - on-time':>18} {'t':>6} | {'re-offered only: R':>18} {'its on-time R':>13}")
            def row(lab, q):
                if not q: return
                re_ = [z[4][md]["re"] for z in q]; on = [z[4][md]["ontime"] for z in q]
                rq = [z for z in q if z[4][md]["status"] == "reoffered"]
                L.append(f"  {lab:14} {len(q):4d} | {st.mean(re_):+9.3f} {st.mean(on):+9.3f} {0:+7.3f} | {st.mean(re_) - st.mean(on):+18.3f} "
                         f"{paired_t(on, re_) if len(q) > 2 else float('nan'):+6.2f} | "
                         + (f"{st.mean(z[4][md]['re'] for z in rq):+18.3f} {st.mean(z[4][md]['ontime'] for z in rq):+13.3f}" if rq else ""))
            row("all", zs); row("longs", [z for z in zs if z[3]]); row("shorts", [z for z in zs if not z[3]])
            for sym in sorted({z[0] for z in zs}): row(sym.split(".")[0], [z for z in zs if z[0] == sym])
            for strat in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ"): row(strat, [z for z in zs if z[2] == strat])
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L[len(__doc__.splitlines()):]))


if __name__ == "__main__": main()
