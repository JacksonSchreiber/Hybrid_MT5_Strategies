#!/usr/bin/env python3
"""combo_bloc_check.py - trader 2026-10-04: the post-build checks for the combo build that added the commodity-bloc dial
(pipeline/combo_build.py BLOC). Run after combo_pack.py; reads only, paced (CHK_MBPS, default 40 MB/s total) so the PC stays usable.
  1 the trade list is identical to the previous build's
  2 bloc OFF is untouched: the first 29,484 combo blocks of the new combos.bin are byte-identical to the previous build's whole file
  3 bloc ON at the live dials == cbloc_timestop_study.run_h "T+L1.0" on every trade of the six bloc symbols (to the file's 0.001R
    rounding), and the 24h cut fires exactly when run_h time-stops
  4 headline: the live setup on the six bloc symbols with the bloc rules (compare the all-six study: T+L1.0 -0.0031R/trade, DD 52.2)
    python3 pipeline/combo_bloc_check.py NEW_RUN OLD_RUN        (CB_HOLDOUT=1, as the build)
"""
from __future__ import annotations
import json, os, statistics as st, sys, time
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import combo_pack as CP                        # noqa: E402
from pipeline import cbloc_timestop_study as CT              # noqa: E402
from pipeline.exit_mgmt_study import maxdd                   # noqa: E402

BLOC6 = {"AUDUSD.dk", "NZDUSD.dk", "USDCAD.dk", "AUDNZD.dk", "AUDCAD.dk", "NZDCAD.dk"}
MBPS = float(os.environ.get("CHK_MBPS", "40"))
POPK: dict = {}


def run_h_sym(arg):
    sym, items = arg
    S = CB.sym_obj(sym); out = []
    for i, key in items:
        x = POPK[key]; p = CB.prep(S, x)
        if p is None: out.append((i, None, None)); continue
        v, _, why = CT.run_h(p["F"], p["e0"], p["be"], p["bank_R"](1.0), p["tp2"], p["bankf_det"], A.F0,
                             p["kbar"][6] if p["kbar"][6] >= 0 else None, p["up"], 1, CB.BLOC_LOCK)
        out.append((i, v, why))
    return out


def main(new, old):
    global POPK
    ok_all = True
    def say(ok, msg):
        nonlocal ok_all; ok_all &= bool(ok); print(("PASS " if ok else "FAIL ") + msg, flush=True)
    mn = json.load(open(os.path.join(new, "combos_meta.json"))); mo = json.load(open(os.path.join(old, "combos_meta.json")))
    nt = mn["n_trades"]; rb = mn["rec_bytes"]; combos = CB.grid(); n_off = sum(1 for c in combos if c[7] == 0)
    say(mn["trades"] == mo["trades"], f"trade list identical to {os.path.basename(old)} ({nt} trades)")
    say(mn["n_combos"] == len(combos) and mo["n_combos"] == n_off, f"combos: new {mn['n_combos']} = grid {len(combos)}, old {mo['n_combos']} = bloc-off {n_off}")
    # 2 byte compare, paced
    want = n_off * nt * rb; chunk = 64 * 1024 * 1024; done = 0; same = True; t0 = time.time()
    with open(os.path.join(new, "combos.bin"), "rb") as fn, open(os.path.join(old, "combos.bin"), "rb") as fo:
        say(os.path.getsize(os.path.join(old, "combos.bin")) == want, f"old combos.bin is exactly the bloc-off block ({want / 1e9:.2f} GB)")
        while done < want:
            tb = time.time(); k = min(chunk, want - done)
            if fn.read(k) != fo.read(k): same = False; break
            done += k; time.sleep(max(0.0, 2 * k / (MBPS * 1e6) - (time.time() - tb)))
    say(same, f"bloc OFF byte-identical to the previous build: {done / 1e9:.2f} of {want / 1e9:.2f} GB compared ({time.time() - t0:.0f}s)")
    # 3 bloc ON at the live dials vs run_h on the six bloc symbols
    def slice_of(ci):
        with open(os.path.join(new, "combos.bin"), "rb") as f:
            f.seek(ci * nt * rb); return np.frombuffer(f.read(nt * rb), dtype=CP.PK)
    ib = CB.find(combos, "25@6", 0, "det@1.0", "0.5@1.5", "off", "off", "off", bloc="on")
    il = CB.find(combos, "25@6", 0, "det@1.0", "0.5@1.5", "0.25", "off", "off")
    sb, sl = slice_of(ib), slice_of(il)
    POPK = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    by: dict = {}
    for i, (s_, t_, k_, _) in enumerate(mn["trades"]):
        if s_ in BLOC6: by.setdefault(s_, []).append((i, (s_, t_, k_)))
    with Pool(2) as pool: res = [z for r in pool.map(run_h_sym, sorted(by.items())) for z in r]
    dmax = 0.0; kmis = 0; n = 0
    for i, v, why in res:
        if v is None: kmis += int(sb["kind"][i] != 255); continue
        dmax = max(dmax, abs(sb["R"][i] / 1000 - v)); kmis += int((why == "time") != (sb["kind"][i] == 6)); n += 1
    say(dmax <= 0.0005 + 1e-9, f"bloc ON (live dials) == run_h T+L1.0 on all six bloc symbols: {n} trades, max |diff| {dmax:.4f}R (file rounding 0.0005)")
    say(kmis == 0, f"24h cut (kind 6) exactly where run_h time-stops (mismatches {kmis})")
    # 4 headline, signal-date order
    idx = sorted((i for i, *_ in res), key=lambda i: mn["trades"][i][1])
    vb = [sb["R"][i] / 1000 for i in idx if sb["kind"][i] != 255]; vl = [sl["R"][i] / 1000 for i in idx if sl["kind"][i] != 255]
    print(f"     six bloc symbols, live setup: without the bloc rules {st.mean(vl):+.4f}R/trade DD {maxdd(vl):.1f} | "
          f"with them {st.mean(vb):+.4f}R/trade DD {maxdd(vb):.1f}  (study: live -0.0738 DD 267.8, T+L1.0 -0.0031 DD 52.2)")
    print("ALL PASS" if ok_all else "SOME CHECKS FAILED")
    return ok_all


if __name__ == "__main__": sys.exit(0 if main(sys.argv[1], sys.argv[2]) else 1)
