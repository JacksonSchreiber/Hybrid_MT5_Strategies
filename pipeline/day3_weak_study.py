#!/usr/bin/env python3
"""day3_weak_study.py - trader 2026-10-02: what to do with trades still weak at day 3. At the close of H4 bar 18 (~3 trading days),
if the trade is still open and its average R since entry (mean of every M1 bid close, first tranche's 1R) is BELOW +0.5R:
  SELL X%     close X% of every open ticket at market (a short at the ask), X = 100 / 75 / 50 / 25; the rest runs on (live stack).
  STOP -> L   move the stop of every ticket up to L R (from the first fill) = -0.5 / -0.25 / 0 (entry) / +0.25R, tighten-only; if
              the bar already closed at/beyond L the position is closed at market there.
Base = live (staged 25%/bar-6, bank per detector at +1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at
+1.5R). Engine pipeline/ask_side_study, SCALED (ask-corrected). All four detectors, 7 symbols, pinned record; 2012-24 / 2025-26;
all / longs only / shorts only (the rule on one side, the other at base); full-position R; worst DD.
"""
from __future__ import annotations
import os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402

MODE = "SCALED"; SR = 0.25
CHK = 18; BAR = 0.5             # checkpoint in H4 bars (18 = day 3, 30 = day 5) and the weak bar; argv overrides in main() only
                                # (other studies import run_v from here - module import must not read their argv)
VARS = [(f"sell {int(f * 100)}%", "sell", f) for f in (1.0, 0.75, 0.5, 0.25)] + [(f"stop -> {l:+g}R", "stop", l) for l in (-0.5, -0.25, 0.0, 0.25)]
REPORT = None


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, kc, act, p):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if kc is not None and j <= kc < j2: j2 = kc
        j = j2
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if kc is not None and j == kc:
            kc = None
            if act == "sell":
                if p >= 1.0: return val(xc[j]), units
                locked += p * sum(s * (xc[j] - e) for s, e in legs); legs = [[s * (1 - p), e] for s, e in legs]
            else:
                lvl = be + p
                if lvl > stop:
                    if xc[j] <= lvl: return val(xc[j]), units          # already at/through the new stop: out at market
                    stop = lvl
        j += 1
    return val(xc[n - 1]), units


def trade(S, x):
    u = A.setup(S, x, MODE)
    if not u: return None
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5)
    args = (F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"])
    out = {"base": run_v(*args, None, None, None)[0]}
    ib = u["i0"] + CHK
    k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    weak = k is not None and 0 <= k < F["n"] and float(F["bc"][:k + 1].mean()) < BAR
    for name, act, p in VARS:
        out[name] = run_v(*args, k, act, p)[0] if weak else out["base"]
    out["weak"] = weak and out["sell 100%"] != out["base"]
    return out


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        r = trade(S, x)
        if r: res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def main():
    global ALL, CHK, BAR, REPORT
    if len(sys.argv) > 1: CHK = int(sys.argv[1])
    if len(sys.argv) > 2: BAR = float(sys.argv[2])
    REPORT = os.path.join(A.STUDY, "day3_weak_report.txt" if (CHK, BAR) == (18, 0.5) else f"weak_bar{CHK}_{BAR:g}_report.txt")
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = sorted([z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs], key=lambda z: z[1])
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for per in ("dev", "hold"):
        zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
        b = [z[4]["base"] for z in zs]; mb = st.mean(b)
        wz = [z for z in zs if z[4]["weak"]]
        L.append(f"\n{'=' * 100}\n{'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}  base {mb:+.4f}R  worst DD {maxdd(b):.1f}R   "
                 f"still open at bar {CHK} (day {CHK // 6}) with an average < {BAR:+g}R: {len(wz)} ({sum(1 for z in wz if z[3])} long / {sum(1 for z in wz if not z[3])} short)")
        L.append(f"  {'rule':14} | {'ALL: vs base [t] / DD':>26} | {'LONGS only':>26} | {'SHORTS only':>26} | {'per weak trade L / S':>22}")
        for name, *_ in VARS:
            cells = []
            for side in (None, True, False):
                v = [z[4][name] if (side is None or z[3] == side) else z[4]["base"] for z in zs]
                cells.append(f"{st.mean(v) - mb:+.4f}[{paired_t(b, v):+.1f}]/{maxdd(v):5.1f}")
            dl = [z[4][name] - z[4]["base"] for z in wz if z[3]]; ds = [z[4][name] - z[4]["base"] for z in wz if not z[3]]
            mm = lambda a: st.mean(a) if a else float("nan")
            L.append(f"  {name:14} | {cells[0]:>26} | {cells[1]:>26} | {cells[2]:>26} | {mm(dl):+.3f} / {mm(ds):+.3f}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
