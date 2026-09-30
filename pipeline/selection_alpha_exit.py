#!/usr/bin/env python3
"""selection_alpha_exit.py - coach 2026-09-30 item 14: the trader's selection alpha under the new exit.

Population: the same TrendCont decisions as trader_selection_study.py - W11-W14 decision journals (his takes and his
own skips; skip reasons 0/8/9 are not trader decisions), each joined by signal_time to the blind AA baseline of the
SAME window. Previously: taken +0.140R vs skipped -0.057R = +0.197R per pick, on the baseline journal r_multiple.

Here each joined row is REPLAYED over H4 from the baseline row's own levels (entry / sl / tp1 / tp2 of the AA row),
starting at the bar after the signal bar, with exit_param_study.replay under:
    50% bank (the old doctrine), 25% bank (the new doctrine), 0% bank (study F)
- all at min(+1R, TP1), stop to entry there. Costed = minus the per-symbol spread drag once per trade.
The journal-based gap is printed first next to the 50% replay as a calibration.
"Widen or narrow" is a difference-in-differences: per row, delta = (R under 25% or 0%) - (R under 50%); the test is
mean delta on taken rows minus mean delta on skipped rows (Welch t). The gap itself carries an SE around 0.25R.
No D1 bars are used.
"""
from __future__ import annotations
import csv, os, statistics as st, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.trader_selection_study import CJ, WINDOWS                        # noqa: E402
from pipeline.trendcont_step13_study import load                               # noqa: E402
from pipeline.exit_param_study import replay                                   # noqa: E402
from pipeline.exit_mgmt_study import DRAG                                      # noqa: E402
from pipeline.coach_items_common import welch                                   # noqa: E402

FRACS = (0.5, 0.25, 0.0)


def main():
    print(__doc__)
    rows = []
    for label, dec, base in WINDOWS:
        dp, bp = os.path.join(CJ, dec), os.path.join(CJ, base)
        if not (os.path.exists(dp) and os.path.exists(bp)): print(f"{label}: missing file"); continue
        bl = {r["signal_time"]: r for r in csv.DictReader(open(bp, newline="", encoding="ascii", errors="replace"))}
        sym = dec.split("_")[0]
        h4 = load(sym, "h4"); ih = {b[0][:16]: k for k, b in enumerate(h4)}
        cost = DRAG.get(sym.split(".")[0], 0.007)
        n = 0
        for r in csv.DictReader(open(dp, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont": continue
            d = r.get("decision", ""); took = d.startswith("approved")
            if not took and (d != "skipped" or r.get("skip_reason") in ("8", "9", "0")): continue
            b = bl.get(r["signal_time"]); i = ih.get(r["signal_time"][:16])
            if not b or i is None or i < 60: continue                     # same exclusions as trader_selection_study
            try:
                e, s = float(b["entry"]), float(b["sl"]); jr = float(b["r_multiple"])
                float(r["orig_entry"]), float(r["orig_sl"])
            except (TypeError, ValueError): continue
            tp1 = float(b["tp1"]) if b.get("tp1") else None
            tp2 = float(b["tp2"]) if b.get("tp2") else (float(b["tp"]) if b.get("tp") else None)
            up = b["direction"].upper().startswith("B")
            res = {f: replay(h4, i + 1, up, e, s, tp1, tp2, ("doc" if f else None), f, 0.0) for f in FRACS}
            if any(v is None for v in res.values()): continue
            rows.append({"took": took, "win": label, "jr": jr, **{f: res[f] - cost for f in FRACS}})
            n += 1
        print(f"{label:16} {n:>4} decisions")
    tk = [r for r in rows if r["took"]]; sk = [r for r in rows if not r["took"]]
    print(f"\n{len(rows)} decisions: {len(tk)} taken, {len(sk)} skipped\n")
    print(f"{'outcome measure':34} {'taken':>8} {'skipped':>8} {'gap':>8} {'Welch t':>8}")
    m = lambda xs, k: st.mean(x[k] for x in xs)
    for k, name in (("jr", "baseline journal r_multiple (raw)"), (0.5, "replay, 50% bank (costed)"),
                    (0.25, "replay, 25% bank (costed)"), (0.0, "replay, 0% bank (costed)")):
        print(f"{name:34} {m(tk, k):+8.3f} {m(sk, k):+8.3f} {m(tk, k) - m(sk, k):+8.3f} "
              f"{welch([x[k] for x in tk], [x[k] for x in sk]):+8.2f}")
    print("\nDIFFERENCE-IN-DIFFERENCES: per-row delta vs the 50% replay, taken minus skipped")
    for f in (0.25, 0.0):
        dt = [x[f] - x[0.5] for x in tk]; ds = [x[f] - x[0.5] for x in sk]
        print(f"  {int(f * 100):>2}% - 50%: taken {st.mean(dt):+.4f}  skipped {st.mean(ds):+.4f}  "
              f"gap change {st.mean(dt) - st.mean(ds):+.4f}R  Welch t {welch(dt, ds):+.2f}  -> "
              + ("WIDENS" if st.mean(dt) > st.mean(ds) else "NARROWS"))
    print("\nper window, gap taken-skipped (costed replay):   50%     25%      0%   (n taken / skipped)")
    for label, *_ in WINDOWS:
        a = [x for x in tk if x["win"] == label]; b = [x for x in sk if x["win"] == label]
        if a and b:
            print(f"  {label:16} " + "  ".join(f"{m(a, f) - m(b, f):+6.3f}" for f in FRACS) + f"   ({len(a)} / {len(b)})")


if __name__ == "__main__": main()
