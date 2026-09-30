#!/usr/bin/env python3
"""exit_pyramid_study.py - trader 2026-09-30: scale INTO winners instead of out of them.

Position sizes are in units of today's full position (1 unit = 1R at the original stop).
  P200  start 0.25 units at entry; add 0.25 units each time the trade reaches +0.25R, +0.50R, ... - full size (1.00) at
        +0.75R, 1.25 at +1R, and so on until the cap of 2.00 units (reached at +1.75R).
  P225  the same, one more add at +2R (2.25 units) - the literal "repeat the pattern up to +2R".
Both: no bank; the stop moves to the ORIGINAL entry once +1R is reached (the doctrine's stop-to-entry, as asked); the
whole position leaves at TP2 or the stop. Units added above entry therefore lose their distance above entry when the
stop-to-entry is hit, and before +1R a full reversal to the original stop costs MORE than 1R (1.375R once full size).
P200avg1 / P200avg / P225avg (2026-09-30 follow-up): at +1R the stop goes to the position's AVERAGE entry instead of the
original entry (a stop-out then nets 0 on the whole position); avg1 fixes it there, avg moves it up to the new average
after every later add.
References: base (doctrine v2: bank 50% at +1R, stop to entry) and F (no bank, stop to entry at +1R - full size from
the start), so P vs F isolates the scaling-in itself.

Bar rules as exit_param_study.py (stop first at the previous bars' level; a bar opening through it fills at the open).
Adds fill at their trigger price (resting buy-stop / sell-stop orders), plus `slip` R each. A bar that touches an add
level and the stop is scored with the add filled first (the worse order). Spread cost is charged per
unit traded (the per-symbol drag x units), so four adds cost more spread than one entry.
"""
from __future__ import annotations
import os, statistics as st, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS   # noqa: E402
from pipeline.exit_s025_study import aa_rows                                # noqa: E402


def replay(bars, i0, up, entry, sl, tp1, tp2, mode, slip=0.0):
    """-> (R in units of the original 1R, units traded, peak open risk to the current stop in R)"""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    tp2_R = toR(tp2) if tp2 else None
    trig = min(1.0, toR(tp1)) if tp1 else 1.0
    if mode == "base":
        bank_R = trig if (tp2_R is None or trig < tp2_R - 1e-9) else None
        legs, adds = [[0.0, 1.0]], []
    elif mode == "F":
        bank_R, legs, adds = None, [[0.0, 1.0]], []
    else:
        cap = 2.25 if mode.startswith("P225") else 2.0
        bank_R, legs = None, [[0.0, 0.25]]
        adds = [0.25 * k for k in range(1, 9) if 0.25 + 0.25 * k <= cap + 1e-9]
    avg_fixed = None
    stop_R, banked, locked, hwm, units, peak_risk = -1.0, False, 0.0, 0.0, legs[0][1], legs[0][1]
    for j in range(i0, min(len(bars), i0 + MAX_BARS)):
        b = bars[j]
        o_R = toR(b[1])
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo_R <= stop_R:
            # same bar touched an add level AND the stop: order unknown - assume the add filled first (the worse case)
            while adds and hi_R >= adds[0] and (tp2_R is None or adds[0] < tp2_R - 1e-9):
                lvl = adds.pop(0); legs.append([lvl + slip, 0.25]); units += 0.25
            px = min(stop_R, o_R)
            return locked + sum(sz * (px - at) for at, sz in legs), units, peak_risk
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked = True, 0.5 * bank_R
            legs = [[0.0, 0.5]]
        while adds and hi_R >= adds[0] and (tp2_R is None or adds[0] < tp2_R - 1e-9):
            lvl = adds.pop(0); legs.append([lvl + slip, 0.25]); units += 0.25
        if tp2_R is not None and hi_R >= tp2_R:
            return locked + sum(sz * (tp2_R - at) for at, sz in legs), units, peak_risk
        hwm = max(hwm, hi_R)
        if hwm >= trig:
            avg = sum(sz * at for at, sz in legs) / sum(sz for _, sz in legs)
            if mode.endswith("avg"): stop_R = max(stop_R, avg)                      # follows the average after each add
            elif mode.endswith("avg1"):                                              # the average at +1R, then fixed
                if avg_fixed is None: avg_fixed = avg
                stop_R = max(stop_R, avg_fixed)
            else: stop_R = max(stop_R, 0.0)
        peak_risk = max(peak_risk, sum(sz * (at - stop_R) for at, sz in legs))
    last = bars[min(len(bars), i0 + MAX_BARS) - 1]
    return locked + sum(sz * (toR(last[4]) - at) for at, sz in legs), units, peak_risk


def longest_dry(v):
    m = c = 0
    for x in v:
        c = c + 1 if x <= 0.01 else 0; m = max(m, c)
    return m


def table(label, rows, slip=0.0):
    rows = sorted(rows, key=lambda x: x["t"])
    out = {m: [replay(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], m, slip) for x in rows]
           for m in ("base", "F", "P200", "P200avg1", "P200avg", "P225avg")}
    keep = [k for k in range(len(rows)) if all(out[m][k] for m in out)]
    half = len(keep) // 2
    print(f"{label}  (n={len(keep)}{', add slippage ' + format(slip, '.2f') + 'R each' if slip else ''})")
    print(f"  {'rule':8} {'R/trade':>8} {'costed':>8} {'vs base':>8} {'t':>6} {'t 1st½':>7} {'t 2nd½':>7} {'worst DD':>9} "
          f"{'win%':>6} {'dry run':>8} {'avg units':>9} {'max open risk':>13}")
    bc = None
    for m in ("base", "F", "P200", "P200avg1", "P200avg", "P225avg"):
        v = [out[m][k] for k in keep]
        r = [x[0] for x in v]; c = [x[0] - rows[k]["cost"] * x[1] for x, k in zip(v, keep)]
        if bc is None: bc, br = c, r
        t = paired_t(bc, c) if m != "base" else 0.0
        t1 = paired_t(bc[:half], c[:half]) if t else 0.0; t2 = paired_t(bc[half:], c[half:]) if t else 0.0
        print(f"  {m:8} {st.mean(r):+8.4f} {st.mean(c):+8.4f} {st.mean(c) - st.mean(bc):+8.4f} {t:+6.2f} {t1:+7.2f} {t2:+7.2f} "
              f"{maxdd(c):9.1f} {100 * sum(1 for x in r if x > 0.01) / len(r):6.1f} {longest_dry(r):8d} "
              f"{st.mean(x[1] for x in v):9.2f} {max(x[2] for x in v):11.2f}R")
    print()


def main():
    rec = load_rows()
    table("BLIND TRENDCONT RECORD", rec)
    table("BLIND TRENDCONT RECORD", rec, slip=0.02)
    table("BLIND TRENDCONT RECORD", rec, slip=0.05)
    table("OUT OF SAMPLE: the other three detectors, 8 symbols", aa_rows({"SweepMSS", "DeepFib", "EMArevQ"}))
    print("R is per trade in units of today's full position (1R = the loss at the original stop at full size).\n"
          "costed = spread drag x units traded. max open risk = the most R the position ever had at risk to its stop.")


if __name__ == "__main__": main()
