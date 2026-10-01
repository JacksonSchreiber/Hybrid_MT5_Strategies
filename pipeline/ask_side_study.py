#!/usr/bin/env python3
"""ask_side_study.py - coach item 23 (2026-10-01): the ask-side correction.

Every EA-vs-replay reproduction gap so far was negative. Diagnosis put to the test here: the replays run on BID-only bars,
so a SHORT is never stopped / banked / filled on the ask. This script builds an ask series (ask = bid + spread) and
re-states the base TrendCont doctrine and every live rule's increment, bid-only vs ask-corrected, on M1, all 7 symbols,
development 2012-2024 and the 2025+ holdout separately, longs and shorts separately.

SIDE RULES (ask-corrected modes)
  every fill on its true side: BUY orders (long entries, long adds, short exits) execute on the ASK, SELL orders (short
  entries, short adds, long exits) on the BID.
  LONG  : stop / bank / TP trigger when the BID reaches them and fill there (what the bid bars already did); the market
          entry at the signal and the staged / pyramid adds fill at ASK = bid + spread.
  SHORT : stop triggers when ASK high >= stop, bank / TP when ASK low <= level, fills at the level (a bar opening through
          the stop fills at the ASK open); the end-of-data mark is the ASK close; entries and adds fill at the BID.
  adds  : the +1.5R pyramid (and early promotion) trigger on the BID, fill on their own side (caller's rule).
  stop to entry: the whole position's stop goes to the FIRST tranche's actual fill (the EA's BEPrice uses
          POSITION_PRICE_OPEN of the first tranche; its 1-pip pad is not modelled) - for a long that fill is the ask.
  bank / TP1 / TP2 / +1.5R levels stay at the signal's prices (the EA places them as absolute prices).
  the Inverse: a short parent is stopped by the ASK and its Inverse BUY-stop fills on the ASK at that moment (the ask
          open if gapped); a long parent is stopped by the BID and its Inverse SELL-stop fills on the BID.
COSTS - one spread per round trip per unit, never two
  BID     (as before): bid-only triggers, the per-symbol DRAG (exit_mgmt_study.DRAG, spread_drag.json drag_scaled) per
          unit traded (the staged add and the pyramid add pay it too; Inverse: DRAG x (1 + 0.5 if pyramided)).
  ABS / SCALED (ask-corrected): the spread is IN the prices (long pays it on entry, short on the exit side), so the DRAG
          is NOT charged. Deviation from the suggested "keep DRAG as the entry cost": (1) a short entered at the bid pays
          its spread through the ask-side exits, so DRAG + ask exits charges shorts twice (in expectation, by optional
          stopping, an ask-side exit costs exactly one spread); (2) DRAG on some legs and an hourly spread on others would
          contaminate the staged and pyramid increments, which trade different numbers of units.
  ABS_C / SCALED_C (control, "cost only"): bid triggers as BID, but every entry fill pays the same explicit spread as
          the ask mode, no DRAG. ASK minus its control = the side effect (which bars trigger, plus a short paying the
          spread at its exit hour instead of its entry hour); control minus BID = the change of cost measure (hourly
          2026 spread vs DRAG).
SPREAD = data/study/spread_profile.csv, the median spread per symbol and UTC hour from the live OANDA feed, Sept 2026.
  ABS    : that 2026 per-hour median in price units applied to ALL years (as specified). An approximation: older years had
           different spreads, and indices / gold traded at a fraction of the 2026 price (US100's 1.8 points is ~0.1R on a
           2012 stop), so ABS overstates the cost in R the further back a trade is. No historical ask ticks are on disk.
  SCALED : the same hourly median as a fraction of the Sept-2026 price (data/study/spreads.json; USDJPY from its M1 tail),
           re-priced at each trade's entry - the same scaling as the DRAG's drag_scaled column.
  The profile is built from MT5 M1 bar spreads (likely the bar's minimum spread): EURUSD reads 0 in 23 of 24 hours and
  GBPUSD / USDJPY ~0 outside the rollover; every symbol sits below the spot sample in spreads.json (US100 1.8 vs 2.3,
  XAUUSD 0.25 vs 0.38, USOIL 0.03 vs 0.071). So the correction is a LOWER bound on the side effect, ~nil on EURUSD by
  construction. The blank rollover hour (21 UTC) of indices / USOIL / XAUUSD takes the wider neighbour hour.
  Clock caveats: the profile's UTC hours are September (EEST) hours, applied to winter dates unshifted (the rollover spike
  sits an hour early in winter); the .dk H4 grid closes at 00/04/../20 UTC while the live broker grid closes at
  01/05/../21 UTC, so a live entry at the 21:00 UTC close (broker midnight, the rollover spread) is NOT modelled here -
  in this replay entries never fall in the rollover hour; only open positions' stops and targets meet it.
BAR RULES as pyramid_full_study / inverse_study: stop checked first each bar at the previous bars' level (a bar opening
  through it fills at the open); bank (+1R or TP1 if nearer, clamped to TP2), then TP2; the staged add at the close of the
  last M1 bar of H4 bar 6; per-trade price offset between the .dk H4 prices and the M1 bid series from the signal-bar
  close. Resolution: M1 (data/qdm_csv/fleet, UTC, bid) for every replay; 600 H4 bars (144,000 M1 bars) cap.
  Engine: event skipping (vectorised search for the next bar where anything can happen, then the exact per-bar logic);
  verified identical to a naive per-bar loop at start-up (--verify).
REPRODUCTION SANITY (section D): TICK = a 1-tick spread (what the tester's .dk ticks carry: a staged BUY add fills at the
  next bar's open + 1 tick, a SELL at the open); DK = TICK plus the M1 series re-aligned to the .dk price space bar by
  bar (the .dk-vs-M1 basis interpolated across each H4 bar from its open and close) instead of one offset per trade.
    python3 pipeline/ask_side_study.py [--verify] [--syms EURUSD.dk,US100.dk]
"""
from __future__ import annotations
import argparse, bisect, csv, json, math, os, statistics as st, sys, time
from collections import defaultdict
import numpy as np, pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS, DRAG   # noqa: E402
from pipeline.exit_s025_study import aa_rows                                      # noqa: E402
from pipeline.trendcont_step13_study import load                                  # noqa: E402
from pipeline.inverse_study import SRC, BANKF, tmin                               # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STUDY = os.path.join(ROOT, "data", "study")
REPORT = os.path.join(STUDY, "ask_side_report.txt")
HOLD = "2025"; MAXM1 = MAX_BARS * 240; W0, W1 = 7, 18
F0, N, PYR_R, PYR_F, EARLY = 0.25, 6, 1.5, 0.5, 0.5
TICK = {"EURUSD.dk": 1e-5, "US100.dk": 0.1, "GBPUSD.dk": 1e-5, "USDJPY.dk": 1e-3, "US500.dk": 0.1, "USOIL.dk": 0.001, "XAUUSD.dk": 0.01}
MODES = {   # side: ask-side triggers/fills; cost_only: bid triggers + explicit spread on entries; spread kind; adj: .dk basis
    "BID":      dict(side=False, cost_only=False, spread=None,     adj=False, drag=True),
    "ABS":      dict(side=True,  cost_only=False, spread="abs",    adj=False, drag=False),
    "SCALED":   dict(side=True,  cost_only=False, spread="scaled", adj=False, drag=False),
    "ABS_C":    dict(side=False, cost_only=True,  spread="abs",    adj=False, drag=False),
    "SCALED_C": dict(side=False, cost_only=True,  spread="scaled", adj=False, drag=False),
    "TICK":     dict(side=True,  cost_only=False, spread="tick",   adj=False, drag=False),
    "DK":       dict(side=True,  cost_only=False, spread="tick",   adj=True,  drag=False),
}
MAIN = ("BID", "ABS", "SCALED", "ABS_C", "SCALED_C")
REPRO = ("BID", "TICK", "DK", "ABS", "SCALED")


# ----------------------------------------------------------------------------------------------------------- spreads
def spread_profile():
    prof = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(STUDY, "spread_profile.csv"))):
        v = r["spread_med_hour"]
        prof[r["symbol"]][int(r["hour_utc"])] = float(v) if v not in ("", None) else None
    out = {}
    for s, d in prof.items():
        a = np.zeros(24)
        for h in range(24):
            v = d.get(h)
            if v is None:
                nb = [d.get((h - 1) % 24), d.get((h + 1) % 24)]
                v = max([x for x in nb if x is not None] or [0.0])
            a[h] = v
        out[s] = a
    return out


def ref_prices():
    j = json.load(open(os.path.join(STUDY, "spreads.json")))
    return {k: v["price"] for k, v in j.items()}


# ----------------------------------------------------------------------------------------------------------- data
class Sym:
    def __init__(self, sym, prof, refs, need_dk=False):
        self.sym = sym; root = sym.split(".")[0]
        p = os.path.join(ROOT, "data", "qdm_csv", "fleet", f"{SRC[sym]}-M1-No Session.csv")
        d = pd.read_csv(p, dtype={"Date": str, "Time": str})
        self.t = pd.to_datetime(d["Date"] + d["Time"], format="%Y%m%d%H:%M:%S").values.astype("datetime64[m]").astype(np.int64)
        self.o, self.h, self.l, self.c = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        del d
        self.sp = prof[root + ".sim"][(self.t // 60) % 24]
        self.ref = refs.get(root + ".sim") or float(np.median(self.c[-20000:]))
        self.tick = TICK[sym]
        self.h4 = load(sym, "h4"); self.h4idx = {b[0][:16]: k for k, b in enumerate(self.h4)}
        self.h4t = np.array([tmin(b[0]) for b in self.h4], dtype=np.int64)
        self.adj = self.dk_basis() if need_dk else None

    def dk_basis(self):
        """per M1 bar: the .dk-minus-M1 price basis, interpolated across each H4 bar from its open and close basis."""
        hk = np.searchsorted(self.h4t, self.t, side="right") - 1
        st_ = np.searchsorted(self.t, self.h4t); en = np.searchsorted(self.t, self.h4t + 240) - 1
        H4o = np.array([b[1] for b in self.h4]); H4c = np.array([b[4] for b in self.h4])
        ok = (en >= st_) & (st_ < len(self.t))
        bo = np.full(len(self.h4), np.nan); bc = np.full(len(self.h4), np.nan)
        bo[ok] = H4o[ok] - self.o[st_[ok]]; bc[ok] = H4c[ok] - self.c[np.clip(en[ok], 0, len(self.t) - 1)]
        bo = pd.Series(bo).ffill().fillna(0).to_numpy(); bc = pd.Series(bc).ffill().fillna(0).to_numpy()
        hk = np.clip(hk, 0, len(self.h4) - 1)
        fr = np.clip((self.t - self.h4t[hk]) / 240.0, 0, 1)
        return bo[hk] * (1 - fr) + bc[hk] * fr


# ----------------------------------------------------------------------------------------------------------- frame + engine
def frame(S, k0, end, up, A, R, mode, scale):
    m = MODES[mode]
    O, H, L, C = S.o[k0:end], S.h[k0:end], S.l[k0:end], S.c[k0:end]
    if m["adj"]:
        a = S.adj[k0:end]; O, H, L, C = O + a, H + a, L + a, C + a
    n = end - k0
    if m["spread"] is None: spR = np.zeros(n)
    elif m["spread"] == "abs": spR = S.sp[k0:end] / R
    elif m["spread"] == "scaled": spR = S.sp[k0:end] * scale / R
    else: spR = np.full(n, S.tick / R)
    if up: bo, bh, bl, bc = (O - A) / R, (H - A) / R, (L - A) / R, (C - A) / R
    else:  bo, bh, bl, bc = (A - O) / R, (A - L) / R, (A - H) / R, (A - C) / R
    if m["side"] and not up: xo, xh, xl, xc = bo - spR, bh - spR, bl - spR, bc - spR
    else: xo, xh, xl, xc = bo, bh, bl, bc
    eo = spR if (up or m["cost_only"]) else np.zeros(n)
    return {"xo": xo, "xh": xh, "xl": xl, "xc": xc, "bo": bo, "bh": bh, "bc": bc, "eo": eo, "spR": spR, "n": n,
            "O": O, "sp": spR * R}


def nxt(F, j, stop, bank_R, tp2_R, pyr_on, early_end, add_k):
    """first bar >= j where anything can happen (stop / bank / TP2 / pyramid trigger / early add / the staged add)."""
    n = F["n"]; xl, xh, bh = F["xl"], F["xh"], F["bh"]; step = 512
    while j < n:
        b = min(n, j + step)
        m = xl[j:b] <= stop
        if bank_R is not None: m |= xh[j:b] >= bank_R
        if tp2_R is not None: m |= xh[j:b] >= tp2_R
        if pyr_on: m |= bh[j:b] >= PYR_R
        if early_end is not None and j < early_end:
            e = min(b, early_end); m[:e - j] |= bh[j:e] >= EARLY
        jj = j + int(m.argmax()) if m.any() else None
        if add_k is not None and j <= add_k < b and (jj is None or add_k < jj): return add_k
        if jj is not None: return jj
        j = b; step *= 4
    return n


def run(F, e0, be, bank_R, tp2_R, bankf, F0_=1.0, k_add=None, pyr=False, early_end=None, naive=False):
    """-> (R raw, units traded, pyramided?). Legs [size, entry R]; R of the full ruled position at the original stop."""
    xo, xh, xl, xc, bo, bh, bc, eo, n = (F[k] for k in ("xo", "xh", "xl", "xc", "bo", "bh", "bc", "eo", "n"))
    legs = [[F0_, e0]]; locked = 0.0; stop = -1.0; banked = False; units = F0_
    add_pend = F0_ < 1.0 and k_add is not None and k_add < n
    early_pend = add_pend and early_end is not None
    pyr_pend = pyr; padd = False
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    j = 0
    while j < n:
        if not naive:
            j = nxt(F, j, stop, None if banked else bank_R, tp2_R, banked and pyr_pend,
                    early_end if early_pend else None, k_add if add_pend else None)
            if j >= n: break
        if xl[j] <= stop: return val(min(stop, xo[j])), units, padd
        if early_pend and j < early_end and bh[j] >= EARLY:
            legs.append([1 - F0_, max(EARLY, bo[j]) + eo[j]]); units += 1 - F0_; add_pend = early_pend = False
        if not banked and xh[j] >= bank_R:
            banked = True; locked += bankf * sum(s * (bank_R - e) for s, e in legs)
            legs = [[s * (1 - bankf), e] for s, e in legs]; stop = be
            if pyr_pend and bh[j] >= PYR_R:
                legs.append([PYR_F, max(PYR_R, bo[j]) + eo[j]]); units += PYR_F; pyr_pend = False; padd = True
            if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, padd
            if add_pend and j == k_add:
                legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = early_pend = False
            j += 1; continue
        if banked and pyr_pend and bh[j] >= PYR_R:
            legs.append([PYR_F, max(PYR_R, bo[j]) + eo[j]]); units += PYR_F; pyr_pend = False; padd = True
        if tp2_R is not None and xh[j] >= tp2_R: return val(tp2_R), units, padd
        if add_pend and j == k_add:
            legs.append([1 - F0_, bc[j] + eo[j]]); units += 1 - F0_; add_pend = early_pend = False
        j += 1
    return val(xc[n - 1]), units, padd


def parent_stop(F, bank_R, tp2_R, naive=False):
    """plain parent: index of the bar that stops it before any bank / TP2, else None."""
    if naive:
        for j in range(F["n"]):
            if F["xl"][j] <= -1.0: return j
            if F["xh"][j] >= bank_R or (tp2_R is not None and F["xh"][j] >= tp2_R): return None
        return None
    j = nxt(F, 0, -1.0, bank_R, tp2_R, False, None, None)
    return j if j < F["n"] and F["xl"][j] <= -1.0 else None


# ----------------------------------------------------------------------------------------------------------- trades
def setup(S, x, mode):
    """levels in the M1 (bid) price space of this mode. -> dict or None (no M1 coverage)."""
    h4, i0 = x["bars"], x["i0"]
    if i0 >= len(h4): return None
    t0 = tmin(h4[i0][0]); k0 = int(np.searchsorted(S.t, t0))
    if k0 <= 0 or k0 >= len(S.t) or S.t[k0] - t0 > 240: return None
    cl = S.c[k0 - 1] + (S.adj[k0 - 1] if MODES[mode]["adj"] else 0.0)
    off = h4[i0 - 1][4] - cl
    e, s = x["e"] - off, x["s"] - off; R = abs(e - s)
    if R <= 0: return None
    up = x["up"]; sg = 1.0 if up else -1.0; toR = lambda p: (p - e) * sg / R
    tp1 = (x["tp1"] - off) if x["tp1"] else None; tp2 = (x["tp2"] - off) if x["tp2"] else None
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tp2_R = toR(tp2) if tp2 else None
    if tp2_R is not None and bank_R >= tp2_R - 1e-9: bank_R = tp2_R
    end = min(len(S.t), k0 + MAXM1)
    k_add = (int(np.searchsorted(S.t, tmin(h4[i0 + N][0]))) - 1 - k0) if i0 + N < len(h4) else None
    k_early = (int(np.searchsorted(S.t, tmin(h4[i0 + N - 1][0]))) - k0) if i0 + N - 1 < len(h4) else None
    return {"k0": k0, "end": end, "e": e, "s": s, "R": R, "up": up, "bank_R": bank_R, "tp2_R": tp2_R,
            "k_add": k_add, "k_early": k_early, "scale": e / S.ref, "i0": i0}


def regular(S, x, mode, scen, naive=False):
    """scen: list of (name, F0, bankf, pyr, early). -> {name: (costed R, raw R, units)} or None."""
    u = setup(S, x, mode)
    if not u: return None
    m = MODES[mode]
    F = frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0
    out = {}
    for name, f0, bankf, pyr, early in scen:
        r, units, _ = run(F, e0, be, u["bank_R"], u["tp2_R"], bankf, f0, u["k_add"] if f0 < 1 else None, pyr,
                          u["k_early"] if early else None, naive)
        out[name] = (r - (x["cost"] * units if m["drag"] else 0.0), r, units)
    return out


def inverse(S, x, mode, naive=False):
    """-> None (no inverse) or {bar, up_inv, plain, pyr (costed), raw_plain}."""
    u = setup(S, x, mode)
    if not u: return None
    m = MODES[mode]
    F = frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], mode, u["scale"])
    j = parent_stop(F, u["bank_R"], u["tp2_R"], naive)
    if j is None: return None
    k = u["k0"] + j
    bar = int(np.searchsorted(S.h4t, S.t[k], side="right")) - u["i0"]
    if bar < W0 or k + 1 >= len(S.t): return None
    gap = F["xo"][j] < -1.0
    if not gap: A = u["s"]
    else:
        A = F["O"][j] + (F["sp"][j] if (m["side"] and not u["up"]) else 0.0)        # the side's open
    end = min(len(S.t), k + 1 + MAXM1)
    G = frame(S, k + 1, end, not u["up"], A, u["R"], mode, u["scale"])
    e0 = float(F["spR"][j]) if m["cost_only"] else 0.0
    bankf = BANKF.get(x["strat"], 0.5); res = {"bar": bar, "up_inv": not u["up"]}
    for pyr in (False, True):
        r, units, added = run(G, e0, 0.0, u["bank_R"], u["tp2_R"], bankf, 1.0, None, pyr, None, naive)
        res["pyr" if pyr else "plain"] = r - (x["cost"] * (1.0 + (PYR_F if added else 0.0)) if m["drag"] else 0.0)
        if not pyr: res["raw_plain"] = r
    return res


SCEN = [("F25", 1.0, 0.25, False, False), ("F50", 1.0, 0.50, False, False),
        ("S25", F0, 0.25, False, False), ("S25P", F0, 0.25, True, False)]


# ----------------------------------------------------------------------------------------------------------- stats
def tstat(v):
    if len(v) < 2: return float("nan")
    sd = st.pstdev(v); return st.mean(v) / (sd / math.sqrt(len(v))) if sd > 0 else 0.0


def share(b, a):
    return "n/a" if b <= 0.005 else f"{a / b * 100:4.0f}%"


class Out:
    def __init__(self): self.lines = []
    def __call__(self, s=""): print(s, flush=True); self.lines.append(s)


# ----------------------------------------------------------------------------------------------------------- reproduction
def _exec_defs(fname, cut):
    src = open(os.path.join(ROOT, "pipeline", fname)).read().split(cut)[0]
    ns = {"__file__": os.path.join(ROOT, "pipeline", fname)}; exec(compile(src, fname, "exec"), ns); return ns


def ea_levels(S, r):
    i = S.h4idx.get(r["signal_time"][:16])
    if i is None: return None
    tp2 = float(r["tp2"]) if r["tp2"] else (float(r["tp"]) if r["tp"] else None)
    return {"bars": S.h4, "i0": i + 1, "up": r["direction"].upper().startswith("B"), "e": float(r["entry"]),
            "s": float(r["sl"]), "tp1": float(r["tp1"]) if r["tp1"] else None, "tp2": tp2, "strat": r["strategy"],
            "cost": 0.0, "t": r["signal_time"], "sym": S.sym}


def repro(S, P):
    """EA real-tick reproductions for S.sym -> list of (check name, n, EA gain, {mode: replay gain}) + inverse detail."""
    out, detail = [], []
    sym = S.sym
    # (1) Inverse: matched fills (as inverse_repro_check) and per-parent totals
    p = os.path.join(STUDY, "inverse_repro", sym + ".csv")
    if os.path.exists(p):
        rows = list(csv.DictReader(open(p)))
        parents = {r["signal_id"]: r for r in rows if r["strategy"] != "Inverse" and r["decision"] in ("approved", "approved_pending")}
        fills = {r["parent_signal_id"]: float(r["r_multiple"]) for r in rows
                 if r["strategy"] == "Inverse" and r.get("inv_state") == "2" and r["r_multiple"] != ""}
        rep = {md: {} for md in REPRO}
        for sid, par in parents.items():
            x = ea_levels(S, par)
            if not x: continue
            for md in REPRO:
                q = inverse(S, x, md)
                rep[md][sid] = q["raw_plain"] if (q and W0 <= q["bar"] <= W1) else None
        for md in REPRO:
            ea_m = [fills[s] for s in fills if rep[md].get(s) is not None]
            rp_m = [rep[md][s] for s in fills if rep[md].get(s) is not None]
            tot_ea = [fills.get(s, 0.0) for s in rep[md]]; tot_rp = [rep[md][s] or 0.0 for s in rep[md]]
            P.setdefault(("inv", sym), {})[md] = (len(ea_m), st.mean(ea_m) if ea_m else float("nan"),
                                                 st.mean(rp_m) if rp_m else float("nan"), len(fills) - len(ea_m),
                                                 sum(1 for s in rep[md] if rep[md][s] is not None and s not in fills),
                                                 len(tot_ea), st.mean(tot_ea), st.mean(tot_rp))
        for s in fills:
            par = parents.get(s)
            if par: detail.append((sym, par["signal_time"], par["strategy"], par["direction"], fills[s],
                                   {md: rep[md].get(s) for md in REPRO}))
    # (2) bank 25% vs 50% (TrendCont rows, full entry)
    a_, b_ = os.path.join(STUDY, "bank_repro", sym + "_0.5.csv"), os.path.join(STUDY, "bank_repro", sym + "_0.25.csv")
    if os.path.exists(a_) and os.path.exists(b_):
        ld = lambda f: {r["signal_time"]: r for r in csv.DictReader(open(f))
                        if r["strategy"] == "TrendCont" and r["decision"] == "approved" and r["r_multiple"] != ""}
        A, B = ld(a_), ld(b_); ea, g = [], {md: [] for md in REPRO}
        for t, r in A.items():
            x = t in B and ea_levels(S, r)
            if not x: continue
            res = {md: regular(S, x, md, [SCEN[0], SCEN[1]]) for md in REPRO}
            if any(v is None for v in res.values()): continue
            ea.append(float(B[t]["r_multiple"]) - float(r["r_multiple"]))
            for md in REPRO: g[md].append(res[md]["F25"][1] - res[md]["F50"][1])
        out.append(("bank 25% vs 50% (TrendCont)", len(ea), st.mean(ea), {md: st.mean(v) for md, v in g.items()}))
    # (3) pyramid (all detectors, full entry, bank 25%/50%) - pyramid_repro_check's EA side
    a_, b_ = os.path.join(STUDY, "pyr_repro_m4", sym + "_nopyr.csv"), os.path.join(STUDY, "pyr_repro_m4", sym + "_pyr.csv")
    if os.path.exists(a_) and os.path.exists(b_):
        eas = _exec_defs("pyramid_repro_check.py", "\nD = sys.argv[1]")["ea_signals"]
        A, B = eas(a_), eas(b_); ea, g = [], {md: [] for md in REPRO}
        for k, (r, R0, _) in A.items():
            x = k in B and ea_levels(S, r)
            if not x: continue
            bk = 0.25 if r["strategy"] == "TrendCont" else 0.5
            sc = [("np", 1.0, bk, False, False), ("p", 1.0, bk, True, False)]
            res = {md: regular(S, x, md, sc) for md in REPRO}
            if any(v is None for v in res.values()): continue
            ea.append(B[k][1] - R0)
            for md in REPRO: g[md].append(res[md]["p"][1] - res[md]["np"][1])
        out.append(("pyramid +1.5R (all detectors)", len(ea), st.mean(ea), {md: st.mean(v) for md, v in g.items()}))
    # (4) early promotion on staged entry (promote_repro)
    a_, b_ = os.path.join(STUDY, "promote_repro", sym + "_plain.csv"), os.path.join(STUDY, "promote_repro", sym + "_early.csv")
    if os.path.exists(a_) and os.path.exists(b_):
        eas = _exec_defs("promote_repro_check.py", "\nD = sys.argv[1]")["ea_signals"]
        A, B = eas(a_), eas(b_); ea, g = [], {md: [] for md in REPRO}
        for k, (r, R0, _, staged) in A.items():
            x = k in B and staged and ea_levels(S, r)
            if not x: continue
            bk = 0.25 if r["strategy"] == "TrendCont" else 0.5
            sc = [("pl", F0, bk, False, False), ("ea", F0, bk, False, True)]
            res = {md: regular(S, x, md, sc) for md in REPRO}
            if any(v is None for v in res.values()): continue
            ea.append(B[k][1] - R0)
            for md in REPRO: g[md].append(res[md]["ea"][1] - res[md]["pl"][1])
        out.append(("early promotion at +0.5R (staged)", len(ea), st.mean(ea), {md: st.mean(v) for md, v in g.items()}))
    return out, detail


def basis_table(S):
    d = defaultdict(list); dy = defaultdict(list)
    for k in range(len(S.h4) - 1):
        tb = S.h4t[k + 1]; kb = int(np.searchsorted(S.t, tb))
        if kb <= 0 or kb >= len(S.t) or S.t[kb] != tb: continue
        off = S.h4[k][4] - S.c[kb - 1]; d[int((tb // 60) % 24)].append(off); dy[S.h4[k][0][:4]].append(off)
    return ({h: float(np.median(v)) for h, v in sorted(d.items())}, {y: float(np.median(v)) for y, v in sorted(dy.items())})


def synthetic_check(paths=3000, n=30000, sp=2.0, R=20.0, seed=5):
    """engine accounting on a driftless random walk, constant spread 0.1R, plain trade (stop -1R / TP +2R, no bank):
    the ask side must cost exactly one spread per round trip in expectation on both sides. -> {(mode, side): (mean, se)}"""
    rng = np.random.default_rng(seed)
    class _S: pass
    res = defaultdict(list)
    for k in range(paths):
        w = rng.normal(0, 1, n).cumsum() + 1000.0
        S = _S(); S.o = np.r_[1000.0, w[:-1]]; S.c = w
        S.h = np.maximum(S.o, S.c) + np.abs(rng.normal(0, .5, n)); S.l = np.minimum(S.o, S.c) - np.abs(rng.normal(0, .5, n))
        S.sp = np.full(n, sp); S.tick = 0.1; S.adj = None
        up = k % 2 == 0
        for md in ("BID", "ABS", "ABS_C"):
            F = frame(S, 1, n, up, 1000.0, R, md, 1.0); e0 = float(F["eo"][0])
            res[(md, up)].append(run(F, e0, e0 if MODES[md]["side"] else 0.0, 99.0, 2.0, 0.0)[0])
    out = {}
    for md in ("ABS", "ABS_C"):
        for up in (True, False):
            d = np.array(res[(md, up)]) - np.array(res[("BID", up)])
            out[(md, "LONG" if up else "SHORT")] = (float(d.mean()), float(d.std() / math.sqrt(len(d))))
    return out


# ----------------------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--verify", action="store_true"); ap.add_argument("--syms", default="")
    a = ap.parse_args(); T0 = time.time()
    prof, refs = spread_profile(), ref_prices()
    rows = sorted(load_rows(), key=lambda x: x["t"])
    others = [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"]
    parents = [dict(x, strat="TrendCont") for x in rows] + others
    syms = sorted({x["sym"] for x in parents})
    if a.syms: syms = [s for s in syms if s in a.syms.split(",")]
    TC = {}; INV = {}; P = {}; RP = {}; DET = []; BAS = {}; nomap = defaultdict(int); tsym = {}
    if a.verify: P[("synth",)] = synthetic_check()
    for sym in syms:
        t1 = time.time()
        S = Sym(sym, prof, refs, need_dk=sym in ("EURUSD.dk", "US100.dk"))
        if a.verify:                                   # event-skip engine == naive per-bar loop, every mode
            import random
            rnd = random.Random(1); smp = rnd.sample([x for x in parents if x["sym"] == sym], 30)
            bad = 0
            for x in smp:
                for md in MODES:
                    if MODES[md]["adj"] and S.adj is None: continue
                    if x["strat"] == "TrendCont":
                        r1 = regular(S, x, md, SCEN + [("E", F0, 0.25, True, True)]); r2 = regular(S, x, md, SCEN + [("E", F0, 0.25, True, True)], naive=True)
                        if (r1 is None) != (r2 is None) or (r1 and any(abs(r1[k][0] - r2[k][0]) > 1e-9 for k in r1)): bad += 1
                    i1, i2 = inverse(S, x, md), inverse(S, x, md, naive=True)
                    if (i1 is None) != (i2 is None) or (i1 and (abs(i1["plain"] - i2["plain"]) > 1e-9 or abs(i1["pyr"] - i2["pyr"]) > 1e-9)): bad += 1
            print(f"  VERIFY {sym}: event-skip vs naive per-bar loop on 30 trades x {len(MODES)} modes: {bad} mismatches", flush=True)
            P[("verify", sym)] = bad
        for x in rows:
            if x["sym"] != sym: continue
            r = {md: regular(S, x, md, SCEN) for md in MAIN}
            if any(v is None for v in r.values()): nomap[sym] += 1; continue
            TC[(x["sym"], x["t"])] = (x, r)
        for x in parents:
            if x["sym"] != sym: continue
            if setup(S, x, "BID") is None: continue
            INV[(x["sym"], x["t"], x["strat"])] = (x, {md: inverse(S, x, md) for md in MAIN})
        if sym in ("EURUSD.dk", "US100.dk"):
            RP[sym], det = repro(S, P); DET += det; BAS[sym] = basis_table(S)
        refs[sym.split(".")[0] + ".sim"] = S.ref
        tsym[sym] = time.time() - t1
        print(f"  {sym}: done in {tsym[sym]:.0f}s", flush=True)
        del S
    report(TC, INV, P, RP, DET, BAS, nomap, tsym, time.time() - T0, prof, refs)


def report(TC, INV, P, RP, DET, BAS, nomap, tsym, secs, prof, refs):
    o = Out()
    o(__doc__.split("    python3")[0].rstrip())
    o(f"\nRUN: {len(TC)} TrendCont trades replayed on M1 ({sum(nomap.values())} without M1 coverage: {dict(nomap)}); "
      f"{len(INV)} Inverse parents; {secs / 60:.1f} min total ("
      + ", ".join(f"{k.split('.')[0]} {v:.0f}s" for k, v in tsym.items()) + ")")
    if any(k[0] == "verify" for k in P):
        o("VERIFY: event-skip engine vs naive per-bar loop, 30 random parents per symbol x every mode: "
          + ", ".join(f"{k[1].split('.')[0]} {v}" for k, v in P.items() if k[0] == "verify") + " mismatches")
    if ("synth",) in P:
        o("SYNTHETIC CHECK (driftless random walk, 1,500 paths per side, constant spread = 0.10R, plain stop -1R / TP +2R): ask-side"
          " cost vs BID = " + ", ".join(f"{md} {sd} {m:+.4f}R (se {se:.4f})" for (md, sd), (m, se) in P[("synth",)].items())
          + " - the ask side costs exactly one spread in expectation; on real data the per-trade ask-vs-control differences are"
          " path flips (a stop or bank reached in one mode and missed in the other), i.e. noise around that expectation.")
    o("\nSPREAD USED (price units, median of the 24 UTC hours / max hour) and the Sept-2026 reference price for SCALED:")
    for s in sorted({k[0] for k in TC}):
        a = prof[s.split(".")[0] + ".sim"]
        o(f"  {s.split('.')[0]:7} median {np.median(a):.5g}  max {a.max():.5g} (hour {int(a.argmax())})  ref price {refs.get(s.split('.')[0] + '.sim', float('nan')):.6g}"
          f"  DRAG (BID mode) {DRAG.get(s.split('.')[0], 0.007):.4f}R")

    keys = sorted(TC, key=lambda k: TC[k][0]["t"])
    def sel(period, side=None, exgold=False, sym=None):
        ks = []
        for k in keys:
            x = TC[k][0]
            if (x["t"][:4] < HOLD) != (period == "dev"): continue
            if side is not None and x["up"] != (side == "L"): continue
            if exgold and x["sym"].startswith("XAUUSD"): continue
            if sym and x["sym"] != sym: continue
            ks.append(k)
        return ks
    val = lambda k, md, sc: TC[k][1][md][sc][0]

    # ---------------------------------------------------------------- increments (TrendCont record)
    INCS = [("bank 25% vs 50% (full entry)", "F50", "F25"), ("staged entry 25%/bar-6 vs full (bank 25%)", "F25", "S25"),
            ("pyramid +50% at +1.5R (on staged + bank 25%)", "S25", "S25P")]
    summ = []
    def inc_stats(ks, md, b, v):
        if len(ks) < 2: return None
        bb = [val(k, md, b) for k in ks]; vv = [val(k, md, v) for k in ks]
        return st.mean(vv) - st.mean(bb), paired_t(bb, vv), maxdd(bb), maxdd(vv), len(ks)
    for name, b, v in INCS:
        r = {}
        for per in ("dev", "hold"):
            for md in MAIN: r[(per, md)] = inc_stats(sel(per), md, b, v)
        summ.append((name, r))

    # ---------------------------------------------------------------- inverse
    ikeys = sorted(INV, key=lambda k: INV[k][0]["t"])
    def isel(period, side=None, exgold=False, strat=None, sym=None):
        out = []
        for k in ikeys:
            x = INV[k][0]
            if (x["t"][:4] < HOLD) != (period == "dev"): continue
            if side is not None and (not x["up"]) != (side == "L"): continue      # side of the INVERSE
            if exgold and x["sym"].startswith("XAUUSD"): continue
            if strat and x["strat"] != strat: continue
            if sym and x["sym"] != sym: continue
            out.append(k)
        return out
    def ifill(k, md):
        q = INV[k][1][md]; return q if (q and W0 <= q["bar"] <= W1) else None
    def inv_stats(ks, md, what):
        """what: 'plain' | 'pyr' | 'pinc' -> per-fill mean, n fills, t, DD; per-parent mean (0 when no fill)."""
        f = [ifill(k, md) for k in ks]
        if what == "pinc": vals = [(q["pyr"] - q["plain"]) for q in f if q]; pp = [(q["pyr"] - q["plain"]) if q else 0.0 for q in f]
        else: vals = [q[what] for q in f if q]; pp = [q[what] if q else 0.0 for q in f]
        if not vals: return None
        return st.mean(vals), len(vals), tstat(vals), maxdd(vals), st.mean(pp) if pp else 0.0, tstat(pp), len(ks)
    for name, what in (("the Inverse (per parent; bars 7-18)", "plain"), ("pyramid on the Inverse (per parent)", "pinc")):
        r = {}
        for per in ("dev", "hold"):
            for md in MAIN:
                q = inv_stats(isel(per), md, what)
                r[(per, md)] = (q[4], q[5], None, None, q[6]) if q else None
        summ.append((name, r))

    # ---------------------------------------------------------------- FLAGS (top)
    o("\n" + "=" * 118)
    o("FLAGS - live rules whose development (2012-24) increment goes <= 0 with the ask side in")
    flagged = False
    for name, r in summ:
        bad = [md for md in ("ABS", "SCALED") if r[("dev", md)] and r[("dev", md)][0] <= 0]
        if bad:
            flagged = True
            vals = ", ".join(f"{md} {r[('dev', md)][0]:+.4f}R" for md in bad)
            o(f"  *** {name.upper()}: INCREMENT <= 0 WITH THE ASK SIDE IN ({vals}; bid-only {r[('dev', 'BID')][0]:+.4f}R) ***")
    if not flagged: o("  none - every live rule's development increment stays > 0 under both ABS and SCALED spreads")
    for name, r in summ:
        if r[("hold", "BID")] and any(r[("hold", md)] and r[("hold", md)][0] <= 0 for md in ("ABS", "SCALED")):
            o(f"  (info, holdout 2025+ - not a flag criterion: {name} {r[('hold', 'BID')][0]:+.4f} bid-only -> ABS {r[('hold', 'ABS')][0]:+.4f} /"
              f" SCALED {r[('hold', 'SCALED')][0]:+.4f}{' - already <= 0 bid-only' if r[('hold', 'BID')][0] <= 0 else ''})")
    for name, b, v in INCS:                                  # side level (coach: every study gets a long/short split)
        for side in ("L", "S"):
            q = {md: inc_stats(sel("dev", side=side), md, b, v) for md in ("BID", "ABS", "SCALED")}
            if q["ABS"][0] <= 0 or q["SCALED"][0] <= 0:
                o(f"  (info, dev {'LONGS' if side == 'L' else 'SHORTS'} only: {name} {q['BID'][0]:+.4f} bid-only -> ABS {q['ABS'][0]:+.4f} / SCALED {q['SCALED'][0]:+.4f}"
                  f"{' - already <= 0 bid-only' if q['BID'][0] <= 0 else ' - CROSSES ZERO WITH THE ASK SIDE'})")

    o("\nSUMMARY - increment of each live rule, mean costed R per trade (Inverse rows: per PARENT, 0 when no Inverse), paired t")
    o("  share = ask-corrected / bid-only increment (n/a when the bid-only increment is <= +0.005R)")
    o(f"  {'rule':46} {'period':6} {'BID':>15} {'ABS':>15} {'share':>6} {'SCALED':>15} {'share':>6} {'n':>5}")
    for name, r in summ:
        for per in ("dev", "hold"):
            b, a1, a2 = r[(per, "BID")], r[(per, "ABS")], r[(per, "SCALED")]
            if not b: continue
            f = lambda q: f"{q[0]:+.4f} (t{q[1]:+.2f})" if q else "-"
            o(f"  {name if per == 'dev' else '':46} {('2012-24' if per == 'dev' else '2025+'):6} {f(b):>15} {f(a1):>15} {share(b[0], a1[0]):>6} "
              f"{f(a2):>15} {share(b[0], a2[0]):>6} {b[4]:5d}")

    # ---------------------------------------------------------------- A. BASE
    o("\n" + "=" * 118)
    o("A. BASE LIVE DOCTRINE (TrendCont blind record): staged 25% at signal + 75% at the bar-6 close, bank 25% at +1R/TP1 on every")
    o("   open leg, stop to the first tranche's entry, runner to TP2. Mean costed R per trade, t, worst DD (R, time order)")
    o(f"  {'subset':22} {'n':>5}" + "".join(f" {md:>22}" for md in MAIN))
    for per in ("dev", "hold"):
        for lab, kw in (("all", {}), ("LONGS", {"side": "L"}), ("SHORTS", {"side": "S"}), ("ex-gold", {"exgold": True})):
            ks = sel(per, **kw)
            if not ks: continue
            cells = []
            for md in MAIN:
                v = [val(k, md, "S25") for k in ks]; cells.append(f"{st.mean(v):+.4f} t{tstat(v):+5.2f} DD{maxdd(v):5.1f}")
            o(f"  {('2012-24 ' if per == 'dev' else '2025+   ') + lab:22} {len(ks):5d}" + "".join(f" {c:>22}" for c in cells))
    o("  per symbol (mean costed R; BID -> ABS / SCALED), longs | shorts:")
    for per in ("dev", "hold"):
        for s in sorted({k[0] for k in keys}):
            cells = []
            for side in ("L", "S"):
                ks = sel(per, side=side, sym=s)
                if not ks: cells.append(f"{'-':>38}"); continue
                m = {md: st.mean(val(k, md, "S25") for k in ks) for md in ("BID", "ABS", "SCALED")}
                cells.append(f"n{len(ks):4d} {m['BID']:+.3f} -> {m['ABS']:+.3f} / {m['SCALED']:+.3f}")
            o(f"    {('2012-24 ' if per == 'dev' else '2025+   ') + s.split('.')[0]:15} L: {cells[0]:38} | S: {cells[1]}")
    o("  ask minus bid on the base, by side (dev): how much of the change is the cost measure (control) vs the side triggers")
    for side in ("L", "S"):
        ks = sel("dev", side=side)
        m = {md: st.mean(val(k, md, "S25") for k in ks) for md in MAIN}
        tt = lambda a_, b_: paired_t([val(k, b_, "S25") for k in ks], [val(k, a_, "S25") for k in ks])
        o(f"    {('LONGS ' if side == 'L' else 'SHORTS')}: ABS {m['ABS'] - m['BID']:+.4f} = cost measure {m['ABS_C'] - m['BID']:+.4f} + side triggers {m['ABS'] - m['ABS_C']:+.4f} (t{tt('ABS', 'ABS_C'):+.2f})"
          f"   | SCALED {m['SCALED'] - m['BID']:+.4f} = cost measure {m['SCALED_C'] - m['BID']:+.4f} + side triggers {m['SCALED'] - m['SCALED_C']:+.4f} (t{tt('SCALED', 'SCALED_C'):+.2f})")
    o("  if the DRAG were ALSO charged on every short unit in ABS (the suggested 'DRAG as entry cost' taken literally = the spread")
    o("  counted twice on shorts), dev: base SHORTS " + f"{st.mean(TC[k][1]['ABS']['S25'][0] - TC[k][0]['cost'] * TC[k][1]['ABS']['S25'][2] for k in sel('dev', side='S')):+.4f}"
      + " | increments (all trades): " + ", ".join(
          f"{lab} {st.mean((TC[k][1]['ABS'][v][0] - (0 if TC[k][0]['up'] else TC[k][0]['cost'] * TC[k][1]['ABS'][v][2])) - (TC[k][1]['ABS'][b][0] - (0 if TC[k][0]['up'] else TC[k][0]['cost'] * TC[k][1]['ABS'][b][2])) for k in sel('dev')):+.4f}"
          for lab, b, v in (("bank", "F50", "F25"), ("staged", "F25", "S25"), ("pyramid", "S25", "S25P"))))

    # ---------------------------------------------------------------- B. increments detail
    o("\n" + "=" * 118)
    o("B. INCREMENTS ON THE TRENDCONT RECORD - paired on the same trades: mean increment (variant - comparator), paired t,")
    o("   worst DD comparator -> variant. Control columns (_C) = bid triggers + the same explicit spread (cost-measure change only).")
    for name, b, v in INCS:
        o(f"\n  {name}   [comparator {b} -> variant {v}]")
        o(f"  {'subset':22} {'n':>5}" + "".join(f" {md:>27}" for md in MAIN) + "  share ABS/SCALED")
        for per in ("dev", "hold"):
            for lab, kw in (("all", {}), ("LONGS", {"side": "L"}), ("SHORTS", {"side": "S"}), ("ex-gold", {"exgold": True})):
                ks = sel(per, **kw)
                if len(ks) < 2: continue
                q = {md: inc_stats(ks, md, b, v) for md in MAIN}
                cells = [f"{q[md][0]:+.4f} t{q[md][1]:+5.2f} {q[md][2]:4.0f}->{q[md][3]:4.0f}" for md in MAIN]
                o(f"  {('2012-24 ' if per == 'dev' else '2025+   ') + lab:22} {len(ks):5d}" + "".join(f" {c:>27}" for c in cells)
                  + f"  {share(q['BID'][0], q['ABS'][0])}/{share(q['BID'][0], q['SCALED'][0])}")
        o("    per symbol (increment BID -> ABS / SCALED):")
        for per in ("dev", "hold"):
            cells = []
            for s in sorted({k[0] for k in keys}):
                ks = sel(per, sym=s)
                q = {md: inc_stats(ks, md, b, v) for md in ("BID", "ABS", "SCALED")}
                if q["BID"]: cells.append(f"{s.split('.')[0]} {q['BID'][0]:+.3f}->{q['ABS'][0]:+.3f}/{q['SCALED'][0]:+.3f}")
            o(f"      {'2012-24' if per == 'dev' else '2025+  '}  " + "  ".join(cells))

    # ---------------------------------------------------------------- C. inverse detail
    o("\n" + "=" * 118)
    o("C. THE INVERSE (TrendCont record + SweepMSS/DeepFib/EMArevQ AA rows, 7 symbols, no BTCUSD); parent stopped in H4 bars 7-18")
    o("   per FILL: mean costed R, n fills, t, DD | per PARENT (0 when no Inverse; paired across modes): mean, t. LONG/SHORT = the Inverse's side.")
    for title, what in (("PLAIN INVERSE", "plain"), ("INVERSE WITH ITS +1.5R PYRAMID", "pyr"), ("PYRAMID-ON-INVERSE INCREMENT (pyr - plain)", "pinc")):
        o(f"\n  {title}")
        o(f"  {'subset':22} {'parents':>7}" + "".join(f" {md:>36}" for md in MAIN))
        for per in ("dev", "hold"):
            for lab, kw in (("all", {}), ("LONG inverses", {"side": "L"}), ("SHORT inverses", {"side": "S"}), ("ex-gold", {"exgold": True}),
                            ("TrendCont parents", {"strat": "TrendCont"})):
                ks = isel(per, **kw); cells = []
                for md in MAIN:
                    q = inv_stats(ks, md, what)
                    cells.append(f"{q[0]:+.3f} n{q[1]:3d} t{q[2]:+5.2f} DD{q[3]:4.1f} | {q[4]:+.4f}" if q else "-")
                o(f"  {('2012-24 ' if per == 'dev' else '2025+   ') + lab:22} {len(ks):7d}" + "".join(f" {c:>36}" for c in cells))
    o("\n  per symbol, plain Inverse per FILL (BID -> ABS / SCALED, n fills BID/ABS/SCALED):")
    for per in ("dev", "hold"):
        cells = []
        for s in sorted({k[0] for k in ikeys}):
            ks = isel(per, sym=s); q = {md: inv_stats(ks, md, "plain") for md in ("BID", "ABS", "SCALED")}
            if all(q.values()): cells.append(f"{s.split('.')[0]} {q['BID'][0]:+.3f}->{q['ABS'][0]:+.3f}/{q['SCALED'][0]:+.3f} (n{q['BID'][1]}/{q['ABS'][1]}/{q['SCALED'][1]})")
        o(f"    {'2012-24' if per == 'dev' else '2025+  '}  " + "  ".join(cells))

    # ---------------------------------------------------------------- D. reproduction sanity
    o("\n" + "=" * 118)
    o("D. SANITY CHECK AGAINST THE EA's REAL-TICK REPRODUCTIONS (raw R, no DRAG: the EA's journal R is price-based)")
    o("   modes: BID (the old replay), TICK (1-tick spread, what the tester's .dk ticks carry), DK (TICK + the M1 series re-aligned")
    o("   bar by bar to the .dk price space), ABS / SCALED (the 2026 OANDA spread of this study)")
    o("\n  D1. What spread did the tester run at? Staged BUY adds fill at the next bar's open + 1 tick (US100 +0.1, EURUSD +0.00001),")
    o("      SELL adds at the open (promote_repro journals, every year 2012-2026) - the .dk tick imports carry a ONE-TICK spread,")
    o("      not the market's. The ask side therefore cannot explain the EA gaps; with the 2026 spread the replay moves AWAY from")
    o("      the EA world, not toward it (ABS/SCALED columns below).")
    o("\n  D2. The .dk-vs-M1 basis (.dk H4 close minus the M1 bid close at the same minute), median by UTC hour of the close / year:")
    for s, (bh, by) in BAS.items():
        o(f"      {s.split('.')[0]:7} by hour: " + "  ".join(f"{h:02d}:{v:+.3g}" for h, v in bh.items()))
        o(f"      {'':7} by year: " + "  ".join(f"{y}:{v:+.3g}" for y, v in by.items()))
    o("      US100's basis moves with the session (~+1.5 outside the US cash session, ~+0.5 inside): a replay that fixes ONE offset")
    o("      at the signal sees the .dk path shifted by up to ~1 point later in the trade. That is a data-alignment gap, not the ask.")
    o("\n  D3. Inverse (EA filled Inverses, data/study/inverse_repro): matched-fill means and per-parent totals")
    for sym in ("EURUSD.dk", "US100.dk"):
        q = P.get(("inv", sym))
        if not q: continue
        for md in REPRO:
            n, ea, rp, um, extra, npar, tea, trp = q[md]
            o(f"    {sym.split('.')[0]:7} {md:7} matched n={n:3d} (EA fills unmatched {um}, replay-only fills {extra:2d}): EA {ea:+.3f} replay {rp:+.3f} "
              f"gap {ea - rp:+.3f} {'PASS' if abs(ea - rp) <= 0.03 else 'FAIL'} | per parent (n={npar}) EA {tea:+.4f} replay {trp:+.4f} gap {tea - trp:+.4f}")
    o("\n    US100 fills where some mode disagrees with the EA by more than 0.5R (EA R; replay R by mode; '-' = no replay fill):")
    for sym, t, strat, d, ea, rp in DET:
        if sym != "US100.dk": continue
        if all(rp[md] is not None and abs(ea - rp[md]) <= 0.5 for md in REPRO): continue
        o(f"      {t} {strat:9} parent {d:4} EA {ea:+.2f}  " + "  ".join(f"{md} {('-' if rp[md] is None else f'{rp[md]:+.2f}')}" for md in REPRO))
    o("\n  D4. Rule gains, EA vs replay (gap = EA gain - replay gain; PASS within 0.02R):")
    for sym, lst in RP.items():
        for name, n, ea, g in lst:
            o(f"    {sym.split('.')[0]:7} {name:36} n={n:4d} EA {ea:+.4f} | " + "  ".join(f"{md} {g[md]:+.4f} (gap {ea - g[md]:+.4f})" for md in REPRO))
    o("\n  READING")
    o("  * The tester ran with a 1-tick spread, so only TICK / DK are like-for-like with the EA. TICK leaves every gap where BID had it;")
    o("    the ask side as the EA saw it is one tick and cannot be the cause of the negative gaps.")
    o("  * The US100 Inverse gap (-0.058 on 45 matched fills) is ONE trade: the 2021.04.29 TrendCont BUY parent's Inverse SELL was stopped")
    o("    at break-even in the EA (+0.16R) and ran to TP2 in the replay (+3.25R): -3.09R / 45 = -0.069R, more than the whole gap (the")
    o("    other 44 net slightly positive - the EA's lot-step rounding of the 25% bank: BE exits +0.14..+0.17 instead of +0.25, TP exits")
    o("    +3.4 instead of +3.25). By hand: after the bank the M1 bid high came within 1.2 points of the replay's BE stop, while the")
    o("    EA's Inverse had filled 0.7 points BELOW the parent's stop (tick slippage, inv_slip_r 0.005) - its BE stop sat 0.7 lower -")
    o("    and the .dk-vs-M1 basis had drifted from +0.6 at the signal to +1.7 that week. A near miss decided by fill slippage and data")
    o("    alignment. ABS 'closes' it only because the 2026 1.8-point spread exceeds the 1.2-point miss - coincidence, not mechanism;")
    o("    the bar-by-bar re-alignment (DK) does not reproduce it either and flips the 2019.03.05 fill the other way (worse gap).")
    o("  * On M1 the bank / pyramid / early-promotion gaps are already inside the 0.02R band in BID mode (-0.002..-0.010); the larger")
    o("    gaps quoted earlier (bank -0.012/-0.009 on H4, early promotion -0.015/-0.027 on H4) were the coarse-bar replay. Their sign")
    o("    is mostly negative but their size is single-trade path flips; no mode here moves them systematically.")
    o("  * ABS / SCALED answer the live question (what OANDA's spread does to the rules - sections A-C), not the reproduction question.")
    with open(REPORT, "w") as f: f.write("\n".join(o.lines) + "\n")
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__": main()
