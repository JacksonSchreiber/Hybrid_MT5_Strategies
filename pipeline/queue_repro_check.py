#!/usr/bin/env python3
"""queue_repro_check.py - coach 2026-10-03 item 5: the EA's own real-tick reproduction of the coach-queue rules A / B / I, each ON vs OFF.

EA side: MT5 tester AA journals with the live stack (STAGED=1 PYR=1 TC_BANK=0.25 SHORT_RAISE=0.25), all four detectors, then one run
per rule with only that rule on:
  off : (nothing)        A : ADD_GATE=1        B : SHORT_CUT=0.5        I : IMPR_ADD=0.5
  -> data/study/queue_repro/<SYM>_{off,A,B,I}.csv (+ .actions.csv)
Per signal (signal_time + strategy) the full-position R = sum of r_multiple x lots / full lots over its rows (first tranche's 1R).
Signals are matched across the runs (a rule that changes an exit can shift the one-setup lock; unmatched signals are counted).

Study side: the SAME signals (from the off run's journal) replayed by pipeline/combo3_study (M1, SCALED) - so the two increments are
on an identical signal set. Checks per rule:
  1. trigger decisions: the EA's held adds (A) / day-2 cuts (B) / improving adds (I) vs the study's flags, signal by signal;
  2. mean increment (on - off): EA vs study, |difference| <= 0.02R (coach bar).
    python3 pipeline/queue_repro_check.py [EURUSD.dk US100.dk]
"""
import csv, glob, math, os, statistics as st, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                         # noqa: E402
from pipeline import combo3_study as K                           # noqa: E402
from pipeline.exit_s025_study import load, DRAG                  # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "queue_repro")
f = lambda x: float(x) if x not in (None, "") else None
RULES = {"A": "A", "B": "B", "I": "I"}
K.BANKF = dict(K.BANKF, DeepFib=0.5)          # the TESTER banks every non-TrendCont detector at 50% (InpTcBankFrac is TrendCont-only)


def journal(p):
    rows = [r for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")) if r.get("decision") in ("approved", "approved_pending")]
    acts = {}
    ap = p[:-4] + ".actions.csv"
    if os.path.exists(ap):
        for a in csv.DictReader(open(ap, newline="", encoding="ascii", errors="replace")):
            if a.get("action") == "DAY2_CUT": acts[a.get("posid")] = a
    g = {}
    for r in rows: g.setdefault((r["signal_time"][:16], r["strategy"]), []).append(r)
    out = {}
    for k, rs in g.items():
        if any(r.get("r_multiple") in (None, "") for r in rs): continue
        first = next((r for r in rs if int(f(r.get("tranche")) or 0) <= 1), rs[0])
        full = f(first.get("full_lots")) or f(first["lots"])
        tot = sum(f(r["r_multiple"]) * (f(r["lots"]) / full if full and f(r["lots"]) else 1.0) for r in rs)
        rj = first.get("reject_reason") or ""
        out[k] = {"tot": tot, "up": first["direction"].upper().startswith("B"), "first": first,
                  "held": ("add held" in rj or "held (gate)" in rj), "cut": any(r.get("posid") in acts for r in rs),
                  "impr": any(r.get("tranche") == "4" for r in rs)}
    return out


def study(sym, sigs):
    """the study's per-signal result and trigger flags for the off run's signals"""
    h4 = load(sym, "h4"); idx = {b[0][:16]: k for k, b in enumerate(h4)}
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); out = {}
    for k, s in sigs.items():
        r = s["first"]; i = idx.get(k[0])
        if i is None: continue
        x = {"sym": sym, "t": r["signal_time"], "up": s["up"], "e": f(r["entry"]), "s": f(r["sl"]), "tp1": f(r.get("tp1")),
             "tp2": f(r.get("tp2")) or f(r.get("tp")), "i0": i + 1, "bars": h4, "strat": k[1], "cost": DRAG.get(sym.split(".")[0], 0.007)}
        res = K.trade(S, x, "SCALED")
        u = A.setup(S, x, "SCALED")
        if not res or not u: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], "SCALED", u["scale"])
        cum = np.cumsum(F["bc"]); n = F["n"]; ka = u["k_add"]
        gate = ka is not None and 0 <= ka < n and cum[ka] / (ka + 1) < 0
        ib = u["i0"] + K.CHK
        kk = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
        avg2 = cum[kk] / (kk + 1) if (kk is not None and 0 <= kk < n) else None
        cut = avg2 is not None and not u["up"] and avg2 <= K.CUT_BAR
        impr = bool(avg2 is not None and u["up"] and ka is not None and 0 <= ka < kk and (cum[kk] - cum[ka]) / (kk - ka) > cum[ka] / (ka + 1))
        out[k] = {"res": res, "held": gate, "cut": cut, "impr": impr}
    return out


def tstat(v):
    if len(v) < 2: return float("nan")
    sd = st.pstdev(v); return st.mean(v) / (sd / math.sqrt(len(v))) if sd > 0 else 0.0


fail = False
for sym in (sys.argv[1:] or ["EURUSD.dk", "US100.dk"]):
    p0 = os.path.join(D, sym + "_off.csv")
    if not os.path.exists(p0): print(f"{sym}: off journal missing"); continue
    OFF = journal(p0); STU = study(sym, OFF)
    print(f"{sym}: off-run signals {len(OFF)}, study-replayed {len(STU)}")
    for rule, flag in (("A", "held"), ("B", "cut"), ("I", "impr")):
        p1 = os.path.join(D, f"{sym}_{rule}.csv")
        if not os.path.exists(p1): print(f"  {rule}: journal missing"); continue
        ON = journal(p1); both = sorted(set(OFF) & set(ON) & set(STU))
        ea_d = [ON[k]["tot"] - OFF[k]["tot"] for k in both]
        st_d = [STU[k]["res"][rule] - STU[k]["res"]["base"] for k in both]
        ea_f = {k for k in both if ON[k][flag]}; st_f = {k for k in both if STU[k][flag]}
        agree = len(ea_f & st_f); only_ea = sorted(ea_f - st_f); only_st = sorted(st_f - ea_f)
        m_ea, m_st = (st.mean(ea_d) if ea_d else float("nan")), (st.mean(st_d) if st_d else float("nan"))
        ok = abs(m_ea - m_st) <= 0.02
        fail |= not ok
        print(f"  {rule}: matched {len(both)} (unmatched off-only {len(set(OFF) - set(ON))}, on-only {len(set(ON) - set(OFF))}) | "
              f"triggers EA {len(ea_f)} study {len(st_f)} agree {agree} | increment EA {m_ea:+.4f} (t {tstat(ea_d):+.2f}) study {m_st:+.4f} "
              f"diff {m_ea - m_st:+.4f} -> {'PASS (<= 0.02R)' if ok else 'FAIL (> 0.02R)'}")
        for k in only_ea[:8]: print(f"     EA-only trigger: {k}")
        for k in only_st[:8]: print(f"     study-only trigger: {k}")
sys.exit(1 if fail else 0)
