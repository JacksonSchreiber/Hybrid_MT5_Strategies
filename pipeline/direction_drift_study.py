#!/usr/bin/env python3
"""direction_drift_study.py - coach item 24 (2026-10-01): direction and drift.

Coach's claim: the long/short gap of the live stack is an ASSET-CLASS effect (indices and commodities drift up, so shorting
them loses), not a general short-side problem. His M5 numbers (TrendCont blind record, live stack, bid-only):
  2012-24 FX L +0.111 / S +0.091; index L +0.256 / S -0.172 (n=138); gold L +0.257 / S +0.013; oil L +0.131 / S +0.012;
  USDJPY S -0.231; 2025-26 index S -0.197.

What this script does
  * LIVE STACK on M1, every fill on its true side (pipeline/ask_side_study.py engine, imported, not copied): staged 25% at
    the signal + 75% at the bar-6 close; bank at +1R or TP1 if nearer on every leg open then (live.json fractions:
    TrendCont 0.25, DeepFib 0.25, SweepMSS 0.50, EMArevQ 0.50); whole-position stop to the first tranche's entry; runner to
    TP2; +1.5R pyramid of 50% of full size after the bank, stop at the original entry, target TP2. No early promotion
    (not live).
  * ALL FOUR detectors: TrendCont = the blind record (exit_mgmt_study.load_rows), SweepMSS / DeepFib / EMArevQ = the long
    blind AA runs (exit_s025_study.aa_rows). BTCUSD skipped (no M1 on disk; not on the live lineup after the 2026-09-16
    feasibility FAIL). US30 skipped (no blind record - see the US30 section).
  * By asset class (FX: EURUSD GBPUSD USDJPY; indices: US100 US500; gold: XAUUSD; oil: USOIL), by symbol, LONG vs SHORT,
    development 2012-24 and holdout 2025+; n, mean costed R/trade, t, worst DD (R, time order), long-minus-short with Welch t.
  * Drift context: buy-and-hold drift of each symbol over each period (H4 .dk bars): total %, annualised %, mean H4
    close-to-close move in ATR(14) units per bar with its t; and a per-trade drift-in-R adjustment (below).
  * The Inverse's long/short split (its side = opposite of the parent), by class, live = with its +1.5R pyramid.

MODES (ask_side_study.MODES): PRIMARY = SCALED (ask = bid + the 2026 hourly median spread as a fraction of the Sept-2026
  price, re-priced at each trade's entry; no DRAG - the spread is in the prices). ABS (2026 spread in price units applied
  to all years) overstates the cost in R the further back a trade is (US100's 1.8 points is ~10x the 2012 cost in R), so it
  is shown only alongside. BID = the old bid-only replay with the per-symbol DRAG per unit traded.
  CAVEAT: data/study/spread_profile.csv is a 2026 MT5 M1 bar-minimum median - it UNDERSTATES spreads (EURUSD reads ~0 in
  23 of 24 hours; every symbol sits below the spot sample in spreads.json). The ask correction is a lower bound.
DRIFT-IN-R per trade = side x entry x mu x minutes held / R, mu = the symbol's log drift per calendar minute over that
  period's H4 coverage. Approximations: full size from the signal to the exit (ignores the 25% stage and the pyramid
  units), in-sample realised period drift. "Drift-adjusted" = costed R minus drift-in-R: what is left once the trade is
  charged / credited the underlying's average move while it was open.
    python3 pipeline/direction_drift_study.py
"""
from __future__ import annotations
import math, os, statistics as st, sys, time
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.ask_side_study import (Sym, setup, frame, run, regular, inverse, spread_profile, ref_prices, MODES,  # noqa: E402
                                     W0, W1, F0, HOLD, PYR_R, PYR_F, EARLY, SCEN, nxt)
from pipeline.exit_mgmt_study import load_rows, maxdd                                                    # noqa: E402
from pipeline.exit_s025_study import aa_rows                                                             # noqa: E402
from pipeline.inverse_study import BANKF, tmin                                                           # noqa: E402
from pipeline.trendcont_step13_study import load                                                         # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STUDY = os.path.join(ROOT, "data", "study")
REPORT = os.path.join(STUDY, "direction_drift_report.txt")
MD = ("BID", "ABS", "SCALED"); PRI = "SCALED"
CLASS = {"EURUSD": "FX", "GBPUSD": "FX", "USDJPY": "FX", "US100": "index", "US500": "index", "XAUUSD": "gold", "USOIL": "oil"}
CLS = ("FX", "index", "gold", "oil")
SYMS = ("EURUSD", "GBPUSD", "USDJPY", "US100", "US500", "XAUUSD", "USOIL")
STRATS = ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")
COACH = {("dev", "FX"): (0.111, 0.091), ("dev", "index"): (0.256, -0.172), ("dev", "gold"): (0.257, 0.013),
         ("dev", "oil"): (0.131, 0.012), ("hold", "index"): (None, -0.197)}
COACH_SYM = {("dev", "USDJPY"): (None, -0.231)}
JRN = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"


# ----------------------------------------------------------------------------------------------------------- engine
def run_x(F, e0, be, bank_R, tp2_R, bankf, F0_=1.0, k_add=None, pyr=False):
    """ask_side_study.run (no early promotion) that also returns the exit bar index (n-1 at end of data)."""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    pyr_pend = pyr; padd = False
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        j = nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
        if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, padd, j
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
            if pyr_pend and bh[j] >= PYR_R:
                legs.append([PYR_F, max(PYR_R, bo[j]) + eo[j]]); units += PYR_F; pyr_pend = False; padd = True
            if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, padd, j
            if add_pend and j == k_add:
                legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
            j += 1; continue
        if banked and pyr_pend and bh[j] >= PYR_R:
            legs.append([PYR_F, max(PYR_R, bo[j]) + eo[j]]); units += PYR_F; pyr_pend = False; padd = True
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, padd, j
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        j += 1
    return val(xc[n - 1]), units, padd, n - 1


MISMATCH = [0]


def live(S, x, mode):
    """live stack -> (costed R, raw R, units, minutes held, entry, R) or None (no M1 coverage)."""
    u = setup(S, x, mode)
    if not u: return None
    m = MODES[mode]
    F = frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0
    bf = BANKF[x["strat"]]
    r, units, _, j = run_x(F, e0, be, u["bank_R"], u["tp2_R"], bf, F0, u["k_add"], True)
    r0, u0, _ = run(F, e0, be, u["bank_R"], u["tp2_R"], bf, F0, u["k_add"], True, None)
    if abs(r - r0) > 1e-12 or abs(units - u0) > 1e-12: MISMATCH[0] += 1
    held = int(S.t[min(u["k0"] + j, len(S.t) - 1)] - S.t[u["k0"]])
    return r - (x["cost"] * units if m["drag"] else 0.0), r, units, held, u["e"], u["R"]


# ----------------------------------------------------------------------------------------------------------- drift
def drift_table():
    """per (symbol, period): dict(t0, t1, p0, p1, tot%, ann%, atr mean, atr t, mu per minute)."""
    out = {}
    for s in SYMS:
        h4 = load(s + ".dk", "h4")
        for per in ("dev", "hold"):
            b = [x for x in h4 if (x[0][:4] < HOLD) == (per == "dev") and x[0][:4] >= "2012"]
            if len(b) < 30: continue
            c = np.array([x[4] for x in b]); hi = np.array([x[2] for x in b]); lo = np.array([x[3] for x in b])
            tr = np.maximum(hi[1:], c[:-1]) - np.minimum(lo[1:], c[:-1])
            atr = np.convolve(tr, np.ones(14) / 14, mode="full")[:len(tr)]           # trailing mean TR
            mv = (c[2:] - c[1:-1]) / atr[:-1]                                         # move / ATR known before the bar
            mv = mv[14:]
            mins = tmin(b[-1][0]) - tmin(b[0][0]); yrs = mins / 525960.0
            out[(s, per)] = dict(t0=b[0][0][:10], t1=b[-1][0][:10], p0=c[0], p1=c[-1], tot=(c[-1] / c[0] - 1) * 100,
                                 ann=((c[-1] / c[0]) ** (1 / yrs) - 1) * 100, am=float(mv.mean()),
                                 at=float(mv.mean() / (mv.std() / math.sqrt(len(mv)))), mu=math.log(c[-1] / c[0]) / mins,
                                 yrs=yrs)
    return out


# ----------------------------------------------------------------------------------------------------------- stats
def tstat(v):
    if len(v) < 2: return float("nan")
    sd = st.pstdev(v); return st.mean(v) / (sd / math.sqrt(len(v))) if sd > 0 else 0.0


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return float("nan"), float("nan")
    d = st.mean(a) - st.mean(b); se = math.sqrt(st.variance(a) / len(a) + st.variance(b) / len(b))
    return d, (d / se if se > 0 else 0.0)


class Out:
    def __init__(self): self.lines = []
    def __call__(self, s=""): print(s, flush=True); self.lines.append(s)


def per(t): return "dev" if t[:4] < HOLD else "hold"
PL = {"dev": "2012-24", "hold": "2025+"}


# ----------------------------------------------------------------------------------------------------------- main
def main():
    T0 = time.time()
    prof, refs = spread_profile(), ref_prices()
    tc = [dict(x, strat="TrendCont") for x in load_rows()]
    others = [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"]
    parents = sorted(tc + others, key=lambda x: x["t"])
    TR = []          # trades: dict(sym, strat, t, up, per, cls, r={md: costed}, drift_R)
    INV = []         # inverse fills: dict(sym, strat, t, up_inv, per, cls, plain={md}, pyr={md})
    REG = defaultdict(list); nomap = defaultdict(int); tsym = {}
    DR = drift_table()
    for sym in [s + ".dk" for s in SYMS]:
        t1 = time.time(); root = sym.split(".")[0]
        S = Sym(sym, prof, refs)
        for x in parents:
            if x["sym"] != sym: continue
            res = {md: live(S, x, md) for md in MD}
            if any(v is None for v in res.values()): nomap[(sym, x["strat"])] += 1; continue
            p = per(x["t"]); _, _, _, held, e, R = res[PRI]
            dr = DR.get((root, p)); dR = ((1 if x["up"] else -1) * e * dr["mu"] * held / R) if dr else 0.0
            TR.append(dict(sym=root, strat=x["strat"], t=x["t"], up=x["up"], per=p, cls=CLASS[root],
                           r={md: res[md][0] for md in MD}, drift_R=dR, held=held))
            if x["strat"] == "TrendCont":            # regression vs ask_side_report section A (S25, no pyramid)
                g = {md: regular(S, x, md, [SCEN[2]])["S25"][0] for md in MD}
                REG[(p, x["up"], root)].append(g)
            q = {md: inverse(S, x, md) for md in MD}
            fills = {md: (q[md] if (q[md] and W0 <= q[md]["bar"] <= W1) else None) for md in MD}
            if any(fills.values()):
                INV.append(dict(sym=root, strat=x["strat"], t=x["t"], up_inv=not x["up"], per=p, cls=CLASS[root],
                                plain={md: (fills[md]["plain"] if fills[md] else None) for md in MD},
                                pyr={md: (fills[md]["pyr"] if fills[md] else None) for md in MD}))
        tsym[root] = time.time() - t1
        print(f"  {root}: {sum(1 for z in TR if z['sym'] == root)} trades, {time.time() - t1:.0f}s", flush=True)
        del S
    report(TR, INV, REG, DR, nomap, tsym, time.time() - T0)


# ----------------------------------------------------------------------------------------------------------- report
READING = """
READING (hand-written from the tables below; SCALED unless stated)
  * 2012-24: the coach's reading holds for INDICES - longs +0.221 (n669, t+3.25), shorts -0.094 (n239), L-S +0.314 (t+2.84).
    The drift collected over the holding time is the same size as the gap (~0.33R vs 0.31R; class drift-adjusted L-S -0.019):
    consistent with drift, but the magnitude is approximate - per symbol it under-corrects US100 (+0.166 left) and
    over-corrects US500 (-0.189) and USDJPY (-0.232); holding time depends on the outcome (winners run, stopped shorts exit
    early), so index longs carry ~2.5x the drift-in-R of shorts. FX pooled is symmetric within noise (L-S +0.031, t+0.31);
    no FX pair's gap is significant (|t| < 0.7).
  * Symbol by symbol, among symbols whose drift is itself significant (|drift t| >= 2), the gap has the drift's sign in every
    case (see the count above); the low-drift symbols (EURUSD, GBPUSD, USOIL dev) are coin flips and are not counted.
  * It does NOT hold for commodities as a class: OIL had no up-drift in 2012-24 (-2%/yr) and has no gap (L +0.032 / S +0.056,
    L-S -0.025); the coach's oil short +0.012 is bid-only - the ask correction lifts oil shorts to +0.056 (TrendCont +0.068).
    GOLD longs > shorts (+0.247 / +0.118) but L-S +0.128 is t+0.57 on 75 shorts - consistent with drift, not established.
  * 2025+ (holdout) does NOT replicate the asset-class pattern - the short-side weakness sits in FX, not indices, the opposite
    of what the claim predicts. Index longs ~0 (+0.001, n59) despite +17-23%/yr drift (drift t ~0.8), index shorts +0.104
    (n31; TrendCont-only -0.189 on n16; per symbol 14 and 17 shorts). FX shorts -0.523 (n57, t-4.89) on every pair (EURUSD
    -0.49, GBPUSD -0.48, USDJPY -0.58); the 2025 dollar slide explains under half (drift-adjusted FX L-S still +0.365, t+2.03),
    and USDJPY shorts lost -0.58 against a drift-in-R of only -0.035 - that cell is not drift. Gold 2025+ longs +1.049 (n40)
    ride a +31%/yr drift.
  * The ask correction (SCALED vs BID, section 2) does not change the picture: 2012-24 shorts +0.005..+0.048R (oil most),
    longs +0.004..+0.027R (oil, where the old DRAG exceeded the 2026 bar-minimum spread); 2025+ FX longs -0.045R.
  * No class x direction cell is negative in BOTH periods with n>=30 in each. Nearest: TrendCont index SHORT (2012-24 n138
    -0.164, 2025+ n16 -0.189) and TrendCont US500 SHORT (n77 -0.156 / n10 -0.439) - holdout n too small to flag.
  * The Inverse does not mirror the drift pattern: 2012-24 short Inverses +0.318 (n213) beat long Inverses +0.164 (n143);
    only indices show long > short (+0.308 n37 / +0.141 n77, t+0.44).
""".rstrip("\n").split("\n")


def cellstats(rows, md="SCALED", key=None):
    rows = sorted(rows, key=lambda z: z["t"])
    v = [key(z) if key else z["r"][md] for z in rows]
    if not v: return None
    return dict(n=len(v), m=st.mean(v), t=tstat(v), dd=maxdd(v), v=v)


def fmt(c, w=24, flag=True):
    if not c: return f"{'-':>{w}}"
    tt = f"t{c['t']:+5.2f}" if c["n"] >= 5 else "t  n/a"
    s = f"n{c['n']:4d} {c['m']:+.3f} {tt} DD{c['dd']:5.1f}" + ("*" if flag and c["n"] < 30 else " ")
    return f"{s:>{w}}"


def lsfmt(a, b):
    if not a or not b or a["n"] < 2 or b["n"] < 2: return f"{'-':>16}"
    d, t = welch(a["v"], b["v"]); return f"{d:+.3f} (t{t:+5.2f})"


def report(TR, INV, REG, DR, nomap, tsym, secs):
    o = Out()
    sel = lambda **kw: [z for z in TR if all(z[k] == v for k, v in kw.items())]
    o(__doc__.split("    python3")[0].rstrip())

    # ------------------------------------------------------------------- compute the core class x side cells first (headline)
    C = {}
    for p in ("dev", "hold"):
        for c in CLS:
            for side in (True, False):
                C[(p, c, side)] = {md: cellstats(sel(per=p, cls=c, up=side), md) for md in MD}
                C[(p, c, side, "TC")] = {md: cellstats(sel(per=p, cls=c, up=side, strat="TrendCont"), md) for md in MD}
    flags, weak = [], []
    def chk(label, rows_by_p):
        a, b = rows_by_p["dev"], rows_by_p["hold"]
        if a and b and a["m"] < 0 and b["m"] < 0:
            (flags if (a["n"] >= 30 and b["n"] >= 30) else weak).append(
                f"{label}: 2012-24 n{a['n']} {a['m']:+.3f} (t{a['t']:+.2f}) | 2025+ n{b['n']} {b['m']:+.3f} (t{b['t']:+.2f})")
    for c in CLS:
        for side in (True, False):
            sd = "LONG" if side else "SHORT"
            chk(f"ALL detectors  {c:5} {sd:5}", {p: C[(p, c, side)][PRI] for p in ("dev", "hold")})
            for sg in STRATS:
                chk(f"{sg:14} {c:5} {sd:5}", {p: cellstats(sel(per=p, cls=c, up=side, strat=sg)) for p in ("dev", "hold")})
    for s in SYMS:
        for side in (True, False):
            chk(f"ALL detectors  {s:6} {'LONG' if side else 'SHORT':5} (symbol)", {p: cellstats(sel(per=p, sym=s, up=side)) for p in ("dev", "hold")})
            chk(f"TrendCont      {s:6} {'LONG' if side else 'SHORT':5} (symbol)",
                {p: cellstats(sel(per=p, sym=s, up=side, strat="TrendCont")) for p in ("dev", "hold")})

    # ------------------------------------------------------------------- HEADLINE
    o("\n" + "=" * 120)
    o("HEADLINE (primary mode SCALED = ask-corrected, 2026 hourly spread scaled to each trade's price; all 4 detectors pooled)")
    def m_(p, c, side, tc=False):
        q = C[(p, c, side, "TC") if tc else (p, c, side)][PRI]; return q
    lines = []
    for p in ("dev", "hold"):
        parts = []
        for c in CLS:
            L, S_ = m_(p, c, True), m_(p, c, False)
            d = lsfmt(L, S_) if (L and S_) else "-"
            parts.append(f"{c} L {L['m']:+.3f}(n{L['n']}) S {S_['m']:+.3f}(n{S_['n']}) gap {d}" if (L and S_)
                         else f"{c} L {L['m']:+.3f}(n{L['n']}) S -" if L else f"{c} -")
        lines.append(f"  {PL[p]:7}: " + " | ".join(parts))
    for ln in lines: o(ln)
    agree = {}
    for p in ("dev", "hold"):
        k = 0; tot = 0; miss = []
        for s_ in SYMS:
            L, S_ = cellstats(sel(per=p, sym=s_, up=True)), cellstats(sel(per=p, sym=s_, up=False))
            d = DR.get((s_, p))
            if not (L and S_ and d): continue
            if abs(d["at"]) < 2: continue                     # drift not itself significant: a coin flip, not counted
            tot += 1
            if (L["m"] - S_["m"] > 0) == (d["ann"] > 0): k += 1; miss.append(s_)
            else: miss.append(s_ + " MISS")
        agree[p] = (k, tot, miss)
    o("  sign(L-S) = sign(drift), symbols with |drift t| >= 2 only: " + "; ".join(
        f"{PL[p]} {k}/{t_} ({', '.join(m) or 'none'})" for p, (k, t_, m) in agree.items()))
    o(f"  flags (class/symbol x direction negative in BOTH periods, n>=30 each): {len(flags) or 'none'}")
    for ln in READING: o(ln)

    # ------------------------------------------------------------------- RUN
    o("\n" + "=" * 120)
    o(f"RUN: {len(TR)} live-stack trades on M1 ({', '.join(f'{sg} {len(sel(strat=sg))}' for sg in STRATS)}); "
      f"{len(INV)} Inverse fills (bars {W0}-{W1}, any mode); {secs / 60:.1f} min ("
      + ", ".join(f"{k} {v:.0f}s" for k, v in tsym.items()) + ")")
    o(f"  no M1 coverage (skipped): {dict(nomap) if nomap else 'none'}; run_x vs ask_side_study.run (live stack) mismatches: {MISMATCH[0]}")
    o("  Regression gate - ask_side_report.txt section A (TrendCont record, S25 = staged + bank 25%, NO pyramid), this run:")
    for p in ("dev", "hold"):
        allv = [g for k, v in REG.items() if k[0] == p for g in v]
        o(f"    {PL[p]:7} all n={len(allv)}: " + "  ".join(f"{md} {st.mean(g[md] for g in allv):+.4f}" for md in MD)
          + ("   (report: n=2078 BID +0.0797 ABS +0.0834 SCALED +0.0905)" if p == "dev" else
             "   (report: n=268 BID +0.0698 ABS +0.0598 SCALED +0.0599)"))
    o("  BTCUSD: skipped - no M1/M5 on disk (H4 only); BTCUSD failed its 2026-09-16 feasibility gate and is not on the live")
    o("  lineup; an H4-only replay would be a different engine from the other 7 symbols, so it is left out rather than mixed in.")

    # ------------------------------------------------------------------- 1. coach comparison
    o("\n" + "=" * 120)
    o("1. COACH COMPARISON - TrendCont blind record, LIVE STACK (staged + bank 25% + pyramid). Coach = M5 bid-only.")
    o("   This run: M1, BID (bid-only + DRAG, like-for-like with his) and SCALED (ask-corrected, primary). Mean costed R/trade.")
    o(f"  {'cell':22} {'coach':>8} {'n':>5} {'BID (M1)':>9} {'ABS':>8} {'SCALED':>8}")
    for p in ("dev", "hold"):
        for c in CLS:
            for side in (True, False):
                q = C[(p, c, side, "TC")]
                if not q[PRI]: continue
                cv = COACH.get((p, c), (None, None))[0 if side else 1]
                o(f"  {PL[p] + ' ' + c + (' LONG' if side else ' SHORT'):22} {('%+.3f' % cv) if cv is not None else '':>8} "
                  f"{q[PRI]['n']:5d} {q['BID']['m']:+9.3f} {q['ABS']['m']:+8.3f} {q['SCALED']['m']:+8.3f}")
    for (p, s), (cl, cs) in COACH_SYM.items():
        q = {md: cellstats(sel(per=p, sym=s, up=False, strat="TrendCont"), md) for md in MD}
        o(f"  {PL[p] + ' ' + s + ' SHORT':22} {cs:+8.3f} {q[PRI]['n']:5d} {q['BID']['m']:+9.3f} {q['ABS']['m']:+8.3f} {q['SCALED']['m']:+8.3f}")
        for p2 in ("hold",):
            q = {md: cellstats(sel(per=p2, sym=s, up=False, strat="TrendCont"), md) for md in MD}
            if q[PRI]: o(f"  {PL[p2] + ' ' + s + ' SHORT':22} {'':>8} {q[PRI]['n']:5d} {q['BID']['m']:+9.3f} {q['ABS']['m']:+8.3f} {q['SCALED']['m']:+8.3f}")
    q = {md: cellstats(sel(sym="USDJPY", up=False, strat="TrendCont"), md) for md in MD}
    o(f"  {'2012-26 pooled USDJPY SHORT':36} n={q[PRI]['n']} BID {q['BID']['m']:+.3f} SCALED {q['SCALED']['m']:+.3f}"
      "  (his -0.231 is this pooled 2012-26 figure, not 2012-24)")
    q = {md: cellstats(sel(per="hold", cls="index", up=False), md) for md in MD}
    o(f"  {'2025+ index SHORT, ALL 4 detectors':36} n={q[PRI]['n']} BID {q['BID']['m']:+.3f} SCALED {q['SCALED']['m']:+.3f}"
      "  (checks whether his -0.197 pooled detectors)")

    # ------------------------------------------------------------------- 2. class x side, all detectors, 3 modes
    o("\n" + "=" * 120)
    o("2. ALL 4 DETECTORS POOLED - class x direction; cell = n, mean costed R/trade, t, worst DD (R, time order); * = n<30")
    o("   L-S = long-minus-short difference of means (Welch t). BID shown so the size of the ask correction is visible.")
    for p in ("dev", "hold"):
        o(f"\n  {PL[p]}")
        o(f"  {'class':6} {'side':5}" + "".join(f" {md:>31}" for md in MD))
        for c in CLS:
            for side in (True, False):
                q = C[(p, c, side)]
                o(f"  {c:6} {'LONG' if side else 'SHORT':5}" + "".join(f" {fmt(q[md], 31)}" for md in MD))
            o(f"  {c:6} {'L-S':5}" + "".join(f" {lsfmt(C[(p, c, True)][md], C[(p, c, False)][md]):>31}" for md in MD))
        for side in (True, False):
            q = {md: cellstats(sel(per=p, up=side), md) for md in MD}
            o(f"  {'ALL':6} {'LONG' if side else 'SHORT':5}" + "".join(f" {fmt(q[md], 31)}" for md in MD))
        o(f"  {'ALL':6} {'L-S':5}" + "".join(f" {lsfmt(cellstats(sel(per=p, up=True), md), cellstats(sel(per=p, up=False), md)):>31}" for md in MD))

    # ------------------------------------------------------------------- 3. per detector x class x side (SCALED)
    o("\n" + "=" * 120)
    o(f"3. PER DETECTOR x CLASS x DIRECTION ({PRI}). cell = n, mean, t, DD; * = n<30 (read as anecdote)")
    o(f"  {'detector':10} {'class':6} {'LONG 2012-24':>26} {'SHORT 2012-24':>26} {'L-S 2012-24':>17} {'LONG 2025+':>26} {'SHORT 2025+':>26}")
    for sg in STRATS:
        for c in CLS + ("ALL",):
            cc = {}
            for p in ("dev", "hold"):
                for side in (True, False):
                    rows = sel(per=p, up=side, strat=sg) if c == "ALL" else sel(per=p, cls=c, up=side, strat=sg)
                    cc[(p, side)] = cellstats(rows)
            o(f"  {sg:10} {c:6} {fmt(cc[('dev', True)], 26)} {fmt(cc[('dev', False)], 26)} {lsfmt(cc[('dev', True)], cc[('dev', False)]):>17}"
              f" {fmt(cc[('hold', True)], 26)} {fmt(cc[('hold', False)], 26)}")

    # ------------------------------------------------------------------- 4. drift
    o("\n" + "=" * 120)
    o("4. BUY-AND-HOLD DRIFT (H4 .dk closes over each period's coverage; mean move = close-to-close / trailing ATR14 per H4 bar)")
    o(f"  {'symbol':7} {'period':7} {'from':>10} {'to':>10} {'total %':>9} {'ann. %':>8} {'move/ATR per bar':>18}")
    for s in SYMS:
        for p in ("dev", "hold"):
            d = DR.get((s, p))
            if not d: continue
            o(f"  {s:7} {PL[p]:7} {d['t0']:>10} {d['t1']:>10} {d['tot']:+9.1f} {d['ann']:+8.1f} {d['am']:+9.4f} (t{d['at']:+5.2f})")

    # ------------------------------------------------------------------- 5. per symbol x side vs drift
    o("\n" + "=" * 120)
    o(f"5. PER SYMBOL x DIRECTION vs DRIFT ({PRI}). L-S = long-minus-short (Welch t). drift-in-R = mean of side x drift over the")
    o("   holding time / R (longs credited, shorts charged the underlying's average move). adj L-S = L-S of (R - drift-in-R).")
    o("   If drift explains the gap, adj L-S ~ 0; sign(L-S) should follow sign(annualised drift) symbol by symbol.")
    for lab, flt in (("ALL 4 detectors", {}), ("TrendCont only", {"strat": "TrendCont"})):
        o(f"\n  {lab}")
        o(f"  {'symbol':7} {'period':7} {'ann.drift':>9} {'LONG':>26} {'SHORT':>26} {'L-S':>17} {'drift-in-R L/S':>16} {'adj L-S':>17} {'med hold h':>10}")
        for grp in [(s,) for s in SYMS] + [("class:" + c,) for c in CLS]:
            g = grp[0]
            for p in ("dev", "hold"):
                if g.startswith("class:"):
                    kw = dict(flt, per=p, cls=g[6:]); ann = "-"
                else:
                    kw = dict(flt, per=p, sym=g); d = DR.get((g, p)); ann = f"{d['ann']:+.1f}%" if d else "-"
                L, S_ = cellstats(sel(up=True, **kw)), cellstats(sel(up=False, **kw))
                if not L and not S_: continue
                La = cellstats(sel(up=True, **kw), key=lambda z: z["r"][PRI] - z["drift_R"])
                Sa = cellstats(sel(up=False, **kw), key=lambda z: z["r"][PRI] - z["drift_R"])
                dL = st.mean(z["drift_R"] for z in sel(up=True, **kw)) if L else float("nan")
                dS = st.mean(z["drift_R"] for z in sel(up=False, **kw)) if S_ else float("nan")
                mh = st.median(z["held"] for z in sel(**kw)) / 60
                o(f"  {g:7} {PL[p]:7} {ann:>9} {fmt(L, 26)} {fmt(S_, 26)} {lsfmt(L, S_):>17} {dL:+7.3f}/{dS:+7.3f} {lsfmt(La, Sa):>17} {mh:10.0f}")

    # ------------------------------------------------------------------- 6. inverse
    o("\n" + "=" * 120)
    o("6. THE INVERSE by its OWN side (opposite of the parent), parents from all 4 detectors, parent stopped in H4 bars 7-18.")
    o("   Per FILL: n, mean costed R, t, DD. live = with its +1.5R pyramid (pyramid_on_inverse=1); plain alongside.")
    for what in ("pyr", "plain"):
        o(f"\n  {'LIVE (with pyramid)' if what == 'pyr' else 'PLAIN'}")
        o(f"  {'period':7} {'class':6} {'side':5} {'BID':>26} {'SCALED':>26}")
        for p in ("dev", "hold"):
            for c in CLS + ("ALL",):
                for side in (True, False):
                    cc = {}
                    for md in ("BID", PRI):
                        rows = [z for z in INV if z["per"] == p and z["up_inv"] == side and (c == "ALL" or z["cls"] == c)
                                and z[what][md] is not None]
                        cc[md] = cellstats(rows, key=lambda z, md=md: z[what][md])
                    o(f"  {PL[p]:7} {c:6} {'LONG' if side else 'SHORT':5} {fmt(cc['BID'], 26)} {fmt(cc[PRI], 26)}")
                rows = lambda s_: [z for z in INV if z["per"] == p and z["up_inv"] == s_ and (c == "ALL" or z["cls"] == c) and z[what][PRI] is not None]
                o(f"  {PL[p]:7} {c:6} {'L-S':5} {'':>26} {lsfmt(cellstats(rows(True), key=lambda z: z[what][PRI]), cellstats(rows(False), key=lambda z: z[what][PRI])):>26}")

    # ------------------------------------------------------------------- 7. flags
    o("\n" + "=" * 120)
    o(f"7. FLAGS - class x direction (and symbol x direction) cells NEGATIVE IN BOTH PERIODS with n>=30 in each ({PRI})")
    for f in flags: o("  *** " + f)
    if not flags: o("  none")
    o("  negative in both periods but n<30 in at least one period (not a flag - anecdote):")
    for f in weak: o("    " + f)

    # ------------------------------------------------------------------- 8. US30
    o("\n" + "=" * 120)
    o("8. US30")
    import glob
    aa = sorted(os.path.basename(p) for p in glob.glob(os.path.join(JRN, "AA_US30*.csv")))
    h4 = sorted(os.path.basename(p) for p in glob.glob(os.path.join(STUDY, "h4", "US30*.csv")))
    m1 = os.path.exists(os.path.join(ROOT, "data", "qdm_csv", "fleet", "USA30IDXUSD-M1-No Session.csv"))
    o(f"  AA journals: {aa or 'none'} - live-sim week only (2 signals, 2026-09-14 and 09-21), no 2012-26 blind record.")
    o(f"  H4 bars: {h4 or 'none'} (US30.sim.csv starts 2026-06-30; no US30.dk history). M1 export on disk: {m1}.")
    o("  QDM store /home/jack/QDM/user/data/History/USA30IDXUSD exists, but without a blind signal record (entry / SL / TP levels)")
    o("  there is nothing to replay; signals are not invented. US30 is SKIPPED; M1 was not exported. Indices = US100 + US500.")

    # ------------------------------------------------------------------- headline reading (written after the tables)
    o("\n" + "=" * 120)
    o("CAVEATS")
    o("  * Spread profile = 2026 MT5 M1 bar-minimum median: understates the real spread (EURUSD ~0), so SCALED is a lower bound")
    o("    on the ask cost; the hour-of-day profile is September (EEST) applied unshifted to winter dates.")
    o("  * SweepMSS / DeepFib / EMArevQ come from the long AA runs, which start 2013-2020 by symbol (EURUSD 2016, USDJPY / XAUUSD 2020)")
    o("    and mostly end 2025-12-30: their 2025+ cells are small. TrendCont's 2025+ record runs into mid-2026.")
    o("  * Drift-in-R uses in-sample realised drift and full size from the signal: a reading aid, not a model.")
    with open(REPORT, "w") as f: f.write("\n".join(o.lines) + "\n")
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__": main()
