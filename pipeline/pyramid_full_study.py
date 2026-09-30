#!/usr/bin/env python3
"""pyramid_full_study.py - coach items 19/20 (2026-09-30): the +1.5R pyramid on the full TrendCont book, all 7 symbols,
H4 and M5, 2012-24 and the 2025+ holdout split by symbol.

Base = the live doctrine as the coach specified it: staged entry (25% of the ruled size at the signal, +75% at the close
of H4 bar 6 if still open, banked or not), bank 25% at +1R (or TP1 if nearer) on every leg open at that moment, stop to
entry, runner to TP2. Variant = base + the pyramid: the first bar whose high reaches +1.5R AFTER the bank adds 50% of the
ORIGINAL full size at +1.5R (the bar's open if it gapped past), stop at entry, target TP2; the runner's stop does not
move. Units: R of the full ruled position at the original stop. Costs: the per-symbol spread drag per unit traded.
Bar rules as the coach's staged_entry_study.py (training/coach): stop checked first at the previous bars' level (a bar
opening through it fills at the open); the bank bar ends that bar's processing except for TP2; M5 replays one M5 bar
at a time with the staged add at the last M5 bar of H4 bar 6 and the same per-trade price offset as m5_study.py.
"""
from __future__ import annotations
import bisect, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS   # noqa: E402
from pipeline.m5_study import load_m5, MAX_M5                                # noqa: E402

BANK, F0, N, PYR_R, PYR_F = 0.25, 0.25, 6, 1.5, 0.5
HOLDOUT = "2025"


def _run(steps, toR, bank_R, tp2_R, cost_unit, pyr, add_at):
    """steps: iterable of (index, o, h, l, c) in price. add_at: the step index of the staged add."""
    legs = [(F0, 0.0)]; locked = 0.0; stop = -1.0; banked = False; added = False; pyr_done = not pyr
    cost = cost_unit * F0
    val = lambda r: locked + sum(s * (r - e) for s, e in legs)
    last_c = None
    for k, O, H, Lo, Cc, up in steps:
        o, c = toR(O), toR(Cc); hi, lo = (toR(H), toR(Lo)) if up else (toR(Lo), toR(H)); last_c = c
        if lo <= stop: return val(o if o < stop else stop) - cost
        if not banked and hi >= bank_R:
            banked = True; locked += BANK * sum(s * (bank_R - e) for s, e in legs)
            legs = [(s * (1 - BANK), e) for s, e in legs]; stop = 0.0
            if not pyr_done and hi >= PYR_R:
                legs.append((PYR_F, max(PYR_R, o))); pyr_done = True; cost += cost_unit * PYR_F
            if tp2_R is not None and hi >= tp2_R: return val(tp2_R) - cost
            if not added and add_at is not None and k == add_at:
                legs.append((1 - F0, c)); added = True; cost += cost_unit * (1 - F0)
            continue
        if banked and not pyr_done and hi >= PYR_R:
            legs.append((PYR_F, max(PYR_R, o))); pyr_done = True; cost += cost_unit * PYR_F
        if tp2_R is not None and hi >= tp2_R: return val(tp2_R) - cost
        if not added and add_at is not None and k == add_at:
            legs.append((1 - F0, c)); added = True; cost += cost_unit * (1 - F0)
    return val(last_c) - cost


def replay_h4(x, pyr):
    bars, i0, up = x["bars"], x["i0"], x["up"]
    e, s = x["e"], x["s"]; R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    bank_R = min(1.0, toR(x["tp1"])) if x["tp1"] else 1.0; tp2_R = toR(x["tp2"]) if x["tp2"] else None
    end = min(len(bars), i0 + MAX_BARS)
    steps = ((j, bars[j][1], bars[j][2], bars[j][3], bars[j][4], up) for j in range(i0, end))
    return _run(steps, toR, bank_R, tp2_R, x["cost"], pyr, i0 + N - 1)


def replay_m5(u, pyr):
    M, k0, off, up = u["M"], u["k0"], u["off"], u["up"]
    e, s = u["e"] - off, u["s"] - off; R = abs(e - s); sg = 1 if up else -1; toR = lambda p: (p - e) * sg / R
    tp1 = (u["tp1"] - off) if u["tp1"] else None; tp2 = (u["tp2"] - off) if u["tp2"] else None
    bank_R = min(1.0, toR(tp1)) if tp1 else 1.0; tp2_R = toR(tp2) if tp2 else None
    h4 = u["bars"]; iN = u["i0"] + N - 1
    k_add = (bisect.bisect_left(M["t"], h4[iN + 1][0][:16]) - 1) if iN + 1 < len(h4) else None
    end = min(len(M["o"]), k0 + MAX_M5)
    steps = ((k, M["o"][k], M["h"][k], M["l"][k], M["c"][k], up) for k in range(k0, end))
    return _run(steps, toR, bank_R, tp2_R, u["cost"], pyr, k_add)


def block(title, rows, idx, b, v):
    order = sorted(idx, key=lambda k: rows[k]["t"])
    bb = [b[k] for k in idx]; vv = [v[k] for k in idx]
    print(f"{title}: n={len(idx)}  base {st.mean(bb):+.4f}  pyramid {st.mean(vv):+.4f}  diff {st.mean(vv) - st.mean(bb):+.4f}  "
          f"t {paired_t(bb, vv):+.2f}  DD {maxdd([b[k] for k in order]):.1f} -> {maxdd([v[k] for k in order]):.1f}R")
    per = defaultdict(list)
    for k in idx: per[rows[k]["sym"]].append((b[k], v[k]))
    better = 0
    for sym in sorted(per):
        p = per[sym]; d = st.mean(y for _, y in p) - st.mean(x for x, _ in p); better += d > 0
        print(f"    {sym.split('.')[0]:7} n={len(p):4d}  base {st.mean(x for x, _ in p):+.3f}  pyramid {st.mean(y for _, y in p):+.3f}  diff {d:+.3f}")
    ex = [k for k in idx if not rows[k]["sym"].startswith("XAUUSD")]
    if ex:
        eb = [b[k] for k in ex]; ev = [v[k] for k in ex]
        print(f"    ex-gold: n={len(ex)}  diff {st.mean(ev) - st.mean(eb):+.4f}  t {paired_t(eb, ev):+.2f}   (pyramid better in {better}/{len(per)} symbols)")


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    dev = [k for k, x in enumerate(rows) if x["t"][:4] < HOLDOUT]; hold = [k for k, x in enumerate(rows) if x["t"][:4] >= HOLDOUT]
    print(__doc__)
    b = [replay_h4(x, False) for x in rows]; v = [replay_h4(x, True) for x in rows]
    print("H4 REPLAY (costed R per trade, full-position units)")
    block("  2012-24", rows, dev, b, v); block("  2025+ holdout", rows, hold, b, v)
    cache, use = {}, []
    for x in rows:
        if x["sym"] not in cache: cache[x["sym"]] = load_m5(x["sym"])
        M = cache[x["sym"]]
        if not M: continue
        t0 = x["bars"][x["i0"]][0][:16]; k0 = bisect.bisect_left(M["t"], t0)
        if k0 <= 0 or k0 >= len(M["t"]) or M["t"][k0][:10] != t0[:10]: continue
        use.append({**x, "M": M, "k0": k0, "off": x["bars"][x["i0"] - 1][4] - M["c"][k0 - 1]})
    ud = [k for k, x in enumerate(use) if x["t"][:4] < HOLDOUT]; uh = [k for k, x in enumerate(use) if x["t"][:4] >= HOLDOUT]
    mb = [replay_m5(u, False) for u in use]; mv = [replay_m5(u, True) for u in use]
    print(f"\nM5 REPLAY ({len(use)} trades, {', '.join(sorted({u['sym'].split('.')[0] for u in use}))})")
    block("  2012-24", use, ud, mb, mv); block("  2025+ holdout", use, uh, mb, mv)


if __name__ == "__main__": main()
