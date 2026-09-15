#!/usr/bin/env python3
"""TrendCont pass-bar evaluation (coach 2026-09-09, part B). Reads the parked fleet
journals (data/study/trendcont/*.csv), doctrine-v2 exit R (r_multiple). FROZEN pass bar:
avg >= +0.15R AND n >= 150 fleet-wide AND both date halves positive (fleet-wide median
split) AND no single symbol > half the fleet net profit. Fail -> report and stop.

NOTE: the default dir data/study/trendcont/ is the model-4 study-B cohort captured BEFORE the
per-pullback re-arm (the study-B anchor: +0.060R, FAIL). It is NOT re-generated post-fix. For the
re-arm before/after (model-1), pass the glob explicitly, e.g.
  python3 trendcont_eval.py "data/study/trendcont_rearm/after/all/*.csv"
See data/study/trendcont_rearm/rearm_report.md."""
import csv, glob, sys
from datetime import datetime
from statistics import mean, median
from collections import defaultdict

SRC = sys.argv[1] if len(sys.argv) > 1 else "data/study/trendcont/*.csv"
PASS_AVG, PASS_N = 0.15, 150


def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s.strip(), f)
        except ValueError: pass
    return None


def load():
    rows = []
    for f in glob.glob(SRC):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            if r.get("strategy") != "TrendCont":
                continue
            if r.get("decision") not in ("approved", "approved_pending"):
                continue
            rm = (r.get("r_multiple") or "").strip()
            if rm == "":
                continue
            rows.append(dict(sym=r["symbol"], t=dt(r["signal_time"]), r=float(rm)))
    return rows


def main():
    rows = load()
    n = len(rows)
    print("# TrendCont — pre-registered pass-bar evaluation\n")
    if n == 0:
        print("No TrendCont trades parked. Run pipeline/run_trendcont_fleet.sh first."); return
    avg = mean(x["r"] for x in rows)
    net = sum(x["r"] for x in rows)
    wr = 100.0 * sum(1 for x in rows if x["r"] > 0) / n
    srt = sorted(rows, key=lambda x: x["t"])
    med = median(x["t"].timestamp() for x in rows)
    early = [x for x in srt if x["t"].timestamp() <= med]
    late = [x for x in srt if x["t"].timestamp() > med]
    ea = mean(x["r"] for x in early) if early else 0.0
    la = mean(x["r"] for x in late) if late else 0.0

    print(f"- fleet n = **{n}**, avg R = **{avg:+.3f}**, net R = {net:+.2f}, WR = {wr:.0f}%")
    print(f"- date split (fleet median {srt[len(srt)//2]['t'].date()}): "
          f"early n={len(early)} avg **{ea:+.3f}** | late n={len(late)} avg **{la:+.3f}**\n")

    print("## Per-symbol\n| symbol | n | avg R | net R | share of fleet net |")
    print("|---|---|---|---|---|")
    bysym = defaultdict(list)
    for x in rows:
        bysym[x["sym"]].append(x["r"])
    max_share = -1.0; max_sym = ""
    for s in sorted(bysym, key=lambda k: -sum(bysym[k])):
        sr = sum(bysym[s]); share = (sr / net) if net > 0 else float("nan")
        if net > 0 and share > max_share:
            max_share = share; max_sym = s
        shstr = f"{share*100:4.0f}%" if net > 0 else "  n/a"
        print(f"| {s} | {len(bysym[s])} | {mean(bysym[s]):+.3f} | {sr:+.2f} | {shstr} |")
    print()

    # frozen pass bar
    t1 = avg >= PASS_AVG
    t2 = n >= PASS_N
    t3 = ea > 0 and la > 0
    t4 = (net > 0) and (max_share <= 0.50)
    print("## Pass bar (all four required)\n")
    print(f"1. avg >= +{PASS_AVG}R : {avg:+.3f} → {'PASS' if t1 else 'FAIL'}")
    print(f"2. n >= {PASS_N} : {n} → {'PASS' if t2 else 'FAIL'}")
    print(f"3. both date halves > 0 : early {ea:+.3f}, late {la:+.3f} → {'PASS' if t3 else 'FAIL'}")
    if net > 0:
        print(f"4. no symbol > 50% of fleet profit : max {max_sym} {max_share*100:.0f}% → {'PASS' if t4 else 'FAIL'}")
    else:
        print(f"4. concentration : fleet net R ≤ 0 ({net:+.2f}) → moot (fails on avg anyway)")
    print()
    if t1 and t2 and t3 and t4:
        print("## Ruling: **PASS** — TrendCont takes EMArev's alert slot after 10b "
              "(trader trains on it in a fresh window before it counts).")
    else:
        print("## Ruling: **FAIL** — report and stop; no parameter search (pre-registered).")


if __name__ == "__main__":
    main()
