#!/usr/bin/env python3
"""trial_renew_study.py - trader 2026-10-01: RENEW THE TRIAL for underwater trades. At the bar-6 close (the live staged add), if
the price (bid close) is below THR R, the add is skipped and the position stays at 25% for another 6 H4 bars:
  R1-THR   one renewal - at the bar-12 close the add goes in unconditionally (if the trade is still open)
  RR-THR   repeated - at every later 6-bar close (12, 18, 24, 30, 36) the add goes in only once the price is >= THR; never after bar 36
THR 0 and -0.5. Everything else is the live stack: bank per detector at +1R/TP1 -> stop to entry (the add then enters with that
stop), pyramid 50% of full size at +1.5R, shorts stop to +0.25R at +1.5R. Engine pipeline/ask_side_study (M1; BID = bid-only +
DRAG, SCALED = ask-corrected). All four detectors, 7 symbols; 2012-24 / 2025-26; long/short; full-position R; worst DD.
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

MODES = ("BID", "SCALED"); SR = 0.25
VARS = [("base", None, 0), ("R1<0", 0.0, 1), ("R1<-0.5", -0.5, 1), ("RR<0", 0.0, 5), ("RR<-0.5", -0.5, 5)]
REPORT = os.path.join(A.STUDY, "trial_renew_report.txt")


def run_v(F, e0, be, bank_R, tp2_R, bankf, F0_, kadds, up, thr, renew):
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; pyr_pend = True
    ka = [k for k in kadds if k is not None and k < n]; ci = 0; renewed = 0; skipped = False
    add_pend = bool(ka)
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        k_add = ka[ci] if add_pend else None
        j = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add)
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, skipped
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if not up: stop = max(stop, be + SR)
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, skipped
        if add_pend and j == k_add:
            last = ci + 1 >= len(ka)
            under = thr is not None and bc[j] < thr
            if under and renewed < renew and not last:
                renewed += 1; ci += 1; skipped = True                        # renew the trial: wait another 6 bars
            elif under and renew > 1 and renewed >= renew:
                add_pend = False                                             # repeated mode ran out of renewals: never add
            elif under and renew > 1 and last:
                add_pend = False
            else:
                legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        j += 1
    return val(xc[n - 1]), units, skipped


def trade(S, x, mode):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    kadds = []
    for b in (6, 12, 18, 24, 30, 36):
        ib = u["i0"] + b
        kadds.append((int(np.searchsorted(S.t, S.h4t[ib])) - 1 - u["k0"]) if ib < len(S.h4) else None)
    out = {}
    for name, thr, renew in VARS:
        ks = kadds[:1] if renew == 0 else (kadds[:2] if renew == 1 else kadds)
        r, units, sk = run_v(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, ks, u["up"], thr, renew)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0); out["sk" + name] = sk
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
            b = [z[4][md]["base"] for z in zs]
            lines.append(f"\n[{md}] {'2012-24' if per == 'dev' else '2025-26'}: n={len(zs)}. Base {st.mean(b):+.4f}R, worst DD {maxdd(b):.1f}R")
            lines.append(f"  {'rule':9} {'renewed':>7} {'R/trade':>8} {'vs base':>8} {'t':>6} {'DD':>6} | {'on renewed':>10} {'longs':>8} {'shorts':>8} {'helped/hurt':>11}")
            for name, *_ in VARS[1:]:
                v = [z[4][md][name] for z in zs]; d = [p - q for p, q in zip(v, b)]
                cz = [z for z in zs if z[4][md]["sk" + name]]
                mm = lambda a: st.mean(a) if a else float("nan")
                dc = [z[4][md][name] - z[4][md]["base"] for z in cz]
                dl = [z[4][md][name] - z[4][md]["base"] for z in cz if z[3]]; ds = [z[4][md][name] - z[4][md]["base"] for z in cz if not z[3]]
                lines.append(f"  {name:9} {len(cz):7d} {st.mean(v):+8.4f} {st.mean(d):+8.4f} {paired_t(b, v):+6.2f} {maxdd(v):6.1f} | "
                             f"{mm(dc):+10.4f} {mm(dl):+8.4f} {mm(ds):+8.4f} {sum(1 for e in dc if e > 1e-9):5d}/{sum(1 for e in dc if e < -1e-9):<5d}")
    open(REPORT, "w").write("\n".join(lines) + "\n"); print("\n".join(lines))


if __name__ == "__main__": main()
