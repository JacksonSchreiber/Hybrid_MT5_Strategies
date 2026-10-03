#!/usr/bin/env python3
"""combo3_study.py - trader 2026-10-02: the three coach-queue rules alone, in pairs and together.
  A  ADD GATE    at the bar-6 close a trial average < 0 holds the staged 75% add until the bid reaches +1R (all trades).
  B  SHORT CUT   at the bar-12 close (~48h) a SHORT whose average since entry is not above +0.5R is closed (100%) at market.
  C  RUNNER ADD  at the bar-12 close a trade whose average since entry is above +1R gets +50% of the full size at market (shares
                 the stop, runs to TP2). Main = LONGS only; C* = both sides.
Averages: mean of every M1 bid close since entry, first tranche's 1R. Live stack otherwise (staged 25%/bar-6, bank per detector at
+1R/TP1 -> stop to entry, pyramid 50% at +1.5R, shorts stop to +0.25R at +1.5R). Engine pipeline/ask_side_study (M1; SCALED =
ask-corrected, BID = bid-only + DRAG). All four detectors, 7 symbols, pinned record; 2012-24 / 2025-26; full-position R; worst DD.
"""
from __future__ import annotations
import itertools, os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402

MODES = ("SCALED", "BID"); SR = 0.25; GATE_LVL = 1.0; CUT_BAR = 0.5; ADD_BAR = 1.0; ADD_F = 0.5; CHK = 12
COMBOS = ["base", "A", "B", "C", "C*", "A+B", "A+C", "B+C", "A+B+C", "A+B+C*"]
REPORT = os.path.join(A.STUDY, "combo3_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, gate, kc, ka2):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n; kd = None
    if gate and add_pend:
        add_pend = False
        msk = bh[k_add + 1:] >= GATE_LVL
        kd = (k_add + 1 + int(msk.argmax())) if msk.any() else None
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        for ev in (kd, kc, ka2):
            if ev is not None and j <= ev < j2: j2 = ev
        j = j2
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if kd is not None and j == kd:
            legs.append([1 - F0_, max(GATE_LVL, bo[j]) + eo[j]]); units += 1 - F0_; kd = None
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if kc is not None and j == kc: return val(xc[j]), units
        if ka2 is not None and j == ka2:
            legs.append([ADD_F, bc[j] + eo[j]]); units += ADD_F; ka2 = None
        j += 1
    return val(xc[n - 1]), units


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    cum = np.cumsum(F["bc"]); n = F["n"]; ka = u["k_add"]
    gate = ka is not None and 0 <= ka < n and cum[ka] / (ka + 1) < 0
    ib = u["i0"] + CHK
    k = (int(np.searchsorted(S.t, S.h4t[ib])) - u["k0"] - 1) if ib < len(S.h4) else None
    avg2 = cum[k] / (k + 1) if (k is not None and 0 <= k < n) else None
    cut = k if (avg2 is not None and not u["up"] and avg2 <= CUT_BAR) else None
    add_l = k if (avg2 is not None and u["up"] and avg2 > ADD_BAR) else None
    add_b = k if (avg2 is not None and avg2 > ADD_BAR) else None
    args = (F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, ka, u["up"])
    spec = {"base": (False, None, None), "A": (gate, None, None), "B": (False, cut, None), "C": (False, None, add_l), "C*": (False, None, add_b),
            "A+B": (gate, cut, None), "A+C": (gate, None, add_l), "B+C": (False, cut, add_l), "A+B+C": (gate, cut, add_l), "A+B+C*": (gate, cut, add_b)}
    out = {}
    for name, (g, c, a) in spec.items():
        r, units = run_v(*args, g, c, a)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0)
    return out


ALL = []
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), A.ref_prices()); res = []
    for x in [x for x in ALL if x["sym"] == sym]:
        r = {md: trade(S, x, md) for md in MODES}
        if any(v is None for v in r.values()): continue
        res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def main():
    global ALL
    T0 = time.time()
    ALL = sorted([dict(x, strat="TrendCont") for x in load_rows()] + [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"],
                 key=lambda x: x["t"])
    with Pool(2) as pool: res = sorted([z for rs in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for z in rs], key=lambda z: z[1])
    L = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for md in MODES:
        for per in ("dev", "hold"):
            zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
            b = [z[4][md]["base"] for z in zs]; mb = st.mean(b)
            L.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}")
            L.append(f"  {'rules':8} {'R/trade':>8} {'vs base':>8} {'t':>6} {'worst DD':>8} {'total R':>8} {'R/DD':>6} | {'longs':>8} {'shorts':>8}")
            for name in COMBOS:
                v = [z[4][md][name] for z in zs]
                lo = st.mean(z[4][md][name] for z in zs if z[3]); sh = st.mean(z[4][md][name] for z in zs if not z[3])
                L.append(f"  {name:8} {st.mean(v):+8.4f} {st.mean(v) - mb:+8.4f} {(paired_t(b, v) if name != 'base' else 0):+6.2f} {maxdd(v):8.1f} {sum(v):8.1f} {sum(v) / maxdd(v):6.1f} | {lo:+8.4f} {sh:+8.4f}")
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
