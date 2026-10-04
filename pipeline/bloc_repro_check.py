#!/usr/bin/env python3
"""bloc_repro_check.py - trader 2026-10-04: the EA's real-tick reproduction of the commodity-bloc T+L1.0 rules vs the study.
EA side: data/study/bloc_repro/<SYM>_{off,bloc}.csv (+ .actions.csv) from pipeline/bloc_repro_run.sh (live stack, bloc OFF vs ON).
Per signal (signal_time + strategy) the full-position R = sum of r_multiple x lots / full lots (first tranche's 1R); cut = a BLOC_CUT
action on any of its tickets, lock = a BLOC_LOCK action.
Study side: the SAME signals (the population row with that time + detector) replayed by cbloc_timestop_study.run_h "live" and
"T+L1.0" (SCALED), banks as the TESTER does them (TrendCont 25%, every other detector 50%). Checks:
  1. cut decisions, signal by signal: EA BLOC_CUT vs the study's time stop (only trades still open at the bar-6 close count);
  2. mean increment (bloc - off): EA vs study, |difference| <= 0.02R (the coach's repro bar).
    python3 pipeline/bloc_repro_check.py [AUDUSD.dk]
"""
import csv, math, os, statistics as st, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                         # noqa: E402
from pipeline import combo_build as CB                           # noqa: E402
from pipeline import cbloc_timestop_study as C                   # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "study", "bloc_repro")
f = lambda x: float(x) if x not in (None, "") else None
TESTER_BANK = {"TrendCont": 0.25}                                # InpTcBankFrac is TrendCont-only; the tester banks the rest at 50%


def journal(p):
    rows = [r for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace")) if r.get("decision") in ("approved", "approved_pending")]
    acts = {}
    ap = p[:-4] + ".actions.csv"
    if os.path.exists(ap):
        for a in csv.DictReader(open(ap, newline="", encoding="ascii", errors="replace")):
            if a.get("action") in ("BLOC_CUT", "BLOC_LOCK"): acts.setdefault(a.get("posid"), set()).add(a["action"])
    g = {}
    for r in rows: g.setdefault((r["signal_time"][:16], r["strategy"]), []).append(r)
    out = {}
    for k, rs in g.items():
        if any(r.get("r_multiple") in (None, "") for r in rs): continue
        first = next((r for r in rs if int(f(r.get("tranche")) or 0) <= 1), rs[0])
        full = f(first.get("full_lots")) or f(first["lots"])
        tot = sum(f(r["r_multiple"]) * (f(r["lots"]) / full if full and f(r["lots"]) else 1.0) for r in rs)
        a = set().union(*[acts.get(r.get("posid"), set()) for r in rs])
        out[k] = {"tot": tot, "up": first["direction"].upper().startswith("B"), "cut": "BLOC_CUT" in a, "lock": "BLOC_LOCK" in a}
    return out


def study(sym, keys):
    pop = {(x["t"][:16], x["strat"]): x for x in CB.population() if x["sym"] == sym}
    S = CB.sym_obj(sym); out = {}
    for k in keys:
        x = pop.get(k)
        if x is None: continue
        u = A.setup(S, x, "SCALED")
        if not u: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], "SCALED", u["scale"])
        e0 = float(F["eo"][0]); bankf = TESTER_BANK.get(x["strat"], 0.5)
        live = C.run_h(F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], None, None)
        tl = C.run_h(F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], 1, 1.0)
        out[k] = {"live": live[0], "tl": tl[0], "cut": tl[2] == "time"}
    return out


def tstat(v):
    if len(v) < 2: return float("nan")
    sd = st.pstdev(v); return st.mean(v) / (sd / math.sqrt(len(v))) if sd > 0 else 0.0


def main():
    sym = (sys.argv[1:] or ["AUDUSD.dk"])[0]
    p0, p1 = os.path.join(D, sym + "_off.csv"), os.path.join(D, sym + "_bloc.csv")
    if not (os.path.exists(p0) and os.path.exists(p1)): print(f"{sym}: journals missing"); return 2
    OFF, ON = journal(p0), journal(p1)
    STU = study(sym, set(OFF) | set(ON))
    both = sorted(set(OFF) & set(ON) & set(STU))
    ea_d = [ON[k]["tot"] - OFF[k]["tot"] for k in both]; st_d = [STU[k]["tl"] - STU[k]["live"] for k in both]
    ea_c = {k for k in both if ON[k]["cut"]}; st_c = {k for k in both if STU[k]["cut"]}
    m_ea, m_st = st.mean(ea_d), st.mean(st_d); ok = abs(m_ea - m_st) <= 0.02
    L = [__doc__.split("\n")[0], f"{sym}: signals off {len(OFF)}, bloc {len(ON)}, matched with the study {len(both)} (off-only {len(set(OFF) - set(ON))}, "
         f"bloc-only {len(set(ON) - set(OFF))}, no population row {len((set(OFF) | set(ON)) - set(STU))})",
         f"  cuts: EA {len(ea_c)}, study {len(st_c)}, agree {len(ea_c & st_c)} ({100 * len(ea_c & st_c) / max(1, len(ea_c | st_c)):.0f}% of the union); "
         f"locks EA {sum(1 for k in both if ON[k]['lock'])}",
         f"  off: EA {st.mean([OFF[k]['tot'] for k in both]):+.4f}R/trade vs study live {st.mean([STU[k]['live'] for k in both]):+.4f}",
         f"  bloc: EA {st.mean([ON[k]['tot'] for k in both]):+.4f}R/trade vs study T+L1.0 {st.mean([STU[k]['tl'] for k in both]):+.4f}",
         f"  increment (bloc - off): EA {m_ea:+.4f} (t {tstat(ea_d):+.2f}) vs study {m_st:+.4f} -> diff {m_ea - m_st:+.4f} {'PASS (<= 0.02R)' if ok else 'FAIL (> 0.02R)'}"]
    for k in sorted(ea_c - st_c)[:8]: L.append(f"     EA-only cut: {k}")
    for k in sorted(st_c - ea_c)[:8]: L.append(f"     study-only cut: {k}")
    open(os.path.join(D, sym + "_check.txt"), "w").write("\n".join(L) + "\n"); print("\n".join(L))
    return 0 if ok else 1


if __name__ == "__main__": sys.exit(main())
