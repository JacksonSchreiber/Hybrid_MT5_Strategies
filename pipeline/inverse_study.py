#!/usr/bin/env python3
"""inverse_study.py - coach item 21 (2026-10-01), the engineer's independent backtest gate for the Inverse signal.

RULE (coach B7): for any detector's trade still open and UNBANKED at the close of H4 bar 6 after the signal, a stop-entry
order in the opposite direction rests at the parent's stop price from bar 7 through bar 18. It fills if the parent is
stopped in that window (at the stop price, or the bar's open if price gapped through it); it is cancelled when the parent
banks or passes bar 18. Inverse stop = 1R from its fill (~ the parent's original entry); bank at the mirrored +1R (or the
mirrored TP1 if nearer) with the parent detector's fraction (TrendCont/DeepFib 25%, SweepMSS/EMArevQ 50%); stop to entry;
runner to the mirrored TP2. Full size at the fill - no staging, no pyramid. R = the parent's stop distance.

LOOK-AHEAD AUDIT (coach B9): the only arming input is "parent open and unbanked at the bar-6 close", known at that close.
The fill is a resting order at a price fixed before it trades (the parent's stop). Nothing reads the stop bar's close,
high or low beyond "did it trade the stop" - the parent's own stop is checked first in every bar (worse for the inverse:
it fills). The inverse is managed from the NEXT M1 bar after its fill.

RESOLUTION: M1 bars (data/qdm_csv/fleet, all 7 symbols, UTC, bid-side; per-trade offset to the .dk prices from the signal
bar close) - the finest data on disk for every symbol. Real ticks: the EA tester reproduction at build time.
POPULATION: the blind TrendCont record (2,346 rows) + the long blind AA runs for SweepMSS / DeepFib / EMArevQ on the same 7
symbols (BTCUSD excluded). Costs: the per-symbol spread drag once, plus slippage on the fill at 0 / 0.05 / 0.10R.
SHIP BAR (coach B11): pooled development >= +0.10R with t >= 2, both halves positive, majority of symbols, holdout not
negative, >= +0.10R at 0.05R slippage, within +-0.03R of the coach's M5 replay (bars 7-18 pooled: dev +0.203R n=389 t 2.75,
holdout +0.170R n=46, ex-gold +0.196R, +0.153R at 0.05R slippage).
"""
from __future__ import annotations
import bisect, datetime as dt, os, statistics as st, sys
from collections import defaultdict
import numpy as np, pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, MAX_BARS, DRAG   # noqa: E402
from pipeline.exit_s025_study import aa_rows                             # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = {"EURUSD.dk": "EURUSD", "GBPUSD.dk": "GBPUSD", "USOIL.dk": "LIGHTCMDUSD", "US500.dk": "USA500IDXUSD",
       "US100.dk": "USATECHIDXUSD", "XAUUSD.dk": "XAUUSD", "USDJPY.dk": "USDJPY"}
BANKF = {"TrendCont": 0.25, "DeepFib": 0.25, "SweepMSS": 0.5, "EMArevQ": 0.5}
HOLD = "2025"; W0, W1 = 7, 18; SLIPS = (0.0, 0.05, 0.10); MAXM1 = MAX_BARS * 240


def load_m1(sym):
    p = os.path.join(ROOT, "data", "qdm_csv", "fleet", f"{SRC[sym]}-M1-No Session.csv")
    d = pd.read_csv(p, dtype={"Date": str, "Time": str})
    t = pd.to_datetime(d["Date"] + d["Time"], format="%Y%m%d%H:%M:%S").values.astype("datetime64[m]").astype(np.int64)
    return {"t": t, "o": d["Open"].to_numpy(float), "h": d["High"].to_numpy(float), "l": d["Low"].to_numpy(float), "c": d["Close"].to_numpy(float)}


def tmin(s):            # 'YYYY.MM.DD HH:MM' -> minutes since epoch
    return int(dt.datetime.strptime(s[:16], "%Y.%m.%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() // 60)


def run_inverse(M, k, end, up_inv, fill, R, bank_R, tp2_R, bankf):
    """the inverse from M1 bar k (the bar AFTER the fill): stop first each bar, gap fills at the open."""
    sg = 1.0 if up_inv else -1.0; toR = lambda p: (p - fill) * sg / R
    O, H, L, C = M["o"], M["h"], M["l"], M["c"]
    stop, banked, locked, size = -1.0, False, 0.0, 1.0
    for j in range(k, end):
        o = toR(O[j]); hi, lo = (toR(H[j]), toR(L[j])) if up_inv else (toR(L[j]), toR(H[j]))
        if lo <= stop: return locked + size * (o if o < stop else stop)
        if not banked and hi >= bank_R:
            banked = True; locked += bankf * bank_R; size *= (1 - bankf); stop = 0.0
            if tp2_R is not None and hi >= tp2_R: return locked + size * tp2_R
            continue
        if banked and tp2_R is not None and hi >= tp2_R: return locked + size * tp2_R
    return locked + size * toR(C[end - 1])


def trade(x, M):
    """-> (bars_to_stop, {slip: R}) when the parent is stopped before any bank at H4 bar >= 7, else None."""
    h4, i0 = x["bars"], x["i0"]
    t0 = tmin(h4[i0][0]); k0 = int(np.searchsorted(M["t"], t0))
    if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0] - t0 > 240: return None
    off = h4[i0 - 1][4] - M["c"][k0 - 1]
    e, s = x["e"] - off, x["s"] - off; up = x["up"]; R = abs(e - s)
    if R <= 0: return None
    sg = 1.0 if up else -1.0; toR = lambda p: (p - e) * sg / R
    tp1 = (x["tp1"] - off) if x["tp1"] else None; tp2 = (x["tp2"] - off) if x["tp2"] else None
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tp2_R = toR(tp2) if tp2 else None
    if tp2_R is not None and bank_R >= tp2_R - 1e-9: bank_R = tp2_R
    h4t = [tmin(b[0]) for b in h4[i0:min(len(h4), i0 + MAX_BARS + 1)]]
    O, H, L = M["o"], M["h"], M["l"]
    end = min(len(O), k0 + MAXM1)
    for k in range(k0, end):
        o = toR(O[k]); hi, lo = (toR(H[k]), toR(L[k])) if up else (toR(L[k]), toR(H[k]))
        if lo <= -1.0:
            bar = bisect.bisect_right(h4t, int(M["t"][k]))               # 1-based H4 bar number after the signal
            if bar < W0 or k + 1 >= len(O): return None
            fill = O[k] if o < -1.0 else s                               # resting stop-entry at the parent's stop
            out = {}
            for sl in SLIPS:
                f = fill - sg * sl * R                                   # slippage against the inverse (it trades opposite)
                out[sl] = run_inverse(M, k + 1, min(len(O), k + 1 + MAXM1), not up, f, R, bank_R,
                                      tp2_R, BANKF.get(x["strat"], 0.5)) - x["cost"] - 0.0
            return bar, out
        if hi >= bank_R: return None                                     # parent banked first: no inverse
        if tp2_R is not None and hi >= tp2_R: return None
    return None


def line(name, v):
    if not v: return f"  {name:46} n=0"
    r = [q["R"] for q in v]; n = len(r); m = st.mean(r); se = st.pstdev(r) / n ** 0.5 if n > 1 else float("nan")
    ps = defaultdict(list)
    for q in v: ps[q["sym"]].append(q["R"])
    pos = sum(1 for k in ps if st.mean(ps[k]) > 0)
    o = sorted(v, key=lambda q: q["t"]); h = n // 2
    halves = f"{st.mean(q['R'] for q in o[:h]):+.3f}/{st.mean(q['R'] for q in o[h:]):+.3f}" if h else "-"
    return (f"  {name:46} n={n:4d} mean {m:+.3f}R t {m / se:+.2f} win {sum(1 for q in r if q > 0) / n * 100:3.0f}% "
            f"DD {maxdd([q['R'] for q in o]):5.1f} sym+ {pos}/{len(ps)} halves {halves}")


def main():
    print(__doc__)
    parents = [dict(x, strat="TrendCont") for x in load_rows()]
    others = [x for x in aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}) if x["sym"] != "BTCUSD.dk"]
    parents += others
    by_sym = defaultdict(list)
    for x in parents: by_sym[x["sym"]].append(x)
    res = []
    for sym in sorted(by_sym):
        M = load_m1(sym); n0 = len(res)
        for x in by_sym[sym]:
            r = trade(x, M)
            if r: res.append({"sym": sym.split(".")[0], "t": x["t"], "strat": x["strat"], "bar": r[0], "Rs": r[1]})
        print(f"  {sym}: {len(by_sym[sym])} parents, {len(res) - n0} slow stop-outs (bar >= {W0}) inverted", flush=True)
        del M
    for sl in SLIPS:
        for q in res: q["R"] = q["Rs"][sl]
        win = [q for q in res if W0 <= q["bar"] <= W1]
        dev = [q for q in win if q["t"][:4] < HOLD]; hold = [q for q in win if q["t"][:4] >= HOLD]
        print(f"\n===== SLIPPAGE {sl:.2f}R on the fill =====")
        print(line(f"POOLED all detectors, bars {W0}-{W1}, 2012-24", dev))
        print(line(f"POOLED all detectors, bars {W0}-{W1}, 2025+", hold))
        print(line("  ex-gold 2012-24", [q for q in dev if q["sym"] != "XAUUSD"]))
        print(line("  ex-gold 2025+", [q for q in hold if q["sym"] != "XAUUSD"]))
        print(line(f"TrendCont only, bars {W0}-{W1}, 2012-24", [q for q in dev if q["strat"] == "TrendCont"]))
        print(line(f"TrendCont only, bars {W0}-{W1}, 2025+", [q for q in hold if q["strat"] == "TrendCont"]))
        if sl == 0.0:
            print("\n  by detector (bars 7-18):")
            for d in ("TrendCont", "SweepMSS", "DeepFib", "EMArevQ"):
                print(line(f"    {d} 2012-24", [q for q in dev if q["strat"] == d]))
                print(line(f"    {d} 2025+", [q for q in hold if q["strat"] == d]))
            print("\n  by symbol (bars 7-18, all detectors):")
            for s in sorted({q["sym"] for q in win}):
                print(line(f"    {s} 2012-24", [q for q in dev if q["sym"] == s]))
                print(line(f"    {s} 2025+", [q for q in hold if q["sym"] == s]))
            unw = [q for q in res if q["bar"] >= W0]
            print("\n  UNWINDOWED (bar >= 7, no bar-18 cut):")
            print(line("    all detectors 2012-24", [q for q in unw if q["t"][:4] < HOLD]))
            print(line("    all detectors 2025+", [q for q in unw if q["t"][:4] >= HOLD]))
            print("\n  dose-response by bars-to-stop (all detectors, 2012-24):")
            for a, b in ((7, 9), (10, 12), (13, 18), (19, 30), (31, 600)):
                print(line(f"    stop at bar {a}-{b}", [q for q in unw if a <= q["bar"] <= b and q["t"][:4] < HOLD]))


if __name__ == "__main__": main()
