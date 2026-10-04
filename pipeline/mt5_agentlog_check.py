#!/usr/bin/env python3
"""mt5_agentlog_check.py - did a tester run lack conversion prices? (holdout stage, 2026-10-03)

The EURJPY / GBPJPY / EURGBP OOS runs had no USDJPY / GBPUSD / EURUSD history before 2020 / 2013 / 2016, and the tester agent
log said so ("no prices for symbol X"). This scans ONLY the bytes a run appended to the agent logs (UTF-16LE,
Tester/<terminal>/Agent-*/logs/YYYYMMDD.log; a run can cross midnight into a new file) and prints COUNTS per symbol.

ONE-LOOK: the agent log also carries the EA's prints and the tester's final balance. This tool never prints a log line except the
"testing of ... started" header and "history data begins from" lines, which hold no outcome.

    python3 pipeline/mt5_agentlog_check.py snap SNAPFILE            # before the run: byte size of every agent log
    python3 pipeline/mt5_agentlog_check.py scan SNAPFILE SYMBOL     # after the run: counts in the new bytes only; exit 3 if any
"""
from __future__ import annotations
import glob, json, os, re, sys
from collections import Counter

AGENTS = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Tester/EE0304F13905552AE0B5EAEFB04866EB"
NOPRICE = re.compile(r"no prices for symbol (\S+)")
SAFE = re.compile(r"(testing of \S+ from .* started|history data begins from .*)")


def logs():
    return sorted(glob.glob(os.path.join(AGENTS, "Agent-*", "logs", "*.log")))


def snap(path):
    json.dump({p: os.path.getsize(p) for p in logs()}, open(path, "w"))


def scan(path, sym):
    before = json.load(open(path))
    cnt, safe, files, nbytes = Counter(), [], 0, 0
    for p in logs():
        start = before.get(p, 0)
        size = os.path.getsize(p)
        if size < start: start = 0                       # rewritten file: scan it whole
        if size == start: continue
        start -= start % 2
        with open(p, "rb") as f:
            f.seek(start); raw = f.read()
        files += 1; nbytes += len(raw)
        txt = raw.decode("utf-16-le", errors="replace")
        for line in txt.splitlines():
            m = NOPRICE.search(line)
            if m: cnt[m.group(1)] += 1; continue
            if sym in line:
                s = SAFE.search(line)
                if s and len(safe) < 6: safe.append(f"{sym}: {s.group(1).strip()}")
    print(f"agent-log check {sym}: {files} log file(s) with new bytes ({nbytes / 1e6:.1f} MB); 'no prices for symbol' lines: "
          + (", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) if cnt else "NONE"))
    for s in safe: print("   " + s)
    return 3 if cnt else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "snap": snap(a[1]); sys.exit(0)
    if len(a) == 3 and a[0] == "scan": sys.exit(scan(a[1], a[2]))
    sys.exit(__doc__)
