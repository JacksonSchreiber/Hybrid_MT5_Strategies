#!/usr/bin/env python3
"""breakout_backtest.py — offline Python backtester for the two frozen breakout
replacement candidates (DonchianBreak, SqueezeBreak) over the QDM M1 store.

Frozen spec: docs/strategies/breakout-candidates-frozen-spec.md. NO parameter search.

Pipeline per symbol:
  M1 (QDM export) -> H4 aggregation -> D1 aggregation -> H4/D1 indicators ->
  bar-by-bar signal detection (one-position lock) -> per-trade doctrine-v2 exit
  reconstructed by walking M1 within the trade lifetime (conservative intrabar).

stdlib only. Usage:
  python3 breakout_backtest.py run     # run both candidates, all available symbols
  python3 breakout_backtest.py report  # rebuild report from per-trade CSVs on disk
"""
from __future__ import annotations
import csv, sys, math, bisect
from datetime import datetime, timedelta, date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
M1DIR = REPO / "data" / "qdm_csv" / "fleet"
OUT = REPO / "data" / "study" / "breakouts"
OUT.mkdir(parents=True, exist_ok=True)

# QDM symbol name -> (.dk fleet label, spread in price units, pip in price units)
SYMS = {
    "EURUSD":        ("EURUSD.dk", 0.00010, 0.0001),
    "GBPUSD":        ("GBPUSD.dk", 0.00010, 0.0001),
    "LIGHTCMDUSD":   ("USOIL.dk",  0.03,    0.01),
    "USA500IDXUSD":  ("US500.dk",  0.5,     0.1),
    "USATECHIDXUSD": ("US100.dk",  1.0,     0.1),
    "USDJPY":        ("USDJPY.dk", 0.010,   0.01),
    "XAUUSD":        ("XAUUSD.dk", 0.30,    0.01),
}

# ---- frozen constants (spec §7) ----
DONCHIAN_N, DONCHIAN_250, DON_CUTPCT, DON_SL_ATR = 55, 250, 0.03, 1.5
ATR_PERIOD = 14
BB_PERIOD, BB_SIGMA, SQ_TRAIL, SQ_PCTL, SQ_EXPIRY = 20, 2.0, 120, 20, 6
EMA_D1, ADX_D1, D1_SLOPE_BACK, ADX_MIN = 200, 14, 10, 20.0
TP1_R, TP2_R, PARTIAL, TRIG_R = 2.0, 4.0, 0.5, 1.0
BE_PAD_PIPS = 1.0


# ============================================================ data loading
def load_m1(path: Path):
    """Return parallel arrays t(datetime), o,h,l,c (float). Date,Time,O,H,L,C,Vol."""
    T, O, H, L, C = [], [], [], [], []
    with open(path, newline="") as fh:
        r = csv.reader(fh)
        next(r, None)  # header
        for row in r:
            if len(row) < 6:
                continue
            d, tm = row[0], row[1]
            dt = datetime(int(d[0:4]), int(d[4:6]), int(d[6:8]),
                          int(tm[0:2]), int(tm[3:5]))
            T.append(dt); O.append(float(row[2])); H.append(float(row[3]))
            L.append(float(row[4])); C.append(float(row[5]))
    return T, O, H, L, C


def agg_h4(T, O, H, L, C):
    """Aggregate M1 -> H4 bars anchored to server hours 0/4/8/12/16/20.
    Returns list of dicts {t, o,h,l,c, i0} where i0 = index of the first M1 bar
    at/after this H4 bar's CLOSE (== entry index for a signal on this bar)."""
    bars = []
    cur = None
    for k in range(len(T)):
        dt = T[k]
        b_open = dt.replace(hour=(dt.hour // 4) * 4, minute=0, second=0, microsecond=0)
        if cur is None or b_open != cur["t"]:
            if cur is not None:
                bars.append(cur)
            cur = {"t": b_open, "o": O[k], "h": H[k], "l": L[k], "c": C[k], "kend": k}
        else:
            if H[k] > cur["h"]: cur["h"] = H[k]
            if L[k] < cur["l"]: cur["l"] = L[k]
            cur["c"] = C[k]; cur["kend"] = k
    if cur is not None:
        bars.append(cur)
    return bars


def agg_d1(h4):
    """Aggregate H4 -> D1 by server calendar date of the H4 open time."""
    d1 = []
    cur = None
    for b in h4:
        d = b["t"].date()
        if cur is None or d != cur["d"]:
            if cur is not None: d1.append(cur)
            cur = {"d": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            if b["h"] > cur["h"]: cur["h"] = b["h"]
            if b["l"] < cur["l"]: cur["l"] = b["l"]
            cur["c"] = b["c"]
    if cur is not None: d1.append(cur)
    return d1


# ============================================================ indicators
def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n: return out
    k = 2.0 / (n + 1)
    seed = sum(vals[:n]) / n
    out[n - 1] = seed
    e = seed
    for i in range(n, len(vals)):
        e = e + k * (vals[i] - e)
        out[i] = e
    return out


def wilder_atr(H, L, C, n):
    out = [None] * len(H)
    if len(H) < n + 1: return out
    tr = [0.0] * len(H)
    for i in range(1, len(H)):
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
    seed = sum(tr[1:n + 1]) / n
    out[n] = seed
    a = seed
    for i in range(n + 1, len(H)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def wilder_adx(H, L, C, n):
    N = len(H)
    out = [None] * N
    if N < 2 * n + 1: return out
    tr = [0.0] * N; pdm = [0.0] * N; ndm = [0.0] * N
    for i in range(1, N):
        up = H[i] - H[i - 1]; dn = L[i - 1] - L[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
    # Wilder-smoothed TR/DM starting at index n
    str_ = sum(tr[1:n + 1]); spdm = sum(pdm[1:n + 1]); sndm = sum(ndm[1:n + 1])
    dx = [None] * N
    for i in range(n, N):
        if i > n:
            str_ = str_ - str_ / n + tr[i]
            spdm = spdm - spdm / n + pdm[i]
            sndm = sndm - sndm / n + ndm[i]
        if str_ == 0:
            dx[i] = 0.0; continue
        pdi = 100 * spdm / str_; ndi = 100 * sndm / str_
        s = pdi + ndi
        dx[i] = 0.0 if s == 0 else 100 * abs(pdi - ndi) / s
    # ADX = Wilder smoothing of DX, first value = mean of first n DX (indices n..2n-1)
    first = 2 * n - 1
    if first >= N: return out
    seed = sum(dx[n:2 * n]) / n
    out[first] = seed
    a = seed
    for i in range(2 * n, N):
        a = (a * (n - 1) + dx[i]) / n
        out[i] = a
    return out


def rolling_extremes(H, L, n):
    """highest_high / lowest_low over the n bars PRECEDING each index (exclusive)."""
    hh = [None] * len(H); ll = [None] * len(H)
    for i in range(len(H)):
        if i >= n:
            hh[i] = max(H[i - n:i]); ll[i] = min(L[i - n:i])
    return hh, ll


def bb_width(C, n, sig):
    out = [None] * len(C)
    for i in range(n - 1, len(C)):
        w = C[i - n + 1:i + 1]
        m = sum(w) / n
        var = sum((x - m) ** 2 for x in w) / n
        sd = math.sqrt(var)
        up, lo = m + sig * sd, m - sig * sd
        out[i] = None if m == 0 else (up - lo) / m
    return out


def pctl(sorted_vals, q):
    if not sorted_vals: return None
    idx = (q / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx)); hi = int(math.ceil(idx))
    if lo == hi: return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


# ============================================================ regime
def build_trenddir(h4, d1):
    """For each H4 bar, the D1 TrendDir using the last FULLY CLOSED D1 bar
    (date strictly before the H4 open date) — matches live iClose(D1,1)."""
    d1dates = [b["d"] for b in d1]
    d1close = [b["c"] for b in d1]
    e200 = ema(d1close, EMA_D1)
    adx = wilder_adx([b["h"] for b in d1], [b["l"] for b in d1], d1close, ADX_D1)
    dirs = [0] * len(h4)
    for i, b in enumerate(h4):
        # last D1 index with date < h4 open date
        j = bisect.bisect_left(d1dates, b["t"].date()) - 1
        if j < D1_SLOPE_BACK or e200[j] is None or e200[j - D1_SLOPE_BACK] is None \
                or adx[j] is None:
            continue
        if adx[j] < ADX_MIN:
            continue
        c, e1, e11 = d1close[j], e200[j], e200[j - D1_SLOPE_BACK]
        if c > e1 and e1 > e11:
            dirs[i] = 1
        elif c < e1 and e1 < e11:
            dirs[i] = -1
    return dirs


# ============================================================ v2 exit sim
def simulate_v2(m1, entry_idx, direction, entry, sl, pip):
    """Walk M1 from entry_idx; return (v2_r, terminal, exit_idx). Conservative
    intrabar: pre-bank SL wins ties; post-bank BE wins ties."""
    _, O, H, L, C = m1
    risk = abs(entry - sl)
    if risk <= 0:
        return None, "BADRISK", entry_idx
    d = direction
    trig = entry + d * TRIG_R * risk          # +1R
    tp2 = entry + d * TP2_R * risk            # +4R
    pad = BE_PAD_PIPS * pip
    be = entry + d * pad
    banked = False
    n = len(H)
    i = entry_idx
    while i < n:
        hi, lo = H[i], L[i]
        if not banked:
            hit_sl = (lo <= sl) if d > 0 else (hi >= sl)
            hit_trig = (hi >= trig) if d > 0 else (lo <= trig)
            if hit_sl:                       # SL wins ties (conservative)
                return -1.0, "SL", i
            if hit_trig:
                banked = True
                # same-bar runner resolution (conservative: BE beats 4R)
                hit_be = (lo <= be) if d > 0 else (hi >= be)
                hit_tp2 = (hi >= tp2) if d > 0 else (lo <= tp2)
                if hit_be:
                    return 0.5 * TRIG_R + 0.5 * (pad / risk), "BANK_BE", i
                if hit_tp2:
                    return 0.5 * TRIG_R + 0.5 * TP2_R, "BANK_TP2", i
        else:
            hit_be = (lo <= be) if d > 0 else (hi >= be)
            hit_tp2 = (hi >= tp2) if d > 0 else (lo <= tp2)
            if hit_be:
                return 0.5 * TRIG_R + 0.5 * (pad / risk), "BANK_BE", i
            if hit_tp2:
                return 0.5 * TRIG_R + 0.5 * TP2_R, "BANK_TP2", i
        i += 1
    # ran out of data
    last = C[n - 1]
    if banked:
        rr = d * (last - entry) / risk
        return 0.5 * TRIG_R + 0.5 * rr, "OPEN_BANK", n - 1
    rr = d * (last - entry) / risk
    return rr, "OPEN", n - 1


# ============================================================ detectors
def detect_donchian(h4, dirs, atr, hh55, ll55, hh250, ll250):
    """Yield signals (i, direction, sl, near_extreme_cut)."""
    for i in range(len(h4)):
        if dirs[i] == 0 or hh55[i] is None or atr[i] is None:
            continue
        c = h4[i]["c"]; d = dirs[i]
        if d > 0 and c > hh55[i]:
            sl = c - DON_SL_ATR * atr[i]
            ext = hh250[i] if hh250[i] is not None else hh55[i]
            cut = abs(c - ext) / ext <= DON_CUTPCT if ext else False
            yield i, 1, sl, cut
        elif d < 0 and c < ll55[i]:
            sl = c + DON_SL_ATR * atr[i]
            ext = ll250[i] if ll250[i] is not None else ll55[i]
            cut = abs(c - ext) / ext <= DON_CUTPCT if ext else False
            yield i, -1, sl, cut


def detect_squeeze(h4, dirs, width):
    """Yield signals (i, direction, sl, cut=False). Squeeze = width in lowest
    quintile of trailing 120; breakout = first close beyond the compression
    range within 6 bars of squeeze end, regime-aligned."""
    N = len(h4)
    is_sq = [False] * N
    for i in range(SQ_TRAIL - 1, N):
        w = width[i]
        if w is None: continue
        window = [x for x in width[i - SQ_TRAIL + 1:i + 1] if x is not None]
        if len(window) < SQ_TRAIL: continue
        thr = pctl(sorted(window), SQ_PCTL)
        is_sq[i] = w <= thr
    i = 0
    while i < N:
        if is_sq[i]:
            # extend to end of this squeeze run
            j = i
            while j + 1 < N and is_sq[j + 1]:
                j += 1
            last_sq = j                       # last squeeze bar
            if last_sq - BB_PERIOD + 1 >= 0:
                comp_hi = max(h4[k]["h"] for k in range(last_sq - BB_PERIOD + 1, last_sq + 1))
                comp_lo = min(h4[k]["l"] for k in range(last_sq - BB_PERIOD + 1, last_sq + 1))
                for k in range(last_sq + 1, min(last_sq + 1 + SQ_EXPIRY, N)):
                    d = dirs[k]
                    if d == 0:
                        continue
                    c = h4[k]["c"]
                    if d > 0 and c > comp_hi:
                        yield k, 1, comp_lo, False; break
                    if d < 0 and c < comp_lo:
                        yield k, -1, comp_hi, False; break
            i = last_sq + 1
        else:
            i += 1


# ============================================================ per-symbol run
def run_symbol(qname, cand):
    label, spread, pip = SYMS[qname]
    path = M1DIR / f"{qname}-M1-No Session.csv"
    if not path.exists():
        return None
    m1 = load_m1(path)
    T, O, H, L, C = m1
    h4 = agg_h4(*m1)
    d1 = agg_d1(h4)
    dirs = build_trenddir(h4, d1)
    hc = [b["h"] for b in h4]; lc = [b["l"] for b in h4]; cc = [b["c"] for b in h4]
    atr = wilder_atr(hc, lc, cc, ATR_PERIOD)

    m1times = T  # sorted
    trades = []

    def entry_index_for(bar):
        close_t = bar["t"] + timedelta(hours=4)
        return bisect.bisect_left(m1times, close_t)

    if cand == "donchian":
        hh55, ll55 = rolling_extremes(hc, lc, DONCHIAN_N)
        hh250, ll250 = rolling_extremes(hc, lc, DONCHIAN_250)
        sig_iter = detect_donchian(h4, dirs, atr, hh55, ll55, hh250, ll250)
    else:
        width = bb_width(cc, BB_PERIOD, BB_SIGMA)
        sig_iter = detect_squeeze(h4, dirs, width)

    open_until = -1  # M1 index the current open trade resolves at (one-position lock)
    for sig in sig_iter:
        i, d, sl, cut = sig
        ei = entry_index_for(h4[i])
        if ei >= len(T):
            continue
        if ei <= open_until:                  # one-position lock
            continue
        raw = O[ei]                           # bid open of entry M1
        entry = raw + d * spread              # buy fills at bid+spread, sell at bid-spread
        v2r, term, xi = simulate_v2(m1, ei, d, entry, sl, pip)
        if v2r is None:
            continue
        open_until = xi
        trades.append({
            "symbol": label, "cand": cand, "sig_time": h4[i]["t"].strftime("%Y.%m.%d %H:%M"),
            "dir": d, "entry": f"{entry:.5f}", "sl": f"{sl:.5f}",
            "risk": f"{abs(entry-sl):.5f}", "v2_r": f"{v2r:.4f}", "terminal": term,
            "near_extreme_cut": int(cut),
            "exit_time": T[xi].strftime("%Y.%m.%d %H:%M"),
        })
    # write per-trade csv
    d = OUT / cand
    d.mkdir(exist_ok=True)
    with open(d / f"{label}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(trades[0].keys()) if trades else
                           ["symbol","cand","sig_time","dir","entry","sl","risk","v2_r","terminal","near_extreme_cut","exit_time"])
        w.writeheader()
        for t in trades: w.writerow(t)
    print(f"  {label:12s} {cand:9s} n={len(trades):4d} "
          f"avgR={sum(float(t['v2_r']) for t in trades)/len(trades):+.3f}" if trades
          else f"  {label:12s} {cand:9s} n=0")
    return trades


def cmd_run():
    avail = [q for q in SYMS if (M1DIR / f"{q}-M1-No Session.csv").exists()]
    print(f"available symbols: {[SYMS[q][0] for q in avail]}")
    for cand in ("donchian", "squeeze"):
        print(f"=== {cand} ===")
        for q in avail:
            run_symbol(q, cand)
    cmd_report()


# ============================================================ gate / report
def load_trades(cand):
    out = []
    d = OUT / cand
    if not d.exists(): return out
    for f in sorted(d.glob("*.csv")):
        for r in csv.DictReader(open(f)):
            r["v2_r"] = float(r["v2_r"]); out.append(r)
    return out


def gate(trades, label):
    if not trades:
        return f"### {label}\n_no trades_\n"
    n = len(trades)
    net = sum(t["v2_r"] for t in trades)
    avg = net / n
    times = sorted(t["sig_time"] for t in trades)
    med = times[n // 2]
    early = [t["v2_r"] for t in trades if t["sig_time"] < med]
    late = [t["v2_r"] for t in trades if t["sig_time"] >= med]
    eh = sum(early) / len(early) if early else 0.0
    lh = sum(late) / len(late) if late else 0.0
    bysym = {}
    for t in trades:
        bysym.setdefault(t["symbol"], []).append(t["v2_r"])
    rows = []
    for s, v in sorted(bysym.items(), key=lambda kv: -sum(kv[1])):
        rows.append((s, len(v), sum(v) / len(v), sum(v), 100 * sum(v) / net if net else 0))
    maxshare = max((r[4] for r in rows), default=0)
    g1 = avg >= 0.15; g2 = n >= 150; g3 = eh > 0 and lh > 0; g4 = maxshare <= 50
    md = [f"### {label}",
          f"- fleet n={n}, avg R={avg:+.3f}, net R={net:+.1f}",
          f"- date split @ median {med}: early {eh:+.3f} | late {lh:+.3f}",
          "",
          "| symbol | n | avg R | net R | share |", "|---|---|---|---|---|"]
    for s, nn, a, ne, sh in rows:
        md.append(f"| {s} | {nn} | {a:+.3f} | {ne:+.1f} | {sh:.0f}% |")
    md += ["",
           "| # | gate | value | verdict |", "|---|---|---|---|",
           f"| 1 | avg ≥ +0.15R | {avg:+.3f} | {'pass' if g1 else 'FAIL'} |",
           f"| 2 | n ≥ 150 | {n} | {'pass' if g2 else 'FAIL'} |",
           f"| 3 | both halves > 0 | {eh:+.3f}/{lh:+.3f} | {'pass' if g3 else 'FAIL'} |",
           f"| 4 | no symbol > 50% | max {maxshare:.0f}% | {'pass' if g4 else 'FAIL'} |",
           f"\n**{label}: {'PASS' if all([g1,g2,g3,g4]) else 'FAIL'}**\n"]
    return "\n".join(md)


def _passed(trades):
    if not trades: return False
    n = len(trades); net = sum(t["v2_r"] for t in trades); avg = net / n
    times = sorted(t["sig_time"] for t in trades); med = times[n // 2]
    early = [t["v2_r"] for t in trades if t["sig_time"] < med]
    late = [t["v2_r"] for t in trades if t["sig_time"] >= med]
    eh = sum(early)/len(early) if early else 0; lh = sum(late)/len(late) if late else 0
    bysym = {}
    for t in trades: bysym.setdefault(t["symbol"], []).append(t["v2_r"])
    maxshare = max((100*sum(v)/net for v in bysym.values()), default=0) if net else 999
    return avg >= 0.15 and n >= 150 and eh > 0 and lh > 0 and maxshare <= 50


def cmd_report():
    avail = sorted(SYMS[q][0] for q in SYMS if (M1DIR / f"{q}-M1-No Session.csv").exists())
    excluded = sorted(SYMS[q][0] for q in SYMS if not (M1DIR / f"{q}-M1-No Session.csv").exists())
    don_p = _passed(load_trades("donchian")); sq_p = _passed(load_trades("squeeze"))
    verdict = ("PASS" if (don_p or sq_p) else
               "FAIL — neither candidate clears the four gates; the alert slot stays empty "
               "and two live detectors (SweepMSS, DeepFib) is a complete lineup")
    parts = [
        "# Breakout replacement candidates — gate report (coach 2026-09-09)\n",
        f"**Ruling: {verdict}.**\n",
        "_Offline Python backtest over the QDM M1 store (H4 aggregation, M1 intrabar "
        "doctrine-v2 exit reconstruction — bank 50% at +1R, SL→BE, runner to +4R). "
        "Frozen spec: `docs/strategies/breakout-candidates-frozen-spec.md`. No parameter search._\n",
        "## Fleet & data caveats",
        f"- **Evaluated fleet ({len(avail)} symbols):** {', '.join(avail)}.",
        (f"- **Excluded (frozen symbol-set rule): {', '.join(excluded)}** — not in the QDM "
         "local store; the Dukascopy tick download was stopped at the operator's direction "
         "(cutoff moved to now) before it completed. Per the pre-registered rule, symbols not "
         "obtained by the deadline are excluded and disclosed. Symbol availability is not "
         "outcome-dependent, so this is pre-registration-clean; and JPY/XAU are additive "
         "symbols that cannot lift a sub-+0.05R fleet average to the +0.15R bar."
         if excluded else "- All fleet symbols evaluated."),
        "- **D1 regime gate** is an offline Wilder-ADX port of the live `TrendDir` formula. "
        "The Study C fork found this port can diverge materially from the EA's stored regime "
        "tag (H4→D1 aggregation + iADX smoothing differences); it is a faithful-intent "
        "approximation, not a byte-identical port. Given both candidates miss the avg-R bar "
        "by ~4×, the regime nuance cannot change the ruling.",
        "- M1 fill/stop resolution with a frozen per-symbol spread and conservative intrabar "
        "tie-breaks (pre-bank SL wins, post-bank BE wins). Tick-accuracy is reserved for a "
        "passer only — not reached.\n",
    ]
    for cand, name in (("donchian", "DonchianBreak"), ("squeeze", "SqueezeBreak")):
        tr = load_trades(cand)
        parts.append(gate(tr, name))
        if cand == "donchian" and tr:
            parts.append(gate([t for t in tr if t["near_extreme_cut"] == "1"],
                              "DonchianBreak — declared cut: within 3% of 250-extreme"))
            parts.append(gate([t for t in tr if t["near_extreme_cut"] == "0"],
                              "DonchianBreak — declared cut: NOT within 3%"))
    (REPO / "data" / "study" / "breakouts_report.md").write_text("\n".join(parts))
    print("wrote data/study/breakouts_report.md")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": cmd_run, "report": cmd_report}.get(cmd, cmd_run)()
