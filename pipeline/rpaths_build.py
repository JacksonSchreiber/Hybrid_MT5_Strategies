#!/usr/bin/env python3
"""rpaths_build.py - the Data visualizers' base data (live/static/rpaths.json, gzip-served by the webapp).

2026-10-03 (trader): the page is a research tool - every chart follows the management dials. Exits, outcomes and position R come
from the combo slices (pipeline/combo_build.py -> combo_pack.py -> combos.bin, served one dial setting at a time); this file holds
only what does NOT depend on the dials, per trade, in the combo file's trade order:
  s symbol, d detector, l long?, y year, dt signal date, g 0 original seven / 1 new nine, p 1 = partial years (EURJPY/GBPJPY 2020+,
  EURGBP 2013-16: the tester lacked conversion history), tp TP2 in R, t1 TP1 in R (or null), f6 best R in the first 6 H4 bars,
  rt the PRICE path: the bid close of every H4 bar from the entry bar on, in the trade's original 1R signed with the trade (x100),
  long enough for the latest exit of ANY combo (capped at 180 bars = 30 trading days).
Population = pipeline/per_symbol_stats.py's (all four detectors from today's EA build; originals' TrendCont from the pinned AA journals).
    python3 pipeline/rpaths_build.py          (after combo_build.py --build and combo_pack.py)
    python3 pipeline/rpaths_build.py --out data/combos/<run>     write rpaths.json(.gz) into the run folder instead of live/static
                                              (a build that is not uploaded yet: deploy_live.sh --code ships live/static as it is)
"""
from __future__ import annotations
import glob, gzip, json, os, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
from pipeline import combo_build as CB                                     # noqa: E402
from pipeline.per_symbol_stats import ORIG                                 # noqa: E402
from pipeline.progress import Bar                                          # noqa: E402

MODE = "SCALED"; CAP = 180
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "live", "static", "rpaths.json")
PARTIAL = {"EURJPY.dk", "GBPJPY.dk", "EURGBP.dk"}
POPK: dict = {}; CM: dict = {}


def do_symbol(arg):
    sym, idxs = arg
    S = CB.sym_obj(sym); out = []
    for i in idxs:
        s_, t_, st_, up_ = CM["trades"][i]; x = POPK[(s_, t_, st_)]
        u = A.setup(S, x, MODE)
        if not u: out.append((i, None)); continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
        bc, bh = F["bc"], F["bh"]; i0, k0, n = u["i0"], u["k0"], F["n"]
        nb = min(CAP, int(np.ceil(CM["max_xb"][i] / 10.0)) + 1)
        rt = []
        for b in range(1, nb + 1):
            ib = i0 + b
            if ib >= len(S.h4): break
            kb = int(np.searchsorted(S.t, S.h4t[ib])) - 1 - k0
            if kb >= n: break
            rt.append(round(float(bc[kb]) * 100))
        k6 = int(np.searchsorted(S.t, S.h4t[i0 + 6])) - k0 if i0 + 6 < len(S.h4) else n
        sg = 1.0 if u["up"] else -1.0
        t1 = round((x["tp1"] - x["e"]) * sg / u["R"], 3) if x["tp1"] else None
        out.append((i, {"s": sym.split(".")[0], "d": x["strat"], "l": 1 if x["up"] else 0, "y": int(x["t"][:4]), "dt": x["t"][:10].replace(".", "-"),
                        "g": 0 if sym in ORIG else 1, "p": 1 if sym in PARTIAL else 0, "tp": round(u["tp2_R"], 2) if u["tp2_R"] is not None else None,
                        "t1": t1, "f6": round(float(bh[:max(1, min(k6, n))].max()), 2), "src": "backtest", "rt": rt}))
    return out


def main():
    global POPK, CM
    T0 = time.time()
    run = sorted(d for d in glob.glob(os.path.join(CB.OUTDIR, "2*")) if os.path.exists(os.path.join(d, "combos_meta.json")))[-1]
    CM = json.load(open(os.path.join(run, "combos_meta.json")))
    POPK = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    by: dict = {}
    for i, (s_, t_, st_, up_) in enumerate(CM["trades"]): by.setdefault(s_, []).append(i)
    trades = [None] * len(CM["trades"]); bar = Bar(len(by), "paths", "symbols")
    with Pool(int(os.environ.get("RP_WORKERS", "16"))) as pool:
        for res in pool.imap_unordered(do_symbol, sorted(by.items(), key=lambda kv: -len(kv[1]))):
            for i, t in res: trades[i] = t
            bar.update(1)
    bar.done()
    missing = sum(t is None for t in trades)
    trades = [t if t is not None else {"s": CM["trades"][i][0].split(".")[0], "src": "backtest", "rt": [], "nodata": 1} for i, t in enumerate(trades)]
    doc = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "engine": "M1 ask-corrected replay (SCALED), every management combo",
           "rt_unit": "H4 bar (6 = 1 trading day)", "cap_bars": CAP, "combo_build": CM["build"], "n_combos": CM["n_combos"],
           "rec_bytes": CM["rec_bytes"], "dials": CM["dials"], "symbols_orig": [s.split(".")[0] for s in ORIG],
           "partial": [s.split(".")[0] for s in sorted(PARTIAL)], "trades": trades}
    raw = json.dumps(doc, separators=(",", ":")).encode()
    out = os.path.join(sys.argv[sys.argv.index("--out") + 1], "rpaths.json") if "--out" in sys.argv else OUT
    with open(out, "wb") as f: f.write(raw)
    with gzip.open(out + ".gz", "wb", compresslevel=9) as f: f.write(raw)
    print(f"{len(trades)} trades ({missing} without data) in {(time.time() - T0) / 60:.1f} min; {len(raw) / 1e6:.2f} MB raw, "
          f"{os.path.getsize(out + '.gz') / 1e6:.2f} MB gzip; combo build {CM['build']} -> {out}")


if __name__ == "__main__": main()
