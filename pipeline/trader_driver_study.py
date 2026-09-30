#!/usr/bin/env python3
"""trader_driver_study.py - coach 2026-09-24 study 3: what actually drives the trader's TrendCont selection?

Same 180 joined decisions as study 2 (W11-W14 decision journals, each signal matched by signal_time to the blind
baseline of the SAME window, so taken and skipped arms share one outcome measure). New features - the ones the
scorecard does not cover.

PRE-REGISTERED TEST (coach): a feature is a candidate driver if conditioning on it collapses the within-bucket
take-minus-skip gap below +0.05R in at least 2 of 3 buckets. Anything that survives goes through the blind record
before it touches the guide.

Not measurable here: "advisor consulted or not" - those windows left only per-window debrief HTML, no per-signal
consult record. Decision latency (journal decision_ms) is reported instead and is the closest available proxy.
"""
from __future__ import annotations
import csv, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from live import overlays as OV                                             # noqa: E402
from pipeline.trendcont_step_study import atr14                             # noqa: E402
from pipeline.trendcont_step13_study import load, ema                       # noqa: E402
from pipeline.trader_selection_study import CJ, WINDOWS, wilder             # noqa: E402

# D1 BAR-INDEX RULE (fixed 2026-09-30, pipeline/d1_lookahead_check.py): a daily feature for an H4 signal uses the last
# D1 bar dated STRICTLY BEFORE the signal's day (d < day) - the previous CLOSED day, what the EA sees. The published
# report used d <= day (the signal day's own, still-forming D1 bar = look-ahead). D1_STRICT = False reproduces it;
# pipeline/d1_rerun_studies.py prints both side by side.
D1_STRICT = True


def adx14(bars: list[list], i: int, n: int = 14) -> float | None:
    lo = max(1, i - 200)
    tr, pdm, ndm = [], [], []
    for j in range(lo, i + 1):
        b, p = bars[j], bars[j - 1]
        tr.append(max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4])))
        up_m, dn_m = b[2] - p[2], p[3] - b[3]
        pdm.append(up_m if up_m > dn_m and up_m > 0 else 0.0)
        ndm.append(dn_m if dn_m > up_m and dn_m > 0 else 0.0)
    if len(tr) < 2 * n: return None
    str_, sp, sn = wilder(tr, n), wilder(pdm, n), wilder(ndm, n)
    dx = []
    for k in range(len(tr)):
        if not str_[k] or sp[k] is None or sn[k] is None: continue
        pdi, ndi = 100 * sp[k] / str_[k], 100 * sn[k] / str_[k]
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
    if len(dx) < n: return None
    a = sum(dx[:n]) / n
    for x in dx[n:]: a = (a * (n - 1) + x) / n
    return a


def features(h4, d1, i, jd, up, a, decision_ms) -> dict | None:
    """everything measured on the bars up to and including the signal bar."""
    sw = OV.swings(h4[:i + 1], 14, 2)
    kind = "hi" if up else "lo"
    origin = next((s for s in sw if s["kind"] == kind and s["bars_ago"] > 0), None)      # pullback retraced FROM here
    start = next((s for s in sw if s["kind"] != kind and origin and s["bars_ago"] > origin["bars_ago"]), None)
    prev = next((s for s in sw if s["kind"] == kind and start and s["bars_ago"] > start["bars_ago"]), None)
    if origin is None or start is None or a <= 0: return None
    io_, is_ = i - origin["bars_ago"], i - start["bars_ago"]
    leg = abs(origin["p"] - start["p"])
    if leg <= 0: return None
    seg = h4[io_:i + 1]
    ext = min(b[3] for b in seg) if up else max(b[2] for b in seg)
    depth = abs(origin["p"] - ext)
    retr = depth / leg
    ov = 0
    for k in range(len(seg) - 1):
        if min(seg[k][2], seg[k + 1][2]) - max(seg[k][3], seg[k + 1][3]) > 0: ov += 1
    ovf = ov / max(1, len(seg) - 1)
    e20 = ema([b[4] for b in h4[:i + 1]], 20)
    slope = ((e20[i] - e20[i - 5]) / a) * (1 if up else -1)
    adx = adx14(h4, i)
    d1a = atr14([[0] + b[1:] for b in d1], jd) if jd >= 20 else 0.0
    d1e = ema([b[4] for b in d1[:jd + 1]], 20)
    d1d = abs(d1[jd][4] - d1e[jd]) / d1a if d1a > 0 else 0.0
    prev_depth = abs(prev["p"] - start["p"]) if prev else None
    ratio = depth / prev_depth if prev_depth else None
    ms = decision_ms / 1000.0
    return {
        "retr": "<38%" if retr < 0.38 else ("38-62%" if retr <= 0.62 else ">62%"),
        "leg_atr": "<3 ATR" if leg / a < 3 else ("3-6 ATR" if leg / a <= 6 else ">6 ATR"),
        "leg_bars": "<6 bars" if io_ - is_ < 6 else ("6-12 bars" if io_ - is_ <= 12 else ">12 bars"),   # start is OLDER, so io_ - is_
        "pb_bars": "1-3 bars" if origin["bars_ago"] <= 3 else ("4-7 bars" if origin["bars_ago"] <= 7 else "8+ bars"),
        "overlap": "<0.85" if ovf < 0.85 else ("0.85-0.95" if ovf <= 0.95 else ">0.95"),   # H4 pullback bars overlap a lot; these split the population
        "slope": "flat/against" if slope < 0.1 else ("0.1-0.4 ATR" if slope <= 0.4 else ">0.4 ATR"),
        "adx": "-" if adx is None else ("<20" if adx < 20 else ("20-30" if adx <= 30 else ">30")),
        "d1_dist": "<0.5 ATR" if d1d < 0.5 else ("0.5-1.5 ATR" if d1d <= 1.5 else ">1.5 ATR"),
        "vs_prev": "-" if ratio is None else ("shallower (<0.7)" if ratio < 0.7 else ("similar (0.7-1.3)" if ratio <= 1.3 else "deeper (>1.3)")),
        "latency": "<10 s" if ms < 10 else ("10-60 s" if ms <= 60 else ">60 s"),
        "hour": "0" + h4[i][0][11:13] if len(h4[i][0]) > 13 else "?",
    }


def report(rows, key, order, title, drivers):
    print(f"\n{title}")
    print(f"  {'bucket':<18} {'take rate':>9} {'n':>5} | {'R taken':>18} {'R skipped':>18} | {'gap':>7}")
    collapsed = tested = 0
    for k in order:
        sub = [r for r in rows if r[key] == k]
        tk = [r for r in sub if r["took"]]; sk = [r for r in sub if not r["took"]]
        if len(tk) < 3 or len(sk) < 3:
            if sub: print(f"  {k:<18} {'':>9} {len(sub):>5} | (too few either side: {len(tk)} taken / {len(sk)} skipped)")
            continue
        f = lambda xs: f"{st.mean(x['base_r'] for x in xs):+.3f}+-{st.stdev([x['base_r'] for x in xs])/len(xs)**0.5:.2f} (n={len(xs)})"
        gap = st.mean(x["base_r"] for x in tk) - st.mean(x["base_r"] for x in sk)
        tested += 1; collapsed += gap < 0.05
        print(f"  {k:<18} {len(tk)/len(sub)*100:>8.0f}% {len(sub):>5} | {f(tk):>18} {f(sk):>18} | {gap:>+7.2f}")
    # the coach's rule is "2 of 3 buckets", i.e. a MAJORITY of them - a 6-bucket feature clearing 2 is a weaker
    # result, not the same one, so the flag needs the ratio as well as the count.
    hit = collapsed >= 2 and tested >= 2 and collapsed / tested >= 0.6
    if tested: print(f"  -> gap < +0.05R in {collapsed} of {tested} buckets" + ("  ** CANDIDATE DRIVER **" if hit else ""))
    if hit: drivers.append(title)


def main():
    rows = []
    for label, dec, base in WINDOWS:
        dp, bp = os.path.join(CJ, dec), os.path.join(CJ, base)
        if not (os.path.exists(dp) and os.path.exists(bp)): continue
        bl = {r["signal_time"]: r for r in csv.DictReader(open(bp, newline="", encoding="ascii", errors="replace"))}
        sym = dec.split("_")[0]
        h4, d1 = load(sym, "h4"), load(sym, "d1")
        ih = {b[0][:16]: k for k, b in enumerate(h4)}
        for r in csv.DictReader(open(dp, newline="", encoding="ascii", errors="replace")):
            if r.get("strategy") != "TrendCont": continue
            d = r.get("decision", "")
            took = d.startswith("approved")
            if not took and (d != "skipped" or r.get("skip_reason") in ("8", "9", "0")): continue
            b = bl.get(r["signal_time"])
            i = ih.get(r["signal_time"][:16])
            if not b or i is None or i < 60: continue
            try:
                base_r = float(b["r_multiple"]); ms = float(r.get("decision_ms") or 0)
                e, s = float(r["orig_entry"]), float(r["orig_sl"])
            except (TypeError, ValueError): continue
            up = r["direction"].upper().startswith("B")
            day = r["signal_time"][:10]
            jd = max((k for k, b2 in enumerate(d1) if (b2[0][:10] < day if D1_STRICT else b2[0][:10] <= day)), default=-1)
            if jd < 0: continue
            a = atr14([[0] + x[1:] for x in h4], i)
            f = features(h4, d1, i, jd, up, a, ms)
            if f is None: continue
            rows.append({"took": took, "base_r": base_r, "win": label, **f})
    tk = [r for r in rows if r["took"]]
    print(f"{len(rows)} decisions with a full feature set: {len(tk)} taken, {len(rows)-len(tk)} skipped")
    print(f"overall gap: taken {st.mean(r['base_r'] for r in tk):+.3f}R vs skipped "
          f"{st.mean(r['base_r'] for r in rows if not r['took']):+.3f}R = "
          f"{st.mean(r['base_r'] for r in tk) - st.mean(r['base_r'] for r in rows if not r['took']):+.3f}R\n"
          "(a feature that EXPLAINS this edge makes the gap vanish inside its buckets)")
    drivers: list[str] = []
    for key, order, title in [
        ("retr", ["<38%", "38-62%", ">62%"], "RETRACEMENT of the prior leg"),
        ("leg_atr", ["<3 ATR", "3-6 ATR", ">6 ATR"], "PRIOR LEG length in ATR"),
        ("leg_bars", ["<6 bars", "6-12 bars", ">12 bars"], "PRIOR LEG length in bars"),
        ("pb_bars", ["1-3 bars", "4-7 bars", "8+ bars"], "PULLBACK duration"),
        ("overlap", ["<0.85", "0.85-0.95", ">0.95"], "PULLBACK bar overlap fraction"),
        ("slope", ["flat/against", "0.1-0.4 ATR", ">0.4 ATR"], "H4 EMA20 slope (in the trade's direction)"),
        ("adx", ["<20", "20-30", ">30"], "H4 ADX(14)"),
        ("d1_dist", ["<0.5 ATR", "0.5-1.5 ATR", ">1.5 ATR"], "D1 distance from its EMA20"),
        ("vs_prev", ["shallower (<0.7)", "similar (0.7-1.3)", "deeper (>1.3)"], "THIS pullback vs the previous one"),
        ("latency", ["<10 s", "10-60 s", ">60 s"], "DECISION LATENCY (decision_ms)"),
        ("hour", sorted({r["hour"] for r in rows}), "BAR OF DAY"),
    ]: report(rows, key, order, title, drivers)
    print("\ncandidate drivers (gap < +0.05R in >= 2 of 3 buckets): " + (", ".join(drivers) if drivers else "NONE"))


if __name__ == "__main__": main()
