#!/usr/bin/env python3
"""cbloc_combo_trial.py - trader 2026-10-03: unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD). The combo (raw signal + 24h cut at +1.5R +
bank 25% at the first +0.5R with the stop to entry + every stop to +1R the moment +1.5R is hit) WITH the trial period: open F0 (25% or
50%) at the signal; at the 24h check (bar-6 close) a trade that is cut closes and never adds, a survivor adds the rest (1 - F0) at that
close, joining at the current stop (entry or +1R). The bank (on the first tranche) is the only partial. Results in full-position R.
Per minute: stop, bank, the +1.5R stop move, TP2, the 24h check (cut, else add). SCALED. -> data/study/cbloc_combo_trial_report.txt"""
import os, sys, statistics as st, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_combo_trial_report.txt")
V = [("Raw signal", dict(F0=1.0, cut=None, bank=False, mv=False)),
     ("Combo, full size at the signal (reference)", dict(F0=1.0)),
     ("Combo + trial 25% now / 75% at the 24h check", dict(F0=0.25)),
     ("Combo + trial 50% now / 50% at the 24h check", dict(F0=0.5)),
     ("Cut + stop +1R at +1.5R (no bank) + trial 25/75", dict(F0=0.25, bank=False))]


def run(F, e0, tp2, k6, F0=1.0, cut=1.5, bank=True, mv=True):
    xo, xh, xl, xc, bh, bc, eo, n = F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], F["bc"], F["eo"], F["n"]
    legs = [[F0, e0]]; locked = 0.0; stop = -1.0; banked = False; moved = False; peak = -1e9; added = F0 >= 1.0
    lv = min(0.5, tp2) if tp2 is not None else 0.5
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    for j in range(n):
        if xl[j] <= stop: return val(min(stop, xo[j]))
        if bh[j] > peak: peak = bh[j]
        if bank and not banked and xh[j] >= lv:
            banked = True; locked += 0.25 * sum(s * (lv - e) for s, e in legs); legs = [[s * 0.75, e] for s, e in legs]; stop = max(stop, e0)
        if mv and not moved and k6 >= 0 and j <= k6 and bh[j] >= 1.5: moved = True; stop = max(stop, e0 + 1.0)
        if tp2 is not None and xh[j] >= tp2: return val(tp2)
        if k6 >= 0 and j == k6:
            if cut is not None and peak < cut: return val(xc[j])
            if not added: legs.append([1 - F0, bc[j] + eo[j]]); added = True
    return val(xc[n - 1])


def do(sym):
    S_ = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S_, x)
        if p is None: continue
        out.append((sym, x["t"], x["up"], [run(p["F"], p["e0"], p["tp2"], p["kbar"][6], **kw) for _, kw in V]))
    return out


POP = []
def main():
    global POP
    POP = [x for x in CB.population() if x["sym"] in SYMS]; t0 = time.time()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z[1])
    ref = [z[3][1] for z in res]
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s\n"]
    for q, (nm, _) in enumerate(V):
        v = [z[3][q] for z in res]; sub = lambda g: st.mean([z[3][q] for z in res if g(z)])
        L.append(f"{nm:50} {st.mean(v):+.4f}R/trade" + (f" (vs combo {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})" if q > 1 else " " * 29) +
                 f"  DD {maxdd(v):6.1f}  total {sum(v):+7.1f}  total/DD {sum(v) / maxdd(v):+.2f}  win {100 * sum(x > 0 for x in v) / len(v):.0f}%"
                 f" | long {sub(lambda z: z[2]):+.4f} short {sub(lambda z: not z[2]):+.4f} | 2012-24 {sub(lambda z: z[1][:4] < '2025'):+.4f} 2025-26 {sub(lambda z: z[1][:4] >= '2025'):+.4f}"
                 f" | " + " ".join(f"{s[:6]} {st.mean([z[3][q] for z in res if z[0] == s]):+.3f}" for s in SYMS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
