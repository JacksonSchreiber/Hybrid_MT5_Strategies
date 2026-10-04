#!/usr/bin/env python3
"""cbloc_raw_combo.py - trader 2026-10-03: unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD), raw signal + the 24h cut at +1.5R +
bank 25% at the first +0.5R (stop to entry at that moment, or left alone) + every stop to +1R the moment +1.5R is first hit (inside the
first 24h, i.e. the cut's survivors). Per minute: stop, bank, the +1.5R stop move, TP2, the 24h cut. SCALED.
    -> data/study/cbloc_raw_combo_report.txt"""
import os, sys, statistics as st, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
SYMS = ["AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"]
REPORT = os.path.join(A.STUDY, "cbloc_raw_combo_report.txt")
V = [("Raw signal", dict()),
     ("Raw + 24h cut 1.5R", dict(cut=1.5)),
     ("cut + stop +1R at +1.5R", dict(cut=1.5, mv=(1.5, 1.0))),
     ("cut + bank 25% @0.5R, stop to entry", dict(cut=1.5, bank=(0.25, 0.5), be=True)),
     ("COMBO: cut + bank 25% @0.5R (stop to entry) + stop +1R at +1.5R", dict(cut=1.5, bank=(0.25, 0.5), be=True, mv=(1.5, 1.0))),
     ("COMBO: cut + bank 25% @0.5R (stop stays) + stop +1R at +1.5R", dict(cut=1.5, bank=(0.25, 0.5), be=False, mv=(1.5, 1.0)))]


def run(F, e0, tp2, k6, cut=None, bank=None, be=False, mv=None):
    xo, xh, xl, xc, bh, n = F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], F["n"]
    size = 1.0; locked = 0.0; stop = -1.0; banked = False; moved = False; peak = -1e9
    lv = None if bank is None else (min(bank[1], tp2) if tp2 is not None else bank[1])
    for j in range(n):
        if xl[j] <= stop: return locked + size * (min(stop, xo[j]) - e0)
        if bh[j] > peak: peak = bh[j]
        if bank is not None and not banked and xh[j] >= lv:
            banked = True; locked += bank[0] * (lv - e0); size *= 1 - bank[0]
            if be: stop = max(stop, e0)
        if mv is not None and not moved and k6 >= 0 and j <= k6 and bh[j] >= mv[0]: moved = True; stop = max(stop, e0 + mv[1])
        if tp2 is not None and xh[j] >= tp2: return locked + size * (tp2 - e0)
        if cut is not None and k6 >= 0 and j == k6 and peak < cut: return locked + size * (xc[j] - e0)
    return locked + size * (xc[n - 1] - e0)


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
    raw = [z[3][0] for z in res]
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s\n"]
    for q, (nm, _) in enumerate(V):
        v = [z[3][q] for z in res]; sub = lambda g: st.mean([z[3][q] for z in res if g(z)])
        L.append(f"{nm:66} {st.mean(v):+.4f}R/trade  t vs raw {paired_t(raw, v) if q else 0:+.2f}  DD {maxdd(v):6.1f}  total {sum(v):+7.1f}  win {100 * sum(x > 0 for x in v) / len(v):.0f}%"
                 f" | long {sub(lambda z: z[2]):+.4f} short {sub(lambda z: not z[2]):+.4f} | 2012-24 {sub(lambda z: z[1][:4] < '2025'):+.4f} 2025-26 {sub(lambda z: z[1][:4] >= '2025'):+.4f}"
                 f" | " + " ".join(f"{s[:6]} {st.mean([z[3][q] for z in res if z[0] == s]):+.3f}" for s in SYMS))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
