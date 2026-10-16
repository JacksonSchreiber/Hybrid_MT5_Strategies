"""backtest_timeline_check.py - trader 2026-10-16 (Practice step-through): live/backtest.timeline replays a plan hour by hour with
plan_live.run_state; on every box frame x every plan (the menu presets / options / defaults, raw, and plan_live_parity's EXTRA plans
that exercise every lever) its end must equal backtest_engine.kern exactly (timeline raises otherwise), the banks / adds it reports must
match kern's counts, and each hour's open R must equal kern run on the frame cut at that hour (a running trade closes at the last close).
  python3 pipeline/backtest_timeline_check.py [--data data/backtest] [--limit N] [--inv]"""
from __future__ import annotations
import argparse, json, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import backtest as BT, backtest_engine as BE
from pipeline.plan_live_parity import EXTRA


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data/backtest"); ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--inv", action="store_true", help="the inverse frames"); ap.add_argument("--cut-every", type=int, default=7)
    a = ap.parse_args()
    m = json.load(open(os.path.join(a.data, "menu_v1.json")))
    plans = [p["cfg"] for p in m["presets"]] + list(m["options"].values()) + [d["cfg"] for d in m["defaults"].values()] + EXTRA
    cfgs = [BE.validate(p)[0] for p in plans]
    fr = BE.Frames(os.path.join(a.data, "frames_inv_v1" if a.inv else "frames_v1")); S = {"frames": fr, "frames_inv": fr}
    hids = sorted(fr.idx)[:: max(1, len(fr.idx) // a.limit)][: a.limit] if a.limit else sorted(fr.idx)
    t0 = time.time(); n = bad = cuts = 0
    for h in hids:
        F, ctx = fr.get(h)
        for c in cfgs:
            try: tl = BT.timeline(S, h, c, a.inv)
            except Exception as e: bad += 1; print("FAIL", h, e); continue
            P = BE.compile_cfg(c, ctx, True); r = BE.run(BE.kern, F, ctx, P); pl = tl["plan"]; hb = ctx["tm"][: F["n"]] // 60
            nb = sum(1 for e in pl["events"] for x in e["text"] if x.startswith("banked")); na = sum(1 for e in pl["events"] for x in e["text"] if "% more at" in x)
            zero_banks = sum(1 for L in c["levers"] if L["type"] == "bank" and L["pct"] == 0)
            if na != int(r[5]) or (not zero_banks and nb != int(r[4])):
                bad += 1; print("COUNT", h, "banks", nb, int(r[4]), "adds", na, int(r[5]), json.dumps(c)[:200])
            # spot-check the hourly open R against kern on the frame cut after that hour (closes at the last close = R if closed now)
            for t in range(0, len(pl["steps"]) - 1, a.cut_every):
                ends = np.r_[np.flatnonzero(hb[1:] != hb[:-1]) + 1, F["n"]]; j1 = int(ends[t])
                Fc = {k: (v[:j1] if hasattr(v, "__len__") and len(v) == F["n"] else v) for k, v in F.items()}; Fc["n"] = j1
                rc = BE.run(BE.kern, Fc, ctx, P); cuts += 1
                if abs(float(rc[0]) - pl["steps"][t]["now"]) > 1e-3: bad += 1; print("NOW", h, t, rc[0], pl["steps"][t]["now"])
            n += 1
    print(f"{n} frame x plan timelines, {cuts} hourly spot checks, {bad} problems, {time.time() - t0:.0f}s")
    sys.exit(1 if bad else 0)


if __name__ == "__main__": main()
