"""backtest_engine.py - trader 2026-10-04 (backtest tab, phase 2): the generic MANAGEMENT RULE ENGINE behind the backtest tab's
lever builder. One kernel, written in plain Python that numba can also compile: pipeline/backtest_rules.py njit-compiles THIS
function for the offline precompute, the box runs it as plain Python (no numba there) - same source, same IEEE operations, so the
two give bit-identical results on the same frame (checked in backtest_rules.py --check).

CONFIG (what the lever builder serialises; schema "levers/v1"):
  {"schema": "levers/v1", "entry": {"pct": 100}, "tp2": true, "cap_pct": 200, "trim_to_cap": false, "levers": [ ...cards... ][, "risk_cap": R]}
  (trim_to_cap: the worst case - entry + every add - may exceed the cap; later adds are trimmed to fit. Off: such a plan is refused)
  (risk_cap, optional, 2026-10-08: the add_bar risk_cap below applied to EVERY add of the plan - add_reach, add_bar, add_tp1; with a
  card's own risk_cap the lower one counts. Only written when set, so plans without it normalise and run exactly as before)
  lever cards ("type" + fields; any card can appear any number of times):
    bank        {pct, at, tp1}              bank pct% of what is open when the trade first reaches +at R (tp1: or TP1 if that is
                                            nearer: min(at, TP1), capped at TP2)
    move        {to, when, tp1, legs, src}  move the stop to `to` R (0 = entry) when +when R is reached; tighten only; legs:
                                            "all" | "original" | "adds"; src: "exit" (default - the price you could close at) |
                                            "bid" (used by the Pyramid preset's -0.5R raise, as the preset kernel)
    add_reach   {pct, at, within_h, stop}   add pct% when +at R is reached (within within_h hours of the entry; null = any time);
                                            its stop: a number in R (0 = entry) or "trade" = the ORIGINAL leg's current stop at
                                            that moment (moves with legs "all" or "original" count; "adds"-only moves do not)
    add_bar     {pct, bar, early_at, hold_avg, stop[, wait_avg]   add pct% at the bar-N close; early_at: go in early at the first
                                            +early_at R before that bar; hold_avg: hold the early add while the average R is below it
                                            and go in the first minute the average reaches it (still before the bar - at the bar close
                                            it goes in regardless; needs early_at); wait_avg (optional, trader 2026-10-06, independent
                                            of early_at): at the bar close, wait while the average R is below it and go in the first
                                            minute after the bar that the average reaches it (no deadline: never, if it never does)
                [, risk_cap]                risk_cap (optional, trader 2026-10-07): when this card's add goes in (any of its fills), keep
                                            the OPEN risk at or below risk_cap R - sum of size x (price in - stop) over the open legs, at
                                            bid prices (no spread: a full entry at the signal stop = 1R); over it, every open leg's stop is
                                            raised to one level S that brings it back to exactly risk_cap (tighten only); if S would be at
                                            or above the price, the add is skipped
    add_tp1     {pct, min_R, stop}          add pct% when TP1 is reached (only if TP1 is above +min_R R and below TP2)
    cut_avg     {at_h, below[, window_h]}   close everything at at_h hours if the average R (the EA's AvgRSince: mean of the M1
                                            bid closes since the entry-bar open, against the card entry) is below `below`; window_h
                                            (optional, trader 2026-10-09): the average over only the last window_h trading hours
                                            (the minutes after the at_h - window_h mark; from the entry when at_h <= window_h)
    cut_unless  {at_h, reach}               close everything at at_h hours unless +reach R was reached (bid high) by then
    trail       {every_h, dist, lower}      (trader 2026-10-09; at most one) every every_h TRADING hours after the entry, at that
                                            minute's close, every open leg's stop goes to the exit price - dist: raised when that is
                                            higher; with lower, also lowered to it - never below the signal stop (-1R: the frames end
                                            at the first -1R touch, and the loss stays capped at 1R). Takes effect from the next minute
  At most 20 levers per list (adds / take profit / stop moves / cuts) and 60 in all (review 2026-10-04).
  entry.pct = the first position (% of ruled size); every leg's first stop is the signal stop (-1R) unless the card says else;
  stops are never set below -1R; cap_pct = gross units entered (banking does not free room): an add is trimmed to what is left.
  Hours: H4 bar b = H // 4, then H % 4 hours into it, from the entry-bar open (24 h = the bar-6 close).
PER MINUTE (the order of pipeline/backtest_presets.py's kernel, which this reproduces exactly - checked):
  per-leg stops (exit-side low) -> "reached" flags -> banks (exit-side high, in level order) -> stop moves -> adds that trigger on a
  level (bid high) + early/held/waiting bar adds -> TP2 (exit-side high) -> cuts (at their minute's close) -> bar adds (at the bar
  close; a wait_avg add whose average is still below its level starts waiting instead).
KERNEL LIMITS: one M1 bar has no intrabar order - stops are checked first; an add and TP2 (or a bank) in the same minute both count;
banks / TP2 fill exactly at the level; adds on a level fill at the level or the minute's open (whichever is worse) + model spread;
stops fill at the stop or the open (whichever is worse); bar / held / waiting adds fill at the minute close; a risk_cap raise takes effect
from the next minute (stops are checked first); model spread only (no commission, swap or slippage); a trade still open at the frame end
closes at the last close (box frames: 40 trading days).
"""
from __future__ import annotations
import json, math, zlib
import numpy as np

SCHEMA = "levers/v1"
NEVER = 1e18                 # a level that is never reached
BEYOND = 1 << 40             # a minute that never comes
NOCHK = 1 << 40
CAP_DEFAULT = 200.0
MAX_PER_LIST, MAX_LEVERS = 20, 60      # review 2026-10-04: the kernel loops every lever every minute, under the tab's lock


# ------------------------------------------------------------------------------------------------------------- the kernel
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, tp2_on, tp2_R, cap, F0,
         bk_lvl, bk_pct, nbk,
         mv_when, mv_to, mv_legs, mv_src, nmv,
         ad_type, ad_lvl, ad_until, ad_k, ad_early, ad_hold, ad_pct, ad_stop, ad_trade, nad,
         ct_type, ct_k, ct_thr, nct, ad_wait, ad_cap, ct_ws, tr_on, tr_dist, tr_lower, tr_chk):
    """-> (R of the full ruled position, kind 0 stopped / 1 TP2 / 2 cut / 3 frame end / 4 banked out (everything open was banked:
    the trade ends that minute), exit minute, gross units entered, banks fired, adds fired, cut index or -1)."""
    nleg = 1 + nad
    ls = np.zeros(nleg); le = np.zeros(nleg); lst = np.zeros(nleg); alive = np.zeros(nleg, dtype=np.bool_)
    lp = np.zeros(nleg); fxd = np.zeros(nleg, dtype=np.bool_)          # lp: each leg's price in without the spread (risk_cap)
    ls[0] = F0; le[0] = e0; lst[0] = -1.0; alive[0] = True; nl = 1; lp[0] = e0 - eo[0]
    gross = F0; locked = 0.0; tstop = -1.0; cs = 0.0
    bk_done = np.zeros(max(nbk, 1), dtype=np.bool_); mv_done = np.zeros(max(nmv, 1), dtype=np.bool_)
    ad_done = np.zeros(max(nad, 1), dtype=np.bool_); ad_held = np.zeros(max(nad, 1), dtype=np.bool_)
    ct_hit = np.zeros(max(nct, 1), dtype=np.bool_); ct_csw = np.zeros(max(nct, 1))      # cs at a windowed cut's window start
    nbf = 0; naf = 0
    for j in range(n):
        cs += bc[j]; avg = cs / (j + 1)
        for c in range(nct):
            if ct_ws[c] == j: ct_csw[c] = cs
        anyalive = False
        for q in range(nl):
            if alive[q]:
                if xl[j] <= lst[q]:
                    r = lst[q] if lst[q] < xo[j] else xo[j]
                    locked += ls[q] * (r - le[q]); alive[q] = False
                else:
                    anyalive = True
        if not anyalive:
            return locked, 0, j, gross, nbf, naf, -1
        for c in range(nct):
            if ct_type[c] == 1 and (not ct_hit[c]) and bh[j] >= ct_thr[c]:
                ct_hit[c] = True
        for b in range(nbk):
            if (not bk_done[b]) and xh[j] >= bk_lvl[b]:
                bk_done[b] = True; nbf += 1
                f = bk_pct[b]
                if f > 0.0:
                    for q in range(nl):
                        if alive[q]:
                            locked += f * ls[q] * (bk_lvl[b] - le[q]); ls[q] = ls[q] * (1.0 - f)
                    left = False
                    for q in range(nl):
                        if alive[q] and ls[q] > 1e-12:
                            left = True
                    if not left:
                        return locked, 4, j, gross, nbf, naf, -1
        for m in range(nmv):
            if not mv_done[m]:
                hv = xh[j] if mv_src[m] == 0 else bh[j]
                if hv >= mv_when[m]:
                    mv_done[m] = True; v = be + mv_to[m]; lg = mv_legs[m]
                    for q in range(nl):
                        if alive[q] and (lg == 0 or (lg == 1 and q == 0) or (lg == 2 and q > 0)) and v > lst[q]:
                            lst[q] = v
                    if (lg == 0 or lg == 1) and v > tstop:
                        tstop = v
        for a in range(nad):
            if ad_type[a] == 1:
                px = -1e18                             # the price this bar add goes in at this minute (-1e18: it does not)
                if (not ad_done[a]) and j < ad_k[a]:
                    if (not ad_held[a]) and bh[j] >= ad_early[a]:
                        if avg < ad_hold[a]:
                            ad_held[a] = True
                        else:
                            px = ad_early[a] if ad_early[a] > bo[j] else bo[j]
                    elif ad_held[a] and avg >= ad_hold[a]:
                        px = bc[j]
                elif (not ad_done[a]) and ad_held[a] and j > ad_k[a] and avg >= ad_wait[a]:     # waiting since the bar close (wait_avg)
                    px = bc[j]
                if px > -1e17:                         # early / held / waiting
                    sz = ad_pct[a] if ad_pct[a] < cap - gross else cap - gross
                    if sz > 1e-12:
                        ls[nl] = sz; lp[nl] = px; le[nl] = px + eo[j]
                        lst[nl] = tstop if ad_trade[a] else be + ad_stop[a]; alive[nl] = True
                        ok = True
                        if ad_cap[a] < 1e17:           # risk_cap: open risk over it -> one stop level S for every open leg
                            rk = 0.0
                            for q in range(nl + 1):
                                if alive[q]: rk += ls[q] * (lp[q] - lst[q])
                            if rk > ad_cap[a] + 1e-9:
                                S = -1.0
                                for q in range(nl + 1): fxd[q] = False
                                for it in range(nl + 1):       # legs already at or above S stay put; solve again without them
                                    nm = -ad_cap[a]; sw = 0.0
                                    for q in range(nl + 1):
                                        if alive[q]:
                                            if fxd[q]: nm += ls[q] * (lp[q] - lst[q])
                                            else: nm += ls[q] * lp[q]; sw += ls[q]
                                    if sw <= 1e-12: break
                                    S = nm / sw; ch = False
                                    for q in range(nl + 1):
                                        if alive[q] and (not fxd[q]) and lst[q] >= S: fxd[q] = True; ch = True
                                    if not ch: break
                                if S >= xc[j]:
                                    ok = False             # the stop would have to sit at or above the price: no add
                                else:
                                    for q in range(nl + 1):
                                        if alive[q] and S > lst[q]: lst[q] = S
                                    if S > tstop: tstop = S
                        if ok:
                            nl += 1; gross += sz; naf += 1
                        else:
                            alive[nl] = False
                    ad_done[a] = True; ad_held[a] = False
            else:
                if (not ad_done[a]) and j <= ad_until[a] and bh[j] >= ad_lvl[a]:
                    sz = ad_pct[a] if ad_pct[a] < cap - gross else cap - gross
                    if sz > 1e-12:
                        px = ad_lvl[a] if ad_lvl[a] > bo[j] else bo[j]
                        ls[nl] = sz; lp[nl] = px; le[nl] = px + eo[j]
                        lst[nl] = tstop if ad_trade[a] else be + ad_stop[a]; alive[nl] = True
                        ok = True
                        if ad_cap[a] < 1e17:           # risk_cap: open risk over it -> one stop level S for every open leg
                            rk = 0.0
                            for q in range(nl + 1):
                                if alive[q]: rk += ls[q] * (lp[q] - lst[q])
                            if rk > ad_cap[a] + 1e-9:
                                S = -1.0
                                for q in range(nl + 1): fxd[q] = False
                                for it in range(nl + 1):       # legs already at or above S stay put; solve again without them
                                    nm = -ad_cap[a]; sw = 0.0
                                    for q in range(nl + 1):
                                        if alive[q]:
                                            if fxd[q]: nm += ls[q] * (lp[q] - lst[q])
                                            else: nm += ls[q] * lp[q]; sw += ls[q]
                                    if sw <= 1e-12: break
                                    S = nm / sw; ch = False
                                    for q in range(nl + 1):
                                        if alive[q] and (not fxd[q]) and lst[q] >= S: fxd[q] = True; ch = True
                                    if not ch: break
                                if S >= xc[j]:
                                    ok = False             # the stop would have to sit at or above the price: no add
                                else:
                                    for q in range(nl + 1):
                                        if alive[q] and S > lst[q]: lst[q] = S
                                    if S > tstop: tstop = S
                        if ok:
                            nl += 1; gross += sz; naf += 1
                        else:
                            alive[nl] = False
                    ad_done[a] = True
        if tp2_on and xh[j] >= tp2_R:
            v = locked
            for q in range(nl):
                if alive[q]:
                    v += ls[q] * (tp2_R - le[q])
            return v, 1, j, gross, nbf, naf, -1
        for c in range(nct):
            if j == ct_k[c]:
                aw = avg if ct_ws[c] < 0 else (cs - ct_csw[c]) / (j - ct_ws[c])          # windowed: the minutes after the window start
                fire = (aw < ct_thr[c]) if ct_type[c] == 0 else (not ct_hit[c])
                if fire:
                    v = locked
                    for q in range(nl):
                        if alive[q]:
                            v += ls[q] * (xc[j] - le[q])
                    return v, 2, j, gross, nbf, naf, c
        for a in range(nad):
            if ad_type[a] == 1 and (not ad_done[a]) and j == ad_k[a]:
                if avg < ad_wait[a]:                   # wait_avg: the average is still below it - wait (unset: -NEVER, never waits)
                    ad_held[a] = True
                    continue
                sz = ad_pct[a] if ad_pct[a] < cap - gross else cap - gross
                if sz > 1e-12:
                    ls[nl] = sz; lp[nl] = bc[j]; le[nl] = bc[j] + eo[j]
                    lst[nl] = tstop if ad_trade[a] else be + ad_stop[a]; alive[nl] = True
                    ok = True
                    if ad_cap[a] < 1e17:           # risk_cap: open risk over it -> one stop level S for every open leg
                        rk = 0.0
                        for q in range(nl + 1):
                            if alive[q]: rk += ls[q] * (lp[q] - lst[q])
                        if rk > ad_cap[a] + 1e-9:
                            S = -1.0
                            for q in range(nl + 1): fxd[q] = False
                            for it in range(nl + 1):       # legs already at or above S stay put; solve again without them
                                nm = -ad_cap[a]; sw = 0.0
                                for q in range(nl + 1):
                                    if alive[q]:
                                        if fxd[q]: nm += ls[q] * (lp[q] - lst[q])
                                        else: nm += ls[q] * lp[q]; sw += ls[q]
                                if sw <= 1e-12: break
                                S = nm / sw; ch = False
                                for q in range(nl + 1):
                                    if alive[q] and (not fxd[q]) and lst[q] >= S: fxd[q] = True; ch = True
                                if not ch: break
                            if S >= xc[j]:
                                ok = False             # the stop would have to sit at or above the price: no add
                            else:
                                for q in range(nl + 1):
                                    if alive[q] and S > lst[q]: lst[q] = S
                                if S > tstop: tstop = S
                    if ok:
                        nl += 1; gross += sz; naf += 1
                    else:
                        alive[nl] = False
                ad_done[a] = True; ad_held[a] = False
        if tr_on and tr_chk[j]:                        # trail: every leg's stop to the exit price - dist (raise; lower too if set)
            tv = xc[j] - tr_dist
            if tv < -1.0: tv = -1.0
            for q in range(nl):
                if alive[q] and (tv > lst[q] or (tr_lower and tv < lst[q])): lst[q] = tv
            if tv > tstop or tr_lower: tstop = tv
    v = locked
    for q in range(nl):
        if alive[q]:
            v += ls[q] * (xc[n - 1] - le[q])
    return v, 3, n - 1, gross, nbf, naf, -1


# ------------------------------------------------------------------------------------------------------------- config
TYPES = {"bank": ("pct", "at", "tp1"), "move": ("to", "when", "tp1", "legs", "src"), "add_reach": ("pct", "at", "within_h", "stop"),
         "add_bar": ("pct", "bar", "early_at", "hold_avg", "stop", "wait_avg", "risk_cap"), "add_tp1": ("pct", "min_R", "stop"),
         "cut_avg": ("at_h", "below", "window_h"), "cut_unless": ("at_h", "reach"), "trail": ("every_h", "dist", "lower")}


def _num(v, lo=None, hi=None, what="value"):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v): raise ValueError(f"{what} must be a number")
    if lo is not None and v < lo: raise ValueError(f"{what} must be at least {lo}")
    if hi is not None and v > hi: raise ValueError(f"{what} must be at most {hi}")
    return float(v)


def validate(cfg: dict) -> tuple[dict, list[str]]:
    """-> (normalised config, warnings). Raises ValueError with a plain-words message when the config is not valid
    (unknown lever, bad number, size over the cap). The size check is the worst case: every add at full size."""
    if not isinstance(cfg, dict): raise ValueError("the plan must be an object")
    if cfg.get("schema", SCHEMA) != SCHEMA: raise ValueError(f"unknown plan schema {str(cfg.get('schema'))[:40]!r} (expected {SCHEMA})")
    ent = cfg.get("entry")
    if ent is None: ent = {}
    if not isinstance(ent, dict): raise ValueError("the entry must be an object like {\"pct\": 100}")
    entry = _num(ent.get("pct", 100), 1, 200, "the entry size")
    cap = _num(cfg.get("cap_pct", CAP_DEFAULT), 1, CAP_DEFAULT, "the size cap")
    levers = cfg.get("levers")
    if levers is None: levers = []
    if not isinstance(levers, list): raise ValueError("levers must be a list")
    if len(levers) > MAX_LEVERS: raise ValueError(f"at most {MAX_LEVERS} levers in a plan")
    fam = {}
    for c in levers:
        t = c.get("type") if isinstance(c, dict) else None
        k = (t[:3] if isinstance(t, str) and t[:3] in ("add", "cut") else t) if isinstance(t, str) else None
        fam[k] = fam.get(k, 0) + 1
        if k is not None and fam[k] > MAX_PER_LIST: raise ValueError(f"at most {MAX_PER_LIST} levers of one kind (adds / take profit / stop moves / cuts)")
    out = []; warn = []
    for i, c in enumerate(levers):
        t = c.get("type") if isinstance(c, dict) else None
        if not isinstance(t, str) or t not in TYPES: raise ValueError(f"lever {i + 1}: unknown type {str(t)[:40]!r}")
        d = {"type": t}
        if t == "bank":
            d.update(pct=_num(c.get("pct"), 0, 100, "a bank's %"), at=_num(c.get("at"), 0.05, 50, "a bank's R level"), tp1=bool(c.get("tp1", False)))
        elif t == "move":
            d.update(to=_num(c.get("to"), -1, 50, "a stop move's new stop"), when=_num(c.get("when"), -0.99, 50, "a stop move's trigger"),
                     tp1=bool(c.get("tp1", False)), legs=c.get("legs", "all"), src=c.get("src", "exit"))
            if not isinstance(d["legs"], str) or d["legs"] not in ("all", "original", "adds"): raise ValueError(f"lever {i + 1}: legs must be all / original / adds")
            if not isinstance(d["src"], str) or d["src"] not in ("exit", "bid"): raise ValueError(f"lever {i + 1}: src must be exit or bid")
            if d["to"] >= d["when"]: warn.append(f"lever {i + 1}: the new stop ({d['to']:+g}R) is at or above its trigger ({d['when']:+g}R) - it will close the trade at once")
        elif t == "add_reach":
            w = c.get("within_h")
            d.update(pct=_num(c.get("pct"), 1, 200, "an add's %"), at=_num(c.get("at"), 0.05, 50, "an add's R level"),
                     within_h=None if w in (None, "", 0) else _num(w, 0.25, 2400, "an add's hours"), stop=_stop(c.get("stop", 0), i))
        elif t == "add_bar":
            ea, ha = c.get("early_at"), c.get("hold_avg")
            d.update(pct=_num(c.get("pct"), 1, 200, "an add's %"), bar=int(_num(c.get("bar"), 1, 60, "an add's bar")),
                     early_at=None if ea in (None, "") else _num(ea, 0.05, 50, "the early add level"),
                     hold_avg=None if ha in (None, "") else _num(ha, -5, 50, "the hold average"), stop=_stop(c.get("stop", "trade"), i))
            if d["hold_avg"] is not None and d["early_at"] is None: raise ValueError(f"lever {i + 1}: the hold needs an early add level")
            wa = c.get("wait_avg")                     # only written when set, so plans without it normalise exactly as before
            if wa not in (None, ""): d["wait_avg"] = _num(wa, -5, 50, "the bar-close wait average")
            rc = c.get("risk_cap")                     # the same: only written when set
            if rc not in (None, ""): d["risk_cap"] = _num(rc, 0.1, 10, "the risk cap")
        elif t == "add_tp1":
            d.update(pct=_num(c.get("pct"), 1, 200, "an add's %"), min_R=_num(c.get("min_R", 1.0), 0, 50, "the TP1 minimum"), stop=_stop(c.get("stop", 1.0), i))
        elif t == "cut_avg":
            d.update(at_h=_num(c.get("at_h"), 0.25, 2400, "a cut's hours"), below=_num(c.get("below", 0.0), -5, 50, "a cut's average"))
            wh = c.get("window_h")                      # only written when set: plans without it normalise and run exactly as before
            if wh not in (None, ""): d["window_h"] = _num(wh, 1, 240, "a cut's average window")
        elif t == "cut_unless":
            d.update(at_h=_num(c.get("at_h"), 0.25, 2400, "a cut's hours"), reach=_num(c.get("reach"), 0.05, 50, "a cut's R level"))
        elif t == "trail":
            d.update(every_h=_num(c.get("every_h", 1), 0.25, 48, "the trail's interval"), dist=_num(c.get("dist", 1.0), 0.1, 10, "the trail's distance"),
                     lower=bool(c.get("lower", False)))
        out.append(d)
    if sum(1 for d in out if d["type"] == "trail") > 1: raise ValueError("at most one trailing stop in a plan")
    adds = sum(d["pct"] for d in out if d["type"].startswith("add")); trim = bool(cfg.get("trim_to_cap", False))
    if entry + adds > cap + 1e-9:
        if not trim:
            raise ValueError(f"the entry ({entry:g}%) plus every add ({adds:g}%) would reach {entry + adds:g}% - over the {cap:g}% cap "
                             f"(turn on 'trim adds to fit the cap' to allow it)")
        warn.append(f"the entry plus every add would reach {entry + adds:g}%; later adds are trimmed to fit the {cap:g}% cap")
    out_cfg = {"schema": SCHEMA, "entry": {"pct": entry}, "tp2": bool(cfg.get("tp2", True)), "cap_pct": cap, "trim_to_cap": trim, "levers": out}
    prc = cfg.get("risk_cap")                          # plan-wide risk cap (2026-10-08): only written when set
    if prc not in (None, ""): out_cfg["risk_cap"] = _num(prc, 0.1, 10, "the plan's risk cap")
    return out_cfg, warn


def _stop(v, i):
    if isinstance(v, str) and v == "trade": return "trade"
    return _num(v, -1, 50, f"lever {i + 1}'s stop")


def mark(ctx: dict, H: float, cut: bool = False) -> int:
    """the minute index of 'H hours after the entry' (the last M1 minute before that time): H4 bar b = H // 4 (its open, from the
    entry-bar open), then H % 4 hours into it. Bars past the data -> the frame's last minute (the preset kernel's fallback); a
    time after the frame's kept minutes -> BEYOND; cut=True: no minute before that time -> NOCHK (the EA skips the check)."""
    b, r = int(H // 4), int(round((H % 4) * 60))
    off = ctx["off"]
    if b >= len(off) or off[b] is None: return ctx["n_full"] - 1
    T = off[b] + r
    tm = ctx["tm"]; idx = int(np.searchsorted(tm, T)) - 1
    if idx == len(tm) - 1 and T > ctx["t_next"]: return BEYOND
    if idx < 0 and cut: return NOCHK
    return idx


def compile_cfg(cfg: dict, ctx: dict, strict_tp2: bool = True) -> dict:
    """a VALIDATED config + one trade's context -> the kernel's per-trade arrays. strict_tp2: a bank above TP2 or an add at/above
    TP2 never fires (they could only fire in the same minute as TP2 - the preset kernel lets them; see backtest_rules)."""
    tp2 = ctx["tp2"]; tp2_on = bool(cfg["tp2"]) and tp2 is not None; tp1 = ctx["tp1"]
    def lvl_tp1(at):                                   # min(at, TP1) capped at TP2 (pipeline/combo_build.prep bank_R)
        b = min(at, tp1) if tp1 is not None else at
        if tp2 is not None and b >= tp2 - 1e-9: b = tp2
        return b
    L = cfg["levers"]
    banks = sorted([c for c in L if c["type"] == "bank"], key=lambda c: c["at"])       # stable: level order, then stack order
    moves = [c for c in L if c["type"] == "move"]; adds = [c for c in L if c["type"].startswith("add")]
    cuts = [c for c in L if c["type"].startswith("cut")]
    bk_lvl = np.array([lvl_tp1(c["at"]) if c["tp1"] else c["at"] for c in banks], dtype=np.float64)
    if strict_tp2 and tp2_on: bk_lvl = np.where(bk_lvl > tp2 + 1e-12, NEVER, bk_lvl)
    bk_pct = np.array([c["pct"] / 100.0 for c in banks], dtype=np.float64)
    mv_when = np.array([lvl_tp1(c["when"]) if c["tp1"] else c["when"] for c in moves], dtype=np.float64)
    mv_to = np.array([c["to"] for c in moves], dtype=np.float64)
    mv_legs = np.array([{"all": 0, "original": 1, "adds": 2}[c["legs"]] for c in moves], dtype=np.int64)
    mv_src = np.array([0 if c["src"] == "exit" else 1 for c in moves], dtype=np.int64)
    na = len(adds)
    ad_type = np.zeros(na, dtype=np.int64); ad_lvl = np.full(na, NEVER); ad_until = np.full(na, BEYOND, dtype=np.int64)
    ad_k = np.full(na, BEYOND, dtype=np.int64); ad_early = np.full(na, NEVER); ad_hold = np.full(na, -NEVER); ad_wait = np.full(na, -NEVER)
    ad_cap = np.full(na, NEVER)
    ad_pct = np.zeros(na); ad_stop = np.zeros(na); ad_trade = np.zeros(na, dtype=np.bool_)
    for i, c in enumerate(adds):
        ad_pct[i] = c["pct"] / 100.0
        if c["stop"] == "trade": ad_trade[i] = True
        else: ad_stop[i] = c["stop"]
        if c["type"] == "add_bar":
            ad_type[i] = 1; ad_k[i] = mark(ctx, 4.0 * c["bar"])
            if c["early_at"] is not None: ad_early[i] = c["early_at"]
            if c["hold_avg"] is not None: ad_hold[i] = c["hold_avg"]
            if c.get("wait_avg") is not None: ad_wait[i] = c["wait_avg"]
            if c.get("risk_cap") is not None: ad_cap[i] = c["risk_cap"]
        elif c["type"] == "add_reach":
            ad_lvl[i] = c["at"]
            if c["within_h"] is not None: ad_until[i] = mark(ctx, c["within_h"])
        else:                                          # add_tp1
            ad_lvl[i] = tp1 if (tp1 is not None and tp1 > c["min_R"]) else NEVER
        if strict_tp2 and tp2_on and ad_type[i] == 0 and ad_lvl[i] >= tp2 - 1e-12: ad_lvl[i] = NEVER
        if cfg.get("risk_cap") is not None and cfg["risk_cap"] < ad_cap[i]: ad_cap[i] = cfg["risk_cap"]      # the plan-wide cap: every add
    ct_type = np.array([0 if c["type"] == "cut_avg" else 1 for c in cuts], dtype=np.int64)
    ct_k = np.array([mark(ctx, c["at_h"], cut=True) for c in cuts], dtype=np.int64)
    ct_thr = np.array([c["below"] if c["type"] == "cut_avg" else c["reach"] for c in cuts], dtype=np.float64)
    def wstart(c):
        if c["type"] != "cut_avg" or not c.get("window_h") or c["at_h"] <= c["window_h"]: return -1
        k = mark(ctx, c["at_h"] - c["window_h"]); return k if 0 <= k < BEYOND else -1
    ct_ws = np.array([wstart(c) for c in cuts], dtype=np.int64)
    tr = next((c for c in L if c["type"] == "trail"), None); tm = np.asarray(ctx["tm"], dtype=np.int64); nn = len(tm)
    tr_chk = np.zeros(max(nn, 1), dtype=np.bool_)
    if tr is not None and nn:                          # the check minutes: every every_h of TRADING time (gaps over 6 h - weekends - removed)
        d = np.diff(tm); tt = tm - np.r_[0, np.cumsum(np.where(d > 360, d - 1, 0))]
        step = tr["every_h"] * 60.0; ks = np.searchsorted(tt, np.arange(1, int(tt[-1] // step) + 2) * step, side="left"); tr_chk[ks[ks < nn]] = True
    return {"tp2_on": tp2_on, "tp2_R": tp2 if tp2 is not None else 0.0, "cap": cfg["cap_pct"] / 100.0, "F0": cfg["entry"]["pct"] / 100.0,
            "bk_lvl": bk_lvl, "bk_pct": bk_pct, "nbk": len(banks), "mv_when": mv_when, "mv_to": mv_to, "mv_legs": mv_legs, "mv_src": mv_src,
            "nmv": len(moves), "ad_type": ad_type, "ad_lvl": ad_lvl, "ad_until": ad_until, "ad_k": ad_k, "ad_early": ad_early,
            "ad_hold": ad_hold, "ad_wait": ad_wait, "ad_cap": ad_cap, "ad_pct": ad_pct, "ad_stop": ad_stop, "ad_trade": ad_trade, "nad": na,
            "ct_type": ct_type, "ct_k": ct_k, "ct_thr": ct_thr, "nct": len(cuts), "ct_ws": ct_ws,
            "tr_on": tr is not None, "tr_dist": float(tr["dist"]) if tr else 1.0, "tr_lower": bool(tr["lower"]) if tr else False, "tr_chk": tr_chk}


def run(fn, F: dict, ctx: dict, P: dict):
    """call kernel `fn` (this module's kern, or its numba compilation) on frame F with compiled params P."""
    return fn(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], F["n"], ctx["e0"], ctx["be"],
              P["tp2_on"], P["tp2_R"], P["cap"], P["F0"], P["bk_lvl"], P["bk_pct"], P["nbk"],
              P["mv_when"], P["mv_to"], P["mv_legs"], P["mv_src"], P["nmv"],
              P["ad_type"], P["ad_lvl"], P["ad_until"], P["ad_k"], P["ad_early"], P["ad_hold"], P["ad_pct"], P["ad_stop"], P["ad_trade"], P["nad"],
              P["ct_type"], P["ct_k"], P["ct_thr"], P["nct"], P["ad_wait"], P["ad_cap"], P["ct_ws"], P["tr_on"], P["tr_dist"], P["tr_lower"], P["tr_chk"])


# ------------------------------------------------------------------------------------------------------------- box frames
Q = 1e6           # frame values are stored as int32 R x 1e6 (delta-coded, zlib); decoded the same way everywhere


def decode_frame(blob: bytes, meta: dict) -> tuple[dict, dict]:
    """one trade's frame blob (data/backtest/frames_v1/<group>.bin slice) + its index entry -> (F, ctx) for run()."""
    n = meta["n"]
    a = np.frombuffer(zlib.decompress(blob), dtype=np.int32).reshape(6, n).astype(np.int64)
    a = np.cumsum(a, axis=1)
    bo, bh, bl, bc, sp = (a[i] / Q for i in range(5)); tm = a[5]
    if meta["up"]: F = {"xo": bo, "xh": bh, "xl": bl, "xc": bc, "eo": sp}
    else: F = {"xo": bo - sp, "xh": bh - sp, "xl": bl - sp, "xc": bc - sp, "eo": np.zeros(n)}
    F.update(bo=bo, bh=bh, bc=bc, n=n)
    e0 = float(F["eo"][0])
    ctx = {"e0": e0, "be": e0, "tp2": meta["tp2"], "tp1": meta["tp1"], "off": meta["off"], "tm": tm, "t_next": meta["t_next"],
           "n_full": meta["n_full"], "up": meta["up"]}
    return F, ctx


def encode_frame(bo, bh, bl, bc, sp, tm) -> bytes:
    a = np.vstack([np.round(np.asarray(v, dtype=np.float64) * Q) for v in (bo, bh, bl, bc, sp)] + [np.asarray(tm, dtype=np.float64)]).astype(np.int64)
    d = np.diff(a, axis=1, prepend=0)
    if np.abs(d).max() >= 2 ** 31: raise ValueError("frame value out of int32 range")
    return zlib.compress(d.astype(np.int32).tobytes(), 6)


class Frames:
    """read-only access to the box frames: <dir>/index.json + one <symbol>.bin per symbol (the index names the file + byte range)."""
    def __init__(self, d: str):
        self.d = d; self.idx = json.load(open(f"{d}/index.json"))["trades"]; self._fh = {}
    def get(self, hid: str):
        m = self.idx[hid]
        fh = self._fh.get(m["f"]) or open(f"{self.d}/{m['f']}", "rb"); self._fh[m["f"]] = fh
        fh.seek(m["off_b"]); return decode_frame(fh.read(m["len_b"]), m)


def evaluate(cfg: dict, F: dict, ctx: dict, fn=None, strict_tp2: bool = True) -> dict:
    cfgn, _ = validate(cfg)
    r = run(fn or kern, F, ctx, compile_cfg(cfgn, ctx, strict_tp2))
    return {"R": float(r[0]), "kind": int(r[1]), "exit_j": int(r[2]), "gross": float(r[3]), "banks": int(r[4]), "adds": int(r[5]), "cut": int(r[6])}
