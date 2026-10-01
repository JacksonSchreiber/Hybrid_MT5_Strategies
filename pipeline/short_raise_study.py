#!/usr/bin/env python3
"""short_raise_study.py - coach item 25 (2026-10-01): SHORTS-only stop raise at +1.5R. Study only - nothing ships.

RULE: on SELL trades, when +1.5R trades (the +1.5R pyramid fires), every leg's stop (the original tranches, the pyramid add,
and a staged add that enters later) moves to +L R, effective from the next bar (a bar opening through it fills at the open).
Longs untouched. Everything else is the live stack: staged 25% at the signal + 75% at the bar-6 close, bank per detector
(TrendCont / DeepFib 25%, SweepMSS / EMArevQ 50%) at +1R (or TP1 if nearer) on every open leg, stop to the first tranche's
entry at the bank, pyramid 50% of full size at +1.5R, runner to TP2.
  L = +0.10 / +0.25 / +0.50 R, measured from the first tranche's fill (the stop-to-entry anchor).
  TIMED variant (this morning's): the same raise applied at the close of the 720th M1 bar (3 H4 bars) after the pyramid bar,
  if TP2 has not been reached; a close already at/below +L closes the position there (a stop cannot sit beyond the market).
  MIRROR: the identical rule applied to BUY trades only (shorts untouched), and to both sides - the asymmetry on one page.
ENGINE: pipeline/ask_side_study (M1 bid series, data/qdm_csv/fleet; event skipping verified == a naive per-bar loop below).
  BID    = bid-only triggers + the per-symbol DRAG per unit traded (the replay the coach's M5 numbers come from, on M1).
  SCALED = ask-corrected: shorts stopped / banked / exited on the ASK, longs enter on the ASK; the Sept-2026 OANDA hourly median
           spread re-priced to each trade's price level (no DRAG - the spread is in the fills). The preferred live estimate.
  ABS    = ask-corrected with the 2026 spread in price units for every year (overstates old index / gold costs in R).
  The spread profile is a LOWER bound (MT5 M1 bar spreads; EURUSD ~0 most hours) - see ask_side_report.txt.
POPULATION: TrendCont blind record + SweepMSS / DeepFib / EMArevQ AA journal rows, 7 symbols (no BTCUSD), H4 bar-index rule
  as ask_side_study (entry at the open of the bar after the signal bar; per-trade .dk-to-M1 offset from the signal-bar close).
  2012-24 (development) and 2025-26 (holdout) separately. Worst DD = the account's R sequence in signal-time order.
"""
from __future__ import annotations
import bisect, math, os, random, statistics as st, sys, time
from collections import defaultdict
from multiprocessing import Pool
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t             # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402
from pipeline.inverse_study import BANKF                                    # noqa: E402

STUDY = A.STUDY; REPORT = os.path.join(STUDY, "short_raise_report.txt")
MODES = ("BID", "SCALED", "ABS"); LS = (0.10, 0.25, 0.50); TIMED = 720
SCEN = [("base", None, False)] + [(f"now{L:.2f}", L, False) for L in LS] + [(f"t3{L:.2f}", L, True) for L in LS]
CLS = lambda s: "index" if s.split(".")[0] in ("US100", "US500") else ("gold" if s.startswith("XAU") else ("oil" if s.startswith("USOIL") else "fx"))


def run_r(F, e0, be, bank_R, tp2_R, bankf, F0_, k_add, L=None, timed=False, naive=False):
    """live stack + optional raise to be+L at the pyramid (or TIMED bars after it). -> (raw R, units)"""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n; pyr_pend = True; tk = None
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        if not naive:
            j2 = A.nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend, None, k_add if add_pend else None)
            if tk is not None and j <= tk < j2: j2 = tk
            j = j2
            if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
        if banked and pyr_pend and bh[j] >= A.PYR_R:
            legs.append([A.PYR_F, max(A.PYR_R, bo[j]) + eo[j]]); units += A.PYR_F; pyr_pend = False
            if L is not None:
                if timed: tk = j + TIMED - 1
                else: stop = max(stop, be + L)                     # checked from the next bar on
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = False
        if tk is not None and j == tk:
            tk = None; lv = be + L
            if lv > stop:
                if xc[j] <= lv: return val(xc[j]), units
                stop = lv
        j += 1
    return val(xc[n - 1]), units


def trade(S, x, mode, naive=False):
    u = A.setup(S, x, mode)
    if not u: return None
    m = A.MODES[mode]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0; bankf = BANKF.get(x["strat"], 0.5)
    out = {}
    for name, L, timed in SCEN:
        r, units = run_r(F, e0, be, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], L, timed, naive)
        out[name] = r - (x["cost"] * units if m["drag"] else 0.0)
    return out


def do_symbol(sym):
    prof, refs = A.spread_profile(), A.ref_prices()
    S = A.Sym(sym, prof, refs)
    rows = [x for x in ALL if x["sym"] == sym]
    rnd = random.Random(7); bad = 0
    for x in rnd.sample(rows, min(25, len(rows))):                     # event-skip == naive per-bar loop
        for md in MODES:
            a, b = trade(S, x, md), trade(S, x, md, naive=True)
            if (a is None) != (b is None) or (a and any(abs(a[k] - b[k]) > 1e-9 for k in a)): bad += 1
    res = []
    for x in rows:
        r = {md: trade(S, x, md) for md in MODES}
        if any(v is None for v in r.values()): continue
        res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return sym, res, bad


# ------------------------------------------------------------------ M5 reproduction of the coach's TrendCont table
def m5_run(M, k0, k_add, up, e, s, tp1, tp2, cost_unit, L=None, sides=None):
    R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tgt = toR(tp2) if tp2 else None
    legs = [[0.25, 0.0]]; locked = 0.0; stop = -1.0; banked = False; added = False; kp = None; cost = cost_unit * 0.25
    val = lambda r: locked + sum(g[0] * (r - g[1]) for g in legs)
    O, H, Lo, C = M["o"], M["h"], M["l"], M["c"]; end = min(len(O), k0 + MAX_M5); last = None
    for k in range(k0, end):
        o, c = toR(O[k]), toR(C[k]); hi, lo = (toR(H[k]), toR(Lo[k])) if up else (toR(Lo[k]), toR(H[k])); last = c
        if lo <= stop: return val(min(stop, o)) - cost
        if not banked and hi >= bank_R:
            banked = True; locked += 0.25 * sum(g[0] * (bank_R - g[1]) for g in legs)
            for g in legs: g[0] *= 0.75
            stop = 0.0
        if banked and kp is None and hi >= 1.5:
            kp = k; legs.append([0.5, max(1.5, o)]); cost += cost_unit * 0.5
            if L is not None and (up in sides): stop = max(stop, L)
        if tgt is not None and hi >= tgt: return val(tgt) - cost
        if not added and k_add is not None and k == k_add:
            legs.append([0.75, c]); added = True; cost += cost_unit * 0.75
    return val(last) - cost


def m5_work(arg):
    L, sides = arg
    return arg, [m5_run(M, k0, ka, *a, L, sides) for x, M, k0, ka, a in M5PREP]


ALL = []; M5PREP = []; M5META = []


def main():
    global ALL, MAX_M5
    T0 = time.time()
    tc = [dict(x, strat="TrendCont") for x in load_rows()]
    others = [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"]
    ALL = sorted(tc + others, key=lambda x: x["t"])
    syms = sorted({x["sym"] for x in ALL})
    # M5 reproduction first (TrendCont, BID + DRAG) - same prep as time_raise_sl_study
    from pipeline.m5_study import load_m5, MAX_M5 as _MX
    MAX_M5 = _MX; cache = {}
    for x in sorted(load_rows(), key=lambda x: x["t"]):
        h4 = x["bars"]; i0 = x["i0"]
        if i0 + 6 >= len(h4): continue
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]; k0 = bisect.bisect_left(M["t"], h4[i0][0][:16])
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != h4[i0][0][:10]: continue
        ka = bisect.bisect_left(M["t"], h4[i0 + 6][0][:16]) - 1; off = h4[i0 - 1][4] - M["c"][k0 - 1]
        M5PREP.append((x, M, k0, ka, (x["up"], x["e"] - off, x["s"] - off, (x["tp1"] - off) if x["tp1"] else None,
                                       (x["tp2"] - off) if x["tp2"] else None, x["cost"])))
    jobs = [(None, None), (0.10, (False,)), (0.25, (False,)), (0.50, (False,)), (0.25, (True, False))]
    with Pool(6) as pool: m5 = dict(pool.map(m5_work, jobs))
    M5META.extend(p[0] for p in M5PREP); M5PREP.clear(); del cache
    import gc; gc.collect()
    with Pool(2) as pool: out = pool.map(do_symbol, syms)
    report(m5, out, time.time() - T0)


# ------------------------------------------------------------------ report
class Out:
    def __init__(self): self.lines = []
    def __call__(self, s=""): print(s, flush=True); self.lines.append(s)


def report(m5, out, secs):
    o = Out()
    o(__doc__.rstrip())
    res = sorted([r for _, rs, _ in out for r in rs], key=lambda z: z[1])
    o(f"\nRUN: {len(res)} trades ({', '.join(f'{k} {v}' for k, v in sorted(defaultdict(int, {s: sum(1 for z in res if z[2] == s) for s in {z[2] for z in res}}).items()))}); "
      f"{secs / 60:.1f} min. VERIFY event-skip vs naive per-bar loop (25 trades x 3 modes per symbol): "
      + ", ".join(f"{s.split('.')[0]} {b}" for s, _, b in out) + " mismatches")

    # ---- 0. M5 reproduction
    o("\n" + "=" * 120)
    o("0. REPRODUCTION OF THE COACH'S M5 TABLE (TrendCont only, bid-only + DRAG, M5, immediate raise effective next M5 bar)")
    P = [(x,) for x in M5META]; b = m5[(None, None)]
    for per, f in (("2012-24", lambda x: x["t"][:4] < "2025"), ("2025-26", lambda x: x["t"][:4] >= "2025")):
        ix = [i for i, p in enumerate(P) if f(p[0])]; sx = [i for i in ix if not P[i][0]["up"]]
        o(f"  {per}: n={len(ix)} ({len(sx)} shorts)  base {st.mean(b[i] for i in ix):+.4f}  DD {maxdd([b[i] for i in ix]):.1f}  shorts {st.mean(b[i] for i in sx):+.4f}")
        for key, lab in (((0.10, (False,)), "shorts +0.10R"), ((0.25, (False,)), "shorts +0.25R"), ((0.50, (False,)), "shorts +0.50R"),
                         ((0.25, (True, False)), "ALL trades +0.25R")):
            v = m5[key]; d = [v[i] - b[i] for i in ix]
            sel = ix if key[1] == (True, False) else sx
            o(f"    {lab:18} overall {st.mean(v[i] for i in ix):+.4f} ({st.mean(d):+.4f}, t {paired_t([b[i] for i in ix], [v[i] for i in ix]):+.2f})  DD {maxdd([v[i] for i in ix]):.1f}"
              f"  shorts {st.mean(v[i] for i in sx):+.4f}  helped/hurt {sum(1 for i in sel if v[i] - b[i] > 1e-9)}/{sum(1 for i in sel if v[i] - b[i] < -1e-9)}")
        o(f"    by class, shorts +0.25R: " + "  ".join(
            f"{c} {st.mean(b[i] for i in sx if CLS(P[i][0]['sym']) == c):+.3f}->{st.mean(m5[(0.25, (False,))][i] for i in sx if CLS(P[i][0]['sym']) == c):+.3f}"
            for c in ("fx", "index", "gold", "oil") if any(CLS(P[i][0]['sym']) == c for i in sx)))

    # ---- helpers on the M1 results
    def subset(per, side=None, cls=None, sym=None, strat=None):
        return [z for z in res if ((z[1][:4] < "2025") == (per == "dev")) and (side is None or z[3] == (side == "L"))
                and (cls is None or CLS(z[0]) == cls) and (sym is None or z[0] == sym) and (strat is None or z[2] == strat)]
    def comp(zs, md, sc, rule_side):
        """account sequence: rule applied on rule_side ('S', 'L', 'B'), the other side at base."""
        return [z[4][md][sc] if (rule_side == "B" or (rule_side == "S") != z[3]) else z[4][md]["base"] for z in zs]
    def cell(zs, md, sc, rule_side="S"):
        if not zs: return None
        b_ = [z[4][md]["base"] for z in zs]; v = comp(zs, md, sc, rule_side); d = [p - q for p, q in zip(v, b_)]
        return dict(n=len(zs), base=st.mean(b_), rule=st.mean(v), inc=st.mean(d), t=paired_t(b_, v) if len(zs) > 2 else float("nan"),
                    dd0=maxdd(b_), dd1=maxdd(v), hp=sum(1 for e in d if e > 1e-9), ht=sum(1 for e in d if e < -1e-9), d=d)
    fmt = lambda c: (f"{c['base']:+.4f}->{c['rule']:+.4f} ({c['inc']:+.4f} t{c['t']:+5.2f}) DD {c['dd0']:4.1f}->{c['dd1']:4.1f} h/h {c['hp']}/{c['ht']}"
                     if c else "-")

    # ---- 1. main table
    o("\n" + "=" * 120)
    o("1. ACCOUNT (all four detectors): base -> with the rule (increment, paired t), worst DD base -> rule, helped/hurt trades")
    for per in ("dev", "hold"):
        zs = subset(per)
        o(f"\n  {'2012-24' if per == 'dev' else '2025-26'}  n={len(zs)} ({sum(1 for z in zs if not z[3])} shorts)")
        o(f"  {'rule':26} " + "".join(f"{md:>66}" for md in MODES))
        for lab, sc, side in ([(f"SHORTS raise +{L:.2f}R", f"now{L:.2f}", "S") for L in LS] + [("SHORTS timed 3 bars +0.25R", "t30.25", "S")]
                              + [(f"LONGS mirror +{L:.2f}R", f"now{L:.2f}", "L") for L in LS] + [("BOTH sides +0.25R", "now0.25", "B")]):
            o(f"  {lab:26} " + "".join(f"{fmt(cell(zs, md, sc, side)):>66}" for md in MODES))

    # ---- 2. shorts alone, by class, symbol, detector (main level +0.25 and the others' increments)
    o("\n" + "=" * 120)
    o("2. SHORT SIDE ONLY - +0.25R immediate: base -> rule (inc, t), DD, helped/hurt;  then the increment at +0.10 / +0.50 / timed +0.25")
    for per in ("dev", "hold"):
        o(f"\n  {'2012-24' if per == 'dev' else '2025-26'}")
        for md in ("BID", "SCALED", "ABS"):
            o(f"   [{md}]")
            groups = ([("all shorts", {})] + [(c, {"cls": c}) for c in ("fx", "index", "gold", "oil")]
                      + [(s.split(".")[0], {"sym": s}) for s in sorted({z[0] for z in res})]
                      + [(s, {"strat": s}) for s in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ")])
            for lab, kw in groups:
                zs = subset(per, side="S", **kw)
                if not zs: continue
                c = cell(zs, md, "now0.25")
                extra = "  ".join(f"{nm} {cell(zs, md, sc)['inc']:+.4f} ({cell(zs, md, sc)['hp']}/{cell(zs, md, sc)['ht']})"
                                  for nm, sc in (("+0.10", "now0.10"), ("+0.50", "now0.50"), ("t3 +0.25", "t30.25")))
                o(f"     {lab:11} n{c['n']:4d} {fmt(c):74} | {extra}")
    # longs mirror by class (asymmetry)
    o("\n  LONG-SIDE MIRROR by class (+0.25R immediate, increment per long trade and helped/hurt), BID | SCALED:")
    for per in ("dev", "hold"):
        cells = []
        for c in ("fx", "index", "gold", "oil"):
            zs = subset(per, side="L", cls=c)
            if not zs: continue
            q = {md: cell(zs, md, "now0.25", "L") for md in ("BID", "SCALED")}
            cells.append(f"{c} n{len(zs)} {q['BID']['inc']:+.4f} ({q['BID']['hp']}/{q['BID']['ht']}) | {q['SCALED']['inc']:+.4f} ({q['SCALED']['hp']}/{q['SCALED']['ht']})")
        o(f"    {'2012-24' if per == 'dev' else '2025-26'}  " + "   ".join(cells))

    # ---- 3. hurt trades
    o("\n" + "=" * 120)
    o("3. EVERY HURT SHORT (+0.25R immediate) - symbol, signal time, detector, R today -> R with the rule (BID | SCALED | ABS)")
    for per in ("dev", "hold"):
        zs = subset(per, side="S"); hurt = [z for z in zs if any(z[4][md]["now0.25"] < z[4][md]["base"] - 1e-9 for md in MODES)]
        o(f"  {'2012-24' if per == 'dev' else '2025-26'}: {len(hurt)} trades hurt in at least one mode")
        for z in sorted(hurt, key=lambda z: z[1]):
            o(f"    {z[0].split('.')[0]:7} {z[1][:16]} {z[2]:9} " + " | ".join(
                f"{md} {z[4][md]['base']:+.2f} -> {z[4][md]['now0.25']:+.2f} ({z[4][md]['now0.25'] - z[4][md]['base']:+.2f})" for md in MODES))
        big = sorted(zs, key=lambda z: -(z[4]["SCALED"]["now0.25"] - z[4]["SCALED"]["base"]))[:3]
        o(f"    largest helped (SCALED): " + "; ".join(f"{z[0].split('.')[0]} {z[1][:10]} {z[2]} {z[4]['SCALED']['now0.25'] - z[4]['SCALED']['base']:+.2f}" for z in big))

    # ---- 4. the pre-registered bar
    o("\n" + "=" * 120)
    o("4. PRE-REGISTERED BAR (rule = shorts +0.25R immediate; all four detectors):")
    o("   (a) overall R not lower in either period  (b) overall worst DD lower in both periods")
    o("   (c) short-side increment > 0 in a majority (>= 3 of 4) of classes, 2012-24")
    o("   (d) overall increment still > 0 with the two largest helped trades removed and every hurt trade counted twice")
    def bar(sc, strat=None):
        for md in MODES:
            cd, ch = cell(subset("dev", strat=strat), md, sc), cell(subset("hold", strat=strat), md, sc)
            a = cd["inc"] >= 0 and ch["inc"] >= 0; b_ = cd["dd1"] < cd["dd0"] and ch["dd1"] < ch["dd0"]
            cls_pos = [c for c in ("fx", "index", "gold", "oil") if cell(subset("dev", side="S", cls=c, strat=strat), md, sc)["inc"] > 0]
            def frag(c):
                d = sorted(c["d"], reverse=True); s_ = sum(d) - sum(e for e in d[:2] if e > 0) + sum(e for e in d if e < 0)
                return s_ / c["n"]
            fd, fh = frag(cd), frag(ch)
            ok = a and b_ and len(cls_pos) >= 3 and fd > 0
            o(f"  [{md:6}] (a) {cd['inc']:+.4f} / {ch['inc']:+.4f} {'PASS' if a else 'FAIL'}   (b) DD {cd['dd0']:.1f}->{cd['dd1']:.1f} / {ch['dd0']:.1f}->{ch['dd1']:.1f} "
              f"{'PASS' if b_ else 'FAIL'}   (c) {len(cls_pos)}/4 ({','.join(cls_pos)}) {'PASS' if len(cls_pos) >= 3 else 'FAIL'}   "
              f"(d) 2012-24 {fd:+.4f} {'PASS' if fd > 0 else 'FAIL'} (2025-26 {fh:+.4f})   => {'CLEARS' if ok else 'DOES NOT CLEAR'}")
    bar("now0.25")
    o("\n  INFORMATION ONLY - not the pre-registered rule / population (chosen after seeing the table; do not read as a pass):")
    o("  shorts +0.10R, all four detectors:"); bar("now0.10")
    o("  shorts +0.25R, TrendCont only (the coach's M5 population, here on M1):"); bar("now0.25", "TrendCont")
    with open(REPORT, "w") as f: f.write("\n".join(o.lines) + "\n")
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__": main()
