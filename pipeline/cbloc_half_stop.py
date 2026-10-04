#!/usr/bin/env python3
"""cbloc_half_stop.py - trader 2026-10-03: unlocked commodity bloc (AUDUSD, NZDUSD, USDCAD) - what if the opening stop is at half (or 3/4)
of the signal's distance? Risk-matched: the same money at risk means a 2x (1.33x) position, so every result is reported in the NEW R
(old-R result / the stop fraction; spreads scale with it). Everything else stays at the same PRICE: TP2, the 24h cut level (old +1.5R),
the bank (old +0.5R, 25%, stop to entry), the stop move (old +1R at old +1.5R). Raw-signal family, SCALED.
Also: how far do eventual winners dip first (the trader's guess: not far)?  -> data/study/cbloc_half_stop_report.txt"""
import os, sys, statistics as st, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline import cbloc_raw_combo as RC               # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
SYMS = RC.SYMS; FR = [1.0, 0.75, 0.5]
SETUPS = [("Raw signal", dict()), ("Raw + 24h cut", dict(cut=1.5)), ("cut + stop +1R at +1.5R", dict(cut=1.5, mv=(1.5, 1.0))),
          ("COMBO (cut + bank 25%@0.5 to entry + stop +1R at +1.5R)", dict(cut=1.5, bank=(0.25, 0.5), be=True, mv=(1.5, 1.0)))]
REPORT = os.path.join(A.STUDY, "cbloc_half_stop_report.txt")


def run(F, e0, tp2, k6, fr, cut=None, bank=None, be=False, mv=None):
    """RC.run with the opening stop at -fr (old R); returns the result in NEW R (/fr)."""
    xo, xh, xl, xc, bh, n = F["xo"], F["xh"], F["xl"], F["xc"], F["bh"], F["n"]
    size = 1.0; locked = 0.0; stop = -fr; banked = False; moved = False; peak = -1e9
    lv = None if bank is None else (min(bank[1], tp2) if tp2 is not None else bank[1])
    def out(v): return v / fr
    for j in range(n):
        if xl[j] <= stop: return out(locked + size * (min(stop, xo[j]) - e0))
        if bh[j] > peak: peak = bh[j]
        if bank is not None and not banked and xh[j] >= lv:
            banked = True; locked += bank[0] * (lv - e0); size *= 1 - bank[0]
            if be: stop = max(stop, e0)
        if mv is not None and not moved and k6 >= 0 and j <= k6 and bh[j] >= mv[0]: moved = True; stop = max(stop, e0 + mv[1])
        if tp2 is not None and xh[j] >= tp2: return out(locked + size * (tp2 - e0))
        if cut is not None and k6 >= 0 and j == k6 and peak < cut: return out(locked + size * (xc[j] - e0))
    return out(locked + size * (xc[n - 1] - e0))


def mae_before_win(F, tp2):
    """for a raw trade: (won?, deepest bid low in old R before it exited)."""
    xl, xh, n = F["xl"], F["xh"], F["n"]; lo = 0.0
    for j in range(n):
        if xl[j] <= -1.0: return False, -1.0
        lo = min(lo, xl[j])
        if tp2 is not None and xh[j] >= tp2: return True, lo
    return None, lo


def do(sym):
    S_ = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        p = CB.prep(S_, x)
        if p is None: continue
        r = {(nm, fr): run(p["F"], p["e0"], p["tp2"], p["kbar"][6], fr, **kw) for nm, kw in SETUPS for fr in FR}
        r["mae"] = mae_before_win(p["F"], p["tp2"])
        out.append((sym, x["t"], x["up"], r))
    return out


POP = []
def main():
    global POP
    POP = [x for x in CB.population() if x["sym"] in SYMS]; t0 = time.time()
    with Pool(3) as pool: res = sorted([z for r in pool.map(do, SYMS) for z in r], key=lambda z: z[1])
    L = [__doc__, f"RUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {time.time() - t0:.0f}s\n"]
    wins = [z[3]["mae"][1] for z in res if z[3]["mae"][0] is True]
    L.append(f"Raw winners (reached TP2 before the stop): {len(wins)}. How far they dipped first (old R):")
    for th in (-0.25, -0.5, -0.75):
        L.append(f"   dipped below {th}R before winning: {sum(1 for w in wins if w < th)} ({100 * sum(1 for w in wins if w < th) / len(wins):.0f}%)")
    L.append(f"   median deepest dip of a winner: {st.median(wins):+.2f}R\n")
    for nm, _ in SETUPS:
        L.append(f"== {nm} ==  (results in each version's own R, risk-matched)")
        base = [z[3][(nm, 1.0)] for z in res]
        for fr in FR:
            v = [z[3][(nm, fr)] for z in res]; sub = lambda g: st.mean([z[3][(nm, fr)] for z in res if g(z)])
            L.append(f"   opening stop {'full (now)' if fr == 1 else f'{fr:.2f} x':>10}: {st.mean(v):+.4f}R/trade" + (f" (vs full stop {st.mean(v) - st.mean(base):+.4f}, t {paired_t(base, v):+.2f})" if fr < 1 else " " * 33) +
                     f"  DD {maxdd(v):6.1f}  total {sum(v):+7.1f}  win {100 * sum(q > 0 for q in v) / len(v):.0f}% | long {sub(lambda z: z[2]):+.4f} short {sub(lambda z: not z[2]):+.4f}"
                     f" | 2012-24 {sub(lambda z: z[1][:4] < '2025'):+.4f} 2025-26 {sub(lambda z: z[1][:4] >= '2025'):+.4f} | " + " ".join(f"{s[:6]} {st.mean([z[3][(nm, fr)] for z in res if z[0] == s]):+.3f}" for s in SYMS))
        L.append("")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
