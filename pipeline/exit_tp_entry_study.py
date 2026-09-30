#!/usr/bin/env python3
"""exit_tp_entry_study.py - trader 2026-09-30: two new questions on the blind TrendCont record, with a HOLDOUT.

HOLDOUT: signals from 2025-01-01 on are set aside. Every variant is judged on 2012-2024 ("dev") only. A finalist per
study is chosen by a rule fixed here, before any holdout number is printed:
    finalist = the best dev costed R/trade among variants with dev paired t >= 2.0 vs the base AND dev worst drawdown
               no more than 1.25x the base's dev drawdown.
Only then is the holdout printed - for the base and the finalist, nothing else.

STUDY 1 - where TP2 sits. The EA's TP2 replaced by a fixed target, or no target and a slow trail:
    under today's doctrine (bank 50% at +1R or TP1 if nearer, stop to entry) and under "F" (no bank, stop to entry)
    targets +2 / +3 / +4 / +5R, the EA's own TP2, and no target with the stop trailing 1.5R under the high once +2R is
    reached, or 2R under once +3R. A trade still open after MAX_BARS is marked at the close.

STUDY 2 - entering at a better price. Instead of the signal's entry, a resting limit order 0.10 / 0.25 / 0.50R (of the
    original stop distance) better; the stop and targets stay at their original PRICES, the position is sized to 1R at
    the new, shorter stop, so results are in the new R. The order is cancelled if it has not filled within 6 or 18 H4
    bars, or if price first reaches the original +1R. A missed signal scores 0. On the fill bar only the stop can hit
    (price came to the order and may have carried on to the stop - the worse order); banks and targets start the next
    bar. Doctrine exits as today, measured from the fill price. The spread drag scales up with the shorter stop.
    Compared per SIGNAL (a no-fill is a 0 in the series), which is what the account sees.

Bar rules as exit_param_study.py.
"""
from __future__ import annotations
import os, statistics as st, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, maxdd, paired_t, MAX_BARS   # noqa: E402

HOLDOUT = "2025"


def manage(bars, j0, up, entry, sl, tp1, tp2, bank_frac, target_R=None, trail=None, fill_bar=None):
    """Run a filled position from bar j0. target_R: None = the EA's tp2, float = fixed R, 'none' = no target.
    trail: (arm_R, lag_R) or None. fill_bar: index of a bar where only the stop may hit. -> R or None."""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    tgt = (toR(tp2) if tp2 else None) if target_R is None else (None if target_R == "none" else target_R)
    trig = min(1.0, toR(tp1)) if tp1 else 1.0
    bank_R = trig if (bank_frac and (tgt is None or trig < tgt - 1e-9)) else None
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    start = fill_bar if fill_bar is not None else j0
    for j in range(start, min(len(bars), start + MAX_BARS)):
        b = bars[j]
        o_R = toR(b[1]) if j != fill_bar else 0.0
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo_R <= stop_R:
            return locked + rem * min(stop_R, o_R)
        if j == fill_bar: continue
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, bank_frac * bank_R, 1.0 - bank_frac
        if tgt is not None and hi_R >= tgt:
            return locked + rem * tgt
        hwm = max(hwm, hi_R)
        if hwm >= trig: stop_R = max(stop_R, 0.0)
        if trail and hwm >= trail[0]: stop_R = max(stop_R, hwm - trail[1])
    last = bars[min(len(bars), start + MAX_BARS) - 1]
    return locked + rem * toR(last[4])


def limit_entry(x, better, max_bars, bank_frac=0.5):
    """-> (R in the new R, cost in the new R) ; (0, 0) when the order never fills."""
    bars, i0, up, e, s = x["bars"], x["i0"], x["up"], x["e"], x["s"]
    R0 = abs(e - s); sgn = 1.0 if up else -1.0
    px = e - sgn * better * R0
    one_R = e + sgn * R0
    for j in range(i0, min(len(bars), i0 + max_bars)):
        b = bars[j]
        filled = (b[3] <= px) if up else (b[2] >= px)
        if filled:
            r = manage(bars, j, up, px, s, x["tp1"], x["tp2"], bank_frac, fill_bar=j)
            return (r, x["cost"] * R0 / abs(px - s)) if r is not None else None
        if (b[2] >= one_R) if up else (b[3] <= one_R): return (0.0, 0.0)   # ran away first: cancelled
    return (0.0, 0.0)


def show(label, series, rows, base_c, base_dd, dev_idx):
    c = [series[k] for k in dev_idx]
    b = [base_c[k] for k in dev_idx]
    t = paired_t(b, c) if c != b else 0.0
    dd = maxdd(c)
    print(f"  {label:44} {st.mean(c):+8.4f} {st.mean(c) - st.mean(b):+8.4f} {t:+6.2f} {dd:8.1f}", end="")
    return {"label": label, "mean": st.mean(c), "t": t, "dd": dd, "series": series,
            "ok": t >= 2.0 and dd <= 1.25 * base_dd}


def pick_and_holdout(title, cands, base, rows, hold_idx):
    ok = [c for c in cands if c["ok"]]
    print(f"\n  FINALIST RULE (fixed in advance): best dev R/trade with t >= 2.0 and DD <= 1.25x base")
    if not ok:
        print("  -> no variant qualifies; holdout not opened for this study.\n"); return
    f = max(ok, key=lambda c: c["mean"])
    b = [base["series"][k] for k in hold_idx]; v = [f["series"][k] for k in hold_idx]
    print(f"  -> finalist: {f['label']}")
    print(f"  HOLDOUT {HOLDOUT}+ (n={len(hold_idx)}): base {st.mean(b):+.4f}  finalist {st.mean(v):+.4f}  "
          f"diff {st.mean(v) - st.mean(b):+.4f}  t {paired_t(b, v):+.2f}  DD {maxdd(b):.1f} -> {maxdd(v):.1f}R\n")


def main():
    rows = sorted(load_rows(), key=lambda x: x["t"])
    dev = [k for k, x in enumerate(rows) if x["t"][:4] < HOLDOUT]
    hold = [k for k, x in enumerate(rows) if x["t"][:4] >= HOLDOUT]
    print(f"blind TrendCont record n={len(rows)}: dev 2012-2024 n={len(dev)}, holdout {HOLDOUT}+ n={len(hold)} (sealed until a finalist is named)\n")

    def run(bank, **kw):
        out = []
        for x in rows:
            r = manage(x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], bank, **kw)
            out.append((r if r is not None else 0.0) - x["cost"])
        return out

    base_s = run(0.5)
    base_dd = maxdd([base_s[k] for k in dev])
    hdr = f"  {'variant (dev 2012-2024, costed)':44} {'R/trade':>8} {'vs base':>8} {'t':>6} {'worst DD':>8}"

    print("STUDY 1 - WHERE TP2 SITS"); print(hdr); print("  " + "-" * (len(hdr) - 2))
    base = show("base: bank 50% at +1R, EA's TP2", base_s, rows, base_s, base_dd, dev); print()
    cands = []
    for bank, bl in ((0.5, "bank 50%"), (0.0, "no bank ")):
        for tg, tl in ((None, "EA's TP2"), (2.0, "TP +2R"), (3.0, "TP +3R"), (4.0, "TP +4R"), (5.0, "TP +5R"),
                       ("none", "no TP, trail 1.5R once +2R"), ("none", "no TP, trail 2R once +3R")):
            if bank == 0.5 and tg is None: continue
            tr = (2.0, 1.5) if "1.5R" in tl else ((3.0, 2.0) if "2R once" in tl else None)
            cands.append(show(f"{bl}, {tl}", run(bank, target_R=tg, trail=tr), rows, base_s, base_dd, dev)); print()
    pick_and_holdout("STUDY 1", cands, base, rows, hold)

    print("STUDY 2 - ENTERING AT A BETTER PRICE (per signal; a missed fill is 0)")
    print(hdr + f" {'fill %':>7} {'R/filled':>9} {'TP-winners missed':>18}"); print("  " + "-" * (len(hdr) + 34))
    show("base: enter at the signal price", base_s, rows, base_s, base_dd, dev); print()
    base_tp = {k for k in dev if base_s[k] + rows[k]["cost"] > 1.2}
    cands = []
    for better in (0.10, 0.25, 0.50):
        for mb in (6, 18):
            res = [limit_entry(x, better, mb) for x in rows]
            ser = [(r[0] - r[1]) if r else 0.0 for r in res]
            filled = [k for k in dev if res[k] and res[k] != (0.0, 0.0)]
            c = show(f"limit {better:.2f}R better, cancel after {mb} bars", ser, rows, base_s, base_dd, dev)
            missed = sum(1 for k in base_tp if k not in set(filled))
            print(f" {100 * len(filled) / len(dev):6.1f}% {st.mean(ser[k] for k in filled) if filled else 0:+9.4f} "
                  f"{missed:7d} of {len(base_tp)}")
            cands.append(c)
    pick_and_holdout("STUDY 2", cands, base, rows, hold)
    print("R is per trade (study 2: per signal, in the R of the order actually placed). costed = spread drag once per fill.")


if __name__ == "__main__": main()
