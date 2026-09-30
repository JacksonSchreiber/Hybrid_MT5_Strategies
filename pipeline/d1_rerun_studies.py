#!/usr/bin/env python3
"""d1_rerun_studies.py - coach 2026-09-30 task 1: rerun the three D1-joined studies with the corrected D1 index.

BAR-INDEX RULE (stated per the coach pipeline rule): every daily feature for an H4 signal uses the last D1 bar dated
STRICTLY BEFORE the signal's day (d < signal date, UTC .dk clock) - the previous CLOSED day, what the EA sees. The
published reports used d <= day, i.e. the signal day's own D1 bar, which closes up to 20 h AFTER the signal
(look-ahead; pipeline/d1_lookahead_check.py). H4 side unchanged: the signal bar is the bar keyed at signal_time.
The >=10 live-card reconciliation of the D1 numbers is PENDING (done separately).

Studies (each module carries D1_STRICT; False reproduces the published report, True is the fix):
  trendcont_step13_study   step-1 D1 staircase (STEP 1 tables move; STEP 3 is H4-only and must not move)
  trader_selection_study   regime age + regime tag (only those two tables can move; the 180 joined decisions and the
                           +0.140 / -0.057 split must not)
  trader_driver_study      the driver table: only "D1 distance from its EMA20" can move
                           (NB: that feature keeps its original definition - Wilder ATR14, UNSIGNED distance - so the
                           diff has one cause; the gate/extension studies use SMA ATR and a signed distance)

Output: for each study, the published (d <= day) output is first checked against the saved report in data/study,
then the lines that change are printed old | new.
"""
from __future__ import annotations
import io, os, sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import trendcont_step13_study as S13, trader_selection_study as SEL, trader_driver_study as DRV  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STUDIES = [("step-1 staircase (trendcont_step13_study)", S13, "trendcont_step13_report.txt"),
           ("regime age / tag (trader_selection_study)", SEL, "trader_selection_report.txt"),
           ("trader-driver table (trader_driver_study)", DRV, "trader_driver_report.txt")]

READING = """READING (written after the run; numbers above are the evidence)
  step-1 staircase: conclusion UNCHANGED - step 1 still does not work as a veto. N=10 'broken' minus 'intact' shrinks
    from +0.096 to +0.066R (halves +0.097/+0.095 -> +0.043/+0.090); N=5 flips sign but is ~0 either way (-0.003 ->
    +0.014). The step stays failed; if anything a broken staircase is not worse.
  regime age: conclusion UNCHANGED - the trader's take-vs-skip gap sits in the 10-40d bucket (+0.55 -> +0.47R) and is
    ~0 in the new-regime bucket; the >40d bucket moves -0.24 -> -0.07 (n=26, noise). Regime TAG: TREND_UP gap
    +0.46 -> +0.55; CHOP flips +0.07 -> -0.12 (n=55, within one SE). The 180 decisions and the overall
    +0.140/-0.057 split do not move (the D1 join does not touch the join or the outcome).
  trader-driver: 'D1 distance from its EMA20' is STILL flagged as the candidate driver (gap < +0.05R in 2 of 3
    buckets, both runs); the >1.5 ATR bucket keeps its gap (+0.67 -> +0.69R). So the look-ahead did not create the
    driver flag. BUT this is a statement about what his selection correlates with, not an outcome edge: on the blind
    record the D1-extension split itself collapses once the index is fixed (d1_lookahead_check: +0.269R -> -0.044R).
  Other pipeline scripts that still join D1 with d <= day (NOT rerun here): d1_extension_study.py (l.40),
    d1_gate_study.py (l.49, l.122), load_filter_retro.py (l.42), and archive_bundles.py (l.124: b[0] <= t on the
    signal bar's OPEN epoch; the D1 bar of the signal day opens 00:00 <= t, so the D1 chart in every blind archive
    bundle showed the signal day's COMPLETE D1 bar - look-ahead in the 2026-09-24 advisor/veto discrimination inputs.
    Its H4 slice h4[:i+1] ends at the signal bar and is fine). Reported, not fixed.
  CAVEAT (all D1 work): the .dk D1 files are UTC-midnight days INCLUDING Sunday stubs (EURUSD.dk: 520 Sundays, no
    Saturdays), as the tester EA saw them - a Monday signal's "previous closed day" is the Sunday stub, and ATR14 runs
    low over stubs. Live OANDA D1 has no Sunday bar and a ~21:00 UTC day boundary: part of the pending reconciliation."""


def capture(mod, strict: bool) -> list[str]:
    mod.D1_STRICT = strict
    buf = io.StringIO()
    with redirect_stdout(buf): mod.main()
    return buf.getvalue().rstrip("\n").split("\n")


def main():
    print(__doc__)
    for title, mod, saved in STUDIES:
        old, new = capture(mod, False), capture(mod, True)
        p = os.path.join(ROOT, "data", "study", saved)
        pub = open(p).read().rstrip("\n").split("\n") if os.path.exists(p) else None
        repro = "reproduces the saved report exactly" if pub == old else (
            "DOES NOT reproduce the saved report" if pub else "no saved report to compare")
        print("=" * 118)
        print(f"{title}\n  d <= day run {repro} ({saved}); {sum(a != b for a, b in zip(old, new))} lines change "
              f"(old {len(old)} lines, new {len(new)} lines)")
        # print every section that contains a changed line, old | new side by side
        sect_old, sect_new, changed = [], [], False
        def flush():
            if changed:
                print()
                for a, b in zip(sect_old, sect_new):
                    if a == b: print(f"      {a}")
                    else: print(f"  OLD {a}\n  NEW {b}")
        for a, b in zip(old + [""], new + [""]):
            if a == "" and b == "":
                flush(); sect_old, sect_new, changed = [], [], False; continue
            sect_old.append(a); sect_new.append(b); changed |= a != b
        flush()
        if len(old) != len(new): print("  (line counts differ - tail not shown side by side)")
        print()
    print(READING)


if __name__ == "__main__": main()
