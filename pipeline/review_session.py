#!/usr/bin/env python3
"""review_session.py - grade a discretionary tester session against the
take-everything baseline, as an AI-coaching report card.

    ./pipeline/review_session.py --user <user_journal.csv> \
                                 --baseline <aa_all_journal.csv> [--out report.md]

Inputs are HybridForwardTest journals (Common\\Files\\journal\\<SYM>_<from>_<to>.csv):
  * --user      : an interactive session (decisions approved / skipped /
                  approved_pending / expired, with edits + skip_reason).
  * --baseline  : an AA_ALL headless run over the SAME symbol + window
                  (every signal taken at the detector's ORIGINAL levels), from
                    ./pipeline/mt5_verify.sh --mode ALL --strat ... --from ... --to ...

Join rule: user<->baseline rows are matched on (strategy, signal_time). If a
signal_time has no exact baseline match (signal populations can differ between
runs because pending orders suppress differently), fall back to the same-strategy
baseline row with the nearest signal_time within JOIN_TOL_BARS H4 bars; beyond
that it is treated as "no baseline" (edit/skip outcomes marked N/A, never faked).

Everything is read in R (realised r_multiple) except the FTMO breach check, which
walks raw closed PnL (hypothetical tester equity - the habit signal is the point;
custom-symbol tick_value can make PnL only approximate, per detectors-implementation.md).

stdlib only (csv, argparse, datetime, statistics).
"""
import argparse, csv, sys
from datetime import datetime, timedelta
from statistics import median

ACCOUNT_EQUITY   = 25000.0
DAILY_LOSS_LIMIT = 0.05 * ACCOUNT_EQUITY   # $1,250  (FTMO 5% daily)
MAX_DD_LIMIT     = 0.10 * ACCOUNT_EQUITY   # $2,500  (FTMO 10% overall)
H4               = timedelta(hours=4)
JOIN_TOL_BARS    = 1                        # nearest-bar fallback tolerance
MIN_STRAT_N      = 10                       # suppress per-strategy stats below this
TRADED           = ("approved", "approved_pending")
SKIP_REASONS = {0: "-", 1: "counter-trend", 2: "news/event", 3: "ugly structure",
                4: "target obstructed", 5: "correlated", 6: "gut/other"}


def parse_dt(s):
    s = (s or "").strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def fnum(s):
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def inum(s):
    v = fnum(s)
    return int(v) if v is not None else None


def load(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["_t"]    = parse_dt(r.get("signal_time"))
        r["_r"]    = fnum(r.get("r_multiple"))
        r["_pnl"]  = fnum(r.get("pnl"))
        r["_ms"]   = inum(r.get("decision_ms"))
        r["_exit"] = parse_dt(r.get("exit_time"))
        r["decision"] = (r.get("decision") or "").strip()
        r["strategy"] = (r.get("strategy") or "").strip()
        # Authoritative flags from the EA, NOT inferred from prices: the `entry`
        # column is overwritten with the realised fill (~never equals the
        # proposal), so orig_entry-vs-entry would flag every row as edited.
        r["_edited"]       = (r.get("edited") or "").strip() == "1"
        r["_entry_edited"] = (r.get("is_pending") or "").strip() == "1"
    return rows


class Baseline:
    """(strategy, signal_time) lookup with nearest-bar fallback."""
    def __init__(self, rows):
        self.exact = {}
        self.by_strat = {}
        for r in rows:
            if r["_t"] is None:
                continue
            self.exact[(r["strategy"], r["_t"])] = r
            self.by_strat.setdefault(r["strategy"], []).append(r)
        for lst in self.by_strat.values():
            lst.sort(key=lambda x: x["_t"])

    def match(self, urow):
        t = urow["_t"]
        if t is None:
            return None
        hit = self.exact.get((urow["strategy"], t))
        if hit:
            return hit
        best, bestd = None, None
        for r in self.by_strat.get(urow["strategy"], []):
            d = abs((r["_t"] - t).total_seconds())
            if bestd is None or d < bestd:
                best, bestd = r, d
        if best is not None and bestd <= JOIN_TOL_BARS * H4.total_seconds():
            return best
        return None


def pct(num, den):
    return (100.0 * num / den) if den else 0.0


def r_sum(rows):
    return sum(r["_r"] for r in rows if r["_r"] is not None)


# ------------------------------------------------------------------ sections
def sec_discretion(u, b, out):
    out.append("## Discretion alpha\n")
    u_traded = [r for r in u if r["decision"] in TRADED]
    b_traded = [r for r in b if r["decision"] in TRADED]
    uR, bR = r_sum(u_traded), r_sum(b_traded)
    n_appr = len(u_traded)
    n_skip = sum(1 for r in u if r["decision"] == "skipped")
    n_exp  = sum(1 for r in u if r["decision"] == "expired")
    n_edit = sum(1 for r in u if r["decision"] in TRADED and r["_edited"])
    out.append(f"- User realised total: **{uR:+.2f}R** over {n_appr} taken "
               f"(closed rows only). Skipped {n_skip}; edited {n_edit}; "
               f"expired {n_exp} (pending intended-to-trade but never filled → 0R, "
               f"counted as neither a trade nor a skip below).")
    out.append(f"- Baseline (take-everything) total: **{bR:+.2f}R** over "
               f"{len(b_traded)} signals.")
    out.append(f"- **Discretion alpha: {uR - bR:+.2f}R** (user minus take-everything).\n")
    # per strategy
    strats = sorted({r["strategy"] for r in u if r["strategy"]})
    out.append("| Strategy | n taken | user R | baseline R | alpha |")
    out.append("|---|---:|---:|---:|---:|")
    for s in strats:
        ut = [r for r in u_traded if r["strategy"] == s]
        bt = [r for r in b_traded if r["strategy"] == s]
        if len(ut) < MIN_STRAT_N:
            out.append(f"| {s} | {len(ut)} | _insufficient sample (<{MIN_STRAT_N})_ | | |")
            continue
        us, bs = r_sum(ut), r_sum(bt)
        out.append(f"| {s} | {len(ut)} | {us:+.2f} | {bs:+.2f} | {us - bs:+.2f} |")
    out.append("")


def sec_skips(u, bl, out):
    out.append("## Skip precision\n")
    skips = [r for r in u if r["decision"] == "skipped"]
    if not skips:
        out.append("_No skips in this session._\n")
        return
    good, bad, unknown = 0, [], 0
    for r in skips:
        m = bl.match(r)
        if m is None or m["_r"] is None:
            unknown += 1
            continue
        if m["_r"] < 0:
            good += 1
        else:
            bad.append((r, m))
    judged = good + len(bad)
    out.append(f"- Skips: **{len(skips)}** (baseline outcome known for {judged}; "
               f"{unknown} had no baseline match).")
    if judged:
        out.append(f"- **Good skips (baseline was a loss): {good}/{judged} "
                   f"= {pct(good, judged):.0f}%** (n={judged}).")
    if bad:
        out.append(f"\n**Bad skips (skipped a baseline winner) - {len(bad)}:**")
        out.append("| signal_time | strategy | skip_reason | baseline R |")
        out.append("|---|---|---|---:|")
        for r, m in sorted(bad, key=lambda x: -(x[1]["_r"] or 0)):
            code = inum(r.get("skip_reason")) or 0
            out.append(f"| {r.get('signal_time')} | {r['strategy']} | "
                       f"{code} {SKIP_REASONS.get(code,'?')} | {m['_r']:+.2f} |")
    # bad approves: taken + closed + user R < 0
    bad_appr = [r for r in u if r["decision"] in TRADED and r["_r"] is not None and r["_r"] < 0]
    if bad_appr:
        out.append(f"\n**Bad approves (took a loser) - {len(bad_appr)}:**")
        out.append("| signal_time | strategy | user R | baseline R |")
        out.append("|---|---|---:|---:|")
        for r in sorted(bad_appr, key=lambda x: x["_r"]):
            m = bl.match(r)
            br = f"{m['_r']:+.2f}" if (m and m["_r"] is not None) else "N/A"
            out.append(f"| {r.get('signal_time')} | {r['strategy']} | {r['_r']:+.2f} | {br} |")
    out.append("")


def sec_edits(u, bl, out):
    out.append("## Edit delta\n")
    edited = [r for r in u if r["decision"] in TRADED and r["_edited"]]
    if not edited:
        out.append("_No edited setups in this session._\n")
        return
    out.append("Realised R on edited setups vs the R the ORIGINAL levels would have "
               "produced (baseline proxy; only clean when the entry was NOT edited).\n")
    out.append("| signal_time | strategy | entry edited? | user R | orig-levels R | delta |")
    out.append("|---|---|---|---:|---:|---:|")
    for r in edited:
        m = bl.match(r)
        ur = r["_r"]
        ustr = f"{ur:+.2f}" if ur is not None else "open"
        if r["_entry_edited"] or m is None or m["_r"] is None or ur is None:
            out.append(f"| {r.get('signal_time')} | {r['strategy']} | "
                       f"{'yes' if r['_entry_edited'] else 'no'} | {ustr} | N/A | N/A |")
        else:
            out.append(f"| {r.get('signal_time')} | {r['strategy']} | no | "
                       f"{ur:+.2f} | {m['_r']:+.2f} | {ur - m['_r']:+.2f} |")
    out.append("")


def sec_latency(u, out):
    out.append("## Decision latency\n")
    ms = [r["_ms"] for r in u if r["_ms"] is not None and r["_ms"] > 0]
    if not ms:
        out.append("_No decision timings recorded (headless run?)._\n")
        return
    ms_sorted = sorted(ms)
    p90 = ms_sorted[min(len(ms_sorted) - 1, int(round(0.9 * (len(ms_sorted) - 1))))]
    out.append(f"- Median **{median(ms):.0f} ms**, p90 **{p90} ms** (n={len(ms)}).")
    # fastest quartile outcome split
    q = max(1, len(ms_sorted) // 4)
    cutoff = ms_sorted[q - 1]
    fast = [r for r in u if r["_ms"] is not None and 0 < r["_ms"] <= cutoff
            and r["decision"] in TRADED and r["_r"] is not None]
    if fast:
        wins = sum(1 for r in fast if r["_r"] > 0)
        out.append(f"- Fastest-quartile decisions (<= {cutoff} ms), taken & closed: "
                   f"{wins}/{len(fast)} winners = {pct(wins, len(fast)):.0f}% "
                   f"(n={len(fast)}) - watch for impulse-clicking if this lags your overall.")
    out.append("")


def sec_ftmo(u, out):
    out.append("## FTMO breach check\n")
    out.append(f"_Hypothetical on tester equity (${ACCOUNT_EQUITY:.0f}); raw PnL, so only "
               "as accurate as custom-symbol tick_value. Habit signal, not a verdict._\n")
    closed = [r for r in u if r["_exit"] is not None and r["_pnl"] is not None]
    if not closed:
        out.append("_No closed trades with PnL._\n")
        return
    closed.sort(key=lambda r: r["_exit"])
    # daily loss
    daily = {}
    for r in closed:
        daily.setdefault(r["_exit"].date(), 0.0)
        daily[r["_exit"].date()] += r["_pnl"]
    breach_days = [(d, v) for d, v in sorted(daily.items()) if v <= -DAILY_LOSS_LIMIT]
    if breach_days:
        out.append(f"- **Daily 5% loss (${DAILY_LOSS_LIMIT:.0f}) breached on {len(breach_days)} day(s):**")
        for d, v in breach_days:
            out.append(f"  - {d}: {v:+.2f}")
    else:
        worst = min(daily.values())
        out.append(f"- Daily 5% loss: **OK** (worst day {worst:+.2f}, limit -{DAILY_LOSS_LIMIT:.0f}).")
    # max drawdown on running equity
    eq = ACCOUNT_EQUITY
    peak = eq
    trough_dd = 0.0
    breached_dd = False
    for r in closed:
        eq += r["_pnl"]
        peak = max(peak, eq)
        dd = peak - eq
        trough_dd = max(trough_dd, dd)
        if dd >= MAX_DD_LIMIT:
            breached_dd = True
    if breached_dd:
        out.append(f"- **Overall 10% drawdown (${MAX_DD_LIMIT:.0f}) breached** "
                   f"(max equity drop ${trough_dd:.2f}).")
    else:
        out.append(f"- Overall 10% drawdown: **OK** (max equity drop ${trough_dd:.2f}, "
                   f"limit ${MAX_DD_LIMIT:.0f}).")
    out.append("")


def main():
    ap = argparse.ArgumentParser(description="Grade a discretionary tester session vs the take-everything baseline.")
    ap.add_argument("--user", required=True, help="interactive session journal CSV")
    ap.add_argument("--baseline", required=True, help="AA_ALL headless journal CSV (same symbol+window)")
    ap.add_argument("--out", help="write markdown report here (default: stdout)")
    a = ap.parse_args()

    u = load(a.user)
    b = load(a.baseline)
    if not u:
        print("ERROR: user journal is empty", file=sys.stderr); sys.exit(1)
    bl = Baseline(b)

    sym = (u[0].get("symbol") or "?").strip()
    span = f"{u[0].get('signal_time','?')} .. {u[-1].get('signal_time','?')}"
    out = [f"# Session review - {sym}", f"_{span}_  ",
           f"user rows: {len(u)}   baseline rows: {len(b)}\n"]
    sec_discretion(u, b, out)
    sec_skips(u, bl, out)
    sec_edits(u, bl, out)
    sec_latency(u, out)
    sec_ftmo(u, out)
    report = "\n".join(out) + "\n"

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"wrote {a.out}")
    else:
        sys.stdout.write(report)


if __name__ == "__main__":
    main()
