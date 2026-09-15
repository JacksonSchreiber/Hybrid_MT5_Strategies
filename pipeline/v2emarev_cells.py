#!/usr/bin/env python3
"""EMArev-by-trigger under doctrine v2 (coach pre-registered study, 2026-09-09).

Cohort: the fleet's approved EMArev signals, EXCLUDING the US500 2014 and GBPUSD 2019
exam windows. Segment by trigger impulse = max single-H4-bar range over the 3 bars
preceding the signal, in ATR(14) units (imp_atr, computed in-EA at signal time):
    <1.5 quiet | 1.5-2.5 big | >2.5 shock.
Secondary cuts: 1-bar (imp_nbig==1) vs 2-3-bar (imp_nbig>=2) impulses; calendar-labeled
(cal_lab==1: a symbol-relevant V/W event within +-4h) vs unlabeled.

Per cell: n, avg R/signal, WR, and a within-cell date-split holdout (median signal_time
-> early/late halves, each avg R). v2_r is the EA shadow-observer's exact doctrine-v2 R.

PRE-REGISTERED pass bar: a cell is tradeable iff avg >= +0.15R AND n >= 80 AND BOTH date
halves are positive. No cell passes -> the line closes permanently.
"""
import csv, glob, sys
from datetime import datetime
from statistics import mean, median

SRC = sys.argv[1] if len(sys.argv) > 1 else "data/study/v2emarev/*.v2.csv"
PASS_AVG, PASS_N = 0.15, 80


def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s.strip(), f)
        except ValueError: pass
    return None


def load():
    rows = []
    for f in glob.glob(SRC):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            if r["strategy"] != "EMArev" or r["closed"] != "1":
                continue
            if r["decision"] not in ("approved", "approved_pending"):
                continue
            if r["v2_runner"] == "-1":     # never traded / observer inactive
                continue
            t = dt(r["signal_time"]); sym = r["symbol"]
            exam = ((sym == "US500.dk" and t and t.year == 2014) or
                    (sym == "GBPUSD.dk" and t and t.year == 2019))
            if exam:
                continue
            # Most-accurate v2 R: for no-bank signals (never reached the bank trigger)
            # v2's exit is IDENTICAL to the old policy (same orig SL), so old_r is exact
            # (captures real SL slippage the observer idealises to -1.0). For bank-fires,
            # the tick observer is exact (models the BE stop + fills). Hybrid = best of both.
            obs = float(r["v2_r"]); mfe = float(r["mfe_r"]); trig = float(r["trigr"])
            oldr = float(r["old_r"]) if (r["old_r"] or "").strip() else obs
            hyb = oldr if mfe < trig else obs
            rows.append(dict(sym=sym, t=t, v2r=hyb, v2r_obs=obs,
                             imp=float(r["imp_atr"]), nbig=int(r["imp_nbig"]),
                             cal=int(r["cal_lab"])))
    return rows


def stat(rows):
    """n, avg, WR, early-half avg, late-half avg (split at median signal_time)."""
    n = len(rows)
    if n == 0:
        return (0, None, None, None, None)
    avg = mean(x["v2r"] for x in rows)
    wr = 100.0 * sum(1 for x in rows if x["v2r"] > 0) / n
    srt = sorted(rows, key=lambda x: x["t"])
    h = n // 2
    early = srt[:h] if h else srt
    late = srt[h:] if h else srt
    ea = mean(x["v2r"] for x in early) if early else None
    la = mean(x["v2r"] for x in late) if late else None
    return (n, avg, wr, ea, la)


def verdict(n, avg, ea, la):
    if n < PASS_N or avg is None:
        return "fail (n<80)" if n < PASS_N else "fail"
    ok = (avg >= PASS_AVG) and (ea is not None and ea > 0) and (la is not None and la > 0)
    return "**PASS**" if ok else "fail"


def line(name, rows):
    n, avg, wr, ea, la = stat(rows)
    if n == 0:
        return f"| {name} | 0 | - | - | - | - | — |"
    return (f"| {name} | {n} | {avg:+.3f} | {wr:3.0f}% | {ea:+.3f} | {la:+.3f} | "
            f"{verdict(n, avg, ea, la)} |")


def imp_cell(x):
    return "quiet" if x["imp"] < 1.5 else ("big" if x["imp"] <= 2.5 else "shock")


def main():
    rows = load()
    print("# EMArev-by-trigger under doctrine v2 — pre-registered cells\n")
    print(f"_Cohort: {len(rows)} approved EMArev signals (exam windows US500-2014 & "
          f"GBPUSD-2019 excluded). v2_r = hybrid (old_r on no-bank, EA shadow-observer on "
          f"bank-fires)._")
    print(f"_Pass bar: avg >= +{PASS_AVG}R AND n >= {PASS_N} AND both date halves positive._\n")

    hdr = ("| cell | n | avg R | WR | early½ | late½ | verdict |\n"
           "|---|---|---|---|---|---|---|")

    print("## Overall\n" + hdr)
    print(line("ALL", rows))
    print()

    print("## Primary — trigger-impulse magnitude\n" + hdr)
    for c in ("quiet", "big", "shock"):
        print(line(f"{c} ({'<1.5' if c=='quiet' else '1.5-2.5' if c=='big' else '>2.5'})",
                   [x for x in rows if imp_cell(x) == c]))
    print()

    print("_Robustness — hybrid avg (used above) vs observer-only avg (idealised -1.0 SL):_")
    for c in ("ALL", "quiet", "big", "shock"):
        rs = rows if c == "ALL" else [x for x in rows if imp_cell(x) == c]
        h = mean(x["v2r"] for x in rs); o = mean(x["v2r_obs"] for x in rs)
        print(f"- {c}: hybrid {h:+.3f}R | observer-only {o:+.3f}R")
    print()

    print("## Secondary A — impulse concentration (fleet-wide)\n" + hdr)
    print(line("1-bar (nbig==1)", [x for x in rows if x["nbig"] == 1]))
    print(line("2-3-bar (nbig>=2)", [x for x in rows if x["nbig"] >= 2]))
    print(line("0-bar (nbig==0)", [x for x in rows if x["nbig"] == 0]))
    print()

    print("## Secondary B — calendar (fleet-wide)\n" + hdr)
    print(line("calendar-labeled", [x for x in rows if x["cal"] == 1]))
    print(line("unlabeled", [x for x in rows if x["cal"] == 0]))
    print()

    print("## Full cross — magnitude x impulse-bar\n" + hdr)
    for c in ("quiet", "big", "shock"):
        for nb, lab in (("1", "1-bar"), ("2", "2-3-bar")):
            sub = [x for x in rows if imp_cell(x) == c and
                   (x["nbig"] == 1 if nb == "1" else x["nbig"] >= 2)]
            print(line(f"{c} x {lab}", sub))
    print()

    print("## Full cross — magnitude x calendar\n" + hdr)
    for c in ("quiet", "big", "shock"):
        for cl, lab in ((1, "labeled"), (0, "unlabeled")):
            print(line(f"{c} x {lab}", [x for x in rows if imp_cell(x) == c and x["cal"] == cl]))
    print()

    # final ruling
    all_cells = []
    all_cells.append(("ALL", rows))
    for c in ("quiet", "big", "shock"):
        all_cells.append((c, [x for x in rows if imp_cell(x) == c]))
    all_cells.append(("1-bar", [x for x in rows if x["nbig"] == 1]))
    all_cells.append(("2-3-bar", [x for x in rows if x["nbig"] >= 2]))
    all_cells.append(("0-bar", [x for x in rows if x["nbig"] == 0]))
    all_cells.append(("cal-labeled", [x for x in rows if x["cal"] == 1]))
    all_cells.append(("unlabeled", [x for x in rows if x["cal"] == 0]))
    for c in ("quiet", "big", "shock"):
        for nb, lab in (("1", "1-bar"), ("2", "2-3-bar")):
            all_cells.append((f"{c}x{lab}", [x for x in rows if imp_cell(x) == c and
                              (x["nbig"] == 1 if nb == "1" else x["nbig"] >= 2)]))
        for cl, lab in ((1, "labeled"), (0, "unlabeled")):
            all_cells.append((f"{c}x{lab}", [x for x in rows if imp_cell(x) == c and x["cal"] == cl]))
    passers = []
    for name, rs in all_cells:
        n, avg, wr, ea, la = stat(rs)
        if n >= PASS_N and avg is not None and avg >= PASS_AVG and ea and la and ea > 0 and la > 0:
            passers.append((name, n, avg))
    print("## Ruling\n")
    if passers:
        print(f"**{len(passers)} cell(s) clear the pre-registered bar:**")
        for name, n, avg in passers:
            print(f"- {name}: n={n}, avg={avg:+.3f}R")
    else:
        print("**No cell clears avg>=+0.15R with n>=80 and both date halves positive.** "
              "Per the pre-registered rule, the EMArev-by-trigger line closes permanently "
              "(third strike).")


if __name__ == "__main__":
    main()
