#!/usr/bin/env python3
"""Manual SL->TP1 ratchet — held-BE counterfactual + trial ledger (coach pair-12 item 2, 2026-09-10).

Same design as be_counterfactual.py, for the item-2 discretionary ratchet button. Sweeps every main
(interactive, non-AA, non-inverse) *.actions.csv for RATCHET_TP1 actions. For each, the counterfactual
is: what the runner would have done if the stop had been LEFT at BE (policy A) instead of ratcheted to
TP1 — a forward H4 bar-walk from the ratchet bar, conservative intrabar (stop-first).

Per use, from the ratchet bar, with the runner's TP at tp2:
  A (held BE)  : stop at entry(BE). tp2 first -> tp2R ; BE first -> 0.
  ratchet used : stop at tp1.        tp2 first -> tp2R ; tp1 first -> tp1R (what the trader booked on
                 the runner). (The journal r_multiple is the blended realised R; this isolates the runner.)
  dR = A_runner - ratchet_runner. dR > 0 => the ratchet CUT a would-be winner (BE would have reached
  tp2); dR < 0 => the ratchet SAVED a fade (BE would have gone to 0); dR = 0 => no difference.

Empty until the button is used in an interactive session (the ledger then fills, 10-use trial).
"""
import csv, glob, os
from datetime import datetime

CJ = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
H4 = "data/backtests/emarev_inv"

def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s.strip(), f)
        except ValueError: pass
    return None

def load_h4(p):
    b = []
    for r in csv.DictReader(open(p, encoding="utf-8", errors="replace")):
        t = dt(r["time"])
        if t: b.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    b.sort(); return b

def sigrow(j, sid):
    for r in csv.DictReader(open(j, encoding="utf-8", errors="replace")):
        if r["signal_id"] == str(sid): return r
    return None

def walk(r, bars, from_t):
    """from the ratchet bar, return (A_runner_R, ratchet_runner_R, reason). R on original risk."""
    d = 1 if r["direction"] == "BUY" else -1
    entry = float(r["entry"]); sl = float(r["sl"])         # original SL = risk basis
    tp2 = float(r["tp2"] or r["tp"]); tp1 = float(r["tp1"] or r["tp"])
    be = entry                                              # BE stop sits at entry (pad ~0)
    risk = abs(entry - sl)
    if risk <= 0: return None, None, "degenerate"
    tp2R = abs(tp2 - entry) / risk; tp1R = abs(tp1 - entry) / risk
    fwd = [x for x in bars if x[0] > from_t]
    A = None; RA = None
    for (t, o, hi, lo, c) in fwd:
        hit_tp2 = (hi >= tp2) if d > 0 else (lo <= tp2)
        hit_tp1 = (lo <= tp1) if d > 0 else (hi >= tp1)     # runner returns DOWN to tp1 (long)
        hit_be  = (lo <= be) if d > 0 else (hi >= be)
        # ratchet runner: tp2 or tp1, stop-first within the bar
        if RA is None:
            if hit_tp1 and hit_tp2: RA = tp1R              # conservative: the ratchet stop first
            elif hit_tp1:           RA = tp1R
            elif hit_tp2:           RA = tp2R
        # held-BE runner: tp2 or BE, stop-first
        if A is None:
            if hit_be and hit_tp2:  A = 0.0
            elif hit_be:            A = 0.0
            elif hit_tp2:           A = tp2R
        if A is not None and RA is not None:
            reason = "both resolved"
            return A, RA, reason
    # open at data end -> mark to the last close for whichever is unresolved
    if fwd:
        c = fwd[-1][4]; mR = ((c - entry) / risk) if d > 0 else ((entry - c) / risk)
        if A is None: A = mR
        if RA is None: RA = mR
        return A, RA, "mark-to-end"
    return None, None, "no-fwd-bars"

def main():
    rows = []; gaps = []; cache = {}
    for af in sorted(glob.glob(f"{CJ}/*.actions.csv")):
        base = os.path.basename(af)
        if base.startswith("AA_") or ".inv." in base: continue
        sym = base.split("_")[0]
        journal = af[:-len(".actions.csv")] + ".csv"
        h4p = f"{H4}/{sym}.h4.csv"
        for a in csv.DictReader(open(af, encoding="utf-8", errors="replace")):
            if a.get("action") != "RATCHET_TP1": continue
            sid = a["signal_id"]
            if not os.path.exists(journal): gaps.append((base, sid, "journal missing")); continue
            r = sigrow(journal, sid)
            if r is None: gaps.append((base, sid, "signal row not found")); continue
            if not os.path.exists(h4p): gaps.append((base, sid, f"no H4 dump for {sym}")); continue
            if h4p not in cache: cache[h4p] = load_h4(h4p)
            bt = dt(a["bar_time"])
            A, RA, reason = walk(r, cache[h4p], bt)
            actual = float(r["r_multiple"]) if (r["r_multiple"] or "").strip() else 0.0
            rows.append((base.replace(".actions.csv", ""), sym, sid, r.get("strategy", "?"),
                         r["direction"], actual, A, RA, reason,
                         float(a.get("open_r") or "nan")))

    print("# Item 2 — manual SL->TP1 ratchet: held-BE counterfactual + trial ledger\n")
    if not rows:
        print("_No RATCHET_TP1 actions found yet — the button ships this batch; the ledger fills as the "
              "trader uses it (interactive sessions only; never fires in AA)._")
        if gaps:
            print("\n## Coverage gaps\n"); [print(f"- {g}") for g in gaps]
        return
    print("| window | # | strat | dir | booked R | held-BE runner (A) | ratchet runner | dR (A-ratchet) | read |")
    print("|---|---|---|---|---|---|---|---|---|")
    ledger = []
    for win, sym, sid, strat, dr, act, A, RA, reason, orr in rows:
        d = (A - RA) if (A is not None and RA is not None) else float("nan")
        read = ("ratchet cut a winner" if d > 0.05 else
                "ratchet saved a fade" if d < -0.05 else "no difference")
        print(f"| {win} | {sid} | {strat} | {dr} | {act:+.2f} | "
              f"{'' if A is None else f'{A:+.2f}'} | {'' if RA is None else f'{RA:+.2f}'} | "
              f"{d:+.2f} | {read} |")
        ledger.append((win, sid, act, A, RA, d))
    print(f"\n## Ratchet trial ledger ({len(ledger)}/10 uses)\n")
    print("| use | window #sid | booked R | held-BE (A) | ratchet | dR | verdict |")
    print("|---|---|---|---|---|---|---|")
    for i, (win, sid, act, A, RA, d) in enumerate(ledger, 1):
        v = "ratchet-hurt" if d > 0.05 else "ratchet-helped" if d < -0.05 else "neutral"
        print(f"| {i} | {win} #{sid} | {act:+.2f} | {'' if A is None else f'{A:+.2f}'} | "
              f"{'' if RA is None else f'{RA:+.2f}'} | {d:+.2f} | {v} |")
    if gaps:
        print("\n## Coverage gaps\n"); [print(f"- {g}") for g in gaps]

if __name__ == "__main__":
    main()
