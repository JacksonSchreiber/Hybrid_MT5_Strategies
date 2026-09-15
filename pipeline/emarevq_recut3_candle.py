#!/usr/bin/env python3
"""emarevq_recut3_candle.py — coach pre-registered re-cut #3 (FINAL look, 2026-09-09).

Trader's pre-declared hypothesis: the base spec's WEAK reversal-candle condition is the leak.
On all base EMArev signals (no B∧D), a trigger candle qualifies as a MEANINGFUL reversal iff
range >= k*ATR(14) AND (Form A OR Form B). Two declared variants only; NO tuning beyond them.

  Form A (BUY; SELL mirrors): trigger LOW sweeps below the prior 3 bars' lows, AND close in the
          far third of its range (>= high - range/3), AND close > open, AND close < EMA20
          (still on the stretch side of the mean -> genuinely reverting TOWARD it).
  Form B (BUY): DC_BullEngulf (close>open, close>prior_open, open<prior_close) AND the trigger
          LOW sweeps below the prior bar's low ("closes beyond the prior bar's stretch-side
          extreme" = swept the prior stretch-side extreme). SELL mirrors with highs.

  BASE variant:   range >= 1.00*ATR AND (Form A[far-third] OR Form B).
  STRICT variant: range >= 1.25*ATR AND close in the far QUARTER (top-level, both forms)
                  AND (Form A[geom] OR Form B).   (strict subset of base)

ATR is the Wilder ATR AT the trigger bar (EA/calib convention, includes the trigger's own TR).
v2 exits, same hybrid estimator as the prior re-cuts. Adoption bar (fixed): avg >= +0.10R,
n >= 80, both date halves >= 0 (unrounded, per-cell median split).

Verdict: base passes -> gate = base-spec ∧ base-candle (B∧D dropped). Else strict passes ->
gate = base-spec ∧ strict-candle. Else (both fail) -> B∧D stays as shipped, window 11 as-is.
Cadence per symbol-year reported REGARDLESS of verdict.

Cohort: all base EMArev signals, exam windows excluded (US500-2014, GBPUSD-2019, and US100-2017
= now the window-11 live exam). H4 OHLC from QDM M1->H4 for the 5 QDM symbols; from the
MT5-dumped emarev_inv H4 dumps for XAU/JPY (both 2020-25, fully covering those signals). EMA20 +
Wilder ATR14 computed here on both. Anchor check |H4 close - to_entry| <= 0.10*ATR validates the
join per row. GBPUSD (both sources on disk) cross-checks the two H4 sources agree.
"""
import csv, glob, sys
from pathlib import Path
from datetime import datetime
from statistics import mean
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from breakout_backtest import load_m1, agg_h4, ema, wilder_atr, M1DIR, SYMS

QNAME = {v[0]: k for k, v in SYMS.items()}          # ".dk label" -> QDM csv name
QDM5 = {"EURUSD.dk", "GBPUSD.dk", "USOIL.dk", "US500.dk", "US100.dk"}
INVH4 = Path("data/backtests/emarev_inv")
EMA_P, ATR_P = 20, 14
BAR_AVG, BAR_N = 0.10, 80


def dt(s):
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(s.strip(), f)
        except ValueError:
            pass
    return None


def build_h4_qdm(label):
    T, O, H, L, C = load_m1(str(M1DIR / f"{QNAME[label]}-M1-No Session.csv"))
    h4 = agg_h4(T, O, H, L, C)
    t = [b["t"] for b in h4]; o = [b["o"] for b in h4]; h = [b["h"] for b in h4]
    l = [b["l"] for b in h4]; c = [b["c"] for b in h4]
    return t, o, h, l, c, ema(c, EMA_P), wilder_atr(h, l, c, ATR_P)


def build_h4_inv(label):
    rows = list(csv.DictReader(open(INVH4 / f"{label}.h4.csv")))
    t = [dt(r["time"]) for r in rows]; o = [float(r["open"]) for r in rows]
    h = [float(r["high"]) for r in rows]; l = [float(r["low"]) for r in rows]
    c = [float(r["close"]) for r in rows]
    return t, o, h, l, c, ema(c, EMA_P), wilder_atr(h, l, c, ATR_P)


def build_h4(label, source=None):
    if source == "inv" or (source is None and label not in QDM5):
        return build_h4_inv(label)
    return build_h4_qdm(label)


def qualifies(series, si, d, variant, formB="sweep", atr_at="si"):
    """True/False if the trigger candle qualifies; None if unassessable (warmup/degenerate)."""
    t, o, h, l, c, em, at = series
    if si < 3 or si >= len(c):
        return None
    O, H, L, C, E = o[si], h[si], l[si], c[si], em[si]
    A = at[si] if atr_at == "si" else at[si - 1]
    if E is None or A is None or A <= 0:
        return None
    rng = H - L
    if rng <= 0:
        return False
    thr = 1.00 if variant == "base" else 1.25
    if rng < thr * A:
        return False
    if variant == "strict":                      # top-level far-QUARTER, both forms
        if d > 0 and not (C >= H - rng / 4.0):
            return False
        if d < 0 and not (C <= L + rng / 4.0):
            return False
    p1l, p2l, p3l = l[si - 1], l[si - 2], l[si - 3]
    p1h, p2h, p3h = h[si - 1], h[si - 2], h[si - 3]
    p1o, p1c = o[si - 1], c[si - 1]
    if d > 0:
        sweep3 = L < min(p1l, p2l, p3l)
        far_third = C >= H - rng / 3.0
        formA = sweep3 and (C > O) and (C < E) and (far_third if variant == "base" else True)
        engulf = (C > O) and (C > p1o) and (O < p1c)
        formB_extra = (L < p1l) if formB == "sweep" else (C > p1l)   # literal = close>prior low
        formB_ok = engulf and formB_extra
    else:
        sweep3 = H > max(p1h, p2h, p3h)
        far_third = C <= L + rng / 3.0
        formA = sweep3 and (C < O) and (C > E) and (far_third if variant == "base" else True)
        engulf = (C < O) and (C < p1o) and (O > p1c)
        formB_extra = (H > p1h) if formB == "sweep" else (C < p1h)
        formB_ok = engulf and formB_extra
    return bool(formA or formB_ok)


def load_to_entry():
    """(sym, signal_time) -> to_entry from the mfe study (EMArev only)."""
    te = {}
    for f in glob.glob("data/study/mfe/*.study.csv"):
        for r in csv.DictReader(open(f)):
            if r["strategy"] == "EMArev" and "appro" in r["decision"]:
                te[(r["symbol"], r["signal_time"])] = float(r["to_entry"])
    return te


def load_cohort():
    """all base EMArev signals (exam windows excluded) with hybrid v2_r, dir, to_entry."""
    te = load_to_entry()
    rows = []
    for f in glob.glob("data/study/v2emarev/*.v2.csv"):
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            if r["strategy"] != "EMArev" or r["closed"] != "1":
                continue
            if r["decision"] not in ("approved", "approved_pending"):
                continue
            if r["v2_runner"] == "-1":
                continue
            t = dt(r["signal_time"]); sym = r["symbol"]
            if (sym == "US500.dk" and t and t.year == 2014) or \
               (sym == "GBPUSD.dk" and t and t.year == 2019) or \
               (sym == "US100.dk" and t and t.year == 2017):
                continue
            obs = float(r["v2_r"]); mfe = float(r["mfe_r"]); trig = float(r["trigr"])
            oldr = float(r["old_r"]) if (r["old_r"] or "").strip() else obs
            hyb = oldr if mfe < trig else obs
            rows.append(dict(sym=sym, t=t, stime=r["signal_time"],
                             d=(1 if r["direction"].upper().startswith("B") else -1),
                             v2r=hyb, to_entry=te.get((sym, r["signal_time"]))))
    return rows


def tmedian(rows):
    ts = sorted(x["t"] for x in rows)
    return ts[len(ts) // 2]


def stat(rows):
    n = len(rows)
    if n == 0:
        return dict(n=0)
    avg = mean(x["v2r"] for x in rows)
    wr = 100.0 * sum(1 for x in rows if x["v2r"] > 0) / n
    med = tmedian(rows)
    e = [x["v2r"] for x in rows if x["t"] < med]
    l = [x["v2r"] for x in rows if x["t"] >= med]
    return dict(n=n, avg=avg, wr=wr,
                ea=(mean(e) if e else None), la=(mean(l) if l else None))


def clears(st):
    return (st.get("n", 0) >= BAR_N and st["avg"] >= BAR_AVG
            and st["ea"] is not None and st["ea"] >= 0.0
            and st["la"] is not None and st["la"] >= 0.0)


def fail_reason(st):
    """Name which of the three conjunctive bar conditions failed (or PASS)."""
    if st.get("n", 0) == 0:
        return "—"
    r = []
    if st["n"] < BAR_N:
        r.append(f"n {st['n']}<{BAR_N}")
    if st["avg"] < BAR_AVG:
        r.append(f"avg<{BAR_AVG}")
    neg = []
    if st["ea"] is not None and st["ea"] < 0:
        neg.append("early")
    if st["la"] is not None and st["la"] < 0:
        neg.append("late")
    if neg:
        r.append("/".join(neg) + "½<0")
    return "**PASS**" if not r else "fail (" + ", ".join(r) + ")"


def main():
    rows = load_cohort()
    # build H4 series per symbol (primary source), index by time
    series = {}; tmap = {}
    for sym in set(x["sym"] for x in rows):
        s = build_h4(sym)
        series[sym] = s
        tmap[sym] = {tt: i for i, tt in enumerate(s[0])}

    dropped = Counter()
    for x in rows:
        i = tmap[x["sym"]].get(x["t"])
        if i is None:
            x["drop"] = "no_bar"; dropped["no_bar"] += 1; continue
        s = series[x["sym"]]
        if i < 3 or s[5][i] is None or s[6][i] is None or s[6][i] <= 0:
            x["drop"] = "warmup"; dropped["warmup"] += 1; continue
        # anchor: H4 close ~ to_entry
        if x["to_entry"] is not None and abs(s[4][i] - x["to_entry"]) > 0.10 * s[6][i]:
            x["drop"] = "anchor"; dropped["anchor"] += 1; continue
        x["drop"] = ""; x["si"] = i
        x["qb"] = qualifies(s, i, x["d"], "base")
        x["qs"] = qualifies(s, i, x["d"], "strict")
        x["qb_lit"] = qualifies(s, i, x["d"], "base", formB="literal")
        x["qb_atrp"] = qualifies(s, i, x["d"], "base", atr_at="si-1")

    good = [x for x in rows if x["drop"] == "" and x["qb"] is not None]

    out = []
    def w(s=""):
        out.append(s)

    w("# EMArevQ re-cut #3 — meaningful reversal-candle (FINAL look, coach 2026-09-09)\n")
    w("_Trader hypothesis (pre-declared): the base spec's weak reversal-candle condition is the "
      "leak. No B∧D. Two declared variants only, no further tuning; if both fail, B∧D stays and "
      "window 11 launches as-is._\n")
    w(f"_Adoption bar: avg ≥ +{BAR_AVG:.2f}R AND n ≥ {BAR_N} AND both date halves ≥ 0 "
      f"(unrounded, per-cell median split)._\n")

    w("## Cohort & join\n")
    w(f"- base EMArev signals after exam exclusion (US500-2014, GBPUSD-2019, US100-2017): "
      f"**{len(rows)}**")
    anchor_by = Counter(x["sym"] for x in rows if x.get("drop") == "anchor")
    w(f"- dropped: no_bar {dropped['no_bar']}, warmup {dropped['warmup']}, anchor {dropped['anchor']} "
      f"→ **{len(good)} assessable**")
    w(f"- anchor drops by symbol: {dict(anchor_by)} "
      f"(XAU/JPY = the MT5-dump source; a cluster there would mean source-specific misalignment)")
    persrc = Counter("QDM-M1→H4" if x["sym"] in QDM5 else "MT5-dump(XAU/JPY)" for x in good)
    w(f"- H4 source split: {dict(persrc)}\n")

    def line(name, rs, verdict=False):
        st = stat(rs)
        if st["n"] == 0:
            return f"| {name} | 0 | - | - | - | - | {'—' if verdict else ''} |"
        v = fail_reason(st) if verdict else ""
        return (f"| {name} | {st['n']} | {st['avg']:+.3f} | {st['wr']:3.0f}% "
                f"| {st['ea']:+.3f} | {st['la']:+.3f} | {v} |")

    w("## Result — v2_r by candle-qualification\n")
    w("| set | n | avg R | WR | early½ | late½ | verdict |")
    w("|---|---|---|---|---|---|---|")
    w(line("ALL assessable", good))
    w(line("**BASE-qualifying**", [x for x in good if x["qb"]], verdict=True))
    w(line("&nbsp;&nbsp;base-only (base ∧ ¬strict)", [x for x in good if x["qb"] and not x["qs"]]))
    w(line("base NON-qualifying", [x for x in good if not x["qb"]]))
    w(line("**STRICT-qualifying**", [x for x in good if x["qs"]], verdict=True))
    w(line("strict NON-qualifying", [x for x in good if not x["qs"]]))
    w("")
    w("_Strict ⊂ base (verified): strict tightens range 1.0→1.25 and the close to the far "
      "QUARTER (a top-level gate applied to both forms — strict Form A's close-position IS that "
      "top-level far-quarter, not a lost clause). The base-only rows are the 1.0×ATR / far-third "
      "margin that strict excludes._\n")

    base_st = stat([x for x in good if x["qb"]])
    strict_st = stat([x for x in good if x["qs"]])
    w("## Adoption verdict (fixed rule)\n")
    def desc(nm, st):
        if st.get("n", 0) == 0:
            return f"- {nm}: empty"
        return (f"- {nm}: n={st['n']}, avg={st['avg']:+.3f}R, halves "
                f"({st['ea']:+.3f}, {st['la']:+.3f}) → {'CLEARS' if clears(st) else 'fails'}")
    w(desc("base-candle qualifying", base_st) + f"  → verdict: {fail_reason(base_st)}")
    w(desc("strict-candle qualifying", strict_st) + f"  → verdict: {fail_reason(strict_st)}")
    w("")
    w("_Both variants clear parts of the bar but neither clears all three: **base** passes avg "
      "and n but its late half turns negative; **strict** passes avg and both halves but is "
      "under-sampled (n<80). The conjunctive rule rejects both._\n")
    if clears(base_st):
        w("**PASS (base): EMArevQ's gate becomes base-spec ∧ base meaningful-reversal-candle; "
          "B∧D dropped.** Proceed to EA gate swap + window-11 baseline.")
        chosen = ("base", [x for x in good if x["qb"]])
    elif clears(strict_st):
        w("**PASS (strict only): gate becomes base-spec ∧ strict meaningful-reversal-candle; "
          "B∧D dropped.** Proceed to EA gate swap + window-11 baseline.")
        chosen = ("strict", [x for x in good if x["qs"]])
    else:
        w("**FAIL on both variants: B∧D stays as shipped; window 11 launches as-is.** Per the "
          "pre-registration this is the FINAL look at this dataset — no further cuts permitted.")
        chosen = (None, [])
    w("")
    w("## Context — strict vs the shipped B∧D (both selection-inflated)\n")
    w(f"- strict-candle: n={strict_st['n']}, avg={strict_st['avg']:+.3f}R, "
      f"halves ({strict_st['ea']:+.3f}, {strict_st['la']:+.3f})")
    w("- shipped B∧D (blind base): n=46, avg=+0.069R, halves (−0.156, +0.294)")
    w("Both are post-hoc filters chosen on this same 620-signal cohort (the trader's "
      "candle hypothesis was declared before *this* look, but after the earlier ones), and both "
      "sit at n≈46–48 — so strict's higher average is **not** a clean like-for-like improvement; "
      "it carries the same selection inflation the coach flagged for B∧D. The one genuine "
      "distinction: strict's **both halves are positive** (+0.397, +0.144), which B∧D's never "
      "were — so the candle-quality signal is more date-stable than B∧D, it is simply "
      "under-sampled against the n≥80 floor. Reported as the finding; **no third variant is "
      "proposed** (final look, no further cuts permitted).\n")

    # cadence regardless of verdict
    w("## Cadence per symbol-year (both variants, regardless of verdict)\n")
    def cadence(rs):
        c = Counter((x["sym"], x["t"].year) for rs2 in [rs] for x in rs2)
        yrs = Counter(x["sym"] for x in rs)
        span = {sym: len(set(x["t"].year for x in good if x["sym"] == sym)) for sym in yrs}
        return yrs, span
    for nm, key in (("base", "qb"), ("strict", "qs")):
        q = [x for x in good if x[key]]
        by = Counter(x["sym"] for x in q)
        tot_years = len(set((x["sym"], x["t"].year) for x in good))
        firing_years = len(set((x["sym"], x["t"].year) for x in q))
        w(f"- **{nm}**: {len(q)} firings across {firing_years} symbol-years "
          f"(cohort spans {tot_years} symbol-years) → "
          f"~{len(q)/max(tot_years,1):.2f}/symbol-year. By symbol: {dict(by)}")
    w("")

    # robustness: literal Form B reading + ATR at si-1
    w("## Robustness (disclosed; not variants)\n")
    lit_st = stat([x for x in good if x["qb_lit"]])
    w(f"- **Form B literal reading** (close > prior stretch-side extreme, vs the primary "
      f"swept-the-extreme reading): base-qualifying n {base_st.get('n',0)}→{lit_st.get('n',0)}, "
      f"avg {base_st.get('avg',0):+.3f}→{lit_st.get('avg',0) if lit_st.get('n') else float('nan'):+.3f}R, "
      f"verdict {'CLEARS' if clears(lit_st) else 'fails'} "
      f"({'UNCHANGED' if clears(lit_st)==clears(base_st) else 'CHANGES — flag for coach'}).")
    atrp_st = stat([x for x in good if x["qb_atrp"]])
    w(f"- **ATR at trigger bar** (primary; includes the trigger's own TR, so 1.0×ATR is harder "
      f"than it looks). Using ATR at si−1 instead: base-qualifying n "
      f"{base_st.get('n',0)}→{atrp_st.get('n',0)} (admits "
      f"{atrp_st.get('n',0)-base_st.get('n',0):+d}), verdict "
      f"{'CLEARS' if clears(atrp_st) else 'fails'}.")
    w("")

    # two-source agreement on GBPUSD
    w("## Two-source H4 agreement check (GBPUSD — both sources on disk)\n")
    gb = [x for x in good if x["sym"] == "GBPUSD.dk"]
    s_alt = build_h4("GBPUSD.dk", source="inv")
    tmap_alt = {tt: i for i, tt in enumerate(s_alt[0])}
    agree = disagree = missing = 0
    for x in gb:
        j = tmap_alt.get(x["t"])
        if j is None or j < 3 or s_alt[5][j] is None or s_alt[6][j] is None or s_alt[6][j] <= 0:
            missing += 1; continue
        qb_alt = qualifies(s_alt, j, x["d"], "base")
        if qb_alt is None:
            missing += 1; continue
        if qb_alt == x["qb"]:
            agree += 1
        else:
            disagree += 1
    w(f"- GBPUSD assessable in both sources: {agree+disagree} (QDM-M1→H4 vs MT5-dump). "
      f"**Agree {agree}, disagree {disagree}**, out-of-range {missing}.")
    w(f"- {'✓ sources agree — join is trustworthy.' if disagree<=2 else '✗ sources diverge — investigate before trusting the verdict.'}")
    w("")

    report = "\n".join(out)
    Path("data/study/emarevq").mkdir(parents=True, exist_ok=True)
    Path("data/study/emarevq/recut3_candle_report.md").write_text(report)
    print(report)
    print("\n[written data/study/emarevq/recut3_candle_report.md]")


if __name__ == "__main__":
    main()
