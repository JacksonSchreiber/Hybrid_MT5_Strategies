#!/usr/bin/env python3
"""trader_selection_study.py - coach 2026-09-24 study 2: what is the trader's eye actually selecting on?

Every TrendCont signal he DECIDED on in the graded windows that ran the 4-detector lineup (W11, W12a, W12b, W13a,
W13b, W14 - W9/W10 predate TrendCont), taken from the decision journals, never the baselines. Each decision is joined
by signal_time to the blind AA baseline for that window, so both arms carry the SAME outcome measure: what the trade
would have returned taken blind. Taken rows also carry his own realised R for reference.

Per feature bucket: how often he took it, and the baseline R of what he took vs what he passed. A feature he is
selecting on shows a take rate that moves AND a taken-vs-skipped R gap in the same direction.

Features: largest pullback bar / ATR(14) (step 2), structure in the road to +1R (step 5), depth beyond the H4 EMA20
(step 3), bar of day, symbol, and regime age (D1 bars the current frozen regime tag has held).
"""
from __future__ import annotations
import csv, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import overlays as OV                                             # noqa: E402
from pipeline.trendcont_step_study import atr14, CLUSTER_R                  # noqa: E402
from pipeline.trendcont_step13_study import load, ema, step3                # noqa: E402

# D1 BAR-INDEX RULE (fixed 2026-09-30, pipeline/d1_lookahead_check.py): a daily feature for an H4 signal uses the last
# D1 bar dated STRICTLY BEFORE the signal's day (d < day) - the previous CLOSED day, what the EA sees. The published
# report used d <= day (the signal day's own, still-forming D1 bar = look-ahead). D1_STRICT = False reproduces it;
# pipeline/d1_rerun_studies.py prints both side by side.
D1_STRICT = True

CJ = "/mnt/c/Users/jacks/AppData/Roaming/MetaQuotes/Terminal/Common/Files/journal"
WINDOWS = [                                       # (label, decision journal, blind baseline for the SAME window)
    ("W11 US100 2017",  "US100.dk_20170103_20171229.csv",  "AA_US100.dk_20170103_20171229.csv"),
    ("W12a EUR 2021",   "EURUSD.dk_20210103_20211230.csv", "AA_EURUSD.dk_20210103_20211230.csv"),
    ("W12b USOIL 2017", "USOIL.dk_20170103_20171229.csv",  "AA_USOIL.dk_20170103_20171229.csv"),
    ("W13a GBP 2023",   "GBPUSD.dk_20230101_20231229.csv", "AA_GBPUSD.dk_20230101_20231229.csv"),
    ("W13b US100 2019", "US100.dk_20190101_20191230.csv",  "AA_US100.dk_20190101_20191230.csv"),
    ("W14 BTCUSD 2019", "BTCUSD.dk_20190101_20191230.csv", "baselines/BTCUSD.dk_20190101_20191231_AA_ALL.csv"),
]


def wilder(xs: list[float], n: int) -> list[float]:
    out, a = [], None
    for i, x in enumerate(xs):
        a = (sum(xs[:n]) / n if i == n - 1 else (None if i < n - 1 else (a * (n - 1) + x) / n))
        out.append(a)
    return out


def regime_series(d1: list[list]) -> list[str]:
    """The frozen tag (EA ComputeRegime): TREND_UP if close > EMA200 and EMA200 > its value 10 bars back and ADX>=20."""
    c = [b[4] for b in d1]; e200 = ema(c, 200)
    tr, pdm, ndm = [], [], []
    for i, b in enumerate(d1):
        if i == 0: tr.append(b[2] - b[3]); pdm.append(0.0); ndm.append(0.0); continue
        p = d1[i - 1]
        tr.append(max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4])))
        up_m, dn_m = b[2] - p[2], p[3] - b[3]
        pdm.append(up_m if up_m > dn_m and up_m > 0 else 0.0)
        ndm.append(dn_m if dn_m > up_m and dn_m > 0 else 0.0)
    str_, sp, sn = wilder(tr, 14), wilder(pdm, 14), wilder(ndm, 14)
    dx = []
    for i in range(len(d1)):
        if not str_[i] or str_[i] == 0 or sp[i] is None or sn[i] is None: dx.append(None); continue
        pdi, ndi = 100 * sp[i] / str_[i], 100 * sn[i] / str_[i]
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
    adx, a = [], None
    seen = [x for x in dx if x is not None]
    for i, x in enumerate(dx):
        if x is None or len(seen) < 14: adx.append(None); continue
        a = x if a is None else (a * 13 + x) / 14
        adx.append(a)
    out = []
    for i in range(len(d1)):
        if i < 210 or adx[i] is None or e200[i] is None: out.append(""); continue
        rising, falling = e200[i] > e200[i - 10], e200[i] < e200[i - 10]
        if c[i] > e200[i] and rising and adx[i] >= 20: out.append("TREND_UP")
        elif c[i] < e200[i] and falling and adx[i] >= 20: out.append("TREND_DOWN")
        else: out.append("CHOP")
    return out


def road_class(h4, i, e, R, up) -> str:
    sw = OV.swings(h4[:i + 1], 14, 2); kind = "hi" if up else "lo"
    tgt = e + R if up else e - R
    ps = sorted(x["p"] for x in sw if x["kind"] == kind and (e < x["p"] <= tgt if up else tgt <= x["p"] < e))
    cl: list[list[float]] = []
    for p in ps:
        if cl and abs(p - cl[-1][-1]) <= CLUSTER_R * R: cl[-1].append(p)
        else: cl.append([p])
    if not cl: return "none"
    return "repeated" if any(len(c) >= 2 for c in cl) else "single"


def mR(xs, fld):
    """mean +- SE: these cells are 3-67 decisions wide, so a gap without its error bar is not a finding."""
    if not xs: return "-"
    v = [x[fld] for x in xs]
    se = (st.stdev(v) / len(v) ** 0.5) if len(v) > 1 else 0.0
    return f"{st.mean(v):+.3f}+-{se:.2f} (n={len(v)})"


def report(rows, keyf, order, title):
    print(f"\n{title}")
    print(f"  {'bucket':<16} {'take rate':>9} | {'baseline R taken':>24} {'baseline R skipped':>24} | {'gap':>7} | {'his own R':>9}")
    for k in order:
        sub = [r for r in rows if keyf(r) == k]
        if not sub: continue
        tk = [r for r in sub if r["took"]]; sk = [r for r in sub if not r["took"]]
        gap = (st.mean(r["base_r"] for r in tk) - st.mean(r["base_r"] for r in sk)) if (tk and sk) else None
        own = f"{st.mean(r['own_r'] for r in tk):+.3f}" if tk else "-"
        print(f"  {k:<16} {len(tk)/len(sub)*100:>8.0f}% | {mR(tk,'base_r'):>24} {mR(sk,'base_r'):>24} | "
              f"{(f'{gap:+.2f}' if gap is not None else '-'):>7} | {own:>9}")


def main():
    rows, miss = [], defaultdict(int)
    for label, dec, base in WINDOWS:
        dp, bp = os.path.join(CJ, dec), os.path.join(CJ, base)
        if not (os.path.exists(dp) and os.path.exists(bp)): print(f"{label}: missing file"); continue
        bl = {r["signal_time"]: r for r in csv.DictReader(open(bp, newline="", encoding="ascii", errors="replace"))}
        sym = dec.split("_")[0]
        h4, d1 = load(sym, "h4"), load(sym, "d1")
        ih = {b[0][:16]: k for k, b in enumerate(h4)}
        reg = regime_series(d1)
        age = [0] * len(reg)
        for k in range(1, len(reg)): age[k] = age[k - 1] + 1 if reg[k] and reg[k] == reg[k - 1] else 0
        n_ok = 0
        for r in csv.DictReader(open(dp, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont": continue
            d = r.get("decision", "")
            took = d.startswith("approved")
            if not took and (d != "skipped" or r.get("skip_reason") in ("8", "9", "0")): continue   # trader decisions only
            b = bl.get(r["signal_time"])
            if not b: miss[label] += 1; continue
            i = ih.get(r["signal_time"][:16])
            if i is None or i < 60: miss[label + " (no bars)"] += 1; continue
            try:
                e, s = float(r["orig_entry"]), float(r["orig_sl"]); base_r = float(b["r_multiple"])
                own_r = float(r["r_multiple"]) if took and r.get("r_multiple") else 0.0
            except (TypeError, ValueError): miss[label + " (unusable)"] += 1; continue
            R = abs(e - s)
            if R <= 0: continue
            up = r["direction"].upper().startswith("B")
            a = atr14([[0] + x[1:] for x in h4], i)
            sw = OV.swings(h4[:i + 1], 14, 2); kind = "hi" if up else "lo"
            j0 = next((i - x["bars_ago"] for x in sw if x["kind"] == kind and x["bars_ago"] > 0), max(0, i - 7))
            mx = max(((h4[j][2] - h4[j][3]) / a) for j in range(max(0, j0), i + 1)) if a > 0 else 0.0
            s3 = step3(h4, i, up) or ("?", "?")
            day = r["signal_time"][:10]
            jd = max((k for k, b2 in enumerate(d1) if (b2[0][:10] < day if D1_STRICT else b2[0][:10] <= day)), default=-1)
            if jd < 0: miss[label + " (no prior D1 bar)"] += 1; continue
            rows.append({"took": took, "base_r": base_r, "own_r": own_r, "win": label, "sym": sym,
                         "slam": "<1.5" if mx < 1.5 else ("1.5-2" if mx < 2 else ">=2"),
                         "road": road_class(h4, i, e, R, up), "depth": s3[1], "run": s3[0],
                         "hour": r["signal_time"][11:13] + ":00",
                         "age": "new (<=10d)" if age[jd] <= 10 else ("10-40d" if age[jd] <= 40 else ">40d"),
                         "regime": reg[jd] or "(blank)"})
            n_ok += 1
        print(f"{label:16} {n_ok:>4} TrendCont decisions joined to the baseline")
    for k, v in sorted(miss.items()): print(f"  unjoined {v:>4}  ({k})")
    tk = [r for r in rows if r["took"]]
    print(f"\ntotal {len(rows)} decisions: {len(tk)} taken ({len(tk)/max(1,len(rows))*100:.0f}%), {len(rows)-len(tk)} skipped")
    print(f"selection alpha on the baseline measure: taken {st.mean(r['base_r'] for r in tk):+.3f}R vs "
          f"skipped {st.mean(r['base_r'] for r in rows if not r['took']):+.3f}R")
    report(rows, lambda r: r["slam"], ["<1.5", "1.5-2", ">=2"], "STEP 2 - largest pullback bar / ATR")
    report(rows, lambda r: r["road"], ["none", "single", "repeated"], "STEP 5 - structure in the road to +1R")
    report(rows, lambda r: r["depth"], ["<=0.5 ATR", "0.5-1 ATR", ">1 ATR"], "STEP 3 - depth beyond the EMA20")
    report(rows, lambda r: r["run"], ["0-2", "3-5", "6+"], "STEP 3 - bars closed beyond the EMA20")
    report(rows, lambda r: r["hour"], sorted({r["hour"] for r in rows}), "BAR OF DAY (broker clock)")
    report(rows, lambda r: r["sym"], sorted({r["sym"] for r in rows}), "SYMBOL")
    report(rows, lambda r: r["age"], ["new (<=10d)", "10-40d", ">40d"], "REGIME AGE (D1 bars the tag has held)")
    report(rows, lambda r: r["regime"], ["TREND_UP", "TREND_DOWN", "CHOP", "(blank)"], "REGIME TAG")


if __name__ == "__main__": main()
