#!/usr/bin/env python3
"""inverse_repro_check.py - coach item 21 A4: does the EA's own EA-held Inverse (latency included) reproduce the backtest?

EA side: MT5 tester AA journals from `INVERSE=1 TC_BANK=0.25 pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ
--symbol <S> --from .. --to .. --model 4` (real ticks) -> data/study/inverse_repro/<SYM>.csv. With InpInverse the tester
journal carries parent_signal_id / bars_to_stop / inv_slip_r / inv_state; the filled Inverses are inv_state 2.
Replay side: pipeline/inverse_study.trade() on M1 bars for the SAME parents (their journal levels), plain (no pyramid),
no slippage, raw R (the spread drag added back, as the EA's journal R is price-based).
Pass: |mean EA R - mean replay R| <= 0.03R on the matched fills of each symbol (coach A4: against the +0.204R backtest).
"""
import csv, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trendcont_step13_study import load                     # noqa: E402
from pipeline.exit_mgmt_study import DRAG                             # noqa: E402
from pipeline import inverse_study as INV                             # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "inverse_repro")
for sym in (sys.argv[1:] or ["EURUSD.dk", "US100.dk"]):
    p = os.path.join(D, sym + ".csv")
    if not os.path.exists(p): print(f"{sym}: no journal"); continue
    rows = list(csv.DictReader(open(p)))
    parents = {r["signal_id"]: r for r in rows if r["strategy"] != "Inverse" and r["decision"] in ("approved", "approved_pending")}
    fills = [r for r in rows if r["strategy"] == "Inverse" and r.get("inv_state") == "2" and r["r_multiple"] != ""]
    states = {}
    for r in rows:
        if r["strategy"] == "Inverse": k = (r.get("inv_state"), (r.get("reject_why") or "").split(":")[0]); states[k] = states.get(k, 0) + 1
    h4 = load(sym, "h4"); idx = {b[0][:16]: k for k, b in enumerate(h4)}; M = INV.load_m1(sym); cost = DRAG.get(sym.split(".")[0], 0.007)
    ea, rp, unmatched = [], [], 0
    for f in fills:
        par = parents.get(f.get("parent_signal_id"))
        if not par or par["signal_time"][:16] not in idx: unmatched += 1; continue
        x = {"bars": h4, "i0": idx[par["signal_time"][:16]] + 1, "up": par["direction"].upper().startswith("B"),
             "e": float(par["entry"]), "s": float(par["sl"]), "tp1": float(par["tp1"]) if par["tp1"] else None,
             "tp2": float(par["tp2"]) if par["tp2"] else (float(par["tp"]) if par["tp"] else None),
             "strat": par["strategy"], "cost": cost, "t": par["signal_time"]}
        r = INV.trade(x, M)
        if not r or not (INV.W0 <= r[0] <= INV.W1): unmatched += 1; continue
        ea.append(float(f["r_multiple"])); rp.append(r[1][(0.0, False)] + cost)
    slips = [float(f["inv_slip_r"]) for f in fills if f.get("inv_slip_r")]
    print(f"{sym}: Inverse rows by state {dict(sorted(states.items()))}")
    if ea:
        gap = st.mean(ea) - st.mean(rp)
        print(f"  matched fills n={len(ea)} (unmatched {unmatched}): EA {st.mean(ea):+.3f}R  replay {st.mean(rp):+.3f}R  gap {gap:+.3f} "
              f"{'PASS' if abs(gap) <= 0.03 else 'FAIL'} | EA fill slippage mean {st.mean(slips):+.3f}R (n={len(slips)})")
    del M
