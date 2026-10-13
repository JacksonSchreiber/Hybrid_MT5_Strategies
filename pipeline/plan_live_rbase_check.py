"""plan_live_rbase_check.py - trader 2026-10-13: a live plan's R counts from the FILL (rbase 'fill'), not the card entry.
Replays the two box cases on their real numbers (one flat minute at the fill price, the broker's own stop):
  - EURCAD.sim #3: SELL card 1.59421 / stop 1.59648, filled 1.59214 (+0.91 card-R). Card frame: 'stop to entry when +0.5R' fires at the
    fill and the runner closes the leg (what happened). Fill frame: nothing to do.
  - AUDUSD.sim #4 (inverse BUY): card 0.69525 / stop 0.69335, filled 0.69588, bid 0.69771. Fill frame: the replay's R equals the EA's
    open R (bid - fill) / |fill - stop| = 0.723.
  python3 pipeline/plan_live_rbase_check.py   (exit 0 = PASS)"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import backtest_engine as BE
from live import plan_live as PL

SPEC = {"point": 1e-5, "digits": 5, "volume_min": 0.01, "volume_step": 0.01, "stops_level": 0}


def replay(rec: dict, bid: float, spread: float, sl_now: float):
    t0 = rec["fill"]["t"] - rec["fill"]["t"] % 60
    bars = [[t0, bid, bid, bid, bid, 0, spread]]
    F, ctx = PL.frame(bars, rec, t0, rec["fill"]["px"], [rec["card"]["entry_bar_open"]])
    P = BE.compile_cfg(PL.Runner.capped(rec), ctx)
    res = PL.run_state(F, ctx, P)
    pid = rec["legs"][0]["posid"]
    bp = {pid: {"ticket": pid, "volume": rec["full_lots"], "sl": sl_now, "price_open": rec["fill"]["px"]}}
    acts = PL.Runner.actions(PL.Runner.__new__(PL.Runner), rec, res, P, bp, {"t": t0 + 30, "bid": bid, "ask": bid + spread}, SPEC)
    return float(res[0][0]), acts


def rec_of(card: dict, plan_levers: list, fill_px: float, rbase: str | None, tp2=True) -> dict:
    r = {"signal_key": "X-1", "card": card, "plan": BE.validate({"entry": {"pct": 100}, "tp2": tp2, "levers": plan_levers})[0],
         "fill": {"t": card["entry_bar_open"] + 2970, "px": fill_px}, "full_lots": 0.07,
         "legs": [{"q": 0, "posid": 1, "src": -1, "status": "open"}], "pending": {}, "log": []}
    if rbase: r["rbase"] = rbase
    return r


def main() -> int:
    ok = True
    eurcad = {"d": -1, "e": 1.59421, "R": 0.00227, "sl": 1.59648, "tp1": 1.58967, "tp2": 1.58513, "entry_bar_open": 1791907200}
    lev = [{"type": "move", "to": 0.0, "when": 0.5, "tp1": False, "legs": "all", "src": "exit"}, {"type": "cut_avg", "at_h": 4.0, "below": -0.1},
           {"type": "trail", "every_h": 1.0, "dist": 0.5, "lower": False}, {"type": "cut_avg", "at_h": 8.0, "below": -0.1},
           {"type": "cut_avg", "at_h": 12.0, "below": 0.0}]
    for rb in (None, "fill"):
        R, acts = replay(rec_of(eurcad, lev, 1.59214, rb), 1.59214, 0.00005, 1.59648)
        verbs = [a["verb"] for a in acts]
        want = (["close"] if rb is None else [])
        good = verbs == want
        ok &= good
        print(f"EURCAD #3 {'card' if rb is None else 'fill'} frame: replay {R:+.3f}R, actions {[a['what'] for a in acts] or 'none'} -> {'ok' if good else 'FAIL'}")
    audusd = {"d": 1, "e": 0.69525, "R": 0.0019, "sl": 0.69335, "tp1": 0.69905, "tp2": 0.70285, "entry_bar_open": 1791864000}
    R, acts = replay(rec_of(audusd, [{"type": "trail", "every_h": 1.0, "dist": 0.75, "lower": False}], 0.69588, "fill"), 0.69771, 0.00001, 0.69335)
    ea = (0.69771 - 0.69588) / (0.69588 - 0.69335)
    good = abs(R - ea) < 1e-3; ok &= good
    print(f"AUDUSD #4 fill frame: replay {R:+.3f}R vs the EA's open R {ea:+.3f}R -> {'ok' if good else 'FAIL'}")
    d, e, R1 = PL.rbase(rec_of(audusd, [], 0.69588, None))
    good = (e, R1) == (0.69525, 0.0019); ok &= good
    print(f"a record with no rbase (approved before 2026-10-13) keeps the card frame: origin {e} 1R {R1} -> {'ok' if good else 'FAIL'}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__": sys.exit(main())
