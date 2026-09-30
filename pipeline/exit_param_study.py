#!/usr/bin/env python3
"""exit_param_study.py - trader 2026-09-30: parameter sweeps around study F (no bank, stop to entry at +1R), on the
blind TrendCont record (the same 2,346 rows and bar replay as exit_mgmt_study.py).

Family S - stop level, NO bank: when the trade reaches the bank point (+1R, or TP1 if nearer - the doctrine's own
    trigger) the stop moves to L, and the FULL position runs to TP2 or the stop.
    L in -0.75 / -0.50 / -0.25 / 0 (= study F) / +0.25 / +0.50 / +0.75 R.
Family K - bank size, stop to entry at the doctrine's +1R point as today:
    K1  bank p at +1R (or TP1 if nearer - today's trigger; p = 50% is today's doctrine)
    K2  bank p at TP1 instead, nothing at +1R (only rows with a TP2 beyond TP1; single-target rows just run)
    K3  bank p at +1.5R, nothing at +1R
    p in 10 / 25 / 35 / 60 / 75% (50% shown as the reference for each trigger)
    A bank level at or beyond TP2 never fires - the target closes the whole trade first.

Bar rules (all variants, including the base): the stop is checked first at the level set by previous bars; a bar that
OPENS through the stop fills at the open (a raised stop can sit above a pulled-back price - the conservative fill).
Then the bank, then TP2; then the high-water mark and the next bar's stop. Costs: the per-symbol spread drag, once.
"""
from __future__ import annotations
import math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS   # noqa: E402


def replay(bars, i0, up, entry, sl, tp1, tp2, bank_at, frac, stop_to):
    """bank_at: 'doc' (min(+1R, TP1)) | 'tp1' | float R | None;  frac: fraction banked;  stop_to: stop level in R set
    once the doctrine's +1R point is reached. -> R for the whole position."""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    doc_R = min(1.0, toR(tp1)) if tp1 else 1.0
    tp2_R = toR(tp2) if tp2 else None
    if bank_at == "doc": bank_R = doc_R
    elif bank_at == "tp1": bank_R = toR(tp1) if (tp1 and tp2_R is not None and toR(tp1) < tp2_R - 1e-9) else None
    else: bank_R = bank_at
    if bank_R is not None and tp2_R is not None and bank_R >= tp2_R - 1e-9: bank_R = None
    if not frac: bank_R = None
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    for j in range(i0, min(len(bars), i0 + MAX_BARS)):
        b = bars[j]
        o_R = toR(b[1])
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo_R <= stop_R:
            return locked + rem * min(stop_R, o_R)
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, frac * bank_R, 1.0 - frac
        if tp2_R is not None and hi_R >= tp2_R:
            return locked + rem * tp2_R
        hwm = max(hwm, hi_R)
        if hwm >= doc_R: stop_R = max(stop_R, stop_to)
    last = bars[min(len(bars), i0 + MAX_BARS) - 1]
    return locked + rem * toR(last[4])


def longest_dry(v):
    m = c = 0
    for x in v:
        c = c + 1 if x <= 0.01 else 0; m = max(m, c)
    return m


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    print(f"blind TrendCont record: {len(rows)} approved rows, replayed over H4\n")
    variants = [("base  (bank 50% at +1R, stop to entry)", "doc", 0.5, 0.0)]
    for L in (-0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75):
        variants.append((f"S  no bank, stop to {L:+.2f}R at +1R" + ("  (= F)" if L == 0 else ""), None, 0.0, L))
    for tag, at in (("K1", "doc"), ("K2", "tp1"), ("K3", 1.5)):
        for p in (0.10, 0.25, 0.35, 0.50, 0.60, 0.75):
            where = {"doc": "+1R", "tp1": "TP1", 1.5: "+1.5R"}[at]
            variants.append((f"{tag} bank {int(p * 100):2d}% at {where}" + ("  (ref)" if p == 0.5 else ""), at, p, 0.0))
    res = {}
    for name, at, p, L in variants:
        res[name] = [replay(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], at, p, L) for x in rows]
    keep = [k for k in range(len(rows)) if all(res[n][k] is not None for n, *_ in variants)]
    cost = [rows[k]["cost"] for k in keep]; half = len(keep) // 2
    base = [res[variants[0][0]][k] for k in keep]
    hdr = (f"{'variant':44} {'R/trade':>8} {'costed':>8} {'vs base':>8} {'t':>6} {'t 1st½':>7} {'t 2nd½':>7} "
           f"{'worst DD':>9} {'win%':>6} {'dry run':>8}")
    print(hdr); print("-" * len(hdr))
    fam = ""
    for name, *_ in variants:
        if name[:2] != fam and fam: print()
        fam = name[:2]
        v = [res[name][k] for k in keep]
        vc = [a - c for a, c in zip(v, cost)]
        t = paired_t(base, v) if name != variants[0][0] else 0.0
        t1 = paired_t(base[:half], v[:half]) if t else 0.0
        t2 = paired_t(base[half:], v[half:]) if t else 0.0
        print(f"{name:44} {st.mean(v):+8.4f} {st.mean(vc):+8.4f} {st.mean(v) - st.mean(base):+8.4f} {t:+6.2f} {t1:+7.2f} {t2:+7.2f} "
              f"{maxdd(vc):9.1f} {100 * sum(1 for x in v if x > 0.01) / len(v):6.1f} {longest_dry(v):8d}")
    print(f"\nn={len(keep)}; halves split at {rows[keep[half]]['t'][:10]}. 'dry run' = longest streak of trades without a win."
          "\nt = paired t vs the base, per trade. Worst DD is on the costed series in signal-time order.")


if __name__ == "__main__": main()
