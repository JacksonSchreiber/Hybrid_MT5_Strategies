#!/usr/bin/env python3
"""concurrent_comovement.py - coach 2026-09-30 item 16: do concurrent positions in the blind record move together?

Evidence base for a 1.5% same-bet cap.

Population: all detectors - TrendCont blind record (7 .dk symbols) + SweepMSS / DeepFib / EMArevQ (aa_rows, 8 .dk
symbols incl. BTCUSD). Each trade replayed under the 50% doctrine (exit_param_study.replay via
coach_items_common.replay_ex, asserted identical): open from the entry bar (the bar after the signal bar; entry at its
open = the signal bar's close) through the exit bar. Raw R (no cost) decides the side: WIN = R > 0, LOSS = R <= 0.
Trades still open at the end of their data are dropped.

Pairs: every two trades whose [entry bar, exit bar] intervals overlap (UTC H4 bar open times; all feeds share the grid).
Signed correlation: 60-day correlation of the two symbols' D1 close-to-close returns, D1 BAR-INDEX RULE: closes dated
STRICTLY BEFORE the later trade's entry day (d < entry day). The two close series are joined BY DATE FIRST and returns
taken on the common dates (so BTCUSD's weekend bars do not misalign against FX/indices); the last 61 common closes ->
60 returns, >= 40 required. Signed by direction: same direction -> +corr, opposite -> -corr.
Same-symbol pairs (two detectors, or a re-entry, on one symbol) have signed corr +-1 by construction: own rows.

Per bucket of signed corr (<0, 0-0.3, 0.3-0.6, >0.6): pairs, distinct trades, share closing on the same side (both
win / both lose), expected share if independent (from the bucket's own win rates), P(both lose), P(both full -1R stop),
and the outcome correlation rho of the paired R (both orders, so symmetric). Effective risk of two concurrent 1R
positions = sqrt(2 + 2 rho) R in standard-deviation terms (1.41R if independent, 2R if identical).
One trade sits in many pairs: pair counts are NOT independent observations; the distinct-trade count is the honest n.
The >=10 live-card reconciliation of D1 numbers is PENDING (done separately).
"""
from __future__ import annotations
import bisect, math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.coach_items_common import all_rows, run, check_replay, root                   # noqa: E402
from pipeline.trendcont_step13_study import load                                            # noqa: E402

BUCKETS = (("< 0", -9, 0.0), ("0 - 0.3", 0.0, 0.3), ("0.3 - 0.6", 0.3, 0.6), ("> 0.6", 0.6, 9))


def pearson(a, b):
    ma, mb = st.mean(a), st.mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a)); sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa > 0 and sb > 0 else 0.0


def table(pairs, groups):
    for name, f in groups:
        P = [z for z in pairs if f(z)]
        if not P: print(f"{name:22} {0:6d}"); continue
        ids = {id(z[0]) for z in P} | {id(z[1]) for z in P}
        same = sum(1 for p, q, *_ in P if (p["R"] > 0) == (q["R"] > 0)) / len(P)
        wa = st.mean([p["R"] > 0 for p, *_ in P]); wb = st.mean([q["R"] > 0 for _, q, *_ in P])
        indep = wa * wb + (1 - wa) * (1 - wb)
        bl = sum(1 for p, q, *_ in P if p["R"] <= 0 and q["R"] <= 0) / len(P)
        bs = sum(1 for p, q, *_ in P if p["stop"] and q["stop"]) / len(P)
        a = [p["R"] for p, *_ in P] + [q["R"] for _, q, *_ in P]
        b = [q["R"] for _, q, *_ in P] + [p["R"] for p, *_ in P]
        rho = pearson(a, b)
        print(f"{name:22} {len(P):6d} {len(ids):6d} {same * 100:8.1f}% {indep * 100:8.1f}% {bl * 100:8.1f}% "
              f"{(1 - wa) * (1 - wb) * 100:8.1f}% {bs * 100:7.1f}% {wa * 100:5.0f}/{wb * 100:3.0f}% {rho:+7.3f} "
              f"{math.sqrt(max(0.0, 2 + 2 * rho)):11.2f} R")


def main():
    print(__doc__)
    rows = all_rows(); check_replay(rows, 0.5)
    closes = {}
    for s in {root(x["sym"]) for x in rows}:
        d1 = load(f"{s}.dk", "d1"); closes[s] = {b[0][:10]: b[4] for b in d1}
    T = []
    for x in rows:
        r = run(x, 0.5, ex=True)
        if r is None or not r[2]: continue
        R, j, _ = r
        bank_t = None                                   # bar at which the doctrine point (+1R / TP1) was first reached
        sg = 1 if x["up"] else -1; Rd = abs(x["e"] - x["s"])
        doc = min(1.0, (x["tp1"] - x["e"]) * sg / Rd) if x["tp1"] else 1.0
        for jj in range(x["i0"], j + 1):
            b_ = x["bars"][jj]
            if ((b_[2] if x["up"] else b_[3]) - x["e"]) * sg / Rd >= doc: bank_t = b_[0][:16]; break
        T.append({"sym": root(x["sym"]), "strat": x["strat"], "up": x["up"], "R": R, "stop": R <= -0.999,
                  "a": x["bars"][x["i0"]][0][:16], "b": x["bars"][j][0][:16], "bank_t": bank_t})
    T.sort(key=lambda z: z["a"])
    print(f"trades replayed and closed: {len(T)}\n")
    cache = {}

    def corr(s1, s2, day):
        k = (min(s1, s2), max(s1, s2), day)
        if k in cache: return cache[k]
        c1, c2 = closes[s1], closes[s2]
        common = sorted(d for d in c1 if d in c2 and d < day)[-61:]
        if len(common) < 41: cache[k] = None; return None
        r1 = [math.log(c1[b] / c1[a]) for a, b in zip(common, common[1:])]
        r2 = [math.log(c2[b] / c2[a]) for a, b in zip(common, common[1:])]
        cache[k] = pearson(r1, r2); return cache[k]

    pairs, nocorr = [], 0
    for i, p in enumerate(T):
        for k in range(i + 1, len(T)):
            q = T[k]
            if q["a"] > p["b"]: break
            if q["a"] > p["b"] or p["a"] > q["b"]: continue
            same_dir = p["up"] == q["up"]
            if p["sym"] == q["sym"]:
                pairs.append((p, q, 1.0 if same_dir else -1.0, "same symbol")); continue
            day = max(p["a"], q["a"])[:10]
            c = corr(p["sym"], q["sym"], day)
            if c is None: nocorr += 1; continue
            sc = c if same_dir else -c
            b = next(n for n, lo, hi in BUCKETS if lo <= sc < hi)
            pairs.append((p, q, sc, b))
    print(f"overlapping pairs: {len(pairs)} (+{nocorr} dropped: < 40 common D1 returns before entry)\n")
    hdr = (f"{'signed corr':22} {'pairs':>6} {'trades':>6} {'same side':>9} {'if indep.':>9} {'both lose':>9} "
           f"{'if indep.':>9} {'both -1R':>8} {'win% 1st/2nd':>12} {'rho(R)':>7} {'eff. risk 2x1R':>14}")
    print(hdr); print("-" * len(hdr))
    groups = [(n, lambda z, n=n: z[3] == n) for n, *_ in BUCKETS] + [
        ("same symbol, same dir", lambda z: z[3] == "same symbol" and z[2] > 0),
        ("same symbol, opposite", lambda z: z[3] == "same symbol" and z[2] < 0),
        ("ALL cross-symbol", lambda z: z[3] != "same symbol")]
    table(pairs, groups)
    atrisk = [z for z in pairs if z[0]["bank_t"] is None or z[0]["bank_t"] >= z[1]["a"]]
    print("\nBOTH AT RISK: only pairs where the first trade had NOT yet reached its bank point (+1R / TP1, after which its"
          "\nstop is at entry) when the second entered - the pairs a same-bet risk cap is actually about:")
    print(hdr); print("-" * len(hdr))
    table(atrisk, groups)
    print("\n'1st/2nd' = the trade that entered first / second. The first leg wins far more often: a trade still open when"
          "\nanother enters has survived - losers stop out fast, so long-lived trades are mostly banked winners. The"
          "\nindependence columns use each bucket's own two win rates, so they carry that survival effect too.")
    print("\n'if indep.' = same-side share expected from the two legs' win rates alone. 'eff. risk' = sd of the sum of two"
          "\n1R positions in units of one position's sd: sqrt(2)=1.41 if independent, 2.00 if they are the same bet.")
    print("\nconcurrency: max trades open at once, and share of trades that overlap at least one other")
    ev = sorted([(z["a"], 1) for z in T] + [(z["b"], -1) for z in T], key=lambda e: (e[0], -e[1]))
    cur = mx = 0
    for _, d in ev: cur += d; mx = max(mx, cur)
    inpair = {id(z[0]) for z in pairs} | {id(z[1]) for z in pairs}
    print(f"  max concurrent {mx}; {len(inpair)} of {len(T)} trades ({len(inpair) / len(T) * 100:.0f}%) overlap another")

    print("\nREADING (written after the run): the full-pair table is dominated by pairs whose first leg was already banked"
          "\n(stop at entry, cannot lose) - it understates co-movement. On BOTH-AT-RISK pairs the effect is monotonic in"
          "\nsigned correlation: same-side share 47% (<0) -> 55% -> 58% -> 74% (>0.6); P(both lose) 23.5% -> 33% (>0.6) vs"
          "\n~21-25% if independent; outcome rho -0.07 -> +0.49, effective risk of two 1R positions 1.36R -> 1.73R (same"
          "\nsymbol same direction 1.77R). Two concurrent 1R positions with signed corr > 0.6 behave like ~1.7R of one bet,"
          "\nnot 2R and not 1.41R; for the whole cross-symbol book the average is 1.47R.")


if __name__ == "__main__": main()
