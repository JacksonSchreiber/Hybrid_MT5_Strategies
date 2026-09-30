#!/usr/bin/env python3
"""pyramid_repro_check.py - coach item 20 step 1: does the EA's own code reproduce the +1.5R pyramid gain?

Inputs: MT5 tester AA journals per symbol from `TC_BANK=0.25 [PYR=1] pipeline/mt5_verify.sh --mode ALL
--strat SMC,Fib,TrendCont,EMArevQ --symbol <S> --from .. --to ..` -> <dir>/<SYM>_nopyr.csv and <SYM>_pyr.csv.
The EA writes the pyramid as its own row (same signal_id/time, entry_mode not journaled in the tester, so identified as the
second approved row of a signal_time); per-signal R in the first position's 1R = R_first + R_add x add_lots / first_lots.
The Python replay scores the SAME tester trades from their journal levels: bank 25% (TrendCont) or 50% (others) at +1R or TP1
if nearer, stop to entry; with the pyramid, the first bar whose high reaches +1.5R after the bank adds 0.5x at +1.5R (the
open if it gaps past), stop at entry, target TP2 (the same bar may carry it to TP2).
Pass: |EA gain - replay gain| <= 0.02R per signal on each symbol.
    python3 pipeline/pyramid_repro_check.py <dir>
"""
import csv, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import paired_t                 # noqa: E402
from pipeline.trendcont_step13_study import load              # noqa: E402

MAXB = 600


def replay(bars, i0, up, e, s, tp1, tp2, bank, pyr):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    trig = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    if tgt is not None and trig >= tgt - 1e-9: bank = 0.0
    stop, banked, locked, rem, add_at, add_sz = -1.0, False, 0.0, 1.0, None, 0.0
    val = lambda r: locked + rem * r + (add_sz * (r - add_at) if add_at is not None else 0.0)
    for j in range(i0, min(len(bars), i0 + MAXB)):
        b = bars[j]; o = toR(b[1]); hi, lo = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo <= stop: return val(min(stop, o))
        if not banked and hi >= trig:
            banked = True; locked = bank * trig; rem = 1.0 - bank; stop = max(stop, 0.0)
        if banked and pyr and add_at is None and hi >= 1.5:
            add_at, add_sz = max(1.5, o), 0.5
        if tgt is not None and hi >= tgt: return val(tgt)
        if banked: stop = max(stop, 0.0)
    return val(toR(bars[min(len(bars), i0 + MAXB) - 1][4]))


def ea_signals(path):
    by = defaultdict(list)
    for r in csv.DictReader(open(path)):
        if r["decision"] == "approved" and r["r_multiple"] != "": by[(r["signal_time"], r["strategy"])].append(r)
    out = {}
    for k, rs in by.items():
        first = rs[0]; l1 = float(first["lots"]); tot = float(first["r_multiple"])
        for x in rs[1:]: tot += float(x["r_multiple"]) * float(x["lots"]) / l1
        out[k] = (first, tot, len(rs) > 1)
    return out


D = sys.argv[1]
for sym in ("EURUSD.dk", "US100.dk"):
    h4 = load(sym, "h4"); idx = {b[0][:16]: k for k, b in enumerate(h4)}
    a, b = ea_signals(os.path.join(D, sym + "_nopyr.csv")), ea_signals(os.path.join(D, sym + "_pyr.csv"))
    ea0, ea1, py0, py1, nadd = [], [], [], [], 0
    for k, (r, R0, _) in a.items():
        if k not in b or k[0][:16] not in idx: continue
        e, s = float(r["entry"]), float(r["sl"]); tp1 = float(r["tp1"]) if r["tp1"] else None; tp2 = float(r["tp2"]) if r["tp2"] else (float(r["tp"]) if r["tp"] else None)
        up = r["direction"].upper().startswith("B"); bank = 0.25 if r["strategy"] == "TrendCont" else 0.5
        i0 = idx[k[0][:16]] + 1
        ea0.append(R0); ea1.append(b[k][1]); nadd += b[k][2]
        py0.append(replay(h4, i0, up, e, s, tp1, tp2, bank, False)); py1.append(replay(h4, i0, up, e, s, tp1, tp2, bank, True))
    de, dp = st.mean(ea1) - st.mean(ea0), st.mean(py1) - st.mean(py0)
    print(f"{sym} n={len(ea0)} signals ({nadd} pyramided in the EA): EA gain {de:+.4f} (t {paired_t(ea0, ea1):+.2f}) | "
          f"replay gain {dp:+.4f} (t {paired_t(py0, py1):+.2f}) | gap {de - dp:+.4f} {'PASS' if abs(de - dp) <= 0.02 else 'FAIL'}")
