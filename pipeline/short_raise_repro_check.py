#!/usr/bin/env python3
"""short_raise_repro_check.py - coach item 25 guard: the EA's own real-tick increment of the shorts-only stop raise.

EA side: MT5 tester AA journals with the live stack, rule off and on:
  STAGED=1 PYR=1 TC_BANK=0.25 [SHORT_RAISE=0.25] pipeline/mt5_verify.sh --mode ALL --strat SMC,Fib,TrendCont,EMArevQ
  --symbol <S> --from 2016.08.01 --to 2026.06.30 --model 4  ->  data/study/short_raise_repro/<SYM>_off.csv / <SYM>_on.csv
Per signal (signal_time + strategy): full-position R = sum over its rows (first tranche, staged add, pyramid add) of the row's
r_multiple x its lots / the full lots (the EA measures every row in the first tranche's 1R). Signals are
matched across the two runs; a stop that exits earlier can change the one-setup lock and so the later signal set - unmatched
signals are counted, not scored.
Guard (coach 2026-10-01): if the EA's mean increment on SHORTS (on - off, matched) is below -0.01R on a symbol, the rule is
switched off and the coach told. Longs must exit identically (their R can differ slightly: the tester sizes by equity, so lot
rounding drifts once the two runs' P&L diverge - only a changed exit price is a defect).
    python3 pipeline/short_raise_repro_check.py [EURUSD.dk US100.dk]
"""
import csv, math, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "short_raise_repro")
f = lambda x: float(x) if x not in (None, "") else None


def signals(p):
    rows = [r for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace"))
            if r.get("decision") in ("approved", "approved_pending") and r.get("r_multiple") not in (None, "")]
    g = {}
    for r in rows: g.setdefault((r["signal_time"], r["strategy"]), []).append(r)
    out = {}
    for k, rs in g.items():
        first = next((r for r in rs if int(f(r.get("tranche")) or 0) <= 1), rs[0])
        e0, s0 = f(first["entry"]), f(first["sl"]); full = f(first.get("full_lots")) or f(first["lots"])
        tot = 0.0
        for r in rs:
            lots, e, s = f(r["lots"]), f(r["entry"]), f(r["sl"])
            sc = lots / full if (full and lots) else 1.0     # r_multiple is in the FIRST tranche's 1R for every row (risk_px = anchor)
            tot += f(r["r_multiple"]) * sc
        out[k] = (first["direction"].upper().startswith("S"), tot, any((r.get("short_raise") or "") == "1" for r in rs), first["signal_time"],
                  tuple(sorted((r.get("tranche") or "", r.get("exit_price") or "") for r in rs)))
    return out


def tstat(v):
    if len(v) < 2: return float("nan")
    sd = st.pstdev(v); return st.mean(v) / (sd / math.sqrt(len(v))) if sd > 0 else 0.0


fail = False
for sym in (sys.argv[1:] or ["EURUSD.dk", "US100.dk"]):
    a, b = os.path.join(D, sym + "_off.csv"), os.path.join(D, sym + "_on.csv")
    if not (os.path.exists(a) and os.path.exists(b)): print(f"{sym}: journals missing"); continue
    A, B = signals(a), signals(b); both = sorted(set(A) & set(B), key=lambda k: k[0])
    for side, lab in ((True, "SHORTS"), (False, "LONGS ")):
        ks = [k for k in both if A[k][0] == side]
        d = [B[k][1] - A[k][1] for k in ks]
        armed = sum(1 for k in ks if B[k][2]); hp = sum(1 for x in d if x > 1e-6); ht = sum(1 for x in d if x < -1e-6)
        m = st.mean(d) if d else float("nan")
        verdict = ""
        if side: verdict = "GUARD TRIPPED (< -0.01R): switch the rule off" if m < -0.01 else "guard holds (>= -0.01R)"; fail |= m < -0.01
        else:   # equity-based sizing re-rounds lots once the runs' P&L diverge - only a changed EXIT is a defect
            moved = sum(1 for k in ks if A[k][4] != B[k][4])
            verdict = (f"DEFECT: {moved} long exits changed" if moved else "longs: exits identical (R differs only by lot rounding)")
            fail |= moved > 0
        print(f"{sym} {lab} matched n={len(ks):4d} armed {armed:3d}  off {st.mean(A[k][1] for k in ks):+.4f}  on {st.mean(B[k][1] for k in ks):+.4f}"
              f"  increment {m:+.4f} (t {tstat(d):+.2f})  helped/hurt {hp}/{ht}  {verdict}")
    print(f"{sym} unmatched signals: off-only {len(set(A) - set(B))}, on-only {len(set(B) - set(A))}")
    hurt = sorted(((B[k][1] - A[k][1], k) for k in both if A[k][0] and B[k][1] - A[k][1] < -1e-6))
    for x, k in hurt: print(f"   hurt: {k[0]} {k[1]} {A[k][1]:+.2f} -> {B[k][1]:+.2f} ({x:+.2f})")
sys.exit(1 if fail else 0)
