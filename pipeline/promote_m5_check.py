#!/usr/bin/env python3
"""promote_m5_check.py - the early-promotion reproduction's trades replayed on M5 instead of H4 (coach 2026-10-01).

Why: inside one H4 bar that touches +0.5R AND the stop, the H4 replay checks the stop first and never adds, while the EA
(tick by tick) adds at +0.5R and is then stopped - so the H4 replay flatters early promotion. M5 removes most of that
ambiguity. Same rules as promote_repro_check.replay, one M5 bar at a time (bars 1..5 = the first five H4 bars after the
signal bar; the bar-6 add at the last M5 bar of H4 bar 6), per-trade price offset as m5_study.py.
    python3 pipeline/promote_m5_check.py <dir> [SYMBOL ...]
"""
import bisect, csv, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import paired_t                 # noqa: E402
from pipeline.trendcont_step13_study import load              # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                  # noqa: E402

F0, N, EARLY = 0.25, 6, 0.5
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "promote_repro_check.py")).read().split("\nD = sys.argv[1]")[0]
_ns = {"__file__": __file__}; exec(compile(_src, "promote_repro_check", "exec"), _ns)
ea_signals = _ns["ea_signals"]


def replay_m5(M, k0, k_early_end, k_add, up, e, s, tp1, tp2, bank, early):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    trig = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    if tgt is not None and trig >= tgt - 1e-9: bank = 0.0
    legs = [(F0, 0.0)]; locked = 0.0; stop = -1.0; banked = False; added = False
    val = lambda r: locked + sum(sz * (r - en) for sz, en in legs)
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k])); last = c
        if lo <= stop: return val(min(stop, o))
        if early and not added and k < k_early_end and hi >= EARLY:
            legs.append((1 - F0, max(EARLY, o))); added = True
        if not banked and hi >= trig:
            banked = True; locked += bank * sum(sz * (trig - en) for sz, en in legs)
            legs = [(sz * (1 - bank), en) for sz, en in legs]; stop = 0.0
        if tgt is not None and hi >= tgt: return val(tgt)
        if not added and k_add is not None and k == k_add:
            legs.append((1 - F0, c)); added = True
    return val(last)


D = sys.argv[1]; syms = sys.argv[2:] or ["EURUSD.dk", "US100.dk"]
for sym in syms:
    h4 = load(sym, "h4"); idx = {b[0][:16]: k for k, b in enumerate(h4)}; M = load_m5(sym)
    a, b = ea_signals(os.path.join(D, sym + "_plain.csv")), ea_signals(os.path.join(D, sym + "_early.csv"))
    ea0, ea1, m0, m1 = [], [], [], []
    for key, (r, R0, _, staged) in a.items():
        if key not in b or not staged or key[0][:16] not in idx: continue
        i = idx[key[0][:16]]; i0 = i + 1
        if i0 + N >= len(h4): continue
        k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        k_early_end = bisect.bisect_left(M["t"], h4[i0 + N - 1][0][:16])            # start of H4 bar 6
        k_add = bisect.bisect_left(M["t"], h4[i0 + N][0][:16]) - 1                     # last M5 bar of H4 bar 6
        off = h4[i][4] - M["c"][k0 - 1]
        e, s = float(r["entry"]) - off, float(r["sl"]) - off
        tp1 = (float(r["tp1"]) - off) if r["tp1"] else None
        tp2 = (float(r["tp2"]) - off) if r["tp2"] else ((float(r["tp"]) - off) if r["tp"] else None)
        up = r["direction"].upper().startswith("B"); bank = 0.25 if r["strategy"] == "TrendCont" else 0.5
        ea0.append(R0); ea1.append(b[key][1])
        m0.append(replay_m5(M, k0, k_early_end, k_add, up, e, s, tp1, tp2, bank, False))
        m1.append(replay_m5(M, k0, k_early_end, k_add, up, e, s, tp1, tp2, bank, True))
    de, dm = st.mean(ea1) - st.mean(ea0), st.mean(m1) - st.mean(m0)
    print(f"{sym} n={len(ea0)}: EA gain {de:+.4f} (t {paired_t(ea0, ea1):+.2f}) | M5 replay gain {dm:+.4f} (t {paired_t(m0, m1):+.2f}) "
          f"| gap {de - dm:+.4f} {'PASS' if abs(de - dm) <= 0.02 else 'FAIL'}")
