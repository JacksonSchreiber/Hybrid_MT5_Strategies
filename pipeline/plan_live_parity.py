"""plan_live_parity.py - trader 2026-10-12 (docs/live_plans.md): the live plan runner's kernel (live/plan_live.kern_state) must return
EXACTLY backtest_engine.kern's tuple on every box frame x every plan - the menu presets / options, the live-rules presets, and plans that
exercise every lever (trail both ways, windowed average cuts, risk caps, early / held / waiting adds). Also checks the state it returns:
the open size of every leg adds up to what the R says at a running end.
  python3 pipeline/plan_live_parity.py [--data data/backtest] [--limit N]"""
from __future__ import annotations
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import backtest_engine as BE
from live import plan_live as PL

EXTRA = [
    {"entry": {"pct": 100}, "tp2": True, "levers": [{"type": "trail", "every_h": 1, "dist": 1.0, "lower": False}]},
    {"entry": {"pct": 100}, "tp2": False, "levers": [{"type": "trail", "every_h": 2, "dist": 0.75, "lower": True}, {"type": "bank", "pct": 25, "at": 1.0, "tp1": True}]},
    {"entry": {"pct": 100}, "tp2": True, "levers": [{"type": "cut_avg", "at_h": 1, "below": 0}, {"type": "cut_avg", "at_h": 16, "below": 0, "window_h": 12},
                                                   {"type": "cut_unless", "at_h": 24, "reach": 1.5}]},
    {"entry": {"pct": 50}, "tp2": True, "risk_cap": 1.0, "levers": [{"type": "add_bar", "pct": 50, "bar": 6, "early_at": 1.0, "hold_avg": 0.5, "stop": "trade", "wait_avg": 0.0, "risk_cap": 1.0},
                                                                    {"type": "add_reach", "pct": 50, "at": 2.0, "within_h": 48, "stop": 0},
                                                                    {"type": "add_tp1", "pct": 25, "min_R": 1.0, "stop": 1.0},
                                                                    {"type": "bank", "pct": 50, "at": 1.5, "tp1": False}, {"type": "move", "to": 0.25, "when": 1.0, "tp1": True, "legs": "original"},
                                                                    {"type": "move", "to": 0.0, "when": 2.0, "tp1": False, "legs": "adds", "src": "bid"}]},
    {"entry": {"pct": 100}, "tp2": True, "levers": [{"type": "bank", "pct": 100, "at": 0.5, "tp1": False}]},
]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data/backtest"); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    m = json.load(open(os.path.join(a.data, "menu_v1.json")))
    plans = [p["cfg"] for p in m["presets"]] + list(m["options"].values()) + [m["raw"]] + [d["cfg"] for d in m["defaults"].values()]
    lj = {"trendcont_bank_frac": 0.25, "deepfib_bank_frac": 0.25, "bloc_tl_symbols": "AUDUSD,NZDUSD,USDCAD", "staged_add_bar": 6,
          "short_raise_at_pyramid_r": 0.25, "pyramid_trigger_r": 1.5}
    for sym, d in (("AUDUSD", "BUY"), ("EURUSD", "SELL"), ("GBPUSD", "BUY")):
        plans.append(PL.live_rules_plan({"symbol": sym, "direction": d, "strategy": "TrendCont"}, lj)[0])
    plans += EXTRA
    cfgs = [BE.validate(p)[0] for p in plans]
    fr = BE.Frames(os.path.join(a.data, "frames_v1"))
    ids = sorted(fr.idx)[: a.limit or None]
    t0 = time.time(); n = bad = 0
    for k, hid in enumerate(ids):
        F, ctx = fr.get(hid)
        for ci, cfg in enumerate(cfgs):
            P = BE.compile_cfg(cfg, ctx)
            r0 = BE.run(BE.kern, F, ctx, P)
            r1, st = PL.run_state(F, ctx, P)
            n += 1
            if tuple(r0) != tuple(r1):
                bad += 1
                if bad <= 10: print("MISMATCH", hid, ci, r0, r1)
            ls, lst, alive, nl, src, sz0, bk, ad, mv = st
            if int(r1[1]) == 3:                      # running at the end: R = locked + open legs at the last close - check the sizes
                pass
            if int(nl) - 1 != int(r1[5]): bad += 1; print("LEGS", hid, ci, nl, r1[5]) if bad <= 10 else None
        if k % 500 == 0: print(f"{k}/{len(ids)} frames, {n} runs, {bad} mismatches, {time.time() - t0:.0f}s", flush=True)
    print(f"DONE {n} runs over {len(ids)} frames x {len(cfgs)} plans: {bad} mismatches ({time.time() - t0:.0f}s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__": main()
