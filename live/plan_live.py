"""plan_live.py - trader 2026-10-12 (docs/live_plans.md): LIVE CUSTOM PLANS. The live signal page is the Practice card: the trader builds
a lever plan ("levers/v1", live/backtest_engine.py) and approves; this module records it, and its Runner (a thread in the monitor) replays
the SAME rule engine on the live M1 path every few seconds and sends the EA its primitives (plan_add, set_sl, close_part, close).

kern_state below is backtest_engine.kern line for line (generated from it, 2026-10-12) plus what live needs: each leg's add lever and
size at creation, and the end state (sizes, stops, alive, which banks / adds / moves fired). tests: pipeline/plan_live_parity.py checks
it returns exactly kern's tuple on every box frame x every menu plan.

Live semantics (Practice parity, the differences named in docs/live_plans.md):
  - prices in the POSITION's R (trader 2026-10-13, rbase 'fill'): R(price) = d x (price - first fill) / |first fill - signal SL|, exit
    side = bid (BUY) / ask (SELL) - the EA's open R and sizing basis, so a lever counts from the fill, not from the card entry (a late
    fill used to start the replay at, say, +0.91R, and 'stop to entry when +0.5R' fired at the fill: EURCAD #3, NZDCHF #6). Records
    approved before that carry no rbase and keep the card's R (origin the card entry, 1R = |card entry - signal SL|);
  - the path starts at the first leg's fill minute; the forming minute counts for level triggers only (a time check waits for its minute
    to close: mark() sees the last bar as not yet closed);
  - "H hours" / "bar N close" on the H4 grid from the ENTRY-BAR open (the signal bar's close), as in Practice; a time that passed before
    the fill never fires; the trail counts its hours from the fill;
  - one-shot actions (a bank, an add, a cut / exit) are sent once, when the replay first shows them; stops follow the replay's level for
    each leg (tighten only - both ways with the trail's 'lower' - never past the signal stop); the broker is the truth for fills and stops.
"""
from __future__ import annotations
import json, math, os, threading, time, traceback
import numpy as np
from live import common as C
from live import backtest_engine as BE

BIG = 1 << 40                       # a minute that never comes (mark(): beyond the data)
POLL_S = 5.0
ACK_WAIT_S = 90.0                   # a task with no ack after this is dropped (and retried if still wanted)
LOG_MAX = 400


# ------------------------------------------------------------------------------------------------------------- the kernel (+ state)
def kern_state(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, tp2_on, tp2_R, cap, F0,
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
    tr_hi = -1e18                                      # trail: the highest exit-side price since the last check
    nbf = 0; naf = 0
    src = np.full(nleg, -1, dtype=np.int64); sz0 = np.zeros(nleg)    # LIVE: each leg's add lever and size at creation
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
            return (locked, 0, j, gross, nbf, naf, -1), (ls, lst, alive, nl, src, sz0, bk_done, ad_done, mv_done)
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
                        return (locked, 4, j, gross, nbf, naf, -1), (ls, lst, alive, nl, src, sz0, bk_done, ad_done, mv_done)
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
                            src[nl] = a; sz0[nl] = sz; nl += 1; gross += sz; naf += 1
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
                            src[nl] = a; sz0[nl] = sz; nl += 1; gross += sz; naf += 1
                        else:
                            alive[nl] = False
                    ad_done[a] = True
        if tp2_on and xh[j] >= tp2_R:
            v = locked
            for q in range(nl):
                if alive[q]:
                    v += ls[q] * (tp2_R - le[q])
            return (v, 1, j, gross, nbf, naf, -1), (ls, lst, alive, nl, src, sz0, bk_done, ad_done, mv_done)
        for c in range(nct):
            if j == ct_k[c]:
                aw = avg if ct_ws[c] < 0 else (cs - ct_csw[c]) / (j - ct_ws[c])          # windowed: the minutes after the window start
                fire = (aw < ct_thr[c]) if ct_type[c] == 0 else (not ct_hit[c])
                if fire:
                    v = locked
                    for q in range(nl):
                        if alive[q]:
                            v += ls[q] * (xc[j] - le[q])
                    return (v, 2, j, gross, nbf, naf, c), (ls, lst, alive, nl, src, sz0, bk_done, ad_done, mv_done)
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
                        src[nl] = a; sz0[nl] = sz; nl += 1; gross += sz; naf += 1
                    else:
                        alive[nl] = False
                ad_done[a] = True; ad_held[a] = False
        if tr_on and xh[j] > tr_hi: tr_hi = xh[j]
        if tr_on and tr_chk[j]:                        # trail: every leg's stop to dist below the window's high (raise; lower too if set)
            tv = tr_hi - tr_dist; tr_hi = -1e18
            if tv < -1.0: tv = -1.0
            for q in range(nl):
                if alive[q] and (tv > lst[q] or (tr_lower and tv < lst[q])): lst[q] = tv
            if tv > tstop or tr_lower: tstop = tv
    v = locked
    for q in range(nl):
        if alive[q]:
            v += ls[q] * (xc[n - 1] - le[q])
    return (v, 3, n - 1, gross, nbf, naf, -1), (ls, lst, alive, nl, src, sz0, bk_done, ad_done, mv_done)


def run_state(F: dict, ctx: dict, P: dict):
    """backtest_engine.run with kern_state -> ((R, kind, exit_j, gross, banks, adds, cut), (ls, lst, alive, nl, src, sz0, bk_done,
    ad_done, mv_done))."""
    return kern_state(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], F["n"], ctx["e0"], ctx["be"],
                      P["tp2_on"], P["tp2_R"], P["cap"], P["F0"], P["bk_lvl"], P["bk_pct"], P["nbk"],
                      P["mv_when"], P["mv_to"], P["mv_legs"], P["mv_src"], P["nmv"],
                      P["ad_type"], P["ad_lvl"], P["ad_until"], P["ad_k"], P["ad_early"], P["ad_hold"], P["ad_pct"], P["ad_stop"], P["ad_trade"], P["nad"],
                      P["ct_type"], P["ct_k"], P["ct_thr"], P["nct"], P["ad_wait"], P["ad_cap"], P["ct_ws"], P["tr_on"], P["tr_dist"], P["tr_lower"], P["tr_chk"])


# ------------------------------------------------------------------------------------------------------------- the plan the EA ran
def live_json(cfg: dict) -> dict:
    return C.load_json(os.path.join(cfg["root"], "config", "live.json"), {}) or {}


def live_rules_plan(sig: dict, lj: dict) -> tuple[dict, str]:
    """today's EA management for this signal, as levers - the page preloads it, so a plan row (the EA's own rules OFF) starts from
    exactly what would have run: the +1R bank (25% TrendCont / DeepFib per live.json, else 50%; TP1 if nearer) with the stop to entry;
    the commodity bloc's T+L1.0 (closed at the bar-N close unless +1.5R, the stop to +1R at +1.5R); the shorts' stop raise."""
    from live import symbol_groups as SG
    strat = sig.get("strategy", ""); sell = (sig.get("direction") == "SELL")
    bf = {"TrendCont": lj.get("trendcont_bank_frac"), "DeepFib": lj.get("deepfib_bank_frac")}.get(strat) or 0.5
    pct = round(float(bf) * 100.0, 2)
    lev = [{"type": "bank", "pct": pct, "at": 1.0, "tp1": True},
           {"type": "move", "to": 0.0, "when": 1.0, "tp1": True, "legs": "all", "src": "exit"}]
    about = [f"bank {pct:g}% at +1R (or TP1 if nearer) and the stop to entry"]
    bloc = [x.strip().upper() for x in str(lj.get("bloc_tl_symbols") or "").split(",") if x.strip()]
    if SG.root(sig.get("symbol", "")) in bloc:
        n = int(lj.get("staged_add_bar") or 6)
        lev += [{"type": "cut_unless", "at_h": 4.0 * n, "reach": 1.5}, {"type": "move", "to": 1.0, "when": 1.5, "tp1": False, "legs": "all", "src": "exit"}]
        about.append(f"commodity bloc: closed at the bar-{n} close unless +1.5R was reached; at +1.5R the stop to +1R")
    sr = float(lj.get("short_raise_at_pyramid_r") or 0); trig = float(lj.get("pyramid_trigger_r") or 1.5)
    if sell and sr > 0 and strat != "Inverse":
        lev.append({"type": "move", "to": sr, "when": trig, "tp1": False, "legs": "all", "src": "exit"})
        about.append(f"short: at +{trig:g}R the stop to +{sr:g}R")
    cfg = {"schema": BE.SCHEMA, "entry": {"pct": 100}, "tp2": True, "cap_pct": BE.CAP_DEFAULT, "trim_to_cap": False, "levers": lev}
    return cfg, "Today's live rules: " + "; ".join(about) + "; out at TP2 or the stop."


def live_validate(plan: dict) -> tuple[dict, list[str]]:
    """the Practice validation + the live limits: the entry at most 100% and every add under a 1R cap on the open risk (CLAUDE.md: the 1%
    risk rule is hard in all sizing code - the EA refuses an add over 1R; the cap here raises the stops first, as Practice's risk cap)."""
    cfg, warn = BE.validate(plan)
    if cfg["entry"]["pct"] > 100 + 1e-9: raise ValueError("live: the entry is at most 100% of the ruled size (the 1% risk rule)")
    if any(c["type"].startswith("add") for c in cfg["levers"]):
        rc = cfg.get("risk_cap")
        cfg["risk_cap"] = 1.0 if rc is None else min(float(rc), 1.0)
    return cfg, warn


# ------------------------------------------------------------------------------------------------------------- records
def plans_dir(cfg: dict) -> str: return os.path.join(cfg["root"], "plans")
def plan_path(cfg: dict, key: str) -> str: return os.path.join(plans_dir(cfg), f"{key}.json")
def load_plan(cfg: dict, key: str) -> dict | None: return C.load_json(plan_path(cfg, key)) if C.safe_key(key) else None
def save_plan(cfg: dict, rec: dict) -> None:
    os.makedirs(plans_dir(cfg), exist_ok=True); C.atomic_write_json(plan_path(cfg, rec["signal_key"]), rec)


def _epoch(s: str | None) -> int | None:
    d = C.parse_iso(s); return int(d.timestamp()) if d else None


def logline(rec: dict, msg: str) -> None:
    rec.setdefault("log", []).append({"t": C.now_iso(), "msg": msg}); rec["log"] = rec["log"][-LOG_MAX:]


def new_record(sig: dict, plan: dict, warn: list[str], inverse: bool, entry_mode: str) -> dict:
    lv = sig.get("levels") or {}
    e, sl = float(lv["entry"]), float(lv["sl"]); R = abs(e - sl)
    d0 = 1 if sig.get("direction") == "BUY" else -1; d = -d0 if inverse else d0
    def mir(p):
        if p in (None, "", 0): return None
        p = float(p); return 2.0 * e - p if inverse else p
    two = bool(lv.get("two_target")) and lv.get("tp1") and lv.get("tp2")
    tp2 = lv.get("tp2") if two else (lv.get("tp2") or lv.get("tp"))
    eb = _epoch(sig.get("decision_bar_server") or sig.get("decision_bar"))
    return {"schema": "live-plan/v1", "signal_key": sig["signal_key"], "symbol": sig["symbol"], "signal_id": int(sig["signal_id"]),
            "strategy": sig.get("strategy", ""), "signal_direction": sig.get("direction"), "created_at": C.now_iso(), "status": "approving",
            "inverse": bool(inverse), "entry_mode": entry_mode, "rbase": "fill", "plan": plan, "warnings": warn,
            "card": {"d": d, "e": e, "R": R, "sl": mir(sl), "tp1": mir(lv.get("tp1")) if two else None, "tp2": mir(tp2), "entry_bar_open": eb},
            "full_lots_est": float((sig.get("sizing") or {}).get("lots") or 0), "approve_task": None,
            "legs": [], "banks": [], "exit": None, "pending": {}, "sl_rej": {}, "log": []}


def approve(cfg: dict, sig: dict, plan_in: dict, inverse: bool, entry_mode: str) -> tuple[bool, str, dict | None]:
    """web app: validate the plan, write the approve task (plan=1 + entry_pct / tp2 / inverse) and the plan record."""
    try: plan, warn = live_validate(plan_in)
    except ValueError as e: return False, f"plan not valid: {e}", None
    old = load_plan(cfg, sig["signal_key"])
    if old and old.get("status") == "approving":                     # its approve already refused (a broker refusal leaves the signal
        a = C.ack(cfg, old.get("approve_task") or "")                # open, 2026-10-12) but the runner has not marked it yet
        if a and a.get("result") != "accepted": old["status"] = "refused"
    if old and old.get("status") in ("approving", "running"): return False, "this signal already has a running plan", None
    if old:                                                          # an earlier refused attempt: kept beside it
        os.makedirs(os.path.join(plans_dir(cfg), "archive"), exist_ok=True)
        C.atomic_write_json(os.path.join(plans_dir(cfg), "archive", f"{sig['signal_key']}.{int(time.time())}.json"), old)
    rec = new_record(sig, plan, warn, inverse, entry_mode)
    params = {"entry_mode": entry_mode, "size": "full", "plan": 1, "entry_pct": plan["entry"]["pct"], "tp2": 1 if plan["tp2"] else 0}
    if inverse: params["inverse"] = 1
    tid = C.write_task(cfg, sig["symbol"], "approve", params, signal_id=sig["signal_id"])
    rec["approve_task"] = tid
    logline(rec, f"approved with a custom plan ({len(plan['levers'])} levers, entry {plan['entry']['pct']:g}%, TP2 {'on' if plan['tp2'] else 'off'}"
                 f"{', INVERSE' if inverse else ''}); waiting for the EA")
    save_plan(cfg, rec)
    return True, "approve sent with your plan", rec


# ------------------------------------------------------------------------------------------------------------- the live frame
def rbase(rec: dict) -> tuple[int, float, float]:
    """the plan's R frame -> (d, origin price, 1R). rbase 'fill' (every record from 2026-10-13): the first leg's fill and |fill - signal
    stop|, the position's own R; else (and before the fill is known) the card: its entry and |card entry - signal stop|."""
    c = rec["card"]; f = rec.get("fill")
    if rec.get("rbase") == "fill" and f:
        px = float(f["px"]); R = abs(px - float(c["sl"]))
        if R > 0: return c["d"], px, R
    return c["d"], c["e"], c["R"]


def frame(bars: list, rec: dict, t0: int, fill_px: float, h4_opens: list[int]) -> tuple[dict, dict]:
    """M1 bars [[t,o,h,l,c,v,spread]] (server clock) from the fill minute t0 -> (F, ctx) in the plan's R (rbase), the box frames' layout
    (backtest_engine.decode_frame): a BUY's exit side is the bid, a SELL's the ask (bid + the bar's spread), its entry spread eo."""
    c = rec["card"]; d, e, R = rbase(rec)
    a = np.asarray([b[:7] for b in bars if b[0] >= t0], dtype=np.float64)
    t, o, h, l, cl, sp = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 6]
    s = sp / R
    if d > 0:
        bo, bh, bl, bc = (o - e) / R, (h - e) / R, (l - e) / R, (cl - e) / R
        F = {"xo": bo, "xh": bh, "xl": bl, "xc": bc, "eo": s}
    else:
        bo, bh, bl, bc = (e - o) / R, (e - l) / R, (e - h) / R, (e - cl) / R
        F = {"xo": bo - s, "xh": bh - s, "xl": bl - s, "xc": bc - s, "eo": np.zeros(len(a))}
    F.update(bo=bo, bh=bh, bc=bc, n=len(a))
    tm = ((t - t0) // 60).astype(np.int64)
    off = [int((x - t0) // 60) for x in h4_opens] + [BIG] * 1000      # bars not yet opened: never
    e0 = d * (fill_px - e) / R
    tp1 = None if c.get("tp1") is None else d * (c["tp1"] - e) / R
    tp2 = None if c.get("tp2") is None else d * (c["tp2"] - e) / R
    ctx = {"e0": e0, "be": e0, "tp2": tp2, "tp1": tp1, "off": off, "tm": tm, "t_next": int(tm[-1]), "n_full": len(a), "up": d > 0}
    return F, ctx


# ------------------------------------------------------------------------------------------------------------- the runner
FINAL = ("done", "refused", "stopped")


class Runner:
    """every POLL_S: for each active plan record, read the EA's acks, find the legs on the account, replay the plan on the live M1 path
    and send what the replay shows (one-shot banks / adds / exits once; stops followed). Runs as a daemon thread in the monitor."""

    def __init__(self, cfg: dict, log=print):
        self.cfg = cfg; self.log = log; self.bars = {}; self.h4 = {}; self.specs = {}; self.errs = {}

    def start(self) -> None:
        threading.Thread(target=self.loop, name="plan-runner", daemon=True).start()

    def loop(self) -> None:
        self.log("plan runner up")
        while True:
            t0 = time.time()
            try: self.tick()
            except Exception as e: self.log(f"plan runner error: {e!r} {traceback.format_exc()[-600:]}")
            time.sleep(max(0.5, POLL_S - (time.time() - t0)))

    def tick(self) -> None:
        d = plans_dir(self.cfg)
        if not os.path.isdir(d): return
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"): continue
            rec = C.load_json(os.path.join(d, fn))
            if not rec or rec.get("status") in FINAL: continue
            try:
                if self.step(rec):
                    cur = C.load_json(os.path.join(d, fn)) or {}
                    if cur.get("status") == "stopped": continue              # stopped from the page meanwhile: the trader's word stands
                    save_plan(self.cfg, rec)
                self.errs.pop(fn, None)
            except Exception as e:
                msg = f"{e!r}"
                if self.errs.get(fn) != msg: self.log(f"plan {fn}: {msg} {traceback.format_exc()[-800:]}")
                self.errs[fn] = msg

    # -------------------------------------------------------------------------------------------------- inputs
    def spec(self, sym: str) -> dict | None:
        from live import mt5feed
        s = self.specs.get(sym)
        if s and time.time() - s[0] < 3600: return s[1]
        sp = mt5feed.spec(sym)
        if sp: self.specs[sym] = (time.time(), sp)
        return sp

    def m1(self, key: str, sym: str, t0: int, now_srv: int) -> list | None:
        """M1 bars from t0 (server clock), cached per plan; the tail refreshed every poll (the forming minute included)."""
        from live import mt5feed
        c = self.bars.get(key)
        if not c or c["t0"] != t0 or not c["bars"]:
            b = mt5feed.bars_sp(sym, "m1", t0 - 60, now_srv + 120)
            if not b: return None
            c = {"t0": t0, "bars": [x for x in b if x[0] >= t0]}
            self.bars[key] = c
        tail = mt5feed.bars_sp(sym, "m1", n=8)
        if not tail: return c["bars"] or None
        if c["bars"] and tail[0][0] > c["bars"][-1][0] + 60:            # a gap (the runner or the feed paused): fill it from the range
            b = mt5feed.bars_sp(sym, "m1", c["bars"][-1][0], now_srv + 120)
            if b: c["bars"] = [x for x in c["bars"] if x[0] < b[0][0]] + [x for x in b if x[0] >= t0]
        ft = tail[0][0]
        c["bars"] = [x for x in c["bars"] if x[0] < ft] + [x for x in tail if x[0] >= t0]
        return c["bars"] or None

    def h4_opens(self, key: str, sym: str, eb: int, now_srv: int) -> list[int] | None:
        from live import mt5feed
        c = self.h4.get(key)
        if c and c["eb"] == eb and now_srv < c["last"] + 14400 and time.time() - c["at"] < 300: return c["opens"]
        b = mt5feed.bars_sp(sym, "h4", eb - 60, now_srv + 14400)
        if b is None: return None
        opens = [int(x[0]) for x in b if x[0] >= eb]
        self.h4[key] = {"eb": eb, "opens": opens, "last": opens[-1] if opens else eb, "at": time.time()}
        return opens

    # -------------------------------------------------------------------------------------------------- one plan
    def acks(self, rec: dict) -> bool:
        ch = False
        for tid, pd in list((rec.get("pending") or {}).items()):
            a = C.ack(self.cfg, tid)
            if not a:
                if time.time() - pd["at"] > ACK_WAIT_S:
                    del rec["pending"][tid]; ch = True
                    if pd["verb"] == "plan_add":
                        for L in rec["legs"]:
                            if L["q"] == pd.get("q") and L["status"] == "sending": L["status"] = "refused"; L["reason"] = "no answer from the EA"
                    logline(rec, f"no answer from the EA to {pd['what']} (will retry if still wanted)")
                continue
            del rec["pending"][tid]; ch = True
            ok = a.get("result") == "accepted"; refs = a.get("refs") or {}; why = C.reason_text(a.get("reason"))
            if pd["verb"] == "plan_add":
                for L in rec["legs"]:
                    if L["q"] != pd.get("q"): continue
                    if ok and int(refs.get("posid") or 0) > 0:
                        L.update(status="open", posid=int(refs["posid"]), lots=refs.get("lots"), entry=refs.get("entry"))
                        logline(rec, f"add {L['q']} filled: {refs.get('lots')} lots at {refs.get('entry')} (pos {refs.get('posid')}), stop {refs.get('pos_sl') or pd.get('sl')}")
                    else:
                        L["status"] = "refused"; L["reason"] = why
                        logline(rec, f"add {L['q']} refused by the EA: {why}")
            elif pd["verb"] == "set_sl":
                if ok: logline(rec, f"stop moved: {pd['what']}")
                else:
                    rec.setdefault("sl_rej", {})[str(pd["posid"])] = pd.get("sl")
                    logline(rec, f"stop move refused ({pd['what']}): {why}")
            else:
                logline(rec, f"{pd['what']}: {'done' if ok else 'refused - ' + why}")
        return ch

    def leg0(self, rec: dict) -> int:
        """the first leg's posid: from the approve ack (a market fill), else the positions files (a pending order that filled)."""
        a = C.ack(self.cfg, rec.get("approve_task") or "")
        pid = int(((a or {}).get("refs") or {}).get("posid") or 0)
        if pid: return pid
        for sub in (False, True):
            for p in C.list_positions(self.cfg, closed=sub):
                if p.get("symbol") == rec["symbol"] and int(p.get("signal_id") or 0) == rec["signal_id"] and int(p.get("tranche") or 0) != 5:
                    return int(p["posid"])
        return 0

    def step(self, rec: dict) -> bool:
        from live import mt5feed
        cfg = self.cfg; ch = self.acks(rec); sym = rec["symbol"]
        if not rec.get("legs"):
            a = C.ack(cfg, rec.get("approve_task") or "")
            if not a:
                if time.time() - (_epoch(rec["created_at"]) or 0) > 900:
                    rec["status"] = "refused"; logline(rec, "the EA never answered the approve (15 min) - plan closed"); return True
                return ch
            if a.get("result") != "accepted":
                rsn = a.get("reason")
                rec["status"] = "refused"
                logline(rec, C.order_fail_text(rsn, a.get("refs"), sym) if str(rsn or "").startswith("order_failed") else f"the EA refused the approve: {C.reason_text(rsn)}")
                return True
            pid = self.leg0(rec)
            if not pid:
                s = C.signal(cfg, rec["signal_key"]) or {}
                if s.get("status") in ("cancelled", "rejected", "expired", "skipped"):
                    rec["status"] = "done"; logline(rec, f"the pending order never filled ({s.get('status')}) - plan closed"); return True
                if rec.get("status") != "waiting_fill": rec["status"] = "waiting_fill"; logline(rec, "pending order placed - the plan starts when it fills"); return True
                return ch
            pj = C.position(cfg, f"{sym}-{pid}") or {}
            if not pj: return ch                                           # the EA writes the positions file a moment after the fill
            if not str(pj.get("entry_mode", "")).startswith("plan"):
                # an EA without plan rows (an old build) opened it as an ordinary trade with its own rules ON: never manage it twice
                rec["status"] = "refused"; logline(rec, f"the EA opened pos {pid} as an ordinary trade (entry mode '{pj.get('entry_mode', '?')}'), "
                                                        "not a plan row - its own rules manage it; the plan is NOT running"); return True
            rec["legs"] = [{"q": 0, "posid": pid, "src": -1, "status": "open"}]; rec["status"] = "running"; ch = True
            logline(rec, f"first leg filled (pos {pid}) - the plan is running")
        pos = mt5feed.positions()
        if pos is None: return ch                                         # feed down: nothing is decided blind
        bp = {int(x["ticket"]): x for x in pos if x.get("symbol") == sym}
        L0 = rec["legs"][0]
        if not rec.get("fill"):
            x = bp.get(L0["posid"])
            pj = C.position(cfg, f"{sym}-{L0['posid']}") or {}
            if x: rec["fill"] = {"t": int(x["time"]), "px": float(x["price_open"])}
            elif pj.get("opened_at") and pj.get("entry"): rec["fill"] = {"t": int(pj["opened_at"]), "px": float(pj["entry"])}
            else: return ch
            fl = float(pj.get("full_lots") or 0)
            rec["full_lots"] = fl if fl > 0 else (float(x["volume"]) * 100.0 / max(1.0, rec["plan"]["entry"]["pct"]) if x else rec["full_lots_est"])
            rec.setdefault("rbase", "fill"); ch = True
            if rec["rbase"] == "fill":
                c = rec["card"]; d, e, R = rbase(rec); dig = int((self.spec(sym) or {}).get("digits") or 5)
                logline(rec, f"R counts from your fill {e:.{dig}f} to the signal stop {float(c['sl']):.{dig}f}: 1R = {R:.{dig}f}"
                             + (f" (the card's entry was {c['e']:.{dig}f} with 1R {c['R']:.{dig}f}: your fill was {d * (e - c['e']) / c['R']:+.2f}R from it)"
                                if abs(e - c["e"]) > 0.05 * c["R"] else ""))
        for L in rec["legs"]:
            if L["status"] == "open" and L.get("posid") and int(L["posid"]) not in bp:
                L["status"] = "closed"; ch = True; logline(rec, f"leg {L['q']} (pos {L['posid']}) is closed on the account")
        if all(L["status"] in ("closed", "refused", "skipped") for L in rec["legs"]) and not rec.get("pending"):
            rec["status"] = "done"; logline(rec, "every leg is closed - the plan is finished"); return True
        tk = mt5feed.tick(sym); spec = self.spec(sym)
        if not tk or not spec: return ch
        now_srv = int(tk["t"]); t0 = rec["fill"]["t"] - rec["fill"]["t"] % 60
        bars = self.m1(rec["signal_key"], sym, t0, now_srv)
        opens = self.h4_opens(rec["signal_key"], sym, int(rec["card"]["entry_bar_open"]), now_srv)
        if not bars or opens is None: return ch
        F, ctx = frame(bars, rec, t0, rec["fill"]["px"], opens)
        if F["n"] < 1: return ch
        P = BE.compile_cfg(self.capped(rec), ctx)
        res = run_state(F, ctx, P)
        acts = self.actions(rec, res, P, bp, tk, spec)
        rec["model"] = {"R": round(float(res[0][0]), 3), "kind": int(res[0][1]), "minute": int(res[0][2]), "at": C.now_iso(), "bars": int(F["n"])}
        for x in acts:
            tid = C.write_task(cfg, sym, x["verb"], x["params"], position_id=x["posid"], issued_by="plan")
            rec.setdefault("pending", {})[tid] = {"verb": x["verb"], "posid": x["posid"], "what": x["what"], "at": time.time(), "q": x.get("q"), "sl": x["params"].get("sl")}
            logline(rec, "sent: " + x["what"])
        return True

    @staticmethod
    def capped(rec: dict) -> dict:
        """the plan with its 1R cap in the EA's terms: the EA measures 1R from the FIRST FILL to the signal stop (full lots x that distance).
        In the fill frame (rbase 'fill') that IS the replay's 1R (ratio 1, the cap as built); a card-frame record replays in the card's R -
        a fill better than the card entry makes the EA's 1R smaller, so the cap shrinks with it (an add the replay fits under the cap must
        also fit under the EA's, which refuses anything over 1R x 1.05)."""
        cfg = rec["plan"]
        if cfg.get("risk_cap") is None or not rec.get("fill"): return cfg
        ratio = abs(float(rec["fill"]["px"]) - float(rec["card"]["sl"])) / rbase(rec)[2]
        if ratio >= 1.0: return cfg
        return dict(cfg, risk_cap=round(min(float(cfg["risk_cap"]), max(0.01, ratio * 0.99)), 4))

    def actions(self, rec: dict, res, P: dict, bp: dict, tk: dict, spec: dict) -> list[dict]:
        (Rv, kind, jx, gross, nbf, naf, cut), (ls, lst, alive, nl, src, sz0, bk_done, ad_done, mv_done) = res
        c = rec["card"]; d, e, R = rbase(rec)
        step = spec.get("volume_step") or 0.01; vmin = spec.get("volume_min") or 0.01
        pt = spec.get("point") or 1e-5; dig = int(spec.get("digits") or 5); stops = (spec.get("stops_level") or 0) * pt
        busy = {(v["posid"], v["verb"]) for v in (rec.get("pending") or {}).values()}
        open_legs = [L for L in rec["legs"] if L["status"] == "open" and int(L["posid"]) in bp]
        price = lambda x: round(e + d * float(x) * R, dig)
        lots = lambda x: round(round(x / step) * step, 2)
        out = []
        def close(L, why):
            if (L["posid"], "close") not in busy:
                out.append({"verb": "close", "posid": int(L["posid"]), "params": {"why": "plan_" + why}, "what": f"close leg {L['q']} (pos {L['posid']}) - {why}"})
        # ---- exits: a cut, or everything banked; then nothing else
        if kind in (2, 4) and not rec.get("exit"):
            why = ("cut at its time (" + self.cut_text(rec, P, cut) + ")") if kind == 2 else "everything banked"
            rec["exit"] = {"kind": int(kind), "why": why, "at": C.now_iso()}
            rec["banks"] = sorted(set(rec.get("banks", [])) | {b for b in range(P["nbk"]) if bk_done[b]})
            for L in open_legs: close(L, why)
            return out
        if rec.get("exit"):
            last = rec["exit"].get("retry_at", 0)
            if open_legs and time.time() - last > 30:
                rec["exit"]["retry_at"] = time.time()
                for L in open_legs: close(L, rec["exit"]["why"] + " (retry)")
            return out
        if kind == 1:                                                      # TP2 in the replay: the broker's TP closes it; else at market
            seen = rec.setdefault("tp2_seen", time.time())
            if (not P["tp2_on"]) or time.time() - seen > 60:
                rec["exit"] = {"kind": 1, "why": "TP2 reached", "at": C.now_iso()}
                for L in open_legs: close(L, "TP2 reached")
            return out
        if kind == 0:
            if not rec.get("model_stopped"): rec["model_stopped"] = C.now_iso(); logline(rec, "the replay has every leg stopped - left to the broker's stops")
            return out
        # ---- banks (one-shot, in level order)
        for b in range(P["nbk"]):
            if not bk_done[b] or b in rec.setdefault("banks", []): continue
            rec["banks"].append(b); f = float(P["bk_pct"][b]); lvl = float(P["bk_lvl"][b])
            for L in open_legs:
                vol = float(bp[int(L["posid"])]["volume"]); amt = lots(f * vol)
                if amt < vmin - 1e-9:
                    logline(rec, f"bank {f * 100:g}% at {lvl:+.2f}R: {f * vol:.3f} lots of leg {L['q']} is under the minimum lot - nothing banked"); continue
                if vol - amt < vmin - 1e-9: close(L, f"bank {f * 100:g}% at {lvl:+.2f}R (the rest is under the minimum lot)")
                else: out.append({"verb": "close_part", "posid": int(L["posid"]), "params": {"lots": amt},
                                  "what": f"bank {f * 100:g}% at {lvl:+.2f}R: {amt:.2f} of {vol:.2f} lots of leg {L['q']} (pos {L['posid']})"})
        # ---- adds (one-shot, a new leg each)
        known = {L["q"] for L in rec["legs"]}; L0 = rec["legs"][0]
        for q in range(1, int(nl)):
            if q in known: continue
            n_l = round(math.floor(float(sz0[q]) * float(rec["full_lots"]) / step + 1e-9) * step, 2)   # adds round DOWN: never over the cap
            L = {"q": q, "src": int(src[q]), "status": "sending", "posid": None}
            rec["legs"].append(L)
            if n_l < vmin - 1e-9:
                L["status"] = "skipped"; logline(rec, f"add {q}: {float(sz0[q]) * 100:g}% is {float(sz0[q]) * rec['full_lots']:.3f} lots - under the minimum lot, skipped"); continue
            tp = c["tp2"] if (P["tp2_on"] and c.get("tp2")) else 0
            out.append({"verb": "plan_add", "posid": int(L0["posid"]), "q": q,
                        "params": {"lots": n_l, "sl": price(lst[q]), "tp": tp, "leg": q},
                        "what": f"add {q}: {n_l:.2f} lots ({float(sz0[q]) * 100:g}%), stop {price(lst[q])} ({float(lst[q]):+.2f}R)"})
        # ---- stops (followed every poll)
        tol = max(2 * pt, 0.003 * R); lower = bool(P["tr_on"] and P["tr_lower"])
        for L in open_legs:
            q = L["q"]; pid = int(L["posid"])
            if q >= int(nl) or not alive[q] or (pid, "set_sl") in busy or (pid, "close") in busy: continue
            want = price(lst[q]); cur = float(bp[pid].get("sl") or 0)
            diff = d * (want - cur) if cur else 1.0
            tighter, looser = diff > tol, diff < -tol
            if not (tighter or (looser and lower)): continue
            if (rec.get("sl_rej") or {}).get(str(pid)) == want: continue
            through = (d > 0 and want >= float(tk["bid"]) - stops) or (d < 0 and want <= float(tk["ask"]) + stops)
            if through: close(L, f"the plan's stop {float(lst[q]):+.2f}R is already past the price"); continue
            out.append({"verb": "set_sl", "posid": pid, "params": {"sl": want, "loosen": 1 if looser else 0},
                        "what": f"leg {q} (pos {pid}) stop {cur} -> {want} ({float(lst[q]):+.2f}R)"})
        return out

    @staticmethod
    def cut_text(rec: dict, P: dict, ci: int) -> str:
        cuts = [x for x in rec["plan"]["levers"] if x["type"].startswith("cut")]
        if not (0 <= ci < len(cuts)): return "a cut"
        x = cuts[ci]
        if x["type"] == "cut_avg": return f"{x['at_h']:g} h, average R below {x['below']:+g}" + (f" over the last {x['window_h']:g} h" if x.get("window_h") else "")
        return f"{x['at_h']:g} h, +{x['reach']:g}R not reached"
