#!/usr/bin/env python3
"""m5_study.py - trader 2026-09-30: the exit findings and confirmation entries re-run on M5 bars.

Data: data/study/m5 (pipeline/build_m5.py - QDM M1 bid bars, UTC). EURUSD, GBPUSD, US100, US500, USOIL; XAUUSD and
USDJPY have no M1 on disk (the QDM store holds none; the local MT5 was not running to dump them) and are left out.
Each trade's levels are shifted into the M5 price space by the difference between the stored H4 signal-bar close and
the M5 close at the same moment (the M1 file is bid-side; the offset is small and constant within a day).

STUDY 1 - does the H4 verdict survive real intrabar order? The same trades replayed on M5 under
    base (bank 50% at +1R, stop to entry), F (no bank, stop to entry), K25 (bank 25%), S25 (no bank, stop to +0.25R),
    side by side with the H4 replay of the SAME trades. Nothing is selected here, so both periods are shown.

STUDY 2 - confirmation entries (the opposite of the resting-limit study): wait for the M5 chart to turn, enter at the
    M5 close that confirms, within a window after the signal (12h or 24h). BUY shown; SELL mirrored.
    C1  EMA reclaim: an M5 close below the M5 EMA20, then the first close back above it
    C2  1h breakout: the first M5 close above the highest high of the previous 12 M5 bars
    C3  dip and reclaim: price trades 0.25R below the signal entry, then the first M5 close back above the entry
    Cancelled (no trade, 0R) if the stop trades first, if price reaches the original +1R first, or at the window end.
    Stop and targets stay at the original prices; the position is sized to 1R at the new stop distance; the spread
    drag scales with it. Doctrine exits (base) from the fill. Per SIGNAL, against the M5 market-entry base.
    HOLDOUT protocol as exit_tp_entry_study.py: judged on 2012-2024; the finalist rule is fixed; 2025+ opened only
    for the finalist.
"""
from __future__ import annotations
import bisect, csv, os, statistics as st, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t          # noqa: E402
from pipeline.exit_param_study import replay as h4_replay                 # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HOLDOUT = "2025"
MAX_M5 = 600 * 48


def load_m5(sym):
    p = os.path.join(ROOT, "data", "study", "m5", f"{sym}.csv")
    if not os.path.exists(p): return None
    t, o, h, l, c = [], [], [], [], []
    with open(p) as f:
        rd = csv.reader(f); next(rd)
        for a, b, d, e, g in rd:
            t.append(a); o.append(float(b)); h.append(float(d)); l.append(float(e)); c.append(float(g))
    ema, k, x = [], 2 / 21, c[0]
    for v in c: x = v * k + x * (1 - k); ema.append(x)
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "ema": ema}


def m5_replay(M, k0, up, entry, sl, tp1, tp2, frac, stop_to):
    """Same rules as exit_param_study.replay, one M5 bar at a time. -> R"""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    trig = min(1.0, toR(tp1)) if tp1 else 1.0
    tgt = toR(tp2) if tp2 else None
    bank_R = trig if (frac and (tgt is None or trig < tgt - 1e-9)) else None
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    O, H, L = M["o"], M["h"], M["l"]
    end = min(len(O), k0 + MAX_M5)
    for j in range(k0, end):
        hi_R, lo_R = (toR(H[j]), toR(L[j])) if up else (toR(L[j]), toR(H[j]))
        if lo_R <= stop_R: return locked + rem * min(stop_R, toR(O[j]))
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, frac * bank_R, 1.0 - frac
        if tgt is not None and hi_R >= tgt: return locked + rem * tgt
        hwm = max(hwm, hi_R)
        if hwm >= trig: stop_R = max(stop_R, stop_to)
    return locked + rem * toR(M["c"][end - 1])


def confirm(M, k0, up, e, s, rule, window):
    """-> (fill index, fill price) or None"""
    R0 = abs(e - s); sgn = 1.0 if up else -1.0
    toR = lambda px: (px - e) * sgn / R0
    H, L, C, EMA = M["h"], M["l"], M["c"], M["ema"]
    seen_below = seen_dip = False
    for j in range(k0, min(len(C), k0 + window)):
        hi_R, lo_R = (toR(H[j]), toR(L[j])) if up else (toR(L[j]), toR(H[j]))
        if lo_R <= -1.0 or hi_R >= 1.0: return None                 # stopped out / ran away before confirming
        cR = toR(C[j])
        above_ema = (C[j] > EMA[j]) if up else (C[j] < EMA[j])
        if rule == "C1":
            if not above_ema: seen_below = True
            elif seen_below: return j, C[j]
        elif rule == "C2":
            if j >= 12:
                ref = max(H[j - 12:j]) if up else min(L[j - 12:j])
                if (C[j] > ref) if up else (C[j] < ref): return j, C[j]
        elif rule == "C3":
            if lo_R <= -0.25: seen_dip = True
            elif seen_dip and cR > 0: return j, C[j]
    return None


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    cache, use = {}, []
    for x in rows:
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]
        if not M: continue
        h4 = x["bars"]; t0 = h4[x["i0"]][0][:16]
        k0 = bisect.bisect_left(M["t"], t0)
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != t0[:10]: continue
        off = h4[x["i0"] - 1][4] - M["c"][k0 - 1]                      # .dk minus M1-bid at the signal close
        use.append({**x, "M": M, "k0": k0, "off": off})
    dev = [k for k, x in enumerate(use) if x["t"][:4] < HOLDOUT]
    hold = [k for k, x in enumerate(use) if x["t"][:4] >= HOLDOUT]
    print(f"M5 coverage: {len(use)} of {len(rows)} TrendCont rows ({', '.join(sorted({x['sym'] for x in use}))}); "
          f"dev n={len(dev)}, holdout n={len(hold)}\n")

    V = {"base": (0.5, 0.0, "doc"), "F": (0.0, 0.0, None), "K25": (0.25, 0.0, "doc"), "S25": (0.0, 0.25, None)}
    m5, h4 = {}, {}
    for name, (frac, stp, at) in V.items():
        m5[name] = [(m5_replay(x["M"], x["k0"], x["up"], x["e"] - x["off"], x["s"] - x["off"],
                               (x["tp1"] - x["off"]) if x["tp1"] else None, (x["tp2"] - x["off"]) if x["tp2"] else None,
                               frac, stp) or 0.0) - x["cost"] for x in use]
        h4[name] = [(h4_replay(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], at, frac, stp) or 0.0)
                    - x["cost"] for x in use]
    print("STUDY 1 - SAME TRADES, H4 REPLAY vs M5 REPLAY (costed R/trade)")
    print(f"  {'rule':6} {'period':10} {'H4':>8} {'M5':>8} {'M5 vs base':>11} {'t':>6} {'M5 DD':>7} {'trades scored differently':>26}")
    for name in V:
        for per, idx in (("2012-24", dev), ("2025+", hold)):
            a = [h4[name][k] for k in idx]; b = [m5[name][k] for k in idx]; bb = [m5["base"][k] for k in idx]
            diff = sum(1 for p, q in zip(a, b) if abs(p - q) > 0.05)
            t = paired_t(bb, b) if name != "base" else 0.0
            print(f"  {name:6} {per:10} {st.mean(a):+8.4f} {st.mean(b):+8.4f} {st.mean(b) - st.mean(bb):+11.4f} {t:+6.2f} "
                  f"{maxdd(b):7.1f} {diff:12d} of {len(idx)}")
    print()

    base_s = m5["base"]; base_dd = maxdd([base_s[k] for k in dev])
    win = {k for k in dev if base_s[k] + use[k]["cost"] > 1.2}
    print("STUDY 2 - CONFIRMATION ENTRIES ON M5 (per signal, costed; a signal that never confirms is 0)")
    print(f"  {'variant (dev 2012-2024)':40} {'R/signal':>9} {'vs base':>8} {'t':>6} {'worst DD':>8} {'fill %':>7} {'R/filled':>9} {'TP winners missed':>18}")
    print(f"  {'base: market at the signal':40} {st.mean(base_s[k] for k in dev):+9.4f}")
    cands = []
    for rule, lbl in (("C1", "EMA20 reclaim"), ("C2", "1h breakout"), ("C3", "dip 0.25R + reclaim")):
        for w, wl in ((144, "12h"), (288, "24h")):
            ser, filled = [], set()
            for k, x in enumerate(use):
                e, s = x["e"] - x["off"], x["s"] - x["off"]
                f = confirm(x["M"], x["k0"], x["up"], e, s, rule, w)
                if not f: ser.append(0.0); continue
                j, px = f
                if abs(px - s) <= 0: ser.append(0.0); continue
                r = m5_replay(x["M"], j + 1, x["up"], px, s, (x["tp1"] - x["off"]) if x["tp1"] else None,
                              (x["tp2"] - x["off"]) if x["tp2"] else None, 0.5, 0.0)
                ser.append((r or 0.0) - x["cost"] * abs(e - s) / abs(px - s)); filled.add(k)
            a = [ser[k] for k in dev]; b = [base_s[k] for k in dev]
            t = paired_t(b, a); dd = maxdd(a); fd = [ser[k] for k in dev if k in filled]
            print(f"  {rule + ' ' + lbl + ', ' + wl:40} {st.mean(a):+9.4f} {st.mean(a) - st.mean(b):+8.4f} {t:+6.2f} {dd:8.1f} "
                  f"{100 * len(fd) / len(dev):6.1f}% {st.mean(fd) if fd else 0:+9.4f} {len(win - filled):8d} of {len(win)}")
            cands.append({"label": f"{rule} {lbl}, {wl}", "mean": st.mean(a), "t": t, "dd": dd, "series": ser,
                          "ok": t >= 2.0 and dd <= 1.25 * base_dd})
    ok = [c for c in cands if c["ok"]]
    print("\n  FINALIST RULE (fixed in advance): best dev R/signal with t >= 2.0 and DD <= 1.25x base")
    if not ok: print("  -> no variant qualifies; holdout not opened."); return
    f = max(ok, key=lambda c: c["mean"])
    b = [base_s[k] for k in hold]; v = [f["series"][k] for k in hold]
    print(f"  -> finalist: {f['label']}\n  HOLDOUT 2025+ (n={len(hold)}): base {st.mean(b):+.4f}  finalist {st.mean(v):+.4f}  "
          f"diff {st.mean(v) - st.mean(b):+.4f}  t {paired_t(b, v):+.2f}  DD {maxdd(b):.1f} -> {maxdd(v):.1f}R")


if __name__ == "__main__": main()
