#!/usr/bin/env python3
"""oos_build_inputs.py - per-symbol study inputs for the out-of-sample (OOS) test of the coach-queue rules on the 10 new symbols
(pre-registration: docs/coach_queue.md). Single source of truth for the symbol list; pipeline/oos_queue_study.py imports it.

The original seven's inputs and where each new one comes from (same method unless marked DEVIATION):
  M1 source      engine SRC (pipeline/inverse_study.SRC) -> data/qdm_csv/fleet/<code>-M1-No Session.csv, Dukascopy via QDM, UTC.
                 New: the same QDM export (data/qdm_csv/fleet/oos_download_report.txt). Registered at runtime by the study, the
                 shared SRC dict is not edited.
  H4 bars        data/study/h4/<SYM>.dk.csv. Originals: pipeline/dump_h4_dk.py copied out of the running terminal after import.
                 New: pipeline/oos_h4_from_ticks.py rebuilds them from the tick CSV the import reads (bid M1 -> H4, UTC), bit-identical to
                 the terminal's bars on EURUSD and GBPUSD (16,110 and 20,147 bars, 0 mismatches). Built by pipeline/oos_mt5_stage.sh.
  spread profile data/study/spread_profile.csv, <ROOT>.sim rows (the box's MT5 M1 bar spreads, 20 days to 2026-09-23). Same file,
                 same run: every new symbol already has its 24 rows. Nothing to build.
  ref price      originals: data/study/spreads.json "price" (12 live samples on the box, 2026-09-17; USDJPY has no entry and uses
                 the engine's M1-tail fallback). New: "price" in data/study/universe_cost_filter.csv (the box, 2026-09-21; that
                 script's spread sampling is the same 12-sample method). DEVIATION: 4 days later, H4 close rather than tick mid.
  drag (BID)     data/study/spread_drag.json drag_scaled = (live spread / live price) x median entry / median |entry - SL| over
                 approved journal rows. New: spread and price from universe_cost_filter.csv; median entry and stop from the
                 approved rows of the new AA journals (signal fields only). DEVIATION: the originals' medians also used the graded
                 window journals (none exist for new symbols). Needs the AA journals -> `drag` step after the MT5 stage.
  class          pipeline/combo3_class_split.cls (unchanged): XAGUSD metal, US30 index, USDMXN and the crosses FX.

    python3 pipeline/oos_build_inputs.py build     # -> data/study/oos_inputs.json  (M1 checks, ref price, spread, tick, class)
    python3 pipeline/oos_build_inputs.py drag      # -> data/study/oos_spread_drag.json + .md   (after the AA journals exist)
    python3 pipeline/oos_build_inputs.py qa        # data-quality summary of the AA journals and H4 files (signal fields only)
    ... build|drag|qa --holdout                    # the same for the HOLDOUT set (AUDNZD, AUDCAD, NZDCAD) -> data/study/holdout_aa/
ONE-LOOK: nothing here reads an outcome column (r_multiple, pnl, terminal, exit_*, mfe_r, rt_*, ...). Keep it that way.
"""
from __future__ import annotations
import csv, glob, hashlib, json, os, statistics as st, sys
from collections import Counter, defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STUDY = os.path.join(ROOT, "data", "study")
FLEET = os.path.join(ROOT, "data", "qdm_csv", "fleet")
OOS_AA = os.path.join(STUDY, "oos_aa")
INPUTS = os.path.join(STUDY, "oos_inputs.json")
DRAGF = os.path.join(STUDY, "oos_spread_drag.json")
H4DIR = os.path.join(STUDY, "h4")

# .dk symbol -> (QDM / Dukascopy code: M1 file stem and qdmcli symbol, first signal date used). Frozen 2026-10-02/03.
NEW = {
    "AUDUSD.dk": ("AUDUSD", "2012.01.01"), "USDCAD.dk": ("USDCAD", "2012.01.01"), "USDCHF.dk": ("USDCHF", "2012.01.01"),
    "NZDUSD.dk": ("NZDUSD", "2012.01.01"), "EURJPY.dk": ("EURJPY", "2012.01.01"), "GBPJPY.dk": ("GBPJPY", "2012.01.01"),
    "EURGBP.dk": ("EURGBP", "2012.01.01"), "USDMXN.dk": ("USDMXN", "2012.01.01"), "XAGUSD.dk": ("XAGUSD", "2012.01.01"),
    "US30.dk": ("USA30IDXUSD", "2013.01.01"),       # trader ruling 2026-10-03: Dukascopy throttled the 2011-12 source
}
# HOLDOUT (2026-10-03): the commodity-bloc time-stop holdout (docs/coach_queue.md "PRE-REGISTRATION - commodity-bloc time stop").
# A SEPARATE set, deliberately NOT in NEW: combo_build, rpaths_build, per_symbol_stats, the website data and every other study build
# their population from NEW, and these three must stay unseen until the one look (pipeline/cbloc_holdout_look.py --look).
# Class FX (combo3_class_split.cls), group commodity bloc. Journals, inputs and drag live in data/study/holdout_aa/ only.
HOLDOUT = {"AUDNZD.dk": ("AUDNZD", "2012.01.01"), "AUDCAD.dk": ("AUDCAD", "2012.01.01"), "NZDCAD.dk": ("NZDCAD", "2012.01.01")}
HOLDOUT_GROUP = "commodity bloc"
HOLDOUT_AA = os.path.join(STUDY, "holdout_aa")
HOLDOUT_INPUTS = os.path.join(HOLDOUT_AA, "holdout_inputs.json")
HOLDOUT_DRAGF = os.path.join(HOLDOUT_AA, "holdout_spread_drag.json")
_REG = {**NEW, **HOLDOUT}          # code / start-date lookup only (disjoint keys); NEW alone stays every tool's population
# set name -> (registry, journal dir, inputs file, drag file)
SETS = {"oos": (NEW, OOS_AA, INPUTS, DRAGF), "holdout": (HOLDOUT, HOLDOUT_AA, HOLDOUT_INPUTS, HOLDOUT_DRAGF)}
ORIG7 = ["EURUSD.dk", "GBPUSD.dk", "US100.dk", "US500.dk", "USDJPY.dk", "USOIL.dk", "XAUUSD.dk"]
# Signals strictly before this time. The latest original window ended 2026.06.29/30 (EURUSD); the same end gives every trade at
# least three months of M1 runway (data to 2026-10-01/02). Frozen before the look; the stage runs the tester to 2026.06.30 too.
SIGNAL_END = "2026.07.01"
TESTER_TO = "2026.06.30"
SIGNAL_COLS = ("signal_id", "signal_time", "symbol", "strategy", "direction", "entry", "sl", "tp", "tp1", "tp2", "decision")


def root(sym): return sym.split(".")[0]


def m1_path(sym):
    code = _REG[sym][0]
    for stem in (code, root(sym)):
        p = os.path.join(FLEET, f"{stem}-M1-No Session.csv")
        if os.path.exists(p): return p
    return None


def importer_digits(sym, decimals):
    """mql5/include/Hybrid/TickImport.mqh TI_SetInferredSpecs: digits = decimals of the first tick line, XAG >= 3, US30 <= 2."""
    d = decimals if decimals > 0 else 5
    b = root(sym)
    if "XAU" in b: d = max(d, 2)
    elif "XAG" in b: d = max(d, 3)
    elif b in ("US100", "US500", "US30"): d = min(d, 2)
    return d


def decimals_of(s):
    return len(s.split(".")[1]) if "." in s else 0


def ucf():
    return {r["symbol"]: r for r in csv.DictReader(open(os.path.join(STUDY, "universe_cost_filter.csv")))}


def profile_rows():
    d = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(STUDY, "spread_profile.csv"))): d[r["symbol"]][int(r["hour_utc"])] = r["spread_med_hour"]
    return d


def sha(p, n=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(n), b""): h.update(b)
    return h.hexdigest()[:16]


def cmd_build(which="oos"):
    import numpy as np, pandas as pd
    from pipeline.combo3_class_split import cls
    REG, _, INPUTS_, _ = SETS[which]
    U, P = ucf(), profile_rows()
    out, bad = {}, []
    old = json.load(open(INPUTS_))["symbols"] if os.path.exists(INPUTS_) else {}   # ref / spread fields stay frozen once written
    for sym, (code, start) in REG.items():
        r = root(sym); e = {"symbol": sym, "qdm_code": code, "start": start, "class": cls(sym)}
        if which == "holdout": e["group"] = HOLDOUT_GROUP
        p = m1_path(sym)
        if not p:
            e["missing"] = "M1 CSV not on disk"; out[sym] = e; bad.append(f"{sym}: no M1 CSV"); print(f"{sym:10} M1 MISSING"); continue
        d = pd.read_csv(p, dtype={"Date": str, "Time": str, "Close": str})
        dec = max(decimals_of(x) for x in d["Close"].iloc[:: max(1, len(d) // 5000)])
        c = d["Close"].astype(float).to_numpy()
        yr = d["Date"].str[:4]
        e.update({"src": os.path.basename(p).replace("-M1-No Session.csv", ""), "m1_file": os.path.basename(p), "m1_rows": int(len(d)),
                  "m1_first": d["Date"].iloc[0] + " " + d["Time"].iloc[0], "m1_last": d["Date"].iloc[-1] + " " + d["Time"].iloc[-1],
                  "m1_decimals": dec, "m1_rows_per_year": {y: int(n) for y, n in yr.value_counts().sort_index().items()},
                  "m1_median_close_per_year": {y: float(np.median(c[(yr == y).to_numpy()])) for y in sorted(yr.unique())},
                  "m1_tail_median": float(np.median(c[-20000:]))})
        dig = importer_digits(sym, dec); e["digits"] = dig; e["tick"] = 10.0 ** -dig
        u = U.get(r + ".sim")
        FROZEN = ("ref_price", "ref_source", "spread_today", "spread_max", "spread_samples", "pct_of_price")
        if all(k in old.get(sym, {}) for k in FROZEN):
            e.update({k: old[sym][k] for k in FROZEN}); e["ref_vs_m1_tail"] = e["ref_price"] / e["m1_tail_median"]; e["ref_frozen"] = True
        elif u:
            price, spread = float(u["price"]), float(u["spread_median"])
            e.update({"ref_price": price, "ref_source": "data/study/universe_cost_filter.csv (box, 2026-09-21)", "spread_today": spread,
                      "spread_max": float(u["spread_max"]), "spread_samples": int(u["samples"]), "pct_of_price": spread / price,
                      "ref_vs_m1_tail": price / e["m1_tail_median"]})
            if not 0.9 < e["ref_vs_m1_tail"] < 1.1: bad.append(f"{sym}: ref price {price} vs M1 tail {e['m1_tail_median']:.6g} - units?")
        else: bad.append(f"{sym}: no universe_cost_filter row")
        hrs = P.get(r + ".sim", {})
        e["profile_hours"] = len(hrs); e["profile_blank_hours"] = sum(1 for v in hrs.values() if v in ("", None))
        if len(hrs) != 24: bad.append(f"{sym}: spread profile has {len(hrs)} hours")
        out[sym] = e
        print(f"{sym:10} {e['class']:5} M1 {e['m1_rows']:>9,} rows {e['m1_first']} .. {e['m1_last']}  dec {dec} -> digits {dig}  "
              f"ref {e.get('ref_price')} (x{e.get('ref_vs_m1_tail', float('nan')):.3f} of M1 tail)  spread {e.get('spread_today')}  "
              f"profile {e['profile_hours']}h ({e['profile_blank_hours']} blank)")
    json.dump({"asof": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()), "signal_end": SIGNAL_END,
               "symbols": out, "problems": bad}, open(INPUTS_, "w"), indent=1)
    print(f"-> {INPUTS_}" + ("" if not bad else "\nPROBLEMS:\n  " + "\n  ".join(bad)))


def load_inputs(required=True, which="oos"):
    INPUTS_ = SETS[which][2]
    if not os.path.exists(INPUTS_):
        if required: sys.exit(f"{INPUTS_} missing - run: python3 pipeline/oos_build_inputs.py build" + (" --holdout" if which == "holdout" else ""))
        return {}
    return json.load(open(INPUTS_))["symbols"]


def journal_for(sym):
    """the one main AA journal for sym in data/study/oos_aa (HOLDOUT symbols: data/study/holdout_aa) - never the .actions / .inv /
    .part / .delays siblings."""
    fs = [f for f in glob.glob(os.path.join(HOLDOUT_AA if sym in HOLDOUT else OOS_AA, f"AA_{sym}_*.csv"))
          if not any(x in os.path.basename(f) for x in (".actions", ".inv", ".part", ".delays"))]
    return fs[0] if len(fs) == 1 else (None if not fs else "AMBIGUOUS:" + ",".join(sorted(fs)))


def signal_rows(path):
    """signal-level fields only (ONE-LOOK: outcome columns are dropped on read)."""
    return [{k: r.get(k) for k in SIGNAL_COLS} for r in csv.DictReader(open(path, newline="", encoding="ascii", errors="replace"))]


def in_population(sym, r):
    return r["decision"] == "approved" and _REG[sym][1] <= r["signal_time"][:10] and r["signal_time"] < SIGNAL_END


def cmd_drag(which="oos"):
    REG, AADIR, _, DRAGF_ = SETS[which]
    I = load_inputs(which=which); out = {}
    for sym in REG:
        j = journal_for(sym)
        if not j or j.startswith("AMBIGUOUS"): print(f"{sym:10} no single journal in {AADIR} ({j}) - skipped"); continue
        e = I.get(sym, {})
        if "pct_of_price" not in e: print(f"{sym:10} no spread/price in oos_inputs.json - skipped"); continue
        stops, prices = [], []
        for r in signal_rows(j):
            if not in_population(sym, r): continue
            try: en, sl = float(r["entry"]), float(r["sl"])
            except (TypeError, ValueError): continue
            if en > 0 and sl > 0 and en != sl: stops.append(abs(en - sl)); prices.append(en)
        if not stops: print(f"{sym:10} no approved rows - skipped"); continue
        ms, mp = st.median(stops), st.median(prices)
        out[root(sym)] = {"spread_today": e["spread_today"], "pct_of_price": e["pct_of_price"], "median_stop": ms, "median_price_graded": mp,
                          "drag_today": e["spread_today"] / ms, "drag_scaled": e["pct_of_price"] * mp / ms, "n": len(stops),
                          "journal": os.path.basename(j), "price_today": e["ref_price"]}
        print(f"{sym:10} n={len(stops):4d} median stop {ms:.6g} median entry {mp:.6g} -> drag today {out[root(sym)]['drag_today']:.4f}R "
              f"scaled {out[root(sym)]['drag_scaled']:.4f}R")
    json.dump(out, open(DRAGF_, "w"), indent=1)
    md = [f"# Spread drag, {'HOLDOUT' if which == 'holdout' else 'OOS'} symbols (method of data/study/spread_drag.md)", "",
          "Spread and price: data/study/universe_cost_filter.csv (box, 2026-09-21, 12 live samples). Median stop and median entry: approved",
          f"rows of the new AA journals in {os.path.relpath(AADIR, ROOT)} (signals from {SIGNAL_END} excluded; US30 from 2013). Used only by the BID mode.", "",
          "| symbol | live spread | spread % of price | median stop | drag at today's spread | **drag scaled** | approved rows |", "|---|---|---|---|---|---|---|"]
    for k, v in out.items():
        md.append(f"| {k} | {v['spread_today']:.6g} | {v['pct_of_price'] * 100:.4f}% | {v['median_stop']:.6g} | {v['drag_today']:.3f}R | "
                  f"**{v['drag_scaled']:.3f}R** | {v['n']} |")
    open(DRAGF_.replace(".json", ".md"), "w").write("\n".join(md) + "\n")
    print(f"-> {DRAGF_} (+ .md), {len(out)} symbols")


def cmd_qa(which="oos"):
    """journal and H4 coverage, signal counts per detector and year, years without a signal, signal-field sanity. No outcome column
    is read."""
    REG = SETS[which][0]
    I = load_inputs(required=False, which=which)
    for sym in REG:
        j = journal_for(sym); h4 = os.path.join(H4DIR, f"{sym}.csv")
        hb = list(csv.reader(open(h4)))[1:] if os.path.exists(h4) else []
        line = f"{sym:10} H4 {len(hb):6d} bars " + (f"{hb[0][0]} .. {hb[-1][0]}" if hb else "MISSING")
        if not j or j.startswith("AMBIGUOUS"): print(line + f" | journal: {j or 'MISSING'}"); continue
        rows = signal_rows(j); idx = {b[0][:16] for b in hb}
        dec = Counter(r["decision"] for r in rows)
        pop = [r for r in rows if in_population(sym, r)]
        noh4 = sum(1 for r in pop if r["signal_time"][:16] not in idx)
        print(line + f" | {os.path.basename(j)} rows {len(rows)} {dict(dec)} | population {len(pop)} (no H4 bar: {noh4})")
        print("   by detector: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r['strategy'] for r in pop).items())) +
              " | by year: " + " ".join(f"{k}:{v}" for k, v in sorted(Counter(r['signal_time'][:4] for r in pop).items())) +
              " | long share {:.0%}".format(sum(1 for r in pop if r["direction"].upper().startswith("B")) / max(1, len(pop))))
        yrs = Counter(r["signal_time"][:4] for r in pop)
        y0, y1 = int(REG[sym][1][:4]), int(SIGNAL_END[:4])
        miss = [str(y) for y in range(y0, y1 + 1) if not yrs.get(str(y))]
        dets = Counter(r["strategy"] for r in pop)
        print("   years with NO population signal: " + (" ".join(miss) if miss else f"none ({y0}-{y1} all present)") +
              " | detectors with no signal: " + (", ".join(d for d in ("SweepMSS", "DeepFib", "TrendCont", "EMArevQ") if not dets.get(d)) or "none"))
        badf = side = 0
        for r in pop:
            try: en, sl = float(r["entry"]), float(r["sl"])
            except (TypeError, ValueError): badf += 1; continue
            up = r["direction"].upper().startswith("B")
            if en <= 0 or sl <= 0 or (up and sl >= en) or (not up and sl <= en): side += 1
        print(f"   signal fields: unparseable entry/sl {badf}, stop on the wrong side / non-positive {side}")
        if sym in I and "m1_last" in I[sym]: print(f"   M1 {I[sym]['m1_first']} .. {I[sym]['m1_last']}; journal sha {sha(j)}")


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    which = "holdout" if "--holdout" in sys.argv[2:] else "oos"
    f = {"build": cmd_build, "drag": cmd_drag, "qa": cmd_qa}.get(cmd)
    f(which) if f else sys.exit(__doc__)
