#!/usr/bin/env python3
"""emarevq_tune.py — tune A/B/C to the trader's marks (frozen procedure, one pass).
Grid A in {1.0..1.6}, B in {5..12}, C in {0.20..0.45}; filter passes iff
A<=a AND B>=b AND C<c. Report best agreement + confusion; flag if the SHAPE can't fit."""
import csv
from pathlib import Path
OUT = Path(__file__).resolve().parent.parent / "data" / "study" / "emarevq"
key = {r["chart"]: r for r in csv.DictReader(open(OUT / "key.csv"))}
marks = {"1":"no","2":"no","3":"no","4":"yes","5":"no","6":"no","7":"no","8":"no","9":"no",
"10":"no","11":"no","12":"no","13":"yes","14":"no","15":"no","16":"skip","17":"no","18":"skip",
"19":"no","20":"no","21":"no","22":"no","23":"no","24":"no","25":"no","26":"no","27":"skip",
"28":"no","29":"no","30":"no"}
rows = []
for c, r in key.items():
    m = marks[c]
    if m == "skip":
        continue
    rows.append((c, float(r["A_maxRangeATR"]), int(r["B_len"]), float(r["C_share"]), m == "yes"))
nyes = sum(1 for *_, y in rows if y)
print(f"gradeable {len(rows)} (excl 3 skips): YES={nyes}  NO={len(rows)-nyes}")

def frange(a, b, s):
    x = a; out = []
    while x <= b + 1e-9:
        out.append(round(x, 2)); x += s
    return out

best = None
for a in frange(1.0, 1.6, 0.1):
    for b in range(5, 13):
        for c in frange(0.20, 0.45, 0.05):
            tp = fp = tn = fn = 0
            for _, A, B, C, y in rows:
                p = (A <= a and B >= b and C < c)
                if p and y: tp += 1
                elif p and not y: fp += 1
                elif not p and y: fn += 1
                else: tn += 1
            agree = (tp + tn) / len(rows)
            # rank: max agreement, then max YES recall, then STRICTER (smaller a, larger b, smaller c)
            score = (agree, tp, -a, b, -c)
            if best is None or score > best[0]:
                best = (score, (a, b, c), (tp, fp, tn, fn))
(sc, (a, b, c), (tp, fp, tn, fn)) = best
print(f"\nbest joint: A<={a}  B>={b}  C<{c}")
print(f"  agreement {100*(tp+tn)/len(rows):.0f}%  |  TP={tp} FP={fp} TN={tn} FN={fn}")
print(f"  YES recall {tp}/{nyes}  |  of the charts it passes, {tp}/{tp+fp} were trader-YES")

# Can ANY rule capture BOTH yes-charts with zero false positives?
yes_charts = [(c, A, B, C) for c, A, B, C, y in rows if y]
no_charts = [(c, A, B, C) for c, A, B, C, y in rows if not y]
# a rule capturing both YES needs a>=max(A_yes), b<=min(B_yes), c>max(C_yes)
amin = max(A for _, A, _, _ in yes_charts)
bmax = min(B for _, _, B, _ in yes_charts)
cmin = max(C for _, _, _, C in yes_charts)
fps = [c for c, A, B, C in no_charts if A <= amin and B >= bmax and C < cmin + 1e-9]
print(f"\nloosest rule that still captures BOTH YES (A<={amin}, B>={bmax}, C<={cmin}):")
print(f"  it ALSO admits these trader-NO charts: {fps}")
print(f"  -> min false positives to capture both YES = {len(fps)}")
print("\nDOMINANCE CHECK (a NO metrically quieter than a YES on all 3 axes = shape mismatch):")
for cy, Ay, By, Cy in yes_charts:
    for cn, An, Bn, Cn in no_charts:
        if An <= Ay and Bn >= By and Cn <= Cy:
            print(f"  chart {cn} (NO) dominates chart {cy} (YES): A {An}<={Ay}, B {Bn}>={By}, C {Cn}<={Cy}")
