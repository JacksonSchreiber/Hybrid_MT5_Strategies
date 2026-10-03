#!/usr/bin/env python3
"""rpaths_build.py - trader 2026-10-02: data for the webapp's R-path explorer (the "Paths" tab, live/static/rpaths.js).

Every trade of the blind record (TrendCont + SweepMSS / DeepFib / EMArevQ AA rows, 7 symbols; pinned data/study/aa_pinned) is
replayed on M1 (pipeline/ask_side_study, SCALED = ask-corrected) under the live stop management: bank at +1R (or TP1 if nearer)
-> stop to entry; shorts' stop to +0.25R at +1.5R; runner to TP2. The PATH is the price itself in the trade's original 1R from
the signal's entry, signed with the trade (+ = in favour), on the bid side.
  rt : one point per closed H4 bar after entry (x = H4 bars; 6 = one trading day), the bar's close; the last point is the
       exit level (TP2 / entry / -1R). Capped at 180 bars (30 trading days).
  pg : 41 points from entry (0%) to exit (100%) - M1 closes at evenly spaced moments, last = the exit level.
Outcome: "tp2" (the runner reached TP2), "be" (banked, then stopped at entry / +0.25R), "sl" (stopped at -1R before the bank),
"open" (still running at the end of the data - dropped). Meta: symbol, detector, direction, year, TP2 in R, best R in the first
6 H4 bars (fast / slow starts), exit time in H4 bars. source = "backtest" (live trades will be appended with source "live").
Output: live/static/rpaths.json (gzip-served by the webapp).
2026-10-03 (trader): ALL 16 downloaded symbols - the original seven AND the nine never used in development - with the population of
pipeline/per_symbol_stats.py (all four detectors from today's EA build: originals' TrendCont from the pinned AA journals, not REC;
the new nine from data/study/oos_aa). Extra per trade: dt signal date, g 0 original / 1 new, p 1 = partial years (EURJPY/GBPJPY
2020+, EURGBP 2013-16: the tester lacked conversion history), rb / ra = full-position R x1000 under the live stack / live + A+B+I
(from data/study/per_symbol_trades.pkl - run per_symbol_stats.py first).
"""
from __future__ import annotations
import gzip, json, os, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                                   # noqa: E402
import pickle                                                               # noqa: E402
from pipeline import oos_queue_study as Q                                   # registers the new nine's engine inputs  # noqa: E402
from pipeline import oos_build_inputs as OI                                 # noqa: E402
from pipeline.per_symbol_stats import ORIG, PKL                             # noqa: E402
from pipeline import mgmt_variants as MV                                    # noqa: E402

MODE = "SCALED"; CAP = 180; NPG = 41
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "live", "static", "rpaths.json")


def walk(F, be, bank_R, tp2_R, up):
    xh, xl, bh = F["xh"], F["xl"], F["bh"]
    stop, banked, raised = -1.0, False, False
    for j in range(F["n"]):
        if xl[j] <= stop: return ("be" if banked else "sl"), j, stop
        if not banked and xh[j] >= bank_R: banked = True; stop = be
        if banked and not raised and bh[j] >= A.PYR_R:
            raised = True
            if not up: stop = max(stop, be + 0.25)
        if tp2_R is not None and xh[j] >= tp2_R: return "tp2", j, tp2_R
    return "open", None, None


ALL = []; RQ = {}
PARTIAL = {"EURJPY.dk", "GBPJPY.dk", "EURGBP.dk"}
def do_symbol(sym):
    S = A.Sym(sym, A.spread_profile(), Q.REFS); out = []
    for x in [x for x in ALL if x["sym"] == sym]:
        u = A.setup(S, x, MODE)
        if not u or u["tp2_R"] is None: continue
        F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
        be = float(F["eo"][0])
        oc, jx, xr = walk(F, be, u["bank_R"], u["tp2_R"], u["up"])
        if oc == "open": continue
        bc, bh = F["bc"], F["bh"]; i0 = u["i0"]; k0 = u["k0"]
        rt = []
        for b in range(1, CAP + 1):
            ib = i0 + b
            if ib >= len(S.h4): break
            kb = int(np.searchsorted(S.t, S.h4t[ib])) - 1 - k0
            if kb >= jx: break
            rt.append(round(float(bc[kb]) * 100))
        tx = S.t[k0 + jx]
        xbars = float(np.searchsorted(S.h4t, tx, side="right") - i0) - 1 + ((tx - S.h4t[min(len(S.h4t) - 1, np.searchsorted(S.h4t, tx, side="right") - 1)]) / 240.0)
        if len(rt) < CAP: rt.append(round(xr * 100))
        idx = np.linspace(0, jx, NPG).astype(int)
        pg = [round(float(bc[k]) * 100) for k in idx[:-1]] + [round(xr * 100)]
        k6 = int(np.searchsorted(S.t, S.h4t[i0 + 6])) - k0 if i0 + 6 < len(S.h4) else jx
        mfe6 = float(bh[:max(1, min(k6, jx + 1))].max())
        q = RQ.get((sym, x["t"], x["strat"]))
        mv = MV.variants(S, x, MODE)                                         # 96 management combinations (webapp selector)
        out.append({"s": sym.split(".")[0], "d": x["strat"], "l": 1 if x["up"] else 0, "y": int(x["t"][:4]), "o": oc,
                    "tp": round(u["tp2_R"], 2), "f6": round(mfe6, 2), "xb": round(xbars, 2), "src": "backtest", "rt": rt, "pg": pg,
                    "dt": x["t"][:10].replace(".", "-"), "g": 0 if sym in ORIG else 1, "p": 1 if sym in PARTIAL else 0,
                    "rb": None if q is None else round(q[0] * 1000), "ra": None if q is None else round(q[1] * 1000),
                    "m": None if mv is None else [round(v * 1000) for v in mv]})
    return out


def main():
    global ALL, RQ
    T0 = time.time()
    RQ = {(z[0], z[1], z[2]): (z[4][MODE]["base"], z[4][MODE]["A+B+I"]) for z in pickle.load(open(PKL, "rb"))}
    new = [s for s in OI.NEW if s != "US30.dk"]
    bad, drag = Q.check_new(new)
    ALL = sorted(Q.pop_original(set(ORIG), aa_trendcont=True) + Q.pop_new([s for s in new if s not in bad], drag), key=lambda x: x["t"])
    with Pool(6) as pool: trades = [t for r in pool.map(do_symbol, sorted({x["sym"] for x in ALL})) for t in r]
    doc = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "engine": "M1 ask-corrected replay, live stop management",
           "symbols_orig": [s.split(".")[0] for s in ORIG], "partial": [s.split(".")[0] for s in sorted(PARTIAL)],
           "mgmt": {"index": "sa*32 + K + 2P + 4R + 8B + 16I (sa 0 = S off, 1 = S on, 2 = S+A)", "live": MV.LIVE, "all": MV.ALL},
           "rt_unit": "H4 bar (6 = 1 trading day)", "pg_points": NPG, "cap_bars": CAP, "trades": trades}
    raw = json.dumps(doc, separators=(",", ":")).encode()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f: f.write(raw)
    with gzip.open(OUT + ".gz", "wb", compresslevel=9) as f: f.write(raw)
    chk = [t for t in trades if t["m"] is not None and t["rb"] is not None]
    d39 = max(abs(t["m"][MV.LIVE] - t["rb"]) for t in chk); d95 = max(abs(t["m"][MV.ALL] - t["ra"]) for t in chk)
    print(f"management variants check on {len(chk)} trades: live combo vs base max |diff| {d39} (x1000 R), all-on vs A+B+I max |diff| {d95}")
    from collections import Counter
    print(f"{len(trades)} trades in {(time.time() - T0) / 60:.1f} min; outcomes {dict(Counter(t['o'] for t in trades))}; "
          f"{len(raw) / 1e6:.2f} MB raw, {os.path.getsize(OUT + '.gz') / 1e6:.2f} MB gzip")


if __name__ == "__main__": main()
