#!/usr/bin/env python3
"""backtest_rules.py - trader 2026-10-04 (backtest tab, phase 2): the menu as lever stacks, the parity proof of the generic rule
engine (live/backtest_engine.py) against the preset kernel (pipeline/backtest_presets.py), the box frames, the precomputed results
and the Practice-only info window. The comparison is ALWAYS the raw signal (trader ruling 2026-10-04: no live-stack numbers here).

  python3 pipeline/backtest_rules.py --menu      write data/backtest/menu_v1.json only
  python3 pipeline/backtest_rules.py --unit      synthetic-path tests of the engine features parity does not cover + the advisor schema
  python3 pipeline/backtest_rules.py             everything:
      PARITY   the engine (numba-compiled from the SAME Python source the box runs) == backtest_presets.kern on every preset, option
               and ladder, every trade, full float64 frames, strict_tp2 off (the preset kernel's behaviour) -> max |diff| must be 0
      STRICT   how many trades change with strict_tp2 on (a bank above TP2 / an add at or above TP2 can only fire in TP2's minute -
               the engine skips them; the preset kernel lets them fire)
      FRAMES   data/backtest/frames_v1/{index.json, com.bin, maj.bin}: per trade the M1 frame from the entry to the first -1R touch
               (every leg is closed by then: no stop is ever below -1R), capped at 40 trading days, as int32 R x 1e6, delta + zlib
      BOX      the plain-Python kernel (what the box runs) == the numba kernel on decoded frames (exact), and its time per trade
      RESULTS  data/backtest/results_v1.json.gz (server-side only, never sent before a decision): per trade R under raw, every menu
               preset, every option and the group default (on the decoded frames, so the box's custom-plan R is consistent), banked
               +1R, hit TP2, exit kinds, the post-signal H4 path for the reveal (unsigned R, as the card)
      INFO     data/backtest/info_v1.json: per group, Practice-only lines, each tagged "tested" or "observed"
"""
from __future__ import annotations
import gzip, json, math, os, statistics as st, sys, time, zlib
import numpy as np
from numba import njit
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import backtest_presets as BP                  # noqa: E402
from live import backtest_engine as E                        # noqa: E402
from live import symbol_groups as SG                         # noqa: E402

MENU_VERSION = "v1"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest")
FR = os.path.join(OUT, "frames_v1")
CAP_DAYS = 40; CAP_MIN = CAP_DAYS * 1440; NBARS = 61; REVEAL_BARS = 60
NB = njit(cache=True)(E.kern)

# ------------------------------------------------------------------------------------------------------------- the menu
BANK1 = {"type": "bank", "pct": 25, "at": 1.0, "tp1": True}
MOVE_BE = {"type": "move", "to": 0.0, "when": 1.0, "tp1": True, "legs": "all", "src": "exit"}
ADD2 = {"type": "add_reach", "pct": 75, "at": 2.0, "within_h": 24, "stop": 0.0}
CUT1H = {"type": "cut_avg", "at_h": 1, "below": 0.0}


def stack(levers, entry=100, tp2=True, trim=False):
    return {"schema": E.SCHEMA, "entry": {"pct": entry}, "tp2": tp2, "cap_pct": 200, "trim_to_cap": trim, "levers": levers}


def ladder(rungs, stops_only=False):
    out = [dict(MOVE_BE)]                                    # Minimal's stop-to-entry at the +1R trigger stays (the preset kernel)
    for lv, f, s in rungs:
        if not stops_only: out.append({"type": "bank", "pct": round(f * 100, 6), "at": lv, "tp1": False})
        if not (lv == 1.0 and s == 0.0):                     # rung 1's stop-to-entry duplicates the +1R trigger's (always earlier)
            out.append({"type": "move", "to": s, "when": lv, "tp1": False, "legs": "all", "src": "exit"})
    return out + [dict(ADD2)]


MENU = {
    "minimal": {"label": "Minimal", "about": "100% at the signal; bank 25% and stop to entry at +1R; +75% if +2R within 24 h (its stop at entry)",
                "cfg": stack([dict(BANK1), dict(MOVE_BE), dict(ADD2)])},
    "ruthless": {"label": "Ruthless cutter", "about": "Minimal + close at 1 h if the average R is negative; stop to +0.25R (not entry) at +1R",
                 "cfg": stack([dict(BANK1), dict(MOVE_BE, to=0.25), dict(ADD2), dict(CUT1H)])},
    "slow": {"label": "Slow burner", "about": "25% at the signal, +75% at the bar-6 close or early at +1R (held while the average R < +0.5R); "
                                              "stop to +0.25R at +1R; Minimal's +2R add on top",
             "cfg": stack([dict(BANK1), dict(MOVE_BE, to=0.25), {"type": "add_bar", "pct": 75, "bar": 6, "early_at": 1.0, "hold_avg": 0.5, "stop": "trade"},
                           dict(ADD2)], entry=25)},
    "banker": {"label": "Protective banker", "about": "ladder (frozen for Test round 1): bank 25% at +1R (stop entry), +2R (stop +1R), +3R (stop +2R); Minimal's +2R add",
               "cfg": stack(ladder(BP.LADDERS[BP.BANKER_DEFAULT]))},
    "pyramid": {"label": "Pyramid", "about": "stop to -0.5R at +0.5R, entry at +1R; at TP1 add 50% (its stop +1R) on top of Minimal's add; trimmed to the 200% cap",
                "cfg": stack([dict(BANK1), dict(MOVE_BE), {"type": "move", "to": -0.5, "when": 0.5, "tp1": False, "legs": "all", "src": "bid"},
                              dict(ADD2), {"type": "add_tp1", "pct": 50, "min_R": 1.0, "stop": 1.0}], trim=True)},
}
OPTIONS = {
    "slow_nohold": stack([dict(BANK1), dict(MOVE_BE, to=0.25), {"type": "add_bar", "pct": 75, "bar": 6, "early_at": 1.0, "hold_avg": None, "stop": "trade"},
                          dict(ADD2)], entry=25),
    "banker_stops": stack(ladder(BP.LADDERS[BP.BANKER_DEFAULT], True)),
    "minimal_noadd": stack([dict(BANK1), dict(MOVE_BE)]),
    **{f"banker:{k}": stack(ladder(v)) for k, v in BP.LADDERS.items()},
    **{f"banker_stops:{k}": stack(ladder(v, True)) for k, v in BP.LADDERS.items()},
}
RAW = stack([])
# coach 2026-10-04: the GRADING COMPARATOR is Minimal for both groups (no approved group traits yet). The bloc's 1h cut is the trader's
# own plan, not an approved tested trait: it stays the PRELOADED plan on bloc cards, labelled as his, and its info line is "observed".
COMPARATOR = "minimal"
DEFAULTS = {"com": {"preset": "com_default", "label": "Your simple rules (Minimal + 1h cut)", "about": "Your plan: Minimal + close at 1 h if the average "
                                                                                      "R is negative ('cut bad trades quickly') - not an approved trait",
                    "cfg": stack([dict(BANK1), dict(MOVE_BE), dict(ADD2), dict(CUT1H)]),
                    "status": "your plan, preloaded - not an approved trait (coach 2026-10-04); the grading comparator is Minimal"},
            "maj": {"preset": "minimal", "label": "Minimal", "about": "Minimal (no approved group traits)", "cfg": MENU["minimal"]["cfg"],
                    "status": "no approved traits - the grading comparator is Minimal"}}
PARITY = {"minimal": "minimal", "ruthless": "ruthless", "slow": "slow", "banker": "banker", "pyramid": "pyramid", "slow_nohold": "slow_nohold",
          "banker_stops": "banker_stops", "minimal_noadd": "minimal_noadd", "com_trait": "com_default", "raw": "raw",
          **{f"banker:{k}": f"banker:{k}" for k in BP.LADDERS}, **{f"banker_stops:{k}": f"banker_stops:{k}" for k in BP.LADDERS}}


def cfg_of(name):
    if name == "raw": return RAW
    if name in MENU: return MENU[name]["cfg"]
    if name in OPTIONS: return OPTIONS[name]
    if name == "com_default": return DEFAULTS["com"]["cfg"]
    raise KeyError(name)


def write_menu():
    doc = {"version": MENU_VERSION, "schema": E.SCHEMA, "built": time.strftime("%Y-%m-%d %H:%M"),
           "presets": [{"key": k, "label": v["label"], "about": v["about"], "cfg": E.validate(v["cfg"])[0]} for k, v in MENU.items()],
           "options": {k: E.validate(v)[0] for k, v in OPTIONS.items()},
           "raw": E.validate(RAW)[0],
           "defaults": {g: {kk: (E.validate(vv)[0] if kk == "cfg" else vv) for kk, vv in d.items()} for g, d in DEFAULTS.items()},
           "note": "The comparison is always the raw signal (trader 2026-10-04). Menu frozen within a Test round; a change = a new version.",
           "frozen": {"at": "2026-10-05", "by": "trader 2026-10-05", "banker": "L1 thirds",
                      "note": "menu v1 frozen for Test round 1; any change = a new menu version and a new Test round (rounds are not pooled)"}}
    json.dump(doc, open(os.path.join(OUT, "menu_v1.json"), "w"), indent=1)
    return doc


# ------------------------------------------------------------------------------------------------------------- per trade
def full_ctx(S, x, p):
    h4, i0, k0, n = x["bars"], p["i0"], p["k0"], p["n"]
    t0 = A.tmin(h4[i0][0])
    tm = S.t[k0:k0 + n].astype(np.int64) - t0
    t_next = int(S.t[k0 + n]) - t0 if k0 + n < len(S.t) else 1 << 50
    off = [A.tmin(h4[i0 + b][0]) - t0 if i0 + b < len(h4) else None for b in range(NBARS)]
    bctx = BP.context(S, x, p)
    return {"e0": p["e0"], "be": p["be"], "tp2": p["tp2"], "tp1": bctx["tp1"], "off": off, "tm": tm, "t_next": t_next, "n_full": n,
            "up": p["up"]}, bctx, t0


def reveal_bars(F, ctx, up):
    """post-signal H4 bars from the M1 frame, unsigned R (a short's SL at +1, as the card), up to REVEAL_BARS bars."""
    out = []; prev = -1
    for b in range(1, REVEAL_BARS + 1):
        k = E.mark(ctx, 4.0 * b)
        if k >= F["n"] or k <= prev: break
        sl = slice(prev + 1, k + 1)
        o, h, l, c = float(F["bo"][prev + 1]), float(F["bh"][sl].max()), float(F["bl"][sl].min()), float(F["bc"][k])
        out.append([round(o, 3), round(h, 3), round(l, 3), round(c, 3)] if up else [round(-o, 3), round(-l, 3), round(-h, 3), round(-c, 3)])
        prev = k
    return out


SPLIT: dict = {}; POPK: dict = {}
NAMES = ["raw", "com_default"] + list(MENU) + list(OPTIONS)


def do_symbol(sym):
    S = CB.sym_obj(sym); out = []; fn = f"{sym.split('.')[0]}.bin"; fh = open(os.path.join(FR, fn), "wb"); pos = 0
    for hid, t in SPLIT["trades"].items():
        if t["key"][0] != sym: continue
        x = POPK[tuple(t["key"])]; p = CB.prep(S, x)
        if p is None: continue
        F = dict(p["F"]); ctx, bctx, t0 = full_ctx(S, x, p)
        F["bl"] = F["xl"] if p["up"] else F["xl"] + F["spR"]
        # PARITY: the preset kernel vs the engine (numba), full frames, strict off
        par = {}
        for bn, en in PARITY.items():
            rb = BP.run(F, bctx, bn); re_ = E.run(NB, F, ctx, E.compile_cfg(E.validate(cfg_of(en))[0], ctx, strict_tp2=False))
            par[bn] = (abs(float(rb[0]) - float(re_[0])), int(rb[1]) == int(re_[1]), abs(float(rb[3]) - float(re_[3])))
        # full-frame results (strict on and off) for the strict / trim comparisons
        full_s = {nm: float(E.run(NB, F, ctx, E.compile_cfg(E.validate(cfg_of(nm))[0], ctx, True))[0]) for nm in NAMES}
        full_n = {nm: float(E.run(NB, F, ctx, E.compile_cfg(E.validate(cfg_of(nm))[0], ctx, False))[0]) for nm in NAMES}
        # the box frame
        hit = np.nonzero(F["xl"] <= -1.0)[0]; jstop = int(hit[0]) if hit.size else p["n"] - 1
        nk = min(jstop + 1, CAP_MIN, p["n"])
        blob = E.encode_frame(F["bo"][:nk], F["bh"][:nk], F["bl"][:nk], F["bc"][:nk], F["spR"][:nk], ctx["tm"][:nk])
        tn = int(ctx["tm"][nk]) if nk < p["n"] else ctx["t_next"]
        meta = {"g": t["group"], "f": fn, "off_b": pos, "len_b": len(blob), "n": nk, "n_full": p["n"], "up": bool(p["up"]), "tp2": p["tp2"],
                "tp1": bctx["tp1"], "off": ctx["off"], "t_next": tn}
        fh.write(blob); pos += len(blob)
        Fd, cd = E.decode_frame(blob, meta)
        box = {}; rows = {}
        for nm in NAMES:
            r = E.run(NB, Fd, cd, E.compile_cfg(E.validate(cfg_of(nm))[0], cd, True))
            box[nm] = (float(r[0]), int(r[1]), int(r[2]), float(r[3]), int(r[4]), int(r[5]), int(r[6]))
        rv = reveal_bars(F, ctx, p["up"])
        ps = BP.path_stats(F, bctx)
        stop_pips = abs(x["e"] - x["s"]) / (10 * A.TICK.get(sym, 1e-5))          # for the +1 pip cost summary - server-side only, never in a card
        out.append((hid, {"meta": meta, "par": par, "full_s": full_s, "full_n": full_n, "box": box, "reveal": rv, "stop_pips": stop_pips,
                          "banked": box["minimal"][4] > 0, "tp2_raw": bool(ps["tp2_raw"]), "ps": ps, "trimmed": jstop + 1 > CAP_MIN and p["n"] > CAP_MIN}))
    fh.close()
    return out


def unit_tests() -> bool:
    """synthetic-path checks of the engine features the preset parity does not exercise, run with the plain-Python kernel (the box path)."""
    ok = True
    def say(c, msg):
        nonlocal ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + msg)
    def frame(rows):                                   # rows: (low, high, close) per minute in R, long, no spread
        lo = np.array([a for a, _, _ in rows]); hi = np.array([b for _, b, _ in rows]); cl = np.array([c for _, _, c in rows])
        op = np.concatenate([[0.0], cl[:-1]]); n = len(cl)
        F = {"xo": op, "xh": hi, "xl": lo, "xc": cl, "bo": op, "bh": hi, "bc": cl, "eo": np.zeros(n), "n": n}
        ctx = {"e0": 0.0, "be": 0.0, "tp2": 4.0, "tp1": 2.0, "off": [i * 240 for i in range(NBARS)], "tm": np.arange(n, dtype=np.int64),
               "t_next": n, "n_full": n, "up": True}
        return F, ctx
    # cut_unless: never reaches +1.5R by 24 h (minute 1439) -> closed there at the close
    F, c = frame([(-0.2, 0.6, 0.3)] * 2000)
    r = E.evaluate(stack([{"type": "cut_unless", "at_h": 24, "reach": 1.5}]), F, c)
    say(r["kind"] == 2 and r["exit_j"] == 1439 and abs(r["R"] - 0.3) < 1e-12, f"cut_unless closes at the 24 h minute ({r['exit_j']}) at {r['R']:+.2f}R")
    F, c = frame([(-0.2, 0.6, 0.3)] * 100 + [(0.3, 1.6, 1.5)] + [(1.0, 1.4, 1.2)] * 2000)
    r = E.evaluate(stack([{"type": "cut_unless", "at_h": 24, "reach": 1.5}]), F, c)
    say(r["kind"] != 2, "cut_unless does not fire once +1.5R was reached")
    # legs: a move on the ORIGINAL only leaves the add's stop alone
    F, c = frame([(0.0, 1.1, 1.0), (1.05, 2.1, 2.0), (1.4, 2.0, 1.5), (0.4, 1.6, 0.5), (0.4, 4.1, 4.0)])
    cfg = stack([{"type": "add_reach", "pct": 50, "at": 2.0, "within_h": None, "stop": -1}, {"type": "move", "to": 1.0, "when": 1.0, "tp1": False, "legs": "original", "src": "exit"}])
    r = E.evaluate(cfg, F, c)
    exp = 1.0 * 1.0 + 0.5 * (4.0 - 2.0)                # the original stopped at +1R in minute 3 (low 0.4); the add (stop -1R) runs to TP2
    say(abs(r["R"] - exp) < 1e-12 and r["kind"] == 1, f"move legs=original: R {r['R']:+.3f} (expected {exp:+.3f})")
    # strict_tp2: an add at/above TP2 never fires
    F, c = frame([(0.0, 4.5, 4.4)]); r1 = E.evaluate(stack([{"type": "add_reach", "pct": 50, "at": 4.2, "within_h": None, "stop": 0}]), F, c)
    r0 = E.evaluate(stack([{"type": "add_reach", "pct": 50, "at": 4.2, "within_h": None, "stop": 0}]), F, c, strict_tp2=False)
    say(r1["adds"] == 0 and r0["adds"] == 1, "strict_tp2: an add above TP2 is skipped (fires only with strict_tp2 off)")
    # wait_avg (trader 2026-10-06): the bar-6 close add waits while the average R is below it, independent of early_at
    W = lambda wa, **k: stack([dict({"type": "add_bar", "pct": 75, "bar": 6, "early_at": None, "hold_avg": None, "stop": "trade", "wait_avg": wa}, **k)], entry=25)
    F, c = frame([(-0.5, -0.1, -0.3)] * 3000)
    r0, r1 = E.evaluate(W(None), F, c), E.evaluate(W(0.0), F, c)
    say(r0["adds"] == 1 and r1["adds"] == 0 and abs(r1["R"] - 0.25 * -0.3) < 1e-12,
        f"wait_avg: the average never recovers -> the add never goes in ({r1['R']:+.3f}R; without the wait it goes in at the bar close: {r0['R']:+.3f}R)")
    F, c = frame([(-0.5, -0.1, -0.3)] * 1500 + [(0.9, 1.1, 1.0)] * 1000 + [(1.9, 2.1, 2.0)] * 500)
    r1 = E.evaluate(W(0.0), F, c); exp = 0.25 * 2.0 + 0.75 * (2.0 - 1.0)     # avg reaches 0 at minute 1949 (close +1.0) -> in there
    say(r1["adds"] == 1 and abs(r1["R"] - exp) < 1e-12, f"wait_avg: goes in the first minute after the bar that the average reaches it: R {r1['R']:+.3f} (expected {exp:+.3f})")
    F, c = frame([(-0.1, 0.6, 0.5)] * 3000)
    r0, r1 = E.evaluate(W(None), F, c), E.evaluate(W(0.25), F, c)
    say(r0 == r1 and r1["adds"] == 1, "wait_avg: an average already at/above the level at the bar close -> the add goes in at the bar close as before")
    F, c = frame([(-0.5, -0.1, -0.3)] * 1500 + [(0.9, 1.1, 1.0)] * 1000 + [(1.9, 2.1, 2.0)] * 500)
    r1 = E.evaluate(W(0.0, early_at=2.0, hold_avg=1.0), F, c)
    say(abs(r1["R"] - exp) < 1e-12, "wait_avg with early_at + hold_avg: the early add never fires before the bar, the bar-close add still waits")
    try: E.validate(W(0.5)); okw = True
    except ValueError: okw = False
    say(okw and "wait_avg" not in E.validate(W(None))[0]["levers"][0], "validation: wait_avg needs no early add; unset, it is left out of the normalised plan")
    # risk_cap (trader 2026-10-07): when the bar add goes in, the open risk stays at or below the cap (every open leg's stop raised to one level)
    K = lambda rc, stop="trade", pre=(), post=(): stack(list(pre) + [{"type": "add_bar", "pct": 75, "bar": 6, "early_at": None, "hold_avg": None,
                                                                      "stop": stop, "risk_cap": rc}] + list(post), entry=25)
    F, c = frame([(0.3, 0.6, 0.5)] * 1440 + [(-0.7, 0.5, -0.6), (-1.1, -0.6, -1.0)])     # in at +0.5R: 0.25 x 1 + 0.75 x 1.5 = 1.375R open
    r0, r1 = E.evaluate(K(None), F, c), E.evaluate(K(1.0), F, c)
    say(abs(r1["R"] + 1.0) < 1e-12 and r1["exit_j"] == 1440 and abs(r0["R"] + 1.375) < 1e-12,
        f"risk_cap 1R: the add at +0.5R raises the stop to -0.625R, a stop-out loses {r1['R']:+.3f}R (without the cap {r0['R']:+.3f}R)")
    F, c = frame([(-0.5, -0.1, -0.3)] * 1440 + [(-1.1, -0.2, -1.0)])
    say(E.evaluate(K(None), F, c) == E.evaluate(K(1.0), F, c), "risk_cap: an add below entry (open risk under 1R) leaves the stop alone")
    F, c = frame([(0.3, 0.6, 0.5)] * 1440 + [(0.4, 2.1, 2.0), (-0.7, 1.0, -0.6)])
    r1 = E.evaluate(K(1.0, post=[{"type": "add_reach", "pct": 50, "at": 2.0, "within_h": None, "stop": "trade"}]), F, c)
    exp = 0.25 * -0.625 + 0.75 * (-0.625 - 0.5) + 0.5 * (-0.625 - 2.0)
    say(abs(r1["R"] - exp) < 1e-12, f"risk_cap: a later add with the trade's stop takes the raised stop: R {r1['R']:+.4f} (expected {exp:+.4f})")
    F, c = frame([(-0.2, 0.5, 0.3)] * 100 + [(0.3, 1.1, 1.0)] + [(0.6, 1.6, 1.5)] * 1339 + [(-0.1, 1.5, 0.0)])
    r1 = E.evaluate(K(1.0, stop=-1, pre=[{"type": "move", "to": 0.5, "when": 1.0, "tp1": False, "legs": "original", "src": "exit"}]), F, c)
    say(abs(r1["R"] + 1.0) < 1e-12, f"risk_cap: a leg already above the level keeps its stop, the rest go to entry so the total is 1R: {r1['R']:+.3f}R")
    F, c = frame([(-0.2, 0.5, 0.3)] * 100 + [(0.3, 3.1, 3.0)] + [(0.9, 1.5, 1.0)] * 1400)
    pre = [{"type": "add_reach", "pct": 75, "at": 3.0, "within_h": None, "stop": 0}]
    r0, r1 = E.evaluate(K(None, pre=pre), F, c), E.evaluate(K(1.0, pre=pre), F, c)
    say(r0["adds"] == 2 and r1["adds"] == 1, "risk_cap: when the stop would have to sit above the price, the add is skipped")
    try: E.validate(K(0.05)); bad = False
    except ValueError: bad = True
    say(bad and "risk_cap" not in E.validate(K(None))[0]["levers"][0] and E.validate(K(1))[0]["levers"][0]["risk_cap"] == 1.0,
        "validation: risk_cap 0.1-10R; unset, it is left out of the normalised plan")
    # validation
    try: E.validate(stack([dict(ADD2), dict(ADD2)])); bad = False
    except ValueError: bad = True
    say(bad, "validation refuses 100 + 75 + 75 = 250% without trim_to_cap")
    try: E.validate(stack([dict(ADD2), dict(ADD2)], trim=True)); okt = True
    except ValueError: okt = False
    say(okt, "validation accepts it with trim_to_cap (later adds trimmed)")
    try: E.validate(stack([{"type": "teleport"}])); bad = False
    except ValueError: bad = True
    say(bad, "validation refuses an unknown lever")
    try: E.validate(stack([{"type": "move", "to": -2, "when": 1, "tp1": False, "legs": "all", "src": "exit"}])); bad = False
    except ValueError: bad = True
    say(bad, "validation refuses a stop below -1R")
    # the advisor schema
    from live import backtest as BT
    good = '```levers\n{"schema": "advisor-levers/v1", "nearest_preset": "ruthless", "rationale": "cut fast", "plan": {"entry": {"pct": 100}, "levers": [{"type": "cut_avg", "at_h": 1, "below": 0}]}}\n```'
    say(BT.parse_recommendation(good)["nearest_preset"] == "ruthless", "advisor-levers/v1 block parses")
    for txt, why in (('```levers\n{"schema": "x"}\n```', "schema"), ('```levers\n{"schema": "advisor-levers/v1", "nearest_preset": "hero", "rationale": "x", "plan": {}}\n```', "preset"),
                     ('```levers\n{"schema": "advisor-levers/v1", "nearest_preset": "slow", "rationale": "x", "plan": {"entry": {"pct": 100}, "levers": [{"type": "add_reach", "pct": 150, "at": 2, "stop": 0}]}}\n```', "cap"),
                     ("no block here", "no block")):
        try: BT.parse_recommendation(txt); bad = False
        except ValueError: bad = True
        say(bad, f"advisor recommendation rejected ({why})")
    return ok


def maxdd(v):
    pk = cur = dd = 0.0
    for r in v: cur += r; pk = max(pk, cur); dd = max(dd, pk - cur)
    return dd


def main():
    global SPLIT, POPK
    T0 = time.time(); os.makedirs(FR, exist_ok=True)
    if "--unit" in sys.argv: return unit_tests()
    menu = write_menu()
    if "--menu" in sys.argv: print("menu written"); return True
    ok_all = True
    def say(c, msg):
        nonlocal ok_all; ok_all &= bool(c); print(("PASS " if c else "FAIL ") + msg, flush=True)
    SPLIT = json.load(open(os.path.join(OUT, "split_v1.json")))
    POPK = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    syms = sorted({t["key"][0] for t in SPLIT["trades"].values()})
    from multiprocessing import Pool
    with Pool(3) as pool: R = {h: v for r in pool.map(do_symbol, syms) for h, v in r}
    print(f"{len(R)} trades replayed ({time.time() - T0:.0f}s)", flush=True)
    # PARITY
    for bn in PARITY:
        d = max(v["par"][bn][0] for v in R.values()); kd = sum(1 for v in R.values() if not v["par"][bn][1]); gd = max(v["par"][bn][2] for v in R.values())
        say(d <= 1e-9 and kd == 0 and gd <= 1e-12, f"parity {bn:24} engine == backtest_presets: max |R diff| {d:.1e}, exit-kind mismatches {kd}, gross diff {gd:.1e}")
    # STRICT
    for nm in NAMES:
        ch = [abs(v["full_s"][nm] - v["full_n"][nm]) for v in R.values()]
        nch = sum(1 for c in ch if c > 1e-12)
        if nch: print(f"     strict_tp2 changes {nm}: {nch} trades, max {max(ch):.4f}R, mean over all {np.mean(ch):.5f}R")
    print("     strict_tp2: every other menu entry unchanged")
    # FRAMES
    idx = {h: v["meta"] for h, v in R.items()}
    json.dump({"version": 1, "built": time.strftime("%Y-%m-%d %H:%M"), "cap_days": CAP_DAYS, "q": E.Q, "split_sha": SPLIT["sha256"], "trades": idx},
              open(os.path.join(FR, "index.json"), "w"), separators=(",", ":"))
    sizes = {f: os.path.getsize(os.path.join(FR, f)) for f in os.listdir(FR)}
    print(f"     frames: {len(idx)} trades, {sum(v['meta']['n'] for v in R.values()):,} minutes, {sum(sizes.values()) / 1e6:.1f} MB on disk in {len(sizes)} files "
          f"({', '.join(f'{k} {v / 1e6:.1f}' for k, v in sorted(sizes.items()))} MB); {sum(1 for v in R.values() if v['trimmed'])} trades cut at the {CAP_DAYS}-day cap")
    # trimming + quantisation vs the full frames (strict on)
    for nm in ("raw", "minimal", "com_default", "ruthless", "slow", "banker", "pyramid"):
        ch = [abs(v["box"][nm][0] - v["full_s"][nm]) for v in R.values()]
        print(f"     box frame vs full frame {nm:12}: {sum(1 for c in ch if c > 1e-4)} trades differ by > 0.0001R (max {max(ch):.4f}R; "
              f"quantisation alone <= ~1e-5R)")
    # BOX: plain Python == numba on decoded frames
    fr = E.Frames(FR); hs = sorted(R)[::15]; mism = 0; tpy = []
    for h in hs:
        Fd, cd = fr.get(h)
        for nm in ("raw", "minimal", "com_default", "slow", "pyramid", "banker"):
            P = E.compile_cfg(E.validate(cfg_of(nm))[0], cd, True)
            t1 = time.time(); a = E.run(E.kern, Fd, cd, P); tpy.append(time.time() - t1)
            b = E.run(NB, Fd, cd, P)
            mism += int(not (float(a[0]) == float(b[0]) and int(a[1]) == int(b[1]) and int(a[2]) == int(b[2])))
    say(mism == 0, f"box kernel (plain Python) == numba kernel on decoded frames, bit for bit: {len(hs)} trades x 6 plans, mismatches {mism}")
    print(f"     plain-Python time per plan evaluation on the box path: median {np.median(tpy) * 1000:.1f} ms, p95 {np.percentile(tpy, 95) * 1000:.1f} ms, "
          f"max {max(tpy) * 1000:.0f} ms")
    # RESULTS (server-side only)
    rows = {}
    for h, v in R.items():
        t = SPLIT["trades"][h]; b = v["box"]
        rows[h] = {"group": t["group"], "part": t["part"], "det": t["det"], "dir": "BUY" if t["up"] else "SELL", "year": t["year"],
                   "R": {nm: round(b[nm][0], 4) for nm in NAMES}, "kind": {nm: b[nm][1] for nm in NAMES},
                   "gross": {nm: round(b[nm][3], 4) for nm in NAMES}, "stop_pips": round(v["stop_pips"], 2),
                   "exit_j": {nm: b[nm][2] for nm in ("raw", "com_default", "minimal")},
                   "default": COMPARATOR, "preload": DEFAULTS[t["group"]]["preset"], "banked_1R": bool(v["banked"]), "hit_tp2": v["tp2_raw"], "reveal": v["reveal"]}
    with gzip.open(os.path.join(OUT, "results_v1.json.gz"), "wt") as f:
        json.dump({"version": 1, "menu": MENU_VERSION, "split_sha": SPLIT["sha256"], "built": time.strftime("%Y-%m-%d %H:%M"), "frames": "frames_v1",
                   "comparison": "raw", "comparator": COMPARATOR, "frame_cap_days": CAP_DAYS, "trades": rows}, f, separators=(",", ":"))
    # INFO (Practice only)
    info = {"version": 1, "built": time.strftime("%Y-%m-%d %H:%M"), "menu": MENU_VERSION, "groups": {}}
    for g in ("com", "maj"):
        gn = next(n for k, n, c, ss, _ in SG.GROUPS if k == g); how = next(hw for k, n, c, ss, hw in SG.GROUPS if k == g)
        lk = SG.RESEARCH_LOCK                                   # the tab serves nothing locked - its statistics don't use locked trades either
        unl = lambda t: not (t["key"][0].split(".")[0] in lk["locked"] or (t["year"] >= lk["period_from"] and t["key"][0].split(".")[0] not in lk["exposed"]))
        hs = sorted([h for h in R if SPLIT["trades"][h]["group"] == g and SPLIT["trades"][h]["part"] == "practice" and unl(SPLIT["trades"][h])],
                    key=lambda h: SPLIT["trades"][h]["key"][1])
        raw = [R[h]["box"]["raw"][0] for h in hs]; L = []
        L.append({"tag": "observed", "text": f"Behaviour (group description, not a measurement): {how}."})
        L.append({"tag": "observed", "text": f"Raw signal on {len(hs)} Practice trades: {np.mean(raw):+.3f}R/trade, TP2 reached {sum(1 for h in hs if R[h]['tp2_raw']) / len(hs):.0%}, "
                                             f"+1R reached {sum(1 for h in hs if R[h]['banked']) / len(hs):.0%} (model cost, a lower bound)."})
        w24 = [R[h]["ps"] for h in hs if R[h]["ps"]["r2_24h"]]; late = [R[h]["ps"] for h in hs if R[h]["ps"]["r2"] and not R[h]["ps"]["r2_24h"]]
        fr_ = lambda xs, k: sum(1 for x in xs if x[k]) / len(xs) if xs else float("nan")
        L.append({"tag": "observed", "text": f"Trades reaching +2R within 24 h go on to TP2 {fr_(w24, 'tp2_raw'):.0%} of the time (n {len(w24)}); later +2R {fr_(late, 'tp2_raw'):.0%} (n {len(late)})."})
        m1 = sorted(R[h]["ps"]["mfe1"] for h in hs if R[h]["ps"]["mfe1"] is not None)
        if m1:
            q = lambda fr2: m1[min(len(m1) - 1, int(fr2 * len(m1)))]
            L.append({"tag": "observed", "text": f"After +1R, the best R before falling back to entry: quartiles {q(.25):+.1f} / {q(.5):+.1f} / {q(.75):+.1f}R."})
        pre = DEFAULTS[g]["preset"]
        names = [COMPARATOR] + ([pre] if pre != COMPARATOR else []) + [k for k in MENU if k not in (COMPARATOR, pre)]
        for nm in names:
            v = [R[h]["box"][nm][0] for h in hs]; d = np.array(v) - np.array(raw); tt = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if d.std() > 0 else 0.0
            own = nm == "com_default"                           # the trader's own plan: not an approved tested trait -> "observed"
            lab = (DEFAULTS[g]["label"] + " - your plan, not an approved trait") if own else (MENU[nm]["label"] + (" (grading comparator)" if nm == COMPARATOR else ""))
            L.append({"tag": "observed" if own else "tested", "text": f"{lab}: {np.mean(v):+.3f}R/trade vs raw {d.mean():+.3f} (t {tt:+.1f}), worst drawdown {maxdd(v):.0f}R vs raw {maxdd(raw):.0f}R "
                                               f"(Practice, in-sample; model cost - read the difference, not the level)."})
        info["groups"][g] = {"name": gn, "n_practice": len(hs), "default": COMPARATOR, "preload": pre, "default_status": DEFAULTS[g]["status"], "lines": L}
    json.dump(info, open(os.path.join(OUT, "info_v1.json"), "w"), indent=1)
    print(json.dumps(info, indent=1)[:3000])
    print(("ALL PASS" if ok_all else "SOME CHECKS FAILED") + f"  ({time.time() - T0:.0f}s)")
    return ok_all


if __name__ == "__main__": sys.exit(0 if main() else 1)
