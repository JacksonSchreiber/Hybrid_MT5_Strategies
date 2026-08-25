#!/usr/bin/env python3
"""test_calendar_coverage.py — acceptance tests for the calendar gap-coverage work
(docs/calendar-gap-coverage-spec.md §4).

Runs tier0.scan_calendar / blind_calendar at the ten decision times in the spec's
table and asserts the class + weekend-vs-violation routing (NOT the placeholder clock
hour — per the reviewer note, W rows carry a placeholder time). Then re-runs the
classifier over the "known date" weekends in the §0 gap tables and reports how many it
now flags.

Usage:
    python3 test_calendar_coverage.py [path/to/econ_events.csv]
Defaults to the deployed feed. Exit 0 iff all §4 assertions pass.
"""
import sys
from datetime import datetime
from pathlib import Path

import tier0

FEED = Path(sys.argv[1]) if len(sys.argv) > 1 else tier0.ECON_CSV


def sig(symbol, y, mo, d, h, mi=0):
    return tier0.Signal(symbol=symbol, direction=1,
                        entry_time=datetime(y, mo, d, h, mi), strategy="SMC")


# (label, signal, predicate on blind_calendar dict, predicate on scan_calendar dict)
CASES = [
    ("#1  Fri->French R1 (W)",       sig("EURUSD", 2017, 4, 21, 12),
     lambda b: b["weekend_event"] is True,
     lambda s: any("french presidential" in w["name"].lower() for w in s["weekend"])),
    ("#2  Fri->French R2 (W)",       sig("EURUSD", 2017, 5, 5, 12),
     lambda b: b["weekend_event"] is True,
     lambda s: any("french presidential" in w["name"].lower() for w in s["weekend"])),
    ("#3  Fri->German Fed (W)",      sig("EURUSD", 2017, 9, 22, 12),
     lambda b: b["weekend_event"] is True,
     lambda s: any("german federal" in w["name"].lower() for w in s["weekend"])),
    ("#4  Fri->Italian ref (W)",     sig("EURUSD", 2016, 12, 2, 12),
     lambda b: b["weekend_event"] is True,
     lambda s: any("italian constitutional" in w["name"].lower() for w in s["weekend"])),
    ("#5  Wed->Yellen testif (V)",   sig("EURUSD", 2017, 7, 12, 12),
     lambda b: b["high_impact_ahead"] is True,
     lambda s: any("testif" in v["name"].lower() for v in s["violations"])),
    ("#6  Wed->UK election (W)",     sig("GBPUSD", 2019, 12, 11, 12),
     lambda b: b["weekend_event"] is True,
     lambda s: any("election" in w["name"].lower() for w in s["weekend"])),
    ("#7  Thu->Jackson Hole (V)",    sig("EURUSD", 2017, 8, 24, 12),
     lambda b: b["high_impact_ahead"] is True,
     lambda s: any("jackson hole" in v["name"].lower() for v in s["violations"])),
    ("#8  Fri->Letwin Sat sit (W)",  sig("GBPUSD", 2019, 10, 18, 12),
     lambda b: b["weekend_event"] is True,
     lambda s: any("letwin" in w["name"].lower() or "saturday" in w["name"].lower()
                   for w in s["weekend"])),
    ("#9  Wed->FOMC, no election",   sig("EURUSD", 2017, 6, 14, 12),
     lambda b: b["high_impact_ahead"] is True and b["weekend_event"] is False,
     lambda s: bool(s["violations"]) and not s["weekend"]),
    ("#10 Fri->JPY: no FR election", sig("USDJPY", 2017, 4, 21, 12),
     lambda b: b["weekend_event"] is False,
     lambda s: not s["weekend"]),
]

# §0 EURUSD gap table — the ten weekends whose trigger date was public before Friday.
# Decision = the preceding Friday 12:00 UTC. Target: flag as many as the calendar now
# carries (elections are in the feed; tariff/summit/truce weekends need the deferred
# political layer of §2 — reported, not asserted).
KNOWN_WEEKENDS = [
    ("2017-04-23 French R1",   "EURUSD", 2017, 4, 21),
    ("2025-02-02 CA/MX tariff","EURUSD", 2025, 1, 31),
    ("2025-04-06 Liberation",  "EURUSD", 2025, 4, 4),
    ("2025-05-11 Geneva talks","EURUSD", 2025, 5, 9),
    ("2018-01-21 US shutdown", "EURUSD", 2018, 1, 19),
    ("2017-09-24 German Fed",  "EURUSD", 2017, 9, 22),
    ("2026-04-12 truce expiry","EURUSD", 2026, 4, 10),
    ("2022-04-10 French R1",   "EURUSD", 2022, 4, 8),
    ("2024-11-03 US election", "EURUSD", 2024, 11, 1),
    ("2017-03-26 AHCA vote",   "EURUSD", 2017, 3, 24),
]


def main():
    events = tier0._load_events(FEED)
    has_class = any(e["cls"] for e in events)
    print(f"feed: {FEED}  ({len(events)} rows, class column: {'yes' if has_class else 'NO (fallback)'})\n")

    print("=== §4 acceptance table ===")
    passed = 0
    for label, s, bpred, spred in CASES:
        b = tier0.blind_calendar(s, events)
        sc = tier0.scan_calendar(s, events)
        ok = bpred(b) and spred(sc)
        passed += ok
        extra = (f"weekend_event={b['weekend_event']} high_impact_ahead={b['high_impact_ahead']} "
                 f"wk_h={b['weekend_hours_until']} v_h={b['hours_until']}")
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:34} {extra}")
        if not ok:
            print(f"         scan weekend={[w['name'] for w in sc['weekend']]}")
            print(f"         scan viol   ={[v['name'] for v in sc['violations']]}")
    print(f"\n  {passed}/{len(CASES)} acceptance cases passed\n")

    print("=== §0 known-date weekend re-run (informational) ===")
    hits = 0
    for label, sym, y, mo, d in KNOWN_WEEKENDS:
        b = tier0.blind_calendar(sig(sym, y, mo, d, 12), events)
        flagged = b["weekend_event"]
        hits += flagged
        print(f"  [{'flag' if flagged else ' -- '}] {label}")
    print(f"\n  {hits}/{len(KNOWN_WEEKENDS)} known-date weekends flagged "
          f"(the misses need the deferred §2 political layer: tariff/summit/truce weekends)\n")

    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
