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
FEED_MISS_S = 1800.0                # a leg the account list misses while the EA still lists it open: no decision for up to this long
LOG_MAX = 400


# ------------------------------------------------------------------------------------------------------------- the kernel (+ state)
def kern_state(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, tp2_on, tp2_R, cap, F0,
         bk_lvl, bk_pct, nbk,
         mv_when, mv_to, mv_legs, mv_src, nmv,
         ad_type, ad_lvl, ad_until, ad_k, ad_early, ad_hold, ad_pct, ad_stop, ad_trade, nad,
         ct_type, ct_k, ct_thr, nct, ad_wait, ad_cap, ct_ws, tr_on, tr_dist, tr_lower, tr_chk, j0=0, j1=-1, init=None):
    """-> (R of the full ruled position, kind 0 stopped / 1 TP2 / 2 cut / 3 frame end / 4 banked out (everything open was banked:
    the trade ends that minute), exit minute, gross units entered, banks fired, adds fired, cut index or -1), state.
    LIVE (a plan edited mid-trade, trader 2026-10-13): minutes j0..j1-1 only, starting from `init` (the previous segment's end state,
    its per-lever flags already mapped to THIS plan's levers - see plan_live.handoff); a segment that ends before the frame end with
    the trade still on returns (None, state). j0=0, j1=-1, init=None is kern exactly (pipeline/plan_live_parity.py)."""
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
    if init is not None:                               # LIVE: continue the previous segment - its legs, sizes, stops and totals
        m0 = int(init["nl"]); nleg = m0 + nad
        ls = np.zeros(nleg); le = np.zeros(nleg); lst = np.zeros(nleg); alive = np.zeros(nleg, dtype=np.bool_)
        lp = np.zeros(nleg); fxd = np.zeros(nleg, dtype=np.bool_); src = np.full(nleg, -1, dtype=np.int64); sz0 = np.zeros(nleg)
        ls[:m0] = init["ls"][:m0]; le[:m0] = init["le"][:m0]; lst[:m0] = init["lst"][:m0]; alive[:m0] = init["alive"][:m0]
        lp[:m0] = init["lp"][:m0]; src[:m0] = init["src"][:m0]; sz0[:m0] = init["sz0"][:m0]; nl = m0
        gross = init["gross"]; locked = init["locked"]; tstop = init["tstop"]; cs = init["cs"]; tr_hi = init["tr_hi"]
        nbf = init["nbf"]; naf = init["naf"]
        bk_done[:nbk] = init["bk_done"][:nbk]; mv_done[:nmv] = init["mv_done"][:nmv]; ad_done[:nad] = init["ad_done"][:nad]
        ad_held[:nad] = init["ad_held"][:nad]; ct_hit[:nct] = init["ct_hit"][:nct]; ct_csw[:nct] = init["ct_csw"][:nct]
    if j1 < 0: j1 = n

    def st():
        return {"ls": ls, "le": le, "lst": lst, "alive": alive, "lp": lp, "nl": nl, "src": src, "sz0": sz0, "gross": gross, "locked": locked,
                "tstop": tstop, "cs": cs, "tr_hi": tr_hi, "nbf": nbf, "naf": naf, "bk_done": bk_done, "mv_done": mv_done, "ad_done": ad_done,
                "ad_held": ad_held, "ct_hit": ct_hit, "ct_csw": ct_csw}
    for j in range(j0, j1):
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
            return (locked, 0, j, gross, nbf, naf, -1), st()
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
                        return (locked, 4, j, gross, nbf, naf, -1), st()
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
            return (v, 1, j, gross, nbf, naf, -1), st()
        for c in range(nct):
            if j == ct_k[c]:
                aw = avg if ct_ws[c] < 0 else (cs - ct_csw[c]) / (j - ct_ws[c])          # windowed: the minutes after the window start
                fire = (aw < ct_thr[c]) if ct_type[c] == 0 else (not ct_hit[c])
                if fire:
                    v = locked
                    for q in range(nl):
                        if alive[q]:
                            v += ls[q] * (xc[j] - le[q])
                    return (v, 2, j, gross, nbf, naf, c), st()
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
    if j1 < n: return None, st()
    v = locked
    for q in range(nl):
        if alive[q]:
            v += ls[q] * (xc[n - 1] - le[q])
    return (v, 3, n - 1, gross, nbf, naf, -1), st()


def run_state(F: dict, ctx: dict, P: dict, j0: int = 0, j1: int = -1, init: dict | None = None):
    """backtest_engine.run with kern_state -> ((R, kind, exit_j, gross, banks, adds, cut) or None at a segment end, state dict: ls, lst,
    alive, nl, src, sz0, bk_done, ad_done, mv_done, ... - kern_state's st())."""
    return kern_state(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], F["n"], ctx["e0"], ctx["be"],
                      P["tp2_on"], P["tp2_R"], P["cap"], P["F0"], P["bk_lvl"], P["bk_pct"], P["nbk"],
                      P["mv_when"], P["mv_to"], P["mv_legs"], P["mv_src"], P["nmv"],
                      P["ad_type"], P["ad_lvl"], P["ad_until"], P["ad_k"], P["ad_early"], P["ad_hold"], P["ad_pct"], P["ad_stop"], P["ad_trade"], P["nad"],
                      P["ct_type"], P["ct_k"], P["ct_thr"], P["nct"], P["ad_wait"], P["ad_cap"], P["ct_ws"], P["tr_on"], P["tr_dist"], P["tr_lower"], P["tr_chk"],
                      j0, j1, init)


# ------------------------------------------------------------------------------------------------------------- plan edits (versions)
# trader 2026-10-13: "on open trades, i'd like to be able to add stuff/edit/delete stuff to the plan". The runner replays the whole trade
# from the fill every poll, so an edit is a new VERSION of the plan from its minute: the replay runs version 0 from the fill, hands its
# end state (legs, sizes, stops, totals) to version 1 at the edit minute, and so on. Each lever has an id (rec["lever_ids"], aligned
# with rec["plan"]["levers"]; BE.validate drops unknown keys and compile_cfg sorts banks, so the id cannot live in the lever):
#   - an unchanged lever keeps its id and what it already did (a bank that fired stays fired);
#   - a changed lever gets a NEW id: it starts fresh from the edit minute (a level touched before the edit does not count);
#   - a deleted lever is gone, but what it did stays (a moved stop stays where it is - stops never move back, except a trail's 'lower');
#   - a time that passed before the edit never fires (as a time before the fill); "average R since entry" and "+X R reached" still
#     look at the whole trade since the fill;
#   - a new or changed trail runs one check at the edit minute (tightening it applies at once), then every N hours as before.
def lever_ids(plan: dict, ids: list | None) -> list[str]:
    n = len(plan.get("levers") or [])
    return list(ids) if ids and len(ids) == n else [f"l{i}" for i in range(n)]


def versions(rec: dict) -> list[dict]:
    """[{plan, ids, from_t (server epoch of the first minute it runs; None = the fill)}, ...] - one entry until the first edit."""
    return rec.get("versions") or [{"plan": rec["plan"], "ids": lever_ids(rec["plan"], rec.get("lever_ids")), "from_t": None}]


def compile_ids(plan: dict, ids: list[str]) -> dict:
    """the lever ids in compile_cfg's order per kind: banks by level (stable), moves / adds / cuts in stack order, the trail."""
    L = list(zip(plan["levers"], ids))
    return {"bank": [i for c, i in sorted([x for x in L if x[0]["type"] == "bank"], key=lambda x: x[0]["at"])],
            "move": [i for c, i in L if c["type"] == "move"], "add": [i for c, i in L if c["type"].startswith("add")],
            "cut": [i for c, i in L if c["type"].startswith("cut")], "trail": [i for c, i in L if c["type"] == "trail"]}


def handoff(st: dict, ia: dict, ib: dict, Pb: dict, F: dict, j0: int) -> dict:
    """the end state of a segment (levers ia) as the init of the next (levers ib, compiled Pb) starting at minute j0: legs and totals
    carry; each lever's flags carry by id (a new id starts fresh); the cuts' history ("+X reached", a window's start sum) is recomputed
    from the path, so a cut added later sees the whole trade."""
    def by_id(arr, a_ids, b_ids, size):
        out = np.zeros(max(size, 1), dtype=np.bool_)
        for k, x in enumerate(b_ids):
            if x in a_ids: out[k] = arr[a_ids.index(x)]
        return out
    init = {k: st[k] for k in ("ls", "le", "lst", "alive", "lp", "nl", "src", "sz0", "gross", "locked", "tstop", "cs", "nbf", "naf")}
    init["bk_done"] = by_id(st["bk_done"], ia["bank"], ib["bank"], Pb["nbk"])
    init["mv_done"] = by_id(st["mv_done"], ia["move"], ib["move"], Pb["nmv"])
    init["ad_done"] = by_id(st["ad_done"], ia["add"], ib["add"], Pb["nad"])
    init["ad_held"] = by_id(st["ad_held"], ia["add"], ib["add"], Pb["nad"])
    bh = np.asarray(F["bh"][:j0]); csum = np.cumsum(np.asarray(F["bc"], dtype=np.float64)[:j0])
    nct = Pb["nct"]; hit = np.zeros(max(nct, 1), dtype=np.bool_); csw = np.zeros(max(nct, 1))
    for c in range(nct):
        if Pb["ct_type"][c] == 1: hit[c] = bool(j0 > 0 and bh.max() >= Pb["ct_thr"][c])
        ws = int(Pb["ct_ws"][c])
        if 0 <= ws < j0: csw[c] = csum[ws]
    init["ct_hit"] = hit; init["ct_csw"] = csw
    init["tr_hi"] = st["tr_hi"] if (ia["trail"] and ib["trail"]) else -1e18      # the window's high since the last check carries
    return init


def replay(rec: dict, F: dict, ctx: dict, t0: int, vers: list[dict] | None = None):
    """the plan over every version -> (res, state, P, vi): res = kern's tuple, P / vi = the version the trade is in (or ended in).
    Version i runs from the first minute at or after its from_t; a version whose minute is not in the bars yet has not started."""
    vers = vers or versions(rec); tm = np.asarray(ctx["tm"], dtype=np.int64); n = int(F["n"])
    segs = []
    for i, v in enumerate(vers):
        j0 = 0 if (i == 0 or v.get("from_t") is None) else int(np.searchsorted(tm, (int(v["from_t"]) - t0) // 60, side="left"))
        if j0 >= n: break
        if segs and j0 <= segs[-1][1]: segs.pop()                    # two edits in one minute: the later one wins from that minute
        segs.append((i, j0))
    st = None; prev = None
    for k, (i, j0) in enumerate(segs):
        v = vers[i]; P = BE.compile_cfg(Runner.capped(rec, v["plan"]), ctx); ib = compile_ids(v["plan"], lever_ids(v["plan"], v.get("ids")))
        init = None
        if k > 0:
            ia = prev
            init = handoff(st, ia, ib, P, F, j0)
            if ib["trail"] and ib["trail"][0] not in ia["trail"]:   # a new or changed trail: one check at the edit minute
                P = dict(P, tr_chk=P["tr_chk"].copy()); P["tr_chk"][j0] = True
        j1 = segs[k + 1][1] if k + 1 < len(segs) else -1
        res, st = run_state(F, ctx, P, j0, j1, init)
        if res is not None: return res, st, P, i
        prev = ib
    raise RuntimeError("replay: the last segment returned no result")


def edit_path(cfg: dict, key: str) -> str: return os.path.join(plans_dir(cfg), "edits", f"{key}.json")


def norm_banks(rec: dict) -> None:
    """executed banks are lever ids since the edits (2026-10-13); an older record kept compile-order indices of its one version."""
    b = rec.get("banks") or []
    if any(isinstance(x, int) for x in b):
        v = versions(rec)[0]; ids = compile_ids(v["plan"], lever_ids(v["plan"], v.get("ids")))["bank"]
        rec["banks"] = [ids[x] if isinstance(x, int) and 0 <= x < len(ids) else x for x in b]


def lever_desc(L: dict) -> str:
    from live import backtest as BT
    return BT.describe({"entry": {"pct": 0}, "tp2": True, "levers": [L]}).split("; ", 1)[-1]


def lever_state(rec: dict, res, S: dict, P: dict, vi: int, F: dict) -> dict:
    """{lever id: 'done' / 'holding' / 'time passed'} for the CURRENT version's levers (the page's pills while editing): a one-shot that
    fired (or carried as fired), an add held at its bar, a cut whose time is behind the trade without firing."""
    vs = versions(rec)
    if vi != len(vs) - 1: return {}
    V = vs[vi]; ci = compile_ids(V["plan"], lever_ids(V["plan"], V.get("ids"))); out = {}; jn = int(F["n"]) - 1
    for k, x in enumerate(ci["bank"]):
        if S["bk_done"][k]: out[x] = "done"
    for k, x in enumerate(ci["move"]):
        if S["mv_done"][k]: out[x] = "done"
    for k, x in enumerate(ci["add"]):
        if S["ad_done"][k]: out[x] = "done"
        elif S["ad_held"][k]: out[x] = "holding"
        elif P["ad_type"][k] == 1 and 0 <= int(P["ad_k"][k]) < jn: out[x] = "bar passed"
    for k, x in enumerate(ci["cut"]):
        if int(P["ct_k"][k]) < jn: out[x] = "time passed"
    return out


def build_edit(rec: dict, plan_in, ids_in, base_rev, from_t: int) -> dict:
    """the trader's edited plan -> the next version {plan, ids, from_t, rev, about, warnings}; raises ValueError in plain words.
    Fixed after the entry: the entry %, TP2 on/off (the EA's set_sl never touches the TP) and the size cap."""
    if rec.get("status") != "running" or rec.get("exit") or not rec.get("fill"): raise ValueError("only a running plan on an open trade can be edited")
    if base_rev not in (None, "") and int(base_rev) != int(rec.get("rev") or 0): raise ValueError("the plan changed since this page loaded - reload it and edit again")
    if not isinstance(plan_in, dict): raise ValueError("the plan must be an object")
    if len(versions(rec)) > 40: raise ValueError("at most 40 edits on one trade")
    cur = rec["plan"]
    newp, warn = live_validate(dict(plan_in, entry={"pct": cur["entry"]["pct"]}, tp2=cur["tp2"], cap_pct=cur.get("cap_pct", BE.CAP_DEFAULT)))
    old_ids = lever_ids(cur, rec.get("lever_ids")); old = dict(zip(old_ids, cur["levers"]))
    raw = plan_in.get("levers") or []
    ids_in = ids_in if isinstance(ids_in, list) and len(ids_in) == len(raw) else [None] * len(raw)
    rev = int(rec.get("rev") or 0) + 1; ids, used, about = [], set(), []
    for i, L in enumerate(newp["levers"]):
        cid = ids_in[i] if isinstance(ids_in[i], str) else None
        if cid in old and cid not in used and old[cid] == L:
            ids.append(cid); used.add(cid); continue
        ids.append(f"e{rev}.{i}")
        if cid in old and cid not in used: used.add(cid); about.append(f"changed: {lever_desc(old[cid])} -> {lever_desc(L)} (starts fresh now)")
        else: about.append(f"added: {lever_desc(L)}")
    about += [f"removed: {lever_desc(L)}" for x, L in old.items() if x not in used]
    if bool(newp.get("trim_to_cap")) != bool(cur.get("trim_to_cap")): about.append("trim adds to fit the cap: " + ("on" if newp.get("trim_to_cap") else "off"))
    if not about: raise ValueError("nothing changed")
    return {"plan": newp, "ids": ids, "from_t": int(from_t), "rev": rev, "at": C.now_iso(), "about": about, "warnings": warn}


def commit_edit(rec: dict, v: dict, req_id=None) -> None:
    if not rec.get("versions"): rec["versions"] = versions(rec)
    rec["versions"].append(v); rec["plan"] = v["plan"]; rec["lever_ids"] = v["ids"]; rec["rev"] = v["rev"]
    logline(rec, f"plan edited by you (edit {v['rev']}, from this minute): " + "; ".join(v["about"]))
    rec["edit_last"] = {"id": req_id, "ok": True, "rev": v["rev"], "at": C.now_iso()}


def preview_edit(cfg: dict, rec: dict, plan_in, ids_in, base_rev) -> dict:
    """the web app's dry run: the edit as if applied now -> what the runner would send on its next poll (the same actions() code), the
    plain-words changes, and warnings (a time already passed never fires). Nothing is written."""
    import copy
    from live import mt5feed
    if rec.get("status") != "running" or not rec.get("fill"): return {"ok": False, "error": "only a running plan on an open trade can be edited"}
    run = Runner(cfg, log=lambda m: None); r0 = copy.deepcopy(rec); norm_banks(r0)
    pos = mt5feed.positions(); fr = run.frame_now(r0)
    if pos is None or not fr: return {"ok": False, "error": "no live prices from the terminal right now - try again in a moment"}
    bp = {int(x["ticket"]): x for x in pos if x.get("symbol") == rec["symbol"]}
    try: v = build_edit(r0, plan_in, ids_in, base_rev, fr["now_srv"] - fr["now_srv"] % 60)
    except ValueError as e: return {"ok": False, "error": str(e)}
    r1 = copy.deepcopy(r0); commit_edit(r1, v)
    res, S, P, vi = replay(r1, fr["F"], fr["ctx"], fr["t0"])
    acts = run.actions(copy.deepcopy(r1), (res, S), P, bp, fr["tk"], fr["spec"], vi)
    res0, S0, P0, vi0 = replay(r0, fr["F"], fr["ctx"], fr["t0"])
    acts0 = run.actions(copy.deepcopy(r0), (res0, S0), P0, bp, fr["tk"], fr["spec"], vi0)
    warn = list(v["warnings"]); jn = int(fr["F"]["n"]) - 1; ci = compile_ids(v["plan"], v["ids"]); fresh = {x for x in v["ids"] if x.startswith(f"e{v['rev']}.")}
    for k, x in enumerate(ci["cut"]):
        if x in fresh and int(P["ct_k"][k]) < jn: warn.append(f"{lever_desc(v['plan']['levers'][v['ids'].index(x)])}: that time has passed - it will never fire")
    for k, x in enumerate(ci["add"]):
        if x in fresh and P["ad_type"][k] == 1 and 0 <= int(P["ad_k"][k]) < jn: warn.append(f"{lever_desc(v['plan']['levers'][v['ids'].index(x)])}: that bar has closed - it adds only if its wait / early condition still applies")
    kind = int(res[1])
    if kind == 0: warn.append("the replay with this plan has every leg stopped - the broker's stops decide from here")
    return {"ok": True, "about": v["about"], "now": [a["what"] for a in acts], "already": [a["what"] for a in acts0], "warn": warn,
            "R": round(float(res[0]), 3), "rev": int(rec.get("rev") or 0)}


def request_edit(cfg: dict, rec: dict, plan_in, ids_in, base_rev, wait_s: float = 12.0) -> dict:
    """the web app's save: drop the request for the runner (the record's only writer) and wait for it to be applied."""
    import uuid
    if rec.get("status") != "running": return {"ok": False, "error": "only a running plan can be edited"}
    rid = uuid.uuid4().hex; p = edit_path(cfg, rec["signal_key"]); os.makedirs(os.path.dirname(p), exist_ok=True)
    C.atomic_write_json(p, {"id": rid, "plan": plan_in, "ids": ids_in, "base_rev": base_rev, "at": C.now_iso()})
    t_end = time.time() + wait_s
    while time.time() < t_end:
        time.sleep(1.0)
        cur = load_plan(cfg, rec["signal_key"]) or {}
        e = cur.get("edit_last") or {}
        if e.get("id") == rid:
            return {"ok": True, "msg": f"plan updated (edit {e.get('rev')}) - the runner carries it out from this minute"} if e.get("ok") else {"ok": False, "error": e.get("msg") or "refused"}
    try: os.remove(p)
    except OSError: pass
    return {"ok": False, "error": f"the plan runner did not pick the edit up within {wait_s:.0f} s (is the monitor running?) - nothing was changed"}


def apply_suggestion(plan: dict, ids: list[str], sug: dict) -> tuple[dict, list]:
    """the positions reviewer's edit_plan params {remove: [id], change: [{id, field: value}], add: [lever]} on the CURRENT plan ->
    (the edited plan, ids: the kept levers' ids, None for added ones) - loaded into the page's editor, where the trader reviews and
    saves (build_edit then decides changed / unchanged). Raises ValueError (an unknown id - the plan changed since - or a bad lever)."""
    if not isinstance(sug, dict): raise ValueError("the suggestion is not an object")
    levers = [dict(L) for L in plan["levers"]]; ids = list(ids)
    rm = [str(x) for x in (sug.get("remove") or [])]
    for x in rm:
        if x not in ids: raise ValueError(f"lever {x} is not in the plan any more")
    for ch in sug.get("change") or []:
        if not isinstance(ch, dict) or ch.get("id") not in ids: raise ValueError(f"lever {ch.get('id') if isinstance(ch, dict) else ch} is not in the plan any more")
        L = levers[ids.index(ch["id"])]
        for k, v in ch.items():
            if k not in ("id", "type"): L[k] = v
    keep = [(L, i) for L, i in zip(levers, ids) if i not in rm] + [(dict(a), None) for a in (sug.get("add") or []) if isinstance(a, dict)]
    new = dict(plan, levers=[L for L, _ in keep])
    new = live_validate(new)[0]
    return new, [i for _, i in keep]


def plan_clock_h(rec: dict) -> float | None:
    """hours on the PLAN's clock now: 'N h' = H4 bar N // 4 after the entry-bar open, then N % 4 hours into it (backtest_engine.mark)."""
    from live import mt5feed
    try:
        tk = mt5feed.tick(rec["symbol"]); eb = int(rec["card"]["entry_bar_open"])
        b = mt5feed.bars_sp(rec["symbol"], "h4", eb - 60, int(tk["t"]) + 14400) if tk else None
        opens = [int(x[0]) for x in (b or []) if int(x[0]) >= eb and int(x[0]) <= int(tk["t"])]
        if not opens: return None
        return 4.0 * (len(opens) - 1) + min(4.0, (int(tk["t"]) - opens[-1]) / 3600.0)
    except Exception: return None


def plan_brief(rec: dict) -> list[str]:
    """the plan of a position, for the positions reviewer's bundle: who manages it, the R it counts in, every lever with its id, state
    and timing, and what the runner did lately."""
    from live import mt5feed
    st = rec.get("status", ""); out = []
    out.append(f"- **managed by the trader's PLAN** (status {st}{', edited ' + str(rec.get('rev')) + ' time(s)' if rec.get('rev') else ''}) - the plan runner carries"
               " it out every few seconds; the EA's own rules (bank at +1R, BE, bloc, shorts raise) are OFF for this trade")
    if st != "running": out.append("- the plan is not running: the trader manages this trade by hand (the EA's rules stay off) - plan edits are not possible")
    d, e, R = rbase(rec); c = rec["card"]; f = rec.get("fill") or {}
    if rec.get("rbase") == "fill": out.append(f"- the plan's R = from the fill {e} to the original stop {c['sl']} (1R = {R:.6g}) - the same R as 'R now' above")
    else:
        line = (f"- the plan's R counts from the SIGNAL CARD (approved before 2026-10-13): origin {e}, 1R = {R:.6g} - NOT the 'R now' above"
                f" (that counts from the fill {f.get('px')}); lever values below are in the card's R")
        try:
            tk = mt5feed.tick(rec["symbol"]); px = float(tk["bid"] if d > 0 else tk["ask"])
            line += f"; now the price is {d * (px - e) / R:+.2f}R in the plan's R"
        except Exception: pass
        out.append(line)
    if rec.get("levels"):
        out.append(f"- approved with the trader's own levels ({levels_text(rec['levels'])}); the signal card had entry {c.get('sig_entry')} / stop {c.get('sig_sl')}")
    if rec.get("arm"): out.append(f"- it was armed first and {arm_text(rec['arm'])[:300]}")
    m = rec.get("model") or {}
    if m: out.append(f"- the plan's own replay: {C.r_fmt(m.get('R'))} on the whole ruled size so far")
    ids = lever_ids(rec["plan"], rec.get("lever_ids")); ls = rec.get("lever_state") or {}; ph = plan_clock_h(rec)
    if ph is not None: out.append(f"- plan clock: {ph:.1f} h since the entry bar (the levers' 'N h' times count on this clock, H4 bars)")
    out.append("- levers (id · state · what it does) - use these ids in an edit_plan suggestion:")
    for L, i in zip(rec["plan"]["levers"], ids):
        t = L.get("at_h") if L["type"].startswith("cut") else (4.0 * L["bar"] if L["type"] == "add_bar" else None)
        when = "" if t is None or ph is None else (" (time passed)" if t <= ph else f" (in ~{t - ph:.1f} h)")
        out.append(f"  - [{i}] {ls.get(i, 'armed')} · {lever_desc(L)}{when}")
    if not rec["plan"]["levers"]: out.append("  - (no levers: in at the entry, out at TP2 or the stop)")
    out.append(f"- TP2 {'on (the broker TP)' if rec['plan'].get('tp2') else 'off - no broker TP'}; entry {rec['plan']['entry']['pct']:g}% (both fixed now)")
    lg = [x for x in (rec.get("log") or []) if not x["msg"].startswith("sent:")][-5:]
    if lg: out.append("- the plan runner lately: " + " | ".join(f"{x['t'][5:16].replace('T', ' ')} {x['msg'][:160]}" for x in lg))
    return out


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


# ------------------------------------------------------------------------------------------------------------- levels + waiting (2026-10-14)
# trader 2026-10-14: "add an option to change the SL, entry, TP1/2. keep the signal's default in the box, but allow it to be changed" and
# (the live advisor's plan) "if the advisor's ruling is a 'wait', it should have a plan that executes on its wait criteria".
#   plan["levels"] = {entry, sl, tp1, tp2} prices (absent / null = the signal's); a custom entry is a pending order at that price (the EA picks
#     limit or stop against the market), entry_h = its expiry in hours. The EA sizes on the custom entry / stop (the 1% rule holds) and the
#     plan's R counts from the fill to the custom stop.
#   plan["arm"] = {"when": [conditions met in order], "cancel": [any one cancels]} - the plan waits ARMED (nothing sent) until every `when`
#     condition has been met, one after the other, then sends the approve. Conditions (prices on the bid, the chart's price):
#       {"type": "touch", "side": "above"|"below", "px": P}       the price trades at or above / at or below P
#       {"type": "close", "tf": "m15"|"h1"|"h4", "side": .., "px": P}   a bar closes above / below P
#       {"type": "after", "t": "<ISO UTC>"}                           not before that time (a release)
#       {"type": "hours", "h": N}                                     N hours after the previous step (in cancel: after arming)
#     A wait always has a time limit (none given: ARM_DEFAULT_H). While armed, the runner keeps the signal open with a delay before its
#     deadline; the signal still dies if the price trades through ITS OWN stop at a bar close (the EA's rule) - the plan then ends.
ARM_TF = {"m15": 900, "h1": 3600, "h4": 14400}
ARM_MAX_H = 120.0
ARM_DEFAULT_H = 48.0
LEVEL_KEYS = ("entry", "sl", "tp1", "tp2")
LEVEL_NAME = {"entry": "entry", "sl": "stop", "tp1": "TP1", "tp2": "TP2"}


def split_plan(plan_in) -> tuple[dict, dict | None, dict | None]:
    """the builder's config -> (the kernel plan, levels, arm): levels and the wait are not lever-engine fields."""
    if not isinstance(plan_in, dict): raise ValueError("the plan must be an object")
    p = dict(plan_in); return p, p.pop("levels", None), p.pop("arm", None)


def sig_levels(sig: dict, inverse: bool = False) -> dict:
    """the signal's own levels as the plan uses them: {d, entry, sl, tp1, tp2} (tp1 only on a two-target card, as before; mirrored round
    the entry for an inverse)."""
    lv = sig.get("levels") or {}
    e, sl = float(lv["entry"]), float(lv["sl"])
    d0 = 1 if sig.get("direction") == "BUY" else -1
    def mir(p):
        if p in (None, "", 0): return None
        p = float(p); return 2.0 * e - p if inverse else p
    two = bool(lv.get("two_target")) and lv.get("tp1") and lv.get("tp2")
    return {"d": -d0 if inverse else d0, "entry": e, "sl": mir(sl), "tp1": mir(lv.get("tp1")) if two else None,
            "tp2": mir(lv.get("tp2") if two else (lv.get("tp2") or lv.get("tp")))}


def _px(v, what: str) -> float | None:
    if v in (None, ""): return None
    try: x = float(v)
    except (TypeError, ValueError): raise ValueError(f"{what} must be a price") from None
    if not (x > 0 and math.isfinite(x)): raise ValueError(f"{what} must be a positive price")
    return x


def _num_in(v, lo: float, hi: float, what: str) -> float:
    try: x = float(v)
    except (TypeError, ValueError): raise ValueError(f"{what} must be a number") from None
    if not (lo <= x <= hi): raise ValueError(f"{what} must be {lo:g}-{hi:g}")
    return x


def validate_levels(lv_in, sig: dict, inverse: bool = False) -> tuple[dict | None, list[str]]:
    """-> ({entry, sl, tp1, tp2, entry_h} with only the CHANGED levels set, else None; warnings). Sides are checked against the direction;
    the EA re-checks against the market (a stop through the price, the stops level, the R:R floor on a market entry)."""
    if lv_in in (None, {}): return None, []
    if not isinstance(lv_in, dict): raise ValueError("levels must be an object like {\"sl\": 1.0812}")
    b = sig_levels(sig); d = b["d"]; warn = []; out = {}
    for k in LEVEL_KEYS:
        x = _px(lv_in.get(k), "the " + LEVEL_NAME[k])
        if x is None: continue
        if b.get(k) is not None and abs(x - b[k]) <= 1e-9 * max(1.0, abs(b[k])): continue          # the signal's own level: unchanged
        out[k] = x
    if not out: return None, []
    if inverse: raise ValueError("custom levels are not available with Inverse - untick Inverse or keep the signal's levels")
    e = out.get("entry", b["entry"]); sl = out.get("sl", b["sl"]); tp1 = out.get("tp1", b["tp1"]); tp2 = out.get("tp2", b["tp2"])
    if not d * (e - sl) > 0: raise ValueError(f"the stop must be {'below' if d > 0 else 'above'} the entry on a {'BUY' if d > 0 else 'SELL'}")
    if tp2 is not None and not d * (tp2 - e) > 0: raise ValueError(f"TP2 must be {'above' if d > 0 else 'below'} the entry")
    if tp1 is not None and not d * (tp1 - e) > 0: raise ValueError(f"TP1 must be {'above' if d > 0 else 'below'} the entry")
    if tp1 is not None and tp2 is not None and d * (tp1 - tp2) > 0: warn.append("TP1 is beyond TP2")
    R0 = abs(b["entry"] - b["sl"]); R1 = abs(e - sl)
    if R1 < 0.25 * R0: warn.append(f"the stop is {R1 / R0:.0%} of the signal's distance - the EA refuses a stop under its minimum distance")
    if R1 > 3.0 * R0: warn.append(f"the stop is {R1 / R0:.1f}x the signal's distance - the size shrinks to keep the risk at 1R")
    if tp2 is not None and abs(tp2 - e) < R1: warn.append(f"TP2 is {abs(tp2 - e) / R1:.2f}R from the entry - the EA refuses a market entry under 1R (its R:R floor)")
    if "entry" in out:
        h = lv_in.get("entry_h")
        out["entry_h"] = 12.0 if h in (None, "") else _num_in(h, 1, ARM_MAX_H, "the pending order's hours")
    return out, warn


def _cond(c, i: int, where: str) -> dict:
    if not isinstance(c, dict): raise ValueError(f"{where} {i + 1}: must be an object")
    t = c.get("type")
    if t in ("touch", "close"):
        side = c.get("side")
        if side not in ("above", "below"): raise ValueError(f"{where} {i + 1}: side must be above or below")
        px = _px(c.get("px"), f"{where} {i + 1}'s price")
        if px is None: raise ValueError(f"{where} {i + 1}: needs a price")
        d = {"type": t, "side": side, "px": px}
        if t == "close":
            tf = c.get("tf", "h1")
            if tf not in ARM_TF: raise ValueError(f"{where} {i + 1}: tf must be m15, h1 or h4")
            d["tf"] = tf
        return d
    if t == "after":
        dt = C.parse_iso(str(c.get("t") or ""))
        if not dt: raise ValueError(f"{where} {i + 1}: 'after' needs a time like 2026-10-14T12:30:00Z")
        return {"type": "after", "t": dt.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if t == "hours":
        return {"type": "hours", "h": _num_in(c.get("h"), 0.25, ARM_MAX_H, f"{where} {i + 1}'s hours")}
    raise ValueError(f"{where} {i + 1}: unknown condition {str(t)[:30]!r} (touch / close / after / hours)")


def validate_arm(arm_in) -> tuple[dict | None, list[str]]:
    """-> ({when, cancel} or None, warnings). A wait always has a time limit: none given -> ARM_DEFAULT_H."""
    if arm_in in (None, {}): return None, []
    if not isinstance(arm_in, dict): raise ValueError("the wait must be an object like {\"when\": [...], \"cancel\": [...]}")
    wh, ca = arm_in.get("when") or [], arm_in.get("cancel") or []
    if not isinstance(wh, list) or not isinstance(ca, list): raise ValueError("when / cancel must be lists")
    if not wh: return None, (["the wait has no conditions - ignored (the plan goes in at once)"] if ca else [])
    if len(wh) > 6 or len(ca) > 6: raise ValueError("at most 6 conditions in each list")
    when = [_cond(c, i, "wait step") for i, c in enumerate(wh)]; cancel = [_cond(c, i, "cancel rule") for i, c in enumerate(ca)]
    warn = []
    if not any(c["type"] == "hours" for c in cancel):
        cancel.append({"type": "hours", "h": ARM_DEFAULT_H}); warn.append(f"no time limit on the wait - it is cancelled after {ARM_DEFAULT_H:g} h")
    for c in when + cancel:
        if c["type"] == "after" and C.parse_iso(c["t"]) < C.now_utc(): warn.append(f"'{cond_text(c)}' has already passed - it counts as met at once")
    return {"when": when, "cancel": cancel}, warn


def cond_text(c: dict, dig: int | None = None) -> str:
    f = (lambda p: f"{p:.{dig}f}") if dig is not None else (lambda p: f"{p:g}")
    t = c.get("type")
    if t == "touch": return f"the price trades at or {c['side']} {f(c['px'])}"
    if t == "close": return f"an {c['tf'].upper()} bar closes {c['side']} {f(c['px'])}"
    if t == "after": return f"it is after {c['t'][:16].replace('T', ' ')} UTC"
    if t == "hours": return f"{c['h']:g} h pass"
    return str(c)


def arm_text(arm: dict | None, dig: int | None = None) -> str:
    if not arm: return ""
    w = ", then ".join(cond_text(c, dig) for c in arm["when"])
    k = "; or ".join(cond_text(c, dig) for c in arm.get("cancel") or [])
    return f"waits until {w}" + (f" - cancelled if {k}" if k else "")


def levels_text(lv: dict | None, dig: int | None = None) -> str:
    if not lv: return ""
    f = (lambda p: f"{p:.{dig}f}") if dig is not None else (lambda p: f"{p:g}")
    parts = [f"{LEVEL_NAME[k]} {f(lv[k])}" for k in LEVEL_KEYS if lv.get(k) is not None]
    if lv.get("entry") is not None: parts[0] += f" (a pending order, expires after {lv.get('entry_h', 12):g} h)"
    return "custom levels: " + ", ".join(parts)


ADVISOR_PLAN_SCHEMA = "live-advisor-plan/v1"


def advisor_plan(text: str | None, sig: dict) -> dict | None:
    """the live advisor's own plan (2026-10-14, CLAUDE.live.md "Your plan"): the fenced ```plan JSON from its reply -> {ok, cfg (the builder's
    config: levers + levels + arm), about [plain lines], rationale, warnings, error}. Validated exactly as an approve would be (the live
    lever limits, the levels' sides, the wait); None when the reply has no plan block."""
    if not text: return None
    out = {"ok": False, "cfg": None, "about": [], "rationale": "", "warnings": [], "error": ""}
    try: j = json.loads(text)
    except ValueError as e: out["error"] = f"the plan block is not valid JSON ({str(e)[:80]})"; return out
    if not isinstance(j, dict): out["error"] = "the plan block is not an object"; return out
    out["rationale"] = " ".join(str(j.get("rationale") or "").split())[:600]
    kp = {"schema": BE.SCHEMA, "entry": j.get("entry") or {"pct": 100}, "tp2": j.get("tp2", True), "cap_pct": BE.CAP_DEFAULT,
          "trim_to_cap": bool(j.get("trim_to_cap", False)), "levers": j.get("levers") or []}
    try:
        plan, w1 = live_validate(kp)
        levels, w2 = validate_levels(j.get("levels"), sig)
        arm, w3 = validate_arm(j.get("wait") if j.get("wait") is not None else j.get("arm"))
    except ValueError as e: out["error"] = str(e); return out
    cfg = dict(plan)
    if levels: cfg["levels"] = levels
    if arm: cfg["arm"] = arm
    from live import backtest as BT
    try: out["about"].append(BT.describe(plan))
    except Exception: pass
    dig = None
    if levels: out["about"].append(levels_text(levels, dig))
    if arm: out["about"].append("it " + arm_text(arm, dig))
    out["warnings"] = w1 + w2 + w3
    if levels and not sig.get("plan_levels"): out["warnings"].append("this chart's EA cannot take custom levels yet (it needs the 2026-10-14 EA update) - Load leaves them out and uses the signal's")
    out.update(ok=True, cfg=cfg); return out


# ------------------------------------------------------------------------------------------------------------- records
def plans_dir(cfg: dict) -> str: return os.path.join(cfg["root"], "plans")
def plan_path(cfg: dict, key: str) -> str: return os.path.join(plans_dir(cfg), f"{key}.json")
def load_plan(cfg: dict, key: str) -> dict | None: return C.load_json(plan_path(cfg, key)) if C.safe_key(key) else None
def save_plan(cfg: dict, rec: dict) -> None:
    os.makedirs(plans_dir(cfg), exist_ok=True)
    for k in range(5):                     # Windows: os.replace fails while another process has the file open (the page reading it)
        try: C.atomic_write_json(plan_path(cfg, rec["signal_key"]), rec); return
        except PermissionError:
            if k == 4: raise
            time.sleep(0.2)


def _epoch(s: str | None) -> int | None:
    d = C.parse_iso(s); return int(d.timestamp()) if d else None


def logline(rec: dict, msg: str) -> None:
    rec.setdefault("log", []).append({"t": C.now_iso(), "msg": msg}); rec["log"] = rec["log"][-LOG_MAX:]


def new_record(sig: dict, plan: dict, warn: list[str], inverse: bool, entry_mode: str, levels: dict | None = None, arm: dict | None = None) -> dict:
    """levels (2026-10-14): the custom entry / stop / TP1 / TP2 replace the signal's in the card, so the R frame (rbase), TP1 / TP2 levers
    and the 1R cap all count from them; arm: the record starts ARMED (nothing sent until the wait is met)."""
    b = sig_levels(sig, inverse); lv = levels or {}
    e = float(lv.get("entry", b["entry"])); sl = float(lv.get("sl", b["sl"]))
    tp1 = lv.get("tp1", b["tp1"]); tp2 = lv.get("tp2", b["tp2"])
    eb = _epoch(sig.get("decision_bar_server") or sig.get("decision_bar"))
    return {"schema": "live-plan/v1", "signal_key": sig["signal_key"], "symbol": sig["symbol"], "signal_id": int(sig["signal_id"]),
            "strategy": sig.get("strategy", ""), "signal_direction": sig.get("direction"), "created_at": C.now_iso(), "status": "armed" if arm else "approving",
            "inverse": bool(inverse), "entry_mode": "pending" if lv.get("entry") is not None else entry_mode, "rbase": "fill", "plan": plan,
            "lever_ids": lever_ids(plan, None), "rev": 0, "warnings": warn, "levels": levels, "arm": arm, "arm_state": None,
            "card": {"d": b["d"], "e": e, "R": abs(e - sl), "sl": sl, "tp1": None if tp1 is None else float(tp1), "tp2": None if tp2 is None else float(tp2),
                     "entry_bar_open": eb, "sig_entry": b["entry"], "sig_sl": b["sl"]},
            "full_lots_est": float((sig.get("sizing") or {}).get("lots") or 0), "approve_task": None,
            "legs": [], "banks": [], "exit": None, "pending": {}, "sl_rej": {}, "log": []}


def approve_params(rec: dict) -> dict:
    """the EA's approve params for a record: plan=1 + entry_pct / tp2 / inverse, and (2026-10-14) the custom levels lv_entry / lv_sl /
    lv_tp1 / lv_tp2 (a custom entry = a pending order there, pend_bars its expiry in H4 bars)."""
    plan = rec["plan"]; lv = rec.get("levels") or {}
    p = {"entry_mode": rec["entry_mode"], "size": "full", "plan": 1, "entry_pct": plan["entry"]["pct"], "tp2": 1 if plan["tp2"] else 0}
    if rec.get("inverse"): p["inverse"] = 1
    for k in LEVEL_KEYS:
        if lv.get(k) is not None: p["lv_" + k] = repr(float(lv[k]))
    if lv.get("entry") is not None:
        p["entry_mode"] = "pending"; p["pend_bars"] = max(1, int(math.ceil(float(lv.get("entry_h", 12)) / 4.0 - 1e-9)))
    return p


def send_approve(cfg: dict, rec: dict, why: str) -> str:
    tid = C.write_task(cfg, rec["symbol"], "approve", approve_params(rec), signal_id=rec["signal_id"], issued_by="plan" if why == "armed" else "web")
    rec["approve_task"] = tid; rec["approve_at"] = time.time(); rec["status"] = "approving"
    plan = rec["plan"]; lv = rec.get("levels")
    logline(rec, ("the wait is over - " if why == "armed" else "") + f"approve sent with the plan ({len(plan['levers'])} levers, entry {plan['entry']['pct']:g}%, "
                 f"TP2 {'on' if plan['tp2'] else 'off'}{', INVERSE' if rec.get('inverse') else ''}{'; ' + levels_text(lv) if lv else ''}); waiting for the EA")
    return tid


def approve(cfg: dict, sig: dict, plan_in: dict, inverse: bool, entry_mode: str) -> tuple[bool, str, dict | None]:
    """web app: validate the plan (levers, custom levels, the wait), write the plan record and - unless the plan waits ARMED - the approve
    task (plan=1 + entry_pct / tp2 / inverse / levels)."""
    try:
        kp, lv_in, arm_in = split_plan(plan_in)
        plan, warn = live_validate(kp)
        levels, w2 = validate_levels(lv_in, sig, inverse)
        arm, w3 = validate_arm(arm_in)
    except ValueError as e: return False, f"plan not valid: {e}", None
    if levels and not sig.get("plan_levels"):
        return False, "the EA on this chart cannot take custom levels yet (it needs the 2026-10-14 EA update) - use the signal's levels", None
    old = load_plan(cfg, sig["signal_key"])
    if old and old.get("status") == "approving":                     # its approve already refused (a broker refusal leaves the signal
        a = C.ack(cfg, old.get("approve_task") or "")                # open, 2026-10-12) but the runner has not marked it yet
        if a and a.get("result") != "accepted": old["status"] = "refused"
    if old and old.get("status") == "armed": return False, "this signal already has an armed plan waiting - disarm it first", None
    if old and old.get("status") in ("approving", "running", "waiting_fill"): return False, "this signal already has a running plan", None
    if old:                                                          # an earlier refused attempt: kept beside it
        os.makedirs(os.path.join(plans_dir(cfg), "archive"), exist_ok=True)
        C.atomic_write_json(os.path.join(plans_dir(cfg), "archive", f"{sig['signal_key']}.{int(time.time())}.json"), old)
    rec = new_record(sig, plan, warn + w2 + w3, inverse, entry_mode, levels, arm)
    if arm:
        logline(rec, f"ARMED: nothing is sent until the wait is over - it {arm_text(arm)}" + (f"; {levels_text(levels)}" if levels else ""))
        save_plan(cfg, rec)
        return True, "plan armed - it " + arm_text(arm), rec
    send_approve(cfg, rec, "web")
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
FINAL = ("done", "refused", "stopped", "cancelled")


def cond_met(c: dict, bars: list, since: int, now: int, tk: dict, off_s: int) -> int | None:
    """the server-clock time a wait / cancel condition was met at or after `since` (None: not yet). bars = M1 [[t,o,h,l,c,...]] (bid, server
    clock) from the arming minute, the forming minute last; a bar close (m15 / h1 / h4) counts once its bar has closed (bucketed from M1:
    the last minute's close is the bar's close); `after` is UTC (+ the server offset off_s)."""
    t = c["type"]
    if t == "hours":
        T = since + int(c["h"] * 3600); return T if now >= T else None
    if t == "after":
        d = C.parse_iso(c["t"]); T = int(d.timestamp()) + off_s
        return max(T, since) if now >= T else None
    m0 = since - since % 60
    if t == "touch":
        up = c["side"] == "above"; px = c["px"]
        for b in bars:
            if b[0] >= m0 and (b[2] >= px if up else b[3] <= px): return max(int(b[0]), since)
        bid = float(tk.get("bid") or 0)
        if bid and (bid >= px if up else bid <= px): return now
        return None
    if t == "close":
        P = ARM_TF[c["tf"]]; up = c["side"] == "above"; px = c["px"]; last = {}
        for b in bars: last[int(b[0]) - int(b[0]) % P] = b[4]
        for b0 in sorted(last):
            end = b0 + P
            if end <= since or end > now: continue
            if (last[b0] > px if up else last[b0] < px): return end
        return None
    return None


class Runner:
    """every POLL_S: for each active plan record, read the EA's acks, find the legs on the account, replay the plan on the live M1 path
    and send what the replay shows (one-shot banks / adds / exits once; stops followed). Runs as a daemon thread in the monitor."""

    def __init__(self, cfg: dict, log=print, notify=None):
        self.cfg = cfg; self.log = log; self.notify = notify or (lambda m: None); self.bars = {}; self.h4 = {}; self.specs = {}; self.errs = {}

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
        if rec.get("status") == "armed": return self.armed(rec) or ch
        if not rec.get("legs"):
            a = C.ack(cfg, rec.get("approve_task") or "")
            if not a:
                if time.time() - (rec.get("approve_at") or _epoch(rec["created_at"]) or 0) > 900:
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
                row = C.row_state(cfg, sym, rec["signal_id"]) or {}                # an expiry is marked on the EA's row only (the signal
                if row.get("decision") == "expired":                               # JSON keeps approved_pending) - 2026-10-14 review
                    nb = int(row.get("pend_bars") or 0) or C.PENDING_EXPIRY_BARS
                    rec["status"] = "done"; logline(rec, f"the pending order expired unfilled after {nb} H4 bars - plan closed"); return True
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
            if (rec.get("levels") or {}).get("entry") is not None:         # 2026-10-14: a pending order at a custom entry fills later - the
                ft = int(rec["fill"]["t"]); rec["card"]["entry_bar_open"] = ft - ft % 14400   # lever hours count from the bar it filled in
            if rec["rbase"] == "fill":
                c = rec["card"]; d, e, R = rbase(rec); dig = int((self.spec(sym) or {}).get("digits") or 5)
                logline(rec, f"R counts from your fill {e:.{dig}f} to {'your' if (rec.get('levels') or {}).get('sl') is not None else 'the signal'} stop {float(c['sl']):.{dig}f}: 1R = {R:.{dig}f}"
                             + (f" (the card's entry was {c['e']:.{dig}f} with 1R {c['R']:.{dig}f}: your fill was {d * (e - c['e']) / c['R']:+.2f}R from it)"
                                if abs(e - c["e"]) > 0.05 * c["R"] else ""))
        # a leg missing from the account list is closed only when the EA agrees (it moves a closed position's file to closed/):
        # right after an MT5 restart the terminal lists no positions until the account loads, and that empty list once marked
        # two running plans finished (2026-10-15). While the EA still lists a missing leg as open, nothing is decided this
        # tick; after FEED_MISS_S the old rule applies (an EA that never moved the file - a dead chart).
        unsure = False
        for L in rec["legs"]:
            if L["status"] != "open" or not L.get("posid"): continue
            if int(L["posid"]) in bp:
                if L.pop("miss_since", None) is not None: ch = True
                continue
            pj = C.position(cfg, f"{sym}-{L['posid']}")
            if pj and not pj.get("_closed"):
                if "miss_since" not in L: L["miss_since"] = time.time(); ch = True
                if time.time() - L["miss_since"] < FEED_MISS_S: unsure = True; continue
            L.pop("miss_since", None)
            L["status"] = "closed"; ch = True; logline(rec, f"leg {L['q']} (pos {L['posid']}) is closed on the account")
        if unsure: return ch
        if all(L["status"] in ("closed", "refused", "skipped") for L in rec["legs"]) and not rec.get("pending"):
            rec["status"] = "done"; logline(rec, "every leg is closed - the plan is finished"); return True
        fr = self.frame_now(rec)
        if not fr: return ch
        if self.apply_edit(rec, fr["now_srv"]): ch = True                # the trader's edit (written by the web app), applied here only
        norm_banks(rec)
        res, stt, P, vi = replay(rec, fr["F"], fr["ctx"], fr["t0"])
        acts = self.actions(rec, (res, stt), P, bp, fr["tk"], fr["spec"], vi)
        rec["model"] = {"R": round(float(res[0]), 3), "kind": int(res[1]), "minute": int(res[2]), "at": C.now_iso(), "bars": int(fr["F"]["n"])}
        rec["lever_state"] = lever_state(rec, res, stt, P, vi, fr["F"])
        for x in acts:
            tid = C.write_task(cfg, sym, x["verb"], x["params"], position_id=x["posid"], issued_by="plan")
            rec.setdefault("pending", {})[tid] = {"verb": x["verb"], "posid": x["posid"], "what": x["what"], "at": time.time(), "q": x.get("q"), "sl": x["params"].get("sl")}
            logline(rec, "sent: " + x["what"])
        return True

    # -------------------------------------------------------------------------------------------------- an armed plan (2026-10-14)
    def armed(self, rec: dict) -> bool:
        """an ARMED record: nothing is on the account yet. Each poll: the signal must still be open; a cancel rule ends the wait; the `when`
        steps are met one after the other (each from the minute the previous one was met); when the last is met, the approve goes out. While
        waiting, a delay before the signal's deadline keeps it open (one per H4 bar, only when the next bar close would expire it)."""
        from live import mt5feed
        cfg = self.cfg; sym = rec["symbol"]; key = rec["signal_key"]; arm = rec["arm"]
        s = C.signal(cfg, key) or {}
        if s.get("status") != "open":
            rec["status"] = "cancelled"; logline(rec, f"the signal is no longer open ({s.get('status') or 'gone'}) - the armed plan ends; nothing was sent")
            self.notify(f"{sym} #{rec['signal_id']}: armed plan ended - the signal is {s.get('status') or 'gone'} (nothing was sent)"); return True
        tk = mt5feed.tick(sym)
        if not tk: return False                                            # feed down: nothing is decided blind
        now = int(tk["t"]); ch = False; dig = int((self.spec(sym) or {}).get("digits") or 5)
        st = rec.get("arm_state")
        if not st:
            m0 = now - now % 60
            st = rec["arm_state"] = {"t0": m0, "since": m0, "k": 0, "met": [], "delay_b": None}
            logline(rec, f"watching: it {arm_text(arm, dig)}"); ch = True
        bars = self.m1(key + "#arm", sym, st["t0"], now)
        if bars is None: return ch
        off = 3600 * C.server_offset_h(C.now_utc(), cfg)
        for c in arm.get("cancel") or []:
            t = cond_met(c, bars, st["t0"], now, tk, off)
            if t is not None:
                rec["status"] = "cancelled"; logline(rec, f"cancelled: {cond_text(c, dig)} before the wait was over - nothing was sent; the signal stays open for you")
                self.notify(f"{sym} #{rec['signal_id']}: armed plan CANCELLED ({cond_text(c, dig)}) - nothing was sent; the signal is still open"); return True
        while st["k"] < len(arm["when"]):
            c = arm["when"][st["k"]]; t = cond_met(c, bars, st["since"], now, tk, off)
            if t is None: break
            st["met"].append({"k": st["k"], "t": int(t)}); st["k"] += 1; st["since"] = int(t); ch = True
            logline(rec, f"wait step {st['k']}/{len(arm['when'])} met: {cond_text(c, dig)}")
        if st["k"] >= len(arm["when"]):
            cur = load_plan(cfg, key) or {}
            if cur.get("status") != "armed": return False                 # disarmed from the page meanwhile: the trader's word stands
            rec["card"]["entry_bar_open"] = now - now % 14400              # lever hours count from the H4 bar the trade enters in (as the role says)
            send_approve(cfg, rec, "armed")
            self.notify(f"{sym} #{rec['signal_id']}: the wait is over ({arm_text(arm, dig)[12:]}) - approve sent with the plan"); return True
        streak, mx = int(s.get("implicit_streak") or 0), int(s.get("max_age_bars") or 3)
        b = now - now % 14400
        if streak >= mx - 1 and st.get("delay_b") != b and not any(p_.get("verb") == "delay" for p_ in (rec.get("pending") or {}).values()):
            tid = C.write_task(cfg, sym, "delay", {}, signal_id=rec["signal_id"], issued_by="plan")
            rec.setdefault("pending", {})[tid] = {"verb": "delay", "posid": None, "what": "a one-bar delay (keeps the signal open while the plan waits)", "at": time.time()}
            st["delay_b"] = b; ch = True; logline(rec, "sent: a one-bar delay - the signal stays open while the plan waits")
        return ch

    def frame_now(self, rec: dict) -> dict | None:
        """the live path since the fill in the plan's R: {tk, spec, now_srv, t0, F, ctx} (None: the feed is not ready - decide nothing)."""
        from live import mt5feed
        sym = rec["symbol"]; tk = mt5feed.tick(sym); spec = self.spec(sym)
        if not tk or not spec: return None
        now_srv = int(tk["t"]); t0 = rec["fill"]["t"] - rec["fill"]["t"] % 60
        bars = self.m1(rec["signal_key"], sym, t0, now_srv)
        opens = self.h4_opens(rec["signal_key"], sym, int(rec["card"]["entry_bar_open"]), now_srv)
        if not bars or opens is None: return None
        F, ctx = frame(bars, rec, t0, rec["fill"]["px"], opens)
        if F["n"] < 1: return None
        return {"tk": tk, "spec": spec, "now_srv": now_srv, "t0": t0, "F": F, "ctx": ctx}

    def apply_edit(self, rec: dict, now_srv: int) -> bool:
        """an edit request from the page (plans/edits/<key>.json): the runner is the only writer of a running record, so the web app drops
        the request and this applies it - effective from the current minute (server clock)."""
        p = edit_path(self.cfg, rec["signal_key"])
        if not os.path.exists(p): return False
        req = C.load_json(p) or {}
        try:
            v = build_edit(rec, req.get("plan"), req.get("ids"), req.get("base_rev"), now_srv - now_srv % 60)
            commit_edit(rec, v, req.get("id"))
        except ValueError as e:
            rec["edit_last"] = {"id": req.get("id"), "ok": False, "msg": str(e), "at": C.now_iso()}
            logline(rec, f"plan edit refused: {e}")
        save_plan(self.cfg, rec)           # saved BEFORE the request goes and before anything is sent: a failed save raises, the request
        try: os.remove(p)                  # stays, and the next poll (the record reloaded, base_rev unchanged) applies it again
        except OSError: pass
        return True

    @staticmethod
    def capped(rec: dict, plan: dict | None = None) -> dict:
        """the plan with its 1R cap in the EA's terms: the EA measures 1R from the FIRST FILL to the signal stop (full lots x that distance).
        In the fill frame (rbase 'fill') that IS the replay's 1R (ratio 1, the cap as built); a card-frame record replays in the card's R -
        a fill better than the card entry makes the EA's 1R smaller, so the cap shrinks with it (an add the replay fits under the cap must
        also fit under the EA's, which refuses anything over 1R x 1.05)."""
        cfg = plan if plan is not None else rec["plan"]
        if cfg.get("risk_cap") is None or not rec.get("fill"): return cfg
        ratio = abs(float(rec["fill"]["px"]) - float(rec["card"]["sl"])) / rbase(rec)[2]
        if ratio >= 1.0: return cfg
        return dict(cfg, risk_cap=round(min(float(cfg["risk_cap"]), max(0.01, ratio * 0.99)), 4))

    def actions(self, rec: dict, res, P: dict, bp: dict, tk: dict, spec: dict, vi: int = -1) -> list[dict]:
        (Rv, kind, jx, gross, nbf, naf, cut), S = res
        ls, lst, alive, nl, src, sz0 = S["ls"], S["lst"], S["alive"], S["nl"], S["src"], S["sz0"]
        bk_done, ad_done, mv_done = S["bk_done"], S["ad_done"], S["mv_done"]
        V = versions(rec)[vi]; bank_ids = compile_ids(V["plan"], lever_ids(V["plan"], V.get("ids")))["bank"]
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
            why = ("cut at its time (" + self.cut_text(V["plan"], cut) + ")") if kind == 2 else "everything banked"
            rec["exit"] = {"kind": int(kind), "why": why, "at": C.now_iso()}
            rec["banks"] = sorted(set(rec.get("banks", [])) | {bank_ids[b] for b in range(P["nbk"]) if bk_done[b]})
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
            if not bk_done[b] or bank_ids[b] in rec.setdefault("banks", []): continue
            rec["banks"].append(bank_ids[b]); f = float(P["bk_pct"][b]); lvl = float(P["bk_lvl"][b])
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
    def cut_text(plan: dict, ci: int) -> str:
        cuts = [x for x in plan["levers"] if x["type"].startswith("cut")]
        if not (0 <= ci < len(cuts)): return "a cut"
        x = cuts[ci]
        if x["type"] == "cut_avg": return f"{x['at_h']:g} h, average R below {x['below']:+g}" + (f" over the last {x['window_h']:g} h" if x.get("window_h") else "")
        return f"{x['at_h']:g} h, +{x['reach']:g}R not reached"
