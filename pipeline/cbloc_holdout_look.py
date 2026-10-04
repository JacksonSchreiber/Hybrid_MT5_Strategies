#!/usr/bin/env python3
"""cbloc_holdout_look.py - THE ONE LOOK at the commodity-bloc time stop on the HOLDOUT symbols AUDNZD, AUDCAD, NZDCAD.

Pre-registration (frozen, written before the holdout data existed): docs/coach_queue.md "PRE-REGISTRATION - commodity-bloc time stop".
  T       if the bid high has not reached +1.5R (first entry's R) by the close of bar 6 after the signal, close everything at that
          close (the staged 75% add is not placed).
  T+L1.0  T, and at the first +1.5R every stop (longs and shorts) moves to +1.0R.
  base    "live" (the live setup: 25%/75% at bar 6, bank at +1R/TP1 by detector -> stop to entry, +50% pyramid at +1.5R, shorts' stop to
          +0.25R at +1.5R).
Engine: pipeline/cbloc_timestop_study.run_h, UNCHANGED, for exactly the variants "live", "T", "T+L1.0"; SCALED mode (ask-corrected,
  live spreads scaled by entry / ref price); M1 arrays from the parse-once cache (pipeline/m1_cache.CachedSym via combo_build.sym_obj),
  the same per-trade frame as cbloc_timestop_study.do_symbol.
Population: the three symbols' four-detector AA journals in data/study/holdout_aa (pipeline/oos_mt5_stage.sh --holdout: model 4,
  2012.01.01..2026.06.30, EA input defaults - the settings of the other new symbols), read by exit_s025_study.aa_rows UNCHANGED
  (approved rows, signal bar found in the .dk H4 file, entry / sl parse), signals from 2012-01-01 and before
  oos_build_inputs.SIGNAL_END (2026.07.01). Ref price / tick / M1 file: data/study/holdout_aa/holdout_inputs.json
  (oos_build_inputs.py build --holdout; ref = universe_cost_filter.csv price, as the nine new symbols).
Bars (pooled over the three, all years; frozen):
  PRIMARY    T+L1.0 increment vs live > 0  AND  paired t >= 1.5  AND  worst DD (T+L1.0) <= worst DD (live)
  SECONDARY  T increment vs live > 0
Reported: pooled all years / 2012-24 / 2025-26, each all / long / short; per symbol the same. ONE look; no rule changes after it.
ADDENDUM 2026-10-04 (trader: "run 5.2 on the holdouts"; written and committed BEFORE the look - docs/coach_queue.md "ADDENDUM - v5.2"):
  v5.2 = pipeline/cbloc_v52.py (frozen engine), computed in the same single look, same population and frames.
  PRIMARY    v5.2 pooled all years: mean R > 0 AND one-sample t >= 1.5
  SECONDARY  v5.2 increment vs live > 0 AND worst DD (v5.2) <= worst DD (live)
  Judged at model cost (spread_profile.csv = M1 bar spreads, a LOWER bound); the extra-spread lines (+0.5/1/1.5/2 pips per unit traded,
  live charged for its own units) are information, not bars. All report text is built before either output file is written.

Usage
  python3 pipeline/cbloc_holdout_look.py                    readiness check only: lists what is missing; computes nothing
  python3 pipeline/cbloc_holdout_look.py --insample-check   validates THIS code path (loader, engine call, blocks, verdict) on
        AUDUSD/NZDUSD/USDCAD from data/study/oos_aa: must reproduce data/study/cbloc_timestop_report.txt's commodity-bloc
        live / T / T+L1.0 lines exactly -> data/study/holdout_aa/cbloc_holdout_insample_check.txt
  python3 pipeline/cbloc_holdout_look.py --build-cache      M1 caches for the three (prices only) + cache-vs-CSV check; no outcome
  python3 pipeline/cbloc_holdout_look.py --look             THE LOOK -> data/study/cbloc_holdout_report.txt (+ cbloc_holdout_trades.pkl)
        Refuses without --look, refuses while any symbol is not ready, and refuses to overwrite an existing report or trades file
        (data/ is gitignored: copy / commit the report text and record its sha256 on the day).
ONE-LOOK: nothing in the readiness check or --build-cache reads an outcome column; the journals' r_multiple / pnl / terminal / mfe_r
are never read by this script at all (the engine replays M1 prices).
"""
from __future__ import annotations
import argparse, hashlib, json, multiprocessing as mp, os, pickle, re, statistics as st, sys, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from pipeline import ask_side_study as A                  # noqa: E402
from pipeline import cbloc_timestop_study as C            # noqa: E402  frozen engine (run_h) + the in-sample report
from pipeline import cbloc_v52 as V52                     # noqa: E402  frozen v5.2 engine (addendum 2026-10-04)
from pipeline import combo_build as CB                    # noqa: E402  sym_obj -> CachedSym(sym, profile, Q.REFS)
from pipeline import exit_s025_study as S025              # noqa: E402  aa_rows (unchanged)
from pipeline import m1_cache as M                        # noqa: E402
from pipeline import oos_build_inputs as OI               # noqa: E402
from pipeline import oos_queue_study as Q                 # noqa: E402  REFS (the nine new symbols registered at import)
from pipeline.exit_mgmt_study import maxdd, paired_t      # noqa: E402
from pipeline.inverse_study import BANKF                  # noqa: E402

VARIANTS = [("live", None, None), ("T", 1, None), ("T+L1.0", 1, 1.0)]
assert all(v in C.VARIANTS for v in VARIANTS), "variant definitions drifted from cbloc_timestop_study.VARIANTS"
STRATS = {"SweepMSS", "DeepFib", "EMArevQ", "TrendCont"}
HOLD = sorted(OI.HOLDOUT)                                  # AUDCAD.dk, AUDNZD.dk, NZDCAD.dk
INSAMPLE = sorted(C.BLOC)                                  # AUDUSD.dk, NZDUSD.dk, USDCAD.dk
REPORT = os.path.join(A.STUDY, "cbloc_holdout_report.txt")
TRADES = os.path.join(A.STUDY, "cbloc_holdout_trades.pkl")
CHECK = os.path.join(OI.HOLDOUT_AA, "cbloc_holdout_insample_check.txt")
V52_REF = os.path.join(A.STUDY, "cbloc_v52_filters_report.txt")     # in-sample v5.2 reference (pipeline/cbloc_v52_filters.py)
PIPS = (0.5, 1.0, 1.5, 2.0)
PERIODS = (("2012-24", lambda z: z[1][:4] < "2025"), ("2025-26", lambda z: z[1][:4] >= "2025"))
SIDES = (("all  ", lambda z: True), ("long ", lambda z: z[2]), ("short", lambda z: not z[2]))


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- holdout registration / readiness
def register_holdout():
    """adds the three symbols' engine inputs (M1 file stem, tick, ref price) to the shared dicts of THIS process only."""
    inp = OI.load_inputs(required=True, which="holdout")
    for s in HOLD:
        e = inp.get(s, {})
        miss = [k for k in ("src", "tick", "ref_price") if k not in e]
        if miss: sys.exit(f"REFUSED: {s} has no {miss} in {OI.HOLDOUT_INPUTS} (run: python3 pipeline/oos_build_inputs.py build --holdout)")
        k = OI.root(s) + ".sim"
        assert s not in A.SRC and s not in A.TICK and k not in Q.REFS, f"{s} already registered - refusing to shadow it"
        A.SRC[s] = e["src"]; A.TICK[s] = e["tick"]; Q.REFS[k] = e["ref_price"]
    return inp


def readiness():
    """-> list of problems (empty = ready). Existence / identity checks only."""
    bad = []
    inp = OI.load_inputs(required=False, which="holdout")
    prof = A.spread_profile()
    for s in HOLD:
        e = inp.get(s, {}); j = OI.journal_for(s)
        for why, ok in (("no holdout_inputs.json entry (oos_build_inputs.py build --holdout)", "src" in e),
                        ("no ref price / tick", "ref_price" in e and "tick" in e),
                        ("M1 CSV missing", "m1_file" in e and os.path.exists(os.path.join(OI.FLEET, e.get("m1_file", "?")))),
                        ("no spread profile rows", OI.root(s) + ".sim" in prof),
                        ("no H4 file data/study/h4/" + s + ".csv", os.path.exists(os.path.join(OI.H4DIR, s + ".csv"))),
                        (f"journal in data/study/holdout_aa: {j or 'missing'}", bool(j) and not str(j).startswith("AMBIGUOUS"))):
            if not ok: bad.append(f"{s}: {why}")
    return bad


def build_caches(verify=True):
    prof = A.spread_profile(); out = []
    for s in HOLD:
        t = time.time(); did = M.build(s, prof, Q.REFS)
        line = f"{s}: m1 cache {'built' if did else 'up to date'} ({time.time() - t:.0f}s)"
        if verify:
            b = M.verify(s, prof, Q.REFS); line += " | cache vs CSV-parsed Sym: " + ("IDENTICAL" if not b else "MISMATCH " + str(b))
            if b: sys.exit("REFUSED: " + line)
        print(line, flush=True); out.append(line)
    return out


# ------------------------------------------------------------------------------------------------- population + engine (shared path)
def population(syms):
    """aa_rows unchanged over each symbol's single AA journal (OI.journal_for: holdout_aa for the holdout, oos_aa for the nine), the
    start / SIGNAL_END filter of oos_queue_study.pop_new, symbol-major time order (combo_build.population's sort)."""
    js = {s: OI.journal_for(s) for s in syms}
    for s, j in js.items():
        if not j or j.startswith("AMBIGUOUS"): sys.exit(f"REFUSED: {s} journal {j}")
    dirs = {os.path.dirname(j) for j in js.values()}
    assert len(dirs) == 1, dirs
    old = (S025.AAJ, S025.AA_FILES)
    try:
        S025.AAJ, S025.AA_FILES = dirs.pop(), [os.path.basename(js[s]) for s in syms]
        rows = S025.aa_rows(STRATS, set(syms))
    finally:
        S025.AAJ, S025.AA_FILES = old
    rows = [x for x in rows if OI._REG[x["sym"]][1] <= x["t"][:10] and x["t"] < OI.SIGNAL_END]
    return sorted(rows, key=lambda x: (x["sym"], x["t"])), js


def do_symbol(arg):
    """cbloc_timestop_study.do_symbol with the rows passed in and only the three pre-registered variants."""
    sym, rows = arg
    S = CB.sym_obj(sym); out = []
    for x in rows:
        u = A.setup(S, x, "SCALED")
        if not u: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], "SCALED", u["scale"])
        e0 = float(F["eo"][0]); bankf = BANKF.get(x["strat"], 0.5); r = {}
        for name, ts, rt in VARIANTS:
            v, units, why = C.run_h(F, e0, e0, u["bank_R"], u["tp2_R"], bankf, A.F0, u["k_add"], u["up"], ts, rt)
            r[name] = v; r[name + ":why"] = why; r[name + ":units"] = units
        r["v5.2"], r["v5.2:units"] = V52.trade(S, x, u, F)                   # addendum 2026-10-04
        r["stop_pips"] = u["R"] / (10 * A.TICK[sym])
        out.append((sym, x["t"], x["up"], r, x["strat"]))
    return out


def evaluate(syms):
    pop, js = population(syms)
    with mp.get_context("fork").Pool(min(3, len(syms))) as pool:
        res = [z for rs in pool.map(do_symbol, [(s, [x for x in pop if x["sym"] == s]) for s in sorted(syms)]) for z in rs]
    return res, pop, js


# ------------------------------------------------------------------------------------------------- report blocks (cbloc format)
def block(L, title, zs, minn=10):
    """cbloc_timestop_study.main's block() for the three variants, same format; returns the numbers used."""
    if len(zs) < minn:
        L.append(f"{title}  n={len(zs)}  (fewer than {minn} trades - not shown)"); return None
    b = [z[3]["live"] for z in zs]
    L.append(f"{title}  n={len(zs)}  live {st.mean(b):+.4f}R/trade  total {sum(b):+.1f}  DD {maxdd(b):.1f}")
    out = {"n": len(zs), "live": {"mean": st.mean(b), "tot": sum(b), "dd": maxdd(b)}}
    for name, ts, rt in VARIANTS[1:]:
        v = [z[3][name] for z in zs]; why = [z[3][name + ':why'] for z in zs]
        tm = sum(1 for w in why if w == "time")
        L.append(f"   {name:8} {st.mean(v):+.4f}R/trade (vs live {st.mean(v) - st.mean(b):+.4f}, t {paired_t(b, v):+.2f})  total {sum(v):+7.1f}  DD {maxdd(v):6.1f}"
                 + (f"  | time-stopped {tm} ({100 * tm / len(zs):.0f}%)" if ts else ""))
        out[name] = {"mean": st.mean(v), "inc": st.mean(v) - st.mean(b), "t": paired_t(b, v), "tot": sum(v), "dd": maxdd(v), "time": tm}
    return out


def tsort(zs):
    return sorted(zs, key=lambda z: z[1])                  # stable: ties keep the symbol-major order (as cbloc_timestop_study)


def verdict(zs):
    b = [z[3]["live"] for z in zs]; tl = [z[3]["T+L1.0"] for z in zs]; t = [z[3]["T"] for z in zs]
    inc = st.mean(tl) - st.mean(b); tt = paired_t(b, tl); ddb, ddv = maxdd(b), maxdd(tl); inc_t = st.mean(t) - st.mean(b)
    c1, c2, c3 = inc > 0, tt >= 1.5, ddv <= ddb
    return {"primary": c1 and c2 and c3, "secondary": inc_t > 0, "lines": [
        f"PRIMARY   T+L1.0 vs live, pooled all years, n={len(zs)}: "
        f"increment {inc:+.4f}R/trade > 0 [{'yes' if c1 else 'NO'}]  AND  paired t {tt:+.2f} >= 1.5 [{'yes' if c2 else 'NO'}]  AND  "
        f"worst DD {ddv:.1f} <= live {ddb:.1f} [{'yes' if c3 else 'NO'}]  ->  {'PASS' if c1 and c2 and c3 else 'FAIL'}",
        f"SECONDARY T vs live, pooled all years, n={len(zs)}: increment {inc_t:+.4f}R/trade > 0  ->  {'PASS' if inc_t > 0 else 'FAIL'}"]}


def tables(res, syms, L):
    """pooled + per symbol; returns {(scope, period, side): numbers} and runs the additivity checks."""
    got = {}
    scopes = [("POOLED", lambda z: True)] + [(s, (lambda s_: lambda z: z[0] == s_)(s)) for s in sorted(syms)]
    for scope, kf in scopes:
        L.append(f"\n===== {scope if scope != 'POOLED' else 'POOLED (' + ', '.join(x.split('.')[0] for x in sorted(syms)) + ')'} =====")
        for per, pf in (("all years", lambda z: True),) + PERIODS:
            for side, sf in SIDES:
                got[(scope, per, side.strip())] = block(L, f"{per} {side}", tsort([z for z in res if kf(z) and pf(z) and sf(z)]), minn=1 if scope == "POOLED" else 10)
            L.append("")
    # additivity: pooled all-years n / totals = sum over symbols = 2012-24 + 2025-26
    problems = []
    P = lambda *k: got.get(k)
    for side in ("all", "long", "short"):
        tot = P("POOLED", "all years", side)
        if not tot: continue
        parts_p = [P("POOLED", per, side) for per, _ in PERIODS]
        parts_s = [P(s, "all years", side) for s in sorted(syms)]
        for name in ("live",) + tuple(v[0] for v in VARIANTS[1:]):
            for parts, what in ((parts_p, "2012-24 + 2025-26"), (parts_s, "sum of symbols")):
                if any(p is None for p in parts): continue
                if sum(p["n"] for p in parts) != tot["n"] or abs(sum(p[name]["tot"] for p in parts) - tot[name]["tot"]) > 1e-9:
                    problems.append(f"{side} {name}: pooled all-years != {what}")
    L.append("additivity check (pooled all-years n and totals = 2012-24 + 2025-26 = sum of symbols, every variant, all/long/short): "
             + ("OK" if not problems else "FAILED: " + "; ".join(problems)))
    return got, problems


# ------------------------------------------------------------------------------------------------- v5.2 (addendum 2026-10-04)
def t1(v):
    if len(v) < 3: return float("nan")
    sd = st.stdev(v); return st.mean(v) / (sd / len(v) ** 0.5) if sd > 0 else float("nan")


def v52_block(L, title, zs, minn=10):
    if len(zs) < minn:
        L.append(f"{title}  n={len(zs)}  (fewer than {minn} trades - not shown)"); return None
    b = [z[3]["live"] for z in zs]; v = [z[3]["v5.2"] for z in zs]
    L.append(f"{title}  n={len(zs)}  v5.2 {st.mean(v):+.4f}R/trade (t vs 0 {t1(v):+.2f})  total {sum(v):+7.1f}  DD {maxdd(v):6.1f}  | live {st.mean(b):+.4f} "
             f"total {sum(b):+7.1f} DD {maxdd(b):6.1f} | v5.2 vs live {st.mean(v) - st.mean(b):+.4f} (t {paired_t(b, v):+.2f})")
    return {"n": len(zs), "mean": st.mean(v), "tot": sum(v), "dd": maxdd(v), "live_tot": sum(b)}


def v52_verdict(zs):
    b = [z[3]["live"] for z in zs]; v = [z[3]["v5.2"] for z in zs]
    m, tt, inc, ddv, ddb = st.mean(v), t1(v), st.mean(v) - st.mean(b), maxdd(v), maxdd(b)
    p1, p2, s1, s2 = m > 0, tt >= 1.5, inc > 0, ddv <= ddb
    return {"primary": p1 and p2, "secondary": s1 and s2, "lines": [
        f"PRIMARY   v5.2, pooled all years, n={len(zs)}: mean {m:+.4f}R/trade > 0 [{'yes' if p1 else 'NO'}]  AND  one-sample t {tt:+.2f} >= 1.5 "
        f"[{'yes' if p2 else 'NO'}]  ->  {'PASS' if p1 and p2 else 'FAIL'}",
        f"SECONDARY v5.2 vs live, pooled all years: increment {inc:+.4f}R/trade > 0 [{'yes' if s1 else 'NO'}]  AND  worst DD {ddv:.1f} <= live "
        f"{ddb:.1f} [{'yes' if s2 else 'NO'}]  ->  {'PASS' if s1 and s2 else 'FAIL'}"]}


def v52_tables(res, syms, L):
    got = {}
    scopes = [("POOLED", lambda z: True)] + [(s, (lambda s_: lambda z: z[0] == s_)(s)) for s in sorted(syms)]
    for scope, kf in scopes:
        L.append(f"\n----- v5.2 | {scope if scope != 'POOLED' else 'POOLED (' + ', '.join(x.split('.')[0] for x in sorted(syms)) + ')'} -----")
        for per, pf in (("all years", lambda z: True),) + PERIODS:
            for side, sf in SIDES:
                got[(scope, per, side.strip())] = v52_block(L, f"{per} {side}", tsort([z for z in res if kf(z) and pf(z) and sf(z)]), minn=1 if scope == "POOLED" else 10)
    L.append("\n----- v5.2 | POOLED by detector, all years -----")
    for d in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ"):
        v52_block(L, f"{d:10}", tsort([z for z in res if z[4] == d]), minn=10)
    problems = []
    for side in ("all", "long", "short"):
        tot = got.get(("POOLED", "all years", side))
        for parts, what in (([got.get(("POOLED", per, side)) for per, _ in PERIODS], "2012-24 + 2025-26"),
                            ([got.get((s, "all years", side)) for s in sorted(syms)], "sum of symbols")):
            if tot is None or any(p is None for p in parts): continue
            if sum(p["n"] for p in parts) != tot["n"] or abs(sum(p["tot"] for p in parts) - tot["tot"]) > 1e-9:
                problems.append(f"v5.2 {side}: pooled all-years != {what}")
    L.append("v5.2 additivity check: " + ("OK" if not problems else "FAILED: " + "; ".join(problems)))
    zs = tsort(res); sp = [z[3]["stop_pips"] for z in zs]
    L.append(f"\n----- SPREAD SENSITIVITY (information, not a bar): extra round-trip spread per unit traded on top of the model's M1-bar spread; "
             f"stop distance median {st.median(sp):.1f} pips, 10th pct {sorted(sp)[len(sp) // 10]:.1f} -----")
    for pp in (0.0,) + PIPS:
        v = [z[3]["v5.2"] - z[3]["v5.2:units"] * pp / z[3]["stop_pips"] for z in zs]
        b = [z[3]["live"] - z[3]["live:units"] * pp / z[3]["stop_pips"] for z in zs]
        per_sym = " ".join(f"{s.split('.')[0]} {st.mean([z[3]['v5.2'] - z[3]['v5.2:units'] * pp / z[3]['stop_pips'] for z in zs if z[0] == s]):+.4f}" for s in sorted(syms))
        L.append(f"  +{pp:.1f} pip: v5.2 {st.mean(v):+.4f}R/trade (t {t1(v):+.2f})  total {sum(v):+7.1f}  DD {maxdd(v):6.1f} | live {st.mean(b):+.4f} DD {maxdd(b):6.1f} | {per_sym}")
    return got, problems


# ------------------------------------------------------------------------------------------------- in-sample validation
def insample_check():
    T0 = time.time()
    res, pop, js = evaluate(INSAMPLE)
    L = [f"cbloc_holdout_look.py --insample-check  RUN {time.strftime('%Y-%m-%d %H:%M:%S')}: the holdout code path (aa_rows loader on "
         f"each symbol's journal, start/SIGNAL_END filter, symbol-major order, CachedSym, run_h live/T/T+L1.0, cbloc block format, "
         f"verdict) on the IN-SAMPLE commodity bloc {', '.join(INSAMPLE)} from data/study/oos_aa; {len(res)} trades of {len(pop)} rows, "
         f"{time.time() - T0:.0f}s",
         "journals: " + ", ".join(f"{os.path.basename(j)} sha {sha256(j)[:16]}" for j in js.values()),
         f"reference: {os.path.relpath(C.REPORT, ROOT)} sha {sha256(C.REPORT)[:16]}, section COMMODITY BLOC, lines live / T / T+L1.0", ""]
    ref = open(C.REPORT).read().split("===== COMMODITY BLOC")[1].split("===== CONTROL")[0].splitlines()
    mine = []
    for per, pf in PERIODS:
        for side, sf in SIDES:
            block(mine, f"{per} {side}", tsort([z for z in res if pf(z) and sf(z)]))
    for s in INSAMPLE:
        block(mine, f"  {s}", tsort([z for z in res if z[0] == s]))
    is_hdr = lambda l: "  n=" in l and (l[:4].isdigit() or (l.startswith("  ") and not l.startswith("   ")))
    want = [l for l in ref if is_hdr(l) or l.startswith("   T        ") or l.startswith("   T+L1.0   ")]
    ok = mine == want
    L.append(f"lines compared: {len(want)} reference vs {len(mine)} reproduced -> {'IDENTICAL' if ok else 'MISMATCH'}")
    for a, b in zip(want, mine):
        L.append(("   = " if a == b else "   X ref " + a + "\n     got ") + b)
    for extra in mine[len(want):]: L.append("   + got " + extra)
    for extra in want[len(mine):]: L.append("   - ref " + extra)
    # the look's own tables + additivity + verdict on the in-sample population (exercises the verdict path)
    L.append("\nthe look's report body, run on the in-sample bloc (code-path exercise; not the holdout):")
    got, problems = tables(res, INSAMPLE, L)
    ref_sym = {s: re.search(r"n=(\d+)\s+live\s+\S+\s+total\s+(\S+)", next(l for l in ref if l.startswith(f"  {s}  n="))).groups() for s in INSAMPLE}
    n_sum = sum(int(v[0]) for v in ref_sym.values()); t_sum = sum(float(v[1]) for v in ref_sym.values())
    pa = got[("POOLED", "all years", "all")]
    c_ref = pa["n"] == n_sum and abs(pa["live"]["tot"] - t_sum) <= 0.05 * len(INSAMPLE) + 1e-9
    L.append(f"pooled all-years vs the reference's three by-symbol all-years lines: n {pa['n']} vs {n_sum}, live total "
             f"{pa['live']['tot']:+.2f} vs {t_sum:+.1f} (sum of rounded lines) -> {'OK' if c_ref else 'MISMATCH'}")
    v = verdict(tsort(res)); L += ["", "verdict code path on the in-sample bloc (NOT the holdout result):"] + ["   " + x for x in v["lines"]]
    # v5.2 (addendum): the look's v5.2 path must reproduce the in-sample reference (cbloc_v52_filters.py) - mean and total exactly
    m = re.search(r"^v5\.2: ([+-]\d+\.\d{4})R/trade\s+total ([+-]\d+\.\d)\s+DD (\d+\.\d)", open(V52_REF).read(), re.M)
    zs = tsort(res); vv = [z[3]["v5.2"] for z in zs]
    mine52 = (f"{st.mean(vv):+.4f}", f"{sum(vv):+.1f}", maxdd(vv))
    c52 = bool(m) and mine52[0] == m.group(1) and mine52[1] == m.group(2) and abs(mine52[2] - float(m.group(3))) <= 0.5 and len(vv) == 1735
    L += ["", f"v5.2 path vs {os.path.relpath(V52_REF, ROOT)} (sha {sha256(V52_REF)[:16]}): n {len(vv)} (ref 1735), mean {mine52[0]} (ref "
          f"{m.group(1) if m else '?'}), total {mine52[1]} (ref {m.group(2) if m else '?'}), DD {mine52[2]:.1f} (ref {m.group(3) if m else '?'}, "
          f"+/-0.5 for tie order) -> {'OK' if c52 else 'MISMATCH'}"]
    L.append("\nthe look's v5.2 report body, run on the in-sample bloc (code-path exercise; not the holdout):")
    g52, p52 = v52_tables(res, INSAMPLE, L)
    v2 = v52_verdict(zs); L += ["v5.2 verdict code path on the in-sample bloc (NOT the holdout result):"] + ["   " + x for x in v2["lines"]]
    allok = ok and not problems and c_ref and c52 and not p52
    L.append(f"\nIN-SAMPLE CHECK: {'PASS' if allok else 'FAIL'}")
    open(CHECK, "w").write("\n".join(L) + "\n"); print("\n".join(L)); print(f"-> {CHECK}")
    return allok


# ------------------------------------------------------------------------------------------------- THE LOOK
def look():
    for p in (REPORT, TRADES):
        if os.path.exists(p): sys.exit(f"REFUSED: {p} exists - the one look has been taken (sha256 {sha256(p)}). No re-run.")
    bad = readiness()
    if bad: sys.exit("REFUSED - not ready:\n  " + "\n  ".join(bad))
    inp = register_holdout()
    cache_lines = build_caches(verify=True)
    T0 = time.time()
    res, pop, js = evaluate(HOLD)
    L = [__doc__.split("\n")[0], f"THE LOOK  RUN {time.strftime('%Y-%m-%d %H:%M:%S')}: {len(res)} trades replayed of {len(pop)} population rows "
         f"({time.time() - T0:.0f}s); engine cbloc_timestop_study.run_h (sha {sha256(C.__file__)[:16]}), SCALED; ask_side_study sha "
         f"{sha256(A.__file__)[:16]}; this script sha {sha256(os.path.abspath(__file__))[:16]}",
         "Pre-registration: docs/coach_queue.md 'PRE-REGISTRATION - commodity-bloc time stop'. Bars on the POOLED ALL-YEARS block.", "INPUTS:",
         "  frozen inputs sha256[:16]: " + ", ".join(f"{os.path.basename(p)} {sha256(p)[:16]}" for p in (
             os.path.join(A.STUDY, "spread_profile.csv"), os.path.join(A.STUDY, "universe_cost_filter.csv"), OI.HOLDOUT_INPUTS))]
    for s in HOLD:
        h = os.path.join(OI.H4DIR, s + ".csv"); e = inp[s]
        L.append(f"  {s:10} journal {os.path.basename(js[s])} sha {sha256(js[s])[:16]} | H4 sha {sha256(h)[:16]} | M1 {e['m1_file']} "
                 f"{e['m1_first']}..{e['m1_last']} | ref {e['ref_price']} | tick {e['tick']} | class {e['class']} | group {e.get('group')}")
    L += ["  " + x for x in cache_lines]
    L.append("trades replayed by symbol x detector (SweepMSS/DeepFib/EMArevQ/TrendCont): " + "; ".join(
        f"{s.split('.')[0]} " + "/".join(str(sum(1 for z in res if z[0] == s and z[4] == d)) for d in ("SweepMSS", "DeepFib", "EMArevQ", "TrendCont"))
        for s in HOLD) + f"; rows without M1 coverage: {len(pop) - len(res)}")
    v = verdict(tsort(res))
    L += ["", "=" * 30 + " VERDICT (pre-registered bars) " + "=" * 30] + v["lines"] + ["=" * 91]
    got, problems = tables(res, HOLD, L)
    v2 = v52_verdict(tsort(res))
    L += ["", "=" * 24 + " ADDENDUM 2026-10-04: v5.2 VERDICT (pre-registered bars, model cost) " + "=" * 24] + v2["lines"] + ["=" * 117]
    g52, p52 = v52_tables(res, HOLD, L)
    with open(TRADES, "xb") as f: pickle.dump(res, f)
    with open(REPORT, "x") as f: f.write("\n".join(L) + "\n")
    print("\n".join(L)); print(f"\n-> {REPORT}  sha256 {sha256(REPORT)}\n-> {TRADES}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--look", action="store_true", help="THE ONE LOOK on AUDNZD/AUDCAD/NZDCAD")
    g.add_argument("--insample-check", action="store_true", help="validate the code path on AUDUSD/NZDUSD/USDCAD")
    g.add_argument("--build-cache", action="store_true", help="M1 caches for the three holdout symbols (prices only)")
    a = ap.parse_args()
    if a.insample_check: sys.exit(0 if insample_check() else 1)
    if a.build_cache:
        bad = [b for b in readiness() if "journal" not in b and "H4" not in b]
        if bad: sys.exit("REFUSED - not ready:\n  " + "\n  ".join(bad))
        register_holdout(); build_caches(verify=True); return
    if a.look: look(); return
    bad = readiness()
    done = [p for p in (REPORT, TRADES) if os.path.exists(p)]
    print("cbloc_holdout_look.py: computes nothing without --look.")
    if done: print("THE LOOK HAS BEEN TAKEN: " + ", ".join(done))
    print("READY for: python3 pipeline/cbloc_holdout_look.py --look" if not bad and not done else "not ready:\n  " + "\n  ".join(bad))
    sys.exit(2)


if __name__ == "__main__":
    main()
