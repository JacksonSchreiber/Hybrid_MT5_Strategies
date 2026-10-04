#!/usr/bin/env python3
"""oos_parity_check.py - EA build parity for the OOS stage: does today's EA, run with the pinned journals' settings, produce the same
SIGNALS as the pinned AA journal? Compares signal-level fields only (time, strategy, direction, entry, sl, tp1, tp2, decision) - no
outcome column is read, so this says nothing about results.
    python3 pipeline/oos_parity_check.py data/study/oos_aa/parity/AA_EURUSD.dk_*.csv [data/study/aa_pinned/AA_EURUSD.dk_20160801_20260629.csv]
"""
import csv, glob, os, sys
from collections import Counter

KEY = ("signal_time", "strategy", "direction")
LEV = ("entry", "sl", "tp1", "tp2", "decision")                     # what the study reads (exit_s025_study.aa_rows)
ORIG = ("orig_entry", "orig_sl", "orig_tp1", "orig_tp2")             # the detector's own levels


def rows(p, cols=LEV):
    return {tuple(r[k] for k in KEY): tuple(r.get(k) for k in cols)
            for r in csv.DictReader(open(p, newline="", encoding="ascii", errors="replace"))}


def main():
    new = sys.argv[1]
    sym = next(csv.DictReader(open(new, newline="", encoding="ascii", errors="replace")))["symbol"]
    ref = sys.argv[2] if len(sys.argv) > 2 else glob.glob(os.path.join(os.path.dirname(__file__), "..", "data", "study", "aa_pinned", f"AA_{sym}_*.csv"))[0]
    a, b = rows(ref), rows(new)
    common = set(a) & set(b); same = sum(1 for k in common if a[k] == b[k])
    ao, bo = rows(ref, ORIG), rows(new, ORIG); same_o = sum(1 for k in common if ao[k] == bo[k])
    print(f"pinned {os.path.basename(ref)}: {len(a)} signals | this build {os.path.basename(new)}: {len(b)} signals")
    print(f"  same time+strategy+direction: {len(common)} (identical study levels + decision: {same}; identical detector orig_* levels: {same_o}); only pinned {len(set(a) - set(b))}; only this build {len(set(b) - set(a))}")
    for lab, s in (("only pinned", set(a) - set(b)), ("only this build", set(b) - set(a))):
        print(f"  {lab} by strategy: {dict(Counter(k[1] for k in s))}")
    print("  PARITY: " + ("IDENTICAL signal set" if same == len(a) == len(b) else "DIFFERENT - the new symbols' signals come from a different build than the pinned record"))


if __name__ == "__main__": main()
