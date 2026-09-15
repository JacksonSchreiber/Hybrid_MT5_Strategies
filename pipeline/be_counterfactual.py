#!/usr/bin/env python3
"""BE-floor lifetime counterfactual (coach: item C 2026-09-08; the +0.25R floor +
[0.25,0.5) revert trial of 2026-09-09 was REVERTED 2026-09-10).

TRIAL OUTCOME (coach 2026-09-10): the pre-registered forward ledger governs — 2 uses,
and window-11 sig-20's Δ = −2.00 hit the ≤−2R trial tripwire, so the +0.25R floor is
reverted and the SL_BE floor is raised back to +0.5R. The [0.25,0.5) TRIAL band is
closed; a BE below +0.5R is now simply a VIOLATION. (The separate SL->TP1 button trial
is unaffected and stands as shipped — see ratchet_counterfactual.py.)

HARDWARE GATE (coach 2026-09-10, on the trader's word): +0.5R is now a LIVE gate, not
just a grading line — BEPlaceable() in HybridForwardTest.mq5 disables the SL->BE button
while Open R < +0.5R (BE_FLOOR_R=0.5, the same OpenR metric this tool reads from open_r).
So new below-floor VIOLATIONs cannot occur going forward; any VIOLATION rows here are
historical (pre-gate). The reversal-bar condition stays a grading judgment.

Sweeps EVERY main (interactive, non-AA, non-inverse) *.actions.csv for manual
SL_BE actions, reads the open R booked at each, and buckets it against the reverted
+0.5R discretionary floor:
    open_r <  0.50  -> VIOLATION  (below the floor; should not have been a BE)
    open_r >= 0.50  -> COMPLIANT

For each, the orig-stop-held counterfactual: a clean forward H4 bar-walk from the
signal's entry with the ORIGINAL stop held to the single target, conservative
intrabar (stop-first). Δ = counterfactual R − actual booked R; a positive Δ means
BE cut a would-be winner, negative means BE saved a would-be loss.

H4 bars: data/backtests/emarev_inv/<SYMBOL>.h4.csv. Missing coverage is reported,
never silently skipped.
"""
import csv, glob, os, sys
from datetime import datetime

CJ = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
H4 = "data/backtests/emarev_inv"
FLOOR = 0.50          # reverted 2026-09-10 from the 0.25 trial floor (trial tripped: sig-20 Δ−2.00)


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
    b.sort()
    return b


def sigrow(j, sid):
    for r in csv.DictReader(open(j, encoding="utf-8", errors="replace")):
        if r["signal_id"] == str(sid):
            return r
    return None


def bucket(orr):
    # Trial reverted 2026-09-10: the [0.25,0.5) TRIAL band is closed; below +0.5R is a VIOLATION.
    if orr < FLOOR: return "VIOLATION"
    return "COMPLIANT"


def counterfactual(r, bars):
    """orig-stop-held forward walk from entry to target; returns (cf_R, reason)."""
    d = 1 if r["direction"] == "BUY" else -1
    entry = float(r["entry"]); sl = float(r["orig_sl"])
    tp = float(r["tp1"] or r["tp"]); risk = abs(entry - sl)
    if risk <= 0: return None, "degenerate"
    et = dt(r["signal_time"])
    fwd = [x for x in bars if x[0] > et]
    for (t, o, hi, lo, c) in fwd:
        stop = (lo <= sl) if d > 0 else (hi >= sl)
        tph = (hi >= tp) if d > 0 else (lo <= tp)
        if stop and tph: return -1.0, "SL(stop-first)"
        if stop: return -1.0, "SL"
        if tph: return abs(tp - entry) / risk, "TP"
    if fwd:
        c = fwd[-1][4]
        return ((c - entry) / risk if d > 0 else (entry - c) / risk), "mark-to-end"
    return 0.0, "no-fwd-bars"


def main():
    rows = []            # (window, sym, sid, dir, actual, open_r, bkt, cf, reason)
    gaps = []
    bars_cache = {}
    for af in sorted(glob.glob(f"{CJ}/*.actions.csv")):
        base = os.path.basename(af)
        if base.startswith("AA_") or ".inv." in base:
            continue
        sym = base.split("_")[0]
        journal = af[:-len(".actions.csv")] + ".csv"
        h4p = f"{H4}/{sym}.h4.csv"
        for a in csv.DictReader(open(af, encoding="utf-8", errors="replace")):
            if a.get("action") != "SL_BE":
                continue
            sid = a["signal_id"]
            try: orr = float(a.get("open_r") or "nan")
            except ValueError: orr = float("nan")
            if not os.path.exists(journal):
                gaps.append((base, sid, "journal missing")); continue
            r = sigrow(journal, sid)
            if r is None:
                gaps.append((base, sid, "signal row not found")); continue
            actual = float(r["r_multiple"]) if (r["r_multiple"] or "").strip() else 0.0
            if not os.path.exists(h4p):
                gaps.append((base, sid, f"no H4 dump for {sym}")); continue
            if h4p not in bars_cache:
                bars_cache[h4p] = load_h4(h4p)
            cf, reason = counterfactual(r, bars_cache[h4p])
            rows.append((base.replace(".actions.csv", ""), sym, sid,
                         r["direction"], actual, orr, bucket(orr), cf, reason,
                         (r.get("strategy") or "?")))

    print("# Item C — lifetime SL_BE counterfactual (all main actions.csv)\n")
    print(f"_Discretionary BE floor = +{FLOOR}R (reverted 2026-09-10 from the 0.25 trial floor: the "
          f"[0.25,0.5) revert trial tripped — 2 forward uses, sig-20 Δ−2.00 ≤ −2R). A BE below "
          f"+{FLOOR}R is a VIOLATION; the TRIAL band is closed._\n")
    def read_of(cf, reason, act):
        # Reason-aware: BE protects against the ORIGINAL stop being hit. If the
        # orig-stop-held walk breaches SL, BE saved the loss; if it reaches target,
        # BE only "cut" a winner when that target beat what was actually booked
        # (a two-target runner can book MORE than the single-target counterfactual).
        if cf is None: return "-"
        if reason.startswith("SL"): return "BE saved a loss"
        if reason == "TP":
            return "BE cut a winner" if (cf - act) > 0.3 else "BE neutral (runner held)"
        return "inconclusive (open at data end)"

    print("_Counterfactual walk targets the strategy's single order level (tp1-or-tp): "
          "for SweepMSS that is the ~2R aux, so its TP-reason rows cap near +2.00R._\n")
    print("| window | # | strat | dir | booked R | open R @ BE | floor bucket | orig-stop-held R | Δ | read |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for win, sym, sid, dr, act, orr, bkt, cf, reason, strat in rows:
        cfs = f"{cf:+.2f} ({reason})" if cf is not None else "n/a"
        dlt = (cf - act) if cf is not None else float("nan")
        print(f"| {win} | {sid} | {strat} | {dr} | {act:+.2f} | {orr:+.2f} | {bkt} | {cfs} | "
              f"{dlt:+.2f} | {read_of(cf, reason, act)} |")

    # ---- bucket summary ---------------------------------------------------
    print("\n## Floor buckets\n")
    for b in ("VIOLATION", "COMPLIANT"):
        sub = [x for x in rows if x[6] == b]
        if not sub:
            print(f"- **{b}**: 0"); continue
        saved = sum(1 for x in sub if x[8].startswith("SL"))            # reason=SL
        cut = sum(1 for x in sub if x[8] == "TP" and (x[7] - x[4]) > 0.3)
        net = sum((x[7] - x[4]) for x in sub if x[7] is not None)
        print(f"- **{b}**: n={len(sub)}  BE-saved-a-loss={saved}  BE-cut-a-winner={cut}  "
              f"net Δ(orig−actual)={net:+.2f}R")

    print(f"\n## Revert trial — CLOSED (reverted 2026-09-10)\n")
    print(f"_The [0.25,0.5) revert trial is over: the pre-registered forward ledger reached 2 uses and "
          f"window-11 sig-20's Δ = −2.00 hit the ≤−2R tripwire, so the floor is reverted to +{FLOOR}R. "
          f"Below-floor BEs above now count as VIOLATIONs, not trial uses._")

    if gaps:
        print("\n## Coverage gaps (reported, not skipped)\n")
        for base, sid, why in gaps:
            print(f"- {base} #{sid}: {why}")
    else:
        print("\n_All SL_BE instances had journal + H4 coverage._")


if __name__ == "__main__":
    main()
