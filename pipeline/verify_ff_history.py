#!/usr/bin/env python3
import csv, os
from collections import Counter, defaultdict

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "econ", "ff_history.csv"))

rows = list(csv.DictReader(open(OUT)))
print(f"CSV: {OUT}")
print(f"TOTAL rows: {len(rows)}")

by_year = Counter(r["datetime_utc"][:4] for r in rows)
print("\nEvents per year:")
for y in sorted(by_year):
    print(f"  {y}: {by_year[y]}")

by_impact = Counter(r["impact"] for r in rows)
print("\nBy impact:", dict(by_impact))

high = [r for r in rows if r["impact"] == "High"]
print(f"\nHIGH total: {len(high)}")
print("HIGH USD:", sum(1 for r in high if r["currency"] == "USD"))
print("HIGH EUR:", sum(1 for r in high if r["currency"] == "EUR"))

# actual populated for PAST high-impact (before today 2026-07-30)
past_high = [r for r in high if r["datetime_utc"] < "2026-07-16"]
with_actual = sum(1 for r in past_high if r["actual"].strip())
print(f"\nPAST HIGH-impact (<2026-07-16): {len(past_high)}, with actual: {with_actual} ({100*with_actual/max(1,len(past_high)):.1f}%)")

# blanks by year for high
print("\nHIGH-impact actual-populated by year:")
hy = defaultdict(lambda: [0, 0])
for r in high:
    y = r["datetime_utc"][:4]
    hy[y][0] += 1
    if r["actual"].strip():
        hy[y][1] += 1
for y in sorted(hy):
    tot, act = hy[y]
    print(f"  {y}: {act}/{tot} with actual")

# sample rows
def find(sub, cur=None, n=1):
    out = []
    for r in rows:
        if sub.lower() in r["event"].lower() and (cur is None or r["currency"] == cur) and r["actual"].strip():
            out.append(r)
            if len(out) >= n:
                break
    return out

print("\n=== SAMPLE HIGH-IMPACT ROWS ===")
samples = []
samples += find("Federal Funds Rate", "USD")
samples += find("CPI m/m", "USD")
samples += find("Non-Farm Employment Change", "USD")
samples += find("Main Refinancing Rate", "EUR")
samples += find("CPI y/y", "EUR")
for r in samples:
    print(f"  {r['datetime_utc']} | {r['currency']} | {r['impact']} | {r['event']} | actual={r['actual']} fc={r['forecast']} prev={r['previous']}")
