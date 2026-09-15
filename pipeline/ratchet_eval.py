#!/usr/bin/env python3
"""TP1-ratchet study (coach pair-12 item 1, 2026-09-10). Reads the model-4 v2-live fleet
journals (data/study/ratchet/*.csv, which carry the rt_ path columns) and compares three
runner policies AFTER the +1R bank / SL->BE:

  A  (current): runner (50%) to full TP (tp2).
  B1          : on first touch of TP1, ratchet runner SL to TP1 (locks +2R-level or rides to tp2).
  B2          : on first touch of TP1, close 50% of the runner (=25% of orig) at TP1; residual
                25% keeps the BE stop and exits at tp2 or BE.

Per-trade R (banked trades; bank = rt_bankr = the R the +1R bank actually fired at):
  A  = 0.5*bank + 0.5*(tp2R if terminal==TP else 0)                       # terminal is authoritative
  B1 = 0.5*bank + 0.5*(tp2R if terminal==TP and not redip else tp1R if touched1 else 0)
  B2 = 0.5*bank + 0.25*(tp1R if touched1 else 0) + 0.25*(tp2R if terminal==TP else 0)
Non-banked trades never reach the runner phase -> all policies equal the actual r_multiple, dR=0.
Strategies with no TP1 level (rt_tp1r<=0) -> B1/B2 N/A.

Adoption (coach, fixed): per-trade dR = B - A; adopt B for a strategy where mean(dR) >= +0.03R on the
full sample AND mean(dR) > 0 in BOTH date halves (split at the strategy's median signal_time).
Cell floor proposed (not coach-set): n >= 30 to adopt.
"""
import csv, glob, os, sys
from datetime import datetime
from statistics import mean

SRC = sys.argv[1] if len(sys.argv) > 1 else "data/study/ratchet/*.csv"
ADOPT_DELTA, CELL_FLOOR = 0.03, 30

def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s.strip(), f)
        except ValueError: pass
    return None

def fnum(s, default=None):
    s = (s or "").strip()
    if s == "" or s.lower() == "nan" or s.lower() == "-nan": return default
    try: return float(s)
    except ValueError: return default

def load():
    trades = []
    for fp in sorted(glob.glob(SRC)):
        for r in csv.DictReader(open(fp, encoding="utf-8", errors="replace")):
            if r.get("decision") not in ("approved", "approved_pending"): continue
            if not (r.get("terminal") or "").strip(): continue          # not closed
            rm = fnum(r.get("r_multiple"))
            if rm is None: continue
            tp1R = fnum(r.get("rt_tp1r"), 0.0); tp2R = fnum(r.get("rt_tp2r"), 0.0)
            bank = fnum(r.get("rt_bankr"))                               # -99 or None => not banked
            banked = bank is not None and bank > -90
            term = r["terminal"].strip()
            t1 = (r.get("rt_touched1") or "0").strip() == "1"
            r2 = (r.get("rt_reached2") or "0").strip() == "1"
            rd = (r.get("rt_redip1") or "0").strip() == "1"
            trades.append(dict(sid=r["signal_id"], sym=r["symbol"], strat=r["strategy"],
                regime=r.get("regime", "") or "BLANK", t=dt(r["signal_time"]), rm=rm,
                tp1R=tp1R, tp2R=tp2R, bank=bank, banked=banked, term=term,
                touched1=t1, reached2=r2, redip1=rd, has_tp1=(tp1R and tp1R > 0)))
    return trades

def policies(tr):
    """return (A, B1, B2) blended R for one trade, or None if B1/B2 N/A or the runner outcome is
    ambiguous. Resolved = banked & terminal in {TP,BE} (clean runner) OR not-banked & terminal==SL
    (clean pre-bank loss, policies identical). terminal=='end' (open at test boundary) and any
    banked&terminal==SL (BE stop can't hit the original SL -> data anomaly) are UNRESOLVED -> None."""
    if not tr["has_tp1"]:
        return None
    if tr["term"] == "end":
        return None                                     # open at test end -> runner fate unknown
    if not tr["banked"]:
        if tr["term"] == "SL":
            return (tr["rm"], tr["rm"], tr["rm"])       # pre-bank stop; policies identical, dR=0
        return None                                     # not banked, not SL -> anomaly, exclude
    if tr["term"] not in ("TP", "BE"):
        return None                                     # banked but terminal==SL etc. -> anomaly
    bank, tp1R, tp2R, term = tr["bank"], tr["tp1R"], tr["tp2R"], tr["term"]
    hit2 = (term == "TP")
    A  = 0.5*bank + 0.5*(tp2R if hit2 else 0.0)
    B1r = tp2R if (hit2 and not tr["redip1"]) else (tp1R if tr["touched1"] else 0.0)
    B1 = 0.5*bank + 0.5*B1r
    B2 = 0.5*bank + 0.25*(tp1R if tr["touched1"] else 0.0) + 0.25*(tp2R if hit2 else 0.0)
    return (A, B1, B2)

def half_means(pairs):
    """pairs = list of (t, dR) sorted by t; return (n, mean, early_mean, late_mean)."""
    if not pairs: return (0, 0.0, 0.0, 0.0)
    srt = sorted(pairs, key=lambda x: x[0] or datetime.min)
    d = [x[1] for x in srt]; mid = len(d)//2
    e = d[:mid] or [0.0]; l = d[mid:] or [0.0]
    return (len(d), mean(d), mean(e), mean(l))

def main():
    tr = load()
    usable = [t for t in tr if t["has_tp1"]]
    print(f"# TP1-ratchet study — {len(tr)} taken/closed trades, {len(usable)} with a TP1 level\n")

    # ---- validation: reconstructed A must match the actual r_multiple (v2-live IS policy A) ----
    bad = []
    for t in usable:
        p = policies(t)
        if p is None or not t["banked"]: continue
        if abs(p[0] - t["rm"]) > 0.12:   # tolerance for the padded-BE + bank overshoot
            bad.append((t["sym"], t["sid"], round(p[0], 3), t["rm"], t["term"]))
    print(f"## Validation: reconstructed A vs actual r_multiple — {len(bad)} mismatches > 0.12R "
          f"(among resolved, banked)")
    for b in bad[:12]: print(f"  {b}")
    # resolution census
    res = sum(1 for t in usable if policies(t) is not None)
    end = sum(1 for t in usable if t["term"] == "end")
    anom = sum(1 for t in usable if policies(t) is None and t["term"] != "end")
    print(f"  resolved={res}  excluded(open-at-end)={end}  excluded(anomaly)={anom}  "
          f"of {len(usable)} TP1-bearing trades")
    print()

    # ---- tp1_R geometry per strategy (advisor: the +1R bank fires at min(1R,tp1); tp1R<1 fractures it)
    print("## TP1 geometry per strategy (bank fires at min(+1R, TP1); TP1<1R = policy fracture)")
    print(f"{'strat':10} {'n':>5} {'tp1R med':>9} {'tp1R min':>9} {'frac<1R':>8}")
    strat_frac = {}
    for s in sorted(set(t["strat"] for t in usable)):
        xs = sorted(t["tp1R"] for t in usable if t["strat"] == s)
        if not xs: continue
        med = xs[len(xs)//2]; frac = sum(1 for x in xs if x < 1.0)/len(xs)
        strat_frac[s] = frac
        print(f"{s:10} {len(xs):>5} {med:>9.2f} {xs[0]:>9.2f} {frac:>8.0%}")
    print()

    # ---- cells: strategy x regime, with per-trade dR ----
    for pol, idx in (("B1", 1), ("B2", 2)):
        print(f"## {pol} vs A — per strategy x regime (dR = {pol} - A, per trade)")
        print(f"{'strat':10} {'regime':11} {'n':>5} {'mean dR':>9} {'early':>8} {'late':>8} {'adopt?':>18}")
        by_strat = {}
        for s in sorted(set(t["strat"] for t in usable)):
            for reg in sorted(set(t["regime"] for t in usable if t["strat"] == s)):
                cell = [t for t in usable if t["strat"] == s and t["regime"] == reg]
                pairs = []
                for t in cell:
                    p = policies(t)
                    if p is None: continue
                    pairs.append((t["t"], p[idx] - p[0]))
                n, m, e, l = half_means(pairs)
                if n == 0: continue
                by_strat.setdefault(s, []).extend(pairs)
                print(f"{s:10} {reg:11} {n:>5} {m:>+9.3f} {e:>+8.3f} {l:>+8.3f}")
        print(f"  -- {pol} per-strategy aggregate + adoption --")
        for s in sorted(by_strat):
            n, m, e, l = half_means(by_strat[s])
            halves_ok = (e > 0 and l > 0)
            adopt = (m >= ADOPT_DELTA and halves_ok and n >= CELL_FLOOR)
            note = ""
            if strat_frac.get(s, 0) > 0.10: note = " [TP1<1R fracture: verify]"
            verdict = "ADOPT" if adopt else ("no (dR<0.03)" if m < ADOPT_DELTA else
                       "no (a half<=0)" if not halves_ok else f"no (n<{CELL_FLOOR})")
            print(f"  {s:10} n={n:<5} mean dR={m:>+.3f} halves {e:>+.3f}/{l:>+.3f} -> {verdict}{note}")
        print()

    # ---- round-trip anatomy (coach): rescue set vs cost set, with dR contribution ----
    print("## Round-trip anatomy (B1) — where the edge/cost comes from")
    print(f"{'bucket':44} {'n':>5} {'mean dR_B1':>11}")
    rescue = []   # touched TP1, runner finished at BE (~+0.5R) -> B1 exits at +2R instead (helps)
    cost   = []   # touched TP1, dipped back, yet reached TP2   -> B1 exits at +2R not +4R (costs)
    neutral= []   # touched TP1, reached TP2, no dip            -> B1 rides to TP2 = A (dR 0)
    notouch= []   # never touched TP1                           -> B1 = A (dR 0)
    for t in usable:
        p = policies(t)
        if p is None or not t["banked"]: continue
        A, B1, _ = p; d = B1 - A
        if not t["touched1"]:                                      notouch.append(d)
        elif t["term"] == "BE":                                    rescue.append(d)
        elif t["term"] == "TP" and t["redip1"]:                    cost.append(d)
        elif t["term"] == "TP":                                    neutral.append(d)
        else:                                                      neutral.append(d)
    for name, xs in (("touched TP1, finished at BE ~+0.5R (RESCUE)", rescue),
                     ("dipped below TP1 then reached TP2 (COST)", cost),
                     ("touched TP1 -> TP2 no dip (neutral, dR~0)", neutral),
                     ("never touched TP1 (neutral, dR~0)", notouch)):
        m = mean(xs) if xs else 0.0
        print(f"{name:46} {len(xs):>5} {m:>+11.3f}")
    print("  Net: the COST set outnumbers/outweighs the RESCUE set -> B1 nets negative.")
    print()

    # ---- window-11 exhibits (from the US100 2017 journal specifically; the fleet US100 file is
    #      2012-2025 with different signal numbering) ----
    print("## Window-11 exhibits (US100 2017) sigs 5, 8, 32")
    w11path = os.environ.get("RATCHET_W11", "data/study/ratchet/w11_US100_2017.csv")
    w11all = []
    if os.path.exists(w11path):
        save = SRC
        globals()["SRC"] = w11path
        w11all = load()
        globals()["SRC"] = save
    else:
        print(f"  (window-11 2017 journal not found at {w11path})")
    w11 = [t for t in w11all if t["sid"] in ("5", "8", "32")]
    for t in sorted(w11, key=lambda x: int(x["sid"])):
        p = policies(t)
        pol = f"A={p[0]:+.3f} B1={p[1]:+.3f} B2={p[2]:+.3f}" if p else "N/A (no TP1)"
        print(f"  sig {t['sid']:>2} {t['strat']:9} {t['regime']:9} term={t['term']:4} "
              f"touched1={int(t['touched1'])} reached2={int(t['reached2'])} redip1={int(t['redip1'])} "
              f"actual={t['rm']:+.2f} | {pol}")

if __name__ == "__main__":
    main()
