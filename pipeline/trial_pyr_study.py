#!/usr/bin/env python3
"""trial_pyr_study.py - trader 2026-10-01: when +1.5R trades DURING THE TRIAL (the staged first 25% is on, the bar-6 add has not
happened yet), what if the live stack did something else at that moment?
  base    - live: pyramid 50% of full size at +1.5R, the 75% add still comes at the bar-6 close
  P66/P75/P100 - the pyramid is 66% / 75% / 100% of full size instead (only when it fires inside the trial)
  SELL    - the original 25% (what is left of it after the bank) is closed at +1.5R; pyramid 50% and the bar-6 add as live
  NOPROM  - the bar-6 add is cancelled (never promote); pyramid 50% as live
Live stack otherwise (staged 25%/bar-6, bank per detector at +1R or TP1, stop to entry, pyramid at +1.5R, SHORTS stop raise to
+0.25R at +1.5R - live since 2026-10-01). Engine: pipeline/ask_side_study (M1, BID = bid-only + DRAG, SCALED = ask-corrected).
All four detectors, 7 symbols; 2012-24 and 2025-26; long/short; full-position R; worst DD in signal-time order.
"""
from __future__ import annotations
import os, statistics as st, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402

MODES = ("BID", "SCALED"); SR = 0.25
VARS = [("base", 0.5, False, False), ("P66", 0.66, False, False), ("P75", 0.75, False, False), ("P100", 1.0, False, False),
        ("SELL", 0.5, True, False), ("NOPROM", 0.5, False, True)]
REPORT = os.path.join(A.STUDY, "trial_pyr_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, side, pf_trial, sell, noprom):
    """-> (raw R, units, fired_in_trial)"""
    xo, xh, xl, xc, bo, bh, bc, eo, spR, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "spR", "n"))
    legs = [[F0_, e0, "T"]]; locked = 0.0; stop = -1.0; banked = False; units = F0_
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n; pyr_pend = True; trial_hit = False
    val = lambda r: locked + sum(s * (r - e) for s, e, _ in legs)
    j = 0
    while j < n:
        j = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, trial_hit
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e, _ in legs)
            legs = [[s * (1 - bankf), e, t] for s, e, t in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            in_trial = add_pend; trial_hit = in_trial; pf = pf_trial if in_trial else 0.5
            legs.append([pf, max(A.PYR_R, bo[j]) + eo[j], "P"]); units += pf; pyr_pend = False
            if in_trial and sell:                                           # close what is left of the trial tranche at +1.5R
                px = max(A.PYR_R, bo[j]) - (spR[j] if (side and not up) else 0.0)
                locked += sum(s * (px - e) for s, e, t in legs if t == "T"); legs = [g for g in legs if g[2] != "T"]
            if in_trial and noprom: add_pend = False
            if not up: stop = max(stop, be + SR)                            # live shorts stop raise
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, trial_hit
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j], "A"]); units += 1 - F0_; add_pend = False
        j += 1
    return val(xc[n - 1]), units, trial_hit


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    out = {}
    for name, pf, sell, noprom in VARS:
        r, units, hit = run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], m["side"], pf, sell, noprom)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0); out["hit"] = hit
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
    lines = [__doc__.rstrip(), f"\nRUN: {len(res)} trades, {(time.time() - T0) / 60:.1f} min"]
    for md in MODES:
        for per in ("dev", "hold"):
            zs = [z for z in res if (z[1][:4] < "2025") == (per == "dev")]
            hit = [z for z in zs if z[4][md]["hit"]]
            b = [z[4][md]["base"] for z in zs]
            lines.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}, +1.5R inside the trial on {len(hit)} trades "
                         f"({sum(1 for z in hit if z[3])} long / {sum(1 for z in hit if not z[3])} short). Base {st.mean(b):+.4f}R, worst DD {maxdd(b):.1f}R")
            lines.append(f"  {'variant':8} {'R/trade':>8} {'vs base':>8} {'t':>6} {'DD':>6} | {'on the hit trades':>18} {'longs':>8} {'shorts':>8}")
            for name, *_ in VARS:
                v = [z[4][md][name] for z in zs]; d = [p - q for p, q in zip(v, b)]
                dh = [z[4][md][name] - z[4][md]["base"] for z in hit]
                dl = [z[4][md][name] - z[4][md]["base"] for z in hit if z[3]]; ds = [z[4][md][name] - z[4][md]["base"] for z in hit if not z[3]]
                mm = lambda a: st.mean(a) if a else float("nan")
                lines.append(f"  {name:8} {st.mean(v):+8.4f} {st.mean(d):+8.4f} {paired_t(b, v) if name != 'base' else 0:+6.2f} {maxdd(v):6.1f} | "
                             f"{mm(dh):+18.4f} {mm(dl):+8.4f} {mm(ds):+8.4f}")
    open(REPORT, "w").write("\n".join(lines) + "\n"); print("\n".join(lines))


if __name__ == "__main__": main()
