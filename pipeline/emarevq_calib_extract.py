#!/usr/bin/env python3
"""emarevq_calib_extract.py — the OUTCOME-BLINDNESS WALL for EMArevQ calibration.

Reads the mfe EMArev cohort and writes data/study/emarevq/triggers_blind.csv with ONLY
{symbol, signal_time, direction, to_entry, to_sl} — NO outcome column exists in that file.
Every calibration step downstream reads only this file, so blindness is structural, not
intent. Outcome data re-enters ONLY in item 3 (blind-base measurement), after the freeze.

Only the 5 symbols with full-range QDM M1 on disk are emitted (enough to span the boundary).
"""
import csv, glob, os
from pathlib import Path
from collections import Counter

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "study" / "emarevq"
OUT.mkdir(parents=True, exist_ok=True)

# symbols with full-range QDM M1 (see data/qdm_csv/fleet/)
HAVE_M1 = {"EURUSD.dk", "GBPUSD.dk", "USOIL.dk", "US500.dk", "US100.dk"}

rows = []
for f in sorted(glob.glob(str(REPO / "data/study/mfe/*.study.csv"))):
    for r in csv.DictReader(open(f)):
        if r["strategy"] != "EMArev" or "appro" not in r["decision"]:
            continue
        if r["symbol"] not in HAVE_M1:
            continue
        rows.append({
            "symbol": r["symbol"],
            "signal_time": r["signal_time"],
            "direction": r["direction"],
            "to_entry": r["to_entry"],
            "to_sl": r["to_sl"],
        })

p = OUT / "triggers_blind.csv"
with open(p, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["symbol", "signal_time", "direction", "to_entry", "to_sl"])
    w.writeheader()
    w.writerows(rows)
print(f"wrote {p} — {len(rows)} EMArev triggers (blind: 5 non-outcome fields only)")
print("per symbol:", dict(Counter(r["symbol"] for r in rows)))
