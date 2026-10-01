#!/usr/bin/env python3
"""promote_repro_check.py - coach 2026-10-01 (item 18 addition): does the EA's own code reproduce the EARLY PROMOTION gain?

EA side: MT5 tester AA journals from `TC_BANK=0.25 STAGED=1 [EARLY=0.5] pipeline/mt5_verify.sh --mode ALL
--strat SMC,Fib,TrendCont,EMArevQ --symbol <S> --from .. --to .. --model 4` -> <dir>/<SYM>_plain.csv and <SYM>_early.csv.
With InpStaged the tester journal carries entry_mode,tranche,full_lots; a signal's R in FULL-position units =
sum(lots x R per lot) / full_lots over its tranche rows (keyed by signal_time + strategy).
Replay side: the SAME trades from the first row's levels (orig stop/targets): 25% at the signal, the rest at the close of
H4 bar 6 if still open - or, with early promotion, at +0.5R (the bar's open if it gapped past) the first time a bar of
1..5 reaches it; bank 25% (TrendCont) / 50% (others) at +1R or TP1 if nearer on every leg open then, stop for the whole
position to the ORIGINAL entry, runner to TP2. Stop checked first each bar.
Pass: |EA gain - replay gain| <= 0.02R per signal on each symbol.
    python3 pipeline/promote_repro_check.py <dir> [SYMBOL ...]
"""
import csv, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import paired_t                 # noqa: E402
from pipeline.trendcont_step13_study import load              # noqa: E402

MAXB, F0, N, EARLY = 600, 0.25, 6, 0.5


def replay(bars, i0, up, e, s, tp1, tp2, bank, early):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    trig = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    if tgt is not None and trig >= tgt - 1e-9: bank = 0.0
    legs = [(F0, 0.0)]; locked = 0.0; stop = -1.0; banked = False; added = False
    val = lambda r: locked + sum(sz * (r - en) for sz, en in legs)
    last = None
    for j in range(i0, min(len(bars), i0 + MAXB)):
        b = bars[j]; o = toR(b[1]); c = toR(b[4]); hi, lo = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2])); last = c
        if lo <= stop: return val(min(stop, o))
        if early and not added and j - i0 + 1 < N and hi >= EARLY:            # bars 1..N-1
            legs.append((1 - F0, max(EARLY, o))); added = True
        if not banked and hi >= trig:
            banked = True; locked += bank * sum(sz * (trig - en) for sz, en in legs)
            legs = [(sz * (1 - bank), en) for sz, en in legs]; stop = 0.0
        if tgt is not None and hi >= tgt: return val(tgt)
        if not added and j - i0 + 1 == N:                                       # the bar-N close
            legs.append((1 - F0, c)); added = True
    return val(last)


def ea_signals(path):
    by = defaultdict(list)
    for r in csv.DictReader(open(path)):
        if r["decision"] in ("approved", "approved_pending") and r["r_multiple"] != "": by[(r["signal_time"], r["strategy"])].append(r)
    out = {}
    for k, rs in by.items():
        first = rs[0]; full = float(first.get("full_lots") or 0) or float(first["lots"])
        out[k] = (first, sum(float(x["lots"]) * float(x["r_multiple"]) for x in rs) / full,
                  any((x.get("entry_mode") or "").endswith("early") for x in rs), int(first.get("tranche") or 0) == 1)
    return out


D = sys.argv[1]; syms = sys.argv[2:] or ["EURUSD.dk", "US100.dk"]
for sym in syms:
    h4 = load(sym, "h4"); idx = {b[0][:16]: k for k, b in enumerate(h4)}
    a, b = ea_signals(os.path.join(D, sym + "_plain.csv")), ea_signals(os.path.join(D, sym + "_early.csv"))
    ea0, ea1, py0, py1, ne, ns = [], [], [], [], 0, 0
    for k, (r, R0, _, staged) in a.items():
        if k not in b or k[0][:16] not in idx: continue
        e, s = float(r["entry"]), float(r["sl"]); tp1 = float(r["tp1"]) if r["tp1"] else None
        tp2 = float(r["tp2"]) if r["tp2"] else (float(r["tp"]) if r["tp"] else None)
        up = r["direction"].upper().startswith("B"); bank = 0.25 if r["strategy"] == "TrendCont" else 0.5
        i0 = idx[k[0][:16]] + 1
        if not staged: continue                                                 # too small to split: identical in both runs
        ns += 1; ne += b[k][2]
        ea0.append(R0); ea1.append(b[k][1])
        py0.append(replay(h4, i0, up, e, s, tp1, tp2, bank, False)); py1.append(replay(h4, i0, up, e, s, tp1, tp2, bank, True))
    de, dp = st.mean(ea1) - st.mean(ea0), st.mean(py1) - st.mean(py0)
    print(f"{sym} n={ns} staged signals ({ne} promoted early in the EA): EA gain {de:+.4f} (t {paired_t(ea0, ea1):+.2f}) | "
          f"replay gain {dp:+.4f} (t {paired_t(py0, py1):+.2f}) | gap {de - dp:+.4f} {'PASS' if abs(de - dp) <= 0.02 else 'FAIL'}")
