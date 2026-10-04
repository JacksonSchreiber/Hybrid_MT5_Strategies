#!/usr/bin/env python3
"""cbloc_trail.py - trader 2026-10-03: a step trailing stop for the unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD). From +1.5R the stop
sits at (the highest +0.5R step the bid has reached) - TRAIL, ratcheting up only: trail 0.5 = +1.5 -> stop +1, +2 -> +1.5, ...; wider trails
0.75 / 1.0 / 1.5. TP2 kept, or no target (the trail decides the exit). Bases: raw + 24h cut at +1.5R, and the simple combo (trial 25%,
bank 25% at +0.5R with the stop to entry, 24h cut at +1.5R). Reference = the fixed lock (+1R at +1.5R). SCALED, full-position R.
TRAIN 2012-21 / TEST 2022-26 reported (nothing is chosen on TEST).  -> data/study/cbloc_trail_report.txt"""
import os, sys, math, statistics as st, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]; START, STEP = 1.5, 0.5
REPORT = os.path.join(A.STUDY, "cbloc_trail_report.txt")


def run(F, e0, tp2, k6, F0=1.0, bank=False, lock=False, trail=None, use_tp2=True):
    xo, xh, xl, xc, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bh", "bc", "eo", "n"))
    legs = [[F0, e0]]; locked = 0.0; stop = -1.0; banked = False; peak = -1e9; added = F0 >= 1.0; lockdone = False
    tgt = tp2 if (use_tp2 and tp2 is not None) else None
    lv = min(0.5, tgt) if tgt is not None else 0.5
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    for j in range(n):
        if xl[j] <= stop: return val(min(stop, xo[j]))
        if bh[j] > peak: peak = bh[j]
        if bank and not banked and xh[j] >= lv:
            banked = True; locked += 0.25 * sum(s * (lv - e) for s, e in legs); legs = [[s * 0.75, e] for s, e in legs]; stop = max(stop, e0)
        if lock and not lockdone and bh[j] >= 1.5: lockdone = True; stop = max(stop, e0 + 1.0)
        if trail is not None and peak >= START:
            lvl = START + math.floor((peak - START) / STEP + 1e-9) * STEP
            stop = max(stop, e0 + lvl - trail)
        if tgt is not None and xh[j] >= tgt: return val(tgt)
        if k6 >= 0 and j == k6:
            if peak < 1.5: return val(xc[j])
            if not added: legs.append([1 - F0, bc[j] + eo[j]]); added = True
    return val(xc[n - 1])


V = []
for base, bk in (("raw+cut", dict()), ("combo", dict(F0=0.25, bank=True))):
    V.append((f"{base}: fixed lock +1R@+1.5R (reference)", dict(bk, lock=True)))
    for tr in (0.5, 0.75, 1.0, 1.5):
        for tp in (True, False):
            V.append((f"{base}: trail {tr}R{'' if tp else ', NO TP2'}", dict(bk, trail=tr, use_tp2=tp)))


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
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s\n"]
    for q, (nm, _) in enumerate(V):
        if nm.endswith("(reference)"):
            L.append(""); ref = [z[3][q] for z in res]
        v = [z[3][q] for z in res]; sub = lambda g: st.mean([z[3][q] for z in res if g(z)])
        tr = [z[3][q] for z in res if z[1][:4] < "2022"]; te = [z[3][q] for z in res if z[1][:4] >= "2022"]
        L.append(f"{nm:44} {st.mean(v):+.4f}R" + ("" if nm.endswith("(reference)") else f" (vs ref {st.mean(v) - st.mean(ref):+.4f}, t {paired_t(ref, v):+.2f})") +
                 f"  DD {maxdd(v):5.1f}  tot/DD {sum(v) / maxdd(v):+.2f} | TRAIN {st.mean(tr):+.4f} DD {maxdd(tr):4.1f} | TEST {st.mean(te):+.4f} DD {maxdd(te):4.1f}"
                 f" | long {sub(lambda z: z[2]):+.4f} short {sub(lambda z: not z[2]):+.4f} | " + " ".join(f"{s[:6]} {st.mean([z[3][q] for z in res if z[0] == s]):+.3f}" for s in SYMS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
