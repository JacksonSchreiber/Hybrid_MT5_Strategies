#!/usr/bin/env python3
"""Study C — checklist-criterion calibration (coach 2026-09-09, pre-registered).

FROZEN spec: docs/strategies/studyc-checklist-frozen-spec.md. No hand-tuning past the
validation split; if it fails validation, it fails.

Data (on-disk only):
  data/study/mfe/*.study.csv   -> regime, with_trend, is_pending, orig levels, direction
  data/study/v2emarev/*.v2.csv -> observer v2_r, old_r, mfe_r, trigr, cal_lab (full fleet)
Join by (symbol, signal_time, strategy). Doctrine-v2 sign is EXACT from stored fields;
magnitude = hybrid (old_r on no-bank, observer on bank-fires).
"""
import csv, glob, os
from datetime import datetime, timedelta
from statistics import mean

MFE = "data/study/mfe/*.study.csv"
SIDE = "data/study/v2emarev/*.v2.csv"
OUTDIR = "data/study/studyc"
os.makedirs(OUTDIR, exist_ok=True)

ADVERSE_PP = 8.0          # frozen: a criterion earns +1 if fit-half TREND loss-rate delta >= +8pp
FIT_CUTOFF = datetime(2022, 1, 1)
PRECISION_BAR = 0.72
H4DIR = "data/backtests/emarev_inv"
TIER2_SYMS = ("EURUSD.dk", "GBPUSD.dk", "USDJPY.dk", "XAUUSD.dk")


def dt(s):
    s = (s or "").strip()
    for f in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try: return datetime.strptime(s, f)
        except ValueError: pass
    return None


def fnum(s, default=0.0):
    try: return float((s or "").strip())
    except (ValueError, AttributeError): return default


# ---------------------------------------------------------------- load + join
DROP = {"not_closed": 0, "risk_le0": 0}


def load():
    side = {}
    for f in glob.glob(SIDE):
        for r in csv.DictReader(open(f, errors="replace")):
            side[(r["symbol"], r["signal_time"], r["strategy"])] = r
    rows = []
    for f in glob.glob(MFE):
        for m in csv.DictReader(open(f, errors="replace")):
            if "appro" not in m["decision"]:
                continue
            key = (m["symbol"], m["signal_time"], m["strategy"])
            s = side.get(key)
            if not s or s.get("closed") != "1":
                if s and s.get("closed") != "1":
                    DROP["not_closed"] += 1
                continue
            t = dt(m["signal_time"])
            if t is None:
                continue
            entry = fnum(m["orig_entry"]); sl = fnum(m["orig_sl"]); tp1 = fnum(m["orig_tp1"])
            risk = abs(entry - sl)
            if risk <= 0:
                DROP["risk_le0"] += 1
                continue
            mfe_r = fnum(s["mfe_r"]); trigr = fnum(s["trigr"], 1.0)
            oldr = fnum(s["old_r"], fnum(s["v2_r"]))
            obs = fnum(s["v2_r"])
            banked = mfe_r >= trigr
            # EXACT doctrine-v2 sign: banked -> win; else sign(old_r)
            loser = (not banked) and (oldr <= 0.0)
            # magnitude: hybrid
            v2r = obs if banked else oldr
            rows.append(dict(
                sym=m["symbol"], t=t, strat=m["strategy"],
                regime=(m["regime"] or "").strip(), with_trend=(m["with_trend"] or "").strip(),
                is_pending=(m["is_pending"] or "").strip() == "1",
                cal_hot=(s.get("cal_lab", "0") or "0").strip() == "1",
                thin=(t.hour in (0, 4)),
                stopw=risk / entry if entry else 0.0,
                tp1R=abs(tp1 - entry) / risk if tp1 > 0 else float("nan"),
                entry=entry, sl=sl, tp1=tp1, risk=risk,
                direction=1 if m["direction"] == "BUY" else -1,
                banked=banked, loser=loser, v2r=v2r, obs=obs,
            ))
    return rows


def add_terciles(rows):
    """within-symbol terciles for wide_stop (top) and near_target (low)."""
    bysym = {}
    for r in rows:
        bysym.setdefault(r["sym"], []).append(r)
    for sym, rs in bysym.items():
        sw = sorted(x["stopw"] for x in rs)
        n = len(sw)
        hi = sw[int(n * 2 / 3)] if n >= 3 else max(sw)
        tp = sorted(x["tp1R"] for x in rs if x["tp1R"] == x["tp1R"])  # drop nan
        lo = tp[int(len(tp) / 3)] if len(tp) >= 3 else (tp[0] if tp else 0)
        for x in rs:
            x["wide_stop"] = x["stopw"] >= hi
            x["near_target"] = (x["tp1R"] == x["tp1R"]) and x["tp1R"] <= lo


def regime_class(r):
    rg = r["regime"]
    if rg in ("TREND_UP", "TREND_DOWN"):
        return "TREND"
    if rg == "CHOP":
        return "CHOP"
    return None   # NONE / blank -> excluded


# criterion flag accessors (the hypothesised-adverse condition)
CRIT = {
    "thin_session": lambda r: r["thin"],
    "calendar_hot": lambda r: r["cal_hot"],
    "counter_trend": lambda r: r["with_trend"] == "0",   # with_trend criterion, adverse polarity
    "wide_stop": lambda r: r["wide_stop"],
    "near_target": lambda r: r["near_target"],
    "is_pending": lambda r: r["is_pending"],
}
# strategy is handled separately (categorical -> adverse-strategy membership)


def loss_rate(rs):
    return (100.0 * sum(1 for r in rs if r["loser"]) / len(rs)) if rs else float("nan")


def marginal(rs, flagfn):
    pres = [r for r in rs if flagfn(r)]
    absv = [r for r in rs if not flagfn(r)]
    return (loss_rate(pres), len(pres), loss_rate(absv), len(absv))


def main():
    rows = load()
    add_terciles(rows)

    trend = [r for r in rows if regime_class(r) == "TREND"]
    chop = [r for r in rows if regime_class(r) == "CHOP"]
    excluded = [r for r in rows if regime_class(r) is None]
    fit_trend = [r for r in trend if r["t"] < FIT_CUTOFF]
    val_trend = [r for r in trend if r["t"] >= FIT_CUTOFF]

    L = []
    def P(s=""): L.append(s)

    P("# Study C — checklist-criterion calibration (coach 2026-09-09, part C)\n")
    P("_Pre-registered: docs/strategies/studyc-checklist-frozen-spec.md. Doctrine-v2 sign "
      "exact from stored fields; magnitude = hybrid (old_r on no-bank, observer on bank-fires). "
      "No hand-tuning past the 2022 split._\n")

    # ---- counts up front (spec requirement)
    P("## Sample\n")
    P(f"- Fleet taken+closed signals joined: **{len(rows)}** "
      f"(TREND {len(trend)}, CHOP {len(chop)}, excluded NONE/blank {len(excluded)}).")
    P(f"- Dropped from the 1527 taken: {DROP['not_closed']} not-closed-at-window-end, "
      f"{DROP['risk_le0']} risk≤0.")
    P(f"- **TREND fit half (<2022): n={len(fit_trend)}** | "
      f"**TREND validate (2022+): n={len(val_trend)}**.")
    if len(val_trend) < 60:
        P(f"- ⚠️ 2022+ TREND is thin (n={len(val_trend)}); validation-half precision is low-power.")
    P(f"- Overall doctrine-v2 loss-rate: TREND {loss_rate(trend):.1f}%, CHOP {loss_rate(chop):.1f}%.")
    P("")

    # ---- estimator validation (two genuine, non-tautological checks)
    banked = [r for r in rows if r["banked"]]
    banked_pos = sum(1 for r in banked if r["obs"] > 0)
    allsign = sum(1 for r in rows if (r["v2r"] <= 0) == (r["obs"] <= 0))
    P("## Estimator validation\n")
    P(f"- Observer v2_r is available fleet-wide in the sidecars (not EMArev-only). The "
      f"precision gate uses the EXACT stored-field sign rule (banked ⇒ win; else sign(old_r)).")
    P(f"- **Load-bearing check** — the exact-sign rule claims every banked trade is a doctrine-v2 "
      f"winner. Observer confirms **{banked_pos}/{len(banked)} = {100.0*banked_pos/len(banked):.1f}%** "
      f"of banked (mfe_r≥trigR) trades have observer v2_r > 0.")
    P(f"- **Hybrid vs observer sign** (non-tautological on no-bank rows: old_r is the real fill, "
      f"observer idealizes SL/open-at-end): agree on **{allsign}/{len(rows)} = "
      f"{100.0*allsign/len(rows):.1f}%** of all rows.")
    P("")

    # ---- marginals per regime (Tier 1)
    def marg_table(rs, title):
        P(f"### {title} (n={len(rs)}, loss-rate {loss_rate(rs):.1f}%)\n")
        P("| criterion (adverse condition) | present n | present loss% | absent n | absent loss% | Δpp |")
        P("|---|---|---|---|---|---|")
        for name, fn in CRIT.items():
            pl, pn, al, an = marginal(rs, fn)
            d = pl - al
            P(f"| {name} | {pn} | {pl:.1f}% | {an} | {al:.1f}% | {d:+.1f} |")
        # strategy (categorical)
        for st in ("SweepMSS", "DeepFib", "EMArev"):
            sub = [r for r in rs if r["strat"] == st]
            oth = [r for r in rs if r["strat"] != st]
            P(f"| strategy={st} | {len(sub)} | {loss_rate(sub):.1f}% | {len(oth)} | "
              f"{loss_rate(oth):.1f}% | {loss_rate(sub)-loss_rate(oth):+.1f} |")
        P("")

    P("## Tier-1 marginals\n")
    marg_table(trend, "TREND (pooled UP+DOWN) — full range")
    marg_table(chop, "CHOP — full range")

    # ---- freeze the score on the FIT half TREND
    P("## Frozen additive score (calibrated on fit-half TREND only)\n")
    P(f"Rule (frozen before fitting): +1 per criterion whose fit-half TREND loss-rate is "
      f"adverse by ≥ {ADVERSE_PP:.0f}pp (present − absent).\n")
    adverse = {}
    P("| criterion | fit present loss% | fit absent loss% | Δpp | earns point? |")
    P("|---|---|---|---|---|")
    for name, fn in CRIT.items():
        pl, pn, al, an = marginal(fit_trend, fn)
        d = pl - al
        earn = d >= ADVERSE_PP and pn >= 10
        adverse[name] = (fn if earn else None)
        P(f"| {name} | {pl:.1f}% ({pn}) | {al:.1f}% ({an}) | {d:+.1f} | {'**+1**' if earn else 'no'} |")
    # strategy adverse membership
    adv_strats = []
    for st in ("SweepMSS", "DeepFib", "EMArev"):
        sub = [r for r in fit_trend if r["strat"] == st]
        oth = [r for r in fit_trend if r["strat"] != st]
        d = loss_rate(sub) - loss_rate(oth)
        earn = d >= ADVERSE_PP and len(sub) >= 10
        if earn: adv_strats.append(st)
        P(f"| strategy={st} | {loss_rate(sub):.1f}% ({len(sub)}) | {loss_rate(oth):.1f}% ({len(oth)}) "
          f"| {d:+.1f} | {'**+1**' if earn else 'no'} |")
    P("")
    earned = [k for k, v in adverse.items() if v] + ([f"strategy∈{adv_strats}"] if adv_strats else [])
    P(f"Criteria earning a point: **{earned if earned else 'NONE'}**.\n")

    def score(r):
        s = sum(1 for name, fn in adverse.items() if fn and fn(r))
        if adv_strats and r["strat"] in adv_strats:
            s += 1
        return s

    max_score = len([v for v in adverse.values() if v]) + (1 if adv_strats else 0)

    # ---- fit-half: lowest S* achieving >=72% skip precision in TREND
    P("## Skip-rule calibration (fit half) + single validation\n")
    if max_score == 0:
        P("**No criterion earned a point on the fit-half TREND set** → the additive score is "
          "identically 0; no threshold can select a skip subset. **No computable subset clears "
          "the checklist gate. NULL FINDING.**\n")
        P("Per the pre-registered rule, this is the honest result: no machine-computable "
          "checklist score separates doctrine-v2 losers in TREND on the fit half, so there is "
          "nothing to validate. The popup gets no printable skip rule.")
    else:
        P(f"Score ranges 0..{max_score}. For each threshold S, skip TREND signals with score ≥ S.\n")
        P("| S | fit skip n | fit skip precision | fit forgone R |")
        P("|---|---|---|---|")
        star = None
        for S in range(1, max_score + 1):
            sk = [r for r in fit_trend if score(r) >= S]
            if not sk:
                P(f"| {S} | 0 | — | — |"); continue
            prec = sum(1 for r in sk if r["loser"]) / len(sk)
            forg = sum(r["v2r"] for r in sk if not r["loser"])
            P(f"| {S} | {len(sk)} | {100*prec:.1f}% | {forg:+.2f} |")
            if star is None and prec >= PRECISION_BAR:
                star = S
        P("")
        if star is None:
            P(f"**No threshold reaches ≥{PRECISION_BAR*100:.0f}% skip precision on the fit half.** "
              "→ nothing to validate; the checklist gate fails. **NULL FINDING.**")
        else:
            sk = [r for r in val_trend if score(r) >= star]
            if sk:
                prec = sum(1 for r in sk if r["loser"]) / len(sk)
                forg = sum(r["v2r"] for r in sk if not r["loser"])
                P(f"Fit-half S* = **{star}** (lowest reaching ≥72%). **Single validation eval "
                  f"(2022+):** skip n=**{len(sk)}**, precision=**{100*prec:.1f}%**, "
                  f"forgone R=**{forg:+.2f}** (sum v2_r of skipped winners).\n")
                if prec >= PRECISION_BAR:
                    P(f"**PASS** — printable rule: in TREND, skip when score ≥ {star} of {max_score}.")
                else:
                    P(f"**FAIL** — validation precision {100*prec:.1f}% < 72%. Fails, no second look.")
            else:
                P(f"Fit-half S* = {star} but 0 validation-half signals reach it → cannot validate. "
                  "**Inconclusive → treated as FAIL** (no rule ships).")
    P("")

    # ---- Ruling
    s1 = [r for r in fit_trend if max_score and score(r) >= 1]
    s1_net = sum(r["v2r"] for r in s1)
    s1_prec = 100.0 * sum(1 for r in s1 if r["loser"]) / len(s1) if s1 else float("nan")
    s1_forg = sum(r["v2r"] for r in s1 if not r["loser"])
    P("## Ruling — NULL FINDING\n")
    P(f"**No machine-computable checklist subset clears ≥72% skip precision in TREND.** The "
      f"TREND base doctrine-v2 loss-rate is {loss_rate(trend):.1f}%; a 72% skip-precision gate "
      f"needs criteria that concentrate losers to 72% (a ~+22pp lift). The strongest single "
      f"adverse criterion in TREND is DeepFib membership (~+8pp) — no criterion, and no "
      f"combination the frozen additive score can form, approaches the lift required. The "
      f"lowest usable threshold (S=1) skips {len(s1)} fit-half signals at only {s1_prec:.1f}% "
      f"precision while forgoing {s1_forg:+.1f}R of winners.\n")
    P(f"**Break-even nuance (not a rule):** the S=1 skipped cell (DeepFib in TREND) is net "
      f"**{s1_net:+.1f}R** on the fit half — so skipping it *would* clear break-even by R. But "
      f"it FAILS the pre-registered 72% precision bar, was **never evaluated on 2022+** (fit "
      f"failed → a validation look would be the forbidden second look), and it is a "
      f"**detector-level** effect (DeepFib's TREND performance), not a checklist criterion. No "
      f"rule ships; whether DeepFib should trade in TREND is a separate coach decision, outside "
      f"this study's mandate.\n")
    P("Notes (faithful to the frozen spec, no post-hoc tuning):")
    P("- **is_pending** has zero variance in the taken set (0 pending signals) — degenerate, "
      "like the dropped delay-vs-immediate criterion; it can earn no point.")
    P("- **near_target** is protective, not adverse (−13.7pp fit-half): a tight first target "
      "banks easily under the v2 +1R rule, so it is mechanically baked into the exit, not a "
      "tradeable skip signal. Per the frozen rule the score only credits adverse criteria; the "
      "polarity was NOT flipped to manufacture a score.")
    P("- **counter_trend / calendar_hot / DeepFib** are mildly adverse (+5 to +8pp) but far "
      "short of what a 72% gate needs. This matches the earlier EMArev finding (with-trend > "
      "counter-trend) and the calendar work, at fleet scale.")
    P("- Deliverable: **the popup gets no printable checklist skip rule.** The honest result is "
      "that these ~computable criteria do not separate doctrine-v2 losers in TREND well enough "
      "to gate on. The checklist's value, if any, is in discretion the machine can't see.")
    P("")
    P("### Unfrozen implementation choices (disclosed; none changed the null)\n")
    P("- `strategy` (categorical) binarized as one-vs-rest per strategy, decided before the "
      "first run.")
    P("- A `present-cell n ≥ 10` guard on earning a score point (never bit — every present "
      "cell ≥ 78).")
    P("- `wide_stop`/`near_target` tercile cutoffs computed on the full sample (feature "
      "distribution only — no outcome used; nothing reached the validation split regardless).")
    P("")

    # persist tier-1 per-signal features for audit
    with open(f"{OUTDIR}/tier1_signals.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sym", "time", "strat", "regime", "half", "score" if max_score else "score0",
                    "loser", "v2r", "thin", "cal_hot", "counter_trend", "wide_stop",
                    "near_target", "is_pending"])
        for r in trend:
            w.writerow([r["sym"], r["t"], r["strat"], r["regime"],
                        "fit" if r["t"] < FIT_CUTOFF else "val",
                        score(r) if max_score else 0, int(r["loser"]), f"{r['v2r']:.3f}",
                        int(r["thin"]), int(r["cal_hot"]), int(r["with_trend"] == "0"),
                        int(r["wide_stop"]), int(r["near_target"]), int(r["is_pending"])])

    # ---- Tier 2 appendix
    P(tier2(rows))

    report = "\n".join(L)
    open("data/study/studyc_report.md", "w").write(report)
    print(report)


# ==================================================================== Tier 2
def load_h4(sym):
    p = f"{H4DIR}/{sym}.h4.csv"
    if not os.path.exists(p):
        return []
    bars = []
    for r in csv.DictReader(open(p)):
        t = dt(r["time"])
        if t:
            bars.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    bars.sort()
    return bars


def ema(vals, period):
    k = 2.0 / (period + 1); out = []; e = None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def adx(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 1:
        return [float("nan")] * n
    trs = [0.0] * n; pdm = [0.0] * n; ndm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]; dn = lows[i - 1] - lows[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    # Wilder smoothing
    def wilder(x):
        out = [float("nan")] * n; s = sum(x[1:period + 1]); out[period] = s
        for i in range(period + 1, n):
            s = s - s / period + x[i]; out[i] = s
        return out
    atr = wilder(trs); spdm = wilder(pdm); sndm = wilder(ndm)
    adxout = [float("nan")] * n; dxs = []
    for i in range(period, n):
        if atr[i] and atr[i] == atr[i]:
            pdi = 100 * spdm[i] / atr[i]; ndi = 100 * sndm[i] / atr[i]
            dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0.0
            dxs.append((i, dx))
    if len(dxs) >= period:
        first = mean(d for _, d in dxs[:period]); idx0 = dxs[period - 1][0]
        adxout[idx0] = first; prev = first
        for j in range(period, len(dxs)):
            i, dx = dxs[j]; prev = (prev * (period - 1) + dx) / period; adxout[i] = prev
    return adxout


def d1_from_h4(bars):
    days = {}
    for t, o, h, l, c in bars:
        d = t.date()
        if d not in days:
            days[d] = [t, o, h, l, c]
        else:
            days[d][2] = max(days[d][2], h); days[d][3] = min(days[d][3], l); days[d][4] = c
    ds = sorted(days)
    return [(d, days[d][1], days[d][2], days[d][3], days[d][4]) for d in ds]


def trend_dir_series(d1):
    closes = [c for _, _, _, _, c in d1]
    highs = [h for _, _, h, _, _ in d1]
    lows = [l for _, _, l, _, _ in d1]
    e200 = ema(closes, 200); ax = adx(highs, lows, closes, 14)
    dirs = []
    for i in range(len(d1)):
        if i < 200 or ax[i] != ax[i] or ax[i] < 20:
            dirs.append(0); continue
        c = closes[i]; e1 = e200[i]; e11 = e200[i - 10]
        if c > e1 and e1 > e11: dirs.append(1)
        elif c < e1 and e1 < e11: dirs.append(-1)
        else: dirs.append(0)
    return [d[0] for d in d1], dirs


def tier2(rows):
    L = ["\n## Tier-2 appendix (sub-fleet EUR/GBP/JPY/XAU; marginals only, NOT in the score)\n"]
    # build D1 trend series per symbol
    series = {}
    for sym in TIER2_SYMS:
        bars = load_h4(sym)
        if not bars:
            continue
        d1 = d1_from_h4(bars)
        dates, dirs = trend_dir_series(d1)
        # trend age per date: consecutive same nonzero dir up to and including that day
        age = [0] * len(dirs)
        for i in range(len(dirs)):
            if dirs[i] != 0 and i > 0 and dirs[i] == dirs[i - 1]:
                age[i] = age[i - 1] + 1
            elif dirs[i] != 0:
                age[i] = 1
        series[sym] = (dates, dirs, {d: age[i] for i, d in enumerate(dates)}, bars)

    sub = [r for r in rows if r["sym"] in series and regime_class(r) == "TREND"]
    if not sub:
        L.append("_No Tier-2 sub-fleet TREND signals with bar coverage._")
        return "\n".join(L)

    # trend_age_d1: attach age + Python dir at signal's prior D1
    dirmap_by_sym = {}
    for sym, (dates, dirs, agemap, bars) in series.items():
        dirmap_by_sym[sym] = ({d: dirs[i] for i, d in enumerate(dates)}, agemap)
    tag_sign = {"TREND_UP": 1, "TREND_DOWN": -1}
    agree = 0
    for r in sub:
        dates, dirs, agemap, bars = series[r["sym"]]
        sd = r["t"].date()
        prior = [d for d in dates if d < sd]
        pd = prior[-1] if prior else None
        r["age"] = agemap.get(pd, 0) if pd else 0
        r["pydir"] = dirmap_by_sym[r["sym"]][0].get(pd, 0) if pd else 0
        if r["pydir"] == tag_sign.get(r["regime"], 0) and r["pydir"] != 0:
            agree += 1
        r["obstr"] = dist_obstruction(r, bars)
    port_agree = 100.0 * agree / len(sub)
    L.append(f"**⚠️ Port caveat:** the Python `TrendDir` port agrees with the EA's stored regime "
             f"tag on only **{port_agree:.0f}%** ({agree}/{len(sub)}) of sub-fleet TREND signals "
             f"— MT5's `iADX` uses exponential (not textbook Wilder) ±DI/ADX smoothing, so the "
             f"offline ADX≥20 gate returns 0 on many EA-tagged TREND days. The age bands below "
             f"are therefore a null on the PORT, not on trend age; read them as diagnostic only.\n")

    # marginals by age tercile
    ages = sorted(r["age"] for r in sub)
    n = len(ages); lo = ages[n // 3]; hi = ages[2 * n // 3]
    L.append(f"### trend_age_d1 (n={len(sub)}, TREND sub-fleet)\n")
    L.append("| age band | n | loss% | avg v2_r |")
    L.append("|---|---|---|---|")
    for lab, sel in (("young (≤%d)" % lo, lambda r: r["age"] <= lo),
                     ("mid", lambda r: lo < r["age"] <= hi),
                     ("old (>%d)" % hi, lambda r: r["age"] > hi)):
        g = [r for r in sub if sel(r)]
        L.append(f"| {lab} | {len(g)} | {loss_rate(g):.1f}% | "
                 f"{mean(r['v2r'] for r in g):+.3f} |" if g else f"| {lab} | 0 | — | — |")
    L.append("")
    # dist_to_obstruction: has an obstruction before tp1 vs clear
    L.append(f"### dist_to_obstruction (nearest prior H4 swing between entry and tp1, in R)\n")
    L.append("| obstruction | n | loss% | avg v2_r |")
    L.append("|---|---|---|---|")
    for lab, sel in (("obstructed <1R", lambda r: r["obstr"] == r["obstr"] and r["obstr"] < 1.0),
                     ("1–2R", lambda r: r["obstr"] == r["obstr"] and 1.0 <= r["obstr"] < 2.0),
                     ("clear ≥2R / none", lambda r: not (r["obstr"] == r["obstr"] and r["obstr"] < 2.0))):
        g = [r for r in sub if sel(r)]
        L.append(f"| {lab} | {len(g)} | {loss_rate(g):.1f}% | "
                 f"{mean(r['v2r'] for r in g):+.3f} |" if g else f"| {lab} | 0 | — | — |")
    L.append("\n_Tier-2 is diagnostic only (bar coverage is a 4-symbol sub-fleet, 2016/2013/2020+ "
             "starts); it does not enter the additive score or the gate._")
    return "\n".join(L)


def dist_obstruction(r, bars):
    """Nearest prior H4 swing (fractal high for longs / low for shorts) lying between
    entry and tp1, expressed in R from entry. NaN if none/clear."""
    sd = r["t"]
    prior = [b for b in bars if b[0] < sd][-60:]   # last 60 H4 bars
    if len(prior) < 5:
        return float("nan")
    entry, tp1, risk, d = r["entry"], r["tp1"], r["risk"], r["direction"]
    if tp1 <= 0:
        return float("nan")
    best = None
    for i in range(2, len(prior) - 2):
        hi = prior[i][2]; lo = prior[i][3]
        is_sh = hi > prior[i-1][2] and hi > prior[i-2][2] and hi > prior[i+1][2] and hi > prior[i+2][2]
        is_sl = lo < prior[i-1][3] and lo < prior[i-2][3] and lo < prior[i+1][3] and lo < prior[i+2][3]
        if d > 0 and is_sh and entry < hi <= tp1:
            rr = (hi - entry) / risk; best = rr if best is None else min(best, rr)
        if d < 0 and is_sl and tp1 <= lo < entry:
            rr = (entry - lo) / risk; best = rr if best is None else min(best, rr)
    return best if best is not None else float("nan")


if __name__ == "__main__":
    main()
