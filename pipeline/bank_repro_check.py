#!/usr/bin/env python3
"""bank_repro_check.py - coach 2026-09-30 guard 1: does the EA's own exit code reproduce the 25%-vs-50% bank gain?

Inputs: two MT5 tester AA journals per symbol from `TC_BANK=0.5|0.25 pipeline/mt5_verify.sh --mode ALL
--strat SMC,Fib,TrendCont,EMArevQ --symbol <S> --from .. --to ..` (EURUSD.dk 2016.08.01-2026.06.29, US100.dk
2012.02.23-2025.12.30). The tester's TrendCont trades are replayed in Python from their OWN journal levels (the tester's
signal set differs from the study record through the one-setup lock cascade), so both sides score the same trades.
Pass: |EA gain - replay gain| <= 0.02R per trade on each symbol.
    python3 pipeline/bank_repro_check.py <dir holding SYM_0.5.csv and SYM_0.25.csv>
"""
import csv, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_param_study import replay                  # noqa: E402
from pipeline.exit_mgmt_study import paired_t                 # noqa: E402
from pipeline.trendcont_step13_study import load              # noqa: E402

D = sys.argv[1]
for sym in ("EURUSD.dk", "US100.dk"):
    h4 = load(sym, "h4"); idx = {b[0][:16]: k for k, b in enumerate(h4)}
    ld = lambda f: {r["signal_time"]: r for r in csv.DictReader(open(os.path.join(D, sym + f)))
                    if r["strategy"] == "TrendCont" and r["decision"] == "approved" and r["r_multiple"] != ""}
    a, b = ld("_0.5.csv"), ld("_0.25.csv")
    ea, eb, pa, pb = [], [], [], []
    for t, r in a.items():
        if t not in b or t[:16] not in idx: continue
        e, s = float(r["entry"]), float(r["sl"])
        tp1 = float(r["tp1"]) if r["tp1"] else None; tp2 = float(r["tp2"]) if r["tp2"] else None
        up = r["direction"].upper().startswith("B")
        x = replay(h4, idx[t[:16]] + 1, up, e, s, tp1, tp2, "doc", 0.5, 0.0)
        y = replay(h4, idx[t[:16]] + 1, up, e, s, tp1, tp2, "doc", 0.25, 0.0)
        if x is None or y is None: continue
        ea.append(float(r["r_multiple"])); eb.append(float(b[t]["r_multiple"])); pa.append(x); pb.append(y)
    de, dp = st.mean(eb) - st.mean(ea), st.mean(pb) - st.mean(pa)
    print(f"{sym} n={len(ea)}: EA 50% {st.mean(ea):+.4f} -> 25% {st.mean(eb):+.4f} (gain {de:+.4f}, t {paired_t(ea, eb):+.2f}) | "
          f"replay {st.mean(pa):+.4f} -> {st.mean(pb):+.4f} (gain {dp:+.4f}) | gap {de - dp:+.4f} {'PASS' if abs(de - dp) <= 0.02 else 'FAIL'}")
