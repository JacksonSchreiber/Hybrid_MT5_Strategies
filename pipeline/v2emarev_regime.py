#!/usr/bin/env python3
"""EMArev final accounting (coach 2026-09-09, part A).

(1) Regime x with_trend cut of the 620-signal AA EMArev cohort under doctrine-v2 exits
    (hybrid v2_r: old_r on no-bank, EA shadow-observer on bank-fires). regime/with_trend
    are joined from the byte-identical 09-08 study CSVs by (symbol, signal_time). This
    closes the 2026-08-28 with-trend-fade watch item.
    RETIREMENT BAR: unless some regime cell shows avg >= +0.15R, n >= 60, both date halves
    positive, EMArev alerts are disabled after window 10b.

(2) Trader lifetime EMArev approve ledger, deduped by (symbol, signal_time) across all
    interactive (non-AA) journals (windows overlap; prefer the closed row).
"""
import csv, glob, sys
from datetime import datetime
from statistics import mean

CJ = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
PASS_AVG, PASS_N = 0.15, 60


def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s.strip(), f)
        except ValueError: pass
    return None


# ---- regime map from the 09-08 study CSVs (col29 regime, col30 with_trend) ----
def load_regime():
    m = {}
    for f in glob.glob("data/study/mfe/*.study.csv"):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            if r["strategy"] != "EMArev":
                continue
            m[(r["symbol"], r["signal_time"].strip())] = (
                (r["regime"] or "").strip() or "BLANK",
                (r["with_trend"] or "").strip() or "BLANK")
    return m


def load_cohort():
    reg = load_regime()
    rows = []
    for f in glob.glob("data/study/v2emarev/*.v2.csv"):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            if r["strategy"] != "EMArev" or r["closed"] != "1" or r["v2_runner"] == "-1":
                continue
            if r["decision"] not in ("approved", "approved_pending"):
                continue
            t = dt(r["signal_time"]); sym = r["symbol"]
            if (sym == "US500.dk" and t and t.year == 2014) or \
               (sym == "GBPUSD.dk" and t and t.year == 2019):
                continue
            obs = float(r["v2_r"]); mfe = float(r["mfe_r"]); trig = float(r["trigr"])
            oldr = float(r["old_r"]) if (r["old_r"] or "").strip() else obs
            hyb = oldr if mfe < trig else obs
            rg, wt = reg.get((sym, r["signal_time"].strip()), ("BLANK", "BLANK"))
            rows.append(dict(sym=sym, t=t, v2r=hyb, regime=rg, wt=wt))
    return rows


def stat(rows):
    n = len(rows)
    if n == 0:
        return (0, None, None, None, None)
    avg = mean(x["v2r"] for x in rows)
    wr = 100.0 * sum(1 for x in rows if x["v2r"] > 0) / n
    srt = sorted(rows, key=lambda x: x["t"]); h = n // 2
    ea = mean(x["v2r"] for x in srt[:h]) if h else None
    la = mean(x["v2r"] for x in srt[h:]) if h else None
    return (n, avg, wr, ea, la)


def verdict(n, avg, ea, la):
    if n < PASS_N or avg is None:
        return "fail (n<60)"
    ok = avg >= PASS_AVG and ea is not None and ea > 0 and la is not None and la > 0
    return "**PASS**" if ok else "fail"


def line(name, rows):
    n, avg, wr, ea, la = stat(rows)
    if n == 0:
        return f"| {name} | 0 | - | - | - | - | — |"
    return f"| {name} | {n} | {avg:+.3f} | {wr:3.0f}% | {ea:+.3f} | {la:+.3f} | {verdict(n,avg,ea,la)} |"


# ---- trader lifetime approve ledger (dedup across overlapping interactive journals) ----
def load_ledger():
    seen = {}   # (sym, signal_time) -> dict(decision, r, closed)
    for f in glob.glob(f"{CJ}/*.csv"):
        b = f.split("/")[-1]
        if b.startswith("AA_") or ".inv." in b or ".part." in b or "actions" in b or "/baselines/" in f:
            continue
        try:
            reader = csv.DictReader(open(f, encoding="utf-8", errors="replace"))
            fields = reader.fieldnames or []
            if not {"strategy", "symbol", "signal_time"}.issubset(set(fields)):
                continue
            rd = list(reader)
        except Exception:
            continue
        for r in rd:
            if (r.get("strategy") or "") != "EMArev":
                continue
            if not (r.get("symbol") and r.get("signal_time")):
                continue
            k = (r["symbol"], r["signal_time"].strip())
            rm = (r.get("r_multiple") or "").strip()
            closed = rm != ""
            rec = dict(decision=(r.get("decision") or "").strip(),
                       r=(float(rm) if closed else None), closed=closed)
            # prefer a closed row (full R) over an open one; else keep first
            if k not in seen or (closed and not seen[k]["closed"]):
                seen[k] = rec
    return list(seen.values())


def main():
    rows = load_cohort()
    print("# EMArev final accounting (part A)\n")
    print(f"_AA cohort: {len(rows)} approved EMArev signals, doctrine-v2 hybrid R, "
          f"regime joined from the 09-08 study. Retirement bar: some regime cell "
          f"avg >= +{PASS_AVG}R, n >= {PASS_N}, both date halves positive._\n")

    hdr = ("| cell | n | avg R | WR | early½ | late½ | verdict |\n"
           "|---|---|---|---|---|---|---|")
    print("## By regime\n" + hdr)
    print(line("ALL", rows))
    for g in ("TREND_UP", "TREND_DOWN", "CHOP", "BLANK"):
        print(line(g, [x for x in rows if x["regime"] == g]))
    print()

    print("## By with_trend (fade watch item)\n" + hdr)
    for w, lab in (("1", "with-trend (wt=1)"), ("0", "counter-trend (wt=0)"), ("BLANK", "chop/blank")):
        print(line(lab, [x for x in rows if x["wt"] == w]))
    print()

    print("## Regime x with_trend\n" + hdr)
    for g in ("TREND_UP", "TREND_DOWN", "CHOP"):
        for w in ("1", "0"):
            print(line(f"{g} x wt={w}", [x for x in rows if x["regime"] == g and x["wt"] == w]))
    print()

    # ledger
    led = load_ledger()
    appr = [x for x in led if x["decision"] in ("approved", "approved_pending")]
    appr_closed = [x for x in appr if x["closed"]]
    skip = [x for x in led if x["decision"] in ("skip", "skipped")]
    rej = [x for x in led if x["decision"] == "rejected"]
    print("## Trader lifetime EMArev approve ledger (deduped by symbol+signal_time across "
          "all interactive journals)\n")
    print(f"- unique EMArev signals presented: **{len(led)}**")
    print(f"- **approved: {len(appr)}** | skipped: {len(skip)} | rejected (safety gate): {len(rej)}")
    if appr_closed:
        tot = sum(x["r"] for x in appr_closed)
        print(f"- of the {len(appr)} approvals, **{len(appr_closed)} reached a closed "
              f"outcome** (the other {len(appr)-len(appr_closed)} were still open at their "
              f"window's end — windows overlap and many are short, so this is a biased "
              f"closed-subset, NOT a clean lifetime P&L):")
        print(f"  - total R: **{tot:+.2f}** | avg R/closed-approve: **{tot/len(appr_closed):+.3f}** "
              f"| WR: {100.0*sum(1 for x in appr_closed if x['r']>0)/len(appr_closed):.0f}% "
              f"(pre-v2 exits, trader's discretion)")
    print()

    # ruling
    cells = [("ALL", rows)]
    for g in ("TREND_UP", "TREND_DOWN", "CHOP", "BLANK"):
        cells.append((g, [x for x in rows if x["regime"] == g]))
    for g in ("TREND_UP", "TREND_DOWN", "CHOP"):
        for w in ("1", "0"):
            cells.append((f"{g}xwt{w}", [x for x in rows if x["regime"] == g and x["wt"] == w]))
    passers = []
    for name, rs in cells:
        n, avg, wr, ea, la = stat(rs)
        if n >= PASS_N and avg is not None and avg >= PASS_AVG and ea and la and ea > 0 and la > 0:
            passers.append((name, n, avg))
    print("## Retirement ruling\n")
    if passers:
        print(f"**{len(passers)} regime cell(s) clear the bar — EMArev alerts survive:**")
        for name, n, avg in passers:
            print(f"- {name}: n={n}, avg={avg:+.3f}R")
    else:
        print("**No regime cell clears avg>=+0.15R with n>=60 and both date halves "
              "positive.** Per the pre-registered rule, EMArev alerts are DISABLED after "
              "window 10b (detector code retained, alert flag off).")


if __name__ == "__main__":
    main()
