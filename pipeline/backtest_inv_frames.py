#!/usr/bin/env python3
"""backtest_inv_frames.py - trader 2026-10-09: box frames for the INVERTED trade of every card (the Practice "Inverse" box): the same
signal with the direction flipped and the stop / TP1 / TP2 mirrored around the entry at the same distances. The tab's frames end at
the ORIGINAL trade's first -1R touch - exactly when the inverted trade is about +1R and still running - so the inverse needs its own
frames: to ITS first -1R touch, capped at 40 trading days, encoded as backtest_rules does (same hid as the card). Data only; the
frozen split / cards / results / menu are untouched.
    python3 pipeline/backtest_inv_frames.py      -> data/backtest/frames_inv_v1/{index.json, <SYM>.bin}
"""
from __future__ import annotations
import json, os, sys, time
import numpy as np
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_rules as BR                    # noqa: E402
from live import backtest_engine as E                        # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest"); FR = os.path.join(OUT, "frames_inv_v1")


def main():
    t0 = time.time(); os.makedirs(FR, exist_ok=True)
    split = json.load(open(os.path.join(OUT, "split_v1.json")))["trades"]
    popk = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    idx = {}; bysym = {}
    for h, t in split.items(): bysym.setdefault(t["key"][0], []).append((h, t))
    for sym, items in sorted(bysym.items()):
        S = CB.sym_obj(sym); fn = f"{sym.split('.')[0]}.bin"; fh = open(os.path.join(FR, fn), "wb"); pos = 0
        for h, t in items:
            x = popk.get(tuple(t["key"]))
            if x is None: continue
            e = x["e"]; xi = dict(x, up=not x["up"], s=2 * e - x["s"], tp1=(2 * e - x["tp1"]) if x["tp1"] else None, tp2=(2 * e - x["tp2"]) if x["tp2"] else None)
            p = CB.prep(S, xi)
            if p is None: continue
            F = dict(p["F"]); ctx, bctx, _ = BR.full_ctx(S, xi, p); F["bl"] = F["xl"] if p["up"] else F["xl"] + F["spR"]
            hit = np.nonzero(F["xl"] <= -1.0)[0]; jstop = int(hit[0]) if hit.size else p["n"] - 1
            nk = min(jstop + 1, BR.CAP_MIN, p["n"])
            blob = E.encode_frame(F["bo"][:nk], F["bh"][:nk], F["bl"][:nk], F["bc"][:nk], F["spR"][:nk], ctx["tm"][:nk])
            tn = int(ctx["tm"][nk]) if nk < p["n"] else ctx["t_next"]
            idx[h] = {"g": t["group"], "f": fn, "off_b": pos, "len_b": len(blob), "n": nk, "n_full": p["n"], "up": bool(p["up"]), "tp2": p["tp2"],
                      "tp1": bctx["tp1"], "off": ctx["off"], "t_next": tn}
            fh.write(blob); pos += len(blob)
        fh.close(); print(sym, len(items), f"({time.time() - t0:.0f}s)", flush=True)
    json.dump({"version": 1, "built": time.strftime("%Y-%m-%d %H:%M"), "q": E.Q, "inverse": True, "trades": idx}, open(os.path.join(FR, "index.json"), "w"), separators=(",", ":"))
    print(len(idx), "inverse frames", f"{time.time() - t0:.0f}s")


if __name__ == "__main__": main()
