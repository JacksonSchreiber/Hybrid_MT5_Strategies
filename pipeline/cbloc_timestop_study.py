#!/usr/bin/env python3
"""cbloc_timestop_study.py - trader 2026-10-03 hypothesis on the commodity bloc (AUDUSD, NZDUSD, USDCAD in the data): winners win
fast, losers lose fast; holding past a couple of days drifts to entry / the stop. Test:
  T   time stop: if +1.5R (bid high, in the first entry's R) is not reached by the close of bar 6 after the signal (~the first day),
      close EVERYTHING at that close (the staged 75% add is not placed - the stop comes first at that minute).
  T+Lx  T, and when +1.5R IS reached, every stop (longs and shorts) goes to +x R (x = 0 entry, 0.25, 0.5, 0.75, 1.0) at that moment.
Base = the live setup (25%/75% at bar 6, bank at +1R/TP1 by detector -> stop to entry, +50% pyramid at +1.5R, shorts' stop to +0.25R
at +1.5R). Engine = mgmt_variants.run_g (the study engine) with the two hooks added; SCALED (ask-corrected, live spreads).
Population = per_symbol_stats.py's (TrendCont from today's EA). Control = the same rules on every other symbol.
    python3 pipeline/cbloc_timestop_study.py      -> data/study/cbloc_timestop_report.txt
"""
from __future__ import annotations
import os, statistics as st, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                 # noqa: E402
from pipeline import combo3_study as K                   # noqa: E402
from pipeline import combo_build as CB                   # noqa: E402
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
from pipeline.inverse_study import BANKF                  # noqa: E402

BLOC = {"AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk"}
TRIG = 1.5
VARIANTS = [("live", None, None), ("T", 1, None)] + [(f"T+L{x}", 1, x) for x in (0.0, 0.25, 0.5, 0.75, 1.0)] + [(f"L{x}", None, x) for x in (0.0, 0.25, 0.5, 1.0)]
REPORT = os.path.join(A.STUDY, "cbloc_timestop_report.txt")


def run_h(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, up, tstop, raise_to):
    """run_g (live: S on, K on, P on, R on) + tstop (exit all at the bar-6 close unless +1.5R was reached) + raise_to (all stops to
    be + raise_to at the first +1.5R; None = live: shorts to +0.25R)."""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_; trig = True; reached = False
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    for j in range(n):
        if xl[j] <= stop: return val(min(stop, xo[j])), units, "stop"
        if bh[j] >= TRIG: reached = True
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and trig and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F
            if raise_to is None:
                if not up: stop = max(stop, be + K.SR)
            else: stop = max(stop, be + raise_to)
            trig = False
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, "tp2"
        if tstop and k_add is not None and j == k_add and not reached: return val(xc[j]), units, "time"
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
    return val(xc[n - 1]), units, "end"


def do_symbol(sym):
    S = CB.sym_obj(sym); out = []
    for x in [x for x in POP if x["sym"] == sym]:
        u = A.setup(S, x, "SCALED")
        if not u: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], "SCALED", u["scale"])
        e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5); r = {}
        for name, ts, rt in VARIANTS:
            v, units, why = run_h(F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], ts, rt)
            r[name] = v; r[name + ":why"] = why
        out.append((sym, x["t"], x["up"], r, x["strat"]))
    return out


POP = []
def main():
    global POP
    T0 = time.time(); POP = CB.population()
    with Pool(16) as pool: res = [z for rs in pool.map(do_symbol, sorted({x["sym"] for x in POP})) for z in rs]
    # engine check: run_h with no hooks must equal the study engine's live result (per_symbol_stats 'base')
    import pickle
    ps = {(z[0], z[1], z[2]): z[4]["SCALED"]["base"] for z in pickle.load(open(os.path.join(A.STUDY, "per_symbol_trades.pkl"), "rb"))}
    dmax = max(abs(z[3]["live"] - ps[(z[0], z[1], z[4])]) for z in res if (z[0], z[1], z[4]) in ps)
    L = [__doc__.rstrip(), f"\nRUN {time.strftime('%Y-%m-%d %H:%M')}: {len(res)} trades, {(time.time() - T0):.0f}s; "
         f"engine check - 'live' vs the study engine: max |diff| {dmax:.1e} ({'EXACT' if dmax < 1e-12 else 'MISMATCH'})\n"]
    def block(title, zs):
        if len(zs) < 10: return
        b = [z[3]["live"] for z in zs]
        L.append(f"{title}  n={len(zs)}  live {st.mean(b):+.4f}R/trade  total {sum(b):+.1f}  DD {maxdd(b):.1f}")
        for name, ts, rt in VARIANTS[1:]:
            v = [z[3][name] for z in zs]; why = [z[3][name + ':why'] for z in zs]
            tm = sum(1 for w in why if w == "time")
            L.append(f"   {name:8} {st.mean(v):+.4f}R/trade (vs live {st.mean(v) - st.mean(b):+.4f}, t {paired_t(b, v):+.2f})  total {sum(v):+7.1f}  DD {maxdd(v):6.1f}"
                     + (f"  | time-stopped {tm} ({100 * tm / len(zs):.0f}%)" if ts else ""))
    for grp, keep in (("COMMODITY BLOC (AUDUSD, NZDUSD, USDCAD)", lambda z: z[0] in BLOC), ("CONTROL: the other 13 symbols", lambda z: z[0] not in BLOC)):
        L.append(f"\n===== {grp} =====")
        for per, f in (("2012-24", lambda z: z[1][:4] < "2025"), ("2025-26", lambda z: z[1][:4] >= "2025")):
            for side, sf in (("all  ", lambda z: True), ("long ", lambda z: z[2]), ("short", lambda z: not z[2])):
                block(f"{per} {side}", sorted([z for z in res if keep(z) and f(z) and sf(z)], key=lambda z: z[1]))
            L.append("")
        if grp.startswith("COMMODITY"):
            L.append("by symbol, all years:")
            for s in sorted(BLOC): block(f"  {s}", sorted([z for z in res if z[0] == s], key=lambda z: z[1]))
    open(REPORT, "w").write("\n".join(L) + "\n"); print("\n".join(L))


if __name__ == "__main__": main()
