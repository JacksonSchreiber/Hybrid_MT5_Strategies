#!/usr/bin/env python3
"""backtest_presets.py - trader + coach 2026-10-04: the backtest tab's PRESET MENU, computed directly per trade (not grid points),
for the two backtest groups (commodity bloc, USD majors) of data/backtest/split_v1.json. Build only - nothing here is served.

MENU v1 (one module holds the definitions; a menu change = a new MENU_VERSION and a new Test round). Every preset inherits MINIMAL
unless it overrides. Size cap: 200% of ruled size, counted as GROSS units entered (banking does not free room).
  minimal   100% at the signal, the signal's stop, out at TP2. At the "+1R" trigger (+1R, or TP1 when
            nearer, capped at TP2): bank 25% and every stop to entry. +75% when the bid first reaches +2R within the first 24h
            (by the bar-6 close); that add's stop is the entry.
  ruthless  + close everything at the 1-hour mark if the average R so far is negative (the EA's AvgRSince: mean of the M1 bid
            closes of the M1 bars opening in [entry-bar open, +60 min), against the card entry, signed, in R; no M1 bar in that
            hour = no check, as the EA's AVG_NA). Stop to +0.25R (instead of entry) at the +1R trigger.
  slow      25% at the signal; the other 75% goes in at the bar-6 close, or EARLY at the first +1R before bar 6 (a stop-entry at
            +1R) - with the HOLD option on (default): if the average R is below +0.5R when +1R prints, the early add waits and goes
            in at market the first minute the average reaches +0.5R (still before bar 6); bar 6 adds regardless. Stop to +0.25R at
            the +1R trigger. Minimal's +2R add applies on top (75%; gross 25 + 75 + 75 = 175%).
  banker    PROVISIONAL ladder (drafted from Practice, for the trader's sign-off): replaces Minimal's single bank -
            +1R bank 25% stop -> entry, +2R bank 25% stop -> +1R, +3R bank 25% stop -> +2R (each bank = that share of what is open).
            Option (off by default): stops only, no banking. Minimal's +2R add is inherited.
  pyramid   stop to -0.5R when the bid first reaches +0.5R; entry at the +1R trigger (Minimal); at TP1 (only when TP1 > +1R and
            below TP2) add 50%, whose stop is +1R while the original and Minimal's add stay at entry. If Minimal's 75% add has
            already fired the TP1 add is 25% (100 + 75 + 25 = 200%); if the TP1 add fires first, Minimal's add is trimmed to 50%.
Group defaults (data/backtest/group_defaults_v1.json): the default slot = Minimal + APPROVED traits. Commodity bloc: the trader's
proposed trait "cut bad trades quickly" (= the 1-hour average check < 0, i.e. Minimal + the ruthless cut, stop rules unchanged) is
PENDING the coach; until approved the grading comparator is Minimal.
Per minute: stops (per leg) -> bank (+ stop move) -> banker milestones -> pre-bank raise -> adds (early/held, +2R, TP1) -> TP2 ->
the 1-hour check (at its minute's close) -> the bar-6 staged add (at its close). Triggers: banks/milestones/TP2 on the exit-side high
(xh), raises/adds on the bid high (bh), stops on the exit-side low (xl) - the study engine's conventions (pipeline/combo_build).
    python3 pipeline/backtest_presets.py            -> data/backtest/presets_v1.json.gz, group_defaults_v1.json, practice_stats_v1.txt
    python3 pipeline/backtest_presets.py --unit     the synthetic-path unit tests only
"""
from __future__ import annotations
import gzip, json, math, os, statistics as st, sys, time
import numpy as np
from numba import njit
os.environ["CB_HOLDOUT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo_build as CB                       # noqa: E402
from pipeline import combo_pack as CP                        # noqa: E402

MENU_VERSION = "v1"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest")
RUN = os.path.join(CB.OUTDIR, "20261004_1402")
CAP = 2.0
NOCHK = 1 << 40
# banker ladders: (level R, bank share of what is open, stop level R)
LADDERS = {"L1 thirds (PROVISIONAL)": [(1.0, 0.25, 0.0), (2.0, 0.25, 1.0), (3.0, 0.25, 2.0)],
           "L2 front-loaded": [(1.0, 0.5, 0.0), (2.0, 0.25, 1.0)],
           "L3 wide": [(1.0, 0.25, 0.0), (2.5, 0.33, 1.0), (4.0, 0.33, 2.5)]}
BANKER_DEFAULT = "L1 thirds (PROVISIONAL)"


def preset(name):
    """-> kernel parameter dict for a menu entry (every key = Minimal unless overridden)."""
    p = dict(F0=1.0, bankf=0.25, bank_stop=0.0, add2=0.75, add2_lvl=2.0, add2_stop=0.0, avg_on=False, avg_thr=0.0,
             stage=0.0, early_lvl=1.0, hold_thr=-1e9, rtrig=1e9, rto=-1e9, tp1_add=0.0, tp1_trim=0.25, tp1_stop=1.0,
             ladder=[], stops_only=False)
    if name == "minimal": pass
    elif name == "minimal_noadd": p["add2"] = 0.0
    elif name == "ruthless": p.update(avg_on=True, bank_stop=0.25)
    elif name == "slow": p.update(F0=0.25, stage=0.75, hold_thr=0.5, bank_stop=0.25)
    elif name == "slow_nohold": p.update(F0=0.25, stage=0.75, bank_stop=0.25)
    elif name.startswith("banker"):
        lad = name.split(":", 1)[1] if ":" in name else BANKER_DEFAULT
        p.update(bankf=0.0, ladder=LADDERS[lad], stops_only=name.startswith("banker_stops"))
    elif name == "pyramid": p.update(rtrig=0.5, rto=-0.5, tp1_add=0.5)
    elif name == "com_trait": p.update(avg_on=True)           # Minimal + the trader's proposed commodity trait (1-hour check)
    elif name == "raw": p.update(bankf=0.0, bank_stop=-1e9, add2=0.0)
    else: raise KeyError(name)
    return p


MENU = ["minimal", "ruthless", "slow", "banker", "pyramid"]
OPTIONS = ["slow_nohold", "banker_stops"]


@njit(cache=True)
def kern(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, has_tp2, tp2_R, bank_R, F0, bankf, bank_stop, add2, add2_lvl, add2_k,
         add2_stop, avg_k, avg_thr, stage, k6, early_lvl, hold_thr, rtrig, rto, tp1_R, tp1_add, tp1_trim, tp1_stop,
         mlv, mbf, mst, nm, cap):
    """-> (R, kind 0 stopped / 1 TP2 / 2 1-hour cut / 3 window end, exit minute, max gross units, banked, add2 fired, tp1 add fired).
    Legs carry their own stops."""
    ls = np.zeros(6); le = np.zeros(6); lst = np.zeros(6); alive = np.zeros(6, dtype=np.bool_); nl = 1
    ls[0] = F0; le[0] = e0; lst[0] = -1.0; alive[0] = True
    gross = F0; locked = 0.0; tstop = -1.0; cs = 0.0
    banked = False; raised = False; a2 = False; t1 = False; mdone = 0
    st_pend = stage > 0.0; held = False
    for j in range(n):
        cs += bc[j]; avg = cs / (j + 1)
        # per-leg stops
        anyalive = False
        for q in range(nl):
            if alive[q]:
                if xl[j] <= lst[q]:
                    r = lst[q] if lst[q] < xo[j] else xo[j]
                    locked += ls[q] * (r - le[q]); alive[q] = False
                else:
                    anyalive = True
        if not anyalive:
            return locked, 0, j, gross, banked, a2, t1
        # bank at the +1R trigger (+ stop move)
        if (not banked) and xh[j] >= bank_R:
            banked = True
            if bankf > 0.0:
                for q in range(nl):
                    if alive[q]:
                        locked += bankf * ls[q] * (bank_R - le[q]); ls[q] = ls[q] * (1.0 - bankf)
            v = be + bank_stop
            for q in range(nl):
                if alive[q] and v > lst[q]: lst[q] = v
            if v > tstop: tstop = v
        # banker milestones
        while mdone < nm and xh[j] >= mlv[mdone]:
            f = mbf[mdone]
            if f > 0.0:
                for q in range(nl):
                    if alive[q]:
                        locked += f * ls[q] * (mlv[mdone] - le[q]); ls[q] = ls[q] * (1.0 - f)
            v = be + mst[mdone]
            for q in range(nl):
                if alive[q] and v > lst[q]: lst[q] = v
            if v > tstop: tstop = v
            mdone += 1
        # pre-bank raise (pyramid: -0.5R at +0.5R)
        if (not raised) and bh[j] >= rtrig:
            raised = True; v = be + rto
            for q in range(nl):
                if alive[q] and v > lst[q]: lst[q] = v
            if v > tstop: tstop = v
        # adds
        if st_pend and j < k6:
            if (not held) and bh[j] >= early_lvl:
                if avg < hold_thr:
                    held = True
                else:
                    sz = stage if stage < cap - gross else cap - gross
                    if sz > 1e-12:
                        ls[nl] = sz; le[nl] = (early_lvl if early_lvl > bo[j] else bo[j]) + eo[j]; lst[nl] = tstop; alive[nl] = True; nl += 1; gross += sz
                    st_pend = False
            elif held and avg >= hold_thr:
                sz = stage if stage < cap - gross else cap - gross
                if sz > 1e-12:
                    ls[nl] = sz; le[nl] = bc[j] + eo[j]; lst[nl] = tstop; alive[nl] = True; nl += 1; gross += sz
                st_pend = False; held = False
        if add2 > 0.0 and (not a2) and j <= add2_k and bh[j] >= add2_lvl:
            sz = add2 if add2 < cap - gross else cap - gross
            if sz > 1e-12:
                ls[nl] = sz; le[nl] = (add2_lvl if add2_lvl > bo[j] else bo[j]) + eo[j]; lst[nl] = be + add2_stop; alive[nl] = True; nl += 1; gross += sz
            a2 = True
        if tp1_add > 0.0 and (not t1) and bh[j] >= tp1_R:
            sz = tp1_trim if a2 else tp1_add
            if sz > cap - gross: sz = cap - gross
            if sz > 1e-12:
                ls[nl] = sz; le[nl] = (tp1_R if tp1_R > bo[j] else bo[j]) + eo[j]; lst[nl] = be + tp1_stop; alive[nl] = True; nl += 1; gross += sz
            t1 = True
        # TP2
        if has_tp2 and xh[j] >= tp2_R:
            v = locked
            for q in range(nl):
                if alive[q]: v += ls[q] * (tp2_R - le[q])
            return v, 1, j, gross, banked, a2, t1
        # the 1-hour average check
        if j == avg_k and avg < avg_thr:
            v = locked
            for q in range(nl):
                if alive[q]: v += ls[q] * (xc[j] - le[q])
            return v, 2, j, gross, banked, a2, t1
        # the bar-6 staged add
        if st_pend and j == k6:
            sz = stage if stage < cap - gross else cap - gross
            if sz > 1e-12:
                ls[nl] = sz; le[nl] = bc[j] + eo[j]; lst[nl] = tstop; alive[nl] = True; nl += 1; gross += sz
            st_pend = False; held = False
    v = locked
    for q in range(nl):
        if alive[q]: v += ls[q] * (xc[n - 1] - le[q])
    return v, 3, n - 1, gross, banked, a2, t1


def run(F, ctx, name):
    pp = preset(name); lad = pp["ladder"]
    mlv = np.array([l for l, _, _ in lad] + [1e9]); mbf = np.array([(0.0 if pp["stops_only"] else f) for _, f, _ in lad] + [0.0])
    mst = np.array([s for _, _, s in lad] + [0.0])
    tp1 = ctx["tp1"] if (pp["tp1_add"] > 0 and ctx["tp1"] is not None and ctx["tp1"] > 1.0) else 1e9
    return kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], ctx["n"], ctx["e0"], ctx["be"],
                ctx["tp2"] is not None, ctx["tp2"] if ctx["tp2"] is not None else 0.0, ctx["bank_R"], pp["F0"], pp["bankf"],
                pp["bank_stop"], pp["add2"], pp["add2_lvl"], ctx["k6"], pp["add2_stop"], ctx["k1h"] if pp["avg_on"] else NOCHK,
                pp["avg_thr"], pp["stage"], ctx["k6"], pp["early_lvl"], pp["hold_thr"], pp["rtrig"], pp["rto"], tp1,
                pp["tp1_add"], pp["tp1_trim"], pp["tp1_stop"], mlv, mbf, mst, len(lad), CAP)


# ------------------------------------------------------------------------------------------------------------- per trade
def context(S, x, p):
    """the combo-independent per-trade values the kernel needs."""
    h4, i0, k0 = x["bars"], p["i0"], p["k0"]
    t0 = A.tmin(h4[i0][0])
    k1h = int(np.searchsorted(S.t, t0 + 60)) - 1 - k0                 # EA AvgRSince window [entry-bar open, +60 min)
    k6 = p["kbar"][6] if p["kbar"][6] >= 0 else p["n"] - 1
    b99 = p["bank_R"](99.0); tp2 = p["tp2"]
    tp1 = b99 if (tp2 is None or b99 < tp2 - 1e-9) else None
    if b99 >= 98.9: tp1 = None
    return {"n": p["n"], "e0": p["e0"], "be": p["be"], "tp2": tp2, "bank_R": p["bank_R"](1.0), "k6": k6,
            "k1h": k1h if k1h >= 0 else NOCHK, "k1h_gap": int(S.t[k0]) != t0, "tp1": tp1, "up": p["up"]}


def path_stats(F, ctx):
    """raw-path facts for the Practice statistics: +2R timing, TP2 before the stop / before back to entry, MFE after +1R."""
    xl, xh, bh, n = F["xl"], F["xh"], F["bh"], ctx["n"]; tp2 = ctx["tp2"] if ctx["tp2"] is not None else 1e9
    hit_s = np.nonzero(xl <= -1.0)[0]; js = int(hit_s[0]) if hit_s.size else n
    hit_t = np.nonzero(xh >= tp2)[0]; jt = int(hit_t[0]) if hit_t.size else n
    r2 = np.nonzero(bh[:min(js, jt) + 1] >= 2.0)[0]; j2 = int(r2[0]) if r2.size else None
    out = {"tp2_raw": jt < js, "r2": j2 is not None, "r2_24h": j2 is not None and j2 <= ctx["k6"], "tp2_after_r2_be": None, "mfe1": None}
    if j2 is not None:
        be_hit = np.nonzero(xl[j2:] <= ctx["be"])[0]; jb = j2 + int(be_hit[0]) if be_hit.size else n
        out["tp2_after_r2_be"] = jt < jb
    b1 = np.nonzero(xh[:min(js, jt) + 1] >= ctx["bank_R"])[0]
    if b1.size:
        j1 = int(b1[0]); be_hit = np.nonzero(xl[j1:] <= ctx["be"])[0]; jb = j1 + int(be_hit[0]) if be_hit.size else n
        end = min(jb, jt); out["mfe1"] = float(min(tp2, xh[j1:end + 1].max())) if end >= j1 else float(xh[j1])
    return out


SPLIT: dict = {}; POPK: dict = {}
ALL = MENU + OPTIONS + [f"banker:{k}" for k in LADDERS] + [f"banker_stops:{k}" for k in LADDERS] + ["minimal_noadd", "com_trait", "raw"]


def do_symbol(sym):
    from pipeline import cbloc_timeraise_study as TR       # the 1-hour check parity reference (argv set by main)
    S = CB.sym_obj(sym); out = []
    for hid, t in SPLIT["trades"].items():
        if t["key"][0] != sym: continue
        x = POPK[tuple(t["key"])]; p = CB.prep(S, x)
        if p is None: out.append((hid, None)); continue
        F = p["F"]; ctx = context(S, x, p)
        res = {nm: run(F, ctx, nm) for nm in ALL}
        # parity reference: cbloc_timeraise_study's raw + --avgcheck 1 (its clock starts at the first M1 minute)
        h4, i0, k0 = x["bars"], p["i0"], p["k0"]
        akj = max(0, int(np.searchsorted(S.t, max(A.tmin(h4[i0][0]), int(S.t[k0])) + 60)) - 1 - k0)
        rk = np.full(3, -1, dtype=np.int64); rl = np.zeros(3)
        tr = TR.kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], p["be"], p["tp2"] is not None,
                     p["tp2"] if p["tp2"] is not None else 0.0, False, 0.0, 0.0, 1.0, -1, p["up"], False, 1.5, 0.5, False, 1, 1.0, 0.25, 1.5,
                     rk, rl, 1, akj, False, 0.0, -1, False)
        mine = kern(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], ctx["n"], ctx["e0"], ctx["be"],
                    ctx["tp2"] is not None, ctx["tp2"] if ctx["tp2"] is not None else 0.0, 1e9, 1.0, 0.0, -1e9, 0.0, 2.0, ctx["k6"], 0.0,
                    ctx["k1h"], 0.0, 0.0, ctx["k6"], 1.0, -1e9, 1e9, -1e9, 1e9, 0.0, 0.25, 1.0, np.array([1e9]), np.array([0.0]),
                    np.array([0.0]), 0, CAP)
        out.append((hid, {"res": {k: (float(v[0]), int(v[1]), int(v[2]), float(v[3]), bool(v[4]), bool(v[5]), bool(v[6])) for k, v in res.items()},
                          "ps": path_stats(F, ctx), "gap": ctx["k1h_gap"], "chk": (int(tr[1]) == 20, int(tr[3]), int(mine[1]) == 2, int(mine[2]))}))
    return out


# ------------------------------------------------------------------------------------------------------------- unit tests
def synth(path_bh, spread=0.0):
    """a synthetic long frame from a list of per-minute (low, high, close) in R (bid); open = previous close."""
    lo = np.array([a for a, _, _ in path_bh]); hi = np.array([b for _, b, _ in path_bh]); cl = np.array([c for _, _, c in path_bh])
    op = np.concatenate([[0.0], cl[:-1]]); n = len(cl); eo = np.full(n, spread)
    return {"xo": op, "xh": hi, "xl": lo, "xc": cl, "bo": op, "bh": hi, "bc": cl, "eo": eo, "n": n}


def unit_tests():
    ok = True
    def say(c, msg):
        nonlocal ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + msg)
    base = {"e0": 0.0, "be": 0.0, "tp2": 4.0, "bank_R": 1.0, "k1h": NOCHK, "k1h_gap": False, "tp1": 2.0, "up": True}
    # path: up to 2.0 by minute 3 (within 24h: k6 = 10), then down to 0.9 -> TP1 add (stop +1) is stopped, others stay at entry,
    # then up to TP2
    pth = [(-0.2, 0.5, 0.4), (0.3, 1.1, 1.0), (0.9, 1.6, 1.5), (1.4, 2.1, 2.0), (0.9, 2.0, 1.2), (1.1, 2.5, 2.4), (2.3, 4.1, 4.0)]
    F = synth(pth); ctx = dict(base, n=F["n"], k6=10)
    r = run(F, ctx, "pyramid")
    # legs: orig 1.0 @0 banked 25% at 1.0 -> 0.75 left; minimal add 0.75 @2.0 stop 0; TP1 add trimmed to 0.25 @2.0 stop +1
    # minute 4 low 0.9 <= 1.0 -> TP1 add out at min(1.0, open 2.0)=1.0 -> -0.25; TP2 at 4: 0.75*4 + 0.75*2 = 4.5; bank 0.25
    exp = 0.25 * 1.0 + 0.25 * (1.0 - 2.0) + 0.75 * 4.0 + 0.75 * (4.0 - 2.0)
    say(abs(r[0] - exp) < 1e-12 and r[1] == 1 and abs(r[3] - 2.0) < 1e-12 and r[5] and r[6],
        f"pyramid per-leg stops + trim: R {r[0]:+.4f} (expected {exp:+.4f}), gross {r[3]:.2f} (cap 2.00), kind {r[1]}")
    # TP1 add first (TP1 1.5 < 2R): TP1 add 50%, then Minimal's add trimmed to 50% (cap)
    ctx2 = dict(ctx, tp1=1.5); r2 = run(F, ctx2, "pyramid")
    say(abs(r2[3] - 2.0) < 1e-12, f"pyramid TP1-first: gross {r2[3]:.2f} (Minimal's add trimmed to 0.50 by the 200% cap)")
    # minimal: same path -> bank 0.25 at 1, add 0.75 @2 stop 0, TP2: 0.25 + 0.75*4 + 0.75*2
    rm = run(F, ctx, "minimal"); expm = 0.25 + 0.75 * 4.0 + 0.75 * 2.0
    say(abs(rm[0] - expm) < 1e-12 and abs(rm[3] - 1.75) < 1e-12, f"minimal: R {rm[0]:+.4f} (expected {expm:+.4f}), gross {rm[3]:.2f}")
    # +2R after 24h: no add
    ctx3 = dict(ctx, k6=1); rm3 = run(F, ctx3, "minimal")
    say(not rm3[5] and abs(rm3[3] - 1.0) < 1e-12, "minimal: +2R after the bar-6 close -> no add")
    # ruthless: negative first hour average -> cut at its minute close
    neg = [(-0.3, 0.0, -0.2)] * 70 + [(-0.2, 4.2, 4.1)]
    Fn = synth(neg); cn = dict(base, n=Fn["n"], k6=200, k1h=59); rr = run(Fn, cn, "ruthless")
    say(rr[1] == 2 and rr[2] == 59 and abs(rr[0] - (-0.2)) < 1e-12, f"ruthless: cut at minute {rr[2]} at {rr[0]:+.2f}R")
    # slow with hold: +1R prints while the average is below 0.5 -> held, released when the average reaches 0.5
    sl = [(-0.1, 0.0, -0.05)] * 5 + [(0.0, 1.05, 1.0)] + [(0.9, 1.2, 1.1)] * 10 + [(1.0, 4.1, 4.0)]
    Fs = synth(sl); cs_ = dict(base, n=Fs["n"], k6=100, tp1=None); rs = run(Fs, cs_, "slow"); rn = run(Fs, cs_, "slow_nohold")
    say(rs[0] != rn[0] and rs[3] <= 2.0 + 1e-12, f"slow: hold changes the result (hold {rs[0]:+.3f} vs no hold {rn[0]:+.3f}); gross {rs[3]:.2f}")
    return ok


def maxdd(v):
    pk = cur = dd = 0.0
    for r in v: cur += r; pk = max(pk, cur); dd = max(dd, pk - cur)
    return dd


def main():
    global SPLIT, POPK
    if "--unit" in sys.argv: sys.exit(0 if unit_tests() else 1)
    T0 = time.time(); ok_all = unit_tests()
    SPLIT = json.load(open(os.path.join(OUT, "split_v1.json")))
    POPK = {(x["sym"], x["t"], x["strat"]): x for x in CB.population()}
    sys.argv = [sys.argv[0], "--hours", "none", "--raw", "--avgcheck", "1"]
    syms = sorted({t["key"][0] for t in SPLIT["trades"].values()})
    from multiprocessing import Pool
    with Pool(3) as pool: R = {hid: v for r in pool.map(do_symbol, syms) for hid, v in r}
    R = {h: v for h, v in R.items() if v is not None}
    # combo slices: raw + the parity slice (stage off, bank 0.25@1.0). No live-stack numbers (trader 2026-10-04: the comparison is raw)
    cm = json.load(open(os.path.join(RUN, "combos_meta.json"))); nt = cm["n_trades"]; combos = CB.grid()
    def slc(ci):
        with open(os.path.join(RUN, "combos.bin"), "rb") as f:
            f.seek(ci * nt * CP.PK.itemsize); return np.frombuffer(f.read(nt * CP.PK.itemsize), dtype=CP.PK)
    pos = {(s, t, k): i for i, (s, t, k, _) in enumerate(cm["trades"])}
    s_raw = slc(CB.find(combos, "off", 0, "off", "off", "off", "off", "off"))
    s_par = slc(CB.find(combos, "off", 0, "0.25@1.0", "off", "off", "off", "off"))
    def say(c, msg):
        nonlocal ok_all; ok_all &= bool(c); print(("PASS " if c else "FAIL ") + msg, flush=True)
    d_par = d_raw = 0.0; n_chk = mis = gapmis = 0; gmax = 0.0
    for h, v in R.items():
        t = SPLIT["trades"][h]; i = pos[tuple(t["key"])]
        d_par = max(d_par, abs(v["res"]["minimal_noadd"][0] - s_par["R"][i] / 1000))
        d_raw = max(d_raw, abs(v["res"]["raw"][0] - s_raw["R"][i] / 1000))
        a_fire, a_j, m_fire, m_j = v["chk"]
        if v["gap"]: gapmis += int(a_fire != m_fire or (a_fire and a_j != m_j))
        else:
            n_chk += 1; mis += int(a_fire != m_fire or (a_fire and a_j != m_j))
        gmax = max(gmax, max(r[3] for r in v["res"].values()))
        v["raw_combo"] = s_raw["R"][i] / 1000
    say(d_par <= 0.0005 + 1e-9, f"Minimal without its add == combo slice (stage off, bank 0.25@1.0, no pyramid, no raise) on all {len(R)} trades: max |diff| {d_par:.5f}R (file rounding 0.0005)")
    say(d_raw <= 0.0005 + 1e-9, f"raw signal == the combo raw slice: max |diff| {d_raw:.5f}R")
    say(mis == 0, f"the 1-hour check fires on exactly the trades (and minute) where cbloc_timeraise_study --avgcheck 1 does: {n_chk} trades, mismatches {mis}"
        f" (+ {sum(1 for v in R.values() if v['gap'])} trades whose first M1 bar is after the entry-bar open (Sunday opens): the EA's window starts at the bar open (no M1 bar in the hour = no check), the study's at the first M1 bar -> {gapmis} differ by construction)")
    say(gmax <= CAP + 1e-9, f"gross size never above 200% (max {gmax * 100:.0f}%)")
    # outputs
    defaults = {"version": MENU_VERSION, "groups": {
        "com": {"default": "minimal", "traits": [{"name": "cut bad trades quickly", "rule": "close at the 1-hour mark if the average R is negative",
                                                  "preset": "com_trait", "status": "trader-proposed, coach approval pending"}]},
        "maj": {"default": "minimal", "traits": []}}}
    json.dump(defaults, open(os.path.join(OUT, "group_defaults_v1.json"), "w"), indent=1)
    rows = {}
    for h, v in R.items():
        t = SPLIT["trades"][h]; rs = v["res"]
        rows[h] = {"group": t["group"], "part": t["part"], "menu": MENU_VERSION,
                   "R": {k: round(rs[k][0], 4) for k in MENU + OPTIONS + ["com_trait"]},
                   "R_banker_ladders": {k: round(rs[f"banker:{k}"][0], 4) for k in LADDERS},
                   "R_raw": round(rs["raw"][0], 4),
                   "default": "minimal", "R_default": round(rs["minimal"][0], 4),
                   "banked_1R": rs["minimal"][4], "hit_tp2": bool(v["ps"]["tp2_raw"])}
    with gzip.open(os.path.join(OUT, "presets_v1.json.gz"), "wt") as f:
        json.dump({"version": MENU_VERSION, "split_sha": SPLIT["sha256"], "built": time.strftime("%Y-%m-%d %H:%M"), "trades": rows}, f, separators=(",", ":"))
    # Practice-only statistics
    L = [f"backtest preset menu {MENU_VERSION} - Practice-only statistics ({time.strftime('%Y-%m-%d %H:%M')}; split {SPLIT['sha256'][:12]}; model cost - read the"
         " differences vs the default, not the levels)\n"]
    for g, gname in (("com", "Commodity bloc"), ("maj", "USD majors")):
        P = sorted([(SPLIT["trades"][h]["key"][1], h) for h in R if SPLIT["trades"][h]["group"] == g and SPLIT["trades"][h]["part"] == "practice"])
        hs = [h for _, h in P]; L.append(f"===== {gname}: {len(hs)} Practice trades =====")
        w24 = [R[h]["ps"] for h in hs if R[h]["ps"]["r2_24h"]]; late = [R[h]["ps"] for h in hs if R[h]["ps"]["r2"] and not R[h]["ps"]["r2_24h"]]
        f = lambda xs, k: (sum(1 for x in xs if x[k]) / len(xs) if xs else float("nan"))
        L.append(f"  [observed] Minimal's +2R add: reached +2R within 24h n {len(w24)}: TP2 before the stop {f(w24, 'tp2_raw'):.0%}, TP2 before back to entry"
                 f" {f(w24, 'tp2_after_r2_be'):.0%} | reached +2R later n {len(late)}: {f(late, 'tp2_raw'):.0%} / {f(late, 'tp2_after_r2_be'):.0%}")
        m1 = sorted(R[h]["ps"]["mfe1"] for h in hs if R[h]["ps"]["mfe1"] is not None)
        if m1:
            q = lambda fr: m1[min(len(m1) - 1, int(fr * len(m1)))]
            L.append(f"  [observed] best R after the +1R trigger before back to entry (n {len(m1)} trades that reached +1R): quartiles {q(0.25):+.2f} / "
                     f"{q(0.5):+.2f} / {q(0.75):+.2f}R; reached +2R {sum(1 for v in m1 if v >= 2) / len(m1):.0%}, +3R {sum(1 for v in m1 if v >= 3) / len(m1):.0%}, +4R {sum(1 for v in m1 if v >= 3.999) / len(m1):.0%}")
        base = [R[h]["res"]["minimal"][0] for h in hs]
        L.append(f"  {'preset':34} {'R/trade':>8} {'vs Minimal':>10} {'t':>6} {'DD':>6} {'long':>7} {'short':>7}")
        def line(lab, vals, tag="tested (Practice, in-sample)"):
            d = np.array(vals) - np.array(base); t_ = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if d.std() > 0 else 0.0
            lo = [v for v, h in zip(vals, hs) if SPLIT["trades"][h]["up"]]; sh = [v for v, h in zip(vals, hs) if not SPLIT["trades"][h]["up"]]
            L.append(f"  {lab:34} {np.mean(vals):+8.4f} {d.mean():+10.4f} {t_:+6.2f} {maxdd(vals):6.1f} {np.mean(lo):+7.4f} {np.mean(sh):+7.4f}  [{tag}]")
        for k in MENU + OPTIONS + ["com_trait"]:
            line(k + (" (= default)" if k == "minimal" else ""), [R[h]["res"][k][0] for h in hs])
        for k in LADDERS:
            line(f"banker {k}", [R[h]["res"][f"banker:{k}"][0] for h in hs]); line(f"banker stops-only {k}", [R[h]["res"][f"banker_stops:{k}"][0] for h in hs])
        line("raw signal", [R[h]["res"]["raw"][0] for h in hs])
        L.append("")
    L.append("Tags: 'tested' = the preset replayed on the Practice partition (in-sample - these numbers chose nothing yet but will be looked at);"
             " 'observed' = a descriptive distribution.\n")
    open(os.path.join(OUT, "practice_stats_v1.txt"), "w").write("\n".join(L) + "\n"); print("\n".join(L))
    print(("ALL PASS" if ok_all else "SOME CHECKS FAILED") + f"  ({time.time() - T0:.0f}s)")
    return ok_all


if __name__ == "__main__": sys.exit(0 if main() else 1)
