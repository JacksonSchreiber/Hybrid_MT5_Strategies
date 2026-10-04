#!/usr/bin/env python3
"""oos_queue_study.py - the ONE LOOK: coach-queue rules A, B, I and the package A+B+I on the 10 new symbols (pre-registration and
coach bars: docs/coach_queue.md, "Pre-registration ...", "Coach ruling 2026-10-03" and "(second)"). Rules FROZEN exactly as
pipeline/combo3_study.py (combo3_study.trade is called unchanged): A GATE_LVL 1.0 when the trial average < 0 at the bar-6 add; B
CUT_BAR 0.5 at CHK 12, shorts; I improving longs, ADD_F 0.5. Engine pipeline/ask_side_study, modes SCALED (main, the bars) and BID.

Population (new symbols): ALL FOUR detectors (SweepMSS, DeepFib, EMArevQ, TrendCont) from the AA journals in data/study/oos_aa
(pipeline/oos_mt5_stage.sh), read by exit_s025_study.aa_rows unchanged (approved rows, signal bar found in the .dk H4 file, entry/sl
parse), signals from NEW[sym] start (US30: 2013-01-01) and before oos_build_inputs.SIGNAL_END. DIFFERENCE from the original seven:
their TrendCont came from the standalone TrendCont record (exit_mgmt_study.REC, an earlier detector build); here TrendCont comes
from the same four-detector AA run as the others. `--symbols <orig> --aa-trendcont` measures what that swap does on the seven.

Frozen readings (decided before the look; every sub-condition is printed next to its verdict; all on SCALED, pooled, full position):
  support (pre-reg)  2012-24 increment > 0 AND 2012-24 worst DD <= base, for each rule and the package
  B   2012-24 increment >= 0 AND 2012-24 worst DD <= base                        (2025-26 DD printed, not part of the bar)
  A   increment >= -0.005R in 2012-24 AND in 2025-26, AND worst DD <= base in 2012-24 AND in 2025-26
      contingency: FX-short cell (2012-24) increment < 0 -> A ships LONGS ONLY  (2025-26 cell printed)
  I   (alone vs base) 2012-24 increment > 0 AND paired t >= 1.5 AND FX-long cell (2012-24) increment > 0 -> pass (ships only with A
      and B on); otherwise I is CLOSED (never narrowed after the fact)

Usage
  python3 pipeline/oos_queue_study.py --symbols EURUSD.dk,GBPUSD.dk,US100.dk,US500.dk,USDJPY.dk,USOIL.dk,XAUUSD.dk
        validation on the original seven with THEIR population loader: per-trade results must equal data/study/combo3_trades.pkl
        exactly and the tables must equal combo3_report.txt / combo3_class_report.txt to the printed digit
        -> data/study/oos_queue_validate_report.txt
  python3 pipeline/oos_queue_study.py --symbols <the seven> --aa-trendcont       (bridge: TrendCont from the pinned AA journals)
        -> data/study/oos_queue_bridge_report.txt
  python3 pipeline/oos_queue_study.py --look [--exclude US30]
        THE LOOK on the new symbols -> data/study/oos_queue_report.txt (+ oos_queue_trades.pkl). Refuses without --look, refuses while
        any registered symbol lacks its journal / H4 / inputs / drag unless that symbol is named in --exclude (recorded in the
        header; an excluded symbol can only ever be reported later as a separate addendum, never re-pooled), and refuses to overwrite
        an existing report (a re-run needs --relook "<reason>", keeps the same exclusions, and writes a separately labelled file).
  python3 pipeline/oos_queue_study.py --look --addendum US30
        a symbol excluded from the first look, evaluated ALONE -> data/study/oos_queue_addendum_US30.txt (never re-pooled)
"""
from __future__ import annotations
import argparse, hashlib, json, os, pickle, re, statistics as st, sys, time
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline import combo3_study as K                                     # noqa: E402
from pipeline import exit_s025_study as S025                               # noqa: E402
from pipeline import oos_build_inputs as OI                                # noqa: E402
from pipeline.combo3_class_split import cls                                # noqa: E402
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t            # noqa: E402

MODES = K.MODES
NAMES = ["base", "A", "B", "I", "A+B+I"]
RULES = ["A", "B", "I", "A+B+I"]
PER = (("2012-24", lambda t: t[:4] < "2025"), ("2025-26", lambda t: t[:4] >= "2025"))
OUT = {"validate": "oos_queue_validate_report.txt", "bridge": "oos_queue_bridge_report.txt", "look": "oos_queue_report.txt"}

# ---- register the new symbols' engine inputs at import time (pool workers see them under any start method). Only ADDS keys to the
# shared dicts in this process; the files that define them are not touched, the original seven are unaffected.
INP = OI.load_inputs(required=False)
REFS = A.ref_prices()
for _s, _e in INP.items():
    if "src" in _e:
        assert _s not in A.SRC and _s not in A.TICK and OI.root(_s) + ".sim" not in REFS, f"{_s} already registered - refusing to shadow it"
        A.SRC[_s] = _e["src"]; A.TICK[_s] = _e["tick"]
        if "ref_price" in _e: REFS[OI.root(_s) + ".sim"] = _e["ref_price"]


def do_symbol(arg):
    """combo3_study.do_symbol with the rows passed in (same order) and the ref prices above."""
    sym, rows = arg
    S = A.Sym(sym, A.spread_profile(), REFS); res = []
    for x in rows:
        r = {md: K.trade(S, x, md) for md in MODES}
        if any(v is None for v in r.values()): continue
        res.append((x["sym"], x["t"], x["strat"], x["up"], r))
    return res


def evaluate(ALL):
    ALL = sorted(ALL, key=lambda x: x["t"])                                 # combo3 order: rows by time, symbols sorted, stable sort
    syms = sorted({x["sym"] for x in ALL})
    with Pool(2) as pool:
        res = pool.map(do_symbol, [(s, [x for x in ALL if x["sym"] == s]) for s in syms])
    return sorted([z for rs in res for z in rs], key=lambda z: z[1])


# ---------------------------------------------------------------------------------------------------------- populations
def pop_original(syms, aa_trendcont=False):
    """combo3_study's population, restricted to syms. aa_trendcont: TrendCont from the pinned AA journals instead of REC."""
    tc = ([dict(x, strat="TrendCont") for x in load_rows()] if not aa_trendcont else
          [x for x in S025.aa_rows({"TrendCont"}) if x["sym"] != "BTCUSD.dk"])
    ALL = sorted(tc + [x for x in S025.aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"], key=lambda x: x["t"])
    return [x for x in ALL if x["sym"] in syms]


def check_new(syms):
    """-> {sym: problem} for every new symbol that is not ready (journal, H4, inputs, profile, drag)."""
    drag = json.load(open(OI.DRAGF)) if os.path.exists(OI.DRAGF) else {}
    prof = A.spread_profile(); bad = {}
    for s in syms:
        e = INP.get(s, {}); j = OI.journal_for(s)
        why = [w for w, ok in (("no M1 / inputs entry (oos_build_inputs.py build)", "src" in e and os.path.exists(os.path.join(OI.FLEET, e.get("m1_file", "?")))),
                               ("no ref price", "ref_price" in e), ("no spread profile", OI.root(s) + ".sim" in prof),
                               ("no H4 file data/study/h4/" + s + ".csv", os.path.exists(os.path.join(OI.H4DIR, s + ".csv"))),
                               (f"journal: {j or 'missing'}", bool(j) and not str(j).startswith("AMBIGUOUS")),
                               ("no drag (oos_build_inputs.py drag)", OI.root(s) in drag)) if not ok]
        if why: bad[s] = "; ".join(why)
    return bad, drag


def pop_new(syms, drag):
    """aa_rows unchanged over the oos_aa journals (module globals swapped for the call, then restored)."""
    files = [os.path.basename(OI.journal_for(s)) for s in syms]
    old = (S025.AAJ, S025.AA_FILES)
    try:
        S025.AAJ, S025.AA_FILES = OI.OOS_AA, files
        rows = S025.aa_rows({"SweepMSS", "DeepFib", "EMArevQ", "TrendCont"}, set(syms))
    finally:
        S025.AAJ, S025.AA_FILES = old
    out = []
    for x in rows:
        if not (OI.NEW[x["sym"]][1] <= x["t"][:10] and x["t"] < OI.SIGNAL_END): continue
        out.append(dict(x, cost=drag[OI.root(x["sym"])]["drag_scaled"]))     # no silent 0.007 default
    return out


# ---------------------------------------------------------------------------------------------------------- report
def stats(zs, md, name):
    b = [z[4][md]["base"] for z in zs]; v = [z[4][md][name] for z in zs]
    return {"n": len(zs), "mean": st.mean(v), "inc": st.mean(v) - st.mean(b), "t": paired_t(b, v) if name != "base" else 0.0,
            "dd": maxdd(v), "dd_base": maxdd(b), "tot": sum(v)}


def pooled_tables(res, L, modes=MODES, names=NAMES):
    for md in modes:
        for per, f in PER:
            zs = [z for z in res if f(z[1])]
            if not zs: continue
            L.append(f"\n[{md}] {per}: n={len(zs)}")
            L.append(f"  {'rules':8} {'R/trade':>8} {'vs base':>8} {'t':>6} {'worst DD':>8} {'total R':>8} {'R/DD':>6} | {'longs':>8} {'shorts':>8}")
            for name in names:
                s = stats(zs, md, name)
                lo = [z[4][md][name] for z in zs if z[3]]; sh = [z[4][md][name] for z in zs if not z[3]]
                L.append(f"  {name:8} {s['mean']:+8.4f} {s['inc']:+8.4f} {s['t']:+6.2f} {s['dd']:8.1f} {s['tot']:8.1f} {s['tot'] / s['dd'] if s['dd'] else float('nan'):6.1f} | "
                         f"{st.mean(lo) if lo else float('nan'):+8.4f} {st.mean(sh) if sh else float('nan'):+8.4f}")


def cell_lines(res, L, key, groups, md="SCALED", names=("A", "B", "I", "A+B", "A+B+I"), minn=5):
    for per, f in PER:
        for g in groups:
            for side in ("long", "short"):
                zs = [z for z in res if f(z[1]) and (g == "all" or key(z) == g) and z[3] == (side == "long")]
                if len(zs) < minn: continue
                b = [z[4][md]["base"] for z in zs]
                cells = [f"{n} {st.mean([z[4][md][n] for z in zs]) - st.mean(b):+.4f} (t {paired_t(b, [z[4][md][n] for z in zs]):+.1f}, "
                         f"DD {maxdd([z[4][md][n] for z in zs]):.1f})" for n in names]
                L.append(f"{per} {g:9} {side:5} n={len(zs):4d} base {st.mean(b):+.4f} DD {maxdd(b):5.1f} | " + " | ".join(cells))
        L.append("")


def bars(res, L):
    md = "SCALED"
    P = {per: [z for z in res if f(z[1])] for per, f in PER}
    S = {(n, per): stats(P[per], md, n) for n in NAMES for per in P if P[per]}
    def cell(per, c, side, n):
        zs = [z for z in P[per] if cls(z[0]) == c and z[3] == (side == "long")]
        return (stats(zs, md, n), len(zs)) if zs else (None, 0)
    ok = lambda b: "PASS" if b else "FAIL"
    L.append("\n=== COACH BARS (SCALED, pooled; frozen readings in the script docstring) ===")
    L.append("support (pre-registration): 2012-24 increment > 0 AND 2012-24 worst DD not above base")
    for n in RULES:
        s = S[(n, "2012-24")]
        L.append(f"  {n:6} inc {s['inc']:+.4f} (t {s['t']:+.2f})  DD {s['dd']:.1f} vs base {s['dd_base']:.1f}  -> "
                 f"{'SUPPORT' if s['inc'] > 0 and s['dd'] <= s['dd_base'] else 'NO SUPPORT'}")
    b1, b2 = S[("B", "2012-24")], S.get(("B", "2025-26"))
    passB = b1["inc"] >= 0 and b1["dd"] <= b1["dd_base"]
    L.append(f"B  : 2012-24 inc {b1['inc']:+.4f} >= 0 [{ok(b1['inc'] >= 0)}]; 2012-24 DD {b1['dd']:.1f} <= base {b1['dd_base']:.1f} [{ok(b1['dd'] <= b1['dd_base'])}]"
             + (f"; (info) 2025-26 inc {b2['inc']:+.4f}, DD {b2['dd']:.1f} vs {b2['dd_base']:.1f}" if b2 else "") + f"  => B {ok(passB)}")
    a = {per: S.get(("A", per)) for per in P}
    conds = []
    for per in P:
        if a[per]: conds += [(f"{per} inc {a[per]['inc']:+.4f} >= -0.005", a[per]["inc"] >= -0.005),
                             (f"{per} DD {a[per]['dd']:.1f} <= base {a[per]['dd_base']:.1f}", a[per]["dd"] <= a[per]["dd_base"])]
    passA = all(c for _, c in conds) and len(conds) == 4
    fx, nfx = cell("2012-24", "FX", "short", "A"); fx2, nfx2 = cell("2025-26", "FX", "short", "A") if P["2025-26"] else (None, 0)
    L.append("A  : " + "; ".join(f"{d} [{ok(c)}]" for d, c in conds) + f"  => A {ok(passA)}")
    if fx:
        L.append(f"     contingency: FX-short cell 2012-24 A inc {fx['inc']:+.4f} (t {fx['t']:+.2f}, n={nfx})"
                 + (f"; (info) 2025-26 {fx2['inc']:+.4f} (n={nfx2})" if fx2 else "")
                 + f"  => {'NEGATIVE: A ships LONGS ONLY' if fx['inc'] < 0 else 'not negative: A ships on both sides'}" + ("" if passA else " (if A ships at all)"))
    i1 = S[("I", "2012-24")]; fl, nfl = cell("2012-24", "FX", "long", "I")
    ci = [(f"2012-24 inc {i1['inc']:+.4f} > 0", i1["inc"] > 0), (f"t {i1['t']:+.2f} >= 1.5", i1["t"] >= 1.5),
          (f"FX-long cell 2012-24 inc {fl['inc'] if fl else float('nan'):+.4f} > 0 (n={nfl})", bool(fl) and fl["inc"] > 0)]
    passI = all(c for _, c in ci)
    L.append("I  : " + "; ".join(f"{d} [{ok(c)}]" for d, c in ci) + f"  => I {'PASS (ships only with A and B on)' if passI else 'FAIL -> I is CLOSED'}")
    s = S[("A+B+I", "2012-24")]
    L.append(f"A+B+I (package, support only): 2012-24 inc {s['inc']:+.4f} (t {s['t']:+.2f}), DD {s['dd']:.1f} vs base {s['dd_base']:.1f}")


def provenance(syms, L):
    L.append("  frozen inputs sha256[:16]: " + ", ".join(f"{os.path.basename(p)} {OI.sha(p)}" for p in (
        os.path.join(A.STUDY, "spread_profile.csv"), os.path.join(A.STUDY, "spreads.json"), os.path.join(A.STUDY, "universe_cost_filter.csv"),
        OI.INPUTS, OI.DRAGF) if os.path.exists(p)))
    for s in syms:
        j = OI.journal_for(s); h = os.path.join(OI.H4DIR, s + ".csv")
        L.append(f"  {s:10} journal {os.path.basename(j)} sha {OI.sha(j)} | H4 sha {OI.sha(h)} | M1 {INP[s]['m1_file']} | ref {INP[s]['ref_price']} | "
                 f"drag {json.load(open(OI.DRAGF))[OI.root(s)]['drag_scaled']:.4f}R | class {cls(s)}")


def report(res, kind, syms, extra_hdr, path):
    L = [(__doc__.split("\n")[0]), *extra_hdr, f"RUN {time.strftime('%Y-%m-%d %H:%M:%S')}: {len(res)} trades; symbols: " +
         ", ".join(f"{s}={cls(s)}" for s in syms)]
    if kind == "look":
        L.append("INPUTS:"); provenance(syms, L)
        L.append("by symbol x detector (trades replayed): " + "; ".join(
            f"{s.split('.')[0]} " + "/".join(str(sum(1 for z in res if z[0] == s and z[2] == d)) for d in ("SweepMSS", "DeepFib", "EMArevQ", "TrendCont"))
            for s in syms) + "  (SweepMSS/DeepFib/EMArevQ/TrendCont)")
    pooled_tables(res, L)
    L.append("\n--- by class x direction x period (SCALED; increments vs base, paired t, worst DD) ---")
    cell_lines(res, L, lambda z: cls(z[0]), ("FX", "index", "metal", "oil", "all"))
    L.append("--- by symbol x direction x period (SCALED) ---")
    cell_lines(res, L, lambda z: z[0], sorted({z[0] for z in res}), names=RULES)
    bars(res, L)
    open(path, "w").write("\n".join(L) + "\n"); print("\n".join(L)); print(f"\n-> {path}")


# ---------------------------------------------------------------------------------------------------------- validation
def validate(res, path):
    """exact per-trade equality with combo3_trades.pkl, and the printed numbers of both combo3 reports."""
    msgs = []
    ref = pickle.load(open(os.path.join(A.STUDY, "combo3_trades.pkl"), "rb"))
    same = len(ref) == len(res) and all(a[:4] == b[:4] and a[4] == b[4] for a, b in zip(ref, res))
    msgs.append(f"per-trade vs combo3_trades.pkl: {len(res)} vs {len(ref)} trades, {'IDENTICAL (exact float equality, order included)' if same else 'DIFFERENT'}")
    # combo3_report.txt rows for the names this report prints
    txt = open(os.path.join(A.STUDY, "combo3_report.txt")).read(); mism = 0; checked = 0
    for md in MODES:
        for per, f in PER:
            blk = txt.split(f"[{md}] {per}:")[1].split("\n[")[0]
            zs = [z for z in res if f(z[1])]
            for name in NAMES:
                line = next(l for l in blk.splitlines() if l.strip().split(" ")[0] == name)
                s = stats(zs, md, name)
                lo = st.mean(z[4][md][name] for z in zs if z[3]); sh = st.mean(z[4][md][name] for z in zs if not z[3])
                mine = f"  {name:8} {s['mean']:+8.4f} {s['inc']:+8.4f} {s['t']:+6.2f} {s['dd']:8.1f} {s['tot']:8.1f} {s['tot'] / s['dd']:6.1f} | {lo:+8.4f} {sh:+8.4f}"
                checked += 1
                if mine != line: mism += 1; msgs.append(f"  MISMATCH [{md}] {per}:\n    report {line}\n    mine   {mine}")
    msgs.append(f"combo3_report.txt: {checked} rows (base, A, B, I, A+B+I x SCALED/BID x 2 periods), {mism} mismatches")
    L = []; cell_lines(res, L, lambda z: cls(z[0]), ("FX", "index", "metal", "oil", "all"))
    cref = [l for l in open(os.path.join(A.STUDY, "combo3_class_report.txt")).read().splitlines() if l[:4] in ("2012", "2025")]
    norm = lambda l: re.sub(r"\s+", " ", l).strip()                        # only the column padding differs
    mine = [norm(l) for l in L if l[:4] in ("2012", "2025")]; cref = [norm(l) for l in cref]
    cm = sum(1 for a, b in zip(mine, cref) if a != b) + abs(len(mine) - len(cref))
    msgs.append(f"combo3_class_report.txt: {len(cref)} cells lines, {cm} mismatches")
    for a, b in zip(mine, cref):
        if a != b: msgs.append(f"  report {b}\n  mine   {a}")
    ok = same and mism == 0 and cm == 0
    msgs.append("VALIDATION " + ("PASS" if ok else "FAIL"))
    open(path, "a").write("\n=== VALIDATION vs combo3 ===\n" + "\n".join(msgs) + "\n"); print("\n".join(msgs))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="", help="comma list of ORIGINAL symbols (validation / bridge). New symbols: use the default run")
    ap.add_argument("--aa-trendcont", action="store_true", help="original symbols only: TrendCont from the pinned AA journals (bridge)")
    ap.add_argument("--look", action="store_true", help="required for the run on the new symbols (the one look)")
    ap.add_argument("--exclude", default="", help="new symbols left out of the look (recorded in the report header)")
    ap.add_argument("--relook", default="", help="reason, if a look report already exists (writes a separately labelled file; same --exclude set)")
    ap.add_argument("--addendum", default="", help="symbols EXCLUDED from the first look (e.g. US30), evaluated alone into a separate file")
    a = ap.parse_args()
    T0 = time.time()
    syms = [s for s in a.symbols.split(",") if s]
    if syms:
        if any(s not in OI.ORIG7 for s in syms): sys.exit(f"--symbols takes original symbols only ({', '.join(OI.ORIG7)}); the new symbols run by default with --look")
        kind = "bridge" if a.aa_trendcont else "validate"
        res = evaluate(pop_original(set(syms), a.aa_trendcont))
        path = os.path.join(A.STUDY, OUT[kind])
        hdr = [f"MODE {kind.upper()} on original symbols: population = combo3's loader" +
               (" with TrendCont from the pinned AA journals (data/study/aa_pinned) instead of REC" if a.aa_trendcont else " (REC TrendCont + pinned AA)")]
        report(res, kind, sorted(set(syms)), hdr, path)
        if kind == "validate" and sorted(syms) == sorted(OI.ORIG7): sys.exit(0 if validate(res, path) else 1)
        return
    # ---------------- the look
    if a.aa_trendcont: sys.exit("--aa-trendcont applies to the original symbols only")
    dk = lambda v: [x if x.endswith(".dk") else x + ".dk" for x in v.split(",") if x]
    excl, add = dk(a.exclude), dk(a.addendum)
    if any(s not in OI.NEW for s in excl + add): sys.exit(f"--exclude / --addendum take registered new symbols: {', '.join(OI.NEW)}")
    first = os.path.join(A.STUDY, OUT["look"])
    first_excl = None
    if os.path.exists(first):
        m = re.search(r"^excluded from the pool: (.*?)(?: \(|$)", open(first).read(), re.M)
        first_excl = set() if (not m or m.group(1) == "none") else set(m.group(1).split(", "))
    if add:                                  # a symbol left out of the look, reported on its own - never re-pooled
        if first_excl is None: sys.exit("--addendum needs the first look on record")
        if not set(add) <= first_excl: sys.exit(f"--addendum takes only symbols excluded from the first look ({', '.join(sorted(first_excl)) or 'none'})")
        bad, drag = check_new(add)
        if bad: sys.exit("NOT READY:\n  " + "\n  ".join(f"{s}: {w}" for s, w in bad.items()))
        if not a.look: sys.exit(f"ready: addendum {', '.join(add)}. Re-run with --look to compute it.")
        path = os.path.join(A.STUDY, f"oos_queue_addendum_{'_'.join(x.split('.')[0] for x in add)}.txt")
        if os.path.exists(path): sys.exit(f"{path} exists - the addendum is on record")
        ALL = pop_new(add, drag); res = evaluate(ALL)
        report(res, "look", add, ["MODE ADDENDUM - symbols excluded from the first look, evaluated ALONE; NOT pooled with the look and never to be",
                                  f"population as the look; {len(ALL)} rows, {len(res)} replayed"], path)
        return
    if a.relook and first_excl is not None and set(excl) != first_excl:
        sys.exit(f"--relook must keep the first look's exclusions ({', '.join(sorted(first_excl)) or 'none'}); an excluded symbol goes to --addendum")
    syms = [s for s in OI.NEW if s not in excl]
    bad, drag = check_new(syms)
    if bad:
        sys.exit("NOT READY (name a symbol in --exclude to leave it out of the look for good):\n  " + "\n  ".join(f"{s}: {w}" for s, w in bad.items()))
    if not a.look:
        sys.exit(f"ready: {len(syms)} symbols ({', '.join(syms)}), excluded: {', '.join(excl) or 'none'}.\n"
                 "This is the ONE LOOK. Re-run with --look to compute and print the rule results.")
    path = os.path.join(A.STUDY, OUT["look"]); tag = ""
    if os.path.exists(path):
        if not a.relook: sys.exit(f"{path} exists - the first look is on record. A re-run needs --relook \"<reason>\" and is labelled as such.")
        tag = time.strftime("%Y%m%d-%H%M%S"); path = path.replace(".txt", f".relook-{tag}.txt")
    ALL = pop_new(syms, drag)
    res = evaluate(ALL)
    hdr = ["MODE LOOK (new symbols, pre-registered, one look)" + (f" - SECOND LOOK {tag}: {a.relook}" if tag else ""),
           f"excluded from the pool: {', '.join(excl) or 'none'}" + (" (may only be reported later as a separate addendum, never re-pooled)" if excl else ""),
           f"population: four detectors from data/study/oos_aa AA journals, aa_rows filters, signals {', '.join(f'{s.split('.')[0]}>={OI.NEW[s][1]}' for s in syms if OI.NEW[s][1] != '2012.01.01') or 'all from 2012'}, "
           f"before {OI.SIGNAL_END}; {len(ALL)} rows, {len(res)} replayed (no M1 coverage dropped: {len(ALL) - len(res)}); {(time.time() - T0) / 60:.1f} min"]
    report(res, "look", syms, hdr, path)
    pickle.dump(res, open(os.path.join(A.STUDY, "oos_queue_trades" + (f".relook-{tag}" if tag else "") + ".pkl"), "wb"))


if __name__ == "__main__": main()
