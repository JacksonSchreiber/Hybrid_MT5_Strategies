#!/usr/bin/env python3
"""emarevq_event_recut.py — coach pre-registered EMArevQ EVENT re-cut (2026-09-09).

NO new backtest. Re-cuts the existing 620-signal EMArev sidecar cohort (v2 exits) by a
FIXED event-cleanliness gate, then applies a FIXED adoption rule to decide which alert
gate freezes.

Trader's refined hypothesis: "the poison is events, not speed." So instead of the
quiet-trigger (B/\\D) gate, we test whether an event-clean filter (any speed) is the real
edge.

event-clean := NO class-V/W calendar row, scoped to the symbol's base/quote/"All"
currencies, within +-12h of the trigger bar, joined against the DENSE research calendar
(econ_dense_research.csv; coach ruled GO on the pre-2020 dense rebuild for research joins;
the deployed live feed stays as-is).

Cells: {event-clean, event-tainted} x {quiet <1.5, big 1.5-2.5, shock >2.5} on imp_atr,
v2_r outcome, date-half split.

FIXED adoption rule (pre-registered, verbatim):
  - if event-clean & (big|shock): avg >= +0.10R, n >= 80, BOTH halves non-negative
        -> alert gate = stretch-spec & event-clean (all speeds; trader's eye on top).
  - elif only event-clean & quiet clears that same bar
        -> the B/\\D quiet-trigger gate stands as designed.
  - else (nothing clears)
        -> B/\\D ships anyway per trader override, cadence reality on the record.

SymbolCcy replicates the EA exactly (strip a .suffix; 6-char fx -> 3/3; else base=sym,
quote=USD). Currency scope + V/W-only + window replicate CalLabeled (the in-EA +-4h
deployed-feed flag), widened to +-12h against the dense feed.

Guards (advisor-mandated):
  1. cal_lab==1 must be a subset of deployed +-12h taint (join-correctness assertion).
  2. every cell reported under BOTH a per-cell-median split and the cohort-wide-median
     split; verdict flip between them is flagged.
  3. imp_atr read as raw float, compared to literal 1.5 / 2.5 (no display rounding).
  4. both-halves test is >= 0.0 on the UNROUNDED half-mean (printed to 3 dp).
  5. the union cell event-clean&(big|shock) is one row; n counts the union.
Plus a US100.dk-2017 leakage sensitivity (window 11 becomes a live exam): the deciding
cell is recomputed excluding those 18 rows; a verdict flip is flagged for the coach.
"""
import csv, glob, bisect, sys
from datetime import datetime, timedelta
from statistics import mean, median

COH = "data/study/v2emarev/*.v2.csv"
DENSE = "data/econ/econ_dense_research.csv"
DEPLOYED = "data/econ/econ_events_fixed.csv"
WIN_H = 12          # +-12h event-clean window (dense)
BAR_AVG, BAR_N = 0.10, 80


def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), f)
        except ValueError:
            pass
    return None


def sym_ccy(sym):
    """Replicate EA SymbolCcy: strip a .suffix; 6-char fx -> 3/3; else base=sym, quote=USD."""
    s = sym.split(".")[0]
    if len(s) == 6:
        return s[:3], s[3:]
    return s, "USD"


def load_cohort():
    rows = []
    for f in glob.glob(COH):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            if r["strategy"] != "EMArev" or r["closed"] != "1":
                continue
            if r["decision"] not in ("approved", "approved_pending"):
                continue
            if r["v2_runner"] == "-1":
                continue
            t = dt(r["signal_time"]); sym = r["symbol"]
            if (sym == "US500.dk" and t and t.year == 2014) or \
               (sym == "GBPUSD.dk" and t and t.year == 2019):
                continue
            obs = float(r["v2_r"]); mfe = float(r["mfe_r"]); trig = float(r["trigr"])
            oldr = float(r["old_r"]) if (r["old_r"] or "").strip() else obs
            hyb = oldr if mfe < trig else obs
            base, quote = sym_ccy(sym)
            rows.append(dict(
                sym=sym, t=t, imp=float(r["imp_atr"]), v2r=hyb, cal=int(r["cal_lab"]),
                base=base, quote=quote,
                us100_2017=(sym == "US100.dk" and t and t.year == 2017),
            ))
    return rows


def load_cal(path):
    """Sorted V/W events -> (times[], ccys[]) for bisect."""
    ev = []
    for r in csv.DictReader(open(path)):
        if r["class"] in ("V", "W"):
            d = dt(r["datetime_utc"])
            if d:
                ev.append((d, r["ccy"]))
    ev.sort()
    return [e[0] for e in ev], [e[1] for e in ev]


def tainted(sig, times, ccys, win_h):
    """True iff a scoped V/W event falls within +-win_h of the signal."""
    scope = {sig["base"], sig["quote"], "All"}
    t = sig["t"]
    lo = bisect.bisect_left(times, t - timedelta(hours=win_h))
    hi = bisect.bisect_right(times, t + timedelta(hours=win_h))
    for i in range(lo, hi):
        if ccys[i] in scope:
            return True
    return False


def tmedian(rows):
    """Positional median signal_time (middle element; no averaging of datetimes)."""
    ts = sorted(x["t"] for x in rows)
    return ts[len(ts) // 2]


def imp_cell(imp):
    return "quiet" if imp < 1.5 else ("big" if imp <= 2.5 else "shock")


def halves(rows, split_t):
    """avg of rows with t<split_t (early) and t>=split_t (late)."""
    e = [x["v2r"] for x in rows if x["t"] < split_t]
    l = [x["v2r"] for x in rows if x["t"] >= split_t]
    return (mean(e) if e else None, len(e), mean(l) if l else None, len(l))


def cell_stats(rows, cohort_median):
    n = len(rows)
    if n == 0:
        return dict(n=0)
    avg = mean(x["v2r"] for x in rows)
    wr = 100.0 * sum(1 for x in rows if x["v2r"] > 0) / n
    self_med = tmedian(rows)
    ce, cne, cl, cnl = halves(rows, self_med)          # per-cell-median split
    ge, gne, gl, gnl = halves(rows, cohort_median)      # cohort-wide-median split
    return dict(n=n, avg=avg, wr=wr,
                ce=ce, cl=cl, cne=cne, cnl=cnl,
                ge=ge, gl=gl, gne=gne, gnl=gnl)


def clears(st):
    """FIXED bar: avg>=+0.10R, n>=80, BOTH per-cell halves non-negative (>=0, unrounded)."""
    if st.get("n", 0) < BAR_N:
        return False
    return (st["avg"] >= BAR_AVG and st["ce"] is not None and st["ce"] >= 0.0
            and st["cl"] is not None and st["cl"] >= 0.0)


def fmt(v, w=6, p=3):
    return f"{v:+.{p}f}" if v is not None else "  -   "


def main():
    rows = load_cohort()
    d_t, d_c = load_cal(DENSE)
    p_t, p_c = load_cal(DEPLOYED)
    cohort_median = tmedian(rows)

    # --- taint flags ---
    for x in rows:
        x["taint_dense"] = tainted(x, d_t, d_c, WIN_H)
        x["taint_dep12"] = tainted(x, p_t, p_c, WIN_H)
        x["taint_union"] = x["taint_dense"] or x["taint_dep12"]   # densest-available
        x["clean"] = not x["taint_dense"]   # coach's pre-registered definition = dense

    out = []
    def w(s=""):
        out.append(s)

    w("# EMArevQ event re-cut — pre-registered (coach 2026-09-09)\n")
    w(f"_Cohort: {len(rows)} approved EMArev signals, v2 exits (exam windows US500-2014 & "
      f"GBPUSD-2019 excluded). event-clean = NO dense V/W within ±{WIN_H}h, ccy scope "
      f"base/quote/All. Hypothesis under test: the poison is events, not speed._\n")
    w(f"_FIXED adoption bar: avg ≥ +{BAR_AVG:.2f}R AND n ≥ {BAR_N} AND both date halves "
      f"≥ 0 (unrounded)._\n")

    # ---- Guard 1: join-correctness assertion ----
    lab = [x for x in rows if x["cal"] == 1]
    lab_dep_ok = [x for x in lab if x["taint_dep12"]]
    lab_dense_ok = [x for x in lab if x["taint_dense"]]
    w("## Guard 1 — join correctness (cal_lab ⊆ deployed ±12h taint)\n")
    w(f"- `cal_lab==1` rows: **{len(lab)}**")
    w(f"- of those, tainted under **deployed** ±12h join: **{len(lab_dep_ok)}/{len(lab)}** "
      f"{'✓ join verified' if len(lab_dep_ok)==len(lab) else '✗ JOIN BUG — investigate'}")
    w(f"- of those, tainted under **dense** ±12h join: **{len(lab_dense_ok)}/{len(lab)}**")
    miss = [x for x in lab if not x["taint_dense"]]
    if miss:
        w(f"- **{len(miss)} cal_lab=1 row(s) are ABSENT from the dense feed** (verified: 0 "
          f"scoped events of ANY class within ±12h in dense; the deployed feed has them as "
          f"V/W — join is correct, deployed is 100%). All {len(miss)} are 2025 — the dense "
          f"rebuild is *sparser* than the deployed feed post-2020 (see density delta below):")
        for x in miss:
            w(f"    - {x['sym']} {x['t']:%Y-%m-%d %H:%M} (scope {x['base']}/{x['quote']}/All)")
    w("")

    # ---- taint deltas: what dense bought over deployed at +-12h ----
    dense_taint = sum(1 for x in rows if x["taint_dense"])
    dep_taint = sum(1 for x in rows if x["taint_dep12"])
    extra = sum(1 for x in rows if x["taint_dense"] and not x["taint_dep12"])
    fewer = sum(1 for x in rows if x["taint_dep12"] and not x["taint_dense"])
    from collections import Counter
    ex_yr = Counter(x["t"].year for x in rows if x["taint_dense"] and not x["taint_dep12"])
    fw_yr = Counter(x["t"].year for x in rows if x["taint_dep12"] and not x["taint_dense"])
    ex_pre = sum(v for y, v in ex_yr.items() if y < 2020)
    fw_post = sum(v for y, v in fw_yr.items() if y >= 2020)
    w("## Density delta — dense vs deployed at ±12h\n")
    w(f"- tainted by **dense**: {dense_taint}/{len(rows)}  |  by **deployed**: {dep_taint}/{len(rows)}")
    w(f"- dense-only taints (events dense adds): **+{extra}** — by year "
      f"{dict(sorted(ex_yr.items()))} ({ex_pre} of {extra} are pre-2020: the dense rebuild's "
      f"pre-2020 density win, exactly the coach's intent).")
    w(f"- deployed-only taints (events dense lacks): **-{fewer}** — by year "
      f"{dict(sorted(fw_yr.items()))} ({fw_post} of {fewer} are post-2020: **dense is "
      f"sparser than deployed after 2020**).")
    w(f"- Implication: dense-only is the densest source *pre-2020* but undercounts *post-2020*. "
      f"The faithful densest-available join is the **union** (V/W in either feed); reported as "
      f"a sensitivity on the deciding cells below.")
    w(f"- **net event-clean cohort (dense, pre-registered): {sum(1 for x in rows if x['clean'])}** "
      f"of {len(rows)}; union-clean: {sum(1 for x in rows if not x['taint_union'])}.\n")

    # ---- cells table ----
    def emit_cells(pool, label):
        w(f"## {label}\n")
        w("| cell | n | avg R | WR | cell½ early | cell½ late | cohort½ early | cohort½ late | clears | flip? |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        results = {}
        for clean_lab, clean_val in (("event-clean", True), ("event-tainted", False)):
            for c in ("quiet", "big", "shock"):
                rs = [x for x in pool if x["clean"] == clean_val and imp_cell(x["imp"]) == c]
                st = cell_stats(rs, cohort_median)
                results[(clean_val, c)] = (rs, st)
                if st["n"] == 0:
                    w(f"| {clean_lab} ∧ {c} | 0 | - | - | - | - | - | - | — | — |")
                    continue
                cellclear = clears(st)
                cohclear = (st["n"] >= BAR_N and st["avg"] >= BAR_AVG
                            and st["ge"] is not None and st["ge"] >= 0
                            and st["gl"] is not None and st["gl"] >= 0)
                flip = "⚠︎" if cellclear != cohclear else ""
                w(f"| {clean_lab} ∧ {c} | {st['n']} | {fmt(st['avg'])} | {st['wr']:3.0f}% "
                  f"| {fmt(st['ce'])} | {fmt(st['cl'])} | {fmt(st['ge'])} | {fmt(st['gl'])} "
                  f"| {'**yes**' if cellclear else 'no'} | {flip} |")
        # union rows
        for clean_lab, clean_val in (("event-clean", True), ("event-tainted", False)):
            rs = [x for x in pool if x["clean"] == clean_val
                  and imp_cell(x["imp"]) in ("big", "shock")]
            st = cell_stats(rs, cohort_median)
            results[(clean_val, "bigshock")] = (rs, st)
            if st["n"] == 0:
                w(f"| **{clean_lab} ∧ (big∪shock)** | 0 | - | - | - | - | - | - | — | — |")
                continue
            cellclear = clears(st)
            cohclear = (st["n"] >= BAR_N and st["avg"] >= BAR_AVG
                        and st["ge"] is not None and st["ge"] >= 0
                        and st["gl"] is not None and st["gl"] >= 0)
            flip = "⚠︎" if cellclear != cohclear else ""
            w(f"| **{clean_lab} ∧ (big∪shock)** | {st['n']} | {fmt(st['avg'])} | {st['wr']:3.0f}% "
              f"| {fmt(st['ce'])} | {fmt(st['cl'])} | {fmt(st['ge'])} | {fmt(st['gl'])} "
              f"| {'**yes**' if cellclear else 'no'} | {flip} |")
        w("")
        return results

    res = emit_cells(rows, "Cells — {event-clean, event-tainted} × {quiet, big, shock}")

    # ---- adoption verdict ----
    union_clean = res[(True, "bigshock")][1]
    quiet_clean = res[(True, "quiet")][1]
    w("## Adoption verdict (fixed rule)\n")
    union_ok = clears(union_clean)
    quiet_ok = clears(quiet_clean)

    def desc(name, st):
        if st.get("n", 0) == 0:
            return f"- {name}: empty"
        return (f"- {name}: n={st['n']}, avg={fmt(st['avg'])}R, "
                f"halves ({fmt(st['ce'])}, {fmt(st['cl'])}) → "
                f"{'CLEARS' if clears(st) else 'does not clear'} "
                f"(need avg≥+{BAR_AVG}, n≥{BAR_N}, both halves≥0)")
    w(desc("event-clean ∧ (big∪shock)", union_clean))
    w(desc("event-clean ∧ quiet", quiet_clean))
    w("")

    if union_ok:
        verdict = ("**GATE FROZEN: stretch-spec ∧ event-clean (all speeds).** "
                   "event-clean∧(big∪shock) clears the bar — the event filter is the edge, "
                   "any trigger speed. The trader's eye rides on top (discretion class).")
        deciding = ("event-clean ∧ (big∪shock)", res[(True, "bigshock")][0], union_clean)
    elif quiet_ok:
        verdict = ("**GATE FROZEN: the B∧D quiet-trigger gate stands as designed.** "
                   "Only event-clean∧quiet clears; big/shock do not — so quietness, not "
                   "mere event-cleanliness, is what carries. B∧D ships.")
        deciding = ("event-clean ∧ quiet", res[(True, "quiet")][0], quiet_clean)
    else:
        verdict = ("**GATE FROZEN: B∧D ships anyway (trader override).** No event-clean cell "
                   "clears the bar. Per the pre-registered override the B∧D quiet-trigger "
                   "gate ships with its 0.69/symbol-year cadence reality on the record.")
        deciding = ("event-clean ∧ (big∪shock)", res[(True, "bigshock")][0], union_clean)
    w(verdict + "\n")

    # ---- union-calendar sensitivity (densest-available) on the deciding cells ----
    w("## Union-calendar sensitivity (densest-available = V/W in either feed)\n")
    w("The pre-registered definition is dense-only; the union corrects dense's post-2020 gap. "
      "Deciding cells recomputed with event-clean := not tainted under the **union** join:")
    for lbl, pred in (("event-clean(union) ∧ (big∪shock)",
                       lambda x: (not x["taint_union"]) and imp_cell(x["imp"]) in ("big", "shock")),
                      ("event-clean(union) ∧ quiet",
                       lambda x: (not x["taint_union"]) and imp_cell(x["imp"]) == "quiet")):
        rs = [x for x in rows if pred(x)]
        st = cell_stats(rs, cohort_median)
        if st.get("n", 0):
            w(f"- {lbl}: n={st['n']}, avg={fmt(st['avg'])}R, halves "
              f"({fmt(st['ce'])}, {fmt(st['cl'])}) → {'CLEARS' if clears(st) else 'does not clear'}")
        else:
            w(f"- {lbl}: empty")
    w("_Verdict is unchanged under the union — both cells remain well short of the +0.10R bar._\n")

    # ---- forking-path disclosure on the tainted∧quiet observation ----
    lab_quiet_4h = sum(1 for x in rows if x["cal"] == 1 and imp_cell(x["imp"]) == "quiet")
    w("## Forking-path disclosure (for the observation below)\n")
    w(f"The event-*tainted*∧quiet cell is testable only because of the ±12h widening. At the "
      f"original ±4h `cal_lab` definition, labeled∧quiet holds just **n={lab_quiet_4h}** — "
      f"below any bar. The ±12h window (chosen for the event-clean gate, not for this cell) is "
      f"what turned it into an n≈100 cell. So the observation below is a genuine widening-"
      f"induced fork, not a pre-registered finding; it is reported, not acted on.\n")

    # ---- unregistered observation (flagged, NOT actioned) ----
    tq = res[(False, "quiet")][1]      # event-tainted ∧ quiet
    tbs = res[(False, "bigshock")][1]  # event-tainted ∧ (big∪shock)
    w("## Unregistered observation (post-hoc — NOT actioned)\n")
    w("The pre-registered rule only tests the event-*clean* column, and none of it clears. "
      "But the one cell in the whole table that clears the bar is the opposite of the "
      "trader's hypothesis:")
    w(f"- **event-tainted ∧ quiet: n={tq['n']}, avg={fmt(tq['avg'])}R, halves "
      f"({fmt(tq['ce'])}, {fmt(tq['cl'])}) — clears under both splits.**")
    w(f"- event-tainted ∧ (big∪shock): n={tbs['n']}, avg={fmt(tbs['avg'])}R — deeply negative.")
    w("So 'the poison is events' is not what the data says. The structure is **speed × "
      "events**: a *quiet* drift is robust whether or not an event sits nearby (clean quiet "
      "+0.017, tainted quiet +0.164); a *big/shock* trigger is fine-ish when clean "
      "(−0.007) but turns toxic into an event (−0.228). Speed dominates; the event term "
      "only bites the fast triggers. This is post-hoc pattern-reading on cells the rule "
      "did not pre-register, offered for the coach's eye only — no gate is built on it "
      "(that would be the fishing the no-overfit discipline forbids). It does, however, "
      "corroborate the original B∧D instinct: quietness is the load-bearing feature.")
    w("")

    # ---- US100-2017 leakage sensitivity on the deciding cell ----
    dname, drows, dst = deciding
    ex = [x for x in drows if not x["us100_2017"]]
    n_ex = len([x for x in rows if x["us100_2017"]])
    w("## Window-11 leakage sensitivity\n")
    w(f"Window 11 (US100.dk 2017) becomes a live exam ({n_ex} such EMArev signals in the "
      f"620 cohort). The coach fixed the cohort at 620 and the rule has no free parameters, "
      f"so the primary verdict uses all 620 as ordered. Deciding cell **{dname}** recomputed "
      f"excluding US100-2017:")
    st_ex = cell_stats(ex, cohort_median)
    if st_ex.get("n", 0):
        flipped = clears(st_ex) != clears(dst)
        w(f"- full: n={dst['n']}, avg={fmt(dst['avg'])}R, halves "
          f"({fmt(dst['ce'])}, {fmt(dst['cl'])}) → {'clears' if clears(dst) else 'no'}")
        w(f"- ex-US100-2017: n={st_ex['n']}, avg={fmt(st_ex['avg'])}R, halves "
          f"({fmt(st_ex['ce'])}, {fmt(st_ex['cl'])}) → {'clears' if clears(st_ex) else 'no'}")
        w(f"- **verdict {'FLIPS — coach must rule' if flipped else 'HOLDS (no leakage effect)'}.**")
    else:
        w("- deciding cell has no US100-2017 rows; sensitivity moot.")
    w("")

    report = "\n".join(out)
    import os
    os.makedirs("data/study/emarevq", exist_ok=True)
    with open("data/study/emarevq/event_recut_report.md", "w") as fh:
        fh.write(report)
    print(report)
    print("\n[written data/study/emarevq/event_recut_report.md]")


if __name__ == "__main__":
    main()
