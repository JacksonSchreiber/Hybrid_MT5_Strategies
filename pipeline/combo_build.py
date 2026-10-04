#!/usr/bin/env python3
"""combo_build.py - trader 2026-10-03: every combination of the post-signal management dials, for every trade, for the webapp
research tool. Engine = combo3_study.run_v / mgmt_variants.run_g, compiled with numba (same checks, same order, same float
operation order -> bit-identical to the Python engine; proven by --verify).

DIALS (defaults = the live setup). Every dial is independent unless noted.
  stage  off (100% at the signal) | 25%@bar6 (live) | 25%@bar3 | 50%@bar6 | 50%@bar3     first share now, the rest at that bar's close
  gateA  off | on (only with staging): the add is held when the trial's average R (entry -> the add bar's close) is below 0, and
         goes in when the bid first reaches +1R (coach queue A; the original rule = gate at bar 6)
  bank   off | share {detector (live: TrendCont/DeepFib 25%, SweepMSS/EMArevQ 50%), 25%, 50%, 75%} x level {+0.75, +1 (live), +1.25R};
         at every level TP1 is used when it is nearer and the level is capped at TP2 (ask_side_study.setup's rule); bank -> stop to entry.
         "off" = no partial and no stop move.
  pyr    off | size {25%, 50% (live)} x level {+1.25, +1.5 (live), +2R} of the full size; with the bank on it is armed after the
         bank (live); with the bank off it is armed from the entry. Enters at max(level, the minute's open) + spread.
  raise  off | shorts' stop to {+0.25R (live), +0.5R} when the bid first reaches +1.5R (fixed, as live), armed like the pyramid.
  cutB   off | threshold {0.25, 0.5, 0.75}: a SHORT is closed at the bar-12 close if its average R since entry is <= the threshold.
  addI   off | size {25%, 50%}: a LONG gets that share of full size at the bar-12 close if day 2 (bar 6 -> 12) averages better than
         day 1 (entry -> bar 6). Day boundaries are fixed at bars 6 / 12 whatever the staging bar.
Per minute the order is: stop, bank, pyramid, raise, held-add release, TP2, staged add, B cut, I add (run_v's order). Raw signal = all
off: 100% at the signal, the signal's stop, out at TP2 or the stop (or the end of the replay window).

OUTPUT (trade-major memmap, data/combos/<run>/results.u8 + meta.json):
  per trade x combo: R float32 (full position, costs in), exit H4 bars x10 uint16, exit kind uint8 (0 stop -1R, 1 stop at entry,
  2 raised stop, 3 TP2, 4 cut B, 5 window end, 255 no data), event times (H4 bars x10, 65535 = none) for the staged add, bank,
  pyramid and I add.
    python3 pipeline/combo_build.py --verify            the verification ladder (see verify())
    python3 pipeline/combo_build.py --bench SYM [N ...] full grid on one symbol with N workers, timing + RAM
    python3 pipeline/combo_build.py --build [--workers N]   everything (only on the trader's go)
"""
from __future__ import annotations
import argparse, hashlib, itertools, json, os, sys, time
import numpy as np
from numba import njit
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import ask_side_study as A                     # noqa: E402
from pipeline import combo3_study as K3                      # noqa: E402
from pipeline import oos_queue_study as Q                    # registers the new symbols' inputs  # noqa: E402
from pipeline import oos_build_inputs as OI                  # noqa: E402
from pipeline.inverse_study import BANKF                     # noqa: E402
from pipeline.m1_cache import CachedSym                      # noqa: E402
from pipeline.per_symbol_stats import ORIG                   # noqa: E402
from pipeline.progress import Bar                            # noqa: E402

MODE = "SCALED"
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "combos")
# ------------------------------------------------------------------------------------------------------------- dials
STAGE = [("off", 1.0, 0), ("25@6", 0.25, 6), ("25@3", 0.25, 3), ("50@6", 0.5, 6), ("50@3", 0.5, 3)]
BANK = [("off", None, None)] + [(f"{s}@{l}", s, l) for s in ("det", 0.25, 0.5, 0.75) for l in (0.75, 1.0, 1.25)]
PYR = [("off", 0.0, 0.0)] + [(f"{f}@{l}", f, l) for f in (0.25, 0.5) for l in (1.25, 1.5, 2.0)]
RAISE = [("off", 0.0), ("0.25", 0.25), ("0.5", 0.5)]
CUTB = [("off", None), ("0.25", 0.25), ("0.5", 0.5), ("0.75", 0.75)]
ADDI = [("off", 0.0), ("0.25", 0.25), ("0.5", 0.5)]
SR_TRIG = 1.5


def grid():
    """-> list of combos, each (stage_i, gateA, bank_i, pyr_i, raise_i, cut_i, addi_i); gateA only with staging."""
    out = []
    for si, st in enumerate(STAGE):
        for ga in ((0,) if st[1] >= 1.0 else (0, 1)):
            for bi, pi, ri, ci, ii in itertools.product(range(len(BANK)), range(len(PYR)), range(len(RAISE)), range(len(CUTB)), range(len(ADDI))):
                out.append((si, ga, bi, pi, ri, ci, ii))
    return out


def find(combos, stage, ga, bank, pyr, rai, cut, addi):
    nm = lambda L, v: [x[0] for x in L].index(v)
    return combos.index((nm(STAGE, stage), ga, nm(BANK, bank), nm(PYR, pyr), nm(RAISE, rai), nm(CUTB, cut), nm(ADDI, addi)))


# ------------------------------------------------------------------------------------------------------------- kernel
@njit(cache=True)
def _val(locked, ls, le, nl, r):
    tot = 0.0
    for q in range(nl): tot += ls[q] * (r - le[q])
    return locked + tot


@njit(cache=True)
def run_one(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, up, has_tp2, tp2_R,
            F0, k_add, gate, gate_lvl, bank_on, bank_R, bankf, pyr_f, pyr_lvl, sr_lvl, sr_trig, kc, ka2, add_f):
    """one trade, one combo -> (R, units, exit_j, kind, add_j, bank_j, pyr_j, imp_j). Mirrors run_v / run_g check for check."""
    ls = np.zeros(4); le = np.zeros(4); nl = 1; ls[0] = F0; le[0] = e0
    locked = 0.0; stop = -1.0; banked = False; units = F0
    pyr_pend = pyr_f > 0.0; sr_pend = sr_lvl > 0.0
    add_pend = F0 < 1.0 and k_add >= 0 and k_add < n
    held = False
    if gate and add_pend:
        add_pend = False; held = True
    add_j = -1; bank_j = -1; pyr_j = -1; imp_j = -1
    for j in range(n):
        if xl[j] <= stop:
            r = stop if stop < xo[j] else xo[j]
            kind = 0 if stop == -1.0 else (1 if stop == be else 2)
            return _val(locked, ls, le, nl, r), units, j, kind, add_j, bank_j, pyr_j, imp_j
        if bank_on and (not banked) and xh[j] >= bank_R:
            banked = True
            tot = 0.0
            for q in range(nl): tot += ls[q] * (bank_R - le[q])
            locked += bankf * tot
            m = 1 - bankf
            for q in range(nl): ls[q] = ls[q] * m
            stop = be; bank_j = j
        armed = banked or (not bank_on)
        if armed and pyr_pend and bh[j] >= pyr_lvl:
            ls[nl] = pyr_f; le[nl] = (pyr_lvl if pyr_lvl > bo[j] else bo[j]) + eo[j]; nl += 1; units += pyr_f; pyr_pend = False; pyr_j = j
        if armed and sr_pend and bh[j] >= sr_trig:
            if not up:
                v = be + sr_lvl
                if v > stop: stop = v
            sr_pend = False
        if held and j > k_add and bh[j] >= gate_lvl:
            ls[nl] = 1 - F0; le[nl] = (gate_lvl if gate_lvl > bo[j] else bo[j]) + eo[j]; nl += 1; units += 1 - F0; held = False; add_j = j
        if has_tp2 and xh[j] >= tp2_R:
            return _val(locked, ls, le, nl, tp2_R), units, j, 3, add_j, bank_j, pyr_j, imp_j
        if add_pend and j == k_add:
            ls[nl] = 1 - F0; le[nl] = bc[j] + eo[j]; nl += 1; units += 1 - F0; add_pend = False; add_j = j
        if kc >= 0 and j == kc:
            return _val(locked, ls, le, nl, xc[j]), units, j, 4, add_j, bank_j, pyr_j, imp_j
        if ka2 >= 0 and j == ka2:
            ls[nl] = add_f; le[nl] = bc[j] + eo[j]; nl += 1; units += add_f; ka2 = -1; imp_j = j
    return _val(locked, ls, le, nl, xc[n - 1]), units, n - 1, 5, add_j, bank_j, pyr_j, imp_j


@njit(cache=True)
def run_all(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, up, has_tp2, tp2_R, P, I, outR, outU, outI):
    """all combos of one trade. P float params (F0, gate_lvl, bank_R, bankf, pyr_f, pyr_lvl, sr_lvl, sr_trig, add_f),
    I int params (k_add, gate, bank_on, kc, ka2). outI columns: exit_j, kind, add_j, bank_j, pyr_j, imp_j."""
    for c in range(P.shape[0]):
        r = run_one(xo, xh, xl, xc, bo, bh, bc, eo, n, e0, be, up, has_tp2, tp2_R,
                    P[c, 0], I[c, 0], I[c, 1] == 1, P[c, 1], I[c, 2] == 1, P[c, 2], P[c, 3], P[c, 4], P[c, 5], P[c, 6], P[c, 7],
                    I[c, 3], I[c, 4], P[c, 8])
        outR[c] = r[0]; outU[c] = r[1]
        outI[c, 0] = r[2]; outI[c, 1] = r[3]; outI[c, 2] = r[4]; outI[c, 3] = r[5]; outI[c, 4] = r[6]; outI[c, 5] = r[7]


# ------------------------------------------------------------------------------------------------------------- per trade
def prep(S, x):
    """everything per trade that does not depend on the combo: frame + trigger points per dial setting. None = no M1 coverage."""
    u = A.setup(S, x, MODE)
    if not u: return None
    m = A.MODES[MODE]
    F = A.frame(S, u["k0"], u["end"], u["up"], u["e"], u["R"], MODE, u["scale"])
    n = F["n"]; e0 = float(F["eo"][0]); be = e0 if m["side"] else 0.0
    h4, i0, k0 = x["bars"], u["i0"], u["k0"]
    kbar = lambda N: (int(np.searchsorted(S.t, A.tmin(h4[i0 + N][0]))) - 1 - k0) if i0 + N < len(h4) else -1
    assert kbar(A.N) == (u["k_add"] if u["k_add"] is not None else -1)
    cum = np.cumsum(F["bc"])
    avg = lambda k: cum[k] / (k + 1)
    gate = {N: (0 <= kbar(N) < n and avg(kbar(N)) < 0) for N in (3, 6)}
    ib = i0 + K3.CHK
    k12 = (int(np.searchsorted(S.t, S.h4t[ib])) - k0 - 1) if ib < len(S.h4) else None
    avg2 = avg(k12) if (k12 is not None and 0 <= k12 < n) else None
    ka6 = u["k_add"]
    impr = bool(avg2 is not None and u["up"] and ka6 is not None and 0 <= ka6 < k12 and
                (cum[k12] - cum[ka6]) / (k12 - ka6) > cum[ka6] / (ka6 + 1))
    sg = 1.0 if u["up"] else -1.0
    tp1R = (x["tp1"] - x["e"]) * sg / u["R"] if x["tp1"] else None
    def bank_R(L):
        b = min(L, tp1R) if tp1R is not None else L
        if u["tp2_R"] is not None and b >= u["tp2_R"] - 1e-9: b = u["tp2_R"]
        return b
    assert bank_R(1.0) == u["bank_R"], (bank_R(1.0), u["bank_R"])
    return {"F": F, "n": n, "e0": e0, "be": be, "up": u["up"], "tp2": u["tp2_R"], "kbar": {N: kbar(N) for N in (3, 6)}, "gate": gate,
            "k12": k12, "avg2": avg2, "impr": impr, "bank_R": bank_R, "bankf_det": BANKF.get(x["strat"], 0.5), "cost": x["cost"],
            "k0": k0, "i0": i0}


def params(p, combos):
    """per-trade parameter matrices for run_all."""
    nc = len(combos); P = np.zeros((nc, 9)); I = np.full((nc, 5), -1, dtype=np.int64)
    for c, (si, ga, bi, pi, ri, ci, ii) in enumerate(combos):
        _, F0, N = STAGE[si]; _, bs, bl = BANK[bi]; _, pf, pl = PYR[pi]; _, sr = RAISE[ri]; _, th = CUTB[ci]; _, af = ADDI[ii]
        P[c] = (F0, K3.GATE_LVL, p["bank_R"](bl) if bl is not None else 0.0, (p["bankf_det"] if bs == "det" else bs) if bs is not None else 0.0,
                pf, pl, sr, SR_TRIG, af)
        k_add = p["kbar"][N] if F0 < 1.0 else -1
        kc = p["k12"] if (th is not None and p["avg2"] is not None and not p["up"] and p["avg2"] <= th) else -1
        ka2 = p["k12"] if (af > 0 and p["impr"]) else -1
        I[c] = (k_add, 1 if (ga and F0 < 1.0 and p["gate"][N]) else 0, 1 if bs is not None else 0, kc if kc is not None else -1, ka2)
    return P, I


_CI: dict = {}
def _cidx(combos):
    """per-combo dial indices as arrays (computed once per combo list)."""
    key = id(combos)
    if _CI.get("key") != key:
        a = np.array(combos, dtype=np.int64)
        _CI.update(key=key, si=a[:, 0], ga=a[:, 1], bi=a[:, 2], pi=a[:, 3], ri=a[:, 4], ci=a[:, 5], ii=a[:, 6])
    return _CI


def params_fast(p, combos):
    """vectorised params(): identical matrices (checked in --verify)."""
    c = _cidx(combos); nc = len(combos)
    st_F0 = np.array([s[1] for s in STAGE]); st_N = [s[2] for s in STAGE]
    st_kadd = np.array([p["kbar"][N] if F0 < 1.0 else -1 for _, F0, N in STAGE], dtype=np.int64)
    st_gate = np.array([1 if (F0 < 1.0 and p["gate"][N]) else 0 for _, F0, N in STAGE], dtype=np.int64)
    bk_R = np.array([p["bank_R"](l) if l is not None else 0.0 for _, _, l in BANK])
    bk_f = np.array([((p["bankf_det"] if sh == "det" else sh) if sh is not None else 0.0) for _, sh, _ in BANK])
    bk_on = np.array([1 if sh is not None else 0 for _, sh, _ in BANK], dtype=np.int64)
    py_f = np.array([f for _, f, _ in PYR]); py_l = np.array([l for _, _, l in PYR])
    sr = np.array([v for _, v in RAISE]); af = np.array([v for _, v in ADDI])
    cut_k = np.array([(p["k12"] if (th is not None and p["avg2"] is not None and not p["up"] and p["avg2"] <= th) else -1) for _, th in CUTB], dtype=np.int64)
    add_k = np.array([(p["k12"] if (v > 0 and p["impr"]) else -1) for _, v in ADDI], dtype=np.int64)
    P = np.empty((nc, 9)); I = np.empty((nc, 5), dtype=np.int64)
    P[:, 0] = st_F0[c["si"]]; P[:, 1] = K3.GATE_LVL; P[:, 2] = bk_R[c["bi"]]; P[:, 3] = bk_f[c["bi"]]
    P[:, 4] = py_f[c["pi"]]; P[:, 5] = py_l[c["pi"]]; P[:, 6] = sr[c["ri"]]; P[:, 7] = SR_TRIG; P[:, 8] = af[c["ii"]]
    I[:, 0] = st_kadd[c["si"]]; I[:, 1] = st_gate[c["si"]] * c["ga"]; I[:, 2] = bk_on[c["bi"]]; I[:, 3] = cut_k[c["ci"]]; I[:, 4] = add_k[c["ii"]]
    return P, I


def run_trade(p, combos, PI=None):
    P, I = PI if PI is not None else params_fast(p, combos)
    F = p["F"]; nc = len(combos)
    outR = np.zeros(nc); outU = np.zeros(nc); outI = np.zeros((nc, 6), dtype=np.int64)
    run_all(F["xo"], F["xh"], F["xl"], F["xc"], F["bo"], F["bh"], F["bc"], F["eo"], p["n"], p["e0"], p["be"], p["up"],
            p["tp2"] is not None, p["tp2"] if p["tp2"] is not None else 0.0, P, I, outR, outU, outI)
    if A.MODES[MODE]["drag"]: outR = outR - p["cost"] * outU
    return outR, outU, outI


# ------------------------------------------------------------------------------------------------------------- population
def population():
    new = [s for s in OI.NEW if s != "US30.dk"]
    bad, drag = Q.check_new(new)
    ALL = Q.pop_original(set(ORIG), aa_trendcont=True) + Q.pop_new([s for s in new if s not in bad], drag)
    return sorted(ALL, key=lambda x: (x["sym"], x["t"]))       # symbol-major: concurrent workers share one symbol's pages


_SYMS: dict = {}
def sym_obj(sym):
    if sym not in _SYMS:
        _SYMS.clear()                                            # one symbol per worker at a time (RAM)
        _SYMS[sym] = CachedSym(sym, A.spread_profile(), Q.REFS)
    return _SYMS[sym]


# ------------------------------------------------------------------------------------------------------------- output
REC = np.dtype([("R", "<f4"), ("xb", "<u2"), ("kind", "u1"), ("ev", "<u2", (4,))])     # 15 bytes, unaligned
NONE16 = 65535


def to_bars(S, p, js):
    """minute offsets -> H4 bars since the entry bar x10 (uint16; -1 -> 65535)."""
    js = np.asarray(js); out = np.full(js.shape, NONE16, dtype=np.uint16); ok = js >= 0
    if ok.any():
        tm = S.t[p["k0"] + js[ok]]
        hb = np.searchsorted(S.h4t, tm, side="right") - 1
        v = (hb - p["i0"]) + (tm - S.h4t[hb]) / 240.0
        out[ok] = np.clip(np.round(v * 10), 0, NONE16 - 1).astype(np.uint16)
    return out


def pack(S, p, R, U, I):
    rec = np.zeros(len(R), dtype=REC)
    rec["R"] = R.astype(np.float32); rec["xb"] = to_bars(S, p, I[:, 0]); rec["kind"] = I[:, 1].astype(np.uint8)
    rec["ev"] = to_bars(S, p, I[:, 2:6])
    return rec


POP: list = []; COMBOS: list = []; OUTP = ""
def _work(rng):
    a, b = rng
    mm = np.memmap(OUTP, dtype=REC, mode="r+", shape=(len(POP), len(COMBOS)))
    for i in range(a, b):
        x = POP[i]; S = sym_obj(x["sym"]); p = prep(S, x)
        if p is None: mm[i]["kind"] = 255; continue
        R, U, I = run_trade(p, COMBOS)
        mm[i] = pack(S, p, R, U, I)
    mm.flush(); del mm
    return b - a


def units_of(pop, chunk=40):
    out = []; i = 0
    while i < len(pop):
        j = i
        while j < len(pop) and j - i < chunk and pop[j]["sym"] == pop[i]["sym"]: j += 1
        out.append((i, j)); i = j
    return out


def _warm():
    """compile the kernel once in the parent (workers inherit it through fork)."""
    S = sym_obj(POP[0]["sym"])
    for x in POP[:5]:
        p = prep(S, x)
        if p: run_trade(p, COMBOS[:3]); return


def run_pool(pop, outp, workers, chunk=10, progress=False):
    import multiprocessing as mp
    global POP, COMBOS, OUTP
    POP, COMBOS, OUTP = pop, grid(), outp
    np.memmap(outp, dtype=REC, mode="w+", shape=(len(POP), len(COMBOS))).flush()
    _warm(); _SYMS.clear()
    units = units_of(POP, chunk)
    bar = Bar(len(POP), "build", "trades") if progress else None
    with mp.get_context("fork").Pool(workers) as pool:
        for k in pool.imap_unordered(_work, units):
            if bar: bar.update(k)
    if bar: bar.done()
    return len(POP), len(COMBOS)


def mem_sampler(stop_evt, out):
    lo = None
    while not stop_evt.is_set():
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable"): v = int(ln.split()[1]) / 1e6; lo = v if lo is None else min(lo, v)
        stop_evt.wait(0.5)
    out.append(lo)


# ------------------------------------------------------------------------------------------------------------- verify
def _vsym(arg):
    """verification work for one symbol's trades -> counters."""
    from pipeline import mgmt_variants as MV
    sym, xs = arg; combos = COMBOS; old = VOLD; sel = VSEL; i_raw, i_live = VRAW, VLIVE
    st = {"n": 0, "nv": 0, "mx1": 0.0, "mx2": 0.0, "mx3": 0.0, "pmx": 0, "det": 0, "pyr": 0, "cut": 0, "addi": 0, "stage3": 0, "units": 0}
    S = sym_obj(sym)
    for x in xs:
        p = prep(S, x); mv = MV.variants(S, x, MODE)
        if p is None or mv is None:
            st["nv"] += (p is None) != (mv is None); continue
        if st["n"] % 25 == 0:                                   # params_fast == params on a sample
            Pa, Ia = params(p, combos); Pb, Ib = params_fast(p, combos)
            st["pmx"] += int(not (np.array_equal(Pa, Pb) and np.array_equal(Ia, Ib)))
        R, U, I = run_trade(p, combos)
        st["mx1"] = max(st["mx1"], max(abs(R[old[q]] - mv[q]) for q in range(96)))
        F = p["F"]
        for F0, pyr in ((1.0, False), (1.0, True), (0.25, False), (0.25, True)):
            r, _, _ = A.run(F, p["e0"], p["be"], p["bank_R"](1.0), p["tp2"], p["bankf_det"], F0,
                            p["kbar"][6] if F0 < 1 and p["kbar"][6] >= 0 else None, pyr)
            st["mx2"] = max(st["mx2"], abs(R[VRUN[(F0, pyr)]] - r))
        xl, xh, xo, xc = F["xl"], F["xh"], F["xo"], F["xc"]
        hit = np.nonzero((xl <= -1.0) | ((xh >= p["tp2"]) if p["tp2"] is not None else np.zeros(p["n"], bool)))[0]
        if hit.size:
            j = int(hit[0]); raw = (min(-1.0, xo[j]) if xl[j] <= -1.0 else p["tp2"]) - p["e0"]
        else: raw = xc[p["n"] - 1] - p["e0"]
        st["mx3"] = max(st["mx3"], abs(R[i_raw] - raw))
        bf = p["bankf_det"]
        if abs(R[i_live] - R[sel["det25" if bf == 0.25 else "det50"]]) > 1e-12: st["det"] += 1
        if I[i_live, 4] < 0 and abs(R[i_live] - R[sel["p25"]]) > 1e-12: st["pyr"] += 1
        for cc in ("c25", "c75"):
            if I[sel[cc], 1] != 4 and abs(R[sel[cc]] - R[i_live]) > 1e-12: st["cut"] += 1
        if I[sel["i25"], 5] < 0 and abs(R[sel["i25"]] - R[i_live]) > 1e-12: st["addi"] += 1
        if 0 <= p["kbar"][3] and I[i_live, 0] < p["kbar"][3] and abs(R[sel["s3"]] - R[i_live]) > 1e-12: st["stage3"] += 1
        if np.any(~np.isfinite(R)) or np.any(U < 0.24) or np.any(U > 2.51): st["units"] += 1
        st["n"] += 1
    return st


VOLD: dict = {}; VSEL: dict = {}; VRUN: dict = {}; VRAW = VLIVE = 0
def verify(n_det=None, workers=8):
    """the ladder; every line prints PASS / FAIL."""
    import multiprocessing as mp
    from pipeline import mgmt_variants as MV
    global COMBOS, VOLD, VSEL, VRUN, VRAW, VLIVE
    pop = population(); combos = grid(); COMBOS = combos; ok_all = True
    if n_det: pop = pop[::max(1, len(pop) // n_det)]
    def say(ok, msg):
        nonlocal ok_all; ok_all &= bool(ok); print(("PASS " if ok else "FAIL ") + msg, flush=True)
    say(len(combos) == len(set(combos)), f"grid: {len(combos)} distinct combos")
    SA = {0: ("off", 0), 1: ("25@6", 0), 2: ("25@6", 1)}
    for sa, k, pp, r, b, i in itertools.product((0, 1, 2), (0, 1), (0, 1), (0, 1), (0, 1), (0, 1)):
        VOLD[MV.idx(sa, k, pp, r, b, i)] = find(combos, SA[sa][0], SA[sa][1], "det@1.0" if k else "off", "0.5@1.5" if pp else "off",
                                                "0.25" if r else "off", "0.5" if b else "off", "0.5" if i else "off")
    VRAW = find(combos, "off", 0, "off", "off", "off", "off", "off")
    VLIVE = find(combos, "25@6", 0, "det@1.0", "0.5@1.5", "0.25", "off", "off")
    say(VOLD[MV.LIVE] == VLIVE, "the live setup maps to the grid's live combo")
    VSEL.update({"det25": find(combos, "25@6", 0, "0.25@1.0", "0.5@1.5", "0.25", "off", "off"),
                 "det50": find(combos, "25@6", 0, "0.5@1.0", "0.5@1.5", "0.25", "off", "off"),
                 "p25": find(combos, "25@6", 0, "det@1.0", "0.25@1.5", "0.25", "off", "off"),
                 "c25": find(combos, "25@6", 0, "det@1.0", "0.5@1.5", "0.25", "0.25", "off"),
                 "c75": find(combos, "25@6", 0, "det@1.0", "0.5@1.5", "0.25", "0.75", "off"),
                 "i25": find(combos, "25@6", 0, "det@1.0", "0.5@1.5", "0.25", "off", "0.25"),
                 "s3": find(combos, "25@3", 0, "det@1.0", "0.5@1.5", "0.25", "off", "off")})
    for F0, pyr in ((1.0, False), (1.0, True), (0.25, False), (0.25, True)):
        VRUN[(F0, pyr)] = find(combos, "off" if F0 >= 1 else "25@6", 0, "det@1.0", "0.5@1.5" if pyr else "off", "off", "off", "off")
    POP[:] = pop; _warm(); _SYMS.clear()
    syms = sorted({x["sym"] for x in pop}); t0 = time.time()
    with mp.get_context("fork").Pool(workers) as pool:
        res = pool.map(_vsym, [(s_, [x for x in pop if x["sym"] == s_]) for s_ in syms])
    T = {k: (max(r[k] for r in res) if k.startswith("mx") else sum(r[k] for r in res)) for k in res[0]}
    print(f"     ({T['n']} trades, {time.time() - t0:.0f}s)")
    say(T["nv"] == 0, f"coverage agrees with the Python engine (mismatches {T['nv']})")
    say(T["pmx"] == 0, f"vectorised parameters == the reference loop (mismatching trades {T['pmx']})")
    say(T["mx1"] < 1e-12, f"bit-exact vs the Python engine (mgmt_variants/run_g) on all 96 existing combos: max |diff| {T['mx1']:.2e}")
    say(T["mx2"] < 1e-12, f"independent ask_side_study.run (staging x pyramid, bank): max |diff| {T['mx2']:.2e}")
    say(T["mx3"] < 1e-12, f"independent from-scratch raw-signal replay: max |diff| {T['mx3']:.2e}")
    say(T["det"] == 0, f"bank 'detector' == the detector's own share (violations {T['det']})")
    say(T["pyr"] == 0, f"pyramid size only matters when the pyramid fired (violations {T['pyr']})")
    say(T["cut"] == 0, f"B threshold only matters when the cut fired (violations {T['cut']})")
    say(T["addi"] == 0, f"I size only matters when the I add fired (violations {T['addi']})")
    say(T["stage3"] == 0, f"bar-3 staging == bar-6 staging for trades out before bar 3 (violations {T['stage3']})")
    say(T["units"] == 0, f"R finite, position size within 25%..250% (violations {T['units']})")
    sub_pop = [x for x in pop if x["sym"] == pop[0]["sym"]][:60]
    d = os.path.join(OUTDIR, "_verify"); os.makedirs(d, exist_ok=True)
    run_pool(sub_pop, os.path.join(d, "a.u8"), 1); run_pool(sub_pop, os.path.join(d, "b.u8"), workers, chunk=7)
    ha = hashlib.sha256(open(os.path.join(d, "a.u8"), "rb").read()).hexdigest(); hb = hashlib.sha256(open(os.path.join(d, "b.u8"), "rb").read()).hexdigest()
    say(ha == hb, f"determinism: 1 worker vs {workers} workers byte-identical on {len(sub_pop)} trades x {len(combos)} combos")
    print("ALL PASS" if ok_all else "SOME CHECKS FAILED")
    return ok_all


# ------------------------------------------------------------------------------------------------------------- bench / build
def bench(sym, workers_list, limit=None):
    import threading, resource
    pop = [x for x in population() if x["sym"] == sym]
    if limit: pop = pop[:limit]
    d = os.path.join(OUTDIR, "_bench"); os.makedirs(d, exist_ok=True)
    print(f"{sym}: {len(pop)} trades x {len(grid())} combos", flush=True)
    for w in workers_list:
        ev = threading.Event(); lo = []; th = threading.Thread(target=mem_sampler, args=(ev, lo)); th.start()
        t = time.time(); run_pool(pop, os.path.join(d, f"w{w}.u8"), w); dt = time.time() - t
        ev.set(); th.join()
        print(f"  {w:2d} workers: {dt:6.1f}s  ({dt / len(pop) * 1000:.1f} ms/trade wall)  lowest MemAvailable {lo[0]:.2f} GB  "
              f"peak worker RSS {resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1e6:.2f} GB", flush=True)
    return len(pop)


def build(workers):
    pop = population(); combos = grid()
    run = time.strftime("%Y%m%d_%H%M"); d = os.path.join(OUTDIR, run); os.makedirs(d, exist_ok=True)
    t = time.time(); run_pool(pop, os.path.join(d, "results.u8"), workers, progress=True)
    meta = {"built": time.strftime("%Y-%m-%d %H:%M"), "mode": MODE, "minutes": round((time.time() - t) / 60, 1), "record": str(REC.descr),
            "dials": {"stage": STAGE, "bank": BANK, "pyr": PYR, "raise": RAISE, "cutB": CUTB, "addI": ADDI, "raise_trigger": SR_TRIG},
            "combos": combos, "trades": [[x["sym"], x["t"], x["strat"], int(x["up"])] for x in pop]}
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
    print(f"{len(pop)} trades x {len(combos)} combos in {meta['minutes']} min -> {d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true"); ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--bench", nargs="+"); ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--build", action="store_true"); ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    if a.verify: sys.exit(0 if verify(a.sample, a.workers) else 1)
    if a.bench: bench(a.bench[0], [int(w) for w in a.bench[1:]] or [4, 8, 12, 16], a.limit)
    if a.build: build(a.workers)
