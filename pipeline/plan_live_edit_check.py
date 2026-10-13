"""plan_live_edit_check.py - trader 2026-10-13: editing a RUNNING plan (live/plan_live.py versions / handoff / build_edit). Scenario checks on
a synthetic BUY (1R = 0.01, no spread) whose path runs 0 -> +2R (minute 150) -> +1.5R (200) -> +1.2R (400) -> +1.8R (599):
  A  tightening the trail 1R -> 0.5R at minute 200 moves the stop AT ONCE to the high since the last check - 0.5R, and later stops it out
  B  a cut added at minute 200 for 6 h fires at 6 h on the average since ENTRY; C  one for 1 h (passed) never fires
  D  a new stop move whose trigger was touched only BEFORE the edit does not fire; one whose trigger is under the price fires at once
  E  a bank that fired, then deleted: its banked R stays and it never fires again; F  kept unchanged: still done, never twice
  G  build_edit ids: unchanged keep their id, changed get a new one, removed listed; entry / TP2 forced; stale rev / no change refused
  H  the runner applies an edit request (plans/edits/<key>.json) and acknowledges it; the web's preview lists the stop move it would send
  python3 pipeline/plan_live_edit_check.py   (exit 0 = PASS)"""
from __future__ import annotations
import os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import backtest_engine as BE
from live import plan_live as PL
from live import common as C
from live import mt5feed

T0 = 1_790_000_000 - 1_790_000_000 % 14400; N = 600; E0 = 1.0; R1 = 0.01
OK = True


def path_r(j):
    pts = [(0, 0.0), (150, 2.0), (200, 1.5), (260, 1.5), (400, 1.2), (599, 1.8)]
    for (a, ra), (b, rb) in zip(pts, pts[1:]):
        if a <= j <= b: return ra + (rb - ra) * (j - a) / (b - a)
    return pts[-1][1]


BARS = [[T0 + j * 60, E0 + path_r(j) * R1, E0 + (path_r(j) + 0.02) * R1, E0 + (path_r(j) - 0.02) * R1, E0 + path_r(j) * R1, 0, 0.0] for j in range(N)]
H4 = [T0 + k * 14400 for k in range(4)]


def rec_of(levers, ids=None):
    plan = PL.live_validate({"entry": {"pct": 100}, "tp2": False, "levers": levers})[0]
    return {"signal_key": "SIM.sim-1", "symbol": "SIM.sim", "status": "running", "rbase": "fill", "plan": plan, "lever_ids": PL.lever_ids(plan, ids), "rev": 0,
            "card": {"d": 1, "e": E0, "R": R1, "sl": E0 - R1, "tp1": None, "tp2": E0 + 4 * R1, "entry_bar_open": T0},
            "fill": {"t": T0, "px": E0}, "full_lots": 1.0, "legs": [{"q": 0, "posid": 7, "src": -1, "status": "open"}], "banks": [], "pending": {}, "log": []}


def edit(rec, levers, ids, minute):
    v = PL.build_edit(rec, {"entry": {"pct": 100}, "tp2": False, "levers": levers}, ids, rec.get("rev", 0), T0 + minute * 60)
    PL.commit_edit(rec, v); return v


def run(rec, upto=N):
    F, ctx = PL.frame(BARS[:upto], rec, T0, E0, H4)
    return PL.replay(rec, F, ctx, T0), F, ctx


def check(name, cond, info=""):
    global OK
    OK &= bool(cond); print(f"{'ok  ' if cond else 'FAIL'} {name} {info}")


def main() -> int:
    hi = lambda a, b: max(path_r(j) + 0.02 for j in range(a, b + 1))
    # A: trail 1R -> 0.5R at minute 200
    base = [{"type": "trail", "every_h": 1, "dist": 1.0, "lower": False}]
    r = rec_of(base); (res0, S0, P0, vi0), F, ctx = run(r)
    check("A0 trail 1R alone survives the dip to +1.2R", int(res0[1]) == 3, f"kind {res0[1]} R {res0[0]:+.3f}")
    r = rec_of(base); edit(r, [{"type": "trail", "every_h": 1, "dist": 0.5, "lower": False}], ["l0"], 200)
    (res, S, P, vi), F, ctx = run(r, 201)
    want = hi(181, 200) - 0.5
    check("A1 tightened trail moves the stop at the edit minute", abs(S["lst"][0] - want) < 1e-9, f"stop {S['lst'][0]:+.3f}R want {want:+.3f}R")
    (res, S, P, vi), F, ctx = run(r)
    check("A2 ... and then stops the trade out on the dip", int(res[1]) == 0 and res[0] >= want - 1e-9, f"kind {res[1]} R {res[0]:+.3f}")
    # B / C: cuts added at minute 200
    r = rec_of([]); edit(r, [{"type": "cut_avg", "at_h": 6, "below": 5.0}], [None], 200)
    (res, S, P, vi), F, ctx = run(r)
    kk = BE.mark(ctx, 6, cut=True); avg = float(np.mean(np.asarray(F["bc"][:kk + 1])))
    check("B  a future cut fires at its time on the average since entry", int(res[1]) == 2 and int(res[2]) == kk, f"kind {res[1]} at minute {res[2]} (6 h = {kk}), avg {avg:+.2f}R")
    r = rec_of([]); edit(r, [{"type": "cut_avg", "at_h": 1, "below": 5.0}], [None], 200)
    (res, S, P, vi), F, ctx = run(r)
    check("C  a cut whose time passed never fires", int(res[1]) == 3, f"kind {res[1]}")
    # D: stop moves added at minute 200 (price +1.5R; +2R was touched at 150 only)
    r = rec_of([]); edit(r, [{"type": "move", "to": 0.5, "when": 1.9, "tp1": False, "legs": "all", "src": "exit"}], [None], 200)
    (res, S, P, vi), F, ctx = run(r, 300)
    check("D1 a trigger touched only before the edit does not fire", abs(S["lst"][0] + 1.0) < 1e-9, f"stop {S['lst'][0]:+.3f}R")
    r = rec_of([]); edit(r, [{"type": "move", "to": 0.5, "when": 1.4, "tp1": False, "legs": "all", "src": "exit"}], [None], 200)
    (res, S, P, vi), F, ctx = run(r, 201)
    check("D2 a trigger under the price fires at the edit minute", abs(S["lst"][0] - 0.5) < 1e-9, f"stop {S['lst'][0]:+.3f}R")
    # E / F: a bank that fired at +1R (minute 75)
    bank = [{"type": "bank", "pct": 50, "at": 1.0, "tp1": False}]
    r = rec_of(bank); edit(r, [], [], 200)
    (res, S, P, vi), F, ctx = run(r)
    want = 0.5 * 1.0 + 0.5 * (path_r(N - 1) - 0.0)
    check("E  a fired bank, deleted: its banked R stays, never again", abs(res[0] - want) < 1e-6 and abs(S["ls"][0] - 0.5) < 1e-12, f"R {res[0]:+.4f} want {want:+.4f} size {S['ls'][0]}")
    r = rec_of(bank); edit(r, bank + [{"type": "cut_avg", "at_h": 9, "below": -5.0}], ["l0", None], 200)
    (res, S, P, vi), F, ctx = run(r)
    check("F  a fired bank kept unchanged stays done (half the size, once)", abs(S["ls"][0] - 0.5) < 1e-12 and bool(S["bk_done"][0]), f"size {S['ls'][0]} done {bool(S['bk_done'][0])}")
    # G: build_edit bookkeeping
    r = rec_of(bank + [{"type": "trail", "every_h": 1, "dist": 1.0, "lower": False}])
    v = PL.build_edit(r, {"entry": {"pct": 30}, "tp2": True, "levers": [bank[0], {"type": "trail", "every_h": 1, "dist": 0.5, "lower": False},
                                                                      {"type": "cut_avg", "at_h": 8, "below": 0}]}, ["l0", "l1", "x9"], 0, T0 + 200 * 60)
    check("G1 ids: unchanged kept, changed and new get new ids", v["ids"][0] == "l0" and v["ids"][1] == "e1.1" and v["ids"][2] == "e1.2", str(v["ids"]))
    check("G2 entry % and TP2 stay as entered", v["plan"]["entry"]["pct"] == 100 and v["plan"]["tp2"] is False)
    check("G3 the changes in words", any(x.startswith("changed:") for x in v["about"]) and any(x.startswith("added:") for x in v["about"]), "; ".join(v["about"]))
    for nm, args in (("stale rev", (r["plan"], r["lever_ids"], 5)), ("nothing changed", (r["plan"], r["lever_ids"], 0))):
        try: PL.build_edit(r, args[0], args[1], args[2], T0); check(f"G4 refused: {nm}", False)
        except ValueError as e: check(f"G4 refused: {nm}", True, f"({e})")
    # H: the runner applies a request; the web preview shows the stop move it would send
    root = tempfile.mkdtemp(); cfg = {"root": root}
    r = rec_of(base); r["plan_key"] = r["signal_key"]; PL.save_plan(cfg, r)
    j = 205
    mt5feed.tick = lambda sym: {"t": T0 + j * 60 + 20, "bid": BARS[j][4], "ask": BARS[j][4]}
    mt5feed.spec = lambda sym: {"point": 1e-5, "digits": 5, "volume_min": 0.01, "volume_step": 0.01, "stops_level": 0}
    mt5feed.bars_sp = lambda sym, tf, t_from=None, t_to=None, n=None: (([[x, 0, 0, 0, 0, 0, 0] for x in H4 if x <= T0 + j * 60]) if tf == "h4" else
                                                                        (BARS[max(0, j + 1 - n):j + 1] if n else [b for b in BARS[:j + 1] if t_from is None or b[0] >= t_from]))
    sl_now = E0 + R1 * float(run(rec_of(base), j + 1)[0][1]["lst"][0])
    mt5feed.positions = lambda: [{"ticket": 7, "symbol": "SIM.sim", "volume": 1.0, "sl": sl_now, "price_open": E0, "time": T0}]
    new = [{"type": "trail", "every_h": 1, "dist": 0.5, "lower": False}]
    pv = PL.preview_edit(cfg, r, {"entry": {"pct": 100}, "tp2": False, "levers": new}, ["l0"], 0)
    check("H1 the preview lists the stop move the runner would send", pv.get("ok") and any("stop" in x for x in pv.get("now", [])), str(pv.get("now") or pv))
    os.makedirs(os.path.dirname(PL.edit_path(cfg, r["signal_key"])), exist_ok=True)
    C.atomic_write_json(PL.edit_path(cfg, r["signal_key"]), {"id": "req1", "plan": {"entry": {"pct": 100}, "tp2": False, "levers": new}, "ids": ["l0"], "base_rev": 0})
    run_ = PL.Runner(cfg, log=lambda m: None); applied = run_.apply_edit(r, T0 + j * 60 + 20)
    check("H2 the runner applies the request and acknowledges it", applied and r["rev"] == 1 and r["edit_last"]["ok"] and r["edit_last"]["id"] == "req1"
          and not os.path.exists(PL.edit_path(cfg, r["signal_key"])), r["log"][-1]["msg"] if r["log"] else "")
    print("RESULT:", "PASS" if OK else "FAIL")
    return 0 if OK else 1


if __name__ == "__main__": sys.exit(main())
