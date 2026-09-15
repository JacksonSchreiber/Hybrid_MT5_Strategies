#!/usr/bin/env python3
"""emarevq_confirm.py — 15-chart CONFIRMATION sheet for the candidate rule B>=12 & D>=0.56.
Fresh triggers (not in the first 30), probing the decision boundary from both sides:
clear passes, just-passes, borderline-length, borderline-direction, and long-but-choppy
rejects. Blind render + a new marking page (db doc emarevq/confirm_marks). Blind throughout
(price metrics only; no outcome). Seeded, reproducible."""
import csv, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emarevq_calib_build import build_symbol, render_blind, OUT
from emarevq_page import build_page

CH = OUT / "charts_confirm"; CH.mkdir(parents=True, exist_ok=True)
SEED = 7

shown = set()
for r in csv.DictReader(open(OUT / "key.csv")):
    shown.add((r["symbol"], r["signal_time"]))

trig = list(csv.DictReader(open(OUT / "triggers_blind.csv")))
bysym = {}
for t in trig:
    bysym.setdefault(t["symbol"], []).append(t)
recs = []
for label, ts in bysym.items():
    print(f"processing {label} ({len(ts)})...")
    recs += build_symbol(label, ts)
ok = [r for r in recs if not r["drop"] and (r["symbol"], r["signal_time"]) not in shown]
print(f"usable fresh (excl the 30 shown): {len(ok)}")


def pool(pred):
    return [r for r in ok if pred(r)]

buckets = {
    "pass_strong":  (5, pool(lambda r: r["B"] >= 15 and r["D"] >= 0.60)),
    "pass_just":    (3, pool(lambda r: 12 <= r["B"] <= 14 and 0.56 <= r["D"] < 0.60)),
    "border_len":   (3, pool(lambda r: 9 <= r["B"] <= 11 and r["D"] >= 0.58)),
    "border_dir":   (2, pool(lambda r: r["B"] >= 14 and 0.50 <= r["D"] < 0.56)),
    "reject_choppy":(2, pool(lambda r: r["B"] >= 18 and r["D"] <= 0.48)),
}
rng = random.Random(SEED)
picks = []
for name, (k, p) in buckets.items():
    take = p if len(p) <= k else rng.sample(p, k)
    print(f"  {name}: pool {len(p)} -> take {len(take)}")
    picks += [(name, r) for r in take]
if len(picks) < 15:
    extra = [r for r in ok if r["B"] >= 12 and r["D"] >= 0.56
             and all((r["symbol"], r["signal_time"]) != (pr["symbol"], pr["signal_time"]) for _, pr in picks)]
    rng.shuffle(extra)
    picks += [("pass_extra", r) for r in extra[:15 - len(picks)]]
rng.shuffle(picks)
print(f"selected {len(picks)}")

with open(OUT / "key_confirm.csv", "w", newline="") as fh:
    kw = csv.DictWriter(fh, fieldnames=["chart", "bucket", "symbol", "signal_time", "direction",
                                        "A", "B", "C", "D", "R2", "rule_pass"])
    kw.writeheader()
    for i, (bucket, r) in enumerate(picks, 1):
        render_blind(r, CH / f"{i:02d}.png")
        kw.writerow({"chart": i, "bucket": bucket, "symbol": r["symbol"],
                     "signal_time": r["signal_time"], "direction": r["direction"],
                     "A": f"{r['A']:.2f}", "B": r["B"], "C": f"{r['C']:.2f}",
                     "D": f"{r['D']:.2f}", "R2": f"{r['R2']:.2f}",
                     "rule_pass": int(r["B"] >= 12 and r["D"] >= 0.56)})
print(f"rendered {len(picks)} -> {CH}; key -> key_confirm.csv (NOT shown to trader)")

build_page(CH, "emarevq/confirm_marks", "EMArevQ — confirmation round",
           OUT / "confirm_page.html")
