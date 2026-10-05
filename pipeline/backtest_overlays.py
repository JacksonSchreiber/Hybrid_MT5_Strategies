#!/usr/bin/env python3
"""backtest_overlays.py - coach 2026-10-04 (overlays required on blind cards): the EA's per-detector chart overlays (zone, zone2, leg,
aux lines, labelled swings) rebuilt in Python from H4 bars up to the SIGNAL BAR only - a port of the detectors in
mql5/include/Hybrid/detectors/*.mqh (read-only), returning exactly what each Detect() writes into the candidate at the emit:
  TrendCont  zone = EMA20(signal bar) <-> the 3-bar pullback swing (bars signal-2..signal), from bar signal-2 to the signal bar;
             aux EMA20 (H4), pullback swing                                              (stateless: straight from the bars)
  DeepFib    leg L0 -> L100 (anchored), zone = the 0.618-0.786 band, from t100 to the signal bar; aux EMA50 (signal bar), TP2 ext
             1.618; labelled confirmed swings (6). L100 = the trade's TP1 and L0 follows from its TP2 (TP2 = L100 + 0.618 x leg), so
             the anchored leg is recovered EXACTLY from the levels; t0 / t100 = the newest strict fractal bars at those prices.
  EMArevQ    zone = frozen EMA20 <-> frozen EMA20 -/+ 1.5 x frozen ATR, from the extreme's bar to the signal bar; aux EMA20 (mean),
             lower band, upper band, stretch anchor. The frozen EMA = the trade's TP1; the frozen bar = the bar in the forming window
             (signal-4..signal) whose EMA20 matches it, which gives the extreme and the frozen ATR.
  SweepMSS   order block zone, aux swept pool, MSS level, sweep extreme, OB equilibrium, TP1 2R; labelled swings (6). The EA's
             IDLE -> FORMING (sweep) -> ARMED (MSS + OB) -> emit-on-pullback state machine is REPLAYED bar by bar from 120 bars before the
             signal; accepted only when the replay emits ON the signal bar with the trade's SL and TP.
Indicators: EMA seeded with the first close (as the cards), ATR = the SMA of the true range (MT5 iATR). DC_Buffer = max(0.1 x ATR,
spread) -> 0.1 x ATR (the spread is not known offline; on H4 the ATR term dominates); DC_Fill -> the trade's entry at the emit.
Every function returns (overlay, prov). Review 2 #11: the cards call them with Bars.upto(signal bar) and Periods/day_of built from
completed days + the partial signal day - arrays that physically END at the signal bar, so any read past it raises IndexError (the
old prov["max_i"] was a self-reported literal). prov["max_i"] is kept for information only.
"""
from __future__ import annotations
import datetime as dt
import numpy as np

FRACTAL_N = 2


def ema(c: np.ndarray, n: int) -> np.ndarray:
    k = 2 / (n + 1); out = np.empty(len(c)); p = c[0]
    for i, v in enumerate(c): p = v if i == 0 else p + k * (v - p); out[i] = p
    return out


def atr_sma(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); tr[0] = h[0] - l[0]
    cs = np.cumsum(tr); out = np.full(len(c), np.nan)
    out[n - 1:] = (cs[n - 1:] - np.concatenate([[0.0], cs[:-n]])) / n
    return out


class Bars:
    """oldest -> newest arrays + the indicators the detectors use (computed once per symbol)."""
    def __init__(self, t: list, o, h, l, c):
        self.t = t; self.o = np.asarray(o, float); self.h = np.asarray(h, float); self.l = np.asarray(l, float); self.c = np.asarray(c, float)
        self.e20 = ema(self.c, 20); self.e50 = ema(self.c, 50); self.atr = atr_sma(self.h, self.l, self.c, 14)

    def upto(self, i: int) -> "Bars":
        """a view holding only bars 0..i (review 2 #11). Every indicator at index k depends only on bars <= k, so slicing equals
        recomputing on the truncated history; a read at an index > i now raises IndexError instead of seeing the future."""
        V = Bars.__new__(Bars); n = i + 1
        V.t = self.t[:n]; V.o = self.o[:n]; V.h = self.h[:n]; V.l = self.l[:n]; V.c = self.c[:n]
        V.e20 = self.e20[:n]; V.e50 = self.e50[:n]; V.atr = self.atr[:n]
        return V


def _close(a, b, tol): return abs(a - b) <= tol


def is_sw_hi(B: Bars, j: int, n: int = FRACTAL_N) -> bool:
    if j - n < 0 or j + n >= len(B.h): return False
    return all(B.h[j] > B.h[j - k] and B.h[j] > B.h[j + k] for k in range(1, n + 1))


def is_sw_lo(B: Bars, j: int, n: int = FRACTAL_N) -> bool:
    if j - n < 0 or j + n >= len(B.l): return False
    return all(B.l[j] < B.l[j - k] and B.l[j] < B.l[j + k] for k in range(1, n + 1))


def collect_swings(B: Bars, isig: int, maxc: int, nbars: int, cap: int):
    """DC_CollectSwings at the signal bar: series r[] = the newest `nbars` bars with r[0] = the bar AFTER the signal (forming, never
    read) and r[1] = the signal bar; centres c = 1+n .. min(maxc, nbars-1-n); a centre's right neighbours are all <= the signal bar.
    -> newest first [(bar index, price)] for highs and lows."""
    hi, lo = [], []
    last = min(maxc, nbars - 1 - FRACTAL_N)
    for cc in range(1 + FRACTAL_N, last + 1):
        j = isig + 1 - cc
        if j - FRACTAL_N < 0: break
        if len(hi) < cap and _sw_hi_upto(B, j, isig): hi.append((j, B.h[j]))
        if len(lo) < cap and _sw_lo_upto(B, j, isig): lo.append((j, B.l[j]))
        if len(hi) >= cap and len(lo) >= cap: break
    return hi, lo


def _sw_hi_upto(B, j, isig):   # a fractal judged only on bars <= isig (the series window never includes the forming bar's right side)
    return j + FRACTAL_N <= isig and is_sw_hi(B, j)


def _sw_lo_upto(B, j, isig):
    return j + FRACTAL_N <= isig and is_sw_lo(B, j)


# --------------------------------------------------------------------------------------------------------------- TrendCont
def trendcont(B: Bars, isig: int, up: bool, lv: dict) -> tuple[dict, dict]:
    e20 = float(B.e20[isig]); j0 = isig - 2
    swing = float(B.l[j0:isig + 1].min()) if up else float(B.h[j0:isig + 1].max())
    z = {"i_from": j0, "i_to": isig, "hi": e20 if up else swing, "lo": swing if up else e20}
    buf = (swing - lv["sl"]) if up else (lv["sl"] - swing)
    atrb = float(B.atr[isig])
    return ({"zone": z, "aux": [(e20, "EMA20 (H4)"), (swing, "pullback swing")]},
            {"max_i": isig, "ok": True, "sl_buf_atr": buf / atrb if atrb > 0 else None})


# --------------------------------------------------------------------------------------------------------------- DeepFib
def deepfib(B: Bars, isig: int, up: bool, lv: dict, tol: float) -> tuple[dict | None, dict]:
    L100 = lv["tp1"]; tp2 = lv["tp2"]
    if not L100 or not tp2: return None, {"max_i": isig, "ok": False, "why": "no tp1/tp2"}
    L0 = L100 - (tp2 - L100) / 0.618 if up else L100 + (L100 - tp2) / 0.618
    lo_win = max(0, isig + 1 - 89)                                          # CopyRates(..., 90): r[0..89]
    j100 = next((j for j in range(isig - FRACTAL_N, lo_win - 1, -1)
                 if ((_close(B.h[j], L100, tol) and _sw_hi_upto(B, j, isig)) if up else (_close(B.l[j], L100, tol) and _sw_lo_upto(B, j, isig)))), None)
    if j100 is None: return None, {"max_i": isig, "ok": False, "why": "no fractal at L100"}
    j0 = next((j for j in range(j100 - 1, lo_win - 1, -1)
               if ((_close(B.l[j], L0, tol * 3) and is_sw_lo(B, j)) if up else (_close(B.h[j], L0, tol * 3) and is_sw_hi(B, j)))), None)
    if j0 is None: return None, {"max_i": isig, "ok": False, "why": "no fractal at L0"}
    L0 = float(B.l[j0] if up else B.h[j0])                                  # the bar's exact price (TP2 is rounded to digits)
    lvl = lambda f: L100 + f * (L0 - L100)
    b618, b786 = lvl(0.618), lvl(0.786)
    hi, lo = collect_swings(B, isig, 75, 90, 6)
    ov = {"zone": {"i_from": j100, "i_to": isig, "hi": max(b618, b786), "lo": min(b618, b786)},
          "leg": {"i0": j0, "p0": L0, "i1": j100, "p1": L100},
          "aux": [(float(B.e50[isig]), "EMA50"), (tp2, "TP2 ext 1.618")], "swings_hi": hi, "swings_lo": lo}
    return ov, {"max_i": isig, "ok": True, "tp2_err_R": abs((L100 + (0.618 if up else -0.618) * abs(L100 - L0)) - tp2) / abs(lv["entry"] - lv["sl"])}


# --------------------------------------------------------------------------------------------------------------- EMArevQ
STRETCH_Q = 1.5


def emarevq(B: Bars, isig: int, up: bool, lv: dict, tol: float) -> tuple[dict | None, dict]:
    ef = lv["tp1"]
    if not ef: return None, {"max_i": isig, "ok": False, "why": "no tp1"}
    ks = [k for k in range(max(1, isig - 4), isig + 1)]
    k = min(ks, key=lambda k: abs(B.e20[k] - ef))
    if abs(B.e20[k] - ef) > max(tol * 20, 2e-5 * abs(ef)): return None, {"max_i": isig, "ok": False, "why": "no bar with EMA20 = TP1"}
    ext = float(B.l[k] if up else B.h[k]); af = float(B.atr[k])
    lowb, upb = ef - STRETCH_Q * af, ef + STRETCH_Q * af
    z = {"i_from": k, "i_to": isig, "hi": ef if up else upb, "lo": lowb if up else ef}
    R = abs(lv["entry"] - lv["sl"]); sl_model = ext - 0.1 * af if up else ext + 0.1 * af
    return ({"zone": z, "aux": [(ef, "EMA20 (mean)"), (lowb, "lower band"), (upb, "upper band"), (ext, "stretch anchor")]},
            {"max_i": isig, "ok": True, "sl_err_R": abs(sl_model - lv["sl"]) / R if R > 0 else None})


# --------------------------------------------------------------------------------------------------------------- SweepMSS
class Periods:
    """previous completed D1 / W1 high-low at a given 'now' (the open of the bar after the evaluated bar) - iHigh/iLow(D1|W1, 1)."""
    def __init__(self, days: list):          # [(date 'YYYY.MM.DD', high, low)] oldest -> newest
        self.days = days; self.keys = [d[0] for d in days]
        wk: dict = {}
        for d, h, l in days:
            y, m, dd = map(int, d.split(".")); dd0 = dt.date(y, m, dd); sun = dd0 - dt.timedelta(days=(dd0.weekday() + 1) % 7)
            w = sun.strftime("%Y.%m.%d"); a = wk.get(w)
            wk[w] = (max(a[0], h), min(a[1], l)) if a else (h, l)
        self.weeks = sorted(wk.items())

    def prev(self, now_day: str):
        import bisect
        i = bisect.bisect_left(self.keys, now_day) - 1
        d = self.days[i] if i >= 0 else None
        y, m, dd = map(int, now_day.split(".")); dd0 = dt.date(y, m, dd); sun = (dd0 - dt.timedelta(days=(dd0.weekday() + 1) % 7)).strftime("%Y.%m.%d")
        wi = bisect.bisect_left([w for w, _ in self.weeks], sun) - 1
        w = self.weeks[wi][1] if wi >= 0 else None
        return (d[1], d[2]) if d else (0.0, 0.0), w if w else (0.0, 0.0)


class SMC:
    LIQ_LOOKBACK, SWEEP_MAX_BARS, MSS_MAX_BARS, ENTRY_VALID_BARS = 20, 1, 6, 8
    EQ_TOL_ATR, SWEEP_MIN_ATR, SWEEP_MAX_ATR, SL_BUFFER_ATR, MIN_RR, TP_R, TP1_R = 0.10, 0.05, 1.2, 0.10, 2.0, 3.0, 2.0

    def __init__(self, B: Bars, P: Periods, day_of):
        self.B, self.P, self.day_of = B, P, day_of; self.reset()

    def reset(self):
        self.state = 0; self.dir = 0; self.pool = 0.0; self.sweep_ext = 0.0; self.atr_f = 0.0; self.ref = 0.0; self.pool_type = 0
        self.bss = 0; self.ob_hi = self.ob_lo = 0.0; self.ob_i = -1; self.bsa = 0

    def swings(self, b: int, maxc: int):
        need = self.LIQ_LOOKBACK + self.MSS_MAX_BARS + 2 * FRACTAL_N + 10
        return collect_swings(self.B, b, maxc, need, 24)

    def opposing(self, d: int, entry: float, hi, lo, pdh, pdl, pwh, pwl) -> float:
        best = 0.0
        if d > 0:
            for _, p in hi:
                if p > entry and (best == 0.0 or p < best): best = p
            for p in (pdh, pwh):
                if p > entry and (best == 0.0 or p < best): best = p
        else:
            for _, p in lo:
                if p < entry and (best == 0.0 or p > best): best = p
            for p in (pdl, pwl):
                if p < entry and (best == 0.0 or p > best): best = p
        return best

    def step(self, b: int, entry_at_emit: float | None = None):
        """one Detect() with r[1] = bar b; returns the emit dict or None."""
        B = self.B; atr = B.atr[b]
        if not (atr > 0): return None
        hi, lo = self.swings(b, self.LIQ_LOOKBACK + self.MSS_MAX_BARS + 2)
        buf = self.SL_BUFFER_ATR * (self.atr_f if self.atr_f > 0 else atr)
        now_day = self.day_of(b + 1)
        (pdh, pdl), (pwh, pwl) = self.P.prev(now_day)
        if self.state == 2:
            self.bsa += 1
            dead = B.c[b] < self.sweep_ext - buf if self.dir > 0 else B.c[b] > self.sweep_ext + buf
            mit = B.c[b] < self.ob_lo if self.dir > 0 else B.c[b] > self.ob_hi
            if dead or mit or self.bsa > self.ENTRY_VALID_BARS: self.reset(); return None
            prox = self.ob_hi if self.dir > 0 else self.ob_lo
            pulled = B.l[b] <= prox if self.dir > 0 else B.h[b] >= prox
            if not pulled: return None
            return self.emit(b, hi, lo, pdh, pdl, pwh, pwl, entry_at_emit)
        if self.state == 1:
            self.bss += 1
            if self.dir > 0 and B.c[b] < self.sweep_ext - buf: self.reset(); return None
            if self.dir < 0 and B.c[b] > self.sweep_ext + buf: self.reset(); return None
            if self.bss > self.MSS_MAX_BARS: self.reset(); return None
            mss = B.c[b] > self.ref if self.dir > 0 else B.c[b] < self.ref
            if not mss: return None
            if abs(B.c[b] - B.o[b]) < 0.5 * self.atr_f: self.reset(); return None
            ob = -1
            for k in range(1, self.MSS_MAX_BARS + 3):
                j = b + 1 - k
                if j < 0: break
                if (self.dir > 0 and B.c[j] < B.o[j]) or (self.dir < 0 and B.c[j] > B.o[j]): ob = j; break
            if ob < 0: self.reset(); return None
            self.ob_hi, self.ob_lo, self.ob_i = float(B.h[ob]), float(B.l[ob]), ob
            prox = self.ob_hi if self.dir > 0 else self.ob_lo
            sl = self.sweep_ext - buf if self.dir > 0 else self.sweep_ext + buf
            risk = prox - sl if self.dir > 0 else sl - prox
            if risk <= 0: self.reset(); return None
            liq = self.opposing(self.dir, prox, hi, lo, pdh, pdl, pwh, pwl)
            ok_liq = liq > 0 and ((liq - prox) if self.dir > 0 else (prox - liq)) >= self.MIN_RR * risk
            tp = liq if ok_liq else (prox + self.TP_R * risk if self.dir > 0 else prox - self.TP_R * risk)
            rr = ((tp - prox) if self.dir > 0 else (prox - tp)) / risk
            if rr < self.MIN_RR: self.reset(); return None
            self.state = 2; self.bsa = 0
            return None
        for d, sw, (lvA, lvB) in ((+1, lo, (pdl, pwl)), (-1, hi, (pdh, pwh))):
            if self.try_sweep(b, d, sw, lvA, lvB, atr): return None
        return None

    def try_sweep(self, b, d, sw, lvA, lvB, atr) -> bool:
        B = self.B
        cand = [(p, 0) for (j, p) in sw if (b + 1 - j) <= self.LIQ_LOOKBACK + FRACTAL_N][:24]
        if lvA > 0: cand.append((lvA, 2))
        if lvB > 0: cand.append((lvB, 3))
        for pool, typ in cand:
            pen = B.l[b] < pool if d > 0 else B.h[b] > pool
            rec = B.c[b] > pool if d > 0 else B.c[b] < pool
            depth = (pool - B.l[b]) if d > 0 else (B.h[b] - pool)
            if not pen or not rec: continue
            if depth < self.SWEEP_MIN_ATR * atr or depth > self.SWEEP_MAX_ATR * atr: continue
            self.state = 1; self.dir = d; self.atr_f = atr
            self.sweep_ext = float(B.l[b] if d > 0 else B.h[b]); self.pool = float(pool); self.pool_type = typ; self.bss = 0
            ref = 0.0
            need = self.LIQ_LOOKBACK + self.MSS_MAX_BARS + 2 * FRACTAL_N + 10
            for cc in range(1 + FRACTAL_N, need - FRACTAL_N):
                j = b + 1 - cc
                if j - FRACTAL_N < 0: break
                if d > 0 and is_sw_hi(B, j): ref = float(B.h[j]); break
                if d < 0 and is_sw_lo(B, j): ref = float(B.l[j]); break
            if ref == 0.0: self.reset(); return False
            self.ref = ref
            return True
        return False

    def emit(self, b, hi, lo, pdh, pdl, pwh, pwl, entry_at_emit):
        B = self.B; d = self.dir
        buf = self.SL_BUFFER_ATR * self.atr_f
        entry = entry_at_emit if entry_at_emit is not None else float(B.c[b])
        sl = self.sweep_ext - buf if d > 0 else self.sweep_ext + buf
        risk = entry - sl if d > 0 else sl - entry
        if risk <= 0: self.reset(); return None
        liq = self.opposing(d, entry, hi, lo, pdh, pdl, pwh, pwl)
        ok_liq = liq > 0 and ((liq - entry) if d > 0 else (entry - liq)) >= self.MIN_RR * risk
        tp = liq if ok_liq else (entry + self.TP_R * risk if d > 0 else entry - self.TP_R * risk)
        rr = ((tp - entry) if d > 0 else (entry - tp)) / risk
        if rr < self.MIN_RR: self.reset(); return None
        tp1 = entry + self.TP1_R * risk if d > 0 else entry - self.TP1_R * risk
        typ = {0: "swing", 1: "EQL", 2: "PD-level", 3: "PW-level"}[self.pool_type]
        out = {"b": b, "dir": d, "entry": entry, "sl": sl, "tp": tp, "tp1": tp1,
               "zone": {"i_from": self.ob_i, "i_to": b, "hi": self.ob_hi, "lo": self.ob_lo},
               "aux": [(self.pool, "swept " + typ), (self.ref, "MSS level"), (self.sweep_ext, "sweep low" if d > 0 else "sweep high"),
                       ((self.ob_hi + self.ob_lo) / 2.0, "OB equilibrium"), (tp1, "TP1 2R")]}
        self.reset()
        return out


def sweepmss(B: Bars, P: Periods, day_of, isig: int, up: bool, lv: dict, back: int = 120) -> tuple[dict | None, dict]:
    m = SMC(B, P, day_of); hit = None
    for b in range(max(45, isig - back), isig + 1):
        r = m.step(b, entry_at_emit=lv["entry"] if b == isig else None)
        if r and b == isig: hit = r
    if not hit: return None, {"max_i": isig, "ok": False, "why": "replay did not emit on the signal bar"}
    R = abs(lv["entry"] - lv["sl"]); want_dir = 1 if up else -1
    if hit["dir"] != want_dir: return None, {"max_i": isig, "ok": False, "why": "replay emitted the other direction"}
    sl_err = abs(hit["sl"] - lv["sl"]) / R
    tp_err = abs(hit["tp"] - lv["tp2"]) / R if lv.get("tp2") else None
    hi, lo = collect_swings(B, isig, SMC.LIQ_LOOKBACK + SMC.MSS_MAX_BARS + 2, SMC.LIQ_LOOKBACK + SMC.MSS_MAX_BARS + 2 * FRACTAL_N + 10, 6)
    ov = {"zone": hit["zone"], "aux": hit["aux"], "swings_hi": hi, "swings_lo": lo}
    ok = sl_err < 0.05 and (tp_err is None or tp_err < 0.05)
    return (ov if ok else None), {"max_i": isig, "ok": ok, "sl_err_R": sl_err, "tp_err_R": tp_err, "why": "" if ok else "SL/TP mismatch"}


def overlay_for(det: str, B: Bars, isig: int, up: bool, lv: dict, tol: float, P: Periods | None = None, day_of=None):
    if det == "TrendCont": return trendcont(B, isig, up, lv)
    if det == "DeepFib": return deepfib(B, isig, up, lv, tol)
    if det == "EMArevQ": return emarevq(B, isig, up, lv, tol)
    if det == "SweepMSS": return sweepmss(B, P, day_of, isig, up, lv)
    return None, {"max_i": isig, "ok": False, "why": f"unknown detector {det}"}
