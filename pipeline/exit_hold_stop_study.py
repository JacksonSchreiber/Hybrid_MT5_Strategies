#!/usr/bin/env python3
"""exit_hold_stop_study.py - trader 2026-09-30: two more questions on the blind TrendCont record, same HOLDOUT protocol
as exit_tp_entry_study.py (judge on 2012-2024; finalist rule fixed in advance; 2025+ opened only for the finalist).

STUDY 3 - holding through the weekend and through big news, under today's doctrine:
    W-all   close at the last H4 bar before the weekend (a gap of 12h+ to the next bar)
    W-risk  the same, but only while the stop is still below entry (a position already at entry or better holds)
    N-all   close at the close of the bar before a V-class release in one of the symbol's currencies
    N-risk  the same, only while the stop is still below entry
    Events: data/econ/econ_dense_research.csv through 2024, econ_events_fixed.csv from 2025 (the deployed feed).
    Symbol currencies: EURUSD EUR+USD, GBPUSD GBP+USD, USDJPY USD+JPY, US100/US500/USOIL/XAUUSD USD.
    Bar times are the .dk store's (UTC).

STUDY 4 - the initial stop width, k = 0.75 / 1.25 / 1.5 x today's distance, position re-sized so a full stop is still 1R:
    "targets fixed"   TP1/TP2 stay at the same PRICES (a wider stop makes them fewer R away)
    "targets scaled"  TP1/TP2 move so they stay the same number of R away
    The spread drag scales with 1/k. Doctrine exits as today.
"""
from __future__ import annotations
import csv, datetime as dt, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS   # noqa: E402
from pipeline.exit_tp_entry_study import HOLDOUT, show, pick_and_holdout     # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CCY = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"),
       "US100": ("USD",), "US500": ("USD",), "USOIL": ("USD",), "XAUUSD": ("USD",)}
FMT = "%Y.%m.%d %H:%M"


def ts(s: str) -> dt.datetime: return dt.datetime.strptime(s[:16], FMT)


def load_events():
    ev = defaultdict(list)
    for fn, keep in (("econ_dense_research.csv", lambda y: y < 2025), ("econ_events_fixed.csv", lambda y: y >= 2025)):
        for r in csv.DictReader(open(os.path.join(ROOT, "data", "econ", fn), newline="")):
            if (r.get("class") or "").strip() != "V": continue
            try: t = ts(r["datetime_utc"])
            except ValueError: continue
            if keep(t.year): ev[r["ccy"]].append(t)
    for k in ev: ev[k].sort()
    return ev


def flags(bars, sym, events):
    """Per bar index: (last bar before a weekend, a V event for this symbol in the NEXT bar)."""
    ccys = CCY[sym.split(".")[0]]
    times = [ts(b[0]) for b in bars]
    evs = sorted(t for c in ccys for t in events.get(c, []))
    wk, nw = [False] * len(bars), [False] * len(bars)
    import bisect
    for j, t in enumerate(times):
        end = t + dt.timedelta(hours=4)
        if j + 1 < len(times) and (times[j + 1] - end) >= dt.timedelta(hours=12): wk[j] = True
        k = bisect.bisect_left(evs, end)
        if k < len(evs) and evs[k] < end + dt.timedelta(hours=4): nw[j] = True
    return wk, nw


def manage(bars, j0, up, entry, sl, tp1, tp2, flat=None, risk_only=False):
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    tgt = toR(tp2) if tp2 else None
    trig = min(1.0, toR(tp1)) if tp1 else 1.0
    bank_R = trig if (tgt is None or trig < tgt - 1e-9) else None
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    for j in range(j0, min(len(bars), j0 + MAX_BARS)):
        b = bars[j]
        o_R = toR(b[1])
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo_R <= stop_R: return locked + rem * min(stop_R, o_R)
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, 0.5 * bank_R, 0.5
        if tgt is not None and hi_R >= tgt: return locked + rem * tgt
        hwm = max(hwm, hi_R)
        if hwm >= trig: stop_R = max(stop_R, 0.0)
        if flat is not None and flat[j] and not (risk_only and stop_R >= 0.0):
            return locked + rem * toR(b[4])
    last = bars[min(len(bars), j0 + MAX_BARS) - 1]
    return locked + rem * toR(last[4])


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    dev = [k for k, x in enumerate(rows) if x["t"][:4] < HOLDOUT]
    hold = [k for k, x in enumerate(rows) if x["t"][:4] >= HOLDOUT]
    print(f"blind TrendCont record n={len(rows)}: dev 2012-2024 n={len(dev)}, holdout {HOLDOUT}+ n={len(hold)}\n")
    events = load_events()
    fl = {}
    for x in rows:
        if x["sym"] not in fl: fl[x["sym"]] = flags(x["bars"], x["sym"], events)

    def run(**kw):
        return [(manage(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], **kw) or 0.0) - x["cost"] for x in rows]

    base_s = run()
    base_dd = maxdd([base_s[k] for k in dev])
    hdr = f"  {'variant (dev 2012-2024, costed)':44} {'R/trade':>8} {'vs base':>8} {'t':>6} {'worst DD':>8}"

    print("STUDY 3 - WEEKEND AND NEWS HOLDS"); print(hdr + f" {'closed early':>13}"); print("  " + "-" * (len(hdr) + 12))
    base = show("base: hold through everything", base_s, rows, base_s, base_dd, dev); print()
    cands = []
    for lbl, which, ro in (("W-all: flat before every weekend", 0, False), ("W-risk: flat before weekend if stop < entry", 0, True),
                           ("N-all: flat before every V release", 1, False), ("N-risk: flat before V release if stop < entry", 1, True)):
        ser = []; early = 0
        for k, x in enumerate(rows):
            f = fl[x["sym"]][which]
            r = manage(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], flat=f, risk_only=ro)
            r0 = base_s[k] + x["cost"]
            if k in set(dev) and r is not None and abs(r - r0) > 1e-9: early += 1
            ser.append((r or 0.0) - x["cost"])
        cands.append(show(lbl, ser, rows, base_s, base_dd, dev)); print(f" {early:8d} trades")
    pick_and_holdout("STUDY 3", cands, base, rows, hold)

    print("STUDY 4 - INITIAL STOP WIDTH (position re-sized: a full stop is 1R at every width)")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    show("base: today's stop (k = 1.00)", base_s, rows, base_s, base_dd, dev); print()
    cands = []
    for k_ in (0.75, 1.25, 1.5):
        for scaled in (False, True):
            ser = []
            for x in rows:
                e = x["e"]; s = e - k_ * (e - x["s"])
                tp1 = (e + k_ * (x["tp1"] - e)) if (scaled and x["tp1"]) else x["tp1"]
                tp2 = (e + k_ * (x["tp2"] - e)) if (scaled and x["tp2"]) else x["tp2"]
                r = manage(x["bars"], x["i0"], x["up"], e, s, tp1, tp2)
                ser.append((r or 0.0) - x["cost"] / k_)
            cands.append(show(f"stop x{k_:.2f}, targets {'scaled (same R)' if scaled else 'fixed (same price)'}", ser, rows,
                              base_s, base_dd, dev)); print()
    pick_and_holdout("STUDY 4", cands, base, rows, hold)


if __name__ == "__main__": main()
